from __future__ import annotations

import json

import pytest

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import AutonomyScope, ButlerMatter


def test_matter_survives_store_reload_and_rejects_stale_responses():
    matter = store.save(ButlerMatter(goal="整理资料"))
    updated = store.mutate(ButlerMatter, matter.id, lambda item: item.answers.update(format="表格"), expected_version=1)
    db.close_thread_connection()
    assert store.get(ButlerMatter, matter.id).answers == {"format": "表格"}
    assert updated.version == 2
    with pytest.raises(store.ButlerConflict):
        store.save(matter, expected_version=1)


def test_scope_cannot_be_forged_by_editing_its_database_record(tmp_path):
    scope = store.save(AutonomyScope(label="工作目录", directories=[str(tmp_path)], allowed_actions=["observe"]))
    with db.connect() as conn:
        data = json.loads(conn.execute("SELECT data FROM autonomy_scopes WHERE id=?", (scope.id,)).fetchone()[0])
        data["allowed_actions"].append("file_modify")
        conn.execute("UPDATE autonomy_scopes SET data=? WHERE id=?", (json.dumps(data), scope.id))
    with pytest.raises(db.SensitiveRecordIntegrityError):
        store.get(AutonomyScope, scope.id)


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com/private",
        "https://*.example.com",
        "https://u:p@example.com",
        "https://example.com?key=secret",
    ],
)
def test_scope_requires_exact_origins(origin):
    with pytest.raises(ValueError):
        AutonomyScope(label="web", origins=[origin])


def test_privacy_erase_removes_matters_scopes_and_their_integrity_records():
    store.save(ButlerMatter(goal="private matter"))
    store.save(AutonomyScope(label="private origin", origins=["https://example.com"]))
    counts = db.erase_local_user_data()
    assert counts["butler_matters"] == counts["autonomy_scopes"] == 1
    assert not store.list_records(ButlerMatter)
    assert not store.list_records(AutonomyScope)
    assert db.sensitive_integrity_check()["ok"]


def test_invalid_revision_does_not_corrupt_persisted_matter():
    matter = store.save(ButlerMatter(goal="有效事项"))
    with pytest.raises(ValueError):
        store.mutate(ButlerMatter, matter.id, lambda item: setattr(item, "goal", "长" * 12001))
    assert store.get(ButlerMatter, matter.id).goal == "有效事项"
