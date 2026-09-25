// Protocol mirror: render the Mineflayer bot's POV on a real Minecraft client.
//
// The bot joins the real server. Every packet it receives is recorded (for late
// joiners) and relayed as raw bytes to viewers connected to a local fake server.
// A viewer "is" the bot: same entity id, locked to the bot's position/rotation.
// Viewer input is ignored (read-only spectator of the bot).
//
// startMirror(bot) is used by index.mjs. Standalone (viewer check without the bridge):
//   node src/mirror.mjs [--walk]
//   env: MC_HOST (localhost) MC_PORT (25565) MIRROR_PORT (25578) BOT_NAME (AILoveShen)

import { createRequire } from 'node:module'
import mineflayer from 'mineflayer'
import mc from 'minecraft-protocol'

const require = createRequire(import.meta.url)
const conv = require('mineflayer/lib/conversions')

const VERSION = '1.21.4'
const MC_HOST = process.env.MC_HOST ?? 'localhost'
const MC_PORT = Number(process.env.MC_PORT ?? 25565)
const MIRROR_PORT = Number(process.env.MIRROR_PORT ?? 25578)
const BOT_NAME = process.env.BOT_NAME ?? 'AILoveShen'
// Camera: the viewer is moved by absolute position packets. Send them at ~60Hz, interpolate the
// bot's 20Hz physics positions, and turn the view at a capped rate so bot.lookAt() snaps are not
// shown as instant jumps.
const CAMERA_INTERVAL_MS = 16
const TICK_MS = 50
const MAX_TURN_DEG_PER_S = 150
// Mineflayer starts digging right after an instant lookAt(); a leaf breaks in ~0.35s. Turn faster
// while a dig is in progress so the view reaches the block before it breaks.
const DIG_TURN_DEG_PER_S = 600
const TURN_EASE = 0.2 // fraction of the remaining angle covered per frame, before the speed cap

// Packets that are connection-scoped, not world state: never relayed.
const NOT_RELAYED = new Set([
  'keep_alive', 'ping', 'ping_response', 'login', 'position', 'start_configuration',
  'kick_disconnect', 'cookie_request', 'store_cookie', 'transfer', 'custom_report_details',
  'server_links', 'select_advancement_tab'
])
// Latest-wins state packets replayed to late joiners.
const SINGLETONS = new Set([
  'difficulty', 'abilities', 'held_item_slot', 'spawn_position', 'update_time', 'update_health',
  'experience', 'update_view_position', 'update_view_distance', 'simulation_distance',
  'initialize_world_border', 'playerlist_header', 'server_data', 'set_ticking_state', 'step_tick',
  'declare_commands', 'tags', 'declare_recipes', 'recipe_book_settings', 'set_cursor_item'
])
// Order-dependent packets replayed as a log to late joiners.
const LOGGED = new Set(['player_info', 'player_remove', 'teams', 'game_state_change', 'set_slot', 'window_items',
  'entity_effect', 'remove_entity_effect', 'boss_bar', 'advancements'])
const ENTITY_SPAWNS = new Set(['spawn_entity', 'spawn_entity_experience_orb'])
const ENTITY_ATTACHED = new Set(['entity_metadata', 'entity_equipment', 'entity_update_attributes',
  'entity_head_rotation', 'set_passengers', 'attach_entity'])

const chunkKey = (x, z) => `${x},${z}`

class WorldRecorder {
  constructor () {
    this.configPackets = []
    this.login = null
    this.singletons = new Map()
    this.log = []
    this.chunks = new Map() // key -> { chunk, light, changes: [] }
    this.entities = new Map() // id -> { spawn, attached: Map(name -> buffer) }
  }

  record (data, meta, buffer) {
    const name = meta.name
    if (meta.state === 'configuration') {
      if (name === 'start_configuration') this.configPackets = []
      if (!['keep_alive', 'ping', 'finish_configuration'].includes(name)) this.configPackets.push(buffer)
      return
    }
    if (meta.state !== 'play') return

    if (name === 'login') { this.login = { data, buffer }; return }
    if (name === 'respawn') { this.chunks.clear(); this.entities.clear() }
    if (SINGLETONS.has(name)) { this.singletons.set(name, buffer); return }
    if (LOGGED.has(name)) { this.log.push(buffer); return }

