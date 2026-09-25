"""ドメイン層の値オブジェクト。"""

from __future__ import annotations

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
    - BUILT: 家のプランのブロックがすべて置かれている
    - PLACED: アイテムがどこかに置かれている（家の中のベッド）
    - AT_HOME: 家の中にいて、扉が閉まっている
    - THROUGH_NIGHT: 夜が明けた（家の中にいたか、寝ていた）
    - EXPLORED: 目標を設定した場所から `distance` ブロック離れた
    - CLEARED: 扉の近くで待つ敵対モブがいない（昼だけ: 外に出て戦う）
    - SURVEYED: 街の候補地を `count` か所調べた（家を中心に、ブリッジが地形を数字にする）
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

    def __post_init__(self) -> None:
        """述語に要る引数を確かめる。"""
        if self.dig_depth is not None and self.dig_depth < 0:
            raise ValueError(f"dig_depth must not be negative, got {self.dig_depth}")
        if self.predicate in (GoalPredicate.HAVE, GoalPredicate.STORED):
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
        for key in ("item", "count", "where", "distance", "dig_depth"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    def describe(self) -> str:
        """プロンプトとログ用の短い形。例: have(planks, 12)。"""
        args = [str(v) for v in (self.item, self.count, self.where, self.distance) if v is not None]
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
    """家のプランが使うブロックの種類（木の種類は問わない）。"""

    PLANKS = "planks"
    LOG = "log"
    DOOR = "door"


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
    busy: bool = False  # ブリッジが行動か反射を実行している
    # ワールドの何日目か（寝て飛ばした夜も数える。まだ時刻がなければ None）
    day: Optional[int] = None


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
