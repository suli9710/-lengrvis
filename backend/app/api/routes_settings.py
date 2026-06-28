from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel as PydanticBaseModel

from app.commerce.entitlements import Feature, active_plan, require_feature
from app.indexer.local_embedding_provider import (
    health_snapshot as embedding_health_snapshot,
)
from app.indexer.local_embedding_provider import (
    test_embedding as onnx_test_embedding,
)
from app.indexer.ocr_service import accelerated_ocr_health, accelerated_ocr_smoke
from app.llm.local_provider import health_snapshot
from app.llm.onnx_provider import (
    health_snapshot as onnx_health_snapshot,
)
from app.llm.onnx_provider import (
    test_generate as onnx_test_generate,
)
from app.llm.onnx_provider import (
    warmup as onnx_warmup,
)
from app.llm.registry import get_effective_settings
from app.policy.permissions import PermissionPolicy, PermissionRule, PermissionStore
from app.policy.redaction import redact_public_text
from app.security.desktop_api import close_unauthorized_desktop_websocket
from app.security.middleware import reject_audit_fail_closed_websocket
from app.security.sensitive_confirmation import (
    create_permission_policy_confirmation,
    create_settings_confirmation,
    permission_delete_relaxations,
    permission_policy_relaxations,
    permission_rule_relaxations,
    require_permission_policy_confirmation,
)
from app.services import ollama_service
from app.services.settings_service import (
    coerce_settings_patch,
    get_llm_cost_summary,
    get_llm_health,
    get_llm_profile,
    get_llm_usage,
    get_settings,
    test_llm_provider,
    update_settings,
)
from app.tools.vision_tools import image_embedding_health, test_image_embedding

router = APIRouter()
ws_router = APIRouter()
_PUBLIC_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
_PUBLIC_PATH_KEYS = {"path", "model_path", "models_path", "manifest_path", "bundle_manifest_path"}
_PUBLIC_PATH_KEY_SUFFIXES = ("_path", "_dir", "_directory", "_file")


@router.get("/settings")
def settings():
    return get_settings()


@router.post("/settings")
def update(payload: dict):
    return update_settings(payload)


@router.post("/settings/confirm-sensitive-change")
def confirm_sensitive_change(payload: dict):
    return create_settings_confirmation(payload, confirmed_payload=coerce_settings_patch(payload))


@router.post("/settings/test-llm-provider")
async def test_provider():
    return await test_llm_provider()


@router.get("/settings/permission-policy")
def permission_policy():
    return PermissionStore().get_policy().model_dump(mode="json")


@router.put("/settings/permission-policy")
def update_permission_policy(payload: PermissionPolicy, confirmation_nonce: str = Query("")):
    require_feature(active_plan(get_effective_settings()), Feature.POLICY_MANAGEMENT)
    store = PermissionStore()
    relaxations = permission_policy_relaxations(store.get_policy(), payload)
    require_permission_policy_confirmation(relaxations, confirmation_nonce)
    return store.save_policy(payload).model_dump(mode="json")


@router.post("/settings/permission-policy/rules")
def upsert_permission_rule(payload: PermissionRule, confirmation_nonce: str = Query("")):
    require_feature(active_plan(get_effective_settings()), Feature.POLICY_MANAGEMENT)
    store = PermissionStore()
    relaxations = permission_rule_relaxations(store.get_policy(), payload)
    require_permission_policy_confirmation(relaxations, confirmation_nonce)
    return store.add_rule(payload).model_dump(mode="json")


@router.delete("/settings/permission-policy/rules/{rule_id}")
def delete_permission_rule(rule_id: str, confirmation_nonce: str = Query("")):
    require_feature(active_plan(get_effective_settings()), Feature.POLICY_MANAGEMENT)
    store = PermissionStore()
    relaxations = permission_delete_relaxations(store.get_policy(), rule_id)
    require_permission_policy_confirmation(relaxations, confirmation_nonce)
    policy, deleted = store.delete_rule(rule_id)
    return {"ok": deleted, "policy": policy.model_dump(mode="json")}


@router.post("/settings/permission-policy/confirm-relaxation")
def confirm_permission_policy_relaxation(payload: dict):
    store = PermissionStore()
    action = str(payload.get("action") or "").strip().lower()
    if action == "upsert_rule":
        changes = permission_rule_relaxations(
            store.get_policy(), PermissionRule.model_validate(payload.get("rule") or {})
        )
    elif action == "delete_rule":
        changes = permission_delete_relaxations(store.get_policy(), str(payload.get("rule_id") or ""))
    else:
        changes = permission_policy_relaxations(
            store.get_policy(), PermissionPolicy.model_validate(payload.get("policy") or {})
        )
    return create_permission_policy_confirmation(changes)


@router.get("/settings/local-llm/health")
def local_llm_health():
    return _public_local_model_payload(health_snapshot(get_effective_settings()))


@router.get("/settings/llm/health")
def llm_health():
    settings = get_effective_settings()
    payload = get_llm_health()
    if (settings.mode or "efficiency").lower() in {"privacy", "hybrid"}:
        payload["local"] = health_snapshot(settings)
    return payload


