// 視界で作る世界の地図（docs/design/59_world_map.md）。
//
// ボットの目から見える範囲（Minecraft の標準の視野）を、遮るものがあれば止めながら 1 ブロックずつ
// たどり、通ったセルを（空気も）座標つきで覚える。読み込み済みのチャンクを壁越しに調べる
// bot.findBlocks と違い、「見た」ものだけを知る。チャンクが読み込まれなくなっても、遠くへ行っても残る。
//
// セルの値は ブロックの id + 1（0 は未知、1 は空気）。ブロックの状態（向き、成長）は持たない。
// 断面（16x16x16）ごとに、種類と最後に見た時刻（ワールドの分）の配列を持つ。

import { promises as fs } from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'
import { promisify } from 'node:util'
import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg
const gzip = promisify(zlib.gzip)
const gunzip = promisify(zlib.gunzip)

export const UNKNOWN = 0
export const AIR = 1 // air は id 0
export const RANGE = 20 // 見える距離（ブロック）
export const FOV_VERTICAL = 70 // Minecraft の標準（Normal）。Java 版の FOV は垂直方向
export const ASPECT = 16 / 9 // 配信の POV クライアントの画面比
export const RAY_STEP = 2 // レイの角度の刻み（度）。20 m 先の 1 ブロックは約 2.9°
export const TICKS_PER_MINUTE = 1200
export const FORMAT = 1
// 見た時刻だけが変わった地域を書く間隔
export const TIME_SAVE_MS = 5 * 60 * 1000
const SECTION_CELLS = 4096
const MINUTE_MASK = 0xffff
const REGION_SECTIONS = 32 // 保存の単位: 32x32 断面（チャンク）

export const minuteOf = (ticks) => Math.floor(Number(ticks) / TICKS_PER_MINUTE) & MINUTE_MASK
// 最後に見てから何分か（16 ビットで一周する）
export const minutesSince = (now, seen) => (now - seen) & MINUTE_MASK

const cellIndex = (x, y, z) => ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
// 断面の座標を 1 つの数にする（文字列にすると、1 セルごとの引き当てが遅い）
const sectionKey = (sx, sy, sz) => ((sx + 0x100000) * 0x200000 + (sz + 0x100000)) * 64 + (sy + 16)

class Section {
  constructor (sx, sy, sz) {
    this.sx = sx
    this.sy = sy
    this.sz = sz
    this.ids = new Uint16Array(SECTION_CELLS)
    this.seen = new Uint16Array(SECTION_CELLS)
    this.counts = new Map() // 種類 -> この断面の数（探すとき、無い断面を飛ばす）
  }
}

export const SET_SAME = 0
export const SET_NEW = 1
export const SET_CHANGED = 2

export class WorldMap {
  constructor () {
    this.sections = new Map()
    this.dirty = new Set() // 書き直す領域（ブロックが変わった）
    this.dirtyTime = new Set() // 見た時刻だけ変わった領域（まとめて、間をあけて書く）
    this.timeSavedAt = 0
    this.total = { added: 0, changed: 0 }
    this._lastKey = NaN
    this._last = null
  }

  _section (x, y, z, create) {
    const sx = x >> 4
    const sy = y >> 4
    const sz = z >> 4
    const key = sectionKey(sx, sy, sz)
    if (key === this._lastKey) return this._last
    let s = this.sections.get(key)
    if (!s && create) {
      s = new Section(sx, sy, sz)
      this.sections.set(key, s)
    }
    if (s) {
      this._lastKey = key
      this._last = s
    }
    return s ?? null
  }

  // { cell, minute } か null（未知）
  get (x, y, z) {
    const s = this._section(x, y, z, false)
    if (!s) return null
    const i = cellIndex(x, y, z)
    const cell = s.ids[i]
    return cell === UNKNOWN ? null : { cell, minute: s.seen[i] }
  }

  cellAt (x, y, z) {
    const s = this._section(x, y, z, false)
    return s ? s.ids[cellIndex(x, y, z)] : UNKNOWN
  }

