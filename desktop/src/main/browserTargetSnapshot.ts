import type { BrowserAction } from "../shared/browserTypes";
import type { NativeFormSubmission } from "./browserSubmissionGuard";

function resolveField(selector: string): Element {
  let matches = document.querySelectorAll(selector);
  if (matches.length === 0) matches = document.querySelectorAll(`[name=${JSON.stringify(selector)}]`);
  if (matches.length !== 1) throw new Error("Prepared field must match exactly one element");
  return matches[0];
}

function captureNativeSubmission(form: HTMLFormElement, proposed: Map<Element, string>): NativeFormSubmission {
  const charset = (form.acceptCharset || document.characterSet).toLowerCase();
  const baseTarget = document.querySelector("base[target]")?.getAttribute("target") ?? "";
  if (form.enctype !== "application/x-www-form-urlencoded" || !["utf-8", "utf8"].includes(charset) ||
      (!form.target && !["", "_self"].includes(baseTarget))) {
    throw new Error("Combined submission requires a same-window UTF-8 URL-encoded form");
  }
  const entries: [string, string][] = [];
  const normalize = (value: string) => value.replace(/\r\n|\r|\n/g, "\r\n");
  for (const control of Array.from(form.elements)) {
    if (control instanceof HTMLButtonElement || control.tagName === "FIELDSET" || control.tagName === "OUTPUT") continue;
    if (!(control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement || control instanceof HTMLSelectElement) ||
        control.getAttribute("dirname") || (control instanceof HTMLInputElement && control.type === "file") ||
        (control instanceof HTMLTextAreaElement && control.wrap === "hard")) {
      throw new Error("Form contains a control requiring a dedicated submission workflow");
    }
    if (control.matches(":disabled") || !control.name || control.closest("datalist")) continue;
    if (control instanceof HTMLInputElement &&
        (/^(submit|image|reset|button)$/.test(control.type) || (/^(checkbox|radio)$/.test(control.type) && !control.checked))) continue;
    let values: string[];
    if (control instanceof HTMLSelectElement) {
      const selected = proposed.has(control)
        ? Array.from(control.options).filter(option => option.value === proposed.get(control)).slice(0, 1)
        : Array.from(control.selectedOptions);
      if (proposed.has(control) && (!selected.length || selected[0].matches(":disabled"))) throw new Error("Prepared selection is unavailable");
      values = selected.filter(option => !option.matches(":disabled")).map(option => option.value);
    } else {
      values = [control instanceof HTMLInputElement && control.type === "hidden" && control.name === "_charset_"
        ? "UTF-8" : proposed.get(control) ?? control.value];
    }
    for (const value of values) entries.push([normalize(control.name), normalize(value)]);
  }
  if (new URLSearchParams(entries).toString().length > 256 * 1024) throw new Error("Form submission is too large for a concrete confirmation");
  return { url: form.action, entries };
}

