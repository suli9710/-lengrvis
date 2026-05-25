from __future__ import annotations

from fastapi import APIRouter

from app.llm.local_provider import health_snapshot
from app.llm.onnx_provider import health_snapshot as onnx_health_snapshot
from app.llm.registry import get_effective_settings
from app.services import ollama_service
from app.services.settings_service import get_settings, test_llm_provider, update_settings


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


@router.get("/settings/local-llm/health")
def local_llm_health():
    return health_snapshot(get_effective_settings())


@router.get("/settings/llm/health")
def llm_health():
    return {"local": health_snapshot(get_effective_settings())}


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
