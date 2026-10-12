"""配信者がしていることと話したことを、どのプロンプトにも同じ書き方で出す。

目標の決定、実況、チャットへの返答は、どれも配信者の活動を `format_activity` で書く。
そのため、どれかだけが違う状況の認識で動くことはない。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ailoveshen.application.ports.output.text_generator import CACHE_BREAK
from ailoveshen.domain.value_objects import (
    HERE_SITE,
    PLANNABLE_CONDITIONS,
    Activity,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalOutcome,
    GoalPredicate,
    Job,
    Lesson,
    MessageRole,
    MessageType,
    JobState,
    Objective,
    ObjectiveState,
    Note,
    NoteKind,
    ScreenNote,
    TownSite,
)

NO_INFORMATION = "特になし"

# Minecraft ブリッジのプリミティブと反射ができること（minecraft-bridge/src/primitives.mjs、
# candidates.mjs）。返答で配信者ができないことを約束しないよう、それらと揃えておくこと
ABILITIES = """\
- 木を切る、石・石炭・鉄を掘る、動物を狩る、道具・ベッド・チェストなどをクラフトする
- 花を摘んで染料を作り、羊毛を染めて色つきのベッドを作る（青: ヤグルマギクかラピスラズリ、赤: ケシ…）
- バニラの作業台・かまどのレシピはすべて知っていて作れる（ランタン、盾、ガラス…。知っている物は
  名前で目標にしてよい。石切台・鍛冶台はまだ使えない）
- かまどで焼く（鉄の延べ棒、木炭、焼いた肉）。鉄の道具や剣、石炭がなくても木炭で松明が作れる
- 家を 1 軒建てる、ベッドで寝る、家のチェストに物を入れる・出す
- 夜の越し方は選べる: 家に帰る、近くのベッド（村など）で寝る、持っているベッドをその場に置いて寝る、
  地下にいればそのまま作業を続ける。家の中にいる夜は外に出ず、持っている物からクラフトできる
- 作業台・かまどは家の中にも外にも置ける。遠くの作業台まで歩くか、その場でもう 1 つ作るかは自分で決める
- 置いてある家具（ベッド、作業台、かまど、チェスト）を拾って置き直す: 近くに置いてあるベッドを家に
  運ぶ、家の中の配置を変える（家の家具は家の中でだけ動かす。中身の入ったチェストは先に中身を出す）
- 近くの敵と戦う・逃げる、お腹が空いたら食べる
- 地上を方角を決めて探索する、前に見た場所（資源・動物・チェスト）を覚えていて戻る
- 土の下に埋まった石や鉱石まで、階段を掘って下りる（どこまで下りるかは自分で決める）
- 暗い場所（洞窟の入口や張り出しの下など、光のない所）では、松明を持っていれば置いて湧き潰しする
- 家のまわりの地面に松明を並べて、敵が湧かないように明るくする
- 整地: 家のまわりや決めた場所のまわり（半径 3〜16）の地面を 1 つの高さにそろえる（出っ張りを掘り、
  穴を土で埋める。家・建物・木・畑は触らない）
- 街の候補地を見て回り、地形（平らな土地、水、崖、地表の石・石炭・鉄、木、動物）を数える
- 選んだ場所に家を建てて引っ越す（前の家は残り、そのチェストから物を運べる）
- 建物を自分で設計して建てる: 家の増築（東西南北のどれかに部屋を足して、壁に入口を開ける）、
  家の近くの倉庫・塔・小屋・塀、大きな建物（城、大広間）など。材料は板材・原木・丸石・土。
  1 つの建物は 3000 ブロック・1 辺 48・高さ 16 まで（視聴者の頼みは 600 ブロックまで）
  （大目標の条件に built(新しい名前) を書くと、そのあとで設計する）
- ほかの条件で書けない頼み（物の並び、動物をどこかに集める、特定の形）も、完了の判定を自分で
  書いて大目標にできる（custom(name)。判定は世界を読む。進め方は道具を組み合わせて自分で考える）
- 家のまわりに苗木を植える（葉を壊して苗木を集める）、畑を作る（クワで耕し、草から取った種や
  にんじん・じゃがいもを植え、育ったら収穫してまたまく）
- ボートで水の上を渡る: 遠くへ行くとき途中に長い水があれば、ボートを持っていれば自分で乗って渡り、
  岸で降りて拾う。ボートは同じ木の板材 5 枚で作る（海や湖の向こうへ行くならボートを持っていく）
- アイテムを使う（右クリック）: 骨粉で育てる、バケツで水を汲む・注ぐ、ハサミで羊の毛を刈る、2 匹の
  動物に餌をやって増やす、など。火打石・溶岩バケツ・TNT は使わない
