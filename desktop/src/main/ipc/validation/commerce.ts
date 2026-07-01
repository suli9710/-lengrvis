import type {
  DesktopCommerceLicenseActivateRequest,
  DesktopCommerceLicenseInstallRequest,
  DesktopCommercePolicyImportRequest
} from "../../../shared/types";
import {
  rejectUnexpectedBridgeKeys,
  validateBridgeStringValue,
  validateOptionalConfirmationNonce,
  validatePlainBridgeBody
} from "./primitives";
import { validatePermissionPolicy } from "./permissionPolicy";

const COMMERCE_LICENSE_INSTALL_ALLOWED_KEYS = new Set(["token"]);
const COMMERCE_LICENSE_ACTIVATE_ALLOWED_KEYS = new Set(["activationKey", "activation_key", "appVersion", "app_version"]);
const COMMERCE_POLICY_IMPORT_ALLOWED_KEYS = new Set(["policy", "confirmationNonce", "confirmation_nonce"]);

export function validateCommerceLicenseInstallRequest(value: unknown): DesktopCommerceLicenseInstallRequest {
  const request = validatePlainBridgeBody(value, "commerce license install request");
  rejectUnexpectedBridgeKeys(request, COMMERCE_LICENSE_INSTALL_ALLOWED_KEYS, "commerce license install request");
  return {
    token: validateBridgeStringValue(request.token, "license token", 65_536, { allowEmpty: false, trim: true })
  };
}

export function validateCommerceLicenseActivateRequest(value: unknown): DesktopCommerceLicenseActivateRequest {
  const request = validatePlainBridgeBody(value, "commerce license activate request");
  rejectUnexpectedBridgeKeys(request, COMMERCE_LICENSE_ACTIVATE_ALLOWED_KEYS, "commerce license activate request");
  return {
    activationKey: validateBridgeStringValue(request.activationKey ?? request.activation_key, "activation key", 256, {
      allowEmpty: false,
      trim: true
    }),
    appVersion: request.appVersion === undefined && request.app_version === undefined
      ? undefined
      : validateBridgeStringValue(request.appVersion ?? request.app_version, "app version", 64, {
          allowEmpty: true,
          trim: true
        })
  };
}

export function validateCommercePolicyImportRequest(value: unknown): DesktopCommercePolicyImportRequest {
  const request = validatePlainBridgeBody(value, "commerce policy import request");
  rejectUnexpectedBridgeKeys(request, COMMERCE_POLICY_IMPORT_ALLOWED_KEYS, "commerce policy import request");
  return {
    policy: validatePermissionPolicy(request.policy),
    confirmationNonce: validateOptionalConfirmationNonce(request.confirmationNonce ?? request.confirmation_nonce)
  };
}
