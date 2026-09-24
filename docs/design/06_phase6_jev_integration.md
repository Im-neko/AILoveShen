# Phase 6: Jev + Minecraft Bridge Integration 詳細設計書 (Issue #4 改訂)

> **改訂の経緯**: 当初はNitroGen/MineDojoによるMinecraft統合を計画していたが、NitroGenは未実装（設計のみ）の段階で、TypeSafe AI社の実運用向けリアルタイム意思決定モデル **[Jev](https://typesafe.ai/)** が登場した。Jevは「型付き・確率付きの高速判断」に特化した**System Oneモデル**であり、LLM（System 2：じっくり推論・言語生成）と組み合わせることで、Minecraft内のリアルタイム操作判断に適している。本改訂では、NitroGenへの依存をやめ、**Mineflayer（Node.jsのMinecraft制御ライブラリ）による実機操作ブリッジ + Jevによる戦術・反射判断**という構成に置き換える。

## 1. 概要

Minecraft操作の「判断」をJevに担わせつつ、配信全体の方向性（何をすべきか、視聴者にどう反応するか）はこれまで通りGemini 3.8 Flash（LLM）が担当する、という役割分担でMinecraft統合を実装する。

### 1.1 要件（Issue #4より、Jev対応版）

- Game State Manager（ゲーム状態の取得・管理） — Minecraft Bridge経由
- Action Executor（判断をゲーム操作に変換） — Jevの判断 → Minecraft Bridgeで実行
- LLM ↔ Jev ↔ Minecraft Bridge の三層連携
  - LLM → Jev: 「今何をすべきか」という**方向性（Goal）**をリクエストする
  - Jev → Minecraft Bridge: Goalを踏まえた**具体的な次の一手**を高速・型安全に決定し、実行させる
  - Minecraft Bridge → Jev / LLM: ゲーム状態・イベントをフィードバックする

### 1.2 なぜJevか（採用理由）

| 観点 | LLM (Gemini) だけで判断する場合 | Jevを間に挟む場合 |
|------|--------------------------------|-------------------|
| レイテンシ | 数百ms〜数秒。Minecraftのリアルタイム操作（索敵・回避等）には遅すぎる | 型付き並列呼び出しでLLMの二桁分の一程度に高速化 |
| 出力の安全性 | 自由テキストの意図を正規表現/キーワードマッチで解析する必要があり脆弱（旧設計の`ACTION_KEYWORDS`参照） | `Choice`/`Score`/`Noul`という型付き出力のみを返すため、パース不要・ハルシネーション不可 |
| 得意なこと | キャラクター性のある実況・チャット応答・大局的な目標設定 | 「今、逃げるべきか戦うべきか」等の閉じた選択肢からの高速・高頻度な判断 |

この特性差から、**LLMは「何を目指すか（Goal）」を決め、Jevは「今この瞬間どう動くか」を決める**という分担にする。

## 2. Jevの基礎知識（実装に必要な範囲）

- Jevは TypeSafe AI の **System One Model**。「非構造なStateを入力し、型付きの確率的Decisionを出力する」ことに特化しており、生成AI（LLM）のような自由文出力は行わない。
- 3つのプリミティブ（質問タイプ）:
  - **Noul**: Yes/Noの二値判定（「はい」である確率を返す）
  - **Choice**: 定義済みの選択肢から1つを選ぶ（各選択肢の確率・信頼度つき）
  - **Score**: 順序付き基準に対する評価（段階的スコア）
- 1回の`system_one()`呼び出しで複数の質問（`questions`）を並列に投げられるため、「脅威レベル・行動・逃走方向」のようなセットを1リクエストで取得できる（後述のReactive Loopで利用）。
- Python SDK: `typesafe-sdk`（`pip install typesafe-sdk` / `uv add typesafe-sdk`）
  - 環境変数 `TYPESAFE_API_KEY` にAPIキーを設定
  - `AsyncTypeSafeClient`（非同期）/ `TypeSafeClient`（同期）
  - 呼び出し例:

    ```python
    from typesafe_sdk import AsyncTypeSafeClient, Noul, Choice, Score

    async with AsyncTypeSafeClient() as client:
        response = await client.system_one(
            state={"health": 12, "hunger": 4, "nearby_hostiles": ["zombie"]},
            questions={
                "threat_level": Score(
                    instructions="現在の危険度は？",
                    criteria=["none", "low", "medium", "high", "critical"],
                ),
                "action": Choice(
                    instructions="次に取るべき行動は？",
                    criteria={"fight": None, "flee": None, "eat": None, "explore": None},
                ),
                "should_eat": Noul(instructions="今すぐ食事すべきか？"),
            },
        )

    threat = response.scores["threat_level"].score       # 例: "high"
    action = response.choices["action"].choice            # 例: "flee"
    should_eat = response.nouls["should_eat"].noul         # 例: False
    ```

- 参考実装: [jev-craft](https://github.com/akash-kamat/jev-craft)（Mineflayer + Jevによる生存Botの先行事例。600ms周期の反射ループと10秒周期の戦術ループという二層構成を採用しており、本設計もこれに倣う）。

## 3. 全体アーキテクチャ（2ループ構成）

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                        AILoveShen × Jev アーキテクチャ                              │
├───────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│  【方向性（Goal）を決める】                     ← 低頻度・LLM主導                    │
│  Gemini 3.8 Flash ──DecideGoalUseCase──▶ Goal (13種の定義済みゴールから選択)        │
│      ▲                                    │                                        │
│      │ ゲーム状況・視聴者コメントを加味          ▼                                   │
│      │                          ┌─────────────────────────┐                        │
│      │                          │   Tactical Loop (10s)    │  ← LLMのGoalの範囲内で │
│      │                          │   Jev: 次の一手/approach/ │     Jevが手段を選ぶ   │
│      │                          │        urgencyを型で決定  │                        │
│      │                          └────────────┬─────────────┘                        │
│      │                                       ▼                                     │
│      │                          ┌─────────────────────────┐                        │
│      │                          │   Reactive Loop (600ms)  │  ← LLMを介さず         │
│      │                          │   Jev: 脅威度/即時行動/   │     Jevのみで反射的に  │
│      │                          │        逃走方向を型で決定 │     生存を優先         │
│      │                          └────────────┬─────────────┘                        │
│      │                                       ▼                                     │
│      │                          ┌─────────────────────────┐                        │
│      └── GameEvent（実況ネタ）───┤   Minecraft Bridge        │                        │
│                                  │   (Node.js + Mineflayer)  │──▶ Minecraft Java Ed. │
│                                  └─────────────────────────┘                        │
└───────────────────────────────────────────────────────────────────────────────────┘
```

- **Reactive Loop（反射ループ, 〜600ms周期）**: LLMを介さずJevのみで完結。体力・空腹・近接モブ等の状態から `threat_level` / `immediate_action` / `should_eat` / `flee_direction` を毎tick判定し、危険時は即座にMinecraft Bridgeへ実行させる。生存に関わる判断はLLMの応答速度を待てないため、ここは完全にJevに委譲する。
- **Tactical Loop（戦術ループ, 〜5〜10秒周期）**: LLMが設定した現在の `Goal`（例：「鉱石を採掘する」）の範囲内で、Jevが次のステップ・approach（慎重/効率重視等）・urgencyを型付きで選ぶ。LLMは「何を目指すか」だけ決め、「今どう動くか」はJevに任せる。
- **Goal選定（LLM主導, イベント駆動 or 30〜60秒周期）**: Gemini 3.8 Flashが、現在のゲーム状態・直近のイベント・視聴者コメント（「あっちの洞窟行って！」等）を踏まえて、13種の定義済みGoalから次のGoalを選ぶ。Reactive Loopが`critical`な脅威を検知した場合は、Tactical LoopがGoalを一時的に`FLEE`へ強制オーバーライドする（LLMの応答を待たない）。
- Reactive/Tactical Loopの結果は `GameEvent` としてイベントバスに流れ、Phase 3のLLM実況（`GenerateCommentaryUseCase`）が「あぶない、逃げた！」のような実況コメントを生成する材料になる。

## 4. クリーンアーキテクチャに基づくコンポーネント構成

```
src/ailoveshen/
├── domain/
│   ├── entities/
│   │   └── game_state.py               # GameState aggregate (Phase 1から)
│   ├── value_objects/
│   │   ├── game_action.py              # GameAction（旧設計を拡張）
│   │   ├── game_event.py               # GameEvent（既存流用）
│   │   ├── position.py                 # Position, Rotation（既存流用）
│   │   ├── goal.py                     # GoalType, Goal ★NEW
│   │   └── jev_decision.py             # ReactiveDecision, TacticalDecision ★NEW
│   └── services/
│       ├── event_detection_service.py  # EventDetectionService（既存流用）
│       └── goal_override_policy.py     # GoalOverridePolicy ★NEW
│
├── application/
│   ├── ports/
│   │   ├── input/
│   │   │   ├── get_game_state.py       # IGetGameState（既存流用）
│   │   │   ├── decide_goal.py          # IDecideGoal ★NEW
│   │   │   ├── run_reactive_loop.py    # IRunReactiveLoop ★NEW
│   │   │   └── run_tactical_loop.py    # IRunTacticalLoop ★NEW
│   │   └── output/
│   │       ├── minecraft_bridge.py     # IMinecraftBridge（旧IGameEnvironmentを改称）
│   │       └── jev_decision_engine.py  # IJevDecisionEngine ★NEW
│   ├── use_cases/
│   │   ├── get_game_state.py           # GetGameStateUseCase（Bridge向けに調整）
│   │   ├── decide_goal.py              # DecideGoalUseCase ★NEW（LLM呼び出し）
│   │   ├── reactive_decision.py        # ReactiveDecisionUseCase ★NEW（Jev呼び出し）
│   │   └── tactical_decision.py        # TacticalDecisionUseCase ★NEW（Jev呼び出し, 旧ExecuteActionUseCaseを置換）
│   └── dto/
│       └── game_dto.py                 # Request/Response DTOs
│
├── infrastructure/
│   └── adapters/
│       ├── minecraft_bridge/
│       │   └── mineflayer_bridge_adapter.py  # MineflayerBridgeAdapter（旧NitroGenAdapterを置換）
│       └── jev/
│           └── jev_client_adapter.py         # JevClientAdapter（typesafe-sdkラッパー）
│
└── presentation/
    └── services/
        └── game_service.py             # GameService（Reactive/Tactical両ループを統括）

minecraft-bridge/                        # ★NEW: Python本体とは別プロセスのNode.jsサイドカー
├── package.json                        # mineflayer, mineflayer-pathfinder 等
├── src/
│   ├── index.js                        # WebSocket/HTTPサーバのエントリポイント
│   ├── observe.js                      # ゲーム状態のスナップショット生成
│   └── actions.js                      # GameAction → Mineflayer API呼び出しの変換
└── README.md
```

## 5. Domain Layer

### 5.1 Value Objects

#### GameAction（拡張版, domain/value_objects/game_action.py）

```python
"""Game action value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ActionType(str, Enum):
    """Types of game actions."""
    MOVE = "move"
    ATTACK = "attack"
    MINE = "mine"
    PLACE = "place"
    USE_ITEM = "use_item"
    CRAFT = "craft"
    INTERACT = "interact"
    EAT = "eat"
    FLEE = "flee"
    SLEEP = "sleep"
    FOLLOW = "follow"
    IDLE = "idle"


@dataclass(frozen=True)
class GameAction:
    """
    Immutable value object representing a game action.

    Jevの`Choice`結果（文字列）をそのままparametersに載せられるよう、
    自由記述の`intention`ではなく型付きの`action_type`から直接構築する。
    """
    action_type: ActionType
    parameters: dict = field(default_factory=dict)
    description: str = ""
    confidence: float = 1.0
    """Jevが返す確信度（0.0〜1.0）。低確信度のアクションはログで警告する。"""

    def is_movement(self) -> bool:
        return self.action_type in (ActionType.MOVE, ActionType.FLEE, ActionType.FOLLOW)

    def is_combat(self) -> bool:
        return self.action_type == ActionType.ATTACK

    def is_survival_critical(self) -> bool:
        """Reactive Loop由来の生存優先アクションか。"""
        return self.action_type in (ActionType.FLEE, ActionType.EAT)
```

#### Goal（domain/value_objects/goal.py）★NEW

```python
"""Goal value object - LLM sets these, Jev executes within them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class GoalType(str, Enum):
    """
    13種の定義済みゴール。

    LLM(Gemini)はこの中からのみ選ぶ（Jevと同様、自由記述ではなく閉じた
    選択肢にすることで、Tactical LoopでのJevのChoice基準として使い回せる）。
    """
    EXPLORE = "explore"                    # 探索
    GATHER_WOOD = "gather_wood"            # 木材収集
    MINE_ORE = "mine_ore"                  # 鉱石採掘
    BUILD_SHELTER = "build_shelter"        # 拠点構築
    FARM = "farm"                          # 農業
    FIGHT = "fight"                        # 戦闘
    FLEE = "flee"                          # 逃走（Reactive Loopが強制上書きすることもある）
    EAT = "eat"                            # 食事
    SLEEP = "sleep"                        # 就寝（夜越え）
    CRAFT = "craft"                        # クラフト
    RETURN_TO_BASE = "return_to_base"      # 拠点帰還
    FOLLOW_CHAT_REQUEST = "follow_chat_request"  # 視聴者リクエスト対応
    IDLE_PERFORM = "idle_perform"          # 待機中の小ネタ（カメラ目線トーク等）

    @property
    def steps(self) -> tuple[str, ...]:
        """
        このGoalに対してTactical LoopのJevが選べる手段（Choiceの選択肢）。

        Mineflayer Bridge側で実装される低レベル操作にマッピングされる。
        """
        return _GOAL_STEPS[self]


_GOAL_STEPS: dict[GoalType, tuple[str, ...]] = {
    GoalType.EXPLORE: ("move_forward", "climb", "swim", "mark_location"),
    GoalType.GATHER_WOOD: ("locate_tree", "path_to_tree", "chop", "return_if_full"),
    GoalType.MINE_ORE: ("locate_ore", "path_to_ore", "mine_block", "return_if_full"),
    GoalType.BUILD_SHELTER: ("clear_area", "place_walls", "place_roof", "place_door"),
    GoalType.FARM: ("till_soil", "plant_seed", "harvest", "replant"),
    GoalType.FIGHT: ("approach_target", "attack", "retreat_if_low_hp", "finish_target"),
    GoalType.FLEE: ("run_from_threat", "seek_shelter", "close_door", "place_block_barrier"),
    GoalType.EAT: ("select_food_item", "consume"),
    GoalType.SLEEP: ("path_to_bed", "use_bed", "wait_for_morning"),
    GoalType.CRAFT: ("open_crafting", "select_recipe", "craft_item"),
    GoalType.RETURN_TO_BASE: ("path_to_base", "store_items", "arrive"),
    GoalType.FOLLOW_CHAT_REQUEST: ("interpret_request", "path_to_target", "perform_request"),
    GoalType.IDLE_PERFORM: ("look_at_camera", "emote", "narrate_surroundings"),
}


@dataclass(frozen=True)
class Goal:
    """LLMが設定する現在のゴール。"""
    goal_type: GoalType
    reason: str = ""
    """LLMがこのゴールを選んだ理由（実況・デバッグ用）。"""
    set_at: datetime = field(default_factory=datetime.now)
    set_by: str = "llm"
    """'llm' | 'reactive_override'（Reactive Loopによる緊急上書き）"""
```

#### JevDecision（domain/value_objects/jev_decision.py）★NEW

```python
"""Value objects representing Jev's typed decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ThreatLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ReactiveDecision:
    """Reactive Loop(600ms)でJevが返す型付き判断。"""
    threat_level: ThreatLevel
    immediate_action: str        # Choice結果: "continue" | "fight" | "flee" | "eat"
    should_eat: bool
    flee_direction: str | None   # threat_level >= HIGH のときのみ設定
    confidence: float

    @property
    def requires_goal_override(self) -> bool:
        """CRITICALな脅威はTactical LoopのGoalを強制的にFLEEへ切り替える。"""
        return self.threat_level == ThreatLevel.CRITICAL


@dataclass(frozen=True)
class TacticalDecision:
    """Tactical Loop(10s)でJevが返す型付き判断。"""
    goal_type: str
    next_step: str               # Goal.stepsのうちの1つ
    approach: str                # "aggressive" | "cautious" | "efficient"
    urgency: str                 # "low" | "medium" | "high"
    confidence: float
```

### 5.2 Domain Services

#### GoalOverridePolicy（domain/services/goal_override_policy.py）★NEW

```python
"""Domain service deciding whether Reactive Loop should override the LLM's Goal."""

from __future__ import annotations

from ailoveshen.domain.value_objects.goal import Goal, GoalType
from ailoveshen.domain.value_objects.jev_decision import ReactiveDecision


class GoalOverridePolicy:
    """
    生存に関わる反射判断が出た場合、LLMの応答を待たずに
    現在のGoalを一時的に上書きするためのビジネスルール。
    """

    def should_override(self, decision: ReactiveDecision, current_goal: Goal) -> bool:
        if current_goal.goal_type == GoalType.FLEE:
            return False  # 既に逃走中なら上書き不要
        return decision.requires_goal_override

    def build_override_goal(self, decision: ReactiveDecision) -> Goal:
        return Goal(
            goal_type=GoalType.FLEE,
            reason=f"Reactive Loop: threat_level={decision.threat_level.value}",
            set_by="reactive_override",
        )
```

`EventDetectionService`（既存, damage/death/mob検知）はそのまま流用する。Reactive/Tactical Loopの結果は`GetGameStateUseCase`を経由せず直接イベント発行するため、この既存サービスとは独立して動作する。

## 6. Application Layer

### 6.1 Output Ports

#### IMinecraftBridge（application/ports/output/minecraft_bridge.py）

旧`IGameEnvironment`と同一のインターフェース形状を維持する（実装をNitroGenからMineflayer Bridgeへ差し替えるだけで、上位のUse Caseは無改修で済むようにするため）。

```python
"""Minecraft bridge output port (formerly IGameEnvironment)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ailoveshen.domain.value_objects.game_action import GameAction


class IMinecraftBridge(ABC):
    """
    Output port for Minecraft control.

    Abstracts the Node.js + Mineflayer sidecar process (formerly NitroGen).
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def get_observation(self) -> dict[str, Any]: ...

    @abstractmethod
    async def execute_action(self, action: GameAction) -> bool: ...

    @abstractmethod
    def is_connected(self) -> bool: ...
```

#### IJevDecisionEngine（application/ports/output/jev_decision_engine.py）★NEW

```python
"""Jev decision engine output port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ailoveshen.domain.value_objects.goal import Goal
from ailoveshen.domain.value_objects.jev_decision import ReactiveDecision, TacticalDecision


class IJevDecisionEngine(ABC):
    """
    Output port abstracting TypeSafe AI's Jev (System One Model).

    2種類の呼び出しのみを提供する。実装(JevClientAdapter)がtypesafe-sdkの
    system_one()呼び出しとNoul/Choice/Scoreへの変換を担う。
    """

    @abstractmethod
    async def reactive_decide(self, state: dict[str, Any]) -> ReactiveDecision:
        """600ms周期の反射判断。LLMを介さない。"""
        ...

    @abstractmethod
    async def tactical_decide(
        self,
        state: dict[str, Any],
        goal: Goal,
    ) -> TacticalDecision:
        """10s周期の戦術判断。現在のGoalの範囲内で次の一手を選ぶ。"""
        ...
```

### 6.2 Input Ports

```python
# application/ports/input/decide_goal.py
"""Decide goal input port (LLM-driven)."""

from __future__ import annotations
from abc import ABC, abstractmethod
from ailoveshen.application.dto.game_dto import DecideGoalRequest, DecideGoalResponse


class IDecideGoal(ABC):
    """LLMが次のGoalを決めるユースケースの入力ポート。"""

    @abstractmethod
    async def execute(self, request: DecideGoalRequest) -> DecideGoalResponse: ...
```

```python
# application/ports/input/run_reactive_loop.py
"""Reactive loop input port."""

from __future__ import annotations
from abc import ABC, abstractmethod


class IRunReactiveLoop(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
```

```python
# application/ports/input/run_tactical_loop.py
"""Tactical loop input port."""

from __future__ import annotations
from abc import ABC, abstractmethod


class IRunTacticalLoop(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def set_goal(self, goal: "Goal") -> None:
        """LLMまたはReactive Loopから現在のGoalを更新する。"""
        ...
```

### 6.3 Use Cases

#### DecideGoalUseCase（application/use_cases/decide_goal.py）★NEW

```python
"""LLM decides the current Goal (direction) - Jev then executes within it."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.dto.game_dto import DecideGoalRequest, DecideGoalResponse
from ailoveshen.application.ports.input.decide_goal import IDecideGoal
from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.value_objects.goal import Goal, GoalType
from ailoveshen.domain.events import GoalChangedEvent


class DecideGoalUseCase(IDecideGoal):
    """
    Gemini(ITextGenerator)に、ゲーム状態・直近イベント・視聴者コメントを渡し、
    GoalType(13種)の中から1つをJSON/function calling形式で選ばせる。

    Jevと同様、LLM側の出力も「自由記述」ではなく「閉じた選択肢からの選択」に
    制約することで、後段のTactical LoopがそのままGoal.stepsを使い回せる。
    """

    def __init__(
        self,
        text_generator: ITextGenerator,
        event_publisher: IEventPublisher,
    ) -> None:
        self._text_generator = text_generator
        self._event_publisher = event_publisher

    async def execute(self, request: DecideGoalRequest) -> DecideGoalResponse:
        try:
            prompt = self._build_goal_selection_prompt(request)
            raw = await self._text_generator.generate(
                prompt=prompt,
                system_instruction=(
                    "あなたはMinecraftを実況プレイするAI配信者の「意思決定担当」です。"
                    f"以下のいずれか1つのgoal_idのみをJSONで返してください: "
                    f"{[g.value for g in GoalType]}"
                ),
            )
            goal_type = self._parse_goal_type(raw)

            goal = Goal(goal_type=goal_type, reason=request.reasoning_hint or "", set_by="llm")

            await self._event_publisher.publish(GoalChangedEvent(goal=goal))
            logger.info(f"LLM set new goal: {goal.goal_type.value} ({goal.reason})")

            return DecideGoalResponse(success=True, goal=goal)

        except Exception as e:
            logger.error(f"Goal decision failed: {e}")
            # フォールバック: 安全側のEXPLOREを維持
            return DecideGoalResponse(
                success=False,
                goal=Goal(goal_type=GoalType.EXPLORE, reason="fallback"),
                error=str(e),
            )

    def _build_goal_selection_prompt(self, request: DecideGoalRequest) -> str:
        return (
            f"## 現在のゲーム状況\n{request.game_state_summary}\n\n"
            f"## 直近のイベント\n{request.recent_events_summary}\n\n"
            f"## 視聴者からのリクエスト\n{request.chat_request_summary or 'なし'}\n\n"
            "## タスク\n次に目指すべきgoal_idを1つだけJSONで選んでください。"
        )

    def _parse_goal_type(self, raw_json: str) -> GoalType:
        import json
        data = json.loads(raw_json)
        return GoalType(data["goal_id"])
```

#### ReactiveDecisionUseCase（application/use_cases/reactive_decision.py）★NEW

```python
"""Reactive decision use case - 600ms tick, Jev only, no LLM in the loop."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.jev_decision_engine import IJevDecisionEngine
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.domain.events import GameEventOccurredEvent
from ailoveshen.domain.value_objects.game_action import ActionType, GameAction
from ailoveshen.domain.value_objects.goal import Goal
from ailoveshen.domain.value_objects.jev_decision import ThreatLevel
from ailoveshen.domain.services.goal_override_policy import GoalOverridePolicy


class ReactiveDecisionUseCase:
    """
    毎tick(〜600ms)呼び出される。生存に関わる判断はLLMを待たずJevのみで完結させる。

    critical脅威時はGoalOverridePolicyを介してTactical LoopのGoalを上書きする。
    """

    ACTION_MAP = {
        "fight": ActionType.ATTACK,
        "flee": ActionType.FLEE,
        "eat": ActionType.EAT,
        "continue": None,  # 現在の行動を継続、何もしない
    }

    def __init__(
        self,
        jev_engine: IJevDecisionEngine,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
        override_policy: GoalOverridePolicy | None = None,
    ) -> None:
        self._jev = jev_engine
        self._bridge = bridge
        self._event_publisher = event_publisher
        self._override_policy = override_policy or GoalOverridePolicy()

    async def execute(self, state: dict, current_goal: Goal) -> Goal:
        """1tick実行し、（必要なら上書きされた）現在のGoalを返す。"""
        decision = await self._jev.reactive_decide(state)

        if decision.threat_level in (ThreatLevel.HIGH, ThreatLevel.CRITICAL):
            await self._event_publisher.publish(GameEventOccurredEvent(
                event_type="threat_detected",
                description=f"脅威レベル: {decision.threat_level.value}",
            ))

        action_type = self.ACTION_MAP.get(decision.immediate_action)
        if action_type is not None:
            action = GameAction(
                action_type=action_type,
                parameters={"direction": decision.flee_direction} if action_type == ActionType.FLEE else {},
                description=f"[reactive] {decision.immediate_action}",
                confidence=decision.confidence,
            )
            success = await self._bridge.execute_action(action)
            if not success:
                logger.warning(f"Reactive action failed: {action.description}")

        if self._override_policy.should_override(decision, current_goal):
            new_goal = self._override_policy.build_override_goal(decision)
            logger.warning(f"Reactive override: Goal -> {new_goal.goal_type.value}")
            return new_goal

        return current_goal
```

#### TacticalDecisionUseCase（application/use_cases/tactical_decision.py）★NEW（旧ExecuteActionUseCaseを置換）

```python
"""Tactical decision use case - 10s tick, Jev picks the next step within the LLM's Goal."""

from __future__ import annotations

from loguru import logger

from ailoveshen.application.ports.output.event_publisher import IEventPublisher
from ailoveshen.application.ports.output.jev_decision_engine import IJevDecisionEngine
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.domain.events import GameActionExecutedEvent
from ailoveshen.domain.value_objects.game_action import GameAction
from ailoveshen.domain.value_objects.goal import Goal

# Goalのstep文字列 → 実際のActionTypeへのマッピングはBridge側の語彙に依存するため
# infrastructure層(MineflayerBridgeAdapter)にActionTypeへの変換テーブルを持たせる。
# ここでは「stepをdescriptionに乗せて丸ごとBridgeへ渡す」ことで責務を分離する。
from ailoveshen.domain.value_objects.game_action import ActionType


class TacticalDecisionUseCase:
    """
    旧`ExecuteActionUseCase`が自由記述の`intention`をキーワードマッチで
    パースしていた箇所を置き換える。LLMの自由記述ではなく、
    「LLMが選んだGoal」×「Jevが選んだstep」という型付きの組み合わせのみを扱う。
    """

    def __init__(
        self,
        jev_engine: IJevDecisionEngine,
        bridge: IMinecraftBridge,
        event_publisher: IEventPublisher,
    ) -> None:
        self._jev = jev_engine
        self._bridge = bridge
        self._event_publisher = event_publisher

    async def execute(self, state: dict, goal: Goal) -> bool:
        decision = await self._jev.tactical_decide(state, goal)

        action = GameAction(
            action_type=ActionType.INTERACT,  # 具体的な種別はBridge側で`next_step`から解決
            parameters={
                "goal": decision.goal_type,
                "step": decision.next_step,
                "approach": decision.approach,
                "urgency": decision.urgency,
            },
            description=f"[tactical] {goal.goal_type.value} -> {decision.next_step}",
            confidence=decision.confidence,
        )

        logger.info(f"Executing tactical step: {action.description}")
        success = await self._bridge.execute_action(action)

        await self._event_publisher.publish(GameActionExecutedEvent(
            action_type=decision.next_step,
            description=action.description,
            success=success,
        ))

        return success
```

## 7. Infrastructure Layer

### 7.1 JevClientAdapter（infrastructure/adapters/jev/jev_client_adapter.py）

```python
"""Jev (TypeSafe AI System One Model) client adapter."""

from __future__ import annotations

from typing import Any

from loguru import logger
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

from ailoveshen.application.ports.output.jev_decision_engine import IJevDecisionEngine
from ailoveshen.core.exceptions import GameConnectionError
from ailoveshen.domain.value_objects.goal import Goal
from ailoveshen.domain.value_objects.jev_decision import (
    ReactiveDecision,
    TacticalDecision,
    ThreatLevel,
)


class JevClientAdapter(IJevDecisionEngine):
    """Implements IJevDecisionEngine using typesafe-sdk's AsyncTypeSafeClient."""

    def __init__(self, api_key: str | None = None) -> None:
        # api_keyを渡さない場合、SDKは環境変数 TYPESAFE_API_KEY を参照する
        self._api_key = api_key
        self._client: AsyncTypeSafeClient | None = None

    async def __aenter__(self) -> "JevClientAdapter":
        self._client = AsyncTypeSafeClient(api_key=self._api_key) if self._api_key else AsyncTypeSafeClient()
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.__aexit__(*exc)

    async def reactive_decide(self, state: dict[str, Any]) -> ReactiveDecision:
        if self._client is None:
            raise GameConnectionError("Jev client not initialized")

        response = await self._client.system_one(
            state=state,
            questions={
                "threat_level": Score(
                    instructions="現在の危険度は？周囲の敵性MOB・体力・空腹度から判断する。",
                    criteria=["none", "low", "medium", "high", "critical"],
                ),
                "immediate_action": Choice(
                    instructions="次のtickで取るべき即時行動は？",
                    criteria={"continue": None, "fight": None, "flee": None, "eat": None},
                ),
                "should_eat": Noul(instructions="空腹度・体力から見て今すぐ食事すべきか？"),
                "flee_direction": Choice(
                    instructions="逃げる場合、どの方向が安全か？（脅威がない場合は無視される）",
                    criteria={"north": None, "south": None, "east": None, "west": None, "toward_base": None},
                ),
            },
        )

        threat_raw = response.scores["threat_level"].score
        return ReactiveDecision(
            threat_level=ThreatLevel(threat_raw),
            immediate_action=response.choices["immediate_action"].choice,
            should_eat=response.nouls["should_eat"].noul,
            flee_direction=response.choices["flee_direction"].choice,
            confidence=response.choices["immediate_action"].confidence,
        )

    async def tactical_decide(self, state: dict[str, Any], goal: Goal) -> TacticalDecision:
        if self._client is None:
            raise GameConnectionError("Jev client not initialized")

        steps = goal.goal_type.steps
        response = await self._client.system_one(
            state={**state, "current_goal": goal.goal_type.value},
            questions={
                "next_step": Choice(
                    instructions=f"Goal「{goal.goal_type.value}」を達成する上で、現在の状態に対して"
                                 f"次に実行すべきステップはどれか？",
                    criteria={step: None for step in steps},
                ),
                "approach": Choice(
                    instructions="このステップをどんなスタイルで実行すべきか？",
                    criteria={"aggressive": None, "cautious": None, "efficient": None},
                ),
                "urgency": Score(
                    instructions="このステップの緊急度は？",
                    criteria=["low", "medium", "high"],
                ),
            },
        )

        return TacticalDecision(
            goal_type=goal.goal_type.value,
            next_step=response.choices["next_step"].choice,
            approach=response.choices["approach"].choice,
            urgency=response.scores["urgency"].score,
            confidence=response.choices["next_step"].confidence,
        )
```

### 7.2 MineflayerBridgeAdapter（infrastructure/adapters/minecraft_bridge/mineflayer_bridge_adapter.py）

Node.js側（`minecraft-bridge/`）のWebSocket/HTTPサーバへ接続するアダプター。設定項目(`host`/`port`)は旧NitroGenAdapterと同じ形状を維持する。

```python
"""Mineflayer bridge adapter (replaces NitroGenAdapter)."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.core.exceptions import GameConnectionError
from ailoveshen.domain.value_objects.game_action import GameAction


class MineflayerBridgeAdapter(IMinecraftBridge):
    """
    Infrastructure adapter for the Node.js + Mineflayer sidecar.

    サイドカーは以下のHTTP APIを公開する想定（詳細は7.3参照）:
      GET  /observe  -> 現在のゲーム状態JSON
      POST /action    -> GameActionを受け取り実行
      GET  /health    -> 接続確認
    """

    def __init__(self, host: str = "localhost", port: int = 8090) -> None:
        self._base_url = f"http://{host}:{port}"
        self._client: httpx.AsyncClient | None = None
        self._connected = False

    async def connect(self) -> None:
        try:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=5.0)
            resp = await self._client.get("/health")
            resp.raise_for_status()
            self._connected = True
            logger.info(f"Connected to Minecraft Bridge at {self._base_url}")
        except Exception as e:
            raise GameConnectionError(f"Failed to connect to Minecraft Bridge: {e}")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
        self._connected = False
        logger.info("Disconnected from Minecraft Bridge")

    async def get_observation(self) -> dict[str, Any]:
        if not self._connected or self._client is None:
            raise GameConnectionError("Not connected")
        resp = await self._client.get("/observe")
        resp.raise_for_status()
        return resp.json()

    async def execute_action(self, action: GameAction) -> bool:
        if not self._connected or self._client is None:
            raise GameConnectionError("Not connected")
        try:
            resp = await self._client.post("/action", json={
                "action_type": action.action_type.value,
                "parameters": action.parameters,
                "description": action.description,
            })
            resp.raise_for_status()
            return bool(resp.json().get("success", False))
        except Exception as e:
            logger.error(f"Action failed: {e}")
            return False

    def is_connected(self) -> bool:
        return self._connected
```

### 7.3 Minecraft Bridge サイドカー（`minecraft-bridge/`, Node.js）

Pythonプロセスとは別に、Mineflayer（Minecraftプロトコルを直接喋るJS製ライブラリ）を動かす小さなNode.jsサービスを立てる。理由:

- MineflayerはNode.jsエコシステムのライブラリであり、Pythonから直接は使えない。
- [jev-craft](https://github.com/akash-kamat/jev-craft)が実証済みの構成（Mineflayer + Jev）にならうことで、実装コストとリスクを下げる。
- Python側（AILoveShen本体）はこのサイドカーをHTTP経由の「ゲーム環境」として扱うだけなので、Phase 1で確立したクリーンアーキテクチャのOutput Port抽象化がそのまま活きる（実装がNitroGenからMineflayer Bridgeに変わっても、Application層以上は無改修）。

```
minecraft-bridge/
├── package.json          # mineflayer, mineflayer-pathfinder, express, ws
├── .env.example           # MC_HOST, MC_PORT, MC_USERNAME, BRIDGE_PORT
└── src/
    ├── index.js           # Express + WebSocketサーバのエントリポイント
    ├── observe.js         # bot.entity/health/food/inventory等 → JSON化
    └── actions.js         # {action_type, parameters} → mineflayer API呼び出し
```

最小限のエンドポイント仕様:

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/health` | サイドカー疎通確認 |
| GET | `/observe` | 体力・空腹・座標・インベントリ・近接エンティティ等のJSONスナップショット |
| POST | `/action` | `{action_type, parameters}` を受け取り、Mineflayer APIで実行し `{success}` を返す |
| WS | `/events` | ダメージ・死亡・アイテム取得等のプッシュ通知（Reactive Loopの追加トリガーとして将来利用） |

実装自体（JSコード）は本Phaseの範囲外とし、別Issueで管理する（本ドキュメントはPython側の設計に集中する）。

## 8. Presentation Layer

### 8.1 GameService（presentation/services/game_service.py）

```python
"""Game service - orchestrates Reactive Loop, Tactical Loop and Goal updates."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from loguru import logger

from ailoveshen.application.ports.input.get_game_state import IGetGameState
from ailoveshen.application.use_cases.reactive_decision import ReactiveDecisionUseCase
from ailoveshen.application.use_cases.tactical_decision import TacticalDecisionUseCase
from ailoveshen.domain.entities.game_state import GameState
from ailoveshen.domain.value_objects.goal import Goal, GoalType
from ailoveshen.domain.value_objects.game_event import GameEvent


class GameService:
    """
    Presentation layer service coordinating:
      - Reactive Loop (~600ms, Jevのみ)
      - Tactical Loop (~10s, LLMのGoal x Jevのstep選択)
      - LLMからのGoal更新受付
    """

    def __init__(
        self,
        get_game_state: IGetGameState,
        reactive_decision: ReactiveDecisionUseCase,
        tactical_decision: TacticalDecisionUseCase,
        reactive_interval_ms: int = 600,
        tactical_interval_s: float = 10.0,
    ) -> None:
        self._get_state = get_game_state
        self._reactive = reactive_decision
        self._tactical = tactical_decision
        self._reactive_interval = reactive_interval_ms / 1000
        self._tactical_interval = tactical_interval_s

        self._current_goal = Goal(goal_type=GoalType.EXPLORE, reason="initial")
        self._running = False
        self._reactive_task: Optional[asyncio.Task] = None
        self._tactical_task: Optional[asyncio.Task] = None
        self._event_handlers: list[Callable[[GameEvent], None]] = []

    async def start(self) -> None:
        self._running = True
        self._reactive_task = asyncio.create_task(self._reactive_loop())
        self._tactical_task = asyncio.create_task(self._tactical_loop())
        logger.info("GameService started (reactive + tactical loops)")

    async def stop(self) -> None:
        self._running = False
        for task in (self._reactive_task, self._tactical_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("GameService stopped")

    async def get_state(self) -> GameState | None:
        response = await self._get_state.execute_request()
        return response.state if response.success else None

    def set_goal(self, goal: Goal) -> None:
        """LLM(DecideGoalUseCase経由)から呼ばれ、Tactical Loopの対象Goalを更新する。"""
        logger.info(f"Goal updated: {self._current_goal.goal_type.value} -> {goal.goal_type.value}")
        self._current_goal = goal

    def on_game_event(self, handler: Callable[[GameEvent], None]) -> None:
        self._event_handlers.append(handler)

    async def _reactive_loop(self) -> None:
        while self._running:
            try:
                state = await self._get_state.execute_raw()
                self._current_goal = await self._reactive.execute(state, self._current_goal)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reactive loop error: {e}")
            await asyncio.sleep(self._reactive_interval)

    async def _tactical_loop(self) -> None:
        while self._running:
            try:
                state = await self._get_state.execute_raw()
                await self._tactical.execute(state, self._current_goal)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Tactical loop error: {e}")
            await asyncio.sleep(self._tactical_interval)
```

## 9. Composition Root（`main.py`抜粋）

```python
from ailoveshen.application.use_cases.decide_goal import DecideGoalUseCase
from ailoveshen.application.use_cases.get_game_state import GetGameStateUseCase
from ailoveshen.application.use_cases.reactive_decision import ReactiveDecisionUseCase
from ailoveshen.application.use_cases.tactical_decision import TacticalDecisionUseCase
from ailoveshen.domain.services.event_detection_service import EventDetectionService
from ailoveshen.infrastructure.adapters.jev.jev_client_adapter import JevClientAdapter
from ailoveshen.infrastructure.adapters.minecraft_bridge.mineflayer_bridge_adapter import (
    MineflayerBridgeAdapter,
)
from ailoveshen.presentation.services.game_service import GameService


async def create_game_service(config: "JevMinecraftConfig", event_publisher, text_generator) -> GameService:
    bridge = MineflayerBridgeAdapter(host=config.bridge.host, port=config.bridge.port)
    await bridge.connect()

    jev = JevClientAdapter(api_key=config.jev.api_key)
    await jev.__aenter__()

    event_detection = EventDetectionService()

    get_game_state = GetGameStateUseCase(
        game_environment=bridge,
        event_detection=event_detection,
        event_publisher=event_publisher,
    )

    reactive_decision = ReactiveDecisionUseCase(
        jev_engine=jev, bridge=bridge, event_publisher=event_publisher,
    )
    tactical_decision = TacticalDecisionUseCase(
        jev_engine=jev, bridge=bridge, event_publisher=event_publisher,
    )
    decide_goal = DecideGoalUseCase(
        text_generator=text_generator, event_publisher=event_publisher,
    )

    game_service = GameService(
        get_game_state=get_game_state,
        reactive_decision=reactive_decision,
        tactical_decision=tactical_decision,
        reactive_interval_ms=config.bridge.reactive_interval_ms,
        tactical_interval_s=config.jev.tactical_interval_seconds,
    )

    # OrchestratorのメインループやDecideGoalUseCaseの結果をgame_service.set_goal()へ橋渡しする
    # 配線はPhase 8 (Orchestration) 側で行う。

    return game_service
```

## 10. LLMとの連携（この設計が満たす要件）

- **方向性の決定はLLMが継続して行う**: `DecideGoalUseCase`がGemini 3.8 Flashを呼び出し、13種のGoalから次の目標を選ぶ。視聴者コメント（Twitch連携, Phase 4）も判断材料に含められる。
- **Jevへのリクエストという形も維持される**: LLMは自由文の指示ではなく、`Goal`という閉じた型でJevに「今の目標」を渡す。Jevはその範囲内で高速に手段（`next_step`/`approach`/`urgency`）を決める。
- **生存に関わる反射的判断はJevが完全に代行する**: Reactive LoopはLLMを介さず600ms周期で回るため、LLMのレイテンシがボトルネックにならない。
- **実況ネタとしてフィードバックされる**: Reactive/Tactical Loopの判断結果は`GameEvent`としてイベントバスに流れ、Phase 3の`GenerateCommentaryUseCase`がこれを材料に実況を生成する（「うわ、ゾンビだ逃げなきゃ！」等）。

## 11. 設定

```yaml
# config/default.yaml への追加想定（実際の反映はPhase 6実装時に行う）
minecraft_bridge:
  enabled: false
  host: "localhost"
  port: 8090
  reactive_interval_ms: 600

jev:
  enabled: false
  api_key: "${TYPESAFE_API_KEY:-}"
  tactical_interval_seconds: 10
```

### 11.1 移行ノート（実装時にあわせて対応すること）

- `src/ailoveshen/core/infrastructure/config.py` に `MinecraftBridgeSettings` + `JevSettings` を追加する（旧`NitroGenSettings`は本改訂にあわせて削除済み）。
- `config/default.yaml` には既に `game:` / `minecraft:` セクションが存在するが、いずれも `config.py` 側に未配線である。Phase 6実装時に `minecraft:` を上記 `minecraft_bridge:` 相当へ整理し、`jev:` セクションを新設したうえで、`Settings` へのロード処理を併せて実装する。
- どちらも本ドキュメントの対象（設計）範囲外のため、Phase 6着手時に別途コード側の変更として実施する。

## 12. テスト計画（概要）

- **Unit**: `GoalOverridePolicy`, `ReactiveDecision`/`TacticalDecision`のバリデーション、`DecideGoalUseCase`のJSONパース異常系。
- **Integration**: `JevClientAdapter`は`typesafe-sdk`をモックし、`system_one()`のリクエスト形状（questionsのキー・criteria）を検証する。`MineflayerBridgeAdapter`は`httpx`をモックしてAPI契約を検証する。
- **E2E（手動）**: 実際のMinecraftサーバ + Minecraft Bridgeサイドカー + 実Jev APIキーで、Reactive Loopが低HP時に`flee`を選ぶこと、Tactical Loopが`MINE_ORE`ゴール下で妥当な`next_step`を選ぶことを確認する。

## 13. 参考

- [TypeSafe AI - Jev](https://typesafe.ai/)
- [Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [jev-craft (Mineflayer + Jev Minecraft bot)](https://github.com/akash-kamat/jev-craft)
- [Mineflayer](https://github.com/PrismarineJS/mineflayer)
