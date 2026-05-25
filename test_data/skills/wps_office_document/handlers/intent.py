from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    args = payload.get("args") or {}
    operation = str(args.get("operation") or "open")
    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": bool(args.get("dry_run", True)),
                "intent": {
                    "target_app": "wps.office",
                    "interface": "com",
                    "action": "open_edit_document",
                    "operation": operation,
                    "path": args.get("path", ""),
                    "requires_authorized_path": True,
                    "steps": [
                        "open document with WPS COM automation",
                        "apply requested edit through COM object model",
                        "save only after explicit approval",
                    ],
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