- まだできない: 洞窟の奥へ降りて探検する、釣り、ネザー、ガラスや階段・柵を建物に使う
  （作ることはできる）"""

PREDICATE_DESCRIPTIONS: dict[GoalPredicate, str] = {
    GoalPredicate.BUILT: (
        "built: 設計図の家を完成させる（材料集めとクラフトも含めて進むが、小目標では材料を"
        "先に have で集め、built は置くだけの仕上げに使う）。"
        "built(name): 自分で設計した建物 name を完成させる（増築、倉庫、塔など。"
        "新しい name を大目標の条件に書くと、足すときに設計する）"
    ),
    GoalPredicate.HAVE: (
        "have(item, count): 合計 count 個持つ（あと count 個ではない。11 個持って 16 個ほしいなら 16）。"
        "item はアイテム名かグループ"
        "（planks, log, door, bed, wool, food）。例: have(wooden_sword, 1)、have(food, 4)"
    ),
    GoalPredicate.STORED: (
        "stored(item, count): 家のチェストに count 個しまってある状態にする"
        "（チェストがなければ作って家に置くところから）。使っても減らない備蓄になる。"
        "例: stored(food, 16)、stored(log, 32)"
    ),
    GoalPredicate.LEVELED: (
        "leveled(where, distance): where のまわり半径 distance（3〜16）の地面をその高さに"
        "そろえる（整地: 出っ張った土や石を掘り、穴を土で埋める。家・建物・木・畑・水・置いた物は"
        "触らない）。where は home（家の床の高さ）か x,y,z（そこに立つ高さ。地面は y-1）。"
        "例: leveled(home, 8)"
    ),
    GoalPredicate.CUSTOM: (
        "custom(name): ほかの条件で書けない、世界に残る結果（花を家の前に並べる、羊を柵に 3 匹）。"
        "新しい name を書くと、大目標の題名と理由から判定を書く。ほかの条件で書けるならそちらを"
        "使う。進め方は道具で選ぶ（提案は出ない）。例: custom(sheep_in_pen_3)"
    ),
    GoalPredicate.LIT: (
        "lit(distance): 家のまわり半径 distance ブロックの地面に、暗い所（敵が湧く所）がない状態にする"
        "（松明を置いて湧き潰し。松明がなければ作るところから）。distance は 8〜32。例: lit(16)"
    ),
    GoalPredicate.PLACED: (
        "placed(item, where, count): 置ける物（松明、ランタン、bed（blue_bed など色も）/ crafting_table / furnace / chest…）を where（home 部屋の中 / "
        "near_home / build:名前 / x,y,z）に count 個（既定 1）置く。なければ作る。例: placed(torch, home)"
    ),
    GoalPredicate.AT_HOME: "at_home: 家に入ってドアを閉める",
    GoalPredicate.THROUGH_NIGHT: (
        "through_night: 夜を越す（家に帰る、近くのベッドや持っているベッドで寝る、どれでもよい）"
    ),
    GoalPredicate.EXPLORED: (
        "explored(distance): 今いる場所から distance ブロック離れるまで探索する"
    ),
    GoalPredicate.FOUND: (
        "found(item, distance): item（sheep などのモブ、plains などの群系）が見つかるまで最大 distance "
        "ブロック探して歩く。見つからなければ行き止まりで差し戻す。羊は草原（plains、meadow）に多い。"
        "探すものがあるときは explored でなく found"
    ),
    GoalPredicate.SURVEYED: (
        "surveyed(count): 街の候補地を count か所見て回る（1 か所ずつ歩いて行き、地形を数える）"
    ),
    GoalPredicate.PLANTED: (
        "planted(item, count): 植林場（facility で設計したもの）に苗木を植え、植えた木を count 本に"
        "する（育った木も数える）。item は sapling か oak_sapling など。苗木は葉を壊すとときどき落ちる。"
        "植林場がないか空きがなければ、先に facility で別の名前の植林場を足す。例: planted(sapling, 4)"
    ),
    GoalPredicate.FARMED: (
        "farmed(item, count): 畑（facility で設計した畑と前からの家の畑の合計）に作物を count マス"
        "植える。item は wheat（種は草からときどき）か、持っていれば carrots / potatoes /"
        " beetroots。畑がないか空きがなければ、先に facility で別の名前の畑を足す。"
        "収穫は have。例: farmed(wheat, 9)"
    ),
    GoalPredicate.FACILITY: (
        "facility(name): 施設（畑、植林場、採掘場）を設計どおりに作る（畑は全部耕してまく、植林場は全部植える、"
        "採掘場は底までのらせん階段と坑道を全部掘る）。"
        "新しく耕す・植えるのは施設のマスだけなので、畑や植林場を作る・増やすときは先にこれを大目標の"
        "条件にする（新しい name を書くと、街の理想図の区画に配置を設計してから作る。理想図に予定の施設が"
        "あればその名前で）。できた施設は広げられない: 増やすときは別の名前（例: wheat_farm_2）。"
        "例: facility(wheat_farm)"
    ),
    GoalPredicate.CLEARED: (
        "cleared: ドアの近くで待ち構える敵を外に出て倒す（昼は素手でも。夜は剣か斧があり体力 14 以上の"
        "とき、持ち物を先に家のチェストへ預けてから。敵がいて眠れない夜に。クリーパーは対象外）"
    ),
}

TICKS_PER_MINUTE = 20 * 60
DUSK_TICK = 12000
NIGHT_TICK = 13000
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
CHEST_ITEMS = 6  # チェストごとに見せる品の数
DEATHS_SHOWN = 2  # 見せる死んだ場所の数（新しいもの）
EQUIPMENT_NAMES = {"head": "頭", "chest": "胴", "legs": "脚", "feet": "足", "off_hand": "左手"}


def format_predicates(predicates: list[GoalPredicate]) -> str:
    """今設定できる目標。1行に 1つ。"""
    return "\n".join(f"- {PREDICATE_DESCRIPTIONS[p]}" for p in predicates)


def format_conditions() -> str:
    """大目標の完了条件に使えるもの。1行に 1つ。"""
    return format_predicates([p for p in GoalPredicate if p in PLANNABLE_CONDITIONS])


def site_name(site_id: str) -> str:
    """候補地の id の日本語（here: 最初の家の場所、N: 北、...）。"""
    return "最初の家の場所" if site_id == HERE_SITE else DIRECTION_NAMES.get(site_id, site_id)


def format_site_facts(row: dict) -> str:
    """ブリッジが測った候補地 1 か所の数字（半径 32 ブロック）。"""
    stand = f" 立つ高さ y={row['stand_y']}" if row.get("stand_y") is not None else ""
    return (
        f"{site_name(str(row['id']))}（x={row['x']} z={row['z']}{stand}、家から {row['distance']}m）: "
        f"家を建てられる平らな区画 {row['flat_plots']}、水 {row['water_pct']}%、"
        f"急な段差 {row['steep_pct']}%、地表の石 {row['stone']}・石炭 {row['coal']}・"
        f"鉄 {row['iron']}、原木 {row['logs']}、溶岩 {row['lava']}、動物 {row['animals']}"
        f"（読み込めた範囲 {row['loaded_pct']}%）"
    )


def format_site_choice(site: TownSite, stand_y: int | None = None) -> str:
    """選んだ街の場所（決めたこと）。`stand_y` はそこに立つ足の高さ（where に x,y,z と書くときの y）。"""
    at = f"{site.x},{stand_y},{site.z}" if stand_y is not None else f"{site.x},{site.z}"
    return f"「{site.name}」を{site_name(site.site_id)}（{at}）に作る: {site.reason}"


# 道具を選ぶ 1 手（step）に出す、直近の小目標の数
STEP_RECENT_GOALS = 3


def format_activity(
    activity: Activity | None, with_ids: bool = False, cache_break: bool = False, step: bool = False
) -> str:
    """
    配信者が何をしていて、なぜか（最終目標から下へ）、ゲームの状況、最近の小目標。
    `with_ids` なら大目標とメモの ID、小目標の番号（メモの根拠）を出す（目標の決定での
    編集用。配信で読み上げられうる所では出さない）。
    `cache_break` なら、目標の階層（変わりにくい）と今の小目標以下（変わる）の間にキャッシュの境目の印を置く。
    `step` なら、道具を 1 つ選ぶ手のための短い形: 調べた候補地の一覧・街の定義・終わった大目標・済んだ定石・
    すり減っていない道具の耐久・直近の行動（「直近の道具」の節と重なる）を出さず、直近の小目標は同じものを 1 つにして新しい 3 つだけ
    （街づくりの見直しの材料で、1 手の選択には要らない。設計書 174）。
    """
    if activity is None:
        return "ゲームはしていない"
    titles = {g.id: g.title for g in activity.objectives}
    lines = []
    if activity.mission is not None:
        lines.append(f"- 最終目標: {activity.mission.text}")
    lines += _format_site(activity, with_survey=not step)
    lines += _format_playbook(activity, remaining_only=step)
    if activity.town is not None and not step:
        lines += _format_town(activity)
    pending = [g for g in activity.objectives if g.state == ObjectiveState.PENDING]
    finished = [g for g in activity.objectives if g.state != ObjectiveState.PENDING]
    lines.append("- 大目標（上から順に取り組む）:")
    lines += [
        f"  {i}. {_format_objective(g, with_ids, current=i == 1)}" for i, g in enumerate(pending, 1)
    ] or ["  - なし"]
    if finished and not step:
        lines.append("- 最近終わった大目標:")
        lines += [f"  - {_format_finished(g)}" for g in finished]
    if cache_break:
        lines.append(CACHE_BREAK)
    recent = activity.recent_goals
    now = f" [{len(recent) + 1}]" if with_ids and activity.goal is not None else ""
    lines.append(f"- 今の小目標{now}: {_format_goal(activity.goal, activity.observation, titles)}")
    if activity.intent:
        lines.append(f"- 今やろうとしていること: {activity.intent}")
    if activity.screen_note is not None:
        lines.append(_format_screen_note(activity.screen_note))
    if activity.observation is not None:
        lines.append(_format_situation(activity.observation, brief=step))
    lines.append("- これまでの小目標（古い順）:")
    outcomes = [(i, _format_outcome(o, titles)) for i, o in enumerate(recent, 1)]
    if step:
        # 同じ小目標が続けて同じ結果で終わっているものは 1 つに、新しいものから数個
        outcomes = [(i, t) for k, (i, t) in enumerate(outcomes) if k == 0 or t != outcomes[k - 1][1]][-STEP_RECENT_GOALS:]
    lines.append(
        "\n".join(f"  - {f'[{i}] ' if with_ids else ''}{t}" for i, t in outcomes) or "  - なし"
    )
    lines += _format_notes(activity, with_ids)
    lines += _format_lessons(activity)
    return "\n".join(lines)


def format_age(seconds: float) -> str:
    """経った時間の短い言い方（秒・分・時間）。"""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒前"
    if seconds < 3600:
        return f"{seconds // 60}分前"
    return f"{seconds // 3600}時間前"


def _speaker(message: ConversationMessage) -> str:
    if message.role == MessageRole.VIEWER:
        return f"{message.speaker_name}さん"
    if message.message_type == MessageType.COMMENTARY:
        return "あなた（実況）"
    if message.message_type == MessageType.RESPONSE and message.addressed_to:
        return f"あなた（{message.addressed_to}さんへの返事）"
    return "あなた"


def format_messages(
    messages: tuple[ConversationMessage, ...] | list[ConversationMessage],
    now: datetime | None = None,
) -> str:
    """
    会話の履歴。1行に 1メッセージ。`now` があれば、行頭に「[N秒前]」と、誰への返事か・実況かを付ける
    （チャットの返事: 時系列と、どのコメントへの返事かを取り違えないため）。
    """
    if not messages:
        return NO_INFORMATION
    lines = []
    for message in messages:
        if now is None:
            speaker = f"{message.speaker_name}さん" if message.role == MessageRole.VIEWER else "あなた"
            lines.append(f"{speaker}: {message.content}")
        else:
            age = format_age((now - message.timestamp).total_seconds())
            lines.append(f"[{age}] {_speaker(message)}: {message.content}")
    return "\n".join(lines)


def _minutes(to_tick: int, tick: float) -> str:
    return f"{(to_tick - tick) / TICKS_PER_MINUTE:.0f}"


def format_time(time: dict) -> str:
    """
    日本語の時間帯。次に変わるまでの分数を付ける。夕方は夜まで（と朝まで）: 前は「夕方（朝まで約 9 分）」
    とだけ書き、Gemini の代わりのモデルが「夜が明けた」と読んで外の中目標を決めた（2026-09-27）。
    """
    phase = time.get("phase", "")
    name = PHASE_NAMES.get(phase, "不明")
    tick = time.get("time_of_day")
    if tick is None:
        return name
    if phase == "day":
        return f"{name}（日暮れまで約 {_minutes(DUSK_TICK, tick)} 分）"
    if phase == "dusk":
        return (
            f"{name}（暗くなってきた。夜になるまで約 {_minutes(NIGHT_TICK, tick)} 分、"
            f"そのあと朝まで約 {_minutes(MORNING_TICK, NIGHT_TICK)} 分）"
        )
    return f"{name}（朝まで約 {_minutes(MORNING_TICK, tick)} 分）"


def format_time_en(time: dict) -> str:
    """英語の時間帯（行動の選択器の言語）。"""
    phase = time.get("phase", "")
    tick = time.get("time_of_day")
    if tick is None:
        return phase
    if phase == "day":
        return f"day ({_minutes(DUSK_TICK, tick)} minutes until dusk)"
    if phase == "dusk":
        return f"dusk (night in {_minutes(NIGHT_TICK, tick)} minutes, then morning after {_minutes(MORNING_TICK, NIGHT_TICK)})"
    return f"{phase} ({_minutes(MORNING_TICK, tick)} minutes until morning)"


def _requested(goal: Objective) -> str:
    if goal.requested_by:
        return f"（{goal.requested_by}さんの頼み）"
    return f"（{goal.suggested_by}さんの提案）" if goal.suggested_by else ""


def _format_memory(memory: dict) -> str:
    def where(p: dict) -> str:
        direction = DIRECTION_NAMES.get(p["direction"], p["direction"])
        return f"{direction} {p['distance_m']}m、{p['minutes_ago']} 分前"

    def gives(p: dict) -> str:
        # ブロックから取れる物（作物の wheat → wheat・wheat_seeds）
        return f"→ {'・'.join(p['gives'])}、" if p.get("gives") else ""

    parts = [f"{p['kind']} {p['count']}（{gives(p)}{where(p)}）" for p in memory.get("places", [])]
    parts += [f"死んだ場所（{where(d)}）" for d in memory.get("deaths", [])[-DEATHS_SHOWN:]]
    return "、".join(parts) or "なし"


def _format_chests(memory: dict) -> str:
    """中身のあるチェストだけ、多い品から数件（26 §4）。"""
    chests = [c for c in memory.get("chests", []) if c.get("contents")]
    if not chests:
        return "なし"

    def contents(c: dict) -> str:
        top = sorted(c["contents"].items(), key=lambda kv: -kv[1])
        items = "、".join(f"{name} {n}" for name, n in top[:CHEST_ITEMS])
        more = f" ほか {len(top) - CHEST_ITEMS} 種" if len(top) > CHEST_ITEMS else ""
        return f"{items}{more}（{c['minutes_ago']} 分前）"

    return " / ".join(contents(c) for c in chests)


def _format_inventory(inventory: dict) -> str:
    return "、".join(f"{name} {n}" for name, n in inventory.items()) or "なし"


def _format_equipment(me: dict) -> str:
    worn = [
        f"{EQUIPMENT_NAMES[part]} {item}"
        for part, item in (me.get("equipment") or {}).items()
        if item and part in EQUIPMENT_NAMES
    ]
    held = [f"手に {me['held_item']}"] if me.get("held_item") else []
    return "、".join(held + worn) or "なし"


def _format_site(activity: Activity, with_survey: bool = True) -> list[str]:
    """街の場所: 決めたこと（配信者）と、調べた事実（ブリッジが測った数字）を分けて出す。`with_survey` でなければ事実の一覧は出さない。"""
    obs = activity.observation
    survey = (obs.state.get("survey") if obs else None) or {}
    rows = survey.get("sites") or []
    if activity.site is None and not rows:
        return []
    if activity.site is not None:
        row = next((r for r in rows if r.get("id") == activity.site.site_id), None)
        stand_y = row.get("stand_y") if row else None
        lines = [f"- 街の場所（決めたこと）: {format_site_choice(activity.site, stand_y)}"]
    else:
        done = f"{len(rows)}/{survey.get('planned', 0)}"
        lines = [f"- 街の場所: まだ決めていない（候補地を調べた数 {done}）"]
    if rows and with_survey:
        lines.append("- 調べた候補地（ブリッジが測った数字。候補地のまわり半径 32 ブロック）:")
        lines += [f"  - {format_site_facts(r)}" for r in rows]
    return lines


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
        if i != activity.town_stage:
            continue
        met = [c.describe() for c in stage.conditions if c in activity.stage_met]
        if met:
            lines.append(f"     できたこと: {', '.join(met)}")
        if not stage.ready:
            waits = "; ".join(stage.unresolved)
            lines.append(
                f"     まだできないこと（できるようになるまで、この段階は終わらない）: {waits}"
            )
    return lines


def _format_playbook(activity: Activity, remaining_only: bool = False) -> list[str]:
    """定石（この順にやる。コードが守る。docs/design/74）: 済・今・先。`remaining_only` なら残りだけ（全部済なら出さない）。"""
    if not activity.playbook:
        return []
    now = next((s.name for s in activity.playbook if s.name not in activity.playbook_done), None)
    steps = [s for s in activity.playbook if not remaining_only or s.name not in activity.playbook_done]
    if not steps:
        return []
    parts = [
        f"[{'済' if s.name in activity.playbook_done else '今' if s.name == now else '先'}] {s.title}"
        for s in steps
    ]
    return [f"- 定石{'の残り' if remaining_only else ''}（この順に。コードが守る）: {' → '.join(parts)}"]


def _format_objective(goal: Objective, with_ids: bool, current: bool) -> str:
    conditions = ", ".join(c.describe() for c in goal.conditions)
    summary = goal.summary()
    progress = f"（{'; '.join(summary)}）" if summary else ""
    stage = f" [街の段階 {goal.stage + 1}: やめられない]" if goal.stage is not None else ""
    if goal.prepares_town:
        stage = " [街の準備: やめられない]"
    if goal.playbook is not None:
        stage = f" [定石 {goal.playbook}: 動かせない・やめられない]"
    jobs = ""
    if current and goal.jobs:
        jobs = " 中目標: " + _format_jobs(goal)
    return (
        f"{f'[{goal.id}] ' if with_ids else ''}{goal.title}{_requested(goal)}{stage}"
        f"{' [取り組み中]' if current else ''} 完了条件: {conditions}{progress}{jobs}"
    )


def _format_jobs(goal: Objective) -> str:
    """
    中目標を順に、済み（一度満たしたら済みのまま）・今（まだの一番先）・待ちで。束の中目標
    （docs/design/44）は [a + b] で 1 つ。視聴者の頼みと採った助言は出どころも。
    """
    first_open = next((s for s in goal.jobs if s.state == JobState.PENDING), None)
    parts = []
    for s in goal.jobs:
        mark = "済" if s.state == JobState.DONE else "今" if s is first_open else "待"
        text = f"[{s.describe()}]" if s.together else s.describe()
        if s.title:
            text = f"{s.title} {text}"
        # 頼み・提案の中目標は、書き直しでは消えない。やめるときは id で drop する
        who = (
            f"（{s.requested_by}さんの頼み・{s.id}）"
            if s.requested_by
            else f"（{s.suggested_by}さんの提案・{s.id}）"
            if s.suggested_by
            else ""
        )
        plan = _format_steps(s) if s is first_open else ""
        parts.append(f"{mark}:{text}{who}{plan}")
    return " → ".join(parts)


def _format_steps(job: Job) -> str:
    """
    今の中目標の計画（docs/design/50 §18）: 小目標の並びを、済（一度満たしたら済みのまま）・今（次にやる）・
    待で。前提は「小目標<前提」。書いていなければ空。
    """
    if not job.steps:
        return ""
    done = set(job.steps_done)
    first_open = next((x for x in job.steps if x.key not in done), None)
    parts = []
    for step in job.steps:
        needs = "".join(f"<{n.describe()}" for n in step.needs if n.key not in done)
        mark = "済" if step.key in done else "今" if step is first_open else "待"
        parts.append(f"{mark}:{step.describe()}{needs}")
    return "（計画: " + " → ".join(parts) + "）"


def _format_finished(goal: Objective) -> str:
    how = "完了" if goal.state == ObjectiveState.DONE else f"断念: {goal.ended_because}"
    return f"{goal.title}{_requested(goal)}（{how}）"


def _serves(goal: Goal, titles: dict[str, str]) -> str:
    if goal.objective_id is None:
        return "（身を守るため）"
    title = titles.get(goal.objective_id)
    return f"（「{title}」のため）" if title else ""


def _format_goal(goal: Goal | None, obs: GameObservation | None, titles: dict[str, str]) -> str:
    if goal is None:
        return "まだない"
    left = goal.unmet(obs.goal if obs is not None else None)
    done = [s.describe() for s in goal.specs if s not in left]
    head = " + ".join(s.describe() for s in left) + (f"（済み: {', '.join(done)}）" if done else "")
    lines = [f"{head}{_serves(goal, titles)}: {goal.reason}"]
    if goal.diagnosis:
        lines.append(f"  前の小目標がうまくいかなかった理由（自分の分析）: {goal.diagnosis}")
    if goal.advice:
        lines.append(f"  やり方の助言: {goal.advice}")
    if goal.review:
        lines.append(f"  決めたときの見直し: {goal.review}")
    if goal.together:
        # 束（docs/design/44）: 条件ごとの状態は下のブリッジの行に出る
        lines.append(f"  一緒に追う条件 {len(goal.specs)} つ（全部そろったら終わり）")
        if goal.approach:
            lines.append(f"  進め方: {goal.approach}")
    if obs is not None and obs.goal is not None:
        lines += [f"  {line}" for line in obs.goal.lines]
        if obs.goal.blocked:
            lines.append("  進められない理由: " + "; ".join(obs.goal.blocked))
    return "\n".join(lines)


def _format_outcome(o: GoalOutcome, titles: dict[str, str]) -> str:
    result = "達成" if o.met else "未達成"
    return (
        f"{o.goal.describe()}{_serves(o.goal, titles)}: {o.goal.reason}"
        f"（{result}、終了: {o.ended_because}）"
    )


NOTE_KINDS = {NoteKind.LESSON: "分かったこと", NoteKind.VIEWER: "視聴者", NoteKind.PLAN: "先のため"}


def _format_notes(activity: Activity, with_ids: bool) -> list[str]:
    """自分のメモ: 世界の事実とは別の欄（確かめていない）。"""
    day = activity.observation.day if activity.observation is not None else None
    today = f"、今日は {day} 日目" if day is not None else ""
    return [
        f"- 自分のメモ（自分で書いたもの。確かめていない。事実は上のゲームの状況{today}）:",
        *([f"  - {_format_note(n, with_ids)}" for n in activity.notes] or ["  - なし"]),
    ]


def _format_note(note: Note, with_ids: bool) -> str:
    about = {NoteKind.LESSON: f"（根拠: {note.about}）", NoteKind.VIEWER: f"（{note.about}さん）"}
    note_id = f"{note.id} " if with_ids else ""
    days = f"（{note.written_day} 日目に書いた、{note.expires_day} 日目まで）"
    return f"{note_id}[{NOTE_KINDS[note.kind]}] {note.text}{about.get(note.kind, '')}{days}"


def _format_lessons(activity: Activity) -> list[str]:
    """
    今の状況で思い出した教訓（docs/design/35）: 前に似た状況で学んだこと。確かめていない。
    思い出したものがなければ欄を出さない。
    """
    if not activity.lessons:
        return []
    return [
        "- 思い出したこと（前に似た状況で学んだこと。確かめていない。事実は上のゲームの状況）:",
        *(f"  - {_format_lesson(n)}" for n in activity.lessons),
    ]


def _format_lesson(lesson: Lesson) -> str:
    when = f"（こういうとき: {lesson.condition}）" if lesson.condition else ""
    source = f"{lesson.viewer}さんに教わった" if lesson.viewer else "自分の失敗から"
    record = f"、効いた {lesson.helped} 回・効かなかった {lesson.failed} 回" if lesson.used else ""
    return f"{lesson.text}{when}（{source}{record}）"


def _format_screen_note(note: ScreenNote) -> str:
    """画面の見直しで書いたこと: 画像の解釈で、確かめていない（ゲームの状況とは別の行）。"""
    minutes = max(0, int((datetime.now(timezone.utc) - note.taken_at).total_seconds() // 60))
    concern = f"。気になったこと: {note.concern}" if note.concern else ""
    fit = "" if note.matches_goal else "。目標と合っていないように見えた"
    return (
        f"- 画面で見たこと（{minutes} 分前、画像の解釈で確かめていない）: {note.seen}{concern}{fit}"
    )


LANDMARK_RADIUS = 24  # ブリッジの landmarks.mjs と同じ
LANDMARK_NAMES = {
    "bed": "ベッド",
    "door": "ドア",
    "crafting_table": "作業台",
    "furnace": "かまど",
    "chest": "チェスト",
    "torch": "松明",
}
OWNER_NAMES = {"home": "家のもの", "former_home": "前の家のもの"}


def _format_nearby(nearby: list) -> str:
    """まわりの目印（ブリッジが見たもの）。家のものでなければそう書く（自分で建てていない物も使える）。"""

    def one(n: dict) -> str:
        p = n.get("nearest") or {}
        where = f"{DIRECTION_NAMES.get(p.get('direction'), p.get('direction', ''))} {p.get('distance_m')}m"
        owner = OWNER_NAMES.get(n.get("owner"), "家のものではない")
        count = f" {n['count']}" if n.get("count", 1) > 1 else ""
        return f"{LANDMARK_NAMES.get(n.get('kind'), n.get('kind'))}{count}（一番近いもの: {where}、{owner}）"

    return "、".join(one(n) for n in nearby) or "なし"


def _xyz(p: dict) -> str:
    """座標をラベル付きで書く（`x=56 y=77 z=-284`）。`56,77,-284` だと、モデルが写すときにマイナスを落とす
    （2026-10-03: z=-285 を 285 と書いて、8 回以上「遠すぎる」と断られた）。道具の引数の名前と同じ書き方。"""
    return f"x={p['x']} y={p['y']} z={p['z']}"


def _format_around(around: dict) -> list[str]:
    """
    目の前にあるもの（ブリッジの around: 半径 8m、足元の高さ ±2 の、見えているブロックと
    落ちているアイテム）。目の前の原木や作物を無視して遠くへ探しに行かないように、毎回見せる。
    """
    if not around:
        return []

    def where(p: dict) -> str:
        direction = DIRECTION_NAMES.get(p.get("direction"), p.get("direction", ""))
        dy = p.get("dy", 0)
        return f"{_xyz(p)} {direction} {p['distance_m']}m、高さ {dy:+d}"

    def block(b: dict) -> str:
        ripe = f"熟した {b['ripe']}、" if "ripe" in b else ""
        gives = f"→ {'・'.join(b['gives'])}、" if b.get("gives") else ""
        # 木でない原木（村の畑の縁、家の柱）: 数だけ。集めない（壊すと畑の水が流れ出す）
        built = f"ほか木ではない{b['not_trees']}個は人が作った物で集めない、" if b.get("not_trees") else ""
        if not b.get("nearest"):
            if "ripe" in b:  # 熟した作物がない: 育ち途中は掘れない
                return f"{b['name']} {b['count']}（{ripe}{gives}育ち途中で、まだ掘れない）"
            return f"{b['name']}（{built.rstrip('、')}。集められる木の幹は目の前にない）"
        mark = "採掘の場所の中の、" if b.get("quarry") else ""
        return f"{b['name']} {b['count']}（{ripe}{gives}{built}{mark}一番近いもの {where(b['nearest'])}）"

    def item(i: dict) -> str:
        direction = DIRECTION_NAMES.get(i.get("direction"), i.get("direction", ""))
        return f"{i['item']} {i['count']}（{direction} {i['distance_m']}m）"

    head = f"半径 {around.get('radius')}m、足元の高さ ±{around.get('dy')}"
    return [
        f"- 目の前のブロック（{head}、見えているもの）: "
        + ("、".join(block(b) for b in around.get("blocks", [])) or "なし"),
        f"- 目の前に落ちている物: {'、'.join(item(i) for i in around.get('items', [])) or 'なし'}",
    ]


def _format_quarry(quarry: dict) -> list[str]:
    """
    採掘の場所（石・土を掘っている所。設計書 64）と、続きから下りられる階段。毎回別の所から掘って
    家のまわりを穴だらけにしないよう、その近くから掘らせる。
    """
    if not quarry:
        return []
    direction = DIRECTION_NAMES.get(quarry.get("direction"), quarry.get("direction", ""))
    line = (
        f"- 採掘の場所（石・土・砂利はここの近くから掘る。別の所を新しく掘らない）: "
        f"{_xyz(quarry)} {direction} {quarry['distance_m']}m"
    )
    shafts = quarry.get("shafts") or []
    if shafts:
        line += "、前に掘った階段 " + "、".join(
            f"入口 {_xyz(s['top'])} → 底 y {s['bottom']['y']}" for s in shafts
        )
        line += "（鉱石が底より深ければ、その底から続けて掘る）"
    return [line]


def _format_home(obs: GameObservation) -> str:
    """
    家そのもの（建ててあるか、ベッド、チェスト）。今いる場所は別の行（_format_place）: 前は同じ行に
    「完成している（外にいる）」と書き、小目標 at_home のときに家に着いたと読まれた（2026-09-27）。
    """
    if not obs.has_home:
        return "まだない（夜までに建てる必要がある。placed の home / near_home は家ができるまで使えない）"
    home = obs.state.get("home") or {}
    name = home.get("name")
    chests = len((obs.state.get("memory") or {}).get("chests", []))
    door = home.get("door") or {}
    # 玄関の座標: ないと、歩く先の座標を思い込みで書く（小目標 at_home の間に 0,0,-347 へ歩き続けた）
    where = (
        f"、玄関 {_xyz(door)}" if all(k in door for k in "xyz") else ""
    )
    if home.get("found"):
        # 村の家など、もとからあった建物。自分で建てた家ではなく、中のベッドも自分の物ではない
        bed = "もとからあるベッド（自分の物ではない）" if obs.bed_in_home else "ベッドなし"
        return (
            f"{f'{name}（もとからあった建物を仮の宿にしている。自分で建てた家ではない）' if name else '仮の宿（自分で建てた家ではない）'}"
            f"、{bed}、チェスト {chests}{where}"
        )
    bed = "ベッドあり" if obs.bed_in_home else "ベッドなし"
    return f"{f'{name}（建ててある）' if name else '建ててある'}、{bed}、チェスト {chests}{where}"


def format_place(obs: GameObservation) -> str:
    """
    今いる場所: 家の中か、家の外と家までの距離。地下なら「地下」、位置（座標と足元の高さ）、バイオーム。
    小目標を選ぶ側にも要る（高さで掘る鉱石の層が決まる、地下では夜でも帰らなくてよい、羊は草原）。
    """
    parts = []
    if obs.has_home:
        distance = obs.home_distance_m
        parts.append("家の中" if obs.inside_home else f"家の外（家まで {distance}m）" if distance is not None else "家の外")
    if obs.underground:
        parts.append("地下")
    me = (obs.state.get("self") or {}).get("position") or {}
    if all(k in me for k in ("x", "y", "z")):
        parts.append(f"x={round(me['x'])} y={round(me['y'])} z={round(me['z'])}")
    biome = format_biome((obs.state.get("self") or {}).get("biome"))
    if biome:
        parts.append(f"バイオーム {biome}")
    return "、".join(parts)


# バイオームの日本語（ここにないものは英語名のまま）
BIOME_JA = {
    "plains": "平原", "sunflower_plains": "ひまわり平原", "meadow": "草原", "cherry_grove": "桜の林",
    "forest": "森", "flower_forest": "花の森", "birch_forest": "白樺の森", "dark_forest": "暗い森",
    "old_growth_birch_forest": "巨大な白樺の森", "taiga": "タイガ", "old_growth_pine_taiga": "巨大なマツのタイガ",
    "old_growth_spruce_taiga": "巨大なトウヒのタイガ", "snowy_plains": "雪原", "snowy_taiga": "雪のタイガ",
    "snowy_slopes": "雪の斜面", "grove": "雪の林", "desert": "砂漠", "savanna": "サバンナ",
    "savanna_plateau": "サバンナの台地", "windswept_savanna": "吹きさらしのサバンナ", "badlands": "荒野",
    "swamp": "沼地", "mangrove_swamp": "マングローブの沼地", "jungle": "ジャングル", "sparse_jungle": "まばらなジャングル",
    "bamboo_jungle": "竹林のジャングル", "beach": "砂浜", "stony_shore": "岩の海岸", "river": "川",
    "frozen_river": "凍った川", "ocean": "海", "deep_ocean": "深い海", "lukewarm_ocean": "ぬるい海",
    "windswept_hills": "吹きさらしの丘", "windswept_forest": "吹きさらしの森", "dripstone_caves": "鍾乳洞",
    "lush_caves": "緑の洞窟", "deep_dark": "ディープダーク", "mushroom_fields": "キノコ島",
}


def format_biome(biome: str | None) -> str:
    """今いるバイオーム（名前が分からなければ空）。日本語名と元の名前。"""
    if not biome:
        return ""
    # 名前の末尾が合うもの（deep_lukewarm_ocean → ぬるい海）は、長く合うほうを取る
    ja = BIOME_JA.get(biome) or next((BIOME_JA[k] for k in sorted(BIOME_JA, key=len, reverse=True) if biome.endswith(f"_{k}")), "")
    return f"{ja}（{biome}）" if ja else biome


def _format_biomes_seen(biomes: list) -> list[str]:
    """歩いて見たバイオームと、そこへの向きと距離（近い順）。羊は草原、など探す方向を選ぶ手がかり。立った所だけの記録。"""
    if not biomes:
        return []
    parts = [
        f"{format_biome(b['biome'])} {DIRECTION_NAMES.get(b.get('direction'), b.get('direction', ''))} {b['distance_m']}m"
        for b in biomes
    ]
    return [f"- 歩いて見たバイオーム（一番近い所。立った所だけ覚えている）: {'、'.join(parts)}"]


def _format_broken(broken: list) -> list[str]:
    """最近壊れた道具（壊れたものはもう持っていない。持ち物の行が正しい）。"""
    if not broken:
        return []
    parts = [
        f"{b['item']}（{max(1, round(b.get('seconds_ago', 0) / 60))}分前、"
        f"{'作り直した' if b.get('replaced') else 'もう持っていない'}）"
        for b in broken
    ]
    return [f"- 壊れた道具: {'、'.join(parts)}"]


TOOL_WEAR_LOW = 0.2  # 残りの耐久がこの割合以下の道具には「もうすぐ壊れる」を付ける


def tool_is_worn(wear: dict) -> bool:
    """残りの耐久が少ない道具か（`tool_wear` の 1 件）。"""
    return wear["left"] <= wear["max"] * TOOL_WEAR_LOW


def _format_tool_wear(wear: list) -> list[str]:
    """道具の残りの耐久。折れる前に予備を作る・家へ帰る判断に使う（2026-10-10: 遠い地下で唯一のツルハシが折れた）。"""
    if not wear:
        return []
    parts = [
        f"{w['item']} 残り {w['left']}/{w['max']}" + ("（もうすぐ壊れる）" if tool_is_worn(w) else "")
        for w in wear
    ]
    return [f"- 道具の耐久: {'、'.join(parts)}"]


def _format_farm(farm: dict) -> list[str]:
    """
    畑（設計した畑と家の畑の合計）: 作物ごとの植わっている数、熟した数、育ち具合、空いたマス、
    乾いた耕地（水が 4 マス以内にない。育ちが遅い）、畑の名前（熟したものは掘ると取れて、植え直す。
    作物は近くにいる間だけ育つ）。
    """
    if not farm:
        return []

    def one(crop: str, f: dict) -> str:
        ages = f.get("ages")
        grow = f"、育ち具合 {ages[0]}〜{ages[1]}/{f.get('mature')}" if ages else ""
        free = f"、空き {f['free']}" if f.get("free") else ""
        dry = f"、乾いた耕地 {f['dry']}" if f.get("dry") else ""
        water = f"、水のマスあと {f['water']}" if f.get("water") else ""
        seen = "、前に見たとき" if f.get("remembered") else ""
        where = f" {'・'.join(f['farms'])}" if f.get("farms") else ""
        return f"{crop}{where} {f['planted']} マス（熟した {f['ripe']}{grow}{free}{water}{dry}{seen}）"

    parts = [one(crop, f) for crop, f in farm.items()]
    dry = "。乾いた耕地は水が 4 マス以内になく、育ちが遅い" if any(f.get("dry") for f in farm.values()) else ""
    return [
        f"- 畑: {'、'.join(parts)}（熟したものを掘ると収穫して植え直す。"
        f"近くにいる間だけ育つ{dry}）"
    ]


_RISK_LEVELS = {"low": "低い", "medium": "やや高い", "high": "高い", "extreme": "極めて高い"}


def _format_risk(risk: dict) -> list[str]:
    """
    外に出る・戦うときの危なさ（ブリッジが体力・時間帯・敵・装備・失うものから測った。docs/design/94）。
    禁止ではなく、選ぶ材料: 危なければ、行動の前にリスクを下げる手（預ける・食べる・装備する）を先に選ぶ。
    """
    if not risk or risk.get("level") not in _RISK_LEVELS:
        return []
    reasons = [*(risk.get("factors") or []), *(risk.get("context") or [])]
    line = f"- 外に出る危なさ: {_RISK_LEVELS[risk['level']]}（{'; '.join(reasons)}）"
    advice = risk.get("advice") or []
    if advice:
        line += f"。出る前に: {'; '.join(advice)}"
    return [line]


def _format_titles(obs: GameObservation) -> list[str]:
    """建物・施設の呼び名。配信で話すときはこの呼び名で言う（目標や判定に書くのは元の名前）。"""
    if not obs.titles:
        return []
    named = "、".join(f"{name}={title}" for name, title in obs.titles)
    return [f"- 建物・施設の呼び名（人に話すときはこの呼び名で言う。目標・判定・道具には元の名前を使う）: {named}"]


def _format_situation(obs: GameObservation, brief: bool = False) -> str:
    s = obs.state
    lines = [
        f"- 時間帯: {format_time(s.get('time', {}))}",
        f"- 家: {_format_home(obs)}",
        *_format_titles(obs),
        *([f"- 今いる場所: {place}"] if (place := format_place(obs)) else []),
        f"- 近くにあるもの（{LANDMARK_RADIUS} m 以内）: {_format_nearby(s.get('nearby') or [])}",
        *_format_around(s.get("around") or {}),
        *_format_quarry(s.get("quarry") or {}),
        f"- 体力 {obs.health}/20、満腹度 {obs.food}/20",
        f"- 装備: {_format_equipment(s.get('self', {}))}",
        f"- 持ち物: {_format_inventory(s.get('inventory', {}))}",
        *_format_broken(s.get("broken_tools") or []),
        *_format_tool_wear([w for w in s.get("tool_wear") or [] if not brief or tool_is_worn(w)]),
        f"- 気をつけること: {'、'.join(n for n in obs.needs if n != 'none') or 'なし'}",
        *_format_risk(s.get("risk") or {}),
        *_format_farm(s.get("farm") or {}),
        f"- チェストの中身: {_format_chests(s.get('memory') or {})}",
        f"- 覚えている場所（前に見た、今は見えない）: {_format_memory(s.get('memory') or {})}",
        *_format_biomes_seen((s.get("memory") or {}).get("biomes") or []),
    ]
    # 道具を選ぶ手には「直近の道具」の節が別にある（同じ手が二重に並ぶ）
    recent = [] if brief else s.get("recent_actions", [])
    if recent:
        lines.append(
            "- 直近の行動: "
            + " / ".join(
                f"{a['action']}=成功" if a["ok"] else f"{a['action']}=失敗（{a.get('result', '')}）"
                for a in recent
            )
        )
    return "\n".join(lines)
