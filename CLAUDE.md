# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AILoveShen is an AI Streamer project for **Twitch** combining:
- **[Jev](https://typesafe.ai/)**: TypeSafe AI's System One model — picks the next primitive action among the candidates the [Mineflayer](https://github.com/PrismarineJS/mineflayer) Node.js bridge in `minecraft-bridge/` grounds for the current goal (replaces the earlier NitroGen plan; see `docs/design/06_phase6_jev_integration.md` and `docs/design/11_primitive_actions.md`)
- **Gemini 3.8 Flash** (`main_model`): Game commentary & thoughts (main), comment responses (sub/interrupt), and high-level Goal/direction decisions that get handed to Jev
- **Gemini 3.8 Flash** (`filter_model`, planned): Comment filtering (dynamic threshold based on volume)
- **Style-Bert-VITS2**: BERT-based TTS with emotional style control (JP/EN/ZH)
- **MCP (Model Context Protocol)**: Memory management, expression control, and extensibility

### Core Concept
- **Main loop**: Minecraft Bridge (Mineflayer) plays game → Gemini 3.8 Flash generates commentary/thoughts → TTS speaks
- **Sub loop**: Twitch comments → Gemini 3.8 Flash filters → Gemini 3.8 Flash responds (interrupts main)
- **Three-layer LLM → Jev → Minecraft Bridge link** (`GameService.play`; design: `docs/design/10_agent_lifecycle.md`, `11_primitive_actions.md`):
  - LLM: Gemini designs the house (JSON blueprint validated by `HouseBlueprint`) and sets goals in a predicate vocabulary (`have(item, n)`, `built`, `placed(bed, home)`, `at_home`, `through_night`, `explored(distance)`, `cleared` (by day: fight what waits at the door)); a goal the bridge rejects goes back with the reason
  - Minecraft Bridge: judges the goal from the world (never from the models), decomposes it with a dependency solver over minecraft-data (recipes, drops, with corrections), grounds concrete candidates (dig this block, craft that item, ...) plus what the body needs, removes unsafe ones (nothing outside while sheltering; what is held back goes to the goal's `blocked`, and by day a wall exit is offered), and runs one bounded primitive (aborted on damage). A reflex handles nearby hostiles and keeps the bot afloat
  - Jev: picks one candidate per step (`Choice`), seeing the goal's progress and the body's needs (no priority order: measured in `spikes/primitive_choice_eval.py`)
  - `PlaySession` ends a goal when it is met, stuck, stalled (the remaining work stops going down), over budget, or the time of day changes

## Common Commands

### Setup
```bash
pip install -r requirements.txt
python initialize.py  # Downloads BERT models and pretrained weights
pip install -e ".[all]"  # AILoveShen core + extras (dev, tts, llm=google-genai)
export GEMINI_API_KEY=...  # Required for the LLM (Phase 3)
```

### Web UI & Servers
```bash
python app.py                           # Main Gradio WebUI (training + inference)
python server_editor.py --inbrowser     # Editor server with browser
python server_fastapi.py                # REST API server (port 5000)
```

### Training Pipeline
```bash
# 1. Slice audio files (2-12 sec segments)
python slice.py --model_name <name>

# 2. Transcribe audio (uses Whisper)
python transcribe.py --model_name <name>

# 3. Preprocess (normalize text, generate BERT features)
python preprocess_all.py -m <model_name> --use_jp_extra

# 4. Train
python train_ms_jp_extra.py  # JP-Extra model (recommended for Japanese)
python train_ms.py           # Standard multi-language model

# 5. Generate style vectors
python style_gen.py -m <model_name>
```

### Testing
```bash
# AILoveShen Core tests
pytest tests/                   # Run all unit tests
python examples/demo_phase1.py  # Demo Phase 1 components
python examples/demo_phase2.py  # Demo Phase 2 TTS pipeline
python examples/demo_phase3.py  # Demo Phase 3 LLM conversation (fake generator, no API key)
GEMINI_API_KEY=... python examples/integration_test_llm.py [--speak]  # Real Gemini API (+ TTS)
# Minecraft (Phase 6): Paper server + bridge, then Gemini + Jev build a house autonomously
docker compose -f docker/docker-compose.minecraft.yml up -d
cd minecraft-bridge && npm install && npm start   # bot + POV mirror (client: 127.0.0.1:25578) + HTTP API (:3000)
GEMINI_API_KEY=... TYPESAFE_API_KEY=... python examples/integration_test_minecraft.py [--max-steps 300]

# Style-Bert-VITS2 tests (legacy)
hatch run test:test          # PyTorch CPU tests
hatch run test:test-cuda     # PyTorch GPU tests
hatch run test-onnx:test     # ONNX CPU tests
hatch run style:check        # Check code formatting
hatch run style:fmt          # Auto-format code
```

### Model Conversion
```bash
python convert_onnx.py       # Convert to ONNX format
python convert_bert_onnx.py  # Convert BERT models to ONNX
```

## Architecture

### AILoveShen (`src/ailoveshen/`)
Clean Architecture with the four layers at the top level. Features (TTS, LLM, Twitch, ...) are spread across the layers; there are no per-feature packages. Layout and dependency rules: `docs/design/00_architecture_overview.md` §2.5.

```
src/ailoveshen/
├── domain/                    # No external dependencies
│   ├── entities.py            # Entity, AggregateRoot, Conversation, PlaySession
│   ├── value_objects.py       # EmotionState, SpeechRequest, SpeechResult, Position, FilterResult,
│   │                          # ConversationMessage, CharacterProfile, GenerationContext,
│   │                          # Minecraft: GoalPredicate, GoalSpec, GoalStatus, Candidate, HouseBlueprint, GameObservation, ...
│   ├── events.py              # DomainEvent, Speech*Event, CommentaryGeneratedEvent, ChatResponseGeneratedEvent
│   └── exceptions.py          # AILoveShenError hierarchy
├── application/
│   ├── ports/input/           # ISpeakText, IGenerateCommentary, IGenerateResponse, IStartPlay, IAdvancePlay
│   ├── ports/output/          # IEventPublisher, ISpeechSynthesizer, IAudioPlayer, ITextGenerator, IPromptBuilder,
│   │                          # IMinecraftBridge, IActionSelector, IGamePromptBuilder
│   ├── use_cases/             # SpeakTextUseCase, GenerateCommentaryUseCase, GenerateResponseUseCase,
│   │                          # StartPlayUseCase, AdvancePlayUseCase
│   └── dto/                   # speech_dto, llm_dto, game_dto
├── infrastructure/
│   ├── config.py              # Settings (default.yaml → {env}.yaml → env vars), GeminiSettings, CharacterSettings, JevSettings, MinecraftSettings
│   ├── logging.py             # Loguru structured logging
│   ├── events.py              # AsyncEventBus (Pub/Sub)
│   └── adapters/
│       ├── tts/               # StyleBertVits2Client, EmotionStyleService, VoiceConfig
│       ├── audio/             # SounddevicePlayer
│       ├── gemini/            # GeminiTextGenerator (google-genai; text + JSON structured output)
│       ├── jev/               # JevActionSelector (typesafe-sdk)
│       ├── minecraft_bridge/  # MineflayerBridgeClient (HTTP to minecraft-bridge/)
│       └── prompts/           # PromptTemplateBuilder, GamePromptTemplateBuilder
├── presentation/
│   └── services/              # TTSService (priority queue), LLMService, GameService
└── factories/                 # Composition Roots (tts.py, llm.py, game.py)

minecraft-bridge/              # Node sidecar: goals, solver, candidates, primitives, reflex, POV mirror,
                               # HTTP API (goal/observe/act/build-plan); tests: npm test
```

**Dependency rule** (enforced by `tests/unit/test_architecture.py`): dependencies point inward only. domain imports no other layer; application must not import infrastructure/presentation; only `factories/` wires everything together.

**Key Classes:**
- `Entity` / `AggregateRoot`: auto-generated UUID, UTC timestamps, equality by ID, domain event collection
- `EmotionState`, `SpeechRequest`, `FilterResult`: Immutable value objects
- `SpeakTextUseCase`: Core TTS orchestration; speaks in `EmotionState`, never in engine style names
- `StyleBertVits2Client`: Maps `EmotionState` → Style-Bert-VITS2 style via `EmotionStyleService`
- `TTSService`: Priority queue-based speech service
- `GenerateCommentaryUseCase` / `GenerateResponseUseCase`: Commentary and chat replies sharing one `Conversation`
- `GeminiTextGenerator`: google-genai adapter; `thinking_level` per slot (no temperature on 3.8), SDK retry on 408/429/5xx, 1s rate limit, token usage logging
- `LLMService`: Returns commentary/replies as strings (empty on failure), holds current emotion
- `AsyncEventBus`: Thread-safe async event publisher/subscriber
- `Settings`: Hierarchical configuration

### Core Library (`style_bert_vits2/`)
- `tts_model.py`: Main `TTSModel` and `TTSModelHolder` classes for inference
- `models/`: Neural network modules (`SynthesizerTrn`, `SynthesizerTrnJPExtra`)
- `nlp/`: Text processing per language (Japanese uses pyopenjtalk, English uses g2p_en)

### Training Scripts (root)
- `train_ms.py` / `train_ms_jp_extra.py`: Multi-GPU training with DDP
- `bert_gen.py`: BERT feature extraction (runs as subprocess for memory isolation)
- `data_utils.py`: Dataset loading and audio preprocessing

### Inference Servers
- `server_fastapi.py`: REST API with `/voice` endpoint
- `server_editor.py`: Editor UI backend (serves `static/` Next.js app)

### Data Flow
```
Raw Audio → slice.py → Segments → transcribe.py → Text
    → preprocess_text.py → Normalized text
    → bert_gen.py → BERT features
    → train_ms.py → Model weights
    → style_gen.py → Style vectors
```

### Model Assets Structure
```
model_assets/{model_name}/
├── config.json           # Hyperparameters, spk2id, style2id
├── *.safetensors         # Model weights
└── style_vectors.npy     # Speaker/style embeddings
```

## Key Classes

**TTSModel**: Load and run inference
- `infer(text, language, speaker_id, style, ...)` → audio waveform

**TTSModelHolder**: Manage multiple models
- `get_model(name)` → TTSModel instance

## Configuration

### AILoveShen Core
- `config/default.yaml`: Default settings for all environments
- `config/development.yaml`: Development overrides
- Environment variables: `${VAR}` or `${VAR:-default}` syntax supported
- `APP_ENV`: Environment name (defaults to "development")

### Style-Bert-VITS2
- `configs/paths.yml`: Dataset and asset paths
- `default_config.yml`: Training hyperparameters template
- Per-model `config.json`: Generated during preprocessing

## Languages

- `JP`: Japanese (pyopenjtalk + deberta-v2-large-japanese)
- `EN`: English (g2p_en + deberta-v3-large)
- `ZH`: Chinese (pypinyin + chinese-roberta-wwm-ext-large)

## Work Log Guidelines

**IMPORTANT**: Always maintain `docs/WORKLOG.md` to enable smooth resumption after context loss.

### When to Update Work Log

1. **Session Start**: Read WORKLOG.md to understand current status
2. **Task Completion**: Record completed work with:
   - Files created/modified
   - Key design decisions
   - Commit hash
3. **Session End**: Update "Current Status" and "Next Steps"
4. **Blockers/Issues**: Document any unresolved problems

### Work Log Structure

```markdown
## Current Status
- Active phase and last updated date

## Completed Work
- Phase/feature name, date, issue number
- List of implemented components with file paths
- Key design decisions made
- Commit hash

## Next Steps
- Planned work items

## Session Notes
- Daily notes with date headers
```

### Resume Checklist

1. Read `docs/WORKLOG.md`
2. Check GitHub Issues (`gh issue list`)
3. Review design docs in `docs/design/`
4. Run tests: `pytest tests/`
5. Run demo if available: `python examples/demo_phase1.py`
