# Commercial License Operations

This runbook defines the offline issuer and first-activation process for Lengrvis Plus and Pro subscriptions. It is operational guidance, not proof that a production issuer key, commercial owner, or live customer workflow has been approved.

## Trust boundary

- The Ed25519 private key stays on a dedicated offline/admin machine or approved HSM-backed signing environment.
- Runtime builds receive only `LENGRVIS_LICENSE_PUBLIC_KEY`.
- Packaged Electron launches force `LENGRVIS_COMMERCIAL_RELEASE=true`; managed Windows services must set it in their approved runtime configuration. In this mode `LENGRVIS_PLAN=plus/pro` cannot unlock paid features without a valid, active, non-revoked signed license.
- First online activation uses `LENGRVIS_ACTIVATION_BASE_URL` on the desktop client and `POST /api/v1/activations` on the activation server. The activation server stores only HMAC-SHA256 activation-key hashes and returns an Ed25519 signed license token.
- Never place a private key, passphrase, `LENGRVIS_LICENSE_PRIVATE_KEY`, or deprecated `LENGRVIS_LICENSE_SIGNING_KEY` in `.env`, GitHub Actions runtime secrets, an installer, a diagnostic package, or a customer machine.
- Never place `LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY`, `LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE`, activation keys, root passwords, database passwords, or raw customer data in tracked docs, installers, or diagnostics.
- License files and revocation manifests may be shared with the licensed customer. The issuer ledger and private key are confidential operational records.
- `order_ref` must be an opaque/redacted internal reference, not a payment card number or unnecessary personal data.

## 1. Generate the issuer keypair

Create the passphrase file on the offline issuer machine, then run:

```powershell
npm run license:admin -- keygen `
  --private-key-out C:\secure\lengrvis-issuer-private.pem `
  --public-key-out C:\secure\lengrvis-issuer-public.key `
  --private-key-passphrase-file C:\secure\issuer-passphrase.txt
```

The private key is encrypted PKCS#8. The public key file contains an `ed25519:` value and may be provisioned as `LENGRVIS_LICENSE_PUBLIC_KEY` in a commercial release profile.

Back up the encrypted private key and passphrase separately. Record the printed public-key fingerprint in the commercial release evidence. Key generation with `--allow-unencrypted-private-key` is only for isolated tests and must not be used for production.

## 2. Issue a license

```powershell
npm run license:admin -- issue `
  --private-key C:\secure\lengrvis-issuer-private.pem `
  --private-key-passphrase-file C:\secure\issuer-passphrase.txt `
  --issuer "Approved contracting entity" `
  --subject "Customer display name" `
  --plan pro `
  --seats 25 `
  --expires-at 2027-06-30T23:59:59Z `
  --order-ref order-redacted-1042 `
  --output C:\secure\out\customer-1042.lic `
  --ledger C:\secure\issuer-ledger.jsonl
```

The command:

- generates a stable `license_id`;
- signs the token without printing it to the terminal;
- writes the license atomically;
- appends a token-free issuance event to a SHA-256 hash-chained JSONL ledger;
- records the public-key fingerprint and license artifact hash.

The customer imports the `.lic` file from “设置 → 套餐与授权”, or an administrator deploys it through `LENGRVIS_LICENSE_KEY` / `<data_dir>/license.key`.

## 3. Create a subscription activation key

The activation server must be configured with:

- `LENGRVIS_ACTIVATION_DB=<private sqlite path>`
- `LENGRVIS_ACTIVATION_KEY_PEPPER=<server-only random secret>`
- `LENGRVIS_ACTIVATION_SERVER_DEVICE_SECRET=<separate server-only random secret for device binding>`
- `LENGRVIS_ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF=true`
- `LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY_FILE=<encrypted issuer private key path>`
- `LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE_FILE=<private passphrase file>` when the key is encrypted
- `LENGRVIS_LICENSE_PUBLIC_KEY=<production Ed25519 public key>` for local self-checks

Create a Plus/Pro activation key without storing the raw key:

```powershell
npm run activation:admin -- create-key `
  --plan pro `
  --subscription-id sub-redacted-001 `
  --status active `
  --subject customer-redacted `
  --max-devices 1 `
  --expires-at 2026-12-31T00:00:00Z `
  --activation-key-out C:\secure\handoff\customer-redacted.activation-key
```

The desktop app sends the key once to `LENGRVIS_ACTIVATION_BASE_URL/api/v1/activations`, stores the returned signed license, and then runs offline until expiry/revocation/refresh.

For a commercial activation deployment, `npm run release:safety` also requires redacted evidence labels for:

- `LENGRVIS_ACTIVATION_REVERSE_PROXY_EVIDENCE`
- `LENGRVIS_ACTIVATION_RATE_LIMIT_EVIDENCE`
- `LENGRVIS_ACTIVATION_AUDIT_EVIDENCE`
- `LENGRVIS_ACTIVATION_OPERATIONS_EVIDENCE`

The activation server rejects weak device profiles when `LENGRVIS_ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF=true`. The client profile must show system-protected local secret storage (`dpapi` or `keyring`) and at least one machine-level signal. This is strong local binding, not TPM/vendor attestation; do not market it as hardware attestation until an attestation provider is integrated and evidenced.

