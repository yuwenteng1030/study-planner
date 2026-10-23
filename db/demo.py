"""公開展示版（``STUDY_PLANNER_DEMO=1``）的訪客帳號。

部署成公開網址之後，單人模式的 ``user_id=1`` 會變成「所有人共用一份資料」：
甲排的計畫乙看得到，乙按「刪除帳號」會把甲的進度一起刪掉。所以展示版裡
**每個瀏覽器分頁各自一個訪客帳號**，而且一打開就已經有一份進行到第 4 天的計畫
（做完幾題、昨天漏了一題），面試官不用先填表就看得到地圖、重排與回顧頁。

訪客帳號放在同一個資料庫、用 ``user_id`` 隔開——這正是「所有資料表從第一版就帶
user_id」的用途。舊的訪客帳號在有人新進來時順手清掉，資料庫不會無限長大。
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import clock
from core.goals import GOAL_PRESETS
from core.planner import build_plan
from core.scoring import JDStats, UserContext
from db import repositories as repo
from db.models import PlanItem, User

#: 訪客帳號的信箱網域。``.invalid`` 是 RFC 2606 保留的網域，保證不會寄到真的信箱。
VISITOR_DOMAIN = "demo.invalid"

#: 訪客帳號留多久。比一場面試加上回頭再看一次長，又不至於讓資料庫越長越大。
VISITOR_TTL = _dt.timedelta(hours=12)

#: 範例計畫：開始於幾天前、共幾天、每天幾分鐘。
#: 14 天 × 90 分鐘排得下全部 30 題重點題組、還有餘裕：漏掉的那一題挪到今天之後
#: 不會超出期限。10 天 × 60 分鐘會排滿，訪客第一眼看到的是紅色的「時間不夠」。
SAMPLE_DAYS_AGO = 3
SAMPLE_DAYS = 14
SAMPLE_DAILY_MINUTES = 90


def is_visitor(user: User) -> bool:
    return user.email.endswith("@" + VISITOR_DOMAIN)


def create_visitor(session: Session, today: _dt.date) -> int:
    """建一個訪客帳號並放好範例資料，回傳 user_id。"""
    user = User(email=f"visitor-{uuid.uuid4().hex[:12]}@{VISITOR_DOMAIN}", display_name="訪客")
    session.add(user)
    session.flush()
    seed_sample_progress(session, user.id, today)
    return user.id


def seed_sample_progress(session: Session, user_id: int, today: _dt.date) -> None:
    """排一份從三天前開始的重點題組計畫，前幾天做完、只留昨天最後一題沒做。

    留一題沒做是刻意的：使用者一打開「今日任務」，每日重排就會把它挪到今天，
    展示版第一眼就看得到「落後會自動重排」這件事。完成時間記在當天中午、
    實際分鐘數在預估的 ±30% 內變動，回顧頁的趨勢圖與估時校正才有東西可畫。
    """
    focus = repo.load_focus_stages(session)
    tasks = [t for t in repo.load_task_inputs(session) if t.id in focus]
    if not tasks:
        return
    goal = GOAL_PRESETS[0]
    start = today - _dt.timedelta(days=SAMPLE_DAYS_AGO)
    core_plan = build_plan(
        topics=repo.load_topic_nodes(session),
        tasks=tasks,
        start_date=start,
        days=SAMPLE_DAYS,
        daily_minutes=SAMPLE_DAILY_MINUTES,
        user=UserContext(levels={}, goal_topics=frozenset(goal.topics)),
        jd=JDStats(),
    )
    if not core_plan.items:
        return
    plan = repo.create_plan(
        session,
        user_id=user_id,
        goal_text=goal.label,
        parsed_goal={
            repo.GOAL_TOPICS_KEY: list(goal.topics),
            repo.FAMILIAR_KEY: [],
            repo.NEW_KEY: [],
            "preset_id": goal.id,
        },
        start_date=start,
        days=SAMPLE_DAYS,
        daily_minutes=SAMPLE_DAILY_MINUTES,
        core_plan=core_plan,
    )

    est = {t.id: t.est_minutes for t in tasks}
    # 用查詢而不是 plan.items：create_plan 是用 plan_id 加項目的，剛建的 plan 物件上那個集合是空的
    past = session.scalars(
        select(PlanItem)
        .where(PlanItem.plan_id == plan.id, PlanItem.scheduled_date < today)
        .order_by(PlanItem.scheduled_date, PlanItem.order_in_day)
    ).all()
    # 昨天的最後一題留著不做
    for n, item in enumerate(past[:-1]):
        # 固定的變動序列（不用亂數）：每個訪客看到的範例一樣，測試也才穩定
        factor = (1.3, 0.8, 1.1, 1.25, 0.9)[n % 5]
        repo.mark_item_done(session, item, actual_minutes=max(1, round(est[item.task_id] * factor)))
        noon = _dt.datetime.combine(item.scheduled_date, _dt.time(12, 0))
        item.done_at = noon - _dt.timedelta(hours=8)  # 台北中午換成 UTC
    session.flush()


def purge_stale_visitors(session: Session, now: _dt.datetime | None = None) -> int:
    """刪掉超過 ``VISITOR_TTL`` 的訪客帳號（連同所有紀錄），回傳刪了幾個。"""
    cutoff = (now or clock.utc_now()) - VISITOR_TTL
    stale = session.scalars(
        select(User.id).where(User.email.like(f"%@{VISITOR_DOMAIN}"), User.created_at < cutoff)
    ).all()
    for user_id in stale:
        repo.delete_user_data(session, user_id, drop_account=True)
    return len(stale)
