// Renderer modules reference `window` lazily inside functions; provide a
// minimal stub so pure-function tests can import them in a node environment.
const globals = globalThis as Record<string, unknown>;
if (typeof globals.window === "undefined") {
  globals.window = {};
}
