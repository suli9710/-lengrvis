import { describe, expect, it } from "vitest";

import { CredentialUseTicketBroker } from "./credentialUseTicketBroker";

const binding = {
  credential_ref_id: "cred_12345678",
  domain: "https://example.test:8443",
  session_id: "session_1",
  page_fingerprint: `sha256:${"a".repeat(64)}`,
  run_id: "run_1",
  task_id: "task_1",
  purpose: "sign-in" as const
};

describe("CredentialUseTicketBroker", () => {
  it("binds a short-lived ticket and consumes it exactly once", () => {
    const broker = fixedBroker();
    const ticket = broker.issue(binding, 60);

    expect(ticket).toMatchObject({
      schema_version: "credential-use-ticket-v1",
      credential_ref_id: binding.credential_ref_id,
      domain: binding.domain,
      run_id: binding.run_id,
      task_id: binding.task_id,
      purpose: "sign-in",
      issued_at: "2026-07-11T00:00:00.000Z",
      expires_at: "2026-07-11T00:01:00.000Z"
    });
    expect(JSON.stringify(ticket)).not.toContain("password");
    expect(broker.consume(ticket, binding)).toEqual(ticket);
    expect(() => broker.consume(ticket, binding)).toThrow(/already consumed/);
  });

  it("rejects mutated bindings without consuming the valid ticket", () => {
    const broker = fixedBroker();
    const ticket = broker.issue(binding);
    const mutated = { ...ticket, domain: "https://evil.test" };

    expect(() => broker.consume(mutated, { ...binding, domain: "https://evil.test" })).toThrow(/binding does not match/);
    expect(broker.consume(ticket, binding)).toEqual(ticket);
  });

  it("fails closed after expiry and rejects long TTLs", () => {
    let now = new Date("2026-07-11T00:00:00.000Z");
    const broker = new CredentialUseTicketBroker({
      now: () => now,
      randomId: () => "ticket-1",
      randomNonce: () => "nonce_123456789012345678901234"
    });
    const ticket = broker.issue(binding, 1);
    now = new Date("2026-07-11T00:00:02.000Z");

    expect(() => broker.consume(ticket, binding)).toThrow(/expired/);
    expect(() => broker.issue(binding, 121)).toThrow(/between 1 and 120/);
  });
});

function fixedBroker(): CredentialUseTicketBroker {
  return new CredentialUseTicketBroker({
    now: () => new Date("2026-07-11T00:00:00.000Z"),
    randomId: () => "11111111-2222-3333-4444-555555555555",
    randomNonce: () => "nonce_123456789012345678901234"
  });
}
