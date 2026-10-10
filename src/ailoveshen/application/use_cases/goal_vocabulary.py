"""LLM から見た目標の語彙: 今意味のある述語、スキーマ、パース。

目標の決定とチャットの返答が共有する。こうして両方が同じ言い方で目標を語る:
- 小目標（述語 1 つ）と、それがリストの一番上の大目標のためか生存のためか。一緒に決めた
  大目標リストへの変更も含む
- 大目標の完了条件（世界だけから判定する述語）。目標の決定での変更と、視聴者の頼みを受ける
  返答の両方で使う
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ailoveshen.domain.entities import ObjectivePlan
from ailoveshen.domain.value_objects import (
    MAX_BUNDLE,
    MAX_NOTE_CHARS,
    PLANNABLE_CONDITIONS,
    SITUATION_VALUES,
    GameObservation,
    GoalPredicate,
    GoalSpec,
    NoteKind,
    Job,
    TownDefinition,
    TownSite,
    TownStage,
    check_bundle,
    objective_kind,
)

# ブリッジの MAX_COUNT（goals.mjs）と同じ。家の板材は 95 枚要る（64 では計画の書き直しが 2 回落ちた）
MAX_GOAL_COUNT = 256
MIN_EXPLORE_DISTANCE = 8  # explored の下限（lit はブリッジが 8〜32、leveled は 3〜16 を確かめる）
MIN_DISTANCE = 3
MAX_EXPLORE_DISTANCE = 128
MAX_DIG_DEPTH = 384  # minecraft-bridge/src/goals.mjs と同じ（任意の上限。省けば上限なし）
MAX_CONDITIONS = 3
MAX_PLAN_CHANGES = 3
MAX_JOBS = 6  # 大目標のための中目標の数（docs/design/26）
MAX_NOTE_CHANGES = 3
ITEM_DESCRIPTION = (
    "have / stored / placed: an item or group, e.g. planks, log, bed, food, crafting_table, stick, "
    "wooden_sword, wooden_pickaxe"
)
BUILD_NAME_DESCRIPTION = (
    "built: the name of a build you design (a-z, 0-9, _; e.g. annex, storehouse, "
    "watchtower). Leave it out for the house. A new name in an objective's conditions "
    "means you design that build right after (an extension of the home, a shed, a "
    "tower, a wall...). custom: the name of a check (a-z, 0-9, _; 2-40 characters, saying "
    "what it tests, e.g. sheep_in_pen_3, flowers_by_door) for a result no other predicate "
    "can say; a new name in an objective's conditions means you write the check right after, "
    "from the objective's title and reason (say exactly what must hold). facility: the name of "
    "a farm or tree farm (a-z, 0-9, _; e.g. wheat_farm, oak_grove; the town map's name for it "
    "when it has one); a new name means you design it right after (zones, size, crop or sapling)"
)
# 条件を示す順番（frozenset には順番がない）
_CONDITION_ORDER = [p for p in GoalPredicate if p in PLANNABLE_CONDITIONS]
_ITEM_PREDICATES = (
    GoalPredicate.HAVE,
    GoalPredicate.STORED,
    GoalPredicate.PLACED,
    GoalPredicate.PLANTED,
    GoalPredicate.FARMED,
)
_COUNT_PREDICATES = (
    GoalPredicate.HAVE,
    GoalPredicate.STORED,
    GoalPredicate.SURVEYED,
    GoalPredicate.PLANTED,
    GoalPredicate.FARMED,
)


class Serves(str, Enum):
    """小目標が何のためか。"""

    CURRENT = "current"  # リストの一番上の大目標（変更のあと）
    SURVIVAL = "survival"  # 生き延びること: 夜、家、扉、食料


class PlanOp(str, Enum):
    """大目標リストの編集。"""

    ADD = "add"
    MOVE = "move"
    DROP = "drop"
    SATISFY = "satisfy"  # もう持っている施設で済んでいる（根拠 `by` をブリッジの記憶と突き合わせて済みにする）


class NoteOp(str, Enum):
    """自分のメモの編集。"""

    ADD = "add"
    DROP = "drop"
    KEEP = "keep"  # まだ正しい: 寿命を今日から数え直す


class RequestHandling(str, Enum):
    """返答が視聴者のコメントをどう扱うか。"""

    NONE = "none"  # 雑談か質問
    ACCEPT = "accept"  # 大目標に足した
    DECLINE = "decline"  # 受けない理由を返答で言う
    WITHDRAW = "withdraw"  # 本人が自分の頼みを取り下げた（「もういいよ」）: その大目標をやめる


class DeclineReason(str, Enum):
    """
    断る理由（決まったものだけ）。「今は別のことをしている」「今の目標が先」は理由にならない:
    やる価値があるなら accept の when=next で後ろに入れる（2026-09-28: もっともな指摘を「今は
    ○○中だから後で」と断り、何も残らなかった）。
    """

    CANNOT = "cannot"  # 完了条件でも custom(名前) でも書けない、まだできない
    WRONG = "wrong"  # 今の状態から見て、指摘・助言が当たっていない（もうある、事実と違う）
    UNSAFE = "unsafe"  # 死ぬ・家や持ち物を失うおそれ
    OFF_MISSION = "off_mission"  # 頼みだけ: 最終目標に関係なく、後の作業も楽にならない
    LIMIT = "limit"  # 視聴者の頼みの上限、大目標のリストがいっぱい


class AdviceRelevance(str, Enum):
    """
    助言・提案が今の目標にどれだけ関わるか（docs/design/49 §8）。related で返答が納得したなら、
    今の小目標を決め直す（rethink）: 助言が来ても今の小目標が見直されなかった。
    """

    RELATED = "related"  # 今の小目標・中目標のやり方や成否が変わる（近道、よい道具、間違い、失敗の理由）
    PARTLY = "partly"  # 今の大目標や、あとの中目標に関わる。今の一歩は変わらない
    UNRELATED = "unrelated"  # あとのこと・ほかのこと


class PromiseHandling(str, Enum):
    """約束している返答の約束を、どう守るか（docs/design/34 §10）。"""

    NONE = "none"  # ゲームでの行動を約束していない（今していることの説明、気持ち）: そのまま話す
    ALREADY = "already"  # リストの大目標がもうそれをしている: そのまま話す
    NOW = "now"  # 今の小目標を区切って今やる（大目標の先頭）
    LATER = "later"  # 今の大目標の後でやる
    CANNOT = "cannot"  # 条件で書けない・受けられない: 約束しない返答に書き直す


@dataclass(frozen=True)
class ObjectiveProposal:
    """足す大目標: `position` は未完了のものの中での 0 始まりの位置（None: 最後）。"""

    title: str
    conditions: tuple[GoalSpec, ...]
    reason: str = ""
    position: Optional[int] = None
    # 視聴者の頼みを、配信者が待っている間（夜に家の中）に今やる（docs/design/13 §8）
    now: bool = False
    # 大目標ではなく、今の大目標の中目標として足す（docs/design/50 §5）。条件の先頭が中目標の
    # 条件、残りが一緒に追う条件
    job: bool = False


@dataclass(frozen=True)
class PlanChange:
    """
    大目標リストの 1 つの編集（add は `proposal`、move と drop と satisfy は `objective_id`）。
    drop に `job_id` があれば、その大目標の中目標だけをやめる。satisfy の `by` は、その大目標の
    施設の条件を代わりに満たす、もう持っている施設の名前。
    """

    op: PlanOp
    reason: str
    objective_id: str = ""
    job_id: str = ""
    position: Optional[int] = None  # 0 始まり
    proposal: Optional[ObjectiveProposal] = None
    by: str = ""


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
    """次の小目標、それが何のためか、一緒に行う大目標リストの編集。"""

    spec: GoalSpec
    reason: str
    serves: Serves
    changes: tuple[PlanChange, ...] = ()
    # 一番上の大目標のための中目標（書き直すときだけ。空: 今の中目標のまま）。docs/design/26
    jobs: tuple[Job, ...] = ()
    # 前の小目標が失敗で終わったときだけ（docs/design/27）: 原因の分析、対処、やり方の助言
    diagnosis: str = ""
    remedy: Optional[Remedy] = None
    advice: str = ""
    # 毎回: 今の状態で何ができるか、もっと早い・無駄のないやり方はないかの見直し（docs/design/31）
    review: str = ""
    # 毎回: 残りを外出ごとに分けたもの（中目標と小目標の束はこれに従う。docs/design/44 §9）
    trips: str = ""
    # 失敗の後だけ（任意）: 似た状況で思い出す教訓と、その条件（docs/design/35）
    lesson: str = ""
    lesson_when: dict[str, Any] = field(default_factory=dict)
    # 小目標の束（docs/design/44）: spec と一緒に追う条件と、進め方
    together: tuple[GoalSpec, ...] = ()
    approach: str = ""


def _bundle_properties() -> dict[str, Any]:
    """小目標・中目標を束にする欄（docs/design/44）。"""
    return {
        "together": {
            "type": "array",
            "maxItems": MAX_BUNDLE - 1,
            "description": "Optional: other conditions to chase at the same time as this one "
            "(a bundle, 4 in all at most): things got on the same trip, at the same place or with "
            "the same tools, also for other objectives. The goal ends when all hold. Only "
            "conditions judged from the world; night, home and fighting goals and explored are "
            "chased alone",
            "items": {
                "type": "object",
                "properties": _spec_properties(_CONDITION_ORDER),
                "required": ["predicate"],
            },
        },
        "approach": {
            "type": "string",
            "description": "With together: how to get them all in one go (the route, what "
            "first, what to pick up on the way), one Japanese sentence",
        },
    }


def _spec_properties(predicates: list[GoalPredicate]) -> dict[str, Any]:
    return {
        "predicate": {"type": "string", "enum": [p.value for p in predicates]},
        "item": {"type": "string", "description": ITEM_DESCRIPTION},
        "count": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_GOAL_COUNT,
            "description": "have / stored: 1-256; farmed and placed: 1-64; planted: 1-32 "
            "(a bigger number is sent back)",
        },
        "distance": {
            "type": "integer",
            "minimum": MIN_DISTANCE,
            "maximum": MAX_EXPLORE_DISTANCE,
            "description": "explored: blocks to go (8-128); lit: the radius around the home "
            "(8-32); leveled: the radius to level (3-16)",
        },
        "name": {
            "type": "string",
            "description": BUILD_NAME_DESCRIPTION,
        },
        "demolish": {
            "type": "boolean",
            "description": "leveled only: true to take down what stands there too (placed blocks, "
            "the home or builds even though they are protected, trees, farms) while leveling; "
            "water, mines and unbreakable blocks are left. A deliberate choice: use it when "
            "something placed blocks a build or the ground cannot be leveled without removing it",
        },
        "where": {
            "type": "string",
            "description": "placed: where to place the item: home (inside the home's room), "
            "near_home (around the home, 3-12 m; a chest is always kept in the home), build:<name> (at a named build), or x,y,z "
            "(within 2 m of a spot you know, e.g. from find_blocks). Default home. home and "
            "near_home need a finished home: before that use build:<name> or x,y,z. leveled: home "
            "(the ground around the home at its floor) or x,y,z (the ground around it; y is the "
            "height you stand at there, the ground is y-1). Write x,y,z only when you know all "
            "three numbers (the town site line gives the standing height y of a surveyed site); "
            "x,z alone is sent back. A bed goes in the home only: placed(bed, home)",
        },
    }


def _conditions_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "description": "All must hold for the objective to be done (judged from the world). An "
        "objective that builds (built) or lays out a facility (facility) holds only that one "
        "kind of condition: put its materials, leveling and furnishing in the jobs. Never "
        "through_night, at_home, cleared or explored: those are small goals (the predicate of "
        "this decision), not conditions of an objective. Getting through the night, going home "
        "or getting away from a place is a small goal now, never an objective of its own",
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
    objective_ids: list[str],
    note_ids: list[str] | None = None,
    goals: int = 0,
    viewers: list[str] | None = None,
    after_failure: bool = False,
    retry_allowed: bool = True,
    plan_owns_jobs: bool = False,
    died: bool = False,
) -> dict[str, Any]:
    """
    目標の決定の JSON スキーマ: 小目標、大目標リストの編集、自分のメモの編集。

    `goals` は示した小目標の数（lesson の根拠に使える）、`viewers` は示した会話にいる
    視聴者。`after_failure` なら、先頭に原因の分析、次に対処と助言（docs/design/27。
    出力はスキーマの順に書かれるので、分析してから決める）。`retry_allowed` が False なら
    同じ小目標のやり直しは選べない。`plan_owns_jobs` なら、一番上の大目標の中目標は計画（LLM が
    段階と小目標の並びとして書いた。docs/design/50 §18）が持つので、`jobs` は書かせない。
    `died`（失敗のうち、死んだとき。docs/design/105）なら、教訓（lesson）を必ず書かせる。
    """
    change: dict[str, Any] = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": [o.value for o in PlanOp]},
            "title": {"type": "string", "description": "add: the objective's title"},
            "conditions": _conditions_schema(),
            "position": {
                "type": "integer",
                "minimum": 1,
                "description": "add / move: its place in the list, 1 = worked on first",
            },
            "job": {
                "type": "string",
                "description": "drop only: the id of one job (e.g. j3) of the objective in id, "
                "to give up just that job (the id is shown next to the jobs viewers asked for or "
                "suggested); omit to drop the whole objective",
            },
            "by": {
                "type": "string",
                "description": "satisfy only: the name of the finished facility you already have "
                "that does what the objective's facility condition asks (from the built "
                "facilities shown); the bridge checks it and sends the edit back if it does not fit",
            },
            "reason": {"type": "string", "description": "Why, in one short sentence"},
        },
        "required": ["op", "reason"],
    }
    if objective_ids:
        change["properties"]["id"] = {
            "type": "string",
            "enum": objective_ids,
            "description": "move / drop / satisfy: the objective",
        }
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "review": {
                "type": "string",
                "description": "First, every time: from the state now (what you carry, the "
                "chests, what is nearby and remembered, the time, which jobs are already done "
                "or impossible), is there a faster or less wasteful way to the objectives (another "
                "order, other jobs, skipping what is done, bundling what one trip gets: open "
                "jobs and other objectives' conditions at the same place or on the way into one "
                "job with together)? Say what you change, or why the plan stays. 1-2 sentences "
                "in Japanese",
            },
            "trips": {
                "type": "string",
                "description": "Next, every time: group what is left to get (the open jobs of "
                "the objective at the top and the other objectives' conditions) by trip: what one "
                "trip or place gets together (the same direction, on the way, the same place), in "
                "order, e.g. 1) north: have(cobblestone, 48) + have(oak_log, 16) 2) home: built. "
                "The small goal (together) follows the first group; the jobs stay what the "
                "objective needs (things to have, states to reach), not trips",
            },
            "plan_changes": {
                "type": "array",
                "description": "Edits of the objective list that the review calls for, applied in "
                "order (none if the order is still the best)",
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
                "description": "Optional limit: at most how many blocks below where you stand to "
                "dig stairs down to buried stone or ore (omit: no limit, as deep as the stone or "
                "ore is; 0: do not dig down). Lava, water, gravel and bedrock always stop it",
            },
            "serves": {
                "type": "string",
                "enum": [s.value for s in Serves],
                "description": "current: for the objective at the top of the list after the "
                "edits; survival: to stay alive (the night, home, the door, food)",
            },
            "reason": {"type": "string", "description": "Why this goal now, in one short sentence"},
            **_bundle_properties(),
            "jobs": {
                "type": "array",
                "maxItems": MAX_JOBS,
                "description": "The jobs (in order) to reach the objective at the top of the list "
                "after the edits, one job per thing to have or state to reach (a tool, a food "
                "stock, a part of the build), not per trip: the trip that gathers a job's raw "
                "materials is chosen automatically as its next step, and a job that is only a raw "
                "material (have(oak_log, 4)) is sent back. Write them when it has no jobs yet, they are not "
                "working, or the review finds a faster way (a job already done, materials at hand "
                "or in a chest, a nearer source); otherwise leave empty (the jobs stay and the "
                "fast model picks the next one itself)",
                "items": {
                    "type": "object",
                    "properties": {
                        **_spec_properties(_CONDITION_ORDER),
                        "reason": {"type": "string", "description": "Why this job, one sentence"},
                        **_bundle_properties(),
                    },
                    "required": ["predicate", "reason"],
                },
            },
        },
        "required": ["review", "trips", "predicate", "serves", "reason"],
    }
    if plan_owns_jobs:
        del schema["properties"]["jobs"]
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
    tail = {
        "lesson": {
            "type": "string",
            "description": "Optional, last: what to remember from this failure for similar "
            "situations later (general, no coordinates), one Japanese sentence of at most 80 "
            "characters",
        },
        "lesson_when": lesson_when_schema(),
    }
    if died:
        tail["lesson"]["description"] = (
            "Required, last: what to remember from this death so the same death does not "
            "happen again (general, no coordinates; the situation and what you could have done "
            "differently), one Japanese sentence of at most 80 characters"
        )
    schema["properties"] = {**head, **schema["properties"], **tail}
    schema["required"] = ["diagnosis", "remedy", *schema["required"], *(["lesson"] if died else [])]
    return schema


def lesson_when_schema() -> dict[str, Any]:
    """教訓の条件（docs/design/35 §3）: どんなときの教訓か。似た状況で思い出すのに使う。"""
    return {
        "type": "object",
        "description": "When the lesson applies (it is recalled in such situations): a short "
        "phrase, and only the keys that really matter for it (leave the others out)",
        "properties": {
            "text": {
                "type": "string",
                "description": "when it applies, a short Japanese phrase (at most 60 characters)",
            },
            "goal": {"type": "string", "enum": [p.value for p in GoalPredicate]},
            "item": {"type": "string", "description": "the item or build it is about"},
            "time": {"type": "string", "enum": list(SITUATION_VALUES["time"])},
            "place": {"type": "string", "enum": list(SITUATION_VALUES["place"])},
            "body": {"type": "string", "enum": ["hungry", "hurt"]},
            "dark": {
                "type": "string",
                "enum": ["yes"],
                "description": "yes for a lesson about dark places (torches, light): caves are dark "
                "by day too, so do not write it as time=night or place=outside",
            },
        },
    }


def reply_schema() -> dict[str, Any]:
    """
    チャットの返答と、それがコメントをどう扱うかの JSON スキーマ。

    返答と扱いは 1 回の生成から出るので、頼みを受けた返答は必ずそれを大目標に足す。
    返答（reply）は最後に置く: モデルはキーの順に書くので、先に書くと、扱い（when、relevance）を
    決める前に文が決まり、文と扱いが食い違う（「斧があれば楽」と言いながら「今の作業の後で作る」。
    2026-10-07、docs/design/92）。
    """
    return {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "enum": [r.value for r in RequestHandling],
                "description": "none: chat or a question; accept: add the request to the "
                "objectives; decline: not taken (the reply says why); withdraw: the viewer calls "
                "off their own request in the list (e.g. never mind, no need any more)",
            },
            "decline_reason": {
                "type": "string",
                "enum": [r.value for r in DeclineReason],
                "description": "decline only (required then): cannot = no condition (not even "
                "custom(name)) can express it; wrong = the point does not hold in the state now; "
                "unsafe = it risks death or losing the home or items; off_mission = a request "
                "that has nothing to do with the mission and makes nothing easier later (never for "
                "advice); limit = the viewers' limit or a full list. Being busy with something "
                "else is never a reason: accept it with when=next instead",
            },
            "suggestion": {
                "type": "boolean",
                "description": "accept only: true when the comment suggests something for the "
                "streamer's own play (a better way, a farm or tree farm, being efficient or safe), "
                "even when it is phrased as a question or a request; false when it is the viewer's "
                "own wish. A suggestion becomes her own objective and does not count toward the "
                "viewers' limits",
            },
            "relevance": {
                "type": "string",
                "enum": [r.value for r in AdviceRelevance],
                "description": "advice or suggestion only (required then): how much it bears on "
                "what you are doing now (the current small goal, the job and the objective in "
                "'what you are doing now'). related = it changes how or whether to do the current "
                "small goal or job (a shortcut, a better tool, a mistake, why it fails); partly = "
                "it bears on the objective or a later job, not the current step; unrelated = it "
                "is for later or for something else",
            },
            "relevance_reason": {
                "type": "string",
                "description": "advice or suggestion only: why, in one English sentence, naming "
                "the current small goal or job it is compared with",
            },
            "level": {
                "type": "string",
                "enum": ["objective", "job"],
                "description": "accept: objective = an objective of its own (a named build or "
                "facility, or a small thing unrelated to the objective worked on now); job = a job "
                "of the objective worked on now (materials or a job it needs; the first condition "
                "is the job, the others are chased with it). Default objective",
            },
            "title": {"type": "string", "description": "accept: the objective's title"},
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
                "description": "accept: now = cut the current small goal and do it right away "
                "because that is better (it is quick, can be done with what is here, gets harder "
                "later or matters more than the current goal; or you are only waiting, e.g. the "
                "night inside the home); next = after the objective worked on now (default)",
            },
            "rethink": {
                "type": "string",
                "description": "Only when the comment points out something concrete that you can "
                "check in the state (a tree right there, a mistake, a faster way) and you agree it "
                "makes the current small goal or plan wrong or wasteful: what to reconsider, in one "
                "English sentence. The small goal is then decided again with it. Otherwise omit",
            },
            "lesson": {
                "type": "string",
                "description": "Only when the viewer gives advice you agree is right for the "
                "future too (cook meat before eating it, craft a crafting table where you work): "
                "the lesson in your own words, one Japanese sentence of at most 80 characters. It "
                "is kept and recalled in similar situations later. A lesson alone makes nothing "
                "happen: advice whose result can be written as conditions (light the room: "
                "placed(torch, home, 2)) is accepted as an objective as well, and a reply with "
                "only a lesson says it will remember, never that it will do something",
            },
            "lesson_when": lesson_when_schema(),
            "reply": {
                "type": "string",
                "description": "What to say to the viewer. Written after the decisions above and "
                "it must agree with them: when=next means it comes after the current work (do not "
                "say it helps the current work and also that it waits); when=now means you do it "
                "now and say why",
            },
            "name_reading": {
                "type": "string",
                "description": "the viewer's name read aloud, in hiragana: only when the prompt "
                "shows no reading for it yet (your guess), or when the viewer says how to read "
                "their name",
            },
            "self_facts": {
                "type": "array",
                "description": "Only when the reply says something about yourself that is not in "
                "what you said about yourself before (what you like, play, are good or bad at, "
                "have done; e.g. your main champion in LoL): each one to remember, so you never "
                "say otherwise later. Not facts about the world or a game in general",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "a short Japanese topic, at most 30 characters "
                            "(LoLのメイン, 好きな食べ物)",
                        },
                        "fact": {
                            "type": "string",
                            "description": "what you said, one Japanese sentence of at most "
                            "120 characters",
                        },
                    },
                    "required": ["topic", "fact"],
                },
            },
        },
        "required": ["reply", "request"],
    }


def promise_schema(allow_now: bool = True) -> dict[str, Any]:
    """
    約束している返答の約束をどう守るかの JSON スキーマ（docs/design/34 §10）。

    allow_now: 断った返答（decline）では今やらない（話と行動が食い違う）ので now を出さない。
    """
    decisions = [
        d.value for d in PromiseHandling if allow_now or d is not PromiseHandling.NOW
    ]
    return {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": decisions,
                "description": "none: the reply promises no new action in the game (it tells "
                "what the streamer is doing now, feelings, trying hard); already: an objective in "
                "the list already has these conditions (what only a survival small goal is doing "
                "does not count: make it an objective); now: cut the current small goal and do it "
                "right away; later: after the objective worked on now (also when it is not the "
                "most important now); cannot: only when its result cannot be written as "
                "conditions",
            },
            "level": {
                "type": "string",
                "enum": ["objective", "job"],
                "description": "now / later (required): decide this FIRST, before the title and "
                "conditions. objective = an objective of its own (a named build or facility, or a "
                "wish unrelated to the objective worked on now). job = a job of the objective "
                "worked on now: a tool or material it needs, or the one step that removes what "
                "blocks it (a pickaxe to get out of a hole and go on, wood for the planks it "
                "uses). When in doubt between the two, it is a job. The first condition is the "
                "job, the others are chased with it",
            },
            "title": {"type": "string", "description": "now / later: the title"},
            "conditions": _conditions_schema(),
            "position": {
                "type": "integer",
                "minimum": 2,
                "description": "later: its place in the list (2 = right after the one worked "
                "on now)",
            },
            "reason": {
                "type": "string",
                "description": "why it helps (now / later: and why now or later), or why it "
                "cannot be done, in one sentence",
            },
            "rewritten_reply": {
                "type": "string",
                "description": "cannot: the reply to the viewer written again without the "
                "promise, spoken as it is (same tone, still answering the comment; say it cannot "
                "be done yet or that you will remember it, never that you will do it). Otherwise "
                "an empty string",
            },
        },
        "required": ["decision", "reason", "rewritten_reply"],
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
            item=str(data["item"]) if predicate in _ITEM_PREDICATES else None,
            count=int(data["count"])
            if predicate in _COUNT_PREDICATES
            else _placed_count(data)
            if predicate == GoalPredicate.PLACED
            else None,
            where=_where(data, predicate)
            if predicate in (GoalPredicate.PLACED, GoalPredicate.LEVELED)
            else None,
            distance=int(data["distance"])
            if predicate in (GoalPredicate.EXPLORED, GoalPredicate.LIT, GoalPredicate.LEVELED)
            else None,
            dig_depth=int(data["dig_depth"]) if data.get("dig_depth") is not None else None,
            name=str(data["name"]).strip().lower() or None
            if predicate in (GoalPredicate.BUILT, GoalPredicate.CUSTOM, GoalPredicate.FACILITY)
            and data.get("name")
            else None,
            started=bool(data.get("started", False)),
            demolish=bool(data.get("demolish", False)) and predicate == GoalPredicate.LEVELED,
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"missing or malformed argument: {e}") from e


def _where(data: dict[str, Any], predicate: GoalPredicate) -> str:
    """
    置く場所。チェストは家の中にまとめる: near_home と書かれても home に読む（2026-10-08: チェストの増設を
    near_home と書き、家の外に 1 つ置いた。user: 「チェストはまとめて家の中において欲しい」）。build:<名前> と
    x,y,z は、置く場所を決めて書いたものなので、そのまま。
    """
    where = str(data.get("where") or "home").strip()
    if predicate == GoalPredicate.PLACED and data.get("item") == "chest" and where == "near_home":
        return "home"
    return where


def _placed_count(data: dict[str, Any]) -> int | None:
    """placed の数（1 なら書かない: 家具の placed(bed, home) と同じ形のまま）。"""
    count = data.get("count")
    return int(count) if count is not None and int(count) != 1 else None


def parse_proposal(data: dict[str, Any]) -> ObjectiveProposal:
    """
    足す大目標をパースする（題名、条件、1 始まりの位置、理由）。

    Raises:
        ValueError: 題名か条件がないか、形が正しくないとき。
    """
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("an objective needs a title")
    raw = data.get("conditions") or []
    if not raw:
        raise ValueError(
            f"objective {title} needs conditions: the world states that mean it is done, e.g. "
            "[{\"predicate\": \"placed\", \"item\": \"torch\"}] for a torch in the home"
        )
    conditions = tuple(parse_spec(c) for c in raw)
    for c in conditions:
        if c.predicate not in PLANNABLE_CONDITIONS:
            raise ValueError(f"{c.predicate.value} cannot be a condition of an objective")
    job = data.get("level") == "job"
    if job:
        check_bundle(conditions[0], conditions[1:])
    else:
        objective_kind(conditions)  # 設計や判定の前に、建てる・施設に材料を混ぜていないか確かめる
    position = data.get("position")
    return ObjectiveProposal(
        title=title,
        conditions=conditions,
        reason=str(data.get("reason", "")),
        position=int(position) - 1 if position is not None else None,
        now=data.get("when") == "now",
        job=job,
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
        objective_id = str(raw.get("id", ""))
        if not objective_id:
            raise ValueError(f"{op.value} needs the id of an objective")
        if op == PlanOp.MOVE and position is None:
            raise ValueError(f"move {objective_id} needs a position")
        job_id = str(raw.get("job") or "")
        if job_id and op != PlanOp.DROP:
            raise ValueError(f"only drop can name a job, not {op.value}")
        by = str(raw.get("by") or "").strip()
        if op == PlanOp.SATISFY and not by:
            raise ValueError(
                f"satisfy {objective_id} needs by: the name of the finished facility that already does it"
            )
        if by and op != PlanOp.SATISFY:
            raise ValueError(f"only satisfy can name a facility with by, not {op.value}")
        changes.append(
            PlanChange(
                op=op,
                reason=reason,
                objective_id=objective_id,
                job_id=job_id,
                position=int(position) - 1 if position is not None else None,
                by=by,
            )
        )
    jobs = []
    for i, raw in enumerate(data.get("jobs") or []):
        try:
            together, approach = parse_bundle(raw)
            jobs.append(
                Job(
                    parse_spec(raw),
                    str(raw.get("reason", "")).strip(),
                    together=together,
                    approach=approach,
                )
            )
        except ValueError as e:
            raise ValueError(f"job {i + 1}: {e}") from e
    remedy = None
    if data.get("remedy"):
        try:
            remedy = Remedy(data["remedy"])
        except ValueError as e:
            raise ValueError(f"remedy must be one of {[r.value for r in Remedy]}") from e
    spec = parse_spec(data)
    together, approach = parse_bundle(data)
    check_bundle(spec, together)
    return GoalDecision(
        spec=spec,
        reason=str(data.get("reason", "")),
        serves=serves,
        changes=tuple(changes),
        jobs=_jobs_with_the_bundle(spec, together, approach, tuple(jobs[:MAX_JOBS])),
        diagnosis=str(data.get("diagnosis", "")).strip(),
        remedy=remedy,
        advice=str(data.get("advice", "")).strip(),
        review=str(data.get("review", "")).strip(),
        trips=str(data.get("trips", "") or "").strip(),
        lesson=str(data.get("lesson", "") or "").strip(),
        lesson_when=dict(data["lesson_when"]) if isinstance(data.get("lesson_when"), dict) else {},
        together=together,
        approach=approach,
    )


def _jobs_with_the_bundle(
    spec: GoalSpec, together: tuple[GoalSpec, ...], approach: str, jobs: tuple[Job, ...]
) -> tuple[Job, ...]:
    """
    小目標を束にしたのに、中目標ではその条件を別々に書いたとき、それらを 1 つの束の中目標にまとめる
    （次に Jev が中目標から選ぶとき、束がばらけないように。docs/design/44）。
    """
    if not together:
        return jobs
    bundle = {spec, *together}
    inside = [i for i, s in enumerate(jobs) if set(s.specs) <= bundle]
    if not inside:
        return jobs
    first = inside[0]
    merged = Job(spec, jobs[first].reason, together=together, approach=approach)
    return tuple(merged if i == first else s for i, s in enumerate(jobs) if i not in inside[1:])


def parse_bundle(data: dict[str, Any]) -> tuple[tuple[GoalSpec, ...], str]:
    """
    束の一緒に追う条件と進め方をパースする（docs/design/44）。

    Raises:
        ValueError: 条件の形が正しくないとき
    """
    together = []
    for i, raw in enumerate(data.get("together") or []):
        try:
            together.append(parse_spec(raw))
        except ValueError as e:
            raise ValueError(f"together {i + 1}: {e}") from e
    approach = str(data.get("approach", "") or "").strip() if together else ""
    return tuple(together), approach


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


def predicates_now(
    obs: GameObservation, plan: ObjectivePlan, tools: bool = True
) -> list[GoalPredicate]:
    """
    今意味のある述語（例: 家ができる前は、家についての述語は出さない）。調査は、それを
    求める大目標があるときだけ（大目標を足した直後の観測には、調査の計画がまだない）。
    custom は、それを条件に持つ大目標があって、道具で進めるときだけ（ブリッジは候補を出さない）。
    """
    out = []
    if (obs.has_plan and not obs.house_complete) or obs.unfinished_builds:
        out.append(GoalPredicate.BUILT)
    out.append(GoalPredicate.HAVE)
    if obs.has_home:
        out.append(GoalPredicate.AT_HOME)
    # 夜は家がなくても越せる（近くのベッド、持っているベッド、地下）
    out.append(GoalPredicate.THROUGH_NIGHT)
    if obs.has_home:
        # 夜も、武器があれば出て倒せる（ブリッジが武器と体力を確かめ、持ち物を先にチェストへ）
        out.append(GoalPredicate.CLEARED)
        # ベッドのほか、作業台・かまど・チェストも家の中に置ける（済んでいれば、ブリッジがすぐ達成とする）
        out.append(GoalPredicate.PLACED)
        out.append(GoalPredicate.STORED)
        out.append(GoalPredicate.LIT)
        # 植林と畑（設計書 33）: 家のまわり
        out += [GoalPredicate.PLANTED, GoalPredicate.FARMED]
        # 整地（設計書 41）: 家のまわりか、x,y,z のまわり
        out.append(GoalPredicate.LEVELED)
    if obs.has_home and any(
        c.predicate == GoalPredicate.SURVEYED for g in plan.pending for c in g.conditions
    ):
        out.append(GoalPredicate.SURVEYED)
    if tools and any(
        c.predicate == GoalPredicate.CUSTOM for g in plan.pending for c in g.conditions
    ):
        out.append(GoalPredicate.CUSTOM)
    # 設計した施設（docs/design/47 §7）: それを条件に持つ大目標があるときだけ
    if obs.has_home and any(
        c.predicate == GoalPredicate.FACILITY for g in plan.pending for c in g.conditions
    ):
        out.append(GoalPredicate.FACILITY)
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
