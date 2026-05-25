from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    args = payload.get("args") or {}
    print(json.dumps({"ok": True, "echo": str(args.get("text", ""))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
