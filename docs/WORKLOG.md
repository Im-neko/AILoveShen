## Current Status

**Active Phase**: Phase 6 の方式検証（プロトコルミラー + Jev の spike）。Phase 4 最小版は保留中
**Last Updated**: 2026-09-24
**Test Status**: 227 unit tests passing (`pytest tests/`)。spike: ミラーのヘッドレス確認と、Jev のシナリオ評価およびクローズドループは成功。実クライアントでの描画確認は未実施

---

## Completed Work

### Spike: プロトコルミラー + Jev による行動選択 (2026-09-24)

**Branch**: `spike/minecraft-mirror-jev`。詳細と表は `spikes/README.md`

- `docker/docker-compose.minecraft.yml`: Paper 1.21.4、offline、127.0.0.1:25565、RCON 有効
- **ミラー**（`spikes/minecraft-mirror/mirror.mjs`）
  - bot の configuration パケットと play パケットを記録し、127.0.0.1:25578 の偽サーバーに生バイトのまま中継する
  - ヘッドレス確認: play まで到達し、チャンク、体力、20Hz の位置を受け取った。パースエラーはなかった
  - **バニラクライアントでの描画は未確認**
- **Jev**（`spikes/minecraft-mirror/bridge.mjs` + `spikes/jev_eval.py`）
  - typesafe-sdk 0.7.1 の `system_one` で、Choice を1問だけ投げる。`jev-1.13.0` で約 0.2s
  - 10シナリオ × 4条件（summary/raw × 手がかり付き/generic な説明）× 3回の結果
    - 妥当な選択にならなかったのは、raw 条件の zombie_far_day だけだった（剣があり、14m 先のゾンビから逃げた）
    - raw は正解の確率が一貫して低く、トークンは 4〜20 倍になる → 状態は要約して渡す
  - generic な説明でも、state に応じて attack と flee を切り替えた。説明文ではなく state を読んでいる
  - confidence は、はっきりした状況で 0.8〜0.99、割れる状況で 0.1〜0.5 だった。LLM へ上げるかどうかの閾値として使える
  - クローズドループ: 昼 10/10 成功。夜にゾンビ2体を倒したが、体力は 20 から 11.3 に減った。アクション実行中の被弾は Jev では防げないので、反射層をコードで持つ必要がある
  - バグ修正: 採掘の遅延。足元を掘った直後の空中で採掘を始めると、速度が 1/5 になっていた。着地を待ってから掘るようにした
- API キーは pass の `JEV_API_KEY` を、SDK が読む `TYPESAFE_API_KEY` として渡す

---

### Phase 3 実機確認: Gemini → TTS 読み上げ (2026-09-24)

**Issue**: #2（このエントリの PR マージでクローズ）

`examples/integration_test_llm.py --speak` で、Gemini 3.8 Flash が生成した3つの発話を、Style-Bert-VITS2（Docker、shen モデル）で最後まで読み上げられることを確認した。

**Changes**:
- `config/default.yaml`
  - `tts.voice.model_name` を `"default"` から `"shen"` に変更
  - `emotion_style_map` をすべて `"Neutral"` に変更。shen モデルは Neutral しか持たず、他のスタイルを指定するとサーバーが 422（`style=Happy not found`）を返す。感情表現は声ではなくアバター（Live2D）で行う方針
- `TTSService.wait_until_idle()` を追加（キューに積んだ発話の再生がすべて終わるまで待つ）
  - `_process_loop` は例外が起きても `task_done()` するようにした
  - `stop()` でキューを捨てた要素も `task_done()` するようにした
  - 以前のスクリプトは「キューが空 + 5秒」を待っていたため、10秒ある3つ目の発話が再生途中で切れていた
- `tests/unit/presentation/test_tts_service.py`（4件）

**計測値**（1〜2文の発話）: 生成 1.6〜2.0秒、合成 2.6〜3.3秒、生成開始から声が出るまで約4.2〜4.6秒

---

