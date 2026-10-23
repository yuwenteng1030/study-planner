"""資料表定義（v0.1-core：7 張）。

分兩類：

* **全域**（`tasks`、`topics`）：所有使用者共用，沒有 `user_id`。
* **使用者資料**（`users`、`plans`、`plan_items`、`progress_events`、
  `task_completions`）：
  一律帶 `user_id`，即使 v0.1 只有一筆使用者。

為什麼第一版就帶 `user_id`：見 README 設計決策表。簡單說是「加欄位很便宜，
回頭改所有查詢很貴」——開放登入時只需要改 `api/deps.py`
的 `get_current_user()`，不必動任何一張表或任何一句查詢。

v0.1.5／v0.2 才會用到的 3 張表（`topic_demand`、`user_task_stats`、
`jds`）刻意不在這裡建，隨各自的功能一起進來。`api_usage` 2026-10-23 加入（額度改存資料庫），`jds` 2026-10-24 隨 JD 出處引用加入。

**schema 變更一律寫 Alembic migration**（`db/migrations/versions/`），不要只改這個檔案。
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有資料表的共同基底。"""


# ---------------------------------------------------------------------------
# 全域資料
# ---------------------------------------------------------------------------


class Topic(Base):
    """知識地圖上的一個主題。種子資料來自 ``data/topics.yaml``。

    主鍵刻意用字串（``arrays``、``hashmaps``）而不是自動編號的整數：
    題庫 CSV 的 ``topic`` 欄填的就是這些名字，用字串當主鍵可以讓匯入
    直接對上，不需要再查一次對照表，出錯時錯誤訊息也看得懂。
    """

    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    domain: Mapped[str] = mapped_column(String(20), nullable=False, default="python")

    #: 主題本身的重要性，1–10。不受使用者程度或 JD 影響。
    base_weight: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 先修主題的 id 清單。構成 DAG；循環與不存在的前置由
    #: core.planner._prereq_closure() 在排程時拋錯攔下。
    prereq: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    #: 顯示在任務卡上的提示；也是 LLM 不可用時的備援文字。
    key_points: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    #: 屬於這個主題的題目。
    #: ``tasks`` 有兩個外鍵指向 ``topics``（``topic_id`` 與 ``prereq``），
    #: 所以必須明寫 foreign_keys，否則 SQLAlchemy 無法判斷要用哪一個。
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="topic", foreign_keys="Task.topic_id"
    )

    __table_args__ = (
        CheckConstraint("base_weight BETWEEN 1 AND 10", name="ck_topics_base_weight"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 除錯用
        return f"<Topic {self.id} w={self.base_weight}>"


class Task(Base):
    """題庫裡的一個項目。種子資料來自 ``data/tasks.csv``。

    表名用 ``tasks`` 而不是 ``problems``，是為了之後要加「讀一篇文章」
    「做一個小專案」這類非刷題任務時不用改表名；``task_type`` 同理。
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: 平台代號，目前只有 ``HR``（HackerRank）。難度尺度的差異在評分時用這欄校正。
    source: Mapped[str] = mapped_column(String(10), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)

    topic_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("topics.id"), nullable=False, index=True
    )

    #: 跨主題的例外前置：這一題還需要哪個**主題**的基礎。
    #: 同主題內的前後關係不填在這裡（那由 topics.prereq 管），
    #: 避免同一份依賴資料存兩個地方而不同步。
    prereq: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("topics.id"), nullable=True
    )

    #: 平台原始難度，照抄不換算：``Easy`` / ``Medium`` / ``Hard``。
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False)

    #: 預估分鐘數。定義是「第一次獨立解題，不含看解答與重寫」。
    est_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 預存的一句說明。Claude API 掛掉或沒額度時用它頂替，
    #: 網頁功能不減、只是文字變罐頭。
    static_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    task_type: Mapped[str] = mapped_column(String(20), nullable=False, default="problem")

    #: 重點題組的階段：1 暖身、2 Medium 主力、3 進階；不在重點題組裡是 NULL。
    #: 依上機考的型態挑的（3 題 90 分鐘，以陣列／字串處理與 Medium 為主），
    #: 讓使用者先練 30 題，而不是一開始就面對整個題庫。
    focus_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)

    topic: Mapped["Topic"] = relationship(
        back_populates="tasks", foreign_keys=[topic_id]
    )

    #: 跨主題前置所指向的主題物件（唯讀，只為了顯示名稱方便）。
    prereq_topic: Mapped["Topic | None"] = relationship(
        foreign_keys=[prereq], viewonly=True
    )

    __table_args__ = (
        UniqueConstraint("url", name="uq_tasks_url"),
        CheckConstraint(
            "difficulty IN ('Easy', 'Medium', 'Hard')", name="ck_tasks_difficulty"
        ),
        CheckConstraint("est_minutes > 0", name="ck_tasks_est_minutes"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 除錯用
        return f"<Task {self.id} {self.title!r} {self.difficulty}>"


# ---------------------------------------------------------------------------
# 使用者資料（都有 user_id）
# ---------------------------------------------------------------------------


class User(Base):
    """使用者。v0.1–v0.3 只有一筆（``id=1``），之後接登入。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)

    #: 每日 LLM 呼叫上限。多使用者時用來防止單一使用者燒掉整個額度。
    daily_api_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    #: IANA 時區名稱。「今天」照使用者的時區算，不照伺服器的時鐘：
    #: 部署在 UTC 的伺服器上，台灣早上 8 點前用伺服器的日期會看到昨天的任務。
    tz: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Asia/Taipei", server_default="Asia/Taipei"
    )

    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    plans: Mapped[list["Plan"]] = relationship(back_populates="user")


