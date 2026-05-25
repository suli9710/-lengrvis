from __future__ import annotations

from fastapi import APIRouter


router = APIRouter()

AGENTS = [
    "OrchestratorAgent",
    "PlannerAgent",
    "FileAgent",
    "DocumentAgent",
    "ComputerAgent",
    "AppAgent",
    "BrowserAgent",
    "SearchAgent",
    "MemoryAgent",
    "SafetyReviewAgent",
    "HumanGateAgent",
]


@router.get("/agents")
def agents():
    return [{"name": name, "status": "available"} for name in AGENTS]


@router.get("/agents/status")
def status():
    return {"agents": agents()}

