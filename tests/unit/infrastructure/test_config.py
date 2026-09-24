"""Unit tests for configuration management."""

import os
import tempfile
from pathlib import Path

import pytest

from ailoveshen.infrastructure.config import (
    CharacterSettings,
    ConfigurationError,
    GeminiSettings,
    LoggingSettings,
    Settings,
    TTSServerSettings,
    TTSSettings,
    TwitchSettings,
    _deep_merge,
    _expand_env_vars,
    load_settings,
    load_yaml_file,
)


class TestExpandEnvVars:
    """Tests for environment variable expansion."""

    def test_expand_simple_var(self):
        """Test expanding simple ${VAR} syntax."""
        os.environ["TEST_VAR"] = "test_value"
        try:
            result = _expand_env_vars("${TEST_VAR}")
            assert result == "test_value"
        finally:
            del os.environ["TEST_VAR"]

    def test_expand_with_default(self):
        """Test expanding ${VAR:-default} syntax."""
        # When var exists
        os.environ["TEST_VAR"] = "test_value"
        try:
            result = _expand_env_vars("${TEST_VAR:-default}")
            assert result == "test_value"
        finally:
            del os.environ["TEST_VAR"]

        # When var doesn't exist
        result = _expand_env_vars("${NONEXISTENT_VAR:-default}")
        assert result == "default"

    def test_expand_missing_var_empty(self):
        """Test missing var without default returns empty."""
        result = _expand_env_vars("${NONEXISTENT_VAR}")
        assert result == ""

    def test_expand_in_dict(self):
        """Test expanding vars in nested dict."""
        os.environ["TEST_KEY"] = "key_value"
        try:
            data = {"nested": {"key": "${TEST_KEY}"}}
            result = _expand_env_vars(data)
            assert result["nested"]["key"] == "key_value"
        finally:
            del os.environ["TEST_KEY"]

    def test_expand_in_list(self):
        """Test expanding vars in list."""
        os.environ["TEST_ITEM"] = "item_value"
        try:
            data = ["${TEST_ITEM}", "plain"]
            result = _expand_env_vars(data)
            assert result == ["item_value", "plain"]
        finally:
            del os.environ["TEST_ITEM"]

    def test_no_expansion_for_non_string(self):
        """Test non-string values pass through unchanged."""
        assert _expand_env_vars(123) == 123
        assert _expand_env_vars(True) is True
        assert _expand_env_vars(None) is None


class TestDeepMerge:
    """Tests for deep merge functionality."""

    def test_simple_merge(self):
        """Test simple dict merge."""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        """Test nested dict merge."""
        base = {"outer": {"a": 1, "b": 2}}
        override = {"outer": {"b": 3, "c": 4}}
        result = _deep_merge(base, override)
        assert result == {"outer": {"a": 1, "b": 3, "c": 4}}

    def test_override_replaces_non_dict(self):
        """Test override replaces non-dict values."""
        base = {"key": "original"}
        override = {"key": "new"}
        result = _deep_merge(base, override)
        assert result["key"] == "new"

    def test_original_unchanged(self):
        """Test original dict is not modified."""
        base = {"a": 1}
        override = {"b": 2}
        _deep_merge(base, override)
        assert "b" not in base


class TestLoadYamlFile:
    """Tests for YAML file loading."""

    def test_load_existing_file(self):
        """Test loading existing YAML file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("key: value\nnumber: 42")
            f.flush()
            path = Path(f.name)

        try:
            result = load_yaml_file(path)
            assert result == {"key": "value", "number": 42}
        finally:
            path.unlink()

    def test_load_nonexistent_file(self):
        """Test loading nonexistent file returns empty dict."""
        result = load_yaml_file(Path("/nonexistent/file.yaml"))
        assert result == {}

    def test_load_empty_file(self):
        """Test loading empty file returns empty dict."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("")
            f.flush()
            path = Path(f.name)

        try:
            result = load_yaml_file(path)
            assert result == {}
        finally:
            path.unlink()

    def test_load_invalid_yaml_raises_error(self):
        """Test loading invalid YAML raises ConfigurationError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("invalid: yaml: content: [")
            f.flush()
            path = Path(f.name)

        try:
            with pytest.raises(ConfigurationError, match="Invalid YAML"):
                load_yaml_file(path)
        finally:
            path.unlink()


class TestLoadSettings:
    """Tests for settings loading."""

    def test_load_default_settings(self):
        """Test loading with no config files returns defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = load_settings(config_dir=tmpdir)
            assert settings.app_name == "AILoveShen"
            assert settings.debug is False

    def test_load_from_default_yaml(self):
        """Test loading from default.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            default_yaml = config_dir / "default.yaml"
            default_yaml.write_text("app_name: TestApp\ndebug: true")

            settings = load_settings(config_dir=config_dir)
            assert settings.app_name == "TestApp"
            assert settings.debug is True

    def test_env_specific_overrides_default(self):
        """Test environment-specific config overrides default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            default_yaml = config_dir / "default.yaml"
            default_yaml.write_text("app_name: DefaultApp\ndebug: false")

            dev_yaml = config_dir / "development.yaml"
            dev_yaml.write_text("debug: true")

            settings = load_settings(config_dir=config_dir, env="development")
            assert settings.app_name == "DefaultApp"  # From default
            assert settings.debug is True  # Overridden

    def test_env_var_expansion(self):
        """Test environment variables are expanded."""
        os.environ["TEST_CHANNEL"] = "testchannel"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                config_dir = Path(tmpdir)
                default_yaml = config_dir / "default.yaml"
                default_yaml.write_text(
                    "twitch:\n  channel: ${TEST_CHANNEL}"
                )

                settings = load_settings(config_dir=config_dir)
                assert settings.twitch.channel == "testchannel"
        finally:
            del os.environ["TEST_CHANNEL"]


