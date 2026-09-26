"""設定の管理の単体テスト。"""

import os
import tempfile
from pathlib import Path

import pytest

from ailoveshen.infrastructure.config import (
    CharacterSettings,
    ConfigurationError,
    GeminiSettings,
    LoggingSettings,
    MissionSettings,
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
    """環境変数の展開のテスト。"""

    def test_expand_simple_var(self):
        """単純な ${VAR} を展開する。"""
        os.environ["TEST_VAR"] = "test_value"
        try:
            result = _expand_env_vars("${TEST_VAR}")
            assert result == "test_value"
        finally:
            del os.environ["TEST_VAR"]

    def test_expand_with_default(self):
        """${VAR:-default} を展開する。"""
        # 変数があるとき
        os.environ["TEST_VAR"] = "test_value"
        try:
            result = _expand_env_vars("${TEST_VAR:-default}")
            assert result == "test_value"
        finally:
            del os.environ["TEST_VAR"]

        # 変数がないとき
        result = _expand_env_vars("${NONEXISTENT_VAR:-default}")
        assert result == "default"

    def test_expand_missing_var_empty(self):
        """既定値のない変数がなければ空になる。"""
        result = _expand_env_vars("${NONEXISTENT_VAR}")
        assert result == ""

    def test_expand_in_dict(self):
        """入れ子の dict の中の変数を展開する。"""
        os.environ["TEST_KEY"] = "key_value"
        try:
            data = {"nested": {"key": "${TEST_KEY}"}}
            result = _expand_env_vars(data)
            assert result["nested"]["key"] == "key_value"
        finally:
            del os.environ["TEST_KEY"]

    def test_expand_in_list(self):
        """リストの中の変数を展開する。"""
        os.environ["TEST_ITEM"] = "item_value"
        try:
            data = ["${TEST_ITEM}", "plain"]
            result = _expand_env_vars(data)
            assert result == ["item_value", "plain"]
        finally:
            del os.environ["TEST_ITEM"]

    def test_no_expansion_for_non_string(self):
        """文字列でない値はそのまま通る。"""
        assert _expand_env_vars(123) == 123
        assert _expand_env_vars(True) is True
        assert _expand_env_vars(None) is None


class TestDeepMerge:
    """深いマージのテスト。"""

    def test_simple_merge(self):
        """単純な dict のマージ。"""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        """入れ子の dict のマージ。"""
        base = {"outer": {"a": 1, "b": 2}}
        override = {"outer": {"b": 3, "c": 4}}
        result = _deep_merge(base, override)
        assert result == {"outer": {"a": 1, "b": 3, "c": 4}}

    def test_override_replaces_non_dict(self):
        """dict でない値は上書きで置き換わる。"""
        base = {"key": "original"}
        override = {"key": "new"}
        result = _deep_merge(base, override)
        assert result["key"] == "new"

    def test_original_unchanged(self):
        """元の dict は変わらない。"""
        base = {"a": 1}
        override = {"b": 2}
        _deep_merge(base, override)
        assert "b" not in base


class TestLoadYamlFile:
    """YAML ファイルの読み込みのテスト。"""

    def test_load_existing_file(self):
        """ある YAML ファイルを読む。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("key: value\nnumber: 42")
            f.flush()
            path = Path(f.name)

        try:
            result = load_yaml_file(path)
            assert result == {"key": "value", "number": 42}
        finally:
            path.unlink()

    def test_load_nonexistent_file(self):
        """ないファイルを読むと空の dict を返す。"""
        result = load_yaml_file(Path("/nonexistent/file.yaml"))
        assert result == {}

    def test_load_empty_file(self):
        """空のファイルを読むと空の dict を返す。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            path = Path(f.name)

        try:
            result = load_yaml_file(path)
            assert result == {}
        finally:
            path.unlink()

    def test_load_invalid_yaml_raises_error(self):
        """不正な YAML を読むと ConfigurationError。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: [")
            f.flush()
            path = Path(f.name)

        try:
            with pytest.raises(ConfigurationError, match="Invalid YAML"):
                load_yaml_file(path)
        finally:
            path.unlink()


class TestLoadSettings:
    """設定の読み込みのテスト。"""

    def test_load_default_settings(self):
        """設定ファイルがなければ既定値を返す。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = load_settings(config_dir=tmpdir)
            assert settings.app_name == "AILoveShen"
            assert settings.debug is False

    def test_load_from_default_yaml(self):
        """default.yaml から読む。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            default_yaml = config_dir / "default.yaml"
            default_yaml.write_text("app_name: TestApp\ndebug: true")

            settings = load_settings(config_dir=config_dir)
            assert settings.app_name == "TestApp"
            assert settings.debug is True

    def test_env_specific_overrides_default(self):
        """環境ごとの設定が default を上書きする。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            default_yaml = config_dir / "default.yaml"
            default_yaml.write_text("app_name: DefaultApp\ndebug: false")

            dev_yaml = config_dir / "development.yaml"
            dev_yaml.write_text("debug: true")

            settings = load_settings(config_dir=config_dir, env="development")
            assert settings.app_name == "DefaultApp"  # default から
            assert settings.debug is True  # 上書きしたもの

    def test_env_var_expansion(self):
        """環境変数が展開される。"""
        os.environ["TEST_CHANNEL"] = "testchannel"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                config_dir = Path(tmpdir)
                default_yaml = config_dir / "default.yaml"
                default_yaml.write_text("twitch:\n  channel: ${TEST_CHANNEL}")

                settings = load_settings(config_dir=config_dir)
                assert settings.twitch.channel == "testchannel"
        finally:
            del os.environ["TEST_CHANNEL"]


class TestLoadGeminiAndCharacterSettings:
    """gemini と character の節の読み込みのテスト。"""

    def test_load_gemini_section(self):
        """gemini の節を、入れ子の retry・rate_limit も含めて読む。"""
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
            assert settings.gemini.filter_thinking_level == "low"  # 既定値
            assert settings.gemini.max_output_tokens == 1024
            assert settings.gemini.retry.max_attempts == 5
            assert settings.gemini.retry.max_delay_seconds == 20.0
            assert settings.gemini.retry.base_delay_seconds == 1.0  # 既定値
            assert settings.gemini.rate_limit.min_interval_seconds == 0.5

    def test_load_character_section(self):
        """character の節を読み、ないキーは既定値にする。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            (config_dir / "default.yaml").write_text(
                "character:\n  name: Shen\n  sentence_endings:\n    - のだ\n"
            )

            settings = load_settings(config_dir=config_dir)
            assert settings.character.name == "Shen"
            assert settings.character.sentence_endings == ["のだ"]
            assert settings.character.first_person == "私"  # 既定値

    def test_project_default_yaml_loads(self):
        """プロジェクトの config/default.yaml は Phase 3 の節を読める。"""
        config_dir = Path(__file__).resolve().parents[3] / "config"
        settings = load_settings(config_dir=config_dir, env="nonexistent")
        assert settings.gemini.main_model == "gemini-3.8-flash"
        assert settings.gemini.main_thinking_level in {"low", "medium", "high"}
        assert settings.gemini.filter_thinking_level in {"low", "medium", "high"}
        assert settings.character.name


class TestSettingsDataclasses:
    """設定の dataclass のテスト。"""

    def test_settings_defaults(self):
        """Settings の既定値が正しい。"""
        settings = Settings()
        assert settings.app_name == "AILoveShen"
        assert isinstance(settings.twitch, TwitchSettings)
        assert isinstance(settings.gemini, GeminiSettings)
        assert isinstance(settings.tts, TTSSettings)
        assert isinstance(settings.logging, LoggingSettings)

    def test_twitch_settings_defaults(self):
        """TwitchSettings の既定値。"""
        twitch = TwitchSettings()
        assert twitch.channel == ""
        assert twitch.client_id == ""

    def test_gemini_settings_defaults(self):
        """GeminiSettings の既定値。"""
        gemini = GeminiSettings()
        assert gemini.main_model == "gemini-3.8-flash"
        assert gemini.filter_model == "gemini-3.8-flash"
        assert gemini.main_thinking_level == "low"
        assert gemini.filter_thinking_level == "low"
        assert gemini.max_output_tokens == 16384
        assert gemini.retry.max_attempts == 3
        assert gemini.retry.base_delay_seconds == 1.0
        assert gemini.retry.max_delay_seconds == 10.0
        assert gemini.rate_limit.min_interval_seconds == 1.0

    def test_character_settings_defaults(self):
        """CharacterSettings の既定値。"""
        character = CharacterSettings()
        assert character.name == "AILoveShen"
        assert character.first_person == "私"
        assert character.sentence_endings == ["だよ", "だね", "かな", "！"]

    def test_tts_settings_defaults(self):
        """TTSSettings の既定値。"""
        tts = TTSSettings()
        assert isinstance(tts.server, TTSServerSettings)
        assert tts.server.host == "localhost"
        assert tts.server.port == 5000

    def test_logging_settings_defaults(self):
        """LoggingSettings の既定値。"""
        logging = LoggingSettings()
        assert logging.level == "INFO"
        assert logging.rotation == "10 MB"
        assert logging.retention == "7 days"


class TestLoadJevAndMinecraftSettings:
    """jev と minecraft の節の読み込みのテスト。"""

    def test_load_sections(self):
        """jev と minecraft（ブリッジとエージェント）を読む。"""
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
                "    max_stalled_steps: 4\n"
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
            assert settings.minecraft.max_stalled_steps == 4

    def test_load_mission(self):
        """大目標、最初の中目標、上限を読む。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            (config_dir / "default.yaml").write_text(
                "minecraft:\n"
                "  mission:\n"
                "    text: 村を作る\n"
                "    mid_goals:\n"
                "      - title: 家\n"
                "        conditions: [{predicate: built}]\n"
                "    max_viewer_mid_goals: 1\n"
            )

            mission = load_settings(config_dir=config_dir).minecraft.mission

            assert mission.text == "村を作る"
            assert mission.mid_goals == [{"title": "家", "conditions": [{"predicate": "built"}]}]
            assert mission.max_viewer_mid_goals == 1
            assert mission.max_mid_goals == 6
            assert mission.viewer_budget_steps == 80
            assert mission.store_path == "data/mission.json"

    def test_project_default_yaml_reads_typesafe_key(self):
        """同梱の default.yaml は、Jev のキーを TYPESAFE_API_KEY から取る。"""
        os.environ["TYPESAFE_API_KEY"] = "from-env"
        try:
            settings = load_settings(config_dir=Path(__file__).parents[3] / "config")
        finally:
            del os.environ["TYPESAFE_API_KEY"]

        assert settings.jev.api_key == "from-env"
        assert settings.minecraft.bridge_port == 3000
        assert settings.minecraft.mission.mid_goals == MissionSettings().mid_goals


class TestToolControlSettings:
    """用途ごとの考える深さ、行動の決め方、見張り（設計書 21）の読み込み。"""

    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "default.yaml").write_text("gemini: {}\nminecraft: {}\n")
            settings = load_settings(config_dir=Path(tmpdir))
        assert settings.minecraft.control == "tools"
        assert settings.minecraft.watch.act_on_progress is False
        assert settings.minecraft.watch.act_on_questions is True
        assert settings.gemini.thinking_levels["town"] == "high"
        assert settings.gemini.thinking_levels["reply"] == "low"

    def test_overrides_keep_the_unlisted_purposes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "default.yaml").write_text(
                "gemini:\n"
                "  thinking_levels:\n"
                "    town: medium\n"
                "minecraft:\n"
                "  agent:\n"
                "    control: tools\n"
                "  watch:\n"
                "    threshold: 0.8\n"
                "    record_dir: ''\n"
            )
            settings = load_settings(config_dir=Path(tmpdir))
        assert settings.gemini.thinking_levels["town"] == "medium"
        assert settings.gemini.thinking_levels["site"] == "high"
        assert settings.minecraft.control == "tools"
        assert settings.minecraft.watch.threshold == 0.8
        assert settings.minecraft.watch.consecutive == 2
        assert settings.minecraft.watch.record_dir == ""


