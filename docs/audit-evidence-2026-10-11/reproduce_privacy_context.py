import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(tempfile.mkdtemp(prefix='mavris-review-privacy-context-'))
os.environ.update({'LENGRVIS_DATA_DIR':str(DATA), 'LENGRVIS_CONFIG_FILE':str(DATA/'absent.yaml'), 'LENGRVIS_ENV_FILE':str(DATA/'absent.env'), 'LENGRVIS_TEST':'1', 'LENGRVIS_AUDIT_HMAC_SECRET':'local-review-test-secret'})
sys.path.insert(0, str(ROOT/'backend'))
from app.config import AppSettings
from app.core import db, butler_store as store
from app.core.butler_models import ButlerMatter, MatterStatus
from app.indexer import embedding_service
from app.llm import registry
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.services.butler_service import ButlerCoordinator

MARK_GOAL = '仅本地演示目标青竹'
MARK_PREF = '我的演示偏好是蓝色宋体'
calls = []
settings = AppSettings(mode='efficiency', provider_name='openai_compatible', base_url='https://example.com/v1', api_key='inert-review-placeholder', allow_mock_fallback=False, allow_file_content_upload=False)

async def structured_chat(provider, messages, output_schema):
    body = str(messages)
    calls.append({'operation':'chat','mode':provider.settings.mode,'base_url':provider.settings.base_url,'contains_private_goal':MARK_GOAL in body,'contains_private_preference':MARK_PREF in body})
    return {'action':'ask','reason':'intercepted for local review', 'missing_fields':['example']}

async def embed(provider, texts, model=None):
    calls.append({'operation':'embedding','mode':provider.settings.mode,'base_url':provider.settings.base_url,'contains_private_preference':MARK_PREF in str(texts)})
    return [[1.0,0.0] for _ in texts]

async def main():
    db.init_db()
    private = store.save(ButlerMatter(goal=MARK_GOAL, mode='privacy', status=MatterStatus.PAUSED))
    current = store.save(ButlerMatter(goal='Organize ordinary public notes', mode='efficiency'))
    coordinator = ButlerCoordinator()
    store.set_control(coordination_enabled=True)
    await coordinator.decide(current)
    coordination_calls = calls.copy()
    calls.clear()
    store.set_control(coordination_enabled=False)
    await coordinator.remember_preference(MARK_PREF, 'privacy')
    await coordinator.decide(current)
    print(json.dumps({'coordination_enabled_case':coordination_calls,'coordination_disabled_memory_case':calls,'source_matter_mode':store.get(ButlerMatter,private.id).mode,'allow_file_content_upload':False,'network_requests_sent':0},ensure_ascii=False,indent=2))

with patch.object(registry,'get_effective_settings',lambda:settings), patch.object(embedding_service,'get_effective_settings',lambda:settings), patch.object(registry,'detect_local_backend',lambda:SimpleNamespace(kind='ollama',base_url='http://127.0.0.1:11434/v1',models=['local-test'])), patch.object(registry,'detect_onnx_backend',lambda _:None), patch.object(embedding_service,'get_local_embedding_provider',lambda _:None), patch.object(OpenAICompatibleProvider,'structured_chat',structured_chat), patch.object(OpenAICompatibleProvider,'embed',embed):
    asyncio.run(main())
