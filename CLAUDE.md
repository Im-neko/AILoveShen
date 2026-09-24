# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AILoveShen is an AI Streamer project for **Twitch** combining:
- **[Jev](https://typesafe.ai/)** (planned): TypeSafe AI's System One model — fast, typed, real-time decision-making for Minecraft control, via a [Mineflayer](https://github.com/PrismarineJS/mineflayer) Node.js bridge (replaces the earlier NitroGen plan; see `docs/design/06_phase6_jev_integration.md`)
- **Gemini 3.8 Flash** (`main_model`): Game commentary & thoughts (main), comment responses (sub/interrupt), and high-level Goal/direction decisions that get handed to Jev
- **Gemini 3.8 Flash** (`filter_model`, planned): Comment filtering (dynamic threshold based on volume)
- **Style-Bert-VITS2**: BERT-based TTS with emotional style control (JP/EN/ZH)
- **MCP (Model Context Protocol)**: Memory management, expression control, and extensibility

### Core Concept
- **Main loop**: Minecraft Bridge (Mineflayer) plays game → Gemini 3.8 Flash generates commentary/thoughts → TTS speaks
- **Sub loop**: Twitch comments → Gemini 3.8 Flash filters → Gemini 3.8 Flash responds (interrupts main)
- **Three-layer LLM → Jev → Minecraft Bridge link**:
  - LLM → Jev: high-level direction — Gemini picks a `Goal` (from a closed set of 13) and hands it to Jev as a request
  - Jev → Minecraft Bridge: Jev makes fast typed decisions within that Goal — a Reactive Loop (~600ms, survival reflexes, no LLM involved) and a Tactical Loop (~10s, next-step selection within the current Goal)
  - Minecraft Bridge → LLM/Jev: Game State Manager feeds position, inventory, surroundings, and events back to both

## Common Commands

### Setup
```bash
pip install -r requirements.txt
python initialize.py  # Downloads BERT models and pretrained weights
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

### AILoveShen Core (`src/ailoveshen/core/`)
Clean Architecture implementation with Domain-Driven Design patterns.

```
src/ailoveshen/core/
├── domain/                    # Domain Layer (no external dependencies)
│   ├── entities.py            # Entity, AggregateRoot base classes
│   └── value_objects.py       # EmotionState, SpeechRequest, Position, etc.
├── application/               # Application Layer
│   └── ports/
│       └── output_ports.py    # IEventPublisher, IEventSubscriber interfaces
└── infrastructure/            # Infrastructure Layer
    ├── config.py              # YAML config with env var expansion
    ├── logging.py             # Loguru structured logging
    └── events.py              # AsyncEventBus (Pub/Sub)
```

**Key Classes:**
- `Entity`: Base class with auto-generated UUID, UTC timestamps, equality by ID
- `AggregateRoot`: Entity with domain event collection
- `EmotionState`, `SpeechRequest`, `FilterResult`: Immutable value objects
- `AsyncEventBus`: Thread-safe async event publisher/subscriber
- `Settings`: Hierarchical configuration (default.yaml → {env}.yaml → env vars)

### AILoveShen TTS (`src/ailoveshen/tts/`)
Clean Architecture TTS pipeline for Style-Bert-VITS2 integration.

```
src/ailoveshen/tts/
├── domain/                    # Domain Layer
│   ├── value_objects.py       # SpeechResult, SpeechStatus, VoiceConfig
│   ├── events.py              # SpeechStartedEvent, SpeechCompletedEvent
│   └── services/              # EmotionStyleService
├── application/               # Application Layer
│   ├── ports/                 # ISpeakText, ISpeechSynthesizer, IAudioPlayer
│   ├── use_cases/             # SpeakTextUseCase
│   └── dto/                   # SpeakTextRequest, SpeakTextResponse
├── infrastructure/            # Infrastructure Layer
│   └── adapters/
│       ├── tts/               # StyleBertVits2Client
│       └── audio/             # SounddevicePlayer
├── presentation/              # Presentation Layer
│   └── services/              # TTSService (queue management)
└── factory.py                 # Composition Root
```

**Key Classes:**
- `TTSService`: Priority queue-based speech service
- `SpeakTextUseCase`: Core TTS orchestration logic
- `EmotionStyleService`: Emotion→Style mapping
- `StyleBertVits2Client`: HTTP client for TTS server
- `SounddevicePlayer`: Audio playback adapter

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
