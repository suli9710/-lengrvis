from __future__ import annotations

import random
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException

from app.security.mobile_jwt import issue_mobile_token, new_device_id

PAIR_CODE_TTL_SECONDS = 300


@dataclass(slots=True)
class PairingCode:
    code: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + PAIR_CODE_TTL_SECONDS)
    used: bool = False


_lock = threading.RLock()
_pairings: dict[str, PairingCode] = {}


def create_pairing_code() -> dict:
    with _lock:
        _prune_expired_locked()
        code = _unique_code_locked()
        pairing = PairingCode(code=code)
        _pairings[code] = pairing
    return {
        "code": code,
        "expires_at": _epoch_to_iso(pairing.expires_at),
        "expires_in": max(0, int(pairing.expires_at - time.time())),
        "server": {
            "host": _lan_ip(),
            "port": _backend_port(),
        },
    }


def redeem_pairing_code(*, code: str, device_name: str) -> dict:
    normalized = "".join(character for character in code if character.isdigit())
    if len(normalized) != 6:
        raise HTTPException(status_code=422, detail="Pairing code must be 6 digits")

    with _lock:
        _prune_expired_locked()
        pairing = _pairings.get(normalized)
        if pairing is None or pairing.used or pairing.expires_at < time.time():
            raise HTTPException(status_code=401, detail="Pairing code is invalid or expired")
        pairing.used = True
        _pairings.pop(normalized, None)

    device_id = new_device_id()
    token = issue_mobile_token(device_id=device_id, device_name=device_name or "Android device")
    return {
        "token": token,
        "token_type": "Bearer",
        "device_id": device_id,
        "expires_in": 60 * 60 * 24 * 30,
        "server": {
            "host": _lan_ip(),
            "port": _backend_port(),
        },
    }


def _unique_code_locked() -> str:
    for _ in range(100):
        code = f"{random.SystemRandom().randint(0, 999999):06d}"
        if code not in _pairings:
            return code
    raise HTTPException(status_code=503, detail="Unable to allocate a pairing code")


def _prune_expired_locked() -> None:
    now = time.time()
    for code, pairing in list(_pairings.items()):
        if pairing.used or pairing.expires_at < now:
            _pairings.pop(code, None)


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def _backend_port() -> int:
    import os

    return int(os.environ.get("MAVRIS_BACKEND_PORT") or os.environ.get("MARVIS_BACKEND_PORT") or "8000")


def _epoch_to_iso(value: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, timezone.utc).isoformat()
