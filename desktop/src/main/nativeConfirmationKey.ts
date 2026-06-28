import { createPrivateKey, createPublicKey, generateKeyPairSync, type KeyObject } from "node:crypto";
import { chmodSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeSync } from "node:fs";
import { dirname, join } from "node:path";

import { protectLocalSecret, unprotectLocalSecret } from "./localSecret";

export const NATIVE_CONFIRMATION_PRIVATE_KEY_FILE = "native_confirmation_private.secret";
export const NATIVE_CONFIRMATION_PUBLIC_KEY_FILE = "native_confirmation_public.key";

type NativeConfirmationKeySource = "file" | "created";

export interface NativeConfirmationKeyResolution {
  privateKey: KeyObject;
  publicKey: string;
  privateKeyPath: string;
  publicKeyPath: string;
  source: NativeConfirmationKeySource;
}

export interface NativeConfirmationKeyOptions {
  dataDir: string;
}

export function resolveNativeConfirmationKey(options: NativeConfirmationKeyOptions): NativeConfirmationKeyResolution {
  const dataDir = options.dataDir;
  const privateKeyPath = join(dataDir, NATIVE_CONFIRMATION_PRIVATE_KEY_FILE);
  const publicKeyPath = join(dataDir, NATIVE_CONFIRMATION_PUBLIC_KEY_FILE);
  const loadedPrivateKey = readPrivateKeyFile(privateKeyPath);

  if (loadedPrivateKey) {
    const publicKey = exportPublicKeyBase64Url(loadedPrivateKey);
    writePublicKeyFile(publicKeyPath, publicKey);
    return {
      privateKey: loadedPrivateKey,
      publicKey,
      privateKeyPath,
      publicKeyPath,
      source: "file"
    };
  }

  const keyPair = generateKeyPairSync("ed25519");
  const privateKey = keyPair.privateKey;
  const publicKey = exportPublicKeyBase64Url(privateKey);
  persistPrivateKeyFile(privateKeyPath, privateKey);
  writePublicKeyFile(publicKeyPath, publicKey);
  return {
    privateKey,
    publicKey,
    privateKeyPath,
    publicKeyPath,
    source: "created"
  };
}

function exportPublicKeyBase64Url(privateKey: KeyObject): string {
  return createPublicKey(privateKey)
    .export({ format: "der", type: "spki" })
    .toString("base64url");
}

function readPrivateKeyFile(privateKeyPath: string): KeyObject | null {
  if (!existsSync(privateKeyPath)) {
    return null;
  }
  const stored = readFileSync(privateKeyPath, "utf-8").trim();
  if (!stored) {
    return null;
  }
  try {
    const pkcs8 = unprotectLocalSecret(stored);
    return createPrivateKey({
      key: Buffer.from(pkcs8, "base64url"),
      format: "der",
      type: "pkcs8"
    });
  } catch {
    return null;
  }
}

function persistPrivateKeyFile(privateKeyPath: string, privateKey: KeyObject): void {
  const pkcs8 = privateKey.export({ format: "der", type: "pkcs8" }).toString("base64url");
  const stored = protectLocalSecret(pkcs8);
  writeSecretFileAtomic(privateKeyPath, stored);
}

function writePublicKeyFile(publicKeyPath: string, publicKey: string): void {
  writeSecretFileAtomic(publicKeyPath, `${publicKey}\n`);
}

function writeSecretFileAtomic(filePath: string, contents: string): void {
  mkdirSync(dirname(filePath), { recursive: true });
  const tmpPath = `${filePath}.tmp`;
  try {
    unlinkSync(tmpPath);
  } catch {
    // Ignore stale temp files from a crashed writer.
  }
  const fd = openSync(tmpPath, "wx", 0o600);
  try {
    writeSync(fd, contents, undefined, "utf-8");
  } finally {
    closeSync(fd);
  }
  renameSync(tmpPath, filePath);
  try {
    chmodSync(filePath, 0o600);
  } catch {
    // Best-effort parity with the backend; Windows ACLs may not honor chmod.
  }
}
