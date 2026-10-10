
import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

base_url = os.getenv(
    "PUBLIC_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")

username = os.getenv("ADMIN_USER")
password = os.getenv("ADMIN_PASSWORD")

if not username or not password:
    sys.exit("请先在 .env 中配置管理员凭据")

origin = base_url
record_file = Path("data/a02_smoke_id.txt")

def check(response, status, name):
    if response.status_code != status:
        raise AssertionError(
            f"{name}失败：期望{status}，实际"
            f"{response.status_code}；"
            f"{response.text[:300]}"
        )
    print(f"[PASS] {name}")

with httpx.Client(
    base_url=base_url,
    auth=(username, password),
    headers={
        "Origin": origin,
        "X-Requested-With": "FirstOrderLab"
    },
    timeout=15,
    trust_env=False,
) as client:

    if "--verify-restart" in sys.argv:
        project_id = record_file.read_text(
            encoding="utf-8"
        ).strip()
        r = client.get(f"/api/projects/{project_id}")
        check(r, 200, "重启后读取项目")
        assert r.json()["data"]["project"]["id"] == project_id
        print("A02 持久化检查通过")
        sys.exit(0)

    r = httpx.get(
        base_url + "/api/projects",
        timeout=10,
        trust_env=False,
    )
    check(r, 401, "未授权访问拦截")

    payload = {
        "name": "A02验收-" + uuid.uuid4().hex[:8],
        "idea": "为校园社团制作低成本活动报名网页。",
        "target_users": "社团负责人、活动组织者",
        "skills": "Python、AI辅助编程",
        "budget_cny": 100,
        "validation_goal": "验证试用需求和重要功能",
        "data_kind": "test"
    }

    r = client.post("/api/projects", json=payload)
    check(r, 201, "创建项目")

    result = r.json()["data"]
    project_id = result["project_id"]
    assert result["status"] == "draft"
    uuid.UUID(project_id)

    r = client.get("/api/projects")
    check(r, 200, "查询项目列表")
    assert any(
        p["id"] == project_id
        for p in r.json()["data"]
    )

    r = client.get(f"/api/projects/{project_id}")
    check(r, 200, "查询项目详情")

    detail = r.json()["data"]
    assert detail["project"]["name"] == payload["name"]
    assert detail["project"]["status"] == "draft"

    for field in [
        "plan",
        "experiment",
        "latest_report_meta",
        "running_run_id",
    ]:
        assert field in detail
        assert detail[field] is None

    invalid_cases = [
        ("负数预算", {"budget_cny": -1}),
        ("空白名称", {"name": "   "}),
        ("超长创业描述", {"idea": "测" * 2001}),
        ("非法数据类型", {"data_kind": "other"}),
    ]

    for label, changes in invalid_cases:
        bad_payload = {**payload, **changes}
        r = client.post("/api/projects", json=bad_payload)
        check(r, 422, label)
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    r = client.get("/api/projects/" + str(uuid.uuid4()))
    check(r, 404, "不存在的项目")

    r = client.post(
        "/api/projects",
        json=payload,
        headers={"Origin": "https://evil.invalid"},
    )
    check(r, 403, "非法来源写入拦截")

    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(project_id, encoding="utf-8")

    print("\nA02 接口冒烟测试全部通过")
    print("测试项目 ID:", project_id)
    print("下一步：重启服务器并运行 --verify-restart")
