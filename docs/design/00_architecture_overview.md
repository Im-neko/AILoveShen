# AILoveShen アーキテクチャ設計書

## 1. システム概要

AILoveShenは、MinecraftをプレイしながらTwitchで配信を行うAIストリーマーシステムです。

### 1.1 コアコンセプト

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         AILoveShen System                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   【メインループ】ゲーム実況                                            │
│   Mineflayer → GameState → Gemini 3.8 Flash → Commentary → TTS → Audio │
│                                                                         │
│   【サブループ】コメント対応（割り込み）                                │
│   Twitch Chat → Filter(3.8 Flash) → Gemini 3.8 Flash → Response → TTS  │
│                                                                         │
│   【三層連携】LLM → Jev → Minecraft Bridge                             │
│   - Goal Decision: LLMが方向性（Goal）を決めてJevへリクエスト            │
│   - Reactive/Tactical Decision: Jevが型付き高速判断でゲーム操作を選択    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 主要コンポーネント

| コンポーネント | 役割 | Issue |
|---------------|------|-------|
| Twitch Connector | チャット取得・配信連携 | #1 |
| Gemini 3.8 Flash Client | ゲーム実況・コメント応答生成 | #2 |
| MCP Server | 記憶管理・表情制御・感情状態 | #3 |
| Minecraft Bridge | ゲーム状態取得・アクション実行（Mineflayer） | #4 |
| Jev Decision Engine | リアルタイム戦術・反射判断（TypeSafe AI Jev） | #4 |
| TTS Pipeline | Style-Bert-VITS2連携・音声合成 | #5 |
| OBS Connector | 配信制御・シーン切り替え | #6 |
| Comment Filter | Gemini 3.8 Flashによる動的フィルタリング | #7 |
| Orchestrator | 全体統括・イベント管理 | - |

## 2. クリーンアーキテクチャ

本システムは**クリーンアーキテクチャ**の原則に従って設計されています。

### 2.1 レイヤー構造

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Presentation Layer                                  │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  各フェーズのService (TTSService, LLMService, TwitchService, etc.)    │  │
│  │  - 外部向けAPIの提供                                                   │  │
│  │  - Use Caseの調整                                                      │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────┬────────────────────────────────────┘
                                         │ uses
┌────────────────────────────────────────▼────────────────────────────────────┐
│                           Application Layer                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Input Ports (Use Case Interfaces)                                  │    │
│  │  - ISpeakText, IGenerateCommentary, IFilterComment, etc.            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Use Cases                                                           │    │
│  │  - SpeakTextUseCase, GenerateCommentaryUseCase, etc.                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Output Ports (External Service Interfaces)                          │    │
│  │  - ISpeechSynthesizer, ITextGenerator, IChatProvider, etc.          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  DTOs (Data Transfer Objects)                                        │    │
│  │  - SpeechRequestDTO, GenerationRequestDTO, etc.                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────┬────────────────────────────────────┘
                                         │ implements
┌────────────────────────────────────────▼────────────────────────────────────┐
│                          Infrastructure Layer                                │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Adapters (Output Port implementations)                              │    │
│  │  - StyleBertVits2Client, GeminiTextGenerator, TwitchChatAdapter     │    │
│  │  - OBSWebSocketAdapter, MineflayerBridgeAdapter, JevClientAdapter   │    │
│  │  - SQLiteMemoryRepository                                            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼────────────────────────────────────┐
│                            Domain Layer                                      │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────────┐    │
│  │     Entities      │  │   Value Objects   │  │   Domain Services     │    │
│  │  - Conversation   │  │  - SpeechRequest  │  │  - EmotionStyleService│    │
│  │  - MemoryEntry    │  │  - GameState      │  │  - FilteringPolicy    │    │
│  │  - StreamSession  │  │  - FilterResult   │  │  - PriorityCalculation│    │
│  └───────────────────┘  └───────────────────┘  └───────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Repository Interfaces (Domain defines, Infrastructure implements)   │    │
│  │  - IMemoryRepository, IConversationRepository                        │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 依存関係のルール

1. **依存性の逆転 (Dependency Inversion)**
   - 上位レイヤーは下位レイヤーに依存しない
   - 具体的な実装ではなく、抽象（インターフェース）に依存する
   - Infrastructure層はApplication層のOutput Portを実装する

