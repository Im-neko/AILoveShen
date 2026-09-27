# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AILoveShen is an AI Streamer project for **Twitch** combining:
- **[Jev](https://typesafe.ai/)**: TypeSafe AI's System One model — picks the next primitive action among the candidates the [Mineflayer](https://github.com/PrismarineJS/mineflayer) Node.js bridge in `minecraft-bridge/` grounds for the current goal (replaces the earlier NitroGen plan; see `docs/design/06_phase6_jev_integration.md` and `docs/design/11_primitive_actions.md`)
- **Gemini 3.8 Flash** (`main_model`): Game commentary & thoughts (main), comment responses (sub/interrupt), and high-level Goal/direction decisions that get handed to Jev
- **Gemini 3.8 Flash** (`filter_model`, planned): Comment filtering (dynamic threshold based on volume)
- **Style-Bert-VITS2**: BERT-based TTS with emotional style control (JP/EN/ZH)
- **Irodori-TTS** (optional, `tts.engine: irodori` / `TTS_ENGINE`): Japanese TTS cloning the voice from reference audio without training (`IrodoriTtsClient` → Irodori-TTS-Server `POST /v1/audio/speech` on :8088; on the Mac it runs natively for MPS, not in Docker: `scripts/irodori/setup_mac.sh` / `start_mac.sh`; reference clips from the Style-Bert-VITS2 training data by `tools/irodori_voice.py`; `tools/tts_compare.py` compares engines; fixed `seed`, emotion captions off by default; `tools/irodori_from_sbv2.py` makes a clean reference voice or a LoRA (`tts.irodori.lora_adapter`) from Style-Bert-VITS2's readings of the recording script, and per-emotion voices (`emotions`: Irodori-TTS reads the script's emotion lines with captions → `tts.irodori.emotion_voices`, and `tools/sbv2_add_styles.py` adds them as Style-Bert-VITS2 styles keeping Neutral; `tts.synthesis.style_weight`); `docs/setup/irodori_tts.md`, design 32)
- **MCP (Model Context Protocol)**: Memory management, expression control, and extensibility

