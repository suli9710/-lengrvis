# Windows Execution-Isolation Host Contract

This contract defines the boundary between the Python backend and a separately built Windows-native isolation host. The repository currently contains the fail-closed adapter and verifier; it does not contain the native AppContainer/restricted-token broker itself.

## Release status

Arbitrary local Skill execution and generated-code/write execution remain disabled in public Beta, RC, and release profiles unless every check below succeeds. A Job Object, a signed JSON response, or an installed executable alone is not sufficient evidence of OS containment.

The native host must be the launcher/broker that established the restricted execution boundary, or must independently verify a boundary established before the backend started. A child process cannot retroactively turn an already-running parent into an AppContainer or replace its token; an implementation that merely echoes the requested capability names must never sign an attestation.

## Installed artifacts and release pins

The release environment must provide all five values:

- `LENGRVIS_WINDOWS_ISOLATION_HOST_PATH`: absolute path to the native `.exe`.
- `LENGRVIS_WINDOWS_ISOLATION_HOST_SHA256`: lowercase `sha256:<64 hex>` digest of that exact executable.
- `LENGRVIS_WINDOWS_ISOLATION_POLICY_PATH`: absolute path to the reviewed policy artifact.
- `LENGRVIS_WINDOWS_ISOLATION_POLICY_SHA256`: lowercase digest of that exact policy artifact.
- `LENGRVIS_WINDOWS_ISOLATION_ATTESTATION_PUBLIC_KEY`: `ed25519:<base64url>` public key whose private key is available only to the native attestation boundary.

`backend/app/security/windows_execution_isolation_host.py` resolves the full artifact path chain and rejects symlinks/reparse points, verifies both digests before and after execution, requires a trusted embedded Authenticode signature on the host, invokes a fixed argument vector without a shell, uses a minimal environment and the host directory as its working directory, and places the attestation process in a kill-on-close Job Object with an active-process limit of one.

## Invocation and response

The fixed invocation is:

```text
<host.exe> --attest-current-process-tree --policy <policy-path>
```

The backend writes exactly one canonical UTF-8 JSON request line to stdin. It contains the schema `lengrvis-windows-execution-isolation-request-v1`, a fresh nonce, backend/parent process IDs, and the two release-pinned digests. The host must return exactly one JSON line, no larger than 64 KiB, with only `payload` and `signature` fields.

The payload schema is `lengrvis-windows-execution-isolation-v1`. The detached Ed25519 signature covers the canonical payload, including the exact challenge, short issuance/expiry window, evidence id, host/policy digests, `enforced:true`, and the sorted capability set:

- `appcontainer`
- `restricted_token`
- `job_object`
- `network_broker`

The backend rejects an unsigned response, an unknown/extra payload shape, a replayed or altered challenge, an expired/future/overlong attestation, a digest that differs from the release pins, a non-canonical capability list, or a missing required capability.

## Evidence required before enabling arbitrary execution

The native candidate still needs candidate-bound evidence that demonstrates enforcement rather than configuration:

- token/AppContainer identity and non-breakaway Job limits for the actual worker tree;
- file broker allow/deny tests, including junction, symlink, UNC, device path, 8.3 alias, race, and inherited-handle attempts;
- default-deny network tests covering DNS rebinding, redirects, loopback/private/metadata addresses, IPv4/IPv6 variants, and child-process egress;
- credential/environment/handle leakage tests and process-spawn escape tests;
- crash, timeout, cancellation, and broker-restart behavior with no orphaned process or replayed side effect;
- Authenticode, host/policy digest, Ed25519 key fingerprint, attack-test report, candidate commit, and release-owner signature bound to the same RC artifact.

Until that packet exists, `current_execution_isolation_attestation()` remains incomplete and release safety continues to fail closed.
