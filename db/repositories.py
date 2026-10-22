"""資料庫與核心邏輯之間的轉接層。

存在的理由是把 SQLAlchemy 關在這一層裡面。``core/`` 只認得自己的 dataclass，
``app/`` 只呼叫這裡的函式——所以 ``core/`` 可以用純字典測試，
而換資料庫時要改的只有 ``db/``。

命名一律用 ``load_*``（讀）、``save_*``／``create_*``（寫）、``record_*``（記事件），
看函式名就知道會不會寫資料。
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from core.planner import Plan as CorePlan
from core.planner import ScheduledItem, TaskInput, TopicNode
from core.replanner import Replan
from core.scoring import UserContext
from db.models import Plan, PlanItem, ProgressEvent, Task, Topic, User

#: ``parsed_goal`` JSON 裡放自評程度的鍵。
#:
#: 為什麼不存 ``plans.level``：那一欄只有一個值，對所有主題一視同仁。
#: 但 ``score = base_weight × jd × gap × goal``，所有主題乘上同一個 gap
#: 等於整體等比放大——排序與分級完全不變，gap_factor 形同失效。
#: 要讓「我已經熟 Hash Map 了」真的改變計畫，程度必須是**逐主題**的。
#: ``parsed_goal`` 本來就是 JSON，放這裡不必動 schema。
FAMILIAR_KEY = "familiar_topics"
NEW_KEY = "new_topics"
GOAL_TOPICS_KEY = "goal_topics"


# ---------------------------------------------------------------------------
# 讀：全域資料 → core 的輸入
# ---------------------------------------------------------------------------


def load_topic_nodes(session: Session) -> list[TopicNode]:
    """讀出知識地圖，轉成 ``core.planner`` 的輸入。"""
    rows = session.scalars(select(Topic).order_by(Topic.id)).all()
    return [
        TopicNode(
            id=row.id,
            name=row.name,
            base_weight=row.base_weight,
            prereq=tuple(row.prereq or ()),
        )
        for row in rows
    ]


def load_task_inputs(session: Session) -> list[TaskInput]:
    """讀出題庫，轉成 ``core.planner`` 的輸入。"""
    rows = session.scalars(select(Task).order_by(Task.id)).all()
    return [
        TaskInput(
            id=row.id,
            title=row.title,
            topic_id=row.topic_id,
            difficulty=row.difficulty,
            est_minutes=row.est_minutes,
            prereq_topic=row.prereq,
        )
        for row in rows
    ]


def load_prereq_closure(session: Session) -> dict[str, frozenset[str]]:
    """主題 id → 遞移前置主題集合。供重排時保護前置用。"""
    from core.planner import _prereq_closure

    return _prereq_closure(load_topic_nodes(session))


def load_topic_key_points(session: Session) -> dict[str, list[str]]:
    """主題 id → 重點提示。寫入 ``plan_items`` 時當快照用。"""
    rows = session.scalars(select(Topic)).all()
    return {row.id: list(row.key_points or ()) for row in rows}


def load_task_static_notes(session: Session) -> dict[int, str | None]:
    """task id → 預存說明。LLM 不可用時的備援文字。"""
    rows = session.scalars(select(Task)).all()
    return {row.id: row.static_note for row in rows}


def build_user_context(parsed_goal: dict) -> UserContext:
    """從 ``plans.parsed_goal`` 還原評分需要的使用者條件。"""
    levels: dict[str, str] = {}
    for topic_id in parsed_goal.get(FAMILIAR_KEY) or ():
        levels[topic_id] = "familiar"
    for topic_id in parsed_goal.get(NEW_KEY) or ():
        levels[topic_id] = "new"
    return UserContext(
        levels=levels,
        goal_topics=frozenset(parsed_goal.get(GOAL_TOPICS_KEY) or ()),
    )


# ---------------------------------------------------------------------------
# 寫：core 的輸出 → 資料庫
# ---------------------------------------------------------------------------


def create_plan(
    session: Session,
    *,
    user_id: int,
    goal_text: str,
    parsed_goal: dict,
    start_date: _dt.date,
    days: int,
    daily_minutes: int,
    core_plan: CorePlan,
    level: str = "normal",
    reasons: dict[int, str] | None = None,
) -> Plan:
    """把排好的計畫寫進資料庫，並把同一使用者的舊計畫收起來。

    ``reasons`` 是 task id → 「為什麼推薦」。沒給或缺某一題時，
    退回 ``tasks.static_note``，所以 LLM 掛掉也不影響這裡。
    """
    # 同時只能有一份進行中的計畫，否則「今日任務」不知道該顯示哪一份。
    for old in session.scalars(
        select(Plan).where(Plan.user_id == user_id, Plan.status == "active")
    ).all():
        old.status = "archived"

    plan = Plan(
        user_id=user_id,
        goal_text=goal_text,
        parsed_goal=parsed_goal,
        start_date=start_date,
        days=days,
        daily_minutes=daily_minutes,
        level=level,
        status="active",
    )
    session.add(plan)
    session.flush()  # 取得 plan.id

    key_points = load_topic_key_points(session)
    static_notes = load_task_static_notes(session)
    reasons = reasons or {}

    for item in core_plan.items:
        session.add(
            PlanItem(
                plan_id=plan.id,
                task_id=item.task_id,
                scheduled_date=item.scheduled_date,
                order_in_day=item.order_in_day,
                priority=item.priority,
                status="todo",
                reason=reasons.get(item.task_id) or static_notes.get(item.task_id),
                key_points=key_points.get(item.topic_id, []),
            )
        )
    session.flush()
    return plan


def load_active_plan(session: Session, user_id: int) -> Plan | None:
    """讀出目前進行中的計畫（含所有項目與題目）。"""
    return session.scalars(
        select(Plan)
        .where(Plan.user_id == user_id, Plan.status == "active")
        .order_by(Plan.created_at.desc(), Plan.id.desc())
        .options(selectinload(Plan.items).selectinload(PlanItem.task))
        .limit(1)
    ).first()


def to_scheduled_items(plan: Plan) -> list[ScheduledItem]:
    """``plan_items`` → ``core.replanner`` 的輸入。

    順序必須是 ``(scheduled_date, order_in_day)``：重排靠「相對順序不變」
    來維持前置正確，順序錯了前置就錯了。

    **已移出計畫的項目（``skipped``）必須排除。** 少了這個過濾，上一次重排
    砍掉的項目會被當成待做工作再餵回去：它們佔用容量、害真正待做的項目被砍，
    而且下一次重排還會更新它們的日期——使用者會看到某一天「有安排」但打開是空的。
    每次重排都會再累積一批，問題越滾越大。
    """
    ordered = sorted(
        (i for i in plan.items if i.status != "skipped"),
        key=lambda i: (i.scheduled_date, i.order_in_day),
    )
    return [
        ScheduledItem(
            task_id=i.task_id,
            title=i.task.title,
            topic_id=i.task.topic_id,
            scheduled_date=i.scheduled_date,
            order_in_day=i.order_in_day,
            priority=i.priority,
            est_minutes=i.task.est_minutes,
            difficulty=i.task.difficulty,
            topic_score=0.0,  # 重排不重新評分，這裡用不到
        )
        for i in ordered
    ]


def completed_task_ids(plan: Plan) -> set[int]:
    return {i.task_id for i in plan.items if i.status == "done"}


def load_completed_task_ids_for_user(session: Session, user_id: int) -> set[int]:
    """這位使用者**所有**計畫裡完成過的題目，供學習地圖用。

    不能只看目前的計畫：重排一份新計畫是常有的事，地圖的進度不該因此歸零。
    已收起來的計畫照樣算——題目做過就是做過。
    """
    rows = session.scalars(
        select(PlanItem.task_id)
        .join(Plan, PlanItem.plan_id == Plan.id)
        .where(Plan.user_id == user_id, PlanItem.status == "done")
        .distinct()
    )
    return set(rows)


# ---------------------------------------------------------------------------
# 寫：進度與重排
# ---------------------------------------------------------------------------


def mark_item_done(
    session: Session, item: PlanItem, actual_minutes: int | None = None
) -> None:
    """標記完成，並記下事件。

    ``actual_minutes`` 從第一版就開始收，即使 estimator 排在 v0.2——
    這段資料事後補不回來。
    """
    item.status = "done"
    item.done_at = _dt.datetime.now()
    if actual_minutes is not None:
        item.actual_minutes = actual_minutes
    _record(session, item.plan, "done", item_id=item.id,
            payload={"actual_minutes": actual_minutes})


def mark_item_undone(session: Session, item: PlanItem) -> None:
    """取消完成。保留 ``actual_minutes``，因為那是觀測值，不是狀態。"""
    item.status = "todo"
    item.done_at = None
    _record(session, item.plan, "undo", item_id=item.id)


def apply_replan(session: Session, plan: Plan, result: Replan) -> None:
    """把重排結果寫回資料庫。

    被砍掉的項目標成 ``skipped`` 而不是刪掉——刪掉就看不出「計畫曾經包含它」，
    也無法回答「為什麼這題不見了」。
    """
    if not result.changed:
        return

    by_task = {i.task_id: i for i in plan.items}

    for new_item in result.items:
        row = by_task.get(new_item.task_id)
        if row is not None:
            row.scheduled_date = new_item.scheduled_date
            row.order_in_day = new_item.order_in_day

    for task_id, reason in result.culled:
        row = by_task.get(task_id)
        if row is not None and row.status != "done":
            row.status = "skipped"
            # 保留原本的推薦理由，只在前面加上被移出的原因。
            # 直接覆寫會永久銷毀「為什麼推薦這一項」那句話——那是 LLM 或
            # static_note 產生的、任務卡上顯示的內容，覆寫掉就回不來了。
            row.reason = f"{reason}（原推薦理由：{row.reason}）" if row.reason else reason

    _record(
        session,
        plan,
        "replan",
        payload={
            "rescheduled": len(result.items),
            "culled": [{"task_id": tid, "reason": r} for tid, r in result.culled],
            "overflow_minutes": result.overflow_minutes,
        },
    )


def _record(
    session: Session,
    plan: Plan,
    event: str,
    item_id: int | None = None,
    payload: dict | None = None,
) -> None:
    session.add(
        ProgressEvent(
            user_id=plan.user_id,
            plan_id=plan.id,
            item_id=item_id,
            event=event,
            payload=payload or {},
        )
    )


# ---------------------------------------------------------------------------
# 讀：進度統計
# ---------------------------------------------------------------------------


def progress_summary(plan: Plan, today: _dt.date) -> dict:
    """畫面上兩條進度條需要的數字。

    「今日」只算今天該做的，「整體」算整份計畫；兩者分開是因為
    使用者最想知道的是「今天還剩幾題」，而不是「整體完成度 37%」。

    **整體進度的分母包含被移出計畫的項目。** 把 ``skipped`` 同時從分子和
    分母扣掉，會讓「放棄一半的計畫」看起來像「完成度大幅提升」——
    極端情況下砍掉 7 項、完成 3 項，進度條會從 30% 直接跳到 100%，
    還配上一句「計畫期限已到」。進度是一個學習追蹤工具唯一不能說謊的數字。
    """
    today_items = [
        i for i in plan.items if i.scheduled_date == today and i.status != "skipped"
    ]

    def ratio(done: int, total: int) -> float:
        # 今天沒有安排時回 1.0：0/0 顯示成 0% 看起來像沒做，
        # 但實際上是「今天沒事」。
        return 1.0 if total == 0 else done / total

    today_done = sum(1 for i in today_items if i.status == "done")
    overall_total = len(plan.items)
    overall_done = sum(1 for i in plan.items if i.status == "done")

    return {
        "today_total": len(today_items),
        "today_done": today_done,
        "today_ratio": ratio(today_done, len(today_items)),
        "overall_total": overall_total,
        "overall_done": overall_done,
        "overall_ratio": ratio(overall_done, overall_total),
        "today_minutes": sum(i.task.est_minutes for i in today_items),
        "skipped": sum(1 for i in plan.items if i.status == "skipped"),
    }


# ---------------------------------------------------------------------------
# 使用者資料的匯出與刪除
# ---------------------------------------------------------------------------
#
# 這一組函式是「最終目標是開放給其他人用」的最低義務：使用者要能把自己的
# 紀錄帶走，也要能把它刪乾淨。兩件事都放在 repository 層，是因為它們牽涉
# 多張表的刪除順序——那是 db/ 的知識，不該漏到 app/ 去。

#: 匯出檔的格式版本。之後欄位有增減時靠這個數字判斷怎麼讀。
EXPORT_FORMAT_VERSION = 1

#: 帳號被刪掉後重建時用的預設值。與 ``scripts/import_data.py`` 的 SEED_USER 一致。
_FALLBACK_EMAIL = "owner@localhost"
_FALLBACK_DISPLAY_NAME = "我"


def ensure_user(session: Session, user_id: int) -> User:
    """取得使用者；不存在就建一個空帳號。

    為什麼需要這支：v0.1 沒有登入，``get_current_user()`` 永遠回傳 1。
    使用者刪掉帳號之後那一列就不見了，但下次打開網頁 ``user_id`` 還是 1，
    於是 ``create_plan`` 會因為外鍵指不到人而炸掉——使用者會看到一個
    壞掉的網站，而不是一個乾淨的新開始。

    所以單人模式下「刪除帳號」的語意是**回到全新狀態**，不是「從此不能用」。
    接上登入之後這支函式就該換成「找不到人就導去登入頁」。
    """
    user = session.get(User, user_id)
    if user is not None:
        return user
    user = User(
        id=user_id,
        # email 是 NOT NULL + UNIQUE，必須給值。單人模式下沒有真的信箱可填。
        email=_FALLBACK_EMAIL if user_id == 1 else f"user{user_id}@localhost",
        display_name=_FALLBACK_DISPLAY_NAME,
    )
    session.add(user)
    session.flush()
    return user


def export_user_data(session: Session, user_id: int) -> dict:
    """把一位使用者的全部學習紀錄匯出成可直接 ``json.dumps`` 的 dict。

    包含**所有**計畫，不只進行中的那一份：已收起來（``archived``）的計畫
    也是使用者的紀錄，匯出時漏掉它們等於匯出了一份殘缺的資料。
    被移出計畫的項目（``skipped``）同理保留。

    日期一律轉成 ISO 字串，``json.dumps`` 才吃得下。

    不含 ``topics`` 與 ``tasks`` 兩張全域表——那是網站的題庫，不是使用者的
    資料。但每一項會帶上題目的標題、網址與難度快照，否則匯出檔只剩一串
    ``task_id``，離開這個資料庫就讀不懂了。

    Raises:
        LookupError: 使用者不存在。沉默地回傳空資料會讓使用者以為
            「我的紀錄真的是空的」，而實際上是查錯了人。
    """
    user = session.get(User, user_id)
    if user is None:
        raise LookupError(f"user_id={user_id} 不存在")

    plans = session.scalars(
        select(Plan)
        .where(Plan.user_id == user_id)
        .order_by(Plan.id)
        .options(selectinload(Plan.items).selectinload(PlanItem.task))
    ).all()

    events = session.scalars(
        select(ProgressEvent)
        .where(ProgressEvent.user_id == user_id)
        .order_by(ProgressEvent.id)
    ).all()

    return {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "daily_api_quota": user.daily_api_quota,
            "created_at": _iso(user.created_at),
        },
        "plans": [
            {
                "id": plan.id,
                "goal_text": plan.goal_text,
                "parsed_goal": plan.parsed_goal,
                "start_date": _iso(plan.start_date),
                "days": plan.days,
                "daily_minutes": plan.daily_minutes,
                "level": plan.level,
                "status": plan.status,
                "created_at": _iso(plan.created_at),
                "items": [
                    {
                        "id": item.id,
                        "task_id": item.task_id,
                        "task_title": item.task.title,
                        "task_url": item.task.url,
                        "topic_id": item.task.topic_id,
                        "difficulty": item.task.difficulty,
                        "est_minutes": item.task.est_minutes,
                        "scheduled_date": _iso(item.scheduled_date),
                        "order_in_day": item.order_in_day,
                        "priority": item.priority,
                        "status": item.status,
                        "done_at": _iso(item.done_at),
                        "actual_minutes": item.actual_minutes,
                        "reason": item.reason,
                        "key_points": list(item.key_points or ()),
                    }
                    for item in sorted(
                        plan.items, key=lambda i: (i.scheduled_date, i.order_in_day)
                    )
                ],
            }
            for plan in plans
        ],
        "progress_events": [
            {
                "id": event.id,
                "plan_id": event.plan_id,
                "item_id": event.item_id,
                "event": event.event,
                "created_at": _iso(event.created_at),
                "payload": event.payload,
            }
            for event in events
        ],
    }


def _iso(value: _dt.date | _dt.datetime | None) -> str | None:
    """日期轉 ISO 字串；``None`` 原樣保留。"""
    return None if value is None else value.isoformat()


def delete_user_data(
    session: Session, user_id: int, *, drop_account: bool = False
) -> dict[str, int]:
    """刪掉一位使用者的學習紀錄；``drop_account`` 時連帳號那一列一起刪。

    **刪除順序是這支函式存在的主要理由。** 現在的 schema 沒辦法只刪 ``users``
    那一列就了事：

    * ``User.plans`` 這條 relationship **沒有** cascade，而 ``plans.user_id``
      是 NOT NULL，所以 ORM 想把它設成 NULL 會失敗。
    * ``progress_events`` 的 ``plan_id`` 指向 ``plans``、``item_id`` 指向
      ``plan_items``，而它完全沒有被任何 cascade 覆蓋到。
    * SQLite 的外鍵檢查是開著的（``db/session.py`` 的 ``PRAGMA foreign_keys=ON``），
      所以順序錯了會直接撞外鍵，不會默默留下指向空氣的孤兒列。

    因此順序固定是 ``progress_events`` → ``plan_items`` → ``plans`` → ``users``，
    由裡往外。這裡刻意用 bulk delete 而不是逐列 ``session.delete()``：
    bulk delete 不觸發 ORM cascade，刪什麼完全由上面這個順序決定，
    不會因為以後有人在 model 上加了一條 cascade 就悄悄改變行為。
    代價是 ORM 的 identity map 裡還握著已經被刪掉的物件，所以結束前要
    ``expire_all()``，否則同一個 session 之後讀到的是幽靈資料。

    Returns:
        每張表實際刪掉幾列。回傳數量而不是 ``None``，是為了讓畫面能說
        「刪掉了 3 份計畫、48 個項目」——使用者才知道這個按鈕真的做了事。
    """
    plan_ids = list(session.scalars(select(Plan.id).where(Plan.user_id == user_id)))

    # 1. progress_events：兩個條件都查。user_id 與 plan_id 理論上一致，
    #    但這是刪除路徑，寧可多刪自己的資料，也不要留下撞外鍵的孤兒列。
    event_filter = ProgressEvent.user_id == user_id
    if plan_ids:
        event_filter = event_filter | ProgressEvent.plan_id.in_(plan_ids)
    deleted_events = session.execute(delete(ProgressEvent).where(event_filter)).rowcount

    # 2. plan_items
    deleted_items = (
        session.execute(delete(PlanItem).where(PlanItem.plan_id.in_(plan_ids))).rowcount
        if plan_ids
        else 0
    )

    # 3. plans
    deleted_plans = session.execute(delete(Plan).where(Plan.user_id == user_id)).rowcount

    # 4. users（只有明確要求時）
    deleted_users = (
        session.execute(delete(User).where(User.id == user_id)).rowcount
        if drop_account
        else 0
    )

    session.expire_all()
    return {
        "progress_events": deleted_events,
        "plan_items": deleted_items,
        "plans": deleted_plans,
        "users": deleted_users,
    }
