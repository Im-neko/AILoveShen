// Jev spike bridge: Mineflayer bot + POV mirror + HTTP API for observation and bounded actions.
//
//   GET  /observe          -> { raw, summary, actions: [{id, description}] }
//   POST /act {id}         -> runs one bounded action to completion (or timeout) and returns its result
//
// Usage: node bridge.mjs   env: BRIDGE_PORT (3000) + mirror.mjs env vars

import http from 'node:http'
import mineflayer from 'mineflayer'
import pathfinderPkg from 'mineflayer-pathfinder'
import { startMirror } from './mirror.mjs'

const { pathfinder, Movements, goals } = pathfinderPkg
const BRIDGE_PORT = Number(process.env.BRIDGE_PORT ?? 3000)
const ACTION_TIMEOUT_MS = 10000
const MOB_RADIUS = 48
const MOB_LIMIT = 16
const DROP_RADIUS = 24
const HOSTILE_ACT_RADIUS = 16
const HISTORY = 5

const bot = mineflayer.createBot({
  host: process.env.MC_HOST ?? 'localhost',
  port: Number(process.env.MC_PORT ?? 25565),
  username: process.env.BOT_NAME ?? 'AILoveShen',
  version: '1.21.4',
  auth: 'offline'
})
bot.loadPlugin(pathfinder)
startMirror(bot)

const history = []
let busy = false

// ---------- observation ----------

const round = (v, d = 1) => Math.round(v * 10 ** d) / 10 ** d

function bearing (from, to) {
  // Compass direction of `to` seen from `from` (Minecraft: -Z north, +X east)
  const dx = to.x - from.x
  const dz = to.z - from.z
  const deg = (Math.atan2(dx, -dz) * 180 / Math.PI + 360) % 360
  return ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(deg / 45) % 8]
}

function dayPhase (timeOfDay) {
  if (timeOfDay < 12000) return 'day'
  if (timeOfDay < 13000) return 'dusk'
  if (timeOfDay < 23000) return 'night'
  return 'dawn'
}

function inventoryCounts () {
  const counts = {}
  for (const it of bot.inventory.items()) counts[it.name] = (counts[it.name] ?? 0) + it.count
  return counts
}

const isFood = (item) => !!bot.registry.foodsByName[item.name]
const isLog = (name) => name.endsWith('_log')
const WEAPONS = ['netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword', 'golden_sword', 'wooden_sword',
  'netherite_axe', 'diamond_axe', 'iron_axe', 'stone_axe', 'golden_axe', 'wooden_axe']

function nearbyEntities () {
  const me = bot.entity.position
  return Object.values(bot.entities)
    .filter((e) => e !== bot.entity && e.position)
    .map((e) => ({ e, dist: e.position.distanceTo(me) }))
    .sort((a, b) => a.dist - b.dist)
}

function findLog () {
  return bot.findBlock({ matching: (b) => isLog(b.name), maxDistance: 32 })
}

function summary () {
  const me = bot.entity.position
  const ents = nearbyEntities()
  const mobs = ents
    .filter(({ e, dist }) => e.type !== 'player' && e.name !== 'item' && e.type !== 'other' && dist <= MOB_RADIUS)
    .slice(0, MOB_LIMIT)
    .map(({ e, dist }) => ({
      name: e.name,
      hostile: e.type === 'hostile',
      distance_m: round(dist),
      direction: bearing(me, e.position),
      height_diff_m: round(e.position.y - me.y)
    }))
  const drops = ents
    .filter(({ e, dist }) => e.name === 'item' && dist <= DROP_RADIUS)
    .map(({ e, dist }) => ({ item: e.getDroppedItem()?.name ?? 'unknown', distance_m: round(dist), direction: bearing(me, e.position) }))
  const log = findLog()
  const held = bot.heldItem
  return {
    time: { phase: dayPhase(bot.time.timeOfDay), time_of_day: bot.time.timeOfDay, raining: bot.isRaining },
    self: {
      health: bot.health,
      max_health: 20,
      food: bot.food,
      position: { x: round(me.x), y: round(me.y), z: round(me.z) },
      held_item: held ? held.name : null,
      in_water: !!bot.entity.isInWater,
      on_ground: bot.entity.onGround
    },
    inventory: inventoryCounts(),
    mobs,
    drops,
    resources: { nearest_log: log ? { block: log.name, distance_m: round(log.position.distanceTo(me)) } : null },
    recent_actions: history.slice(-HISTORY)
  }
}

