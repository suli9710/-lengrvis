import vm from "node:vm";

import { describe, expect, it, vi } from "vitest";

import { domClickScript, domFillScript, domScrollScript, domSubmitScript, observeScript } from "./browserHostDomActions";

class FakeEvent {
  constructor(
    readonly type: string,
    readonly init?: Record<string, unknown>
  ) {}
}

class FakeMouseEvent extends FakeEvent {}

function runScript(script: string, context: Record<string, unknown>): unknown {
  return vm.runInNewContext(script, {
    Event: FakeEvent,
    MouseEvent: FakeMouseEvent,
    ...context
  });
}

describe("browserHostDomActions", () => {
  it("clicks the selected element and escapes selectors in failure messages", () => {
    const selector = 'button[data-label="C:\\Temp"]';
    const clickScript = domClickScript(selector);
    const events: FakeEvent[] = [];
    const element = {
      dispatchEvent: vi.fn((event: FakeEvent) => {
        events.push(event);
        return true;
      }),
      scrollIntoView: vi.fn()
    };

    expect(clickScript).toContain('Selector not found: button[data-label=\\"C:\\\\Temp\\"]');

    const result = runScript(clickScript, {
      document: { querySelector: vi.fn(() => element) },
      window: {}
    });

    expect(result).toBe(true);
    expect(element.scrollIntoView).toHaveBeenCalledWith({ block: "center", inline: "center" });
    expect(events[0]?.type).toBe("click");
    expect(events[0]?.init).toMatchObject({ bubbles: true, cancelable: true });
    expect(() =>
      runScript(domClickScript(selector), {
        document: { querySelector: () => null },
        window: {}
      })
    ).toThrow('Selector not found: button[data-label="C:\\Temp"]');
  });

  it("fills values through input and change events", () => {
    const events: string[] = [];
    const element = {
      dispatchEvent: vi.fn((event: FakeEvent) => {
        events.push(event.type);
        return true;
      }),
      focus: vi.fn(),
      scrollIntoView: vi.fn(),
      value: ""
    };

    const result = runScript(domFillScript("#note", "hello\nquoted \"text\""), {
      document: { querySelector: () => element }
    });

    expect(result).toBe(true);
    expect(element.focus).toHaveBeenCalledOnce();
    expect(element.value).toBe("hello\nquoted \"text\"");
    expect(events).toEqual(["input", "change"]);
  });

  it("submits a containing form and fails closed without one", () => {
    class FakeForm {
      requestSubmit = vi.fn();
    }
    const form = new FakeForm();
    const element = { closest: vi.fn(() => form) };

    expect(
      runScript(domSubmitScript("#send"), {
        document: { querySelector: () => element },
        HTMLFormElement: FakeForm
      })
    ).toBe(true);
    expect(form.requestSubmit).toHaveBeenCalledOnce();

    expect(() =>
      runScript(domSubmitScript("#send"), {
        document: { querySelector: () => ({ closest: () => null }) },
        HTMLFormElement: FakeForm
      })
    ).toThrow("No form found for selector: #send");
  });

  it("scrolls and observes a bounded page summary", () => {
    const windowContext = { scrollBy: vi.fn(), scrollX: 0, scrollY: 0 };
    windowContext.scrollBy.mockImplementation(() => {
      windowContext.scrollY = 700;
    });

    expect(
      runScript(domScrollScript(700), {
        window: windowContext
      })
    ).toEqual({ x: 0, y: 700 });
    expect(windowContext.scrollBy).toHaveBeenCalledWith({ top: 700, behavior: "smooth" });

    const observed = runScript(observeScript(), {
      document: {
        body: { innerText: "x".repeat(4100) },
        links: Array.from({ length: 45 }, (_, index) => ({
          href: `https://example.test/${index}`,
          innerText: `Link ${index}`.repeat(20)
        })),
        title: "Observed page"
      },
      location: { href: "https://example.test" }
    }) as { links: Array<{ text: string; url: string }>; text: string; title: string; url: string };

    expect(observed).toMatchObject({
      text: "x".repeat(4000),
      title: "Observed page",
      url: "https://example.test"
    });
    expect(observed.links).toHaveLength(40);
    expect(observed.links[0]?.text.length).toBeLessThanOrEqual(120);
  });
});
