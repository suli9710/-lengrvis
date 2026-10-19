"""Office creation tools on the normal scoped, reversible runtime path."""

from __future__ import annotations

from app.core.office_models import CreateReport, CreateWorkbook
from app.policy.risk import RiskLevel
from app.services.office_artifacts import (
    context_matter,
    create_artifact,
    find_artifact,
    reserve,
    reviewed_sources,
    verify_artifact,
)
from app.tools.schemas import ToolDefinition


def create_xlsx(args: dict, context: dict) -> dict:
    request = CreateWorkbook.model_validate({k: v for k, v in args.items() if k not in {"approved", "approval_id"}})
    matter = context_matter(context)
    # Preserve frozen sources across restart, even if a later page observation changes.
    previous = next((a for a in matter.artifacts if a.kind == "xlsx"), None)
    content = request.model_dump(exclude={"path", "dry_run"})
    content["sources"] = previous.content["sources"] if previous else reviewed_sources(matter, request.rows)
    artifact = reserve(matter, "xlsx", request.path, content, context)
    return create_artifact(artifact, context, dry_run=request.dry_run)


def read_artifact(args: dict, context: dict) -> dict:
    matter = context_matter(context)
    artifact = find_artifact(matter, args["path"])
    data = verify_artifact(matter, artifact)
    return {
        "ok": True,
        "path": artifact.path,
        "artifact_id": artifact.id,
        "sha256": artifact.sha256,
        "verified": True,
        "data": data,
        "sources": artifact.content["sources"],
    }


def create_docx(args: dict, context: dict) -> dict:
    from app.services.butler_evidence import reviewed_results

    request = CreateReport.model_validate({k: v for k, v in args.items() if k not in {"approved", "approval_id"}})
    matter = context_matter(context)
    source = find_artifact(matter, request.source_path)
    if source.kind != "xlsx":
        raise ValueError("Word report requires this matter's verified workbook.")
    projection = verify_artifact(matter, source)
    if not any(
        call["tool_name"] == "document.read_artifact"
        and result.output.get("artifact_id") == source.id
        and result.output.get("sha256") == source.sha256
        for call, result in reviewed_results(matter)
    ):
        raise ValueError("请先用 document.read_artifact 读取实际保存的表格，再根据读取结果生成报告。")
    content = request.model_dump(exclude={"path", "source_path", "dry_run"})
    content.update(
        headers=projection["整理数据"][0][:-2],
        sources=source.content["sources"],
        rows=[{"values": row[:-2], "source_url": row[-2], "quote": row[-1]} for row in projection["整理数据"][1:]],
    )
    artifact = reserve(matter, "docx", request.path, content, context, source)
    return create_artifact(artifact, context, dry_run=request.dry_run)


def register(registry) -> None:
    for name, model, execute, description in [
        (
            "document.create_xlsx",
            CreateWorkbook,
            create_xlsx,
            "Create a new sourced Excel workbook in a butler matter. First read public pages in BrowserHost. "
            "Each row supplies source_url and verbatim quote; cell values must occur in that quote. "
            "Only one workbook per matter; existing names are reserved without overwriting. 表格 新建 Excel 来源",
        ),
        (
            "document.create_docx",
            CreateReport,
            create_docx,
            "Create a new Word report from this matter's saved Excel workbook. First use document.read_artifact "
            "and draft summary/sections from that readback. "
            "Actual data tables and citations are inserted automatically. "
            "Word 报告 新建",
        ),
    ]:
        registry.register(
            ToolDefinition(
                name=name,
                description=description,
                search_hint=description,
                input_schema=model.model_json_schema(),
                output_schema={"type": "object"},
                risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
                agent_owner="DocumentAgent",
                supports_dry_run=True,
                requires_authorized_path=True,
                execute=execute,
                read_only=False,
                concurrency_safe=False,
                effects=["write"],
                resource_kinds=["document"],
                fast_path_eligible=True,
                trust_tier="builtin",
                compensation_strength="reversible",
                validate_input=lambda args, context, model=model: model.model_validate(args),
            )
        )
    registry.register(
        ToolDefinition(
            name="document.read_artifact",
            description="Read and independently verify the complete saved Office artifact of this matter. "
            "Use its actual contents to prepare the Word report. 核验 读取 成果 Excel Word",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="DocumentAgent",
            supports_dry_run=False,
            requires_authorized_path=True,
            execute=read_artifact,
            read_only=True,
            concurrency_safe=True,
            effects=["read"],
            resource_kinds=["document"],
            fast_path_eligible=True,
            trust_tier="builtin",
            max_result_size=250000,
        )
    )
