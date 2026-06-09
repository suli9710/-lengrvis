const assert = require("node:assert/strict");
const fs = require("node:fs");

const {
  assertInsecureLanError,
  assertJsonRequest,
  jsonResponse,
  loadMobileClient,
  loadTsModule,
  mobilePath,
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
  const completionEvidence =
    overrides.completion_evidence ??
    (status === "completed"
      ? { level: "visible_progress", result_verified: false, signoff: false, missing_count: 2 }
      : status === "failed"
        ? { level: "safe_failure", result_verified: false, signoff: false, missing_count: 1 }
        : { level: actions.length ? "visible_progress" : "task_created", result_verified: false, signoff: false, missing_count: 1 });
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
    completion_evidence: completionEvidence,
    result_verified: completionEvidence.result_verified,
    evidence_verified: completionEvidence.result_verified,
    credibility: completionEvidence.result_verified ? "verified" : completionEvidence.level === "safe_failure" ? "failed" : "partial",
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

function loadTaskDisplayModules() {
  const safeDisplay = loadTsModule(mobilePath("src/safeDisplay.ts"));
  const taskDisplay = loadTsModule(mobilePath("src/taskCompanionDisplay.ts"), {
    require: (specifier) => {
      if (specifier === "./safeDisplay") return safeDisplay;
      return require(specifier);
    },
  });
  return { safeDisplay, taskDisplay };
}

function assertNoRawMobileLeak(value, label = "mobile display text") {
  assert.doesNotMatch(value, /secret-token|session-token|Bearer\s+(?!\[已隐藏\])[A-Za-z0-9._~+/=-]+/i, `${label} must not expose tokens or passwords`);
  assert.doesNotMatch(value, /\b(?:token|password|secret|authorization)\b\s*[:=]\s*(?!\[已隐藏\])\S/i, `${label} must not expose secret assignments`);
  assert.doesNotMatch(value, /\b[A-Z][A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PASS|AUTH|CREDENTIAL)[A-Z0-9_]*\b\s*[:=]\s*(?!\[已隐藏\])\S/i, `${label} must not expose env-style secrets`);
  assert.doesNotMatch(value, /\b(?:sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b/i, `${label} must not expose platform tokens`);
  assert.doesNotMatch(value, /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i, `${label} must not expose email addresses`);
  assert.doesNotMatch(value, /\b(?:https?|wss?|file):\/\/|(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?\b|localhost(?::\d+)?/i, `${label} must not expose hosts or protocols`);
  assert.doesNotMatch(value, /[A-Za-z]:[\\/][^\s]+|\\\\[^\\/\s]+\\[^\s]+|\/(?:Users|home|var|tmp|etc|mnt|Volumes)\b|~[\\/][^\s]+/i, `${label} must not expose raw local paths`);
  assert.doesNotMatch(value, /\b(?:args|tool_args|arguments)\s*[:=]\s*(?!\[已隐藏\])\S/i, `${label} must not expose tool args`);
}

function assertTaskDisplaySafety(taskDisplay) {
  const dangerousTask = makeTask("task-dangerous", "execution", {
    title: "检查 C:\\Users\\Suli\\Desktop\\private-contract password=hunter2",
    summary: "已读取 http://192.168.1.20:8000/files secret-token args={\"path\":\"C:\\Users\\Suli\\Desktop\"}",
    status_detail: "{\"args\":{\"path\":\"C:\\Users\\Suli\\Desktop\\private-contract\",\"token\":\"secret-token\"}}",
    available_actions: ["cancel", "follow_up"],
    can_pause: false,
    content_redacted: false,
  });
  const safeText = [
    taskDisplay.taskDisplayTitle(dangerousTask),
    taskDisplay.taskDisplaySummary(dangerousTask),
    taskDisplay.taskStatusDetailText(dangerousTask),
    taskDisplay.taskCredibilityText(dangerousTask),
    taskDisplay.taskNextStepText(dangerousTask),
  ].join("\n");
  assertNoRawMobileLeak(safeText, "task companion display");
  assert.equal(taskDisplay.taskActionAllowed(dangerousTask, "pause"), false, "task actions must honor available_actions over status guesses");
  assert.equal(taskDisplay.taskActionAllowed(dangerousTask, "follow_up"), true);
  assert.equal(taskDisplay.taskCredibilityText(makeTask("done-unverified", "completed", { content_redacted: false })), "已有进度；手机未收到已核验结果。");
  assert.equal(
    taskDisplay.taskStatusBadgeText(makeTask("done-unverified-badge", "completed", { content_redacted: false })),
    "结果待核验",
  );
  assert.equal(
    taskDisplay.taskStatusBadgeIsDone(makeTask("done-unverified-style", "completed", { content_redacted: false })),
    false,
  );
  assert.equal(
    taskDisplay.taskCredibilityText(
      makeTask("done-overclaimed", "completed", { content_redacted: false, result_verified: true, credibility: "verified" }),
    ),
    "已有进度；手机未收到已核验结果。",
  );
  assert.equal(
    taskDisplay.taskCredibilityText(
      makeTask("done-verified", "completed", {
        content_redacted: false,
        completion_evidence: { level: "completed_result", result_verified: true, signoff: false, missing_count: 0 },
        result_verified: true,
        evidence_verified: true,
        credibility: "verified",
      }),
    ),
    "已带可核对的完成结果。",
  );
  assert.equal(
    taskDisplay.taskStatusBadgeText(
      makeTask("done-verified-badge", "completed", {
        content_redacted: false,
        completion_evidence: { level: "completed_result", result_verified: true, signoff: false, missing_count: 0 },
      }),
    ),
    "已完成",
  );
  assert.equal(
    taskDisplay.taskStatusBadgeIsDone(
      makeTask("done-verified-style", "completed", {
        content_redacted: false,
        completion_evidence: { level: "completed_result", result_verified: true, signoff: false, missing_count: 0 },
      }),
    ),
    true,
  );
  assert.equal(
    taskDisplay.taskNextStepText(
      makeTask("done-verified-next", "completed", {
        content_redacted: false,
        completion_evidence: { level: "completed_result", result_verified: true, signoff: false, missing_count: 0 },
      }),
    ),
    "回电脑端核对结果和来源，再决定是否签收。",
  );
}

function assertSafeDisplayHelpers(safeDisplay) {
  const redacted = safeDisplay.safeDisplayText(
    "Bearer secret-token host=192.168.1.20 protocol=http path=C:\\Users\\Suli\\Desktop\\private-contract args={\"token\":\"secret-token\"}",
  );
  assertNoRawMobileLeak(redacted, "safeDisplayText");
  assert.match(redacted, /已隐藏/);

  const preview = safeDisplay.safePreviewText("变更: C:\\Users\\Suli\\Desktop\\private-contract password=hunter2");
  assertNoRawMobileLeak(preview, "safePreviewText");
  assert.match(preview, /已隐藏/);

  const envSecret = safeDisplay.safeDisplayText(
    "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz reviewer=suli@example.com workspace=~/Desktop/private",
  );
  assertNoRawMobileLeak(envSecret, "safeDisplayText env secret");
  assert.match(envSecret, /已隐藏/);
}

function assertTaskCompanionSourceSafety() {
  const source = fs.readFileSync(mobilePath("src/screens/ApprovalsScreen.tsx"), "utf8");
  assert.doesNotMatch(source, /D:\/Downloads|任务 Companion|return error\.message/);
  assert.match(source, /approvalListSafety/);
  assert.match(source, /ListEmptyComponent/);
  assert.match(source, /refreshing=\{isRefreshing\}/);
  assert.match(source, /重新同步/);
  assert.match(source, /taskCredibilityText/);
  assert.match(source, /taskStatusBadgeText/);
  assert.match(source, /taskStatusBadgeIsDone/);
  assert.match(source, /safePreviewText/);
}

async function main() {
  const client = loadMobileClient();
  const { safeDisplay, taskDisplay } = loadTaskDisplayModules();
  assertSafeDisplayHelpers(safeDisplay);
  assertTaskDisplaySafety(taskDisplay);
  assertTaskCompanionSourceSafety();

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
            message: "已从手机任务助手添加补充指令，电脑端会作为相关任务继续处理。",
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
    const lanSession = makeSession(client, "http://192.168.1.20:8000");
    await assert.rejects(() => client.listMobileTasks(lanSession), assertInsecureLanError);
    await assert.rejects(() => client.submitMobileTaskCommand(lanSession, ACTIVE_TASK_ID, "pause"), assertInsecureLanError);
    await assert.rejects(
      () => client.submitMobileTaskFollowUp(lanSession, PAUSED_TASK_ID, { instruction: "blocked" }),
      assertInsecureLanError,
    );
    await assert.rejects(
      () => client.createMobileTask(lanSession, { template_id: "check_computer_status", mode: "hybrid" }),
      assertInsecureLanError,
    );
    assert.equal(server.requests.length, 0, "blocked insecure LAN task companion calls must not reach the smoke server");

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
