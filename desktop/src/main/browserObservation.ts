import type { BrowserAction } from "../shared/browserTypes";
import { BROWSER_SENSITIVE_FIELD_PATTERN } from "./browserSensitiveFields";

function observePage(action: BrowserAction, sensitivePattern: string) {
  const sensitive = new RegExp(sensitivePattern, "i");
  const isSensitive = (element: Element) => {
    const field = element as HTMLInputElement;
    return field.type === "hidden" || field.type === "password" || sensitive.test([field.name, field.id, field.type, element.getAttribute("autocomplete"), element.getAttribute("aria-label")].join(" "));
  };
  let source = document.body?.innerText ?? "";
  if (action.selector) {
    const selected = document.querySelectorAll(action.selector);
    if (selected.length !== 1 || isSensitive(selected[0])) throw new Error("Readable target must be unique and non-sensitive");
    const field = selected[0] as HTMLInputElement;
    source = "value" in field ? String(field.value) : (selected[0] as HTMLElement).innerText;
  }
  const maximum = Math.min(65536, Math.max(1, Number(action.max_chars ?? 4000)));
  const extracted: Record<string, { text: string; matched: boolean; truncated: boolean }> = Object.create(null);
  const requested = Object.entries(action.selectors ?? {});
  if (requested.length > 32) throw new Error("Too many observation fields");
  for (const [label, selector] of requested) {
    if (typeof selector !== "string" || selector.length > 2048) throw new Error("Invalid observation selector");
    const nodes = document.querySelectorAll(selector);
    if (nodes.length !== 1 || isSensitive(nodes[0])) {
      extracted[label] = { text: "", matched: false, truncated: false }; continue;
    }
    const node = nodes[0] as HTMLInputElement;
    const text = "value" in node ? String(node.value) : String((node as HTMLElement).innerText ?? "");
    extracted[label] = { text: text.slice(0, maximum), matched: true, truncated: text.length > maximum };
  }
  const controls = Array.from(document.querySelectorAll("input,textarea,select,button,[role=button]")).filter(element => !isSensitive(element)).slice(0, 80).flatMap(element => {
    const field = element as HTMLInputElement;
    const selector = element.id ? `#${CSS.escape(element.id)}` : field.name ? `${element.tagName.toLowerCase()}[name=${JSON.stringify(field.name)}]` : "";
    if (!selector || document.querySelectorAll(selector).length !== 1) return [];
    return [{ selector, tag: element.tagName, name: element.getAttribute("aria-label") || field.name || (element as HTMLElement).innerText.slice(0, 120),
      type: field.type || "", value: "value" in field ? String(field.value).slice(0, 1000) : "", disabled: Boolean(field.disabled) }];
  });
  const elements = Array.from(document.querySelectorAll("[id]")).filter(node => !isSensitive(node) && !["SCRIPT", "STYLE"].includes(node.tagName)).slice(0, 60).flatMap(node => {
    const selector = `#${CSS.escape(node.id)}`;
    if (document.querySelectorAll(selector).length !== 1) return [];
    const text = (node as HTMLElement).innerText ?? "";
    return text.trim() ? [{ selector, tag: node.tagName, text: text.slice(0, 250), truncated: text.length > 250 }] : [];
  });
  return { url: location.href, title: document.title, text: source.slice(0, maximum), truncated: source.length > maximum, controls, extracted, elements,
    links: Array.from(document.links).slice(0, 40).map(link => ({ text: link.innerText.slice(0, 120), url: link.href })) };
}

export function browserObservationScript(action: BrowserAction): string {
  return `(${observePage.toString()})(${JSON.stringify(action)}, ${JSON.stringify(BROWSER_SENSITIVE_FIELD_PATTERN)})`;
}
