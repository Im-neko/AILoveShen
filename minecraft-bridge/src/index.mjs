// Minecraft ブリッジ: Mineflayer のボット + 視点のミラー + Python 側のための HTTP API。
//
//   PUT  /goal {predicate, item?, count?, where?, distance?, keep?: [{item, count}], also?: [{item, count}]}
//                          -> 目標を設定する（不正なら理由を添えて 400）。目標の状態を返す。
//                             keep: 中目標のためにチェストに取っておくもの（この目標のために取り出さない）
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
//   GET  /builds           -> [{ name, purpose, anchor, placed, total, complete, origin }]
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
import { plantingStatus, cropStatus, cropOf } from './farming.mjs'
import { watchWear, recentBreaks } from './wear.mjs'
import { guardDigLoops } from './move.mjs'
import http from 'node:http'
import mineflayer from 'mineflayer'
import minecraftData from 'minecraft-data'
import pathfinderPkg from 'mineflayer-pathfinder'
import { startMirror } from './mirror.mjs'
import { registerBuild, buildsStatus, BuildError } from './builds.mjs'
import { mapAround } from './map.mjs'
import { summarize, bearing, inventoryCounts, isUnderground } from './observe.mjs'
import { configureMovements, DAMAGE_TOLERANT } from './primitives.mjs'
import { createRunner, abortCurrent } from './runner.mjs'
import { createTools } from './tools.mjs'
import { sharedState } from './state.mjs'
import { BuildPlan } from './build.mjs'
import { RecipeBook } from './craft.mjs'
import { Knowledge, useRecipes } from './knowledge.mjs'
import { RecipeIndex } from './recipes.mjs'
import { makeGoal, evaluate, needs, checkConditions } from './goals.mjs'
import { ground, describe } from './candidates.mjs'
import { snapshot, sightings } from './world.mjs'
import { loadState, saveState, settleHome, isInside, isDoorOpen, hasBed } from './home.mjs'
import { startReflex, steerReflex, dangerView } from './reflex.mjs'
import { newMemory, remember, rememberDeath, summarizeMemory, homeChests } from './memory.mjs'
import { surveyedSites } from './survey.mjs'
import { landmarks, adoptFoundBase } from './landmarks.mjs'
import { SkillStore, SkillError, runSkill, expectsBaseline, judgeExpects, LIMITS, DEFAULT_DIR } from './skills.mjs'
import vec3Pkg from 'vec3'

const BRIDGE_PORT = Number(process.env.BRIDGE_PORT ?? 3000)
const VERSION = '1.21.4'

const bot = mineflayer.createBot({
  host: process.env.MC_HOST ?? 'localhost',
  port: Number(process.env.MC_PORT ?? 25565),
  username: process.env.BOT_NAME ?? 'AILoveShen',
  version: VERSION,
  auth: 'offline'
})
bot.loadPlugin(pathfinderPkg.pathfinder)
startMirror(bot)

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
  builds: {}, // 名前付きの建物（builds.mjs）
  survey: null, // 街の候補地の調査の計画（survey.mjs）
  memory: newMemory(), // 前に見た場所（memory.mjs）。state.json に保存する
  skill: null, // 実行中の技 { name, version, control }（skills.mjs）
  pendingJudge: null, // 技の judge() が Jev の答えを待っている質問 { id, question, kind, options, resolve }
  deaths: 0 // このブリッジが動いてから死んだ回数（増えたら、Python は小目標を選び直す）
}
loadState(state)
// バニラのレシピ全部（data-static、設計書 29）: 計画と調べものが使う。クラフトはサーバーのレシピ本
const recipeIndex = RecipeIndex.load(VERSION)
useRecipes(recipeIndex)
const knowledge = new Knowledge(minecraftData(VERSION), recipeIndex)

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
  extra.memory = summarizeMemory(state.memory, bot.entity.position, worldAge(), bearing)
  // 調べた候補地の数字（ブリッジが測ったものだけ。まだ調べていない候補地は入らない）
  if (state.survey) extra.survey = { planned: state.survey.sites.length, sites: surveyedSites(state).map((s) => state.memory.sites[s.id]) }
  extra.deaths = state.deaths
  // まわりの目印（ベッド、ドア、作業台、かまど、チェスト、松明）と、それが家のものか
  extra.nearby = landmarks(bot, state)
  extra.underground = isUnderground(bot)
  // 最近壊れた道具（壊れた直後から持ち物の見方を直す。replaced: 同じ種類をもう持っている）
  const broken = recentBreaks(bot, state)
  if (broken.length) extra.broken_tools = broken
  extra.home = state.home ? { name: state.home.name, design: state.home.design, inside: isInside(bot, state.home), door_open: isDoorOpen(bot, state.home), bed: hasBed(bot, state.home), sleeping: bot.isSleeping } : null
  return summarize(bot, state.history, extra)
}

