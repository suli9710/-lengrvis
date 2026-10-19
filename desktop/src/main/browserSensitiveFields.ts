// Keep the read boundary aligned with backend browser_activity_safety.
export const BROWSER_SENSITIVE_FIELD_PATTERN = String.raw`password|passwd|\bpwd\b|token|secret|cookie|credential|credit|card.?number|cc-|\bcvv\b|\bcvc\b|\botp\b|totp|ssn|(?:^|[^a-z])(?:pin|pan)(?:$|[^a-z])|mfa|2fa|passcode|one.time|verification.?code|security.?code|auth.?code|验证码|密码|密钥`;
