import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(tempfile.mkdtemp(prefix='mavris-review-submission-'))
os.environ.update({
    'LENGRVIS_DATA_DIR': str(DATA),
    'LENGRVIS_CONFIG_FILE': str(DATA / 'absent.yaml'),
    'LENGRVIS_ENV_FILE': str(DATA / 'absent.env'),
    'LENGRVIS_TEST': '1',
    'LENGRVIS_AUDIT_HMAC_SECRET': 'local-review-test-secret',
    'LENGRVIS_NATIVE_CONFIRMATION_SECRET': 'local-review-native-secret',
    'LENGRVIS_PROVIDER_NAME': 'mock',
    'LENGRVIS_TASK_RECORDING_ENABLED': 'false',
})
sys.path.insert(0, str(ROOT / 'backend'))
from app.core import db, butler_store
from app.core.butler_models import ButlerMatter, MatterStatus
from app.core.schemas import Task, PlanStep, SafetyReview
from app.agents.orchestrator_agent import OrchestratorAgent
from app.orchestration.runtime_context import TaskRuntimeContext
from app.llm.registry import get_effective_settings
from app.policy.risk import SafetyVerdict
from app.policy.effective_risk_binding import build_effective_risk_binding
from app.orchestration.tool_execution_journal import mark_tool_call_executing, mark_tool_call_committed
from app.services.butler_effects import repeated_submission
from app.orchestration.tool_runtime import ToolRuntime
from app.orchestration.butler_runtime import validate_task_action

async def main():
    db.init_db()
    orchestrator = OrchestratorAgent()
    results = []
    for tool_name in ['browser.submit_form', 'browser.act']:
        task = Task(user_goal='Submit a test form once', metadata={'butler_matter_id': 'matter-' + tool_name, 'butler_execution_ticket': 'ticket'})
        matter = butler_store.save(ButlerMatter(id=task.metadata['butler_matter_id'], goal=task.user_goal, source='user', status=MatterStatus.RUNNING, active_task_id=task.id, task_ids=[task.id], execution_ticket='ticket'))
        db.upsert_model('tasks', task)
        action = {'url': 'https://example.com/test-form', 'selector': '#review-form', 'fields': {'reference': 'review-once'}}
        args = {**action, 'dry_run': False} if tool_name == 'browser.submit_form' else {'action': {'kind': 'submit', **action}, 'dry_run': False}
        step = PlanStep(task_id=task.id, agent_name='BrowserAgent', tool_name=tool_name, description='Submit once', args=args)
        runtime = TaskRuntimeContext.from_task(task, get_effective_settings(), orchestrator.bus)
        count = []
        tool = replace(orchestrator.registry.get(tool_name), execute=lambda a,c: (count.append(1) or {'ok': True}))
        before = repeated_submission(matter, tool_name, args)
        # Use real proposal redaction, risk binding, reservation and claim.
        # The external executor is replaced with a counter; no request is sent.
        review = SafetyReview(task_id=task.id, step_id=step.id, target_type='tool_call', verdict=SafetyVerdict.ALLOW, risk_level=tool.risk_level)
        db.upsert_model('safety_reviews', review)
        runtime.extra_context['effective_risk_binding'] = build_effective_risk_binding(tool.risk_level, [review])
        tool_runtime = ToolRuntime(orchestrator)
        call, created = tool_runtime._publish_tool_call_proposal(task, step, tool, runtime, args, approval_id=None)
        assert created
        call = mark_tool_call_executing(call)
        assert call is not None
        after_claim = repeated_submission(matter, tool_name, args)
        result = await tool_runtime._execute_tool_call(task, step, tool, runtime, call, args, threaded_tools=False, approval_id=None)
        after_commit = None
        continuation_allowed = None
        if result.ok:
            mark_tool_call_committed(call)
            after_commit = repeated_submission(matter, tool_name, args)
            continuation = Task(user_goal=task.user_goal, metadata=dict(task.metadata))
            db.upsert_model('tasks', continuation)
            def continue_matter(item):
                item.active_task_id = continuation.id
                item.task_ids.append(continuation.id)
            matter = butler_store.mutate(ButlerMatter, matter.id, continue_matter)
            continuation_allowed = validate_task_action(continuation, tool, args, live=True) is not None
        results.append({'tool': tool_name, 'previous_calls': 0, 'stored_args': call.args, 'guard_before_claim': before, 'guard_after_own_claim': after_claim, 'actual_executor_calls':len(count), 'ok': result.ok, 'error':result.error, 'guard_with_committed_prior':after_commit, 'continuation_guard_allows_same_submission':continuation_allowed})
    print(json.dumps(results, ensure_ascii=False, indent=2))

asyncio.run(main())
