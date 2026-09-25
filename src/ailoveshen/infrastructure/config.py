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


@dataclass
class GeminiRetrySettings:
    """Gemini API の再試行の設定（指数バックオフ）。"""

    max_attempts: int = 3  # 最初のリクエストを含む
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 10.0
    exponential_base: float = 2.0


@dataclass
class GeminiRateLimitSettings:
    """Gemini API のレート制限の設定。"""

    min_interval_seconds: float = 1.0


DEFAULT_THINKING_LEVELS = {
    "town": "high",
    "site": "high",
    "house": "medium",
    "build_design": "high",
    "goal": "low",
    "goal_after_failure": "medium",
    "tool": "low",
    "tool_after_failure": "medium",
    "commentary": "low",
    "reply": "low",
    "screen_review": "medium",
}


@dataclass
class GeminiSettings:
    """Gemini API の設定。"""

    api_key: str = ""
    main_model: str = "gemini-3.8-flash"
    filter_model: str = "gemini-3.8-flash"
    main_thinking_level: str = "low"
    filter_thinking_level: str = "low"
    # 思考のトークンを含む。小さすぎると出力が空になる
    max_output_tokens: int = 8192
    # 用途（呼び出しの purpose）ごとの thinking_level（docs/design/19 §7、21 §7）。
    # ない用途は main_thinking_level
    thinking_levels: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_THINKING_LEVELS))
    # デバッグ: 思考の要約も返させ、直近の呼び出しを目標ボードの /api/debug/gemini に出す
    include_thoughts: bool = True
    debug_log_size: int = 50
    # 画像を添えるときの解像度（low / medium / high。低いほど画像のトークンが少ない）
    media_resolution: str = "low"
    # 用途ごとの解像度（建物の設計の地図は、細かい色を読むので medium）
    media_resolutions: dict[str, str] = field(default_factory=lambda: {"build_design": "medium"})
    retry: GeminiRetrySettings = field(default_factory=GeminiRetrySettings)
    rate_limit: GeminiRateLimitSettings = field(default_factory=GeminiRateLimitSettings)


@dataclass
class JevSettings:
    """Jev（TypeSafe AI の System One）の設定。"""

    api_key: str = ""
    model: str = "jev-latest"
    timeout_seconds: float = 10.0


def _default_mid_goals() -> list[dict[str, Any]]:
    return [
        {
            "title": "自分の家を作る",
            "conditions": [{"predicate": "built"}],
            "reason": "夜を安全に過ごす拠点",
        },
        {
            "title": "夜に寝られるようにする",
            "conditions": [{"predicate": "placed", "item": "bed"}],
            "reason": "夜を飛ばして昼に活動する",
        },
        {
            "title": "身を守る道具を持つ",
            "conditions": [{"predicate": "have", "item": "wooden_sword", "count": 1}],
            "reason": "敵と戦えるようにする",
        },
        {
            "title": "食料を蓄える",
            "conditions": [{"predicate": "have", "item": "food", "count": 8}],
            "reason": "空腹で動けなくならないように",
        },
    ]


@dataclass
class MissionSettings:
    """大目標（コメントでは変わらない）、最初の中目標、計画の上限。"""

    text: str = "生き延びながら家を建て、街にしていく"
    # [{title, conditions: [{predicate, item?, count?}], reason}]: 保存したものがないときのリスト
    mid_goals: list[dict[str, Any]] = field(default_factory=_default_mid_goals)
    max_mid_goals: int = 6
    max_viewer_mid_goals: int = 2
    viewer_budget_steps: int = 80
    # 中目標は再起動をまたいで引き継ぐ（大目標の文が同じなら）
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
class VisionSettings:
    """配信の画面を Gemini に見せる（docs/design/23 §2）。撮り方は obs の設定。"""

    enabled: bool = True
    review_interval_seconds: float = 240.0  # 定期の見直し
    failure_interval_seconds: float = 60.0  # 失敗の後に画像を添える最短の間隔


