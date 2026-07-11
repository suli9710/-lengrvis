const assert = require("node:assert/strict");
const fs = require("node:fs");

const { loadTsModule, mobilePath } = require("./behavior-smoke-helpers.cjs");

async function main() {
  const records = new Map();
  const store = {
    getItemAsync: async (key) => records.get(key) ?? null,
    setItemAsync: async (key, value) => records.set(key, value),
    deleteItemAsync: async (key) => records.delete(key),
  };
  const consent = loadTsModule(mobilePath("src/store/consent.ts"), {
    require: (id) => (id === "expo-secure-store" ? store : require(id)),
  });

  assert.equal((await consent.loadConsentState()).needsConsent, true);
  await consent.acceptConsent({ eula: true, privacy: true });

  const accepted = await consent.loadConsentState();
  assert.equal(accepted.eulaAccepted, true);
  assert.equal(accepted.privacyAccepted, true);
  assert.equal(accepted.needsConsent, false);
  assert.match(accepted.eulaAcceptedAt, /^\d{4}-\d{2}-\d{2}T/);
  assert.match(accepted.privacyAcceptedAt, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(records.get("lengrvis.mobile.consent.eula_version"), "v1.0");
  assert.equal(records.get("lengrvis.mobile.consent.privacy_version"), "v1.2");
  assert.equal(records.get("lengrvis.mobile.consent.eula_accepted_at"), accepted.eulaAcceptedAt);
  assert.equal(records.get("lengrvis.mobile.consent.privacy_accepted_at"), accepted.privacyAcceptedAt);

  await consent.clearConsent();
  const cleared = await consent.loadConsentState();
  assert.equal(cleared.needsConsent, true);
  assert.equal(records.size, 0, "clearing consent must also clear its timestamp records");

  const screenSource = fs.readFileSync(mobilePath("src/screens/ConsentScreen.tsx"), "utf8");
  assert.match(screenSource, /MOBILE_LEGAL_VERSIONS/, "the mobile screen must show the stored legal document versions");
  assert.match(screenSource, /accessibilityRole="link"/, "the mobile screen must expose complete legal-document links");
  assert.match(screenSource, /Linking\.openURL\(MOBILE_LEGAL_DOCUMENT_URLS\[document\]\)/, "complete legal documents must be directly openable on the phone");
  assert.match(screenSource, /accessibilityRole="checkbox"/, "EULA and privacy acknowledgement must be separate explicit controls");
  assert.match(screenSource, /Expo/, "the privacy notice must disclose approval push routing through Expo when enabled");
  assert.doesNotMatch(screenSource, /BSL.*开源|BSL.*\\u5f00\\u6e90/, "the consent screen must not mislabel BUSL as an open-source BSL license");

  const legalDocuments = loadTsModule(mobilePath("src/legalDocuments.ts"), {
    require: (id) => id === "./store/consent" ? consent : require(id),
  });
  assert.match(legalDocuments.MOBILE_LEGAL_DOCUMENT_URLS.eula, /\/blob\/v0\.1\.2\/docs\/legal\/eula\.md$/);
  assert.match(legalDocuments.MOBILE_LEGAL_DOCUMENT_URLS.privacy, /\/blob\/v0\.1\.2\/docs\/legal\/privacy-policy\.md$/);
  assert.doesNotMatch(legalDocuments.MOBILE_LEGAL_DOCUMENT_URLS.eula, /\/(?:main|master)\//, "consent text must not point at a moving branch");
}

main()
  .then(() => console.log("Mobile consent smoke passed"))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
