#!/usr/bin/env python
"""最小可重复验收脚本（成员C指南 C02 第 7 条）。

用途：在**已启动的服务**上跑一遍最小验收，默认只使用 ``data_kind=test`` 项目，
绝不向 ``real`` 实验灌入任何回答，也不修改已有实验内容。

用法::

    # 先在另一个终端用 test 数据库启动服务：
    #   $env:DATABASE_PATH="data/smoke.db"; python -m uvicorn app.main:app --port 8000
    python scripts/smoke.py --base-url http://127.0.0.1:8000

    # 顺便验证真实模型一次计划（会真的调用模型，耗时和费用由团队承担）：
    python scripts/smoke.py --with-plan

    # 只检查某个已有项目（必须是 test）：
    python scripts/smoke.py --project-id <uuid>

管理凭据从 ``ADMIN_USER`` / ``ADMIN_PASSWORD`` 读取，也可用命令行覆盖。

退出码：0 全部通过；1 有检查失败；3 服务不可达或缺少管理配置（阻塞）。
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import uuid
from dataclasses import dataclass, field

try:
    import httpx
except ImportError:  # pragma: no cover - 环境缺依赖时给出可读提示
    print("缺少依赖 httpx，请先按 README 安装 requirements。", file=sys.stderr)
    raise SystemExit(3)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 3

SITE_HEADER = "X-Requested-With"
SITE_HEADER_VALUE = "FirstOrderLab"


@dataclass
class Report:
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, bool(ok), detail))
        mark = "PASS" if ok else "FAIL"
        line = f"[{mark}] {name}"
        if detail:
            line += f" —— {detail}"
        print(line)
        return bool(ok)

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [item for item in self.results if not item[1]]


def basic_auth(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FirstOrder Lab 最小验收")
    parser.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--admin-user", default=os.environ.get("ADMIN_USER", ""))
    parser.add_argument("--admin-pass", default=os.environ.get("ADMIN_PASSWORD", ""))
    parser.add_argument("--project-id", default=None, help="复用已有项目；必须是 data_kind=test")
    parser.add_argument("--with-plan", action="store_true", help="额外触发一次真实模型计划")
    parser.add_argument("--timeout", type=float, default=15.0, help="普通请求超时（秒）")
    parser.add_argument("--plan-timeout", type=float, default=85.0, help="计划请求超时（秒）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_url = args.base_url.rstrip("/")
    report = Report()

    if not args.admin_user or not args.admin_pass:
        print(
            "缺少管理凭据：请设置 ADMIN_USER / ADMIN_PASSWORD，或用 --admin-user/--admin-pass 传入。",
            file=sys.stderr,
        )
        return EXIT_BLOCKED

    origin = base_url
    admin_headers = {
        **basic_auth(args.admin_user, args.admin_pass),
        "Origin": origin,
        SITE_HEADER: SITE_HEADER_VALUE,
    }

    client = httpx.Client(base_url=base_url, timeout=args.timeout, follow_redirects=True)

    # 1. 健康检查（公开、无包装）
    try:
        health = client.get("/health")
    except Exception as exc:  # noqa: BLE001 - 连不上就是阻塞
        print(f"无法连接 {base_url}：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("请先按 README 启动服务（建议用独立的 test 数据库）。", file=sys.stderr)
        return EXIT_BLOCKED

    body = health.json() if health.headers.get("content-type", "").startswith("application/json") else {}
    # 对齐基线 3f79df9：/health 与其它成功响应一致，统一 {"data": ...} 包装。
    report.check("/health 返回 200 且 data.status=ok",
                 health.status_code == 200 and body == {"data": {"status": "ok"}},
                 f"HTTP {health.status_code} {body}")

    # 2. 管理接口必须要求认证
    anonymous = client.get("/api/projects")
    report.check(
        "未登录读取 /api/projects 返回 401",
        anonymous.status_code == 401,
        f"HTTP {anonymous.status_code}",
    )
    if anonymous.status_code == 200:
        print("  严重：管理接口未受保护，停止后续检查以免写入未保护的数据。", file=sys.stderr)
        return EXIT_FAIL

    # 3. 管理端可读项目列表
    listing = client.get("/api/projects", headers=admin_headers)
    if not report.check("管理员可读取 /api/projects", listing.status_code == 200,
                        f"HTTP {listing.status_code}"):
        print("  管理凭据或权限配置可能不正确，停止后续检查。", file=sys.stderr)
        return EXIT_FAIL

    # 4. 准备一个 test 项目
    project_id = args.project_id
    if project_id:
        detail_response = client.get(f"/api/projects/{project_id}", headers=admin_headers)
        if not report.check("指定项目可读取", detail_response.status_code == 200,
                            f"HTTP {detail_response.status_code}"):
            return EXIT_FAIL
        data_kind = detail_response.json()["data"]["project"].get("data_kind")
    else:
        created = client.post(
            "/api/projects",
            headers=admin_headers,
            json={
                "name": f"smoke 冒烟项目 {uuid.uuid4().hex[:8]}",
                "idea": "smoke.py 生成的测试项目，仅用于最小验收，不代表真实需求。",
                "target_users": "自动测试",
                "skills": "无",
                "budget_cny": 0,
                "validation_goal": "验证服务能创建并读回项目",
                "data_kind": "test",
            },
        )
        if not report.check("创建 test 项目返回 201", created.status_code == 201,
                            f"HTTP {created.status_code} {created.text[:200]}"):
            return EXIT_FAIL
        project_id = created.json()["data"]["project_id"]
        data_kind = "test"

    report.check(
        "项目 data_kind 必须是 test（保护真实实验数据）",
        data_kind == "test",
        f"data_kind={data_kind!r}；smoke 脚本只允许 test 项目",
    )
    if data_kind != "test":
        print("  拒绝在非 test 项目上继续，避免污染真实需求样本。", file=sys.stderr)
        return EXIT_FAIL

    # 5. 详情结构固定为五个键
    detail = client.get(f"/api/projects/{project_id}", headers=admin_headers).json()["data"]
    missing = [
        key
        for key in ("project", "plan", "experiment", "latest_report_meta", "running_run_id")
        if key not in detail
    ]
    report.check("详情包含 project/plan/experiment/latest_report_meta/running_run_id",
                 not missing, f"缺少 {missing}" if missing else "")

    listed = client.get("/api/projects", headers=admin_headers).json()["data"]
    report.check("新项目出现在项目列表中", any(item["id"] == project_id for item in listed))

    # 6. 可选：真实模型计划
    if args.with_plan:
        plan_response = client.post(
            f"/api/projects/{project_id}/plan",
            headers=admin_headers,
            json={},
            timeout=args.plan_timeout,
        )
        ok = report.check("计划请求返回 200", plan_response.status_code == 200,
                          f"HTTP {plan_response.status_code} {plan_response.text[:200]}")
        if ok:
            data = plan_response.json()["data"]
            experiment_id = data.get("experiment_id")
            run_id = data.get("run_id")
            report.check("计划后状态为 planned", data.get("status") == "planned",
                         f"status={data.get('status')!r}")

            experiment = client.get(f"/api/experiments/{experiment_id}", headers=admin_headers).json()["data"]
            questions = experiment.get("questions") or []
            report.check("草稿题量为 4～6 题", 4 <= len(questions) <= 6,
                         f"实际 {len(questions)} 题")
            types = {q.get("type") for q in questions}
            report.check("三类题齐全", types == {"single", "multiple", "text"}, f"实际 {sorted(types)}")
            report.check("未发布草稿的公开链接为 null",
                         experiment.get("service_url") is None and experiment.get("survey_url") is None)

            run = client.get(f"/api/runs/{run_id}", headers=admin_headers).json()["data"]
            names = [call["tool_name"] for call in run.get("tool_calls", [])]
            report.check("工具日志顺序正确",
                         names == ["make_validation_plan", "create_experiment"], f"实际 {names}")
            report.check("运行状态为 succeeded", run.get("status") == "succeeded",
                         f"status={run.get('status')!r}")
            print(f"  真实模型 run_id = {run_id}（请记入 docs/test-results.md）")

    client.close()

    print("\n" + "=" * 60)
    total = len(report.results)
    failed = report.failed
    if failed:
        print(f"结果：{total - len(failed)}/{total} 通过，{len(failed)} 项失败")
        for name, _, detail in failed:
            print(f"  - {name} {detail}")
        return EXIT_FAIL
    print(f"结果：{total}/{total} 全部通过（项目 {project_id}）")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
