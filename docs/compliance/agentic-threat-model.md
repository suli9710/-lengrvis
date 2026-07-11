# Lengrvis Agentic Threat Model

Version: TM-2026-07-11-v1  
Status: release-gated baseline  
Last reviewed: 2026-07-11  
Control map: docs/compliance/agentic-control-map.json

## Purpose

This threat model defines the trust boundaries that apply when Lengrvis turns
natural-language goals and untrusted content into local or remote side effects.
It is an engineering control document, not a penetration-test report, legal
opinion, certification, or release sign-off.

Every new Agent, tool, connector, data source, remote channel, credential use,
or execution capability must update this document and the machine-readable
control map before a release profile can expose the capability.

## Security objectives

1. A model, document, webpage, MCP server, Skill, or tool result cannot expand
   the user's goal, permissions, recipients, paths, network destinations, or
   execution budget.
2. Every side effect is revalidated by deterministic policy at the execution
   point and is bound to the current goal, plan revision, resource state, data
   provenance, policy version, and short-lived capability.
3. Credentials remain in a trusted host boundary and are never placed in model
   prompts, ordinary tool arguments, diagnostics, generated code, or child
   process environments.
4. Cancellation, budget exhaustion, device revocation, policy changes, taint
   escalation, and target identity changes fail closed.
5. Completion is separate from verification. External writes, submissions, and
   notifications require post-action evidence or an explicit unverified result.

## Trust boundaries

### TB-01 User and local Windows session

- Assets: user intent, authorized files, application sessions, credentials.
- Threats: a different local process, accidental broad authorization, stale
  intent, user-session compromise.
- Controls: Windows user boundary, DPAPI-backed secrets, native confirmation,
  task-scoped intent capsules, application identity checks, explicit revocation.
- Residual risk: a fully compromised process running as the same Windows user
  may access user-visible data or manipulate the desktop.

### TB-02 Electron renderer to main/preload

- Assets: desktop token, privileged IPC actions, local paths, browser sessions.
- Threats: renderer compromise, route confusion, encoded-path bypass, newly
  added backend endpoints becoming reachable by default.
- Controls: context isolation, sandboxed renderer, sender validation, explicit
  method-and-route allowlist, typed IPC handlers, schema validation.
- Residual risk: a defect in an explicitly allowed IPC handler can still expose
  excess capability.

### TB-03 Electron main to embedded browser content

- Assets: cookies, authenticated sessions, form values, downloaded files.
- Threats: prompt injection, popup escape, permission abuse, navigation drift,
  hidden downloads, credential-field targeting.
- Controls: isolated persistent partition, hardened webContents, origin and page
  fingerprint checks, downloads denied by default, sensitive-field guards,
  post-submit verification.
- Residual risk: website UI and accessibility semantics can change without
  notice and may require a connector to be suspended.

### TB-04 Desktop to local FastAPI

- Assets: task plans, approvals, tool calls, settings, audit events.
- Threats: forged local requests, approval replay, policy drift, argument
  mutation between preview and execution.
- Controls: loopback binding, desktop API secret, origin checks, HMAC-bound
  approvals, atomic approval consumption, intent capsule validation.
- Residual risk: same-user local compromise may obtain local secrets.

### TB-05 Planner and model providers

- Assets: goals, context, document excerpts, screenshots, tool observations.
- Threats: provider compromise, data over-egress, prompt injection, goal drift,
  malicious or malformed model output.
- Controls: content envelopes, data-egress scope, structured schemas, policy
  revalidation, sensitive-value redaction, bounded context and budgets.
- Residual risk: approved cloud content is visible to the selected provider and
  remains subject to that provider's operational controls.

### TB-06 Files, documents, OCR, RAG, and memory

- Assets: local business data, extracted text, embeddings, learned preferences.
- Threats: indirect prompt injection, poisoned retrieval, persistence of hostile
  instructions, cross-task information leakage.
