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
  - LLM: Gemini designs the house (JSON blueprint validated by `HouseBlueprint`) and sets goals in a predicate vocabulary (`have(item, n)`, `built`, `placed(item, home)` (bed / crafting_table / furnace / chest in the home's room), `at_home`, `through_night`, `explored(distance)`, `cleared` (by day: fight what waits at the door), `stored(item, n)` (in the home's chests)); the house is designed only when there is no home yet (a home keeps its name and design across restarts); a goal the bridge rejects goes back with the reason
  - Recipes (`docs/design/29_recipes.md`): every vanilla 1.21.4 recipe and item tag is bundled in `minecraft-bridge/data-static/` (`npm run recipes:fetch`, from misode/mcmeta); `RecipeIndex` (`src/recipes.mjs`) expands tags and searches; `Knowledge` plans crafting (as `#tag` / `any:` groups; same-kind recipes like dyeing only from the white one) and smelting from it; tools `recipe_of` / `find_recipes` (also `t.find_recipes` in skills). Crafting itself goes through the server's recipe book (all recipes are given on RCON connect), so Gemini may name any craftable item it knows
  - Minecraft Bridge: judges the goal from the world (never from the models), decomposes it with a dependency solver over minecraft-data (recipes, drops, with corrections), grounds concrete candidates (dig this block, craft that item, ...) plus what the body needs, removes unsafe ones (nothing outside while sheltering; what is held back goes to the goal's `blocked`, and by day a wall exit is offered), and runs one bounded primitive (aborted on damage). A reflex handles nearby hostiles and keeps the bot afloat
  - World memory (`docs/design/14_world_memory.md`, bridge `memory.mjs`, saved in `state.json`): only what was seen and is out of view now, with when (places per 16x16 region, explored regions, deaths, chest contents as last opened). Recalled places are offered as trips before blind exploration, the solver takes stored items from a chest before gathering, and `/observe` summarises it for the prompts and the goal board
  - Surroundings (`minecraft-bridge/src/landmarks.mjs`): `observation.nearby` lists beds, doors, crafting tables, furnaces, chests and torches within 24 m (count, nearest with distance and direction, owner home / former_home / none), shown in `activity()` as 「近くにあるもの」. With no home yet, a door with a roofed, closed room behind it (≤ 100 cells) is adopted as the home (`adoptFoundBase`, named 見つけた拠点), so a base the bot did not build is used
  - Furniture (`minecraft-bridge/src/furniture.mjs`): placed beds, crafting tables, furnaces and chests can be picked up and put down again. For `placed(bed, home)` without a bed, a bed placed nearby outside the home is offered as `take the <bed> placed at …` (a dig) beside crafting one; the tool `move_furniture` (tools mode and skills) moves one, the home's furniture only inside the home, a bed only into the home, a chest with known contents is refused
  - Reflex (`docs/design/28_jev_reflex.md`, bridge `reflex.mjs` + `DangerWatcher`): the bridge starts at once with its rule (fight or flee) on a close hostile (5 m, creeper 7 m), on damage (the nearest hostile within 16 m, seen or not) or on a hostile walking toward the bot, and publishes `GET /danger` (the options possible now: fight / flee / go_home / keep_distance / ignore; inside the home only fight / ignore) with the shared state, landmarks, home distance and last hit. `DangerWatcher` (runs with `GameService.play`, every 0.25 s) asks Jev once per danger and switches via `POST /reflex`; late (0.8 s), unsure or failing Jev keeps the rule; `logs/reflex/*.jsonl`; `minecraft.reflex.judge: jev | rules`. Steps cut by damage or the reflex (`ActionResult.cut_by_attack`) are not failures or stalls of the small goal
  - Jev: picks one candidate per step (`Choice`), seeing the goal's progress and the body's needs (no priority order: measured in `spikes/primitive_choice_eval.py`)
  - `PlaySession` ends a goal when the streamer died and respawned (the bridge's `observation.deaths` count went up since the goal was set; checked first, always decided by Gemini with the screen), it is met, the mid goal it served ended, stuck, stalled (the remaining work stops going down), over budget, or the time of day changes
- **POV mirror** (`minecraft-bridge/src/mirror.mjs`): relays the server's packets to the viewer client; what the bot decides itself is not echoed by the server, so the mirror also sends the bot's `held_item_slot`, the player inventory (`window_items`, shortly after its `window_click`s) and closed windows, and both from the current state to late joiners
- **Tool control** (`minecraft.agent.control: tools`, `docs/design/19_gemini_tools.md`, `21_tool_control.md`; default `candidates` is the path above, kept as the baseline): within the same predicate small goals, Gemini calls one bridge tool per step by function calling (`POST /tool`: goto/dig/place/craft/…, `do_suggestion` runs a solver candidate, `find_blocks`/`recipe_of`/`how_to_get` are lookups). Action tools carry an `intent` (shown via `activity()`) and up to 3 English `watch` questions. While a tool runs, `ToolWatcher` reads `GET /state` (progress, a `look_around` height grid, mobs with ids, needs) every second and asks Jev all questions in one `system_one` call; two strong yeses stop / wake / report maybe-done via `POST /abort` (Jev never judges completion; the code's own progress question only records until it beats a rule). Safety stays in the bridge: sheltering refuses outside tools, the current home and the house being built are never dug (former homes can be). Ticks go to `logs/watch/*.jsonl`. Thinking depth per purpose: `gemini.thinking_levels`
- **Skills** (`docs/design/22_skills.md`, tools mode, `minecraft.agent.skills`): Gemini writes JS skills over the same tool API (`t.goto/dig/...`, `t.state()`, `t.block_at()`, `t.judge()` answered by Jev on the watcher's tick via `pending_judge` / `POST /judge`, `t.done/fail/log`; no `do_suggestion`) with `expects` (have/stored/built/placed/lit with a number, `+N` or `$param`, or `progress`) judged in the world. Tools `run_skill` / `write_skill`; `SkillWriter` (`purpose=skill_write`, thinking high, 3 tries, 3 writes per skill per hour) saves via `PUT /skills/<name>` and the new version is tried at once; failures (reason, log, last calls) are shown for the fix. The bridge runs skills in `isolated-vm` (`src/skills.mjs`: 180 s, 40 actions, 200 lookups, 10 judges, 1 s CPU, 32 MB; `/abort` and the reflex stop them; safety stays in the tools) and keeps versions and counts in `minecraft-bridge/data/skills/` (kept across world resets). `npm run skills:clean` (outside the stream) removes versions that never succeeded. `SkillLearnedEvent` is narrated at once and makes the avatar cheer
- **Screen vision** (`docs/design/23_screen_vision.md`): Gemini sees the stream screen as an image, captured by `ObsScreenCapture` (OBS WebSocket `GetSourceScreenshot` of the one source `obs.game_source` showing the POV client, never the whole scene; 3 s timeout, 60 s back-off, no capture → text only, the password never logged). `ScreenReviewer` (called from the step loop only): a periodic review every `minecraft.vision.review_interval_seconds` compares the screen with `activity()` and may `request_rethink` (ends the small goal like stuck); after a failure a screenshot goes with `goal_after_failure` / `tool_after_failure` (at most once per `failure_interval_seconds`); in tools mode `look_screen` attaches one to the next `choose_tool` of the same step. What Gemini reads from an image is shown as an unverified `screen_note`, never judges completion, never goes to Jev. Images go to Gemini with `media_resolution` (`gemini.media_resolution`, low)
- **Small goals by Jev** (`docs/design/26_steps_and_context.md`, `minecraft.agent.small_goals: jev`): when a mid goal reaches the top (or its steps fail, run out, Jev is unsure, or every `replan_minutes` if the list changed) Gemini's goal decision also writes `steps` (1–6 condition specs with a reason, `MidGoal.plan_steps`, saved); at other boundaries (met, 40-step budget, time change) `GoalChooser` asks Jev (`IFastJudge` Choice) to pick among the open steps (judged by `/check`) and survival goals (night/dusk/hunger, `mid_goal_id` None). `AdvancePlay._needs_gemini` returns why Gemini is called (logged); Jev's choices go to `logs/goals/*.jsonl`. `small_goals: gemini` keeps the old per-goal Gemini decision. Gemini usage lines carry `purpose=`/`cached=`; `tools/gemini_usage.py` sums a log per purpose; `include_thoughts` is off unless `--debug`; the periodic screen review is off (`review_interval_seconds: 0`). Prompts are cache-friendly (26 §4): each purpose's fixed rules go in `system_instruction` (`IGamePromptBuilder.build_goal_system()`, `IPromptBuilder.build_system_prompt(character, purpose)` with `commentary`/`reply`/`reply_requests`) and the body holds only the state, most stable first; chests show non-empty ones' top items, the goal decision sees the last 3 commentary lines; `TestPromptLength` guards the size
- **Failure diagnosis** (`docs/design/27_failure_diagnosis.md`): when a small goal is stuck or stalled, the same `goal_after_failure` call also gets the goal's action record (`AdvancePlay._goal_steps`, 12: action, confidence, result) and the last options offered (distance, been there, seen ago), and its schema puts `diagnosis` first, then `remedy` (retry / change) and `advice`. Code keeps the rules (`_check_remedy`): retrying the same kind of goal (`GoalSpec.same_kind`: numbers ignored) needs advice, change must be a different kind, and after 2 failures in a row of the same kind retry is not offered. `Goal.advice` goes to Jev's state (and the tool prompt via `activity()`), `Goal.diagnosis` is shown in `activity()` so the commentary can say why
- **Goal hierarchy** (`docs/design/13_goal_hierarchy.md`): mission (config `minecraft.mission`, never changed on stream) → mid goals (`MidGoalPlan`: prioritised list, done when their conditions `built`/`placed`/`have` hold, judged by the bridge's `POST /check` at small-goal boundaries; limits kept by code: 6 in the list, 2 viewers' at a time, one per viewer, a reply's request goes behind the current one unless Gemini accepts it `when: now` (then it goes first and the small goal is cut with `request_rethink`; 13 §13–14), Gemini's own plan edits may reorder anything including viewers' goals when it judges it better, 80-step budget). Crafting tables and furnaces may go inside the home (keeping the door line and room for a bed) or outside; a far table also offers crafting a new one here (13 §14) → the small goal (serves the top mid goal, or survival). Gemini edits the list with the small goal (add/move/drop with a reason); `MidGoalKeeper` applies edits atomically, saves (`data/mission.json`) and publishes MidGoal events
- **Coherent decisions** (`docs/design/12_coherent_decisions.md`, request flow replaced by 13): `PlaySession` alone owns the goals; its `activity()` (mission, mid goals, small goal and status, recent goals) is rendered by one formatter (`prompts/stream_context.py`) for the goal decision, the commentary and the chat replies, and the goal decision also sees the conversation. A chat reply and its handling of a request (none/accept/decline) come from one generation; an accepted request becomes a viewer's mid goal behind the current one (the small goal is not interrupted). `Narrator` says why goals change and never drops a promise silently
- **Goal board** (`presentation/web/goal_board.py`, extra `ailoveshen[stream]`): `GET /api/goals`, `/api/goals/stream` (SSE), `/overlay` (OBS browser source), `/overlay/vtuber` (the styled stream overlay: mission, mid goals with progress bars and viewer requests, the small goal as a speech bubble with a Japanese `label` and the `intent`, toasts when a mid goal clears; `?demo=1&pos=right&theme=mint|sky|lemon&scale=&compact=1&toast=0`), read-only in the play process (`examples/integration_test_minecraft.py --board-port 8765`). Debug: `GET /api/debug/gemini?limit=N` (the last Gemini calls, newest first: purpose, thinking level, thought summary with `gemini.include_thoughts`, output, tool calls, tokens, prompt; kept by `InMemoryGenerationLog`, shared by the game and chat generators) and `/debug/gemini` (a page that polls it; works as an OBS browser source); the screenshots attached to calls are served by id from `/api/debug/screen/<id>`
- **Avatar** (`docs/design/24_avatar.md`, `presentation/web/avatar.py` + `avatar.html`): the VRM (`avatar.model_path`, `models/vrm/ailoveshen.vrm`, VRoid VRM 1.0) at `/avatar` on the goal board (OBS browser source, transparent; three.js + three-vrm vendored in `presentation/web/vendor/`, served at `/vendor/`). `AvatarStage` turns domain events into cues on `/api/avatar/stream` (SSE): speak (text + length; the page makes mouth shapes from kana), quiet, emote (happy/sad/angry/surprised/relaxed + nod/cheer/flinch/tilt/wave). With `avatar.judge: jev` (and a TypeSafe key) `AvatarDirector` asks Jev in one `system_one` call for the expression, its strength and a gesture from the moment, the line and `activity()`; the rules above are the fallback (error, `judge_timeout_seconds`, low confidence); the mouth moves at once and the face follows; Jev vs rule records in `logs/avatar/*.jsonl` (24 §8). Lip sync from TTS playback events, or from generated text when there is no TTS (`avatar.lip_sync: auto|text|tts`); `?demo=1&view=full&pos=left|center|right&scale=`
- **Builds** (`docs/design/25_builds.md`): Gemini designs named builds (home extensions, sheds, towers, walls) as shapes (`BuildDesign`: fill / hollow_box / clear / door, planks/log/cobblestone/dirt, ≤ 3000 blocks and 48x16x48 for the streamer's own and town builds, ≤ 600 for a viewer's request) that the domain expands to blocks; the place is an anchor, never coordinates (`home:east|west|north|south` joins the home's wall, `near_home` finds flat ground, `map` + a cell like C7 of a top-down map image: bridge `GET /map`, drawn by `PilMapRenderer`, sent at `media_resolutions.build_design: medium`; code turns the cell into coordinates). A mid goal condition `built(name)` with a new name makes `MidGoalKeeper` call `BuildDesigner` first (checks materials with `/check`, registers with the bridge's `PUT /builds/<name>`, retries 3 times with the reason); a viewer's build widens its step budget. The bridge keeps `state.builds` apart from the house plan, refuses builds over the home's door/bed/chests/room/floor, lets `place_plan`/`dig`/`build_next` dig only the home wall cells a build marks `clear`, protects builds from mining, and grows the home's room (`home.cells`) when an extension completes enclosed
- **The stream** (`python -m ailoveshen.stream`, `src/ailoveshen/stream.py` → `factories/stream.py` → `presentation/services/stream.py`; runbook `docs/streaming.md`): runs until Ctrl-C with no step limit; `GameService.play(max_steps=None, keep_going=True)` logs a failed start or step and retries after 5 s doubling to 60 s (reset on success); refuses to start if the board port is taken or `stream.speak` and the TTS server is unreachable; a background task (board, chat) that ends unexpectedly is logged at ERROR while the play goes on. Twitch chat (`twitch.enabled` and `TWITCH_CHANNEL`) is read anonymously over IRC (`TwitchIrcChat`, `justinfan` nick, reconnects with back-off, 15 s connect timeout); `ChatResponder` answers one comment at a time (`twitch.response.min_interval_seconds`, keeps the newest `backlog`, skips `!` commands) through `LLMService.generate_response` with the session, spoken at HIGH priority. Goal board and avatar on `stream.board_port` (0: off); on stop the SSE streams get an end mark so uvicorn shuts down cleanly. `examples/integration_test_minecraft.py` stays as the test harness (step budget, scripted comments)

## Common Commands

Streaming runbook (setup, start order, OBS sources, stop, troubleshooting): `docs/streaming.md`. Keep it in sync when commands, ports, options or OBS URLs change.

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
python -m ailoveshen.stream [--control tools] [--no-speak] [--no-chat] [--no-board] [--debug]
# Minecraft (Phase 6): Paper server + bridge, then Gemini + Jev build a house autonomously
docker compose -f docker/docker-compose.minecraft.yml up -d
cd minecraft-bridge && npm install && npm start   # bot + POV mirror (client: 127.0.0.1:25578) + HTTP API (:3000)
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
│   ├── entities.py            # Entity, AggregateRoot, Conversation, MidGoalPlan, PlaySession
│   ├── value_objects.py       # EmotionState, SpeechRequest, SpeechResult, Position, FilterResult,
│   │                          # ConversationMessage, CharacterProfile, GenerationContext,
│   │                          # Minecraft: GoalPredicate, GoalSpec, GoalStatus, Candidate, HouseBlueprint, GameObservation,
│   │                          # Mission, MidGoal, ConditionStatus, Activity, ...
│   ├── events.py              # DomainEvent, Speech*Event, CommentaryGeneratedEvent, ChatResponseGeneratedEvent
│   └── exceptions.py          # AILoveShenError hierarchy
├── application/
│   ├── ports/input/           # ISpeakText, IGenerateCommentary, IGenerateResponse, IStartPlay, IAdvancePlay
│   ├── ports/output/          # IEventPublisher, ISpeechSynthesizer, IAudioPlayer, ITextGenerator, IPromptBuilder,
│   │                          # IMinecraftBridge, IActionSelector, IGamePromptBuilder, IMissionStore, IFastJudge, IWatchRecorder, IScreenCapture, IGenerationLog, IChatSource
│   ├── use_cases/             # SpeakTextUseCase, GenerateCommentaryUseCase, GenerateResponseUseCase,
│   │                          # StartPlayUseCase, AdvancePlayUseCase, MidGoalKeeper, goal_vocabulary (schemas),
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
│       ├── storage/           # JsonMissionStore (mid goals across restarts), JsonlWatchRecorder
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
