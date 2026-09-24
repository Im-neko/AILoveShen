"""Core exceptions for AILoveShen."""

from __future__ import annotations


class AILoveShenError(Exception):
    """Base exception for all AILoveShen errors."""

    pass


class ConfigurationError(AILoveShenError):
    """Raised when configuration is invalid or missing."""

    pass


class SynthesisError(AILoveShenError):
    """Raised when speech synthesis fails."""

    pass


class AudioPlaybackError(AILoveShenError):
    """Raised when audio playback fails."""

    pass


class ConnectionError(AILoveShenError):
    """Raised when connection to external service fails."""

    pass


class TextGenerationError(AILoveShenError):
    """Raised when LLM text generation fails."""

    pass


class GameBridgeError(AILoveShenError):
    """Raised when the game bridge cannot be reached or rejects a request."""

    pass


class GoalRejectedError(GameBridgeError):
    """Raised when the game bridge rejects a goal (unknown item, no home yet, ...)."""

    pass


class ActionSelectionError(AILoveShenError):
    """Raised when the action selector (Jev) fails to decide."""

    pass