## 4. Replace or migrate a license

Issue the new license with `--replaces <old-license-id>`, then revoke the old license with a replacement reference:

```powershell
npm run license:admin -- issue <common issuer arguments> `
  --replaces lic_old `
  --output C:\secure\out\customer-replacement.lic `
  --ledger C:\secure\issuer-ledger.jsonl

npm run license:admin -- revoke `
  --private-key C:\secure\lengrvis-issuer-private.pem `
  --private-key-passphrase-file C:\secure\issuer-passphrase.txt `
  --issuer "Approved contracting entity" `
  --license-id lic_old `
  --reason replacement `
  --replacement-license-id lic_new `
  --ledger C:\secure\issuer-ledger.jsonl `
  --manifest-out C:\secure\out\license-revocations.key
```

Do not revoke the old license until the replacement artifact has been delivered through the approved support channel.

## 5. Refund, chargeback, or administrative revocation

If the subscription was created through the activation server, first revoke the
subscription key in the activation admin UI or with the activation database
runbook. This blocks new activations and surfaces the already activated
`license_id` values. It does not by itself disable already installed offline
licenses; continue with the signed revocation manifest flow below.

```powershell
npm run license:admin -- revoke `
  --private-key C:\secure\lengrvis-issuer-private.pem `
  --private-key-passphrase-file C:\secure\issuer-passphrase.txt `
  --issuer "Approved contracting entity" `
  --license-id lic_customer `
  --reason refund `
  --ledger C:\secure\issuer-ledger.jsonl `
  --manifest-out C:\secure\out\license-revocations.key
```

Valid reasons are `refund`, `chargeback`, `replacement`, `breach`, and `admin`. The command appends a revocation event and republishes the full signed manifest. If publication fails after the ledger append, rebuild it with `publish-revocations`; do not edit the ledger manually.

```powershell
npm run license:admin -- publish-revocations `
  --private-key C:\secure\lengrvis-issuer-private.pem `
  --private-key-passphrase-file C:\secure\issuer-passphrase.txt `
  --issuer "Approved contracting entity" `
  --ledger C:\secure\issuer-ledger.jsonl `
  --manifest-out C:\secure\out\license-revocations.key `
  --force
```

## 6. Deploy revocations

Provision the signed manifest through either:

- `LENGRVIS_LICENSE_REVOCATIONS=<signed manifest token>` for managed deployments; or
- `<data_dir>/license-revocations.key` for an offline installation.

On the next settings resolution or application restart:

- a matching `license_id` becomes `revoked` and paid features fall back to Free;
- a tampered or malformed manifest becomes `revocation_data_invalid` and paid features fail closed;
- a commercial offline paid license without a signed manifest becomes `revocation_required` and paid features fail closed;
- a commercial offline paid license with a stale manifest becomes `revocation_stale` and paid features fail closed; default freshness is seven days and can be tightened with `LENGRVIS_LICENSE_REVOCATION_MAX_AGE_SECONDS`;
- a legacy license without `license_id` remains readable for compatibility but is marked not revocation-capable and must be replaced before production sale.
- a legacy device-bound license without `device_fingerprint` is rejected when `LENGRVIS_COMMERCIAL_RELEASE=true`; customers must re-activate or refresh online to receive a fingerprint-bound replacement token.

Offline revocation is not instantaneous. The customer or deployment administrator must receive and install the updated signed manifest. Do not describe this as online real-time revocation.

## 7. Verify artifacts

```powershell
npm run license:admin -- inspect `
  --public-key C:\secure\lengrvis-issuer-public.key `
  --license C:\secure\out\customer-1042.lic `
  --revocations C:\secure\out\license-revocations.key
```

Inspection prints only safe metadata. A revoked, expired, malformed, or wrongly signed license returns a non-zero exit code.

## Production evidence checklist

- [ ] Named commercial owner and backup operator.
- [ ] Approved contracting entity appears as issuer.
- [ ] Encrypted private key custody and separate passphrase backup reviewed.
- [ ] Public-key fingerprint recorded in RC evidence.
- [ ] `LENGRVIS_COMMERCIAL_RELEASE=true` and public key pass `npm run release:safety`.
- [ ] Test issue, import, replacement, refund revocation, manifest deployment, and Free fallback recorded.
- [ ] Test activation key creation, first activation, repeated activation, device-limit rejection, renewal refresh, cancellation at period end, refund revocation, expiry downgrade, and rate limiting recorded.
- [ ] Activation server uses separate key-hash pepper and device-binding HMAC secret; no production fallback shares those secrets.
- [ ] Activation deployment evidence records HTTPS reverse proxy, rate limiting, safe audit events, operations runbook, and strong device-binding profile enforcement.
- [ ] Offline paid deployments ship a fresh signed revocation manifest and have a tested update channel before paid entitlement is enabled.
- [ ] Ledger backup and restore tested; tamper detection demonstrated.
- [ ] Customer delivery and revocation-manifest update channel approved.

Until every item has named evidence, `MR-P0-004` remains `blocked` in the authoritative market-readiness dashboard.