### Phase 3 最小版: Gemini LLM Integration (2026-09-24)

**Issue**: #2（実 API での確認が済むまでオープンのまま）

Gemini 3.8 Flash（google-genai SDK）で、実況と視聴者コメントへの返答を生成する。層別構成に合わせて実装した。

**Implemented Components**:
- Domain
  - `domain/value_objects.py`: `MessageRole`, `MessageType`, `ConversationMessage`, `CharacterProfile`, `GenerationContext`
  - `domain/entities.py`: `Conversation`（最新 max_history 件の会話履歴）
  - `domain/events.py`: `CommentaryGeneratedEvent`, `ChatResponseGeneratedEvent`
  - `domain/exceptions.py`: `TextGenerationError`
- Application
  - `ports/output/{text_generator,prompt_builder}.py`、`ports/input/{generate_commentary,generate_response}.py`
  - `dto/llm_dto.py`、`use_cases/{generate_commentary,generate_response}.py`
- Infrastructure
  - `adapters/gemini/gemini_text_generator.py`: `GeminiTextGenerator`
  - `adapters/prompts/prompt_template_builder.py`: `PromptTemplateBuilder`
  - `config.py`: `GeminiSettings` から `temperature`/`max_tokens` を削除し、`main_thinking_level`/`filter_thinking_level`/`max_output_tokens`/`retry`/`rate_limit` を追加。`CharacterSettings` を新設
- Presentation / Composition Root: `presentation/services/llm_service.py`、`factories/llm.py`
- `pyproject.toml`: `llm` extra（`google-genai>=2.25`）
- `config/default.yaml`: gemini セクションを更新し、`character` セクションを追加
- Tests: 67 件追加（計 223）
- Examples: `demo_phase3.py`（偽の生成器、キー不要）、`integration_test_llm.py [--speak]`（実 API、TTS 読み上げは任意）
- Docs: 設計書 03 を実装に合わせて全面改訂、04 のフィルタのコード例を google-genai 化、01 §6.1 は 03 への参照に置き換え、CLAUDE.md・README を更新

**Key Design Decisions**:
1. Gemini 固有の概念（thinking_level、SDK の型、ロール名）は `GeminiTextGenerator` に閉じ込めた。domain は `VIEWER`/`STREAMER` という配信の言葉で会話を表す
2. `generate_with_history` は作らない。履歴は文字列としてプロンプトに埋め込む（3.8 は prefill を推奨していないため）
3. プロンプトの文面はモデルに依存しないので `adapters/prompts/` に置いた
4. リトライは SDK の `HttpRetryOptions` を使う。ローカルの模擬サーバーで、503/429 は計3回、400 は1回で失敗することを確認した
5. `thinking_level` は low/medium/high のみ受け付ける。SDK は `minimal` を通すが 3.8 Flash は拒否するので、起動時に弾く
6. `max_output_tokens` には思考トークンが含まれ、上限に達すると出力が空になりうる。500 から 8192 に上げたが、**仮の値で未調整**
7. 実況と応答で `Conversation` を1つ共有する。応答生成に失敗しても、視聴者コメントの記録は残す
8. 依存方向のテストで、新しいファイルにも規則違反がないことを確認済み

**確認済み / 未確認**:
- 確認済み: ユニットテスト、`demo_phase3.py`、無効なキーでの実リクエスト（400 → `TextGenerationError`、即時失敗、レート制限の間隔）
- 確認済み: 模擬サーバーで実際のリクエスト本文を確認した（`systemInstruction`、`maxOutputTokens`、`thinkingConfig` あり。`temperature`/`topP`/`topK`/`tools` なし）。SDK は `thinkingConfig` の中を `thinking_level` と snake_case で送っている
- 確認済み（マージ後の 2026-09-24）: 実キーで `integration_test_llm.py` の3回の生成がすべて成功。snake_case の `thinking_level` も API に受理された。low と medium を比べて main=low に決定（下記「thinking_level の調整」）
- 未確認: `--speak` での TTS 連携（このマシンに Style-Bert-VITS2 のモデルがなく、Docker も停止しているため）

