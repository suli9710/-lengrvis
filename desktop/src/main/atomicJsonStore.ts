import {
  closeSync,
  fsyncSync,
  mkdirSync,
  openSync,
  renameSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname } from "node:path";

/** Write a complete JSON document and atomically replace the destination. */
export function writeJsonAtomically(filePath: string, value: unknown): void {
  const directory = dirname(filePath);
  mkdirSync(directory, { recursive: true });
  const tempPath = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  let committed = false;

  try {
    const descriptor = openSync(tempPath, "wx", 0o600);
    try {
      writeFileSync(descriptor, JSON.stringify(value, null, 2), "utf8");
      fsyncSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    renameSync(tempPath, filePath);
    committed = true;
  } finally {
    if (!committed) {
      rmSync(tempPath, { force: true });
    }
  }
}
