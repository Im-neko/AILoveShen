# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AILoveShen is an AI Streamer project for **Twitch**. Components:
- **[Jev](https://typesafe.ai/)** (TypeSafe AI's System One): picks the next primitive action among the candidates the Mineflayer bridge (`minecraft-bridge/`) grounds for the current goal (`docs/design/06_phase6_jev_integration.md`, `11_primitive_actions.md`)
- **Gemini 3.8 Flash** (`main_model`): game commentary and thoughts, chat replies, and the high-level goal decisions handed to Jev; `filter_model` (comment filtering) is planned
- **Local LLM** (optional, `llm.local.enabled`): OpenAI-compatible server for everything except calls with images (`docs/design/36_local_llm.md`)
- **Style-Bert-VITS2** (JP/EN/ZH TTS with style control) or **Irodori-TTS** (`tts.engine: irodori`, voice cloned from reference audio; `docs/setup/irodori_tts.md`, design 32)
- **MCP**: memory management, expression control, extensibility

### Core Concept
- **Main loop**: Minecraft bridge plays → Gemini generates commentary / thoughts → TTS speaks
- **Sub loop**: Twitch comments → triage (Jev) → Gemini reply (interrupts main) → TTS
- **LLM → Jev → bridge** (`GameService.play`; `docs/design/10_agent_lifecycle.md`, `11_primitive_actions.md`): the LLM sets goals in a predicate vocabulary (`have`, `built`, `placed`, `stored`, `facility`, `farmed`, …); the bridge judges them from the world (never from the models), decomposes them with a dependency solver, grounds candidates and runs one primitive per step; Jev (or the LLM in tools mode) chooses among the candidates. Safety (reflex, sheltering, protected blocks) stays in the bridge.
- **Goal layers** (`docs/design/50_goal_layers.md`): mission → objectives → jobs → small goal (the next step of a job, or a survival goal).
- **Control mode**: `minecraft.agent.control: tools` (default since 2026-09-26; Gemini calls bridge tools, Jev first via `StepPicker`); `candidates` (Jev picks the bridge's candidate) is kept for comparison.
- **Memory of the system**: learned candidates (one tool call that worked), skills (multi-step JS), lessons (text advice), playbook (fixed order of life infrastructure, in settings).

### Feature reference
The per-feature details (what each does, its settings, logs and tests, with dates and what is unverified on the real server) are in **`docs/feature_reference.md`**; reasons and history are in `docs/design/`. Read the relevant entry there before changing a feature. Where to look:

| Topic | `docs/feature_reference.md` section | Main design docs |
|---|---|---|
| Goal predicates, solver, recipes, world memory, death recovery, food / torches / night / doors / boats / leveling / farming / stuck walking / reflex | 3 層 | 10, 11, 14, 29, 33, 37–39, 41, 45, 50, 69, 84, 86–89, 94–99 |
| POV mirror, tool control, Jev first, learned candidates, lessons, search tools, experiments when stuck | 視点ミラー・エージェント制御 | 19, 21, 34, 35, 47, 52, 53, 85 |
| Infrastructure, playbook, tidying, spawn proofing, windfall, play report, benchmark, swimming, facilities, town map | 生活インフラ・安全・計測 | 47, 72–80, 82, 83 |
| Local LLM, skills, screen vision | ローカル LLM・スキル・画面 | 22, 23, 36 |
| Small goals, bundles, failure diagnosis, town stages, objective plans, plan linting | 目標の階層・計画 | 15, 26, 27, 31, 44, 50, 67 |
| Liquid / trees, leftover logs, sleepers, goto through rock, stuck-walk report, world map from sight | ブリッジの世界の扱い | 55–60 |
| Chat flow, viewer advice, decision latency, admin page, goal board, avatar, builds, live narration, burrow, mining, speech, small talk, readings, support thanks, the stream | チャット・配信・アバター | 12, 24, 25, 30, 40, 43, 49, 51, 54, 61, 65, 68, 71, 81, 91, 92, 93 |

When you add or change a feature: update its entry in `docs/feature_reference.md` (new feature → new bullet in the matching section), add or update the tool's entry in `src/ailoveshen/infrastructure/adapters/prompts/abilities.yaml` (a test checks the tools named exist and appear in `how`, and that `see` keys exist), and write the reasoning in `docs/design/`. Keep CLAUDE.md to what every session needs.

## Common Commands

Streaming runbook (setup, start order, OBS sources, stop, troubleshooting): `docs/streaming.md`. Rebuild the world and the bot's memory together: `mise run world:reset -- [--dry-run|--level NAME|--seed SEED|random|--existing|--yes]` (`scripts/reset_world.sh`; stop the stream and the bridge first, it refuses otherwise). It sets aside everything that is about the old world with a dated name (`state.json`, `skills/`, `mission.json`, `notes.json`, `moves.json`) and keeps only what does not depend on the world: lessons, what she said about herself, name readings. Compare local LLMs from the main log (per model run: step failure rate, schema rewrites, goal-decision send-backs, speed, goal outcomes; environment failures such as `ConnectTimeout` are counted apart): `python tools/model_compare.py [--since DATE] [--merge] [--min-calls N]` (2026-10-04; code changes take effect only at a restart, so cut with `--since "<last restart that changed code>"`; LLM outages are left out automatically, `--exclude FROM TO` leaves out other stalls). Measure a change with repeatable tasks apart from the stream (own server :25566 and bridge :3001; stop the stream first): `mise run bench -- [--dry-run|--tasks food,pickaxe,house|--repeats N|--cap-minutes M]`, `report`, `compare` (`tools/benchmark.py`, design 83; accident reproductions run only when named: `--tasks night,pond,cage`). Every LLM call is also written whole to `logs/generations/` (design 90): `python tools/replay_decisions.py` (recorded outputs through today's validators), `tools/prompt_budget.py` (prompt size by section), `tools/refusal_report.py` (refused steps by cause), `tools/unverified.py` (WORKLOG entries still 「未確認」; mark a verified one 「確認済み（日付）」 in its heading). Keep it in sync when commands, ports, options or OBS URLs change.

### Setup
```bash
pip install -r requirements.txt  # AILoveShen core + all extras + the package itself (-e .); same as pip install -e ".[all]"
# Keep requirements.txt and pyproject.toml in sync. On Linux, sounddevice needs PortAudio (apt install libportaudio2).
# The Style-Bert-VITS2 TTS server runs in Docker (docker/Dockerfile.tts-server), not from this requirements.txt.
cp .env.example .env  # then fill in GEMINI_API_KEY, TYPESAFE_API_KEY, ... (.env is git-ignored)
# .env at the repo root is read by load_settings (Python) and by the bridge (minecraft-bridge/src/env.mjs);
# variables exported in the shell win. Docker: docker compose --env-file .env -f docker/<file>.yml up -d
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
# The stream (docs/streaming.md): Docker servers + bridge first, then
python -m ailoveshen.stream [--control candidates] [--no-speak] [--no-chat] [--no-board] [--debug]
# Irodori-TTS instead of Style-Bert-VITS2 (TTS_ENGINE=irodori): scripts/irodori/start_mac.sh; compare: python tools/tts_compare.py
# Minecraft (Phase 6): Paper server + bridge, then Gemini + Jev build a house autonomously
docker compose -f docker/docker-compose.minecraft.yml up -d
cd minecraft-bridge && npm install && npm start   # bot + POV mirror (client: 127.0.0.1:25578) + HTTP API (:3000)
# while developing: npm run dev (restarts on changes under src/; state saved, bot rejoins; the POV client must reconnect)
GEMINI_API_KEY=... TYPESAFE_API_KEY=... python examples/integration_test_minecraft.py [--max-steps 300] [--comments c.json] [--board-port 8765] [--control tools] [--speak]

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
│   ├── entities.py            # Entity, AggregateRoot, Conversation, ObjectivePlan, PlaySession
│   ├── value_objects.py       # EmotionState, SpeechRequest, SpeechResult, Position, FilterResult,
│   │                          # ConversationMessage, CharacterProfile, GenerationContext,
│   │                          # Minecraft: GoalPredicate, GoalSpec, GoalStatus, Candidate, HouseBlueprint, GameObservation,
│   │                          # Mission, Objective, ConditionStatus, Activity, ...
│   ├── events.py              # DomainEvent, Speech*Event, CommentaryGeneratedEvent, ChatResponseGeneratedEvent
│   └── exceptions.py          # AILoveShenError hierarchy
├── application/
│   ├── ports/input/           # ISpeakText, IGenerateCommentary, IGenerateResponse, IStartPlay, IAdvancePlay
│   ├── ports/output/          # IEventPublisher, ISpeechSynthesizer, IAudioPlayer, ITextGenerator, IPromptBuilder,
│   │                          # IMinecraftBridge, IActionSelector, IGamePromptBuilder, IMissionStore, IFastJudge, IWatchRecorder, IScreenCapture, IGenerationLog, IChatSource
│   ├── use_cases/             # SpeakTextUseCase, GenerateCommentaryUseCase, GenerateResponseUseCase,
│   │                          # StartPlayUseCase, AdvancePlayUseCase, ObjectiveKeeper, goal_vocabulary (schemas),
│   │                          # tool_catalog (tools for Gemini), ToolWatcher (Jev answers watch questions),
│   │                          # ScreenReviewer (when Gemini sees the stream screen)
│   │                          # AvatarDirector (Jev picks the avatar's expression and gesture)
│   └── dto/                   # speech_dto, llm_dto, game_dto
├── infrastructure/
│   ├── config.py              # Settings (default.yaml → {env}.yaml → env vars), GeminiSettings, CharacterSettings, JevSettings, MinecraftSettings
│   ├── logging.py             # Loguru structured logging
│   ├── events.py              # AsyncEventBus (Pub/Sub)
│   └── adapters/
│       ├── tts/               # StyleBertVits2Client, EmotionStyleService, VoiceConfig
│       ├── audio/             # SounddevicePlayer
│       ├── gemini/            # GeminiTextGenerator (google-genai; text + JSON structured output)
│       ├── jev/               # JevActionSelector, JevFastJudge (typesafe-sdk)
│       ├── minecraft_bridge/  # MineflayerBridgeClient (HTTP to minecraft-bridge/)
│       ├── obs/               # ObsScreenCapture (obsws-python; screenshots of the game source)
│       ├── twitch/            # TwitchIrcChat (anonymous read-only IRC)
│       ├── storage/           # JsonMissionStore (objectives across restarts), JsonlWatchRecorder
│       └── prompts/           # PromptTemplateBuilder, GamePromptTemplateBuilder, stream_context
├── presentation/
│   ├── services/              # TTSService (priority queue), LLMService, GameService, Narrator, ChatResponder, Stream
│   └── web/                   # GoalBoard (FastAPI: /api/goals, SSE, /overlay)
├── factories/                 # Composition Roots (tts.py, llm.py, game.py, avatar.py, stream.py)
└── stream.py                  # python -m ailoveshen.stream (the stream's entry point)

minecraft-bridge/              # Node sidecar: goals, solver, candidates, primitives, reflex, POV mirror,
                               # world memory, chests, tools, shared state, HTTP API
                               # (goal/check/observe/act/tool/state/abort/build-plan/skills); tests: npm test
```

**Dependency rule** (enforced by `tests/unit/test_architecture.py`): dependencies point inward only. domain imports no other layer; application must not import infrastructure/presentation; only `factories/` wires everything together.

**Key Classes:**
- `Entity` / `AggregateRoot`: auto-generated UUID, UTC timestamps, equality by ID, domain event collection
- `EmotionState`, `SpeechRequest`, `FilterResult`: Immutable value objects
- `SpeakTextUseCase`: Core TTS orchestration; speaks in `EmotionState`, never in engine style names
- `StyleBertVits2Client`: Maps `EmotionState` → Style-Bert-VITS2 style via `EmotionStyleService`
- `TTSService`: Priority queue-based speech service
- `GenerateCommentaryUseCase` / `GenerateResponseUseCase`: Commentary and chat replies sharing one `Conversation`
- `GeminiTextGenerator`: google-genai adapter; `thinking_level` per purpose (`purpose=` on every call, table in `gemini.thinking_levels`; no temperature on 3.8), `choose_tool` (function calling, mode ANY), SDK retry on 408/429/5xx, 1s rate limit, token usage logging
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
- Environment variables: `${VAR}` or `${VAR:-default}` syntax supported; `.env` at the repo root is loaded first (`.env.example` lists them)
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
