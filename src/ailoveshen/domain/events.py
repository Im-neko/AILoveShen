"""ドメインイベント。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ailoveshen.domain.value_objects import EmotionState

# =============================================================================
# 基底のイベント
# =============================================================================


def _generate_event_id() -> str:
    """一意なイベント ID を作る。"""
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    """今の UTC の日時を返す。"""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DomainEvent:
    """
    ドメインイベントの基底クラス。

    ドメインイベントは、ドメインで起きた意味のあることを表す。
    不変（frozen=True）で、必要な情報をすべて持つ。
    """

    event_id: str = field(default_factory=_generate_event_id)
    occurred_at: datetime = field(default_factory=_utc_now)

    @property
    def event_type(self) -> str:
        """イベントの型の名前を返す。"""
        return self.__class__.__name__


# =============================================================================
# 発話のイベント
# =============================================================================


@dataclass(frozen=True)
class SpeechStartedEvent(DomainEvent):
    """
    音声の合成・再生が始まったときのドメインイベント。

    AI が話していることを、他のコンポーネントに知らせるのに使う。
    """

    text: str = field(default="")
    source: str = field(default="unknown")  # 例: "commentary"、"chat_response"
    emotion: EmotionState = field(default_factory=EmotionState)
    duration_ms: int = field(default=0)  # これから再生する音声の長さ（口の動きを合わせる）


@dataclass(frozen=True)
class SpeechCompletedEvent(DomainEvent):
    """
    音声の再生が終わったときのドメインイベント。

    最後まで話したか、割り込まれたかを示す。
    """

    text: str = field(default="")
    source: str = field(default="unknown")
    completed: bool = field(default=True)  # 割り込まれたら False
    duration_ms: int = field(default=0)


@dataclass(frozen=True)
class SpeechQueuedEvent(DomainEvent):
    """
    発話のリクエストをキューに入れたときのドメインイベント。

    待っている発話のリクエストを追うのに使える。
    """

    text: str = field(default="")
    source: str = field(default="unknown")
    queue_position: int = field(default=0)


# =============================================================================
# 会話のイベント
# =============================================================================


@dataclass(frozen=True)
class CommentaryGeneratedEvent(DomainEvent):
    """配信者の実況を生成したときのドメインイベント。"""

    text: str = field(default="")


@dataclass(frozen=True)
class ChatResponseGeneratedEvent(DomainEvent):
    """視聴者のチャットへの返答を生成したときのドメインイベント。"""

    text: str = field(default="")
    original_message: str = field(default="")
    user_name: str = field(default="")


# =============================================================================
# ゲームのイベント
# =============================================================================


@dataclass(frozen=True)
class HouseDesignedEvent(DomainEvent):
    """LLM が建てる家を設計したときのイベント。"""

    name: str = ""
    concept: str = ""


@dataclass(frozen=True)
class GoalSetEvent(DomainEvent):
    """
    新しい小目標を設定したときのイベント（例: goal="have(planks, 12)"）。

    `mid_goal` は、その小目標が役立つ中目標の題名（生存のためなら ""）。
    """

    goal: str = ""
    reason: str = ""
    mid_goal: str = ""


@dataclass(frozen=True)
class GoalEndedEvent(DomainEvent):
    """小目標が終わったときのイベント: 達成したか、あきらめた（進まない、行き詰まった、など）。"""

    goal: str = ""
    reason: str = ""
    ended_because: str = ""
    met: bool = False


@dataclass(frozen=True)
class MidGoalAddedEvent(DomainEvent):
    """
    中目標がリストに加わったときのイベント（`position` は 1 始まり）。

    視聴者の中目標（`requested_by`）は、返答でもう伝えてある。
    """

    title: str = ""
    reason: str = ""
    requested_by: str = ""
    position: int = 0


@dataclass(frozen=True)
class MidGoalCompletedEvent(DomainEvent):
    """中目標の条件が世界で満たされたときのイベント。"""

    title: str = ""
    requested_by: str = ""


@dataclass(frozen=True)
class MidGoalDroppedEvent(DomainEvent):
    """中目標を断念したときのイベント（必ず配信で言う）。"""

    title: str = ""
    reason: str = ""
    requested_by: str = ""


@dataclass(frozen=True)
class GameActionExecutedEvent(DomainEvent):
    """エージェントがゲームで行動を 1 つ実行したあとのイベント。"""

    action_id: str = ""
    ok: bool = True
    result: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class HouseCompletedEvent(DomainEvent):
    """家のブロックがすべて置かれたときのイベント。"""

    name: str = ""


@dataclass(frozen=True)
class TownDefinedEvent(DomainEvent):
    """大目標の街がどんなものかを、配信者が決めたときのイベント。"""

    text: str = ""
    stages: tuple[str, ...] = ()  # 段階の題名（順番どおり）


@dataclass(frozen=True)
class TownSiteChosenEvent(DomainEvent):
    """配信者が候補地を見比べて、街の場所を選んだときのイベント。"""

    name: str = ""  # 街の名前
    site_id: str = ""
    reason: str = ""
    moving: bool = False  # 最初の家から引っ越すか


@dataclass(frozen=True)
class TownCompletedEvent(DomainEvent):
    """街のすべての段階が済んだときのイベント。"""

    text: str = ""