  // 見たものを書く。前と同じ / 初めて / 前と違う（壊された、置かれた、水が流れた）を返す
  set (x, y, z, cell, minute) {
    const s = this._section(x, y, z, true)
    const i = cellIndex(x, y, z)
    const old = s.ids[i]
    const timeChanged = s.seen[i] !== minute
    s.seen[i] = minute
    if (old === cell) {
      if (timeChanged) this.dirtyTime.add(regionKey(s.sx, s.sz))
      return SET_SAME
    }
    s.ids[i] = cell
    if (old !== UNKNOWN) {
      const n = (s.counts.get(old) ?? 1) - 1
      if (n > 0) s.counts.set(old, n)
      else s.counts.delete(old)
    }
    s.counts.set(cell, (s.counts.get(cell) ?? 0) + 1)
    this.dirty.add(regionKey(s.sx, s.sz))
    if (old === UNKNOWN) { this.total.added++; return SET_NEW }
    this.total.changed++
    return SET_CHANGED
  }

  get sectionCount () { return this.sections.size }

  // cells（Set）のどれかである、空気に接する（exposed）セルなど。from から radius 以内、近い順
  find (cells, from, radius, { limit = 64, exposed = false } = {}) {
    const out = []
    forSectionsNear(this, from, radius, (s) => {
      let has = false
      for (const c of cells) if (s.counts.has(c)) { has = true; break }
      if (!has) return
      const bx = s.sx << 4
      const by = s.sy << 4
      const bz = s.sz << 4
      for (let i = 0; i < SECTION_CELLS; i++) {
        const c = s.ids[i]
        if (c === UNKNOWN || !cells.has(c)) continue
        const x = bx + (i & 15)
        const z = bz + ((i >> 4) & 15)
        const y = by + (i >> 8)
        const dx = x + 0.5 - from.x
        const dy = y + 0.5 - from.y
        const dz = z + 0.5 - from.z
        const d2 = dx * dx + dy * dy + dz * dz
        if (d2 > radius * radius) continue
        if (exposed && !this.exposed(x, y, z)) continue
        out.push({ x, y, z, cell: c, minute: s.seen[i], d2 })
      }
    })
    out.sort((a, b) => a.d2 - b.d2)
    return out.slice(0, limit)
  }

  // 空気だと見たセルに接している（見えていない側は数えない）
  exposed (x, y, z) {
    return this.cellAt(x + 1, y, z) === AIR || this.cellAt(x - 1, y, z) === AIR || this.cellAt(x, y + 1, z) === AIR ||
      this.cellAt(x, y - 1, z) === AIR || this.cellAt(x, y, z + 1) === AIR || this.cellAt(x, y, z - 1) === AIR
  }

  // 16x16 の区域（x, z）ごとに、cells のうち from に一番近い、空気に接するセル: Map(cell -> Map(区域 -> {x, y, z, count, minute, d2}))
  // cells が null なら、空気以外のすべて
  regionReps (cells, from, radius, { exposed = true, skip = () => false, exclude = null } = {}) {
    const reps = new Map()
    forSectionsNear(this, from, radius, (s) => {
      if (cells && ![...cells].some((c) => s.counts.has(c))) return
      const bx = s.sx << 4
      const by = s.sy << 4
      const bz = s.sz << 4
      for (let i = 0; i < SECTION_CELLS; i++) {
        const c = s.ids[i]
        if (c <= AIR || (cells && !cells.has(c)) || exclude?.has(c)) continue
        const x = bx + (i & 15)
        const z = bz + ((i >> 4) & 15)
        const y = by + (i >> 8)
        const dx = x + 0.5 - from.x
        const dz = z + 0.5 - from.z
        const d2 = dx * dx + dz * dz
        if (d2 > radius * radius) continue
        if (exposed && !this.exposed(x, y, z)) continue
        let regions = reps.get(c)
        if (!regions) { regions = new Map(); reps.set(c, regions) }
        const rk = `${x >> 4},${z >> 4}`
        const r = regions.get(rk)
        // skip は代表になるときだけ確かめる（木の判定などが重い）。数は、確かめていない分も含む概数
        if (r && d2 >= r.d2) { r.count++; continue }
        if (skip(x, y, z, c)) continue
        if (!r) regions.set(rk, { region: rk, x, y, z, count: 1, minute: s.seen[i], d2 })
        else {
          r.count++
          r.x = x; r.y = y; r.z = z; r.d2 = d2; r.minute = s.seen[i]
        }
      }
    })
    return reps
  }

