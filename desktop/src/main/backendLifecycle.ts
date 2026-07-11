/**
 * Serializes backend process lifecycle transitions without coupling the policy
 * to Electron or child-process primitives.
 */
export class BackendLifecycleCoordinator<T> {
  private startInFlight: Promise<T> | null = null;
  private stopInFlight: Promise<T> | null = null;

  constructor(
    private readonly startOperation: () => Promise<T>,
    private readonly stopOperation: () => Promise<T>
  ) {}

  start(): Promise<T> {
    if (this.stopInFlight) {
      return this.stopInFlight.then(() => this.start());
    }
    if (this.startInFlight) {
      return this.startInFlight;
    }

    const pending = Promise.resolve().then(this.startOperation);
    this.startInFlight = pending;
    pending.then(
      () => this.clearStart(pending),
      () => this.clearStart(pending)
    );
    return pending;
  }

  stop(): Promise<T> {
    if (this.stopInFlight) {
      return this.stopInFlight;
    }

    const pendingStart = this.startInFlight;
    const pending = (async () => {
      if (pendingStart) {
        try {
          await pendingStart;
        } catch {
          // A failed start can still leave a child process behind; always run
          // the stop operation to reconcile and terminate what was created.
        }
      }
      return this.stopOperation();
    })();
    this.stopInFlight = pending;
    pending.then(
      () => this.clearStop(pending),
      () => this.clearStop(pending)
    );
    return pending;
  }

  private clearStart(pending: Promise<T>): void {
    if (this.startInFlight === pending) {
      this.startInFlight = null;
    }
  }

  private clearStop(pending: Promise<T>): void {
    if (this.stopInFlight === pending) {
      this.stopInFlight = null;
    }
  }
}
