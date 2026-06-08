from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    args = payload.get("args", {})
    dry_run = bool(args.get("dry_run", True))
    preview = [
        {"boundary": "file.read", "target": args.get("path", "")},
        {"boundary": "file.write", "target": args.get("path", "")},
        {"boundary": "ui.control", "target": "Product Manifest Showcase"},
        {"boundary": "network.external", "target": args.get("endpoint", "")},
        {"boundary": "messaging.send", "target": "sample conversation"},
        {"boundary": "filesystem.delete", "target": "generated output"},
    ]
    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": dry_run,
                "preview": preview,
                "rollback": "Restore files from the preview plan or hand off external message/delete recovery to the user.",
            }
        )
    )


if __name__ == "__main__":
    main()