- Controls: provenance and taint propagation, memory quarantine, TTL and scope,
  explicit promotion, source review, tainted-to-write reauthorization.
- Residual risk: deterministic extraction cannot establish semantic truth and
  may still produce incorrect business values.

### TB-07 Tools, Skills, MCP, and generated execution

- Assets: filesystem, subprocess, network, application automation, credentials.
- Threats: malicious extension, tool poisoning, path escape, token forwarding,
  unbounded subprocesses, network exfiltration.
- Controls: signed manifests, runtime policy, path sandbox, SSRF pinning, tool
  schema validation, capability manifest, execution disabled without an OS
  sandbox, per-run budget ledger.
- Residual risk: signatures prove provenance rather than safety; third-party
  code remains untrusted.

### TB-08 SQLite, recordings, logs, and diagnostics

- Assets: task history, approvals, audit chain, screenshots, support artifacts.
- Threats: local disclosure, uncontrolled retention, misleading tamper-proof
  claims, accidental support-package exfiltration.
- Controls: DPAPI-wrapped encryption for high-sensitivity data, data-class TTLs,
  redacted exports, user review, tamper-evident chain, optional external anchor.
- Residual risk: a same-user attacker may access DPAPI material and recompute a
  purely local chain.

### TB-09 Android companion and LAN transport

- Assets: mobile session, approvals, task commands, screen and input grants.
- Threats: lost phone, bearer-token reuse, stale TLS pins, LAN MITM, replay.
- Controls: one-time pairing, TLS fingerprint confirmation, device revocation,
  short access tokens, rotating refresh family, scoped remote grants, and a
  fail-closed denial of high-impact mobile approval until proof of possession
  plus trustworthy biometric step-up are implemented and evidenced.
- Residual risk: Preview transports are not GA evidence until real-device and
  release-certificate gates pass.

### TB-10 Internet relay and account control plane

- Assets: account identity, device directory, encrypted task envelopes, quotas.
- Threats: account takeover, cross-device delivery, replay, queue retention,
  metadata disclosure, service compromise.
- Controls: versioned ciphertext-only RelayEnvelope contracts enforce Preview
  action types, 24-hour TTL, idempotency and replay metadata. Email-code login,
  device public keys, ACK ordering, and the independent relay service remain GA
  prerequisites rather than current production controls.
- Residual risk: routing metadata and account identifiers remain visible to the
  relay service.

### TB-11 Build, update, and release supply chain

- Assets: source, prompts, policy, tool schemas, dependencies, installers, APK.
- Threats: dependency substitution, signing-key misuse, bad update, capability
  drift between builds, unsigned or mismatched artifacts.
- Controls: locked dependencies, SBOM, CodeQL, secret scanning, release-signing
  requirements and artifact-verification gates, candidate binding, capability
  manifests, provenance requirements, rollback and kill-switch requirements.
- Residual risk: hosted builders and external signing services remain trusted
  dependencies until stronger reproducibility and isolation evidence exists.

## Mandatory execution invariants

- Untrusted content may supply facts, but never authority.
- A plan revision, provenance change, target change, scope expansion, expired
  capability, or policy change invalidates prior execution authorization.
- High-impact actions require a valid intent capsule and sufficient remaining
  deterministic budget at the moment of execution.
- Sensitive host capabilities use opaque references; raw secrets are rejected
  from ordinary tool schemas and diagnostics.
- Repeated identical side effects, recipient fan-out, destination fan-out, and
  retry loops are bounded independently of model reasoning.
- Unsafe local code execution remains disabled in release profiles until a
  Windows OS sandbox broker has candidate evidence.

## Change gate

A release-bound change that adds or expands a capability must provide:

1. Updated trust boundary and abuse scenario.
2. Control owner and residual risk.
3. Deterministic unit or integration test.
4. Capability-manifest entry and revocation behavior.
5. Release evidence classification: machine, manual, external, or pending.

The command npm run security:threat-model validates these requirements. A
passing document check is necessary but is not release sign-off.
