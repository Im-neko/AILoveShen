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
  // 記録したパケットはサーバーが送ったものだけ: ボット自身の持ち替えと持ち物の移動は今の状態から送る
  if (bot.inventory) viewer.write('window_items', inventoryPacket(bot, itemClass(bot)))
  if (bot.quickBarSlot != null) viewer.write('held_item_slot', { slot: bot.quickBarSlot })
  viewer.write('position', positionPacket(bot, 1))
}

const itemClass = (bot) => require('prismarine-item')(bot.registry ?? VERSION)

// 視聴者に見せる画面（チェスト、かまど、作業台）は、ボットが閉じてもしばらく開けておく
export const WINDOW_HOLD_MS = 2000

// チェストや作業台の画面が視聴者に映らなかった理由（設計書 53）:
//  - ボットは開いて、数十ミリ秒で操作して、すぐ閉じる。閉じたことをそのまま中継すると、画面は一瞬で消える
//  - ボット自身のクリック（取り出す、入れる、クラフトの結果を取る）はサーバーが送り返さない
//    （クライアントが結果のハッシュを付けて送るため）。視聴者の開いた画面の中身が変わらなかった
// そこで、開いた画面ごとに: ボットのクリックの後に画面の今の中身を送り、閉じるのは最後の変化から
// `holdMs` 後にする。待つのは視聴者に送る分だけで、ボットの操作は待たせない。
// ウィンドウ 0（持ち物、2x2 のクラフト）は視聴者のクライアントが開かないので、そのまま（閉じるだけ即送る）。
export function windowPacket (window, Item) {
  return {
    windowId: window.id,
    stateId: 0,
    items: window.slots.map((i) => safeSlot(Item.toNotch(i ?? null))),
    carriedItem: safeSlot(Item.toNotch(null))
  }
}

export function relayWindows (bot, viewers, Item, { holdMs = WINDOW_HOLD_MS, delayMs = INVENTORY_SYNC_MS, now = Date.now } = {}) {
  const client = bot._client
  const write = client.write.bind(client)
  const shown = new Map() // windowId -> { opened: バッファ, at: 開いた／最後に変わった時刻, window, timer, closing }
  const send = (name, params) => { for (const v of viewers) v.write(name, params) }

  const sync = (entry) => {
    if (entry.window && viewers.size) send('window_items', windowPacket(entry.window, Item()))
  }
  const forget = (id) => {
    const entry = shown.get(id)
    if (!entry) return
    clearTimeout(entry.timer)
    clearTimeout(entry.syncTimer)
    shown.delete(id)
  }
  const finish = (id) => {
    if (!shown.has(id)) return
    forget(id)
    send('close_window', { windowId: id })
  }
  const requestClose = (id) => {
    const entry = shown.get(id)
    if (!entry || entry.closing) return
    entry.closing = true
    clearTimeout(entry.syncTimer)
    sync(entry) // 閉じる前の最後の中身
    const wait = entry.at + holdMs - now()
    if (wait <= 0) finish(id)
    else entry.timer = setTimeout(() => finish(id), wait)
  }
  const touch = (id) => {
    const entry = shown.get(id)
    if (!entry || entry.closing) return
    // ボットの window はこの時点で currentWindow（閉じた後は null になるので、ここで覚える）
    if (bot.currentWindow?.id === id) entry.window = bot.currentWindow
    entry.at = now()
    if (entry.syncTimer) return
    entry.syncTimer = setTimeout(() => { entry.syncTimer = null; sync(entry) }, delayMs)
  }

  client.write = (name, params) => {
    write(name, params)
    if (name === 'window_click' && params.windowId) touch(params.windowId)
    if (name === 'close_window') {
      if (params.windowId) {
        const entry = shown.get(params.windowId)
        if (entry && bot.currentWindow?.id === params.windowId) entry.window = bot.currentWindow
        requestClose(params.windowId)
      } else send('close_window', { windowId: 0 })
    }
  }

  return {
    // サーバーのパケット。true なら、そのまま中継しない
    fromServer (name, data, buffer) {
      if (name === 'open_window') {
        // 開いている画面は新しい画面に置き換わる
        for (const id of [...shown.keys()]) forget(id)
        shown.set(data.windowId, { opened: buffer, at: now(), window: null, timer: null, syncTimer: null, closing: false })
        return false
      }
      if (name === 'close_window') { requestClose(data.windowId); return true }
      if (name === 'respawn' || name === 'login') for (const id of [...shown.keys()]) forget(id)
      return false
    },
    // あとから参加した視聴者にも、開いている画面を見せる
    replay (viewer) {
      for (const entry of shown.values()) {
        viewer.writeRaw(entry.opened)
        if (entry.window) viewer.write('window_items', windowPacket(entry.window, Item()))
      }
    },
    open: () => [...shown.keys()],
    dispose: () => { for (const id of [...shown.keys()]) forget(id) }
  }
}

