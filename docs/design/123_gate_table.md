# 123 付録: ゲート一覧（分類つき）

`tools/gate_inventory.py` が挙げた 316 箇所の分類。分類は設計書 123 の基準で、箇所ごとにコードを読んで付けた（読んだのはサブエージェント。抜き取りで確かめた）。回数は 2026-10-01〜10-08 のブリッジのログ（`[tool]` 行の失敗・断り）で、`-` は文が短すぎて数えられない。

| 分類 | ファイル:行 | 関数 | 回数 | 出口 | 今の上書き | 案 | メッセージ | 理由 |
|---|---|---|---|---|---|---|---|---|
| knowledge | primitives.mjs:1107 | dig | 116 | no | no | advise+force | will not dig ${block.name} at ${c.pos.x},${c.pos.y},${c.pos.z}: ${wet} | Bans digging beside water or lava. Lava half is safety; water half is tidiness an LLM could override. Split the two. |
| knowledge | primitives.mjs:2281 | place_water | 98 | no | no | advise+force | the ground beside the hole at ${at} (${p.x},${p.y},${p.z}) is lower: the water would run o | Refuses a water hole when a neighbour is lower; spill is reversible (scoopBack exists). Warn, let the LLM proceed. |
| knowledge | tools.mjs:346 | dig | 75 | no | no | advise+force | the ${block.name} at ${fmt(pos)} is not ripe yet (age ${ageOf(block)}/${CROPS[cropByBlock( | Unripe crop: digging yields seeds and loses crop. Harmless loss; LLM may want to clear or get seeds. No bypass. |
| knowledge | tools.mjs:540 | plant | 59 | yes | no | advise+force | will not plant at ${fmt(pos)}: ${NO_FACILITY}${nextCell('plant', { sapling: item })} | Planting sapling only on designed tree-farm cells. Author layout preference; no bypass; 59 fires. |
| knowledge | tools.mjs:543 | plant | 59 | no | no | advise+force | will not plant at ${fmt(pos)}: ${why} | farmRefusal: within 1 of home/build (safety-ish) or too close to home ring (layout). Fires inside designed cells; mostly layout policy. |
| knowledge | tools.mjs:363 | place | 50 | yes | no | advise+force | will not place at ${fmt(pos)}: ${stationWhy} | Furnace only inside house, never underground; (crafting table already freed after 55-min stuck). Smelting safety is preference. |
| knowledge | tools.mjs:339 | dig | 41 | yes | no | advise+force | will not dig ${block.name} at ${fmt(pos)}: the ground there is being leveled (or was): tak | Terrain at/below floor inside a leveling zone (+margin). Protects levelling work; no bypass; 41 fires. |
| knowledge | tools.mjs:553 | till | 30 | yes | no | advise+force | will not till at ${fmt(pos)}: ${NO_FACILITY}${nextCell('till', { crop: state.goal?.spec?.p | Tilling only inside designed farm layout; no bypass; 30 fires. |
| knowledge | tools.mjs:555 | till | 30 | no | no | advise+force | will not till at ${fmt(pos)}: ${why} | farmRefusal at till: near home/build or inside home ring; layout preference, same helper as plant. |
| knowledge | tools.mjs:344 | dig | 22 | yes | yes | advise+force | will not dig farmland at ${fmt(pos)}: it is the ground of a farm and digging it turns the  | Digging farmland turns it to dirt; reversible. demolish bypasses; message already suggests digging the crop. |
| knowledge | tools.mjs:334 | dig | 16 | yes | no | advise+force | will not dig ${block.name} at ${fmt(pos)}: it is close to the surface. ${SHALLOW_REMEDY} | Stone with <14 cover, when a home exists: forced to dig a mine. No bypass; blocks cheap surface cobble and stuck escapes. |
| knowledge | tools.mjs:290 | goto | 14 | yes | no | advise+force | will not walk to ${fmt(pos)}: the goal is at_home and the home's door is at ${fmt(door)}.  | When goal is at_home, goto only within 16 of door (guards a past wrong-coordinate walk). LLM can judge; go_home exists. |
| knowledge | tools.mjs:307 | goto | 8 | yes | no | advise+force | will not walk down to ${fmt(pos)}: it is ${Math.round(bot.entity.position.y - pos.y)} bloc | Descending 8+ below with fewer torches than distance needs. Past near-death, but a lit/known route or other plan is legitimate. |
| knowledge | tools.mjs:479 | toss | 7 | yes | yes | advise+force | ${args.item}: only ${inv[args.item]} held, ${RESERVE[args.item] ?? 0} are kept; pass count | Reserve of dirt/cobble kept; passing count already overrides. |
| knowledge | tools.mjs:477 | toss | 6 | yes | no | advise+force | ${args.item} is not thrown away by this tool (only natural ground blocks, cobblestone, flo | Toss whitelist (ground blocks, cobble, flowers, wool). LLM may reasonably toss junk like rotten flesh; deposit suggested. |
| knowledge | tools.mjs:651 | use_item | 6 | yes | no | advise+force | will not pour water in or beside the farm ${farm.name} by hand (it would run over the farm | Hand-pouring water within 1 of a designed farm: floods farmland (reversible). water tool suggested. |
| knowledge | tools.mjs:551 | till | 2 | yes | no | advise+force | no cell left to till in the farms in view: ${NO_FACILITY} | No cell left in designed farms; consequence of designed-only farming. |
| knowledge | tools.mjs:337 | dig | 1 | yes | yes | advise+force | will not dig ${block.name} at ${fmt(pos)}: ore this deep is taken only where it shows in t | Deep ore not touching a mine cell: branch-mining-only rule after a spider death. Rule of the author; demolish bypasses. |
| knowledge | tools.mjs:563 | till | 1 | yes | no | advise+force | will not till more: you have ${seeds} ${seed} for ${tilled} tilled cells with nothing plan | Seeds <= tilled-unplanted cells: avoids reverting farmland. Harmless waste; author tidiness. |
| knowledge | tools.mjs:590 | water | 1 | yes | no | advise+force | will not put water at ${fmt(pos)}: it is not a water cell of a designed farm (${NO_FACILIT | Water only on designed farm water cells; no bypass. |
| knowledge | facilities.mjs:282 | farmLayout | 0 | yes | no | hint | not enough flat dirt or grass for a ${width}x${depth} farm in these zones (at most ${most} | USABLE_SHARE 0.6 is author preference; a sparser farm could still be levelled and used. |
| knowledge | facilities.mjs:286 | farmLayout | 0 | yes | no | hint | not enough ground for a ${width}x${depth} farm with water in these zones (${crops.length}  | Same 60 percent share applied after water placement; preference, not impossibility. |
| knowledge | facilities.mjs:315 | fenceRing | 0 | yes | no | advise+force | the fence around the farm would run over the home, a build or another facility at ${c.x},$ | Free ring of 1 around farm is design convention; overlap with other facility is the LLM's call. |
| knowledge | facilities.mjs:375 | treeLayout | 0 | yes | no | hint | only ${best.length} places for saplings ${spacing} apart in these zones (asked ${count}, a | Minimum sapling places (half of count) is author threshold; LLM could accept fewer. Caused 3 send-backs on 2026-10-01. |
| knowledge | facilities.mjs:401 | registerFacility | - | yes | no | hint | ${k} must be ${FARM_SIDE.min}-${FARM_SIDE.max} | Farm side cap 1-16 is author sizing choice, not a world limit. |
| knowledge | facilities.mjs:412 | registerFacility | 0 | yes | no | hint | count must be ${TREES.min}-${TREES.max} | Tree count cap 1-32 is author sizing choice. |
| knowledge | facilities.mjs:414 | registerFacility | 0 | yes | no | hint | spacing must be ${SPACING.min}-${SPACING.max} (saplings closer than ${TREE_GAP} do not gro | Spacing 4-6 is author growth rule; LLM might want denser or sparser planting. |
| knowledge | facilities.mjs:844 | mineLayout | 0 | yes | no | hint | tunnels must be ${TUNNELS.min}-${TUNNELS.max} | Tunnel count 0-4 is author design cap. |
| knowledge | facilities.mjs:846 | mineLayout | 0 | yes | no | hint | tunnel_length must be ${TUNNEL_LENGTH.min}-${TUNNEL_LENGTH.max} | Tunnel length 4-32 is author design cap. |
| knowledge | facilities.mjs:848 | mineLayout | 0 | yes | no | hint | branch_length must be 0-${BRANCH.max} | Branch length cap 16 is author design cap. |
| knowledge | goals.mjs:107 | together | 0 | yes | no | hint | a bundle holds at most ${MAX_BUNDLE} conditions (the first and ${MAX_BUNDLE - 1} together) | Bundle size cap of 4 is author cap on parallel conditions. |
| knowledge | goals.mjs:217 | validGoal | 0 | yes | no | hint | distance (the radius) must be ${MIN_LIT}-${MAX_LIT} | Lit radius 8-32 is author range; a larger or smaller radius is the LLM's choice. |
| knowledge | goals.mjs:225 | validGoal | 0 | yes | no | hint | distance (the radius) must be ${MIN_LEVEL}-${MAX_LEVEL} | Level radius 3-16 is author range, bounded for work cost. |
| knowledge | primitives.mjs:1736 | smelt | 0 | yes | no | advise+force | the furnace at ${c.pos.x},${c.pos.y},${c.pos.z} is not inside the house: smelting and cook | Smelting only in the house furnace is the author's preference; a furnace outside is fine in daylight or a camp. |
| knowledge | primitives.mjs:1739 | smelt | 0 | yes | no | advise+force | smelting is not done underground (monsters spawn in the dark and a smelt keeps you standin | Underground smelting ban is a risk estimate (dark spawns), not a certain loss; with torches or walls the LLM may choose it. |
| knowledge | primitives.mjs:1818 | dig_down | 0 | yes | no | advise+force | dig_down needs ${need} torches for ${Math.round(downBy)} blocks down (one about every ${TO | Torch count for descent is the author's spacing rule; the LLM may accept spawn risk. Stalls whole mining objectives. |
| knowledge | primitives.mjs:1879 | dig_mine | 0 | yes | no | advise+force | no torch to light the mine ${fac.name} (a torch goes on the wall every ${MINE_TORCH.tunnel | No-torch mine ban is a spawn-risk policy; the LLM may mine a short mine unlit or by day. |
| knowledge | primitives.mjs:1883 | dig_mine | 0 | yes | no | advise+force | the mine ${fac.name} has dark cells (light below ${MIN_LIGHT}: monsters spawn there) and y | Dark-cell relight demand is spawn-risk policy; digging on without torches is a choice, not an impossibility. |
| knowledge | primitives.mjs:1996 | make_mine | 0 | no | no | hint | a mine is designed near a home you built (there is none) | Mines only near a home the bot built is a design limitation; the LLM could design a mine elsewhere if the reason were told. |
| knowledge | primitives.mjs:2119 | level | 0 | no | no | hint | ${block?.name} is not natural ground | Level only touches natural terrain: leveling-zone protection by the author; the LLM may want to clear a built or odd block. |
| knowledge | tools.mjs:332 | dig | 0 | yes | yes | advise+force | will not dig ${block.name} at ${fmt(pos)}: ${body}. Dig the rock beside the mine instead | Mine staircase pillar/step ("no way back up"). Recoverable via closed-space escape; demolish bypasses today. |
| knowledge | tools.mjs:476 | toss | 0 | no | no | advise+force | ${args.item} is thrown away only beyond one stack per farm (inventory and chests together) | Surplus-seed rule: one stack per farm kept. Author preference; blocks freeing slots. |
| knowledge | tools.mjs:537 | plant | 0 | yes | no | advise+force | no free cell for ${item} in the tree farms in view: ${NO_FACILITY} | No free cell in designed tree farms in view; designed-only rule. Add facility suggested. |
| knowledge | tools.mjs:541 | plant | 0 | no | no | advise+force | the tree farm ${fac.name} is for ${fac.layout.sapling}, not ${item} | Tree farm assigned a particular sapling type; design preference. |
| knowledge | tools.mjs:579 | sow | 0 | no | no | advise+force | the farm ${fac.name} is for ${fac.layout.crop}, not ${crop} | Designed farm is for another crop; design preference. |
| knowledge | tools.mjs:588 | water | 0 | yes | no | advise+force | no water cell left to fill in the farms in view (search_facility shows the farms) | No water cell left in designed farms; designed-only rule. |
| knowledge | tools.mjs:633 | use_item | 0 | yes | no | hint | ${block.name} has its own tools (sleep, deposit, withdraw, craft, smelt, move_furniture) | Bare click on bed/chest/furnace/table redirected to dedicated tools. Pure redirect, could just be advice. |
| knowledge | tools.mjs:644 | use_item | 0 | yes | no | delete | to eat ${args.item} use eat (use_item with food is for feeding a mob: give entity) | Food via use_item: could simply route to eat. Redirect only. |
| knowledge | tools.mjs:654 | use_item | 0 | yes | no | hint | ${block.name} has its own tools (sleep, deposit, withdraw, craft, smelt, move_furniture) | Item use on bed/chest/furnace/table redirected to dedicated tools; redirect only. |
| ledger | tools.mjs:778 | callTool | 175 | yes | yes | advise+force | ${name} at ${fmt(tried.pos)} was tried before and will not work: ${tried.reason}; choose a | 5-min ban on same verb+cell after earlier refusal/no path; also stores policy refusals (will not dig/place). Show as note, allow retry. |
| ledger | primitives.mjs:1333 | climb_out | 1 | yes | no | hint | piled up ${raised} block${raised === 1 ? '' : 's'} and now stand over a drop on every side | Sets climbBlockedUntil, withholding climb_out for a while after one outcome; the throw itself is a fact. |
| ledger | primitives.mjs:1514 | place_plan_once | 0 | yes | no | hint | no flat ${plan.size.width}x${plan.size.depth} site nearby; explore elsewhere | siteSearchFailedAt withholds house-site retry until the bot moves far; the failure itself is a fact. |
| ledger | primitives.mjs:1582 | sleep_at | 0 | no | no | hint | No path to the bed at ${c.pos.x},${c.pos.y},${c.pos.z} (closed in or cut off): it is not o | markNoPath hides the bed from candidates for a while after one No path; the path failure is a fact. |
| ledger | primitives.mjs:1864 | dig_down | 0 | no | no | hint | could not dig stairs down toward ${c.target}: ${stop} | markUnreachable bans the target after one stair-dig failure; the failure itself is a fact. |
| ledger | primitives.mjs:2115 | level | 0 | no | no | keep-fact | tried 3 times and it did not change | Per-cell try cap of 3 inside one level run; stops a no-change loop in that run only. |
| safety | tools.mjs:773 | callTool | 203 | no | no | keep | the reflex is handling a nearby threat | Reflex active: emergency system invariant, 203 fires but transient. Keep as hard refusal. |
| safety | tools.mjs:313 | goto | 21 | yes | no | keep | will not walk to ${fmt(pos)}: it is over water (sea, river or lake), and walking there goe | Column below target (<=8) is water: drowning risk. Heuristic also fires for shallow water; consider depth check. |
| safety | tools.mjs:329 | dig | 14 | yes | yes | keep | will not dig ${block.name} at ${fmt(pos)}: ${why}. If taking it down is the point of this  | Part of current home, build cells or house under construction. demolish:true already bypasses. |
| safety | tools.mjs:638 | use_item | 9 | yes | no | keep | will not use ${args.item}: ${why} | flint_and_steel, fire_charge, lava_bucket, tnt, end crystal: fire/explosion; spawn eggs and boats are redirects. No override. |
| safety | primitives.mjs:1316 | climb_out | 3 | no | no | keep | ${bad.name} at ${bad.position.x},${bad.position.y},${bad.position.z} beside the rock above | Digging rock beside water, lava or gravel in a one-cell shaft floods or buries the bot with no escape. |
| safety | tools.mjs:326 | dig | 3 | yes | no | keep | will not dig ${block.name} at ${fmt(pos)}: ${wet}. Leave it, or get the material somewhere | Block holds back water/lava (sides/top): digging floods. Even demolish does not bypass. |
| safety | tools.mjs:656 | use_item | 1 | yes | yes | keep | will not use ${args.item} on ${block.name} at ${fmt(pos)}: ${why} (override: true uses it  | use_item on protected home/build blocks (e.g. pouring water/lava); override:true exists. |
| safety | build.mjs:53 | constructor | 0 | no | no | keep | air is only for named builds | Air (digging) only in named builds, so house plan cannot dig out its own walls. |
| safety | build.mjs:284 | placeOne | 0 | no | no | keep | site blocked by ${found.name} at ${pos} | Refuses to dig non-clearable, non-natural blocks at a house cell; protects walls, chests, user structures. |
| safety | build.mjs:296 | placeOne | 0 | no | no | keep | site blocked by ${current.name} at ${pos} | Same removable() rule re-checked after walking; protects non-natural blocks from being dug. |
| safety | build.mjs:335 | clearCell | 0 | no | no | keep | cannot clear ${found.name} at ${pos} | clearCell refuses to dig blocks outside removable() set; protects built structure. |
| safety | build.mjs:374 | placeDoor | 0 | no | no | keep | site blocked by ${found.name} at ${pos} | Door site occupied by non-clearable block; refuses to dig it. |
| safety | build.mjs:396 | placeDoor | 0 | no | no | keep | site blocked by ${current.name} at ${pos} | Re-check before breaking the old block at door site; only plants/old door allowed. |
| safety | facilities.mjs:909 | mineLayout | 0 | yes | no | keep | no safe shaft down to y ${bottom} in these zones (the deepest safe one reaches y ${deepest | No shaft path free of lava/water/cave danger; message names deepest safe y and options. |
| safety | home.mjs:744 | leaveHome | 0 | no | yes | keep | staying inside: ${why} | Refuses to leave the home while sheltering from threats; confront option bypasses for cleared goal. |
| safety | primitives.mjs:544 | leaveHouse | 0 | no | yes | keep | staying inside: ${why} | Leaving while hostiles wait at the door risks death and item loss; confront already bypasses it for fighting. |
| safety | primitives.mjs:1320 | climb_out | 0 | no | no | keep | ${roof.name} above at ${roof.position.x},${roof.position.y},${roof.position.z}: not piling | Piling up into water or lava above in a closed cell is dangerous. |
| safety | primitives.mjs:1382 | climb_down | 0 | no | no | keep | the block under your feet is not a free-standing pillar (natural ground or a wall beside i | Digging a block that is not a free-standing pillar (liquid or open sides) risks liquid or a long fall. |
| safety | tools.mjs:293 | goto | 0 | no | no | keep | will not walk to ${fmt(pos)}, next to the home: ${CREEPER_HOME_REASON} | Creeper within range and target within 12 of door: prevents leading it to blow up home walls. Radius heuristic. |
| safety | tools.mjs:361 | place | 0 | yes | yes | keep | will not place at ${fmt(pos)}: ${why} (override: true places it anyway if you mean to)${st | Place inside current home/build/house-in-progress cells; override:true already exists. |
| safety | tools.mjs:523 | move_furniture | 0 | yes | yes | keep | will not move ${block.name} at ${fmt(pos)}: ${guard} (override: true moves it anyway if yo | Moving non-home furniture that belongs to a build/home plan; override:true exists. |
| safety | tools.mjs:789 | callTool | 0 | yes | no | keep | staying inside: too dangerous to go out (${why}).  | Sheltering at extreme risk or creeper at door; steps outside refused. Attack allowed in day. |
| fact | tools.mjs:160 | within | 494 | yes | no | keep-fact | ${what} is ${round(d)}m away (at most ${max}m)${hint}${sign} | Reach cap (32 dig/place, 256 goto); mostly catches sign/swap typos and hints the fix. Cap itself arbitrary. |
| fact | tools.mjs:100 | short | 240 | yes | no | keep-fact | cannot craft ${item} x${times}: needs ${lacks.join(' and ')};  | Ingredients missing; states exact shortfall and points to how_to_get. |
| fact | tools.mjs:281 | do_suggestion | 190 | yes | no | keep-fact | ${id} is not a current suggestion${shown.length ? ' (now: ${shown.join('; ')})' : ''} | Candidate id not in current list (after loose matching); lists current ones. |
| fact | tools.mjs:323 | dig | 164 | yes | no | keep-fact | there is ${block.name} at ${fmt(pos)}, nothing to dig${nearbySolids(bot, state, pos)} | Target has no collision block (air etc.); lists nearby solids. |
| fact | tools.mjs:297 | goto | 102 | yes | no | keep-fact | will not walk to ${fmt(pos)}: you are in a closed space (${rock.cells} cells you can walk  | Closed space and target beyond MAX_DIG_GAP of rock: pathfinder predictably times out. Prediction, not policy; exit given. |
| fact | primitives.mjs:952 | afterDig | 96 | no | no | keep-fact | dug ${c.block} but picked nothing up (${dropsAround(bot)}${why ? '; path: ${why}' : ''}${f | Dug but nothing entered the inventory: world result, includes full-inventory reason. |
| fact | tools.mjs:417 | eat | 90 | yes | no | keep-fact | ${args.item} is not food.  | Item is not food; lists food carried. |
| fact | primitives.mjs:2246 | till | 88 | yes | no | keep-fact | nothing to till at ${c.pos.x},${c.pos.y},${c.pos.z} any more (${why}) | Block not tillable or covered; message names blocker and next step. |
| fact | tools.mjs:399 | pickup | 80 | no | no | keep-fact | no item on the ground within ${DROP_RADIUS}m | No dropped item within 16 m. |
| fact | primitives.mjs:2411 | use_item | 60 | no | no | keep-fact | nothing changed after using ${what} ${on} | Using the item changed nothing: world result. |
| fact | tools.mjs:348 | dig | 56 | yes | no | keep-fact | ${block.name} drops nothing without one of: ${tools.join(', ')} | Block drops nothing without required tool (exempt in closed space underground). Also blocks clearing by hand; consider warn only. |
| fact | tools.mjs:485 | withdraw | 48 | no | no | keep-fact | no chest is known to hold ${args.item} | No remembered chest holds the item. |
| fact | move.mjs:153 | walkTo | 44 | no | no | keep-fact | stuck: ${why} at ${where(stuck)} (tried: ${tried.join(', ')}) | Stuck timeout after escapes; 44 times, reports what was tried. |
| fact | primitives.mjs:113 | arrivalReport | 43 | no | no | keep-fact | did not reach ${target.x},${target.y},${target.z}: stopped at ${at}  | Position after walking is not the goal: reports what the world did. |
| fact | tools.mjs:365 | place | 43 | yes | no | keep-fact | you are standing in ${fmt(pos)}; move first | Target is the bot own feet/head cell; move first. |
| fact | primitives.mjs:103 | arrivalReport | 39 | no | no | keep-fact | did not reach ${target.x},${target.y},${target.z}: you are in the water at ${at}, not on l | Swimming and target is not water: the world says you have not arrived. |
| fact | primitives.mjs:2222 | plant | 39 | yes | no | keep-fact | no free ground for a sapling at ${c.pos.x},${c.pos.y},${c.pos.z} any more (${why}) | Ground or cell not plantable; message names the blocker and next step. |
| fact | primitives.mjs:2107 | level | 34 | no | no | keep-fact | could not level any of the ground${left} | Nothing could be levelled; reports failure with reasons. |
| fact | primitives.mjs:1291 | leave_burrow | 29 | no | no | keep-fact | climbed up but still cannot step out of the hole | Climbed up but cannot step out: world result. |
| fact | tools.mjs:387 | craft | 29 | yes | no | keep-fact | ${item} needs a crafting table within reach; craft one (4 planks) and place it | Recipe needs a table and none in reach. |
| fact | primitives.mjs:2189 | fence | 28 | no | no | keep-fact | could not place any fence around ${fac.name}${skipped.size ? ' (${[...skipped.values()].sl | No fence placed; skip reasons listed: world result. |
| fact | primitives.mjs:875 | checkHarvestable | 24 | yes | no | keep-fact | no tool that can mine ${block.name}${last ? ' (the ${last.item} broke)' : ''}: make one fi | Bare hands drop nothing from this block (unless trapped): the world makes the dig meaningless. |
| fact | tools.mjs:577 | sow | 23 | yes | no | keep-fact | ${fmt(pos)} is not farmland (give the farmland block itself) | Target is not farmland. |
| fact | primitives.mjs:1161 | pickup | 20 | no | no | keep-fact | reached the item but picked nothing up (${dropsAround(bot)}${full}) | Reached the item but gained nothing: world result. |
| fact | craft.mjs:110 | craftWithRecipeBook | 18 | no | no | keep-fact | crafting ${itemName} produced nothing (missing ingredients?) | Craft produced nothing; ingredients missing. Message only guesses the cause (18 times). |
| fact | primitives.mjs:2611 | placeBuildBlock | 18 | no | no | keep-fact | there is no build named ${name} | No build with that name: wrong argument. |
| fact | tools.mjs:505 | build_next | 18 | no | no | keep-fact | there is no build named ${name} | Unknown build name. |
| fact | home.mjs:478 | enterHome | 17 | no | no | keep-fact | No path to the home's door (${describeDoorstep(bot, home)}) | No path to the door; 17 times, message gives doorstep description only. |
| fact | primitives.mjs:2060 | place_at | 17 | no | no | keep-fact | the ${c.item} was not placed | Cell unchanged after placing: server refused. |
| fact | tools.mjs:77 | position | 16 | yes | no | keep-fact | x, y and z must be numbers: ${bad.join(' and ')} ${bad.length > 1 ? 'are' : 'is'} missing  | Malformed or missing x/y/z; names which one is missing. |
| fact | tools.mjs:191 | entity | 16 | yes | no | keep-fact | no mob with id ${args.entity} nearby (use the ids in mobs) | Entity id not found among nearby mobs. |
| fact | tools.mjs:772 | callTool | 16 | no | no | keep-fact | unknown tool ${name} | Unknown tool name. |
| fact | primitives.mjs:2330 | ride_boat | 13 | no | no | keep-fact | no water to cross toward ${c.target} within ${BOARD_WITHIN_M}m (the way there is land) | No water to cross; the way is land: world fact. |
| fact | camp.mjs:140 | buildCamp | 12 | no | no | keep-fact | nothing to place against at ${cell.x},${cell.y},${cell.z} | No adjacent solid block to place against; fired 12 times, geometry of the site. |
| fact | land.mjs:119 | reachLand | 12 | no | no | keep-fact | could not climb onto land: ${why} | Could not climb out after tries; 12 times, only reason given. |
| fact | primitives.mjs:1306 | climb_out | 10 | no | no | keep-fact | piled up ${raised} blocks and ran out of dirt and cobblestone | Ran out of filler blocks: missing item. |
| fact | primitives.mjs:1303 | climb_out | 9 | no | no | keep-fact | piled up ${raised} blocks and still cannot walk out | Piled up the height cap and still shut in: reports the outcome of an unbounded loop stop. |
| fact | tools.mjs:384 | craft | 9 | no | no | keep-fact | ${item} has no crafting recipe | No crafting recipe for the item. |
| fact | tools.mjs:556 | till | 9 | yes | no | keep-fact | ${fmt(pos)} is not dirt or grass (give the ground block itself) | Hoe works only on dirt/grass/path. |
| fact | tools.mjs:448 | smelt | 8 | yes | no | keep-fact | no fuel (coal, charcoal, planks or logs) | No usable fuel in inventory; lists fuels. |
| fact | tools.mjs:687 | find_blocks | 8 | no | no | keep-fact | unknown block ${args.block} | Unknown block name. |
| fact | home.mjs:443 | fillDoorstep | 7 | yes | no | keep-fact | the floor in front of the door is missing at ${cell.x},${cell.y},${cell.z} and you carry n | Nothing to fill the doorstep hole with; names dirt or cobblestone. Fired 7 times. |
| fact | primitives.mjs:1287 | leave_burrow | 7 | no | no | keep-fact | opened the top but carry no dirt or cobblestone to climb out with | No dirt or cobblestone to climb out with: missing item. |
| fact | home.mjs:438 | fillDoorstep | 6 | no | no | keep-fact | the floor in front of the door is a hole deeper than ${DOORSTEP_DEPTH} blocks (${describeD | Doorstep hole deeper than can be filled from below; fired 6 times, no way out offered. |
| fact | tools.mjs:665 | ride_boat | 6 | yes | no | keep-fact | you have no boat (craft one: 5 planks of one wood at a crafting table) | No boat item and not riding; recipe given. |
| fact | primitives.mjs:1712 | defrag | 5 | no | no | keep-fact | no chest in the home | No chest recorded in the home: missing target. |
| fact | primitives.mjs:2031 | recover_items | 5 | no | no | keep-fact | nothing left to pick up where you died (${dropsAround(bot)}) | Nothing left where you died: world fact. |
| fact | tools.mjs:185 | holding | 5 | no | no | keep-fact | item is required | Required argument item missing. |
| fact | tools.mjs:454 | deposit | 5 | yes | no | keep-fact | no chest in the home: to free inventory slots, throw spare items away (toss); or place a c | No chest in home to deposit into; offers toss or craft chest. |
| fact | tools.mjs:483 | withdraw | 5 | no | no | keep-fact | item is required | Required argument item missing. |
| fact | home.mjs:708 | repairWall | 4 | no | no | keep-fact | no ${kind} to close the wall at ${pos} | No log/planks to close the dug wall. |
| fact | primitives.mjs:1592 | bed_here | 4 | no | no | keep-fact | no flat free spot for a bed here | No flat free spot for the bed: no space. |
| fact | primitives.mjs:2275 | place_water | 3 | no | no | keep-fact | no water_bucket | No water bucket: missing item. |
| fact | tools.mjs:382 | craft | 3 | yes | no | keep-fact | ${item} is not an item name; use one of ${named.slice(0, 6).join(', ')} (the wood of the p | Item is a kind word, not an item name; lists real names. |
| fact | tools.mjs:629 | use_item | 3 | yes | no | keep-fact | without an item, say which block to click with an empty hand: x, y, z | Bare-hand click needs coordinates. |
| fact | build.mjs:304 | placeOne | 2 | yes | no | keep-fact | no ${b.block} left in the inventory (gather more, then continue the build) | Out of the block item; message says gather more and continue. |
| fact | build.mjs:392 | placeDoor | 2 | no | no | keep-fact | no place to stand in front of the door at ${pos}: ${error.message} | Pathing to either side of the door failed; world reachability. |
| fact | home.mjs:410 | jumpAndPlace | 2 | no | no | keep-fact | could not jump up from the hole at ${cell} | Could not jump out of hole before timeout. |
| fact | leveling.mjs:54 | levelTarget | 2 | yes | no | keep-fact | where must be home or x,y,z (y: the height to stand on, the ground is y-1) | Malformed where argument; shows the format. |
| fact | primitives.mjs:435 | swimAcross | 2 | no | no | keep-fact | swam ${r.moved}m toward ${what} but ${r.why}: you are still in the water at ${round(p.x)}, | Swim ended without landing; reports what the swim did. |
| fact | tools.mjs:534 | plant | 2 | yes | no | keep-fact | item must be a sapling you have (e.g. oak_sapling) | Item is not a sapling; example given. |
| fact | tools.mjs:549 | till | 2 | yes | no | keep-fact | you have no hoe (craft one: 2 sticks + 2 planks or cobblestone) | No hoe in inventory; recipe given. |
| fact | tools.mjs:571 | sow | 2 | yes | no | keep-fact | item must be seeds you have: ${Object.values(CROPS).map((c) => c.seed).join(', ')} | Item not a known seed; lists seeds. |
| fact | tools.mjs:575 | sow | 2 | yes | no | keep-fact | no free farmland for ${item} in the farms in view (till the farm's cells first) | No tilled farmland available to sow. |
| fact | home.mjs:336 | walkInto | 1 | no | no | keep-fact | could not walk to ${cell} (${why}) | Walk-through timed out; stall description attached. |
| fact | knowledge.mjs:143 | resolve | 1 | yes | no | keep-fact | unknown item or group: ${spec}${this.didYouMean(spec)} | Unknown item or group; did-you-mean suggestion. |
| fact | primitives.mjs:659 | flee | 1 | no | no | keep-fact | could not get away from ${h.name}: still ${left}m${why ? ' (${why})' : ' after ${STUCK_MS  | Could not get away from the hostile: reports the outcome of fleeing. |
| fact | primitives.mjs:1145 | pickup | 1 | no | no | keep-fact | the item is gone | Dropped item entity gone: target gone. |
| fact | primitives.mjs:1216 | dig_in | 1 | yes | no | keep-fact | dug the hole but could not seal it (${!item ? 'no dirt or cobblestone to seal with' : 'not | No sealing material or no support beside the top: missing item or space. |
| fact | primitives.mjs:1347 | dig_out | 1 | no | no | keep-fact | no way to dig out: ${route.why} | No diggable route from the closed space; reason given by the route search. |
| fact | primitives.mjs:1744 | smelt | 1 | no | no | keep-fact | no furnace at ${c.pos.x},${c.pos.y},${c.pos.z} any more | Furnace block gone: target gone. |
| fact | primitives.mjs:2267 | sow | 1 | no | no | keep-fact | the ${c.item} was not sown | Seed not sown: server result. |
| fact | primitives.mjs:2320 | place_water | 1 | no | no | keep-fact | the water ran onto the farm at ${[...new Set(where)].join(' ')} instead of staying in the  | Water ran onto the farm after placing; post-hoc report with scoop-back result. |
| fact | primitives.mjs:2355 | use_item | 1 | no | no | keep-fact | the ${c.target} is gone | Entity gone: target gone. |
| fact | primitives.mjs:2415 | use_item | 1 | yes | no | keep-fact | the bucket was not filled (${changes.join(', ')}): stand within reach of the water source  | Bucket not filled; message says aim at the water source. |
| fact | tools.mjs:430 | smelt | 1 | no | no | keep-fact | ${args.input} cannot be smelted | Item has no smelting product. |
| fact | tools.mjs:510 | build_next | 1 | no | no | keep-fact | the house plan is complete | House plan already complete. |
| fact | tools.mjs:585 | water | 1 | yes | no | keep-fact | you have no water_bucket (craft a bucket: 3 iron ingots, then use_item {item: bucket, x,y, | No water bucket in inventory; recipe given. |
| fact | tools.mjs:607 | level | 1 | yes | no | keep-fact | only holes are left (${s.fills.length} blocks to fill) and you carry ${carried} dirt: gath | Only holes left and not enough dirt carried. |
| fact | tools.mjs:774 | callTool | 1 | no | no | keep-fact | another action is running | Another action running; concurrency limit. |
| fact | boat.mjs:176 | placeBoat | - | no | no | keep-fact | no boat | No boat item in inventory; nothing to place. |
| fact | build.mjs:52 | constructor | 0 | no | no | keep-fact | unknown block kind: ${b.block} | Malformed plan: block kind not in vocabulary. |
| fact | build.mjs:302 | placeOne | 0 | no | no | keep-fact | no reference face for ${b.block} at ${pos} | No face to place against from reachable position; geometry fact. |
| fact | build.mjs:404 | placeDoor | 0 | no | no | keep-fact | no door to place (the old door was not picked up) | Old door was not picked up; no item to place. |
| fact | camp.mjs:138 | buildCamp | 0 | no | no | keep-fact | ran out of blocks after placing ${placed} | Out of camp blocks mid-build. |
| fact | camp.mjs:148 | buildCamp | 0 | no | no | keep-fact | could not place at ${cell.x},${cell.y},${cell.z}: ${e.message} | Server/placement call failed twice. |
| fact | camp.mjs:153 | buildCamp | 0 | no | no | keep-fact | the block at ${cell.x},${cell.y},${cell.z} did not stay | Placed block not present afterwards; world state mismatch. |
| fact | craft.mjs:88 | craftWithRecipeBook | 0 | no | no | keep-fact | no unlocked recipe for ${itemName}${table ? '' : ' in the 2x2 grid'} | No unlocked recipe or fits-grid recipe; world/recipe-book fact. |
| fact | facilities.mjs:211 | unionBox | 0 | yes | no | keep-fact | boxes: give the zones as {min_x, min_z, max_x, max_z} | Malformed boxes argument; message shows the format. |
| fact | facilities.mjs:214 | unionBox | 0 | no | no | keep-fact | boxes: ${k} must be an integer | Non-integer coordinate in boxes. |
| fact | facilities.mjs:241 | groundCells | 0 | yes | no | keep-fact | the area is not loaded (too far from the bot): go near it before designing | Area not loaded; go near before designing. |
| fact | facilities.mjs:383 | registerFacility | 0 | yes | no | keep-fact | the name must be lowercase letters, digits and _ (2-32) | Malformed facility name. |
| fact | facilities.mjs:385 | registerFacility | 0 | no | no | keep-fact | there is already a facility named ${name} | Duplicate facility name. |
| fact | facilities.mjs:387 | registerFacility | 0 | yes | no | keep-fact | kind must be one of ${DESIGNABLE.join(', ')} | Unknown facility kind; lists valid kinds. |
| fact | facilities.mjs:398 | registerFacility | 0 | yes | no | keep-fact | crop must be one of ${Object.keys(CROPS).join(', ')} | Unknown crop; lists valid crops. |
| fact | facilities.mjs:410 | registerFacility | 0 | yes | no | keep-fact | sapling must be sapling (any kind) or one kind like oak_sapling | Unknown sapling kind. |
| fact | facilities.mjs:842 | mineLayout | 0 | yes | no | keep-fact | bottom_y must be ${MINE_BOTTOM.min}-${MINE_BOTTOM.max} | bottom_y bounded by bedrock and build height; world limit. |
| fact | facilities.mjs:860 | mineLayout | 0 | no | no | keep-fact | the zones are smaller than the 4 x 4 shaft | Zones smaller than the 4x4 shaft; area too small. |
| fact | facilities.mjs:874 | mineLayout | 0 | yes | no | keep-fact | bottom_y ${bottom} is not below the ground (y ${y0 - 1}) by ${MINE_MIN_STEPS} or more | Shaft needs stairs depth; bottom_y too shallow, meaningless otherwise. |
| fact | facilities.mjs:908 | mineLayout | 0 | yes | no | keep-fact | the area is not loaded (too far from the bot): go near it before designing | Area not loaded; go near before designing. |
| fact | facilities.mjs:910 | mineLayout | 0 | no | no | keep-fact | no ground for the mine entrance in these zones (houses, builds, other facilities or no sol | No ground for mine entrance (houses, builds, other facilities, no solid ground). |
| fact | goals.mjs:87 | makeGoal | 0 | yes | no | keep-fact | dig_depth must be 0-${MAX_DIG_DEPTH} | dig_depth out of range; cap above world height, effectively malformed. |
| fact | goals.mjs:106 | together | 0 | yes | no | keep-fact | together must be a list of conditions | together is not a list; malformed. |
| fact | goals.mjs:110 | together | 0 | yes | no | keep-fact | ${m?.predicate} cannot be in a bundle; only conditions judged from the world: ${CONDITION_ | Predicate cannot be judged from the world; vocabulary limit, lists allowed ones. |
| fact | goals.mjs:148 | validKeep | 0 | no | no | keep-fact | keep must be a list of {item, count} | keep is not a list; malformed. |
| fact | goals.mjs:152 | validKeep | 0 | no | no | keep-fact | keep: count must be a positive integer, got ${count} | keep count not a positive integer; malformed. |
| fact | goals.mjs:164 | validGoal | 0 | yes | no | keep-fact | count must be 1-${MAX_COUNT} | Count out of 1-256; malformed range. |
| fact | goals.mjs:170 | validGoal | 0 | yes | no | keep-fact | count must be 1-${MAX_COUNT} | Count out of 1-256; malformed range. |
| fact | goals.mjs:188 | validGoal | 0 | no | no | keep-fact | there is no item ${spec.item} | Bed item name does not exist in registry. |
| fact | goals.mjs:202 | validGoal | 0 | yes | no | keep-fact | distance must be ${MIN_EXPLORE}-${MAX_EXPLORE} | Explore distance outside 8-256; range of input. |
| fact | goals.mjs:211 | validGoal | 0 | yes | no | keep-fact | count must be 1-${planned} (the candidate sites) | Survey count exceeds candidate sites that exist. |
| fact | goals.mjs:239 | validGoal | 0 | no | no | keep-fact | ${name} was not designed (it was already there): facility() is for designed farms and tree | Facility was not designed, so has no layout to check. |
| fact | goals.mjs:245 | validGoal | 0 | no | no | keep-fact | planted takes sapling (any kind) or one kind like oak_sapling | Unknown sapling kind. |
| fact | goals.mjs:247 | validGoal | 0 | yes | no | keep-fact | count must be 1-${MAX_PLANTED} | Planted count outside 1-32; input range. |
| fact | goals.mjs:253 | validGoal | 0 | yes | no | keep-fact | farmed takes one of ${Object.keys(CROPS).join(', ')} | Unknown crop; lists valid crops. |
| fact | goals.mjs:255 | validGoal | 0 | yes | no | keep-fact | count must be 1-${MAX_FARMED} | Farmed count outside 1-64; input range. |
| fact | goals.mjs:266 | validGoal | 0 | yes | no | keep-fact | unknown predicate ${predicate}; use one of ${PREDICATES.join(', ')} | Unknown predicate; lists valid ones. |
| fact | goals.mjs:701 | evaluateOne | 0 | no | no | keep-fact | the check ${goal.spec.name} is gone | Referenced custom check no longer exists. |
| fact | goals.mjs:1083 | checkConditions | 0 | yes | no | keep-fact | ${spec.predicate} cannot be a condition; use one of ${CONDITION_PREDICATES.join(', ')} | Predicate cannot be a condition; lists allowed ones. |
| fact | home.mjs:206 | waitDoor | 0 | no | no | keep-fact | no upper half | Door has no upper half in the world. |
| fact | home.mjs:214 | setDoor | - | no | no | keep-fact | no door at ${home.door} | No door at the recorded position. |
| fact | home.mjs:454 | fillDoorstep | 0 | no | no | keep-fact | could not fill the hole in front of the door at ${cell.x},${cell.y},${cell.z} | Filled cell not solid afterwards. |
| fact | home.mjs:480 | enterHome | 0 | no | no | keep-fact | could not stand in front of the door (${describeDoorstep(bot, home)}) | Could not stand at door level. |
| fact | home.mjs:488 | enterHome | - | no | no | keep-fact | ${e.message} (${describeDoorstep(bot, home)}) | Walking in through the door failed; world state. |
| fact | knowledge.mjs:133 | resolve | 0 | yes | no | keep-fact | unknown item tag: ${spec}${this.didYouMeanTag(spec.slice(1))} | Unknown tag; did-you-mean suggestion. |
| fact | land.mjs:104 | reachLand | 0 | yes | no | keep-fact | you are shut in by walls: the land you can see is outside them, so swimming cannot reach i | Walled in so swimming cannot reach land; message names dig_out. |
| fact | land.mjs:113 | reachLand | 0 | yes | no | keep-fact | no land within ${LAND_RADIUS} m in the loaded world${tried.length ? ' besides the ones tha | No reachable land in loaded world; suggests swimming on or finding a boat. |
| fact | leveling.mjs:49 | levelTarget | 0 | yes | no | keep-fact | there is no home yet | No home yet; precondition of the world. |
| fact | move.mjs:147 | walkTo | 0 | no | no | keep-fact | stuck: did not get closer to the goal in ${noProgressMs * WORKING_FACTOR / 1000}s while di | No-progress timeout while digging and placing; reports what was tried. |
| fact | placing.mjs:69 | parseWhere | 0 | yes | no | keep-fact | placed(…, home) needs a home: there is none yet (the house is not finished). Use build:<na | No home yet; suggests build:name or x,y,z. |
| fact | placing.mjs:73 | parseWhere | 0 | yes | no | keep-fact | placed(…, near_home) needs a home: there is none yet (the house is not finished). Use buil | No home yet; suggests build:name or x,y,z. |
| fact | placing.mjs:79 | parseWhere | 0 | no | no | keep-fact | there is no build named ${b[1].trim()} yet | Named build does not exist. |
| fact | placing.mjs:84 | parseWhere | 0 | yes | no | keep-fact | where must be home, near_home, build:<name> or x,y,z | Malformed where; lists valid forms. |
| fact | placing.mjs:92 | checkPlacedSpec | 0 | yes | no | keep-fact | placed needs an item that is a block when placed (torch, lantern, oak_planks, …), got ${sp | Item is not a block; names examples. |
| fact | placing.mjs:94 | checkPlacedSpec | 0 | yes | no | keep-fact | count must be 1-${MAX_COUNT} | Count out of 1-64; input range. |
| fact | primitives.mjs:146 | gatherScaffold | 0 | no | no | keep-fact | the log at ${log.x},${log.y},${log.z} is ${height} blocks above the ground: reaching it ta | Reaching a high log needs scaffold dirt; none within reach to dig: material fact. |
| fact | primitives.mjs:147 | gatherScaffold | 0 | no | no | keep-fact | dug ${dug - 1} blocks of dirt and still carry only ${have} of the ${need} needed to reach  | Dug the cap of dirt blocks and still short; reports failure to gather. |
| fact | primitives.mjs:414 | swimInstead | 0 | no | no | keep-fact | ${why.message}; you are in open water and no shore is loaded within ${CROSSING_LOOK_M}m to | Open water and no loaded shore: nothing to swim to. |
| fact | primitives.mjs:420 | swimInstead | 0 | no | no | keep-fact | ${note}swam ${r.moved}m toward the shore at ${round(shore.x)},${round(shore.z)} but ${r.wh | Swim ended without landing; reports what the swim did. |
| fact | primitives.mjs:545 | leaveHouse | 0 | no | no | keep-fact | the way out through the door is gone (no floor in front of it, or no ground within ${EXIT_ | No exit route exists from the house: geometry fact. |
| fact | primitives.mjs:1103 | dig | 0 | no | no | keep-fact | ${c.block} is gone from ${c.pos} | Block no longer at that position: target gone. |
| fact | primitives.mjs:1167 | attack | - | no | no | keep-fact | ${c.target} is gone | Entity gone: target gone. |
| fact | primitives.mjs:1181 | wake_sleeper | - | no | no | keep-fact | ${c.target} is gone | Entity gone: target gone. |
| fact | primitives.mjs:1190 | wake_sleeper | 0 | no | no | keep-fact | hit the ${c.target} once but it is still asleep | Hit it once and it still sleeps: world result. |
| fact | primitives.mjs:1198 | dig_in | 0 | no | no | keep-fact | no place to dig in within reach: ${why} | No diggable spot in reach; reason is given: world fact. |
| fact | primitives.mjs:1222 | dig_in | 0 | no | no | keep-fact | dug the hole but the top at ${seal.x},${seal.y},${seal.z} is still open | Seal block did not appear: server result. |
| fact | primitives.mjs:1233 | wall_in | 0 | yes | no | keep-fact | you are in the water: swim to land first (reach_land) | Cannot build a camp while swimming: states the way out. |
| fact | primitives.mjs:1235 | wall_in | 0 | no | no | keep-fact | no place to make a camp within reach: ${why} | No camp site in reach; reason given: world fact. |
| fact | primitives.mjs:1241 | wall_in | 0 | no | no | keep-fact | the camp needs ${site.need} blocks and you carry ${campCount(bot)}: no dirt within reach t | No dirt within reach to gather for the camp: material fact. |
| fact | primitives.mjs:1242 | wall_in | 0 | no | no | keep-fact | dug ${dug - 1} blocks of dirt and still carry only ${campCount(bot)} of the ${site.need} n | Dug the cap and still short of blocks; reports failure. |
| fact | primitives.mjs:1249 | wall_in | 0 | no | no | keep-fact | the camp spot is no longer usable: ${again} | Camp spot became unusable after walking: world changed. |
| fact | primitives.mjs:1251 | wall_in | 0 | no | no | keep-fact | placed ${placed} blocks but still not enclosed (a wall or the roof is missing) | Placed blocks but not enclosed: world result. |
| fact | primitives.mjs:1310 | climb_out | 0 | no | no | keep-fact | the space above is not loaded | Chunk above not loaded: world fact. |
| fact | primitives.mjs:1365 | dig_out | 0 | no | no | keep-fact | dug ${dug} blocks and still cannot walk out | Dug the round cap and still shut in; reports outcome (blocked-until is set in the catch). |
| fact | primitives.mjs:1396 | climb_down | 0 | no | no | keep-fact | dug down ${dug} blocks and still stand over a drop at ${at.x},${at.y},${at.z} | Dug the pillar down and still over a drop: world result. |
| fact | primitives.mjs:1425 | equip | - | no | no | keep-fact | no ${c.item} | Item not in inventory: missing item. |
| fact | primitives.mjs:1436 | craft | 0 | yes | no | keep-fact | no crafting table nearby${carried ? ': you carry a crafting_table, place it next to you (p | No crafting table nearby; message says place the carried one. |
| fact | primitives.mjs:1442 | craft | 0 | yes | no | keep-fact | ${e.message} (the crafting table at ${table.position.x},${table.position.y},${table.positi | Table behind walls unreachable; message says place your own. |
| fact | primitives.mjs:1471 | place_station | - | no | no | keep-fact | no ${c.item} | Item not in inventory: missing item. |
| fact | primitives.mjs:1475 | place_station | 0 | no | no | keep-fact | no free spot for the ${c.item} | No free spot to place the station: no space. |
| fact | primitives.mjs:1542 | place_bed | 0 | no | no | keep-fact | no free spot for the bed in the house | No free bed spot in the house: no space. |
| fact | primitives.mjs:1557 | place_bed | 0 | no | no | keep-fact | the bed was not placed | Bed block absent after placing: server result. |
| fact | primitives.mjs:1558 | place_bed | 0 | no | no | keep-fact | the bed was placed facing the wrong way (head not at ${head}) | Bed head missing, wrong facing: server result. |
| fact | primitives.mjs:1572 | sleep | 0 | no | no | keep-fact | woke up but the night was not skipped (time ${tod}) | Night not skipped after sleeping: world result. |
| fact | primitives.mjs:1585 | sleep_at | 0 | no | no | keep-fact | no bed at ${c.pos.x},${c.pos.y},${c.pos.z} any more | Bed block gone: target gone. |
| fact | primitives.mjs:1590 | bed_here | 0 | no | no | keep-fact | no bed to place | No bed in inventory: missing item. |
| fact | primitives.mjs:1597 | bed_here | 0 | no | no | keep-fact | the bed was not placed | Bed block absent after placing: server result. |
| fact | primitives.mjs:1608 | exit_wall | 0 | no | no | keep-fact | closed the wall again but is still inside | Still inside after closing the wall: world result. |
| fact | primitives.mjs:1645 | place_chest | 0 | no | no | keep-fact | no free spot for a chest in the house | No free chest spot in the house: no space. |
| fact | primitives.mjs:1647 | place_chest | - | no | no | keep-fact | no chest | No chest in inventory: missing item. |
| fact | primitives.mjs:1651 | place_chest | 0 | no | no | keep-fact | the chest was not placed | Chest absent after placing: server result. |
| fact | primitives.mjs:1657 | deposit | 0 | no | no | keep-fact | no ${c.item} to put in | Item not carried: missing item. |
| fact | primitives.mjs:1677 | toss | 0 | no | no | keep-fact | nothing to throw away: it is all gone from the inventory | Nothing left to throw: missing item. |
| fact | primitives.mjs:1726 | withdraw | 0 | no | no | keep-fact | no ${c.item} in the chest (the record was wrong) | Chest lacks the item; record was wrong: world fact. |
| fact | primitives.mjs:1751 | surface | 0 | yes | no | keep-fact | no known way out (nowhere higher that you stood): dig upward with a pickaxe, or explore to | No known exit upward; message suggests digging up or exploring. |
| fact | primitives.mjs:1765 | surface | 0 | no | no | keep-fact | could not get to the surface by the ways known (${exits.length}): ${last?.message ?? 'stil | All known exits failed: reports outcome. |
| fact | primitives.mjs:1770 | place_torch_at | - | no | no | keep-fact | no torch | No torch in inventory: missing item. |
| fact | primitives.mjs:1775 | place_torch_at | 0 | no | no | keep-fact | no ground for a torch at ${c.pos.x},${c.pos.z} any more (${floor?.boundingBox !== 'block'  | Floor or cell at the spot changed: world fact. |
| fact | primitives.mjs:1779 | place_torch_at | 0 | no | no | keep-fact | the torch was not placed | Torch absent after placing: server result. |
| fact | primitives.mjs:1784 | place_torch | 0 | no | no | keep-fact | no floor to stand a torch on here | No floor for a torch: no space. |
| fact | primitives.mjs:1786 | place_torch | - | no | no | keep-fact | no torch | No torch in inventory: missing item. |
| fact | primitives.mjs:1789 | place_torch | 0 | no | no | keep-fact | the torch was not placed | Torch absent after placing: server result. |
| fact | primitives.mjs:1876 | dig_mine | 0 | no | no | keep-fact | there is no mine named ${c.facility} | No such mine facility: wrong argument. |
| fact | primitives.mjs:1894 | dig_mine | 0 | no | no | keep-fact | could not clear ${m.next.x},${m.next.y},${m.next.z} of the mine ${fac.name} (dug ${dug} ce | Same cell not cleared twice: world result. |
| fact | primitives.mjs:2005 | make_mine | 0 | no | no | keep-fact | could not design a mine down to y ${c.bottom_y} near the home: ${e.message} | Mine design failed with the facility reason: site fact. |
| fact | primitives.mjs:2015 | recover_items | 0 | no | no | keep-fact | no death is remembered | No death remembered: nothing to recover. |
| fact | primitives.mjs:2044 | place_at | - | no | no | keep-fact | no ${c.item} | Item not in inventory: missing item. |
| fact | primitives.mjs:2050 | place_at | 0 | yes | no | keep-fact | nothing to place against at ${c.against.x},${c.against.y},${c.against.z} any more (it is $ | Block to place against is gone: world changed; message advises next step. |
| fact | primitives.mjs:2069 | plug_hole | 0 | no | no | keep-fact | nothing next to ${c.pos.x},${c.pos.y},${c.pos.z} to plug against | No neighbouring block to plug against: no support. |
| fact | primitives.mjs:2080 | level | 0 | no | no | keep-fact | there is no designed farm named ${c.facility} to level | No such designed farm: wrong argument. |
| fact | primitives.mjs:2130 | level | 0 | no | no | keep-fact | nothing to place on below ${p.x},${p.y},${p.z} | Nothing below to place onto: world fact. |
| fact | primitives.mjs:2144 | level | 0 | no | no | keep-fact | standing in the hole | Would place into the cell you stand in: geometry fact. |
| fact | primitives.mjs:2167 | fence | 0 | no | no | keep-fact | there is no designed farm named ${c.facility} with a fence | No such farm with a fence: wrong argument. |
| fact | primitives.mjs:2197 | fence_gate | 0 | no | no | keep-fact | there is no designed farm named ${c.facility} with a gate | No such farm with a gate: wrong argument. |
| fact | primitives.mjs:2199 | fence_gate | 0 | no | no | keep-fact | no fence gate | No fence gate in inventory: missing item. |
| fact | primitives.mjs:2205 | fence_gate | 0 | no | no | keep-fact | nothing to place the gate on at ${against.x},${against.y},${against.z} | No block below the gate cell: no support. |
| fact | primitives.mjs:2212 | plant | - | no | no | keep-fact | no ${c.item} | No sapling in inventory: missing item. |
| fact | primitives.mjs:2227 | plant | 0 | no | no | keep-fact | the ${c.item} was not planted (${placed?.name ?? 'nothing'} there) | Sapling absent after placing: server result. |
| fact | primitives.mjs:2233 | till | - | no | no | keep-fact | no hoe | No hoe in inventory: missing item. |
| fact | primitives.mjs:2251 | till | 0 | no | no | keep-fact | the ground at ${c.pos.x},${c.pos.y},${c.pos.z} did not turn into farmland | Ground did not become farmland: server result. |
| fact | primitives.mjs:2256 | sow | - | no | no | keep-fact | no ${c.item} | No seed in inventory: missing item. |
| fact | primitives.mjs:2261 | sow | 0 | yes | no | keep-fact | no free farmland at ${c.pos.x},${c.pos.y},${c.pos.z} any more (${farmland?.name !== 'farml | Not farmland or covered; message gives next step. |
| fact | primitives.mjs:2288 | place_water | 0 | no | no | keep-fact | no free cell to stand on beside the water cell ${at} | No free neighbour cell to stand on: no space. |
| fact | primitives.mjs:2292 | place_water | 0 | no | no | keep-fact | ${at} is not loaded | Chunk not loaded: world fact. |
| fact | primitives.mjs:2297 | place_water | 0 | no | no | keep-fact | could not dig the hole at ${at} | Could not dig the hole: world result. |
| fact | primitives.mjs:2298 | place_water | 0 | no | no | keep-fact | no solid floor under the hole at ${at}: the water would drain away | No solid floor under the hole: water physically drains, placing is meaningless. |
| fact | primitives.mjs:2323 | place_water | 0 | no | no | keep-fact | the water was not placed at ${at} (${now ?? 'unloaded'} there; ${empty ? 'the bucket is em | Water absent after placing: server result. |
| fact | primitives.mjs:2328 | ride_boat | 0 | yes | no | keep-fact | no boat (craft one from 5 planks of one wood at a crafting table) | No boat carried; message says how to craft one. |
| fact | primitives.mjs:2338 | use_item | - | no | no | keep-fact | no ${c.item} | Item not in inventory: missing item. |
| fact | primitives.mjs:2365 | use_item | 0 | no | no | keep-fact | ${c.pos.x},${c.pos.y},${c.pos.z} is not loaded | Chunk not loaded: world fact. |
| fact | primitives.mjs:2435 | explore | 0 | no | no | keep-fact | could not walk ${c.target} from ${round(start.x)},${round(start.z)}${error ? ': ${error}'  | Could not walk in that direction: pathfinding failure. |
| fact | primitives.mjs:2448 | withOpenChest | 0 | no | no | keep-fact | no chest at ${c.pos.x},${c.pos.y},${c.pos.z} any more | Chest block gone: target gone. |
| fact | primitives.mjs:2589 | sleepIn | 0 | no | no | keep-fact | woke up but the night was not skipped (time ${tod}) | Night not skipped after sleeping: world result. |
| fact | tools.mjs:186 | holding | - | no | no | keep-fact | you have no ${item} | Item not in inventory. |
| fact | tools.mjs:321 | dig | 0 | no | no | keep-fact | the block at ${fmt(pos)} is not loaded | Chunk not loaded. |
| fact | tools.mjs:355 | place | 0 | no | no | keep-fact | ${fmt(pos)} is not loaded | Chunk not loaded. |
| fact | tools.mjs:356 | place | - | yes | no | keep-fact | ${fmt(pos)} is taken by ${cell.name}${nearbySpots(args, pos)} | Target cell occupied; lists free neighbouring spots. |
| fact | tools.mjs:369 | place | 0 | yes | no | keep-fact | nothing ${on ? 'on the ${on} side' : 'next to'} ${fmt(pos)} to place against (chests, tabl | No adjacent block to place against; lists spots. |
| fact | tools.mjs:398 | pickup | 0 | yes | no | keep-fact | the inventory is full and cannot take what lies on the ground: throw away spare items firs | Drops on ground but inventory full; says toss or deposit. |
| fact | tools.mjs:464 | toss | 0 | no | no | keep-fact | no place outside the town to throw the seeds away (the ground around the town is not loade | No standable place outside town loaded for tossing seeds. |
| fact | tools.mjs:470 | toss | 0 | no | no | keep-fact | nothing spare to throw away (dirt and cobblestone are kept up to ${RESERVE.dirt}; tools, f | Toss-all found nothing spare under keep rules; no-op, item arg can still target one item. |
| fact | tools.mjs:490 | go_home | 0 | no | no | keep-fact | there is no home yet | No home exists. |
| fact | tools.mjs:495 | sleep | 0 | no | no | keep-fact | there is no bed in the home | No bed in home. |
| fact | tools.mjs:506 | build_next | 0 | no | no | keep-fact | the build ${name} is complete | Build already complete. |
| fact | tools.mjs:509 | build_next | 0 | no | no | keep-fact | there is no house plan | No house plan exists. |
| fact | tools.mjs:526 | move_furniture | - | no | no | keep-fact | ${fmt(to)} is not free | Destination cell not free. |
| fact | tools.mjs:545 | plant | 0 | yes | no | keep-fact | ${fmt(pos)} is not free ground (give the cell above dirt or grass; it holds ${here \|\| 'not | Cell not air/clearable, or no solid block below. |
| fact | tools.mjs:603 | level | 0 | yes | no | keep-fact | radius must be ${MIN_LEVEL}-${MAX_LEVEL} | radius outside allowed range; range is stated. |
| fact | tools.mjs:617 | use_item | 0 | yes | no | keep-fact | target must be home_door (or give x, y, z) | Invalid target argument. |
| fact | tools.mjs:619 | use_item | 0 | no | no | keep-fact | you have no home door yet | No home door recorded. |
| fact | tools.mjs:624 | use_item | 0 | yes | no | keep-fact | face must be one of ${Object.keys(FACE_VECTORS).join(', ')} | Invalid face argument; valid set listed. |
| fact | tools.mjs:628 | use_item | 0 | yes | no | keep-fact | to use something on a mob you need an item | Entity use needs an item. |
| fact | tools.mjs:632 | use_item | 0 | no | no | keep-fact | the block at ${fmt(pos)} is not loaded | Chunk not loaded. |
| fact | tools.mjs:653 | use_item | 0 | no | no | keep-fact | the block at ${fmt(pos)} is not loaded | Chunk not loaded. |
| fact | tools.mjs:662 | ride_boat | 0 | no | no | keep-fact | x and z must be numbers | x,z not numbers. |
| fact | tools.mjs:726 | find_recipes | 0 | no | no | keep-fact | the recipe list is not loaded | Recipe index not loaded. |
| fact | tools.mjs:732 | uses_of | 0 | no | no | keep-fact | the recipe list is not loaded | Recipe index not loaded. |
| fact | tools.mjs:734 | uses_of | 0 | yes | no | keep-fact | unknown item ${item} (check the name with item_info) | Unknown item name. |
| fact | trail.mjs:54 | placeTrailTorch | 0 | no | no | keep-fact | the torch was not placed | Torch not present after placing; world mismatch. |
