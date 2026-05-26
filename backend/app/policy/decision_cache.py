from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from app.policy.risk import RiskLevel, SafetyVerdict


@dataclass(slots=True)
class CachedDecision:
    verdict: SafetyVerdict
    risk_level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    expires_at: float = 0.0

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


class ToolDecisionCache:
    def __init__(
        self,
        max_entries: int = 256,
        ttl_seconds: float = 30.0,
        now_provider: Callable[[], float] | None = None,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._now_provider = now_provider or time.time
        self._items: OrderedDict[str, CachedDecision] = OrderedDict()

    def get(
        self,
        tool_name: str,
        args: dict[str, Any] | str | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> CachedDecision | None:
        key = tool_name if args is None or isinstance(args, str) else self._key(tool_name, args, context=context)
        item = self._items.get(key)
        if item is None:
            return None
        if item.expires_at <= self._now():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return item

    def put(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        verdict: SafetyVerdict | str,
        risk_level: RiskLevel | str,
        reasons: list[str] | None = None,
        reason: str = "",
        context: dict[str, Any] | None = None,
    ) -> None:
        if not self._cacheable(args, verdict, risk_level, context=context):
            return
        self._store(
            self._key(tool_name, args, context=context),
            verdict=verdict,
            risk_level=risk_level,
            reasons=list(reasons or ([reason] if reason else [])),
        )

    def set(self, key: str, *, verdict: SafetyVerdict | str, risk_level: RiskLevel | str, reason: str = "") -> None:
        self._store(key, verdict=verdict, risk_level=risk_level, reasons=[reason] if reason else [])

    def put_review(
        self,
        tool_name: str,
        args: dict[str, Any],
        review: Any,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.put(
            tool_name,
            args,
            verdict=review.verdict,
            risk_level=review.risk_level,
            reasons=list(getattr(review, "reasons", []) or []),
            context=context,
        )

    def clear(self) -> None:
        self._items.clear()

    def _store(
        self,
        key: str,
        *,
        verdict: SafetyVerdict | str,
        risk_level: RiskLevel | str,
        reasons: list[str],
    ) -> None:
        normalized_verdict = verdict if isinstance(verdict, SafetyVerdict) else SafetyVerdict(str(verdict))
        normalized_risk = risk_level if isinstance(risk_level, RiskLevel) else RiskLevel(str(risk_level))
        self._items[key] = CachedDecision(
            verdict=normalized_verdict,
            risk_level=normalized_risk,
            reasons=reasons,
            expires_at=self._now() + self.ttl_seconds,
        )
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def _now(self) -> float:
        return float(self._now_provider())

    def _key(self, tool_name: str, args: dict[str, Any], *, context: dict[str, Any] | None = None) -> str:
        payload = {"tool": tool_name, "args": args, "context": context or {}}
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return sha256(data.encode("utf-8")).hexdigest()

    def _cacheable(
        self,
        args: dict[str, Any],
        verdict: SafetyVerdict | str,
        risk_level: RiskLevel | str,
        *,
        context: dict[str, Any] | None = None,
    ) -> bool:
        normalized_verdict = verdict if isinstance(verdict, SafetyVerdict) else SafetyVerdict(str(verdict))
        normalized_risk = risk_level if isinstance(risk_level, RiskLevel) else RiskLevel(str(risk_level))
        context = context or {}
        if context.get("cache_scope") != "deterministic_fast_path":
            return False
        if normalized_verdict != SafetyVerdict.ALLOW:
            return False
        if normalized_risk not in {RiskLevel.R0_READ_ONLY, RiskLevel.R1_OPEN_ONLY}:
            return False
        if args.get("approved") or args.get("approval_id"):
            return False
        if args.get("dry_run") is False:
            return False
        return True


tool_decision_cache = ToolDecisionCache()
