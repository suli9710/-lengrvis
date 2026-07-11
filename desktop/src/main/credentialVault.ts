import { app, safeStorage } from "electron";
import { randomUUID } from "node:crypto";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";

import {
  CREDENTIAL_REF_SCHEMA_VERSION,
  type CredentialRef
} from "../shared/credentialTypes";
import { writeJsonAtomically } from "./atomicJsonStore";

const CREDENTIAL_VAULT_SCHEMA_VERSION = "credential-vault-v1" as const;
const CREDENTIAL_VAULT_FILENAME = "credential-vault.json";
const MAX_USERNAME_CHARS = 512;
const MAX_PASSWORD_CHARS = 4096;

export interface SafeStorageAdapter {
  isEncryptionAvailable: () => boolean;
  getSelectedStorageBackend?: () => string;
  encryptString: (value: string) => Buffer;
  decryptString: (value: Buffer) => string;
}

interface StoredCredentialRecord {
  ref: CredentialRef;
  encrypted_payload: string;
}

interface StoredCredentialVault {
  schema_version: typeof CREDENTIAL_VAULT_SCHEMA_VERSION;
  records: StoredCredentialRecord[];
}

export interface CredentialSecret {
  username: string;
  password: string;
}

export interface CredentialVaultOptions {
  filePath?: () => string;
  storage?: SafeStorageAdapter;
  now?: () => Date;
  randomId?: () => string;
}

/** Electron-main-only encrypted storage. No method returns plaintext metadata to renderer code. */
export class CredentialVault {
  private readonly filePath: () => string;
  private readonly storage: SafeStorageAdapter;
  private readonly now: () => Date;
  private readonly randomId: () => string;

  constructor(options: CredentialVaultOptions = {}) {
    this.filePath = options.filePath ?? defaultCredentialVaultPath;
    this.storage = options.storage ?? safeStorage;
    this.now = options.now ?? (() => new Date());
    this.randomId = options.randomId ?? randomUUID;
  }

  list(domain?: string): CredentialRef[] {
    const normalizedDomain = domain === undefined ? undefined : normalizeCredentialDomain(domain);
    return this.readVault().records
      .map((record) => ({ ...record.ref }))
      .filter((ref) => normalizedDomain === undefined || ref.domain === normalizedDomain)
      .sort((left, right) => left.created_at.localeCompare(right.created_at));
  }

  getRef(credentialRefId: string): CredentialRef | undefined {
    const normalizedId = normalizeCredentialIdentifier(credentialRefId, "credential ref id");
    const record = this.readVault().records.find((candidate) => candidate.ref.id === normalizedId);
    return record ? { ...record.ref } : undefined;
  }

  store(domain: string, secret: CredentialSecret): CredentialRef {
    this.assertSecureStorage();
    const normalizedDomain = normalizeCredentialDomain(domain);
    const normalizedSecret = normalizeCredentialSecret(secret);
    const vault = this.readVault();
    const timestamp = this.now().toISOString();
    const ref: CredentialRef = {
      schema_version: CREDENTIAL_REF_SCHEMA_VERSION,
      id: `cred_${this.randomId().replace(/-/g, "")}`,
      domain: normalizedDomain,
      kind: "password",
      created_at: timestamp,
      updated_at: timestamp
    };
    const encrypted = this.storage.encryptString(JSON.stringify(normalizedSecret));
    vault.records.push({ ref, encrypted_payload: encrypted.toString("base64") });
    this.persist(vault);
    return { ...ref };
  }

  resolve(credentialRefId: string): { ref: CredentialRef; secret: CredentialSecret } {
    this.assertSecureStorage();
    const normalizedId = normalizeCredentialIdentifier(credentialRefId, "credential ref id");
    const record = this.readVault().records.find((candidate) => candidate.ref.id === normalizedId);
    if (!record) {
      throw new Error("Saved credential is unavailable");
    }
    try {
      const plaintext = this.storage.decryptString(Buffer.from(record.encrypted_payload, "base64"));
      const secret = normalizeCredentialSecret(JSON.parse(plaintext) as unknown);
      return { ref: { ...record.ref }, secret };
    } catch {
      throw new Error("Saved credential could not be decrypted");
    }
  }

  delete(credentialRefId: string): boolean {
    const normalizedId = normalizeCredentialIdentifier(credentialRefId, "credential ref id");
    const vault = this.readVault();
    const remaining = vault.records.filter((candidate) => candidate.ref.id !== normalizedId);
    if (remaining.length === vault.records.length) return false;
    this.persist({ ...vault, records: remaining });
    return true;
  }

  clear(): void {
    rmSync(this.filePath(), { force: true });
  }

