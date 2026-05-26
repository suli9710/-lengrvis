from __future__ import annotations

from app.orchestration.coordinator_worker import CoordinatorWorkerPolicy, WorkerRole, WorkerTaskKind, WorkerTaskSpec


def test_worker_specs_render_self_contained_prompt():
    spec = WorkerTaskSpec(
        goal="Implement runtime",
        kind=WorkerTaskKind.IMPLEMENTATION,
        prompt="Edit the runtime module only.",
        owned_paths=["backend/app/orchestration/tool_runtime.py"],
        completion_criteria=["pytest passes"],
        forbidden_actions=["Do not edit orchestrator_agent.py"],
    )

    prompt = spec.self_contained_prompt()

    assert "Role: worker" in prompt
    assert "Do not coordinate" in prompt
    assert "Goal: Implement runtime" in prompt
    assert "Owned paths:" in prompt
    assert "pytest passes" in prompt
    assert spec.is_write_task is True


def test_policy_batches_overlapping_write_tasks_serially_but_parallelizes_research():
    policy = CoordinatorWorkerPolicy()
    research = WorkerTaskSpec(
        goal="Research",
        kind=WorkerTaskKind.RESEARCH,
        prompt="Read files.",
        completion_criteria=["Report findings"],
    )
    write_a = WorkerTaskSpec(
        goal="A",
        kind=WorkerTaskKind.IMPLEMENTATION,
        prompt="Edit A.",
        owned_paths=["backend/app/orchestration"],
        completion_criteria=["Tests pass"],
    )
    write_b = WorkerTaskSpec(
        goal="B",
        kind=WorkerTaskKind.IMPLEMENTATION,
        prompt="Edit B.",
        owned_paths=["backend/app/orchestration/tool_runtime.py"],
        completion_criteria=["Tests pass"],
    )

    batches = policy.partition_batches([research, write_a, write_b])

    assert batches[0] == [research, write_a]
    assert batches[1] == [write_b]


def test_policy_requires_owned_paths_for_implementation_workers():
    policy = CoordinatorWorkerPolicy()
    spec = WorkerTaskSpec(
        goal="No ownership",
        kind=WorkerTaskKind.IMPLEMENTATION,
        prompt="Edit something.",
        completion_criteria=["Done"],
    )

    assert "implementation workers must declare owned_paths" in policy.review_spec(spec)


def test_policy_rejects_worker_specs_with_coordinator_responsibilities():
    policy = CoordinatorWorkerPolicy()
    spec = WorkerTaskSpec(
        goal="Run assigned verification",
        kind=WorkerTaskKind.VERIFICATION,
        prompt="Coordinate the worker pool and split tasks before running tests.",
        completion_criteria=["Report test result"],
    )

    assert "worker prompt must not assign coordinator responsibilities" in policy.review_spec(spec)


def test_coordinator_role_can_describe_coordination_work():
    policy = CoordinatorWorkerPolicy()
    spec = WorkerTaskSpec(
        goal="Plan worker fan-out",
        kind=WorkerTaskKind.RESEARCH,
        role=WorkerRole.COORDINATOR,
        prompt="Coordinate worker batches and split tasks by owned path.",
        completion_criteria=["Return worker specs"],
    )

    assert "worker prompt must not assign coordinator responsibilities" not in policy.review_spec(spec)
    assert "Role: coordinator" in spec.self_contained_prompt()
