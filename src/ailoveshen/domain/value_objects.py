"""Value objects for Domain layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Optional


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


# =============================================================================
# Emotion Value Objects
# =============================================================================


class EmotionType(str, Enum):
    """Types of emotions the AI character can express."""

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
    Emotion state value object.

    Value objects are immutable and compared by their attributes.

    Raises:
        ValueError: If intensity is not between 0.0 and 1.0.
    """

    primary: EmotionType = EmotionType.NEUTRAL
    intensity: float = 0.5  # 0.0 - 1.0

    def __post_init__(self) -> None:
        """Validate intensity range."""
        if not 0.0 <= self.intensity <= 1.0:
            raise ValueError(f"intensity must be between 0.0 and 1.0, got {self.intensity}")

    def with_intensity(self, new_intensity: float) -> EmotionState:
        """Create a new EmotionState with different intensity (clamped)."""
        clamped = max(0.0, min(1.0, new_intensity))
        return EmotionState(primary=self.primary, intensity=clamped)

    def decay(self, rate: float = 0.1) -> EmotionState:
        """
        Decay emotion intensity over time.

        Returns neutral if intensity drops below threshold.
        """
        new_intensity = max(0.0, self.intensity - rate)
        if new_intensity < 0.3:
            return EmotionState(EmotionType.NEUTRAL, 0.5)
        return EmotionState(primary=self.primary, intensity=new_intensity)


# =============================================================================
# Speech Value Objects
# =============================================================================


class SpeechPriority(IntEnum):
    """Priority levels for speech requests."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    INTERRUPT = 3


@dataclass(frozen=True)
class SpeechRequest:
    """
    Speech request value object.

    Represents a request to synthesize and play speech.
    """

    text: str
    priority: SpeechPriority = SpeechPriority.NORMAL
    emotion: EmotionState = field(default_factory=EmotionState)
    source: str = "unknown"  # "commentary" | "response" | "game_event"

    def should_interrupt(self) -> bool:
        """Check if this request should interrupt current speech."""
        return self.priority >= SpeechPriority.INTERRUPT


class SpeechStatus(str, Enum):
    """Speech playback status."""

    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    PLAYING = "playing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True)
class SpeechResult:
    """
    Value object representing speech synthesis/playback result.

    Immutable record of what happened with a speech request.
    """

    request_text: str
    status: SpeechStatus
    audio_duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    completed_at: datetime = field(default_factory=_utc_now)

    @classmethod
    def queued(cls, text: str) -> SpeechResult:
        """Create a queued result."""
        return cls(
            request_text=text,
            status=SpeechStatus.QUEUED,
        )

    @classmethod
    def completed(cls, text: str, duration_ms: int) -> SpeechResult:
        """Create a completed result."""
        return cls(
            request_text=text,
            status=SpeechStatus.COMPLETED,
            audio_duration_ms=duration_ms,
        )

    @classmethod
    def interrupted(cls, text: str) -> SpeechResult:
        """Create an interrupted result."""
        return cls(
            request_text=text,
            status=SpeechStatus.INTERRUPTED,
        )

    @classmethod
    def failed(cls, text: str, error: str) -> SpeechResult:
        """Create a failed result."""
        return cls(
            request_text=text,
            status=SpeechStatus.FAILED,
            error_message=error,
        )

    @property
    def is_success(self) -> bool:
        """Check if the speech completed successfully."""
        return self.status == SpeechStatus.COMPLETED


# =============================================================================
# Conversation Value Objects
# =============================================================================


class MessageRole(str, Enum):
    """Who sent a conversation message."""

    VIEWER = "viewer"
    STREAMER = "streamer"


class MessageType(str, Enum):
    """What kind of utterance a conversation message is."""

    CHAT = "chat"  # Viewer chat comment
    COMMENTARY = "commentary"  # Streamer's game commentary / thoughts
    RESPONSE = "response"  # Streamer's reply to a viewer


@dataclass(frozen=True)
class ConversationMessage:
    """
    A single message in the stream conversation.

    Raises:
        ValueError: If content is empty.
    """

    role: MessageRole
    message_type: MessageType
    content: str
    speaker_name: str = ""
    speaker_id: Optional[str] = None
    timestamp: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        """Validate content."""
        if not self.content.strip():
            raise ValueError("content must not be empty")

    @classmethod
    def from_viewer(
        cls,
        content: str,
        user_name: str,
        user_id: Optional[str] = None,
    ) -> ConversationMessage:
        """Create a chat message sent by a viewer."""
        return cls(
            role=MessageRole.VIEWER,
            message_type=MessageType.CHAT,
            content=content,
            speaker_name=user_name,
            speaker_id=user_id,
        )

    @classmethod
    def from_streamer(cls, content: str, message_type: MessageType) -> ConversationMessage:
        """Create a message spoken by the AI streamer."""
        return cls(
            role=MessageRole.STREAMER,
            message_type=message_type,
            content=content,
        )


@dataclass(frozen=True)
class CharacterProfile:
    """
    The AI streamer's character.

    Raises:
        ValueError: If name is empty.
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
        """Validate name."""
        if not self.name.strip():
            raise ValueError("name must not be empty")


