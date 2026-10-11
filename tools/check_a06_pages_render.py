"""三页模板渲染与契约自检（B 侧，A06 版）。

覆盖：
  1) 三个管理页面按 A 的真实 context 渲染（A 的测试断言 context 恰好是那 5 个键）
  2) 关键 data-testid、空状态、禁用状态齐全
  3) 不依赖 bjtime / status_cn 等后端过滤器（A06 里没有这些过滤器）
  4) C 交付的 4 个模板未被破坏

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
LANDING = {"title": "校园活动报名网页服务", "summary": "面向社团组织者的需求验证",
           "target_users": "社团负责人", "value_proposition": "简化报名信息收集",
           "features": ["定制活动介绍页", "报名信息统一收集"],
           "disclaimer": "目前为需求验证，不代表已有商业成交。"}
QUESTIONS = [
    {"id": "q1", "type": "single", "title": "是否遇到整理困难？", "required": True,
     "options": [{"id": "o1", "label": "经常"}, {"id": "o2", "label": "偶尔"}]},
    {"id": "q2", "type": "multiple", "title": "最关心哪些功能？", "required": True,
     "options": [{"id": "o1", "label": "手机方便"}, {"id": "o2", "label": "可导出"}]},
    {"id": "q3", "type": "text", "title": "还有什么问题？", "required": False, "options": []},
    {"id": "q4", "type": "single", "title": "愿意试用吗？", "required": True,
     "options": [{"id": "o1", "label": "愿意"}, {"id": "o2", "label": "不愿意"}]},
]
PLAN = {
    "target_customer": "有活动组织经验的社团负责人",
    "pain_points": ["报名信息汇总耗时"],
    "value_proposition": "提供简单、可维护的活动报名网页",
    "hypotheses": [
        {"id": "h1", "statement": "部分组织者存在报名整理困难", "question_ids": ["q1"]},
        {"id": "h2", "statement": "部分组织者愿意试用定制网页", "question_ids": ["q2"]},
        {"id": "h3", "statement": "用户能指出现有流程的改进点", "question_ids": ["q3"]},
    ],
    "experiment_steps": ["邀请有活动组织经验的同学填写问卷"],
    "next_tasks": ["人工检查问卷是否诱导回答"],
    "landing": LANDING, "questions": QUESTIONS,
}
PROJECT = {"id": "p-1", "name": "校园活动报名网站服务", "idea": "为校园社团制作低成本活动报名网页。",
           "target_users": "社团负责人", "skills": "基础网页开发", "budget_cny": 100.0,
           "validation_goal": "验证是否有试用需求", "data_kind": "test",
           "created_at": "2026-10-10T00:45:14+00:00", "status": "draft"}
EXPERIMENT = {"id": "exp-1", "project_id": "p-1", "landing": LANDING, "questions": QUESTIONS,
              "published_at": None, "service_url": None, "survey_url": None}

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


def workspace_ctx(**over):
    """A 的 project_workspace 走的是 render_experiment(..., detail)，即 5 个键平铺。"""
    ctx = {"project": PROJECT, "plan": PLAN, "experiment": EXPERIMENT,
           "latest_report_meta": None, "running_run_id": None}
    ctx.update(over)
    return ctx


def main() -> int:
    env = make_env()
    print("模板目录：%s" % TPL)
    print("=" * 92)

    # ---- projects.html：A 传 {"projects": [...]} ----
    try:
        html = env.get_template("projects.html").render(projects=PROJECTS)
        check("projects.html 按 A 的 context 渲染", "校园活动报名网页服务" in html)
        check("  渲染出两行项目", html.count("project-row-") == 2)
        check("  状态与数据类型中文化", "已出方案" in html and "测试样例" in html)
        check("  创建时间以 data-utc 输出（前端转北京时间）", html.count("data-utc=") == 2)
        check("  含空状态容器与新建入口",
              'data-testid="project-empty"' in html and 'data-testid="project-create-link"' in html)
        check("  含打开工作台链接", "/projects/p-1" in html and "project-open-p-1" in html)
    except Exception as error:  # noqa: BLE001
        check("projects.html 按 A 的 context 渲染", False, str(error))

    # ---- project_new.html：A 传 {} ----
    try:
        html = env.get_template("project_new.html").render()
        check("project_new.html 空 context 渲染成功", len(html) > 500)
        check("  含总纲 §7.1 全部字段",
              all(('name="%s"' % f) in html for f in
                  ("name", "idea", "target_users", "skills", "budget_cny",
                   "validation_goal", "data_kind")))
        check("  依赖的 testid 齐全",
              all(m in html for m in ('data-testid="project-form"', 'data-testid="project-create"',
                                      'data-testid="project-idea"', 'data-testid="field-errors"')))
        check("  预算默认 100 且给出范围提示", 'value="100"' in html and "0～100000" in html)
    except Exception as error:  # noqa: BLE001
        check("project_new.html 空 context 渲染成功", False, str(error))

    # ---- workspace.html：A 传 detail 的 5 个键 ----
    try:
        html = env.get_template("workspace.html").render(**workspace_ctx())
        compact = html.replace("\n", "").replace(" ", "")
        check("workspace.html 按 A 的 5 键 context 渲染", 'data-testid="project-name"' in html)
        check("  项目信息来自服务端渲染", "为校园社团制作低成本活动报名网页" in html)
        check("  状态与数据类型标签", ">草稿<" in compact and "测试样例" in html)
        check("  test 项目显示测试样例警示", 'data-testid="test-badge"' in html)
        check("  方案区渲染 3 条假设",
              all(("%s　" % h) in html for h in ("h1", "h2", "h3")))
        check("  草稿区渲染题目表（4 题）", html.count("question-q") == 4)
        check("  未发布时给出发布提示与发布按钮",
              'data-testid="publish-hint"' in html and 'data-testid="experiment-publish"' in html)
        check("  未发布时分享链接为空状态", 'data-testid="links-empty"' in html)
        for marker in ('data-testid="plan-generate"', 'data-testid="run-log"',
                       'data-testid="ws-status"', 'data-testid="field-errors"',
                       'data-testid="statistics-card"',
                       'data-testid="responses-load"'):
            check("  含 %s" % marker, marker in html)
        check("  A07 后端已有、页面未接通的入口明确禁用",
              all(('data-testid="%s" disabled' % t) in html
                  for t in ("review-generate", "report-content", "report-download")))
        check("  未发布时统计区说明接口返回 400", "INVALID_STATE" in html)
        check("  不出现任何假统计数字", "percentage" not in html and "response_count" not in html)
        check("  内联配置存在且含题干", "__FO_WORKSPACE__" in html and "是否遇到整理困难" in html)
    except Exception as error:  # noqa: BLE001
        check("workspace.html 按 A 的 5 键 context 渲染", False,
              "%s: %s" % (type(error).__name__, error))

    published = dict(EXPERIMENT, published_at="2026-10-11T01:00:00+00:00",
                     service_url="http://x/e/tok", survey_url="http://x/e/tok/survey")
    try:
        html = env.get_template("workspace.html").render(**workspace_ctx(experiment=published))
        check("workspace 已发布态：显示分享链接与复制按钮",
              'data-testid="service-url"' in html and 'data-testid="copy-survey-url"' in html)
        check("workspace 已发布态：隐藏发布按钮并说明已锁定",
              'data-testid="experiment-publish"' not in html and "已锁定" in html)
        check("workspace 已发布态：统计区进入取数分支", "正在读取统计" in html)
        check("workspace 已发布态：统计口径元素齐全",
              'data-testid="response-count"' in html and 'data-testid="cutoff-response-id"' in html
              and 'data-testid="statistics-body"' in html)
    except Exception as error:  # noqa: BLE001
        check("workspace 已发布态渲染", False, str(error))

    try:
        html = env.get_template("workspace.html").render(**workspace_ctx(plan=None, experiment=None))
        check("workspace 无方案无草稿：空状态齐全",
              'data-testid="plan-empty"' in html and 'data-testid="draft-empty"' in html
              and 'data-testid="statistics-pending"' in html)
    except Exception as error:  # noqa: BLE001
        check("workspace 空状态渲染", False, str(error))

    # ---- C 交付的 4 个模板回归 ----
    try:
        html = env.get_template("experiment_edit.html").render(
            experiment=EXPERIMENT, running_run_id=None, is_locked=False)
        check("experiment_edit.html 未受影响", 'data-testid="experiment-save"' in html)
    except Exception as error:  # noqa: BLE001
        check("experiment_edit.html 未受影响", False, str(error))

    for name, ctx in (("service.html", {"landing": LANDING, "questions": QUESTIONS,
                                        "is_preview": True, "service_url": "/x", "survey_url": "/y",
                                        "submit_url": None}),
                      ("survey.html", {"landing": LANDING, "questions": QUESTIONS,
                                       "is_preview": False, "service_url": "/x", "survey_url": "/y",
                                       "submit_url": "http://x/api/public/e/t/responses"})):
        try:
            html = env.get_template(name).render(**ctx)
            check("%s 未被破坏" % name, len(html) > 200)
            if name == "survey.html":
                check("  survey.html 保留 C 的 testid 且含两处修复",
                      all(m in html for m in ('data-testid="survey-form"', 'data-testid="survey-consent"',
                                              'data-testid="survey-submit"', 'data-testid="submission-success"',
                                              'data-testid="survey-status"', "data-questions=",
                                              'data-testid="field-errors"')))
        except Exception as error:  # noqa: BLE001
            check("%s 未被破坏" % name, False, str(error))

    failed = [name for ok, name in results if not ok]
    print("=" * 92)
    print("共 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(failed), len(failed)))
    for name in failed:
        print("  失败：", name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
