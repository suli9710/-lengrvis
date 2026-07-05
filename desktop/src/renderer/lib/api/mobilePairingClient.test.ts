import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createMobilePairingCodeEndpoint,
  createRemoteInputGrantEndpoint,
  listMobileDevicesEndpoint,
  revokeMobileDeviceEndpoint,
  revokeRemoteInputGrantEndpoint,
  type MobilePairingEndpointRequest
} from "./mobilePairingClient";

describe("mobile pairing client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("maps the browser fallback interface to encoded backend requests", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    const endpointRequest = request as MobilePairingEndpointRequest;

    await createMobilePairingCodeEndpoint(endpointRequest);
    await listMobileDevicesEndpoint(endpointRequest);
    await revokeMobileDeviceEndpoint(endpointRequest, "device/one");
    await createRemoteInputGrantEndpoint(endpointRequest, "device/one");
    await revokeRemoteInputGrantEndpoint(endpointRequest, "device/one", "grant/two");

    expect(request.mock.calls.map(([input]) => input)).toEqual([
      { endpoint: "/api/pair/request", method: "POST" },
      { endpoint: "/api/pair/devices" },
      { endpoint: "/api/pair/devices/device%2Fone", method: "DELETE" },
      {
        endpoint: "/api/pair/devices/device%2Fone/remote-input-grants",
        method: "POST",
        body: { expires_in: 300 }
      },
      {
        endpoint: "/api/pair/devices/device%2Fone/remote-input-grants/grant%2Ftwo",
        method: "DELETE"
      }
    ]);
  });

  it("prefers the Electron adapter and preserves grant arguments", async () => {
    const response = { ok: true, status: 200 };
    const mobilePairing = {
      createCode: vi.fn().mockResolvedValue(response),
      listDevices: vi.fn().mockResolvedValue(response),
      revokeDevice: vi.fn().mockResolvedValue(response),
      createRemoteInputGrant: vi.fn().mockResolvedValue(response),
      revokeRemoteInputGrant: vi.fn().mockResolvedValue(response)
    };
    (window as unknown as { lengrvis?: { mobilePairing: typeof mobilePairing } }).lengrvis = {
      mobilePairing
    };
    const request = vi.fn();
    const endpointRequest = request as MobilePairingEndpointRequest;

    await createMobilePairingCodeEndpoint(endpointRequest);
    await listMobileDevicesEndpoint(endpointRequest);
    await revokeMobileDeviceEndpoint(endpointRequest, "device-one");
    await createRemoteInputGrantEndpoint(endpointRequest, "device-one", 900);
    await revokeRemoteInputGrantEndpoint(endpointRequest, "device-one", "grant-two");

    expect(request).not.toHaveBeenCalled();
    expect(mobilePairing.revokeDevice).toHaveBeenCalledWith("device-one");
    expect(mobilePairing.createRemoteInputGrant).toHaveBeenCalledWith({
      deviceId: "device-one",
      expiresInSeconds: 900
    });
    expect(mobilePairing.revokeRemoteInputGrant).toHaveBeenCalledWith({
      deviceId: "device-one",
      grantId: "grant-two"
    });
  });
});
