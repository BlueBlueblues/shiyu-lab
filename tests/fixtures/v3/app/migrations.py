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
