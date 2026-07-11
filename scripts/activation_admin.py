#!/usr/bin/env python3
"""Admin helper for the Lengrvis subscription activation server.

This tool seeds the activation-server database with hashed subscription keys.
It never stores activation keys in clear text. When a key is generated, print or
write it once and deliver it through an approved secure channel.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.commerce.activation import (  # noqa: E402
    ActivationError,
    initialize_activation_db,
    upsert_subscription_key,
    write_activation_key_once,
)
from app.api.routes_activation_admin import hash_admin_password  # noqa: E402


def _parse_datetime(value: str | None, *, field: str) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def command_init_db(args: argparse.Namespace) -> dict[str, Any]:
    path = initialize_activation_db(Path(args.db) if args.db else None)
    return {"database": str(path)}


def command_create_key(args: argparse.Namespace) -> dict[str, Any]:
    activation_key = args.activation_key or f"lgrv_{secrets.token_urlsafe(24)}"
    if args.activation_key_out:
        write_activation_key_once(Path(args.activation_key_out), activation_key)
    result = upsert_subscription_key(
        activation_key=activation_key,
        plan=args.plan,
        subscription_id=args.subscription_id,
        status=args.status,
        subject=args.subject,
        seats=args.seats,
        max_devices=args.max_devices,
        expires_at=_parse_datetime(args.expires_at, field="expires-at"),
        renews_at=_parse_datetime(args.renews_at, field="renews-at"),
        cancel_at_period_end=args.cancel_at_period_end,
        order_ref=args.order_ref,
        db_path=Path(args.db) if args.db else None,
    )
    payload: dict[str, Any] = {"activation_key_generated": args.activation_key is None, **result}
    if args.activation_key_out:
        payload["activation_key_out"] = str(Path(args.activation_key_out).resolve())
    elif args.activation_key is None:
        payload["activation_key"] = activation_key
    return payload


def command_hash_password(args: argparse.Namespace) -> dict[str, Any]:
    return {"password_hash": hash_admin_password(args.password)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Lengrvis subscription activation keys")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="Create activation server tables")
    init_db.add_argument("--db")
    init_db.set_defaults(handler=command_init_db)

    create_key = subparsers.add_parser("create-key", help="Create or update a subscription activation key")
    create_key.add_argument("--db")
    create_key.add_argument("--activation-key")
    create_key.add_argument("--activation-key-out")
    create_key.add_argument("--plan", choices=("free", "plus", "pro", "max", "team"), required=True)
    create_key.add_argument("--subscription-id", required=True)
    create_key.add_argument(
        "--status",
        choices=("active", "trialing", "past_due", "canceled", "expired", "revoked"),
        default="active",
    )
    create_key.add_argument("--subject", default="")
    create_key.add_argument("--seats", type=int, default=1)
    create_key.add_argument("--max-devices", type=int)
    create_key.add_argument("--expires-at")
    create_key.add_argument("--renews-at")
    create_key.add_argument("--cancel-at-period-end", action="store_true")
    create_key.add_argument("--order-ref", default="")
    create_key.set_defaults(handler=command_create_key)

    hash_password = subparsers.add_parser("hash-password", help="Hash an admin UI password for deployment")
    hash_password.add_argument("--password", required=True)
    hash_password.set_defaults(handler=command_hash_password)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "seats", 1) < 1:
        parser.error("--seats must be at least 1")
    if getattr(args, "max_devices", None) is not None and args.max_devices < 1:
        parser.error("--max-devices must be at least 1")
    try:
        result = args.handler(args)
    except (ActivationError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
