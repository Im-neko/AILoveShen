"""YAML と環境変数に対応した設定の管理。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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

    channel: str = ""
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""


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
    "goal": "low",
    "goal_after_failure": "medium",
    "tool": "low",
    "tool_after_failure": "medium",
    "commentary": "low",
    "reply": "low",
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
    logging: LoggingSettings = field(default_factory=LoggingSettings)


# =============================================================================
# 設定の読み込み
# =============================================================================


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
        settings.twitch = TwitchSettings(
            channel=twitch_data.get("channel", ""),
            client_id=twitch_data.get("client_id", ""),
            client_secret=twitch_data.get("client_secret", ""),
            access_token=twitch_data.get("access_token", ""),
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
        defaults = MinecraftSettings()
        watch_defaults = defaults.watch
        mission_defaults = defaults.mission
        settings.minecraft = MinecraftSettings(
            bridge_host=bridge_data.get("host", defaults.bridge_host),
            bridge_port=bridge_data.get("port", defaults.bridge_port),
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

    if "obs" in data:
        obs_data = data["obs"]
        settings.obs = OBSSettings(
            host=obs_data.get("host", "localhost"),
            port=obs_data.get("port", 4455),
            password=obs_data.get("password", ""),
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
) -> Settings:
    """
    YAML ファイルから、階層的に上書きして設定を読み込む。

    読み込む順（後のものが前のものを上書きする）:
    1. default.yaml
    2. {env}.yaml（例: development.yaml、production.yaml）
    3. 環境変数

    Args:
        config_dir: 設定ファイルのあるディレクトリ。既定は 'config/'。
        env: 環境名。既定は APP_ENV、なければ 'development'。

    Returns:
        すべての設定を読み込んだ Settings。
    """
    if config_dir is None:
        config_dir = Path("config")
    elif isinstance(config_dir, str):
        config_dir = Path(config_dir)

    if env is None:
        env = os.environ.get("APP_ENV", "development")

    # 既定の設定を読み込む
    config: dict[str, Any] = {}
    default_path = config_dir / "default.yaml"
    if default_path.exists():
        config = load_yaml_file(default_path)

    # 環境ごとの設定を読み込む
    env_path = config_dir / f"{env}.yaml"
    if env_path.exists():
        env_config = load_yaml_file(env_path)
        config = _deep_merge(config, env_config)

    # 環境変数を展開する
    config = _expand_env_vars(config)

    return _dict_to_settings(config)
