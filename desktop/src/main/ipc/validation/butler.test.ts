import { describe, expect, it } from "vitest";
import { validateButlerMessage, validateButlerResponse, validateButlerScope, validateButlerOpenArtifact } from "./butler";

describe("butler IPC contracts", () => {
  it("opens versioned artifact IDs and rejects caller-selected paths and applications", () => {
    const request = { matter_id: "matter_a", artifact_id: "artifact_a", version: 3 };
    expect(validateButlerOpenArtifact(request)).toEqual(request);
    expect(() => validateButlerOpenArtifact({ ...request, path: "C:/other.xlsx" })).toThrow();
    expect(() => validateButlerOpenArtifact({ ...request, application: "other" })).toThrow();
    expect(() => validateButlerOpenArtifact({ ...request, version: 0 })).toThrow();
  });
  it("preserves a multiline followup and its selected matter", () => {
    expect(validateButlerMessage({ message: "修改报告\n保留原始数据", mode: "efficiency", matter_id: "matter_123" })).toMatchObject({ matter_id: "matter_123", message: "修改报告\n保留原始数据" });
  });
  it("requires versioned interactions and rejects extra authority fields", () => {
    expect(() => validateButlerResponse({ matter_id: "matter_123", interaction_id: "input_123", message: "tomorrow" })).toThrow();
    expect(() => validateButlerMessage({ message: "run", approved: true })).toThrow();
  });
  it("normalizes exact origins and rejects credentials and wildcards", () => {
    expect(validateButlerScope({ label: "site", origins: ["https://example.com:443/"] }).origins).toEqual(["https://example.com"]);
    expect(() => validateButlerScope({ label: "site", origins: ["https://user:secret@example.com"] })).toThrow();
    expect(() => validateButlerScope({ label: "site", origins: ["https://*.example.com"] })).toThrow();
  });
});
