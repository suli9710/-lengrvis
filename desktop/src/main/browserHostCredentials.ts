import type { BrowserActivityEvent, BrowserSession } from "../shared/browserTypes";
import type { CredentialBrokerResult, CredentialFillRequest, CredentialRef, CredentialRefRequest, CredentialSessionRequest, CredentialUseTicketRequest } from "../shared/credentialTypes";
import { capturePageCredentialScript, credentialPageFingerprintScript, fillPageCredentialScript, parseCapturedPageCredential, parseFilledPageCredentialResult, parsePageCredentialFingerprint } from "./browserCredentialDom";
import type { BrowserCredentialPreview } from "./browserCredentialIpcHandlers";
import { safeCredentialErrorMessage, type BrowserContainer } from "./browserHostRuntime";
import { CredentialVault, credentialDomainFromUrl, normalizeCredentialIdentifier } from "./credentialVault";
import { CredentialUseTicketBroker } from "./credentialUseTicketBroker";
import { BrowserPrivacyBoundary } from "./browserPrivacyBoundary";

interface CredentialSession { container: BrowserContainer; session: BrowserSession; }
interface CredentialContext { entry: CredentialSession; domain: string; taskId: string; }
interface CredentialHost {
  getSession: (id: string) => CredentialSession | undefined;
  addEvent: (entry: CredentialSession, input: Omit<BrowserActivityEvent, "id" | "session_id" | "task_id" | "created_at">) => unknown;
  emitSnapshot: () => void;
}

export class BrowserHostCredentialBroker {
  constructor(private readonly host: CredentialHost, private readonly credentialVault: CredentialVault,
    private readonly credentialTickets: CredentialUseTicketBroker, private readonly boundary: BrowserPrivacyBoundary) {}
  listCredentialRefs(request: CredentialSessionRequest): CredentialRef[] {
    this.boundary.assertAvailable();
    const context = this.credentialContext(request.session_id, false);
    return this.credentialVault.list(context.domain);
  }

  async previewCredentialCapture(request: CredentialSessionRequest): Promise<BrowserCredentialPreview> {
    const release = this.boundary.enter();
    try {
    const context = this.credentialContext(request.session_id, true);
    const page = await this.credentialPageContext(context);
    return {
      domain: context.domain,
      session_id: request.session_id,
      page_fingerprint: page.page_fingerprint,
      task_id: context.taskId
    };
    } finally { release(); }
  }

  async captureCredential(
    request: CredentialSessionRequest,
    preview: BrowserCredentialPreview
  ): Promise<CredentialBrokerResult> {
    const release = this.boundary.enter();
    try {
    try {
      const context = this.credentialContext(request.session_id, true);
      const captured = parseCapturedPageCredential(
        await context.entry.container.view.webContents.executeJavaScript(
          capturePageCredentialScript(preview.domain, preview.page_fingerprint),
          true
        )
      );
      this.assertCredentialPreview(preview, request.session_id, context, captured);
      const credentialRef = this.credentialVault.store(context.domain, {
        username: captured.username,
        password: captured.password
      });
      this.host.addEvent(context.entry, {
        type: "credential.saved",
        ok: true,
        url: context.entry.container.view.webContents.getURL()
      });
      this.host.emitSnapshot();
      return { ok: true, credential_ref: credentialRef };
    } catch (error) { // broad-exception-boundary
      return { ok: false, error: safeCredentialErrorMessage(error) };
    }
    } finally { release(); }
  }

  async previewCredentialUse(request: CredentialUseTicketRequest): Promise<BrowserCredentialPreview> {
    const release = this.boundary.enter();
    try {
    const context = this.credentialContext(request.session_id, true);
    if (request.task_id !== context.taskId) {
      throw new Error("Credential task binding does not match the browser session");
    }
    const credentialRef = this.credentialVault.getRef(request.credential_ref_id);
    if (!credentialRef || credentialRef.domain !== context.domain) {
      throw new Error("Saved credential is unavailable for this domain");
    }
    const page = await this.credentialPageContext(context);
    return {
      domain: context.domain,
      session_id: request.session_id,
      page_fingerprint: page.page_fingerprint,
      task_id: context.taskId,
      credential_ref_id: credentialRef.id,
      run_id: request.run_id,
      purpose: request.purpose,
      ttl_seconds: request.ttl_seconds ?? 60
    };
    } finally { release(); }
  }

