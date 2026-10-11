"""ドメイン層の値オブジェクト。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Collection, Iterator, Mapping, Optional


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
    # 言った時刻。視聴者のコメントは届いた時刻（返事を書き終えた時刻ではない）
    timestamp: datetime = field(default_factory=_utc_now)
    # 返答（RESPONSE）が誰のコメントへのものか
    addressed_to: str = ""

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
        received_at: Optional[datetime] = None,
    ) -> ConversationMessage:
        """視聴者が送ったチャットのメッセージを作る（届いた時刻が分かれば received_at に）。"""
        return cls(
            role=MessageRole.VIEWER,
            message_type=MessageType.CHAT,
            content=content,
            speaker_name=user_name,
            speaker_id=user_id,
            **({"timestamp": received_at} if received_at is not None else {}),
        )

    @classmethod
    def from_streamer(
        cls, content: str, message_type: MessageType, addressed_to: str = ""
    ) -> ConversationMessage:
        """AI の配信者が話したメッセージを作る（返答なら、誰のコメントへのものかを addressed_to に）。"""
        return cls(
            role=MessageRole.STREAMER,
            message_type=message_type,
            content=content,
            addressed_to=addressed_to,
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
    # チャットへの返答のとき: 今の時刻、返事をするコメントが届いた時刻、ほかに届いてまだ返事していない
    # コメント（会話の時系列を、経った時間で見せる）
    now: Optional[datetime] = None
    comment_received_at: Optional[datetime] = None
    waiting_comments: tuple[ConversationMessage, ...] = ()


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
    - LEVELED: `where`（home か x,y,z）のまわり半径 `distance` の地面がその高さにそろっている
      （整地）。`demolish` なら、置いた物・保護された家や建物・木・畑も壊してそろえる
    - CUSTOM: `name` の判定（Gemini が書いた JS。ブリッジが世界を読んで動かす）が成り立っている
      （組み込みの述語で書けない頼み。docs/design/42_custom_conditions.md）
    - FACILITY: `name` の施設（設計した畑・植林場、docs/design/47 §7）のマスがすべて済んでいる
      （畑は耕してまいた、植林場は苗木を植えた）。`started` なら、全マスの 1/4 が済んでいれば成り立つ
      （残りは、育ったものから種・苗木を回収して埋める。docs/design/74）
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
    LEVELED = "leveled"
    SURVEYED = "surveyed"
    PLANTED = "planted"
    FARMED = "farmed"
    CUSTOM = "custom"
    FACILITY = "facility"


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
    # built の建物の名前（None: 家）、custom の判定の名前、facility の施設の名前
    name: Optional[str] = None
    # facility だけ: 全マスでなく、全マスの 1/4 が済んでいれば成り立つ（定石の `ready` に使う）
    started: bool = False
    # leveled だけ: 置いた物・保護された家や建物・木・畑も壊してそろえる（水・採掘場・壊せない所は残す）
    demolish: bool = False

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
        if self.predicate == GoalPredicate.LEVELED and not self.where:
            raise ValueError("leveled needs where (home or x,y,z)")
        if self.predicate == GoalPredicate.CUSTOM and not self.name:
            raise ValueError("custom needs the name of its check")
        if self.predicate == GoalPredicate.FACILITY and not self.name:
            raise ValueError("facility needs the name of the facility")
        if self.started and self.predicate != GoalPredicate.FACILITY:
            raise ValueError("started is only for facility")
        if self.demolish and self.predicate != GoalPredicate.LEVELED:
            raise ValueError("demolish is only for leveled")
        radius = (GoalPredicate.EXPLORED, GoalPredicate.LIT, GoalPredicate.LEVELED)
        if self.predicate in radius and (self.distance is None or self.distance < 1):
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
        if self.started:
            out["started"] = True
        if self.demolish:
            out["demolish"] = True
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
        if self.started:
            args.append("started")
        if self.demolish:
            args.append("demolish")
        return f"{self.predicate.value}({', '.join(args)})"


@dataclass(frozen=True)
class Goal:
    """
    小目標: LLM が決めた今の方向。行動選択器はその中で動く。

    `objective_id` は、それが役立つ大目標（None: 生存のため。例えば夜を越すことは、
    大目標を待ってくれない）。
    """

    spec: GoalSpec
    reason: str = ""
    objective_id: Optional[str] = None
    set_at: datetime = field(default_factory=_utc_now)
    # 前の小目標が失敗で終わったとき: その原因の分析と、この小目標のやり方の助言（選択器が読む。
    # docs/design/27）
    diagnosis: str = ""
    advice: str = ""
    # この小目標を決めたときの、今の状態での見直し（docs/design/31）
    review: str = ""
    # この小目標で思い出した教訓の文（選択器と道具のプロンプトが読む。docs/design/35）
    lessons: tuple[str, ...] = ()
    # 小目標の束（docs/design/44）: spec と一緒に追う条件と、進め方
    together: tuple[GoalSpec, ...] = ()
    approach: str = ""

    def __post_init__(self) -> None:
        check_bundle(self.spec, self.together)

    @property
    def specs(self) -> tuple[GoalSpec, ...]:
        """束の条件すべて（先頭が spec）。"""
        return (self.spec, *self.together)

    def describe(self) -> str:
        """ログとプロンプト用の短い形。束なら + でつなぐ。"""
        return " + ".join(s.describe() for s in self.specs)

    def unmet(self, status: Optional["GoalStatus"]) -> tuple[GoalSpec, ...]:
        """
        束の条件のうち、まだ済んでいないもの（ブリッジの判定から。束でないか判定がなければ全部）。
        先頭（spec）は決めたときのまま動かないので、先に済んだ条件を「手に入れる」と見せない
        （かまどを持っているのに、小目標がかまどを手に入れる、と出た）。全部済みなら全部を返す。
        """
        if status is None or not status.members:
            return self.specs
        met = {m.label for m in status.members if m.met}
        left = tuple(s for s in self.specs if replace(s, dig_depth=None).describe() not in met)
        return left or self.specs


@dataclass(frozen=True)
class MaterialTree:
    """
    物を 1 から作るとき、材料の木に出てくるアイテム（持ち物を使わない静的な木。ブリッジのソルバーが作る）。
    `root` は物そのもの（グループなら中身）、`all` は木のすべて（道具や作業台も）。
    """

    root: tuple[str, ...]
    all: tuple[str, ...]
    # 持ち物・チェスト・作業台を数えたうえで、まだ足りない物（None: 分からない）。レシピが直接使う材料を並べ、
    # 持っているぶんを引いた残り。足りない物がなければ、その物を作る手だけが残る
    missing: Optional[tuple[str, ...]] = None


@dataclass(frozen=True)
class ConditionStatus:
    """ブリッジが判定した大目標の条件（目標としては設定しない）。"""

    spec: GoalSpec
    met: bool
    lines: tuple[str, ...] = ()
    impossible: tuple[str, ...] = ()  # 配信者が今できることでは手に入らないもの
    # have の条件で、集めに出る材料があるとき、その次の一歩（have の小目標。docs/design/50 §13）。なければ None
    next_step: Optional[GoalSpec] = None
    # have の条件そのものが素材の採集（掘る・狩る・チェストから出す）: 小目標と同じ 1 回の遠征で、中目標にする
    # 意味が薄い（何のための素材かを中目標にする。docs/design/50 §14）
    gather: bool = False
    # have / placed / stored の条件で、その物の材料の木（`check(materials=True)` のときだけ）
    materials: Optional[MaterialTree] = None


# 世界の状態だけから判定できる: 大目標の完了条件になれる
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
        GoalPredicate.LEVELED,
        GoalPredicate.CUSTOM,
        GoalPredicate.FACILITY,
    }
)
# LLM が大目標や街の段階に書ける条件。調査は、街の場所を決める前にコードが足すだけ
PLANNABLE_CONDITIONS = CONDITION_PREDICATES - {GoalPredicate.SURVEYED}
_SURVIVAL_PREDICATES = frozenset(
    {GoalPredicate.THROUGH_NIGHT, GoalPredicate.AT_HOME, GoalPredicate.CLEARED}
)


# 小目標の束（docs/design/44）: 一度に追う条件の数の上限
MAX_BUNDLE = 4


def check_bundle(lead: GoalSpec, together: tuple[GoalSpec, ...]) -> None:
    """
    束の形を確かめる: 合わせて MAX_BUNDLE まで、すべて世界から判定できる条件、同じ条件は 1 つ。

    Raises:
        ValueError: 束にできないとき
    """
    if not together:
        return
    specs = (lead, *together)
    if len(specs) > MAX_BUNDLE:
        raise ValueError(f"a bundle holds at most {MAX_BUNDLE} conditions, got {len(specs)}")
    for spec in specs:
        if spec.predicate not in CONDITION_PREDICATES:
            raise ValueError(
                f"{spec.predicate.value} cannot be in a bundle (only conditions judged from the "
                "world; survival goals and explored are chased alone)"
            )
    if len(set(specs)) != len(specs):
        raise ValueError("the same condition is twice in the bundle")


def is_survival(spec: GoalSpec) -> bool:
    """小目標が配信者を生き延びさせるためのものか（どの大目標のためでなくてもよい）。"""
    return spec.predicate in _SURVIVAL_PREDICATES or (
        spec.predicate == GoalPredicate.HAVE and spec.item == "food"
    )


@dataclass(frozen=True)
class Mission:
    """全体を貫く唯一の目標: 設定で決め、コメントでは変わらない。"""

    text: str


class ObjectiveState(str, Enum):
    """大目標の状態。"""

    PENDING = "pending"
    DONE = "done"
    DROPPED = "dropped"


class ObjectiveKind(str, Enum):
    """
    大目標の種類。コードが条件から決める（docs/design/50 §3）。build と facility は
    成果物だけを条件にし、資材・整地・内装は中目標に置く。
    """

    BUILD = "build"
    FACILITY = "facility"
    INFRASTRUCTURE = "infrastructure"


_DELIVERABLE_KINDS = {
    GoalPredicate.BUILT: ObjectiveKind.BUILD,
    GoalPredicate.FACILITY: ObjectiveKind.FACILITY,
}


def objective_kind(conditions: tuple[GoalSpec, ...]) -> ObjectiveKind:
    """
    条件から大目標の種類を決める。

    Raises:
        ValueError: 建てる・施設と、ほかの条件が混ざっているとき（材料は中目標に書く）
    """
    kinds = {
        _DELIVERABLE_KINDS[c.predicate] for c in conditions if c.predicate in _DELIVERABLE_KINDS
    }
    if not kinds:
        return ObjectiveKind.INFRASTRUCTURE
    if len(kinds) > 1 or any(c.predicate not in _DELIVERABLE_KINDS for c in conditions):
        raise ValueError(
            "an objective that builds (built) or lays out a facility (facility) holds only that "
            "one kind of condition; put the materials, leveling and furnishing in its jobs"
        )
    return next(iter(kinds))


@dataclass(frozen=True)
class StartingObjective:
    """最初のプランに入っている大目標（設定 `minecraft.mission.objectives`）。目標のリセットで戻す先。"""

    title: str
    conditions: tuple[GoalSpec, ...]
    reason: str = ""


@dataclass(frozen=True)
class PlaybookStep:
    """
    定石の 1 段: 街づくりの土台として、この順にやる（設定 `minecraft.playbook`。docs/design/74）。
    条件がすべて満たされたら済み。畑なら施設と最初の収穫、というように、作って回るところまでを 1 段にする
    ので、建てる・施設の条件にほかの条件が混ざってよい（街の段階と同じ）。
    `ready` は、その段の設営ができて育つのを待つだけの状態の条件（畑なら施設ができた。施設は `started`
    で、全マスの 1/4 が済んだ時点にしてよい）。満たしている間、
    その段は「育成中」で、次の段を同時に進めてよい（ただし 2 段まで。3 段目は、2 つとも済んでから）。
    `keep` は、済んだあとも持ち続けるもの（武器）の段: 条件が崩れたら（死んで失った）また今の段にする。
    作ったものが残る段（畑）は、壊れても作り直さない（済みのまま）。家は `keep` で、壊れたら直す（docs/design/88）。

    Raises:
        ValueError: 名前か題名か条件がないとき
    """

    name: str
    title: str
    conditions: tuple[GoalSpec, ...]
    reason: str = ""
    ready: tuple[GoalSpec, ...] = ()
    keep: bool = False

    def __post_init__(self) -> None:
        """形を確かめる。"""
        if not self.name or not self.title:
            raise ValueError("a playbook step needs a name and a title")
        if not self.conditions:
            raise ValueError(f"playbook step {self.name} needs at least one condition")
        missing = [
            c.describe()
            for c in self.ready
            if replace(c, started=False) not in self.conditions
        ]
        if missing:
            raise ValueError(
                f"playbook step {self.name}: ready must be some of its conditions ({', '.join(missing)})"
            )
        for c in self.conditions:
            if c.predicate not in CONDITION_PREDICATES:
                raise ValueError(f"{c.predicate.value} cannot be a condition of playbook step {self.name}")


@dataclass(frozen=True)
class Supply:
    """
    1 つの物（松明か食べ物）の持ち方: 手持ちとチェストの合計が `low` を下回ったら集め、手に `carry` 個持つ。
    `stock` は、手持ちとチェストの合計で持っておきたい量（`carry` を超えるぶんは家のチェストに置く備蓄）。

    Raises:
        ValueError: 1 <= low <= carry <= stock でないとき
    """

    low: int
    carry: int
    stock: int

    def __post_init__(self) -> None:
        """数の関係を確かめる。"""
        if not 1 <= self.low <= self.carry <= self.stock:
            raise ValueError(
                f"supply needs 1 <= low <= carry <= stock, got {self.low}/{self.carry}/{self.stock}"
            )


@dataclass(frozen=True)
class SupplyTier:
    """
    ストックの段階（設定 `minecraft.supplies`。docs/design/84）: 生活インフラの整い方で、松明と食べ物を
    どれだけ持つかを変える。`after` の定石の段がすべて済んでいる、または `or_mine` で鉱山が設計されて
    いれば、その段階に着いている。着いている段階のうち、一覧で後ろのものが今の段階。

    Raises:
        ValueError: 名前がないとき
    """

    name: str
    torch: Supply
    food: Supply
    after: tuple[str, ...] = ()
    or_mine: bool = False

    def __post_init__(self) -> None:
        """名前を確かめる。"""
        if not self.name:
            raise ValueError("a supply tier needs a name")

    def reached(self, done: Collection[str], has_mine: bool) -> bool:
        """この段階に着いているか（定石で済んだ段の名前、鉱山が設計済みか）。"""
        return all(step in done for step in self.after) or (self.or_mine and has_mine)


@dataclass(frozen=True)
class Objective:
    """
    大目標: 最終目標への一歩。条件がすべて世界で満たされたら完了する。

    `requested_by` は頼んだ視聴者（None: 配信者自身のもの）。
    `suggested_by` は、配信者が納得して自分の大目標にした助言をくれた視聴者（頼みではないので、
    視聴者の上限と予算には数えない）。
    `stage` は、それが表す街の段階（None: 段階ではない）。
    `prepares_town` は、街の段階の前にやる準備（候補地の調査、選んだ場所への引っ越し）。
    段階と同じく、やめられない。
    `playbook` は、それが表す定石の段の名前（None: 定石ではない）。定石の大目標は、ほかのどの大目標より
    先にあり、動かせず、やめられない（docs/design/74）。
    `budget_used` は、そのために小目標が使ったステップの数（視聴者のものには予算がある）。

    Raises:
        ValueError: 条件がないか、世界から判定できない条件があるとき。
    """

    id: str
    title: str
    conditions: tuple[GoalSpec, ...]
    reason: str = ""
    requested_by: Optional[str] = None
    state: ObjectiveState = ObjectiveState.PENDING
    suggested_by: Optional[str] = None
    ended_because: str = ""
    budget_used: int = 0
    progress: tuple[str, ...] = ()  # 最後に判定したときの、条件の進み具合
    stage: Optional[int] = None
    prepares_town: bool = False
    playbook: Optional[str] = None
    # 視聴者の頼みのステップの予算（None: プランの既定。建物は大きさに合わせて広げる）
    budget: Optional[int] = None
    # Gemini が書いた、この大目標のための中目標（小目標の並び。済んだものも残る）。Jev が次を選ぶ
    # （docs/design/26、50 §4）
    jobs: tuple["Job", ...] = ()
    # 一度 LLM が計画（中目標と小目標）を書いたか（docs/design/50 §18）。失敗・行き詰まりで書き直す
    planned: bool = False

    def __post_init__(self) -> None:
        """条件を確かめる。"""
        if not self.title:
            raise ValueError("an objective needs a title")
        if not self.conditions:
            raise ValueError(f"objective {self.title} needs at least one condition")
        for c in self.conditions:
            if c.predicate not in CONDITION_PREDICATES:
                allowed = ", ".join(sorted(p.value for p in CONDITION_PREDICATES))
                raise ValueError(
                    f"{c.predicate.value} cannot be a condition of an objective; use {allowed}"
                )
        if self.stage is None and not self.prepares_town and self.playbook is None:
            objective_kind(self.conditions)  # 街の段階と準備は、段階の定義が条件を決める

    @property
    def kind(self) -> ObjectiveKind:
        """種類（条件から決まる）。街の段階の混ぜた条件は基盤整備として扱う。"""
        try:
            return objective_kind(self.conditions)
        except ValueError:
            return ObjectiveKind.INFRASTRUCTURE

    @property
    def open_jobs(self) -> tuple["Job", ...]:
        """まだ済んでいない中目標（順番どおり）。"""
        return tuple(s for s in self.jobs if s.state == JobState.PENDING)

    @property
    def done_jobs(self) -> tuple["Job", ...]:
        """済んだ中目標（一度満たしたら済みのまま）。"""
        return tuple(s for s in self.jobs if s.state == JobState.DONE)

    def closed(self) -> "Objective":
        """
        終わった大目標の、まだ済んでいない中目標を閉じる（大目標が済んだら、残りの中目標はやらなくてよい）。
        済んだ中目標はそのまま。
        """
        return replace(
            self,
            jobs=tuple(
                replace(s, state=JobState.CLOSED, ended_because="its objective ended")
                if s.state == JobState.PENDING
                else s
                for s in self.jobs
            ),
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
    # 小目標の束（docs/design/44）のとき、条件ごとの状態（先頭が lead）
    members: tuple["BundleMember", ...] = ()
    # 進める手が 1 つもなく、今は手に入らない部分があるとき、その理由（例: 畑がない。設計書 47 §15）
    dead_end: tuple[str, ...] = ()
    # ブリッジが今追っている小目標の名前（have(log, 16) の形）。再起動のあと、保存した小目標と同じか確かめる
    spec_label: str = ""


@dataclass(frozen=True)
class BundleMember:
    """小目標の束の条件 1 つの状態（label はブリッジが書いた条件の形。例: have(log, 16)）。"""

    label: str
    met: bool
    remaining: int


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
    MIN_WALL_HEIGHT = 3  # 家の設計の下限（LLM が決める家）
    MAX_WALL_HEIGHT = 4
    MIN_HUT_WALL_HEIGHT = 2  # 仮の小屋の壁（設計書 133）。屋根の下はドアと同じ 2 マス
    WALL_MATERIALS = (BlockKind.PLANKS, BlockKind.DIRT)  # 壁と屋根の材料

    name: str
    concept: str
    width: int  # x 方向
    depth: int  # z 方向
    wall_height: int
    door_side: Side
    door_offset: int
    corner_pillars: bool = False  # 四隅を板材ではなく原木にする
    wall_material: BlockKind = BlockKind.PLANKS  # 壁と屋根の材料（土は仮の小屋だけ）

    def __post_init__(self) -> None:
        """寸法と扉を検証する。"""
        for label, value in (("width", self.width), ("depth", self.depth)):
            if not self.MIN_SIDE <= value <= self.MAX_SIDE:
                raise ValueError(f"{label} must be {self.MIN_SIDE}-{self.MAX_SIDE}, got {value}")
        if not self.MIN_HUT_WALL_HEIGHT <= self.wall_height <= self.MAX_WALL_HEIGHT:
            raise ValueError(
                f"wall_height must be {self.MIN_HUT_WALL_HEIGHT}-{self.MAX_WALL_HEIGHT}, "
                f"got {self.wall_height}"
            )
        if self.wall_material not in self.WALL_MATERIALS:
            raise ValueError(
                f"wall_material must be one of {[m.value for m in self.WALL_MATERIALS]}, "
                f"got {self.wall_material.value}"
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
                    kind = BlockKind.LOG if pillar else self.wall_material
                    out.append(PlannedBlock(x, y, z, kind))

        for ring in range((min(self.width, self.depth) + 1) // 2):
            for x in range(ring, self.width - ring):
                for z in range(ring, self.depth - ring):
                    if x in (ring, self.width - 1 - ring) or z in (ring, self.depth - 1 - ring):
                        out.append(PlannedBlock(x, self.wall_height, z, self.wall_material))

        out.append(PlannedBlock(door_x, 0, door_z, BlockKind.DOOR))
        return tuple(out)

    def material_counts(self) -> dict[BlockKind, int]:
        """家全体に要る、種類ごとのブロックの数。"""
        counts: dict[BlockKind, int] = {}
        for b in self.blocks():
            counts[b.kind] = counts.get(b.kind, 0) + 1
        return counts


class JobState(str, Enum):
    """中目標（大目標の中の仕事）の状態。断念した中目標はリストから外す。"""

    PENDING = "pending"
    DONE = "done"
    DROPPED = "dropped"
    CLOSED = "closed"  # 大目標が終わった（済んだ・断念した）ので、やらなくてよくなった


# 1 つの大目標の、まだ済んでいない中目標の数の上限（Gemini の 6 つ + 視聴者・助言の分）
MAX_OPEN_JOBS = 8

# 中目標の下の小目標（計画。docs/design/50 §18）: 1 つの中目標に 8 つまで、前提は 2 段（2.1 の下に 2.1.1）、
# 1 つの小目標の前提は 3 つまで
MAX_STEPS = 8
MAX_STEP_NEEDS = 3
MAX_STEP_DEPTH = 2
# 計画の小目標に書ける条件: 世界から判定できて、ほかの手を介さないもの（custom は判定を書く手間が別に要る）
STEP_PREDICATES = PLANNABLE_CONDITIONS - {GoalPredicate.CUSTOM}


@dataclass(frozen=True)
class Step:
    """
    中目標の計画の小目標 1 つ: 条件と理由、その前にそろえる前提（`needs`）。

    前提は、この小目標がまだ済んでいないときだけ、先にやる（バケツを持っていれば、バケツの前提の
    鉄は飛ばす）。前提のさらに前提まで 2 段。条件は世界から判定できるもの（`/check`）。

    Raises:
        ValueError: 判定できない条件、前提が多すぎる・深すぎるとき。
    """

    spec: GoalSpec
    reason: str = ""
    needs: tuple["Step", ...] = ()

    def __post_init__(self) -> None:
        if self.spec.predicate not in STEP_PREDICATES:
            allowed = ", ".join(sorted(p.value for p in STEP_PREDICATES))
            raise ValueError(f"{self.spec.predicate.value} cannot be a step (use {allowed})")
        if len(self.needs) > MAX_STEP_NEEDS:
            raise ValueError(f"a step has at most {MAX_STEP_NEEDS} prerequisites")
        if self.depth > MAX_STEP_DEPTH:
            raise ValueError(f"prerequisites go {MAX_STEP_DEPTH} levels deep at most")

    @property
    def key(self) -> str:
        """済みを覚えるときの名前（条件の書き方）。"""
        return self.spec.describe()

    @property
    def depth(self) -> int:
        """前提の段数（前提がなければ 0）。"""
        return 1 + max(n.depth for n in self.needs) if self.needs else 0

    def walk(self) -> Iterator["Step"]:
        """前提を先に、この小目標を最後に、すべて順にたどる。"""
        for n in self.needs:
            yield from n.walk()
        yield self

    def describe(self) -> str:
        return self.spec.describe()



@dataclass(frozen=True)
class Job:
    """
    大目標のための中目標の 1 つ: 小目標の条件と、なぜそれか（ゴールボードと実況が使う）。
    条件は世界から判定できるもの（`/check` で済んだかを見る）。

    `id` はプランが付ける（Gemini が書いたときは空）。`state` は、一度 `/check` で満たしたら済み
    のまま（あとで材料を使って条件が崩れても戻さない。docs/design/50 §4）。`requested_by` は
    頼んだ視聴者、`suggested_by` は納得して採った助言をくれた視聴者（どちらも書き直しで消さない）。
    `budget_used` は視聴者の中目標がこれまでに使った小目標のステップ数（予算）、`progress` は最後に
    判定したときの進み具合の行、`ended_because` は断念した理由。

    Raises:
        ValueError: 世界から判定できない条件のとき。
    """

    spec: GoalSpec
    reason: str = ""
    # 小目標の束（docs/design/44）: 一緒に追う条件と、進め方
    together: tuple[GoalSpec, ...] = ()
    approach: str = ""
    id: str = ""
    state: JobState = JobState.PENDING
    requested_by: Optional[str] = None
    suggested_by: Optional[str] = None
    budget_used: int = 0
    progress: tuple[str, ...] = ()
    ended_because: str = ""  # 断念した理由
    # 計画（docs/design/50 §18）: 中目標の名前（日本語）と、それを達するための小目標の並び。
    # `steps_done` は一度満たした小目標の `Step.key`（あとで材料を使って条件が崩れても戻さない）
    title: str = ""
    steps: tuple[Step, ...] = ()
    steps_done: tuple[str, ...] = ()
    # 最後に世界と照合したとき、今満たしていた条件の名前（`GoalSpec.describe()`: 中目標の条件と計画の
    # 小目標）。済み（ラッチ）とは別: 材料を使えば外れる。表示だけが使う（docs/design/50 §19）
    met_now: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.steps) > MAX_STEPS:
            raise ValueError(f"a job has at most {MAX_STEPS} steps")
        for spec in (self.spec, *self.together):
            if spec.predicate not in PLANNABLE_CONDITIONS:
                raise ValueError(
                    f"{spec.predicate.value} cannot be a job (jobs must be conditions the "
                    "world can judge: have, stored, built, placed, lit)"
                )
        check_bundle(self.spec, self.together)

    @property
    def specs(self) -> tuple[GoalSpec, ...]:
        """束の条件すべて（先頭が spec）。"""
        return (self.spec, *self.together)

    def same_as(self, other: "Job") -> bool:
        """条件が同じか（順番は問わない）。書き直しで同じ中目標を見分ける。"""
        return {s.describe() for s in self.specs} == {s.describe() for s in other.specs}

    def step_keys(self) -> tuple[str, ...]:
        """計画の小目標すべての `Step.key`（重複なし、前提が先）。"""
        return tuple(dict.fromkeys(x.key for s in self.steps for x in s.walk()))

    def all_specs(self) -> tuple[GoalSpec, ...]:
        """中目標の条件（束も）と計画の小目標すべての条件（`describe()` で重複なし）。世界と照合する対象。"""
        seen: dict[str, GoalSpec] = {s.describe(): s for s in self.specs}
        for step in self.steps:
            for x in step.walk():
                seen.setdefault(x.key, x.spec)
        return tuple(seen.values())

    def pending_steps(self) -> tuple[Step, ...]:
        """まだ済みにしていない計画の小目標（重複なし）。判定に出す条件を集める。"""
        done = set(self.steps_done)
        seen: dict[str, Step] = {}
        for s in self.steps:
            for x in s.walk():
                if x.key not in done:
                    seen.setdefault(x.key, x)
        return tuple(seen.values())

    def next_step(self, met: Mapping[str, bool]) -> Optional[Step]:
        """
        計画の次の小目標（`met`: `Step.key` → 今満たしているか）。順に見て、済んでいない最初のもの。
        その前提に済んでいないものがあれば、前提を先にやる。全部済んでいれば None（中目標そのものが小目標）。
        """
        done = set(self.steps_done)

        def pick(step: Step) -> Optional[Step]:
            if step.key in done or met.get(step.key, False):
                return None
            for need in step.needs:
                found = pick(need)
                if found is not None:
                    return found
            return step

        for s in self.steps:
            found = pick(s)
            if found is not None:
                return found
        return None

    def describe(self) -> str:
        return " + ".join(s.describe() for s in self.specs)


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
        self._check_entrances(blocks)

    def _check_entrances(self, blocks: tuple[PlannedBlock, ...]) -> None:
        """
        ドアの奥に立てるか。ドアの内側の 2 マス（足と頭）が固いブロックで埋まっていると、外から入れない。
        hollow_box の底の面は固いので、床を y=0 に作ったうえで y=1 から箱を置くと、y=1 の板が床として
        残り、ドアの奥がふさがる（2026-10-10 storehouse: 内側の 25 マスが板のままで入れなかった）。
        """
        grid = {(b.x, b.y, b.z): b.kind for b in blocks}
        width = max(c[0] for c in grid) + 1
        depth = max(c[2] for c in grid) + 1

        def wall(x: int, y: int, z: int) -> bool:
            return grid.get((x, y, z), BlockKind.AIR) not in (BlockKind.AIR, BlockKind.DOOR)

        for (x, y, z), kind in grid.items():
            if kind != BlockKind.DOOR:
                continue
            # BuildPlan.doorOutward（ブリッジ）と同じ向き: 左右が壁なら南北、そうでなければ東西
            if wall(x - 1, y, z) or wall(x + 1, y, z):
                inside = (x, z + 1) if z < depth / 2 else (x, z - 1)
            else:
                inside = (x + 1, z) if x < width / 2 else (x - 1, z)
            for dy in (0, 1):
                cell = (inside[0], y + dy, inside[1])
                if wall(*cell):
                    raise ValueError(
                        f"the door at ({x},{y},{z}) opens onto solid {grid[cell].value} at {cell}: "
                        f"nobody can walk in (you stand at y={y}). Keep the two cells behind the "
                        f"door free: clear the inside of the room from y={y}. A hollow_box also has "
                        "a solid bottom face, so a box that starts at the floor level leaves a "
                        "second floor there"
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
    # 家の中にチェストがある（預ける先。なければ持ち物がいっぱいでも預けられない）
    chest_in_home: bool = False
    # 家のチェストの数（ブロック数。ラージは 2）と、記録からの空き。まとめる（デフラグ）と空くスロット数
    chest_blocks: Optional[int] = None
    chest_slots: Optional[int] = None
    chest_free: Optional[int] = None
    chest_saving: Optional[int] = None
    # 家にもう 1 つチェストを置ける場所がある（家の中が読み込まれていなければ None）
    chest_spot: Optional[bool] = None
    # まだできていない建物（Gemini が設計したもの）の名前
    unfinished_builds: tuple[str, ...] = ()
    # 建物と施設の呼び名（識別子, 呼び名）。視聴者に見せ、話す名前。判定には識別子を使う（設計書 160）
    titles: tuple[tuple[str, str], ...] = ()
    busy: bool = False  # ブリッジが行動か反射を実行している
    # ワールドの何日目か（寝て飛ばした夜も数える。まだ時刻がなければ None）
    day: Optional[int] = None
    # 地下にいる（頭の上がふさがっていて海面より下）: 夜でも家に帰らなくてよい
    underground: bool = False

    @property
    def home_distance_m(self) -> Optional[int]:
        """家の扉までの水平の距離（m）。家か位置が分からなければ None。"""
        door = (self.state.get("home") or {}).get("door") or {}
        me = (self.state.get("self") or {}).get("position") or {}
        try:
            return round(math.hypot(door["x"] - me["x"], door["z"] - me["z"]))
        except (KeyError, TypeError):
            return None


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
    # 未解決の部分を最後に書いた・書き直したときの条件の語彙の版（同じ語彙では書き直さない）
    tried_with: str = ""

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
    """最終目標の街がどんなものか（配信で言う）と、順に並べた段階。"""

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


TOWN_CELLS = 12  # 街の理想図の区画の数（1 辺。docs/design/47 §6）
ZONE_SIZE = 8  # 1 区画のブロック数（1 辺）
# 区画の用途。unusable（水・溶岩）と、今ある施設の区画はコードが決め、描き直しでも動かさない
ZONE_USES = (
    "home",
    "residence",
    "building",
    "farm",
    "tree_farm",
    "mine",
    "pen",
    "trap",
    "storage",
    "workshop",
    "plaza",
    "road",
    "free",
    "unusable",
)
# 施設の種類 → 区画の用途
FACILITY_USE = {
    "home": "home",
    "build": "building",
    "farm": "farm",
    "tree_farm": "tree_farm",
    "mine": "mine",
    "pen": "pen",
    "trap": "trap",
}


@dataclass(frozen=True)
class Zone:
    """
    街の理想図の区画 1 つ: id（列 A.. が西→東、行 1.. が北→南）、ワールドの範囲（両端を含む）、
    用途、そこに作る・ある施設の名前、ひとこと。`fixed` は今ある施設か使えない地形で、描き直しでも
    変えない。

    Raises:
        ValueError: 用途が ZONE_USES にないとき。
    """

    id: str
    min_x: int
    min_z: int
    max_x: int
    max_z: int
    use: str = "free"
    facility: str = ""
    note: str = ""
    fixed: bool = False

    def __post_init__(self) -> None:
        """用途を確かめる。"""
        if self.use not in ZONE_USES:
            raise ValueError(f"zone {self.id}: unknown use {self.use!r} (one of {ZONE_USES})")


@dataclass(frozen=True)
class TownMap:
    """
    街の理想図（マスターデータ。docs/design/47 §6）: 街の中心から TOWN_CELLS x TOWN_CELLS の区画。
    `concept` は全体の考え（1 文）、`stage` は描いたときの街の段階（段階が進んだら見直す）、
    `history` は描き直しの理由（新しいものが後ろ）。`clear` は、描き直したとき計画に合わないと決めた既存の
    建築（壊して更地にしてよい。設計書 167）。
    """

    center_x: int
    center_z: int
    zones: tuple[Zone, ...]
    concept: str = ""
    stage: int = 0
    history: tuple[str, ...] = ()
    clear: tuple[tuple[str, str], ...] = ()  # 計画に合わない既存の建築（施設の名前, 理由）。壊して更地にしてよい

    def zone(self, zone_id: str) -> Optional[Zone]:
        """id の区画（なければ None）。"""
        return next((z for z in self.zones if z.id == zone_id), None)

    def by_use(self) -> dict[str, list[Zone]]:
        """用途ごとの区画（区画の順）。"""
        out: dict[str, list[Zone]] = {}
        for z in self.zones:
            out.setdefault(z.use, []).append(z)
        return out


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


MAX_LESSON_CHARS = 80
MAX_LESSON_CONDITION_CHARS = 60
# 状況のキーと、取れる値（docs/design/35 §2.1。コードが観測から決める）
SITUATION_VALUES: dict[str, tuple[str, ...]] = {
    "time": ("day", "dusk", "night", "dawn"),
    "place": ("home", "underground", "outside"),
    "body": ("hungry", "hurt", "ok"),
    "pickaxe": ("yes", "no"),
    "dark": ("yes", "no"),
    "ended": ("stuck", "stalled", "died", "met", "other"),
}
SITUATION_KEYS = ("goal", "item", *SITUATION_VALUES)


@dataclass(frozen=True)
class Lesson:
    """
    教訓帳の 1 件（docs/design/35）: 似た状況で思い出す教訓。確かめていない（世界の事実ではない）。

    `situation` は決まった語彙のキー（SITUATION_KEYS）。`explicit` なら Gemini がその教訓の
    条件として選んだキーで、違う状況では思い出さない。そうでなければ、教わったときの状況の
    スナップショット。場所によらない（ワールドが作り直されても残す）。

    Raises:
        ValueError: 本文が空か長すぎる、条件が長すぎる、知らないキーがあるとき。
    """

    id: str
    text: str
    condition: str = ""
    situation: tuple[tuple[str, str], ...] = ()
    explicit: bool = False
    source: str = "failure"  # "viewer:<名前>" か "failure"
    taught: int = 1
    used: int = 0
    helped: int = 0
    failed: int = 0
    learned_at: str = ""  # ISO 8601（実時間）
    last_used_at: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("a lesson needs text")
        if len(self.text) > MAX_LESSON_CHARS:
            raise ValueError(f"a lesson is one sentence of at most {MAX_LESSON_CHARS} characters")
        if len(self.condition) > MAX_LESSON_CONDITION_CHARS:
            raise ValueError(
                f"a lesson's condition is at most {MAX_LESSON_CONDITION_CHARS} characters"
            )
        for key, _ in self.situation:
            if key not in SITUATION_KEYS:
                raise ValueError(f"unknown situation key {key!r}")

    @property
    def keys(self) -> dict[str, str]:
        return dict(self.situation)

    @property
    def proven(self) -> bool:
        """確かな教訓（何度も効いた）: 消さない。"""
        return self.helped >= 2 and self.helped > self.failed

    @property
    def viewer(self) -> str:
        """教えてくれた視聴者（自分の失敗からなら ""）。"""
        return self.source.split(":", 1)[1] if self.source.startswith("viewer:") else ""


@dataclass(frozen=True)
class LearnedCandidate:
    """
    覚えた候補の 1 件（docs/design/52）: LLM が選んで、うまくいった道具の呼び出し。似た状況で Jev の
    選択肢にもう一度出す。世界の座標を含まない道具の呼び出しだけ（クラフト、装備、預ける・出す、
    かまど…）で、場所によらないのでワールドを作り直しても残す。

    `situation` は目標の種類・対象・場所（教訓と同じ語彙）。`worked` / `failed` は、選んだあとに持ち物か
    小目標の進み具合が変わったかどうか。
    """

    id: str
    situation: tuple[tuple[str, str], ...]
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    worked: int = 1
    failed: int = 0
    learned_at: str = ""  # ISO 8601（実時間）
    last_used_at: str = ""

    @property
    def keys(self) -> dict[str, str]:
        return dict(self.situation)

    def describe(self) -> str:
        """選択肢の id と記録に使う 1 行（例: craft(item=wooden_hoe)）。座標は含まない。"""
        return ToolCall(self.tool, self.args).describe()


@dataclass(frozen=True)
class Activity:
    """
    配信者が今していることとその理由: 目標の決定、実況、チャットの返答がすべて見る
    唯一の見え方。これで、言うこととすることが合う。最終目標から下へ: 順に並べた
    大目標（未完了のものが先、次に最近終わったもの）、小目標、ゲーム。
    """

    mission: Optional[Mission] = None
    town: Optional[TownDefinition] = None
    site: Optional[TownSite] = None  # 選んだ街の場所（決めたこと）
    town_stage: int = 0  # 済んだ段階の数
    stage_met: tuple[GoalSpec, ...] = ()  # 今の段階の条件のうち、これまでに満たしたもの
    objectives: tuple[Objective, ...] = ()
    goal: Optional[Goal] = None
    observation: Optional[GameObservation] = None
    recent_goals: tuple[GoalOutcome, ...] = ()
    notes: tuple[Note, ...] = ()  # 自分のメモ（確かめていない）
    intent: str = ""  # 道具で操作しているとき、今やろうとしていること（配信者が道具に添えた 1 文）
    action: str = ""  # いま実行している手の、視聴者向けの動作（小目標の欄に出す。次の手か小目標が変わるまで残る）
    acting: bool = False  # 行動を実行している最中か（選んでいる間・目標を決めている間は偽）
    failing: int = 0  # 今の小目標で続けて失敗した行動の数（行き詰まりの目安。一言は黙る）
    stagnant_seconds: float = 0.0  # 状況が変わらない時間（秒。動かないと数えたときだけ。一言は黙る）
    screen_note: Optional["ScreenNote"] = None  # 画面で見たこと（確かめていない）
    lessons: tuple[Lesson, ...] = ()  # 今の状況で思い出した教訓（確かめていない。docs/design/35）
    town_map: Optional[TownMap] = None  # 街の理想図（docs/design/47 §6。目標の決定とボードが見る）
    playbook: tuple[PlaybookStep, ...] = ()  # 定石（この順にやる。docs/design/74）
    playbook_done: tuple[str, ...] = ()  # 済んだ定石の段の名前


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
    # 襲われて止まった（ダメージ、反射。docs/design/28 §3）、必要が出て止めた（夕方・夜、空腹・体力）、
    # 考え直しで止めた（docs/design/38）: 小目標の失敗に数えない
    interrupted: bool = False

    @property
    def cut_by_attack(self) -> bool:
        """やり方の失敗でなく止まったか（印か、ブリッジの結果の文から。道具は文だけ）。"""
        return self.interrupted or self.result.startswith(INTERRUPTED_PREFIXES)

    @property
    def missed_by_chance(self) -> bool:
        """確率で落ちる物（草から種、葉から苗木）が今回は落ちなかった（やり方の失敗ではない）。"""
        return self.ok and CHANCE_MISS in self.result


CHANCE_MISS = "nothing dropped this time"
INTERRUPTED_PREFIXES = (
    "failed: interrupted: took damage",
    "failed: reflex: ",
    "failed: stopped: ",
    "failed: rethink: ",
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


@dataclass(frozen=True)
class SameCause:
    """
    同じ道具が、同じ理由（数字を除いて同じ文）で続けて失敗・拒否されている状態。引数の数字を少し変えて
    繰り返すだけでは抜けられない（2026-10-03: `dig(z=285)`、本当は -285 のまま、座標を 54, 55, 56 と
    変えて 8 回以上断られた）。`withheld` なら、その道具をこの手番では出さない。
    """

    tool: str
    reason: str
    times: int
    withheld: bool = False


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
    verified: bool = False  # 一番新しい版が 1 回でも成功し、今、同じ理由の失敗が続いていない
    uses: int = 0
    successes: int = 0
    failures: int = 0
    streak: int = 0  # 一番新しい版の、同じ理由の失敗の連続（成功で 0 に戻る）
    failing: bool = False  # 同じ理由の失敗が続いて、ブリッジが続きを動かさない
    last_failure: Optional[str] = None
    last_good_version: Optional[int] = None  # 一番新しい版が未成功のとき、前に成功した版
    raw: bool = False  # 生の mineflayer の bot を触る技（docs/design/168。メインプロセスで動く）
    runs_version: Optional[int] = None  # 版を指定せずに動かす版（一番新しい版の試しが失敗していれば、前に成功した版）
    code: str = ""  # 直すときだけ読む（一覧には入らない）

    @property
    def needs_arguments(self) -> bool:
        """呼ぶのに要る引数があるか（params は引数がなくても {"type": "object", "properties": {}} が入る）。"""
        return bool(self.params.get("required"))

    def describe(self) -> str:
        """プロンプト用の 1 行。"""
        good = self.last_good_version
        mark = (
            ""
            if self.verified
            else f"（同じ理由で {self.streak} 回続けて失敗中。書き直すか、ほかの手で）"
            if self.failing
            else f"（試し中。v{good} は成功した）"
            if good and good != self.version
            else "（試し中）"
        )
        failure = f"、最後の失敗: {self.last_failure}" if self.last_failure else ""
        failure += f"（同じ理由で {self.streak} 回続けて失敗）" if self.streak >= 2 and not self.failing else ""
        return (
            f"{self.name} v{self.version}{mark}: {self.description}"
            f"{'（生の bot）' if self.raw else ''}"
            f"{f'（呼ぶと v{self.runs_version} が動く）' if self.runs_version not in (None, self.version) else ''}"
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
    raw: bool = False  # 生の bot を触る技（docs/design/168）

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "params": self.params,
            "expects": self.expects,
            "code": self.code,
            **({"raw": True} if self.raw else {}),
        }


@dataclass(frozen=True)
class SkillRun:
    """技を 1 回実行した結果（成功は、技の done() と expects の世界での判定の両方）。"""

    name: str
    version: int
    ok: bool
    ended: str  # done | fail | error | stopped | refused（同じ理由の失敗が続いて動かさなかった）
    summary: str = ""
    reason: str = ""
    expects_lines: tuple[str, ...] = ()
    calls: tuple[dict[str, Any], ...] = ()
    log: tuple[str, ...] = ()
    seconds: float = 0.0
    learned: bool = False  # この実行で初めて成功した
    fell_back_from: Optional[int] = None  # 新しい版の試しが失敗していたので、前に成功した版を動かした（docs/design/168）

    def describe(self) -> str:
        """次の道具の選択に見せる結果（失敗なら理由、記録、最後の道具）。"""
        head = f"skill {self.name} v{self.version}"
        if self.fell_back_from is not None:
            head += f" (v{self.fell_back_from} failed its trial, so the version that worked was run)"
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


class SupportKind(str, Enum):
    """配信を応援してくれた出来事の種類（フォロー、サブスク、ギフト、ビッツ、レイド）。"""

    FOLLOW = "follow"
    SUBSCRIBE = "subscribe"  # はじめてのサブスク
    RESUBSCRIBE = "resubscribe"  # 続けてのサブスク（months か月目）
    GIFT = "gift"  # サブスクのプレゼント（amount 人分）
    CHEER = "cheer"  # ビッツ（amount ビッツ）
    RAID = "raid"  # レイド（amount 人が来た）


@dataclass(frozen=True)
class SupportEvent:
    """
    配信への応援 1 件（Twitch のフォロー、サブスク、ギフト、ビッツ、レイドなど）。お礼を言う元になる。

    Raises:
        ValueError: 名前が空のとき、数が負のとき。
    """

    kind: SupportKind
    user_name: str
    login: Optional[str] = None
    # GIFT: 贈った人数、CHEER: ビッツ、RAID: 連れてきた人数。ほかは 0
    amount: int = 0
    # RESUBSCRIBE: 何か月目か
    months: int = 0
    # SUBSCRIBE / RESUBSCRIBE / GIFT: 1 = Tier 1、2 = Tier 2、3 = Tier 3（プライムは 1）
    tier: int = 1
    # GIFT で 1 人に贈ったとき、その人の名前
    recipient: str = ""
    # 添えられたメッセージ（サブスクやビッツに付く）
    message: str = ""
    received_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.user_name.strip():
            raise ValueError("a support event needs a user name")
        if self.amount < 0 or self.months < 0:
            raise ValueError("amount and months must not be negative")


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
