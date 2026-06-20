const publisherName = process.env.AZURE_TRUSTED_SIGNING_PUBLISHER_NAME;

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
  }
};
