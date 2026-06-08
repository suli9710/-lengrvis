const assert = require("node:assert/strict");

const {
  assertJsonRequest,
  jsonResponse,
  loadMobileClient,
  startHttpWsSmokeServer,
} = require("./behavior-smoke-helpers.cjs");

const SESSION_TOKEN = "session-token";
const DEVICE_ID = "device-1";
const ACTIVE_TASK_ID = "task/active id";
const PAUSED_TASK_ID = "task-paused";

function makeSession(client, baseUrl) {
  const security = client.describeBaseUrlSecurity(baseUrl);
  return {
    baseUrl: security.normalizedBaseUrl,
    baseUrlSecurity: security,
    deviceId: DEVICE_ID,
    token: SESSION_TOKEN,
  };
}

function makeTask(id, status, overrides = {}) {
  const actions =
    status === "paused"
      ? ["resume", "cancel", "follow_up"]
      : status === "completed" || status === "cancelled" || status === "failed"
        ? []
        : ["pause", "cancel", "follow_up"];
  return {
    id,
    title: "安全任务标题",
    status,
    status_label: status === "paused" ? "已暂停" : status === "execution" ? "运行中" : "已完成",
    status_detail: "任务状态已同步。",
    mode: "hybrid",
    summary: "已隐藏敏感正文。",
    available_actions: actions,
    can_pause: actions.includes("pause"),
    can_resume: actions.includes("resume"),
    can_cancel: actions.includes("cancel"),
    can_follow_up: actions.includes("follow_up"),
    is_terminal: actions.length === 0,
    content_redacted: true,
    privacy_redacted: false,
    created_at: "2026-06-01T00:00:00.000Z",
    updated_at: "2026-06-01T00:01:00.000Z",
    ...overrides,
  };
}

function decodeTaskCommandPath(pathname) {
  const match = pathname.match(/^\/api\/mobile\/tasks\/(.+)\/(pause|resume|cancel|follow-up)$/);
  if (!match) return null;
  return {
    taskId: decodeURIComponent(match[1]),
    command: match[2],
  };
}

async function main() {
  const client = loadMobileClient();
  const server = await startHttpWsSmokeServer({
    handleRequest: ({ res, url, request }) => {
      if (request.method === "GET" && url.pathname === "/api/mobile/tasks") {
        assertJsonRequest(request, {
          method: "GET",
          path: "/api/mobile/tasks",
          authorization: `Bearer ${SESSION_TOKEN}`,
        });
        jsonResponse(res, 200, {
          tasks: [
            makeTask(ACTIVE_TASK_ID, "execution"),
            makeTask(PAUSED_TASK_ID, "paused"),
            makeTask("task-private", "created", {
              title: "隐私任务",
              summary: "隐私模式：请在电脑端查看任务详情。",
              available_actions: ["cancel", "follow_up"],
              can_pause: false,
              privacy_redacted: true,
            }),
          ],
        });
        return true;
      }

      const command = decodeTaskCommandPath(url.pathname);
      if (request.method === "POST" && command) {
        const encodedTaskId = encodeURIComponent(command.taskId);
        assertJsonRequest(request, {
          method: "POST",
          path: `/api/mobile/tasks/${encodedTaskId}/${command.command}`,
          authorization: `Bearer ${SESSION_TOKEN}`,
        });
        if (command.command === "follow-up") {
          assert.deepEqual(request.json, { instruction: "继续检查状态" });
          jsonResponse(res, 201, {
            task: makeTask("task-follow-up", "created", {
              available_actions: ["cancel", "follow_up"],
              can_pause: false,
            }),
            message: "已从手机 Companion 添加补充指令，电脑端会作为相关任务继续处理。",
            source_task_id: command.taskId,
          });
          return true;
        }
        const nextStatus = command.command === "pause" ? "paused" : command.command === "resume" ? "execution" : "cancelled";
        jsonResponse(res, 200, makeTask(command.taskId, nextStatus));
        return true;
      }

      return false;
    },
    handleUpgrade: ({ socket }) => {
      socket.destroy();
    },
  });

  try {
    const session = makeSession(client, server.origin);
    const tasks = await client.listMobileTasks(session);
    assert.equal(tasks.length, 3);
    assert.equal(tasks[0].id, ACTIVE_TASK_ID);
    assert.deepEqual(tasks[0].available_actions, ["pause", "cancel", "follow_up"]);
    assert.equal(tasks[0].can_pause, true);
    assert.equal(tasks[1].can_resume, true);
    assert.equal(tasks[2].privacy_redacted, true);
    assert.doesNotMatch(JSON.stringify(tasks), /secret-token|private-contract|password=/);

    const paused = await client.submitMobileTaskCommand(session, ACTIVE_TASK_ID, "pause");
    assert.equal(paused.status, "paused");
    assert.deepEqual(paused.available_actions, ["resume", "cancel", "follow_up"]);

    const resumed = await client.submitMobileTaskCommand(session, ACTIVE_TASK_ID, "resume");
    assert.equal(resumed.status, "execution");
    assert.equal(resumed.can_pause, true);

    const cancelled = await client.submitMobileTaskCommand(session, ACTIVE_TASK_ID, "cancel");
    assert.equal(cancelled.status, "cancelled");
    assert.deepEqual(cancelled.available_actions, []);
    assert.equal(cancelled.is_terminal, true);

    const followUp = await client.submitMobileTaskFollowUp(session, PAUSED_TASK_ID, { instruction: "继续检查状态" });
    assert.equal(followUp.source_task_id, PAUSED_TASK_ID);
    assert.equal(followUp.task.id, "task-follow-up");
    assert.equal(server.requests.length, 5);
    assert.equal(server.requests[0].path, "/api/mobile/tasks");
    assert.equal(server.requests[1].path, "/api/mobile/tasks/task%2Factive%20id/pause");
    assert.equal(server.requests[4].path, "/api/mobile/tasks/task-paused/follow-up");
  } finally {
    await server.close();
  }
}

main()
  .then(() => console.log("mobile task companion behavior smoke passed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