class TestEnvFile:
    """.env ファイル（リポジトリ直下）から環境変数を読む。"""

    def test_env_file_fills_the_yaml_but_the_shell_wins(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(
            'gemini:\n  api_key: "${GEMINI_API_KEY:-}"\n'
            'jev:\n  api_key: "${TYPESAFE_API_KEY:-}"\n'
            'minecraft:\n  bridge:\n    port: "${BRIDGE_PORT:-3000}"\n'
        )
        (tmp_path / ".env").write_text(
            "# コメント\n"
            "GEMINI_API_KEY=from-file\n"
            "export TYPESAFE_API_KEY='quoted # not a comment'\n"
            "BRIDGE_PORT=3100  # 行末のコメント\n"
            "not a line\n"
        )
        # .env で入れた変数を後のテストに残さない
        monkeypatch.setattr(os, "environ", dict(os.environ))
        for name in ("GEMINI_API_KEY", "TYPESAFE_API_KEY", "BRIDGE_PORT"):
            os.environ.pop(name, None)
        os.environ["GEMINI_API_KEY"] = "from-shell"

        settings = load_settings(config_dir=config_dir)

        assert settings.gemini.api_key == "from-shell"
        assert settings.jev.api_key == "quoted # not a comment"
        assert settings.minecraft.bridge_port == 3100

    def test_no_env_file_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "environ", dict(os.environ))
        os.environ.pop("BRIDGE_PORT", None)
        (tmp_path / "default.yaml").write_text(
            'minecraft:\n  bridge:\n    port: "${BRIDGE_PORT:-3000}"\n'
        )
        settings = load_settings(config_dir=tmp_path, env_file=tmp_path / "missing.env")
        assert settings.minecraft.bridge_port == 3000


def test_config_dict_merges_the_env_file_and_expands_variables(tmp_path, monkeypatch):
    # TTS のファクトリーは辞書で受け取る（--speak）
    from ailoveshen.infrastructure.config import load_config_dict

    monkeypatch.setattr(os, "environ", {"TTS_MODEL_NAME": "yui"})
    (tmp_path / "default.yaml").write_text(
        'tts:\n  voice:\n    model_name: "${TTS_MODEL_NAME:-shen}"\n  server:\n    port: 5001\n'
    )
    (tmp_path / "production.yaml").write_text("tts:\n  server:\n    port: 5002\n")
    tts = load_config_dict(tmp_path, env="production")["tts"]
    assert tts == {"voice": {"model_name": "yui"}, "server": {"port": 5002}}
    monkeypatch.setattr(os, "environ", {})
    assert load_config_dict(tmp_path)["tts"]["voice"]["model_name"] == "shen"
