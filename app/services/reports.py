import html
import re


def text(value):
    # Markdown 导出仍将模型与用户文本视为数据，避免形成 HTML、链接或表格结构。
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html.escape(str(value))).replace("\n", " ").replace("\r", " ")


def markdown_report(report):
    stats, review = report["statistics"], report["review"]
    lines = [f"# {text(report['project_name'])}：实验复盘", "",
             "试玉 · SHIYU LAB｜以试辨真，以攻成玉", "",
             f"数据标识：{report['data_kind']}（test 为测试样例，非真实试用证据）",
             f"创建时间（UTC）：{report['created_at']}", f"运行 ID：{report['run_id']}",
             f"报告 ID：{report['id']}",
             f"快照有效回答份数：{report['response_count']}；截止回答 ID：{report['cutoff_response_id']}",
             "回答份数不等同独立参与人数。", "",
             "数据只反映问卷自报内容；未采集真实订单、履约、付款或访问量。", ""]
    if report['response_count'] < 5:
        lines.extend(["证据不足或探索性小样本：不足 5 份回答，不能代表总体；5 份也不是统计充分性的保证。", ""])
    practice = report.get("practice")
    if practice:
        lines.extend(["## 学生创业实践记录", "", f"创业想法：{text(practice['idea'])}",
            f"学生设定的目标用户：{text(practice['target_users'])}",
            f"验证目标：{text(practice['validation_goal'])}",
            f"可投入预算：{practice['budget_cny']} 元（不是客户可接受价格）", ""])
    plan = report.get("plan_snapshot")
    if plan:
        questions = {question['id']: question for question in plan['questions']}
        lines.extend(["## 本轮假设与验证方法（保存的计划快照）", "",
            f"实验 ID：{text(report['experiment_id'])}", f"实验页面与问卷成果：{text(plan['landing']['title'])}"])
        if report.get('public_path'):
            lines.extend([f"公开服务路径：{text(report['public_path'])}", f"公开问卷路径：{text(report['public_path'] + '/survey')}"])
        for hypothesis in plan['hypotheses']:
            lines.append(f"- {text(hypothesis['id'])}：{text(hypothesis['statement'])}")
            lines.extend(f"  - {text(qid)}：{text(questions[qid]['title'])}" for qid in hypothesis['question_ids'])
        lines.extend(["", "学生已完成发布审核；未记录逐次编辑日志，以发布后保存的计划快照为准。", ""])
    else:
        lines.extend(["历史报告缺少计划快照，不能用当前计划补造原始假设。", ""])
    iteration = report.get("iteration")
    if iteration:
        if iteration.get('contract_version') == 4:
            strategy = iteration['strategy']
            iteration = {**iteration, 'diagnosis':strategy['diagnosis'], 'changes':strategy['suggested_changes']}
            lines.extend(['攻玉提出策略，人工批准后琢玉独立设计；策略建议不等于最终实验内容。', ''])
        lines.extend(["## 多轮修改与人工确认", "", f"方案 ID：{text(iteration['id'])}",
            f"来源报告：{text(iteration['source_report_id'])}", f"人工确认时间（UTC）：{text(iteration['confirmed_at'])}",
            f"修改诊断：{text(iteration['diagnosis'])}"])
        lines.extend(f"- {text(item['field'])}：{text(item['reason'])}" for item in iteration['changes'])
        if plan and "next_plan" in iteration:
            changed = [field for field in plan if plan[field] != iteration['next_plan'][field]]
            lines.append("确认方案后至本报告快照的最终内容差异：" + ("、".join(changed) or "无"))
        lines.append("")
    lines.extend(["## 已记录的 AI 与程序任务", "", "以下仅列报告生成时及之前已保存的工具执行，不追认缺失记录或后续任务。"])
    for task in report.get('tasks', []):
        execution = {'model':'独立模型推理','program':'程序工具，非AI推理'}.get(task.get('execution'),'历史工具，执行方式未单独记录')
        lines.append(f"- {text(task.get('agent') or task['tool_name'])}：{text(execution)}；{text(task['status'])}；运行 {text(task['run_id'])}")
    if not report.get('tasks'):
        lines.append("缺少工具执行记录，不能据此证明识玉实时调用。")
    lines.extend(["", "## 保存的统计快照", ""])
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
