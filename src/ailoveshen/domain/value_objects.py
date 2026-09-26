"""ドメイン層の値オブジェクト。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Optional


def _utc_now() -> datetime:
    """今の UTC の日時を返す。"""
    return datetime.now(timezone.utc)


# =============================================================================
# 感情の値オブジェクト
# =============================================================================


class EmotionType(str, Enum):
    """AI のキャラクターが表せる感情の種類。"""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    SCARED = "scared"
    EXCITED = "excited"


@dataclass(frozen=True)
class EmotionState:
    """
    感情の状態の値オブジェクト。

    値オブジェクトは不変で、属性で比較する。

    Raises:
        ValueError: intensity が 0.0 から 1.0 の間にないとき。
    """

    primary: EmotionType = EmotionType.NEUTRAL
    intensity: float = 0.5  # 0.0 - 1.0

    def __post_init__(self) -> None:
        """intensity の範囲を検証する。"""
        if not 0.0 <= self.intensity <= 1.0:
            raise ValueError(f"intensity must be between 0.0 and 1.0, got {self.intensity}")

    def with_intensity(self, new_intensity: float) -> EmotionState:
        """intensity を変えた新しい EmotionState を作る（範囲内に収める）。"""
        clamped = max(0.0, min(1.0, new_intensity))
        return EmotionState(primary=self.primary, intensity=clamped)

    def decay(self, rate: float = 0.1) -> EmotionState:
        """
        時間とともに感情の intensity を弱める。

        intensity がしきい値を下回ったら neutral を返す。
        """
        new_intensity = max(0.0, self.intensity - rate)
        if new_intensity < 0.3:
            return EmotionState(EmotionType.NEUTRAL, 0.5)
        return EmotionState(primary=self.primary, intensity=new_intensity)


# =============================================================================
# 発話の値オブジェクト
# =============================================================================


class SpeechPriority(IntEnum):
    """発話のリクエストの優先度。"""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    INTERRUPT = 3


@dataclass(frozen=True)
class SpeechRequest:
    """
    発話のリクエストの値オブジェクト。

    音声を合成して再生するリクエストを表す。
    """

    text: str
    priority: SpeechPriority = SpeechPriority.NORMAL
    emotion: EmotionState = field(default_factory=EmotionState)
    source: str = "unknown"  # "commentary" | "response" | "game_event"

    def should_interrupt(self) -> bool:
        """このリクエストが今の発話に割り込むべきかを調べる。"""
        return self.priority >= SpeechPriority.INTERRUPT


class SpeechStatus(str, Enum):
    """発話の再生の状態。"""

    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    PLAYING = "playing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True)
class SpeechResult:
    """
    音声の合成・再生の結果を表す値オブジェクト。

    発話のリクエストがどうなったかの、不変の記録。
    """

    request_text: str
    status: SpeechStatus
    audio_duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    completed_at: datetime = field(default_factory=_utc_now)

    @classmethod
    def queued(cls, text: str) -> SpeechResult:
        """キューに入れたときの結果を作る。"""
        return cls(
            request_text=text,
            status=SpeechStatus.QUEUED,
        )

    @classmethod
    def completed(cls, text: str, duration_ms: int) -> SpeechResult:
        """完了したときの結果を作る。"""
        return cls(
            request_text=text,
            status=SpeechStatus.COMPLETED,
            audio_duration_ms=duration_ms,
        )

    @classmethod
    def interrupted(cls, text: str) -> SpeechResult:
        """割り込まれたときの結果を作る。"""
        return cls(
            request_text=text,
            status=SpeechStatus.INTERRUPTED,
        )

    @classmethod
    def failed(cls, text: str, error: str) -> SpeechResult:
        """失敗したときの結果を作る。"""
        return cls(
            request_text=text,
            status=SpeechStatus.FAILED,
            error_message=error,
        )

    @property
    def is_success(self) -> bool:
        """発話が最後まで成功したかを調べる。"""
        return self.status == SpeechStatus.COMPLETED


# =============================================================================
# 会話の値オブジェクト
# =============================================================================


class MessageRole(str, Enum):
    """会話のメッセージを送ったのは誰か。"""

    VIEWER = "viewer"
    STREAMER = "streamer"


class MessageType(str, Enum):
    """会話のメッセージがどんな種類の発言か。"""

    CHAT = "chat"  # 視聴者のチャットのコメント
    COMMENTARY = "commentary"  # 配信者のゲーム実況 / 考え
    RESPONSE = "response"  # 配信者から視聴者への返答


@dataclass(frozen=True)
class ConversationMessage:
    """
    配信の会話の 1 つのメッセージ。

    Raises:
        ValueError: content が空のとき。
    """

    role: MessageRole
    message_type: MessageType
    content: str
    speaker_name: str = ""
    speaker_id: Optional[str] = None
    timestamp: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        """content を検証する。"""
        if not self.content.strip():
            raise ValueError("content must not be empty")

    @classmethod
    def from_viewer(
        cls,
        content: str,
        user_name: str,
        user_id: Optional[str] = None,
    ) -> ConversationMessage:
        """視聴者が送ったチャットのメッセージを作る。"""
        return cls(
            role=MessageRole.VIEWER,
            message_type=MessageType.CHAT,
            content=content,
            speaker_name=user_name,
            speaker_id=user_id,
        )

    @classmethod
    def from_streamer(cls, content: str, message_type: MessageType) -> ConversationMessage:
        """AI の配信者が話したメッセージを作る。"""
        return cls(
            role=MessageRole.STREAMER,
            message_type=message_type,
            content=content,
        )


@dataclass(frozen=True)
class CharacterProfile:
    """
    AI の配信者のキャラクター。

    Raises:
        ValueError: name が空のとき。
    """

    name: str = "AILoveShen"
    description: str = "明るく元気なAI配信者"
    speech_style: str = "フレンドリーで親しみやすい"
    first_person: str = "私"
    sentence_endings: tuple[str, ...] = ("だよ", "だね", "かな", "！")
    personality_traits: tuple[str, ...] = (
        "好奇心旺盛",
        "ポジティブ",
        "ちょっとおっちょこちょい",
        "視聴者思い",
    )

    def __post_init__(self) -> None:
        """name を検証する。"""
        if not self.name.strip():
            raise ValueError("name must not be empty")


@dataclass(frozen=True)
class GenerationContext:
    """
    発言を生成するときに、配信者が知っていることのすべて。

    `activity` は配信者が今していることとその理由（目標の決定が見るものと同じ）。
    ゲームをプレイしていない間は None。
    """

    emotion_state: EmotionState = field(default_factory=EmotionState)
    activity: Optional[Activity] = None
    recent_events: tuple[str, ...] = ()
    recent_messages: tuple[ConversationMessage, ...] = ()


# =============================================================================
# 位置の値オブジェクト
# =============================================================================


@dataclass(frozen=True)
class Position:
    """ゲームの世界の 3 次元の位置。"""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def distance_to(self, other: Position) -> float:
        """別の位置までのユークリッド距離を計算する。"""
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2) ** 0.5

    def __str__(self) -> str:
        return f"({self.x:.1f}, {self.y:.1f}, {self.z:.1f})"


@dataclass(frozen=True)
class Rotation:
    """
    プレイヤーの向き（yaw と pitch）。

    Raises:
        ValueError: yaw が -180 から 180 の間にないか、pitch が -90 から 90 の間にないとき。
    """

    yaw: float = 0.0  # -180 から 180
    pitch: float = 0.0  # -90 から 90

    def __post_init__(self) -> None:
        """向きの範囲を検証する。"""
        if not -180.0 <= self.yaw <= 180.0:
            raise ValueError(f"yaw must be between -180 and 180, got {self.yaw}")
        if not -90.0 <= self.pitch <= 90.0:
            raise ValueError(f"pitch must be between -90 and 90, got {self.pitch}")

    def __str__(self) -> str:
        return f"(yaw={self.yaw:.1f}, pitch={self.pitch:.1f})"


# =============================================================================
# フィルター結果の値オブジェクト
# =============================================================================


@dataclass(frozen=True)
class FilterResult:
    """
    コメントのフィルターの結果。

    Raises:
        ValueError: score が 0.0 から 1.0 の間にないとき。
    """

    should_respond: bool
    score: float  # 0.0 - 1.0。コメントがどれだけ重要で関係があるか
    reason: str

    def __post_init__(self) -> None:
        """score の範囲を検証する。"""
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score must be between 0.0 and 1.0, got {self.score}")

    @classmethod
    def accept(cls, score: float, reason: str = "Accepted") -> FilterResult:
        """受け入れるフィルターの結果を作る。"""
        return cls(should_respond=True, score=score, reason=reason)

    @classmethod
    def reject(cls, score: float, reason: str = "Rejected") -> FilterResult:
        """退けるフィルターの結果を作る。"""
        return cls(should_respond=False, score=score, reason=reason)


# =============================================================================
# Minecraft の値オブジェクト
# =============================================================================


class GoalPredicate(str, Enum):
    """
    目標を書く語彙（Minecraft ブリッジが世界から判定する）。

    - HAVE: アイテムかグループを `count` 個持つ（planks、log、door、bed、wool、food など）
    - STORED: チェストにアイテムかグループが `count` 個ある（最後に開けたときの記憶で）
    - LIT: 家から `distance` ブロック以内に暗い地面がない（周りに松明を置いた）
    - BUILT: 家のプランのブロックがすべて置かれている。`name` があれば、その名前の建物
      （Gemini が設計したもの、docs/design/25_builds.md）
    - PLACED: アイテムがどこかに置かれている（家の中のベッド）
    - AT_HOME: 家の中にいて、扉が閉まっている
    - THROUGH_NIGHT: 夜が明けた（家の中にいたか、寝ていた）
    - EXPLORED: 目標を設定した場所から `distance` ブロック離れた
    - CLEARED: 扉の近くで待つ敵対モブがいない（昼だけ: 外に出て戦う）
    - SURVEYED: 街の候補地を `count` か所調べた（家を中心に、ブリッジが地形を数字にする）
    - PLANTED: 自分が植えた苗木（item: sapling か種類）が `count` 本ある（育った木も数える）
    - FARMED: 家のまわりの耕地に作物（item: wheat など）が `count` マス植わっている
    """

    HAVE = "have"
    BUILT = "built"
    PLACED = "placed"
    AT_HOME = "at_home"
    THROUGH_NIGHT = "through_night"
    EXPLORED = "explored"
    CLEARED = "cleared"
    STORED = "stored"
    LIT = "lit"
    SURVEYED = "surveyed"
    PLANTED = "planted"
    FARMED = "farmed"


@dataclass(frozen=True)
class GoalSpec:
    """
    述語の語彙で書いた目標と、その引数。

    ここで確かめるのは形だけ。アイテムが実在するか、目標が世界で意味をなすか
    （例: 帰る家があるか）は、ブリッジが確かめる。

    Raises:
        ValueError: 述語に要る引数がないか、正しくないとき。
    """

    predicate: GoalPredicate
    item: Optional[str] = None
    count: Optional[int] = None
    where: Optional[str] = None
    distance: Optional[int] = None
    # 埋まった石・鉱石まで階段で掘り下げてよい深さ（小目標だけ。None: 掘り下げない）
    dig_depth: Optional[int] = None
    # built の建物の名前（None: 家）
    name: Optional[str] = None

    def __post_init__(self) -> None:
        """述語に要る引数を確かめる。"""
        if self.dig_depth is not None and self.dig_depth < 0:
            raise ValueError(f"dig_depth must not be negative, got {self.dig_depth}")
        if self.predicate in (
            GoalPredicate.HAVE,
            GoalPredicate.STORED,
            GoalPredicate.PLANTED,
            GoalPredicate.FARMED,
        ):
            name = self.predicate.value
            if not self.item:
                raise ValueError(f"{name} needs an item")
            if self.count is None or self.count < 1:
                raise ValueError(f"{name} needs a positive count, got {self.count}")
        if self.predicate == GoalPredicate.SURVEYED and (self.count is None or self.count < 1):
            raise ValueError(f"surveyed needs a positive count, got {self.count}")
        if self.predicate == GoalPredicate.PLACED and not (self.item and self.where):
            raise ValueError("placed needs an item and where")
        if self.predicate in (GoalPredicate.EXPLORED, GoalPredicate.LIT) and (
            self.distance is None or self.distance < 1
        ):
            raise ValueError(
                f"{self.predicate.value} needs a positive distance, got {self.distance}"
            )

    def to_dict(self) -> dict[str, Any]:
        """ブリッジに送る形の spec（設定されている引数だけ）。"""
        out: dict[str, Any] = {"predicate": self.predicate.value}
        for key in ("item", "count", "where", "distance", "dig_depth", "name"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    def same_kind(self, other: GoalSpec) -> bool:
        """数や距離だけが違う同じ目標か（have(food, 2) と have(food, 1)。docs/design/27）。"""
        return (self.predicate, self.item, self.where, self.name) == (
            other.predicate,
            other.item,
            other.where,
            other.name,
        )

    def describe(self) -> str:
        """プロンプトとログ用の短い形。例: have(planks, 12)。"""
        args = [
            str(v)
            for v in (self.name, self.item, self.count, self.where, self.distance)
            if v is not None
        ]
        if self.dig_depth is not None:
            args.append(f"dig_depth={self.dig_depth}")
        return f"{self.predicate.value}({', '.join(args)})"


@dataclass(frozen=True)
class Goal:
    """
    小目標: LLM が決めた今の方向。行動選択器はその中で動く。

    `mid_goal_id` は、それが役立つ中目標（None: 生存のため。例えば夜を越すことは、
    中目標を待ってくれない）。
    """

    spec: GoalSpec
    reason: str = ""
    mid_goal_id: Optional[str] = None
    set_at: datetime = field(default_factory=_utc_now)
    # 前の小目標が失敗で終わったとき: その原因の分析と、この小目標のやり方の助言（選択器が読む。
    # docs/design/27）
    diagnosis: str = ""
    advice: str = ""
    # この小目標を決めたときの、今の状態での見直し（docs/design/31）
    review: str = ""


@dataclass(frozen=True)
class ConditionStatus:
    """ブリッジが判定した中目標の条件（目標としては設定しない）。"""

    spec: GoalSpec
    met: bool
    lines: tuple[str, ...] = ()
    impossible: tuple[str, ...] = ()  # 配信者が今できることでは手に入らないもの


# 世界の状態だけから判定できる: 中目標の完了条件になれる
CONDITION_PREDICATES = frozenset(
    {
        GoalPredicate.BUILT,
        GoalPredicate.PLACED,
        GoalPredicate.HAVE,
        GoalPredicate.STORED,
        GoalPredicate.LIT,
        GoalPredicate.SURVEYED,
        GoalPredicate.PLANTED,
        GoalPredicate.FARMED,
    }
)
# LLM が中目標や街の段階に書ける条件。調査は、街の場所を決める前にコードが足すだけ
PLANNABLE_CONDITIONS = CONDITION_PREDICATES - {GoalPredicate.SURVEYED}
_SURVIVAL_PREDICATES = frozenset(
    {GoalPredicate.THROUGH_NIGHT, GoalPredicate.AT_HOME, GoalPredicate.CLEARED}
)


def is_survival(spec: GoalSpec) -> bool:
    """小目標が配信者を生き延びさせるためのものか（どの中目標のためでなくてもよい）。"""
    return spec.predicate in _SURVIVAL_PREDICATES or (
        spec.predicate == GoalPredicate.HAVE and spec.item == "food"
    )


@dataclass(frozen=True)
class Mission:
    """全体を貫く唯一の目標: 設定で決め、コメントでは変わらない。"""

    text: str


class MidGoalState(str, Enum):
    """中目標の状態。"""

    PENDING = "pending"
    DONE = "done"
    DROPPED = "dropped"


@dataclass(frozen=True)
class MidGoal:
    """
    中目標: 大目標への一歩。条件がすべて世界で満たされたら完了する。

    `requested_by` は頼んだ視聴者（None: 配信者自身のもの）。
    `stage` は、それが表す街の段階（None: 段階ではない）。
    `prepares_town` は、街の段階の前にやる準備（候補地の調査、選んだ場所への引っ越し）。
    段階と同じく、やめられない。
    `steps` は、そのために小目標が使ったステップの数（視聴者のものには予算がある）。

    Raises:
        ValueError: 条件がないか、世界から判定できない条件があるとき。
    """

    id: str
    title: str
    conditions: tuple[GoalSpec, ...]
    reason: str = ""
    requested_by: Optional[str] = None
    state: MidGoalState = MidGoalState.PENDING
    ended_because: str = ""
    steps: int = 0
    progress: tuple[str, ...] = ()  # 最後に判定したときの、条件の進み具合
    stage: Optional[int] = None
    prepares_town: bool = False
    # 視聴者の頼みのステップの予算（None: プランの既定。建物は大きさに合わせて広げる）
    budget: Optional[int] = None
    # Gemini が書いた、この中目標のための手順（小目標の並び）。Jev が次を選ぶ（docs/design/26）
    plan_steps: tuple["PlannedStep", ...] = ()

    def __post_init__(self) -> None:
        """条件を確かめる。"""
        if not self.title:
            raise ValueError("a mid goal needs a title")
        if not self.conditions:
            raise ValueError(f"mid goal {self.title} needs at least one condition")
        for c in self.conditions:
            if c.predicate not in CONDITION_PREDICATES:
                allowed = ", ".join(sorted(p.value for p in CONDITION_PREDICATES))
                raise ValueError(
                    f"{c.predicate.value} cannot be a condition of a mid goal; use {allowed}"
                )

    def summary(self) -> tuple[str, ...]:
        """細かい手順（字下げした進み具合の行）を除いた、条件の進み具合。"""
        return tuple(line for line in self.progress if not line.startswith(" "))

    def describe(self) -> str:
        """短い形。例: 自分の家を作る (built())。"""
        return f"{self.title} ({', '.join(c.describe() for c in self.conditions)})"


@dataclass(frozen=True)
class GoalOutcome:
    """過去の目標と、それが終わった理由。"""

    goal: Goal
    ended_because: str
    met: bool = False


@dataclass(frozen=True)
class GoalStatus:
    """
    ブリッジが世界から判定した今の目標。

    `remaining` は残りの作業（集めるアイテム、クラフト、置くブロック）。これが減る
    ことを進んだとみなす。`lines` はサブゴールとその進み具合、`blocked` はその一部を
    今進められない理由。
    """

    met: bool
    remaining: int
    lines: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()


class Side(str, Enum):
    """家の壁の面（Minecraft: 北は -Z、東は +X）。"""

    NORTH = "north"
    EAST = "east"
    SOUTH = "south"
    WEST = "west"


class BlockKind(str, Enum):
    """建築プランが使うブロックの種類（木の種類は問わない）。AIR は「空ける」（掘る）。"""

    PLANKS = "planks"
    LOG = "log"
    DOOR = "door"
    COBBLESTONE = "cobblestone"
    DIRT = "dirt"
    AIR = "air"


@dataclass(frozen=True)
class PlannedBlock:
    """建築プランのブロック 1 つ。敷地の原点（最小の角、地面の高さ）からの相対位置。"""

    x: int
    y: int
    z: int
    kind: BlockKind


@dataclass(frozen=True)
class HouseBlueprint:
    """
    一部屋の小さな家: 壁、平らな屋根、扉 1 つ。

    窓はない: ガラスのない開口部から、外のモブが中のボットを攻撃した
    （ガラスには製錬が要り、ブリッジはまだそれができない）。

    大きさの範囲は、Minecraft ブリッジが確実に建てられると計測したものから決めた
    （5x5x3 と 7x7x4 はどちらも、置くのに一度も失敗せずに完成した）。

    Raises:
        ValueError: 寸法が範囲の外か、扉がその壁に収まらないとき。
    """

    MIN_SIDE = 5
    MAX_SIDE = 7
    MIN_WALL_HEIGHT = 3
    MAX_WALL_HEIGHT = 4

    name: str
    concept: str
    width: int  # x 方向
    depth: int  # z 方向
    wall_height: int
    door_side: Side
    door_offset: int
    corner_pillars: bool = False  # 四隅を板材ではなく原木にする

    def __post_init__(self) -> None:
        """寸法と扉を検証する。"""
        for label, value in (("width", self.width), ("depth", self.depth)):
            if not self.MIN_SIDE <= value <= self.MAX_SIDE:
                raise ValueError(f"{label} must be {self.MIN_SIDE}-{self.MAX_SIDE}, got {value}")
        if not self.MIN_WALL_HEIGHT <= self.wall_height <= self.MAX_WALL_HEIGHT:
            raise ValueError(
                f"wall_height must be {self.MIN_WALL_HEIGHT}-{self.MAX_WALL_HEIGHT}, "
                f"got {self.wall_height}"
            )
        self._check_offset("door", self.door_side, self.door_offset)

    def _wall_length(self, side: Side) -> int:
        return self.width if side in (Side.NORTH, Side.SOUTH) else self.depth

    def _check_offset(self, label: str, side: Side, offset: int) -> None:
        # 角は除く: そこに扉があると、壁の支えが切れる
        if not 1 <= offset <= self._wall_length(side) - 2:
            raise ValueError(
                f"{label} offset on the {side.value} wall must be "
                f"1-{self._wall_length(side) - 2}, got {offset}"
            )

    def _wall_position(self, side: Side, offset: int) -> tuple[int, int]:
        """壁に沿った、与えられた位置の (x, z)。"""
        if side == Side.NORTH:
            return offset, 0
        if side == Side.SOUTH:
            return offset, self.depth - 1
        if side == Side.WEST:
            return 0, offset
        return self.width - 1, offset

    @property
    def height(self) -> int:
        """屋根を含めた全体の高さ。"""
        return self.wall_height + 1

    def blocks(self) -> tuple[PlannedBlock, ...]:
        """
        置ける順にブロックへ展開する。

        壁は 1 段ずつ積み、屋根は外側から内側へ 1 周ずつ敷く（どのブロックも壁か、
        先に置いた屋根のブロックの上に載る）。扉は最後。
        """
        door_x, door_z = self._wall_position(self.door_side, self.door_offset)
        last_x, last_z = self.width - 1, self.depth - 1
        corners = {(0, 0), (0, last_z), (last_x, 0), (last_x, last_z)}
        out: list[PlannedBlock] = []

        for y in range(self.wall_height):
            for x in range(self.width):
                for z in range(self.depth):
                    if not (x in (0, self.width - 1) or z in (0, self.depth - 1)):
                        continue
                    if (x, z) == (door_x, door_z) and y < 2:
                        continue
                    pillar = self.corner_pillars and (x, z) in corners
                    kind = BlockKind.LOG if pillar else BlockKind.PLANKS
                    out.append(PlannedBlock(x, y, z, kind))

        for ring in range((min(self.width, self.depth) + 1) // 2):
            for x in range(ring, self.width - ring):
                for z in range(ring, self.depth - ring):
                    if x in (ring, self.width - 1 - ring) or z in (ring, self.depth - 1 - ring):
                        out.append(PlannedBlock(x, self.wall_height, z, BlockKind.PLANKS))

        out.append(PlannedBlock(door_x, 0, door_z, BlockKind.DOOR))
        return tuple(out)

    def material_counts(self) -> dict[BlockKind, int]:
        """家全体に要る、種類ごとのブロックの数。"""
        counts: dict[BlockKind, int] = {}
        for b in self.blocks():
            counts[b.kind] = counts.get(b.kind, 0) + 1
        return counts


@dataclass(frozen=True)
class PlannedStep:
    """
    中目標のための手順の 1 つ: 小目標の条件と、なぜそれか（ゴールボードと実況が使う）。
    条件は世界から判定できるもの（`/check` で済んだかを見る）。

    Raises:
        ValueError: 世界から判定できない条件のとき。
    """

    spec: GoalSpec
    reason: str = ""

    def __post_init__(self) -> None:
        if self.spec.predicate not in PLANNABLE_CONDITIONS:
            raise ValueError(
                f"{self.spec.predicate.value} cannot be a step (steps must be conditions the "
                "world can judge: have, stored, built, placed, lit)"
            )


class ShapeKind(str, Enum):
    """建物の設計の形（docs/design/25_builds.md §2）。どれも直方体で、後の形が前を上書きする。"""

    FILL = "fill"  # 埋める（床、壁、柱、屋根）
    HOLLOW_BOX = "hollow_box"  # 外殻だけ（中は触らない）
    CLEAR = "clear"  # 空気にする（入口、窓、部屋の中）
    DOOR = "door"  # ドア（from の位置が下のマス。上のマスもドアになる）


class BuildAnchor(str, Enum):
    """建物を置く場所。座標は書かず、コードが原点を決める（25 §3）。"""

    HOME_EAST = "home:east"
    HOME_WEST = "home:west"
    HOME_NORTH = "home:north"
    HOME_SOUTH = "home:south"
    NEAR_HOME = "near_home"
    MAP = "map"  # 地図の画像のマス目（`BuildDesign.cell`）

    @property
    def extends_home(self) -> bool:
        """家の増築か（家の外壁に重ねて建てる）。"""
        return self.value.startswith("home:")


@dataclass(frozen=True)
class BuildShape:
    """
    設計の形 1 つ。座標は建物の原点（最小の角）からの相対で、両端を含む。
    y=0 は床の層（地面・家の床と同じ高さ）、立つ高さは y=1。

    Raises:
        ValueError: 材料が形に合わないとき（clear と door は材料を持たない）。
    """

    kind: ShapeKind
    start: tuple[int, int, int]
    end: tuple[int, int, int]
    block: Optional[BlockKind] = None

    def __post_init__(self) -> None:
        if self.kind in (ShapeKind.FILL, ShapeKind.HOLLOW_BOX):
            if self.block is None or self.block in (BlockKind.AIR, BlockKind.DOOR):
                raise ValueError(f"{self.kind.value} needs a solid block, got {self.block}")

    def cells(self) -> list[tuple[int, int, int]]:
        (x0, y0, z0), (x1, y1, z1) = self.start, self.end
        xs = range(min(x0, x1), max(x0, x1) + 1)
        ys = range(min(y0, y1), max(y0, y1) + 1)
        zs = range(min(z0, z1), max(z0, z1) + 1)
        out = []
        for x in xs:
            for y in ys:
                for z in zs:
                    if self.kind == ShapeKind.HOLLOW_BOX and not (
                        x in (xs[0], xs[-1]) or y in (ys[0], ys[-1]) or z in (zs[0], zs[-1])
                    ):
                        continue
                    out.append((x, y, z))
        return out


@dataclass(frozen=True)
class BuildDesign:
    """
    Gemini が設計した建物（docs/design/25_builds.md）。形を並べ、`blocks()` でブロックに展開する。

    Raises:
        ValueError: 名前、範囲、ブロックの数が決まりに合わないとき（理由は Gemini に返す）。
    """

    MAX_BLOCKS = 3000  # 大きな建築（配信者自身・街の段階）
    VIEWER_MAX_BLOCKS = 600  # 視聴者の頼み（1 つの頼みに何時間も使わない）
    MAX_SIDE = 48
    MAX_HEIGHT = 16  # 足場は作らない（25 §2）
    MAX_SHAPES = 40

    name: str
    purpose: str
    anchor: BuildAnchor
    shapes: tuple[BuildShape, ...]
    # anchor が MAP のとき: 地図のマス目（例: C7）と、コードがそこから決めた中心 (x, z)
    cell: Optional[str] = None
    site: Optional[tuple[int, int]] = None
    # 展開したブロックの上限（視聴者の頼みは VIEWER_MAX_BLOCKS）
    max_blocks: int = MAX_BLOCKS

    def __post_init__(self) -> None:
        import re

        if self.anchor == BuildAnchor.MAP and not self.cell:
            raise ValueError("anchor map needs a cell of the map (e.g. C7)")
        if not re.fullmatch(r"[a-z0-9_]{1,32}", self.name):
            raise ValueError(f"name must be 1-32 of a-z, 0-9, _, got {self.name!r}")
        if not self.shapes:
            raise ValueError("a design needs at least one shape")
        if len(self.shapes) > self.MAX_SHAPES:
            raise ValueError(f"at most {self.MAX_SHAPES} shapes, got {len(self.shapes)}")
        for shape in self.shapes:
            for x, y, z in (shape.start, shape.end):
                if min(x, y, z) < 0:
                    raise ValueError(f"coordinates must not be negative: {shape.start}-{shape.end}")
                if x >= self.MAX_SIDE or z >= self.MAX_SIDE or y >= self.MAX_HEIGHT:
                    raise ValueError(
                        f"the build must fit in {self.MAX_SIDE}x{self.MAX_HEIGHT}x{self.MAX_SIDE} "
                        f"(x, y, z < {self.MAX_SIDE}, {self.MAX_HEIGHT}, {self.MAX_SIDE}), "
                        f"got {(x, y, z)}"
                    )
        blocks = self.blocks()
        if not any(b.kind not in (BlockKind.AIR,) for b in blocks):
            raise ValueError("a design needs at least one block to place")
        if len(blocks) > self.max_blocks:
            raise ValueError(
                f"the design expands to {len(blocks)} blocks, over {self.max_blocks}: make it "
                "smaller or hollow (walls and roof only)"
            )

    def blocks(self) -> tuple[PlannedBlock, ...]:
        """
        置ける順に展開する: ブロック（下の層から、外周から内側へ）、空けるマス（上から。家の
        外壁に重なる面は最後）、ドア。

        空けるのはブロックの後: 増築で家の壁の入口を先に空けると、部屋ができるまで家に穴が
        開いたままになる（夜に敵が入る）。予定地の土や石は、置くときに掘ってから置く。
        """
        grid: dict[tuple[int, int, int], BlockKind] = {}
        for shape in self.shapes:
            if shape.kind == ShapeKind.DOOR:
                x, y, z = shape.start
                grid[(x, y, z)] = BlockKind.DOOR
                grid.pop((x, y + 1, z), None)  # 上のマスはドアの上半分になる
                continue
            kind = BlockKind.AIR if shape.kind == ShapeKind.CLEAR else shape.block
            assert kind is not None
            for cell in shape.cells():
                grid[cell] = kind
        if not grid:
            return ()
        # ドアの上半分のマスに、後の形が何かを置いていたら外す
        for (x, y, z), kind in list(grid.items()):
            if kind == BlockKind.DOOR:
                grid.pop((x, y + 1, z), None)
        max_x = max(c[0] for c in grid)
        max_z = max(c[2] for c in grid)

        def edge(c: tuple[int, int, int]) -> int:
            return min(c[0], max_x - c[0], c[2], max_z - c[2])

        def on_home_face(c: tuple[int, int, int]) -> bool:
            # 増築で家の外壁に重なる面（そこを空けると家に穴が開く）
            return {
                BuildAnchor.HOME_EAST: c[0] == 0,
                BuildAnchor.HOME_WEST: c[0] == max_x,
                BuildAnchor.HOME_SOUTH: c[2] == 0,
                BuildAnchor.HOME_NORTH: c[2] == max_z,
            }.get(self.anchor, False)

        clears = sorted(
            (c for c, k in grid.items() if k == BlockKind.AIR),
            key=lambda c: (on_home_face(c), -c[1]),
        )
        solids = sorted(
            (c for c, k in grid.items() if k not in (BlockKind.AIR, BlockKind.DOOR)),
            key=lambda c: (c[1], edge(c), c[0], c[2]),
        )
        doors = sorted(c for c, k in grid.items() if k == BlockKind.DOOR)
        return tuple(PlannedBlock(x, y, z, grid[(x, y, z)]) for x, y, z in solids + clears + doors)

    def size(self) -> tuple[int, int, int]:
        """(幅 x, 高さ y, 奥行き z)。"""
        cells = [(b.x, b.y, b.z) for b in self.blocks()]
        return tuple(max(c[i] for c in cells) + 1 for i in range(3))  # type: ignore[return-value]

    def material_counts(self) -> dict[BlockKind, int]:
        """置くブロックの種類ごとの数（空けるマスは数えない）。"""
        counts: dict[BlockKind, int] = {}
        for b in self.blocks():
            if b.kind != BlockKind.AIR:
                counts[b.kind] = counts.get(b.kind, 0) + 1
        return counts


@dataclass(frozen=True)
class Candidate:
    """ブリッジが今すぐ実行できる具体的な行動（例: 3,70,5 の oak_log を掘る）。"""

    action_id: str
    description: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GameObservation:
    """
    判断のための、ゲームの簡潔なスナップショット。

    `state` はブリッジによる世界の JSON の要約。型のあるフィールドは、
    アプリケーションのロジックが読むもの。
    """

    state: dict
    candidates: tuple[Candidate, ...]
    health: float
    food: int
    needs: tuple[str, ...] = ()
    goal: Optional[GoalStatus] = None
    time_phase: str = "day"  # day / dusk / night / dawn
    has_plan: bool = False
    house_complete: bool = False
    has_home: bool = False
    inside_home: bool = False
    bed_in_home: bool = False
    # まだできていない建物（Gemini が設計したもの）の名前
    unfinished_builds: tuple[str, ...] = ()
    busy: bool = False  # ブリッジが行動か反射を実行している
    # ワールドの何日目か（寝て飛ばした夜も数える。まだ時刻がなければ None）
    day: Optional[int] = None
    # 地下にいる（頭の上がふさがっていて海面より下）: 夜でも家に帰らなくてよい
    underground: bool = False


@dataclass(frozen=True)
class TownStage:
    """
    街の段階: 条件が世界で満たされたら済む。

    `unresolved` は、配信者がまだできないか判定できない部分（能力が足されるのを
    待つ。それまで段階は終えられない）。

    Raises:
        ValueError: 題名がないか、やることがないか、世界から判定できない条件が
            あるとき。
    """

    title: str
    why: str
    conditions: tuple[GoalSpec, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """段階を確かめる。"""
        if not self.title:
            raise ValueError("a town stage needs a title")
        if not self.conditions and not self.unresolved:
            raise ValueError(f"town stage {self.title} has nothing to do")
        for c in self.conditions:
            if c.predicate not in PLANNABLE_CONDITIONS:
                raise ValueError(f"{c.predicate.value} cannot be a condition of a town stage")

    @property
    def ready(self) -> bool:
        """すべての部分を今実行でき、判定できるか。"""
        return not self.unresolved


@dataclass(frozen=True)
class TownDefinition:
    """大目標の街がどんなものか（配信で言う）と、順に並べた段階。"""

    text: str
    stages: tuple[TownStage, ...]

    def __post_init__(self) -> None:
        """定義を確かめる。"""
        if not self.text:
            raise ValueError("the town needs a definition")
        if not self.stages:
            raise ValueError("the town needs at least one stage")


HERE_SITE = "here"  # 最初の家の場所の候補地（minecraft-bridge/src/survey.mjs）


@dataclass(frozen=True)
class TownSite:
    """
    配信者が選んだ街の場所（決めたこと）。候補地の数字（調べた事実）はブリッジが持つ。

    `site_id` は候補地の id（here: 最初の家の場所、N / NE / ...: その方角）。

    Raises:
        ValueError: 候補地、理由、名前のどれかがないとき。
    """

    site_id: str
    x: int
    z: int
    reason: str
    name: str

    def __post_init__(self) -> None:
        """決めたことを確かめる。"""
        for label in ("site_id", "reason", "name"):
            if not getattr(self, label):
                raise ValueError(f"the town site needs a {label}")

    @property
    def moving(self) -> bool:
        """最初の家から引っ越すか。"""
        return self.site_id != HERE_SITE


MAX_NOTE_CHARS = 80
NOTE_LIFETIME_DAYS = 3  # ゲーム内の日。keep で延びる


class NoteKind(str, Enum):
    """
    自分のメモの種類。

    lesson: やってみて分かったこと（それが起きた小目標を根拠に持つ）
    viewer: 視聴者について覚えておくこと（その視聴者の名前を持つ。頼みや指示は書かない）
    plan: 先のためのメモ
    """

    LESSON = "lesson"
    VIEWER = "viewer"
    PLAN = "plan"


@dataclass(frozen=True)
class Note:
    """
    配信者が自分で書き残したメモ（確かめていない。世界の事実ではない）。

    `about` は lesson ならその小目標（終わり方も含めた文）、viewer なら視聴者の名前。
    `expires_day` の日が終わるまで残る。

    Raises:
        ValueError: 本文が空か長すぎる、種類に要る根拠がないとき。
    """

    id: str
    kind: NoteKind
    text: str
    written_day: int
    expires_day: int
    about: str = ""

    def __post_init__(self) -> None:
        """本文と根拠を確かめる。"""
        if not self.text.strip():
            raise ValueError("a note needs text")
        if len(self.text) > MAX_NOTE_CHARS:
            raise ValueError(f"a note is one sentence of at most {MAX_NOTE_CHARS} characters")
        if self.kind == NoteKind.LESSON and not self.about:
            raise ValueError("a lesson note needs the small goal it was learned from")
        if self.kind == NoteKind.VIEWER and not self.about:
            raise ValueError("a viewer note needs the viewer's name")
        if self.kind == NoteKind.PLAN and self.about:
            raise ValueError("a plan note has no small goal or viewer")


@dataclass(frozen=True)
class Activity:
    """
    配信者が今していることとその理由: 目標の決定、実況、チャットの返答がすべて見る
    唯一の見え方。これで、言うこととすることが合う。大目標から下へ: 順に並べた
    中目標（未完了のものが先、次に最近終わったもの）、小目標、ゲーム。
    """

    mission: Optional[Mission] = None
    town: Optional[TownDefinition] = None
    site: Optional[TownSite] = None  # 選んだ街の場所（決めたこと）
    town_stage: int = 0  # 済んだ段階の数
    stage_met: tuple[GoalSpec, ...] = ()  # 今の段階の条件のうち、これまでに満たしたもの
    mid_goals: tuple[MidGoal, ...] = ()
    goal: Optional[Goal] = None
    observation: Optional[GameObservation] = None
    recent_goals: tuple[GoalOutcome, ...] = ()
    notes: tuple[Note, ...] = ()  # 自分のメモ（確かめていない）
    intent: str = ""  # 道具で操作しているとき、今やろうとしていること（配信者が道具に添えた 1 文）
    screen_note: Optional["ScreenNote"] = None  # 画面で見たこと（確かめていない）


@dataclass(frozen=True)
class ActionDecision:
    """選択器が選んだ行動と、その確信度（0.0 - 1.0）。"""

    action_id: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    """ブリッジの行動を 1 つ実行した結果。"""

    action_id: str
    ok: bool
    result: str
    seconds: float
    # 襲われて止まった（ダメージ、反射）: 小目標の失敗に数えない（docs/design/28 §3）
    interrupted: bool = False

    @property
    def cut_by_attack(self) -> bool:
        """襲われて止まったか（印か、ブリッジの結果の文から。道具モードの結果は文だけ）。"""
        return self.interrupted or self.result.startswith(INTERRUPTED_PREFIXES)

    @property
    def missed_by_chance(self) -> bool:
        """確率で落ちる物（草から種、葉から苗木）が今回は落ちなかった（やり方の失敗ではない）。"""
        return self.ok and CHANCE_MISS in self.result


CHANCE_MISS = "nothing dropped this time"
INTERRUPTED_PREFIXES = (
    "failed: interrupted: took damage",
    "failed: reflex: ",
    "not started: the reflex",
)


# =============================================================================
# 道具での操作と見張り（docs/design/21_tool_control.md）
# =============================================================================

MAX_WATCH_QUESTIONS = 3
MAX_WATCH_QUESTION_CHARS = 120


class WatchAction(str, Enum):
    """見張りの質問の答えが「はい」のときに起きること。止める・起こす・終わったかも、だけ。"""

    STOP = "stop"  # 行動を止める（続けても意味がない、危ない）
    WAKE = "wake"  # 行動を止めて、配信者に考え直させる（何かが起きた）
    MAYBE_DONE = (
        "maybe_done"  # 行動を止める。完了はワールドの述語で確かめる（Jev は完了を判定しない）
    )


@dataclass(frozen=True)
class WatchQuestion:
    """
    道具の実行中に高頻度の判断モデル（Jev）が答える、はい/いいえの質問。配信者（Gemini）が
    道具を呼ぶときに書く。

    Raises:
        ValueError: 質問が空か長すぎるとき。
    """

    question: str
    on_yes: WatchAction

    def __post_init__(self) -> None:
        """質問を検証する。"""
        if not self.question.strip():
            raise ValueError("watch question must not be empty")
        if len(self.question) > MAX_WATCH_QUESTION_CHARS:
            raise ValueError(f"watch question is longer than {MAX_WATCH_QUESTION_CHARS} characters")


@dataclass(frozen=True)
class ToolCall:
    """
    配信者が選んだ道具の呼び出し: 道具の名前と引数、今やろうとしていること、見張りの質問。

    Raises:
        ValueError: 名前が空か、見張りの質問が多すぎるとき。
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    watch: tuple[WatchQuestion, ...] = ()

    def __post_init__(self) -> None:
        """呼び出しを検証する。"""
        if not self.name:
            raise ValueError("tool name must not be empty")
        if len(self.watch) > MAX_WATCH_QUESTIONS:
            raise ValueError(f"at most {MAX_WATCH_QUESTIONS} watch questions")

    def describe(self) -> str:
        """ログと記録のための 1 行（例: dig(x=3, y=70, z=4)）。"""
        args = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.name}({args})"


