# Commercial License Operations

This runbook defines the offline issuer process for Lengrvis Pro and Team licenses. It is operational guidance, not proof that a production issuer key, commercial owner, or live customer workflow has been approved.

## Trust boundary

- The Ed25519 private key stays on a dedicated offline/admin machine or approved HSM-backed signing environment.
- Runtime builds receive only `LENGRVIS_LICENSE_PUBLIC_KEY`.
- Packaged Electron launches force `LENGRVIS_COMMERCIAL_RELEASE=true`; managed Windows services must set it in their approved runtime configuration. In this mode `LENGRVIS_PLAN=pro/team` cannot unlock paid features without a valid, active, non-revoked signed license.
- Never place a private key, passphrase, `LENGRVIS_LICENSE_PRIVATE_KEY`, or deprecated `LENGRVIS_LICENSE_SIGNING_KEY` in `.env`, GitHub Actions runtime secrets, an installer, a diagnostic package, or a customer machine.
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
  --plan team `
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

## 3. Replace or migrate a license

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

## 4. Refund, chargeback, or administrative revocation

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

## 5. Deploy revocations

Provision the signed manifest through either:

- `LENGRVIS_LICENSE_REVOCATIONS=<signed manifest token>` for managed deployments; or
- `<data_dir>/license-revocations.key` for an offline installation.

On the next settings resolution or application restart:

- a matching `license_id` becomes `revoked` and paid features fall back to Free;
- a tampered or malformed manifest becomes `revocation_data_invalid` and paid features fail closed;
- a legacy license without `license_id` remains readable for compatibility but is marked not revocation-capable and must be replaced before production sale.

Offline revocation is not instantaneous. The customer or deployment administrator must receive and install the updated signed manifest. Do not describe this as online real-time revocation.

## 6. Verify artifacts

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
- [ ] Ledger backup and restore tested; tamper detection demonstrated.
- [ ] Customer delivery and revocation-manifest update channel approved.

Until every item has named evidence, `MR-P0-004` remains `in_progress`.