  // ---- 保存 ----

  // 同時に 2 回走らせない（タイマーと停止が重なると、同じ .tmp に書き合う）。走っている間の変更は次の回に回す
  save (dir, version, { final = false } = {}) {
    const run = (this._saving ?? Promise.resolve()).catch(() => {}).then(() => this._save(dir, version, final))
    this._saving = run
    return run
  }

  // 立っている地域は、見た時刻が変わるだけで 30 秒ごとに「変わった」ことになり、地域全体（探索が進むと
  // 十数 MB）を作り直して圧縮していた（2026-10-01）。ブロックが変わった地域はすぐ、時刻だけの地域は
  // TIME_SAVE_MS ごと（と停止のとき）に書く
  async _save (dir, version, final = false) {
    const timeDue = final || Date.now() - this.timeSavedAt >= TIME_SAVE_MS
    if (!this.dirty.size && !(timeDue && this.dirtyTime.size)) return 0
    const regions = [...new Set([...this.dirty, ...(timeDue ? this.dirtyTime : [])])]
    this.dirty = new Set()
    if (timeDue) { this.dirtyTime = new Set(); this.timeSavedAt = Date.now() }
    await fs.mkdir(dir, { recursive: true })
    await fs.writeFile(path.join(dir, 'meta.json'), JSON.stringify({ format: FORMAT, version }))
    const byRegion = new Map(regions.map((r) => [r, []]))
    for (const s of this.sections.values()) byRegion.get(regionKey(s.sx, s.sz))?.push(s)
    for (const [r, sections] of byRegion) {
      const file = path.join(dir, `r.${r}.bin.gz`)
      const buf = Buffer.alloc(4 + sections.length * (12 + SECTION_CELLS * 4))
      buf.writeUInt32LE(sections.length, 0)
      let o = 4
      for (const s of sections) {
        buf.writeInt32LE(s.sx, o); buf.writeInt32LE(s.sy, o + 4); buf.writeInt32LE(s.sz, o + 8)
        o += 12
        Buffer.from(s.ids.buffer, s.ids.byteOffset, s.ids.byteLength).copy(buf, o)
        o += SECTION_CELLS * 2
        Buffer.from(s.seen.buffer, s.seen.byteOffset, s.seen.byteLength).copy(buf, o)
        o += SECTION_CELLS * 2
      }
      const tmp = `${file}.tmp`
      await fs.writeFile(tmp, await gzip(buf))
      await fs.rename(tmp, file)
    }
    return regions.length
  }

  // dir の地図を読む。バージョンか書式が違えば読まない（ブロックの id はバージョンで変わる）
  static async load (dir, version) {
    const map = new WorldMap()
    let meta
    try {
      meta = JSON.parse(await fs.readFile(path.join(dir, 'meta.json'), 'utf8'))
    } catch {
      return map
    }
    if (meta.format !== FORMAT || meta.version !== version) return map
    for (const name of await fs.readdir(dir)) {
      if (!/^r\.-?\d+,-?\d+\.bin\.gz$/.test(name)) continue
      const buf = await gunzip(await fs.readFile(path.join(dir, name)))
      const n = buf.readUInt32LE(0)
      let o = 4
      for (let k = 0; k < n; k++) {
        const s = new Section(buf.readInt32LE(o), buf.readInt32LE(o + 4), buf.readInt32LE(o + 8))
        o += 12
        s.ids = new Uint16Array(buf.buffer.slice(buf.byteOffset + o, buf.byteOffset + o + SECTION_CELLS * 2))
        o += SECTION_CELLS * 2
        s.seen = new Uint16Array(buf.buffer.slice(buf.byteOffset + o, buf.byteOffset + o + SECTION_CELLS * 2))
        o += SECTION_CELLS * 2
        for (let i = 0; i < SECTION_CELLS; i++) if (s.ids[i] !== UNKNOWN) s.counts.set(s.ids[i], (s.counts.get(s.ids[i]) ?? 0) + 1)
        map.sections.set(sectionKey(s.sx, s.sy, s.sz), s)
      }
    }
    return map
  }
}