  async issueCredentialUseTicket(
    request: CredentialUseTicketRequest,
    preview: BrowserCredentialPreview
  ): Promise<CredentialBrokerResult> {
    const release = this.boundary.enter();
    try {
    try {
      const current = await this.previewCredentialUse(request);
      this.assertCredentialPreview(
        preview,
        request.session_id,
        this.credentialContext(request.session_id, true),
        { origin: current.domain, page_fingerprint: current.page_fingerprint ?? "" }
      );
      const ticket = this.credentialTickets.issue({
        credential_ref_id: preview.credential_ref_id ?? request.credential_ref_id,
        domain: preview.domain,
        session_id: preview.session_id ?? request.session_id,
        page_fingerprint: preview.page_fingerprint ?? "",
        run_id: request.run_id,
        task_id: preview.task_id,
        purpose: request.purpose
      }, request.ttl_seconds);
      return { ok: true, ticket };
    } catch (error) { // broad-exception-boundary
      return { ok: false, error: safeCredentialErrorMessage(error) };
    }
    } finally { release(); }
  }

  async fillCredential(request: CredentialFillRequest): Promise<CredentialBrokerResult> {
    const release = this.boundary.enter();
    try {
    try {
      const context = this.credentialContext(request.session_id, true);
      const page = await this.credentialPageContext(context);
      const ticket = this.credentialTickets.consume(request.ticket, {
        credential_ref_id: request.ticket.credential_ref_id,
        domain: context.domain,
        session_id: request.session_id,
        page_fingerprint: page.page_fingerprint,
        run_id: request.ticket.run_id,
        task_id: context.taskId,
        purpose: request.ticket.purpose
      });
      const credential = this.credentialVault.resolve(ticket.credential_ref_id);
      if (credential.ref.domain !== context.domain) {
        throw new Error("Saved credential is unavailable for this domain");
      }
      const filled = parseFilledPageCredentialResult(
        await context.entry.container.view.webContents.executeJavaScript(
          fillPageCredentialScript(
            context.domain,
            ticket.page_fingerprint,
            credential.secret.username,
            credential.secret.password
          ),
          true
        )
      );
      this.host.addEvent(context.entry, {
        type: "credential.filled",
        ok: true,
        url: context.entry.container.view.webContents.getURL()
      });
      this.host.emitSnapshot();
      return {
        ok: true,
        credential_ref: credential.ref,
        filled_username: filled.filled_username,
        filled_password: filled.filled_password
      };
    } catch (error) { // broad-exception-boundary
      return { ok: false, error: safeCredentialErrorMessage(error) };
    }
    } finally { release(); }
  }

  previewCredentialDelete(request: CredentialRefRequest): BrowserCredentialPreview {
    this.boundary.assertAvailable();
    const context = this.credentialContext(request.session_id, false);
    const credentialRef = this.credentialVault.getRef(request.credential_ref_id);
    if (!credentialRef || credentialRef.domain !== context.domain) {
      throw new Error("Saved credential is unavailable for this domain");
    }
    return {
      domain: context.domain,
      task_id: context.taskId,
      credential_ref_id: credentialRef.id
    };
  }

  deleteCredential(request: CredentialRefRequest): CredentialBrokerResult {
    this.boundary.assertAvailable();
    try {
      this.previewCredentialDelete(request);
      if (!this.credentialVault.delete(request.credential_ref_id)) {
        return { ok: false, error: "Saved credential is unavailable" };
      }
      this.credentialTickets.revokeCredential(request.credential_ref_id);
      return { ok: true };
    } catch (error) { // broad-exception-boundary
      return { ok: false, error: safeCredentialErrorMessage(error) };
    }
  }

  private credentialContext(sessionId: string, requireTask: boolean): {
    entry: CredentialSession;
    domain: string;
    taskId: string;
  } {
    const normalizedSessionId = normalizeCredentialIdentifier(sessionId, "session id");
    const entry = this.host.getSession(normalizedSessionId);
    if (!entry) throw new Error("Browser session is no longer available");
    const currentUrl = entry.container.view.webContents.getURL() || entry.session.current_url;
    const domain = credentialDomainFromUrl(currentUrl);
    const rawTaskId = entry.session.task_id?.trim() ?? "";
    if (requireTask && !rawTaskId) throw new Error("Credential use requires a task-bound browser session");
    const taskId = rawTaskId ? normalizeCredentialIdentifier(rawTaskId, "task id") : "manual";
    return { entry, domain, taskId };
  }

  private async credentialPageContext(
    context: CredentialContext
  ): Promise<{ origin: string; page_fingerprint: string }> {
    const page = parsePageCredentialFingerprint(
      await context.entry.container.view.webContents.executeJavaScript(credentialPageFingerprintScript(), true)
    );
    if (page.origin !== context.domain) throw new Error("Browser page origin changed before credential review");
    return page;
  }

  private assertCredentialPreview(
    preview: BrowserCredentialPreview,
    sessionId: string,
    context: CredentialContext,
    current: { origin: string; page_fingerprint: string }
  ): void {
    if (
      preview.session_id !== sessionId
      || preview.domain !== context.domain
      || preview.task_id !== context.taskId
      || preview.page_fingerprint !== current.page_fingerprint
      || current.origin !== context.domain
    ) {
      throw new Error("Browser page or credential fields changed after confirmation");
    }
  }

}
