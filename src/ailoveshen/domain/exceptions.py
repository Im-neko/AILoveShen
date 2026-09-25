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


class GoalRejectedError(GameBridgeError):
    """ブリッジが目標を拒否したときに送出する（知らないアイテム、まだ家がない、など）。"""

    pass


class ActionSelectionError(AILoveShenError):
    """行動選択器（Jev）が決められなかったときに送出する。"""

    pass