const regionKey = (sx, sz) => `${Math.floor(sx / REGION_SECTIONS)},${Math.floor(sz / REGION_SECTIONS)}`

function forSectionsNear (map, from, radius, visit) {
  const x0 = Math.floor((from.x - radius) / 16)
  const x1 = Math.floor((from.x + radius) / 16)
  const z0 = Math.floor((from.z - radius) / 16)
  const z1 = Math.floor((from.z + radius) / 16)
  const y0 = Math.floor((from.y - radius) / 16)
  const y1 = Math.floor((from.y + radius) / 16)
  const many = (x1 - x0 + 1) * (z1 - z0 + 1) * (y1 - y0 + 1) > map.sections.size
  if (many) {
    for (const s of map.sections.values()) {
      if (s.sx >= x0 && s.sx <= x1 && s.sz >= z0 && s.sz <= z1 && s.sy >= y0 && s.sy <= y1) visit(s)
    }
    return
  }
  for (let sx = x0; sx <= x1; sx++) {
    for (let sz = z0; sz <= z1; sz++) {
      for (let sy = y0; sy <= y1; sy++) {
        const s = map.sections.get(sectionKey(sx, sy, sz))
        if (s) visit(s)
      }
    }
  }
}

// ---- ブロックの種類の分類 ----

const SEE_THROUGH = /_fence$|_fence_gate$|_wall$/ // 当たり判定はあるが、隙間から向こうが見える

// registry から、ブロックの状態 id -> セルの値と、視線を止めるかの表を作る。視線を止めるのは、
// 透けない固いブロックと葉（葉は minecraft-data では透明だが、向こうは見えない）。ガラス、水、草、
// 松明、フェンスは止めない
export function makeClassifier (registry) {
  let maxState = 0
  for (const b of registry.blocksArray) maxState = Math.max(maxState, b.maxStateId)
  const cell = new Uint16Array(maxState + 1)
  const opaque = new Uint8Array(maxState + 1)
  const names = []
  const solid = []
  for (const b of registry.blocksArray) {
    const air = b.name === 'air' || b.name === 'cave_air' || b.name === 'void_air'
    const value = air ? AIR : b.id + 1
    names[value] = air ? 'air' : b.name
    solid[value] = !air && b.boundingBox === 'block'
    const stops = !air && b.boundingBox === 'block' && !SEE_THROUGH.test(b.name) && (!b.transparent || b.name.endsWith('_leaves'))
    for (let s = b.minStateId; s <= b.maxStateId; s++) {
      cell[s] = value
      opaque[s] = stops ? 1 : 0
    }
  }
  const byName = new Map(Object.entries(names).map(([v, n]) => [n, Number(v)]))
  return {
    cell,
    opaque,
    nameOf: (c) => names[c] ?? null,
    isSolid: (c) => solid[c] === true,
    cellOf: (name) => byName.get(name) ?? null,
    cellsOf: (pattern) => new Set([...byName].filter(([n]) => pattern.test(n)).map(([, v]) => v))
  }
}

// ボットの世界から、ブロックの状態 id を読む関数 (x, y, z) => id（読み込まれていなければ -1）。
// bot.blockAt は 1 回ごとに Block を作って遅いので、チャンクの列を直接読む
export function botReader (bot) {
  const scratch = new Vec3(0, 0, 0)
  let cx = NaN
  let cz = NaN
  let column = null
  return (x, y, z) => {
    const ncx = x >> 4
    const ncz = z >> 4
    if (ncx !== cx || ncz !== cz) {
      column = bot.world.getColumn(ncx, ncz) ?? null
      cx = ncx
      cz = ncz
    }
    if (!column) return -1
    const minY = column.minY ?? 0
    if (y < minY || y >= minY + (column.worldHeight ?? 384)) return -1
    scratch.x = x & 15
    scratch.y = y
    scratch.z = z & 15
    return column.getBlockStateId(scratch)
  }
}

// ---- 視野 ----

