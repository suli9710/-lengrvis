"""Load and materialize the versioned real-LLM benchmark catalog."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "test_data" / "real_llm_benchmark" / "catalog.json"
REQUIRED_CATEGORIES = frozenset(
    {"read", "write", "browser", "document", "memory", "mobile", "developer"}
)
REQUIRED_ATTACK_VECTORS = frozenset(
    {
        "web_hidden_instruction",
        "pdf_office_injection",
        "ocr_injection",
        "mcp_tool_poisoning",
        "cross_agent_message",
        "memory_poisoning",
    }
)
MIN_REAL_LLM_BENCHMARK_CASES = 100
VALID_PHASES = frozenset(
    {
        "completed",
        "failed",
        "denied",
        "cancelled",
        "awaiting_approval",
    }
)
VALID_RISK_LEVELS = (
    "R0_READ_ONLY",
    "R1_OPEN_ONLY",
    "R2_REVERSIBLE_MODIFY",
    "R3_DESTRUCTIVE_OR_SYSTEM",
    "R4_FORBIDDEN_OR_HANDOFF",
)
RISK_ORDER = {risk: index for index, risk in enumerate(VALID_RISK_LEVELS)}


def load_real_llm_benchmark(
    path: Path = CATALOG_PATH,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_catalog(catalog)
    if errors:
        raise ValueError("invalid real-LLM benchmark catalog: " + "; ".join(errors))
    return catalog, materialize_cases(catalog)


def materialize_cases(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for scenario in catalog.get("scenarios") or []:
        for variant in catalog.get("variants") or []:
            prompt = f"{variant.get('prefix', '')}{scenario['prompt']}{variant.get('suffix', '')}"
            case = {
                "id": f"rllm-{scenario['id']}-{variant['id']}",
                "category": scenario["category"],
                "title": f"{scenario['title']} [{variant['id']}]",
                "entry": "runs",
                "message": prompt,
                "mode": "efficiency",
                "engine": "os",
                "expect": copy.deepcopy(scenario["expect"]),
                "benchmark": {
                    "schema_version": catalog["schema_version"],
                    "scenario_id": scenario["id"],
                    "variant_id": variant["id"],
                    "attack_vector": scenario["attack_vector"],
                    "evidence_kind": scenario.get("evidence_kind", "task_prompt"),
                },
            }
            if scenario.get("fixtures"):
                case["fixtures"] = copy.deepcopy(scenario["fixtures"])
            cases.append(case)
    return cases


def validate_catalog(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if catalog.get("schema_version") != "real-llm-benchmark-v1":
        errors.append("schema_version must be real-llm-benchmark-v1")
    variants = catalog.get("variants")
    scenarios = catalog.get("scenarios")
    if not isinstance(variants, list) or len(variants) < 2:
        errors.append("at least two benchmark variants are required")
        variants = []
    if not isinstance(scenarios, list) or not scenarios:
        errors.append("benchmark scenarios are required")
        scenarios = []

    if any(not isinstance(item, dict) for item in variants):
        errors.append("every variant must be an object")
    variant_ids = [
        str(item.get("id") or "") for item in variants if isinstance(item, dict)
    ]
    scenario_ids = [
        str(item.get("id") or "") for item in scenarios if isinstance(item, dict)
    ]
    if len(variant_ids) != len(set(variant_ids)) or any(
        not item for item in variant_ids
    ):
        errors.append("variant ids must be non-empty and unique")
    if len(scenario_ids) != len(set(scenario_ids)) or any(
        not item for item in scenario_ids
    ):
        errors.append("scenario ids must be non-empty and unique")
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        variant_id = str(variant.get("id") or "<missing>")
        for field in ("prefix", "suffix"):
            if not isinstance(variant.get(field, ""), str):
                errors.append(f"{variant_id}: variant {field} must be a string")

    categories: set[str] = set()
    attack_vectors: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            errors.append("every scenario must be an object")
            continue
        scenario_id = str(scenario.get("id") or "<missing>")
        category = str(scenario.get("category") or "")
        attack_vector = str(scenario.get("attack_vector") or "")
        categories.add(category)
        attack_vectors.add(attack_vector)
        if category not in REQUIRED_CATEGORIES:
            errors.append(f"{scenario_id}: unsupported category {category!r}")
        if not attack_vector:
            errors.append(f"{scenario_id}: attack_vector is required")
        if not str(scenario.get("title") or "").strip():
            errors.append(f"{scenario_id}: title is required")
        if not str(scenario.get("prompt") or "").strip():
            errors.append(f"{scenario_id}: prompt is required")
        expect = scenario.get("expect")
        if not isinstance(expect, dict):
            errors.append(f"{scenario_id}: expect.phase is required")
            continue
        phases = expect.get("phase")
        if (
            not isinstance(phases, list)
            or not phases
            or any(phase not in VALID_PHASES for phase in phases)
        ):
            errors.append(
                f"{scenario_id}: expect.phase must contain only supported phases"
            )
        plan_tools = expect.get("plan_tools")
        if plan_tools is not None and (
            not isinstance(plan_tools, list)
            or any(not isinstance(tool, str) or not tool.strip() for tool in plan_tools)
            or len(plan_tools) != len(set(plan_tools))
        ):
            errors.append(
                f"{scenario_id}: expect.plan_tools must contain unique non-empty strings"
            )
        global_risk = expect.get("global_risk")
        if global_risk is not None and global_risk not in VALID_RISK_LEVELS:
            errors.append(
                f"{scenario_id}: unsupported expect.global_risk {global_risk!r}"
            )
        fixtures = scenario.get("fixtures")
        if fixtures is not None:
            if not isinstance(fixtures, dict):
                errors.append(f"{scenario_id}: fixtures must be an object")
            else:
                for relative_path, content in fixtures.items():
                    path = Path(str(relative_path))
                    if (
                        not isinstance(relative_path, str)
                        or not relative_path.strip()
                        or path.is_absolute()
                        or ".." in path.parts
                    ):
                        errors.append(
                            f"{scenario_id}: fixture paths must stay within the task workspace"
                        )
                    if not isinstance(content, str):
                        errors.append(f"{scenario_id}: fixture content must be text")
    missing_categories = sorted(REQUIRED_CATEGORIES - categories)
    if missing_categories:
        errors.append("missing categories: " + ", ".join(missing_categories))
    missing_vectors = sorted(REQUIRED_ATTACK_VECTORS - attack_vectors)
    if missing_vectors:
        errors.append("missing adversarial vectors: " + ", ".join(missing_vectors))

    materialized_count = len(variants) * len(scenarios)
    if materialized_count < MIN_REAL_LLM_BENCHMARK_CASES:
        errors.append(
            f"materialized benchmark has {materialized_count} cases; minimum is {MIN_REAL_LLM_BENCHMARK_CASES}"
        )
    if not errors:
        materialized = materialize_cases(catalog)
        materialized_ids = [case["id"] for case in materialized]
        if len(materialized_ids) != len(set(materialized_ids)):
            errors.append("materialized benchmark case ids must be unique")
        if any(len(case["message"]) > 16000 for case in materialized):
            errors.append("materialized benchmark messages must fit the run API limit")
    return errors


def validate_catalog_tool_contract(
    catalog: dict[str, Any], tool_risks: Mapping[str, str]
) -> list[str]:
    """Check expected planner tools against the executable builtin registry."""

    errors: list[str] = []
    for scenario in catalog.get("scenarios") or []:
        if not isinstance(scenario, dict):
            continue
        scenario_id = str(scenario.get("id") or "<missing>")
        expect = scenario.get("expect") or {}
        plan_tools = expect.get("plan_tools") or []
        unknown = [tool for tool in plan_tools if tool not in tool_risks]
        if unknown:
            errors.append(
                f"{scenario_id}: unknown expected tool(s): {', '.join(unknown)}"
            )
            continue
        global_risk = expect.get("global_risk")
        if plan_tools and global_risk in RISK_ORDER:
            planned_risk = max(
                (tool_risks[tool] for tool in plan_tools), key=RISK_ORDER.__getitem__
            )
            if global_risk != planned_risk:
                errors.append(
                    f"{scenario_id}: expected risk {global_risk} does not match tool risk {planned_risk}"
                )
    return errors
