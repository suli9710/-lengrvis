from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core import butler_store as store
from app.core.butler_models import (
    AutonomyScope,
    ButlerMatter,
    ButlerMessage,
    MatterArrangement,
    MatterResponse,
    ScopeAction,
)
from app.core.office_models import OpenArtifact
from app.policy.approval_binding import preview_hmac
from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_ID_HEADER,
    NATIVE_CONFIRMATION_SIGNATURE_HEADER,
    NATIVE_CONFIRMATION_TIMESTAMP_HEADER,
    create_native_confirmation_challenge,
    enforce_native_confirmation_challenge_rate_limit,
    require_native_confirmation,
)
from app.services.butler_scopes import create_scope
from app.services.butler_service import get_butler, public_matter

router = APIRouter(prefix="/butler")


@router.post("/matters/{matter_id}/artifacts/{artifact_id}/open")
def open_office_artifact(matter_id: str, artifact_id: str, body: OpenArtifact):
    from app.services.office_artifacts import find_artifact, open_artifact

    try:
        matter = store.get(ButlerMatter, matter_id)
        if matter is None:
            raise KeyError(matter_id)
        if matter.version != body.version:
            raise store.ButlerConflict("事项已更新，请刷新后打开成果。")
        result = open_artifact(matter, find_artifact(matter, artifact_id), {}, reopen=True)
        return {"ok": bool(result.get("ok")), "artifact_id": artifact_id, "opened": bool(result.get("opened"))}
    except (ValueError, KeyError, OSError) as exc:
        raise _error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: redact third-party COM failures at the HTTP boundary.
        raise HTTPException(503, "指定 Office 应用暂时无法打开，成果已保留，可稍后重试。") from exc


class ScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default="", max_length=256)
    version: int | None = Field(default=None, ge=1)
    label: str = Field(min_length=1, max_length=160)
    directories: list[str] = Field(default_factory=list, max_length=32)
    applications: list[str] = Field(default_factory=list, max_length=32)
    origins: list[str] = Field(default_factory=list, max_length=32)
    allowed_actions: list[ScopeAction] = Field(default_factory=lambda: ["observe"], max_length=4)
    account_id: str = Field(default="", max_length=256)


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str


def _error(exc: Exception) -> HTTPException:
    status = 409 if isinstance(exc, store.ButlerConflict) else 404 if isinstance(exc, KeyError) else 400
    return HTTPException(status, "事项不存在。" if isinstance(exc, KeyError) else str(exc))


@router.get("/overview")
def overview():
    from app.core.butler_models import TERMINAL_MATTER_STATUSES
    from app.services.butler_evidence import undo_available

    records = store.list_records(ButlerMatter, limit=None)
    from app.services.butler_coordination import coordination_view

    state = store.control_state()
    coordination = coordination_view(records, enabled=state["coordination_enabled"])
    matters = [item for item in records if item.status not in TERMINAL_MATTER_STATUSES]
    matters.extend(item for item in records[:200] if item.status in TERMINAL_MATTER_STATUSES)
    payloads = []
    for matter in matters:
        payload = public_matter(matter)
        payload["undo_available"] = bool(matter.task_ids) and undo_available(matter)
        payload["coordination"] = coordination[matter.id]
        payloads.append(payload)
    return {
        **state,
        "matters": payloads,
        "scopes": [scope.model_dump(mode="json") for scope in store.list_records(AutonomyScope)],
        "defaults": {"event_merge_seconds": 30, "background_concurrency": 2, "desktop_concurrency": 1},
    }


@router.post("/messages")
async def message(request: ButlerMessage):
    try:
        return await get_butler().message(request)
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc


@router.post("/matters/{matter_id}/respond")
async def respond(matter_id: str, request: MatterResponse):
    try:
        return await get_butler().respond(matter_id, request)
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc


@router.post("/matters/{matter_id}/control")
async def matter_control(matter_id: str, request: ControlRequest):
    try:
        return public_matter(await get_butler().control_matter(matter_id, request.action))
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc


@router.post("/control")
async def control(request: ControlRequest):
    if request.action in {"pause", "resume"}:
        return await get_butler().pause(request.action == "pause")
    if request.action in {"enable", "disable"}:
        return store.set_control(enabled=request.action == "enable")
    if request.action in {"auto_memory_enable", "auto_memory_disable"}:
        return store.set_control(auto_memory_enabled=request.action == "auto_memory_enable")
    if request.action in {"coordination_enable", "coordination_disable"}:
        state = store.set_control(coordination_enabled=request.action == "coordination_enable")
        if request.action == "coordination_disable":
            from app.services.butler_notifications import flush_notifications

            flush_notifications(force=True)
        return state
    raise HTTPException(400, "不支持的管家控制操作。")


@router.post("/matters/{matter_id}/arrangement")
def arrange_matter(matter_id: str, request: MatterArrangement):
    from app.services.butler_coordination import update_arrangement

    try:
        return public_matter(update_arrangement(matter_id, request))
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc


def _scope_input(request: ScopeRequest) -> AutonomyScope:
    if bool(request.id) != (request.version is not None):
        raise ValueError("调整授权必须携带范围 ID 与当前版本。")
    value = request.model_dump(exclude={"id", "version"})
    if request.id:
        current = store.get(AutonomyScope, request.id)
        if current is None:
            raise KeyError(request.id)
        if current.version != request.version:
            raise store.ButlerConflict("授权范围已改变，请刷新后重试。")
        value.update(id=current.id, version=current.version, created_at=current.created_at)
    return AutonomyScope(**value)


