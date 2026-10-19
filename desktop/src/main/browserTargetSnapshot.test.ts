import { webcrypto } from "node:crypto";
import { createContext, runInContext } from "node:vm";
import { describe, expect, it } from "vitest";
import { browserTargetSnapshotScript, guardedBrowserActionScript } from "./browserTargetSnapshot";

class Element {
  tagName = "DIV"; id = ""; name = ""; type = ""; innerText = ""; isConnected = true;
  disabled = false; parentElement: Element | null = null; attrs: Record<string, string> = {};
  form: Form | null = null;
  getAttribute(name: string) { return this.attrs[name] ?? null; }
  closest() { return this.form; }
  click() {}
}
class Input extends Element { tagName = "INPUT"; value = "100"; type = "hidden"; }
class Form extends Element {
  tagName = "FORM"; action = "https://example.com/commit"; method = "post"; enctype = "application/x-www-form-urlencoded";
  target = ""; noValidate = false; elements: Element[] = []; submitted = false;
  requestSubmit() { this.submitted = true; }
}
class Select extends Element { selectedOptions: { value: string }[] = []; }

function fixture() {
  const form = new Form(); form.id = "order";
  const amount = new Input(); amount.name = "amount"; amount.form = form;
  form.elements = [amount];
  const document = { title: "Review", querySelectorAll: () => [form], querySelector: () => form };
  const context = createContext({ document, location: { origin: "https://example.com", href: "https://example.com/review" },
    crypto: webcrypto, Uint32Array, HTMLInputElement: Input, HTMLTextAreaElement: class extends Element {},
    HTMLSelectElement: Select, HTMLFormElement: Form, HTMLButtonElement: class extends Element {} });
  return { form, amount, document, context };
}

it("requires explicit submission instead of clicking a native submit button", () => {
  const { form, document, context } = fixture();
  const button = new Input();
  button.type = "submit";
  button.form = form;
  button.closest = () => button as unknown as Form;
  document.querySelectorAll = () => [button as unknown as Form];
  expect(() => runInContext(browserTargetSnapshotScript({ kind: "click", selector: "#submit" }), context)).toThrow("explicit submit");
  expect(form.submitted).toBe(false);
});

describe("approved browser target snapshots", () => {
  it.each(["amount", "action", "method", "replacement"])("blocks changed form %s before submission", (change) => {
    const { form, amount, document, context } = fixture();
    const action = { kind: "submit" as const, selector: "#order" };
    const snapshot = runInContext(browserTargetSnapshotScript(action), context);
    expect(snapshot.targets[0]).not.toHaveProperty("value");
    if (change === "amount") amount.value = "999";
    if (change === "action") form.action = "https://example.com/different";
    if (change === "method") form.method = "get";
    if (change === "replacement") {
      const replacement = new Form(); replacement.id = "order"; replacement.elements = [amount];
      document.querySelectorAll = () => [replacement]; document.querySelector = () => replacement;
    }
    expect(() => runInContext(guardedBrowserActionScript(action, snapshot), context)).toThrow("changed");
    expect(form.submitted).toBe(false);
  });
  it("submits an unchanged complete form", () => {
    const { form, context } = fixture();
    const action = { kind: "submit" as const, selector: "#order" };
    const snapshot = runInContext(browserTargetSnapshotScript(action), context);
    expect(runInContext(guardedBrowserActionScript(action, snapshot), context)).toBe(true);
    expect(form.submitted).toBe(true);
  });
  it("shows exact business values while keeping hidden anti-forgery secrets private", () => {
    const { form, context } = fixture();
    const csrf = new Input(); csrf.name = "csrf_token"; csrf.value = "private-request-proof";
    form.elements.push(csrf);
    const snapshot = runInContext(browserTargetSnapshotScript({ kind: "submit", selector: "#order" }), context);
    expect(snapshot.targets[0].preview.items[0]).toMatchObject({ label: "amount", content: "100" });
    expect(snapshot.targets[0].preview.items[1].content).toBe("[protected]");
    expect(JSON.stringify(snapshot)).not.toContain(csrf.value);
  });
});