  private assertSecureStorage(): void {
    let available = false;
    let backend = "";
    try {
      available = this.storage.isEncryptionAvailable();
      backend = this.storage.getSelectedStorageBackend?.() ?? "";
    } catch {
      available = false;
    }
    if (!available || backend === "basic_text") {
      throw new Error("Secure OS credential storage is unavailable");
    }
  }

  private readVault(): StoredCredentialVault {
    const path = this.filePath();
    if (!existsSync(path)) {
      return { schema_version: CREDENTIAL_VAULT_SCHEMA_VERSION, records: [] };
    }
    try {
      const value = JSON.parse(readFileSync(path, "utf8")) as unknown;
      return normalizeStoredVault(value);
    } catch {
      throw new Error("Saved credential vault is invalid");
    }
  }

  private persist(vault: StoredCredentialVault): void {
    writeJsonAtomically(this.filePath(), vault);
  }
}

export function normalizeCredentialDomain(value: string): string {
  const trimmed = String(value ?? "").trim();
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error("Credential binding must be an exact HTTPS origin");
  }
  if (
    parsed.protocol !== "https:"
    || parsed.username
    || parsed.password
    || !parsed.hostname
    || (parsed.pathname !== "/" && parsed.pathname !== "")
    || parsed.search
    || parsed.hash
  ) {
    throw new Error("Credential binding must be an exact HTTPS origin");
  }
  return parsed.origin;
}

export function credentialDomainFromUrl(value: string): string {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new Error("Saved credentials can only be used on an HTTPS page without URL credentials");
  }
  return normalizeCredentialDomain(parsed.origin);
}

function defaultCredentialVaultPath(): string {
  const envDir = process.env.LENGRVIS_DATA_DIR?.trim();
  return join(envDir || app.getPath("userData"), CREDENTIAL_VAULT_FILENAME);
}

function normalizeCredentialSecret(value: unknown): CredentialSecret {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Credential secret is invalid");
  }
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some((key) => !["username", "password"].includes(key))) {
    throw new Error("Credential secret is invalid");
  }
  const username = typeof record.username === "string" ? record.username : "";
  const password = typeof record.password === "string" ? record.password : "";
  if (username.length > MAX_USERNAME_CHARS || !password || password.length > MAX_PASSWORD_CHARS) {
    throw new Error("Credential secret is invalid");
  }
  if (username.includes("\0") || password.includes("\0")) {
    throw new Error("Credential secret is invalid");
  }
  return { username, password };
}

function normalizeStoredVault(value: unknown): StoredCredentialVault {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid vault");
  }
  const record = value as Record<string, unknown>;
  if (record.schema_version !== CREDENTIAL_VAULT_SCHEMA_VERSION || !Array.isArray(record.records)) {
    throw new Error("invalid vault");
  }
  const records = record.records.map(normalizeStoredRecord);
  if (new Set(records.map((item) => item.ref.id)).size !== records.length) {
    throw new Error("invalid vault");
  }
  return { schema_version: CREDENTIAL_VAULT_SCHEMA_VERSION, records };
}

function normalizeStoredRecord(value: unknown): StoredCredentialRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid record");
  const record = value as Record<string, unknown>;
  if (typeof record.encrypted_payload !== "string" || !record.encrypted_payload) throw new Error("invalid record");
  return {
    ref: normalizeCredentialRef(record.ref),
    encrypted_payload: record.encrypted_payload
  };
}

function normalizeCredentialRef(value: unknown): CredentialRef {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid ref");
  const record = value as Record<string, unknown>;
  if (record.schema_version !== CREDENTIAL_REF_SCHEMA_VERSION || record.kind !== "password") {
    throw new Error("invalid ref");
  }
  const id = normalizeCredentialIdentifier(record.id, "credential ref id");
  const domain = normalizeStoredCredentialDomain(String(record.domain ?? ""));
  const createdAt = normalizeIsoTimestamp(record.created_at);
  const updatedAt = normalizeIsoTimestamp(record.updated_at);
  return {
    schema_version: CREDENTIAL_REF_SCHEMA_VERSION,
    id,
    domain,
    kind: "password",
    created_at: createdAt,
    updated_at: updatedAt
  };
}

function normalizeStoredCredentialDomain(value: string): string {
  try {
    return normalizeCredentialDomain(value);
  } catch {
    const legacyHostname = String(value ?? "").trim().toLowerCase().replace(/\.$/, "");
    if (!legacyHostname || /[\s/:@\\]/.test(legacyHostname)) throw new Error("invalid ref");
    return normalizeCredentialDomain(`https://${legacyHostname}`);
  }
}

export function normalizeCredentialIdentifier(value: unknown, label: string): string {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(normalized)) {
    throw new Error(`Invalid ${label}`);
  }
  return normalized;
}

function normalizeIsoTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value || Number.isNaN(Date.parse(value))) throw new Error("invalid timestamp");
  return new Date(value).toISOString();
}
