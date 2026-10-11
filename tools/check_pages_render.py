"""三页模板渲染与契约自检（B 侧）。

覆盖：
  1) 六个模板都能在"传 context"与"不传 context"两种情况下渲染成功（不抛异常）
  2) 三个新页面的关键 data-testid 与路由接线齐全
  3) 不依赖 bjtime / status_cn 等后端过滤器（A05 里没有这些过滤器）

用法：python tools/check_pages_render.py <app 目录>
"""
from __future__ import annotations

import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
TPL = ROOT / "app" / "templates"

PROJECTS = [
    {"id": "p-1", "name": "校园活动报名网页服务", "status": "planned",
     "data_kind": "real", "created_at": "2026-10-10T00:45:14+00:00"},
    {"id": "p-2", "name": "JS 探针项目", "status": "draft",
     "data_kind": "test", "created_at": "2026-10-10T00:49:12+00:00"},
]
EXPERIMENT = {
    "id": "exp-1", "project_id": "p-1",
    "landing": {"title": "校园活动报名网页服务", "summary": "面向社团组织者的需求验证",
                "target_users": "社团负责人", "value_proposition": "简化报名信息收集",
                "features": ["定制活动介绍页", "报名信息统一收集"],
                "disclaimer": "目前为需求验证，不代表已有商业成交。"},
    "questions": [
        {"id": "q1", "type": "single", "title": "是否遇到整理困难？", "required": True,
         "options": [{"id": "o1", "label": "经常"}, {"id": "o2", "label": "偶尔"}]},
        {"id": "q2", "type": "multiple", "title": "最关心哪些功能？", "required": True,
         "options": [{"id": "o1", "label": "手机方便"}, {"id": "o2", "label": "可导出"}]},
        {"id": "q3", "type": "text", "title": "还有什么问题？", "required": False, "options": []},
    ],
    "published_at": None, "service_url": None, "survey_url": None,
}

results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name))
    print(("PASS  " if ok else "FAIL  ") + name + (("  — " + detail) if detail else ""))


def make_env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TPL)),
                      autoescape=select_autoescape(["html"]))
    env.globals["url_for"] = lambda name, path=None, **kw: (
        "/static" + (path or "") if name == "static" else "/" + str(name)
    )
    return env


def main() -> int:
    env = make_env()
    print("模板目录：%s" % TPL)
    print("=" * 92)

    # 1) 不传 context 也要能渲染（A 只加路由、不准备 context 时仍可用）
    for name in ("projects.html", "project_new.html", "workspace.html"):
        try:
            html = env.get_template(name).render()
            check("%s 无 context 也能渲染" % name, len(html) > 200, "%d 字节" % len(html))
        except Exception as error:  # noqa: BLE001
            check("%s 无 context 也能渲染" % name, False, "%s: %s" % (type(error).__name__, error))

    # 2) 传 context 正常
    try:
        html = env.get_template("projects.html").render(projects=PROJECTS)
        check("projects.html 传 projects 渲染成功", "校园活动报名网页服务" in html)
        check("  渲染出两行项目", html.count("project-row-") == 2)
        check("  状态中文化", "已出方案" in html and "草稿" in html)
        check("  数据类型中文化", "真实实验" in html and "测试样例" in html)
        check("  时间以 data-utc 输出（前端转北京时间）", html.count("data-utc=") == 2)
        check("  含空状态容器", 'data-testid="project-empty"' in html)
        check("  含新建入口", 'data-testid="project-create-link"' in html)
        check("  含打开工作台链接", "/projects/p-1" in html and "project-open-p-1" in html)
    except Exception as error:  # noqa: BLE001
        check("projects.html 传 projects 渲染成功", False, str(error))

    try:
        html = env.get_template("projects.html").render(projects=[])
        check("projects.html 空列表仍渲染出表头与空状态", 'data-testid="project-list-body"' in html)
    except Exception as error:  # noqa: BLE001
        check("projects.html 空列表", False, str(error))

    # 3) 新建页字段与总纲 §7.1 一致
    html = env.get_template("project_new.html").render()
    for field in ("name", "idea", "target_users", "skills", "budget_cny", "validation_goal", "data_kind"):
        check("project_new 含字段 %s" % field, ('name="%s"' % field) in html)
    check("project_new 依赖的 testid 齐全",
          all(marker in html for marker in
              ('data-testid="project-form"', 'data-testid="project-create"',
               'data-testid="project-idea"', 'data-testid="field-errors"')))
    check("project_new 预算默认 100 且给出范围提示", 'value="100"' in html and "0～100000" in html)
    check("project_new 不把 placeholder 当已填值", "placeholder=" in html)

    # 4) 工作台接线
    html = env.get_template("workspace.html").render()
    for marker in ('data-testid="plan-generate"', 'data-testid="run-log"',
                   'data-testid="experiment-publish"', 'data-testid="service-url"',
                   'data-testid="survey-url"', 'data-testid="copy-service-url"',
                   'data-testid="project-status"', 'data-testid="draft-edit"',
                   'data-testid="field-errors"'):
        check("workspace 含 %s" % marker, marker in html)
    check("workspace 未接通的统计/复盘明确禁用",
          all(('data-testid="%s" disabled' % t) in html
              for t in ("response-count", "review-generate", "report-content", "report-download")))
    check("workspace 不显示任何假统计数字",
          "response_count" not in html and "percentage" not in html)
    check("workspace 声明统计尚未接入页面", "反馈统计尚未接入页面，暂不可用" in html)

    # 5) 既有页面回归（C 交付的 4 个）
    try:
        html = env.get_template("experiment_edit.html").render(
            experiment=EXPERIMENT, running_run_id=None, is_locked=False)
        check("experiment_edit.html 仍可渲染且保留 experiment-save",
              'data-testid="experiment-save"' in html)
    except Exception as error:  # noqa: BLE001
        check("experiment_edit.html 仍可渲染", False, str(error))

    for name, ctx in (("service.html", {"landing": EXPERIMENT["landing"], "questions": EXPERIMENT["questions"],
                                        "is_preview": True, "service_url": "/x", "survey_url": "/y",
                                        "submit_url": None}),
                      ("survey.html", {"landing": EXPERIMENT["landing"], "questions": EXPERIMENT["questions"],
                                       "is_preview": False, "service_url": "/x", "survey_url": "/y",
                                       "submit_url": "http://x/api/public/e/t/responses"})):
        try:
            html = env.get_template(name).render(**ctx)
            check("%s 仍可渲染" % name, len(html) > 200)
            if name == "survey.html":
                check("  survey.html 保留 C 的 testid",
                      all(m in html for m in ('data-testid="survey-form"', 'data-testid="survey-consent"',
                                              'data-testid="survey-submit"', 'data-testid="submission-success"',
                                              'data-testid="survey-status"')))
                check("  survey.html 新增 data-questions", "data-questions=" in html)
                check("  survey.html 新增 field-errors 容器", 'data-testid="field-errors"' in html)
        except Exception as error:  # noqa: BLE001
            check("%s 仍可渲染" % name, False, str(error))

    failed = [name for ok, name in results if not ok]
    print("=" * 92)
    print("共 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(failed), len(failed)))
    for name in failed:
        print("  失败：", name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
