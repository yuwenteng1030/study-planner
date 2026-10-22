"""今日任務：打勾、記實際時間、看進度。

落後時會自動重排（``core/replanner.py``），所以漏做一天不會讓計畫卡死在
過去的日期——那是「能用」與「只能 demo」的差別。
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.deps import get_current_user  # noqa: E402
from core.replanner import replan  # noqa: E402
from db import repositories as repo  # noqa: E402
from db.session import session_scope  # noqa: E402

st.set_page_config(page_title="今日任務", page_icon="✅", layout="centered")

TIER_LABEL = {"must": "必學", "recommended": "建議", "bonus": "加分"}
TIER_ICON = {"must": "🔴", "recommended": "🟡", "bonus": "⚪"}


def main() -> None:
    st.title("✅ 今日任務")
    today = _dt.date.today()
    user_id = get_current_user()

    with session_scope() as session:
        plan = repo.load_active_plan(session, user_id)
        if plan is None:
            st.info("還沒有計畫。先到「Home」填條件排一份。")
            return

        end_date = plan.start_date + _dt.timedelta(days=plan.days - 1)

        # 落後就重排。沒落後時 replan 會回 changed=False，不動任何東西——
        # 每次開頁都重排會讓計畫看起來不穩定，使用者就不會信任它。
        result = replan(
            items=repo.to_scheduled_items(plan),
            completed_task_ids=repo.completed_task_ids(plan),
            today=today,
            end_date=end_date,
            daily_minutes=plan.daily_minutes,
            # 砍項目時不要砍掉保留項目的前置主題，否則會排出
            # 「先做後置」的計畫。
            prereq_closure=repo.load_prereq_closure(session),
        )
        if result.changed:
            repo.apply_replan(session, plan, result)
            session.flush()
            _report_replan(result)

        summary = repo.progress_summary(plan, today)
        today_items = sorted(
            (
                i
                for i in plan.items
                if i.scheduled_date == today and i.status != "skipped"
            ),
            key=lambda i: i.order_in_day,
        )

        # 進度條在版面上要留在最上面，但它的數字是**項目迴圈跑完之後**才正確的：
        # 打勾的寫入發生在 ``_render_item`` 裡面，而 Streamlit 在 widget 變動時
        # 只重跑一次腳本。照順序畫的話，這一輪畫出來的是點擊**之前**的狀態——
        # 卡片的刪除線立刻更新（它在寫入之後才算 ``done``），上面的「今天 n / m 題」
        # 卻停在舊數字，使用者得按一次重新整理才看得到自己剛完成的那一題。
        # ``st.container()`` 先把位置佔下來，迴圈之後再往裡面畫：版面順序不變，
        # 數字是這一輪最新的。不用 ``st.rerun()`` 的理由見 ``_render_item``。
        progress_slot = st.container()

        if not today_items:
            with progress_slot:
                _show_progress(summary, plan, end_date, today)
            _show_empty_day(plan, today, end_date)
            return

        st.subheader(f"{today:%m/%d}（{summary['today_minutes']} 分鐘）")
        for item in today_items:
            _render_item(session, item)

        with progress_slot:
            # 重新算一次，不要沿用迴圈之前的 ``summary``。
            # ``progress_summary`` 讀的是記憶體裡的 ``plan.items``，
            # 所以這裡拿到的是這一輪打勾之後的狀態。
            _show_progress(repo.progress_summary(plan, today), plan, end_date, today)

        _show_upcoming(plan, today)


def _report_replan(result) -> None:
    """重排後要講清楚發生了什麼事，不能默默改動使用者的計畫。"""
    message = f"你有進度落後，已經把未完成的項目重排到今天之後（{len(result.items)} 項）。"
    if result.culled:
        message += f"時間不夠，{len(result.culled)} 項較低優先度的項目已移出計畫。"
    st.warning(message)

    # 條件要同時看 extra_days 與 overflow_minutes。只看 extra_days 的話，
    # 有些超量情形（例如期限已過）會完全不提示，使用者看不到還差多少。
    if result.extra_days > 0 or result.overflow_minutes > 0:
        st.error(
            f"剩下的時間不夠排完「必學」項目，計畫已往後延 "
            f"{result.extra_days} 天（還差 {result.overflow_minutes} 分鐘）。\n\n"
            "必學項目不會被自動砍掉，每日時間上限也不會被偷偷超過——"
            "把 340 分鐘排進 60 分鐘的一天只是帳面好看。"
            "請自己決定：接受延期、調高每日時間，或回到 Home 重排一份較短的計畫。"
        )


def _show_progress(summary: dict, plan, end_date: _dt.date, today: _dt.date) -> None:
    col_today, col_all = st.columns(2)
    with col_today:
        st.markdown("**今天**")
        st.progress(
            summary["today_ratio"],
            text=f"{summary['today_done']} / {summary['today_total']} 題",
        )
    with col_all:
        st.markdown("**整體**")
        st.progress(
            summary["overall_ratio"],
            text=f"{summary['overall_done']} / {summary['overall_total']} 題",
        )

    days_left = (end_date - today).days
    caption = f"目標：{plan.goal_text}　|　每天 {plan.daily_minutes} 分鐘　|　"
    caption += f"剩 {days_left} 天" if days_left >= 0 else "已超過計畫期限"
    if summary["skipped"]:
        caption += f"　|　已移出計畫 {summary['skipped']} 項"
    st.caption(caption)


def _render_item(session, item) -> None:
    """一張任務卡。打勾即寫入 ``progress_events``。

    這裡刻意**不呼叫** ``st.rerun()``。

    原因：``st.rerun()`` 是用拋例外實作的（``RerunException``），而這段程式
    跑在 ``session_scope()`` 裡面——``session_scope`` 看到任何例外都會 rollback。
    結果是「標記完成」被回捲，但 checkbox 的狀態留在前端，下一輪又再標記一次、
    再 rollback、再 rerun……變成無窮迴圈，整頁卡死。
    （這個 bug 是 UI 冒煙測試抓到的：``pytest`` 全綠、伺服器回 200、
    log 也乾淨，只有真的去點那個 checkbox 才會發現。）

    代價是：這一輪的畫面順序與寫入順序不一致（widget 變動只重跑一次腳本，
    畫面上在迴圈之前的東西會早於寫入被畫出來）。所以需要「寫入之後的數字」的
    區塊，必須自己用 ``st.container()`` 把版面位置先佔下來、迴圈之後再畫——
    ``main()`` 的 ``progress_slot`` 就是為此存在的。
    """
    task = item.task
    icon = TIER_ICON.get(item.priority, "")
    tier = TIER_LABEL.get(item.priority, item.priority)

    with st.container(border=True):
        body, action = st.columns([5, 1])

        with action:
            checked = st.checkbox("完成", value=(item.status == "done"), key=f"done_{item.id}")

        # 先處理狀態變更，再決定怎麼顯示；否則這一輪會顯示變更前的舊狀態。
        if checked and item.status != "done":
            recorded = st.session_state.get(f"actual_{item.id}", task.est_minutes)
            # 用 `int(recorded) or None` 會把刻意填的 0 當成「沒填」而丟掉。
            # 這個欄位的資料事後補不回來，所以 0 也要存。
            repo.mark_item_done(
                session,
                item,
                actual_minutes=int(recorded) if recorded is not None else None,
            )
        elif not checked and item.status == "done":
            repo.mark_item_undone(session, item)

        done = item.status == "done"

        with body:
            title = f"~~{task.title}~~" if done else f"**{task.title}**"
            st.markdown(
                f"{icon} {title}　"
                f"`{tier}`　`{task.difficulty}`　`預估 {task.est_minutes} 分鐘`"
            )
            st.markdown(f"[在 HackerRank 開啟題目]({task.url})")

        # 「為什麼重要」——這是整個網站的賣點，不是裝飾。
        if item.reason:
            st.info(item.reason, icon="💡")
        if item.key_points:
            st.caption("重點：" + "、".join(item.key_points))

        # 實際分鐘數一律顯示，不是打勾之後才出現：
        # 要讓使用者先填再打勾，否則打勾的那一瞬間還沒有值可以記。
        # estimator 排在 v0.2，但這個欄位從現在就開始收——事後補不回來。
        st.number_input(
            "實際花了幾分鐘？",
            min_value=0,
            max_value=600,
            value=item.actual_minutes or task.est_minutes,
            step=5,
            key=f"actual_{item.id}",
            help="打勾時會一起記錄下來。之後用來校正預估時間。",
        )


def _show_empty_day(plan, today: _dt.date, end_date: _dt.date) -> None:
    if today > end_date:
        st.success("計畫期限已到。到「Home」排一份新的。")
        return
    upcoming = sorted(
        (i for i in plan.items if i.scheduled_date > today and i.status == "todo"),
        key=lambda i: (i.scheduled_date, i.order_in_day),
    )
    if upcoming:
        next_day = upcoming[0].scheduled_date
        st.success(f"今天沒有排任務。下一批是 {next_day:%m/%d}。")
    else:
        st.success("整份計畫都完成了。到「Home」排下一份。")


def _show_upcoming(plan, today: _dt.date) -> None:
    with st.expander("接下來幾天"):
        grouped: dict[_dt.date, list] = {}
        for i in plan.items:
            if i.scheduled_date > today and i.status != "skipped":
                grouped.setdefault(i.scheduled_date, []).append(i)
        if not grouped:
            st.write("之後沒有排定的項目了。")
            return
        for day in sorted(grouped)[:5]:
            items = sorted(grouped[day], key=lambda i: i.order_in_day)
            total = sum(i.task.est_minutes for i in items)
            st.markdown(f"**{day:%m/%d}**　{len(items)} 題 / {total} 分鐘")
            for i in items:
                st.write(f"　- {i.task.title}（{i.task.difficulty}）")


main()