class FastQuestionKind(str, Enum):
    """高頻度の判断モデルに聞ける質問の形。"""

    YES_NO = "yes_no"
    CHOICE = "choice"
    SCORE = "score"


@dataclass(frozen=True)
class FastQuestion:
    """
    高頻度の判断モデルへの質問 1 つ（モデルに依存しない形）。

    - YES_NO: `criteria` は None か {"yes": 説明, "no": 説明}
    - CHOICE: `criteria` は {選択肢: 説明}
    - SCORE: `criteria` は 0 から順の段階の説明のリスト
    """

    name: str
    kind: FastQuestionKind
    instructions: str
    criteria: Any = None


@dataclass(frozen=True)
class FastAnswer:
    """高頻度の判断モデルの答え 1 つ: はい/いいえ（bool）、選んだもの（str）、段階（float）。"""

    name: str
    value: Any
    confidence: float


@dataclass(frozen=True)
class FastVerdict:
    """1 回の呼び出しの答えと、その費用（ログと記録用）。"""

    answers: dict[str, FastAnswer]
    elapsed_ms: int
    input_tokens: Optional[int] = None


@dataclass(frozen=True)
class ToolOutcome:
    """
    道具を 1 つ呼んだ結果。`result` は結果の文（調べものなら JSON）。`refused` はブリッジが
    実行する前に断ったとき（安全の制約、引数が世界と合わない）で、理由は `result` にある。
    """

    call: ToolCall
    ok: bool
    result: str
    seconds: float
    refused: bool = False
    stopped_by: Optional[str] = None  # 見張りが止めたとき、その質問と答え

    def as_action_result(self) -> ActionResult:
        """ステップの記録（停滞・予算の数え方）に使う形。"""
        return ActionResult(
            action_id=self.call.describe(), ok=self.ok, result=self.result, seconds=self.seconds
        )


