// Minecraft bridge: Mineflayer bot + POV mirror + HTTP API for the Python side.
//
//   GET  /observe          -> { observation, actions: [{id, description}], busy }
//   POST /act {id}         -> runs one bounded action to completion; { ok, result, seconds }
//   PUT  /build-plan       -> { blocks: [{x,y,z,block}], width, depth, height } sets the plan to build
//   GET  /build-plan       -> build status (placed/total, origin, first missing blocks)
//
// env: MC_HOST (localhost) MC_PORT (25565) BOT_NAME (AILoveShen) BRIDGE_PORT (3000) MIRROR_PORT (25578)

import http from 'node:http'
import mineflayer from 'mineflayer'
import pathfinderPkg from 'mineflayer-pathfinder'
import { startMirror } from './mirror.mjs'
import { summarize, round, threats } from './observe.mjs'
import { availableActions, configureMovements, findLog, findTable, EXECUTORS, ACTION_TIMEOUT_MS, ACTION_TIMEOUTS_MS } from './actions.mjs'
import { BuildPlan } from './build.mjs'
import { RecipeBook } from './craft.mjs'
import { loadState, saveState, homeFromPlan, isInside, isDoorOpen, leaveHome } from './home.mjs'
import { startReflex } from './reflex.mjs'

const BRIDGE_PORT = Number(process.env.BRIDGE_PORT ?? 3000)

const bot = mineflayer.createBot({
  host: process.env.MC_HOST ?? 'localhost',
  port: Number(process.env.MC_PORT ?? 25565),
  username: process.env.BOT_NAME ?? 'AILoveShen',
  version: '1.21.4',
  auth: 'offline'
})
bot.loadPlugin(pathfinderPkg.pathfinder)
startMirror(bot)

const state = {
  plan: null,
  home: null,
  history: [],
  busy: false, // an action is running
  reflex: false, // the reflex is handling a nearby threat
  current: null, // { id, abort(reason), done } of the running action
  recipeBook: new RecipeBook(bot),
  unreachableDrops: new Set()
}
loadState(state)

// A finished plan becomes the home; both survive restarts
function persist () {
  if (state.plan?.origin && !state.home && state.plan.status(bot).complete) {
    state.home = homeFromPlan(state.plan)
    console.log(`[bridge] home set: door ${state.home?.door}`)
  }
  saveState(state)
}

function observation () {
  const me = bot.entity.position
  const log = findLog(bot)
  const table = findTable(bot)
  const extra = {
    resources: {
      nearest_reachable_log: log ? { block: log.name, distance_m: round(log.position.distanceTo(me)) } : null,
      crafting_table: table ? { distance_m: round(table.position.distanceTo(me)) } : null
    },
    crafting_table_nearby: !!table
  }
  if (state.plan) extra.build = { ...state.plan.status(bot), materials_needed: state.plan.materialsNeeded(bot) }
  extra.home = state.home ? { inside: isInside(bot, state.home), door_open: isDoorOpen(bot, state.home) } : null
  extra.food_items = bot.inventory.items().filter((i) => bot.registry.foodsByName[i.name]).reduce((n, i) => n + i.count, 0)
  return summarize(bot, state.history, extra)
}

// Survival actions are how the bot answers an attack, so taking damage does not interrupt them.
const DAMAGE_TOLERANT = new Set(['attack_hostile', 'flee_hostile'])
// Actions done where the bot stands; every other action first leaves the house if the bot is in it
const IN_PLACE = new Set(['eat', 'equip_weapon', 'idle', 'craft_planks', 'craft_crafting_table', 'go_home', 'stay_inside'])

// Runs one action. It is aborted on timeout, or when the bot takes damage (a reflex: the decision
// maker then sees the attacker and can fight or flee). Aborting cancels pathing and digging, and
// the executor is awaited until it has really stopped, so actions never overlap.
async function act (id) {
  if (state.reflex) return { ok: false, result: 'not started: the reflex is handling a nearby threat', seconds: 0 }
  if (!availableActions(bot, state).some((a) => a.id === id)) return { ok: false, result: `action ${id} is not available now`, seconds: 0 }
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
  const timer = setTimeout(() => abort('timeout'), ACTION_TIMEOUTS_MS[id] ?? ACTION_TIMEOUT_MS)
  const onHurt = (entity) => {
    if (entity !== bot.entity || DAMAGE_TOLERANT.has(id)) return
    const attacker = threats(bot)[0]?.e.name
    abort(`interrupted: took damage${attacker ? ` (${attacker} nearby)` : ''}`)
  }
  bot.on('entityHurt', onHurt)
  let ok = true
  let result
  let finished
  state.current = { id, abort, done: new Promise((resolve) => { finished = resolve }) }
  try {
    if (!IN_PLACE.has(id) && isInside(bot, state.home)) await leaveHome(bot, state.home)
    controller.signal.throwIfAborted()
    result = await EXECUTORS[id](bot, state, controller.signal)
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
    persist()
  }
  const seconds = round((Date.now() - t) / 1000)
  state.history.push({ action: id, ok, result, seconds })
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
    return send(res, 200, { busy: state.busy || state.reflex, observation: observation(), actions: availableActions(bot, state) })
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
  if (req.method === 'GET' && req.url === '/build-plan') {
    return send(res, state.plan ? 200 : 404, state.plan ? state.plan.status(bot) : { error: 'no plan' })
  }
  send(res, 404, { error: 'not found' })
}

http.createServer((req, res) => handle(req, res).catch((e) => send(res, 500, { error: e.message })))
  .listen(BRIDGE_PORT, '127.0.0.1', () => console.log(`[bridge] http://127.0.0.1:${BRIDGE_PORT}`))

startReflex(bot, state, {
  async preempt (reason) {
    const current = state.current
    if (!current) return true
    if (DAMAGE_TOLERANT.has(current.id)) return false // already dealing with the threat
    current.abort(reason)
    await current.done
    return true
  },
  record (entry) { state.history.push(entry) }
})

bot.once('spawn', () => {
  configureMovements(bot, state)
  console.log(`[bot] spawned at ${bot.entity.position}`)
})
// Every dig start, including those the pathfinder makes to clear its way
let lastDig = null
bot.on('physicsTick', () => {
  const t = bot.targetDigBlock
  if (t && t !== lastDig) console.log(`[bot] digging ${t.name} at ${t.position}`)
  lastDig = t
})
bot.on('death', () => { console.log('[bot] died'); state.history.push({ action: 'event', ok: false, result: 'died', seconds: 0 }) })
bot.on('kicked', (r) => console.log('[bot] kicked', JSON.stringify(r)))
bot.on('error', (e) => console.log('[bot] error', e.message))
