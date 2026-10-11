"""统计口径用例。

成员C指南 C02 第 5 条、C03 与总纲 §7.4 / §10 T07。

固定样本（全部为 ``data_kind=test``，见 conftest.SAMPLE_RESPONSES）：

    | 回答 | 必答单选 q1 | 必答单选 q2 | 可选文字 q3 | 可选多选 q4 |
    | R1   | o1          | o1          | 第一条文字  | o1、o2      |
    | R2   | o2          | o1          | 未答        | o1          |
    | R3   | o1          | o2          | 第二条文字  | o2          |
    | R4   | o2          | o2          | 未答        | 未答        |

手算期望（不从被测实现反推）：
    response_count = 4
    q1 answered_count=4 → o1 2/4=50.00%，o2 2/4=50.00%，o3 0/4=0.00%
    q2 answered_count=4 → o1 2/4=50.00%，o2 2/4=50.00%，o3 0/4=0.00%
    q3 answered_count=2 → 两条文字，带各自的 response_id
    q4 answered_count=3 → o1 2/3=66.67%，o2 2/3=66.67%，o3 0%，o4 0%
    多选百分比合计 133.34% > 100%，是允许的（总纲 §7.4）。
"""

from __future__ import annotations

import pytest

from c_integration.conftest import SAMPLE_RESPONSES, data_of


def question(stats: dict, question_id: str) -> dict:
    matches = [q for q in stats["questions"] if q["id"] == question_id]
    assert len(matches) == 1, f"统计里应恰好有题目 {question_id}：{[q['id'] for q in stats['questions']]}"
    return matches[0]


def option(question_stats: dict, option_id: str) -> dict:
    matches = [o for o in question_stats["options"] if o["id"] == option_id]
    assert len(matches) == 1, (
        f"题目 {question_stats['id']} 的统计应包含全部选项（含 0 次），缺少 {option_id}"
    )
    return matches[0]


def test_statistics_include_every_question_and_option(app_env, published_with_sample):
    """总纲 §7.4：真实输出必须包含所有题和全部选项，即使计数为 0。"""
    stats = app_env.statistics(published_with_sample["experiment_id"])

    assert stats["experiment_id"] == published_with_sample["experiment_id"]
    assert stats["response_count"] == 4, f"应统计到 4 份回答：{stats['response_count']}"
    assert stats["cutoff_response_id"] == max(published_with_sample["response_ids"]), (
        "cutoff_response_id 应是本快照包含的最大回答 ID"
    )

    assert [q["id"] for q in stats["questions"]] == ["q1", "q2", "q3", "q4"]
    assert question(stats, "q1")["options"] and len(question(stats, "q1")["options"]) == 3
    assert len(question(stats, "q4")["options"]) == 4, "未被选择的选项也必须输出"
    assert set(question(stats, "q3")) == {"id", "type", "answered_count", "texts"}, "简答题只返回文本统计，不含选项字段"


def test_required_single_choice_uses_all_responses_as_denominator(app_env, published_with_sample):
    """必答单选分母为全部 4 份回答。"""
    stats = app_env.statistics(published_with_sample["experiment_id"])
    q1 = question(stats, "q1")

    assert q1["type"] == "single"
    assert q1["answered_count"] == 4, f"必答单选应有 4 份作答：{q1['answered_count']}"
    assert option(q1, "o1")["count"] == 2 and option(q1, "o1")["percentage"] == 50.0
    assert option(q1, "o2")["count"] == 2 and option(q1, "o2")["percentage"] == 50.0
    assert option(q1, "o3")["count"] == 0 and option(q1, "o3")["percentage"] == 0.0, (
        "0 次选项要输出 0（此时分母不为 0，0% 是真实观测）"
    )


def test_optional_multiple_choice_denominator_is_answered_count(app_env, published_with_sample):
    """T07 关键点：可选多选只有 3 份作答，2/3 应得 66.67%，而不是 2/4=50%。"""
    stats = app_env.statistics(published_with_sample["experiment_id"])
    q4 = question(stats, "q4")

    assert q4["type"] == "multiple"
    assert q4["answered_count"] == 3, (
        f"可选多选的分母应是作答份数 3，实际 {q4['answered_count']}"
    )
    assert option(q4, "o1")["count"] == 2
    assert option(q4, "o1")["percentage"] == 66.67, (
        f"应为 2/3=66.67%，实际 {option(q4, 'o1')['percentage']}（若为 50.0 说明分母用错）"
    )
    assert option(q4, "o2")["count"] == 2
    assert option(q4, "o2")["percentage"] == 66.67
    assert option(q4, "o3")["count"] == 0 and option(q4, "o3")["percentage"] == 0.0
    assert option(q4, "o4")["count"] == 0 and option(q4, "o4")["percentage"] == 0.0

    total = sum(o["percentage"] for o in q4["options"])
    assert round(total, 2) == 133.34, (
        f"多选百分比总和允许超过 100%，本样本应为 133.34%，实际 {round(total, 2)}"
    )


def test_multiple_choice_counts_each_response_once(app_env, published_with_sample):
    """总纲 §7.4：一条回答对某选项最多贡献 1 次，所以 o1 的计数不会超过 3。"""
    stats = app_env.statistics(published_with_sample["experiment_id"])
    q4 = question(stats, "q4")
    for each in q4["options"]:
        assert 0 <= each["count"] <= q4["answered_count"], (
            f"选项计数超出作答份数：{each}"
        )


def test_optional_text_question_lists_texts_with_response_id(app_env, published_with_sample):
    """可选简答：answered_count 为实际填写份数，texts 带 response_id 与文本。"""
    stats = app_env.statistics(published_with_sample["experiment_id"])
    q3 = question(stats, "q3")

    assert q3["type"] == "text"
    assert q3["answered_count"] == 2, f"只有 2 份填写了简答：{q3['answered_count']}"

    texts = q3["texts"]
    assert len(texts) == 2, f"应返回 2 条文字：{texts}"
    by_id = {item["response_id"]: item["text"] for item in texts}
    first_id, third_id = (
        published_with_sample["response_ids"][0],
        published_with_sample["response_ids"][2],
    )
    assert by_id.get(first_id) == SAMPLE_RESPONSES[0]["q3"], by_id
    assert by_id.get(third_id) == SAMPLE_RESPONSES[2]["q3"], by_id
    assert all(isinstance(item["response_id"], int) for item in texts)


def test_zero_response_statistics_are_null_not_zero(app_env, published):
    """总纲 §7.4：分母为 0 时 percentage 必须为 null，不能显示 0% 暗示观察到零需求。"""
    stats = app_env.statistics(published["experiment_id"])

    assert stats["response_count"] == 0
    assert stats["cutoff_response_id"] == 0, "零回答时截止 ID 应为 0"
    for question_stats in stats["questions"]:
        assert question_stats["answered_count"] == 0, question_stats
        if question_stats["type"] == "text":
            assert question_stats["texts"] == []
        else:
            for each in question_stats["options"]:
                assert each["count"] == 0, each
                assert each["percentage"] is None, (
                    f"零分母必须返回 null，实际 {each['percentage']}（{each}）"
                )


def test_statistics_are_deterministic_and_server_side(app_env, published_with_sample):
    """统计由程序计算：同样的数据连续读取结果一致，且不因读统计而调用模型。"""
    before = app_env.fake_llm.call_count
    first = app_env.statistics(published_with_sample["experiment_id"])
    second = app_env.statistics(published_with_sample["experiment_id"])

    assert first == second, "同一快照下统计必须可重复"
    assert app_env.fake_llm.call_count == before, "统计接口不得调用模型"
