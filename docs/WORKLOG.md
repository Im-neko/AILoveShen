# Work Log

This document tracks development progress to enable smooth resumption of work after context loss.

---

## Current Status

**Active Phase**: Docker化 Complete, Ready for Phase 3
**Last Updated**: 2026-01-09
**Test Status**: 148 unit tests passing, Docker integration verified

---

## Completed Work

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

### Phase 3: Gemini LLM Integration (Ready for Implementation)

**Issue**: #2 (Gemini 2.5による会話システムの実装)

Refer to design document: `docs/design/03_phase3_llm_integration.md`

**Confirmed Specifications (2026-01-10)**:
- Model: `gemini-2.5-pro` (not preview version)
- Authentication: API Key only (no OAuth2)
- Retry: 3 attempts with exponential backoff (base=1s, max=10s)
- Fallback: None (no switch to Flash model on failure)
- Default response: None (return empty string on failure)
- Rate limit: Simple sleep (1 second interval)
- Token monitoring: Log output only (no alerts)
- GameState: Optional (Phase 3 works without it)

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

### Phase 6: Game Integration (Ready for Implementation)

**Issue**: #4

Refer to design document: `docs/design/06_phase6_nitrogen_integration.md` (renamed conceptually to Game Integration)

**Confirmed Specifications (2026-01-10)**:
- **Framework change**: NitroGen → Mineflayer/Mindcraft
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