class Plan(Base):
    """一份學習計畫。"""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    #: 使用者填的自由文字目標。v0.1 的表單是下拉選單，所以這裡存選到的那句。
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)

    #: 解析後的結構化目標。v0.1 由下拉選單直接給，不經 LLM（goal_parser 排在 v0.2）。
    parsed_goal: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # 刻意**不設** target_company / target_dept 欄位。
    #
    # 這個 repository 是公開的，而這兩個欄位的用途就是存放「我想進哪家公司的
    # 哪個單位」——等於在公開的 schema 上宣告求職對象。而且它們從來沒被讀寫過，
    # 是純粹的死欄位。目標資訊一律用 core/goals.py 的題型選項表達
    # （「程式上機測驗」而不是某公司某部門），對其他使用者也才有意義。

    start_date: Mapped[_dt.date] = mapped_column(Date, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 使用者自評程度，決定 gap_factor：``familiar`` / ``normal`` / ``new``。
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    #: 最後一次檢查「要不要重排」是哪一天（使用者時區）。落後只會在日子過去時發生，
    #: 所以一天檢查一次就夠；用條件式 UPDATE 搶這一格，兩個分頁同時開也只有一個會重排。
    last_replanned_on: Mapped[_dt.date | None] = mapped_column(Date, nullable=True)

    user: Mapped["User"] = relationship(back_populates="plans")
    items: Mapped[list["PlanItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("days > 0", name="ck_plans_days"),
        CheckConstraint("daily_minutes > 0", name="ck_plans_daily_minutes"),
        CheckConstraint(
            "level IN ('familiar', 'normal', 'new')", name="ck_plans_level"
        ),
        # 同一個使用者同時只能有一份進行中的計畫。原本只靠 create_plan 先把舊的收起來，
        # 兩次「排出計畫」同時送出就可能留下兩份 active。partial index 兩種資料庫都支援。
        Index(
            "uq_plans_one_active_per_user",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )


class PlanItem(Base):
    """計畫裡的一項：某一天要做某一題。"""

    __tablename__ = "plan_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=False
    )

    scheduled_date: Mapped[_dt.date] = mapped_column(Date, nullable=False, index=True)

    #: 同一天之內的顯示順序。欄位名不用 ``order``（SQL 保留字）。
    order_in_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: 百分位分級的結果：``must`` 必學 / ``recommended`` 建議 / ``bonus`` 加分。
    #: 重排時 ``bonus`` 先被砍，``must`` 一題都不能砍。
    priority: Mapped[str] = mapped_column(String(20), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="todo")
    done_at: Mapped[_dt.datetime | None] = mapped_column(DateTime, nullable=True)

    #: 實際花的分鐘數。v0.1 只記錄不使用（estimator 排在 v0.2），
    #: 但從第一版就開始記，否則這段資料事後補不回來。
    actual_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: 「為什麼推薦這一項」。LLM 生成，失敗時退回 tasks.static_note。
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 這句話怎麼來的：``llm`` 即時生成 / ``static`` 預存。任務卡用它標出來，
    #: 讓人知道現在看到的是客製的還是罐頭。舊資料是 NULL（當預存）。
    reason_source: Mapped[str | None] = mapped_column(String(10), nullable=True)

    #: 顯示用的重點提示，從 topics.key_points 複製一份快照。
    key_points: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    plan: Mapped["Plan"] = relationship(back_populates="items")
    task: Mapped["Task"] = relationship()

    __table_args__ = (
        UniqueConstraint("plan_id", "task_id", name="uq_plan_items_plan_task"),
        CheckConstraint(
            "priority IN ('must', 'recommended', 'bonus')",
            name="ck_plan_items_priority",
        ),
        CheckConstraint(
            "status IN ('todo', 'done', 'skipped')", name="ck_plan_items_status"
        ),
        Index("ix_plan_items_plan_date", "plan_id", "scheduled_date"),
    )


class ProgressEvent(Base):
    """互動事件紀錄。

    只存「目前狀態」會失去歷史，記錄事件才能算連續天數、支援取消、
    回溯「為什麼重排」。概念上等同 MES 的 lot history。
    """

    __tablename__ = "progress_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id"), nullable=False, index=True
    )

    #: 事件若不針對單一項目（例如整份計畫重排），這裡留空。
    item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("plan_items.id"), nullable=True
    )

    #: ``done`` / ``undo`` / ``replan``
    event: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )

    #: 事件的附帶資料，例如重排前後的差異。
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "event IN ('done', 'undo', 'replan')", name="ck_progress_events_event"
        ),
    )


