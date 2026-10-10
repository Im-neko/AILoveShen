// Minecraft ブリッジ: Mineflayer のボット + 視点のミラー + Python 側のための HTTP API。
//
//   PUT  /goal {predicate, item?, count?, where?, distance?, keep?: [{item, count}], also?: [{item, count}],
//               together?: [spec…]}（together: 一緒に追う条件。束は 4 つまで、条件の述語だけ。設計書 44）
//                          -> 目標を設定する（不正なら理由を添えて 400）。目標の状態を返す。
//                             keep: 大目標のためにチェストに取っておくもの（この目標のために取り出さない）
//   GET  /observe          -> { busy, observation（survey: 調べた候補地の数字を含む）, needs, goal: {spec, met, remaining, lines, blocked} | null,
//                               candidates: [{id, verb, target, ...}] }
//   POST /act {id}         -> 候補をもう一度作り、この id の候補を終わるまで実行する。
//                             { ok, result, seconds }
//   POST /check {specs: [...]} -> 目標を設定せずに条件（have, built, placed）を判定する:
//                             [{ spec, met, lines }]（1つでも不正なら理由を添えて 400）
//   PUT  /build-plan       -> { blocks: [{x,y,z,block}], width, depth, height, design, site? } 建てる計画を設定する
//                             （design: モデルの設計図。できた家とともに取っておく。site: 建てる場所 {x, z}。
//                             建ち終わると家になり、前の家は formerHomes に残る）
//   GET  /build-plan       -> 建築の状態（placed/total、原点、場所、まだないブロックの先頭）
//   PUT  /builds/<name>   -> { blocks, size: {width, depth, height}, anchor, purpose } 名前付きの建物を登録する
//                             （docs/design/25_builds.md。原点はアンカーから決める。守るものに掛かれば 400 と理由）
//   GET  /builds           -> [{ name, purpose, title?, anchor, placed, total, complete, origin }]
//   PUT  /titles           -> { titles: { <建物・施設の名前>: <呼び名> } } 呼び名をつける（視聴者に見せ、話す名前。識別子は変えない。設計書 160）。{ set, refused }
//   PUT  /roads            -> { strips: [{min_x, min_z, max_x, max_z}] } 敷く道の細い列を受け取る（roads.mjs、設計書 167）。{ strips }
//   GET  /facilities       -> 施設（設計書 47 §5）: [{ name, kind, status, box, entrance?, center, distance_m, direction, contents }]
//                             （今ある家・建物・畑・植えた苗木を先に取り込む）
//   PUT  /facilities/<name> {kind, zones, boxes, purpose, design} -> 施設を設計どおりに登録する（畑: crop, width, depth、植林場: sapling, count, spacing、採掘場: bottom_y, tunnels, tunnel_length。設計書 47 §16）。
//                             実際のマスはブリッジが区画の中から選ぶ。直せる理由は 400、読み込まれていない区画は 409
//   GET  /worldmap         -> 視界の地図の様子: { sections, cells_added, cells_changed, samples, avg_ms, max_ms }（設計書 59）
//   GET  /memory           -> 覚えている場所を座標つきで: { places（種類ごと）, chests, furnaces, deaths }（search_memory）
//   GET  /zones?x=&z=[&cells=&size=] -> 街の理想図の区画の地形（zones.mjs、設計書 47 §6）: [{ id, min_x.., seen, water, lava, trees, built, stone, flat, height, facilities }]
//   GET  /items            -> アイテムとブロックの名前と日本語名 [{ name, ja }]（item_info の一覧）
//   GET  /items/<name>     -> 取り方（作る・焼く・掘る・モブ・育てる）、置けるか、食べられるか、何に使うか（item_info）
//   PUT  /conditions/<name> {description, code} -> Gemini が書いた判定を確かめて登録する（設計書 42）:
//                             { name, met, remaining, lines }。形が違う・動かない・もう成り立っていれば 400 と理由
//   GET  /conditions       -> [{ name, description }]
//   GET  /map              -> 家のまわりの真上から見た地図（map.mjs）: { center, radius, base_y, cells, home, builds }
//   POST /tool {name, args} -> 道具を 1 つ呼ぶ（設計書 21）: { ok, result, seconds, refused? }。行動の道具は /act と同じ経路で
//                             実行する。断るとき（安全の制約、引数が世界と合わない）は refused: true と理由
//   GET  /state            -> 共通の状態（実行中の行動の進み具合、まわりの形、モブ、欲求）。見張りの質問と道具の選択が見る
//   POST /abort {reason}   -> 実行中の行動を理由をつけて止める: { aborted, action? | why }。終わった行動には何もしない
//                             （技の実行中なら技ごと止める）
//   GET  /skills           -> 技の一覧（設計書 22）: [{ name, description, params, expects, version, verified, uses, successes, failures, last_failure }]
//   GET  /skills/<name>    -> 一番新しい版（コードを含む。直すときに読む）
//   PUT  /skills/<name> {description, params, expects, code} -> 確かめて新しい版として保存: { name, version }（だめなら 400 と理由）
//   POST /skills/<name>/run {args, version?} -> 技を 1 回実行する: { ok, ended, version, summary?, reason?, expects: {met, lines},
//                             calls, log, seconds, learned }（learned: この実行で初めて成功した）
//   GET  /danger           -> 反射が動いているとき、共通の状態に danger（id, trigger, target, options, rule, choice）、まわりの目印、
//                             家までの距離、最近のダメージを足したもの。動いていなければ null（設計書 28。見張りが 0.25 秒ごとに読む）
//   POST /reflex {id, choice, confidence} -> 反射を Jev の選んだもの（fight / flee / go_home / keep_distance / ignore）に切り替える
//   POST /judge {id, answer, confidence} -> 技の judge() の質問（/state の pending_judge）に答える（見張りのティック）
//
// 設計: docs/design/11_primitive_actions.md
// 環境変数: MC_HOST (localhost) MC_PORT (25565) BOT_NAME (AILoveShen) BRIDGE_PORT (3000) MIRROR_PORT (25578)
//   リポジトリ直下の .env にも書ける（env.mjs。シェルの環境変数が優先）