const INVENTORY_SYNC_MS = 100

// 自分で作り直して送ってよい部品（アイテムのコンポーネント）: 数 1 つだけのもの。ほかの部品（名前、
// 附呪、NBT など）は minecraft-data の型が本物のクライアントと 1 バイトでもずれると、視聴者が
// 「… bytes extra whilst reading packet」で切れる。サーバーの生のバイト列を中継するときは関係ない
const SAFE_COMPONENTS = new Set(['damage', 'max_damage', 'max_stack_size'])

// 視聴者に送るアイテム 1 つ: 安全な部品だけを残す（見た目の附呪の光などは消えるが、切れない）
export function safeSlot (notch) {
  if (!notch || !Array.isArray(notch.components)) return notch
  const components = notch.components.filter((c) => SAFE_COMPONENTS.has(c.type))
  return { ...notch, addedComponentCount: components.length, components, removedComponentCount: 0, removeComponents: [] }
}

// プレイヤーの持ち物（ウィンドウ 0）の今の中身（視聴者に送る window_items）
export function inventoryPacket (bot, Item) {
  return {
    windowId: 0,
    stateId: 0,
    items: bot.inventory.slots.map((i) => safeSlot(Item.toNotch(i ?? null))),
    carriedItem: safeSlot(Item.toNotch(null))
  }
}

// サーバーは、ボット自身が決めたことを送り返さない: 持ち替え（held_item_slot）と、クリックでの持ち物の
// 移動（window_click: 装備する、クラフトの結果を取る、チェストとのやりとり）、ボートの動き（vehicle_move）。視聴者の手に持つ物と
// 持ち物がサーバーとずれていた。ボットの持ち替えはそのまま、持ち物はクリックの後に今の中身を送る
export function relayOwnState (bot, viewers, Item, { delayMs = INVENTORY_SYNC_MS } = {}) {
  const client = bot._client
  const write = client.write.bind(client)
  let timer = null
  const syncInventory = () => {
    timer = null
    if (!bot.inventory || !viewers.size) return
    const pkt = inventoryPacket(bot, Item())
    for (const v of viewers) v.write('window_items', pkt)
  }
  client.write = (name, params) => {
    write(name, params)
    if (name === 'held_item_slot') for (const v of viewers) v.write('held_item_slot', { slot: params.slotId })
    // ボートの位置は乗っている側が決めて送る（設計書 37）: 視聴者のボートも同じ位置へ
    if (name === 'vehicle_move') for (const v of viewers) v.write('vehicle_move', { x: params.x, y: params.y, z: params.z, yaw: params.yaw, pitch: params.pitch })
    // Mineflayer はクリックの結果を自分の持ち物に先に反映する。少し待ってまとめて送る
    if (name === 'window_click' && !timer) timer = setTimeout(syncInventory, delayMs)
  }
}

// 装備の表示（set_equipment）に部品（附呪、名前、NBT など）つきのアイテムがあると、視聴者が
// 「74 bytes extra whilst reading packet set_equipment」で切れた。数だけの部品（damage など）に絞って
// 書き直しても、また「6 bytes extra」で切れた（2026-09-30 16:42）。装備の見た目に部品は要らないので、
// 部品のある装備は部品を全部外して書き直して送る（部品がなければそのまま中継）。持ち物（safeSlot）とは別
export function sanitizedEquipment (data) {
  const risky = (data.equipments ?? []).some((e) => (e.item?.addedComponentCount ?? 0) > 0 || (e.item?.removedComponentCount ?? 0) > 0)
  if (!risky) return null
  const bare = (item) => (item && Array.isArray(item.components)
    ? { ...item, addedComponentCount: 0, components: [], removedComponentCount: 0, removeComponents: [] }
    : item)
  return { entityId: data.entityId, equipments: data.equipments.map((e) => ({ slot: e.slot, item: bare(e.item) })) }
}

