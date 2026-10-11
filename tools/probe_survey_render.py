"""把 survey.html 用真实 context 渲染出来，交给 Node 探针驱动（B 侧取证用）。

为什么要这一步：C 的 verify_templates.py 用 TestClient，只验证"服务端返回的 HTML 里有某个字符串"，
不执行 JS。本脚本产出"真实渲染结果"，再由 Node 探针按这份 HTML 搭 DOM 并跑 app.js，
从而覆盖 C 自己承认未测的"前端 JS 交互"。

用法：
    python tools/probe_survey_render.py <模板目录> <输出目录> [--preview]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# 与 docs/verify_templates.py 同一套夹具，保证对比基线一致
LANDING = {
    "title": "校园活动报名网页服务",
    "summary": "面向社团组织者的服务需求验证，帮助收集并整理报名信息。",
    "target_users": "社团负责人、活动组织者",
    "value_proposition": "简化报名信息收集与整理",
    "features": ["定制活动介绍页", "报名信息统一收集", "结果导出"],
    "disclaimer": "目前为需求验证，不代表已有商业成交或完整服务上线。",
}
QUESTIONS = [
    {"id": "q1", "type": "single", "title": "你是否遇到报名信息整理困难？", "required": True,
     "options": [{"id": "o1", "label": "经常"}, {"id": "o2", "label": "偶尔"}, {"id": "o3", "label": "没有"}]},
    {"id": "q2", "type": "single", "title": "是否愿意了解或试用定制报名网页？", "required": True,
     "options": [{"id": "o1", "label": "愿意"}, {"id": "o2", "label": "不确定"}, {"id": "o3", "label": "不愿意"}]},
    {"id": "q3", "type": "text", "title": "你目前的报名流程有什么问题？", "required": False, "options": []},
    {"id": "q4", "type": "multiple", "title": "你最关心哪些功能？", "required": True,
     "options": [{"id": "o1", "label": "手机使用方便"}, {"id": "o2", "label": "信息导出"},
                 {"id": "o3", "label": "操作简单"}]},
]
TOKEN = "v" * 44


def main() -> int:
    tpl_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    preview = "--preview" in sys.argv
    out_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(str(tpl_dir)),
                      autoescape=select_autoescape(["html"]))
    # Starlette 的 Jinja2Templates 会注入 url_for；裸 Jinja 环境需要自己补一个等价实现
    env.globals["url_for"] = lambda name, path=None, **kw: (
        "/static" + (path or "") if name == "static" else "/" + str(name)
    )
    template = env.get_template("survey.html")

    submit_url = None if preview else f"http://testserver/api/public/e/{TOKEN}/responses"
    html = template.render(
        landing=LANDING, questions=QUESTIONS, is_preview=preview,
        service_url=f"http://testserver/e/{TOKEN}",
        survey_url=f"http://testserver/e/{TOKEN}/survey",
        submit_url=submit_url,
    )
    target = out_dir / ("survey_preview.html" if preview else "survey_public.html")
    target.write_text(html, encoding="utf-8")

    # 把"渲染结果里到底有没有这些契约要素"直接打出来——这是静态证据
    markers = {
        "form 带 data-questions": 'data-questions=' in html,
        "form 带 data-submit-url": 'data-submit-url=' in html,
        "form 带 data-is-preview": 'data-is-preview=' in html,
        "存在 field-errors 容器": 'data-testid="field-errors"' in html,
        "存在 survey-status 容器": 'data-testid="survey-status"' in html,
        "存在 submission-success": 'data-testid="submission-success"' in html,
        "提交按钮被禁用": 'data-testid="survey-submit" disabled' in html,
    }
    summary = {"rendered": str(target), "is_preview": preview, "markers": markers,
               "bytes": len(html)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    expected = {key: True for key in markers}
    expected["提交按钮被禁用"] = preview
    return 0 if markers == expected else 1


if __name__ == "__main__":
    sys.exit(main())
