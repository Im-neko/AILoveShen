"""What the streamer is doing and what was said, written the same way for every prompt.

The goal decision, the commentary and the chat replies all describe the streamer's activity with
`format_activity`, so none of them works from a different picture of what is going on.
"""

from __future__ import annotations

import json

from ailoveshen.domain.value_objects import (
    CONDITION_PREDICATES,
    Activity,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalOutcome,
    GoalPredicate,
    MessageRole,
    MidGoal,
    MidGoalState,
)

NO_INFORMATION = "特になし"

# What the Minecraft bridge's primitives and reflexes do (minecraft-bridge/src/primitives.mjs,
# candidates.mjs): keep in step with them, so replies never promise what the streamer cannot do
ABILITIES = """\
- 木を切る、石・石炭・鉄を掘る、動物を狩る、道具・ベッド・チェストなどをクラフトする
- かまどで焼く（鉄の延べ棒、木炭、焼いた肉）。鉄の道具や剣、石炭がなくても木炭で松明が作れる
- 家を 1 軒建てる、ベッドで寝る、家のチェストに物を入れる・出す
- 近くの敵と戦う・逃げる、お腹が空いたら食べる
- 地上を方角を決めて探索する、前に見た場所（資源・動物・チェスト）を覚えていて戻る
- 暗い場所（洞窟の入口や張り出しの下など、光のない所）では、松明を持っていれば置いて湧き潰しする
- 家のまわりの地面に松明を並べて、敵が湧かないように明るくする
- まだできない: 洞窟の奥へ降りて探検する、2 軒目の建物、畑、釣り、ネザー"""

PREDICATE_DESCRIPTIONS: dict[GoalPredicate, str] = {
    GoalPredicate.BUILT: "built: 設計図の家を完成させる（材料集めとクラフトも含めて進む）",
    GoalPredicate.HAVE: (
        "have(item, count): アイテムを count 個持つ。item はアイテム名かグループ"
        "（planks, log, door, bed, wool, food）。例: have(wooden_sword, 1)、have(food, 4)"
    ),
    GoalPredicate.STORED: (
        "stored(item, count): 家のチェストに count 個しまってある状態にする"
        "（チェストがなければ作って家に置くところから）。使っても減らない備蓄になる。"
        "例: stored(food, 16)、stored(log, 32)"
    ),
    GoalPredicate.LIT: (
        "lit(distance): 家のまわり半径 distance ブロックの地面に、暗い所（敵が湧く所）がない状態にする"
        "（松明を置いて湧き潰し。松明がなければ作るところから）。distance は 8〜32。例: lit(16)"
    ),
    GoalPredicate.PLACED: (
        "placed(item=bed): 家の中にベッドを置く（ベッドがなければ作るところから）"
    ),
    GoalPredicate.AT_HOME: "at_home: 家に入ってドアを閉める",
    GoalPredicate.THROUGH_NIGHT: "through_night: 家で夜を越す（ベッドがあれば寝る）",
    GoalPredicate.EXPLORED: (
        "explored(distance): 今いる場所から distance ブロック離れるまで探索する"
    ),
    GoalPredicate.CLEARED: (
        "cleared: ドアの近くで待ち構える敵を外に出て倒す（昼だけ。素手でも戦える。"
        "クリーパーは近くで爆発するので対象外）"
    ),
}

TICKS_PER_MINUTE = 20 * 60
DUSK_TICK = 12000
MORNING_TICK = 24000
PHASE_NAMES = {"day": "昼", "dusk": "夕方", "night": "夜", "dawn": "明け方"}
DIRECTION_NAMES = {
    "N": "北",
    "NE": "北東",
    "E": "東",
    "SE": "南東",
    "S": "南",
    "SW": "南西",
    "W": "西",
    "NW": "北西",
}
EQUIPMENT_NAMES = {"head": "頭", "chest": "胴", "legs": "脚", "feet": "足", "off_hand": "左手"}


def format_predicates(predicates: list[GoalPredicate]) -> str:
    """The goals that can be set now, one per line."""
    return "\n".join(f"- {PREDICATE_DESCRIPTIONS[p]}" for p in predicates)


def format_conditions() -> str:
    """What a mid goal's completion conditions can be, one per line."""
    return format_predicates([p for p in GoalPredicate if p in CONDITION_PREDICATES])


