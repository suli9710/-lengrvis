from __future__ import annotations

import threading
from collections import deque


class RemoteInputRateLimiter:
    def __init__(self) -> None:
        self._event_times: deque[float] = deque()
        self._last_seen = 0.0
        self._lock = threading.Lock()

    def allow(self, now: float, *, window_seconds: float, max_events: int) -> bool:
        with self._lock:
            self._last_seen = now
            window_seconds = max(0.1, float(window_seconds))
            max_events = max(1, int(max_events))
            cutoff = now - window_seconds
            while self._event_times and self._event_times[0] <= cutoff:
                self._event_times.popleft()
            if len(self._event_times) >= max_events:
                return False
            self._event_times.append(now)
            return True

    def is_idle(self, now: float, ttl_seconds: float) -> bool:
        with self._lock:
            return self._last_seen > 0 and self._last_seen <= now - ttl_seconds


class RemoteInputRateLimiterStore:
    def __init__(self) -> None:
        self._limiters: dict[tuple[str, str], RemoteInputRateLimiter] = {}
        self._lock = threading.Lock()

    def limiter_for(
        self,
        grant_id: str,
        device_id: str,
        *,
        fallback: RemoteInputRateLimiter,
        now: float,
        window_seconds: float,
    ) -> RemoteInputRateLimiter:
        if not grant_id or not device_id:
            return fallback
        key = (grant_id, device_id)
        with self._lock:
            self._prune_locked(now, window_seconds=window_seconds)
            limiter = self._limiters.get(key)
            if limiter is None:
                limiter = RemoteInputRateLimiter()
                self._limiters[key] = limiter
            return limiter

    def clear(self) -> None:
        with self._lock:
            self._limiters.clear()

    def keys(self) -> set[tuple[str, str]]:
        with self._lock:
            return set(self._limiters)

    def _prune_locked(self, now: float, *, window_seconds: float) -> None:
        window_seconds = max(0.1, float(window_seconds))
        ttl_seconds = max(window_seconds * 2.0, window_seconds + 1.0)
        expired = [key for key, limiter in self._limiters.items() if limiter.is_idle(now, ttl_seconds)]
        for key in expired:
            self._limiters.pop(key, None)