@router.post("/scopes")
async def grant_scope(request: ScopeRequest, http_request: Request):
    digest = preview_hmac(request.model_dump())
    native = _require_native(
        http_request,
        action="butler_scope_create",
        endpoint="/api/butler/scopes",
        identity="butler_scope_create",
        digest=digest,
    )
    try:
        scope = _scope_input(request)
        scope.authorization = native
        saved = create_scope(scope, expected_version=request.version)
        if request.id:
            await get_butler().invalidate_scope_work(saved.id)
        return saved.model_dump(mode="json")
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc


@router.post("/scopes/native-confirmation-challenge")
def scope_confirmation(request: ScopeRequest, http_request: Request):
    try:
        _scope_input(request)
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc
    return _challenge(
        http_request,
        action="butler_scope_create",
        endpoint="/api/butler/scopes",
        identity="butler_scope_create",
        digest=preview_hmac(request.model_dump()),
    )


@router.post("/scopes/{scope_id}/revoke")
async def revoke_scope(scope_id: str, request: VersionRequest):
    try:
        return (await get_butler().revoke_scope(scope_id, request.version)).model_dump(mode="json")
    except (ValueError, KeyError) as exc:
        raise _error(exc) from exc


@router.post("/matters/{matter_id}/undo")
async def undo_matter(matter_id: str, request: VersionRequest, http_request: Request):
    from app.orchestration.task_rollback_workflow import RollbackSource, TaskRollbackRequest, execute_task_rollback

    matter = store.get(ButlerMatter, matter_id)
    if matter is None:
        raise HTTPException(404, "事项不存在。")
    if matter.version != request.version:
        raise HTTPException(409, "事项已改变，请刷新后再撤销。")
    inventory = _undo_inventory(matter)
    digest = preview_hmac({"matter_id": matter.id, "version": matter.version, "inventory": inventory})
    native = _require_native(
        http_request,
        action="butler_undo",
        endpoint=f"/api/butler/matters/{matter_id}/undo",
        identity=matter_id,
        digest=digest,
    )
    await get_butler().control_matter(matter_id, "pause")

    # Mark before rollback: partial/failed reversal must never release dependents.
    def invalidate_outcome(item: ButlerMatter) -> None:
        item.outcome_revoked = True
        item.outcome_revision += 1

    store.mutate(ButlerMatter, matter_id, invalidate_outcome)
    outcomes = []
    for task_id, inventory_hmac in reversed(list(inventory.items())):
        result = await execute_task_rollback(
            TaskRollbackRequest(
                task_id=task_id,
                source=RollbackSource.NATIVE_CONFIRMATION,
                confirmation_id=str(native["confirmation_id"]),
                expected_inventory_hmac=inventory_hmac,
                actor="user",
            )
        )
        outcomes.append(result.outcome)
        if result.outcome.get("state") not in {"completed", "no_changes", "succeeded"}:
            break
    updated = store.mutate(
        ButlerMatter, matter.id, lambda item: setattr(item, "progress", "已处理撤销请求，请查看回滚结果。")
    )
    return {"matter": public_matter(updated), "outcomes": outcomes}


@router.post("/matters/{matter_id}/undo/native-confirmation-challenge")
def undo_confirmation(matter_id: str, request: VersionRequest, http_request: Request):
    matter = store.get(ButlerMatter, matter_id)
    if matter is None:
        raise HTTPException(404, "事项不存在。")
    if matter.version != request.version:
        raise HTTPException(409, "事项已改变，请刷新后重试。")
    inventory = _undo_inventory(matter)
    digest = preview_hmac({"matter_id": matter.id, "version": matter.version, "inventory": inventory})
    return _challenge(
        http_request,
        action="butler_undo",
        endpoint=f"/api/butler/matters/{matter_id}/undo",
        identity=matter_id,
        digest=digest,
    )


def _undo_inventory(matter: ButlerMatter) -> dict[str, str]:
    from app.services.butler_evidence import undo_inventory

    inventory = undo_inventory(matter)
    if not inventory:
        raise HTTPException(409, "目前没有可整体撤销的执行，或事项包含需要单独处理的外部操作。")
    return inventory


def _challenge(request: Request, *, action: str, endpoint: str, identity: str, digest: str) -> dict:
    enforce_native_confirmation_challenge_rate_limit(request.client.host if request.client else "local")
    return create_native_confirmation_challenge(
        action=action, endpoint=endpoint, approval_id=identity, preview_hmac=digest
    )


def _require_native(request: Request, *, action: str, endpoint: str, identity: str, digest: str) -> dict:
    return require_native_confirmation(
        action=action,
        endpoint=endpoint,
        approval_id=identity,
        preview_hmac=digest,
        confirmation_id=request.headers.get(NATIVE_CONFIRMATION_ID_HEADER, ""),
        timestamp=request.headers.get(NATIVE_CONFIRMATION_TIMESTAMP_HEADER, ""),
        signature=request.headers.get(NATIVE_CONFIRMATION_SIGNATURE_HEADER, ""),
    )