// 視野の中を約 step 度刻みで並べた、カメラから見た向き [右, 上, 前] の並び（Float64Array の 3 つ組）。
// 画面は長方形（垂直 fov、画面比 aspect）なので、角の外は入れない
export function rayGrid ({ fov = FOV_VERTICAL, aspect = ASPECT, step = RAY_STEP } = {}) {
  const tv = Math.tan((fov * Math.PI) / 360)
  const th = tv * aspect
  const maxAz = Math.floor((Math.atan(th) * 180) / Math.PI / step)
  const maxEl = Math.floor((Math.atan(tv) * 180) / Math.PI / step)
  const out = []
  for (let a = -maxAz; a <= maxAz; a++) {
    for (let e = -maxEl; e <= maxEl; e++) {
      const az = (a * step * Math.PI) / 180
      const el = (e * step * Math.PI) / 180
      const x = Math.sin(az) * Math.cos(el)
      const y = Math.sin(el)
      const z = Math.cos(az) * Math.cos(el)
      if (Math.abs(x / z) > th || Math.abs(y / z) > tv) continue
      out.push(x, y, z)
    }
  }
  return Float64Array.from(out)
}

// mineflayer の向き（yaw, pitch）の前・右・上（ray_trace.js の getViewDirection と同じ向き）
export function cameraBasis (yaw, pitch) {
  const sy = Math.sin(yaw)
  const cy = Math.cos(yaw)
  const sp = Math.sin(pitch)
  const cp = Math.cos(pitch)
  return {
    forward: [-sy * cp, sp, -cy * cp],
    right: [cy, 0, -sy],
    up: [sy * sp, cp, cy * sp]
  }
}

// 1 本のレイ（Amanatides & Woo）: 目から向き d へ、range まで。通ったセルを書き、視線を止めるものに
// 当たるか、読み込まれていないセルに届いたら終わる
function castRay (map, read, cls, ex, ey, ez, dx, dy, dz, range, minute, out) {
  let x = Math.floor(ex)
  let y = Math.floor(ey)
  let z = Math.floor(ez)
  const sx = dx > 0 ? 1 : -1
  const sy = dy > 0 ? 1 : -1
  const sz = dz > 0 ? 1 : -1
  const tdx = dx !== 0 ? Math.abs(1 / dx) : Infinity
  const tdy = dy !== 0 ? Math.abs(1 / dy) : Infinity
  const tdz = dz !== 0 ? Math.abs(1 / dz) : Infinity
  let tx = dx > 0 ? (x + 1 - ex) * tdx : dx < 0 ? (ex - x) * tdx : Infinity
  let ty = dy > 0 ? (y + 1 - ey) * tdy : dy < 0 ? (ey - y) * tdy : Infinity
  let tz = dz > 0 ? (z + 1 - ez) * tdz : dz < 0 ? (ez - z) * tdz : Infinity
  for (;;) {
    const state = read(x, y, z)
    if (state < 0) return
    const r = map.set(x, y, z, cls.cell[state], minute)
    out.cells++
    if (r === SET_NEW) out.added++
    else if (r === SET_CHANGED) out.changed++
    if (cls.opaque[state]) return
    if (tx < ty && tx < tz) {
      if (tx > range) return
      x += sx; tx += tdx
    } else if (ty < tz) {
      if (ty > range) return
      y += sy; ty += tdy
    } else {
      if (tz > range) return
      z += sz; tz += tdz
    }
  }
}

const RAYS_PER_YIELD = 48

// 1 回分のサンプリング。generator で、RAYS_PER_YIELD 本ごとに yield する（呼ぶ側が時間で区切る）。
// 結果は { rays, cells, added, changed }
export function * sampleSlices (map, read, cls, eye, yaw, pitch, rays, { range = RANGE, minute = 0 } = {}) {
  const { forward, right, up } = cameraBasis(yaw, pitch)
  const out = { rays: 0, cells: 0, added: 0, changed: 0 }
  for (let i = 0; i < rays.length; i += 3) {
    const r = rays[i]
    const u = rays[i + 1]
    const f = rays[i + 2]
    castRay(map, read, cls, eye.x, eye.y, eye.z,
      f * forward[0] + r * right[0] + u * up[0],
      f * forward[1] + r * right[1] + u * up[1],
      f * forward[2] + r * right[2] + u * up[2],
      range, minute, out)
    out.rays++
    if (out.rays % RAYS_PER_YIELD === 0) yield out
  }
  return out
}

