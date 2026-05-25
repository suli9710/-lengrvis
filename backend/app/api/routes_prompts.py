from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.llm import prompts


router = APIRouter()


@router.get("/dev/prompts")
def list_prompts() -> dict:
    items: list[dict] = []
    for path in sorted(prompts._prompt_dir().glob("*.md")):
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "modified_at": _iso_timestamp(stat.st_mtime),
            }
        )
    return {"prompts": items}


@router.get("/dev/prompts/{name:path}")
def get_prompt(name: str, request: Request) -> dict:
    if Path(name).name != name:
        raise HTTPException(status_code=400, detail="Prompt name must be a file name")
    try:
        path = prompts.prompt_path(name)
        content = prompts.render_prompt(name, dict(request.query_params))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not content and not path.exists():
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"name": name, "content": content}


@router.post("/dev/prompts/reload")
def reload_prompts() -> dict:
    result = prompts.reload_prompt_cache()
    return {"status": "ok", **result}


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