2. **ドメイン層の独立性**
   - Domain層は他のどのレイヤーにも依存しない
   - ビジネスルールは純粋なPythonで実装
   - 外部ライブラリへの依存を排除

3. **依存の方向**
   ```
   Presentation → Application → Domain
                      ↑
   Infrastructure ────┘
   ```

### 2.3 各レイヤーの責務

| レイヤー | 責務 | 主要要素 |
|---------|------|----------|
| **Domain** | ビジネスルール・ドメイン知識 | Entity, Value Object, Domain Service, Repository Interface |
| **Application** | ユースケースの実装 | Input Port, Output Port, Use Case, DTO |
| **Infrastructure** | 外部サービスとの統合 | Adapter, Repository Implementation |
| **Presentation** | 外部へのAPI提供 | Service, Controller |

### 2.4 命名規則

| 種類 | 命名規則 | 例 |
|------|----------|-----|
| Entity | 名詞 | `Conversation`, `MemoryEntry`, `StreamSession` |
| Value Object | 名詞 | `SpeechRequest`, `GameState`, `FilterResult` |
| Input Port | I + 動詞 + 名詞 | `ISpeakText`, `IGenerateCommentary`, `IFilterComment` |
| Output Port | I + 名詞 + (er/or) | `ISpeechSynthesizer`, `ITextGenerator`, `IChatProvider` |
| Use Case | 動詞 + 名詞 + UseCase | `SpeakTextUseCase`, `GenerateCommentaryUseCase` |
| Adapter | 技術名 + 役割 | `StyleBertVits2Client`, `GeminiTextGenerator` |
| DTO | 名詞 + DTO | `SpeechRequestDTO`, `GenerationResultDTO` |

### 2.5 ディレクトリ構造

最上位を4層で分け、各層の中を種類ごとに分けます。機能（TTS、LLM、Twitch…）は各層にまたがって配置し、機能ごとのパッケージは作りません。

```
src/ailoveshen/
├── domain/                  # Domain Layer（外部依存なし）
│   ├── entities.py          # Entity, AggregateRoot
│   ├── value_objects.py     # EmotionState, SpeechRequest, Position, FilterResult ...
│   ├── events.py            # DomainEvent, Speech*Event ...
│   ├── exceptions.py        # AILoveShenError 系
│   └── services/            # Domain Service
├── application/             # Application Layer
│   ├── ports/
│   │   ├── input/           # Use Case Interface（ISpeakText, IGenerateCommentary ...）
│   │   └── output/          # 外部サービス Interface（IEventPublisher, ISpeechSynthesizer, ITextGenerator ...）
│   ├── use_cases/           # Use Case 実装
│   └── dto/                 # DTO
├── infrastructure/          # Infrastructure Layer
│   ├── config.py            # Settings
│   ├── logging.py           # Loguru
│   ├── events.py            # AsyncEventBus
│   └── adapters/            # Output Port 実装
│       ├── tts/             # StyleBertVits2Client, EmotionStyleService, VoiceConfig
│       ├── audio/           # SounddevicePlayer
│       └── gemini/          # GeminiTextGenerator, プロンプト
├── presentation/            # Presentation Layer
│   └── services/            # TTSService, LLMService ...
└── factories/               # Composition Root（機能ごとに1ファイル: tts.py, llm.py ...）
```

**依存の向き**（内側へのみ）:

| 層 | import してはいけない層 |
|----|------------------------|
| domain | application, infrastructure, presentation, factories |
| application | infrastructure, presentation, factories |
| infrastructure | presentation, factories |
| presentation | infrastructure, factories |

`factories/` だけが全層を import して組み立てます。この規則は `tests/unit/test_architecture.py` で検査しています。

エンジン固有の語彙（例: Style-Bert-VITS2 のスタイル名 `"Happy"`）は infrastructure の adapter に閉じ込めます。domain と application はドメインの概念（`EmotionState` など）だけを扱います。

### 2.6 Composition Root

依存性注入は Composition Root（`factories/` 配下の機能ごとのファイル）で行います：

