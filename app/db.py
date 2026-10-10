import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY NOT NULL CHECK (
        length(id) = 36 AND substr(id, 9, 1) = '-' AND substr(id, 14, 1) = '-'
        AND substr(id, 19, 1) = '-' AND substr(id, 24, 1) = '-'
        AND length(replace(id, '-', '')) = 32
        AND lower(replace(id, '-', '')) NOT GLOB '*[^0-9a-f]*'
    ),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    idea TEXT NOT NULL CHECK (length(trim(idea)) BETWEEN 1 AND 2000),
    target_users TEXT NOT NULL CHECK (length(trim(target_users)) BETWEEN 1 AND 500),
    skills TEXT NOT NULL CHECK (length(trim(skills)) BETWEEN 1 AND 500),
    budget_cny REAL NOT NULL CHECK (
        typeof(budget_cny) IN ('integer', 'real') AND budget_cny BETWEEN 0 AND 100000
    ),
    validation_goal TEXT NOT NULL CHECK (length(trim(validation_goal)) BETWEEN 1 AND 500),
    data_kind TEXT NOT NULL CHECK (data_kind IN ('real', 'test')),
    plan_json TEXT CHECK (plan_json IS NULL OR json_valid(plan_json)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id),
    -- shortcut: 长度不能证明随机性，A04 创建 token 时必须使用至少 128 位安全随机量。
    public_token TEXT NOT NULL UNIQUE CHECK (length(public_token) >= 22),
    landing_json TEXT NOT NULL CHECK (json_valid(landing_json)),
    questions_json TEXT NOT NULL CHECK (json_valid(questions_json)),
    published_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    client_submission_id TEXT NOT NULL,
    answers_json TEXT NOT NULL CHECK (json_valid(answers_json)),
    consent INTEGER NOT NULL CHECK (consent = 1),
    created_at TEXT NOT NULL,
    UNIQUE (experiment_id, client_submission_id)
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id),
    action TEXT NOT NULL CHECK (action IN ('plan', 'review')),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT,
    error_message TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_run_per_project
    ON agent_runs(project_id) WHERE status = 'running';
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    tool_name TEXT NOT NULL CHECK (
        tool_name IN ('make_validation_plan', 'create_experiment', 'analyze_experiment')
    ),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    input_summary_json TEXT NOT NULL CHECK (json_valid(input_summary_json)),
    output_ref_json TEXT CHECK (output_ref_json IS NULL OR json_valid(output_ref_json)),
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY NOT NULL,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(id),
    response_count INTEGER NOT NULL CHECK (typeof(response_count) = 'integer' AND response_count >= 0),
    cutoff_response_id INTEGER NOT NULL CHECK (
        typeof(cutoff_response_id) = 'integer'
        AND ((response_count = 0 AND cutoff_response_id = 0)
             OR (response_count > 0 AND cutoff_response_id > 0))
    ),
    statistics_json TEXT NOT NULL CHECK (json_valid(statistics_json)),
    review_json TEXT NOT NULL CHECK (json_valid(review_json)),
    created_at TEXT NOT NULL
);
"""


@contextmanager
def connect_db(database_path: Path):
    connection = sqlite3.connect(database_path, timeout=5, check_same_thread=False)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def get_db(request: Request):
    with connect_db(request.app.state.settings.database_path) as connection:
        yield connection


def init_db(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_db(database_path) as connection:
        connection.executescript(SCHEMA)
        finished_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """UPDATE tool_calls SET status = 'failed', finished_at = ?,
               error_code = 'INTERRUPTED' WHERE status = 'running'""",
            (finished_at,),
        )
        connection.execute(
            """UPDATE agent_runs SET status = 'failed', finished_at = ?,
               error_code = 'INTERRUPTED', error_message = ? WHERE status = 'running'""",
            (finished_at, "上次服务中断，任务未完成，请手动重试。"),
        )
