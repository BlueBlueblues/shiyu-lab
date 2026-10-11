import html
import re


def text(value):
    # Markdown 导出仍将模型与用户文本视为数据，避免形成 HTML、链接或表格结构。
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html.escape(str(value))).replace("\n", " ").replace("\r", " ")


def markdown_report(report):
    stats, review = report["statistics"], report["review"]
    lines = [f"# {text(report['project_name'])}：实验复盘", "",
             f"数据标识：{report['data_kind']}（test 为测试样例，非真实试用证据）",
             f"创建时间（UTC）：{report['created_at']}", f"运行 ID：{report['run_id']}",
             f"报告 ID：{report['id']}",
             f"快照有效回答份数：{report['response_count']}；截止回答 ID：{report['cutoff_response_id']}",
             "回答份数不等同独立参与人数。", "", "## 保存的统计快照", ""]
    for question in stats["questions"]:
        lines.append(f"### {text(question['id'])}（{question['type']}）")
        lines.append(f"该题有效作答份数（分母）：{question['answered_count']}")
        if question["type"] == "text":
            lines.extend(f"- 回答 {item['response_id']}：{text(item['text'])}" for item in question["texts"])
        else:
            for option in question["options"]:
                percent = "暂无数据（percentage=null）" if option["percentage"] is None else f"{option['percentage']:.2f}%"
                lines.append(f"- {text(option['id'])} / {text(option['label'])}：{option['count']} / {question['answered_count']}；{percent}")
        lines.append("")
    lines.extend(["## 结论", "", text(review["summary"]), "", "## 假设与证据", ""])
    for item in review["hypothesis_assessments"]:
        lines.append(f"- {item['hypothesis_id']}：{item['status']}；{text(item['reason'])}")
        for ref in item["evidence_refs"]:
            lines.append(f"  - 证据：{text(ref['question_id'])} / " +
                         (f"选项 {text(ref['option_id'])}" if "option_id" in ref else f"回答 {ref['response_id']}"))
    for heading, values in (("关键反馈", review["key_feedback"]), ("局限", review["limitations"])):
        lines.extend(["", f"## {heading}", ""] + [f"- {text(value)}" for value in values])
    lines.extend(["", "## 下一步行动", ""])
    lines.extend(f"- {text(item['action'])}；负责人：{text(item['owner_role'])}；完成标准：{text(item['completion_criterion'])}"
                 for item in review["next_actions"])
    return "\n".join(lines) + "\n"
