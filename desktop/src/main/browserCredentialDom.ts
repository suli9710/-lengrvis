const FORBIDDEN_AUTH_FIELD_PATTERN = String.raw`(?:one[-_ ]?time|otp|mfa|2fa|captcha|verification|verify[-_ ]?code|auth[-_ ]?code|security[-_ ]?code|sms[-_ ]?code|new[-_ ]?password|confirm[-_ ]?password|repeat[-_ ]?password|reset[-_ ]?password|create[-_ ]?password|\bpasscode\b|\bpin\b|\bcode\b|验证码|校验码|动态码|短信码|新密码|确认密码|重复密码|重置密码)`;

export function credentialPageFingerprintScript(): string {
  return credentialPageScript("fingerprint");
}

/** Reads one ordinary sign-in password from the managed page into Electron main only. */
export function capturePageCredentialScript(expectedOrigin?: string, expectedPageFingerprint?: string): string {
  return credentialPageScript("capture", expectedOrigin, expectedPageFingerprint);
}

function credentialPageScript(
  mode: "fingerprint" | "capture",
  expectedOrigin = "",
  expectedPageFingerprint = ""
): string {
  return `
    (async () => {
      const visible = (element) => !element.disabled && element.getClientRects().length > 0;
      const descriptorParts = (element) => [
        element.autocomplete,
        element.type,
        element.name,
        element.id,
        element.placeholder,
        element.getAttribute("aria-label") || ""
      ].map((value) => String(value || "").trim().toLowerCase());
      const descriptor = (element) => descriptorParts(element).join(" ");
      const forbidden = new RegExp(${JSON.stringify(FORBIDDEN_AUTH_FIELD_PATTERN)}, "i");
      const passwords = Array.from(document.querySelectorAll('input[type="password"]')).filter(visible);
      if (passwords.length !== 1) return { ok: false, error_code: "password-field-count" };
      const password = passwords[0];
      if (["one-time-code", "new-password"].includes(password.autocomplete) || forbidden.test(descriptor(password))) {
        return { ok: false, error_code: "mfa-or-verification-field" };
      }
      const form = password.form || document;
      const candidates = Array.from(form.querySelectorAll('input:not([type="password"]):not([type="hidden"])'))
        .filter(visible)
        .filter((element) => element.autocomplete !== "one-time-code" && !forbidden.test(descriptor(element)));
      const username = candidates.find((element) => element.autocomplete === "username")
        || candidates.find((element) => element.type === "email")
        || candidates.find((element) => /(?:user|login|email|account)/i.test(descriptor(element)))
        || candidates.filter((element) => element.compareDocumentPosition(password) & Node.DOCUMENT_POSITION_FOLLOWING).at(-1);
      const fingerprintPayload = JSON.stringify({
        origin: location.origin,
        path: location.pathname,
        form_action: password.form ? password.form.action : "",
        form_method: password.form ? password.form.method : "",
        password: descriptorParts(password),
        username: username ? descriptorParts(username) : []
      });
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(fingerprintPayload));
      const pageFingerprint = "sha256:" + Array.from(new Uint8Array(digest))
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("");
      const expectedOrigin = ${JSON.stringify(expectedOrigin)};
      const expectedPageFingerprint = ${JSON.stringify(expectedPageFingerprint)};
      if (expectedOrigin && location.origin !== expectedOrigin) return { ok: false, error_code: "origin-mismatch" };
      if (expectedPageFingerprint && pageFingerprint !== expectedPageFingerprint) {
        return { ok: false, error_code: "page-fingerprint-mismatch" };
      }
      if (${JSON.stringify(mode)} === "fingerprint") {
        return { ok: true, origin: location.origin, page_fingerprint: pageFingerprint };
      }
      if (!password.value) return { ok: false, error_code: "password-field-count" };
      return {
        ok: true,
        username: username && username.value ? username.value : "",
        password: password.value,
        origin: location.origin,
        page_fingerprint: pageFingerprint
      };
    })()
  `;
}

