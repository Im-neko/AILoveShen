// Minecraft ブリッジ: Mineflayer のボット + 視点のミラー + Python 側のための HTTP API。
//
//   PUT  /goal {predicate, item?, count?, where?, distance?, keep?: [{item, count}]}
//                          -> 目標を設定する（不正なら理由を添えて 400）。目標の状態を返す。
//                             keep: 中目標のためにチェストに取っておくもの（この目標のために取り出さない）
//   GET  /observe          -> { busy, observation, needs, goal: {spec, met, remaining, lines, blocked} | null,
//                               candidates: [{id, verb, target, ...}] }
//   POST /act {id}         -> 候補をもう一度作り、この id の候補を終わるまで実行する。
//                             { ok, result, seconds }
//   POST /check {specs: [...]} -> 目標を設定せずに条件（have, built, placed）を判定する:
//                             [{ spec, met, lines }]（1つでも不正なら理由を添えて 400）
//   PUT  /build-plan       -> { blocks: [{x,y,z,block}], width, depth, height, design, site? } 建てる計画を設定する
//                             （design: モデルの設計図。できた家とともに取っておく。site: 建てる場所 {x, z}。
//                             建ち終わると家になり、前の家は formerHomes に残る）
//   GET  /build-plan       -> 建築の状態（placed/total、原点、場所、まだないブロックの先頭）
//   GET  /sites            -> 調べた候補地の数字 { center, planned, sites: [{id, x, z, distance, flat_plots, ...}] }
//
// 設計: docs/design/11_primitive_actions.md
// 環境変数: MC_HOST (localhost) MC_PORT (25565) BOT_NAME (AILoveShen) BRIDGE_PORT (3000) MIRROR_PORT (25578)

import http from 'node:http'
import mineflayer from 'mineflayer'
import minecraftData from 'minecraft-data'
import pathfinderPkg from 'mineflayer-pathfinder'
import { startMirror } from './mirror.mjs'
import { summarize, round, threats, bearing } from './observe.mjs'
import { configureMovements, PRIMITIVES, DAMAGE_TOLERANT, TIMEOUTS_MS, DEFAULT_TIMEOUT_MS, HEALTH_CRITICAL } from './primitives.mjs'
import { BuildPlan } from './build.mjs'
import { RecipeBook } from './craft.mjs'
import { Knowledge } from './knowledge.mjs'
import { makeGoal, evaluate, needs, checkConditions } from './goals.mjs'
import { ground, describe, needsOutside } from './candidates.mjs'
import { snapshot, sightings } from './world.mjs'
import { loadState, saveState, settleHome, isInside, isDoorOpen, leaveHome, hasBed } from './home.mjs'
import { startReflex } from './reflex.mjs'
import { newMemory, remember, rememberDeath, summarizeMemory } from './memory.mjs'
import { surveyedSites } from './survey.mjs'

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
  survey: null, // 街の候補地の調査の計画（survey.mjs）
  memory: newMemory() // 前に見た場所（memory.mjs）。state.json に保存する
}
loadState(state)
const knowledge = new Knowledge(minecraftData(VERSION))

const worldAge = () => Number(bot.time.age)

// 完成した計画は家になる（別の場所なら引っ越し）。計画、家、前の家、調査、目標は再起動しても残る
function persist () {
  const former = state.home
  if (settleHome(state, bot)) console.log(`[bridge] ${former ? '引っ越した' : '家を設定'}: ドア ${state.home.door}`)
  saveState(state)
}

function observation () {
  const extra = {}
  if (state.plan) extra.build = state.plan.status(bot)
  extra.memory = summarizeMemory(state.memory, bot.entity.position, worldAge(), bearing)
  if (state.survey) extra.survey = { planned: state.survey.sites.length, surveyed: surveyedSites(state).length }
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

// 候補を1つ実行する。タイムアウトか、ボットがダメージを受けたら中断する（反射: 判断する側は
// そのあと攻撃してきた相手を見て、戦うか逃げるかを選べる）。中断すると経路移動と採掘を取り消し、
// 実行関数が本当に止まるまで待つので、行動が重なることはない。
async function act (id) {
  if (state.reflex) return { ok: false, result: 'not started: the reflex is handling a nearby threat', seconds: 0 }
  const c = decisionView().candidates.find((x) => x.id === id)
  if (!c) return { ok: false, result: `${id} is not available now`, seconds: 0 }
  state.busy = true
  const t = Date.now()
  const controller = new AbortController()
  const abort = (reason) => {
    if (controller.signal.aborted) return
    controller.abort(new Error(reason))
    if (bot.targetDigBlock) bot.stopDigging()
    bot.pathfinder.setGoal(null)
    bot.clearControlStates()
  }
  const timer = setTimeout(() => abort('timeout'), TIMEOUTS_MS[c.verb] ?? DEFAULT_TIMEOUT_MS)
  const onHurt = (entity) => {
    if (entity !== bot.entity) return
    if (DAMAGE_TOLERANT.has(c.verb)) {
      // 逃走は体力にかかわらず続ける。戦いはやめる
      if (c.verb === 'attack' && bot.health <= HEALTH_CRITICAL) abort(`stopped: health critical (${round(bot.health)}/20)`)
      return
    }
    const attacker = threats(bot)[0]?.e.name
    abort(`interrupted: took damage${attacker ? ` (${attacker} nearby)` : ''}`)
  }
  bot.on('entityHurt', onHurt)
  let ok = true
  let result
  let finished
  state.current = { id, verb: c.verb, abort, done: new Promise((resolve) => { finished = resolve }) }
  try {
    if (isInside(bot, state.home) && needsOutside(c, state.home)) await leaveHome(bot, state.home, { confront: !!c.confront })
    controller.signal.throwIfAborted()
    result = await PRIMITIVES[c.verb](bot, state, c, controller.signal)
    if (controller.signal.aborted) throw controller.signal.reason
  } catch (e) {
    ok = false
    const reason = controller.signal.aborted ? controller.signal.reason : e
    result = `failed: ${reason.message}`
    abort(reason.message)
  } finally {
    clearTimeout(timer)
    bot.off('entityHurt', onHurt)
    state.busy = false
    state.current = null
    finished()
    // 途中で見えたもの（変わるのは移動したあと）
    remember(state.memory, sightings(bot), bot.entity.position, worldAge())
    persist()
  }
  const seconds = round((Date.now() - t) / 1000)
  state.history.push({ action: id, ok, result, seconds })
  console.log(`[act] ${id}: ${ok ? '成功' : '失敗'} ${result}（${seconds}秒）`)
  return { ok, result, seconds }
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
      busy: state.busy || state.reflex,
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
    if (state.busy) return send(res, 409, { error: 'busy' })
    return send(res, 200, await act(id))
  }
  if (req.method === 'PUT' && req.url === '/build-plan') {
    state.plan = new BuildPlan(await readJson(req))
    persist()
    return send(res, 200, state.plan.status(bot))
  }
  if (req.method === 'GET' && req.url === '/sites') {
    // 調べた候補地の数字（ブリッジが測ったものだけ）。まだ調べていない候補地は入らない
    return send(res, 200, { center: state.survey?.center ?? null, planned: state.survey?.sites.length ?? 0, sites: surveyedSites(state).map((s) => state.memory.sites[s.id]) })
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
  rememberDeath(state.memory, bot.entity.position, worldAge())
  persist()
  console.log(`[bot] ${bot.entity.position} で死んだ`)
})

bot.once('spawn', () => {
  configureMovements(bot, state)
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