import './env.mjs' // 最初に: 他のモジュールが読み込み時に使う環境変数を .env から入れる
import { teeConsole } from './bridge-log.mjs'
import { plantingStatus, cropOf } from './farming.mjs'
import { aroundMe } from './around.mjs'
import { setLevelWanted } from './leveling.mjs'
import { quarrySpot, liveShafts } from './quarry.mjs'
import { listRefusals } from './refusals.mjs'
import { freeGuards } from './guards.mjs'
import { watchWear, recentBreaks, toolWear } from './wear.mjs'
import { guardDigLoops, installAutoJump } from './move.mjs'
import { installLedger } from './ledger.mjs'
import { installPlaceReport } from './place-report.mjs'
import { WorldMap, makeClassifier, createSampler, botReader, reflectLive, minuteOf } from './worldmap.mjs'
import { liquidReason } from './liquid.mjs'
import { memoryView, summaryKinds } from './worldmap-places.mjs'
import { gatherableLog, isNaturalLogName } from './trees.mjs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import mineflayer from 'mineflayer'
import minecraftData from 'minecraft-data'
import pathfinderPkg from 'mineflayer-pathfinder'
import { startMirror } from './mirror.mjs'
import { watchVehicle } from './boat.mjs'
import { registerBuild, buildsStatus, BuildError } from './builds.mjs'
import { titlesOf, setTitles } from './titles.mjs'
import { setRoads } from './roads.mjs'
import { adoptExisting, facilitiesStatus, registerFacility, updateStatus, FacilityError, cropFarms, farmSummary, mineSummaries, mineBodyReason } from './facilities.mjs'
import { loadNames, itemList, itemInfo } from './items.mjs'
import { zonesAround, TOWN_CELLS, ZONE_SIZE } from './zones.mjs'
import { registerCheck, checksList, CheckError } from './conditions.mjs'
import { mapAround } from './map.mjs'
import { summarize, bearing, round, inventoryCounts, isUnderground, blockInfo, pushHistory } from './observe.mjs'
import { foodCount, foodIn, foodPointsIn } from './cooking.mjs'
import { trackSurface } from './surface.mjs'
import { installDamageLog } from './damage-log.mjs'
import { noteDamage, deathReport } from './death-report.mjs'
import { configureMovements, DAMAGE_TOLERANT, knownFurnace, nearbyDrops } from './primitives.mjs'
import { createRunner, abortCurrent } from './runner.mjs'
import { createTools, DROP_RADIUS as PICKUP_RADIUS } from './tools.mjs'
import { sharedState } from './state.mjs'
import { BuildPlan } from './build.mjs'
import { RecipeBook } from './craft.mjs'
import { Knowledge, useRecipes } from './knowledge.mjs'
import { RecipeIndex } from './recipes.mjs'
import { makeGoal, evaluate, needs, checkConditions } from './goals.mjs'
import { ground, describe } from './candidates.mjs'
import { snapshot, sightings } from './world.mjs'
import { loadState, saveState, settleHome, isInside, isDoorOpen, hasBed, protectedReason } from './home.mjs'
import { assessRisk } from './risk.mjs'
import { startReflex, steerReflex, releaseReflex, dangerView } from './reflex.mjs'
import { storageUsage, chestSpotFact, syncHomeChests } from './storage.mjs'
import { countPlaced } from './placing.mjs'
import { newMemory, remember, rememberDeath, summarizeMemory, memoryList, homeChests, storedCounts } from './memory.mjs'
import { surveyedSites } from './survey.mjs'
import { landmarks, adoptFoundBase } from './landmarks.mjs'
import { SkillStore, SkillError, runSkill, expectsBaseline, judgeExpects, blockedReason, LIMITS, DEFAULT_DIR } from './skills.mjs'

// 家のチェストの数と空き（観測の home に付ける）: chest_blocks = 家にあるチェストのブロック数（ラージは 2）、
// chest_slots / chest_free = 記録からのスロット数と空き、chest_saving = まとめると空くスロット、chest_spot = もう 1 つ置ける場所があるか（storage.mjs）
function chestFacts (bot, state) {
  if (!state.memory) return {}
  syncHomeChests(bot, state)
  const u = storageUsage(bot, state)
  return { chest_blocks: countPlaced(bot, state, 'home', 'chest'), chest_slots: u.size, chest_free: u.free, chest_saving: u.saving, chest_spot: chestSpotFact(bot, state.home) }
}


