"""AILoveShen の基本の例外。"""

from __future__ import annotations


class AILoveShenError(Exception):
    """AILoveShen のすべてのエラーの基底の例外。"""

    pass


class ConfigurationError(AILoveShenError):
    """設定が不正、または足りないときに送出する。"""

    pass


class SynthesisError(AILoveShenError):
    """音声合成に失敗したときに送出する。"""

    pass


class AudioPlaybackError(AILoveShenError):
    """音声の再生に失敗したときに送出する。"""

    pass


class ConnectionError(AILoveShenError):
    """外部サービスへの接続に失敗したときに送出する。"""

    pass


class TextGenerationError(AILoveShenError):
    """LLM のテキスト生成に失敗したときに送出する。"""

    pass


class GameBridgeError(AILoveShenError):
    """ゲームのブリッジに届かないか、ブリッジが要求を拒否したときに送出する。"""

    pass


class SkillRejectedError(GameBridgeError):
    """ブリッジが技を受け取らなかった（形、構文、expects）。理由は Gemini に返して書き直させる。"""

    pass


class GoalRejectedError(GameBridgeError):
    """ブリッジが目標を拒否したときに送出する（知らないアイテム、まだ家がない、など）。"""

    pass


class BuildNotDesignedError(GoalRejectedError):
    """名前付きの建物の設計ができなかった（Gemini が答えない・使える設計が出ない）。`name` はその建物。"""

    def __init__(self, message: str, name: str = "") -> None:
        super().__init__(message)
        self.name = name


class FacilityNotDesignedError(BuildNotDesignedError):
    """
    施設（facility(name)）の設計ができなかった（答えない・使える設計が出ない・区画が読み込まれて
    いない）。建物と同じく、しばらくその名前を大目標に足さない。`name` はその施設。
    """


class FacilityAreaNotReadyError(GoalRejectedError):
    """施設の区画が今は使えない（読み込まれていない）。設計をやり直しても直らない。"""


class CheckNotWrittenError(GoalRejectedError):
    """custom(name) の判定が書けなかった（答えない・使える判定が出ない）。`name` はその判定。"""

    def __init__(self, message: str, name: str = "") -> None:
        super().__init__(message)
        self.name = name


class ActionSelectionError(AILoveShenError):
    """行動選択器（Jev）が決められなかったときに送出する。"""

    pass
