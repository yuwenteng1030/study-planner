"""學習地圖：一關＝一題。點關卡就到 HackerRank 做題，回來按「我 submit 過了」就破關。

關卡順序與區域從 ``topics.prereq`` 算（``core/game_map.py``），插畫在
``core/map_art.py``，完成紀錄寫在 ``db/repositories.py``：題目在進行中的
計畫裡就勾計畫項目，不在就寫一筆計畫外完成。

為什麼是「我 submit 過了」而不是自動同步：HackerRank 沒有公開 API，
能讀解題紀錄的網址在 ``robots.txt`` 裡是 ``Disallow: /rest/``。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.deps import get_current_user  # noqa: E402
from core.encouragement import Cheer, template_message, toast_text  # noqa: E402
from core.game_map import (  # noqa: E402
    FOCUS_ZONE_NAMES,
    Stage,
    bear_stage,
    build_map,
    build_stages,
    experience,
    focus_route,
    parse_map_action,
)
from core.map_art import render_svg, safe_url, zone_theme  # noqa: E402
from db import repositories as repo  # noqa: E402
from db.quota import DbLedger  # noqa: E402
from db.session import session_scope  # noqa: E402
from llm.client import encourage, use_ledger  # noqa: E402

# 額度存資料庫（重開伺服器不歸零）；llm.client 本身不碰資料庫，所以在這裡接上
use_ledger(DbLedger())

st.set_page_config(page_title="學習地圖", page_icon="🗺️", layout="centered")

MAP_KEY = "level_map"
FOCUS_KEY = "map_focus"  # 小卡開在哪一關（task_id）；"next" 表示破關後跳到下一關
CLEARED_KEY = "map_cleared"  # 剛破關的題名，給小卡說一聲「過關」
ERROR_KEY = "map_error"
CHEER_KEY = "map_cheer"  # 破關的鼓勵訊息，下一輪用 toast 顯示一次

# 地圖用 st.components.v2 直接放進頁面：st.html 的 DOMPurify 會把整段 SVG 濾掉，
# st.image 又點不到。v2 元件不經過消毒，所以 SVG 裡的文字一律在 core/map_art.py
# escape、連結只放行 HackerRank；小卡的文字在下面的 JS 一律用 textContent。
_MAP_CSS = """
.map-root { position: relative; }
.card {
  position: absolute; z-index: 2; width: 240px; box-sizing: border-box;
  background: #FFFDF7; color: #4A3728; border: 2px solid #E8A33A; border-radius: 14px;
  padding: 10px 12px; box-shadow: 0 6px 18px rgba(0,0,0,.18);
  font: 13px/1.45 system-ui, sans-serif;
}
.card .cheer { font-weight: 700; color: #B86B00; margin-bottom: 4px; }
.card .title { font-weight: 700; font-size: 14px; margin-right: 18px; }
.card .meta { opacity: .75; margin: 2px 0 8px; }
.card .row { display: flex; gap: 6px; flex-wrap: wrap; }
.card a.go, .card button {
  font: inherit; border-radius: 9px; padding: 5px 10px; cursor: pointer; border: 0;
  text-decoration: none; display: inline-block;
}
.card a.go { background: #4E9A2E; color: #fff; }
.card button.done { background: #E8A33A; color: #fff; }
.card button.undo { background: #EDE4D3; color: #4A3728; }
.card button.close {
  position: absolute; top: 4px; right: 6px; background: none; padding: 2px 6px; opacity: .6;
}
"""

_MAP_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let root = parentElement.querySelector('.map-root');
  if (!root) {
    root = document.createElement('div');
    root.className = 'map-root';
    parentElement.appendChild(root);
  }
  // data.svg 由 core/map_art.py 產生，文字已 escape、連結只放行 HackerRank
  root.innerHTML = data.svg;

  const send = (type, task) => setTriggerValue('action', { type, task });

  root.querySelectorAll('.stage').forEach((node) => {
    const task = Number(node.getAttribute('data-task'));
    // 不擋預設動作：<a target=_blank> 照常開題目，這裡只是順便開小卡
    node.addEventListener('click', () => send('pick', task));
  });

  const focus = data.focus;
  if (focus) {
    const node = root.querySelector(`.stage[data-task="${focus.task}"]`);
    if (node) {
      const card = document.createElement('div');
      card.className = 'card';
      const add = (tag, cls, text) => {
        const el = document.createElement(tag);
        if (cls) el.className = cls;
        if (text) el.textContent = text;
        card.appendChild(el);
        return el;
      };
      if (focus.cleared) add('div', 'cheer', `🎉 過關：${focus.cleared}　下一關 ↓`);
      add('div', 'title', focus.title);
      add('div', 'meta', `${focus.topic} · ${focus.difficulty}` + (focus.done ? ' · 已完成 ✓' : ''));
      const row = add('div', 'row');
      if (!focus.done) {
        if (focus.url) {
          const go = document.createElement('a');
          go.className = 'go';
          go.href = focus.url;
          go.target = '_blank';
          go.rel = 'noopener noreferrer';
          go.textContent = '開始做題 ↗';
          row.appendChild(go);
        }
        const done = document.createElement('button');
        done.className = 'done';
        done.textContent = '我 submit 過了';
        done.onclick = () => send('done', focus.task);
        row.appendChild(done);
      } else {
        const undo = document.createElement('button');
        undo.className = 'undo';
        undo.textContent = '取消完成';
        undo.onclick = () => send('undo', focus.task);
        row.appendChild(undo);
      }
      const close = add('button', 'close', '✕');
      close.setAttribute('aria-label', '關閉');
      close.onclick = () => send('close', focus.task);

      root.appendChild(card);
      const box = root.getBoundingClientRect();
      const r = node.getBoundingClientRect();
      const cx = r.left - box.left + r.width / 2;
      const left = Math.max(4, Math.min(cx - card.offsetWidth / 2, box.width - card.offsetWidth - 4));
      let top = r.bottom - box.top + 8;
      if (top + card.offsetHeight > box.height) top = r.top - box.top - card.offsetHeight - 8;
      card.style.left = `${left}px`;
      card.style.top = `${top}px`;
    }
  }

  // 小熊換了位置（第一次打開、或剛破關）才捲過去；點別的關卡不要把畫面拉走
  // 稍等再捲、而且不用 smooth：元件剛掛上時頁面其他元素還在排版，
  // 平滑捲動會被接著來的版面變動打斷，結果停在頁首（手機寬度上實測過）
  if (parentElement.__lastHere !== data.here) {
    parentElement.__lastHere = data.here;
    setTimeout(() => {
      const target = root.querySelector(`.stage[data-task="${data.here}"]`);
      if (target) target.scrollIntoView({ block: 'center' });
    }, 250);
  }
}
"""

_level_map = st.components.v2.component("level_map", html="", css=_MAP_CSS, js=_MAP_JS)


# ---------------------------------------------------------------------------
# 寫：先寫資料庫再畫圖
# ---------------------------------------------------------------------------
#
# 兩個入口（地圖上的小卡、上方的「下一關」面板）都走 callback：callback 在腳本
# 重跑**之前**執行，所以這一輪畫出來的地圖已經是寫入之後的樣子。
# 不用 st.rerun()：它靠拋例外實作，session_scope() 看到例外會 rollback
# （同樣的陷阱寫在 1_今日任務.py）。


def _mark_done(task_id: int, title: str) -> None:
    user_id = get_current_user()
    cheer = None
    try:
        with session_scope() as session:
            where = repo.mark_task_done_for_user(session, user_id, task_id)
            if where != "already":
                try:
                    cheer, quota = _cheer_inputs(session, user_id, task_id, title, where)
                except Exception:
                    # 鼓勵是附加的：算它出錯不能讓這個交易 rollback、把破關一起收回
                    cheer = None
    except LookupError:
        st.session_state[ERROR_KEY] = "找不到這一題，可能題庫剛更新過。重新整理再試一次。"
        return
    st.session_state[FOCUS_KEY] = "next"
    st.session_state[CLEARED_KEY] = title
    if cheer is not None:
        # 交易已經 commit 才問 Claude：等網路時不能抓著 SQLite 的寫入鎖
        message = encourage(cheer, template_message(cheer), user_id=user_id, quota=quota)
        st.session_state[CHEER_KEY] = toast_text(message.text) + ("　✨ Claude" if message.is_generated else "")


def _cheer_inputs(session, user_id: int, task_id: int, title: str, where: str) -> tuple[Cheer, int]:
    """破關的鼓勵訊息要的資料。題目在今天的計畫裡就帶上今天的進度。"""
    # 從資料庫查，不靠 session_state：callback 可能比 main() 先跑（例如伺服器剛重開）
    _, topic_name, difficulty, est = repo.load_task_brief(session, task_id) or (title, "", "", None)
    today_done = today_total = None
    if where == "plan":
        session.flush()
        plan = repo.load_active_plan(session, user_id)
        if plan is not None:
            summary = repo.progress_summary(plan, repo.user_today(session, user_id))
            today_done, today_total = summary["today_done"], summary["today_total"]
    cheer = Cheer(
        title=title, topic_name=topic_name, difficulty=difficulty, est_minutes=est,
        today_done=today_done, today_total=today_total,
    )
    return cheer, repo.ensure_user(session, user_id).daily_api_quota


def _mark_undone(task_id: int) -> None:
    with session_scope() as session:
        repo.mark_task_undone_for_user(session, get_current_user(), task_id)
    st.session_state[FOCUS_KEY] = task_id
    st.session_state.pop(CLEARED_KEY, None)


def _on_map_action() -> None:
    """地圖送來的事件。內容來自瀏覽器，型別與 id 都要檢查，不能直接信。"""
    titles = st.session_state.get("_map_titles", {})
    parsed = parse_map_action((st.session_state.get(MAP_KEY) or {}).get("action"), titles)
    if parsed is None:
        return
    kind, task = parsed
    if kind == "pick":
        st.session_state[FOCUS_KEY] = task
        st.session_state.pop(CLEARED_KEY, None)
    elif kind == "close":
        st.session_state[FOCUS_KEY] = None
        st.session_state.pop(CLEARED_KEY, None)
    elif kind == "done":
        _mark_done(task, titles.get(task, ""))
    elif kind == "undo":
        _mark_undone(task)


# ---------------------------------------------------------------------------
# 畫
# ---------------------------------------------------------------------------


def main() -> None:
    st.title("🗺️ 學習地圖")
    st.caption(
        "一關＝一題，從最下面的起點往上走。點關卡會在新分頁打開 HackerRank 的題目；"
        "submit 之後回來按「我 submit 過了」，關卡變金色，小熊走到下一關。"
        "後面的關卡沒有鎖，想跳著做也可以。"
    )

    with session_scope() as session:
        topics = repo.load_topic_nodes(session)
        tasks = repo.load_task_inputs(session)
        urls = repo.load_task_urls(session)
        done_ids = repo.load_map_done_task_ids(session, get_current_user())
        today = repo.user_today(session, get_current_user())
        plan = repo.load_active_plan(session, get_current_user())
        # 先檢查要不要重排，地圖上的「今天」才會跟今日任務頁一致
        replanned = repo.ensure_replanned(session, plan, today) if plan is not None else None
        marks = repo.plan_marks(plan, today)
        focus_stages = repo.load_focus_stages(session)

    if not tasks:
        st.info("還沒有題庫。先執行 `python -m scripts.import_data` 匯入題庫。")
        return
    if replanned is not None:
        # 重排一天只跑一次，今日任務頁之後拿到的是 None；把結果留給它顯示完整說明
        st.session_state["pending_replan_report"] = replanned
        st.info("你有進度落後，已經把未完成的題目重排到今天之後。細節在「今日任務」頁。", icon="🔁")

    levels = build_map(topics, tasks, done_ids)
    all_stages = build_stages(levels, tasks, done_ids)
    # 「在地圖上看」連到不在重點路線上的題目時，先切到整個題庫，否則那一關根本沒畫出來
    linked_early = st.query_params.get("task")
    if (
        linked_early is not None
        and linked_early.isdigit()
        and int(linked_early) not in focus_stages
        and any(s.task_id == int(linked_early) for s in all_stages)
    ):
        st.session_state["map_focus_only"] = False
    focus_only = bool(focus_stages) and st.toggle(
        f"只看重點路線（{len(focus_stages)} 關）",
        value=True,
        key="map_focus_only",
        help="依上機考型態挑的重點題組，分暖身 → Medium 主力 → 進階三段。關掉就顯示整個題庫。",
    )
    stages = focus_route(all_stages, focus_stages) if focus_only else all_stages
    if not stages:
        st.info("題庫裡的題目都對不上知識地圖的主題，畫不出關卡。重新匯入題庫試試。")
        return
    by_id = {s.task_id: s for s in stages}
    st.session_state["_map_titles"] = {s.task_id: s.title for s in stages}
    here = bear_stage(stages, marks)
    here_is_today = here is not None and here.task_id in marks.today

    # 從「今日任務」的「在地圖上看」過來：?task=<id> 打開那一關的小卡（只用一次）
    linked = st.query_params.get("task")
    if linked is not None:
        del st.query_params["task"]
        if linked.isdigit() and int(linked) in by_id:
            st.session_state[FOCUS_KEY] = int(linked)
            st.session_state.pop(CLEARED_KEY, None)

    if cheer := st.session_state.pop(CHEER_KEY, None):
        st.toast(cheer, icon="🎉")
    if error := st.session_state.pop(ERROR_KEY, None):
        st.error(error)

    done = sum(s.done for s in stages)
    _render_hud(all_stages, done, len(stages), focus_only)

    focus_id = st.session_state.get(FOCUS_KEY)
    if focus_id == "next":
        focus_id = here.task_id if here is not None else None
        st.session_state[FOCUS_KEY] = focus_id
    focus = by_id.get(focus_id) if isinstance(focus_id, int) else None

    _render_next_panel(here, urls, all_done=done == len(stages), today=here_is_today)
    if marks.today or marks.overdue:
        # 用整個題庫算：重點路線只畫一部分，用畫出來的關卡算會少數今天的任務
        all_by_id = {s.task_id: s for s in all_stages}
        today_left = sum(1 for t in marks.today if t in all_by_id and not all_by_id[t].done)
        hidden = sum(1 for t in marks.today if t not in by_id)
        st.caption(
            f"🔵 藍圈＝今天的任務（還剩 {today_left} / {len(marks.today)} 題）"
            + (f"，其中 {hidden} 題不在重點路線上（關掉上面的開關就看得到）" if hidden else "")
            + (f"　🔴 紅圈＝過期還沒做（{len(marks.overdue)} 題，到「今日任務」按重排）" if marks.overdue else "")
        )

    _level_map(
        key=MAP_KEY,
        data={
            "svg": render_svg(
                stages, urls, here.task_id if here else None,
                today_ids=frozenset(marks.today), overdue_ids=marks.overdue,
                # 整個題庫時才標星：重點路線裡每一關都是重點
                focus_ids=frozenset() if focus_only else frozenset(focus_stages),
                zone_names=FOCUS_ZONE_NAMES if focus_only else None,
                by_topic=not focus_only,
            ),
            "here": here.task_id if here else None,
            "focus": _focus_payload(focus, urls) if focus else None,
        },
        on_action_change=_on_map_action,
    )

    # 插畫在窄螢幕上字會很小，另外列一份文字版
    with st.expander("每一關的題目（文字版）"):
        st.caption(
            "⭐ 一星：Easy 做 3 題（沒有 Easy 就 Medium 1 題）"
            "　⭐⭐ 二星：Medium 做一半　⭐⭐⭐ 三星：Hard 全做（沒有 Hard 就 Medium 全做）。"
            "星等只是進度，不會鎖住後面的關卡。"
        )
        if focus_only:
            for zone in sorted({s.zone for s in stages}):
                members = [s for s in stages if s.zone == zone]
                st.subheader(f"{FOCUS_ZONE_NAMES.get(zone, zone_theme(zone)[0])}　{sum(s.done for s in members)}/{len(members)}")
                st.markdown("\n".join(
                    f"- {'✅' if s.done else '⬜'} [{_md(s.title)}]({safe_url(urls.get(s.task_id)) or '#'})"
                    f"（{_md(s.topic_name)}・{s.difficulty}）"
                    for s in members
                ))
            return
        for zone in sorted({s.zone for s in stages}):
            st.subheader(zone_theme(zone)[0])
            for level in (lv for lv in levels if lv.zone == zone and not lv.is_concept_only):
                members = [s for s in stages if s.topic_id == level.topic_id]
                lines = [
                    f"- {'✅' if s.done else '⬜'} [{_md(s.title)}]({safe_url(urls.get(s.task_id)) or '#'})"
                    f"（{s.difficulty}）"
                    for s in members
                ]
                stars = "⭐" * level.stars + "☆" * (3 - level.stars)
                st.markdown(
                    f"**{_md(level.name)}**　{stars}　{sum(s.done for s in members)}/{len(members)}"
                )
                st.markdown("\n".join(lines))
            for level in (lv for lv in levels if lv.zone == zone and lv.is_concept_only):
                st.caption(f"{_md(level.name)}：觀念關，沒有題目，重點寫在後面關卡的任務卡上。")


# ``$`` 會被當成 LaTeX、``~`` 是刪除線、``:名稱:`` 是 emoji 代碼，都是 Streamlit 的 Markdown 擴充
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>$~:])")


def _md(text: str) -> str:
    """題名、主題名放進 Markdown 前先跳脫：題名裡的 ``](`` 不能變成一個新連結。"""
    return _MD_SPECIAL.sub(r"\\\1", text)


def _focus_payload(stage: Stage, urls: dict[int, str]) -> dict:
    # 只說一次：換頁回來不該又看到「過關」
    cleared = st.session_state.pop(CLEARED_KEY, None)
    return {
        "task": stage.task_id,
        "title": stage.title,
        "topic": stage.topic_name,
        "difficulty": stage.difficulty,
        "done": stage.done,
        "url": safe_url(urls.get(stage.task_id)),
        # 最後一關做完時小熊停在終點（已完成），照樣要說一聲過關
        "cleared": cleared,
    }


def _render_hud(all_stages: list[Stage], done: int, total: int, focus_only: bool) -> None:
    """遊戲的狀態列：等級與經驗值（整個題庫算）、這條路線的進度。"""
    exp = experience(all_stages)
    left, mid, right = st.columns(3)
    left.metric("等級", f"Lv. {exp.level}", help="Easy 10、Medium 25、Hard 50 經驗值；每 100 升一級")
    mid.metric("經驗值", f"{exp.xp} XP")
    right.metric("重點路線" if focus_only else "全部關卡", f"{done} / {total}")
    st.progress(exp.ratio, text=f"再 {exp.per_level - exp.into_level} XP 升到 Lv. {exp.level + 1}")


def _render_next_panel(
    here: Stage | None, urls: dict[int, str], *, all_done: bool, today: bool = False
) -> None:
    """地圖很長，所以「下一關」在最上面也放一份：手機上不必捲到小熊那裡。"""
    with st.container(border=True):
        if here is None:
            return
        if all_done:
            st.markdown("🏆 **全部破關！** 每一題都做完了。")
            return
        label = "今天的下一題" if today else "下一關"
        st.markdown(f"🐻 **{label}**：{_md(here.title)}")
        st.caption(f"{_md(here.topic_name)} · {here.difficulty}")
        left, right = st.columns(2)
        url = safe_url(urls.get(here.task_id))
        if url:
            left.link_button("開始做題 ↗", url, width="stretch")
        right.button(
            "我 submit 過了",
            key=f"done_{here.task_id}",
            type="primary",
            width="stretch",
            on_click=_mark_done,
            args=(here.task_id, here.title),
        )


main()