// Runs only in the managed isolated world. Raw DOM state remains there; the
// expected native body stays private to Electron. Only the redacted preview and
// fingerprint cross the backend/renderer boundary.
function captureTargets(action: BrowserAction) {
  type Registry = {
    document: Document; documentId: string; next: number;
    nodes: WeakMap<object, number>; states: WeakMap<object, { raw: string; revision: number }>;
  };
  const host = globalThis as typeof globalThis & { __lengrvisTargets?: Registry };
  if (!host.__lengrvisTargets || host.__lengrvisTargets.document !== document) {
    host.__lengrvisTargets = {
      document, documentId: Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-"), next: 0,
      nodes: new WeakMap(), states: new WeakMap()
    };
  }
  const registry = host.__lengrvisTargets;
  const identity = (element: object) => {
    let id = registry.nodes.get(element);
    if (id === undefined) { id = ++registry.next; registry.nodes.set(element, id); }
    return id;
  };
  const fieldState = (element: Element) => {
    const input = element as HTMLInputElement;
    if (input.type === "password" || /credit|card.?number|\bcvv\b|\bcvc\b|\botp\b|2fa|支付密码|验证码/i.test([input.name, input.id, input.autocomplete].join(" "))) {
      throw new Error("Sensitive fields require protected user interaction");
    }
    if (input.type === "file" && input.files?.length) throw new Error("File submission requires a dedicated upload approval");
    return {
      node: identity(element), tag: element.tagName, id: element.id, name: input.name ?? "", type: input.type ?? "",
      value: "value" in element ? String(input.value) : "", checked: Boolean(input.checked),
      disabled: input.disabled === true, readOnly: input.readOnly === true, multiple: input.multiple === true,
      selected: element instanceof HTMLSelectElement ? Array.from(element.selectedOptions).map(option => option.value) : [],
      formAction: element.getAttribute("formaction"), formMethod: element.getAttribute("formmethod"),
      formTarget: element.getAttribute("formtarget"), formEnctype: element.getAttribute("formenctype")
    };
  };
  const combined = action.kind === "submit" && action.fields && Object.keys(action.fields).length > 0;
  const proposed = new Map<Element, string>();
  if (combined) {
    if (!action.selector || Object.keys(action.fields!).length > 32) throw new Error("A bounded explicit form is required");
    for (const [selector, value] of Object.entries(action.fields!)) {
      const node = resolveField(selector);
      if (!(node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement || node instanceof HTMLSelectElement)) throw new Error("Prepared field must be an ordinary input");
      const input = node as HTMLInputElement;
      if (input.matches(":disabled") || !input.name || input.readOnly || /^(hidden|password|file|checkbox|radio|submit|image|reset|button)$/i.test(input.type) || /password|secret|token|credential|验证码|密码/i.test([input.name, input.id, input.autocomplete].join(" "))) throw new Error("Prepared field requires protected user interaction");
      fieldState(input);
      if (proposed.has(input)) throw new Error("Prepared field aliases must not target the same input twice");
      proposed.set(input, String(value));
    }
  }
  const selectors = combined ? [action.selector!] : action.fields ? Object.keys(action.fields).sort() : action.selector ? [action.selector] : [];
  let submission: NativeFormSubmission | null = null;
  if (["click", "fill", "submit"].includes(action.kind) && !selectors.length) throw new Error("A unique browser target is required");
  const targets = selectors.map(selector => {
    const elements = document.querySelectorAll(selector);
    if (elements.length !== 1) throw new Error("Browser target must match exactly one element");
    const element = elements[0] as HTMLElement;
    if (action.kind === "click") {
      const button = element.closest("button,input") as HTMLButtonElement | HTMLInputElement | null;
      if (button?.form && ["submit", "image"].includes(button.type)) {
        throw new Error("Use an explicit submit action to preview and approve a form submission");
      }
    }
    const input = element as HTMLInputElement;
    // Form named properties may expose an input called "disabled" here.
    if (!element.isConnected || input.disabled === true) throw new Error("Browser target is not available");
    if (action.kind === "fill" && /password|token|secret|\bpwd\b|密码/i.test([input.name, input.id, input.type, element.getAttribute("aria-label")].join(" "))) {
      throw new Error("Sensitive fields require protected user interaction");
    }
    const ancestors: number[] = [];
    let parent = element.parentElement;
    for (let depth = 0; parent && depth < 32; depth++, parent = parent.parentElement) ancestors.push(identity(parent));
    const form = element instanceof HTMLFormElement ? element : input.form ?? element.closest("form");
    if (combined && (!(element instanceof HTMLFormElement) || !form || form.method.toLowerCase() !== "post" || !form.getAttribute("action") || !/^https?:$/.test(new URL(form.action).protocol) || !["", "_self"].includes(form.target) || Array.from(proposed.keys()).some(node => (node as HTMLInputElement).form !== form))) {
      throw new Error("Combined submission requires one explicit same-window native POST form");
    }
    if (combined) submission = captureNativeSubmission(form!, proposed);
    const raw = {
      ...fieldState(element), ancestors, text: (element.innerText ?? "").slice(0, 2000),
      href: element.getAttribute("href"), role: element.getAttribute("role"),
      form: form ? {
        node: identity(form), action: form.action, method: form.method, enctype: form.enctype,
        target: form.target, noValidate: form.noValidate,
        controls: Array.from(form.elements).map(fieldState)
      } : null
    };
    const serialized = JSON.stringify(raw);
    const previous = registry.states.get(element);
    const revision = previous ? previous.revision + (previous.raw === serialized ? 0 : 1) : 1;
    registry.states.set(element, { raw: serialized, revision });
    const publicField = (control: ReturnType<typeof fieldState>) => ({
      label: control.name || control.id || control.tag, type: control.type,
      content: /password|secret|token|cookie|csrf|credential|otp|2fa/i.test(control.name + " " + control.id)
        ? "[protected]" : Array.from(proposed.entries()).find(([node]) => identity(node) === control.node)?.[1] ?? control.value,
      checked: control.checked, disabled: control.disabled
    });
    const preview = {
      label: element.getAttribute("aria-label") || raw.text || input.name || element.id,
      destination: form?.action || element.getAttribute("href") || location.href,
      method: form?.method || "", items: raw.form ? raw.form.controls.map(publicField) : [publicField(raw)]
    };
    if (JSON.stringify(preview).length > 65536) throw new Error("Form content is too large for a concrete confirmation; review it manually");
    return { selector, node: identity(element), revision, preview };
  });
  return { documentId: registry.documentId, origin: location.origin, url: location.href, title: document.title, targets, submission };
}

