"""學習地圖：知識地圖按區域攤開，每一關顯示打了幾題。

這一版刻意只讀不寫：沒有星等、沒有解鎖、不新增資料表。區域與進度都是
從既有的 ``topics.prereq`` 與 ``plan_items.status`` 算出來的（``core/game_map.py``）。
破關條件是參數，等看過這張圖再定。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.deps import get_current_user  # noqa: E402
from core.game_map import Level, build_map, difficulty_order  # noqa: E402
from db import repositories as repo  # noqa: E402
from db.session import session_scope  # noqa: E402

st.set_page_config(page_title="學習地圖", page_icon="🗺️", layout="centered")

ZONE_NAME = {0: "起點", 1: "第一區", 2: "第二區", 3: "第三區"}
DIFFICULTY_SHORT = {"Easy": "E", "Medium": "M", "Hard": "H"}


def main() -> None:
    st.title("🗺️ 學習地圖")
    st.caption(
        "每一關是一個主題，區域照前置關係排：排計畫時，前置主題的題目一定排在"
        "後面關卡之前。進度算的是你所有計畫裡打過勾的題目。"
    )

    with session_scope() as session:
        topics = repo.load_topic_nodes(session)
        tasks = repo.load_task_inputs(session)
        done_ids = repo.load_completed_task_ids_for_user(session, get_current_user())

    if not topics:
        st.info("還沒有知識地圖。先執行 `python -m scripts.import_data` 匯入題庫。")
        return

    levels = build_map(topics, tasks, done_ids)
    names = {t.id: t.name for t in topics}

    total = sum(lv.total_count for lv in levels)
    done = sum(lv.done_count for lv in levels)
    st.progress(done / total if total else 0.0, text=f"全部 {done} / {total} 題")

    for zone in sorted({lv.zone for lv in levels}):
        st.subheader(ZONE_NAME.get(zone, f"第 {zone} 區"))
        in_zone = [lv for lv in levels if lv.zone == zone]
        columns = st.columns(2)
        for i, level in enumerate(in_zone):
            with columns[i % 2]:
                _render_level(level, names)


def _render_level(level: Level, names: dict[str, str]) -> None:
    with st.container(border=True):
        st.markdown(f"**{level.name}**")

        if level.is_concept_only:
            st.caption("觀念關：沒有題目，重點寫在後面關卡的任務卡上。")
        else:
            parts = [
                f"{DIFFICULTY_SHORT.get(d, d)} {level.done.get(d, 0)}/{n}"
                for d, n in sorted(level.total.items(), key=lambda kv: difficulty_order(kv[0]))
            ]
            st.progress(
                level.done_count / level.total_count,
                text=" · ".join(parts),
            )

        if level.prereq:
            st.caption("前置：" + "、".join(names.get(p, p) for p in level.prereq))


main()
