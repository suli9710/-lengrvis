AUTOMATION_FOUNDATION = """
        CREATE TABLE IF NOT EXISTS automation_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            current_version INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_templates_updated
            ON automation_templates(updated_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS automation_template_versions (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(template_id, version),
            FOREIGN KEY(template_id) REFERENCES automation_templates(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_automation_template_versions_template
            ON automation_template_versions(template_id, version DESC);

        CREATE TABLE IF NOT EXISTS automation_triggers (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(template_id) REFERENCES automation_templates(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_automation_triggers_template_enabled
            ON automation_triggers(template_id, enabled, updated_at);

        CREATE TABLE IF NOT EXISTS application_grants (
            id TEXT PRIMARY KEY,
            app_id TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_application_grants_app_status_expiry
            ON application_grants(app_id, status, expires_at);

        CREATE TABLE IF NOT EXISTS automation_runs (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            template_version INTEGER NOT NULL,
            task_id TEXT,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_runs_template_status
            ON automation_runs(template_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS automation_run_items (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            status TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, item_key),
            FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_automation_run_items_run_status
            ON automation_run_items(run_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS execution_exceptions (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            item_id TEXT,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_execution_exceptions_run_status
            ON execution_exceptions(run_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS intent_capsules (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            plan_revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_intent_capsules_task_status_expiry
            ON intent_capsules(task_id, status, expires_at);

        CREATE TABLE IF NOT EXISTS run_budget_ledgers (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_run_budget_ledgers_status_updated
            ON run_budget_ledgers(status, updated_at);
        """


MOBILE_IDENTITY_FOUNDATION = """
        CREATE TABLE IF NOT EXISTS device_credentials (
            id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            credential_type TEXT NOT NULL,
            status TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            FOREIGN KEY(device_id) REFERENCES mobile_devices(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_device_credentials_device_status
            ON device_credentials(device_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS token_families (
            id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            credential_id TEXT NOT NULL,
            status TEXT NOT NULL,
            current_generation INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            reuse_detected_at TEXT,
            FOREIGN KEY(device_id) REFERENCES mobile_devices(id) ON DELETE CASCADE,
            FOREIGN KEY(credential_id) REFERENCES device_credentials(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_token_families_device_status_expiry
            ON token_families(device_id, status, expires_at);

        CREATE TABLE IF NOT EXISTS mobile_refresh_tokens (
            id TEXT PRIMARY KEY,
            family_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            secret_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            used_at TEXT,
            replaced_by_id TEXT,
            FOREIGN KEY(family_id) REFERENCES token_families(id) ON DELETE CASCADE,
            FOREIGN KEY(device_id) REFERENCES mobile_devices(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_refresh_tokens_family_generation
            ON mobile_refresh_tokens(family_id, generation);
        CREATE INDEX IF NOT EXISTS idx_mobile_refresh_tokens_device_status
            ON mobile_refresh_tokens(device_id, status, updated_at);
        """


AUTOMATION_FILE_TRIGGER_FOUNDATION = """
        CREATE TABLE IF NOT EXISTS automation_trigger_events (
            id TEXT PRIMARY KEY,
            trigger_id TEXT NOT NULL,
            event_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            run_id TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(trigger_id) REFERENCES automation_triggers(id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_trigger_events_trigger_status
            ON automation_trigger_events(trigger_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_automation_trigger_events_run
            ON automation_trigger_events(run_id)
            WHERE run_id IS NOT NULL;
        """


MEMORY_QUARANTINE_FOUNDATION = """
        CREATE TABLE IF NOT EXISTS memory_quarantine (
            memory_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('quarantined', 'active', 'revoked')),
            source TEXT NOT NULL,
            user_confirmed INTEGER NOT NULL CHECK (user_confirmed IN (0, 1)),
            expires_at TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            provenance_source_kind TEXT,
            provenance_source_id TEXT,
            provenance_origin TEXT,
            provenance_content_hash TEXT,
            provenance_trust_level TEXT,
            provenance_taint_flags TEXT,
            provenance_observed_at TEXT,
            provenance_task_scope TEXT,
            provenance_user_confirmed INTEGER CHECK (provenance_user_confirmed IN (0, 1)),
            provenance_sanitizers_applied TEXT,
            provenance_integrity_hmac TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_memory_quarantine_state_expiry
            ON memory_quarantine(state, expires_at, memory_id);
        CREATE INDEX IF NOT EXISTS idx_memory_quarantine_source_confirmation
            ON memory_quarantine(source, user_confirmed, memory_id);
        """


