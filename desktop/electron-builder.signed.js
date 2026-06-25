const publisherName = process.env.AZURE_TRUSTED_SIGNING_PUBLISHER_NAME;
const macIdentity = process.env.MAC_CSC_NAME || process.env.CSC_NAME;
const appleTeamId = process.env.APPLE_TEAM_ID;

function macNotarizeOptions() {
  if (process.env.APPLE_ID && process.env.APPLE_APP_SPECIFIC_PASSWORD && appleTeamId) {
    return {
      teamId: appleTeamId,
      appleId: process.env.APPLE_ID,
      appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD
    };
  }

  if (process.env.APPLE_API_KEY && process.env.APPLE_API_KEY_ID && process.env.APPLE_API_ISSUER) {
    return {
      teamId: appleTeamId,
      appleApiKey: process.env.APPLE_API_KEY,
      appleApiKeyId: process.env.APPLE_API_KEY_ID,
      appleApiIssuer: process.env.APPLE_API_ISSUER
    };
  }

  return undefined;
}

// Azure Trusted Signing authentication is read by the TrustedSigning module
// from AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET.
module.exports = {
  extends: "./electron-builder.yml",
  win: {
    azureSignOptions: {
      endpoint: process.env.AZURE_TRUSTED_SIGNING_ENDPOINT,
      codeSigningAccountName: process.env.AZURE_TRUSTED_SIGNING_ACCOUNT_NAME,
      certificateProfileName: process.env.AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME,
      publisherName
    },
    verifyUpdateCodeSignature: true,
    publisherName: [publisherName]
  },
  mac: {
    hardenedRuntime: true,
    gatekeeperAssess: false,
    identity: macIdentity,
    entitlements: "build/entitlements.mac.plist",
    entitlementsInherit: "build/entitlements.mac.plist",
    notarize: macNotarizeOptions()
  }
};