**Commit**: `2727c02` - feat: implement Phase 3 minimal LLM conversation with Gemini 3.8 Flash (#2)
**PR**: #16（base: #15）

---

### 層別構成への移行: Layer-first Clean Architecture (2026-09-24)

`core/` と `tts/` の「機能ごとに4層」構成をやめ、最上位を
`domain / application / infrastructure / presentation / factories` に分けた。
設計書 02〜07 はもともとこの構成を前提にしていたので、実装が設計書に揃った。

**AI 層の方針（決定事項）**: Flue（TypeScript のエージェントフレームワーク）は採用しない。
Python + google-genai で `ITextGenerator` ポートの内側に実装する。Flue は検討したが、
次の理由で見送った。
- 任せられるのは API 呼び出しと短期の会話履歴（要約圧縮）だけ。長期記憶・感情などのドメイン記憶は結局自前で作る
- Python ↔ Node の二重ランタイムとプロセス間通信が増える
- 最新版 2.1.1 が pi-ai `^0.83` に固定されており、`gemini-3.8-flash` を解決できない
Phase 3 の最小版を動かしたあとに、実際に困った点が Flue で解消するなら再検討する。

**移動先**:
- `core/domain/*`, `core/exceptions.py` → `domain/`（`DomainEvent` は `value_objects.py` から `events.py` へ移動）
- `tts/domain/value_objects.py` → `SpeechResult`/`SpeechStatus` は `domain/value_objects.py` へ、`VoiceConfig` は `infrastructure/adapters/tts/voice_config.py` へ
- `tts/domain/events.py` → `domain/events.py`
- `core/application/ports/output_ports.py` → `application/ports/output/event_publisher.py`
- `tts/application/*` → `application/{ports,use_cases,dto}/`
- `core/infrastructure/*` → `infrastructure/`
- `tts/infrastructure/adapters/*` → `infrastructure/adapters/{tts,audio}/`
- `tts/domain/services/emotion_style_service.py` → `infrastructure/adapters/tts/`
- `tts/presentation/*` → `presentation/services/`
- `tts/factory.py` → `factories/tts.py`
- テストも同じ構成に移動（`tests/unit/{domain,application,infrastructure/adapters/...}`）

**Key Design Decisions**:
1. エンジン固有の語彙は adapter に閉じ込める。スタイル名（"Happy" 等）への変換を `StyleBertVits2Client` 側へ移し、domain/application は `EmotionState` だけを扱う
   - `ISpeechSynthesizer.synthesize(text, emotion, ...)`、`SpeakTextRequest.emotion`、`SpeechRequest.emotion`、`SpeechStartedEvent.emotion`
   - 未使用だった `ISpeechSynthesizer.get_available_styles` をポートから削除（adapter には残す）
2. 依存の向きを `tests/unit/test_architecture.py` で検査する（AST で import を解析）
3. 設計書 03〜08 は `domain/value_objects/` をパッケージとして分割する前提だが、実装は単一ファイルのまま。各フェーズの実装時に判断する

**Files Changed**: `src/ailoveshen/**`, `tests/unit/**`, `examples/demo_phase{1,2}.py`, `examples/integration_test_tts.py`,
`CLAUDE.md`, `docs/design/00〜07`（構成・import パス・Composition Root の場所）

**Commit**: `f57d37c` - refactor: move to layer-first Clean Architecture
**PR**: #15（base: #14）

---

### Gemini モデル切り替え: → gemini-3.8-flash (2026-09-24)

`main_model` と `filter_model` の両方を `gemini-3.8-flash` に統一した（GA、2026-09-02 リリース）。

**Files Changed**:
- `src/ailoveshen/core/infrastructure/config.py` - `GeminiSettings` のデフォルト値と `.get()` のフォールバック値
- `config/default.yaml`, `tests/unit/infrastructure/test_config.py`
- `docs/design/00, 01, 03, 04, 06, 08` - モデルID・本文・図
- `README.md`, `CLAUDE.md`, `.serena/`

**Key Design Decisions**:
1. 同じモデルで 2 枠を使い、役割の差は `thinking_level` で付ける想定（filter=low, main=medium）。未実装
2. 旧設定では `config.py` のデフォルト値（`gemini-2.5-pro-preview-05-06`）と yaml（`gemini-2.5-pro`）が食い違っていたが、今回の統一で解消

**未解決（Phase 3 で対応）**:
- 3.8 Flash では `temperature` / `top_p` / `top_k` が廃止され、`thinking_level`（low/medium/high）に置き換わった。`GeminiSettings.temperature` と設計書 03/04 のコード例がまだ残っている
- 設計書のコード例は旧 SDK `google.generativeai` 前提。`google-genai`（`genai.Client`）への書き換えが必要
- Flue（TypeScript のエージェントフレームワーク）の採用を検討 → 見送り（層別構成への移行の項を参照）

**Commit**: `51e9176` - chore: switch Gemini models to gemini-3.8-flash
**PR**: #14

### Phase 6 設計改訂: NitroGen → Jev + Mineflayer (2026-09-20)

**Issue**: #4 (実装は未着手のためオープンのまま)

NitroGen (MineDojo) が設計のみで未実装のため、Phase 6 の Minecraft 統合設計を
TypeSafe AI の System One モデル **Jev** + **Mineflayer** ブリッジ構成へ改訂。

**役割分担**:
- Gemini 2.5 (System 2): 方向性 = `Goal`（13種の閉じた集合）を決定
- Jev (System 1): Goal 範囲内の高速・型付き判断
  - Reactive Loop (~600ms): 生存反射、LLM を介さない
  - Tactical Loop (~10s): Goal 内の次の一手の選択
- Mineflayer Bridge (Node.js サイドカー): 判断をゲーム操作へ変換、状態・イベントを双方へ還流

**Files Changed**:
- `docs/design/06_phase6_jev_integration.md` - 追加（全13章）
- `docs/design/06_phase6_nitrogen_integration.md` - 削除
- `docs/design/00_architecture_overview.md`, `01_phase1_core_infrastructure.md`,
  `08_phase8_orchestration.md` - 参照更新
- `README.md`, `CLAUDE.md` - プロジェクト概要更新
- `src/ailoveshen/core/infrastructure/config.py` - `NitroGenSettings` 削除

**Key Design Decisions**:
1. LLM は「何を目指すか(Goal)」、Jev は「今どう動くか」という System 2 / System 1 分担
2. Jev の型付き出力 (`Noul`/`Choice`/`Score`) により旧設計の `ACTION_KEYWORDS` による
   自由テキストのキーワードマッチを廃止
3. Mineflayer は Node.js 専用のため、Python 本体とは別プロセスのサイドカー構成とする
4. `MinecraftBridgeSettings` / `JevSettings` の追加は Phase 6 実装時に行う（設計書 11.1）

**Commit**: `2944add` - docs: replace NitroGen plan with Jev + Mineflayer bridge design (#4)
**PR**: #11

---

### Docker化: TTS Server Containerization (2026-01-09)

**Implemented Components**:
- Docker Configuration
  - `docker/Dockerfile.tts-server` - Style-Bert-VITS2 server image (python:3.10-slim, CPU inference)
  - `docker/docker-compose.yml` - Service definition with health checks and resource limits
  - `docker/config.yml` - TTS server configuration for CPU mode
  - `docker/.env.example` - Environment variable template
  - `docker/docker-compose.override.yml.windows` - Windows-specific volume path settings
  - `docker/README.md` - Quick reference guide
  - `.dockerignore` - Build optimization (excludes models, venv, etc.)
- Documentation
  - `docs/setup/docker.md` - Comprehensive Docker setup guide with architecture diagram
  - `docs/setup/obs-audio-routing.md` - OBS audio routing via virtual audio devices
- Integration Tests
  - Docker integration test: 4/4 tests passing
  - Voice synthesis verified with Docker container

**Key Design Decisions**:
1. Hybrid architecture: TTS server in Docker, client on host (Docker cannot access host audio devices)
2. Volume mounts for model files (bert/, model_assets/) - smaller image, easier model updates
3. CPU inference only (macOS/Windows Docker don't support GPU passthrough)
4. torch >= 2.6 required for CVE-2025-32434 security fix in transformers
5. pyopenjtalk dictionary downloaded during build (avoids runtime permission issues)
6. 120s health check start period for BERT model loading
7. VB-Audio Cable (Windows) / BlackHole (macOS) for OBS audio routing

**Resolved Issues**:
- pip install --index-url applying to all packages → Split into two RUN commands
- pyopenjtalk permission denied → Download dictionary during build before USER switch
- transformers CVE-2025-32434 → Upgrade to torch >= 2.6
- typing-extensions version conflict → Install typing-extensions>=4.10.0 before torch
- Missing numba module → Add numba to dependencies

**Commit**: `a857832` - feat: implement Phase 2 TTS pipeline and Docker containerization

---

### Phase 2: TTS Pipeline (2026-01-09)

**Issue**: #5 (リアルタイム音声合成パイプラインの実装)

**Implemented Components**:
- Domain Layer
  - `src/ailoveshen/tts/domain/value_objects.py` - SpeechResult, SpeechStatus, VoiceConfig
  - `src/ailoveshen/tts/domain/events.py` - SpeechStartedEvent, SpeechCompletedEvent, SpeechQueuedEvent
  - `src/ailoveshen/tts/domain/services/emotion_style_service.py` - EmotionStyleService
- Application Layer
  - `src/ailoveshen/tts/application/ports/input/speak_text.py` - ISpeakText interface
  - `src/ailoveshen/tts/application/ports/output/speech_synthesizer.py` - ISpeechSynthesizer interface
  - `src/ailoveshen/tts/application/ports/output/audio_player.py` - IAudioPlayer interface
  - `src/ailoveshen/tts/application/dto/speech_dto.py` - SpeakTextRequest, SpeakTextResponse
  - `src/ailoveshen/tts/application/use_cases/speak_text.py` - SpeakTextUseCase
- Infrastructure Layer
  - `src/ailoveshen/tts/infrastructure/adapters/tts/style_bert_vits2_client.py` - StyleBertVits2Client
  - `src/ailoveshen/tts/infrastructure/adapters/audio/sounddevice_player.py` - SounddevicePlayer
- Presentation Layer
  - `src/ailoveshen/tts/presentation/services/tts_service.py` - TTSService with priority queue
- Configuration
  - `config/default.yaml` - Extended TTS configuration
  - `src/ailoveshen/tts/factory.py` - Composition Root (create_tts_service)
- Exceptions
  - `src/ailoveshen/core/exceptions.py` - SynthesisError, AudioPlaybackError
- Tests
  - `tests/unit/tts/domain/` - 26 tests for value objects and services
  - `tests/unit/tts/application/` - 20 tests for DTOs and use cases
  - `tests/unit/tts/infrastructure/` - 11 tests for adapters
  - Total: 71 new tests (148 total passing)
- Demo
  - `examples/demo_phase2.py` - All TTS components verified working
- Integration Test
  - `examples/integration_test_tts.py` - End-to-end TTS with real server

**Key Design Decisions**:
1. Lazy imports for heavy dependencies (sounddevice, httpx) via `__getattr__`
2. Dataclass events with default field values for inheritance compatibility
3. Sync methods (stop, is_playing, get_duration_ms) vs async methods (play, synthesize)
4. Priority queue with sequence numbers for FIFO within same priority
5. Optional TTS dependencies via `pip install ailoveshen[tts]`

**Commit**: `a857832` - feat: implement Phase 2 TTS pipeline and Docker containerization

---

### Phase 1: Core Infrastructure (2026-01-09)

**Issue**: #8 (Closed)

**Implemented Components**:
- Domain Layer
  - `src/ailoveshen/core/domain/entities.py` - Entity, AggregateRoot base classes
  - `src/ailoveshen/core/domain/value_objects.py` - EmotionState, SpeechRequest, Position, Rotation, FilterResult, DomainEvent
- Application Layer
  - `src/ailoveshen/core/application/ports/output_ports.py` - IEventPublisher, IEventSubscriber interfaces
- Infrastructure Layer
  - `src/ailoveshen/core/infrastructure/config.py` - YAML config with env var expansion
  - `src/ailoveshen/core/infrastructure/logging.py` - Loguru structured logging
  - `src/ailoveshen/core/infrastructure/events.py` - AsyncEventBus (thread-safe Pub/Sub)
- Configuration Files
  - `config/default.yaml` - Default settings
  - `config/development.yaml` - Development overrides
- Tests
  - `tests/unit/domain/test_entities.py`
  - `tests/unit/domain/test_value_objects.py`
  - `tests/unit/infrastructure/test_config.py`
  - `tests/unit/infrastructure/test_events.py`
  - Total: 77 tests passing
- Demo
  - `examples/demo_phase1.py` - All components verified working

**Key Design Decisions**:
1. UTC timestamps for all datetime fields (distributed system compatibility)
2. Value Objects raise ValueError for invalid values (explicit over silent)
3. Thread safety with threading.Lock (sync) + asyncio.Lock (async)
4. diagnose flag controlled by debug parameter (security consideration)
5. TYPE_CHECKING import to avoid circular imports

**Commit**: `96f7eba` - feat: implement Phase 1 Core Infrastructure

---

## Next Steps

### Phase 3 の積み残し

- **返答にゲーム状況を渡す**: `GenerateResponseRequest` には `game_state_summary` / `recent_events` がない。会話履歴が空だと、モデルが今やっていることを作り話で答える（計測中に「新しいお家を建てている」と答えた）。Phase 6/8 でゲーム状態を渡すときに追加する
- **声が出るまでの遅延**: 生成開始から声が出るまで約4.2〜4.6秒。内訳は生成 1.6〜2.0秒、合成 2.6〜3.3秒（Docker 内・CPU）で、合成のほうが遅い。改善案は、文ごとに分けて合成と再生を並行させる、Docker を使わずに GPU/MPS で推論する、など。Phase 8 で配信の間合いを見ながら判断する
- **`StyleBertVits2Client.get_available_styles()` の不具合**: サーバーの `/models/info` はモデル ID（`"0"`）をキーに返すが、クライアントはモデル名（`"shen"`）で引いているため、常に `["Neutral"]` を返す。現在は呼び出し元がないので実害はない

### thinking_level の調整（2026-09-24 実測）

同じプロンプトで各4回計測した結果:

| thinking_level | 実況（中央値） | 返答（中央値） | 思考トークン |
|---|---|---|---|
| low | 1.90s | 1.64s | 0〜93 |
| medium | 2.87s | 3.10s | 129〜405 |

品質に目立った差がないので、main=low に決めた。`max_output_tokens` は 8192 のまま（実測の合計は最大約1,300トークン）。

**Confirmed Specifications (2026-01-10, 2026-09-24 更新)**:
- Model: `gemini-3.8-flash`（2026-09-24 変更。main/filter とも同じモデル）
- SDK: `google-genai`（`genai.Client`）。旧 `google.generativeai` は使わない
- Generation params: `temperature`/`top_p`/`top_k` は 3.8 で廃止。`thinking_level` を枠ごとに設定（main=low, filter=low。実測にもとづき 2026-09-24 に決定）
- Authentication: API Key only (no OAuth2)
- Retry: 3 attempts including the original request, exponential backoff (base=1s, max=10s), 408/429/5xx only
- Fallback: None (no switch to another model on failure)
- Default response: None (return empty string on failure)
- Rate limit: Simple sleep (1 second interval)
- Token monitoring: Log output only (no alerts)
- GameState: Optional（Phase 6 まではテキスト要約で受け取る）

### 既知の課題（今回の作業で発見）

- **`AggregateRoot` の等価性と hash の不具合**: `@dataclass` の既定（eq=True）によって、ID ではなくフィールドで比較され、hash もできない（`unhashable type`）。サブクラスも同じ。`Conversation` は `eq=False` で回避したが、`AggregateRoot` 自体は未修正
- **TTS の設定経路のずれ**: `create_tts_service` は YAML の生の dict（voice/synthesis/queue/audio）を受け取るが、`Settings.tts`（`TTSSettings`）はその形になっていない。docstring にある `settings.get(...)` も存在しない。`integration_test_llm.py --speak` は YAML を直接読んで回避している
- **プロンプトインジェクション**: 視聴者コメントはそのままプロンプトに入る。Phase 4 のフィルタで対処する
- **設計書のパス**: 03〜08 には `domain/value_objects/` をパッケージとして分割する前提の import パスが残っている。実装は単一ファイル。各フェーズの実装時に判断する

### Phase 4: Twitch Integration (Ready for Implementation)

**Issue**: #1, #7

Refer to design document: `docs/design/04_phase4_twitch_integration.md`

**Confirmed Specifications (2026-01-10)**:
- OAuth: Browser auth flow for full permissions (BAN etc.), but initial impl uses Client Credentials
- Response rate limit: 5 seconds interval, no per-user limit
- Reconnection: Exponential backoff for server errors (infinite retry), no retry for client errors
- NG words: Delegate to Gemini Flash via prompt instructions
- Filter results: Log output + JSONL file (`data/logs/filter_results.jsonl`)

### Phase 5: MCP Server (Ready for Implementation)

**Issue**: #3

Refer to design document: `docs/design/05_phase5_mcp_server.md`

**Confirmed Specifications (2026-01-10)**:
- Short-term memory: 20 entries
- Long-term memory: Auto-save for importance >= HIGH
- Search: SQLite LIKE (vector search planned for future)
- Emotion decay: Applied when generating commentary
- VTube Studio: API v1.0, token file auth

### Phase 6: Jev + Minecraft Bridge Integration (Ready for Implementation)

**Issue**: #4

Refer to design document: `docs/design/06_phase6_jev_integration.md`

**Confirmed Specifications (2026-09-20 改訂)**:
- **Framework change**: NitroGen → Jev (TypeSafe AI System One) + Mineflayer
- Abstraction: `IGameEnvironment` interface for future games (Terraria, Factorio, etc.)
- Minecraft connection: External server (not managed by AILoveShen)
- Action timeout: 5 seconds
- Parallel actions: Enabled with queue management
- Node.js bridge: HTTP API or WebSocket
- Minecraft version: Java Edition v1.21.4

### Phase 7: OBS Integration (Ready for Implementation)

**Issue**: #6

Refer to design document: `docs/design/07_phase7_obs_integration.md`

**Confirmed Specifications (2026-01-10)**:
- OBS version: 28+ (WebSocket 5.x)
- Scene transition: Use OBS default settings
- Subtitle position/font: Configured in OBS (not AILoveShen)
- Subtitle default duration: Persistent (manual hide)

---

## How to Resume Work

1. Read this WORKLOG.md for current status
2. Check GitHub Issues for active tasks
3. Review design documents in `docs/design/`
4. Run tests to verify current state: `pytest tests/`
5. Run demo to verify functionality: `python examples/demo_phase1.py`

---

## Session Notes

### 2026-09-24 (Minecraft サーバー・ミラー・Jev spike)
- Minecraft サーバーを Docker で起動し、Mineflayer の bot で参加と移動を確認した
- bot として参加すると、カメラの動きや UI が配信向きではない。そこで、パケットを実クライアントに中継する方式（プロトコルミラー）を採用し、spike で検証した
- Jev に今実行できるアクションを列挙して選ばせる方式を、シナリオ評価とクローズドループで検証した
- 設計書 06 の書き換えは、実クライアントでの確認が済んでから行う

### 2026-09-24 (モデル切り替え・層別構成・Phase 3 最小版)
- Gemini を 3.8 Flash に統一した（PR #14）
- AI 層に Flue を使うか検討し、見送った。Python + google-genai をポートの内側に実装する方針にした
- 最上位を4層に分ける構成へ移行し、Style-Bert-VITS2 のスタイル名を adapter に閉じ込めた（PR #15）
- Phase 3 最小版を実装した（PR #16）。実キーがないため、実 API での確認は未実施
- #14 → #15 → #16 をこの順でマージした
- 実 API で確認し、thinking_level を low と medium で比較して main=low に決めた
- TTS を Docker で起動し、`--speak` で読み上げまで確認した。shen モデルは Neutral のみなので、感情表現はアバター側で行う

### 2026-09-20 (Phase 6 設計改訂)

- `jev-integration-design.patch` を適用し Phase 6 設計を Jev + Mineflayer 構成へ改訂
  - `CLAUDE.md` のみ改行コード不一致 (パッチは LF / 作業ツリーは CRLF) で自動適用に失敗したため、
    該当6行を CRLF 維持のまま手適用
- 未コミットだった Phase 2 TTS / Docker 化を先行コミット `a857832` として分離
- Phase 6 設計改訂を `2944add` としてコミット、PR #11 経由で main へマージ
- PR 本文の `Closes #4` で Issue #4 が自動クローズされたため再オープン
  (#4 は実装チケットであり、今回のマージは設計書のみ)
- NitroGen 残存を一掃: `config.py` の `NitroGenSettings` 削除、
  `.serena/` メモリ・`docs/WORKLOG.md` の記述を更新
- `pytest tests/` 148 passed で回帰なしを確認

### 2026-01-09 (Docker化)

- Docker化を実装:
  - `docker/Dockerfile.tts-server` - TTS server用Dockerfile
  - `docker/docker-compose.yml` - Compose設定
  - `docker/config.yml` - CPU用TTS設定
  - `docker/docker-compose.override.yml.windows` - Windows用設定
  - `.dockerignore` - ビルド最適化
- 解決した問題:
  - pyopenjtalk辞書: ビルド時にダウンロード
  - transformers CVE-2025-32434: torch >= 2.6必須
  - numba依存: monotonic_alignment用に追加
  - typing-extensions: バージョン競合解決
- Docker統合テスト: 全4テスト合格
- ドキュメント作成:
  - `docs/setup/docker.md` - Dockerセットアップガイド
  - `docs/setup/obs-audio-routing.md` - OBS音声ルーティング設定
  - `docker/README.md` - クイックリファレンス

### 2026-01-09 (Phase 2 Integration)

- Integrated with real Style-Bert-VITS2 server
- Changed server port from 5000 to 5001 (macOS AirPlay conflict)
- Updated config/default.yaml and Style-Bert-VITS2/config.yml
- Created integration test: `examples/integration_test_tts.py`
- Full end-to-end TTS working:
  - Server connects on port 5001
  - Speech synthesis with "shen" model
  - Audio playback via sounddevice
- All 148 unit tests passing

### 2026-01-09 (Phase 2)

- Implemented Phase 2 TTS Pipeline
- Clean Architecture structure: Domain → Application → Infrastructure → Presentation
- All 134 unit tests passing (57 new TTS tests)
- Demo script verified all TTS components working with mocks
- Lazy imports for optional dependencies (sounddevice, httpx)
- Factory pattern for dependency injection
- Ready for Issue #5 closure

### 2026-01-09 (Phase 1)

- Implemented Phase 1 Core Infrastructure
- Code review performed, all Critical/High issues fixed
- All 77 unit tests passing
- Demo script verified all components working
- Pushed to main branch
- Issue #8 closed with completion comment
- Updated CLAUDE.md and design document with completion status
