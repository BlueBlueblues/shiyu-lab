import json
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from app.schemas import Plan, ProjectCreate

# 单条 SELECT 保证状态、回答数量和最新报告来自同一个读取快照。
PROJECT_QUERY = """
SELECT p.*, e.id AS experiment_id, e.public_token, e.landing_json,
       e.questions_json, e.published_at,
       r.id AS report_id, r.response_count AS report_response_count,
       r.cutoff_response_id AS report_cutoff_response_id, r.created_at AS report_created_at,
       (SELECT COUNT(*) FROM responses WHERE experiment_id = e.id) AS current_response_count,
       (SELECT COALESCE(MAX(id), 0) FROM responses WHERE experiment_id = e.id) AS current_cutoff_response_id,
       (SELECT id FROM agent_runs WHERE project_id = p.id AND status = 'running') AS running_run_id
FROM projects p
LEFT JOIN experiments e ON e.project_id = p.id
LEFT JOIN reports r ON r.id = (
    SELECT id FROM reports WHERE experiment_id = e.id
    ORDER BY created_at DESC, id DESC LIMIT 1
)
"""


def create_project(connection: sqlite3.Connection, project: ProjectCreate) -> str:
    project_id = str(uuid4())
    connection.execute(
        """INSERT INTO projects
           (id, name, idea, target_users, skills, budget_cny, validation_goal,
            data_kind, plan_json, created_at)
           VALUES (:id, :name, :idea, :target_users, :skills, :budget_cny,
                   :validation_goal, :data_kind, NULL, :created_at)""",
        {**project.model_dump(), "id": project_id, "created_at": datetime.now(timezone.utc).isoformat()},
    )
    return project_id


def report_is_stale(row: sqlite3.Row) -> bool:
    return (
        row["report_response_count"] != row["current_response_count"]
        or row["report_cutoff_response_id"] != row["current_cutoff_response_id"]
    )


def project_status(row: sqlite3.Row) -> str:
    if row["experiment_id"] is None:
        return "draft"
    if row["published_at"] is None:
        return "planned"
    if row["report_id"] is None or report_is_stale(row):
        return "collecting"
    return "reviewed"


