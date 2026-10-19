"""Derive Office completion requirements from declared conditions and user intent."""

from __future__ import annotations

import re

from app.core.butler_models import ButlerMatter

_KINDS = {
    "xlsx": re.compile(r"excel|xlsx|工作簿|电子表格", re.I),
    "docx": re.compile(r"word|docx", re.I),
}
_NEGATIVE = re.compile(
    r"(?:不(?:需要|要|用|必|生成|制作|创建|输出|保存)?|无需|别|不要|without|do\s+not|don't|\bno\b|\bnot\b)\s*.{0,12}$",
    re.I,
)
_OPEN = re.compile(r"打开|启动|\bopen\b|\blaunch\b", re.I)
_ACTION = re.compile(
    r"(?P<output>生成|制作|创建|编写|撰写|做|输出|导出|保存|另存为|写入|写|转换为|转换成|转成|整理为|整理成|改为|改成|换成|保留"
    r"|\b(?:generate|create|produce|make|draft|compose|export|save|write|output)\b)"
    r"|(?P<input>读取|读入|加载|导入|引用|根据|基于|使用|现有|输入|来源|取自|来自|从|将|把"
    r"|\b(?:read|load|import|use|using|from|existing|input|source|convert|transform|replace)\b|\bbased\s+on\b)"
    r"|(?P<conversion>\b(?:to|into|with)\b)"
    r"|(?P<open>打开|启动|\bopen\b|\blaunch\b)",
    re.I,
)
_ONLY = re.compile(
    r"只(?:要|需|生成|创建|保存|做|输出|导出|保留|编写|撰写|写)|改为|改成|换成|\bonly\b|\binstead\b|\breplace\b", re.I
)
_INPUT_SUFFIX = re.compile(
    r"^\s*(?:(?:数据|文件|文档|表格|工作簿|\b(?:data|file|document|workbook)\b)\s*)?"
    r"(?:(?:作为|为|as(?:\s+(?:an?|the))?)\s*)?(?:输入|来源|\b(?:input|source)\b)",
    re.I,
)


def _format_role(prefix: str, suffix: str) -> tuple[str, bool]:
    """The closest action distinguishes a source format from a deliverable."""
    if _INPUT_SUFFIX.search(suffix):
        return "input", False
    actions = list(_ACTION.finditer(prefix))
    if not actions:
        return "unspecified", bool(_NEGATIVE.search(prefix))
    action = actions[-1]
    role = action.lastgroup or "unspecified"
    if role == "conversion":
        # The first format is the source in both "convert Word to Excel" and
        # "replace Word with Excel"; the connector introduces its replacement.
        verb = r"\breplace\b" if action.group().lower() == "with" else r"\b(?:convert|transform)\b"
        role = "output" if re.search(verb, prefix[: action.start()], re.I) else "input"
    elif role == "input":
        # A source qualifier can sit inside an output phrase, as in
        # "生成来自 Excel 数据的 Word 报告". Its final 的 resumes the output action.
        outputs = [prior for prior in actions[:-1] if prior.lastgroup == "output"]
        sources = [
            prior
            for prior in actions
            if prior.lastgroup == "input" and prior.group() in {"来自", "从", "取自", "根据", "基于"}
        ]
        if (
            outputs
            and sources
            and sources[-1].start() > outputs[-1].start()
            and re.search(r"的\s*$", prefix[sources[-1].end() :])
        ):
            action, role = outputs[-1], "output"
    negative = bool(_NEGATIVE.search(prefix) or _NEGATIVE.search(prefix[: action.start()]))
    return role, negative


def office_requirements(matter: ButlerMatter) -> tuple[set[str], set[str], bool]:
    conditions = [condition for condition in matter.completion_conditions if condition.kind == "office_artifacts"]
    required = {kind for condition in conditions for kind in condition.artifact_kinds}
    contracted = bool(required)
    opened: set[str] = set()
    open_all = False
    no_open = False
    no_open_kinds: set[str] = set()
    # Only direct user intent participates. Readback data and source pages cannot
    # add output types or a requirement to launch an application.
    messages = [*matter.goal.split("用户更新："), *[str(value) for value in matter.answers.get("user_replies", [])]]
    for index, text in enumerate(messages):
        # Initial goal mentions cannot expand a declared output contract. Later
        # direct user updates can explicitly add or replace deliverables.
        allow_outputs = not contracted or index > 0
        for clause in re.split(r"[，,。；;\n]|\.\s+", text):
            present: set[str] = set()
            excluded: set[str] = set()
            only = bool(_ONLY.search(clause))
            for kind, pattern in _KINDS.items():
                for match in pattern.finditer(clause):
                    role, negative = _format_role(clause[: match.start()], clause[match.end() :])
                    if role in {"input", "open"}:
                        continue
                    if negative:
                        excluded.add(kind)
                        present.discard(kind)
                    elif role == "output" or only:
                        present.add(kind)
                        excluded.discard(kind)
            if present and only:
                required.intersection_update(present)
            if allow_outputs or only:
                required.update(present)
            required.difference_update(excluded)
            for match in _OPEN.finditer(clause):
                # Associate "open" with its following formats, rather than an
                # input or a different output mentioned earlier in the clause.
                next_action = _ACTION.search(clause, match.end())
                target = clause[match.end() : next_action.start() if next_action else len(clause)]
                mentioned = {kind for kind, pattern in _KINDS.items() if pattern.search(target)}
                if _NEGATIVE.search(clause[: match.start()]):
                    if mentioned:
                        no_open_kinds.update(mentioned)
                        opened.difference_update(mentioned)
                    else:
                        no_open = True
                        opened.clear()
                        open_all = False
                else:
                    no_open = False
                    if mentioned:
                        opened.update(mentioned)
                        no_open_kinds.difference_update(mentioned)
                    else:
                        open_all = True
    for condition in conditions:
        if condition.require_open and not no_open:
            opened.update(condition.artifact_kinds or required or {artifact.kind for artifact in matter.artifacts})
    if open_all and not no_open:
        opened.update(required or {artifact.kind for artifact in matter.artifacts})
    opened.difference_update(no_open_kinds)
    opened.intersection_update(required or {artifact.kind for artifact in matter.artifacts})
    return required, opened, bool(conditions)


def verify_office_completion(matter: ButlerMatter) -> tuple[bool, list[dict], str]:
    from app.core.schemas import now_iso
    from app.services.office_artifacts import verify_artifact

    required, opened, declared = office_requirements(matter)
    kinds = {artifact.kind for artifact in matter.artifacts}
    missing = required - kinds
    if missing or (declared and not matter.artifacts):
        names = ", ".join("Excel" if kind == "xlsx" else "Word" for kind in sorted(missing)) or "Office"
        return False, [], f"仍需完成请求中的 {names} 成果。"
    evidence = []
    try:
        for artifact in matter.artifacts:
            # Extra artifacts never weaken integrity checking, but need not be
            # opened unless the user or a completion condition requested it.
            verify_artifact(matter, artifact, require_open=artifact.kind in opened)
            evidence.append(
                {
                    "condition": "office_artifacts",
                    "artifact_id": artifact.id,
                    "kind": artifact.kind,
                    "matched": True,
                    "verified_at": now_iso(),
                }
            )
    except (OSError, ValueError) as exc:
        return False, evidence, str(exc)
    return True, evidence, "请求中的 Office 成果已核验。"