function raw () {
  // Deliberately unsummarized: roughly what the bot "knows", as Mineflayer exposes it.
  const me = bot.entity.position
  const blocks = []
  for (let dx = -3; dx <= 3; dx++) {
    for (let dy = -2; dy <= 2; dy++) {
      for (let dz = -3; dz <= 3; dz++) {
        const b = bot.blockAt(me.offset(dx, dy, dz))
        if (b && b.name !== 'air') blocks.push([dx, dy, dz, b.name])
      }
    }
  }
  return {
    entity: { id: bot.entity.id, position: me, velocity: bot.entity.velocity, yaw: bot.entity.yaw, pitch: bot.entity.pitch, onGround: bot.entity.onGround },
    health: bot.health,
    food: bot.food,
    foodSaturation: bot.foodSaturation,
    oxygenLevel: bot.oxygenLevel,
    experience: bot.experience,
    game: bot.game,
    time: bot.time,
    isRaining: bot.isRaining,
    quickBarSlot: bot.quickBarSlot,
    inventory: bot.inventory.slots.filter(Boolean).map((it) => ({ slot: it.slot, name: it.name, count: it.count, type: it.type, durabilityUsed: it.durabilityUsed })),
    entities: Object.values(bot.entities).filter((e) => e !== bot.entity).map((e) => ({
      id: e.id, type: e.type, name: e.name, username: e.username, position: e.position, velocity: e.velocity,
      yaw: e.yaw, pitch: e.pitch, onGround: e.onGround, height: e.height, width: e.width
    })),
    blocksAround: blocks
  }
}

// ---------- bounded actions ----------

function hostilesWithin (r) {
  return nearbyEntities().filter(({ e, dist }) => e.type === 'hostile' && dist <= r && Math.abs(e.position.y - bot.entity.position.y) < 6)
}

function availableActions () {
  const acts = []
  const inv = inventoryCounts()
  const hostiles = hostilesWithin(HOSTILE_ACT_RADIUS)
  if (hostiles.length) {
    const h = hostiles[0]
    acts.push({ id: 'attack_hostile', description: `Fight the nearest hostile mob (${h.e.name}, ${round(h.dist)}m away) with the held item until it dies or 10s pass`, generic: 'Fight the nearest hostile mob' })
    acts.push({ id: 'flee_hostile', description: `Run 16m away from the nearest hostile mob (${h.e.name}, ${round(h.dist)}m away)`, generic: 'Run away from the nearest hostile mob' })
  }
  const weapon = WEAPONS.find((w) => inv[w])
  if (weapon && bot.heldItem?.name !== weapon) acts.push({ id: 'equip_weapon', description: `Hold ${weapon} in hand`, generic: 'Hold the best weapon in hand' })
  const food = bot.inventory.items().find(isFood)
  if (food && bot.food < 20) acts.push({ id: 'eat', description: `Eat one ${food.name} (hunger ${bot.food}/20)`, generic: 'Eat food from the inventory' })
  const drop = nearbyEntities().find(({ e, dist }) => e.name === 'item' && dist <= DROP_RADIUS)
  if (drop) acts.push({ id: 'pickup_drop', description: `Walk to the dropped ${drop.e.getDroppedItem()?.name ?? 'item'} ${round(drop.dist)}m away and pick it up`, generic: 'Pick up the nearest dropped item' })
  const log = findLog()
  if (log) acts.push({ id: 'collect_log', description: `Walk to the nearest ${log.name} (${round(log.position.distanceTo(bot.entity.position))}m away) and chop it`, generic: 'Chop the nearest tree log' })
  const logs = Object.entries(inv).find(([n]) => isLog(n))
  if (logs) acts.push({ id: 'craft_planks', description: `Craft planks from ${logs[0]} (have ${logs[1]})`, generic: 'Craft planks from logs' })
  acts.push({ id: 'explore', description: 'Walk about 20m in a random direction to find new things', generic: 'Explore in a random direction' })
  acts.push({ id: 'idle', description: 'Stay still and look around for 3 seconds', generic: 'Stay still and look around' })
  return acts
}

const withTimeout = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))])

async function goNear (pos, range) {
  bot.pathfinder.setMovements(new Movements(bot))
  try {
    await bot.pathfinder.goto(new goals.GoalNear(pos.x, pos.y, pos.z, range))
  } finally {
    bot.pathfinder.stop()
  }
}