def list_projects(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(PROJECT_QUERY + " ORDER BY p.created_at DESC, p.id DESC").fetchall()
    return [
        {"id": row["id"], "name": row["name"], "status": project_status(row),
         "data_kind": row["data_kind"], "created_at": row["created_at"]}
        for row in rows
    ]


def get_project(connection: sqlite3.Connection, project_id: str, public_base_url: str) -> dict | None:
    row = connection.execute(PROJECT_QUERY + " WHERE p.id = ?", (project_id,)).fetchone()
    if row is None:
        return None
    experiment = None
    if row["experiment_id"] is not None:
        public_url = f"{public_base_url.rstrip('/')}/e/{quote(row['public_token'], safe='')}"
        experiment = {
            "id": row["experiment_id"], "project_id": row["id"],
            "landing": json.loads(row["landing_json"]), "questions": json.loads(row["questions_json"]),
            "published_at": row["published_at"],
            "service_url": public_url if row["published_at"] is not None else None,
            "survey_url": public_url + "/survey" if row["published_at"] is not None else None,
        }
    report_meta = None
    if row["report_id"] is not None:
        report_meta = {
            "id": row["report_id"], "response_count": row["report_response_count"],
            "cutoff_response_id": row["report_cutoff_response_id"],
            "created_at": row["report_created_at"], "stale": report_is_stale(row),
        }
    return {
        "project": {**{name: row[name] for name in ProjectCreate.model_fields},
                    "id": row["id"], "created_at": row["created_at"], "status": project_status(row)},
        "plan": json.loads(row["plan_json"]) if row["plan_json"] is not None else None,
        "experiment": experiment, "latest_report_meta": report_meta,
        "running_run_id": row["running_run_id"],
    }


class RepositoryError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def load_project_input(connection: sqlite3.Connection, project_id: str) -> ProjectCreate:
    row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise RepositoryError("NOT_FOUND")
    return ProjectCreate.model_validate({name: row[name] for name in ProjectCreate.model_fields})


def start_plan_run(connection: sqlite3.Connection, project_id: str, run_id: str, started_at: str):
    project = load_project_input(connection, project_id)
    if connection.execute(
        "SELECT id FROM agent_runs WHERE project_id = ? AND status = 'running'", (project_id,),
    ).fetchone():
        raise RepositoryError("RUN_IN_PROGRESS")
    experiment = connection.execute(
        "SELECT published_at FROM experiments WHERE project_id = ?", (project_id,),
    ).fetchone()
    if experiment and experiment["published_at"] is not None:
        raise RepositoryError("EXPERIMENT_LOCKED")
    connection.execute(
        """INSERT INTO agent_runs (id, project_id, action, status, started_at)
           VALUES (?, ?, 'plan', 'running', ?)""", (run_id, project_id, started_at),
    )
    return project


def start_tool(connection: sqlite3.Connection, run_id: str, tool_id: str,
               tool_name: str, started_at: str, summary: dict):
    run = connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["status"] != "running":
        raise RepositoryError("INVALID_STATE")
    connection.execute(
        """INSERT INTO tool_calls
           (id, run_id, tool_name, status, started_at, input_summary_json)
           VALUES (?, ?, ?, 'running', ?, ?)""",
        (tool_id, run_id, tool_name, started_at, json.dumps(summary, ensure_ascii=False)),
    )


def finish_tool(connection: sqlite3.Connection, tool_id: str, finished_at: str, output: dict):
    updated = connection.execute(
        """UPDATE tool_calls SET status = 'succeeded', finished_at = ?, output_ref_json = ?
           WHERE id = ? AND status = 'running'""",
        (finished_at, json.dumps(output, ensure_ascii=False), tool_id),
    )
    if updated.rowcount != 1:
        raise RepositoryError("INVALID_STATE")


def save_experiment(connection: sqlite3.Connection, project_id: str, plan: Plan,
                    experiment_id: str, public_token: str, created_at: str) -> str:
    experiment = connection.execute(
        "SELECT id, published_at FROM experiments WHERE project_id = ?", (project_id,),
    ).fetchone()
    if experiment and experiment["published_at"] is not None:
        raise RepositoryError("EXPERIMENT_LOCKED")
    plan_json = plan.model_dump_json()
    landing_json = plan.landing.model_dump_json()
    questions_json = json.dumps([question.model_dump() for question in plan.questions], ensure_ascii=False)
    connection.execute("UPDATE projects SET plan_json = ? WHERE id = ?", (plan_json, project_id))
    if experiment:
        experiment_id = experiment["id"]
        connection.execute(
            "UPDATE experiments SET landing_json = ?, questions_json = ? WHERE id = ?",
            (landing_json, questions_json, experiment_id),
        )
    else:
        connection.execute(
            """INSERT INTO experiments
               (id, project_id, public_token, landing_json, questions_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (experiment_id, project_id, public_token, landing_json, questions_json, created_at),
        )
    return experiment_id


def complete_plan_run(connection: sqlite3.Connection, run_id: str, finished_at: str):
    updated = connection.execute(
        """UPDATE agent_runs SET status = 'succeeded', finished_at = ?
           WHERE id = ? AND status = 'running'""", (finished_at, run_id),
    )
    if updated.rowcount != 1:
        raise RepositoryError("INVALID_STATE")


def fail_run(connection: sqlite3.Connection, run_id: str, finished_at: str,
             code: str, message: str, model_call: dict | None = None):
    connection.execute(
        """UPDATE tool_calls SET status = 'failed', finished_at = ?, error_code = ?,
           output_ref_json = ? WHERE run_id = ? AND status = 'running'""",
        (finished_at, code, json.dumps({"model_call": model_call}) if model_call else None, run_id),
    )
    connection.execute(
        """UPDATE agent_runs SET status = 'failed', finished_at = ?, error_code = ?, error_message = ?
           WHERE id = ? AND status = 'running'""", (finished_at, code, message, run_id),
    )


def list_runs(connection: sqlite3.Connection, project_id: str) -> list[dict]:
    if connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
        raise RepositoryError("NOT_FOUND")
    return [dict(row) for row in connection.execute(
        "SELECT * FROM agent_runs WHERE project_id = ? ORDER BY started_at DESC, rowid DESC",
        (project_id,),
    ).fetchall()]


def get_run(connection: sqlite3.Connection, run_id: str) -> dict | None:
    # 同一读取事务中取 run 与工具，避免看到跨提交的混合状态。
    connection.execute("BEGIN")
    row = connection.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    tools = []
    for tool in connection.execute(
        "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY started_at, rowid", (run_id,),
    ).fetchall():
        item = dict(tool)
        item["input_summary"] = json.loads(item.pop("input_summary_json"))
        raw_output = item.pop("output_ref_json")
        item["output_ref"] = json.loads(raw_output) if raw_output is not None else None
        tools.append(item)
    return {**dict(row), "tool_calls": tools}


def experiment_record(connection, experiment_id):
    return connection.execute(
        """SELECT e.*, p.plan_json,
        (SELECT id FROM agent_runs WHERE project_id=e.project_id AND status='running') AS running_run_id
        FROM experiments e JOIN projects p ON p.id=e.project_id WHERE e.id=?""",
        (experiment_id,),
    ).fetchone()


def published_experiment(connection, token):
    return connection.execute(
        "SELECT * FROM experiments WHERE public_token=? AND published_at IS NOT NULL", (token,),
    ).fetchone()


def update_draft(connection, row, plan):
    connection.execute("UPDATE projects SET plan_json=? WHERE id=?",
                       (plan.model_dump_json(), row["project_id"]))
    connection.execute("UPDATE experiments SET landing_json=?, questions_json=? WHERE id=?",
                       (plan.landing.model_dump_json(),
                        json.dumps([q.model_dump() for q in plan.questions], ensure_ascii=False), row["id"]))


def publish_experiment(connection, experiment_id, published_at):
    connection.execute("UPDATE experiments SET published_at=? WHERE id=? AND published_at IS NULL",
                       (published_at, experiment_id))


def find_response(connection, experiment_id, submission_id):
    return connection.execute(
        "SELECT * FROM responses WHERE experiment_id=? AND client_submission_id=?",
        (experiment_id, submission_id),
    ).fetchone()


def insert_response(connection, experiment_id, submission_id, answers, submitted_at):
    return connection.execute(
        """INSERT INTO responses (experiment_id, client_submission_id, answers_json, consent, created_at)
        VALUES (?, ?, ?, 1, ?)""",
        (experiment_id, submission_id, json.dumps(answers, ensure_ascii=False, sort_keys=True), submitted_at),
    ).lastrowid


def response_snapshot(connection, experiment_id):
    # 题目和回答共享读取快照，数量及截止 ID 从这份回答计算。
    connection.execute("BEGIN")
    experiment = connection.execute(
        "SELECT id, questions_json, published_at FROM experiments WHERE id=?", (experiment_id,),
    ).fetchone()
    responses = [{"id": row["id"], "answers": json.loads(row["answers_json"]), "created_at": row["created_at"]}
                 for row in connection.execute(
                     "SELECT id, answers_json, created_at FROM responses WHERE experiment_id=? ORDER BY id",
                     (experiment_id,),
                 ).fetchall()]
    return experiment, responses


def start_review_run(connection, project_id, run_id, started_at):
    project = load_project_input(connection, project_id)
    if connection.execute("SELECT id FROM agent_runs WHERE project_id=? AND status='running'", (project_id,)).fetchone():
        raise RepositoryError("RUN_IN_PROGRESS")
    experiment = connection.execute("SELECT id, published_at FROM experiments WHERE project_id=?", (project_id,)).fetchone()
    if experiment is None or experiment["published_at"] is None:
        raise RepositoryError("INVALID_STATE")
    connection.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'review','running',?)",
                       (run_id, project_id, started_at))
    return project, experiment["id"]


def save_report(connection, report_id, experiment_id, run_id, statistics, review, created_at):
    run = connection.execute("SELECT r.status, r.action, r.project_id FROM agent_runs r WHERE r.id=?", (run_id,)).fetchone()
    experiment = connection.execute("SELECT project_id FROM experiments WHERE id=?", (experiment_id,)).fetchone()
    if (run is None or experiment is None or run["status"] != "running" or run["action"] != "review"
            or run["project_id"] != experiment["project_id"]):
        raise RepositoryError("INVALID_STATE")
    connection.execute("""INSERT INTO reports(id,experiment_id,run_id,response_count,cutoff_response_id,
        statistics_json,review_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
        (report_id, experiment_id, run_id, statistics.response_count, statistics.cutoff_response_id,
         statistics.model_dump_json(), review.model_dump_json(), created_at))


def latest_report(connection, project_id):
    row = connection.execute(PROJECT_QUERY + " WHERE p.id=?", (project_id,)).fetchone()
    if row is None:
        raise RepositoryError("NOT_FOUND")
    if row["report_id"] is None:
        return None
    report = connection.execute("SELECT * FROM reports WHERE id=?", (row["report_id"],)).fetchone()
    return {"id": report["id"], "created_at": report["created_at"], "response_count": report["response_count"],
            "cutoff_response_id": report["cutoff_response_id"], "statistics": json.loads(report["statistics_json"]),
            "review": json.loads(report["review_json"]), "stale": report_is_stale(row), "data_kind": row["data_kind"],
            "run_id": report["run_id"], "project_name": row["name"]}