teeConsole() // 標準出力を logs/bridge.log にも残す（端末を閉じても、あとで読める）

const BRIDGE_PORT = Number(process.env.BRIDGE_PORT ?? 3000)
const VERSION = '1.21.4'

// 視界で作る世界の地図（docs/design/59_world_map.md）: 見えたセルを座標つきで覚える。state.json とは別に保存する。
// 読み込みが遅れて spawn の待ち受けに間に合わないことがないよう、ボットを作る前に読む
const WORLDMAP_DIR = process.env.WORLDMAP_DIR ?? path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data', 'worldmap')
const WORLDMAP_SAVE_MS = 30 * 1000
const worldMap = await WorldMap.load(WORLDMAP_DIR, VERSION)
const mapCells = makeClassifier(minecraftData(VERSION))
console.log(`[worldmap] ${WORLDMAP_DIR} から断面 ${worldMap.sectionCount} を読んだ`)

const bot = mineflayer.createBot({
  host: process.env.MC_HOST ?? 'localhost',
  port: Number(process.env.MC_PORT ?? 25565),
  username: process.env.BOT_NAME ?? 'AILoveShen',
  version: VERSION,
  auth: 'offline'
})
bot.loadPlugin(pathfinderPkg.pathfinder)
const sampler = createSampler(bot, worldMap, mapCells)
startMirror(bot)
watchVehicle(bot) // 降りたのに bot.vehicle が残る Mineflayer の不具合を直す（設計書 37）

const state = {
  plan: null,
  home: null,
  goal: null, // { spec, ...述語が保持するもの（開始位置、夜を見たか） }
  history: [],
  busy: false, // 行動の実行中
  reflex: false, // 反射が近くの脅威に対処中
  current: null, // 実行中の行動の { id, verb, abort(reason), done }
  recipeBook: new RecipeBook(bot),
  unreachableDrops: new Set(),
  unreachableBlocks: new Set(),
  formerHomes: [], // 引っ越す前の家（壊さない）
  roads: null, // 敷く道の細い列（roads.mjs。設計書 167）
  builds: {}, // 名前付きの建物（builds.mjs）
  conditions: {}, // Gemini が書いた判定 custom(name)（conditions.mjs）
  survey: null, // 街の候補地の調査の計画（survey.mjs）
  memory: newMemory(), // 前に見た場所（memory.mjs）。state.json に保存する
  worldMap, // 視界の地図（worldmap.mjs）。別のファイルに保存する
  mapCells: mapCells, // ブロックの種類とセルの値の表
  skill: null, // 実行中の技 { name, version, control }（skills.mjs）
  pendingJudge: null, // 技の judge() が Jev の答えを待っている質問 { id, question, kind, options, resolve }
  deaths: 0 // このブリッジが動いてから死んだ回数（増えたら、Python は小目標を選び直す）
}
loadState(state)
// バニラのレシピ全部（data-static、設計書 29）: 計画と調べものが使う。クラフトはサーバーのレシピ本
const recipeIndex = RecipeIndex.load(VERSION)
useRecipes(recipeIndex)
const knowledge = new Knowledge(minecraftData(VERSION), recipeIndex)
const names = loadNames(VERSION)
const jaOf = (name) => names.items[name] ?? ''

const worldAge = () => Number(bot.time.age)

// 完成した計画は家になる（別の場所なら引っ越し）。計画、家、前の家、調査、目標は再起動しても残る
function persist () {
  const former = state.home
  if (settleHome(state, bot)) console.log(`[bridge] ${former ? '引っ越した' : '家を設定'}: ドア ${state.home.door}`)
  adoptBase()
  saveState(state)
}

// 家がないとき、近くのドアつきの閉じた部屋を家にする（landmarks.mjs）
function adoptBase () {
  if (!adoptFoundBase(bot, state)) return false
  console.log(`[bridge] 近くの拠点を家にした: ドア ${state.home.door}、部屋 ${state.home.cells.length} マス`)
  return true
}

