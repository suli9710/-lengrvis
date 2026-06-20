const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const configPath = join(__dirname, "..", "electron-builder.signed.js");
const configText = readFileSync(configPath, "utf8");

const requiredEnv = [
  "AZURE_TENANT_ID",
  "AZURE_CLIENT_ID",
  "AZURE_CLIENT_SECRET",
  "AZURE_TRUSTED_SIGNING_ENDPOINT",
  "AZURE_TRUSTED_SIGNING_ACCOUNT_NAME",
  "AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME",
  "AZURE_TRUSTED_SIGNING_PUBLISHER_NAME"
];

const requiredConfigMarkers = [
  "endpoint: process.env.AZURE_TRUSTED_SIGNING_ENDPOINT",
  "codeSigningAccountName: process.env.AZURE_TRUSTED_SIGNING_ACCOUNT_NAME",
  "certificateProfileName: process.env.AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME",
  "publisherName"
];

const issues = [];
const placeholders = [...new Set(configText.match(/REPLACE_[A-Z0-9_]+/g) ?? [])];
if (placeholders.length > 0) {
  issues.push(`Signed build config still contains placeholder values: ${placeholders.join(", ")}`);
}

for (const marker of requiredConfigMarkers) {
  if (!configText.includes(marker)) {
    issues.push(`Signed build config must contain ${marker}`);
  }
}

for (const name of requiredEnv) {
  const value = process.env[name];
  if (!value || value.trim() === "" || /^REPLACE_/i.test(value.trim())) {
    issues.push(`Missing non-placeholder environment variable: ${name}`);
  }
}

if (issues.length === 0) {
  delete require.cache[configPath];
  const config = require(configPath);
  const win = config && config.win ? config.win : {};
  const azure = win.azureSignOptions || {};
  const resolvedChecks = [
    ["win.azureSignOptions.endpoint", azure.endpoint, process.env.AZURE_TRUSTED_SIGNING_ENDPOINT],
    [
      "win.azureSignOptions.codeSigningAccountName",
      azure.codeSigningAccountName,
      process.env.AZURE_TRUSTED_SIGNING_ACCOUNT_NAME
    ],
    [
      "win.azureSignOptions.certificateProfileName",
      azure.certificateProfileName,
      process.env.AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME
    ],
    [
      "win.azureSignOptions.publisherName",
      azure.publisherName,
      process.env.AZURE_TRUSTED_SIGNING_PUBLISHER_NAME
    ],
    ["win.publisherName[0]", Array.isArray(win.publisherName) ? win.publisherName[0] : undefined, process.env.AZURE_TRUSTED_SIGNING_PUBLISHER_NAME]
  ];
  for (const [field, actual, expected] of resolvedChecks) {
    if (actual !== expected) {
      issues.push(`${field} did not resolve from the expected environment variable.`);
    }
  }
  if (win.verifyUpdateCodeSignature !== true) {
    issues.push("win.verifyUpdateCodeSignature must remain true for signed builds.");
  }
}

if (issues.length > 0) {
  console.error("Signed Windows distribution configuration is incomplete:");
  for (const issue of issues) {
    console.error(` - ${issue}`);
  }
  console.error(
    "Unsigned local builds must use `npm --prefix desktop run dist`; signed release builds must set Azure Trusted Signing environment values and verify the backend binary signature before packaging."
  );
  process.exit(1);
}

console.log("Signed Windows distribution configuration verified.");
