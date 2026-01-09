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
│   NitroGen(Game) → GameState → Gemini 2.5 → Commentary → TTS → Audio   │
│                                                                         │
│   【サブループ】コメント対応（割り込み）                                │
│   Twitch Chat → Gemini Flash(Filter) → Gemini 2.5 → Response → TTS     │
│                                                                         │
│   【双方向連携】NitroGen ↔ LLM                                         │
│   - Game State Manager: ゲーム状態 → LLM                                │
│   - Action Executor: LLMの意図 → ゲーム操作                             │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 主要コンポーネント

| コンポーネント | 役割 | Issue |
|---------------|------|-------|
| Twitch Connector | チャット取得・配信連携 | #1 |
| Gemini 2.5 Client | ゲーム実況・コメント応答生成 | #2 |
| MCP Server | 記憶管理・表情制御・感情状態 | #3 |
| NitroGen Bridge | ゲーム状態取得・アクション実行 | #4 |
| TTS Pipeline | Style-Bert-VITS2連携・音声合成 | #5 |
| OBS Connector | 配信制御・シーン切り替え | #6 |
| Comment Filter | Gemini Flashによる動的フィルタリング | #7 |
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
│  │  - OBSWebSocketAdapter, NitroGenAdapter, SQLiteMemoryRepository     │    │
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

各フェーズは以下の統一されたディレクトリ構造を持ちます：

```
src/ailoveshen/{module}/
├── domain/
│   ├── __init__.py
│   ├── entities.py          # Entity定義
│   ├── value_objects.py     # Value Object定義
│   └── services.py          # Domain Service定義
├── application/
│   ├── __init__.py
│   ├── ports/
│   │   ├── __init__.py
│   │   ├── input_ports.py   # Use Case Interface定義
│   │   └── output_ports.py  # 外部サービスInterface定義
│   ├── use_cases/
│   │   ├── __init__.py
│   │   └── *.py             # Use Case実装
│   └── dto.py               # DTO定義
├── infrastructure/
│   ├── __init__.py
│   └── adapters/
│       ├── __init__.py
│       └── *.py             # Adapter実装
├── presentation/
│   ├── __init__.py
│   └── service.py           # Service実装
└── main.py                  # Composition Root
```

### 2.6 Composition Root

依存性注入はComposition Root（各フェーズの`main.py`）で行います：

```python
def create_tts_service(config: TTSConfig, event_publisher: IEventPublisher) -> TTSService:
    """Create TTS service with all dependencies."""
    # Infrastructure
    synthesizer = StyleBertVits2Client(host=config.host, port=config.port)
    player = SounddevicePlayer()

    # Domain Service
    emotion_service = EmotionStyleService(style_map=config.emotion_style_map)

    # Use Cases
    speak_use_case = SpeakTextUseCase(
        synthesizer=synthesizer,
        player=player,
        emotion_service=emotion_service,
        event_publisher=event_publisher,
    )

    # Presentation
    return TTSService(speak_text=speak_use_case)
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
│  │    Twitch    │  │   NitroGen   │  │           │  │     OBS      │  │     MCP      │ │
│  │  Connector   │  │    Bridge    │  │Orchestrator│  │  Connector   │  │   Server     │ │
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
│  │                           Gemini 2.5 Client                                  │     │
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
│  NitroGen   │───▶│ GameState   │───▶│ Gemini 2.5  │───▶│    TTS      │
│   Bridge    │    │  Manager    │    │  (思考生成)  │    │  Pipeline   │
└─────────────┘    └─────────────┘    └──────┬──────┘    └─────────────┘
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
│   Twitch    │───▶│   Comment   │───▶│ Gemini 2.5  │───▶│    TTS      │
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
| LLM | Google Gemini API (2.5 Pro, Flash) |
| ゲーム | NitroGen / MineDojo |
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

### Phase 6: NitroGen Integration (Issue #4)
- **Domain**: GameAction/GameEvent Value Objects, EventDetectionService
- **Application**: IGetGameState/IExecuteAction Input Ports, IGameEnvironment Output Port
- **Infrastructure**: NitroGenAdapter
- **Presentation**: GameService

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
| [06_phase6_nitrogen_integration.md](./06_phase6_nitrogen_integration.md) | NitroGen Integration詳細設計 |
| [07_phase7_obs_integration.md](./07_phase7_obs_integration.md) | OBS Integration詳細設計 |
| [08_phase8_orchestration.md](./08_phase8_orchestration.md) | Orchestration詳細設計 |
