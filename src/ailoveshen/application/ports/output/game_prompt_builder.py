"""Game prompt builder output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ailoveshen.domain.value_objects import (
    CharacterProfile,
    GameObservation,
    Goal,
    GoalType,
    HouseBlueprint,
    MaterialNeeds,
)


class IGamePromptBuilder(ABC):
    """
    Output port for building the LLM prompts that direct the game agent.

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
        blueprint: HouseBlueprint,
        needs: MaterialNeeds,
        observation: GameObservation,
        current_goal: Goal | None,
        goal_ended_because: str,
        recent_goals: Sequence[Goal],
        goals: Sequence[GoalType],
    ) -> str:
        """Build the prompt asking the LLM to choose the next goal among `goals`."""
        ...

    @abstractmethod
    def build_action_instructions(self, goal: Goal, blueprint: HouseBlueprint) -> str:
        """Build the instructions given to the action selector for the current goal."""
        ...
