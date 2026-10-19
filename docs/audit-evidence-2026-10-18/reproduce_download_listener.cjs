// Review harness: two tabs sharing one Electron Session partition.
// Each hardenEmbeddedWebContents() call registers a session-level will-download
// listener; destroying a webContents only removes its request route.
const { EventEmitter } = require("node:events");
const { hardenEmbeddedWebContents } = require("./hardening.cjs");

class FakeSession extends EventEmitter {
  constructor() {
    super();
    this.webRequest = { onBeforeRequest: () => undefined };
    this.permissionHandlers = [];
  }
  setPermissionRequestHandler(handler) { this.permissionHandlers.push(handler); }
  setPermissionCheckHandler() {}
}

class FakeWebContents extends EventEmitter {
  constructor(id, session) {
    super();
    this.id = id;
    this.session = session;
  }
  setWindowOpenHandler() {}
  setAudioMuted() {}
}

const session = new FakeSession();
const first = new FakeWebContents(1, session);
const second = new FakeWebContents(2, session);
hardenEmbeddedWebContents(first);
hardenEmbeddedWebContents(second);
const before = session.listenerCount("will-download");
first.emit("destroyed");
second.emit("destroyed");
const after = session.listenerCount("will-download");
console.log(`will_download_listeners_before_destroy=${before}`);
console.log(`will_download_listeners_after_destroy=${after}`);
console.log(after === 0 ? "NO_LEAK" : "LISTENER_LEAK_CONFIRMED");
