"""Activate a registered Store application on an initialized COM worker."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


def activate_application(app_user_model_id: str) -> int:
    import comtypes
    import comtypes.client

    class IApplicationActivationManager(comtypes.IUnknown):
        _iid_ = comtypes.GUID("{2E941141-7F97-4756-BA1D-9DECDE894A3D}")
        # Only the first method after IUnknown is needed; its vtable position
        # and signature are defined by shobjidl_core.h.
        _methods_ = [
            comtypes.COMMETHOD(
                [],
                comtypes.HRESULT,
                "ActivateApplication",
                (["in"], wintypes.LPCWSTR, "appUserModelId"),
                (["in"], wintypes.LPCWSTR, "arguments"),
                (["in"], wintypes.DWORD, "options"),
                (["out"], ctypes.POINTER(wintypes.DWORD), "processId"),
            )
        ]

    # Out-of-process activation keeps the launch arguments alive even if this
    # caller is a short-lived CLI. Do not retry failed activation automatically.
    manager = comtypes.client.CreateObject(
        "{45BA127D-10A8-46EA-8AB7-56EA9078943C}",
        interface=IApplicationActivationManager,
        clsctx=comtypes.CLSCTX_LOCAL_SERVER,
    )
    process_id = int(manager.ActivateApplication(app_user_model_id, None, 0x2))  # AO_NOERRORUI
    if process_id <= 0:
        raise OSError("Windows did not report an activated application process.")
    return process_id
