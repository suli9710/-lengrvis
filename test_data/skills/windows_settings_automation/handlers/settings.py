from __future__ import annotations

import json
import platform
import subprocess
import sys
from typing import Any


def _bool_arg(args: dict[str, Any], key: str, default: bool) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_arg(args: dict[str, Any], key: str) -> int:
    try:
        return int(args.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _ps(operation: str, script: str) -> dict[str, Any]:
    return {
        "type": "powershell",
        "operation": operation,
        "command": [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
    }


def _theme_plan(args: dict[str, Any]) -> list[dict[str, Any]]:
    theme = str(args.get("theme") or "").strip().lower()
    if theme not in {"light", "dark"}:
        raise ValueError("theme must be 'light' or 'dark'")
    value = 1 if theme == "light" else 0
    path = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    script = (
        f"New-Item -Path '{path}' -Force | Out-Null; "
        f"Set-ItemProperty -Path '{path}' -Name AppsUseLightTheme -Type DWord -Value {value}; "
        f"Set-ItemProperty -Path '{path}' -Name SystemUsesLightTheme -Type DWord -Value {value}"
    )
    return [_ps("set_theme", script)]


def _lock_screen_ads_plan(args: dict[str, Any]) -> list[dict[str, Any]]:
    enabled = _bool_arg(args, "enabled", False)
    value = 1 if enabled else 0
    path = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
    names = [
        "RotatingLockScreenEnabled",
        "RotatingLockScreenOverlayEnabled",
        "SubscribedContent-338387Enabled",
    ]
    script = f"New-Item -Path '{path}' -Force | Out-Null; " + "; ".join(
        f"Set-ItemProperty -Path '{path}' -Name {name} -Type DWord -Value {value}" for name in names
    )
    return [_ps("set_lock_screen_ads", script)]


def _night_light_plan(args: dict[str, Any]) -> list[dict[str, Any]]:
    enabled = _bool_arg(args, "enabled", True)
    state = "on" if enabled else "off"
    script = (
        "Start-Process 'ms-settings:nightlight'; "
        f"Write-Output 'Night light requested: {state}. Windows stores this in CloudStore binary state; "
        "Mavris opens the native settings page instead of mutating undocumented binary data.'"
    )
    return [
        {
            **_ps("set_night_light", script),
            "note": "Direct Night Light registry writes use undocumented CloudStore binary data, so this skill opens the native settings page.",
        }
    ]


def _resolution_plan(args: dict[str, Any]) -> list[dict[str, Any]]:
    width = _int_arg(args, "width")
    height = _int_arg(args, "height")
    if width < 640 or height < 480:
        raise ValueError("resolution must be at least 640x480")
    script = rf"""
$code = @'
using System;
using System.Runtime.InteropServices;
public class DisplaySettings {{
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
  public struct DEVMODE {{
    private const int CCHDEVICENAME = 32;
    private const int CCHFORMNAME = 32;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=CCHDEVICENAME)] public string dmDeviceName;
    public short dmSpecVersion;
    public short dmDriverVersion;
    public short dmSize;
    public short dmDriverExtra;
    public int dmFields;
    public int dmPositionX;
    public int dmPositionY;
    public int dmDisplayOrientation;
    public int dmDisplayFixedOutput;
    public short dmColor;
    public short dmDuplex;
    public short dmYResolution;
    public short dmTTOption;
    public short dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=CCHFORMNAME)] public string dmFormName;
    public short dmLogPixels;
    public int dmBitsPerPel;
    public int dmPelsWidth;
    public int dmPelsHeight;
    public int dmDisplayFlags;
    public int dmDisplayFrequency;
  }}
  [DllImport("user32.dll")] public static extern int EnumDisplaySettings(string deviceName, int modeNum, ref DEVMODE devMode);
  [DllImport("user32.dll")] public static extern int ChangeDisplaySettings(ref DEVMODE devMode, int flags);
}}
'@
Add-Type $code
$devmode = New-Object DisplaySettings+DEVMODE
$devmode.dmSize = [System.Runtime.InteropServices.Marshal]::SizeOf($devmode)
[DisplaySettings]::EnumDisplaySettings($null, -1, [ref]$devmode) | Out-Null
$devmode.dmPelsWidth = {width}
$devmode.dmPelsHeight = {height}
$devmode.dmFields = 0x180000
$result = [DisplaySettings]::ChangeDisplaySettings([ref]$devmode, 1)
if ($result -ne 0) {{ throw "ChangeDisplaySettings failed with code $result" }}
""".strip()
    return [_ps("set_resolution", script)]


def _query_plan() -> list[dict[str, Any]]:
    script = (
        "$themePath='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize'; "
        "$lockPath='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\ContentDeliveryManager'; "
        "Get-ItemProperty -Path $themePath,$lockPath -ErrorAction SilentlyContinue | ConvertTo-Json -Compress"
    )
    return [_ps("query", script)]


def _plan(args: dict[str, Any]) -> list[dict[str, Any]]:
    action = str(args.get("action") or "query").strip().lower()
    if action == "query":
        return _query_plan()
    if action == "set_theme":
        return _theme_plan(args)
    if action == "set_resolution":
        return _resolution_plan(args)
    if action == "set_night_light":
        return _night_light_plan(args)
    if action == "set_lock_screen_ads":
        return _lock_screen_ads_plan(args)
    raise ValueError(f"Unsupported action: {action}")


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    args = payload.get("args") or {}
    dry_run = _bool_arg(args, "dry_run", True)
    try:
        operations = _plan(args)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 0

    if dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "operations": operations}))
        return 0
    if platform.system().lower() != "windows":
        print(json.dumps({"ok": False, "error": "Windows settings automation can only apply changes on Windows.", "operations": operations}))
        return 0

    results = []
    for operation in operations:
        completed = subprocess.run(
            operation["command"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            shell=False,
        )
        results.append(
            {
                "operation": operation["operation"],
                "return_code": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        )
        if completed.returncode != 0:
            print(json.dumps({"ok": False, "applied": False, "operations": operations, "results": results}))
            return 0
    print(json.dumps({"ok": True, "applied": True, "operations": operations, "results": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
