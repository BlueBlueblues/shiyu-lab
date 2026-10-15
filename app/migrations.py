"""Experiment rounds are schema v3; user/invitation migrations remain v1/v2."""
import json


def migrate_experiment_rounds(connection):
    if "round_number" in {r["name"] for r in connection.execute("PRAGMA table_info(experiments)")}:
        if connection.execute("PRAGMA user_version").fetchone()[0] < 3:
            connection.execute("PRAGMA user_version = 3")
        return
    if connection.in_transaction:
        raise RuntimeError("Round migration requires a committed connection")
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table, old, new in (
            ("experiments", "project_id TEXT NOT NULL UNIQUE REFERENCES", "project_id TEXT NOT NULL REFERENCES"),
            ("agent_runs", "('plan', 'review')", "('plan', 'review', 'iterate', 'build')"),
            ("tool_calls", "'analyze_experiment')", "'analyze_experiment', 'propose_iteration')"),
        ):
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            sql = sql.replace(old, new)
            sql = sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_v3", 1)
            connection.execute(sql)
            connection.execute(f"INSERT INTO {table}_v3 SELECT * FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {table}_v3 RENAME TO {table}")
        connection.execute("ALTER TABLE experiments ADD COLUMN round_number INTEGER NOT NULL DEFAULT 1 CHECK(round_number>=1)")
        connection.execute("ALTER TABLE experiments ADD COLUMN parent_experiment_id TEXT REFERENCES experiments(id)")
        connection.execute("ALTER TABLE experiments ADD COLUMN plan_json TEXT CHECK(plan_json IS NULL OR json_valid(plan_json))")
        connection.execute("ALTER TABLE reports ADD COLUMN plan_snapshot_json TEXT CHECK(plan_snapshot_json IS NULL OR json_valid(plan_snapshot_json))")
        for row in connection.execute("SELECT e.*,p.plan_json AS project_plan FROM experiments e JOIN projects p ON p.id=e.project_id").fetchall():
            raw = row["project_plan"]
            if raw is not None:
                plan = json.loads(raw)
                plan.update(landing=json.loads(row["landing_json"]), questions=json.loads(row["questions_json"]))
                connection.execute("UPDATE experiments SET plan_json=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), row["id"]))
        connection.execute("UPDATE reports SET plan_snapshot_json=(SELECT plan_json FROM experiments WHERE id=reports.experiment_id)")
        connection.execute("CREATE UNIQUE INDEX experiment_round ON experiments(project_id,round_number)")
        connection.execute("CREATE UNIQUE INDEX experiment_successor ON experiments(parent_experiment_id) WHERE parent_experiment_id IS NOT NULL")
        connection.execute("CREATE UNIQUE INDEX one_running_run_per_project ON agent_runs(project_id) WHERE status='running'")
        connection.execute("""CREATE TABLE iteration_proposals (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            source_experiment_id TEXT NOT NULL REFERENCES experiments(id),
            source_report_id TEXT NOT NULL REFERENCES reports(id),
            run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(id),
            proposal_json TEXT NOT NULL CHECK(json_valid(proposal_json)),
            created_at TEXT NOT NULL, confirmed_at TEXT,
            created_experiment_id TEXT UNIQUE REFERENCES experiments(id))""")
        connection.execute("""CREATE TRIGGER published_content_immutable
            BEFORE UPDATE OF landing_json,questions_json,plan_json ON experiments
            WHEN OLD.published_at IS NOT NULL BEGIN
            SELECT RAISE(ABORT, 'published experiment is immutable'); END""")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Round migration failed foreign key validation")
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def migrate_agent_tasks(connection):
    """Additive v4; never backfill fictional AI executions into legacy history."""
    if connection.execute('PRAGMA user_version').fetchone()[0] >= 4:
        return
    if connection.in_transaction:
        raise RuntimeError('Agent migration requires committed connection')
    connection.execute('BEGIN IMMEDIATE')
    try:
        statements = (
            """CREATE TABLE agent_tasks (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
              run_id TEXT NOT NULL REFERENCES agent_runs(id), tool_id TEXT NOT NULL REFERENCES tool_calls(id),
              round_number INTEGER NOT NULL CHECK(round_number>=1),
              role TEXT NOT NULL CHECK(role IN ('shiyu','zhuoyu','jianyu','gongyu')),
              version INTEGER NOT NULL CHECK(version>=1), contract_version INTEGER NOT NULL DEFAULT 4,
              status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
              started_at TEXT NOT NULL, finished_at TEXT, error_code TEXT,
              input_json TEXT NOT NULL CHECK(json_valid(input_json)), input_digest TEXT NOT NULL,
              call_json TEXT CHECK(call_json IS NULL OR json_valid(call_json)),
              UNIQUE(project_id,round_number,role,version))""",
            """CREATE TABLE agent_artifacts (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES agent_tasks(id),
              output_json TEXT NOT NULL CHECK(json_valid(output_json)), output_digest TEXT NOT NULL,
              created_at TEXT NOT NULL)""",
            "CREATE INDEX agent_tasks_project_round ON agent_tasks(project_id,round_number,role)",
            """CREATE TRIGGER agent_artifacts_immutable BEFORE UPDATE ON agent_artifacts
              BEGIN SELECT RAISE(ABORT,'agent artifact immutable'); END""",
            "ALTER TABLE iteration_proposals ADD COLUMN rejected_at TEXT",
            "ALTER TABLE iteration_proposals ADD COLUMN decision_json TEXT CHECK(decision_json IS NULL OR json_valid(decision_json))",
        )
        for sql in statements:
            connection.execute(sql)
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Agent migration foreign key failure')
        connection.execute('PRAGMA user_version=4')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
