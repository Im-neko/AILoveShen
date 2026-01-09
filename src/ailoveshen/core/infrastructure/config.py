"""Configuration management with YAML and environment variable support."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# =============================================================================
# Exceptions
# =============================================================================


class ConfigurationError(Exception):
    """Raised when configuration loading or parsing fails."""

    pass


# =============================================================================
# Settings Data Classes
# =============================================================================


@dataclass
class TwitchSettings:
    """Twitch integration settings."""

    channel: str = ""
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""


@dataclass
class GeminiSettings:
    """Gemini API settings."""

    api_key: str = ""
    main_model: str = "gemini-2.5-pro-preview-05-06"
    filter_model: str = "gemini-2.0-flash"
    max_tokens: int = 500
    temperature: float = 0.7


@dataclass
class TTSServerSettings:
    """TTS server connection settings."""

    host: str = "localhost"
    port: int = 5000


@dataclass
class TTSSettings:
    """TTS (Style-Bert-VITS2) settings."""

    server: TTSServerSettings = field(default_factory=TTSServerSettings)
    model_name: str = "default"
    default_style: str = "Neutral"
    speaking_rate: float = 1.0


@dataclass
class MemorySettings:
    """Memory management settings."""

    short_term_capacity: int = 20
    long_term_db: str = "data/memory.db"


@dataclass
class MCPSettings:
    """MCP (Model Context Protocol) settings."""

    memory: MemorySettings = field(default_factory=MemorySettings)


@dataclass
class OBSSettings:
    """OBS WebSocket settings."""

    host: str = "localhost"
    port: int = 4455
    password: str = ""


@dataclass
class NitroGenSettings:
    """NitroGen integration settings."""

    enabled: bool = False
    host: str = "localhost"
    port: int = 8080


@dataclass
class LoggingSettings:
    """Logging configuration."""

    level: str = "INFO"
    format: str = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    file: str = "data/logs/ailoveshen.log"
    rotation: str = "10 MB"
    retention: str = "7 days"
    compression: str = "zip"


@dataclass
class Settings:
    """
    Application settings.

    Supports hierarchical configuration loading from YAML files
    with environment variable expansion.
    """

    app_name: str = "AILoveShen"
    debug: bool = False

    twitch: TwitchSettings = field(default_factory=TwitchSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    mcp: MCPSettings = field(default_factory=MCPSettings)
    obs: OBSSettings = field(default_factory=OBSSettings)
    nitrogen: NitroGenSettings = field(default_factory=NitroGenSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)


# =============================================================================
# Configuration Loading
# =============================================================================


def _expand_env_vars(value: Any) -> Any:
    """
    Recursively expand environment variables in string values.

    Supports ${VAR} and ${VAR:-default} syntax.
    """
    if isinstance(value, str):
        # Pattern: ${VAR} or ${VAR:-default}
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
    Deep merge two dictionaries.

    Values from override take precedence. Nested dicts are merged recursively.
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def _dict_to_settings(data: dict[str, Any]) -> Settings:
    """Convert a dictionary to Settings dataclass."""
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
        settings.gemini = GeminiSettings(
            api_key=gemini_data.get("api_key", ""),
            main_model=gemini_data.get("main_model", "gemini-2.5-pro-preview-05-06"),
            filter_model=gemini_data.get("filter_model", "gemini-2.0-flash"),
            max_tokens=gemini_data.get("max_tokens", 500),
            temperature=gemini_data.get("temperature", 0.7),
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

    if "nitrogen" in data:
        nitrogen_data = data["nitrogen"]
        settings.nitrogen = NitroGenSettings(
            enabled=nitrogen_data.get("enabled", False),
            host=nitrogen_data.get("host", "localhost"),
            port=nitrogen_data.get("port", 8080),
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
    Load and parse a YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed YAML content as a dictionary.

    Raises:
        ConfigurationError: If the file cannot be read or parsed.
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
    Load settings from YAML files with hierarchical override.

    Loading order (later overrides earlier):
    1. default.yaml
    2. {env}.yaml (e.g., development.yaml, production.yaml)
    3. Environment variables

    Args:
        config_dir: Directory containing config files. Defaults to 'config/'.
        env: Environment name. Defaults to APP_ENV or 'development'.

    Returns:
        Settings object with all configuration loaded.
    """
    if config_dir is None:
        config_dir = Path("config")
    elif isinstance(config_dir, str):
        config_dir = Path(config_dir)

    if env is None:
        env = os.environ.get("APP_ENV", "development")

    # Load default config
    config: dict[str, Any] = {}
    default_path = config_dir / "default.yaml"
    if default_path.exists():
        config = load_yaml_file(default_path)

    # Load environment-specific config
    env_path = config_dir / f"{env}.yaml"
    if env_path.exists():
        env_config = load_yaml_file(env_path)
        config = _deep_merge(config, env_config)

    # Expand environment variables
    config = _expand_env_vars(config)

    return _dict_to_settings(config)
