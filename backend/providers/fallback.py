from __future__ import annotations

from typing import Any


class ProviderFallback:
    def __init__(self, providers: list[Any]) -> None:
        self.providers = providers

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                return provider.complete(prompt, **kwargs)
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("No providers configured.")


FallbackProvider = ProviderFallback


def fallback_complete(prompt: str, providers: list[Any], **kwargs: Any) -> Any:
    return ProviderFallback(providers).complete(prompt, **kwargs)


complete_with_fallback = fallback_complete

