export function domClickScript(selector: string): string {
  return `
    (() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element) throw new Error("Selector not found: ${escapeForScriptMessage(selector)}");
      element.scrollIntoView({ block: "center", inline: "center" });
      element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      return true;
    })()
  `;
}

export function domFillScript(selector: string, text: string): string {
  return `
    (() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element) throw new Error("Selector not found: ${escapeForScriptMessage(selector)}");
      element.scrollIntoView({ block: "center", inline: "center" });
      element.focus();
      if (!("value" in element)) throw new Error("Selector is not fillable: ${escapeForScriptMessage(selector)}");
      element.value = ${JSON.stringify(text)};
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()
  `;
}

export function domSubmitScript(selector: string): string {
  return `
    (() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element) throw new Error("Selector not found: ${escapeForScriptMessage(selector)}");
      const form = element instanceof HTMLFormElement ? element : element.closest("form");
      if (!form) throw new Error("No form found for selector: ${escapeForScriptMessage(selector)}");
      form.requestSubmit();
      return true;
    })()
  `;
}

export function domScrollScript(deltaY: number): string {
  return `
    (() => {
      window.scrollBy({ top: ${JSON.stringify(deltaY)}, behavior: "smooth" });
      return { x: window.scrollX, y: window.scrollY };
    })()
  `;
}

export function observeScript(): string {
  return `
    (() => ({
      url: location.href,
      title: document.title,
      text: document.body ? document.body.innerText.slice(0, 4000) : "",
      links: Array.from(document.links).slice(0, 40).map((link) => ({
        text: link.innerText.slice(0, 120),
        url: link.href
      }))
    }))()
  `;
}

function escapeForScriptMessage(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}
