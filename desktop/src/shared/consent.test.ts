import { describe, expect, it } from "vitest";

import {
  LEGAL_VERSIONS,
  mergeConsentRecord,
  needsEulaReconsent,
  needsPrivacyReconsent,
  type ConsentRecord
} from "./consent";

describe("legal consent records", () => {
  it("does not infer EULA acceptance from privacy acceptance", () => {
    const record = mergeConsentRecord(
      null,
      {
        privacy_version: LEGAL_VERSIONS.privacy,
        privacy_accepted_at: "2026-06-24T00:00:00.000Z"
      },
      "win32"
    );

    expect(record.eula_accepted_at).toBeNull();
    expect(needsEulaReconsent(record)).toBe(true);
    expect(needsPrivacyReconsent(record)).toBe(false);
  });

  it("preserves independent acceptance timestamps when one document changes", () => {
    const existing: ConsentRecord = {
      eula_version: LEGAL_VERSIONS.eula,
      eula_accepted_at: "2026-06-20T00:00:00.000Z",
      privacy_version: "v1.0",
      privacy_accepted_at: "2026-06-20T00:00:00.000Z",
      installer_version: "0.1.0",
      platform: "win32"
    };

    const record = mergeConsentRecord(
      existing,
      {
        privacy_version: LEGAL_VERSIONS.privacy,
        privacy_accepted_at: "2026-06-24T00:00:00.000Z"
      },
      "win32"
    );

    expect(record.eula_accepted_at).toBe(existing.eula_accepted_at);
    expect(record.privacy_accepted_at).toBe("2026-06-24T00:00:00.000Z");
    expect(needsEulaReconsent(record)).toBe(false);
    expect(needsPrivacyReconsent(record)).toBe(false);
  });
});