def format_activity(activity: Activity | None, with_ids: bool = False) -> str:
    """
    What the streamer is doing and why, from the mission down, the game situation, and the
    recent small goals. `with_ids` shows the mid goals' ids (for the goal decision's edits;
    not where they could be read out on stream).
    """
    if activity is None:
        return "ゲームはしていない"
    titles = {g.id: g.title for g in activity.mid_goals}
    lines = []
    if activity.mission is not None:
        lines.append(f"- 大目標: {activity.mission.text}")
    if activity.town is not None:
        lines += _format_town(activity)
    pending = [g for g in activity.mid_goals if g.state == MidGoalState.PENDING]
    finished = [g for g in activity.mid_goals if g.state != MidGoalState.PENDING]
    lines.append("- 中目標（上から順に取り組む）:")
    lines += [
        f"  {i}. {_format_mid_goal(g, with_ids, current=i == 1)}" for i, g in enumerate(pending, 1)
    ] or ["  - なし"]
    if finished:
        lines.append("- 最近終わった中目標:")
        lines += [f"  - {_format_finished(g)}" for g in finished]
    lines.append(f"- 今の小目標: {_format_goal(activity.goal, activity.observation, titles)}")
    if activity.observation is not None:
        lines.append(_format_situation(activity.observation))
    lines.append("- これまでの小目標（古い順）:")
    lines.append(
        "\n".join(f"  - {_format_outcome(o, titles)}" for o in activity.recent_goals) or "  - なし"
    )
    return "\n".join(lines)


def format_messages(messages: tuple[ConversationMessage, ...] | list[ConversationMessage]) -> str:
    """Conversation history, one message per line."""
    if not messages:
        return NO_INFORMATION
    lines = []
    for message in messages:
        speaker = f"{message.speaker_name}さん" if message.role == MessageRole.VIEWER else "あなた"
        lines.append(f"{speaker}: {message.content}")
    return "\n".join(lines)


def format_time(time: dict) -> str:
    """Time of day in Japanese, with the minutes until dusk or morning."""
    phase = time.get("phase", "")
    name = PHASE_NAMES.get(phase, "不明")
    tick = time.get("time_of_day")
    if tick is None:
        return name
    if phase == "day":
        return f"{name}（日暮れまで約 {(DUSK_TICK - tick) / TICKS_PER_MINUTE:.0f} 分）"
    return f"{name}（朝まで約 {(MORNING_TICK - tick) / TICKS_PER_MINUTE:.0f} 分）"


def format_time_en(time: dict) -> str:
    """Time of day in English (the action selector's language)."""
    phase = time.get("phase", "")
    tick = time.get("time_of_day")
    if tick is None:
        return phase
    if phase == "day":
        return f"day ({(DUSK_TICK - tick) / TICKS_PER_MINUTE:.0f} minutes until dusk)"
    return f"{phase} ({(MORNING_TICK - tick) / TICKS_PER_MINUTE:.0f} minutes until morning)"


def _requested(goal: MidGoal) -> str:
    return f"（{goal.requested_by}さんの頼み）" if goal.requested_by else ""


def _format_memory(memory: dict) -> str:
    def where(p: dict) -> str:
        direction = DIRECTION_NAMES.get(p["direction"], p["direction"])
        return f"{direction} {p['distance_m']}m、{p['minutes_ago']} 分前"

    parts = [f"{p['kind']} {p['count']}（{where(p)}）" for p in memory.get("places", [])]
    parts += [f"死んだ場所（{where(d)}）" for d in memory.get("deaths", [])]
    return "、".join(parts) or "なし"


def _format_chests(memory: dict) -> str:
    chests = memory.get("chests", [])
    if not chests:
        return "なし"

    def contents(c: dict) -> str:
        items = "、".join(f"{name} {n}" for name, n in c["contents"].items()) or "空"
        return f"{items}（{c['minutes_ago']} 分前に開けたとき）"

    return " / ".join(contents(c) for c in chests)


def _format_equipment(me: dict) -> str:
    worn = [
        f"{EQUIPMENT_NAMES[part]} {item}"
        for part, item in (me.get("equipment") or {}).items()
        if item and part in EQUIPMENT_NAMES
    ]
    held = [f"手に {me['held_item']}"] if me.get("held_item") else []
    return "、".join(held + worn) or "なし"


