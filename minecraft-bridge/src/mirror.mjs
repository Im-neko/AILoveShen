// プロトコルのミラー: Mineflayer のボットの視点を本物の Minecraft クライアントに映す。
//
// ボットは本物のサーバーに参加する。受け取ったパケットはすべて記録し（後から参加する視聴者の
// ため）、ローカルの偽サーバーにつないだ視聴者にそのままのバイト列で中継する。
// 視聴者はボット「そのもの」: 同じエンティティ id で、ボットの位置と向きに固定される。
// 視聴者の入力は無視する（ボットを見るだけの読み取り専用の観戦者）。
//
// startMirror(bot) は index.mjs が使う。単体で動かす（ブリッジなしで視聴者を確かめる）:
//   node src/mirror.mjs [--walk]
//   環境変数: MC_HOST (localhost) MC_PORT (25565) MIRROR_PORT (25578) BOT_NAME (AILoveShen)

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
// カメラ: 視聴者は絶対位置のパケットで動かす。約 60Hz で送り、ボットの 20Hz の物理の位置を補間し、
// 視点の回転速度に上限を設けて、bot.lookAt() の瞬時の向き変えが一瞬で飛ぶように見えないようにする。
const CAMERA_INTERVAL_MS = 16
const TICK_MS = 50
const MAX_TURN_DEG_PER_S = 150
// Mineflayer は瞬時の lookAt() の直後に掘り始め、葉は約 0.35 秒で壊れる。掘っている間は速く回し、
// 壊れる前に視点がブロックに届くようにする。
const DIG_TURN_DEG_PER_S = 600
const TURN_EASE = 0.2 // 1フレームで進む残りの角度の割合（速度の上限をかける前）

// ワールドの状態ではなく接続ごとのパケット: 中継しない。
const NOT_RELAYED = new Set([
  'keep_alive', 'ping', 'ping_response', 'login', 'position', 'start_configuration',
  'kick_disconnect', 'cookie_request', 'store_cookie', 'transfer', 'custom_report_details',
  'server_links', 'select_advancement_tab'
])
// 最新のものだけが意味を持つ状態のパケット。後から参加する視聴者に再生する。
const SINGLETONS = new Set([
  'difficulty', 'abilities', 'held_item_slot', 'spawn_position', 'update_time', 'update_health',
  'experience', 'update_view_position', 'update_view_distance', 'simulation_distance',
  'initialize_world_border', 'playerlist_header', 'server_data', 'set_ticking_state', 'step_tick',
  'declare_commands', 'tags', 'declare_recipes', 'recipe_book_settings', 'set_cursor_item'
])
// 順序が意味を持つパケット。後から参加する視聴者にログとして再生する。
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
    this.chunks = new Map() // キー -> { chunk, light, changes: [] }
    this.entities = new Map() // id -> { spawn, attached: Map(名前 -> バッファ) }
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
  // 13 = 「レベルのチャンクを待ち始める」: チャンクが届いたら地形の読み込み画面を抜ける
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

// ボットがウィンドウ（チェスト、かまど、作業台）を閉じるのはサーバーへのパケットで、サーバーは
// 何も返さない: これがないと、視聴者の画面には一度表示されたウィンドウがすべて開いたまま残った
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

  // registryCodec: {} -> nmp は自前の registry_data を書かない。代わりに、記録したボットの
  // 設定のパケット（known packs、registry data、tags、feature flags）を再生する。
  const server = mc.createServer({
    'online-mode': false, port, host: '127.0.0.1', version: VERSION,
    registryCodec: {}, enforceSecureProfile: false, motd: `${BOT_NAME} POV mirror`, maxPlayers: 4
  })

  server.on('login', (viewer) => {
    // nmp 自身のハンドラより先に動く。nmp のハンドラはすぐに finish_configuration を書いてしまう。
    viewer.prependOnceListener('login_acknowledged', () => {
      viewer.state = mc.states.CONFIGURATION
      for (const buf of rec.configPackets) viewer.writeRaw(buf)
    })
  })

  server.on('playerJoin', (viewer) => {
    if (!rec.login) { viewer.end('bot is not in the world yet'); return }
    log(`[mirror] 視聴者 ${viewer.username} が参加した`)
    viewer.write('login', { ...rec.login.data, maxPlayers: 4 })
    replayWorld(viewer, rec, bot)
    viewers.add(viewer)
    viewer.on('end', () => { viewers.delete(viewer); log(`[mirror] 視聴者 ${viewer.username} が退出した`) })
    viewer.on('error', (e) => log(`[mirror] 視聴者のエラー: ${e.message}`))
  })

  let teleportId = 2
  let camera = null
  const timer = setInterval(() => {
    if (!bot.entity || viewers.size === 0) return
    camera ??= new Camera(bot)
    const pkt = camera.frame(teleportId++)
    for (const v of viewers) v.write('position', pkt)
  }, CAMERA_INTERVAL_MS)

  // サーバーはプレイヤー自身の腕振りや採掘の進み具合を送り返さない: 視聴者のために合成する。
  // Mineflayer には採掘開始のイベントがないので、毎ティック bot.targetDigBlock を見る。
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

  server.on('listening', () => log(`[mirror] 127.0.0.1:${port} で待ち受け中`))
  return { server, viewers, recorder: rec, close: () => { clearInterval(timer); server.close() } }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const bot = mineflayer.createBot({ host: MC_HOST, port: MC_PORT, username: BOT_NAME, version: VERSION, auth: 'offline' })
  startMirror(bot)
  bot.once('spawn', () => {
    console.log(`[bot] ${bot.entity.position} にスポーンした`)
    if (!process.argv.includes('--walk')) return
    // 視聴者に移動とカメラの回転が映るようにするデモの動き。
    let t = 0
    setInterval(() => {
      t += 1
      bot.look(Math.sin(t / 10) * Math.PI, 0, false)
      bot.setControlState('forward', t % 40 < 20)
      bot.setControlState('jump', t % 40 === 5)
    }, 100)
  })
  bot.on('kicked', (r) => console.log('[bot] キックされた', JSON.stringify(r)))
  bot.on('error', (e) => console.log('[bot] エラー', e.message))
}
