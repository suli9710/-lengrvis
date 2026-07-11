from __future__ import annotations

import re
from pathlib import Path

from app.main import create_app

ALLOWLIST_PATH = Path(__file__).resolve().parents[2] / "desktop" / "src" / "shared" / "apiRequestAllowlist.ts"
RULE_RE = re.compile(r'\{\s*method:\s*"(GET|POST|PUT|PATCH|DELETE)",\s*template:\s*"([^"]+)"\s*\}')
GENERIC_MUTATING_RULES = {
    ("POST", "/api/chat"),
    ("POST", "/api/files/cluster"),
    ("POST", "/api/files/cleanup/scan"),
    ("POST", "/api/files/cleanup/plan"),
    ("POST", "/api/perception/voice/transcribe"),
}


def _allowlist_rules() -> list[tuple[str, str]]:
    source = ALLOWLIST_PATH.read_text(encoding="utf-8")
    return [(method, template) for method, template in RULE_RE.findall(source)]


def _route_shape(path: str) -> tuple[str, ...]:
    shape: list[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        if segment.startswith(":") or (segment.startswith("{") and segment.endswith("}")):
            shape.append(":param")
        else:
            shape.append(segment)
    return tuple(shape)


def _backend_route_shapes() -> set[tuple[str, tuple[str, ...]]]:
    routes: set[tuple[str, tuple[str, ...]]] = set()
    for route in create_app().routes:
        path = str(getattr(route, "path", "") or "")
        for method in getattr(route, "methods", set()) or set():
            routes.add((str(method).upper(), _route_shape(path)))
    return routes


def _backend_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in create_app().routes:
        path = str(getattr(route, "path", "") or "")
        for method in getattr(route, "methods", set()) or set():
            routes.add((str(method).upper(), path))
    return routes


def _template_matches_static_path(template: str, path: str) -> bool:
    template_segments = [segment for segment in template.split("/") if segment]
    path_segments = [segment for segment in path.split("/") if segment]
    if len(template_segments) != len(path_segments):
        return False
    return all(template_segment.startswith(":") or template_segment == path_segment for template_segment, path_segment in zip(template_segments, path_segments, strict=True))


def test_renderer_api_allowlist_matches_real_fastapi_routes() -> None:
    rules = _allowlist_rules()
    assert rules, "Renderer API route allowlist could not be parsed."
    assert len(rules) == len(set(rules)), "Renderer API route allowlist contains duplicate method/route entries."

    backend_routes = _backend_route_shapes()
    stale = [rule for rule in rules if (rule[0], _route_shape(rule[1])) not in backend_routes]

    assert stale == [], f"Renderer API allowlist contains routes absent from FastAPI: {stale}"


def test_renderer_generic_mutating_routes_require_explicit_security_review() -> None:
    actual = {rule for rule in _allowlist_rules() if rule[0] != "GET"}

    assert actual == GENERIC_MUTATING_RULES, (
        "Renderer generic API mutating routes changed. Use a dedicated typed IPC bridge for sensitive actions, "
        "or explicitly review and update GENERIC_MUTATING_RULES for a low-risk generic route."
    )


def test_dynamic_allowlist_templates_do_not_capture_unreviewed_static_routes() -> None:
    rules = set(_allowlist_rules())
    dynamic_rules = [rule for rule in rules if ":" in rule[1]]
    static_backend_routes = {
        (method, path) for method, path in _backend_routes() if "{" not in path and "}" not in path
    }
    collisions = sorted(
        (method, template, path)
        for method, template in dynamic_rules
        for route_method, path in static_backend_routes
        if route_method == method
        and _template_matches_static_path(template, path)
        and (method, path) not in rules
    )

    assert collisions == [], (
        "Dynamic renderer allowlist templates captured unreviewed static FastAPI routes. "
        f"Add an exact reviewed allowlist rule or move the sensitive route behind dedicated IPC: {collisions}"
    )
