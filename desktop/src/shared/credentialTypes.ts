export const CREDENTIAL_REF_SCHEMA_VERSION = "credential-ref-v1" as const;
export const CREDENTIAL_USE_TICKET_SCHEMA_VERSION = "credential-use-ticket-v1" as const;

export type CredentialPurpose = "sign-in";

/** Public metadata for a password stored only in the Electron main process. */
export interface CredentialRef {
  schema_version: typeof CREDENTIAL_REF_SCHEMA_VERSION;
  id: string;
  domain: string;
  kind: "password";
  created_at: string;
  updated_at: string;
}

/** Short-lived, single-use capability. It intentionally contains no credential plaintext. */
export interface CredentialUseTicket {
  schema_version: typeof CREDENTIAL_USE_TICKET_SCHEMA_VERSION;
  ticket_id: string;
  credential_ref_id: string;
  domain: string;
  session_id: string;
  page_fingerprint: string;
  run_id: string;
  task_id: string;
  purpose: CredentialPurpose;
  issued_at: string;
  expires_at: string;
  nonce: string;
}

export interface CredentialSessionRequest {
  session_id: string;
}

export interface CredentialRefRequest extends CredentialSessionRequest {
  credential_ref_id: string;
}

export interface CredentialUseTicketRequest extends CredentialRefRequest {
  run_id: string;
  task_id: string;
  purpose: CredentialPurpose;
  ttl_seconds?: number;
}

export interface CredentialFillRequest extends CredentialSessionRequest {
  ticket: CredentialUseTicket;
}

export interface CredentialBrokerResult {
  ok: boolean;
  credential_ref?: CredentialRef;
  ticket?: CredentialUseTicket;
  filled_username?: boolean;
  filled_password?: boolean;
  error?: string;
}
