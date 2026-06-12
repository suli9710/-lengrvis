const assert = require("node:assert/strict");

const {
  assertJsonRequest,
  jsonResponse,
  loadMobileClient,
  startHttpWsSmokeServer,
} = require("./behavior-smoke-helpers.cjs");

const sampleWakeup = {
  id: "wakeup_smoke_1",
  source: "scheduler",
  source_id: "sched_1",
  title: "Morning review",
  body: "Check overnight runs",
  goal: "Review guardian tasks",
  mode: "efficiency",
  status: "pending",
  due_at: "2026-06-11T08:00:00+00:00",
  created_at: "2026-06-11T07:55:00+00:00",
  updated_at: "2026-06-11T07:55:00+00:00",
};

async function main() {
  const { origin, requests, close } = await startHttpWsSmokeServer({
    handleRequest: async ({ request, res }) => {
      if (request.method === "GET" && request.path === "/api/mobile/wakeups/pending") {
        assert.equal(request.headers.authorization, "Bearer smoke-token");
        jsonResponse(res, 200, [sampleWakeup]);
        return true;
      }
      if (request.method === "POST" && request.path === `/api/mobile/wakeups/${sampleWakeup.id}/approve`) {
        assert.equal(request.headers.authorization, "Bearer smoke-token");
        jsonResponse(res, 200, { ...sampleWakeup, status: "approved", decided_at: "2026-06-11T08:01:00+00:00" });
        return true;
      }
      if (request.method === "POST" && request.path === `/api/mobile/wakeups/${sampleWakeup.id}/reject`) {
        assert.equal(request.headers.authorization, "Bearer smoke-token");
        jsonResponse(res, 200, { ...sampleWakeup, status: "rejected", decided_at: "2026-06-11T08:01:00+00:00" });
        return true;
      }
      return false;
    },
  });

  const baseUrl = origin;
  const client = loadMobileClient();
  const session = { baseUrl, token: "smoke-token", deviceName: "Smoke Phone", pairedAt: new Date().toISOString() };

  const pending = await client.listPendingMobileWakeups(session);
  assert.equal(pending.length, 1);
  assert.equal(pending[0].id, sampleWakeup.id);
  assert.equal(pending[0].status, "pending");

  const approved = await client.approveMobileWakeup(session, sampleWakeup.id);
  assert.equal(approved.status, "approved");

  const rejected = await client.rejectMobileWakeup(session, sampleWakeup.id);
  assert.equal(rejected.status, "rejected");

  assertJsonRequest(requests, "GET", "/api/mobile/wakeups/pending");
  assertJsonRequest(requests, "POST", `/api/mobile/wakeups/${sampleWakeup.id}/approve`);
  assertJsonRequest(requests, "POST", `/api/mobile/wakeups/${sampleWakeup.id}/reject`);

  await close();
  console.log("wakeup-contract-smoke: ok");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