export function browserTargetSnapshotScript(action: BrowserAction): string {
  return `(() => { const resolveField = ${resolveField.toString()}; const captureNativeSubmission = ${captureNativeSubmission.toString()}; return (${captureTargets.toString()})(${JSON.stringify(action)}); })()`;
}

export function guardedBrowserActionScript(action: BrowserAction, expected: unknown): string {
  return `(() => {
    const action = ${JSON.stringify(action)};
    const resolveField = ${resolveField.toString()};
    const captureNativeSubmission = ${captureNativeSubmission.toString()};
    const observed = (${captureTargets.toString()})(action);
    if (JSON.stringify(observed) !== JSON.stringify(${JSON.stringify(expected)})) throw new Error("Browser target changed after approval");
    const elements = observed.targets.map(target => document.querySelector(target.selector));
    if (action.kind === "fill") {
      elements.forEach((element, index) => {
        if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) throw new Error("Target is not a supported input");
        const value = action.fields ? action.fields[observed.targets[index].selector] : action.text;
        const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, String(value ?? ""));
      });
      elements.forEach(element => {
        if (!element.isConnected) throw new Error("Browser target was replaced during input");
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
      });
    } else if (action.kind === "click") {
      elements[0].click();
    } else if (action.kind === "submit") {
      const element = elements[0];
      const form = element instanceof HTMLFormElement ? element : element.form ?? element.closest("form");
      if (!form) throw new Error("Target form is unavailable");
      if (action.fields && Object.keys(action.fields).length) {
        const originals = [];
        for (const [selector, value] of Object.entries(action.fields)) {
          const input = resolveField(selector);
          const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : input instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
          originals.push([input, prototype, input.value]);
          Object.getOwnPropertyDescriptor(prototype, "value").set.call(input, String(value));
          if (input.value !== String(value)) {
            originals.forEach(([node, proto, old]) => Object.getOwnPropertyDescriptor(proto, "value").set.call(node, old));
            return { ok: false, commit_state: "not_submitted", error: "The field did not accept its approved value" };
          }
        }
        if (Array.from(form.elements).some(input => input.willValidate && !input.validity.valid)) {
          originals.forEach(([node, proto, old]) => Object.getOwnPropertyDescriptor(proto, "value").set.call(node, old));
          return { ok: false, commit_state: "not_submitted", error: "The completed form did not pass native validation" };
        }
        HTMLFormElement.prototype.submit.call(form);
        return { ok: true, commit_state: "submitted" };
      }
      const submitter = element instanceof HTMLButtonElement && element.type === "submit" || element instanceof HTMLInputElement && ["submit", "image"].includes(element.type);
      form.requestSubmit(submitter ? element : undefined);
    } else if (action.kind === "scroll") {
      window.scrollBy(0, Number(action.delta_y ?? 700));
    } else {
      throw new Error("Unsupported authorized browser mutation");
    }
    return true;
  })()`;
}