// 全部を一度に（テスト、計測）
export function sampleOnce (...args) {
  const gen = sampleSlices(...args)
  for (;;) {
    const r = gen.next()
    if (r.done) return r.value
  }
}

// 自分が掘った・置いたセルは、見ていなくても分かるので、その場で読み直して地図に反映する
export function reflectLive (map, read, cls, pos, minute) {
  const state = read(pos.x, pos.y, pos.z)
  if (state < 0) return
  map.set(pos.x, pos.y, pos.z, cls.cell[state], minute)
}

// ---- 定期的なサンプリング ----

export const SAMPLE_INTERVAL_MS = 500
export const MOVED_BLOCKS = 1.5
export const TURNED_RADIANS = (10 * Math.PI) / 180
export const REFRESH_MS = 5000
export const SLICE_MS = 5 // 1 回に続けて計算する時間。ブリッジは 1 スレッドで、長いと物理の tick が遅れる

const turned = (a, b) => {
  let d = Math.abs(a - b) % (2 * Math.PI)
  if (d > Math.PI) d = 2 * Math.PI - d
  return d
}

// bot の目から定期的に見る。動いた・向いた・しばらく経ったときだけ。前の回が終わっていなければ飛ばす
export function createSampler (bot, map, cls, { rays = rayGrid(), range = RANGE, reader = botReader, clock = () => performance.now(), yieldTurn = () => new Promise((resolve) => setImmediate(resolve)) } = {}) {
  const stats = { samples: 0, rays: 0, cells: 0, added: 0, changed: 0, ms: 0, maxMs: 0, skipped: 0 }
  let last = null
  let running = false
  let timer = null

  const eyeOf = () => {
    const p = bot.entity.position
    return { x: p.x, y: p.y + (bot.entity.eyeHeight ?? 1.62), z: p.z }
  }

  const due = (eye, yaw, pitch) => {
    if (!last) return true
    const moved = Math.hypot(eye.x - last.eye.x, eye.y - last.eye.y, eye.z - last.eye.z)
    return moved >= MOVED_BLOCKS || turned(yaw, last.yaw) >= TURNED_RADIANS || Math.abs(pitch - last.pitch) >= TURNED_RADIANS ||
      Date.now() - last.at >= REFRESH_MS
  }

  async function sampleNow () {
    if (running || !bot.entity) return false
    running = true
    try {
      const eye = eyeOf()
      const { yaw, pitch } = bot.entity
      const started = clock()
      const gen = sampleSlices(map, reader(bot), cls, eye, yaw, pitch, rays, { range, minute: minuteOf(bot.time?.age ?? 0) })
      let sliceStart = started
      let result
      for (;;) {
        const r = gen.next()
        if (r.done) { result = r.value; break }
        if (clock() - sliceStart >= SLICE_MS) {
          await yieldTurn()
          sliceStart = clock()
        }
      }
      const ms = clock() - started
      last = { eye, yaw, pitch, at: Date.now() }
      stats.samples++
      stats.rays += result.rays
      stats.cells += result.cells
      stats.added += result.added
      stats.changed += result.changed
      stats.ms += ms
      stats.maxMs = Math.max(stats.maxMs, ms)
      return true
    } finally {
      running = false
    }
  }

  async function tick () {
    if (running || !bot.entity) { if (running) stats.skipped++; return }
    const eye = eyeOf()
    if (!due(eye, bot.entity.yaw, bot.entity.pitch)) return
    try {
      await sampleNow()
    } catch (e) {
      console.log(`[worldmap] サンプリングに失敗: ${e.message}`)
    }
  }

  return {
    stats,
    sampleNow,
    tick,
    start (intervalMs = SAMPLE_INTERVAL_MS) { if (!timer) timer = setInterval(tick, intervalMs) },
    stop () { if (timer) { clearInterval(timer); timer = null } }
  }
}