class TaskCompletion(Base):
    """在**計畫之外**做完的一題（從學習地圖按「我 submit 過了」）。

    為什麼不塞進 ``plan_items``：計畫項目代表「排程排了這一題」，有日期與分級；
    地圖上自己跑去做的題目沒有這些東西，硬塞會讓「計畫完成率」被灌水。
    為什麼不記成 ``progress_events``：那張表的 ``plan_id`` 是 NOT NULL，
    事件本來就是「某份計畫裡發生的事」。

    題目若在進行中的計畫裡、而且還沒勾（``todo``），完成照舊寫在 ``plan_items``，
    不會寫進這裡。新的寫入只寫一個地方；但舊資料可能兩邊都有（例如舊計畫勾過、
    這裡也記過），所以「取消完成」會把每一個說它做完的地方都清掉。
    """

    __tablename__ = "task_completions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=False
    )
    done_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    #: 冒險計時算出來的實際分鐘數（從地圖開始做題到按「我 submit 過了」）；沒計時是 NULL。
    actual_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    task: Mapped["Task"] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "task_id", name="uq_task_completions_user_task"),
    )


class ApiUsage(Base):
    """每位使用者每天呼叫 Claude 的次數，依用途分開記。

    原本存在記憶體：伺服器重開就歸零、多個 worker 各算各的，額度形同虛設。
    ``day`` 是使用者時區的日期，所以「每天」跟使用者看到的「今天」一致。
    """

    __tablename__ = "api_usage"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    day: Mapped[_dt.date] = mapped_column(Date, primary_key=True)
    #: ``explain``（計畫說明）或 ``encourage``（鼓勵訊息）
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (CheckConstraint("count >= 0", name="ck_api_usage_count"),)


class ActiveAdventure(Base):
    """正在進行的「冒險」：使用者從地圖按了開始做題，計時中。每位使用者同時只有一場。

    按「我 submit 過了」時，經過的分鐘數就是實際花的時間，自動記下來、這一列刪掉。
    存資料庫而不是 session_state：做題時會切到 HackerRank 的分頁，回來時 Streamlit
    可能已經換了一個 session（重新整理、伺服器重開），計時不能因此不見。
    """

    __tablename__ = "active_adventures"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=False)
    #: 開始的時間（UTC）
    started_at: Mapped[_dt.datetime] = mapped_column(DateTime, nullable=False)


class JobDescription(Base):
    """使用者貼上的一份職缺描述。

    只存原文，不存分析結果：「提到哪些主題、引用哪一句」每次從原文用
    ``core.jd_parser.analyze`` 算出來。關鍵字清單改了，舊的 JD 馬上套用新規則，
    不會有一份過期的分析結果躺在資料庫裡跟原文對不上。
    表名照架構設計文件叫 ``jds``。
    """

    __tablename__ = "jds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    #: 使用者自己取的名字（例如「後端工程師 A」），列表與出處裡顯示用。
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
