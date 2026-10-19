import type { BrowserSession } from "../shared/browserTypes";
import type { BrowserContainer } from "./browserHostRuntime";
import type { BrowserScreenshotStore } from "./browserScreenshotStore";
import type { BrowserProfileRegistry } from "./browserProfileRegistry";
import type { CredentialVault } from "./credentialVault";
import type { CredentialUseTicketBroker } from "./credentialUseTicketBroker";

export async function eraseBrowserPrivateData(context: {
  sessions: Array<{ container: BrowserContainer; session: BrowserSession }>;
  stop: (sessionId: string) => Promise<unknown>;
  screenshotStore: BrowserScreenshotStore;
  browserProfiles: BrowserProfileRegistry;
  credentialTickets: CredentialUseTicketBroker;
  credentialVault: CredentialVault;
}): Promise<void> {
  const failures: unknown[] = [];
  const attempt = async (operation: () => unknown) => {
    try { await operation(); }
    catch (error) { // broad-exception-boundary: collect all participants' failures for a retryable partial wipe.
      failures.push(error);
    }
  };
  for (const entry of context.sessions) {
    const storage = entry.container.view.webContents.session;
    // Destroy the page before clearing storage, so pending navigation cannot
    // recreate cookies or caches after the clear completes.
    await attempt(() => context.stop(entry.session.id));
    await attempt(() => storage.clearStorageData());
    await attempt(() => storage.clearCache());
  }
  await attempt(() => context.screenshotStore.clear());
  await attempt(() => context.browserProfiles.clear());
  await attempt(() => context.credentialTickets.clear());
  await attempt(() => context.credentialVault.clear());
  if (failures.length) throw new Error("浏览器部分数据未能删除，清除预留已保留，请关闭占用程序后重试。");
}