const EXECUTORS = {
  async attack_hostile () {
    const target = hostilesWithin(HOSTILE_ACT_RADIUS)[0]?.e
    if (!target) return 'no hostile in range'
    const start = Date.now()
    while (bot.entities[target.id] && target.isValid !== false && Date.now() - start < ACTION_TIMEOUT_MS) {
      const d = target.position.distanceTo(bot.entity.position)
      if (d > 3) {
        bot.pathfinder.setMovements(new Movements(bot))
        bot.pathfinder.setGoal(new goals.GoalFollow(target, 2), true)
      } else {
        await bot.lookAt(target.position.offset(0, target.height * 0.8, 0), true)
        bot.attack(target)
      }
      await bot.waitForTicks(12)
    }
    bot.pathfinder.stop()
    return bot.entities[target.id] ? `${target.name} still alive` : `${target.name} killed`
  },
  async flee_hostile () {
    const h = hostilesWithin(HOSTILE_ACT_RADIUS)[0]?.e
    if (!h) return 'no hostile in range'
    const me = bot.entity.position
    const away = me.minus(h.position).normalize().scaled(16)
    await goNear(me.plus(away), 3)
    return `now ${round(h.position.distanceTo(bot.entity.position))}m from ${h.name}`
  },
  async equip_weapon () {
    const inv = inventoryCounts()
    const w = WEAPONS.find((n) => inv[n])
    await bot.equip(bot.inventory.items().find((i) => i.name === w), 'hand')
    return `holding ${w}`
  },
  async eat () {
    const food = bot.inventory.items().find(isFood)
    const before = bot.food
    await bot.equip(food, 'hand')
    await bot.consume()
    return `ate ${food.name}, hunger ${before} -> ${bot.food}`
  },
  async pickup_drop () {
    const d = nearbyEntities().find(({ e }) => e.name === 'item')?.e
    if (!d) return 'no drop'
    const before = bot.inventory.items().length
    await goNear(d.position, 0.5)
    await bot.waitForTicks(10)
    return `picked up (stacks ${before} -> ${bot.inventory.items().length})`
  },
  async collect_log () {
    const log = findLog()
    if (!log) return 'no log nearby'
    await goNear(log.position, 2)
    // Digging while airborne is 5x slower (e.g. right after chopping the block we stood on)
    for (let i = 0; i < 40 && !bot.entity.onGround; i++) await bot.waitForTicks(1)
    await bot.dig(bot.blockAt(log.position), true)
    const drop = nearbyEntities().find(({ e, dist }) => e.name === 'item' && dist < 6)?.e
    if (drop) { await goNear(drop.position, 0.5).catch(() => {}); await bot.waitForTicks(10) }
    return `chopped ${log.name}; logs now ${Object.entries(inventoryCounts()).filter(([n]) => isLog(n)).map(([n, c]) => `${n}x${c}`).join(',') || 0}`
  },
  async craft_planks () {
    const logItem = bot.inventory.items().find((i) => isLog(i.name))
    const plankName = logItem.name.replace(/_log$/, '_planks')
    const recipe = bot.recipesFor(bot.registry.itemsByName[plankName].id, null, 1, null)[0]
    if (!recipe) return `no recipe for ${plankName}`
    await bot.craft(recipe, 1, null)
    return `crafted ${plankName}`
  },
  async explore () {
    const a = Math.random() * Math.PI * 2
    const target = bot.entity.position.offset(Math.cos(a) * 20, 0, Math.sin(a) * 20)
    bot.pathfinder.setMovements(new Movements(bot))
    await goNear(target, 3).catch(() => {})
    return `walked to ${round(bot.entity.position.x)},${round(bot.entity.position.z)}`
  },
  async idle () {
    for (let i = 0; i < 3; i++) { await bot.look(bot.entity.yaw + Math.PI / 2, 0); await bot.waitForTicks(20) }
    return 'looked around'
  }
}

async function act (id) {
  const known = availableActions().map((a) => a.id)
  if (!known.includes(id)) return { ok: false, result: `action ${id} not available now` }
  busy = true
  const t = Date.now()
  let ok = true
  let result
  try {
    result = await withTimeout(EXECUTORS[id](), ACTION_TIMEOUT_MS + 2000)
  } catch (e) {
    ok = false
    result = `failed: ${e.message}`
    if (bot.targetDigBlock) bot.stopDigging()
    bot.pathfinder.stop()
    bot.clearControlStates()
  } finally {
    busy = false
  }
  history.push({ action: id, ok, result, seconds: round((Date.now() - t) / 1000) })
  return { ok, result, seconds: round((Date.now() - t) / 1000) }
}

// ---------- HTTP ----------

const send = (res, code, body) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(body, (_k, v) => typeof v === 'bigint' ? Number(v) : v)) }

http.createServer((req, res) => handle(req, res).catch((e) => send(res, 500, { error: e.stack }))).listen(BRIDGE_PORT, '127.0.0.1', () => console.log(`[bridge] http://127.0.0.1:${BRIDGE_PORT}`))

async function handle (req, res) {
  if (!bot.entity) return send(res, 503, { error: 'bot not spawned' })
  if (req.method === 'GET' && req.url === '/observe') {
    return send(res, 200, { busy, raw: raw(), summary: summary(), actions: availableActions() })
  }
  if (req.method === 'POST' && req.url === '/act') {
    let body = ''
    for await (const chunk of req) body += chunk
    const { id } = JSON.parse(body || '{}')
    if (busy) return send(res, 409, { error: 'busy' })
    return send(res, 200, await act(id))
  }
  send(res, 404, { error: 'not found' })
}

bot.once('spawn', () => console.log(`[bot] spawned at ${bot.entity.position}`))
bot.on('death', () => { console.log('[bot] died'); history.push({ action: 'event', ok: false, result: 'died' }) })
bot.on('kicked', (r) => console.log('[bot] kicked', JSON.stringify(r)))
bot.on('error', (e) => console.log('[bot] error', e.message))
