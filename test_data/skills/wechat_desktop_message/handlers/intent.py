from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    args = payload.get("args") or {}
    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": bool(args.get("dry_run", True)),
                "intent": {
                    "target_app": "wechat.desktop",
                    "interface": "ui_automation",
                    "action": "send_message",
                    "contact": args.get("contact", ""),
                    "message_length": len(str(args.get("message", ""))),
                    "steps": [
                        "focus WeChat main window",
                        "find contact search box",
                        "open contact conversation",
                        "type message into editor",
                        "click send after approval",
                    ],
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