def _format_town(activity: Activity) -> list[str]:
    town = activity.town
    assert town is not None
    n = len(town.stages)
    lines = [f"- 街の定義: {town.text}"]
    if activity.town_stage >= n:
        return [*lines, f"  - 街は完成した（全 {n} 段階）"]
    for i, stage in enumerate(town.stages):
        mark = "済" if i < activity.town_stage else "今" if i == activity.town_stage else "先"
        lines.append(f"  {i + 1}. [{mark}] {stage.title}: {stage.why}")
        if i == activity.town_stage and not stage.ready:
            waits = "; ".join(stage.unresolved)
            lines.append(f"     まだできないこと（できるようになるまで進めない）: {waits}")
    return lines


def _format_mid_goal(goal: MidGoal, with_ids: bool, current: bool) -> str:
    conditions = ", ".join(c.describe() for c in goal.conditions)
    summary = goal.summary()
    progress = f"（{'; '.join(summary)}）" if summary else ""
    stage = f" [街の段階 {goal.stage + 1}: やめられない]" if goal.stage is not None else ""
    return (
        f"{f'[{goal.id}] ' if with_ids else ''}{goal.title}{_requested(goal)}{stage}"
        f"{' [取り組み中]' if current else ''} 完了条件: {conditions}{progress}"
    )


def _format_finished(goal: MidGoal) -> str:
    how = "完了" if goal.state == MidGoalState.DONE else f"断念: {goal.ended_because}"
    return f"{goal.title}{_requested(goal)}（{how}）"


def _serves(goal: Goal, titles: dict[str, str]) -> str:
    if goal.mid_goal_id is None:
        return "（身を守るため）"
    title = titles.get(goal.mid_goal_id)
    return f"（「{title}」のため）" if title else ""


def _format_goal(goal: Goal | None, obs: GameObservation | None, titles: dict[str, str]) -> str:
    if goal is None:
        return "まだない"
    lines = [f"{goal.spec.describe()}{_serves(goal, titles)}: {goal.reason}"]
    if obs is not None and obs.goal is not None:
        lines += [f"  {line}" for line in obs.goal.lines]
        if obs.goal.blocked:
            lines.append("  進められない理由: " + "; ".join(obs.goal.blocked))
    return "\n".join(lines)


def _format_outcome(o: GoalOutcome, titles: dict[str, str]) -> str:
    result = "達成" if o.met else "未達成"
    return (
        f"{o.goal.spec.describe()}{_serves(o.goal, titles)}: {o.goal.reason}"
        f"（{result}、終了: {o.ended_because}）"
    )


def _format_home(obs: GameObservation) -> str:
    if not obs.has_home:
        return "まだない（夜までに建てる必要がある）"
    name = (obs.state.get("home") or {}).get("name")
    where = "家の中にいる" if obs.inside_home else "完成している（外にいる）"
    bed = "ベッドあり" if obs.bed_in_home else "ベッドなし"
    chests = len((obs.state.get("memory") or {}).get("chests", []))
    return f"{f'{name}、' if name else ''}{where}、{bed}、チェスト {chests}"


def _format_situation(obs: GameObservation) -> str:
    s = obs.state
    lines = [
        f"- 時間帯: {format_time(s.get('time', {}))}",
        f"- 家: {_format_home(obs)}",
        f"- 体力 {obs.health}/20、満腹度 {obs.food}/20",
        f"- 装備: {_format_equipment(s.get('self', {}))}",
        f"- 持ち物: {json.dumps(s.get('inventory', {}), ensure_ascii=False)}",
        f"- 気をつけること: {'、'.join(n for n in obs.needs if n != 'none') or 'なし'}",
        f"- チェストの中身: {_format_chests(s.get('memory') or {})}",
        f"- 覚えている場所（前に見た、今は見えない）: {_format_memory(s.get('memory') or {})}",
    ]
    recent = s.get("recent_actions", [])
    if recent:
        lines.append(
            "- 直近の行動: "
            + " / ".join(
                f"{a['action']}=成功" if a["ok"] else f"{a['action']}=失敗（{a.get('result', '')}）"
                for a in recent
            )
        )
    return "\n".join(lines)