function observation () {
  if (adoptBase()) saveState(state)
  const extra = {}
  if (state.plan) extra.build = state.plan.status(bot)
  if (Object.keys(state.builds ?? {}).length) extra.builds = buildsStatus(bot, state)
  const titles = titlesOf(state)
  if (Object.keys(titles).length) extra.titles = titles
  extra.memory = summarizeMemory(memoryView(state, bot.entity.position, worldAge(), summaryKinds(mapCells)), bot.entity.position, worldAge(), bearing)
  // 覚えている場所のブロックから取れるアイテム（作物の wheat → wheat と wheat_seeds）
  for (const p of extra.memory.places) {
    const gives = knowledge.dropsOf(p.kind)
    if (gives.length) p.gives = gives
  }
  // 畑（設計した畑と家の畑。作物ごとの植わっている数、熟した数、空いたマス。設計書 47 §14）
  if (state.home) {
    const farm = farmSummary(bot, state)
    if (Object.keys(farm).length) extra.farm = farm
  }
  // 調べた候補地の数字（ブリッジが測ったものだけ。まだ調べていない候補地は入らない）
  if (state.survey) extra.survey = { planned: state.survey.sites.length, sites: surveyedSites(state).map((s) => state.memory.sites[s.id]) }
  extra.deaths = state.deaths
  extra.last_death = state.lastDeath ?? null // 死んだ瞬間の様子（死んだあとの目標の決定が教訓にする。設計書 105）
  // まわりの目印（ベッド、ドア、作業台、かまど、チェスト、松明）と、それが家のものか
  extra.nearby = landmarks(bot, state)
  // 目の前にあるもの（半径 8、足元の高さ ±2 の、見えているブロックと落ちているアイテム）
  // 水をせき止めているブロックも出さない。木でない原木（村の畑の縁など）は、数だけ（not_trees）で座標は出さない
  extra.around = aroundMe(
    bot,
    (name) => knowledge.dropsOf(name),
    (p, b) => protectedReason(state, p, 0, b) !== null || mineBodyReason(state, p) !== null || !!liquidReason(bot, p, b),
    (p, b) => isNaturalLogName(b.name) && !gatherableLog(bot, state, p),
    state
  )
  // 石・土を掘る採掘の場所と、続きから下りられる階段（設計書 64）
  const spot = quarrySpot(state, bot.entity.position)
  if (spot) extra.quarry = { x: spot.x, y: spot.y, z: spot.z, distance_m: round(spot.distanceTo(bot.entity.position)), direction: bearing(bot.entity.position, spot), shafts: liveShafts(state).map((s) => ({ top: s.top, bottom: s.bottom })) }
  extra.underground = isUnderground(bot)
  // 外に出る・戦うときの危なさ（risk.mjs。家の中でも、外に出たときの）
  extra.risk = assessRisk(bot, state)
  trackSurface(bot, state, extra.underground)
  // 手持ちのストック（食べ物の個数、松明の本数。Python が、切らす前に集める小目標を出す）
  // 家のチェストにある数は最後に開けたときの中身（備蓄。手持ちと合わせて数える）。mines は設計した鉱山の数
  const homeStored = state.home ? storedCounts(state.memory ?? {}, state.home) : {}
  const furnaceKnown = !!knownFurnace(bot, state)
  extra.supplies = {
    food: foodCount(bot, knowledge),
    torch: inventoryCounts(bot).torch ?? 0,
    food_stored: foodIn(homeStored, knowledge),
    food_points: foodPointsIn(inventoryCounts(bot), knowledge, furnaceKnown),
    food_points_stored: foodPointsIn(homeStored, knowledge, furnaceKnown),
    torch_stored: homeStored.torch ?? 0,
    mines: mineSummaries(state).length
  }
  // 最近壊れた道具（壊れた直後から持ち物の見方を直す。replaced: 同じ種類をもう持っている）
  const broken = recentBreaks(bot, state)
  if (broken.length) extra.broken_tools = broken
  const wear = toolWear(bot)
  if (wear.length) extra.tool_wear = wear
  const xyz = (v) => v && { x: v.x, y: v.y, z: v.z }
  extra.home = state.home ? { name: state.home.name, design: state.home.design, door: xyz(state.home.door), min: xyz(state.home.min), max: xyz(state.home.max), inside: isInside(bot, state.home), door_open: isDoorOpen(bot, state.home), bed: hasBed(bot, state.home), chest: homeChests(state.memory ?? {}, state.home).length > 0, ...chestFacts(bot, state), found: !!state.home.found, sleeping: bot.isSleeping } : null
  return summarize(bot, state.history, extra)
}

// 目標の状態と候補。ワールドの1つのスナップショットから作る
// strict: 評価で落ちるなら投げる（PUT /goal が目標を受けるかの検査）。そうでなければ、評価できない目標は捨てる
// （保存された目標が再起動のあとも /observe を 500 にし続けないように。2026-10-04 `have #oak_log`）
function decisionView (strict = false) {
  syncHomeChests(bot, state)
  const world = snapshot(bot, state)
  let status = null
  if (state.goal) {
    try {
      status = evaluate(bot, state, knowledge, world)
    } catch (e) {
      if (strict) throw e
      console.log(`[bridge] 目標を評価できないので捨てた: ${JSON.stringify(state.goal.spec)}: ${e.message}`)
      state.goal = null
      persist()
    }
  }
  const { candidates, withheld } = ground(bot, state, knowledge, world, status)
  // 避難の規則で外したものは、目標が進まない理由の一部
  if (status && withheld) status.blocked.push(withheld)
  return { status, candidates }
}

// dead_end: 進める手が 1 つもなく、今は手に入らない部分がある（その理由）。Python は決定を差し戻す
const deadEnd = (s) => !s.met && !s.leaves?.length && s.impossible?.length ? [...new Set(s.impossible)] : []
const publicStatus = (s) => s && { spec: s.spec, met: s.met, remaining: s.remaining, lines: s.lines, blocked: [...new Set(s.blocked)], dead_end: deadEnd(s), ...(s.members ? { members: s.members.map(({ spec, met, remaining }) => ({ spec, met, remaining })) } : {}) }