# =============================================================================
# 技（docs/design/22_skills.md）
# =============================================================================


@dataclass(frozen=True)
class SkillInfo:
    """覚えた技の一覧の 1 件（一番新しい版と、全部の版の合計）。"""

    name: str
    description: str
    version: int
    params: dict[str, Any] = field(default_factory=dict)
    expects: dict[str, Any] = field(default_factory=dict)
    verified: bool = False  # 一番新しい版が 1 回でも成功した
    uses: int = 0
    successes: int = 0
    failures: int = 0
    last_failure: Optional[str] = None
    last_good_version: Optional[int] = None  # 一番新しい版が未成功のとき、前に成功した版
    code: str = ""  # 直すときだけ読む（一覧には入らない）

    def describe(self) -> str:
        """プロンプト用の 1 行。"""
        good = self.last_good_version
        mark = (
            ""
            if self.verified
            else f"（試し中。v{good} は成功した）"
            if good and good != self.version
            else "（試し中）"
        )
        failure = f"、最後の失敗: {self.last_failure}" if self.last_failure else ""
        return (
            f"{self.name} v{self.version}{mark}: {self.description}"
            f"（引数 {list((self.params.get('properties') or {}).keys())}、"
            f"成功 {self.successes}/{self.uses}{failure}）"
        )


