import { randomBytes, randomUUID } from "node:crypto";

import {
  CREDENTIAL_USE_TICKET_SCHEMA_VERSION,
  type CredentialPurpose,
  type CredentialUseTicket
} from "../shared/credentialTypes";
import { normalizeCredentialDomain, normalizeCredentialIdentifier } from "./credentialVault";

export const DEFAULT_CREDENTIAL_TICKET_TTL_SECONDS = 60;
export const MAX_CREDENTIAL_TICKET_TTL_SECONDS = 120;

export interface CredentialTicketBinding {
  credential_ref_id: string;
  domain: string;
  session_id: string;
  page_fingerprint: string;
  run_id: string;
  task_id: string;
  purpose: CredentialPurpose;
}

export interface CredentialUseTicketBrokerOptions {
  now?: () => Date;
  randomId?: () => string;
  randomNonce?: () => string;
}

/** In-memory capability broker. Restarting the app invalidates every outstanding ticket. */
export class CredentialUseTicketBroker {
  private readonly tickets = new Map<string, CredentialUseTicket>();
  private readonly now: () => Date;
  private readonly randomId: () => string;
  private readonly randomNonce: () => string;

  constructor(options: CredentialUseTicketBrokerOptions = {}) {
    this.now = options.now ?? (() => new Date());
    this.randomId = options.randomId ?? randomUUID;
    this.randomNonce = options.randomNonce ?? (() => randomBytes(24).toString("base64url"));
  }

  issue(binding: CredentialTicketBinding, ttlSeconds = DEFAULT_CREDENTIAL_TICKET_TTL_SECONDS): CredentialUseTicket {
    const normalized = normalizeBinding(binding);
    if (!Number.isInteger(ttlSeconds) || ttlSeconds < 1 || ttlSeconds > MAX_CREDENTIAL_TICKET_TTL_SECONDS) {
      throw new Error(`Credential use ticket TTL must be between 1 and ${MAX_CREDENTIAL_TICKET_TTL_SECONDS} seconds`);
    }
    this.pruneExpired();
    const issued = this.now();
    const ticket: CredentialUseTicket = {
      schema_version: CREDENTIAL_USE_TICKET_SCHEMA_VERSION,
      ticket_id: `ctkt_${this.randomId().replace(/-/g, "")}`,
      ...normalized,
      issued_at: issued.toISOString(),
      expires_at: new Date(issued.getTime() + ttlSeconds * 1000).toISOString(),
      nonce: this.randomNonce()
    };
    this.tickets.set(ticket.ticket_id, ticket);
    return { ...ticket };
  }

  consume(ticket: CredentialUseTicket, expected: CredentialTicketBinding): CredentialUseTicket {
    const normalizedTicket = normalizeTicket(ticket);
    const stored = this.tickets.get(normalizedTicket.ticket_id);
    if (!stored) throw new Error("Credential use ticket is invalid or already consumed");
    if (!ticketEquals(stored, normalizedTicket)) throw new Error("Credential use ticket binding does not match");
    if (Date.parse(stored.expires_at) <= this.now().getTime()) {
      this.tickets.delete(stored.ticket_id);
      throw new Error("Credential use ticket has expired");
    }
    const normalizedExpected = normalizeBinding(expected);
    if (!bindingEquals(stored, normalizedExpected)) {
      throw new Error("Credential use ticket is not valid for this browser context");
    }

    // Synchronous delete is the atomic claim; all decrypt/fill work happens afterwards.
    this.tickets.delete(stored.ticket_id);
    return { ...stored };
  }

  revokeCredential(credentialRefId: string): void {
    const normalized = normalizeCredentialIdentifier(credentialRefId, "credential ref id");
    for (const [ticketId, ticket] of this.tickets) {
      if (ticket.credential_ref_id === normalized) this.tickets.delete(ticketId);
    }
  }

  clear(): void {
    this.tickets.clear();
  }

  private pruneExpired(): void {
    const now = this.now().getTime();
    for (const [ticketId, ticket] of this.tickets) {
      if (Date.parse(ticket.expires_at) <= now) this.tickets.delete(ticketId);
    }
  }
}