// 視聴者が切れたときの手がかり: 最近中継した装備のパケット（どのアイテムか、書き直したか、先頭のバイト列）
const RECENT_EQUIPMENT = 6
const summarizeEquipment = (data, path, buffer) => ({
  at: new Date().toISOString().slice(11, 19),
  entity: data.entityId,
  path,
  items: (data.equipments ?? []).map((e) => `${e.slot}:${e.item?.itemId ?? '-'}x${e.item?.itemCount ?? 0}[${(e.item?.components ?? []).map((c) => c.type).join(',')}]`),
  hex: buffer.subarray(0, 48).toString('hex')
})

export function startMirror (bot, { port = MIRROR_PORT, log = console.log } = {}) {
  const rec = new WorldRecorder()
  const viewers = new Set()
  let equipmentSerializer = null
  const recentEquipment = []
  const windows = relayWindows(bot, viewers, () => itemClass(bot))
  const encodeEquipment = (params) => {
    equipmentSerializer ??= mc.createSerializer({ state: 'play', isServer: true, version: VERSION })
    return equipmentSerializer.createPacketBuffer({ name: 'entity_equipment', params })
  }

  bot._client.on('packet', (data, meta, _buffer, fullBuffer) => {
    let buffer = fullBuffer
    if (meta.state === 'play' && meta.name === 'entity_equipment') {
      const safe = sanitizedEquipment(data)
      if (safe) {
        try {
          buffer = encodeEquipment(safe)
        } catch (e) {
          return // 書き直せなければ送らない（装備の見た目だけ）
        }
      }
      recentEquipment.push(summarizeEquipment(data, safe ? 'rewritten' : 'raw', buffer))
      if (recentEquipment.length > RECENT_EQUIPMENT) recentEquipment.shift()
    }
    rec.record(data, meta, buffer)
    if (meta.state !== 'play' || NOT_RELAYED.has(meta.name)) return
    if (windows.fromServer(meta.name, data, buffer)) return
    for (const v of viewers) v.writeRaw(buffer)
  })

  relayOwnState(bot, viewers, () => itemClass(bot))

  // registryCodec: {} -> nmp は自前の registry_data を書かない。代わりに、記録したボットの
  // 設定のパケット（known packs、registry data、tags、feature flags）を再生する。
  const server = mc.createServer({
    'online-mode': false, port, host: '127.0.0.1', version: VERSION,
    registryCodec: {}, enforceSecureProfile: false, motd: `${BOT_NAME} の視点（POV ミラー）`, maxPlayers: 4
  })

  server.on('login', (viewer) => {
    // nmp 自身のハンドラより先に動く。nmp のハンドラはすぐに finish_configuration を書いてしまう。
    viewer.prependOnceListener('login_acknowledged', () => {
      viewer.state = mc.states.CONFIGURATION
      for (const buf of rec.configPackets) viewer.writeRaw(buf)
    })
  })

  server.on('playerJoin', (viewer) => {
    if (!rec.login) { viewer.end('ボットがまだワールドに入っていません'); return }
    log(`[mirror] 視聴者 ${viewer.username} が参加した`)
    viewer.write('login', { ...rec.login.data, maxPlayers: 4 })
    replayWorld(viewer, rec, bot)
    windows.replay(viewer)
    viewers.add(viewer)
    viewer.on('end', () => {
      viewers.delete(viewer)
      log(`[mirror] 視聴者 ${viewer.username} が退出した`)
      // 装備のパケットで切れたとき（クライアントのエラーは、ここでは「退出した」としか分からない）の手がかり
      if (recentEquipment.length) log(`[mirror] 直近の装備パケット: ${JSON.stringify(recentEquipment)}`)
    })
    viewer.on('error', (e) => log(`[mirror] 視聴者のエラー: ${e.message}`))
  })

  let teleportId = 2
  let camera = null
  const timer = setInterval(() => {
    if (!bot.entity || viewers.size === 0) return
    camera ??= new Camera(bot)
    const pkt = camera.frame(teleportId++)
    // ボートに乗っている間の位置はボートが決める（vehicle_move を中継）: 向きだけ送る
    if (bot.vehicle) for (const v of viewers) v.write('player_rotation', { yaw: pkt.yaw, pitch: pkt.pitch })
    else for (const v of viewers) v.write('position', pkt)
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
  return { server, viewers, recorder: rec, close: () => { clearInterval(timer); windows.dispose(); server.close() } }
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