@dataclass(frozen=True)
class SkillDraft:
    """Gemini が書いた技（保存する前）。"""

    description: str
    params: dict[str, Any]
    expects: dict[str, Any]
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "params": self.params,
            "expects": self.expects,
            "code": self.code,
        }


@dataclass(frozen=True)
class SkillRun:
    """技を 1 回実行した結果（成功は、技の done() と expects の世界での判定の両方）。"""

    name: str
    version: int
    ok: bool
    ended: str  # done | fail | error | stopped
    summary: str = ""
    reason: str = ""
    expects_lines: tuple[str, ...] = ()
    calls: tuple[dict[str, Any], ...] = ()
    log: tuple[str, ...] = ()
    seconds: float = 0.0
    learned: bool = False  # この実行で初めて成功した

    def describe(self) -> str:
        """次の道具の選択に見せる結果（失敗なら理由、記録、最後の道具）。"""
        head = f"skill {self.name} v{self.version}"
        if self.ok:
            return f"{head} succeeded: {self.summary}"
        parts = [f"{head} failed ({self.ended}): {self.reason}"]
        if self.expects_lines:
            parts.append("expects: " + "; ".join(self.expects_lines))
        if self.log:
            parts.append("log: " + " | ".join(self.log[-5:]))
        if self.calls:
            last = self.calls[-3:]
            parts.append(
                "last calls: "
                + " | ".join(
                    f"{c.get('tool')}({json.dumps(c.get('args', {}), ensure_ascii=False)})"
                    f"={'ok' if c.get('ok') else 'failed'} {c.get('result', '')}"
                    for c in last
                )
            )
        return "; ".join(parts)


