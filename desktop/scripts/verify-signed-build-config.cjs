const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const configPath = join(__dirname, "..", "electron-builder.signed.js");
const configText = readFileSync(configPath, "utf8");
const args = process.argv.slice(2);
const structureOnly = args.includes("--structure-only");
const targetArgs = args.filter((arg) => arg !== "--structure-only");
const requestedTargets = new Set(
  (targetArgs.length > 0 ? targetArgs : ["win"])
    .flatMap((arg) => arg.split(","))
    .map((arg) => arg.trim().toLowerCase())
    .filter(Boolean)
);

if (requestedTargets.has("all")) {
  requestedTargets.add("win");
  requestedTargets.add("mac");
  requestedTargets.delete("all");
}

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

function configured(name) {
  const value = process.env[name];
  return Boolean(value && value.trim() !== "" && !/^REPLACE_/i.test(value.trim()));
}

function requireEnv(name) {
  if (!configured(name)) {
    issues.push(`Missing non-placeholder environment variable: ${name}`);
  }
}

function requireAny(label, names) {
  if (!names.some(configured)) {
    issues.push(`Missing ${label}; set one of: ${names.join(", ")}`);
  }
}

if (requestedTargets.has("win")) {
  for (const marker of requiredConfigMarkers) {
    if (!configText.includes(marker)) {
      issues.push(`Signed Windows build config must contain ${marker}`);
    }
  }

  if (!structureOnly) {
    for (const name of requiredEnv) {
      requireEnv(name);
    }
  }
}

if (requestedTargets.has("mac")) {
  for (const marker of [
    "hardenedRuntime: true",
    "gatekeeperAssess: false",
    "entitlements.mac.plist",
    "notarize: macNotarizeOptions()"
  ]) {
    if (!configText.includes(marker)) {
      issues.push(`Signed macOS build config must contain ${marker}`);
    }
  }
  if (!structureOnly) {
    requireEnv("APPLE_TEAM_ID");
    requireAny("macOS signing identity or certificate", ["MAC_CSC_NAME", "CSC_NAME", "MAC_CSC_LINK", "CSC_LINK"]);

    const hasAppleIdAuth = configured("APPLE_ID") && configured("APPLE_APP_SPECIFIC_PASSWORD");
    const hasApiKeyAuth = configured("APPLE_API_KEY") && configured("APPLE_API_KEY_ID") && configured("APPLE_API_ISSUER");
    if (!hasAppleIdAuth && !hasApiKeyAuth) {
      issues.push(
        "Missing Apple notarization credentials; set APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD, or APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER."
      );
    }
  }
}

const unsupportedTargets = [...requestedTargets].filter((target) => !["win", "mac"].includes(target));
for (const target of unsupportedTargets) {
  issues.push(`Unsupported signed build config target: ${target}`);
}

if (!structureOnly && issues.length === 0 && (requestedTargets.has("win") || requestedTargets.has("mac"))) {
  delete require.cache[configPath];
  const config = require(configPath);

  if (requestedTargets.has("win")) {
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
      [
        "win.publisherName[0]",
        Array.isArray(win.publisherName) ? win.publisherName[0] : undefined,
        process.env.AZURE_TRUSTED_SIGNING_PUBLISHER_NAME
      ]
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

  if (requestedTargets.has("mac")) {
    const mac = config && config.mac ? config.mac : {};
    if (mac.hardenedRuntime !== true) {
      issues.push("mac.hardenedRuntime must remain true for signed builds.");
    }
    if (mac.gatekeeperAssess !== false) {
      issues.push("mac.gatekeeperAssess must remain false so notarization/stapler validation is authoritative.");
    }
    if (mac.entitlements !== "build/entitlements.mac.plist") {
      issues.push("mac.entitlements must point at build/entitlements.mac.plist.");
    }
    if (mac.entitlementsInherit !== "build/entitlements.mac.plist") {
      issues.push("mac.entitlementsInherit must point at build/entitlements.mac.plist.");
    }
    if (!mac.notarize || mac.notarize.teamId !== process.env.APPLE_TEAM_ID) {
      issues.push("mac.notarize must resolve with APPLE_TEAM_ID and Apple notarization credentials.");
    }
  }
}

if (issues.length > 0) {
  const onlyWindows = requestedTargets.size === 1 && requestedTargets.has("win");
  console.error(
    onlyWindows
      ? "Signed Windows distribution configuration is incomplete:"
      : `Signed distribution configuration is incomplete for target(s): ${[...requestedTargets].join(", ")}`
  );
  for (const issue of issues) {
    console.error(` - ${issue}`);
  }
  if (onlyWindows) {
    console.error(
      "Unsigned local builds must use `npm --prefix desktop run dist:unsigned`; signed release builds must set Azure Trusted Signing environment values and verify the backend binary signature before packaging."
    );
  } else {
    console.error(
      "Unsigned local builds must use the unsigned dist scripts. Signed release builds must set platform signing/notarization environment values and verify the backend binary signature or integrity before packaging."
    );
  }
  process.exit(1);
}

if (requestedTargets.size === 1 && requestedTargets.has("win")) {
  console.log("Signed Windows distribution configuration verified.");
} else {
  console.log(`Signed distribution configuration verified for target(s): ${[...requestedTargets].join(", ")}.`);
}
