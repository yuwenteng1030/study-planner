"""建立計畫時的「為什麼該做」說明：來源標記、並行呼叫、失敗退回預存。

一律不呼叫外部服務：用假的 ``anthropic.Anthropic``。
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

from sqlalchemy import select, text

import llm.client as client_mod
from core.planner import build_plan
from core.scoring import JDStats, UserContext
from db import repositories as repo
from db.models import PlanItem


class _FakeAnthropic:
    def __init__(self, reply="一句客製說明", fail_on=None, **_kw):
        self._reply, self._fail_on = reply, fail_on
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        if self._fail_on and self._fail_on in kw["messages"][0]["content"]:
            raise RuntimeError("boom")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self._reply)])


def _inputs(session):
    core_plan = build_plan(
        topics=repo.load_topic_nodes(session),
        tasks=repo.load_task_inputs(session),
        start_date=_dt.date(2026, 10, 26),
        days=3,
        daily_minutes=60,
        user=UserContext(),
        jd=JDStats(),
    )
    return core_plan, repo.load_explain_inputs(session, [i.task_id for i in core_plan.items])


def _make(session, core_plan, explanations):
    repo.ensure_user(session, 1)
    return repo.create_plan(
        session,
        user_id=1,
        goal_text="t",
        parsed_goal={},
        start_date=_dt.date(2026, 10, 26),
        days=3,
        daily_minutes=60,
        core_plan=core_plan,
        reasons={k: v.text for k, v in explanations.items() if v.text},
        generated_task_ids={k for k, v in explanations.items() if v.is_generated},
    )


def test_without_key_everything_is_static(db_session, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    core_plan, inputs = _inputs(db_session)
    explanations = client_mod.explain_many(inputs)
    assert all(e.source in ("static", "none") for e in explanations.values())
    plan = _make(db_session, core_plan, explanations)
    rows = db_session.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id)).all()
    assert rows and all(r.reason_source == "static" for r in rows)


def test_generated_text_is_marked_llm_and_failures_fall_back(db_session, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    core_plan, inputs = _inputs(db_session)
    bad_title = inputs[0]["title"]
    import anthropic

    monkeypatch.setattr(
        anthropic, "Anthropic", lambda **kw: _FakeAnthropic(fail_on=f"題目：{bad_title}\n", **kw)
    )
    explanations = client_mod.explain_many(inputs)
    assert len(explanations) == len(inputs)
    assert explanations[inputs[0]["id"]].source in ("static", "none")  # 失敗退回預存
    assert any(e.is_generated for e in explanations.values())
    plan = _make(db_session, core_plan, explanations)
    by_task = {r.task_id: r for r in db_session.scalars(
        select(PlanItem).where(PlanItem.plan_id == plan.id))}
    assert by_task[inputs[0]["id"]].reason_source == "static"
    llm_rows = [r for r in by_task.values() if r.reason_source == "llm"]
    assert llm_rows and all(r.reason == "一句客製說明" for r in llm_rows)


def test_generated_text_drops_links_and_extra_lines(db_session, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    import anthropic

    monkeypatch.setattr(
        anthropic, "Anthropic",
        lambda **kw: _FakeAnthropic(reply="練雜湊[點我](http://evil.example)\n第二行", **kw),
    )
    out = client_mod.explain_task("T", "Arrays", [], "預存")
    assert out.text == "練雜湊點我" and "http" not in out.text


def test_existing_database_gets_the_new_column(tmp_path) -> None:
    from sqlalchemy import create_engine

    import db.session as session_mod

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE plan_items (id INTEGER PRIMARY KEY, reason TEXT)"))
    original = session_mod._engine
    session_mod._engine = engine
    try:
        session_mod._add_missing_columns()
        session_mod._add_missing_columns()  # 重跑要安全
    finally:
        session_mod._engine = original
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(plan_items)"))]
    assert "reason_source" in cols


def _count_calls(monkeypatch):
    import anthropic

    calls = []

    class Counting(_FakeAnthropic):
        def _create(self, **kw):
            calls.append(kw["messages"][0]["content"])
            return super()._create(**kw)

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: Counting(**kw))
    return calls


def _fake_tasks(n):
    return [
        {"id": i, "title": f"T{i}", "topic_name": "A", "key_points": [], "static_note": f"預存{i}"}
        for i in range(1, n + 1)
    ]


def test_reused_reasons_cost_nothing(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    calls = _count_calls(monkeypatch)
    out = client_mod.explain_many(_fake_tasks(3), user_id=901, reuse={1: "舊的", 2: "舊的二"})
    assert len(calls) == 1  # 只有第 3 題要呼叫
    assert out[1].text == "舊的" and out[1].is_generated
    assert out[3].text == "一句客製說明"


def test_failed_calls_do_not_burn_the_quota(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    import anthropic

    class Down(_FakeAnthropic):
        def _create(self, **kw):
            raise RuntimeError("429")

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: Down(**kw))
    client_mod.explain_many(_fake_tasks(4), user_id=903, quota=4)
    calls = _count_calls(monkeypatch)  # 恢復後，額度還在
    client_mod.explain_many(_fake_tasks(4), user_id=903, quota=4)
    assert len(calls) == 4


def test_daily_quota_caps_calls_and_rest_fall_back(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    calls = _count_calls(monkeypatch)
    out = client_mod.explain_many(_fake_tasks(5), user_id=902, quota=3)
    assert len(calls) == 3
    assert sum(e.is_generated for e in out.values()) == 3
    assert out[5].text == "預存5" and not out[5].is_generated
    # 同一天再排一次：額度已用完，一次都不呼叫
    calls.clear()
    out = client_mod.explain_many(_fake_tasks(5), user_id=902, quota=3)
    assert calls == [] and not any(e.is_generated for e in out.values())


def test_earlier_generated_reasons_are_loaded_per_user(db_session) -> None:
    core_plan, inputs = _inputs(db_session)
    first = inputs[0]["id"]
    explanations = {first: client_mod.Explanation("以前的一句", "llm")}
    _make(db_session, core_plan, explanations)
    assert repo.load_generated_reasons(db_session, 1) == {first: "以前的一句"}
    assert repo.load_generated_reasons(db_session, 2) == {}


def test_skipped_and_other_users_reasons_are_not_reused(db_session) -> None:
    core_plan, inputs = _inputs(db_session)
    a, b = inputs[0]["id"], inputs[1]["id"]
    plan = _make(db_session, core_plan, {
        a: client_mod.Explanation("甲", "llm"), b: client_mod.Explanation("乙", "llm")})
    item = db_session.scalars(
        select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.task_id == b)).one()
    item.status = "skipped"
    item.reason = "容量不足（原推薦理由：乙）"
    db_session.flush()
    assert repo.load_generated_reasons(db_session, 1) == {a: "甲"}
    repo.ensure_user(db_session, 2)
    assert repo.load_generated_reasons(db_session, 2) == {}


def test_explain_inputs_follow_the_given_order(db_session) -> None:
    _, inputs = _inputs(db_session)
    ids = [i["id"] for i in inputs][::-1]
    assert [i["id"] for i in repo.load_explain_inputs(db_session, ids)] == ids