@router.get("/settings/llm/profile")
def llm_profile():
    return get_llm_profile()


@router.get("/settings/llm/usage")
def llm_usage(limit: int = 100):
    return get_llm_usage(limit=limit)


@router.get("/settings/llm/cost-summary")
def llm_cost_summary(hours: int = 24):
    return get_llm_cost_summary(hours=hours)


@router.get("/settings/onnx/status")
def onnx_status():
    settings = get_effective_settings()
    payload = onnx_health_snapshot(settings)
    payload["text_embedding"] = embedding_health_snapshot(settings)
    payload["image_embedding"] = image_embedding_health(settings)
    payload["ocr"] = accelerated_ocr_health(settings)
    return payload


class OnnxSmokeRequest(PydanticBaseModel):
    prompt: str | None = None
    max_tokens: int | None = None
    model_path: str | None = None
    texts: list[str] | None = None
    image_path: str | None = None


@router.post("/settings/onnx/warmup")
def onnx_warmup_route(payload: OnnxSmokeRequest = OnnxSmokeRequest()):
    return onnx_warmup(get_effective_settings(), model_path=payload.model_path)


@router.post("/settings/onnx/test-generate")
async def onnx_test_generate_route(payload: OnnxSmokeRequest = OnnxSmokeRequest()):
    return await onnx_test_generate(
        get_effective_settings(),
        prompt=payload.prompt or "Say hello from ONNX.",
        max_tokens=payload.max_tokens or 16,
        model_path=payload.model_path,
    )


@router.post("/settings/onnx/test-embedding")
def onnx_test_embedding_route(payload: OnnxSmokeRequest = OnnxSmokeRequest()):
    return onnx_test_embedding(get_effective_settings(), texts=payload.texts)


@router.post("/settings/onnx/test-ocr")
def onnx_test_ocr_route(payload: OnnxSmokeRequest = OnnxSmokeRequest()):
    return accelerated_ocr_smoke(get_effective_settings())


@router.post("/settings/onnx/test-image-embedding")
def onnx_test_image_embedding_route(payload: OnnxSmokeRequest = OnnxSmokeRequest()):
    return test_image_embedding(get_effective_settings(), image_path=payload.image_path)


@router.get("/settings/ollama/status")
async def ollama_status():
    return await ollama_service.status()


@router.get("/settings/local-model/setup-plan")
async def local_model_setup_plan(model: str | None = None):
    try:
        target = _allowed_install_model(model) if model else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await ollama_service.setup_plan(target)


@router.get("/settings/local-model/readiness")
def local_model_readiness(model: str | None = None):
    try:
        target = _allowed_install_model(model) if model else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ollama_service.hardware_readiness(target)


@router.post("/settings/ollama/install")
async def ollama_install():
    return await ollama_service.install()


@router.post("/settings/ollama/start")
async def ollama_start():
    return await ollama_service.start_server()


@router.post("/settings/ollama/pull")
async def ollama_pull(payload: dict | None = None):
    try:
        model = _allowed_install_model((payload or {}).get("model"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await ollama_service.pull_model(model)


class InstallLocalModelRequest(PydanticBaseModel):
    model: str | None = None


@router.post("/settings/install-local-model")
async def install_local_model(payload: InstallLocalModelRequest = InstallLocalModelRequest()):
    """Install Ollama (if needed) and pull a local model.
    Returns final status. For streaming progress, use the WebSocket endpoint."""
    results = []
    try:
        model = _allowed_install_model(payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async for progress in ollama_service.install_local_model(model):
        results.append(progress)

    last = results[-1] if results else {"status": "error", "error": "No progress received"}
    ok = last.get("status") not in ("error",)
    return {
        "ok": ok,
        "model": model,
        "progress": results,
        "final": last,
    }


@ws_router.websocket("/ws/settings/install-local-model")
async def install_local_model_stream(websocket: WebSocket, model: str | None = None):
    if await close_unauthorized_desktop_websocket(websocket):
        return
    await reject_audit_fail_closed_websocket(websocket)
    try:
        model = _allowed_install_model(model)
    except ValueError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        async for progress in ollama_service.install_local_model(model):
            await websocket.send_json(progress)
    except WebSocketDisconnect:
        return


def _allowed_install_model(model: str | None) -> str:
    return ollama_service.normalize_install_model(model)


def _public_local_model_payload(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(item_key): _public_local_model_payload(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_public_local_model_payload(item, key) for item in value]
    if isinstance(value, str):
        normalized_key = key.replace("-", "_").casefold()
        if _public_local_model_secret_location_key(normalized_key):
            return ""
        without_urls = _PUBLIC_URL_RE.sub("[REDACTED_URL]", value)
        return redact_public_text(without_urls)
    return value


def _public_local_model_secret_location_key(normalized_key: str) -> bool:
    return (
        normalized_key.endswith("url")
        or normalized_key in _PUBLIC_PATH_KEYS
        or normalized_key.endswith(_PUBLIC_PATH_KEY_SUFFIXES)
        or "path" in normalized_key
    )