// 候補を1つ実行する（実行の経路は runner.mjs。道具と同じ）
const run = createRunner(bot, state, {
  afterRun () {
    // 途中で見えた動物（ブロックは視界の地図が、歩きながら覚える）
    remember(state.memory, sightings(bot), bot.entity.position, worldAge())
    persist()
  }
})

const callTool = createTools({
  bot,
  state,
  knowledge,
  run,
  candidates: () => decisionView().candidates,
  check: (specs) => checkConditions(specs, bot, state, knowledge, snapshot(bot, state))
})

// 技（設計書 22）: 保存はワールドのリセットの後も残す
const skills = new SkillStore(process.env.SKILLS_DIR ?? DEFAULT_DIR)
let judgeIds = 0
const skillDeps = {
  callTool,
  state: () => sharedState(bot, state, needs),
  blockAt: (args) => blockInfo(bot, args),
  // 質問を /state に出し、見張りのティックで Jev が答えるのを待つ（来なければ answer: null）
  judge (q, signal, waitMs) {
    return new Promise((resolve) => {
      const id = ++judgeIds
      const done = (a) => {
        if (state.pendingJudge?.id === id) state.pendingJudge = null
        clearTimeout(timer)
        resolve({ question: q.question, answer: null, confidence: 0, ...a })
      }
      const timer = setTimeout(() => done({ why: 'no answer in time' }), waitMs)
      signal.addEventListener('abort', () => done({ why: 'stopped' }), { once: true })
      state.pendingJudge = { id, question: q.question, kind: q.kind ?? 'yes_no', options: q.options ?? null, resolve: done }
    })
  },
  abortTool: (reason) => abortCurrent(state, reason),
  count (predicate, item) {
    // 植林と畑の「+N」の起点（設計書 33）
    if (predicate === 'planted') { const t = plantingStatus(bot, state, item); return t.saplings + t.trees + t.unseen }
    if (predicate === 'farmed') return cropFarms(bot, state, cropOf(item)).planted
    const { members } = knowledge.resolve(item)
    if (predicate === 'stored') {
      return homeChests(state.memory ?? {}, state.home).reduce((n, c) => n + members.reduce((m, x) => m + (c.contents?.[x] ?? 0), 0), 0)
    }
    const inv = inventoryCounts(bot)
    return members.reduce((n, x) => n + (inv[x] ?? 0), 0)
  },
  check: (specs) => checkConditions(specs, bot, state, knowledge, snapshot(bot, state)),
  remaining: () => state.goal ? evaluate(bot, state, knowledge, snapshot(bot, state)).remaining : null
}

// 反射の判断に渡す状態（設計書 28 §3）: 共通の状態に、危険、まわりの目印、家までの距離、最近のダメージ
function reflexState () {
  const home = state.home
  return {
    ...sharedState(bot, state, needs),
    danger: dangerView(state),
    nearby: landmarks(bot, state),
    home: home ? { distance_m: Math.round(bot.entity.position.distanceTo(home.door)), inside: isInside(bot, home) } : null,
    last_hurt: state.lastHurt ? { seconds_ago: Math.round((Date.now() - state.lastHurt.at) / 100) / 10, damage: state.lastHurt.damage } : null
  }
}

async function runSkillOnce (name, args, version) {
  const skill = skills.get(name, version)
  if (!skill) return null
  // 同じ理由の失敗が続いている版は動かさない（前の失敗から SKILL_PROBE_MS たてば、1 回試せる）
  const blocked = blockedReason(skill, skills.list().find((s) => s.name === name)?.last_good_version)
  if (blocked) return { ok: false, ended: 'refused', version: skill.version, reason: blocked, calls: [], log: [], seconds: 0, expects: { met: false, lines: [] } }
  let before
  try {
    before = expectsBaseline(skill.expects, args, skillDeps)
  } catch (e) {
    return { ok: false, ended: 'error', version: skill.version, reason: e.message, calls: [], log: [], seconds: 0, expects: { met: false, lines: [] } }
  }
  const control = {}
  state.skill = { name, version: skill.version, control }
  let r
  try {
    r = await runSkill(skill, args, skillDeps, LIMITS, control)
  } finally {
    state.skill = null
    state.pendingJudge = null
  }
  let expects = { met: false, lines: [] }
  try {
    expects = judgeExpects(skill.expects, args, before, skillDeps)
  } catch (e) {
    expects = { met: false, lines: [e.message] }
  }
  const ok = r.ended === 'done' && expects.met
  const reason = ok ? null : r.reason ?? `expects not met: ${expects.lines.join('; ')}`
  const learned = skills.record(name, skill.version, ok, reason)
  persist()
  return { ok, version: skill.version, ...r, reason, expects, learned }
}

async function act (id) {
  if (state.reflex) return { ok: false, result: 'not started: the reflex is handling a nearby threat', seconds: 0 }
  const c = decisionView().candidates.find((x) => x.id === id)
  if (!c) return { ok: false, result: `${id} is not available now`, seconds: 0 }
  return run(c, id)
}