### Core Concept
- **Main loop**: Minecraft Bridge (Mineflayer) plays game → Gemini 3.8 Flash generates commentary/thoughts → TTS speaks
- **Sub loop**: Twitch comments → Gemini 3.8 Flash filters → Gemini 3.8 Flash responds (interrupts main)
- **Three-layer LLM → Jev → Minecraft Bridge link** (`GameService.play`; design: `docs/design/10_agent_lifecycle.md`, `11_primitive_actions.md`):
  - LLM: Gemini designs the house (JSON blueprint validated by `HouseBlueprint`) and sets goals in a predicate vocabulary (`have(item, n)`, `built`, `placed(item, where, count)` (any placeable item: the home's furniture bed / crafting_table / furnace / chest in its room, or anything (torch, lantern, …) `where` = `home` / `near_home` / `build:<name>` / `x,y,z`, counted in that area from the world: bridge `placing.mjs`, leaf `place_at` with the face to place against (`supportFor`: torches, signs, banners and ladders go on a wall first, in the home at floor + 1; never against chests, tables, doors or beds, which would open); the tool `place {item, x, y, z, on?: floor|wall|ceiling}` may put anything in the home's room except the doorway column and the bed's room), `at_home`, `through_night`, `explored(distance)`, `cleared` (by day: fight what waits at the door), `stored(item, n)` (in the home's chests)); the house is designed only when there is no home yet (a home keeps its name and design across restarts); a goal the bridge rejects goes back with the reason
  - Recipes (`docs/design/29_recipes.md`): every vanilla 1.21.4 recipe and item tag is bundled in `minecraft-bridge/data-static/` (`npm run recipes:fetch`, from misode/mcmeta); `RecipeIndex` (`src/recipes.mjs`) expands tags and searches; `Knowledge` plans crafting (as `#tag` / `any:` groups; same-kind recipes like dyeing only from the white one) and smelting from it; tools `recipe_of` / `find_recipes` (also `t.find_recipes` in skills). Crafting itself goes through the server's recipe book (all recipes are given on RCON connect), so Gemini may name any craftable item it knows
  - Minecraft Bridge: judges the goal from the world (never from the models), decomposes it with a dependency solver over minecraft-data (recipes, drops, with corrections), grounds concrete candidates (dig this block, craft that item, ...) plus what the body needs, removes unsafe ones (nothing outside while sheltering; what is held back goes to the goal's `blocked`, and by day a wall exit is offered), and runs one primitive (aborted on damage). A reflex handles nearby hostiles and keeps the bot afloat
  - World memory (`docs/design/14_world_memory.md`, bridge `memory.mjs`, saved in `state.json`): only what was seen and is out of view now, with when (places per 16x16 region, explored regions, deaths, chest contents as last opened). Recalled places are offered as trips before blind exploration, the solver takes stored items from a chest before gathering, and `/observe` summarises it for the prompts and the goal board
  - Surroundings (`minecraft-bridge/src/landmarks.mjs`): `observation.nearby` lists beds, doors, crafting tables, furnaces, chests and torches within 24 m (count, nearest with distance and direction, owner home / former_home / none), shown in `activity()` as 「近くにあるもの」. With no home yet, a door with a roofed, closed room behind it (≤ 100 cells) is adopted as the home (`adoptFoundBase`, named 見つけた拠点), so a base the bot did not build is used
  - Planting and farming (`docs/design/33_planting_and_farming.md`, bridge `farming.mjs`): `planted(sapling|kind, n)` counts saplings or grown trees where the bot planted (`state.plantings`), `farmed(crop, n)` crops on farmland near the home (wheat / carrots / potatoes / beetroots); both are conditions and skill expects. Tools and primitives `plant` / `till` / `sow` (the bridge picks a spot 6–24 m from the home when none is given; never on or next to the home or builds); `dig` also takes grass, crops and saplings. Saplings come from leaves and seeds from grass (chance drops: nothing dropped is not a failure). Crops are gathering sources (`NATURAL_BLOCKS`), only ripe ones are dug (`isRipe`; the tool `dig` refuses unripe ones), a ripe crop's `dig` harvests the ripe ones within 5 m and replants them with the seeds picked up; world memory remembers crops; `/observe` gives `farm` (per crop: planted, ripe → 「家のまわりの畑」) and each remembered place's `gives` (`Knowledge.dropsOf`: what the block drops, e.g. wheat → wheat, wheat_seeds); `find_blocks` marks crops `ripe` / `age`. The procedure is left to Gemini's skills in tools mode; candidates mode has the basic plant / sow / till steps
  - Gathering: `stored(item, n)` carries items home in batches (deposit only at 32 carried, enough to finish, a nearly full inventory, near the home, nothing left to gather here, or dusk/night), and every goal is sent with `also` (items the other mid goals and the current steps gather: have / stored / planted → saplings / farmed → seeds): gather candidates for them within 8 m are offered by day, marked `also` (a tree gives logs and, from its leaves, saplings); such side work is not counted as a stall
  - Food: cooked food is eaten first; raw meat only when starving (food ≤ 6) or when nothing can cook it (≤ 12); raw meat is cooked at a visible furnace or walked to the last seen one (`state.lastFurnace`, 3+ pieces). Chance-drop digging (grass for seeds, leaves for saplings) sweeps the same kind within 5 m in one action until something drops, and digs plants within reach without walking
  - Night (`through_night`, no home needed): inside the home, sleep in its bed; outside, go home, sleep in a bed within 32 m that is not the home's (`sleep_at`, e.g. a village), place a carried bed and sleep (`bed_here`, the bed stays and sets the spawn), or stay underground (`observation.underground`: covered and below y 60; the survival goals for night/dusk are not offered there). Jev picks among them; `sleep_at` walks to the bed like any walk (doors are passable, below), beds in the home or former homes are never offered, a bed or the home's doorstep that got "No path" is not offered for 5 minutes (`markNoPath` / `isNoPathTo` in `move.mjs`; the goal's `blocked` says so), `bed_here` is offered beside `sleep_at`, and `enterHome` falls back to standing next to the doorstep and reports the doorstep's blocks when there is no path. Night fighting: `cleared` is allowed at night with a sword or axe and health ≥ 14 (without a weapon it is refused); with 8+ other items it first offers `stash` (put everything but weapons, armor, food and torches into the home's chest), then the attacks; tools may go out at night only while the small goal is `cleared`; inside the home at night with hostiles near, `through_night` says they keep the bot awake, and `GoalChooser` offers `cleared` as a survival option when armed
  - No time limits (`docs/design/38_no_time_limits.md`, user 2026-09-27: 「基本制限は不要。必要になったら野営」): no action is stopped by time (walks go all the way in 48 m legs via `legToward`, boats row to the shore, smelting waits for all, `dig_down` goes on, fights end when the target is gone, lost at 24 m or unreachable for `STUCK_MS`). The runner stops an action only when a need appears that was not there at its start (`needNow`, every 0.5 s: a darker time of day except the night verbs, food ≤ 6 except eat, health ≤ 8 except fight/flee; not while riding) → `failed: stopped: <need> (while …; moved Xm)`, on damage / the reflex, or on `POST /abort` (the watcher, and `AdvancePlayUseCase._cut_on_rethink` sends `rethink: <reason>` as soon as `PlaySession.rethink_mark` grows during an action); `stopped:` / `rethink:` are not failures (`NOT_A_FAILURE`, `INTERRUPTED_PREFIXES`). The Python client waits for `/act`, `/tool` and skill runs without a read timeout
  - Doors (`docs/design/39_doors.md`, bridge `doors.mjs`): mineflayer-pathfinder only passes cells whose boundingBox is `empty`, so doors (even open) were blocks to break and no walk went through a door that was not the home's; `canOpenDoors` only handles fence gates. `teachDoors` (from `configureMovements`) makes wooden doors and fence gates passable for straight moves along their facing (not diagonal, not iron, not the home's or former homes' doors, which `enterHome` / `leaveHome` keep handling); `doorOpener` (in every `walkWatched`) opens a closed door on the next path nodes within 3 m, waits until the server shows it open, closes the ones it opened once passed through (or when the walk ends, within reach), and puts door nodes back on the floor (the pathfinder's `postProcessPath` put them on top of the door panel)
  - Boats (`docs/design/37_boats_and_items.md`, bridge `boat.mjs`): Mineflayer cannot move a boat (it stops its physics when mounted and only sends inputs, while the rider computes the boat's position), so `drive` sends `vehicle_move` itself every tick (0.35 blocks, about 7 m/s, accepted by Paper 1.21.4) over navigable water, steering around islands and stopping at the far shore; getting off is `entity_action` sneak (Mineflayer's `dismount` sends jump) and `watchVehicle` clears `bot.vehicle` (not cleared on `set_passengers`). Every long move goes through `legToward` → `crossWater`: water of 12 m or more on the way (`waterCrossing`) with a boat carried → walk to the water's edge, place the boat on open water (8 neighbours water), ride, get off at the shore and break the boat to pick it up again; while riding, the next move keeps paddling (`TRAVELS`), other actions get off first (`disembark`), damage does not stop the ride and the reflex waits; no boat → `state.waterAhead`, and goals offers crafting one (`also`) when the materials are at hand. The mirror relays the bot's `vehicle_move` to viewers and sends only the camera's rotation while riding. Tools `ride_boat {x, z}` and `use_item {item, entity? | x,y,z?, hold?}` (right click: on a mob, on a block's face or at water/air, or ahead; fails when nothing changed; no fire, explosives or spawn eggs; boats, food without a mob, containers and beds go to their own tools; never on protected blocks)
  - Leveling (`docs/design/41_leveling.md`, bridge `leveling.mjs`): `leveled(where, distance)` (where `home` = the home's floor, or `x,y,z` = the height to stand at; radius 3-16) holds when every column's top solid block (plants not counted) is y-1; bumps of natural ground (`TERRAIN`, shared with the pathfinder's break rule) are dug from the top, holes filled with dirt / coarse dirt / cobblestone from the bottom (standing in the hole with dirt: `GoalBlock` one above, so the pathfinder towers up and fills it; with only cobblestone: step to the rim first); homes, builds and 1 around, planted trees and 1 around, farmland, trees, water, placed blocks and columns over 8 off are left alone and not counted, unseen columns count as not level; remaining = the columns off. One `level` action (primitive and tool `level {where?, radius?}`) levels the reachable columns nearest first (bumps first for the dirt), skips what it cannot reach or mine with the reason; the goal makes a pickaxe first when only stone is left and gathers dirt when only holes are left
  - Custom conditions (`docs/design/42_custom_conditions.md`, bridge `conditions.mjs`, tools mode only): `custom(name)` holds when a check Gemini wrote holds — a synchronous JS `function check(w)` returning `{met, remaining, lines}`, run in isolated-vm over a read-only `w` (`block`, `find`, `inventory`, `me`, `home`, `chests`, `mobs`, `time`; 50 ms, 20000 reads, 20 finds, 3000 chars), saved in `state.json` (`conditions`). A new name in a mid goal's conditions makes `MidGoalKeeper` call `CheckWriter` first (`purpose=check_write`, the mid goal's title and reason, the bot's and home's coordinates; 3 tries); `PUT /conditions/<name>` validates, runs it once and refuses a check that already holds; `GET /conditions`. No candidates are offered (Gemini's tools do it; a broken check goes to `blocked`); also a skill `expects`
  - Tool wear (`minecraft-bridge/src/wear.mjs`): a tool that disappears at its last durability is recorded as broken at once (`state.brokenTools`, `observation.broken_tools`, shown as 「壊れた道具」, and appended to the action's result); while no tool of that kind is held, crafting another is offered (`also`) when it is craft-only; a better tier of a held pickaxe / axe / shovel / sword (wooden → stone → iron → diamond) that can be crafted from what is carried is offered the same way (`upgradesToTry`); blocks that need a tool are never dug bare-handed (the dig fails with the reason). Chat replies re-read `/observe` first (`GameService.refresh`)
  - Stuck walking (`minecraft-bridge/src/move.mjs`, `walkTo`): when the bot moves < 0.5 m for 6 s while walking (not digging or placing), it jumps out, then walks carefully (no sprint or parkour), then backs off a few blocks, before failing with `stuck: …` (digging/placing counts only after 30 s); a block the pathfinder digs 3 times in a minute (dig → place dirt → dig loop) is neither dug nor placed for 3 minutes (`guardDigLoops`, `state.loopCells`)
  - Exploring: each direction is offered up to where unseen land begins (`memory.frontierDistance` over explored 16x16 regions) and walked in legs, so a search with nothing nearby does not circle the same area; logs are reachable down through air and logs to the first ground (5 blocks, 8 with dirt to tower); digging stairs down to buried stone/ore has no depth limit by default (`dig_depth` is an optional cap, 0 = do not dig down; lava, water, gravel and bedrock stop it)
  - Furniture (`minecraft-bridge/src/furniture.mjs`): placed beds, crafting tables, furnaces and chests can be picked up and put down again. For `placed(bed, home)` without a bed, a bed placed nearby outside the home is offered as `take the <bed> placed at …` (a dig) beside crafting one; the tool `move_furniture` (tools mode and skills) moves one, the home's furniture only inside the home, a bed only into the home, a chest with known contents is refused
  - Reflex (`docs/design/28_jev_reflex.md`, bridge `reflex.mjs` + `DangerWatcher`): the bridge starts at once with its rule (fight or flee) on a close hostile (5 m, creeper 7 m), on damage (the nearest hostile within 16 m, seen or not) or on a hostile walking toward the bot, and publishes `GET /danger` (the options possible now: fight / flee / go_home / keep_distance / ignore; inside the home only fight / ignore) with the shared state, landmarks, home distance and last hit. `DangerWatcher` (runs with `GameService.play`, every 0.25 s) asks Jev once per danger and switches via `POST /reflex`; late (0.8 s), unsure or failing Jev keeps the rule; `logs/reflex/*.jsonl`; `minecraft.reflex.judge: jev | rules`. Steps cut by damage or the reflex (`ActionResult.cut_by_attack`) are not failures or stalls of the small goal
  - Jev: picks one candidate per step (`Choice`), seeing the goal's progress and the body's needs (no priority order: measured in `spikes/primitive_choice_eval.py`)
  - `PlaySession` ends a goal when the streamer died and respawned (the bridge's `observation.deaths` count went up since the goal was set; checked first, always decided by Gemini with the screen), it is met, the mid goal it served ended, stuck, stalled (the remaining work stops going down), over budget, or the time of day changes
- **POV mirror** (`minecraft-bridge/src/mirror.mjs`): relays the server's packets to the viewer client; what the bot decides itself is not echoed by the server, so the mirror also sends the bot's `held_item_slot`, the player inventory (`window_items`, shortly after its `window_click`s) and closed windows, and both from the current state to late joiners
- **Tool control** (`minecraft.agent.control: tools`, the default since 2026-09-26, `docs/design/19_gemini_tools.md`, `21_tool_control.md`; `candidates` (the path above, Jev picks the bridge's candidate) is kept for comparison with `--control candidates`): within the same predicate small goals, Gemini calls one bridge tool per step by function calling (`POST /tool`: goto/dig/place/craft/…, `do_suggestion` runs a solver candidate, `find_blocks`/`recipe_of`/`how_to_get` are lookups). Action tools carry an `intent` (shown via `activity()`) and up to 3 English `watch` questions. While a tool runs, `ToolWatcher` reads `GET /state` (progress, a `look_around` height grid, mobs with ids, needs) every second and asks Jev all questions in one `system_one` call; two strong yeses stop / wake / report maybe-done via `POST /abort` (Jev never judges completion; the code's own progress question only records until it beats a rule). Safety stays in the bridge: sheltering refuses outside tools, the current home and the house being built are never dug (former homes can be). Ticks go to `logs/watch/*.jsonl`. Thinking depth per purpose: `gemini.thinking_levels`
- **Jev first** (`docs/design/34_jev_first.md`): in tools mode each step first goes to Jev (`StepPicker`, `minecraft.agent.step_picker: jev`): it picks among the bridge's candidates (run as `do_suggestion`), learned skills that worked and take no arguments (`run_skill`) or `ask_gemini`; Gemini's `choose_tool` decides when Jev asks, is unsure (< `step_min_confidence`), slow, fails, after a failed tool, after `step_max_failures` failed Jev steps or every `step_gemini_every` steps; records in `logs/steps/*.jsonl`. The voice's emotion (`tts.emotion_judge: jev`): before a line is spoken, one `AvatarDirector.react` call picks the expression, strength and gesture; `voice_emotion` turns it into the line's `EmotionState` (`TTSService.speak(emotion=)`: Style-Bert-VITS2 style, Irodori-TTS emotion voice/caption) and the avatar reuses the same reaction (`LineReactions`); no answer → neutral as before. Chat triage (`twitch.response.triage: jev`, `CommentTriage`): before a reply Jev answers reply-or-not and the kind (request / advice / withdraw / question / chat / noise); requests, advice and withdrawals always get Gemini's reply; only chat / noise Jev is sure needs no reply is skipped (`logs/chat/*.jsonl`); withdrawals and requests are answered first. Reply promises (`twitch.response.promise_check: jev`, `PromiseCheck`): a reply that neither accepts a request nor writes a rethink is checked by one Jev YES_NO (does it promise a concrete action in the game?); on a sure yes (≥ 0.6) Gemini (`purpose=promise`) keeps the promise (design 34 §10.2–10.3): `none` (no new action is promised: what she is doing now, feelings; "some day I will …" is a promise) → spoken as is, `already` (a mid goal in the list has it; a survival small goal alone does not count) → spoken as is, `now` → a mid goal first and the small goal is cut (not within 60 s of a chat rethink: then `later`; never after a decline), `later` → a mid goal behind the current one, `cannot` (not writable as conditions; priority is never a reason, that is `later`) → the same call's `rewritten_reply` (the reply without the promise) is spoken, no second reply generation (only an empty one falls back to writing the reply again); readings and lessons are kept only from the reply used, `logs/promises/*.jsonl`; advice whose result is a condition (`placed(torch, home, 2)`, `lit`, `farmed`) is accepted as a mid goal, not only a lesson. Narration timing (`stream.commentary_judge: jev`, `CommentaryGate`): at a small-goal boundary Jev says whether it is worth speaking now; highlights (mid goal completed / dropped, house, town) and `max_silence_seconds` (60) of quiet always speak, skipped events wait for the next line (`logs/commentary/*.jsonl`). Failure routing (`minecraft.agent.failure_routing: jev`): after a stuck / stalled small goal, `GoalChooser.choose(avoid=)` offers Jev the other open steps (not the same kind) plus `rethink` (Gemini's design-27 analysis); Gemini always after 2 same-kind failures, a death, a stalled mid goal or a screen rethink. Stuck check (`minecraft.agent.stuck_check_steps`, 6, `StuckCheck`): every 6 actions of a small goal Jev reads the action record (actions, results, positions) and picks fine / repeating / failing / back_and_forth / no_progress; a sure non-fine answer (≥ 0.75) calls `request_rethink` and Gemini re-decides with the action record (`logs/stuck/*.jsonl`)
- **Lesson book** (`docs/design/35_lessons.md`, `application/use_cases/lessons.py` `LessonBook`, `data/lessons.json`, kept across world resets, `minecraft.agent.lessons: jev|rules|off`): lessons from viewers' advice (the reply's `lesson` + `lesson_when`, into the book instead of the notes) and own failures (the after-failure decision's optional `lesson` + `lesson_when`), each with situation keys in a fixed vocabulary (goal, item, time, place, body, pickaxe, how the last goal ended: `situation_of`) — Gemini's `lesson_when` keys when given (`explicit`: a mismatch never recalls it), else a snapshot. Recall: code scores the key overlap (also against the top mid goal's steps) → up to 6 candidates → one Jev `ask` with a YES_NO per lesson (≥ 0.6 kept; no answer → the code's top 3) → up to 4, shown as 「思い出したこと」 (unverified) before a Gemini goal decision and at every small-goal start (`Goal.lessons` → Jev's state `remember` and the tool prompt). Lessons recalled at a start count helped (met) / failed (stuck, stalled, died); proven ones stay, never-helping ones (4 uses) go, 200 at most; the same lesson again strengthens it (`taught`). `logs/lessons/*.jsonl`
- **Local LLM** (`docs/design/36_local_llm.md`, `llm.local.enabled` / `LOCAL_LLM=1`, off by default): `factories/text.py` `create_text_generator` builds a `RoutingTextGenerator` over `OpenAICompatTextGenerator` (any OpenAI-compatible `/v1/chat/completions`: FreeToken :1919, Ollama :11434/v1, llama.cpp, vLLM) and Gemini: routes per purpose (`llm.routes`, `default: local`), calls with images always to Gemini (the local model runs without vision), a prompt over `context_tokens` (estimated: non-ASCII 1 char = 1 token, ASCII 4) to Gemini for that call, local failures go to Gemini (`llm.fallback: gemini`; design 36 §7). Routes (2026-09-27, after Gemini's spend cap was hit): local for `tool`, `tool_after_failure`, `commentary`, `live` (the default: ~80% of Gemini's input was tool calls, which the local model kept right); Gemini for `goal`, `goal_after_failure`, `reply`, `promise`, `town`, `site`, `house`, `build_design`, `skill_write`, `check_write` (planning, viewers waiting, schemas the local model broke). The local adapter always writes the schema into the prompt, also sends `response_format: json_schema` (dropped for good when the server refuses it), checks the answer (`schema_check.check`: type/enum/required/range/items) and asks once again with the errors; tools via `tools` + `tool_choice: required` (→ auto when refused), a text answer is asked again as `{tool, args}` JSON; thinking only for `llm.local.thinking_purposes` (default skill_write, house, town, site; everyday decisions never think) via `chat_template_kwargs.enable_thinking`. Usage lines `Local <model> …` in the Gemini line's shape (`tools/gemini_usage.py` → `local:<purpose>`), debug records carry `model`. `tools/llm_probe.py` measures schema/tool support, time to first token, prefill/decode speed and whether prefix caches survive another prefix
- **Skills** (`docs/design/22_skills.md`, tools mode, `minecraft.agent.skills`): Gemini writes JS skills over the same tool API (`t.goto/dig/...`, `t.state()`, `t.block_at()`, `t.judge()` answered by Jev on the watcher's tick via `pending_judge` / `POST /judge`, `t.done/fail/log`; no `do_suggestion`) with `expects` (have/stored/built/placed/lit with a number, `+N` or `$param`, or `progress`) judged in the world. Tools `run_skill` / `write_skill`; `SkillWriter` (`purpose=skill_write`, thinking high, 3 tries, 3 writes per skill per hour) saves via `PUT /skills/<name>` and the new version is tried at once; failures (reason, log, last calls) are shown for the fix. The bridge runs skills in `isolated-vm` (`src/skills.mjs`: 40 actions, 200 lookups, 10 judges, 1 s CPU, 32 MB; `/abort` and the reflex stop them; safety stays in the tools) and keeps versions and counts in `minecraft-bridge/data/skills/` (kept across world resets). `npm run skills:clean` (outside the stream) removes versions that never succeeded. `SkillLearnedEvent` is narrated at once and makes the avatar cheer
- **Screen vision** (`docs/design/23_screen_vision.md`): Gemini sees the stream screen as an image, captured by `ObsScreenCapture` (OBS WebSocket `GetSourceScreenshot` of the one source `obs.game_source` showing the POV client, never the whole scene; 3 s timeout, 60 s back-off, no capture → text only, the password never logged). `ScreenReviewer` (called from the step loop only): a periodic review every `minecraft.vision.review_interval_seconds` compares the screen with `activity()` and may `request_rethink` (ends the small goal like stuck); after a failure a screenshot goes with `goal_after_failure` / `tool_after_failure` (at most once per `failure_interval_seconds`); in tools mode `look_screen` attaches one to the next `choose_tool` of the same step. What Gemini reads from an image is shown as an unverified `screen_note`, never judges completion, never goes to Jev. Images go to Gemini with `media_resolution` (`gemini.media_resolution`, low)
- **Small goals by Jev** (`docs/design/26_steps_and_context.md`, `minecraft.agent.small_goals: jev`): when a mid goal reaches the top (or its steps fail, run out, Jev is unsure, or every `replan_minutes` if the list changed) Gemini's goal decision also writes `steps` (1–6 condition specs with a reason, `MidGoal.plan_steps`, saved); at other boundaries (met, 40-step budget, time change) `GoalChooser` asks Jev (`IFastJudge` Choice) to pick among the open steps (judged by `/check`) and survival goals (night/dusk/hunger, `mid_goal_id` None). `AdvancePlay._needs_gemini` returns why Gemini is called (logged); Jev's choices go to `logs/goals/*.jsonl`. `small_goals: gemini` keeps the old per-goal Gemini decision. Gemini usage lines carry `purpose=`/`cached=`; `tools/gemini_usage.py` sums a log per purpose; `include_thoughts` is off unless `--debug`; the periodic screen review is off (`review_interval_seconds: 0`). Prompts are cache-friendly (26 §4): each purpose's fixed rules go in `system_instruction` (`IGamePromptBuilder.build_goal_system()`, `IPromptBuilder.build_system_prompt(character, purpose)` with `commentary`/`reply`/`reply_requests`) and the body holds only the state, most stable first; chests show non-empty ones' top items, the goal decision sees the last 3 commentary lines; `TestPromptLength` guards the size
- **Goal bundles** (`docs/design/44_goal_bundles.md`): a small goal (and each of a mid goal's steps) may carry `together` (up to 3 more condition specs, 4 in all, only `CONDITION_PREDICATES`, no repeats: `check_bundle`) and an `approach` sentence, so one trip gathers several things (also for other mid goals). The bridge's `PUT /goal {…, together}` evaluates each member and merges them (met = all, remaining = the sum, lines `[i/n] label: N left`, leaves tagged `for`, `members` in the status → `GoalStatus.members` / `BundleMember`); the lead (`Goal.spec`) stays fixed for the goal; `GoalChooser` checks every member of each step and passes only the unmet ones; the step budget is 40 + 20 per extra member; `_also` leaves out the bundle's items; a member met on the way publishes `GoalMemberMetEvent`, narrated like a boundary; `activity()` shows the bundle and the approach, the board's label adds 「（ほか N つ）」. Bundles are revisited at every Gemini goal decision (design 44 §9): the review rule re-bundles or unbundles, the schema's required `trips` (right after `review`) groups what is left by trip and the steps follow it, and a bundled small goal merges steps written apart (`_steps_with_the_bundle`)
- **Failure diagnosis** (`docs/design/27_failure_diagnosis.md`): when a small goal is stuck or stalled, the same `goal_after_failure` call also gets the goal's action record (`AdvancePlay._goal_steps`, 12: action, confidence, result) and the last options offered (distance, been there, seen ago), and its schema puts `diagnosis` first, then `remedy` (retry / change) and `advice`. Code keeps the rules (`_check_remedy`): retrying the same kind of goal (`GoalSpec.same_kind`: numbers ignored) needs advice, change must be a different kind, and after 2 failures in a row of the same kind retry is not offered. `Goal.advice` goes to Jev's state (and the tool prompt via `activity()`), `Goal.diagnosis` is shown in `activity()` so the commentary can say why
- **Mid goal not progressing** (`minecraft.agent.mid_goal_stall_steps`, 60): when the top mid goal's progress (the `(n/m)` counts of its conditions) stays the same over that many actions for it, the next boundary goes to Gemini (`goal_after_failure`, retry not offered) with the reason and alternatives (another method, grow what cannot be found: `planted` saplings when there are no trees, `farmed` for food; explore far; move down or drop), even when small goals keep ending as met or stalled and Jev would pick the same step again
- **Review each decision** (`docs/design/31_review_each_decision.md`): every Gemini goal decision writes a required `review` first (after diagnosis/remedy/advice on a failure): from the state now (inventory, chests, nearby, memory, time, the top mid goal's steps judged by `/check` and shown as 「手順の今の状態」) is there a faster or less wasteful way; it then edits the mid goals / rewrites the steps, or says why the plan stays (no change is a valid answer). `Goal.review` is shown in `activity()` and logged as 見直し
- **Town stages** (`docs/design/15_town.md`): a stage's `unresolved` parts are written again only for the current stage and only when the vocabulary shown to the stage prompt (`IGamePromptBuilder.vocabulary_version()`: a hash of the conditions and abilities) changed since the last try (`TownStage.tried_with`, saved); checked at start and at small-goal boundaries (`TownPlanner.advance`). Before 2026-09-27 every start rewrote all later unresolved stages (≈4 min with the local LLM)
- **Goal hierarchy** (`docs/design/13_goal_hierarchy.md`): mission (config `minecraft.mission`, never changed on stream) → mid goals (`MidGoalPlan`: prioritised list, done when their conditions `built`/`placed`/`have` hold, judged by the bridge's `POST /check` at small-goal boundaries; limits kept by code: 6 in the list, 2 viewers' at a time, one per viewer, a reply's request goes behind the current one unless Gemini accepts it `when: now` (then it goes first and the small goal is cut with `request_rethink`; 13 §13–14), Gemini's own plan edits may reorder anything including viewers' goals when it judges it better, 80-step budget). Crafting tables and furnaces may go inside the home (keeping the door line and room for a bed) or outside; a far table also offers crafting a new one here (13 §14) → the small goal (serves the top mid goal, or survival). Gemini edits the list with the small goal (add/move/drop with a reason); `MidGoalKeeper` applies edits atomically, saves (`data/mission.json`) and publishes MidGoal events
- **Coherent decisions** (`docs/design/12_coherent_decisions.md`, request flow replaced by 13): `PlaySession` alone owns the goals; its `activity()` (mission, mid goals, small goal and status, recent goals) is rendered by one formatter (`prompts/stream_context.py`) for the goal decision, the commentary and the chat replies, and the goal decision also sees the conversation. A chat reply and its handling of a request (none/accept/decline) come from one generation; a reply that agrees with a concrete, checkable point (a tree right there, a faster way) also writes `rethink`, which cuts the small goal at the next boundary (`request_rethink`; reasons add up instead of replacing each other, and a reason that arrives while the next goal is being decided is kept for the next boundary (`rethink_mark` → `set_goal(rethinks_seen=)`); from chat at most one cut a minute: a point joins a pending rethink freely, but a reply that would rethink too soon is written again (accept, or keep going and say so)) so the goal is decided again; `lesson` (advice the streamer agrees with, in its own words) is kept as a lesson note via `NoteKeeper.learn_from_viewer` (repeated advice extends it; design 18 addendum); `withdraw` (the requester calls off their own request) drops that viewer's mid goal (`MidGoalKeeper.withdraw`, only their own); an accepted request becomes a viewer's mid goal behind the current one (the small goal is not interrupted). `Narrator` says why goals change and never drops a promise silently
- **Goal board** (`presentation/web/goal_board.py`, extra `ailoveshen[stream]`): `GET /api/goals`, `/api/goals/stream` (SSE), `/overlay` (OBS browser source), `/overlay/vtuber` (the styled stream overlay: mission, mid goals with progress bars and viewer requests, the small goal as a speech bubble with a Japanese `label` and the `intent`, toasts when a mid goal clears; `?demo=1&pos=right&theme=mint|sky|lemon&scale=&compact=1&toast=0`), read-only in the play process (`examples/integration_test_minecraft.py --board-port 8765`). Debug: `GET /api/debug/gemini?limit=N` (the last Gemini calls, newest first: purpose, thinking level, thought summary with `gemini.include_thoughts`, output, tool calls, tokens, prompt; kept by `InMemoryGenerationLog`, shared by the game and chat generators) and `/debug/gemini` (a page that polls it; works as an OBS browser source); the screenshots attached to calls are served by id from `/api/debug/screen/<id>`
- **Avatar** (`docs/design/24_avatar.md`, `presentation/web/avatar.py` + `avatar.html`): the VRM (`avatar.model_path`, `models/vrm/ailoveshen.vrm`, VRoid VRM 1.0) at `/avatar` on the goal board (OBS browser source, transparent; three.js + three-vrm vendored in `presentation/web/vendor/`, served at `/vendor/`). `AvatarStage` turns domain events into cues on `/api/avatar/stream` (SSE): speak (text + length; the page makes mouth shapes from kana), quiet, emote (happy/sad/angry/surprised/relaxed + nod/cheer/flinch/tilt/wave). With `avatar.judge: jev` (and a TypeSafe key) `AvatarDirector` asks Jev in one `system_one` call for the expression, its strength and a gesture from the moment, the line and `activity()`; the rules above are the fallback (error, `judge_timeout_seconds`, low confidence); the mouth moves at once and the face follows; Jev vs rule records in `logs/avatar/*.jsonl` (24 §8). Lip sync from TTS playback events, or from generated text when there is no TTS (`avatar.lip_sync: auto|text|tts`); full body by default (crop in OBS), `?demo=1&view=bust&pos=left|center|right&scale=`
- **Builds** (`docs/design/25_builds.md`): Gemini designs named builds (home extensions, sheds, towers, walls) as shapes (`BuildDesign`: fill / hollow_box / clear / door, planks/log/cobblestone/dirt, ≤ 3000 blocks and 48x16x48 for the streamer's own and town builds, ≤ 600 for a viewer's request) that the domain expands to blocks; the place is an anchor, never coordinates (`home:east|west|north|south` joins the home's wall, `near_home` finds flat ground, `map` + a cell like C7 of a top-down map image: bridge `GET /map`, drawn by `PilMapRenderer`, sent at `media_resolutions.build_design: medium`; code turns the cell into coordinates). A mid goal condition `built(name)` with a new name makes `MidGoalKeeper` call `BuildDesigner` first (checks materials with `/check`, registers with the bridge's `PUT /builds/<name>`, retries 3 times with the reason); a viewer's build widens its step budget. The bridge keeps `state.builds` apart from the house plan, refuses builds over the home's door/bed/chests/room/floor, lets `place_plan`/`dig`/`build_next` dig only the home wall cells a build marks `clear`, protects builds' own cells (`onBuildCell`: planned blocks and clear cells, +1 around for scaffolding; not the whole bounding box, whose trees and ground stay usable) from mining, and grows the home's room (`home.cells`) when an extension completes enclosed
- **Live narration** (`docs/design/43_live_narration.md`, `presentation/services/live_narration.py`): during long actions, after `stream.live_interval_seconds` (10; 0 = off) of quiet measured from the end of playback (`TTSService.is_idle()`), `LiveNarration` says the tool's new Japanese `intent` as is, or a new short line (`purpose=live`, not kept in the conversation) from the recent action results, the inventory change and distance moved since the last line, and the last lines it must not repeat; a line made while another started playing is dropped. Every commentary line (`say` in `factories/stream.py`) is skipped when it is like one of the last 8 spoken lines (`SpokenLines`, character-bigram Jaccard ≥ 0.5)
- **Small talk** (`docs/design/40_small_talk.md`): replies take up any topic (other games like LoL, anime, food, daily life) with the streamer's own tastes instead of deflecting as a Minecraft streamer; what she says about herself (main champion, likes, experiences) is written in the reply's `self_facts` ([{topic, fact}], JSON replies only) and kept by `SelfFacts` + `JsonSelfFactStore` (`data/self_facts.json`, `twitch.self_facts_path`, kept across world resets, one fact per topic: the same topic replaces it, 120 at most, the longest unspoken forgotten first); every reply's body starts with 「自分について話したこと」 and the rules say never to contradict it and to decide new things in character
- **Name readings** (`docs/design/30_name_readings.md`): viewers' names → kana readings (`data/readings.json`, `twitch.readings_path`; `NameReadings` + `JsonReadingStore`), learned from `!yomi <reading>` (`ChatResponder`, acknowledged) and from the reply's `name_reading` (a guess when none is known; the viewer's own when the comment says how to read it; a guess never overwrites the viewer's), used only in the text sent to TTS (`SpeakTextUseCase(pronounce=)`), looked up at `GET /api/readings` on the goal board
- **The stream** (`python -m ailoveshen.stream`, `src/ailoveshen/stream.py` → `factories/stream.py` → `presentation/services/stream.py`; runbook `docs/streaming.md`): runs until Ctrl-C with no step limit; `GameService.play(max_steps=None, keep_going=True)` logs a failed start or step and retries after 5 s doubling to 60 s (reset on success); refuses to start if the board port is taken or `stream.speak` and the TTS server is unreachable; a background task (board, chat) that ends unexpectedly is logged at ERROR while the play goes on. Twitch chat (`twitch.enabled` and `TWITCH_CHANNEL`) is read anonymously over IRC (`TwitchIrcChat`, `justinfan` nick, reconnects with back-off, 15 s connect timeout); `ChatResponder` answers one comment at a time (`twitch.response.min_interval_seconds`, keeps the newest `backlog`, no reply to `!` commands; commands `!yomi` and `!commands`/`!help`/`!コマンド` (the list, posted to chat when `TWITCH_ACCESS_TOKEN` (+ `TWITCH_BOT_LOGIN`, default the channel) logs `TwitchIrcChat` in, an `IChatSink`; otherwise spoken; once per 30 s); the channel's own account and the bot login get no reply but may use commands; `twitch.ignore_users` (default `[streamelements]`) get nothing) through `LLMService.generate_response` with the session, spoken at HIGH priority. Goal board and avatar on `stream.board_port` (0: off); on stop the SSE streams get an end mark so uvicorn shuts down cleanly. `examples/integration_test_minecraft.py` stays as the test harness (step budget, scripted comments)

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