    switch (name) {
      case 'map_chunk': {
        this.chunks.set(chunkKey(data.x, data.z), { chunk: buffer, light: null, changes: [] })
        return
      }
      case 'update_light': {
        const c = this.chunks.get(chunkKey(data.chunkX, data.chunkZ))
        if (c) c.light = buffer
        return
      }
      case 'unload_chunk': {
        this.chunks.delete(chunkKey(data.chunkX, data.chunkZ))
        return
      }
      case 'block_change': {
        const c = this.chunks.get(chunkKey(data.location.x >> 4, data.location.z >> 4))
        if (c) c.changes.push(buffer)
        return
      }
      case 'multi_block_change': {
        const { x, z } = data.chunkCoordinates
        const c = this.chunks.get(chunkKey(x, z))
        if (c) c.changes.push(buffer)
        return
      }
    }
    if (ENTITY_SPAWNS.has(name)) {
      this.entities.set(data.entityId, { spawn: buffer, attached: new Map() })
      return
    }
    if (ENTITY_ATTACHED.has(name)) {
      const e = this.entities.get(data.entityId)
      if (e) e.attached.set(name, buffer)
      return
    }
    if (name === 'entity_destroy') {
      for (const id of data.entityIds) this.entities.delete(id)
    }
  }
}

function positionPacket (bot, teleportId) {
  const p = bot.entity.position
  return {
    teleportId,
    x: p.x, y: p.y, z: p.z,
    dx: 0, dy: 0, dz: 0,
    yaw: conv.toNotchianYaw(bot.entity.yaw),
    pitch: conv.toNotchianPitch(bot.entity.pitch),
    flags: {}
  }
}

const wrapDeg = (d) => ((d + 540) % 360) - 180

class Camera {
  constructor (bot) {
    this.bot = bot
    this.prev = null
    this.curr = null
    this.tickAt = 0
    this.yaw = null
    this.pitch = null
    this.lastFrame = Date.now()
    bot.on('physicsTick', () => {
      this.prev = this.curr ?? bot.entity.position.clone()
      this.curr = bot.entity.position.clone()
      this.tickAt = Date.now()
    })
  }

  frame (teleportId) {
    const bot = this.bot
    const now = Date.now()
    const dt = Math.min((now - this.lastFrame) / 1000, 0.1)
    this.lastFrame = now
    const targetYaw = conv.toNotchianYaw(bot.entity.yaw)
    const targetPitch = conv.toNotchianPitch(bot.entity.pitch)
    if (this.yaw === null) { this.yaw = targetYaw; this.pitch = targetPitch }
    const maxStep = (bot.targetDigBlock ? DIG_TURN_DEG_PER_S : MAX_TURN_DEG_PER_S) * dt
    const approach = (from, diff) => from + Math.sign(diff) * Math.min(Math.abs(diff), maxStep, Math.max(Math.abs(diff) * TURN_EASE, 0.5))
    this.yaw = approach(this.yaw, wrapDeg(targetYaw - this.yaw))
    this.pitch = approach(this.pitch, targetPitch - this.pitch)
    let p = bot.entity.position
    if (this.prev && this.curr) {
      const a = Math.min((now - this.tickAt) / TICK_MS, 1)
      p = this.prev.plus(this.curr.minus(this.prev).scaled(a))
    }
    return { teleportId, x: p.x, y: p.y, z: p.z, dx: 0, dy: 0, dz: 0, yaw: this.yaw, pitch: this.pitch, flags: {} }
  }
}

function replayWorld (viewer, rec, bot) {
  for (const buf of rec.singletons.values()) viewer.writeRaw(buf)
  for (const buf of rec.log) viewer.writeRaw(buf)
  // 13 = "start waiting for level chunks": leaves the loading-terrain screen once chunks arrive
  viewer.write('game_state_change', { reason: 'level_chunks_load_start', gameMode: 0 })
  viewer.write('chunk_batch_start', {})
  for (const c of rec.chunks.values()) {
    viewer.writeRaw(c.chunk)
    if (c.light) viewer.writeRaw(c.light)
    for (const ch of c.changes) viewer.writeRaw(ch)
  }
  viewer.write('chunk_batch_finished', { batchSize: rec.chunks.size })
  for (const [id, e] of rec.entities) {
    const live = bot.entities[id]
    viewer.writeRaw(e.spawn)
    for (const buf of e.attached.values()) viewer.writeRaw(buf)
    if (live) {
      viewer.write('entity_teleport', {
        entityId: id,
        x: live.position.x, y: live.position.y, z: live.position.z,
        dx: 0, dy: 0, dz: 0,
        yaw: conv.toNotchianYaw(live.yaw), pitch: conv.toNotchianPitch(live.pitch),
        flags: {}, onGround: !!live.onGround
      })
    }
  }
  viewer.write('position', positionPacket(bot, 1))
}

