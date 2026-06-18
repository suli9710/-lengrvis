"""One-off debug for approval resume test."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agents.planner_agent import PlannerAgent
from app.core import db
from app.core.schemas import Plan, PlanStep, RiskLevel
from app.main import create_app


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    os.environ["LENGRVIS_DATA_DIR"] = str(tmp / "data")
    os.environ["LENGRVIS_ALLOWED_DIRECTORIES"] = str(tmp)
    target = tmp / "approved-multi-step.txt"
    target.write_text("remove me", encoding="utf-8")
    db.init_db()

    async def spy_create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ANN001, ARG002
        approval_step = PlanStep(
            id="approval_step",
            task_id=task_id,
            order=1,
            agent_name="FileAgent",
            tool_name="file.trash",
            description="Move file to trash after approval.",
            args={"path": str(target)},
            expected_observation="file.trash completed.",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_approval=True,
        )
        follow_up = PlanStep(
            id="follow_up_step",
            task_id=task_id,
            order=2,
            agent_name="ComputerAgent",
            tool_name="system.get_info",
            description="Inspect system after approval.",
            args={},
            expected_observation="system.get_info completed.",
            risk_level=RiskLevel.R0_READ_ONLY,
            depends_on=[approval_step.id],
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            steps=[approval_step, follow_up],
            global_risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_user_approval=True,
        )

    with patch.object(PlannerAgent, "create_plan", spy_create_plan):
        client = TestClient(create_app())
        created = client.post(
            "/api/runs",
            json={"message": "delete approved file then inspect system", "mode": "efficiency", "engine": "os"},
        ).json()
        for _ in range(120):
            run = client.get(f"/api/runs/{created['run_id']}").json()
            if run["phase"] == "awaiting_approval":
                break
            time.sleep(0.05)
        approval = db.fetch_many("approvals", limit=10)[0]
        client.post(f"/api/approvals/{approval['id']}/approve")
        for _ in range(120):
            run = client.get(f"/api/runs/{created['run_id']}").json()
            if run["phase"] in {"completed", "failed", "denied"}:
                break
            time.sleep(0.05)
        task = db.fetch_one("tasks", approval["task_id"])
        plans = db.fetch_many("plans", "task_id = ?", (approval["task_id"],), limit=10)
        print("run phase:", run["phase"])
        print("task status:", task["status"], "summary:", task.get("final_summary"))
        for i, raw in enumerate(plans):
            plan = Plan.model_validate(raw)
            print(f"plan[{i}] id={plan.id} steps:", [(s.id, s.status) for s in plan.steps])


if __name__ == "__main__":
    main()
