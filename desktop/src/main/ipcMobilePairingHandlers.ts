import { ipcMain } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  MobilePairingRemoteInputGrantRequest,
  MobilePairingRevokeRemoteInputGrantRequest
} from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import { proxyExplicitDesktopBridgeRequest } from "./ipcBackendProxy";
import { confirmNativeDesktopAction } from "./ipcNativeConfirmation";
import {
  validateBridgeIdentifier,
  validateBridgePositiveInteger
} from "./ipcValidation";
import { assertTrustedRenderer } from "./rendererTrust";

const DEFAULT_REMOTE_INPUT_GRANT_TTL_SECONDS = 300;

export function registerMobilePairingIpcHandlers(backend: BackendProcessManager): void {
  ipcMain.handle(IPC_CHANNELS.mobilePairingCreateCode, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm mobile pairing",
      message: "Create a new mobile pairing code?",
      detail: "Anyone who can see the temporary code may attempt to pair a device until it expires."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/pair/request",
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingListDevices, async (event) => {
    assertTrustedRenderer(event);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/pair/devices"
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingRevokeDevice, async (event, deviceId: string) => {
    assertTrustedRenderer(event);
    const safeDeviceId = validateBridgeIdentifier(deviceId, "mobile device id");
    await confirmNativeDesktopAction(event, {
      title: "Confirm device disconnect",
      message: "Disconnect this paired mobile device?",
      detail: `Device id: ${safeDeviceId}\n\nThe device will lose access until paired again.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/pair/devices/${encodeURIComponent(safeDeviceId)}`,
      method: "DELETE"
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingCreateRemoteInputGrant, async (event, request: MobilePairingRemoteInputGrantRequest) => {
    assertTrustedRenderer(event);
    const safeDeviceId = validateBridgeIdentifier(request?.deviceId, "mobile device id");
    const expiresInSeconds = validateBridgePositiveInteger(
      request?.expiresInSeconds,
      "remote input grant expiry",
      DEFAULT_REMOTE_INPUT_GRANT_TTL_SECONDS,
      1,
      86_400
    );
    await confirmNativeDesktopAction(event, {
      title: "Confirm remote input",
      message: "Allow this paired mobile device to send remote input?",
      detail: `Device id: ${safeDeviceId}\nExpires in: ${expiresInSeconds} seconds\n\nThe grant can be revoked from the desktop or mobile app.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/pair/devices/${encodeURIComponent(safeDeviceId)}/remote-input-grants`,
      method: "POST",
      body: { expires_in: expiresInSeconds }
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingRevokeRemoteInputGrant, async (event, request: MobilePairingRevokeRemoteInputGrantRequest) => {
    assertTrustedRenderer(event);
    const safeDeviceId = validateBridgeIdentifier(request?.deviceId, "mobile device id");
    const safeGrantId = validateBridgeIdentifier(request?.grantId, "remote input grant id");
    await confirmNativeDesktopAction(event, {
      title: "Confirm remote input revoke",
      message: "Revoke remote input access for this device?",
      detail: `Device id: ${safeDeviceId}\nGrant id: ${safeGrantId}\n\nThe mobile device will return to read-only remote view.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/pair/devices/${encodeURIComponent(safeDeviceId)}/remote-input-grants/${encodeURIComponent(safeGrantId)}`,
      method: "DELETE"
    });
  });
}