// 目標の状態と候補。ワールドの1つのスナップショットから作る
function decisionView () {
  const world = snapshot(bot, state)
  const status = state.goal ? evaluate(bot, state, knowledge, world) : null
  const { candidates, withheld } = ground(bot, state, knowledge, world, status)
  // 避難の規則で外したものは、目標が進まない理由の一部
  if (status && withheld) status.blocked.push(withheld)
  return { status, candidates }
}

const publicStatus = (s) => s && { spec: s.spec, met: s.met, remaining: s.remaining, lines: s.lines, blocked: [...new Set(s.blocked)] }

// 候補を1つ実行する（実行の経路は runner.mjs。道具と同じ）
const run = createRunner(bot, state, {
  afterRun () {
    // 途中で見えたもの（変わるのは移動したあと）
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
  blockAt ({ x, y, z }) {
    const p = new vec3Pkg.Vec3(Math.floor(Number(x)), Math.floor(Number(y)), Math.floor(Number(z)))
    const b = bot.blockAt(p)
    return b ? { name: b.name, x: p.x, y: p.y, z: p.z } : { name: null, error: 'not loaded' }
  },
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
    if (predicate === 'farmed') return cropStatus(bot, state, cropOf(item)).planted
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
    try {
      state.goal = makeGoal(await readJson(req), bot, state, knowledge)
    } catch (e) {
      return send(res, 400, { error: e.message })
    }
    persist()
    console.log(`[bridge] 目標: ${JSON.stringify(state.goal.spec)}`)
    return send(res, 200, publicStatus(decisionView().status))
  }
  if (req.method === 'POST' && req.url === '/check') {
    const { specs } = await readJson(req)
    persist() // 建ち終わったばかりの家を、判定の前に家にする
    try {
      return send(res, 200, checkConditions(specs, bot, state, knowledge, snapshot(bot, state)))
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
    return send(res, 200, { ...sharedState(bot, state, needs), skill: skill || null, pending_judge: judge || null })
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
  if (req.method === 'GET' && req.url === '/build-plan') {
    return send(res, state.plan ? 200 : 404, state.plan ? state.plan.status(bot) : { error: 'no plan' })
  }
  send(res, 404, { error: 'not found' })
}

http.createServer((req, res) => handle(req, res).catch((e) => send(res, 500, { error: e.message })))
  .listen(BRIDGE_PORT, '127.0.0.1', () => console.log(`[bridge] 待ち受け中 http://127.0.0.1:${BRIDGE_PORT}`))

startReflex(bot, state, {
  async preempt (reason) {
    const current = state.current
    if (!current) return true
    if (DAMAGE_TOLERANT.has(current.verb)) return false // すでに脅威に対処している
    current.abort(reason)
    await current.done
    return true
  },
  record (entry) { state.history.push(entry) }
})

bot.on('death', () => {
  state.deaths += 1
  rememberDeath(state.memory, bot.entity.position, worldAge())
  persist()
  console.log(`[bot] ${bot.entity.position} で死んだ`)
})

bot.once('spawn', () => {
  configureMovements(bot, state)
  watchWear(bot, state)
  guardDigLoops(bot, state)
  console.log(`[bot] ${bot.entity.position} にスポーンした`)
})
// 採掘の開始をすべて記録する。pathfinder が道を開けるために掘るものも含む
let lastDig = null
bot.on('physicsTick', () => {
  const t = bot.targetDigBlock
  if (t && t !== lastDig) console.log(`[bot] ${t.position} の ${t.name} を掘る`)
  lastDig = t
})
bot.on('death', () => { console.log('[bot] 死んだ'); state.history.push({ action: 'event', ok: false, result: 'died', seconds: 0 }) })
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
    setTimeout(() => process.exit(0), 500)
  })
}
