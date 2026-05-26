from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any
from urllib.parse import urlparse

from app.config import AppSettings
from app.llm.types import LLMCost, LLMUsage


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    tools: bool = False
    structured_json: bool = True
    vision: bool = False
    embeddings: bool = False
    prompt_cache: bool = False
    responses_api: bool = False
    reasoning_effort: bool = False
    usage_breakdown: bool = False
    local: bool = False
    cloud: bool = False


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_per_million_tokens_usd: float | None = None
    output_per_million_tokens_usd: float | None = None
    estimated: bool = True

    def estimate(self, usage: LLMUsage) -> LLMCost:
        if self.input_per_million_tokens_usd is None or self.output_per_million_tokens_usd is None:
            return LLMCost(estimated=True)
        input_cost = usage.prompt_tokens * self.input_per_million_tokens_usd / 1_000_000
        output_cost = usage.completion_tokens * self.output_per_million_tokens_usd / 1_000_000
        return LLMCost(
            input_cost_usd=round(input_cost, 8),
            output_cost_usd=round(output_cost, 8),
            total_cost_usd=round(input_cost + output_cost, 8),
            estimated=self.estimated or usage.estimated,
        )


@dataclass(frozen=True, slots=True)
class ModelProfile:
    model: str
    context_window: int
    max_output_tokens: int
    capabilities: ProviderCapabilities
    pricing: ModelPricing = field(default_factory=ModelPricing)
    known: bool = False
    family: str = ""


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    provider_name: str
    model: str
    base_url: str
    wire_api: str
    location: str
    active_backend: str
    capabilities: ProviderCapabilities
    model_profile: ModelProfile

    def estimate_cost(self, usage: LLMUsage) -> LLMCost:
        return self.model_profile.pricing.estimate(usage)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_OPENAI_PRICING: dict[str, ModelPricing] = {
    "gpt-4o-mini": ModelPricing(0.15, 0.60, estimated=True),
    "gpt-4o": ModelPricing(2.50, 10.0, estimated=True),
}


def profile_for_settings(
    settings: AppSettings,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    active_backend: str | None = None,
) -> ProviderProfile:
    name = (provider_name or settings.provider_name or "mock").lower()
    target_model = model or settings.model or "unknown"
    wire_api = (settings.wire_api or "chat_completions").lower()
    location = _location_for(name, base_url if base_url is not None else settings.base_url)
    capabilities = _capabilities_for(name, wire_api, location, settings)
    model_profile = _model_profile_for(target_model, settings, capabilities)
    return ProviderProfile(
        provider_name=name,
        model=target_model,
        base_url=base_url if base_url is not None else settings.base_url,
        wire_api=wire_api,
        location=location,
        active_backend=active_backend or name,
        capabilities=capabilities,
        model_profile=model_profile,
    )


def profile_for_provider(provider: Any, settings: AppSettings) -> ProviderProfile:
    provider_settings = getattr(provider, "settings", settings)
    backend = getattr(provider, "backend", None)
    provider_name = getattr(provider, "name", None) or getattr(provider_settings, "provider_name", settings.provider_name)
    active_backend = getattr(backend, "kind", None) or provider_name
    base_url = getattr(provider_settings, "base_url", settings.base_url)
    model = getattr(provider_settings, "model", settings.model)
    profile = profile_for_settings(
        provider_settings,
        provider_name=str(provider_name),
        model=str(model or settings.model),
        base_url=str(base_url or ""),
        active_backend=str(active_backend),
    )
    if str(provider_name).lower() == "onnx":
        capabilities = replace(profile.capabilities, local=True, cloud=False, usage_breakdown=False)
        model_profile = replace(profile.model_profile, capabilities=capabilities, pricing=ModelPricing())
        return replace(profile, location="local", capabilities=capabilities, model_profile=model_profile)
    return profile


def _model_profile_for(model: str, settings: AppSettings, capabilities: ProviderCapabilities) -> ModelProfile:
    normalized = (model or "").lower()
    context_window = max(1, int(settings.model_context_window or 1))
    max_output = max(1, int(settings.max_tokens or 1))
    pricing = _OPENAI_PRICING.get(normalized, ModelPricing())
    known = normalized in _OPENAI_PRICING
    family = normalized.split(":", 1)[0].split("-", 1)[0] if normalized else ""
    return ModelProfile(
        model=model,
        context_window=context_window,
        max_output_tokens=max_output,
        capabilities=capabilities,
        pricing=pricing,
        known=known,
        family=family,
    )


def _capabilities_for(
    provider_name: str,
    wire_api: str,
    location: str,
    settings: AppSettings,
) -> ProviderCapabilities:
    name = provider_name.lower()
    is_mock = name == "mock"
    is_onnx = name == "onnx"
    is_local = location == "local" or is_mock or is_onnx
    is_cloud = location == "cloud" and not is_mock
    openai_like = not is_onnx
    return ProviderCapabilities(
        tools=openai_like and not is_mock,
        structured_json=True,
        vision=openai_like and bool(settings.vision_model or not is_mock),
        embeddings=openai_like and not is_onnx,
        prompt_cache=False,
        responses_api=wire_api == "responses" and openai_like,
        reasoning_effort=wire_api == "responses" and bool(settings.model_reasoning_effort),
        usage_breakdown=openai_like and not is_mock,
        local=is_local,
        cloud=is_cloud,
    )


def _location_for(provider_name: str, base_url: str) -> str:
    name = provider_name.lower()
    if name in {"mock", "onnx", "ollama", "lmstudio", "llamacpp", "llama.cpp", "vllm_local", "local"}:
        return "local"
    parsed = urlparse(base_url or "")
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return "local"
    return "cloud"
