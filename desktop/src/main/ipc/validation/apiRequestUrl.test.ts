import { describe, expect, it } from "vitest";

import { validateApiEndpoint } from "./apiRequestUrl";

describe("validateApiEndpoint decoded character safety", () => {
  it.each(["/api/tasks/task%200", "/api/tasks/task%090", "/api/tasks/task%000"])(
    "rejects unsafe decoded characters in %s",
    (endpoint) => {
      expect(() => validateApiEndpoint(endpoint, "GET")).toThrow(
        "Renderer API endpoint contains unsafe decoded characters"
      );
    }
  );

  it("still accepts an allowlisted dynamic route with a normal identifier", () => {
    expect(validateApiEndpoint("/api/tasks/task-123", "GET")).toBe("/api/tasks/task-123");
  });
});