const send = (res, code, body) => {
  res.writeHead(code, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

async function readJson (req) {
  let body = ''
  for await (const chunk of req) body += chunk
  return JSON.parse(body || '{}')
}

async function handle (req, res) {
  if (!bot.entity) return send(res, 503, { error: 'bot not spawned' })
  if (req.method === 'GET' && req.url === '/observe') {
    const { status, candidates } = decisionView()
    return send(res, 200, {
      busy: state.busy || state.reflex || !!state.skill,
      observation: observation(),
      needs: needs(bot, state),
      goal: publicStatus(status),
      candidates: candidates.map((c) => ({ id: c.id, ...describe(c) }))
    })
  }
  if (req.method === 'PUT' && req.url === '/goal') {
    // 決める前に評価する: 評価で落ちる目標を残すと、/observe が毎回 500 になり続ける
    // （2026-10-04 `have #oak_log` で 109 分止まった）。落ちたら元の目標に戻して 400 で返す
    const previous = state.goal
    let status
    try {
      const body = await readJson(req)
      state.goal = makeGoal(body, bot, state, knowledge)
      setLevelWanted(state, body.level_areas)
      status = decisionView(true).status
    } catch (e) {
      state.goal = previous
      return send(res, 400, { error: e.message })
    }
    persist()
    console.log(`[bridge] 目標: ${JSON.stringify(state.goal.spec)}`)
    return send(res, 200, publicStatus(status))
  }
  if (req.method === 'POST' && req.url === '/check') {
    const { specs, materials } = await readJson(req)
    persist() // 建ち終わったばかりの家を、判定の前に家にする
    try {
      return send(res, 200, checkConditions(specs, bot, state, knowledge, snapshot(bot, state), { materials: !!materials }))
    } catch (e) {
      return send(res, 400, { error: e.message })
    }
  }
  if (req.method === 'POST' && req.url === '/act') {
    const { id } = await readJson(req)
    if (state.busy || state.skill) return send(res, 409, { error: 'busy' })
    return send(res, 200, await act(id))
  }
  if (req.method === 'POST' && req.url === '/tool') {
    const { name, args } = await readJson(req)
    if (state.skill) return send(res, 409, { error: `the skill ${state.skill.name} is running` })
    const r = await callTool(name, args ?? {})
    console.log(`[tool] ${name} ${JSON.stringify(args ?? {})}: ${r.refused ? '断った' : r.ok ? '成功' : '失敗'} ${typeof r.result === 'string' ? r.result : '(調べた)'}`)
    return send(res, 200, r)
  }
  if (req.method === 'GET' && req.url === '/state') {
    const skill = state.skill && { name: state.skill.name, version: state.skill.version, ...state.skill.control.view?.() }
    const judge = state.pendingJudge && { id: state.pendingJudge.id, question: state.pendingJudge.question, kind: state.pendingJudge.kind, options: state.pendingJudge.options }
    // reflex: 反射が動いていて道具は断られる。pickup_ready: pickup が拾える落とし物があるか（tools.mjs と同じ範囲）
    return send(res, 200, {
      ...sharedState(bot, state, needs),
      reflex: Boolean(state.reflex),
      pickup_ready: nearbyDrops(bot, state, PICKUP_RADIUS).length > 0,
      refusals: listRefusals(state),
      skill: skill || null,
      pending_judge: judge || null
    })
  }
  if (req.method === 'POST' && req.url === '/abort') {
    const { reason } = await readJson(req)
    if (state.skill) {
      const name = state.skill.name
      state.skill.control.stop?.(String(reason ?? 'aborted'))
      return send(res, 200, { aborted: true, action: `skill ${name}` })
    }
    return send(res, 200, await abortCurrent(state, String(reason ?? 'aborted')))
  }
  if (req.method === 'PUT' && req.url === '/build-plan') {
    state.plan = new BuildPlan(await readJson(req))
    persist()
    return send(res, 200, state.plan.status(bot))
  }
  const buildPath = req.url.match(/^\/builds\/([a-z0-9_]{1,32})$/)
  if (req.method === 'PUT' && buildPath) {
    try {
      const plan = registerBuild(bot, state, { ...(await readJson(req)), name: buildPath[1] })
      persist()
      return send(res, 200, { name: buildPath[1], ...plan.status(bot) })
    } catch (e) {
      if (e instanceof BuildError) return send(res, 400, { error: e.message })
      throw e
    }
  }
  const conditionPath = req.url.match(/^\/conditions\/([a-z][a-z0-9_]{1,39})$/)
  if (req.method === 'PUT' && conditionPath) {
    try {
      const r = registerCheck(bot, state, conditionPath[1], await readJson(req))
      persist()
      console.log(`[check] ${conditionPath[1]} を登録した: 残り ${r.remaining}`)
      return send(res, 200, { name: conditionPath[1], ...r })
    } catch (e) {
      if (e instanceof CheckError) return send(res, 400, { error: e.message })
      throw e
    }
  }
  if (req.method === 'GET' && req.url === '/conditions') {
    return send(res, 200, checksList(state))
  }
  if (req.method === 'GET' && req.url === '/skills') {
    return send(res, 200, skills.list())
  }
  const skillPath = req.url.match(/^\/skills\/([a-z][a-z0-9_]{1,39})(\/run)?$/)
  if (req.method === 'GET' && skillPath && !skillPath[2]) {
    const skill = skills.get(skillPath[1])
    return send(res, skill ? 200 : 404, skill ?? { error: `there is no skill named ${skillPath[1]}` })
  }
  if (req.method === 'PUT' && skillPath && !skillPath[2]) {
    try {
      const version = skills.put(skillPath[1], await readJson(req))
      console.log(`[skill] ${skillPath[1]} v${version} を保存した`)
      return send(res, 200, { name: skillPath[1], version })
    } catch (e) {
      if (e instanceof SkillError) return send(res, 400, { error: e.message })
      throw e
    }
  }
  if (req.method === 'POST' && skillPath?.[2]) {
    const { args, version } = await readJson(req)
    if (state.busy || state.reflex || state.skill) return send(res, 409, { error: 'busy' })
    const r = await runSkillOnce(skillPath[1], args ?? {}, version)
    if (!r) return send(res, 404, { error: `there is no skill named ${skillPath[1]}${version != null ? ` v${version}` : ''}` })
    console.log(`[skill] ${skillPath[1]} v${r.version}: ${r.ok ? '成功' : '失敗'} ${r.ok ? r.summary : r.reason}（${r.seconds}秒、道具 ${r.calls.length}）`)
    return send(res, 200, r)
  }
  if (req.method === 'GET' && req.url === '/danger') {
    // 危険がなければ null（見張りは 0.25 秒ごとに読むので軽くする）。あれば Jev に渡す状態つき
    return send(res, 200, state.danger ? reflexState() : null)
  }
  // 目標外で集めるブロック（鉱石のほかに）: Python の設定の一覧（windfall.mjs）
  if (req.method === 'PUT' && req.url === '/collect') {
    const { blocks } = await readJson(req)
    const known = (Array.isArray(blocks) ? blocks : []).filter((n) => typeof n === 'string' && bot.registry.blocksByName[n])
    state.collect = { extra: known }
    return send(res, 200, { extra: known })
  }
  if (req.method === 'POST' && req.url === '/reflex/release') {
    return send(res, 200, releaseReflex(state, (await readJson(req)).seconds))
  }
  if (req.method === 'POST' && req.url === '/reflex') {
    const r = steerReflex(state, await readJson(req))
    return send(res, 200, r)
  }
  if (req.method === 'POST' && req.url === '/judge') {
    const { id, answer, confidence } = await readJson(req)
    const pending = state.pendingJudge
    if (!pending || pending.id !== Number(id)) return send(res, 200, { accepted: false })
    pending.resolve({ answer: answer ?? null, confidence: Number(confidence ?? 0) })
    return send(res, 200, { accepted: true })
  }
  if (req.method === 'GET' && req.url === '/map') {
    return send(res, 200, mapAround(bot, state))
  }
  if (req.method === 'GET' && req.url === '/builds') {
    return send(res, 200, buildsStatus(bot, state))
  }
  if (req.method === 'PUT' && req.url === '/roads') {
    // 敷く道の細い列（設計書 167）。前のものは置き換える
    try {
      const n = setRoads(state, (await readJson(req)).strips)
      persist()
      return send(res, 200, { strips: n })
    } catch (e) {
      return send(res, 400, { error: e.message })
    }
  }
  if (req.method === 'PUT' && req.url === '/titles') {
    const result = setTitles(state, (await readJson(req)).titles)
    if (result.set.length) persist()
    return send(res, 200, result)
  }
  const facility = req.url.match(/^\/facilities\/([a-z0-9_]{1,40})$/)
  if (req.method === 'PUT' && facility) {
    // 施設の設計を登録する（設計書 47 §7）: 区画の中から実際のマスを選ぶ。直せる理由は 400、
    // 世界の状態（読み込まれていない）は 409
    try {
      adoptExisting(bot, state)
      const fac = registerFacility(bot, state, facility[1], await readJson(req))
      saveState(state)
      return send(res, 200, facilitiesStatus(bot, state, jaOf).find((f) => f.name === fac.name))
    } catch (e) {
      if (e instanceof FacilityError) return send(res, e.world ? 409 : 400, { error: e.message })
      throw e
    }
  }
  if (req.method === 'GET' && req.url === '/facilities') {
    let changed = adoptExisting(bot, state)
    for (const f of Object.values(state.facilities ?? {})) changed = updateStatus(bot, f) || changed
    if (changed) saveState(state)
    return send(res, 200, facilitiesStatus(bot, state, jaOf))
  }
  if (req.method === 'GET' && req.url === '/worldmap') return send(res, 200, worldMapStatus())
  if (req.method === 'GET' && req.url === '/memory') {
    return send(res, 200, memoryList(memoryView(state, bot.entity.position, worldAge()), bot.entity.position, worldAge(), bearing, (k) => knowledge.dropsOf(k), jaOf))
  }
  if (req.method === 'GET' && req.url.startsWith('/zones?')) {
    const q = new URL(req.url, 'http://bridge').searchParams
    const center = { x: Number(q.get('x')), z: Number(q.get('z')) }
    if (!Number.isFinite(center.x) || !Number.isFinite(center.z)) return send(res, 400, { error: 'x and z are required' })
    if (adoptExisting(bot, state)) saveState(state)
    const cells = Math.max(2, Math.min(16, Number(q.get('cells') ?? TOWN_CELLS)))
    const size = Math.max(4, Math.min(16, Number(q.get('size') ?? ZONE_SIZE)))
    return send(res, 200, zonesAround(bot, state, center, cells, size))
  }
  if (req.method === 'GET' && req.url === '/items') {
    return send(res, 200, itemList(knowledge.md, names))
  }
  if (req.method === 'GET' && req.url.startsWith('/items/')) {
    const info = itemInfo(decodeURIComponent(req.url.slice('/items/'.length)), { md: knowledge.md, knowledge, index: recipeIndex, names })
    return send(res, info ? 200 : 404, info ?? { error: 'unknown item' })
  }
  if (req.method === 'GET' && req.url === '/build-plan') {
    return send(res, state.plan ? 200 : 404, state.plan ? state.plan.status(bot) : { error: 'no plan' })
  }
  send(res, 404, { error: 'not found' })
}

http.createServer((req, res) => handle(req, res).catch((e) => send(res, 500, { error: e.message })))
  .listen(BRIDGE_PORT, '127.0.0.1', () => console.log(`[bridge] 待ち受け中 http://127.0.0.1:${BRIDGE_PORT}${freeGuards() ? '' : '（BRIDGE_GUARDS=standard: 作業知識の断りも断る）'}`))

startReflex(bot, state, {
  async preempt (reason, force = false) {
    const current = state.current
    if (!current) return true
    if (!force && DAMAGE_TOLERANT.has(current.verb)) return false // すでに脅威に対処している（溺れそうなときは止める）
    current.abort(reason)
    await current.done
    return true
  },
  record (entry) { pushHistory(state, entry) }
})

installDamageLog(bot, () => state.current?.id ?? (state.reflex ? 'the reflex' : ''), (d) => noteDamage(state, d))
bot.on('death', () => {
  state.deaths += 1
  state.lastDeath = deathReport(bot, state)
  rememberDeath(state.memory, bot.entity.position, worldAge())
  persist()
  console.log(`[bot] ${bot.entity.position} で死んだ`)
})

bot.once('spawn', () => {
  configureMovements(bot, state)
  watchWear(bot, state)
  guardDigLoops(bot, state)
  installAutoJump(bot)
  installPlaceReport(bot)
  installLedger(bot, state, (pos) => reflectLive(worldMap, botReader(bot), mapCells, pos, minuteOf(bot.time.age)))
  sampler.start()
  console.log(`[bot] ${bot.entity.position} にスポーンした`)
})
// 地図の保存と、様子の記録（サンプリングの時間、断面の数）。ブリッジは 1 スレッドなので、重くなっていないか見える
function worldMapStatus () {
  const st = sampler.stats
  return {
    sections: worldMap.sectionCount,
    cells_added: worldMap.total.added,
    cells_changed: worldMap.total.changed,
    samples: st.samples,
    skipped: st.skipped,
    avg_ms: st.samples ? Math.round((st.ms / st.samples) * 100) / 100 : 0,
    max_ms: Math.round(st.maxMs * 100) / 100
  }
}
setInterval(() => {
  worldMap.save(WORLDMAP_DIR, VERSION).catch((e) => console.log('[worldmap] 保存できなかった', e.message))
}, WORLDMAP_SAVE_MS).unref()
setInterval(() => console.log(`[worldmap] ${JSON.stringify(worldMapStatus())}`), 5 * 60 * 1000).unref()
// 採掘の開始をすべて記録する。pathfinder が道を開けるために掘るものも含む
let lastDig = null
bot.on('physicsTick', () => {
  const t = bot.targetDigBlock
  if (t && t !== lastDig) console.log(`[bot] ${t.position} の ${t.name} を掘る`)
  lastDig = t
})
bot.on('death', () => { console.log('[bot] 死んだ'); pushHistory(state, { action: 'event', ok: false, result: 'died', seconds: 0 }) })
bot.on('kicked', (r) => console.log('[bot] キックされた', JSON.stringify(r)))
bot.on('error', (e) => console.log('[bot] エラー', e.message))

// 止めるとき（Ctrl-C、npm run dev のファイル変更での再起動）: 実行中の行動を止め、状態を保存し、ボットを
// サーバーから抜けさせる（抜けないと、次に入ったボットと同じ名前で重なる）
let stopping = false
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    if (stopping) process.exit(1)
    stopping = true
    console.log(`[bridge] 止める（${signal}）`)
    try {
      state.current?.abort?.('the bridge is restarting')
      if (bot.entity) saveState(state)
    } catch (e) {
      console.log('[bridge] 状態を保存できなかった', e.message)
    }
    try { bot.quit('bridge restarting') } catch {}
    sampler.stop()
    const saved = worldMap.save(WORLDMAP_DIR, VERSION, { final: true }).catch((e) => console.log('[worldmap] 保存できなかった', e.message))
    setTimeout(() => process.exit(0), 3000).unref()
    saved.then(() => setTimeout(() => process.exit(0), 500))
  })
}
