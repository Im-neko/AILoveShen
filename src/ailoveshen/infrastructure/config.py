"""YAML と環境変数に対応した設定の管理。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# =============================================================================
# 例外
# =============================================================================


class ConfigurationError(Exception):
    """設定の読み込みか解析に失敗したときに送出する。"""

    pass


# =============================================================================
# 設定のデータクラス
# =============================================================================


@dataclass
class TwitchSettings:
    """Twitch 連携の設定。"""

    # 配信（python -m ailoveshen.stream）でチャットを読むか。channel が空なら読まない
    enabled: bool = True
    channel: str = ""
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    # 返事と返事の最短の間、返事を待つコメントを何件まで取っておくか
    min_interval_seconds: float = 5.0
    backlog: int = 3
    # 視聴者の名前の読みの辞書（docs/design/30_name_readings.md）
    readings_path: str = "data/readings.json"
    # 配信者が自分について話したこと（docs/design/40_small_talk.md。ワールドを作り直しても残す）
    self_facts_path: str = "data/self_facts.json"
    # チャットに書き込むアカウントのログイン名（access_token のアカウント。空ならチャンネル名）
    bot_login: str = ""
    # 何にも反応しないボットのアカウント（ログイン名）。配信者自身と bot_login は返事だけしない
    ignore_users: list[str] = field(default_factory=lambda: ["streamelements"])
    # 返事の前の仕分け（docs/design/34 §5）: jev（返事の要らないものを飛ばし、取り下げと頼みを先に）か off
    triage: str = "jev"
    skip_min_confidence: float = 0.7
    triage_record_dir: str = "logs/chat"
    # 返事が行動を約束しているのに大目標にも考え直しにもならないとき書き直させる（docs/design/34 §10）: jev か off
    promise_check: str = "jev"
    promise_record_dir: str = "logs/promises"
    # 返事が持っている物を「ない・見つからない」と言っているのを、持ち物とチェストの事実と照らして書き直させる: jev か off
    fact_check: str = "jev"
    fact_record_dir: str = "logs/facts"
    # フォロー通知（EventSub）に使う、手で作ったトークン。空なら client_id から自動で取って更新し、
    # token_path に残す（docs/design/71_support_thanks.md の追記）
    eventsub_token: str = ""
    token_path: str = "data/twitch_token.json"
    # 許可が要るとき、この PC のブラウザで許可の画面を開く（声でも知らせる）
    open_browser: bool = True
    # 配信への応援（フォロー、サブスク、ギフト、ビッツ、レイド）にお礼を言う
    support: "TwitchSupportSettings" = field(default_factory=lambda: TwitchSupportSettings())


@dataclass
class TwitchSupportSettings:
    """配信への応援へのお礼の設定（docs/design/71_support_thanks.md）。"""

    enabled: bool = True
    # フォローを EventSub で受ける（client_id と、スコープ moderator:read:followers のトークンが要る）
    follows: bool = True
    min_interval_seconds: float = 3.0
    batch_seconds: float = 2.0
    backlog: int = 20
    dedupe_seconds: float = 3600.0


DEFAULT_THINKING_LEVELS = {
    "town": "high",
    "site": "high",
    "house": "medium",
    "build_design": "high",
    "goal": "low",
    "goal_after_failure": "medium",
    "tool": "low",
    "tool_after_failure": "medium",
    "tool_stuck": "medium",
    "commentary": "low",
    "reply": "low",
    "live": "low",
    "promise": "low",
    "screen_review": "medium",
    "skill_write": "high",
    "check_write": "medium",
}


@dataclass
class LocalLlmSettings:
    """ローカルの LLM（OpenAI 互換: FreeToken、Ollama など）の設定（docs/design/36）。"""

    enabled: bool = False
    base_url: str = "http://127.0.0.1:1919/v1"
    model: str = ""  # 空: /v1/models の最初
    api_key: str = ""
    context_tokens: int = 32768
    max_output_tokens: int = 16384  # 思考を含む（1 回ごとにコンテキストの残りまでに減らす）
    response_format: str = "auto"  # auto | off
    thinking_param: str = "chat_template_kwargs"  # chat_template_kwargs | none
    # 思考させる用途（ほかは思考なし）。ふだんの操作の判断（tool / goal と失敗の後）は思考なし
    thinking_purposes: list[str] = field(
        default_factory=lambda: ["skill_write", "house", "town", "site"]
    )
    # 思考させる用途の出力（思考を含む）の上限。上限で答えが出なければ、思考なしで聞き直す
    thinking_max_tokens: dict[str, int] = field(default_factory=lambda: {"tool_stuck": 8000})
    # 思考させない用途の出力の上限。止まらずに書き続ける呼び出しを切る（用途を書かなければ上限なし）
    output_max_tokens: dict[str, int] = field(default_factory=dict)
    max_concurrent: int = 2
    timeout_seconds: float = 180.0


@dataclass
class LiteLlmSettings:
    """LiteLLM 経由のモデル（Claude Haiku など。OpenAI 互換のプロキシ）の設定（docs/design/127）。"""

    base_url: str = "https://llm.ideta.net/v1"
    model: str = ""  # LiteLLM のモデル名（必須）
    api_key: str = ""
    context_tokens: int = 200000
    max_output_tokens: int = 8192
    response_format: str = "auto"  # auto | off
    # 用途 → モデル（表にない用途は model）。コードを書く用途だけ上のモデルにする。LiteLLM に回った呼び出しにだけ効く
    # （どの用途が回るかは llm.routes と、ローカルを切っているかで決まる）
    models: dict[str, str] = field(default_factory=dict)
    max_concurrent: int = 4
    timeout_seconds: float = 120.0


@dataclass
class LlmSettings:
    """
    LLM の設定: ローカルの LLM と、リモート（LiteLLM 経由のモデル。Claude も Gemini もここ）の振り分け
    （docs/design/36、127）。画像のある呼び出しはいつもリモート。
    """

    local: LocalLlmSettings = field(default_factory=LocalLlmSettings)
    litellm: LiteLlmSettings = field(default_factory=LiteLlmSettings)
    # 用途（purpose）→ local / remote。default は表にない用途
    routes: dict[str, str] = field(default_factory=lambda: {"default": "local"})
    fallback: str = "none"  # none | remote（ローカルが失敗したとき）
    # 用途（呼び出しの purpose）ごとの思考の深さ（docs/design/19 §7、21 §7）。low / medium / high。
    # medium と high の用途だけ思考させる。ない用途は default_thinking_level
    thinking_levels: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_THINKING_LEVELS))
    default_thinking_level: str = "low"
    # 呼び出しの記録（プロンプト全文、出力、道具の呼び出し、トークン）を JSON Lines で残すディレクトリ
    # （リポジトリの直下から。空ならメモリだけ）と、古い記録のファイルを消すまでの日数（docs/design/90）
    generation_log_dir: str = "logs/generations"
    generation_log_keep_days: int = 3
    # 目標ボードの /api/debug/gemini に出す直近の呼び出しの数（メモリだけのとき）
    debug_log_size: int = 50


@dataclass
class JevSettings:
    """Jev（TypeSafe AI の System One）の設定。"""

    api_key: str = ""
    model: str = "jev-latest"
    timeout_seconds: float = 10.0


def _default_objectives() -> list[dict[str, Any]]:
    return []


@dataclass
class MissionSettings:
    """最終目標（コメントでは変わらない）、最初の大目標、計画の上限。"""

    text: str = "生き延びながら家を建て、街にしていく"
    # [{title, conditions: [{predicate, item?, count?}], reason}]: 保存したものがないときのリスト
    objectives: list[dict[str, Any]] = field(default_factory=_default_objectives)
    # 定石: この順に、前の段が済むまで次の段の大目標を先頭に出さない（コードが守る）。
    # [{name, title, conditions: [{predicate, item?, count?, where?, name?}], reason}]（docs/design/74）
    playbook: list[dict[str, Any]] = field(default_factory=list)
    max_objectives: int = 6
    max_viewer_objectives: int = 2
    viewer_budget_steps: int = 80
    # 大目標は再起動をまたいで引き継ぐ（最終目標の文が同じなら）
    store_path: str = "data/mission.json"


@dataclass
class WatchSettings:
    """道具の実行の見張り（docs/design/21 §5）。"""

    interval_seconds: float = 1.0
    grace_seconds: float = 2.0
    threshold: float = 0.7
    consecutive: int = 2
    # コードの 1 問（進んでいるか）で止める。規則に勝つまでは記録だけ（19 §13 の 4）
    act_on_progress: bool = False
    # 配信者が道具に添えた質問で止める・起こす
    act_on_questions: bool = True
    # ティックごとの記録（19 §6 の評価、費用の比較）。空なら残さない
    record_dir: str = "logs/watch"


@dataclass
class ReflexSettings:
    """襲われたときの反射の判断（docs/design/28）。きっかけと規則はブリッジ、選ぶのは Jev。"""

    judge: str = "jev"  # jev（Jev のキーがなければ rules）| rules（ブリッジの規則だけ）
    min_confidence: float = 0.5
    poll_seconds: float = 0.25
    timeout_seconds: float = 0.8
    record_dir: str = "logs/reflex"


@dataclass
class VisionSettings:
    """配信の画面を Gemini に見せる（docs/design/23 §2）。撮り方は obs の設定。"""

    enabled: bool = True
    review_interval_seconds: float = 240.0  # 定期の見直し
    failure_interval_seconds: float = 60.0  # 失敗の後に画像を添える最短の間隔


@dataclass
class MinecraftSettings:
    """Minecraft ブリッジへの接続、エージェント、最終目標の設定。"""

    bridge_host: str = "localhost"
    bridge_port: int = 3000
    # 行動の実行以外のリクエストのタイムアウト（行動は時間で止めないので、その返事は待ち続ける）
    request_timeout_seconds: float = 60.0
    max_steps_per_goal: int = 40
    max_consecutive_failures: int = 3
    max_stalled_steps: int = 8
    # 松明と食べ物のストックの段階（docs/design/84）: [{name, after?, or_mine?, torch: {low, carry, stock}, food: {...}}]
    supplies: list[dict[str, Any]] = field(default_factory=list)
    # 自分のメモ（docs/design/18_notes.md）。再起動をまたいで残す
    notes_path: str = "data/notes.json"
    # Blocks to collect when found nearby even if no goal needs them (the bridge always collects ores;
    # which tool can mine a block is the bridge's rule: docs/design/79_windfall.md)
    collect: list[str] = field(default_factory=lambda: ["obsidian", "clay", "sugar_cane"])
    mission: MissionSettings = field(default_factory=MissionSettings)
    # 行動の決め方（docs/design/21）: tools（既定。Gemini が道具を呼ぶ）か candidates（候補から Jev が
    # 選ぶ。前の方式、比べるときだけ）
    control: str = "tools"
    # 小目標の決め方（docs/design/26）: jev（Gemini が書いた中目標から Jev が選ぶ）か gemini（毎回 Gemini）
    small_goals: str = "jev"
    # 道具モードの 1 手（docs/design/34 §3）: jev（まず Jev が候補と技から選び、迷ったら Gemini）か
    # gemini（毎回 Gemini）
    step_picker: str = "jev"
    step_min_confidence: float = 0.5
    step_timeout_seconds: float = 3.0
    step_max_failures: int = 2
    step_gemini_every: int = 8
    step_record_dir: str = "logs/steps"
    # 小目標が行き詰まった後（docs/design/34 §7）: jev（まず Jev が別の中目標か Gemini に考え直させるかを
    # 選ぶ）か gemini（いつも Gemini が原因を分析する）
    failure_routing: str = "jev"
    # 行動の記録から行き詰まりを Jev が定期的に確かめる（docs/design/34 §9）。0 で確かめない
    stuck_check_steps: int = 6
    stuck_min_confidence: float = 0.75
    # 小目標の進み具合がこの分数変わらなければ、行動の記録を Jev に渡して確かめる（0: 時間では確かめない）
    stuck_check_quiet_minutes: float = 5.0
    # 位置・持ち物・小目標の進み具合がこの秒数変わらなければ（2 手以上したのに）、Jev に任せず LLM に
    # 失敗と結果を踏まえて選び直させる（0: しない）
    stagnation_seconds: float = 10.0
    stuck_record_dir: str = "logs/stuck"
    # 教訓帳（docs/design/35）: jev（コードで絞って Jev が確かめる）、rules（コードの上位だけ）、
    # off（使わない: 視聴者の教訓はメモへ）
    lessons: str = "jev"
    lessons_path: str = "data/lessons.json"
    lessons_record_dir: str = "logs/lessons"
    lessons_shown: int = 4
    lesson_candidates: int = 6
    lesson_min_confidence: float = 0.6
    lesson_timeout_seconds: float = 2.0
    max_lessons: int = 200
    # 覚えた候補（docs/design/52）: LLM が選んでうまくいった手（座標を含まない道具）を覚え、似た状況で
    # Jev の選択肢（候補）に出す（道具モードだけ）。learned_candidates: false で使わない
    learned_candidates: bool = True
    learned_candidates_path: str = "data/learned_candidates.json"
    learned_candidates_record_dir: str = "logs/learned_candidates"
    learned_candidates_shown: int = 3
    max_learned_candidates: int = 100
    # 今の小目標と歩みの数え（docs/design/63）: 再起動のあと、ブリッジが同じ小目標を追っていれば
    # 続きから追う。空にすると保存しない
    goal_path: str = "data/goal.json"
    # 検索の道具（docs/design/47 §3）: 一覧から検索語に合うキーを Jev が選ぶ
    search_min_confidence: float = 0.5
    search_timeout_seconds: float = 3.0
    search_record_dir: str = "logs/search"
    jev_goal_min_confidence: float = 0.4
    replan_minutes: float = 20.0
    # 一番上の大目標が、そのための行動をこれだけ続けても進まなければ Gemini が考え直す（0: 見ない）
    objective_stall_steps: int = 60
    goal_record_dir: str = "logs/goals"
    # 小目標が終わるたびの成績（終わり方、ステップ数、秒数。集計は tools/goal_outcomes.py。docs/design/82）
    goal_end_record_dir: str = "logs/goal_ends"
    # 実行の始まりごとの条件（commit、設定、モデル）。成績を実行ごとに比べるため（空: 残さない）
    run_record_dir: str = "logs/runs"
    # 技（docs/design/22）: 道具モードで、Gemini が JS の技を書いて覚え、直して使い回す
    skills_enabled: bool = True
    skill_rewrites_per_hour: int = 3  # 1 つの技を書き直せる回数（費用の上限）
    watch: WatchSettings = field(default_factory=lambda: WatchSettings())
    vision: VisionSettings = field(default_factory=lambda: VisionSettings())
    reflex: ReflexSettings = field(default_factory=lambda: ReflexSettings())


@dataclass
class CharacterSettings:
    """AI 配信者のキャラクターの設定。"""

    name: str = "AILoveShen"
    description: str = "明るく元気なAI配信者"
    speech_style: str = "フレンドリーで親しみやすい"
    first_person: str = "私"
    sentence_endings: list[str] = field(default_factory=lambda: ["だよ", "だね", "かな", "！"])
    personality_traits: list[str] = field(
        default_factory=lambda: [
            "好奇心旺盛",
            "ポジティブ",
            "ちょっとおっちょこちょい",
            "視聴者思い",
        ]
    )


@dataclass
class TTSServerSettings:
    """TTS サーバーへの接続の設定。"""

    host: str = "localhost"
    port: int = 5000


@dataclass
class TTSSettings:
    """TTS（Style-Bert-VITS2）の設定。"""

    server: TTSServerSettings = field(default_factory=TTSServerSettings)
    model_name: str = "default"
    default_style: str = "Neutral"
    speaking_rate: float = 1.0


@dataclass
class MemorySettings:
    """記憶の管理の設定。"""

    short_term_capacity: int = 20
    long_term_db: str = "data/memory.db"


@dataclass
class MCPSettings:
    """MCP（Model Context Protocol）の設定。"""

    memory: MemorySettings = field(default_factory=MemorySettings)


@dataclass
class OBSSettings:
    """OBS WebSocket の設定。"""

    host: str = "localhost"
    port: int = 4455
    password: str = ""
    # 配信の画面を Gemini に見せる（docs/design/23）: ゲームを映しているソースの名前。
    # シーン全体は撮らない（目標ボードなどが映り込む）。空なら撮らない
    game_source: str = "Minecraft"
    screenshot_width: int = 768
    screenshot_quality: int = 70
    timeout_seconds: float = 3.0
    retry_seconds: float = 60.0


@dataclass
class AvatarSettings:
    """配信者のアバター（VRM、docs/design/24_avatar.md）。目標ボードの /avatar で出す。"""

    model_path: str = "models/vrm/ailoveshen.vrm"
    # 口の動きの元: auto（TTS の再生のイベントが来たらそれに、来なければ生成した本文に）/ text / tts
    lip_sync: str = "auto"
    # 表情としぐさを選ぶもの: jev（状況と発言から Jev が選び、だめなら規則）/ rules（規則だけ）
    judge: str = "jev"
    judge_timeout_seconds: float = 1.5
    # Jev と規則の答えを並べた記録（見比べるため）。空なら残さない
    record_dir: str = "logs/avatar"


@dataclass
class StreamSettings:
    """本番の配信（python -m ailoveshen.stream、docs/streaming.md）。"""

    # 目標ボード・アバター・デバッグの Web サーバーのポート。0 なら出さない
    board_port: int = 8765
    # 実況と返事を TTS で読み上げる
    speak: bool = True
    # 小目標の切れ目の実況の間合い（docs/design/34 §6）: jev（今話す価値があるかを Jev が決める）か always
    commentary_judge: str = "jev"
    max_silence_seconds: float = 180.0
    commentary_record_dir: str = "logs/commentary"
    # 読み上げた文と、そのときの状態の記録（docs/design/65 §2）。空なら残さない
    spoken_record_dir: str = "logs/spoken"
    # 実況・一言を読む前に今の状態と照らす（docs/design/65 §4）: jev（Jev に聞く）か off
    speech_check: str = "jev"
    # 行動の途中の一言（docs/design/43）: 読み上げが終わってからこれだけ黙っていたら話す。0 なら話さない
    live_interval_seconds: float = 30.0
    # 状況が何も変わらないまま黙っていたら雑談を 1 行話すまでの秒数（待つ、長い歩き。0 なら話さない）
    live_idle_seconds: float = 60.0
    # 次の手や目標を決めている間・行き詰まっている間に黙っていたら、考え中の独り言を話すまでの秒数（0 なら話さない）
    live_thinking_seconds: float = 40.0
    # 管理画面（docs/design/54）で喋らせる文章のプリセットの保存先
    presets_path: str = "data/presets.json"


@dataclass
class LoggingSettings:
    """ログの設定。"""

    level: str = "INFO"
    format: str = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    file: str = "data/logs/ailoveshen.log"
    rotation: str = "10 MB"
    retention: str = "7 days"
    compression: str = "zip"


@dataclass
class Settings:
    """
    アプリケーションの設定。

    YAML ファイルから階層的に設定を読み込み、環境変数を展開する。
    """

    app_name: str = "AILoveShen"
    debug: bool = False

    twitch: TwitchSettings = field(default_factory=TwitchSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    character: CharacterSettings = field(default_factory=CharacterSettings)
    jev: JevSettings = field(default_factory=JevSettings)
    minecraft: MinecraftSettings = field(default_factory=MinecraftSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    mcp: MCPSettings = field(default_factory=MCPSettings)
    obs: OBSSettings = field(default_factory=OBSSettings)
    avatar: AvatarSettings = field(default_factory=AvatarSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    stream: StreamSettings = field(default_factory=StreamSettings)


# =============================================================================
# 設定の読み込み
# =============================================================================


def _truthy(value: object) -> bool:
    """設定の真偽（環境変数から来た "0" / "false" / "" は偽）。"""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def load_env_file(path: Path) -> list[str]:
    """
    .env ファイル（KEY=VALUE の行）を読み、まだ設定されていない環境変数だけを設定する。
    実際の環境変数（シェルで export したもの）が優先する。ファイルがなければ何もしない。

    書き方: 空行と # で始まる行は無視する。`export KEY=VALUE` も読む。値を ' か " で囲めば、
    そのまま（囲みを外して）使う。囲まない値の後ろの ` #` からはコメント。

    Returns:
        設定した変数の名前（値はログに出さない）
    """
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            # 値のない行の後ろのコメント（`KEY=   # 説明`）も、値の後ろのコメントも外す
            value = re.split(r"(?:^|\s)#", value, maxsplit=1)[0].rstrip()
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _expand_env_vars(value: Any) -> Any:
    """
    文字列の値の中の環境変数を再帰的に展開する。

    ${VAR} と ${VAR:-default} の構文に対応する。
    """
    if isinstance(value, str):
        # パターン: ${VAR} または ${VAR:-default}
        pattern = r"\$\{([^}:]+)(?::-([^}]*))?\}"

        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default = match.group(2) if match.group(2) is not None else ""
            return os.environ.get(var_name, default)

        return re.sub(pattern, replacer, value)

    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}

    elif isinstance(value, list):
        return [_expand_env_vars(item) for item in value]

    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    2つの辞書を深くマージする。

    override の値を優先する。入れ子の辞書は再帰的にマージする。
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def _support_settings(data: dict[str, Any]) -> TwitchSupportSettings:
    """twitch.support の辞書を設定にする。"""
    d = TwitchSupportSettings()
    return TwitchSupportSettings(
        enabled=bool(data.get("enabled", d.enabled)),
        follows=bool(data.get("follows", d.follows)),
        min_interval_seconds=float(data.get("min_interval_seconds", d.min_interval_seconds)),
        batch_seconds=float(data.get("batch_seconds", d.batch_seconds)),
        backlog=int(data.get("backlog", d.backlog)),
        dedupe_seconds=float(data.get("dedupe_seconds", d.dedupe_seconds)),
    )


def _dict_to_settings(data: dict[str, Any]) -> Settings:
    """辞書を Settings データクラスに変換する。"""
    settings = Settings()

    if "app_name" in data:
        settings.app_name = data["app_name"]
    if "debug" in data:
        settings.debug = bool(data["debug"])

    if "twitch" in data:
        twitch_data = data["twitch"]
        response_data = twitch_data.get("response") or {}
        settings.twitch = TwitchSettings(
            enabled=bool(twitch_data.get("enabled", True)),
            channel=twitch_data.get("channel", ""),
            client_id=twitch_data.get("client_id", ""),
            client_secret=twitch_data.get("client_secret", ""),
            access_token=twitch_data.get("access_token", ""),
            eventsub_token=str(twitch_data.get("eventsub_token") or ""),
            token_path=str(twitch_data.get("token_path", TwitchSettings.token_path)),
            open_browser=bool(twitch_data.get("open_browser", TwitchSettings.open_browser)),
            support=_support_settings(twitch_data.get("support") or {}),
            min_interval_seconds=float(
                response_data.get("min_interval_seconds", TwitchSettings.min_interval_seconds)
            ),
            backlog=int(response_data.get("backlog", TwitchSettings.backlog)),
            readings_path=str(twitch_data.get("readings_path", TwitchSettings.readings_path)),
            self_facts_path=str(twitch_data.get("self_facts_path", TwitchSettings.self_facts_path)),
            bot_login=str(twitch_data.get("bot_login") or ""),
            ignore_users=[
                str(u) for u in twitch_data.get("ignore_users", TwitchSettings().ignore_users) or []
            ],
            triage=str(response_data.get("triage", TwitchSettings.triage)),
            skip_min_confidence=float(
                response_data.get("skip_min_confidence", TwitchSettings.skip_min_confidence)
            ),
            triage_record_dir=str(
                response_data.get("triage_record_dir", TwitchSettings.triage_record_dir)
            ),
            promise_check=str(response_data.get("promise_check", TwitchSettings.promise_check)),
            promise_record_dir=str(
                response_data.get("promise_record_dir", TwitchSettings.promise_record_dir)
            ),
            fact_check=str(response_data.get("fact_check", TwitchSettings.fact_check)),
            fact_record_dir=str(
                response_data.get("fact_record_dir", TwitchSettings.fact_record_dir)
            ),
        )

    if "stream" in data:
        stream_data = data["stream"] or {}
        settings.stream = StreamSettings(
            board_port=int(stream_data.get("board_port") or 0),
            speak=bool(stream_data.get("speak", StreamSettings.speak)),
            commentary_judge=str(
                stream_data.get("commentary_judge", StreamSettings.commentary_judge)
            ),
            max_silence_seconds=float(
                stream_data.get("max_silence_seconds", StreamSettings.max_silence_seconds)
            ),
            commentary_record_dir=str(
                stream_data.get("commentary_record_dir", StreamSettings.commentary_record_dir)
            ),
            spoken_record_dir=str(
                stream_data.get("spoken_record_dir", StreamSettings.spoken_record_dir)
            ),
            speech_check=str(stream_data.get("speech_check", StreamSettings.speech_check)),
            live_interval_seconds=float(
                stream_data.get("live_interval_seconds", StreamSettings.live_interval_seconds)
            ),
            live_idle_seconds=float(
                stream_data.get("live_idle_seconds", StreamSettings.live_idle_seconds)
            ),
            live_thinking_seconds=float(
                stream_data.get("live_thinking_seconds", StreamSettings.live_thinking_seconds)
            ),
            presets_path=str(stream_data.get("presets_path", StreamSettings.presets_path)),
        )

    if "llm" in data:
        llm_data = data["llm"] or {}
        local_data = llm_data.get("local") or {}
        litellm_data = llm_data.get("litellm") or {}
        d = LocalLlmSettings()
        lite = LiteLlmSettings()
        settings.llm = LlmSettings(
            local=LocalLlmSettings(
                enabled=_truthy(local_data.get("enabled", d.enabled)),
                base_url=str(local_data.get("base_url", d.base_url)),
                model=str(local_data.get("model", d.model) or ""),
                api_key=str(local_data.get("api_key", d.api_key) or ""),
                context_tokens=int(local_data.get("context_tokens", d.context_tokens)),
                max_output_tokens=int(local_data.get("max_output_tokens", d.max_output_tokens)),
                response_format=str(local_data.get("response_format", d.response_format)),
                thinking_param=str(local_data.get("thinking_param", d.thinking_param)),
                thinking_purposes=[
                    str(p) for p in (local_data.get("thinking_purposes", d.thinking_purposes) or [])
                ],
                thinking_max_tokens={
                    str(k): int(v)
                    for k, v in (local_data.get("thinking_max_tokens", d.thinking_max_tokens) or {}).items()
                },
                output_max_tokens={
                    str(k): int(v)
                    for k, v in (local_data.get("output_max_tokens", d.output_max_tokens) or {}).items()
                },
                max_concurrent=int(local_data.get("max_concurrent", d.max_concurrent)),
                timeout_seconds=float(local_data.get("timeout_seconds", d.timeout_seconds)),
            ),
            litellm=LiteLlmSettings(
                base_url=str(litellm_data.get("base_url", lite.base_url)),
                model=str(litellm_data.get("model", lite.model) or ""),
                api_key=str(litellm_data.get("api_key", lite.api_key) or ""),
                context_tokens=int(litellm_data.get("context_tokens", lite.context_tokens)),
                max_output_tokens=int(litellm_data.get("max_output_tokens", lite.max_output_tokens)),
                response_format=str(litellm_data.get("response_format", lite.response_format)),
                models={
                    str(k): str(v) for k, v in (litellm_data.get("models", lite.models) or {}).items() if v
                },
                max_concurrent=int(litellm_data.get("max_concurrent", lite.max_concurrent)),
                timeout_seconds=float(litellm_data.get("timeout_seconds", lite.timeout_seconds)),
            ),
            routes={"default": "local", **dict(llm_data.get("routes") or {})},
            fallback=str(llm_data.get("fallback", "none")),
            thinking_levels={
                **DEFAULT_THINKING_LEVELS,
                **{str(k): str(v) for k, v in (llm_data.get("thinking_levels") or {}).items()},
            },
            default_thinking_level=str(llm_data.get("default_thinking_level", "low")),
            generation_log_dir=str(llm_data.get("generation_log_dir", "logs/generations") or ""),
            generation_log_keep_days=int(llm_data.get("generation_log_keep_days", 3)),
            debug_log_size=int(llm_data.get("debug_log_size", 50)),
        )

    if "character" in data:
        character_data = data["character"]
        defaults = CharacterSettings()
        settings.character = CharacterSettings(
            name=character_data.get("name", defaults.name),
            description=character_data.get("description", defaults.description),
            speech_style=character_data.get("speech_style", defaults.speech_style),
            first_person=character_data.get("first_person", defaults.first_person),
            sentence_endings=list(
                character_data.get("sentence_endings", defaults.sentence_endings)
            ),
            personality_traits=list(
                character_data.get("personality_traits", defaults.personality_traits)
            ),
        )

    if "jev" in data:
        jev_data = data["jev"]
        defaults = JevSettings()
        settings.jev = JevSettings(
            api_key=jev_data.get("api_key", defaults.api_key),
            model=jev_data.get("model", defaults.model),
            timeout_seconds=jev_data.get("timeout_seconds", defaults.timeout_seconds),
        )

    if "minecraft" in data:
        mc_data = data["minecraft"]
        bridge_data = mc_data.get("bridge", {})
        agent_data = mc_data.get("agent", {})
        mission_data = mc_data.get("mission", {})
        watch_data = mc_data.get("watch", {})
        reflex_data = mc_data.get("reflex", {}) or {}
        vision_data = mc_data.get("vision", {})
        defaults = MinecraftSettings()
        watch_defaults = defaults.watch
        mission_defaults = defaults.mission
        settings.minecraft = MinecraftSettings(
            bridge_host=bridge_data.get("host", defaults.bridge_host),
            bridge_port=int(bridge_data.get("port", defaults.bridge_port)),
            request_timeout_seconds=bridge_data.get(
                "timeout_seconds", defaults.request_timeout_seconds
            ),
            max_steps_per_goal=agent_data.get("max_steps_per_goal", defaults.max_steps_per_goal),
            max_consecutive_failures=agent_data.get(
                "max_consecutive_failures", defaults.max_consecutive_failures
            ),
            max_stalled_steps=agent_data.get("max_stalled_steps", defaults.max_stalled_steps),
            supplies=list(mc_data.get("supplies", defaults.supplies) or []),
            notes_path=agent_data.get("notes_path", defaults.notes_path),
            collect=list(agent_data.get("collect", defaults.collect)),
            mission=MissionSettings(
                text=mission_data.get("text", mission_defaults.text),
                objectives=mission_data.get("objectives", mission_defaults.objectives),
                playbook=mission_data.get("playbook", mission_defaults.playbook),
                max_objectives=mission_data.get("max_objectives", mission_defaults.max_objectives),
                max_viewer_objectives=mission_data.get(
                    "max_viewer_objectives", mission_defaults.max_viewer_objectives
                ),
                viewer_budget_steps=mission_data.get(
                    "viewer_budget_steps", mission_defaults.viewer_budget_steps
                ),
                store_path=mission_data.get("store_path", mission_defaults.store_path),
            ),
            control=agent_data.get("control", defaults.control),
            small_goals=agent_data.get("small_goals", defaults.small_goals),
            step_picker=agent_data.get("step_picker", defaults.step_picker),
            step_min_confidence=float(
                agent_data.get("step_min_confidence", defaults.step_min_confidence)
            ),
            step_timeout_seconds=float(
                agent_data.get("step_timeout_seconds", defaults.step_timeout_seconds)
            ),
            step_max_failures=int(agent_data.get("step_max_failures", defaults.step_max_failures)),
            step_gemini_every=int(agent_data.get("step_gemini_every", defaults.step_gemini_every)),
            step_record_dir=agent_data.get("step_record_dir", defaults.step_record_dir),
            failure_routing=agent_data.get("failure_routing", defaults.failure_routing),
            stuck_check_steps=int(agent_data.get("stuck_check_steps", defaults.stuck_check_steps)),
            stuck_min_confidence=float(
                agent_data.get("stuck_min_confidence", defaults.stuck_min_confidence)
            ),
            stagnation_seconds=float(agent_data.get("stagnation_seconds", defaults.stagnation_seconds)),
            stuck_check_quiet_minutes=float(
                agent_data.get("stuck_check_quiet_minutes", defaults.stuck_check_quiet_minutes)
            ),
            stuck_record_dir=agent_data.get("stuck_record_dir", defaults.stuck_record_dir),
            lessons=str(agent_data.get("lessons", defaults.lessons)),
            lessons_path=agent_data.get("lessons_path", defaults.lessons_path),
            lessons_record_dir=agent_data.get("lessons_record_dir", defaults.lessons_record_dir),
            lessons_shown=int(agent_data.get("lessons_shown", defaults.lessons_shown)),
            lesson_candidates=int(agent_data.get("lesson_candidates", defaults.lesson_candidates)),
            lesson_min_confidence=float(
                agent_data.get("lesson_min_confidence", defaults.lesson_min_confidence)
            ),
            lesson_timeout_seconds=float(
                agent_data.get("lesson_timeout_seconds", defaults.lesson_timeout_seconds)
            ),
            max_lessons=int(agent_data.get("max_lessons", defaults.max_lessons)),
            learned_candidates=bool(agent_data.get("learned_candidates", defaults.learned_candidates)),
            learned_candidates_path=agent_data.get("learned_candidates_path", defaults.learned_candidates_path),
            learned_candidates_record_dir=agent_data.get("learned_candidates_record_dir", defaults.learned_candidates_record_dir),
            learned_candidates_shown=int(agent_data.get("learned_candidates_shown", defaults.learned_candidates_shown)),
            max_learned_candidates=int(agent_data.get("max_learned_candidates", defaults.max_learned_candidates)),
            search_min_confidence=float(
                agent_data.get("search_min_confidence", defaults.search_min_confidence)
            ),
            search_timeout_seconds=float(
                agent_data.get("search_timeout_seconds", defaults.search_timeout_seconds)
            ),
            search_record_dir=agent_data.get("search_record_dir", defaults.search_record_dir),
            jev_goal_min_confidence=float(
                agent_data.get("jev_goal_min_confidence", defaults.jev_goal_min_confidence)
            ),
            replan_minutes=float(agent_data.get("replan_minutes", defaults.replan_minutes)),
            objective_stall_steps=int(
                agent_data.get("objective_stall_steps", defaults.objective_stall_steps)
            ),
            goal_record_dir=agent_data.get("goal_record_dir", defaults.goal_record_dir),
            goal_end_record_dir=agent_data.get(
                "goal_end_record_dir", defaults.goal_end_record_dir
            ),
            run_record_dir=agent_data.get("run_record_dir", defaults.run_record_dir),
            skills_enabled=bool(agent_data.get("skills", defaults.skills_enabled)),
            skill_rewrites_per_hour=int(
                agent_data.get("skill_rewrites_per_hour", defaults.skill_rewrites_per_hour)
            ),
            watch=WatchSettings(
                interval_seconds=watch_data.get(
                    "interval_seconds", watch_defaults.interval_seconds
                ),
                grace_seconds=watch_data.get("grace_seconds", watch_defaults.grace_seconds),
                threshold=watch_data.get("threshold", watch_defaults.threshold),
                consecutive=watch_data.get("consecutive", watch_defaults.consecutive),
                act_on_progress=watch_data.get("act_on_progress", watch_defaults.act_on_progress),
                act_on_questions=watch_data.get(
                    "act_on_questions", watch_defaults.act_on_questions
                ),
                record_dir=watch_data.get("record_dir", watch_defaults.record_dir),
            ),
            reflex=ReflexSettings(
                judge=reflex_data.get("judge", defaults.reflex.judge),
                min_confidence=reflex_data.get("min_confidence", defaults.reflex.min_confidence),
                poll_seconds=reflex_data.get("poll_seconds", defaults.reflex.poll_seconds),
                timeout_seconds=reflex_data.get("timeout_seconds", defaults.reflex.timeout_seconds),
                record_dir=reflex_data.get("record_dir", defaults.reflex.record_dir),
            ),
            vision=VisionSettings(
                enabled=vision_data.get("enabled", defaults.vision.enabled),
                review_interval_seconds=vision_data.get(
                    "review_interval_seconds", defaults.vision.review_interval_seconds
                ),
                failure_interval_seconds=vision_data.get(
                    "failure_interval_seconds", defaults.vision.failure_interval_seconds
                ),
            ),
        )

    if "tts" in data:
        tts_data = data["tts"]
        server_data = tts_data.get("server", {})
        settings.tts = TTSSettings(
            server=TTSServerSettings(
                host=server_data.get("host", "localhost"),
                port=server_data.get("port", 5000),
            ),
            model_name=tts_data.get("model_name", "default"),
            default_style=tts_data.get("default_style", "Neutral"),
            speaking_rate=tts_data.get("speaking_rate", 1.0),
        )

    if "mcp" in data:
        mcp_data = data["mcp"]
        memory_data = mcp_data.get("memory", {})
        settings.mcp = MCPSettings(
            memory=MemorySettings(
                short_term_capacity=memory_data.get("short_term_capacity", 20),
                long_term_db=memory_data.get("long_term_db", "data/memory.db"),
            ),
        )

    if "avatar" in data:
        avatar_data = data["avatar"] or {}
        settings.avatar = AvatarSettings(
            model_path=avatar_data.get("model_path", AvatarSettings.model_path),
            lip_sync=avatar_data.get("lip_sync", AvatarSettings.lip_sync),
            judge=avatar_data.get("judge", AvatarSettings.judge),
            judge_timeout_seconds=avatar_data.get(
                "judge_timeout_seconds", AvatarSettings.judge_timeout_seconds
            ),
            record_dir=avatar_data.get("record_dir", AvatarSettings.record_dir),
        )

    if "obs" in data:
        obs_data = data["obs"]
        obs_defaults = OBSSettings()
        settings.obs = OBSSettings(
            host=obs_data.get("host", "localhost"),
            port=int(obs_data.get("port", 4455)),
            password=obs_data.get("password", ""),
            game_source=obs_data.get("game_source", obs_defaults.game_source) or "",
            screenshot_width=obs_data.get("screenshot_width", obs_defaults.screenshot_width),
            screenshot_quality=obs_data.get("screenshot_quality", obs_defaults.screenshot_quality),
            timeout_seconds=obs_data.get("timeout_seconds", obs_defaults.timeout_seconds),
            retry_seconds=obs_data.get("retry_seconds", obs_defaults.retry_seconds),
        )

    if "logging" in data:
        logging_data = data["logging"]
        settings.logging = LoggingSettings(
            level=logging_data.get("level", "INFO"),
            format=logging_data.get(
                "format",
                "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            ),
            file=logging_data.get("file", "data/logs/ailoveshen.log"),
            rotation=logging_data.get("rotation", "10 MB"),
            retention=logging_data.get("retention", "7 days"),
            compression=logging_data.get("compression", "zip"),
        )

    return settings


def load_yaml_file(path: Path) -> dict[str, Any]:
    """
    YAML ファイルを読み込んで解析する。

    Args:
        path: YAML ファイルのパス。

    Returns:
        解析した YAML の内容（辞書）。

    Raises:
        ConfigurationError: ファイルを読めないか、解析できないとき。
    """
    if not path.exists():
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if data else {}
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Invalid YAML in {path}: {e}") from e
    except OSError as e:
        raise ConfigurationError(f"Failed to read {path}: {e}") from e


def load_settings(
    config_dir: Path | str | None = None,
    env: str | None = None,
    env_file: Path | str | None = None,
) -> Settings:
    """
    YAML ファイルから、階層的に上書きして設定を読み込む。

    読み込む順（後のものが前のものを上書きする）:
    1. default.yaml
    2. {env}.yaml（例: development.yaml、production.yaml）
    3. 環境変数（YAML の ${VAR}）。.env ファイルに書いたものも環境変数として読む（実際の
       環境変数が優先する）

    Args:
        config_dir: 設定ファイルのあるディレクトリ。既定は 'config/'。
        env: 環境名。既定は APP_ENV、なければ 'development'。
        env_file: 読む .env ファイル。既定は config_dir の親（リポジトリの直下）の .env。

    Returns:
        すべての設定を読み込んだ Settings。
    """
    if config_dir is None:
        config_dir = Path("config")
    elif isinstance(config_dir, str):
        config_dir = Path(config_dir)

    # .env を先に読む（APP_ENV も .env に書ける）
    env_path_file = Path(env_file) if env_file is not None else config_dir.parent / ".env"
    loaded = load_env_file(env_path_file)
    if loaded:
        logger.debug(f"{env_path_file} から環境変数を読んだ: {', '.join(loaded)}")

    config = load_config_dict(config_dir, env)
    return _dict_to_settings(config)


def load_config_dict(config_dir: Path, env: str | None = None) -> dict[str, Any]:
    """
    default.yaml に {env}.yaml を重ね、${VAR} を展開した辞書（.env は読まない。先に
    load_settings か load_env_file で読む）。辞書で設定を受け取るファクトリー（TTS）に渡す。
    """
    if env is None:
        env = os.environ.get("APP_ENV", "development")
    config = load_yaml_file(config_dir / "default.yaml")
    env_path = config_dir / f"{env}.yaml"
    if env_path.exists():
        config = _deep_merge(config, load_yaml_file(env_path))
    return _expand_env_vars(config)