```python
# src/ailoveshen/factories/tts.py
def create_tts_service(config: dict, event_publisher: IEventPublisher) -> TTSService:
    """Create TTS service with all dependencies."""
    # Infrastructure
    emotion_style_service = EmotionStyleService(style_map=...)
    synthesizer = StyleBertVits2Client(
        host=..., port=..., emotion_style_service=emotion_style_service,
    )
    player = SounddevicePlayer()

    # Use Cases
    speak_use_case = SpeakTextUseCase(
        synthesizer=synthesizer,
        audio_player=player,
        event_publisher=event_publisher,
        get_current_emotion=...,
    )

    # Presentation
    return TTSService(speak_text_use_case=speak_use_case)
```

## 3. システムアーキテクチャ図

```
                                    ┌─────────────────┐
                                    │   Config/Env    │
                                    └────────┬────────┘
                                             │
┌────────────────────────────────────────────┼────────────────────────────────────────────┐
│                                            │                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌─────┴─────┐  ┌──────────────┐  ┌──────────────┐ │
│  │    Twitch    │  │  Minecraft   │  │           │  │     OBS      │  │     MCP      │ │
│  │  Connector   │  │ Bridge + Jev │  │Orchestrator│  │  Connector   │  │   Server     │ │
│  └──────┬───────┘  └──────┬───────┘  │           │  └──────┬───────┘  └──────┬───────┘ │
│         │                 │          │           │         │                 │         │
│         │    ┌────────────┴──────────┤           ├─────────┴─────────────────┤         │
│         │    │                       │           │                           │         │
│         ▼    ▼                       └─────┬─────┘                           │         │
│  ┌──────────────┐                          │                                 │         │
│  │   Comment    │                          │                                 │         │
│  │   Filter     │◄─────────────────────────┤                                 │         │
│  │(Gemini Flash)│                          │                                 │         │
│  └──────┬───────┘                          │                                 │         │
│         │                                  │                                 │         │
│         ▼                                  ▼                                 ▼         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              Event Bus (asyncio)                                │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│         │                                  │                                 │         │
│         ▼                                  ▼                                 ▼         │
│  ┌──────────────────────────────────────────────────────────────────────────────┐     │
│  │                        Gemini 3.8 Flash Client                               │     │
│  │  - Commentary Generator (Main Loop)                                          │     │
│  │  - Response Generator (Sub Loop / Interrupt)                                 │     │
│  └──────────────────────────────────────────┬───────────────────────────────────┘     │
│                                             │                                         │
│                                             ▼                                         │
│  ┌──────────────────────────────────────────────────────────────────────────────┐     │
│  │                            TTS Pipeline                                      │     │
│  │  - Speech Queue (Priority-based)                                             │     │
│  │  - Style-Bert-VITS2 Client                                                   │     │
│  │  - Audio Output                                                              │     │
│  └──────────────────────────────────────────────────────────────────────────────┘     │
│                                                                                       │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

## 4. データフロー

### 4.1 メインループ（ゲーム実況）

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Jev(反射/戦術)│───▶│ GameState   │───▶│Gemini Flash │───▶│    TTS      │
│+Minecraft Br.│    │  Manager    │    │  (思考生成)  │    │  Pipeline   │
└─────────────┘    └─────────────┘    └──────┬──────┘    └─────────────┘
       ▲                                     │
       └──────────────── Goal (方向性) ───────┘
                                             │
                                             ▼
                                      ┌─────────────┐
                                      │   Action    │
                                      │  Executor   │
                                      │(意図→操作)  │
                                      └─────────────┘
```

### 4.2 サブループ（コメント対応）

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Twitch    │───▶│   Comment   │───▶│Gemini Flash │───▶│    TTS      │
│    Chat     │    │   Filter    │    │  (応答生成)  │    │ (Interrupt) │
└─────────────┘    │(Gemini Flash)│    └─────────────┘    └─────────────┘
                   └─────────────┘
                         │
                         ▼
                   動的閾値調整
                   - 少量時: 多く反応
                   - 多量時: 選択的
