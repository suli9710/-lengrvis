from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel as PydanticBaseModel

from app.llm.local_provider import health_snapshot
from app.llm.onnx_provider import health_snapshot as onnx_health_snapshot
from app.llm.registry import get_effective_settings
from app.policy.permissions import PermissionPolicy, PermissionRule, PermissionStore
from app.services import ollama_service
from app.services.settings_service import (
    get_llm_cost_summary,
    get_llm_health,
    get_llm_profile,
    get_llm_usage,
    get_settings,
    test_llm_provider,
    update_settings,
)


router = APIRouter()


@router.get("/settings")
def settings():
    return get_settings()


@router.post("/settings")
def update(payload: dict):
    return update_settings(payload)


@router.post("/settings/test-llm-provider")
async def test_provider():
    return await test_llm_provider()


@router.get("/settings/permission-policy")
def permission_policy():
    return PermissionStore().get_policy().model_dump(mode="json")


@router.put("/settings/permission-policy")
def update_permission_policy(payload: PermissionPolicy):
    return PermissionStore().save_policy(payload).model_dump(mode="json")


@router.post("/settings/permission-policy/rules")
def upsert_permission_rule(payload: PermissionRule):
    return PermissionStore().add_rule(payload).model_dump(mode="json")


@router.delete("/settings/permission-policy/rules/{rule_id}")
def delete_permission_rule(rule_id: str):
    policy, deleted = PermissionStore().delete_rule(rule_id)
    return {"ok": deleted, "policy": policy.model_dump(mode="json")}


@router.get("/settings/local-llm/health")
def local_llm_health():
    return health_snapshot(get_effective_settings())


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
    return onnx_health_snapshot(get_effective_settings())


@router.get("/settings/ollama/status")
async def ollama_status():
    return await ollama_service.status()


@router.post("/settings/ollama/install")
async def ollama_install():
    return await ollama_service.install()


@router.post("/settings/ollama/pull")
async def ollama_pull(payload: dict = {}):
    model = payload.get("model")
    return await ollama_service.pull_model(model)


class InstallLocalModelRequest(PydanticBaseModel):
    model: str | None = None


@router.post("/settings/install-local-model")
async def install_local_model(payload: InstallLocalModelRequest = InstallLocalModelRequest()):
    """Install Ollama (if needed) and pull a local model.
    Returns final status. For streaming progress, use the WebSocket endpoint."""
    results = []
    async for progress in ollama_service.install_local_model(payload.model):
        results.append(progress)

    last = results[-1] if results else {"status": "error", "error": "No progress received"}
    ok = last.get("status") not in ("error",)
    return {
        "ok": ok,
        "model": payload.model or ollama_service.RECOMMENDED_MODEL,
        "progress": results,
        "final": last,
    }