/** Fills only username/password fields and returns booleans, never credential values. */
export function fillPageCredentialScript(
  expectedOrigin: string,
  expectedPageFingerprint: string,
  username: string,
  password: string
): string {
  return `
    (async () => {
      if (location.origin !== ${JSON.stringify(expectedOrigin)}) {
        return { ok: false, error_code: "origin-mismatch" };
      }
      const visible = (element) => !element.disabled && element.getClientRects().length > 0;
      const descriptorParts = (element) => [
        element.autocomplete,
        element.type,
        element.name,
        element.id,
        element.placeholder,
        element.getAttribute("aria-label") || ""
      ].map((value) => String(value || "").trim().toLowerCase());
      const descriptor = (element) => descriptorParts(element).join(" ");
      const forbidden = new RegExp(${JSON.stringify(FORBIDDEN_AUTH_FIELD_PATTERN)}, "i");
      const passwords = Array.from(document.querySelectorAll('input[type="password"]')).filter(visible);
      const currentPassword = passwords.find((element) => element.autocomplete === "current-password")
        || (passwords.length === 1 ? passwords[0] : null);
      if (!currentPassword) return { ok: false, error_code: "password-field-count" };
      if (["one-time-code", "new-password"].includes(currentPassword.autocomplete) || forbidden.test(descriptor(currentPassword))) {
        return { ok: false, error_code: "mfa-or-verification-field" };
      }
      const setValue = (element, value) => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
        if (!setter) return false;
        element.focus();
        setter.call(element, value);
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      };
      const form = currentPassword.form || document;
      const candidates = Array.from(form.querySelectorAll('input:not([type="password"]):not([type="hidden"])'))
        .filter(visible)
        .filter((element) => element.autocomplete !== "one-time-code" && !forbidden.test(descriptor(element)));
      const usernameField = candidates.find((element) => element.autocomplete === "username")
        || candidates.find((element) => element.type === "email")
        || candidates.find((element) => /(?:user|login|email|account)/i.test(descriptor(element)))
        || candidates.filter((element) => element.compareDocumentPosition(currentPassword) & Node.DOCUMENT_POSITION_FOLLOWING).at(-1);
      const fingerprintPayload = JSON.stringify({
        origin: location.origin,
        path: location.pathname,
        form_action: currentPassword.form ? currentPassword.form.action : "",
        form_method: currentPassword.form ? currentPassword.form.method : "",
        password: descriptorParts(currentPassword),
        username: usernameField ? descriptorParts(usernameField) : []
      });
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(fingerprintPayload));
      const pageFingerprint = "sha256:" + Array.from(new Uint8Array(digest))
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("");
      if (pageFingerprint !== ${JSON.stringify(expectedPageFingerprint)}) {
        return { ok: false, error_code: "page-fingerprint-mismatch" };
      }
      const usernameValue = ${JSON.stringify(username)};
      const passwordValue = ${JSON.stringify(password)};
      const filledUsername = usernameField && usernameValue ? setValue(usernameField, usernameValue) : false;
      const filledPassword = setValue(currentPassword, passwordValue);
      return { ok: Boolean(filledPassword), filled_username: Boolean(filledUsername), filled_password: Boolean(filledPassword) };
    })()
  `;
}

export interface CapturedPageCredential {
  username: string;
  password: string;
  origin: string;
  page_fingerprint: string;
}

export interface PageCredentialFingerprint {
  origin: string;
  page_fingerprint: string;
}

export interface FilledPageCredentialResult {
  filled_username: boolean;
  filled_password: boolean;
}

export function parseCapturedPageCredential(value: unknown): CapturedPageCredential {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("The page did not provide a savable password");
  }
  const record = value as Record<string, unknown>;
  if (record.ok !== true || typeof record.username !== "string" || typeof record.password !== "string") {
    if (record.error_code === "origin-mismatch" || record.error_code === "page-fingerprint-mismatch") {
      throw new Error("Browser page or credential fields changed before the credential was captured");
    }
    if (record.error_code === "mfa-or-verification-field") {
      throw new Error("MFA, passcodes, and verification fields cannot be saved");
    }
    throw new Error("Exactly one filled password field is required");
  }
  if (typeof record.origin !== "string" || !/^sha256:[0-9a-f]{64}$/.test(String(record.page_fingerprint ?? ""))) {
    throw new Error("Browser page changed before the credential was captured");
  }
  return {
    username: record.username,
    password: record.password,
    origin: record.origin,
    page_fingerprint: String(record.page_fingerprint)
  };
}

export function parsePageCredentialFingerprint(value: unknown): PageCredentialFingerprint {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Browser page credential fields are unavailable");
  }
  const record = value as Record<string, unknown>;
  if (
    record.ok !== true
    || typeof record.origin !== "string"
    || !/^sha256:[0-9a-f]{64}$/.test(String(record.page_fingerprint ?? ""))
  ) {
    throw new Error("Browser page credential fields are unavailable");
  }
  return { origin: record.origin, page_fingerprint: String(record.page_fingerprint) };
}

export function parseFilledPageCredentialResult(value: unknown): FilledPageCredentialResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Saved credential could not be filled");
  }
  const record = value as Record<string, unknown>;
  if (record.ok !== true || record.filled_password !== true) {
    if (record.error_code === "mfa-or-verification-field") {
      throw new Error("Saved credentials cannot be filled into MFA or verification fields");
    }
    if (record.error_code === "origin-mismatch" || record.error_code === "page-fingerprint-mismatch") {
      throw new Error("Browser page or credential fields changed before the credential was filled");
    }
    throw new Error("Saved credential could not be filled");
  }
  return {
    filled_username: record.filled_username === true,
    filled_password: true
  };
}