```

## 5. 技術スタック

| カテゴリ | 技術 |
|---------|------|
| 言語 | Python 3.10+ |
| 非同期 | asyncio |
| Web API | FastAPI |
| TTS | Style-Bert-VITS2 |
| LLM | Google Gemini API (3.8 Flash) |
| ゲーム操作 | Mineflayer (Node.jsブリッジ) + Jev (TypeSafe AI, System Oneモデル) |
| 配信 | Twitch API (IRC/EventSub), OBS WebSocket |
| MCP | Model Context Protocol |
| DB | SQLite (メモリ管理) |
| 設定 | YAML + 環境変数 |
| ログ | loguru |

## 6. 実装フェーズ

### Phase 1: Core Infrastructure
- プロジェクト構造（クリーンアーキテクチャ準拠）
- 設定管理（YAMLベース、環境変数サポート）
- ロギング（loguru）
- 共通データモデル（Entity, Value Object）
- イベントバス（非同期Pub/Sub）

### Phase 2: TTS Integration (Issue #5)
- **Domain**: SpeechRequest/Result Value Objects, EmotionStyleService
- **Application**: ISpeakText Input Port, ISpeechSynthesizer/IAudioPlayer Output Ports
- **Infrastructure**: StyleBertVits2Client, SounddevicePlayer
- **Presentation**: TTSService

### Phase 3: LLM Integration (Issue #2)
- **Domain**: Conversation Entity, ConversationMessage Value Object, ConversationService
- **Application**: IGenerateCommentary/IGenerateResponse Input Ports, ITextGenerator Output Port
- **Infrastructure**: GeminiTextGenerator, PromptTemplateBuilder
- **Presentation**: LLMService

### Phase 4: Twitch Integration (Issue #1, #7)
- **Domain**: FilterResult Value Object, FilteringPolicyService（動的閾値計算）
- **Application**: IFilterComment/IProcessChatMessage Input Ports, IChatProvider Output Port
- **Infrastructure**: TwitchChatAdapter, GeminiCommentAnalyzer
- **Presentation**: TwitchService

### Phase 5: MCP Server (Issue #3)
- **Domain**: MemoryEntry Entity, IMemoryRepository Interface, EmotionDecayService
- **Application**: IStoreMemory/IRecallMemory/IManageEmotion Input Ports
- **Infrastructure**: SQLiteMemoryRepository, MCPServerAdapter
- **Presentation**: MCPService

### Phase 6: Jev + Minecraft Bridge Integration (Issue #4)
- **Domain**: GameAction/GameEvent/Goal/JevDecision Value Objects, EventDetectionService, GoalOverridePolicy
- **Application**: IGetGameState/IDecideGoal/IRunReactiveLoop/IRunTacticalLoop Input Ports, IMinecraftBridge/IJevDecisionEngine Output Ports
- **Infrastructure**: MineflayerBridgeAdapter (Node.jsサイドカー経由), JevClientAdapter (typesafe-sdk)
- **Presentation**: GameService（Reactive Loop 600ms / Tactical Loop 10s を統括）

### Phase 7: OBS Integration (Issue #6)
- **Domain**: Scene/Source Value Objects
- **Application**: IControlScene/IDisplaySubtitle/IControlStream Input Ports, IOBSConnection Output Port
- **Infrastructure**: OBSWebSocketAdapter
- **Presentation**: OBSService

### Phase 8: Orchestration
- **Domain**: StreamSession Entity, PriorityCalculationService, InterruptionPolicyService
- **Application**: IStartStreaming/IStopStreaming/IRunMainLoop/IRunSubLoop Input Ports
- **Infrastructure**: Service Adapters（各フェーズのServiceへの接続）
- **Presentation**: OrchestratorService
- Composition Root: 全体の依存関係ワイヤリング

## 7. 設計書一覧

| ドキュメント | 内容 |
|-------------|------|
| [01_phase1_core_infrastructure.md](./01_phase1_core_infrastructure.md) | Core Infrastructure詳細設計 |
| [02_phase2_tts_pipeline.md](./02_phase2_tts_pipeline.md) | TTS Pipeline詳細設計 |
| [03_phase3_llm_integration.md](./03_phase3_llm_integration.md) | LLM Integration詳細設計 |
| [04_phase4_twitch_integration.md](./04_phase4_twitch_integration.md) | Twitch Integration詳細設計 |
| [05_phase5_mcp_server.md](./05_phase5_mcp_server.md) | MCP Server詳細設計 |
| [06_phase6_jev_integration.md](./06_phase6_jev_integration.md) | Jev + Minecraft Bridge Integration詳細設計 |
| [07_phase7_obs_integration.md](./07_phase7_obs_integration.md) | OBS Integration詳細設計 |
| [08_phase8_orchestration.md](./08_phase8_orchestration.md) | Orchestration詳細設計 |
