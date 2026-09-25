"""Game prompt builder output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ailoveshen.domain.value_objects import (
    Activity,
    CharacterProfile,
    ConversationMessage,
    GameObservation,
    Goal,
    GoalPredicate,
    HouseBlueprint,
)


class IGamePromptBuilder(ABC):
    """
    Output port for building the prompts that direct the game agent.

    This interface is defined in the Application layer.
    Infrastructure adapters implement this interface.
    """

    @abstractmethod
    def build_house_design_prompt(
        self, character: CharacterProfile, previous_error: str = ""
    ) -> str:
        """
        Build the prompt asking the LLM to design a small house.

        Args:
            character: The streamer, whose personality the design should reflect
            previous_error: Why the previous design was rejected, if any
        """
        ...

    @abstractmethod
    def build_goal_prompt(
        self,
        blueprint: HouseBlueprint | None,
        activity: Activity,
        goal_ended_because: str,
        recent_messages: Sequence[ConversationMessage],
        predicates: Sequence[GoalPredicate],
        previous_error: str = "",
    ) -> str:
        """
        Build the prompt asking the LLM to set the next goal.

        Args:
            blueprint: The house being built (None: the home was built in an earlier run)
            activity: What the streamer is doing (the mission and mid goals, the ending
                goal with its status, recent goals), the same view the commentary and
                replies get
            goal_ended_because: Why a new goal is due
            recent_messages: What was said on stream (the streamer's words and viewers' chat)
            predicates: The predicates that make sense now
            previous_error: Why the previous goal or mid-goal edit could not be used
        """
        ...

    @abstractmethod
    def build_action_context(
        self, goal: Goal, observation: GameObservation
    ) -> tuple[dict[str, Any], str]:
        """
        Build what the action selector sees: the state and the instructions.

        The state states the body's needs (numbers and severity) but never
        which action to take; the instructions give no priority order (both
        measured: spikes/primitive_choice_eval.py).
        """
        ...