@dataclass
class MinecraftSettings:
    """Minecraft ブリッジへの接続、エージェント、大目標の設定。"""

    bridge_host: str = "localhost"
    bridge_port: int = 3000
    # ブリッジの行動の最も長いタイムアウト（45秒、primitives.mjs の TIMEOUTS_MS）より長くすること
    request_timeout_seconds: float = 60.0
    max_steps_per_goal: int = 40
    max_consecutive_failures: int = 3
    max_stalled_steps: int = 8
    # 自分のメモ（docs/design/18_notes.md）。再起動をまたいで残す
    notes_path: str = "data/notes.json"
    mission: MissionSettings = field(default_factory=MissionSettings)
    # 行動の決め方（docs/design/21）: candidates（候補から Jev が選ぶ）か
    # tools（Gemini が道具を呼ぶ）
    control: str = "candidates"
    watch: WatchSettings = field(default_factory=lambda: WatchSettings())
    vision: VisionSettings = field(default_factory=lambda: VisionSettings())


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
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
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
            value = value.split(" #", 1)[0].rstrip()
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
            min_interval_seconds=float(
                response_data.get("min_interval_seconds", TwitchSettings.min_interval_seconds)
            ),
            backlog=int(response_data.get("backlog", TwitchSettings.backlog)),
        )

    if "stream" in data:
        stream_data = data["stream"] or {}
        settings.stream = StreamSettings(
            board_port=int(stream_data.get("board_port") or 0),
            speak=bool(stream_data.get("speak", StreamSettings.speak)),
        )

    if "gemini" in data:
        gemini_data = data["gemini"]
        retry_data = gemini_data.get("retry", {})
        rate_limit_data = gemini_data.get("rate_limit", {})
        settings.gemini = GeminiSettings(
            api_key=gemini_data.get("api_key", ""),
            main_model=gemini_data.get("main_model", "gemini-3.8-flash"),
            filter_model=gemini_data.get("filter_model", "gemini-3.8-flash"),
            main_thinking_level=gemini_data.get("main_thinking_level", "low"),
            filter_thinking_level=gemini_data.get("filter_thinking_level", "low"),
            max_output_tokens=gemini_data.get("max_output_tokens", 8192),
            thinking_levels={
                **DEFAULT_THINKING_LEVELS,
                **dict(gemini_data.get("thinking_levels") or {}),
            },
            include_thoughts=gemini_data.get("include_thoughts", True),
            debug_log_size=gemini_data.get("debug_log_size", 50),
            media_resolution=gemini_data.get("media_resolution", "low"),
            media_resolutions={
                "build_design": "medium",
                **dict(gemini_data.get("media_resolutions") or {}),
            },
            retry=GeminiRetrySettings(
                max_attempts=retry_data.get("max_attempts", 3),
                base_delay_seconds=retry_data.get("base_delay_seconds", 1.0),
                max_delay_seconds=retry_data.get("max_delay_seconds", 10.0),
                exponential_base=retry_data.get("exponential_base", 2.0),
            ),
            rate_limit=GeminiRateLimitSettings(
                min_interval_seconds=rate_limit_data.get("min_interval_seconds", 1.0),
            ),
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
            notes_path=agent_data.get("notes_path", defaults.notes_path),
            mission=MissionSettings(
                text=mission_data.get("text", mission_defaults.text),
                mid_goals=mission_data.get("mid_goals", mission_defaults.mid_goals),
                max_mid_goals=mission_data.get("max_mid_goals", mission_defaults.max_mid_goals),
                max_viewer_mid_goals=mission_data.get(
                    "max_viewer_mid_goals", mission_defaults.max_viewer_mid_goals
                ),
                viewer_budget_steps=mission_data.get(
                    "viewer_budget_steps", mission_defaults.viewer_budget_steps
                ),
                store_path=mission_data.get("store_path", mission_defaults.store_path),
            ),
            control=agent_data.get("control", defaults.control),
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