class TestLoadGeminiAndCharacterSettings:
    """Tests for loading Gemini and character sections."""

    def test_load_gemini_section(self):
        """Test gemini section including nested retry/rate_limit is parsed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            (config_dir / "default.yaml").write_text(
                "gemini:\n"
                "  main_model: gemini-x\n"
                "  main_thinking_level: high\n"
                "  max_output_tokens: 1024\n"
                "  retry:\n"
                "    max_attempts: 5\n"
                "    max_delay_seconds: 20.0\n"
                "  rate_limit:\n"
                "    min_interval_seconds: 0.5\n"
            )

            settings = load_settings(config_dir=config_dir)
            assert settings.gemini.main_model == "gemini-x"
            assert settings.gemini.main_thinking_level == "high"
            assert settings.gemini.filter_thinking_level == "low"  # default
            assert settings.gemini.max_output_tokens == 1024
            assert settings.gemini.retry.max_attempts == 5
            assert settings.gemini.retry.max_delay_seconds == 20.0
            assert settings.gemini.retry.base_delay_seconds == 1.0  # default
            assert settings.gemini.rate_limit.min_interval_seconds == 0.5

    def test_load_character_section(self):
        """Test character section is parsed with defaults for missing keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            (config_dir / "default.yaml").write_text(
                "character:\n"
                "  name: Shen\n"
                "  sentence_endings:\n"
                "    - のだ\n"
            )

            settings = load_settings(config_dir=config_dir)
            assert settings.character.name == "Shen"
            assert settings.character.sentence_endings == ["のだ"]
            assert settings.character.first_person == "私"  # default

    def test_project_default_yaml_loads(self):
        """Test the project's config/default.yaml loads the Phase 3 sections."""
        config_dir = Path(__file__).resolve().parents[3] / "config"
        settings = load_settings(config_dir=config_dir, env="nonexistent")
        assert settings.gemini.main_model == "gemini-3.8-flash"
        assert settings.gemini.main_thinking_level in {"low", "medium", "high"}
        assert settings.gemini.filter_thinking_level in {"low", "medium", "high"}
        assert settings.character.name


class TestSettingsDataclasses:
    """Tests for settings dataclasses."""

    def test_settings_defaults(self):
        """Test Settings has correct defaults."""
        settings = Settings()
        assert settings.app_name == "AILoveShen"
        assert isinstance(settings.twitch, TwitchSettings)
        assert isinstance(settings.gemini, GeminiSettings)
        assert isinstance(settings.tts, TTSSettings)
        assert isinstance(settings.logging, LoggingSettings)

    def test_twitch_settings_defaults(self):
        """Test TwitchSettings defaults."""
        twitch = TwitchSettings()
        assert twitch.channel == ""
        assert twitch.client_id == ""

    def test_gemini_settings_defaults(self):
        """Test GeminiSettings defaults."""
        gemini = GeminiSettings()
        assert gemini.main_model == "gemini-3.8-flash"
        assert gemini.filter_model == "gemini-3.8-flash"
        assert gemini.main_thinking_level == "low"
        assert gemini.filter_thinking_level == "low"
        assert gemini.max_output_tokens == 8192
        assert gemini.retry.max_attempts == 3
        assert gemini.retry.base_delay_seconds == 1.0
        assert gemini.retry.max_delay_seconds == 10.0
        assert gemini.rate_limit.min_interval_seconds == 1.0

    def test_character_settings_defaults(self):
        """Test CharacterSettings defaults."""
        character = CharacterSettings()
        assert character.name == "AILoveShen"
        assert character.first_person == "私"
        assert character.sentence_endings == ["だよ", "だね", "かな", "！"]

    def test_tts_settings_defaults(self):
        """Test TTSSettings defaults."""
        tts = TTSSettings()
        assert isinstance(tts.server, TTSServerSettings)
        assert tts.server.host == "localhost"
        assert tts.server.port == 5000

    def test_logging_settings_defaults(self):
        """Test LoggingSettings defaults."""
        logging = LoggingSettings()
        assert logging.level == "INFO"
        assert logging.rotation == "10 MB"
        assert logging.retention == "7 days"


class TestLoadJevAndMinecraftSettings:
    """Tests for loading the jev and minecraft sections."""

    def test_load_sections(self):
        """Test jev and minecraft (bridge + agent) are parsed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            (config_dir / "default.yaml").write_text(
                "jev:\n"
                "  api_key: k\n"
                "  model: jev-preview\n"
                "minecraft:\n"
                "  bridge:\n"
                "    port: 4000\n"
                "    timeout_seconds: 90\n"
                "  agent:\n"
                "    max_steps_per_goal: 9\n"
            )

            settings = load_settings(config_dir=config_dir)

            assert settings.jev.api_key == "k"
            assert settings.jev.model == "jev-preview"
            assert settings.jev.timeout_seconds == 10.0
            assert settings.minecraft.bridge_port == 4000
            assert settings.minecraft.bridge_host == "localhost"
            assert settings.minecraft.request_timeout_seconds == 90
            assert settings.minecraft.max_steps_per_goal == 9
            assert settings.minecraft.max_consecutive_failures == 3

    def test_project_default_yaml_reads_typesafe_key(self):
        """Test the shipped default.yaml takes the Jev key from TYPESAFE_API_KEY."""
        os.environ["TYPESAFE_API_KEY"] = "from-env"
        try:
            settings = load_settings(config_dir=Path(__file__).parents[3] / "config")
        finally:
            del os.environ["TYPESAFE_API_KEY"]

        assert settings.jev.api_key == "from-env"
        assert settings.minecraft.bridge_port == 3000
