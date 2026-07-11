from __future__ import annotations

import json
import sqlite3

from app.core import db_migrations


def test_memory_quarantine_migration_backfills_legacy_and_malformed_rows() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            task_id TEXT,
            embedding BLOB,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )
        """
    )
    envelope = {
        "source_kind": "agent_message",
        "source_id": "task_legacy",
        "origin": "PlannerAgent",
        "content_hash": "sha256:" + ("a" * 64),
        "trust_level": "unknown",
        "taint_flags": ["derived_content", "unreviewed_memory"],
        "observed_at": "2026-07-01T00:00:00+00:00",
        "task_scope": "task_legacy",
        "user_confirmed": False,
        "sanitizers_applied": [],
        "integrity_hmac": "",
    }
    rows = [
        (
            "mem_system",
            json.dumps(
                {
                    "source": "PlannerAgent",
                    "expires_at": "2026-08-01T00:00:00+00:00",
                    "content_envelope": envelope,
                }
            ),
        ),
        ("mem_user", json.dumps({"source": "user"})),
        ("mem_malformed", "{not-json"),
    ]
    conn.executemany(
        """
        INSERT INTO memories (id, kind, content, tags, task_id, embedding, data, created_at, last_used_at)
        VALUES (?, 'fact', 'legacy', '', '', NULL, ?, '2026-07-01T00:00:00+00:00', NULL)
        """,
        rows,
    )

    try:
        assert db_migrations.apply_schema_migrations(conn) == [1, 2, 3, 4]
        normalized = {
            row["memory_id"]: row
            for row in conn.execute("SELECT * FROM memory_quarantine ORDER BY memory_id").fetchall()
        }
        migrations = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()

        assert normalized["mem_system"]["state"] == "quarantined"
        assert normalized["mem_system"]["source"] == "PlannerAgent"
        assert normalized["mem_system"]["user_confirmed"] == 0
        assert normalized["mem_system"]["expires_at"] == "2026-08-01T00:00:00+00:00"
        assert normalized["mem_system"]["provenance_source_kind"] == "agent_message"
        assert normalized["mem_system"]["provenance_content_hash"] == envelope["content_hash"]
        assert json.loads(normalized["mem_system"]["provenance_taint_flags"]) == envelope["taint_flags"]
        assert normalized["mem_user"]["state"] == "active"
        assert normalized["mem_user"]["user_confirmed"] == 1
        assert normalized["mem_malformed"]["state"] == "quarantined"
        assert normalized["mem_malformed"]["source"] == "unknown"
        assert normalized["mem_malformed"]["user_confirmed"] == 0
        assert [(row["version"], row["name"]) for row in migrations][-1] == (
            4,
            "memory_quarantine_foundation",
        )

        state_index = conn.execute('PRAGMA index_info("idx_memory_quarantine_state_expiry")').fetchall()
        assert [row["name"] for row in state_index] == ["state", "expires_at", "memory_id"]
        source_index = conn.execute('PRAGMA index_info("idx_memory_quarantine_source_confirmation")').fetchall()
        assert [row["name"] for row in source_index] == ["source", "user_confirmed", "memory_id"]
        table_info = conn.execute('PRAGMA table_info("memory_quarantine")').fetchall()
        assert [row["name"] for row in table_info if row["pk"]] == ["memory_id"]
        not_null = {row["name"] for row in table_info if row["notnull"]}
        assert {"state", "source", "user_confirmed", "created_at", "updated_at"} <= not_null
        foreign_keys = conn.execute('PRAGMA foreign_key_list("memory_quarantine")').fetchall()
        assert any(
            row["from"] == "memory_id"
            and row["table"] == "memories"
            and row["to"] == "id"
            and row["on_delete"] == "CASCADE"
            for row in foreign_keys
        )
    finally:
        conn.close()


def test_memory_quarantine_migration_revalidates_index_shape() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                task_id TEXT,
                embedding BLOB,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT
            )
            """
        )
        db_migrations.apply_schema_migrations(conn)
        conn.execute("DROP INDEX idx_memory_quarantine_state_expiry")
        conn.execute("CREATE INDEX idx_memory_quarantine_state_expiry ON memory_quarantine(state)")

        try:
            db_migrations.apply_schema_migrations(conn)
        except RuntimeError as error:
            assert "idx_memory_quarantine_state_expiry" in str(error)
        else:
            raise AssertionError("migration validation accepted an invalid memory quarantine index")
    finally:
        conn.close()
