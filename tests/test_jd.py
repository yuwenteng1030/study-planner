"""JD 出處引用：關鍵字比對、引用一定是原文、JD 進評分、只刪得到自己的、匯出與刪除。"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.jd_parser import (
    MAX_JD_CHARS,
    analyze,
    highlight_markdown,
    jd_stats,
    md_escape,
    sentences,
)
from core.scoring import jd_factor
from db import repositories as repo
from db.models import User

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JD_PAGE = str(PROJECT_ROOT / "app" / "pages" / "5_職缺JD.py")


# --- 比對 ---------------------------------------------------------------


def test_quote_is_the_original_sentence_and_points_at_the_keyword() -> None:
    text = "工作內容：\n- 熟悉 Hash Tables 與排序\n- 會用 Python"
    a = analyze(text)
    m = a.topics["hashmaps"]
    assert m.quote == "熟悉 Hash Tables 與排序"
    assert m.quote[m.start : m.end] == "Hash Tables"
    assert m.quote in text, "出處必須是原文，不能改寫"
    assert a.topics["sorting"].quote == m.quote
    assert a.topics["python_basic"].keyword == "Python"


@pytest.mark.parametrize(
    "text, topic",
    [
        ("We use a modern tech stack.", "stacks_queues"),
        ("Experience with full-stack development.", "stacks_queues"),
        ("Kafka message queue experience", "stacks_queues"),
        ("Live on Main Street", "trees"),
        ("cheap and fast", "heaps"),
        ("JavaScript developer", "python_basic"),
        ("Our tech-stack is modern", "stacks_queues"),
        ("ELK stack monitoring", "stacks_queues"),
        ("Kafka queue systems", "stacks_queues"),
        ("a wide array of tools", "arrays"),
        ("decision trees and random forests", "trees"),
    ],
)
def test_words_that_only_look_like_a_topic_do_not_count(text, topic) -> None:
    assert topic not in analyze(text).topics


@pytest.mark.parametrize(
    "text, topic",
    [
        ("Implement a stack and a queue", "stacks_queues"),
        ("binary trees and BST", "trees"),
        ("BFS/DFS on graphs", "graphs"),
        ("熟悉動態規劃", "dp_1d"),
        ("熟悉動態規劃", "dp_2d"),
        ("priority queue", "heaps"),
        ("矩陣運算", "two_d_arrays"),
        ("Python3 scripting", "python_basic"),
        ("Python3.11", "python_basic"),
        ("linked-list and two-pointer tricks", "linked_lists"),
        ("two-pointer tricks", "arrays"),
        ("熟悉堆積", "heaps"),
    ],
)
def test_explicit_words_count(text, topic) -> None:
    assert topic in analyze(text).topics


def test_generic_words_do_not_boost_any_topic() -> None:
    a = analyze("熟悉資料結構與演算法，能通過線上測驗。")
    assert a.topics == {}, "泛稱算進任何一個主題都不公平"
    assert a.generic


def test_skills_outside_the_bank_are_listed_separately() -> None:
    a = analyze("需要 SQL、Linux、Docker，懂 C++ 更好。Plan C 也要準備。")
    assert {"SQL／資料庫", "Linux／Shell", "Docker／Kubernetes", "C／C++"} <= set(a.other_skills)
    assert a.other_skills["C／C++"].keyword == "C++", "單獨的 C 不算"


def test_keyword_does_not_swallow_the_following_space() -> None:
    """「Python 經驗」要標成 **Python** 經驗：吃進空白的話，結尾的 ** 前面是空白，markdown 不會加粗。"""
    m = analyze("三年 Python 經驗").topics["python_basic"]
    assert m.keyword == "Python"
    assert "**Python**" in highlight_markdown(m)


def test_proper_noun_skills_are_case_sensitive() -> None:
    a = analyze("Please react quickly; spring 招募開始。")
    assert "前端" not in a.other_skills and "Java／C#／.NET" not in a.other_skills
    assert "Java／C#／.NET" in analyze("ASP.NET Core").other_skills


def test_bullets_are_stripped_but_numbers_in_text_are_kept() -> None:
    assert sentences("- 熟悉 Python\n1. 會 SQL\n3+ years of Python\n3.5 年以上 Python 經驗") == [
        "熟悉 Python", "會 SQL", "3+ years of Python", "3.5 年以上 Python 經驗"
    ], "引用必須是原文：3.5 年不能變成 5 年"


def test_long_sentence_is_clipped_around_the_keyword() -> None:
    text = "甲" * 200 + "binary search" + "乙" * 200
    m = analyze(text).topics["search"]
    assert m.quote.startswith("…") and m.quote.endswith("…")
    assert m.quote[m.start : m.end] == "binary search"
    assert len(m.quote) < 200


def test_huge_paste_is_cut_off() -> None:
    text = "x " * MAX_JD_CHARS + "Python"
    assert "python_basic" not in analyze(text).topics


def test_one_jd_counts_once_per_topic_however_often_it_repeats() -> None:
    stats = jd_stats([analyze("Python Python Python"), analyze("SQL only")])
    assert stats.total == 2 and stats.hits == {"python_basic": 1}
    assert jd_factor("python_basic", stats) == 1.5
    assert jd_factor("arrays", stats) == 1.0


def test_markdown_in_a_jd_cannot_inject_links_or_colors() -> None:
    m = analyze("熟悉 hash tables，詳見 [這裡](http://evil.example) :red[注意]").topics["hashmaps"]
    out = highlight_markdown(m)
    assert "**hash tables**" in out
    assert "](" not in out and ":red[" not in out
    assert md_escape("a_b*c") == r"a\_b\*c"


# --- 資料庫 -------------------------------------------------------------


def _user(session, user_id=1):
    session.add(User(id=user_id, email=f"u{user_id}@example.com", display_name="測試"))
    session.flush()


def test_save_load_and_stats(db_session) -> None:
    _user(db_session)
    repo.save_jd(db_session, 1, "  後端 A  ", "需要 Python 與 hash table")
    repo.save_jd(db_session, 1, "", "需要 Python")
    jds = repo.load_jds(db_session, 1)
    assert [j.title for j in jds] == ["未命名職缺", "後端 A"], "新的在前，空標題給預設名"
    stats = repo.load_jd_stats(db_session, 1)
    assert stats.total == 2 and stats.hits["python_basic"] == 2 and stats.hits["hashmaps"] == 1
    cites = repo.load_jd_citations(db_session, 1)
    assert {t for t, _m in cites["python_basic"]} == {"後端 A", "未命名職缺"}


def test_empty_body_and_too_many_jds_are_rejected(db_session) -> None:
    _user(db_session)
    with pytest.raises(ValueError):
        repo.save_jd(db_session, 1, "空的", "   ")
    for n in range(repo.MAX_JDS_PER_USER):
        repo.save_jd(db_session, 1, f"JD {n}", "Python")
    with pytest.raises(ValueError):
        repo.save_jd(db_session, 1, "一份太多", "Python")


def test_cannot_delete_someone_elses_jd(db_session) -> None:
    _user(db_session, 1)
    _user(db_session, 2)
    theirs = repo.save_jd(db_session, 2, "別人的", "Python")
    assert not repo.delete_jd(db_session, 1, theirs.id)
    assert repo.load_jds(db_session, 2)
    assert repo.delete_jd(db_session, 2, theirs.id)
    assert not repo.load_jds(db_session, 2)


def test_export_includes_jds_and_clearing_records_keeps_them(db_session) -> None:
    _user(db_session)
    repo.save_jd(db_session, 1, "後端 A", "需要 Python")
    export = repo.export_user_data(db_session, 1)
    assert export["format_version"] == 4
    assert export["jds"][0]["title"] == "後端 A" and export["jds"][0]["body"] == "需要 Python"

    counts = repo.delete_user_data(db_session, 1)
    assert counts["jds"] == 0 and repo.load_jds(db_session, 1), "清除學習紀錄不刪使用者貼的 JD"
    counts = repo.delete_user_data(db_session, 1, drop_account=True)
    assert counts["jds"] == 1 and db_session.get(User, 1) is None


def test_jds_change_what_gets_planned(db_session) -> None:
    """貼了 JD 的主題要真的排得比較前面，不是只顯示好看。"""
    from core.planner import build_plan
    from core.scoring import UserContext

    _user(db_session)
    topics = repo.load_topic_nodes(db_session)
    tasks = repo.load_task_inputs(db_session)

    def first_day_topics(jd):
        plan = build_plan(
            topics=topics, tasks=tasks, start_date=_dt.date(2026, 10, 24), days=10,
            daily_minutes=60, user=UserContext(levels={}, goal_topics=frozenset()), jd=jd,
        )
        return [s.topic_id for s in sorted(plan.scored_topics.values(), key=lambda s: -s.score)]

    before = first_day_topics(repo.load_jd_stats(db_session, 1))
    repo.save_jd(db_session, 1, "A", "Experience with heaps and priority queues")
    repo.save_jd(db_session, 1, "B", "Heap and greedy algorithms")
    after = first_day_topics(repo.load_jd_stats(db_session, 1))
    assert after.index("heaps") < before.index("heaps")


# --- 頁面 ---------------------------------------------------------------


def test_jd_page_saves_and_shows_citations(seeded_app_db) -> None:
    app = AppTest.from_file(JD_PAGE, default_timeout=60).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    app.text_input[0].set_value("UI 測試職缺")
    app.text_area[0].set_value("必要條件：熟悉 binary search 與 Python。")
    app.button[0].click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.success and "UI 測試職缺" in app.success[0].value
    assert app.text_area[0].value == "", "存成功才清空表單"
    assert any("**binary search**" in m.value for m in app.markdown)

    # 刪掉它，頁面回到原本的樣子
    delete_buttons = [b for b in app.button if b.label == "刪掉這份 JD"]
    for button in delete_buttons:
        button.click().run()
        app.run()
    assert not app.exception


def test_failed_save_keeps_what_was_pasted(seeded_app_db) -> None:
    app = AppTest.from_file(JD_PAGE, default_timeout=60).run()
    app.text_input[0].set_value("空的")
    app.text_area[0].set_value("   ")
    app.button[0].click().run()
    assert app.error, "內容空白要擋下"
    assert app.text_input[0].value == "空的", "存檔失敗不能把使用者填的東西清掉"


def test_jd_page_renders_for_demo_visitors_with_sample_jds(monkeypatch, seeded_app_db) -> None:
    monkeypatch.setenv("STUDY_PLANNER_DEMO", "1")
    app = AppTest.from_file(JD_PAGE, default_timeout=60).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert any("範例：應用系統工程師" in e.label for e in app.expander)
    assert any("題庫以外的技能" in m.value for m in app.markdown)
