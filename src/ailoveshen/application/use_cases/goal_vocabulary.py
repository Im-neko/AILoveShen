"""LLM から見た目標の語彙: 今意味のある述語、スキーマ、パース。

目標の決定とチャットの返答が共有する。こうして両方が同じ言い方で目標を語る:
- 小目標（述語 1 つ）と、それがリストの一番上の中目標のためか生存のためか。一緒に決めた
  中目標リストへの変更も含む
- 中目標の完了条件（世界だけから判定する述語）。目標の決定での変更と、視聴者の頼みを受ける
  返答の両方で使う
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ailoveshen.domain.entities import MidGoalPlan
from ailoveshen.domain.value_objects import (
    MAX_NOTE_CHARS,
    PLANNABLE_CONDITIONS,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    NoteKind,
    PlannedStep,
    TownDefinition,
    TownSite,
    TownStage,
)

MAX_GOAL_COUNT = 64
MIN_EXPLORE_DISTANCE = 8
MAX_EXPLORE_DISTANCE = 128
MAX_DIG_DEPTH = 64  # minecraft-bridge/src/goals.mjs と同じ
MAX_CONDITIONS = 3
MAX_PLAN_CHANGES = 3
MAX_STEPS = 6  # 中目標のための手順の数（docs/design/26）
MAX_NOTE_CHANGES = 3
ITEM_DESCRIPTION = (
    "have / stored / placed: an item or group, e.g. planks, log, bed, food, crafting_table, stick, "
    "wooden_sword, wooden_pickaxe"
)
BUILD_NAME_DESCRIPTION = (
    "built: the name of a build you design (a-z, 0-9, _; e.g. annex, storehouse, "
    "watchtower). Leave it out for the house. A new name in a mid goal's conditions "
    "means you design that build right after (an extension of the home, a shed, a "
    "tower, a wall...)"
)
# 条件を示す順番（frozenset には順番がない）
_CONDITION_ORDER = [p for p in GoalPredicate if p in PLANNABLE_CONDITIONS]


class Serves(str, Enum):
    """小目標が何のためか。"""

    CURRENT = "current"  # リストの一番上の中目標（変更のあと）
    SURVIVAL = "survival"  # 生き延びること: 夜、家、扉、食料


class PlanOp(str, Enum):
    """中目標リストの編集。"""

    ADD = "add"
    MOVE = "move"
    DROP = "drop"


class NoteOp(str, Enum):
    """自分のメモの編集。"""

    ADD = "add"
    DROP = "drop"
    KEEP = "keep"  # まだ正しい: 寿命を今日から数え直す


class RequestHandling(str, Enum):
    """返答が視聴者のコメントをどう扱うか。"""

    NONE = "none"  # 雑談か質問
    ACCEPT = "accept"  # 中目標に足した
    DECLINE = "decline"  # 受けない理由を返答で言う


@dataclass(frozen=True)
class MidGoalProposal:
    """足す中目標: `position` は未完了のものの中での 0 始まりの位置（None: 最後）。"""

    title: str
    conditions: tuple[GoalSpec, ...]
    reason: str = ""
    position: Optional[int] = None
    # 視聴者の頼みを、配信者が待っている間（夜に家の中）に今やる（docs/design/13 §8）
    now: bool = False


@dataclass(frozen=True)
class PlanChange:
    """中目標リストの 1 つの編集（add は `proposal`、move と drop は `mid_goal_id`）。"""

    op: PlanOp
    reason: str
    mid_goal_id: str = ""
    position: Optional[int] = None  # 0 始まり
    proposal: Optional[MidGoalProposal] = None


@dataclass(frozen=True)
class NoteChange:
    """
    自分のメモの 1 つの編集（add は種類と本文、drop と keep は `note_id`）。

    add の根拠: lesson は示した小目標の番号（1 始まり）、viewer は視聴者の名前。
    """

    op: NoteOp
    note_id: str = ""
    kind: Optional[NoteKind] = None
    text: str = ""
    goal: Optional[int] = None
    viewer: str = ""


class Remedy(str, Enum):
    """失敗の後の対処: 同じ小目標をやり方を変えてやり直すか、別の小目標にするか。"""

    RETRY = "retry"
    CHANGE = "change"


@dataclass(frozen=True)
class GoalDecision:
    """次の小目標、それが何のためか、一緒に行う中目標リストの編集。"""

    spec: GoalSpec
    reason: str
    serves: Serves
    changes: tuple[PlanChange, ...] = ()
    # 一番上の中目標のための手順（書き直すときだけ。空: 今の手順のまま）。docs/design/26
    steps: tuple[PlannedStep, ...] = ()
    # 前の小目標が失敗で終わったときだけ（docs/design/27）: 原因の分析、対処、やり方の助言
    diagnosis: str = ""
    remedy: Optional[Remedy] = None
    advice: str = ""


def _spec_properties(predicates: list[GoalPredicate]) -> dict[str, Any]:
    return {
        "predicate": {"type": "string", "enum": [p.value for p in predicates]},
        "item": {"type": "string", "description": ITEM_DESCRIPTION},
        "count": {"type": "integer", "minimum": 1, "maximum": MAX_GOAL_COUNT},
        "distance": {
            "type": "integer",
            "minimum": MIN_EXPLORE_DISTANCE,
            "maximum": MAX_EXPLORE_DISTANCE,
            "description": "explored: blocks to go; lit: the radius around the home (8-32)",
        },
        "name": {
            "type": "string",
            "description": BUILD_NAME_DESCRIPTION,
        },
    }


def _conditions_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "description": "All must hold for the mid goal to be done (judged from the world)",
        "minItems": 1,
        "maxItems": MAX_CONDITIONS,
        "items": {
            "type": "object",
            "properties": _spec_properties(_CONDITION_ORDER),
            "required": ["predicate"],
        },
    }


def _note_change_schema(note_ids: list[str], goals: int, viewers: list[str]) -> dict[str, Any]:
    """メモの編集 1 つ。書ける種類と根拠は、今示しているものに合わせる。"""
    kinds = [NoteKind.PLAN.value]
    properties: dict[str, Any] = {
        "op": {"type": "string", "enum": [o.value for o in NoteOp]},
        "text": {
            "type": "string",
            "description": f"add: the note, one sentence of at most {MAX_NOTE_CHARS} characters",
        },
    }
    if goals:
        kinds.insert(0, NoteKind.LESSON.value)
        properties["goal"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": goals,
            "description": "lesson: the number of the small goal it was learned from",
        }
    if viewers:
        kinds.append(NoteKind.VIEWER.value)
        properties["viewer"] = {
            "type": "string",
            "enum": viewers,
            "description": "viewer: whom the note is about",
        }
    properties["kind"] = {"type": "string", "enum": kinds, "description": "add: the kind"}
    if note_ids:
        properties["id"] = {"type": "string", "enum": note_ids, "description": "drop / keep"}
    return {"type": "object", "properties": properties, "required": ["op"]}


def goal_schema(
    predicates: list[GoalPredicate],
    mid_goal_ids: list[str],
    note_ids: list[str] | None = None,
    goals: int = 0,
    viewers: list[str] | None = None,
    after_failure: bool = False,
    retry_allowed: bool = True,
) -> dict[str, Any]:
    """
    目標の決定の JSON スキーマ: 小目標、中目標リストの編集、自分のメモの編集。

    `goals` は示した小目標の数（lesson の根拠に使える）、`viewers` は示した会話にいる
    視聴者。`after_failure` なら、先頭に原因の分析、次に対処と助言（docs/design/27。
    出力はスキーマの順に書かれるので、分析してから決める）。`retry_allowed` が False なら
    同じ小目標のやり直しは選べない。
    """
    change: dict[str, Any] = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": [o.value for o in PlanOp]},
            "title": {"type": "string", "description": "add: the mid goal's title"},
            "conditions": _conditions_schema(),
            "position": {
                "type": "integer",
                "minimum": 1,
                "description": "add / move: its place in the list, 1 = worked on first",
            },
            "reason": {"type": "string", "description": "Why, in one short sentence"},
        },
        "required": ["op", "reason"],
    }
    if mid_goal_ids:
        change["properties"]["id"] = {
            "type": "string",
            "enum": mid_goal_ids,
            "description": "move / drop: the mid goal",
        }
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "plan_changes": {
                "type": "array",
                "description": "Edits of the mid-goal list, applied in order (usually none)",
                "maxItems": MAX_PLAN_CHANGES,
                "items": change,
            },
            "note_changes": {
                "type": "array",
                "description": "Edits of your own notes, applied in order (usually none)",
                "maxItems": MAX_NOTE_CHANGES,
                "items": _note_change_schema(note_ids or [], goals, viewers or []),
            },
            **_spec_properties(predicates),
            "dig_depth": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_DIG_DEPTH,
                "description": "Optional: how many blocks below where you stand you may dig "
                "stairs down to buried stone or ore for this goal (omit: do not dig down)",
            },
            "serves": {
                "type": "string",
                "enum": [s.value for s in Serves],
                "description": "current: for the mid goal at the top of the list after the "
                "edits; survival: to stay alive (the night, home, the door, food)",
            },
            "reason": {"type": "string", "description": "Why this goal now, in one short sentence"},
            "steps": {
                "type": "array",
                "maxItems": MAX_STEPS,
                "description": "The steps (small goals, in order) to reach the mid goal at the top "
                "of the list after the edits. Write them when it has no steps yet or they are not "
                "working; otherwise leave empty (the fast model picks the next step itself)",
                "items": {
                    "type": "object",
                    "properties": {
                        **_spec_properties(_CONDITION_ORDER),
                        "reason": {"type": "string", "description": "Why this step, one sentence"},
                    },
                    "required": ["predicate", "reason"],
                },
            },
        },
        "required": ["predicate", "serves", "reason"],
    }
    if not after_failure:
        return schema
    remedies = [Remedy.RETRY.value, Remedy.CHANGE.value] if retry_allowed else [Remedy.CHANGE.value]
    head = {
        "diagnosis": {
            "type": "string",
            "description": "First: why the last small goal made no progress, citing the facts "
            "(its action record, the options offered, the state, the screen). 1-2 sentences in "
            "Japanese",
        },
        "remedy": {
            "type": "string",
            "enum": remedies,
            "description": "retry: the same small goal again (same predicate and item), done "
            "differently as the advice says; change: a different small goal",
        },
        "advice": {
            "type": "string",
            "description": "retry: required. 1-2 English sentences for the action chooser, "
            "concrete to the options it was offered (which to prefer or avoid). change: optional",
        },
    }
    schema["properties"] = {**head, **schema["properties"]}
    schema["required"] = ["diagnosis", "remedy", *schema["required"]]
    return schema


def reply_schema() -> dict[str, Any]:
    """
    チャットの返答と、それがコメントをどう扱うかの JSON スキーマ。

    返答と扱いは 1 回の生成から出るので、頼みを受けた返答は必ずそれを中目標に足す。
    """
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string", "description": "What to say to the viewer"},
            "request": {
                "type": "string",
                "enum": [r.value for r in RequestHandling],
                "description": "none: chat or a question; accept: add the request to the mid "
                "goals; decline: not taken (the reply says why)",
            },
            "title": {"type": "string", "description": "accept: the mid goal's title"},
            "conditions": _conditions_schema(),
            "position": {
                "type": "integer",
                "minimum": 2,
                "description": "accept: its place in the list (2 = right after the one worked "
                "on now)",
            },
            "reason": {"type": "string", "description": "accept: why it helps, in one sentence"},
            "when": {
                "type": "string",
                "enum": ["now", "next"],
                "description": "accept: now = do it right away because you are only waiting "
                "(the night inside the home) and it can be done where you are; next = after "
                "the mid goal worked on now (default)",
            },
            "name_reading": {
                "type": "string",
                "description": "the viewer's name read aloud, in hiragana: only when the prompt "
                "shows no reading for it yet (your guess), or when the viewer says how to read "
                "their name",
            },
        },
        "required": ["reply", "request"],
    }


def parse_spec(data: dict[str, Any]) -> GoalSpec:
    """
    述語 1 つを引数と一緒にパースする。

    Raises:
        ValueError: 引数がないか、形が正しくないとき。
    """
    try:
        predicate = GoalPredicate(data["predicate"])
        return GoalSpec(
            predicate=predicate,
            item=str(data["item"])
            if predicate in (GoalPredicate.HAVE, GoalPredicate.STORED, GoalPredicate.PLACED)
            else None,
            count=int(data["count"])
            if predicate in (GoalPredicate.HAVE, GoalPredicate.STORED, GoalPredicate.SURVEYED)
            else None,
            where="home" if predicate == GoalPredicate.PLACED else None,
            distance=int(data["distance"])
            if predicate in (GoalPredicate.EXPLORED, GoalPredicate.LIT)
            else None,
            dig_depth=int(data["dig_depth"]) if data.get("dig_depth") is not None else None,
            name=str(data["name"]).strip().lower() or None
            if predicate == GoalPredicate.BUILT and data.get("name")
            else None,
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"missing or malformed argument: {e}") from e


def parse_proposal(data: dict[str, Any]) -> MidGoalProposal:
    """
    足す中目標をパースする（題名、条件、1 始まりの位置、理由）。

    Raises:
        ValueError: 題名か条件がないか、形が正しくないとき。
    """
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("a mid goal needs a title")
    raw = data.get("conditions") or []
    if not raw:
        raise ValueError(f"mid goal {title} needs conditions")
    conditions = tuple(parse_spec(c) for c in raw)
    for c in conditions:
        if c.predicate not in PLANNABLE_CONDITIONS:
            raise ValueError(f"{c.predicate.value} cannot be a condition of a mid goal")
    position = data.get("position")
    return MidGoalProposal(
        title=title,
        conditions=conditions,
        reason=str(data.get("reason", "")),
        position=int(position) - 1 if position is not None else None,
        now=data.get("when") == "now",
    )


def parse_decision(data: dict[str, Any]) -> GoalDecision:
    """
    目標の決定をパースする。

    Raises:
        ValueError: 目標か編集がないか、形が正しくないとき。
    """
    try:
        serves = Serves(data["serves"])
    except (KeyError, ValueError) as e:
        raise ValueError(f"serves must be one of {[s.value for s in Serves]}: {e}") from e
    changes = []
    for raw in data.get("plan_changes") or []:
        try:
            op = PlanOp(raw["op"])
        except (KeyError, ValueError) as e:
            raise ValueError(f"plan change op must be one of {[o.value for o in PlanOp]}") from e
        reason = str(raw.get("reason", ""))
        position = raw.get("position")
        if op == PlanOp.ADD:
            changes.append(PlanChange(op=op, reason=reason, proposal=parse_proposal(raw)))
            continue
        mid_goal_id = str(raw.get("id", ""))
        if not mid_goal_id:
            raise ValueError(f"{op.value} needs the id of a mid goal")
        if op == PlanOp.MOVE and position is None:
            raise ValueError(f"move {mid_goal_id} needs a position")
        changes.append(
            PlanChange(
                op=op,
                reason=reason,
                mid_goal_id=mid_goal_id,
                position=int(position) - 1 if position is not None else None,
            )
        )
    steps = []
    for i, raw in enumerate(data.get("steps") or []):
        try:
            steps.append(PlannedStep(parse_spec(raw), str(raw.get("reason", "")).strip()))
        except ValueError as e:
            raise ValueError(f"step {i + 1}: {e}") from e
    remedy = None
    if data.get("remedy"):
        try:
            remedy = Remedy(data["remedy"])
        except ValueError as e:
            raise ValueError(f"remedy must be one of {[r.value for r in Remedy]}") from e
    return GoalDecision(
        spec=parse_spec(data),
        reason=str(data.get("reason", "")),
        serves=serves,
        changes=tuple(changes),
        steps=tuple(steps[:MAX_STEPS]),
        diagnosis=str(data.get("diagnosis", "")).strip(),
        remedy=remedy,
        advice=str(data.get("advice", "")).strip(),
    )


def parse_note_changes(data: dict[str, Any]) -> tuple[NoteChange, ...]:
    """
    目標の決定の中のメモの編集をパースする（根拠が示したものかは NoteKeeper が確かめる）。

    Raises:
        ValueError: 編集の形が正しくないとき。
    """
    changes = []
    for raw in data.get("note_changes") or []:
        try:
            op = NoteOp(raw["op"])
        except (KeyError, ValueError) as e:
            raise ValueError(f"note change op must be one of {[o.value for o in NoteOp]}") from e
        if op != NoteOp.ADD:
            note_id = str(raw.get("id", ""))
            if not note_id:
                raise ValueError(f"{op.value} needs the id of a note")
            changes.append(NoteChange(op=op, note_id=note_id))
            continue
        try:
            kind = NoteKind(raw["kind"])
        except (KeyError, ValueError) as e:
            raise ValueError(f"a new note needs a kind: {[k.value for k in NoteKind]}") from e
        goal = raw.get("goal")
        changes.append(
            NoteChange(
                op=op,
                kind=kind,
                text=str(raw.get("text", "")).strip(),
                goal=int(goal) if goal is not None and kind == NoteKind.LESSON else None,
                viewer=str(raw.get("viewer", "")) if kind == NoteKind.VIEWER else "",
            )
        )
    return tuple(changes)


def predicates_now(obs: GameObservation, plan: MidGoalPlan) -> list[GoalPredicate]:
    """
    今意味のある述語（例: 家ができる前は、家についての述語は出さない）。調査は、それを
    求める中目標があるときだけ（中目標を足した直後の観測には、調査の計画がまだない）。
    """
    out = []
    if (obs.has_plan and not obs.house_complete) or obs.unfinished_builds:
        out.append(GoalPredicate.BUILT)
    out.append(GoalPredicate.HAVE)
    if obs.has_home:
        out += [GoalPredicate.AT_HOME, GoalPredicate.THROUGH_NIGHT]
        if obs.time_phase == "day":
            out.append(GoalPredicate.CLEARED)
        # ベッドのほか、作業台・かまど・チェストも家の中に置ける（済んでいれば、ブリッジがすぐ達成とする）
        out.append(GoalPredicate.PLACED)
        out.append(GoalPredicate.STORED)
        out.append(GoalPredicate.LIT)
    if obs.has_home and any(
        c.predicate == GoalPredicate.SURVEYED for g in plan.pending for c in g.conditions
    ):
        out.append(GoalPredicate.SURVEYED)
    out.append(GoalPredicate.EXPLORED)
    return out


MAX_TOWN_STAGES = 5


def _stage_properties() -> dict[str, Any]:
    return {
        "conditions": {
            **_conditions_schema(),
            "minItems": 0,
            "description": "What is judged done from the world, with the conditions available now",
        },
        "unresolved": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Parts that cannot be written with the conditions available now "
            "(each with what would be needed to do or judge it)",
        },
    }


def town_schema() -> dict[str, Any]:
    """街の定義の JSON スキーマ: 文と、順に並べた段階。"""
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What the town is, in 2-3 sentences"},
            "stages": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_TOWN_STAGES,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "why": {"type": "string", "description": "Why the town needs it"},
                        **_stage_properties(),
                    },
                    "required": ["title", "why", "conditions", "unresolved"],
                },
            },
        },
        "required": ["text", "stages"],
    }


def stage_schema() -> dict[str, Any]:
    """今使える条件で書き直した段階の JSON スキーマ。"""
    return {
        "type": "object",
        "properties": _stage_properties(),
        "required": ["conditions", "unresolved"],
    }


def parse_stage(data: dict[str, Any], title: str, why: str) -> TownStage:
    """
    段階の条件と、まだ解決していない部分をパースする。

    Raises:
        ValueError: 条件の形が正しくないか、世界から判定できないとき
    """
    conditions = tuple(parse_spec(c) for c in data.get("conditions") or [])
    for c in conditions:
        if c.predicate not in PLANNABLE_CONDITIONS:
            raise ValueError(f"{c.predicate.value} cannot be a condition of a town stage")
    unresolved = tuple(str(u).strip() for u in data.get("unresolved") or [] if str(u).strip())
    return TownStage(title=title, why=why, conditions=conditions, unresolved=unresolved)


def parse_town(data: dict[str, Any]) -> TownDefinition:
    """
    街の定義をパースする。

    Raises:
        ValueError: 文か段階がないか、形が正しくないとき
    """
    stages = []
    for raw in data.get("stages") or []:
        title = str(raw.get("title", "")).strip()
        if not title:
            raise ValueError("a town stage needs a title")
        stages.append(parse_stage(raw, title, str(raw.get("why", ""))))
    return TownDefinition(text=str(data.get("text", "")).strip(), stages=tuple(stages))


def site_schema(site_ids: list[str]) -> dict[str, Any]:
    """街の場所の選択の JSON スキーマ: 調べた候補地から 1 か所、理由、街の名前。"""
    return {
        "type": "object",
        "properties": {
            "site_id": {"type": "string", "enum": site_ids},
            "reason": {
                "type": "string",
                "description": "Why this site, from its numbers, in 1-2 sentences (said on stream)",
            },
            "town_name": {"type": "string", "description": "A short name for the town"},
        },
        "required": ["site_id", "reason", "town_name"],
    }


def parse_site(data: dict[str, Any], rows: list[dict[str, Any]]) -> TownSite:
    """
    選んだ街の場所をパースする。

    Raises:
        ValueError: 調べた候補地にない場所か、理由か名前がないとき
    """
    site_id = str(data.get("site_id", ""))
    row = next((r for r in rows if str(r.get("id")) == site_id), None)
    if row is None:
        ids = ", ".join(str(r.get("id")) for r in rows)
        raise ValueError(f"{site_id or 'no site'} is not one of the surveyed sites ({ids})")
    return TownSite(
        site_id=site_id,
        x=int(row["x"]),
        z=int(row["z"]),
        reason=str(data.get("reason", "")).strip(),
        name=str(data.get("town_name", "")).strip(),
    )
