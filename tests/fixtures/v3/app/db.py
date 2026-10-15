import sqlite3
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request

from app.passwords import LEGACY_ADMIN_ID, hash_password

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


def migrate_users(connection, admin_user=None, admin_password=None):
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY NOT NULL,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        email TEXT,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL CHECK (expires_at > created_at)
    )""")
    admin = connection.execute("SELECT username FROM users WHERE id=?", (LEGACY_ADMIN_ID,)).fetchone()
    if admin is None:
        connection.execute("INSERT INTO users (id,username,email,password_hash,created_at) VALUES (?,?,?,?,?)", (
            LEGACY_ADMIN_ID, admin_user or "__legacy_admin__", None,
            hash_password(admin_password if admin_password is not None else secrets.token_urlsafe(32)),
            datetime.now(timezone.utc).isoformat()))
    elif admin["username"] == "__legacy_admin__" and admin_user is not None:
        connection.execute("UPDATE users SET username=?, password_hash=? WHERE id=?",
                           (admin_user, hash_password(admin_password), LEGACY_ADMIN_ID))
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(projects)")}
    if "user_id" not in columns:
        connection.execute("ALTER TABLE projects ADD COLUMN user_id TEXT REFERENCES users(id)")
    connection.execute("UPDATE projects SET user_id=? WHERE user_id IS NULL", (LEGACY_ADMIN_ID,))
    connection.execute("CREATE INDEX IF NOT EXISTS projects_by_user ON projects(user_id)")
    # 兼容旧版种子脚本的 INSERT；HTTP 创建始终显式指定当前用户。
    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS legacy_project_owner
        AFTER INSERT ON projects WHEN NEW.user_id IS NULL BEGIN
        UPDATE projects SET user_id='{LEGACY_ADMIN_ID}' WHERE id=NEW.id;
        END""")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS project_owner_required
        BEFORE UPDATE OF user_id ON projects WHEN NEW.user_id IS NULL BEGIN
        SELECT RAISE(ABORT, 'project owner required'); END""")
    if connection.execute("PRAGMA user_version").fetchone()[0] < 1:
        connection.execute("PRAGMA user_version = 1")


def migrate_registration(connection):
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    if "email_verified_at" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
    # 旧邮箱不视为已验证；保留历史重复邮箱，新注册仍检查全部用户。
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS verified_email_unique
        ON users(lower(trim(email))) WHERE email_verified_at IS NOT NULL""")
    statements = (
        """CREATE TABLE IF NOT EXISTS invitations (
            id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
            created_by TEXT NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL, max_uses INTEGER NOT NULL CHECK(max_uses>0),
            used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count>=0 AND used_count<=max_uses),
            revoked_at INTEGER, last_used_at INTEGER)""",
        """CREATE TABLE IF NOT EXISTS invitation_uses (
            invitation_id TEXT NOT NULL REFERENCES invitations(id),
            user_id TEXT NOT NULL UNIQUE REFERENCES users(id), used_at INTEGER NOT NULL,
            PRIMARY KEY(invitation_id,user_id))""",
        """CREATE TABLE IF NOT EXISTS registration_challenges (
            id TEXT PRIMARY KEY, email TEXT NOT NULL,
            invitation_id TEXT NOT NULL REFERENCES invitations(id),
            code_hash TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0,
            consumed_at INTEGER)""",
        """CREATE INDEX IF NOT EXISTS registration_challenge_lookup
            ON registration_challenges(email,invitation_id,created_at)""",
        """CREATE TABLE IF NOT EXISTS registration_limits (
            scope TEXT NOT NULL, key_hash TEXT NOT NULL, attempted_at INTEGER NOT NULL)""",
        """CREATE INDEX IF NOT EXISTS registration_limit_lookup
            ON registration_limits(scope,key_hash,attempted_at)""",
    )
    for statement in statements:
        connection.execute(statement)
    if connection.execute("PRAGMA user_version").fetchone()[0] < 2:
        connection.execute("PRAGMA user_version = 2")


def init_db(database_path: Path, admin_user=None, admin_password=None) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_db(database_path) as connection:
        existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "projects" in existing and ("users" not in existing or "invitations" not in existing):
            kind = "pre-users" if "users" not in existing else "pre-invites"
            backup_path = database_path.with_name(database_path.name + "." + kind + "-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
            backup_path.touch(mode=0o600, exist_ok=False)
            with sqlite3.connect(backup_path) as backup:
                connection.backup(backup)
        if "experiments" in existing and "round_number" not in {
                row["name"] for row in connection.execute("PRAGMA table_info(experiments)")}:
            backup_path = database_path.with_name(database_path.name + ".pre-rounds-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
            backup_path.touch(mode=0o600, exist_ok=False)
            with sqlite3.connect(backup_path) as backup:
                connection.backup(backup)
        connection.executescript(SCHEMA)
        migrate_users(connection, admin_user, admin_password)
        migrate_registration(connection)
        connection.commit()
        from app.migrations import migrate_experiment_rounds
        migrate_experiment_rounds(connection)
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
