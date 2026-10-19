"""Matter-owned Office outputs, frozen provenance, reservations and readback."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter, MatterStatus
from app.core.office_models import OfficeArtifact
from app.core.paths import resolve_authorized
from app.core.schemas import Task, now_iso
from app.orchestration.resource_leases import hold_resource_lease
from app.orchestration.resource_state import resource_state
from app.services.butler_scopes import matter_scopes, path_in_scope
from app.services.office_documents import render_report, render_workbook, verify_document
from app.tools.filesystem_safety import ensure_mutation_path_safe, safe_create_bytes
from app.tools.tool_abort import raise_if_tool_aborted


def context_matter(context: dict) -> ButlerMatter:
    from app.orchestration.butler_runtime import bound_matter

    row = db.fetch_one("tasks", str(context.get("task_id") or ""))
    matter = bound_matter(Task.model_validate(row)) if row else None
    if not matter:
        raise ValueError("Office output tools require an active butler matter.")
    if store.control_state()["paused"] or matter.status in {
        MatterStatus.PAUSED,
        MatterStatus.CANCELLED,
        MatterStatus.COMPLETED,
    }:
        raise ValueError("事项已暂停或结束。")
    matter_scopes(matter)
    return matter


def artifact_path(matter: ButlerMatter, artifact: OfficeArtifact) -> Path:
    scopes = matter_scopes(matter)
    if not any("observe" in scope.allowed_actions and path_in_scope(artifact.path, scope) for scope in scopes):
        raise ValueError("成果目录授权已撤销或改变。")
    roots = [root for scope in scopes for root in scope.directories]
    path = resolve_authorized(artifact.path, roots)
    ensure_mutation_path_safe(path, roots, include_self=path.exists())
    return path


def find_artifact(matter: ButlerMatter, identity: str) -> OfficeArtifact:
    matches = [item for item in matter.artifacts if item.id == identity or item.path == identity]
    if len(matches) != 1:
        raise ValueError("成果不属于当前事项。")
    return matches[0]


def verify_artifact(matter: ButlerMatter, artifact: OfficeArtifact, *, require_open: bool = False) -> dict:
    path = artifact_path(matter, artifact)
    if artifact.state != "verified" or not artifact.sha256 or not path.is_file():
        raise ValueError("成果尚未完成保存和核验。")
    if path.stat().st_size > 8 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise ValueError("成果已被修改，请核对后继续；管家不会覆盖你的修改。")
    digest, projection = verify_document(path, artifact.kind, artifact.content)
    if digest != artifact.sha256:
        raise ValueError("成果已被修改，请核对后继续；管家不会覆盖你的修改。")
    if artifact.source_artifact_id:
        source = find_artifact(matter, artifact.source_artifact_id)
        verify_artifact(matter, source)
        if source.sha256 != artifact.source_sha256:
            raise ValueError("报告使用的工作簿版本已改变。")
    if require_open and not artifact.opened:
        raise ValueError("成果已保存，尚未确认在指定应用中打开。")
    return projection


def reviewed_sources(matter: ButlerMatter, rows) -> list[dict]:
    from app.services.butler_evidence import reviewed_results

    sources = {}
    for call, result in reviewed_results(matter):
        if call["tool_name"] not in {"browser.read_page", "browser.observe"}:
            continue
        data = result.output
        url = data.get("url")
        if url:
            sources[url] = (call, data)
    chosen = {}
    for row in rows:
        call, source = sources.get(row.source_url, (None, {}))
        text = str(source.get("text") or "")
        if (
            not call
            or source.get("truncated") is not False
            or not source.get("observed_at")
            or source.get("adapter") != "desktop_browser_host"
            or not text
            or row.quote not in text
        ):
            raise ValueError("资料需要先在内置浏览器完整读取；缺失、截断或无来源的内容不能生成成果。")
        for value in row.values:
            if value is not None and str(value) and str(value) not in row.quote:
                raise ValueError("表格字段必须来自该行的原文依据；请核对来源冲突或缺失字段。")
        chosen[row.source_url] = {
            "url": row.source_url,
            "title": str(source.get("title") or row.source_url),
            "observed_at": source["observed_at"],
            "tool_call_id": call["id"],
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    return list(chosen.values())


def planning_readbacks(matter: ButlerMatter) -> dict:
    """Carry complete reviewed data into the next planning run, never guesses.

    Planning often must stop after reading a previously unknown page/workbook.
    Its next run needs the real contents, not only a tool name or URL receipt.
    Oversized inputs are explicitly incomplete rather than silently clipped.
    """
    import json

    from app.services.butler_evidence import reviewed_results

    if not matter.artifacts and not any(c.kind == "office_artifacts" for c in matter.completion_conditions):
        return {"observations": [], "incomplete": False}
    latest = {}
    for call, result in reviewed_results(matter):
        if call["tool_name"] not in {"browser.read_page", "browser.observe", "document.read_artifact"}:
            continue
        data = result.output
        identity = data.get("artifact_id") or data.get("url")
        if not identity:
            continue
        latest[identity] = {
            "tool": call["tool_name"],
            "tool_call_id": call["id"],
            "data": {
                k: data[k]
                for k in (
                    "url",
                    "title",
                    "text",
                    "truncated",
                    "observed_at",
                    "adapter",
                    "artifact_id",
                    "path",
                    "sha256",
                    "verified",
                    "data",
                    "sources",
                    "has_more",
                )
                if k in data
            },
        }
    observations, size, incomplete = [], 0, False
    for item in latest.values():
        size += len(json.dumps(item, ensure_ascii=False))
        if size > 200_000:
            incomplete = True
            break
        observations.append(item)
    return {"observations": observations, "incomplete": incomplete}


def reserve(
    matter: ButlerMatter, kind: str, requested: str, content: dict, context: dict, source: OfficeArtifact | None = None
) -> OfficeArtifact:
    scopes = matter_scopes(matter)
    roots = [root for scope in scopes for root in scope.directories]
    path = resolve_authorized(requested, roots)
    if path.suffix.lower() != f".{kind}" or not path.parent.is_dir():
        raise ValueError("请选择已存在的授权目录与正确的 Office 文件扩展名。")
    if not any("file_modify" in scope.allowed_actions and path_in_scope(str(path), scope) for scope in scopes):
        raise ValueError("请先授权成果目录中的可撤销文件修改。")
    with hold_resource_lease(["office-artifact-reservations"], context):
        matter = store.get(ButlerMatter, matter.id)
        existing = next((a for a in matter.artifacts if a.kind == kind), None)
        if existing:
            if str(path) not in {existing.requested_path, existing.path} or existing.content != content:
                raise ValueError("事项已有固定成果内容或路径，请接续原成果，变更目标需新建事项。")
            return existing
        reserved = {a.path.casefold() for m in store.list_records(ButlerMatter, limit=None) for a in m.artifacts}
        candidate = path
        for number in range(1, 1001):
            if not candidate.exists() and str(candidate).casefold() not in reserved:
                break
            candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        else:
            raise ValueError("同名成果过多，请选择其他文件名。")
        ensure_mutation_path_safe(candidate, roots, include_self=False)
        artifact = OfficeArtifact(
            kind=kind,
            requested_path=str(path),
            path=str(candidate),
            title=content["title"],
            content=content,
            source_artifact_id=source.id if source else "",
            source_sha256=source.sha256 if source else "",
        )
        store.mutate(ButlerMatter, matter.id, lambda item: item.artifacts.append(artifact))
        return artifact


def create_artifact(artifact: OfficeArtifact, context: dict, *, dry_run: bool) -> dict:
    matter = context_matter(context)
    artifact = find_artifact(matter, artifact.id)
    path = artifact_path(matter, artifact)
    source = find_artifact(matter, artifact.source_artifact_id) if artifact.source_artifact_id else None
    if source:
        verify_artifact(matter, source)
    paths = [path] + ([artifact_path(matter, source)] if source else [])
    if artifact.state == "verified":
        verify_artifact(matter, artifact)
        return {
            "ok": True,
            "dry_run": dry_run,
            "artifact_id": artifact.id,
            "path": str(path),
            "verified": True,
            "reused": True,
            "sha256": artifact.sha256,
            "diff_preview": [],
            "_resource_state": [resource_state(p) for p in paths],
        }
    preview = {
        "action": "create_office_document",
        "path": str(path),
        "title": artifact.title,
        "rows": len(artifact.content["rows"]),
        "sources": len(artifact.content["sources"]),
    }
    if dry_run:
        if path.exists():
            raise ValueError("预留成果路径已有文件，请核对原执行结果，不能覆盖。")
        return {
            "ok": True,
            "dry_run": True,
            "path": str(path),
            "artifact_id": artifact.id,
            "diff_preview": [preview],
            "_resource_state": [resource_state(p) for p in paths],
        }
    from app.policy.execution_marker import execution_is_marked_approved

    if not execution_is_marked_approved(context):
        raise ValueError("Office writes must pass the existing runtime approval gate.")
    if path.exists():
        raise ValueError("成果路径已存在，请核对；不会覆盖或重复写入。")
    data = render_workbook(artifact.content) if artifact.kind == "xlsx" else render_report(artifact.content)
    digest = hashlib.sha256(data).hexdigest()

    def writing(item):
        record = find_artifact(item, artifact.id)
        record.state = "writing"
        record.sha256 = digest

    store.mutate(ButlerMatter, matter.id, writing)
    raise_if_tool_aborted(context)
    context_matter(context)  # Recheck pause, task ownership and scope immediately before the effect.
    roots = [root for scope in matter_scopes(matter) for root in scope.directories]
    safe_create_bytes(path, data, roots, context)
    actual, _ = verify_document(path, artifact.kind, artifact.content)
    if actual != digest:
        raise ValueError("保存后的成果发生变化，需要核对。")

    def completed(item):
        record = find_artifact(item, artifact.id)
        record.state = "verified"
        record.verified_at = now_iso()
        item.progress = (
            "表格已保存并核验，继续生成报告" if artifact.kind == "xlsx" else "报告已保存并核验，准备打开成果"
        )

    store.mutate(ButlerMatter, matter.id, completed)
    from app.tools.file_tools import _with_rollback_state

    return {
        "ok": True,
        "path": str(path),
        "artifact_id": artifact.id,
        "sha256": digest,
        "verified": True,
        "changed_paths": [str(path)],
        "rollback_info": _with_rollback_state({"trash_created_file": str(path)}, [path], roots, context),
    }


def execution_paths(tool_name: str, args: dict, context: dict) -> list[str] | None:
    if tool_name not in {"document.create_xlsx", "document.create_docx"}:
        return None
    matter = context_matter(context)
    kind = "xlsx" if tool_name.endswith("xlsx") else "docx"
    artifact = next((a for a in matter.artifacts if a.kind == kind), None)
    return [artifact.path if artifact else str(args.get("path", ""))] + (
        [str(args["source_path"])] if args.get("source_path") else []
    )


def public_artifacts(matter: ButlerMatter) -> list[dict]:
    from app.policy.redaction import redact_value

    result = []
    for artifact in matter.artifacts:
        valid = False
        reason = "成果尚未完成核验"
        try:
            verify_artifact(matter, artifact)
            valid, reason = True, "内容与来源已核验"
        except ValueError as exc:
            reason = str(exc)
        except OSError:
            reason = "成果无法读取，请关闭占用程序并检查文件权限后重试。"
        result.append(
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "name": redact_value(Path(artifact.path).name),
                "verified": valid,
                "opened": artifact.opened,
                "status": reason,
            }
        )
    return result


def open_bound_artifact(path: Path, application: str, context: dict) -> dict | None:
    if not context.get("butler_matter_id"):
        return None
    matter = context_matter(context)
    artifact = find_artifact(matter, str(path))
    if application != ("excel" if artifact.kind == "xlsx" else "word"):
        raise ValueError("指定应用与成果格式不匹配。")
    return open_artifact(matter, artifact, context)


def verified_office_preview(task: Task, tool, args: dict) -> bool:
    """Allow only the exact generated macro-free artifact to be previewed.

    Web content stays untrusted. It cannot select an arbitrary path/application:
    both come from the signed matter record, with fresh readback and prior root
    review of the creating tool. The Office adapter disables active content and
    opens read-only; the live tool repeats the artifact and scope checks.
    """
    from app.orchestration.butler_runtime import bound_matter
    from app.services.butler_evidence import reviewed_results

    if tool.name != "app.open_file" or tool.trust_tier != "builtin" or args.get("application") not in {"excel", "word"}:
        return False
    matter = bound_matter(task)
    if matter is None:
        return False
    try:
        artifact = find_artifact(matter, str(args.get("path") or ""))
        if args["application"] != ("excel" if artifact.kind == "xlsx" else "word"):
            return False
        verify_artifact(matter, artifact)
    except (OSError, ValueError):
        return False
    return any(
        call["tool_name"] == f"document.create_{artifact.kind}"
        and result.output.get("artifact_id") == artifact.id
        and result.output.get("sha256") == artifact.sha256
        for call, result in reviewed_results(matter)
    )


def open_artifact(matter: ButlerMatter, artifact: OfficeArtifact, context: dict, *, reopen: bool = False) -> dict:
    from app.services.office_application import open_office_document

    with hold_resource_lease([f"office-open:{artifact.id}"], context):
        matter = store.get(ButlerMatter, matter.id)
        artifact = find_artifact(matter, artifact.id)
        verify_artifact(matter, artifact)
        application = "excel" if artifact.kind == "xlsx" else "word"
        if artifact.opened and not reopen:
            return {
                "ok": True,
                "path": artifact.path,
                "application": application,
                "opened": True,
                "verified": True,
                "reused": True,
                "artifact_id": artifact.id,
            }
        result = open_office_document(
            Path(artifact.path),
            application,
            {
                **context,
                "office_matter_id": matter.id,
                "office_artifact_id": artifact.id,
            },
        )
        if result.get("ok") and result.get("verified"):
            # Opening must not have changed the saved content or authority.
            verify_artifact(store.get(ButlerMatter, matter.id), artifact)
            store.mutate(
                ButlerMatter, matter.id, lambda item: setattr(find_artifact(item, artifact.id), "opened", True)
            )
        return {**result, "artifact_id": artifact.id}
