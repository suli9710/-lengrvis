import { describe, expect, it } from "vitest";

import { validateRendererBackendRelativeEndpoint } from "./transport";

describe("renderer backend endpoint decoded character safety", () => {
  it.each(["/api/tasks/task%200", "/api/tasks/task%090", "/api/tasks/task%000"])(
    "rejects unsafe decoded characters in %s",
    (endpoint) => {
      expect(() => validateRendererBackendRelativeEndpoint(endpoint, ["/api"])).toThrow(
        "Renderer backend endpoint contains unsafe decoded characters"
      );
    }
  );

  it("still accepts a normal backend-relative route", () => {
    expect(validateRendererBackendRelativeEndpoint("/api/tasks/task-123", ["/api"])).toBe(
      "/api/tasks/task-123"
    );
  });
});