MEMORY_NAMESPACE_FOUNDATION = """
        CREATE TABLE IF NOT EXISTS memory_namespace (
            memory_id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            domain_scope TEXT NOT NULL,
            version INTEGER NOT NULL CHECK (version >= 1),
            supersedes TEXT,
            conflict_status TEXT NOT NULL
                CHECK (conflict_status IN ('none', 'conflicting', 'resolved', 'superseded')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE,
            FOREIGN KEY(supersedes) REFERENCES memories(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_namespace_recall
            ON memory_namespace(
                principal_id, workspace_id, domain_scope, conflict_status, memory_id
            );
        CREATE INDEX IF NOT EXISTS idx_memory_namespace_lineage
            ON memory_namespace(supersedes, version, memory_id);
        """


MEMORY_ACTIVE_SUCCESSOR_DUPLICATES = """
        SELECT scope.supersedes
        FROM memory_namespace AS scope
        JOIN memory_quarantine AS quarantine ON quarantine.memory_id = scope.memory_id
        WHERE scope.supersedes IS NOT NULL
          AND scope.conflict_status IN ('none', 'resolved')
          AND quarantine.state = 'active'
        GROUP BY scope.supersedes
        HAVING COUNT(*) > 1
        """


MEMORY_MARK_ACTIVE_SUCCESSOR_CONFLICTS = """
            UPDATE memory_namespace
            SET conflict_status = 'conflicting', updated_at = ?
            WHERE supersedes = ?
              AND conflict_status IN ('none', 'resolved')
              AND memory_id IN (
                  SELECT memory_id FROM memory_quarantine WHERE state = 'active'
              )
            """


MEMORY_ACTIVE_SUCCESSOR_GUARD = """
        CREATE TABLE IF NOT EXISTS memory_active_successors (
            parent_memory_id TEXT PRIMARY KEY NOT NULL,
            successor_memory_id TEXT NOT NULL UNIQUE,
            FOREIGN KEY(parent_memory_id) REFERENCES memories(id) ON DELETE CASCADE,
            FOREIGN KEY(successor_memory_id) REFERENCES memories(id) ON DELETE CASCADE
        );

        INSERT INTO memory_active_successors (parent_memory_id, successor_memory_id)
        SELECT scope.supersedes, scope.memory_id
        FROM memory_namespace AS scope
        JOIN memory_quarantine AS quarantine ON quarantine.memory_id = scope.memory_id
        WHERE scope.supersedes IS NOT NULL
          AND scope.conflict_status IN ('none', 'resolved')
          AND quarantine.state = 'active';

        CREATE TRIGGER IF NOT EXISTS trg_memory_quarantine_active_successor_insert
        AFTER INSERT ON memory_quarantine
        WHEN NEW.state = 'active'
        BEGIN
            INSERT INTO memory_active_successors (parent_memory_id, successor_memory_id)
            SELECT scope.supersedes, NEW.memory_id
            FROM memory_namespace AS scope
            WHERE scope.memory_id = NEW.memory_id
              AND scope.supersedes IS NOT NULL
              AND scope.conflict_status IN ('none', 'resolved');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_memory_quarantine_active_successor_update
        AFTER UPDATE OF state ON memory_quarantine
        BEGIN
            DELETE FROM memory_active_successors WHERE successor_memory_id = NEW.memory_id;
            INSERT INTO memory_active_successors (parent_memory_id, successor_memory_id)
            SELECT scope.supersedes, NEW.memory_id
            FROM memory_namespace AS scope
            WHERE NEW.state = 'active'
              AND scope.memory_id = NEW.memory_id
              AND scope.supersedes IS NOT NULL
              AND scope.conflict_status IN ('none', 'resolved');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_memory_quarantine_active_successor_delete
        AFTER DELETE ON memory_quarantine
        BEGIN
            DELETE FROM memory_active_successors WHERE successor_memory_id = OLD.memory_id;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_memory_namespace_active_successor_insert
        AFTER INSERT ON memory_namespace
        WHEN NEW.supersedes IS NOT NULL AND NEW.conflict_status IN ('none', 'resolved')
        BEGIN
            INSERT INTO memory_active_successors (parent_memory_id, successor_memory_id)
            SELECT NEW.supersedes, NEW.memory_id
            FROM memory_quarantine AS quarantine
            WHERE quarantine.memory_id = NEW.memory_id AND quarantine.state = 'active';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_memory_namespace_active_successor_update
        AFTER UPDATE OF supersedes, conflict_status ON memory_namespace
        BEGIN
            DELETE FROM memory_active_successors WHERE successor_memory_id = NEW.memory_id;
            INSERT INTO memory_active_successors (parent_memory_id, successor_memory_id)
            SELECT NEW.supersedes, NEW.memory_id
            FROM memory_quarantine AS quarantine
            WHERE NEW.supersedes IS NOT NULL
              AND NEW.conflict_status IN ('none', 'resolved')
              AND quarantine.memory_id = NEW.memory_id
              AND quarantine.state = 'active';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_memory_namespace_active_successor_delete
        AFTER DELETE ON memory_namespace
        BEGIN
            DELETE FROM memory_active_successors WHERE successor_memory_id = OLD.memory_id;
        END;
        """
