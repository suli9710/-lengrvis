from __future__ import annotations

import json
import sys


SETTINGS_URIS = {
    "display": "ms-settings:display",
    "network": "ms-settings:network",
}


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    args = payload.get("args") or {}
    area = str(args.get("area") or "display")
    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": bool(args.get("dry_run", True)),
                "intent": {
                    "target_app": "windows.settings",
                    "interface": "ui_automation",
                    "action": "adjust_display_or_network",
                    "area": area,
                    "settings_uri": SETTINGS_URIS.get(area, "ms-settings:"),
                    "requested_action": args.get("action", ""),
                    "value": args.get("value", ""),
                    "steps": [
                        "open the relevant Windows Settings URI",
                        "read current setting state",
                        "locate control with UIAutomation",
                        "apply change only after approval",
                    ],
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
