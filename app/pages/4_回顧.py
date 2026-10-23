"""回顧：每週／每月的完成率趨勢，以及最常落後的主題。

只讀不寫。數字在 ``core/review.py`` 算，資料由 ``db/repositories.load_review_data``
整理——跨所有計畫，包含已收起來的，因為回顧看的是歷史。
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.deps import get_current_user  # noqa: E402
from core.calibration import ACCURATE_RANGE, MIN_SAMPLES, TOO_FEW, calibrate  # noqa: E402
from core.review import MONTH, WEEK, completion_trend, lagging_topics, period_start  # noqa: E402
from db import repositories as repo  # noqa: E402
from db.session import session_scope  # noqa: E402

st.set_page_config(page_title="回顧", page_icon="📈", layout="centered")

#: 已完成用地圖上 Easy 的綠，未完成用中性的米灰——未完成不是一個「類別」，
#: 是「還差多少」，所以不給它搶眼的顏色。
DONE_COLOR = "#4E9A2E"
UNDONE_COLOR = "#D9D2C3"


def main() -> None:
    st.title("📈 回顧")
    st.caption(
        "完成率＝原本排在那段期間的題目裡做完了幾題。照**原本**的日子算，"
        "重排把題目挪到後面也不會讓過去的週看起來比較好；被移出計畫的題目照樣算在分母裡。"
        "只算日子已經到了的題目；同一題排進兩份計畫就算兩次。"
    )

    today = _dt.date.today()
    with session_scope() as session:
        user_id = get_current_user()
        items, moved, extra = repo.load_review_data(session, user_id)
        samples = repo.load_calibration_samples(session, user_id)
        names = {t.id: t.name for t in repo.load_topic_nodes(session)}

    if not items:
        st.info("還沒有任何計畫。先到「Home」排一份，做幾天之後這裡就有東西看了。")
        return

    _render_headline(items, extra, today)

    # 用 radio 而不是 segmented_control：後者再點一次會變成「都沒選」
    by = st.radio("期間", ["每週", "每月"], horizontal=True, key="review_period")
    rows = completion_trend(items, today, MONTH if by == "每月" else WEEK)
    _render_trend(rows)

    st.subheader("最常落後的主題")
    st.caption(
        "落後＝比原本的日子晚做完、過了日子還沒做、被移出計畫或計畫收起來時還沒做，"
        "或過期後被重排往後挪（被別題擠到後面的不算）。一題有好幾種情況只算一題；"
        "在別處做完了就不算。「被往後挪」與原本的日子從 2026-10-23 起才有紀錄。"
    )
    lags = lagging_topics(items, moved, names, today, done_elsewhere=extra)
    if lags:
        _render_lags(lags)
    else:
        st.success("目前沒有落後的主題。")

    _render_calibration(samples)


def _render_lags(lags) -> None:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "主題": lag.name,
                    "落後題數": lag.lagging,
                    "落後比例": lag.rate,
                    "晚做完": lag.late,
                    "過期未做": lag.overdue,
                    "被移出／放下": lag.cut,
                    "被往後挪": lag.moved,
                    "已排定": lag.planned,
                }
                for lag in lags
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={"落後比例": st.column_config.ProgressColumn("落後比例", format="percent", min_value=0, max_value=1)},
    )


def _render_headline(items, extra: set[int], today: _dt.date) -> None:
    week = completion_trend(
        [i for i in items if period_start(i.scheduled_date, WEEK) == period_start(today, WEEK)],
        today,
        WEEK,
    )
    this_week = week[0] if week else None
    done_tasks = len({i.task_id for i in items if i.status == "done"})

    a, b, c = st.columns(3)
    a.metric(
        "本週完成率",
        f"{this_week.ratio:.0%}" if this_week else "—",
        help="本週到今天為止排定的題目裡，做完了幾成。",
    )
    b.metric("計畫裡做完的題目", f"{done_tasks} 題", help="跨所有計畫，同一題只算一次。")
    c.metric("計畫外完成", f"{len(extra)} 題", help="在學習地圖上做完、但不在計畫裡的題目。")


def _render_trend(rows) -> None:
    if not rows:
        st.info("排定的日子還沒到，還沒有可以回顧的期間。")
        return
    # 長表：一列＝某期間的「已完成」或「未完成」題數，Altair 才能疊起來並各自給提示
    frame = pd.DataFrame(
        [
            {"期間": r.label, "狀態": status, "題數": count, "順序": order}
            for r in rows
            for status, count, order in (("已完成", r.done, 0), ("未完成", r.planned - r.done, 1))
        ]
    )
    # 不用 st.bar_chart：它預設可以用滾輪縮放，捲頁面時會把 y 軸縮成負數。
    # 柱寬跟著期數縮：只有一週時不撐滿整張圖，期數多時也不會疊在一起。
    # 不加圓角：疊起來的兩段都會被圓角，交界處會出現缺口。
    bar = max(6, min(36, int(560 / len(rows) * 0.6)))
    chart = (
        alt.Chart(frame)
        .mark_bar(size=bar)
        .encode(
            x=alt.X("期間:N", sort=[r.label for r in rows], title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("sum(題數):Q", title="題數", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color(
                "狀態:N",
                scale=alt.Scale(domain=["已完成", "未完成"], range=[DONE_COLOR, UNDONE_COLOR]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            order=alt.Order("順序:Q"),
            tooltip=["期間:N", "狀態:N", "題數:Q"],
        )
    )
    st.altair_chart(chart, width="stretch")

    with st.expander("表格"):
        st.dataframe(
            pd.DataFrame(
                {
                    "期間": [r.label for r in rows],
                    "排定": [r.planned for r in rows],
                    "完成": [r.done for r in rows],
                    "完成率": [r.ratio for r in rows],
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={"完成率": st.column_config.ProgressColumn("完成率", format="percent", min_value=0, max_value=1)},
        )


def _render_calibration(samples) -> None:
    st.subheader("預估時間準不準")
    result = calibrate(samples)
    low, high = ACCURATE_RANGE
    st.caption(
        f"比的是打勾時填的「實際花了幾分鐘」÷ 題庫的預估。用中位數，每個難度至少 {MIN_SAMPLES} 筆才下結論；"
        f"比例在 {low:g}–{high:g} 倍之間算準。**沒改過預估值就打勾的不算**（分不出是真的剛好準時，"
        "還是沒填），所以真的準時的題也會被排除，這裡看到的「多準」偏保守。"
        "同一題做過好幾次只算第一次。只看不改：預估值不會因此自動調整。"
    )
    usable_n = result.groups[-1].n
    if usable_n == 0:
        st.info(
            f"還沒有能用的紀錄（{result.excluded} 筆打勾紀錄都不能用：沒改過預估值、填 0、或同一題重做）。"
            "做完一題時，把「實際花了幾分鐘」改成真的花的時間再打勾。"
        )
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "難度": g.label,
                    "筆數": g.n,
                    "實際÷預估": None if g.median_ratio is None else round(g.median_ratio, 2),
                    "差幾分鐘": None if g.median_diff is None else g.median_diff,
                    "結論": g.verdict if g.verdict != TOO_FEW else f"{TOO_FEW}（要 {MIN_SAMPLES} 筆）",
                }
                for g in result.groups
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "實際÷預估": st.column_config.NumberColumn(format="%.2f 倍"),
            "差幾分鐘": st.column_config.NumberColumn(format="%+g 分", help="中位數。正的＝比預估久。"),
        },
    )
    if result.excluded:
        st.caption(f"另有 {result.excluded} 筆打勾紀錄沒算進來（沒改過預估值、填 0、或同一題重做）。")


main()