function normalizeBinding(binding: CredentialTicketBinding): CredentialTicketBinding {
  return {
    credential_ref_id: normalizeCredentialIdentifier(binding.credential_ref_id, "credential ref id"),
    domain: normalizeCredentialDomain(binding.domain),
    session_id: normalizeCredentialIdentifier(binding.session_id, "session id"),
    page_fingerprint: normalizePageFingerprint(binding.page_fingerprint),
    run_id: normalizeCredentialIdentifier(binding.run_id, "run id"),
    task_id: normalizeCredentialIdentifier(binding.task_id, "task id"),
    purpose: binding.purpose === "sign-in" ? "sign-in" : invalidPurpose()
  };
}

function normalizeTicket(ticket: CredentialUseTicket): CredentialUseTicket {
  if (!ticket || typeof ticket !== "object" || Array.isArray(ticket)) throw new Error("Credential use ticket is invalid");
  if (Object.getOwnPropertySymbols(ticket).length) throw new Error("Credential use ticket is invalid");
  const descriptors = Object.getOwnPropertyDescriptors(ticket);
  if (Object.values(descriptors).some((descriptor) => descriptor.get || descriptor.set)) {
    throw new Error("Credential use ticket is invalid");
  }
  const record = Object.fromEntries(
    Object.entries(descriptors).map(([key, descriptor]) => [key, descriptor.value])
  ) as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  const expectedKeys = [
    "credential_ref_id",
    "domain",
    "expires_at",
    "issued_at",
    "nonce",
    "page_fingerprint",
    "purpose",
    "run_id",
    "schema_version",
    "session_id",
    "task_id",
    "ticket_id"
  ].sort();
  if (keys.length !== expectedKeys.length || keys.some((key, index) => key !== expectedKeys[index])) {
    throw new Error("Credential use ticket is invalid");
  }
  if (record.schema_version !== CREDENTIAL_USE_TICKET_SCHEMA_VERSION) throw new Error("Credential use ticket is invalid");
  const issuedAt = normalizeTimestamp(record.issued_at);
  const expiresAt = normalizeTimestamp(record.expires_at);
  const nonce = typeof record.nonce === "string" ? record.nonce : "";
  if (!/^[A-Za-z0-9_-]{24,128}$/.test(nonce)) throw new Error("Credential use ticket is invalid");
  return {
    schema_version: CREDENTIAL_USE_TICKET_SCHEMA_VERSION,
    ticket_id: normalizeCredentialIdentifier(record.ticket_id, "ticket id"),
    ...normalizeBinding({
      credential_ref_id: String(record.credential_ref_id ?? ""),
      domain: String(record.domain ?? ""),
      session_id: String(record.session_id ?? ""),
      page_fingerprint: String(record.page_fingerprint ?? ""),
      run_id: String(record.run_id ?? ""),
      task_id: String(record.task_id ?? ""),
      purpose: record.purpose as CredentialPurpose
    }),
    issued_at: issuedAt,
    expires_at: expiresAt,
    nonce
  };
}

function normalizeTimestamp(value: unknown): string {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) throw new Error("Credential use ticket is invalid");
  return new Date(value).toISOString();
}

function ticketEquals(left: CredentialUseTicket, right: CredentialUseTicket): boolean {
  return Object.keys(left).every((key) => left[key as keyof CredentialUseTicket] === right[key as keyof CredentialUseTicket]);
}

function bindingEquals(left: CredentialUseTicket, right: CredentialTicketBinding): boolean {
  return left.credential_ref_id === right.credential_ref_id
    && left.domain === right.domain
    && left.session_id === right.session_id
    && left.page_fingerprint === right.page_fingerprint
    && left.run_id === right.run_id
    && left.task_id === right.task_id
    && left.purpose === right.purpose;
}

function normalizePageFingerprint(value: unknown): string {
  const normalized = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (!/^sha256:[0-9a-f]{64}$/.test(normalized)) throw new Error("Credential page fingerprint is invalid");
  return normalized;
}

function invalidPurpose(): never {
  throw new Error("Credential purpose is not allowed");
}
