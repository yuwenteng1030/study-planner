"""首頁：填條件、產生計畫。

用 Streamlit 而不是 React＋FastAPI，理由寫在 README 設計決策表：
v0.1 的目標是「十天內做出能用的東西」，而排程邏輯已經全在 ``core/``，
前端換成什麼都不影響它。

``streamlit run app/Home.py`` 啟動。
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import streamlit as st

# Streamlit 會把 sys.path[0] 設成這個檔案所在的資料夾（app/），
# 所以 import core / db 之前要先把專案根目錄加進來。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.deps import get_current_user  # noqa: E402
from core.goals import GOAL_PRESETS  # noqa: E402
from core.planner import build_plan  # noqa: E402
from core.scoring import JDStats, UserContext  # noqa: E402
from db import repositories as repo  # noqa: E402
from db.session import session_scope  # noqa: E402

st.set_page_config(page_title="學習計畫", page_icon="🗺️", layout="centered")

DEFAULT_DAYS = 10
DEFAULT_HOURS = 1.0


def main() -> None:
    st.title("🗺️ 學習計畫")
    st.caption(
        "填三個條件，排出每天該做哪幾題，並說明每一項為什麼值得做。"
        "排序規則是公開的，不是黑箱。"
    )

    with session_scope() as session:
        topics = repo.load_topic_nodes(session)
        tasks = repo.load_task_inputs(session)
        done_ids = repo.load_completed_task_ids_for_user(session, get_current_user())
        topic_names = {t.id: t.name for t in topics}

    if not tasks:
        st.error(
            "題庫是空的。請先執行 `python -m scripts.import_data` 匯入"
            "`data/topics.yaml` 與 `data/tasks.csv`。"
        )
        return

    topics_with_tasks = sorted(
        {t.topic_id for t in tasks}, key=lambda tid: topic_names.get(tid, tid)
    )

    with st.form("plan_form"):
        st.subheader("1. 你的目標")
        goal = st.selectbox(
            "想準備哪種題型？",
            options=GOAL_PRESETS,
            format_func=lambda preset: preset.label,
        )
        st.caption(goal.description)

        st.subheader("2. 時間")
        col_left, col_right = st.columns(2)
        with col_left:
            days = st.number_input(
                "希望幾天內完成", min_value=1, max_value=120, value=DEFAULT_DAYS
            )
        with col_right:
            hours = st.number_input(
                "每天可投入幾小時",
                min_value=0.25,
                max_value=12.0,
                value=DEFAULT_HOURS,
                step=0.25,
            )
        start_date = st.date_input("從哪一天開始", value=_dt.date.today())

        st.subheader("3. 你目前的程度")
        st.caption(
            "選過的主題會調整權重：已經熟的往後排但不會消失（仍需複習），"
            "完全沒學過的往前排。兩邊都不選也可以。"
        )
        familiar = st.multiselect(
            "這些我已經熟了",
            options=topics_with_tasks,
            format_func=lambda tid: topic_names.get(tid, tid),
        )
        brand_new = st.multiselect(
            "這些我完全沒學過",
            options=topics_with_tasks,
            format_func=lambda tid: topic_names.get(tid, tid),
        )

        submitted = st.form_submit_button("排出計畫", type="primary")

    if not submitted:
        _show_existing_plan_hint()
        return

    overlap = set(familiar) & set(brand_new)
    if overlap:
        st.error(
            "同一個主題不能同時是「已經熟」和「完全沒學過」："
            + "、".join(topic_names.get(t, t) for t in sorted(overlap))
        )
        return

    daily_minutes = int(round(hours * 60))
    levels = {tid: "familiar" for tid in familiar}
    levels.update({tid: "new" for tid in brand_new})

    # 做過的題目（任何一份計畫裡打過勾，或在學習地圖上完成的）不再排進新計畫
    remaining = [t for t in tasks if t.id not in done_ids]
    if not remaining:
        st.success("🏆 題庫裡每一題都做過了，沒有新的題目可以排。")
        return

    core_plan = build_plan(
        topics=topics,
        tasks=remaining,
        start_date=start_date,
        days=int(days),
        daily_minutes=daily_minutes,
        user=UserContext(levels=levels, goal_topics=frozenset(goal.topics)),
        jd=JDStats(),  # v0.1 沒有 JD，冷啟動就是主路徑
    )

    if not core_plan.items:
        st.error(
            f"每天 {daily_minutes} 分鐘排不進任何一題——"
            "題庫裡最短的題目也需要更多時間。請調高每日時間。"
        )
        return

    parsed_goal = {
        repo.GOAL_TOPICS_KEY: list(goal.topics),
        repo.FAMILIAR_KEY: list(familiar),
        repo.NEW_KEY: list(brand_new),
        "preset_id": goal.id,
    }

    with session_scope() as session:
        repo.create_plan(
            session,
            user_id=get_current_user(),
            goal_text=goal.label,
            parsed_goal=parsed_goal,
            start_date=start_date,
            days=int(days),
            daily_minutes=daily_minutes,
            core_plan=core_plan,
        )

    _show_result(core_plan, topic_names, int(days), daily_minutes)


def _show_result(core_plan, topic_names: dict[str, str], days: int, daily: int) -> None:
    st.success(
        f"排好了：{len(core_plan.items)} 題、共 {core_plan.total_minutes()} 分鐘。"
        "到左邊的「今日任務」開始做。"
    )

    capacity = days * daily
    st.metric(
        "時間利用率",
        f"{core_plan.total_minutes() / capacity:.0%}",
        help=f"排進計畫的總時數 ÷ 可用時數（{days} 天 × {daily} 分鐘）",
    )

    # 說明哪些題目沒被選上。使用者會問「為什麼沒有某一題」，
    # 答得出來才叫推薦系統，答不出來只是隨機清單。
    if core_plan.dropped:
        with st.expander(f"有 {len(core_plan.dropped)} 題這次沒排進去（點開看原因）"):
            reasons: dict[str, int] = {}
            for _task_id, reason in core_plan.dropped:
                reasons[reason] = reasons.get(reason, 0) + 1
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
                st.write(f"- {reason}：{count} 題")
            st.caption(
                "時間不夠時，分數低的主題會先被放棄。想多做幾題就調高每日時間"
                "或拉長天數。"
            )

    st.subheader("主題優先度")
    tiers = {"must": "必學", "recommended": "建議", "bonus": "加分"}
    scheduled_topics = {i.topic_id for i in core_plan.items}
    rows = sorted(
        core_plan.scored_topics.values(), key=lambda s: (-s.score, s.topic_id)
    )
    st.dataframe(
        [
            {
                "主題": topic_names.get(s.topic_id, s.topic_id),
                "優先度": tiers.get(s.priority, s.priority),
                "分數": round(s.score, 2),
                "排進計畫": "是" if s.topic_id in scheduled_topics else "—",
                "為什麼": s.explain(),
            }
            for s in rows
        ],
        hide_index=True,
        width="stretch",
    )

    st.subheader("前三天預覽")
    for day, items in list(core_plan.by_date().items())[:3]:
        total = sum(i.est_minutes for i in items)
        st.markdown(f"**{day:%m/%d}**　{len(items)} 題 / {total} 分鐘")
        for i in items:
            st.write(
                f"　{i.order_in_day + 1}. {i.title}"
                f"（{i.difficulty}、{i.est_minutes} 分鐘）"
            )


def _show_existing_plan_hint() -> None:
    with session_scope() as session:
        plan = repo.load_active_plan(session, get_current_user())
        if plan is None:
            return
        summary = repo.progress_summary(plan, _dt.date.today())
        st.info(
            f"你已經有一份進行中的計畫（{plan.start_date:%m/%d} 起、"
            f"{plan.days} 天），完成 {summary['overall_done']}/"
            f"{summary['overall_total']} 題。"
            "到左邊的「今日任務」繼續，或在上面重新排一份（舊的會被收起來）。"
        )


if __name__ == "__main__":
    main()
else:
    main()
