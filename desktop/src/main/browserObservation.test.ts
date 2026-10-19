import { createContext, runInContext } from "node:vm";
import { describe, expect, it } from "vitest";
import { browserObservationScript } from "./browserObservation";

describe("browser observation sensitive fields", () => {
  it.each(["ssn", "pin", "totp", "cc-number"])("withholds %s from explicit reads and the control inventory", id => {
    const field = { id, name: id, type: "text", value: "PRIVATE_VALUE", tagName: "INPUT",
      getAttribute: (key: string) => key === "autocomplete" && id === "cc-number" ? id : null };
    const context = createContext({
      document: { body: { innerText: "public" }, links: [], querySelectorAll: () => [field] },
      location: { href: "https://example.test/" }, CSS: { escape: (value: string) => value }
    });
    expect(() => runInContext(browserObservationScript({ kind: "observe", selector: `#${id}` }), context)).toThrow("non-sensitive");
    const result = runInContext(browserObservationScript({ kind: "observe", selectors: { value: `#${id}` } }), context);
    expect(JSON.stringify(result)).not.toContain("PRIVATE_VALUE");
    expect(result.extracted.value.matched).toBe(false);
    expect(result.controls).toHaveLength(0);
  });
});
