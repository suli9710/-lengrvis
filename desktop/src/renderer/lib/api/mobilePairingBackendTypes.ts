import type { DesktopMobilePairingCode } from "../../../shared/mobilePairingPayload";

export interface MobilePairingCode extends DesktopMobilePairingCode {}

export interface MobileDevice {
  device_id: string;
  device_name: string;
  status?: string;
  created_at: string;
  updated_at: string;
  revoked_at?: string;
  remote_input_grants?: RemoteInputGrant[];
}

export interface MobileDeviceList {
  devices: MobileDevice[];
}

export interface RemoteInputGrant {
  id: string;
  status?: string;
  scope?: "remote:input" | string;
  created_at?: string;
  expires_at?: string;
  revoked_at?: string;
}

export interface RemoteInputGrantIssueResult {
  grant_id: string;
  device_id: string;
  expires_at: string;
  expires_in: number;
  device?: MobileDevice;
}