// The bot closing a window (a chest, a furnace, a crafting table) is a packet to the server, which
// sends nothing back: without this the viewers kept every window they were shown open on screen
export function relayCloses (client, viewers) {
  const write = client.write.bind(client)
  client.write = (name, params) => {
    write(name, params)
    if (name === 'close_window') for (const v of viewers) v.write('close_window', { windowId: params.windowId })
  }
}

export function startMirror (bot, { port = MIRROR_PORT, log = console.log } = {}) {
  const rec = new WorldRecorder()
  const viewers = new Set()

  bot._client.on('packet', (data, meta, _buffer, fullBuffer) => {
    rec.record(data, meta, fullBuffer)
    if (meta.state !== 'play' || NOT_RELAYED.has(meta.name)) return
    for (const v of viewers) v.writeRaw(fullBuffer)
  })

  relayCloses(bot._client, viewers)

  // registryCodec: {} -> nmp writes no registry_data of its own; the bot's recorded
  // configuration packets (known packs, registry data, tags, feature flags) are replayed instead.
  const server = mc.createServer({
    'online-mode': false, port, host: '127.0.0.1', version: VERSION,
    registryCodec: {}, enforceSecureProfile: false, motd: `${BOT_NAME} POV mirror`, maxPlayers: 4
  })

  server.on('login', (viewer) => {
    // Runs before nmp's own handler, which writes finish_configuration right away.
    viewer.prependOnceListener('login_acknowledged', () => {
      viewer.state = mc.states.CONFIGURATION
      for (const buf of rec.configPackets) viewer.writeRaw(buf)
    })
  })

  server.on('playerJoin', (viewer) => {
    if (!rec.login) { viewer.end('bot is not in the world yet'); return }
    log(`[mirror] viewer ${viewer.username} joined`)
    viewer.write('login', { ...rec.login.data, maxPlayers: 4 })
    replayWorld(viewer, rec, bot)
    viewers.add(viewer)
    viewer.on('end', () => { viewers.delete(viewer); log(`[mirror] viewer ${viewer.username} left`) })
    viewer.on('error', (e) => log(`[mirror] viewer error: ${e.message}`))
  })

  let teleportId = 2
  let camera = null
  const timer = setInterval(() => {
    if (!bot.entity || viewers.size === 0) return
    camera ??= new Camera(bot)
    const pkt = camera.frame(teleportId++)
    for (const v of viewers) v.write('position', pkt)
  }, CAMERA_INTERVAL_MS)

  // Server never echoes a player's own swing/dig progress: synthesize them for the viewer.
  // Mineflayer has no dig-start event, so watch bot.targetDigBlock every tick.
  const DIG_BREAKER_ID = 0x7ffffff0
  let dig = null // { pos, start, total, lastStage }
  bot.on('physicsTick', () => {
    const target = bot.targetDigBlock
    if (dig && (!target || !target.position.equals(dig.pos))) {
      for (const v of viewers) v.write('block_break_animation', { entityId: DIG_BREAKER_ID, location: dig.pos, destroyStage: -1 })
      dig = null
    }
    if (!target) return
    if (!dig) dig = { pos: target.position.clone(), start: Date.now(), total: Math.max(bot.digTime(target), 1), ticks: 0 }
    if (dig.ticks++ % 5 !== 0) return
    const stage = Math.min(9, Math.floor(((Date.now() - dig.start) / dig.total) * 10))
    for (const v of viewers) {
      v.write('animation', { entityId: bot.entity.id, animation: 0 })
      v.write('block_break_animation', { entityId: DIG_BREAKER_ID, location: dig.pos, destroyStage: stage })
    }
  })

  server.on('listening', () => log(`[mirror] listening on 127.0.0.1:${port}`))
  return { server, viewers, recorder: rec, close: () => { clearInterval(timer); server.close() } }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const bot = mineflayer.createBot({ host: MC_HOST, port: MC_PORT, username: BOT_NAME, version: VERSION, auth: 'offline' })
  startMirror(bot)
  bot.once('spawn', () => {
    console.log(`[bot] spawned at ${bot.entity.position}`)
    if (!process.argv.includes('--walk')) return
    // Demo motion so the viewer shows movement and camera turns.
    let t = 0
    setInterval(() => {
      t += 1
      bot.look(Math.sin(t / 10) * Math.PI, 0, false)
      bot.setControlState('forward', t % 40 < 20)
      bot.setControlState('jump', t % 40 === 5)
    }, 100)
  })
  bot.on('kicked', (r) => console.log('[bot] kicked', JSON.stringify(r)))
  bot.on('error', (e) => console.log('[bot] error', e.message))
}
