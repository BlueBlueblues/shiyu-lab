import json
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from app.schemas import ProjectCreate

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