@dataclass(frozen=True)
class GenerationContext:
    """
    Everything the streamer knows when generating an utterance.

    game_state_summary is optional until the game integration (Phase 6)
    provides a structured game state.
    """

    emotion_state: EmotionState = field(default_factory=EmotionState)
    game_state_summary: Optional[str] = None
    recent_events: tuple[str, ...] = ()
    recent_messages: tuple[ConversationMessage, ...] = ()


# =============================================================================
# Position Value Objects
# =============================================================================


@dataclass(frozen=True)
class Position:
    """3D position in the game world."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def distance_to(self, other: Position) -> float:
        """Calculate Euclidean distance to another position."""
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2) ** 0.5

    def __str__(self) -> str:
        return f"({self.x:.1f}, {self.y:.1f}, {self.z:.1f})"


@dataclass(frozen=True)
class Rotation:
    """
    Player rotation (yaw and pitch).

    Raises:
        ValueError: If yaw is not between -180 and 180, or pitch is not between -90 and 90.
    """

    yaw: float = 0.0  # -180 to 180
    pitch: float = 0.0  # -90 to 90

    def __post_init__(self) -> None:
        """Validate rotation ranges."""
        if not -180.0 <= self.yaw <= 180.0:
            raise ValueError(f"yaw must be between -180 and 180, got {self.yaw}")
        if not -90.0 <= self.pitch <= 90.0:
            raise ValueError(f"pitch must be between -90 and 90, got {self.pitch}")

    def __str__(self) -> str:
        return f"(yaw={self.yaw:.1f}, pitch={self.pitch:.1f})"


# =============================================================================
# Filter Result Value Objects
# =============================================================================


@dataclass(frozen=True)
class FilterResult:
    """
    Result of comment filtering.

    Raises:
        ValueError: If score is not between 0.0 and 1.0.
    """

    should_respond: bool
    score: float  # 0.0 - 1.0, how important/relevant the comment is
    reason: str

    def __post_init__(self) -> None:
        """Validate score range."""
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score must be between 0.0 and 1.0, got {self.score}")

    @classmethod
    def accept(cls, score: float, reason: str = "Accepted") -> FilterResult:
        """Create an accepting filter result."""
        return cls(should_respond=True, score=score, reason=reason)

    @classmethod
    def reject(cls, score: float, reason: str = "Rejected") -> FilterResult:
        """Create a rejecting filter result."""
        return cls(should_respond=False, score=score, reason=reason)


# =============================================================================
# Minecraft Value Objects
# =============================================================================


class GoalType(str, Enum):
    """
    Goals the LLM can set for the game agent.

    Only goals that have executable actions in the Minecraft bridge are
    listed; offering the LLM a goal nothing can carry out would stall the agent.
    """

    GATHER_WOOD = "gather_wood"
    CRAFT = "craft"
    BUILD_SHELTER = "build_shelter"
    EXPLORE = "explore"
    SURVIVE_NIGHT = "survive_night"
    GET_FOOD = "get_food"


# Bridge action ids that serve each goal. Survival actions are always allowed.
GOAL_ACTIONS: dict[GoalType, frozenset[str]] = {
    GoalType.GATHER_WOOD: frozenset({"collect_log", "pickup_drop"}),
    GoalType.CRAFT: frozenset(
        {
            "craft_planks",
            "craft_crafting_table",
            "place_crafting_table",
            "craft_door",
            "craft_sword",
        }
    ),
    GoalType.BUILD_SHELTER: frozenset({"build_step"}),
    GoalType.EXPLORE: frozenset({"explore", "pickup_drop"}),
    GoalType.SURVIVE_NIGHT: frozenset({"go_home", "stay_inside"}),
    GoalType.GET_FOOD: frozenset({"hunt_animal"}),
}
SURVIVAL_ACTIONS: frozenset[str] = frozenset(
    {"attack_hostile", "flee_hostile", "equip_weapon", "eat"}
)


@dataclass(frozen=True)
class Goal:
    """The current direction set by the LLM; the action selector works within it."""

    goal_type: GoalType
    reason: str = ""
    set_at: datetime = field(default_factory=_utc_now)

    def allows(self, action_id: str) -> bool:
        """Whether an action may be chosen while pursuing this goal."""
        return action_id in SURVIVAL_ACTIONS or action_id in GOAL_ACTIONS[self.goal_type]


class Side(str, Enum):
    """Wall side of a house (Minecraft: north is -Z, east is +X)."""

    NORTH = "north"
    EAST = "east"
    SOUTH = "south"
    WEST = "west"


class BlockKind(str, Enum):
    """Kinds of blocks a house plan uses (any wood type)."""

    PLANKS = "planks"
    LOG = "log"
    DOOR = "door"


@dataclass(frozen=True)
class WallOpening:
    """A one-block window in a wall, at eye height."""

    side: Side
    offset: int  # position along the wall, 0 = the corner with the smallest x/z


@dataclass(frozen=True)
class PlannedBlock:
    """A block of a build plan, relative to the site origin (min corner, ground level)."""

    x: int
    y: int
    z: int
    kind: BlockKind


@dataclass(frozen=True)
class HouseBlueprint:
    """
    A small single-room house: walls, a flat roof, one door, optional windows.

    Size bounds come from what the Minecraft bridge was measured to build
    reliably (5x5x3 and 7x7x4 both completed without failed placements).

    Raises:
        ValueError: If a dimension is out of bounds, or the door or a window does
            not fit on its wall.
    """

    MIN_SIDE = 5
    MAX_SIDE = 7
    MIN_WALL_HEIGHT = 3
    MAX_WALL_HEIGHT = 4
    WINDOW_Y = 1

    name: str
    concept: str
    width: int  # along x
    depth: int  # along z
    wall_height: int
    door_side: Side
    door_offset: int
    windows: tuple[WallOpening, ...] = ()
    corner_pillars: bool = False  # logs at the four corners instead of planks

    def __post_init__(self) -> None:
        """Validate dimensions and openings."""
        for label, value in (("width", self.width), ("depth", self.depth)):
            if not self.MIN_SIDE <= value <= self.MAX_SIDE:
                raise ValueError(f"{label} must be {self.MIN_SIDE}-{self.MAX_SIDE}, got {value}")
        if not self.MIN_WALL_HEIGHT <= self.wall_height <= self.MAX_WALL_HEIGHT:
            raise ValueError(
                f"wall_height must be {self.MIN_WALL_HEIGHT}-{self.MAX_WALL_HEIGHT}, "
                f"got {self.wall_height}"
            )
        self._check_offset("door", self.door_side, self.door_offset)
        seen = {(self.door_side, self.door_offset)}
        for w in self.windows:
            self._check_offset("window", w.side, w.offset)
            if (w.side, w.offset) in seen:
                raise ValueError(
                    f"window at {w.side.value}:{w.offset} overlaps the door or another window"
                )
            seen.add((w.side, w.offset))

    def _wall_length(self, side: Side) -> int:
        return self.width if side in (Side.NORTH, Side.SOUTH) else self.depth

    def _check_offset(self, label: str, side: Side, offset: int) -> None:
        # Corners are excluded: an opening there would cut the wall's support
        if not 1 <= offset <= self._wall_length(side) - 2:
            raise ValueError(
                f"{label} offset on the {side.value} wall must be "
                f"1-{self._wall_length(side) - 2}, got {offset}"
            )

    def _wall_position(self, side: Side, offset: int) -> tuple[int, int]:
        """(x, z) of the given position along a wall."""
        if side == Side.NORTH:
            return offset, 0
        if side == Side.SOUTH:
            return offset, self.depth - 1
        if side == Side.WEST:
            return 0, offset
        return self.width - 1, offset

    @property
    def height(self) -> int:
        """Total height including the roof."""
        return self.wall_height + 1

    def blocks(self) -> tuple[PlannedBlock, ...]:
        """
        Expand to blocks in a placeable order.

        Walls go up layer by layer, the roof is laid ring by ring from the
        edges inward (each block rests on a wall or a previous roof block),
        and the door comes last.
        """
        door_x, door_z = self._wall_position(self.door_side, self.door_offset)
        windows = {self._wall_position(w.side, w.offset) for w in self.windows}
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
                    if (x, z) in windows and y == self.WINDOW_Y:
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
        """Number of blocks of each kind the whole house needs."""
        counts: dict[BlockKind, int] = {}
        for b in self.blocks():
            counts[b.kind] = counts.get(b.kind, 0) + 1
        return counts


@dataclass(frozen=True)
class BuildStatus:
    """Progress of the house plan in the world, as reported by the bridge."""

    total: int
    placed: int
    complete: bool
    site_chosen: bool
    remaining: dict[BlockKind, int] = field(default_factory=dict)


@dataclass(frozen=True)
class MaterialNeeds:
    """What is still missing to finish the house, given the current inventory."""

    PLANKS_PER_LOG = 4
    PLANKS_PER_TABLE = 4
    PLANKS_PER_DOOR_CRAFT = 6
    PLANKS_PER_SWORD = 4  # 2 planks + a stick (2 planks make 4 sticks)

    logs_short: int
    planks_short: int
    door_needed: bool
    table_needed: bool
    sword_needed: bool = False

    @property
    def wood_ready(self) -> bool:
        """Enough logs are held to craft everything still missing."""
        return self.logs_short == 0

    @property
    def crafted(self) -> bool:
        """All planks, the door and the sword are crafted."""
        return self.planks_short == 0 and not self.door_needed and not self.sword_needed


@dataclass(frozen=True)
class AvailableAction:
    """An action the bridge can execute right now."""

    action_id: str
    description: str


@dataclass(frozen=True)
class GameObservation:
    """
    A compact snapshot of the game for decision making.

    `state` is the JSON-serializable summary given to the decision models
    as-is; the typed fields are what the domain logic reads.
    """

    state: dict
    inventory: dict[str, int]
    actions: tuple[AvailableAction, ...]
    health: float
    food: int
    crafting_table_nearby: bool = False
    build: Optional[BuildStatus] = None
    time_phase: str = "day"  # day / dusk / night / dawn
    food_items: int = 0
    has_home: bool = False
    inside_home: bool = False
    busy: bool = False  # the bridge is running an action or a reflex

    def can(self, action_id: str) -> bool:
        """Whether the action is executable right now."""
        return any(a.action_id == action_id for a in self.actions)

    def count(self, suffix: str) -> int:
        """Total count of inventory items whose name ends with suffix (e.g. "_log")."""
        return sum(n for name, n in self.inventory.items() if name.endswith(suffix))


@dataclass(frozen=True)
class ActionDecision:
    """The action picked by the selector, with its confidence (0.0 - 1.0)."""

    action_id: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    """Outcome of executing one bridge action."""

    action_id: str
    ok: bool
    result: str
    seconds: float