# =============================================================================
# 配信の画面（docs/design/23_screen_vision.md）
# =============================================================================


@dataclass(frozen=True)
class Screenshot:
    """
    配信の画面（ゲームのソース）の 1 枚。

    Raises:
        ValueError: データが空か、画像の形式でないとき。
    """

    data: bytes = field(repr=False)
    mime_type: str
    taken_at: datetime = field(default_factory=_utc_now)
    width: Optional[int] = None

    def __post_init__(self) -> None:
        """画像を検証する。"""
        if not self.data:
            raise ValueError("screenshot data must not be empty")
        if not self.mime_type.startswith("image/"):
            raise ValueError(f"not an image type: {self.mime_type}")


@dataclass(frozen=True)
class ScreenNote:
    """
    定期の見直しで配信者（Gemini）が画面について書いたこと。画像の解釈で、確かめていない
    （世界の状態とは別に扱う。完了の判定には使わない）。
    """

    seen: str
    concern: str = ""
    matches_goal: bool = True
    taken_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class ChatComment:
    """
    配信のチャットのコメント 1 つ（Twitch など）。

    Raises:
        ValueError: 名前か本文が空のとき。
    """

    user_name: str
    message: str
    user_id: Optional[str] = None
    received_at: datetime = field(default_factory=_utc_now)
    # ログイン名（小文字。表示名とは違うことがある）。自分や決めたボットのコメントを見分ける
    login: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.user_name.strip():
            raise ValueError("a chat comment needs a user name")
        if not self.message.strip():
            raise ValueError("a chat comment needs a message")
