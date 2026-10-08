// 施設の登録（設計書 47 §5）: 家、名前付きの建物、畑、植林場、採掘場…。施設の座標を持つのはここだけ
// （Python の街の理想図は区画の範囲だけ）。state.facilities に保存する。
//
// 今ある物は施設として取り込む（adoptExisting）: 家、state.builds、家のまわりの耕地（home_farm）、
// 植えた苗木のかたまり（tree_farm_N）。取り込まないと、理想図が今の畑の上に別の区画を描き、畑を忘れる。
import vec3Pkg from 'vec3'
import { round, bearing, isLog } from './observe.mjs'
import { buildBox } from './builds.mjs'
import { refusalAt } from './refusals.mjs'
import { inHouse } from './home.mjs'
import { farmLevel } from './leveling.mjs'
import { CROPS, cropByBlock, cropOf, cropStatus, ageOf, isDry, isRipe, SAPLING, PLANT_RING, TILLABLE, CLEARABLE, SAPLING_GROUND, TREE_GAP, farmRefusal, nearName, surfaceAt } from './farming.mjs'

const { Vec3 } = vec3Pkg

export const FACILITY_KINDS = ['home', 'build', 'farm', 'tree_farm', 'mine', 'pen', 'trap']
const GROUP_GAP = 8 // 植えた苗木がこの距離より離れていれば、別の植林場
const FARM_SEARCH = PLANT_RING.max + 8 // 家から、耕地を探す範囲
export const HOME_FARM = 'home_farm'

const xyz = (v) => ({ x: v.x, y: v.y, z: v.z })
const boxOf = (points, pad = 0) => ({
  min: { x: Math.min(...points.map((p) => p.x)) - pad, y: Math.min(...points.map((p) => p.y)), z: Math.min(...points.map((p) => p.z)) - pad },
  max: { x: Math.max(...points.map((p) => p.x)) + pad, y: Math.max(...points.map((p) => p.y)), z: Math.max(...points.map((p) => p.z)) + pad }
})
const inBox = (box, p) => p.x >= box.min.x && p.x <= box.max.x && p.z >= box.min.z && p.z <= box.max.z
const center = (box) => new Vec3((box.min.x + box.max.x) / 2, box.min.y, (box.min.z + box.max.z) / 2)

// 植えた苗木を、近いものどうしでかたまりにする（1 つのかたまり = 1 つの植林場）
export function plantingGroups (plantings, gap = GROUP_GAP) {
  const groups = []
  for (const p of plantings) {
    const near = groups.filter((g) => g.some((q) => Math.abs(q.x - p.x) <= gap && Math.abs(q.z - p.z) <= gap))
    const merged = near.flat().concat([p])
    for (const g of near) groups.splice(groups.indexOf(g), 1)
    groups.push(merged)
  }
  return groups
}

// 家のまわりの耕地（見えていなければ null: 前の範囲のまま）
function farmlandAroundHome (bot, state) {
  const home = state.home
  const id = bot.registry?.blocksByName?.farmland?.id
  if (!home || id == null || !bot.blockAt(home.inside)) return null
  return bot.findBlocks({ point: home.inside, matching: id, maxDistance: FARM_SEARCH, count: 512 })
}

// 今ある物を施設として取り込む・広げる。変わったら真（保存する）
export function adoptExisting (bot, state) {
  state.facilities ??= {}
  const f = state.facilities
  const before = JSON.stringify(f)
  // 家は今の家（引っ越したら取り込み直す: 旧家のままだと、LLM が旧家の座標へ向かう）
  if (state.home) {
    const h = state.home
    const home = { name: 'home', kind: 'home', box: { min: xyz(h.min.offset(-1, 0, -1)), max: xyz(h.max.offset(1, 0, 1)) }, entrance: xyz(h.outside), status: 'done', adopted: true }
    if (!f.home || JSON.stringify(f.home.box) !== JSON.stringify(home.box)) f.home = home
  }
  for (const [name, plan] of Object.entries(state.builds ?? {})) {
    if (f[name] || !plan.origin) continue
    const box = buildBox(plan)
    f[name] = { name, kind: 'build', box: { min: xyz(box.min), max: xyz(box.max) }, status: 'building', adopted: true, purpose: plan.design?.purpose ?? '' }
  }
  // 設計した畑（取り込みでない施設）の耕地は、家の畑に入れない（2 度数えない）
  const designed = Object.values(f).filter((x) => !x.adopted && x.kind === 'farm')
  const farmland = farmlandAroundHome(bot, state)?.filter((p) => !designed.some((d) => inBox(d.box, p)))
  if (farmland?.length && (!f[HOME_FARM] || f[HOME_FARM].adopted)) {
    f[HOME_FARM] = { name: HOME_FARM, kind: 'farm', box: boxOf(farmland), status: 'done', adopted: true }
  }
  // 植えた苗木: どの植林場にも入っていないかたまりを、新しい植林場にする
  const trees = Object.values(f).filter((x) => x.kind === 'tree_farm')
  const loose = (state.plantings ?? []).filter((p) => !trees.some((t) => inBox(t.box, p)))
  for (const group of plantingGroups(loose)) {
    let n = 1
    while (f[`tree_farm_${n}`]) n++
    f[`tree_farm_${n}`] = { name: `tree_farm_${n}`, kind: 'tree_farm', box: boxOf(group, 1), status: 'done', adopted: true }
  }
  for (const fac of Object.values(f)) retireUnclimbableMine(fac)
  return JSON.stringify(f) !== before
}

// 部屋が階段の床を消す形の採掘場（geometry 1）は、使わない: 下りると部屋から階段へ上がれない。掘り尽くした扱いにして、
// 次は新しい形で別の入口に作らせる（階段の穴はそのまま残る）
export function retireUnclimbableMine (fac) {
  if (fac.kind !== 'mine' || !fac.layout?.cells || fac.layout.geometry === MINE_GEOMETRY || fac.layout.exhausted) return false
  const stairs = fac.layout.cells.filter((c) => c.part === 'stairs')
  const support = stairSupport(stairs, stairs.at(-1)?.y ?? 0)
  if (!fac.layout.cells.some((c) => c.part === 'room' && support.has(`${c.x},${c.z}`))) { fac.layout.geometry = MINE_GEOMETRY; return false }
  fac.layout.exhausted = true
  fac.layout.retired = 'the room undermined the last two steps of the stairs: from the bottom there is no way back up on foot'
  return true
}

// 施設の中身（今見えている分。見えていなければ null）
function contents (bot, state, fac, jaOf) {
  const loaded = bot.blockAt(center(fac.box))
  if (!loaded) return null
  if (fac.layout && fac.kind === 'mine') {
    const s = mineStatus(bot, fac)
    const bottom = fac.layout.cells.filter((c) => c.part === 'stairs').at(-1)
    return {
      bottom_y: bottom?.y ?? null,
      tunnels: fac.layout.tunnels,
      cells: s.total,
      done: s.done,
      ...(s.unseen ? { out_of_view: s.unseen } : {}),
      ...(fac.layout.stopped ? { stopped: fac.layout.stopped } : {})
    }
  }
  if (fac.layout) {
    // 設計した施設: 設計のマスのうち済んだもの、これからのもの（設計書 47 §7）
    const s = layoutStatus(bot, fac)
    // 畑は、湿っていない耕地の数も（知らせるだけ: 水を引くかどうかは判断に任せる）
    const work = fac.kind === 'farm'
      ? { to_till: s.till.length, to_sow: s.sow.length, to_water: s.water.length, dry: fac.layout.cells.filter((c) => isDry(bot.blockAt(new Vec3(c.x, c.y, c.z)))).length }
      : { to_plant: s.plant.length }
    const what = fac.kind === 'farm' ? { crop: fac.layout.crop, crop_ja: jaOf(fac.layout.crop) } : { sapling: fac.layout.sapling, spacing: fac.layout.spacing }
    return { ...what, cells: s.total, done: s.done, ...work, ...(s.unseen ? { out_of_view: s.unseen } : {}) }
  }
  if (fac.kind === 'farm') {
    const crops = {}
    for (let x = fac.box.min.x; x <= fac.box.max.x; x++) {
      for (let z = fac.box.min.z; z <= fac.box.max.z; z++) {
        for (let y = fac.box.min.y; y <= fac.box.max.y + 1; y++) {
          const b = bot.blockAt(new Vec3(x, y, z))
          const crop = cropByBlock(b?.name)
          if (!crop || bot.blockAt(new Vec3(x, y - 1, z))?.name !== 'farmland') continue
          crops[crop] ??= { planted: 0, ripe: 0, dry: 0, ja: jaOf(crop) }
          crops[crop].planted++
          if (isRipe(b)) crops[crop].ripe++
          if (isDry(bot.blockAt(new Vec3(x, y - 1, z)))) crops[crop].dry++
        }
      }
    }
    return { crops }
  }
  if (fac.kind === 'tree_farm') {
    const out = { saplings: 0, trees: 0, empty: 0 }
    for (const p of (state.plantings ?? []).filter((q) => inBox(fac.box, q))) {
      const b = bot.blockAt(new Vec3(p.x, p.y, p.z))
      if (SAPLING.test(b?.name ?? '')) out.saplings++
      else if (isLog(b?.name ?? '')) out.trees++
      else out.empty++
    }
    return out
  }
  if (fac.kind === 'build') {
    const plan = state.builds?.[fac.name]
    if (!plan) return null
    const s = plan.status(bot)
    return { placed: s.placed, total: s.total, complete: s.complete }
  }
  return null
}

// GET /facilities: 一覧（近い順）と今の中身。jaOf(name) は作物の日本語名（日本語の検索語で引く）
export function facilitiesStatus (bot, state, jaOf = () => '') {
  const me = bot.entity.position
  return Object.values(state.facilities ?? {})
    .map((fac) => {
      const c = center(fac.box)
      const inside = fac.kind === 'build' ? contents(bot, state, fac, jaOf) : null
      const status = fac.kind === 'build' && inside?.complete ? 'done' : fac.status
      return {
        name: fac.name,
        kind: fac.kind,
        status,
        ...(fac.purpose ? { purpose: fac.purpose } : {}),
        ...(fac.zone ? { zone: fac.zone } : {}),
        box: fac.box,
        ...(fac.entrance ? { entrance: fac.entrance } : {}),
        center: { x: Math.round(c.x), y: c.y, z: Math.round(c.z) },
        distance_m: round(Math.hypot(c.x - me.x, c.z - me.z)),
        direction: bearing(me, c),
        contents: inside ?? contents(bot, state, fac, jaOf)
      }
    })
    .sort((a, b) => a.distance_m - b.distance_m)
}

// ---- 施設の設計（設計書 47 §7）: Python の設計（区画と大きさ）から、ブリッジが実際のマスを選ぶ ----

export const DESIGNABLE = ['farm', 'tree_farm', 'mine']
export const FACILITY_NAME = /^[a-z][a-z0-9_]{1,31}$/
export const FARM_SIDE = { min: 1, max: 16 }
const WATER_SPAN = 9 // 水 1 マスが湿らせる四角（縦横 9）
const HYDRATION = 4 // 水から耕地までの距離（縦横）
export const TREES = { min: 1, max: 32 }
export const SPACING = { min: TREE_GAP, max: 6 } // 苗木どうしの間（farming.mjs の TREE_GAP 以上: 植える規則と同じ）
const USABLE_SHARE = 0.6 // 畑の四角のうち耕せるマスがこれ未満なら、そこには作れない
// 畑のまわりの柵（設計書 75）: 畑の四角の 1 マス外側の輪に柵を立て、1 か所に柵のゲートを付ける。柵と壁は
// 立っていれば済み。固いブロック（木、石、崖）で塞がっている所も、柵は要らない（済み）
export const FENCE_ITEM = /_fence$/
export const FENCE_GATE_ITEM = /_fence_gate$/
const FENCE_CLOSED = /_(fence|wall)$|_fence_gate$/
const LOGS = /(_log|_sapling|_propagule)$/

// 断る理由。world は世界の状態（読み込まれていない）: 設計をやり直しても直らない
export class FacilityError extends Error {
  constructor (message, { world = false } = {}) {
    super(message)
    this.world = world
  }
}

const cellKey = (p) => `${p.x},${p.z}`

// 区画の範囲（複数）をまとめた範囲
function unionBox (boxes) {
  if (!Array.isArray(boxes) || !boxes.length) throw new FacilityError('boxes: give the zones as {min_x, min_z, max_x, max_z}')
  const n = (v, k) => {
    const x = Number(v?.[k])
    if (!Number.isInteger(x)) throw new FacilityError(`boxes: ${k} must be an integer`)
    return x
  }
  return {
    min_x: Math.min(...boxes.map((b) => n(b, 'min_x'))),
    min_z: Math.min(...boxes.map((b) => n(b, 'min_z'))),
    max_x: Math.max(...boxes.map((b) => n(b, 'max_x'))),
    max_z: Math.max(...boxes.map((b) => n(b, 'max_z')))
  }
}

// 施設に使える地面（範囲のすべての列）。ほかの施設・家・建物のそばは除く
function groundCells (bot, state, area, name) {
  const y0 = state.home ? state.home.inside.y : Math.floor(bot.entity.position.y)
  const others = Object.values(state.facilities ?? {}).filter((x) => x.name !== name && x.kind !== 'home')
  const out = []
  let unloaded = 0
  for (let x = area.min_x; x <= area.max_x; x++) {
    for (let z = area.min_z; z <= area.max_z; z++) {
      if (!bot.blockAt(new Vec3(x, y0, z))) { unloaded++; continue }
      const g = groundWithPlants(bot, x, z, y0)
      if (!g) continue
      if (farmRefusal(state, g.cell) || others.some((o) => inBox(o.box, g.cell))) continue
      out.push(g)
    }
  }
  const total = (area.max_x - area.min_x + 1) * (area.max_z - area.min_z + 1)
  if (unloaded > total / 2) throw new FacilityError('the area is not loaded (too far from the bot): go near it before designing', { world: true })
  return out
}

// 地面（固いブロック）と、その上のマス（空気か、どけられる草花）
function groundWithPlants (bot, x, z, y0) {
  const s = surfaceAt(bot, x, z, y0)
  if (s) return { ground: s.ground, cell: s.cell, plant: false }
  for (let y = y0 + 4; y >= y0 - 4; y--) {
    const b = bot.blockAt(new Vec3(x, y, z))
    if (!b) return null
    if (b.boundingBox !== 'block') continue
    const above = bot.blockAt(new Vec3(x, y + 1, z))
    const head = bot.blockAt(new Vec3(x, y + 2, z))
    if (above && CLEARABLE.test(above.name) && head?.boundingBox === 'empty') return { ground: b, cell: new Vec3(x, y + 1, z), plant: true }
    return null
  }
  return null
}

// 畑: 幅 x 奥行きの四角（向きは両方試す）のうち、耕せる地面が同じ高さ（±1）に一番多く並ぶ所。
// 四角の中に水のマスを 9 マスごとに 1 つ置く（水から縦横 4 マス以内の耕地が湿る）: { cells: 作物のマス, water: 水のマス }
function farmLayout (bot, cells, area, width, depth) {
  const byXZ = new Map(cells.filter((c) => TILLABLE.test(c.ground.name) || c.ground.name === 'farmland')
    .filter((c) => !nearName(bot, c.cell, LOGS, 1)).map((c) => [cellKey(c.cell), c]))
  let best = null
  for (const [w, d] of width === depth ? [[width, depth]] : [[width, depth], [depth, width]]) {
    for (let x0 = area.min_x; x0 + w - 1 <= area.max_x; x0++) {
      for (let z0 = area.min_z; z0 + d - 1 <= area.max_z; z0++) {
        const inside = []
        for (let x = x0; x < x0 + w; x++) for (let z = z0; z < z0 + d; z++) { const c = byXZ.get(`${x},${z}`); if (c) inside.push(c) }
        if (!inside.length) continue
        const ys = inside.map((c) => c.cell.y).sort((a, b) => a - b)
        const mid = ys[Math.floor(ys.length / 2)]
        const level = inside.filter((c) => Math.abs(c.cell.y - mid) <= 1)
        if (!best || level.length > best.cells.length) best = { cells: level, size: w * d, x0, z0, w, d }
      }
    }
  }
  if (!best || best.cells.length < best.size * USABLE_SHARE) {
    const most = best ? best.cells.length : 0
    throw new FacilityError(`not enough flat dirt or grass for a ${width}x${depth} farm in these zones (at most ${most} of ${width * depth} cells): choose a smaller size or other zones`)
  }
  const { crops, water } = waterCells(bot, best)
  if (crops.length + water.length < best.size * USABLE_SHARE) {
    throw new FacilityError(`not enough ground for a ${width}x${depth} farm with water in these zones (${crops.length} crop cells and ${water.length} water of ${width * depth}: the rest is uneven or out of the water's reach): choose a smaller size or other zones`)
  }
  // 耕すのは地面のブロック（y は地面）。水も地面のブロックを掘ってそこに置く。高さは、いちばん多い高さに
  // そろえる（整地: 設計書 75。そろっていない所は、そろえてから耕す）
  const ys = [...crops, ...water].map((c) => c.cell.y - 1)
  const counts = new Map()
  for (const y of ys) counts.set(y, (counts.get(y) ?? 0) + 1)
  const y = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0][0]
  const at = (c) => ({ x: c.cell.x, y, z: c.cell.z })
  return { cells: crops.map(at), water: water.map(at), y, rect: { x0: best.x0, z0: best.z0, w: best.w, d: best.d } }
}

// 畑のまわりの柵の輪（四角の 1 マス外側。高さは地面の上）と、ゲートのマス（輪の角でない、家にいちばん近い所）。
// 輪が家・建物・ほかの施設にかかるなら断る。ゲートの out は、輪から外へ向かう向き（ゲートを置くとき、そこに
// 立って向きをそろえる）
function fenceRing (bot, state, name, { x0, z0, w, d }, y) {
  const others = Object.values(state.facilities ?? {}).filter((x) => x.name !== name && x.kind !== 'home')
  const ring = []
  for (let x = x0 - 1; x <= x0 + w; x++) {
    for (let z = z0 - 1; z <= z0 + d; z++) {
      if (x >= x0 && x < x0 + w && z >= z0 && z < z0 + d) continue
      const corner = (x === x0 - 1 || x === x0 + w) && (z === z0 - 1 || z === z0 + d)
      const out = x === x0 - 1 ? [-1, 0] : x === x0 + w ? [1, 0] : z === z0 - 1 ? [0, -1] : [0, 1]
      ring.push({ x, y: y + 1, z, corner, out })
    }
  }
  for (const c of ring) {
    const cell = new Vec3(c.x, c.y, c.z)
    if (inHouse(state, cell, 0) || others.some((o) => inBox(o.box, cell))) {
      throw new FacilityError(`the fence around the farm would run over the home, a build or another facility at ${c.x},${c.z}: choose other zones (the farm needs a free ring of 1 cell around it)`)
    }
  }
  const from = state.home?.inside ?? bot.entity.position
  const open = ring.filter((c) => !c.corner && bot.blockAt(new Vec3(c.x, c.y, c.z))?.boundingBox !== 'block')
  const pool = open.length ? open : ring.filter((c) => !c.corner)
  const gate = pool.sort((a, b) => Math.hypot(a.x - from.x, a.z - from.z) - Math.hypot(b.x - from.x, b.z - from.z))[0]
  const plain = (c) => ({ x: c.x, y: c.y, z: c.z })
  return { fence: ring.filter((c) => c !== gate).map(plain), gate: { ...plain(gate), out: gate.out } }
}

// 水のマスを選ぶ。四角を縦横 9 マスずつに区切り、それぞれで一番多く湿らせるマス（同じなら真ん中に近いマス）に 1 つ。
// 水から縦横 HYDRATION マス以内で、水と同じ高さか 1 段低い耕地が湿る。どの水も届かないマスは畑に入れない。
// 水は地面のブロックを掘った穴に置くので、穴の横 4 マスの地面が水の高さ以上でなければ水が流れ出す（そういうマスは選ばない）
function waterCells (bot, { cells, x0, z0, w, d }) {
  const solid = (x, y, z) => bot.blockAt(new Vec3(x, y, z))?.boundingBox === 'block'
  const reach = (wet, c) => c !== wet && Math.max(Math.abs(c.cell.x - wet.cell.x), Math.abs(c.cell.z - wet.cell.z)) <= HYDRATION &&
    [0, 1].includes(wet.cell.y - c.cell.y)
  const water = []
  let unsafe = false
  for (let sx = 0; sx < w; sx += WATER_SPAN) {
    for (let sz = 0; sz < d; sz += WATER_SPAN) {
      const cw = Math.min(WATER_SPAN, w - sx)
      const cd = Math.min(WATER_SPAN, d - sz)
      const [cx, cz] = [x0 + sx + Math.floor(cw / 2), z0 + sz + Math.floor(cd / 2)]
      const chunk = cells.filter((c) => c.cell.x >= x0 + sx && c.cell.x < x0 + sx + cw && c.cell.z >= z0 + sz && c.cell.z < z0 + sz + cd)
      if (chunk.length < 2) continue // 水だけになる区切りは、まわりの水が届くなら届く
      const pick = chunk
        .filter((c) => [[1, 0], [-1, 0], [0, 1], [0, -1]].every(([dx, dz]) => solid(c.cell.x + dx, c.cell.y - 1, c.cell.z + dz)))
        .map((c) => ({ c, covered: chunk.filter((o) => reach(c, o)).length }))
        .sort((a, b) => b.covered - a.covered || Math.hypot(a.c.cell.x - cx, a.c.cell.z - cz) - Math.hypot(b.c.cell.x - cx, b.c.cell.z - cz))[0]
      if (pick) water.push(pick.c)
      else unsafe = true
    }
  }
  if (!water.length) {
    throw new FacilityError(unsafe
      ? 'there is no safe place for water in this farm (the ground around every cell is uneven: water would run out of its hole): choose other zones or a flatter place'
      : 'the farm is too small: one cell in every 9x9 is water, so it needs at least 2 cells (a 2x1 farm is 1 water and 1 crop)')
  }
  return { crops: cells.filter((c) => !water.includes(c) && water.some((wet) => reach(wet, c))), water }
}

// 植林場: 間隔 spacing の格子で、苗木を植えられる地面（範囲の中心に近い順に count か所）
function treeLayout (bot, cells, area, count, spacing) {
  const ok = cells.filter((c) => SAPLING_GROUND.test(c.ground.name) && !nearName(bot, c.cell, LOGS, TREE_GAP - 1))
  const byXZ = new Map(ok.map((c) => [cellKey(c.cell), c]))
  const cx = (area.min_x + area.max_x) / 2
  const cz = (area.min_z + area.max_z) / 2
  let best = []
  for (let ox = 0; ox < spacing; ox++) {
    for (let oz = 0; oz < spacing; oz++) {
      const grid = []
      for (let x = area.min_x + ox; x <= area.max_x; x += spacing) for (let z = area.min_z + oz; z <= area.max_z; z += spacing) { const c = byXZ.get(`${x},${z}`); if (c) grid.push(c) }
      if (grid.length > best.length) best = grid
    }
  }
  // 頼まれた数に少し足りないだけなら、ある所だけで作る（16 か所を頼んで 13 か所あった植林場が、3 回差し戻されて作られなかった: 2026-10-01）。
  // 半分に満たなければ、場所の選び直しを求める
  const least = Math.min(count, Math.max(4, Math.ceil(count / 2)))
  if (best.length < least) throw new FacilityError(`only ${best.length} places for saplings ${spacing} apart in these zones (asked ${count}, at least ${least} are needed): plant fewer, closer (spacing ${SPACING.min}), or choose more zones`)
  best.sort((a, b) => Math.hypot(a.cell.x - cx, a.cell.z - cz) - Math.hypot(b.cell.x - cx, b.cell.z - cz))
  return best.slice(0, count).map((c) => ({ x: c.cell.x, y: c.cell.y, z: c.cell.z })) // 植えるのは地面の上のマス
}

// PUT /facilities/<name> { kind, zones: [id], boxes: [{min_x, min_z, max_x, max_z}] | near_home: true, purpose, design }
//   farm: design { crop, width, depth }、tree_farm: design { sapling, count, spacing }
export function registerFacility (bot, state, name, body) {
  if (!FACILITY_NAME.test(name)) throw new FacilityError('the name must be lowercase letters, digits and _ (2-32)')
  state.facilities ??= {}
  if (state.facilities[name]) throw new FacilityError(`there is already a facility named ${name}`)
  const kind = String(body?.kind ?? '')
  if (!DESIGNABLE.includes(kind)) throw new FacilityError(`kind must be one of ${DESIGNABLE.join(', ')}`)
  // 理想図がまだなければ（街が決まる前）、家のまわり（24 m まで。家に近すぎる所は farmRefusal で除く）から選ぶ
  const around = PLANT_RING.max
  const area = body.near_home && state.home
    ? { min_x: state.home.inside.x - around, min_z: state.home.inside.z - around, max_x: state.home.inside.x + around, max_z: state.home.inside.z + around }
    : unionBox(body.boxes)
  const design = body.design ?? {}
  const cells = kind === 'mine' ? [] : groundCells(bot, state, area, name)
  let layout
  if (kind === 'farm') {
    const crop = cropOf(design.crop ?? 'wheat')
    if (!crop) throw new FacilityError(`crop must be one of ${Object.keys(CROPS).join(', ')}`)
    const side = (k) => {
      const n = Number(design[k])
      if (!Number.isInteger(n) || n < FARM_SIDE.min || n > FARM_SIDE.max) throw new FacilityError(`${k} must be ${FARM_SIDE.min}-${FARM_SIDE.max}`)
      return n
    }
    const farm = farmLayout(bot, cells, area, side('width'), side('depth'))
    layout = { crop, ...farm, ...fenceRing(bot, state, name, farm.rect, farm.y) }
  } else if (kind === 'mine') {
    layout = mineLayout(bot, state, area, name, design)
  } else {
    const sapling = String(design.sapling ?? 'sapling')
    if (sapling !== 'sapling' && !(SAPLING.test(sapling) && bot.registry.itemsByName[sapling])) throw new FacilityError('sapling must be sapling (any kind) or one kind like oak_sapling')
    const count = Number(design.count)
    if (!Number.isInteger(count) || count < TREES.min || count > TREES.max) throw new FacilityError(`count must be ${TREES.min}-${TREES.max}`)
    const spacing = Number(design.spacing ?? SPACING.min)
    if (!Number.isInteger(spacing) || spacing < SPACING.min || spacing > SPACING.max) throw new FacilityError(`spacing must be ${SPACING.min}-${SPACING.max} (saplings closer than ${TREE_GAP} do not grow well)`)
    layout = { sapling, spacing, cells: treeLayout(bot, cells, area, count, spacing) }
  }
  return storeFacility(state, name, kind, body, layout)
}

// 設計を施設として登録する
function storeFacility (state, name, kind, body, layout) {
  // 採掘場の範囲は地表の輪（4 x 4）。坑道は地下なので、上の区画はほかの施設に使える
  const e = layout.cells[0]
  const box = kind === 'mine' ? boxOf(RING.map(([dx, dz]) => ({ x: e.x + dx, y: e.y, z: e.z + dz }))) : boxOf([...layout.cells, ...(layout.water ?? []), ...(layout.fence ?? []), ...(layout.gate ? [layout.gate] : [])])
  state.facilities[name] = {
    name,
    kind,
    zone: (body.zones ?? []).map(String).join(','),
    purpose: String(body.purpose ?? ''),
    box,
    entrance: kind === 'mine' ? xyz(layout.cells[0]) : xyz(layout.cells.reduce((a, b) => (Math.hypot(a.x - state.home?.inside.x, a.z - state.home?.inside.z) <= Math.hypot(b.x - state.home?.inside.x, b.z - state.home?.inside.z) ? a : b))),
    layout,
    status: 'planned',
    createdAt: Date.now()
  }
  return state.facilities[name]
}

// 設計した施設の今: 済んだマス、耕す・まく・植えるマス、見えていないマス
// 畑・植林場の最初に植える量（全マスの割合）。種・苗木は、これだけ集めたら植え、残りは育ったものから回収して増やす
// （探し回らない。設計書 74）。済んだ（`started`）の判定にも同じ値を使う
export const START_SHARE = 0.25
export const startQuota = (cells) => Math.max(1, Math.ceil(cells * START_SHARE))

export function layoutStatus (bot, fac) {
  const water = fac.layout.water ?? []
  const out = { total: fac.layout.cells.length + water.length, done: 0, planted: 0, quota: startQuota(fac.layout.cells.length), started: false, till: [], sow: [], plant: [], water: [], unseen: 0, fence: [], gate: null, all: fac.layout.cells.length + water.length + (fac.layout.fence?.length ?? 0) + (fac.layout.gate ? 1 : 0) }
  // 柵（畑）: 立っているか、固いブロックで塞がっていれば済み。ゲートは柵のゲートが立っていれば済み
  for (const c of fac.layout.fence ?? []) {
    const p = new Vec3(c.x, c.y, c.z)
    const b = bot.blockAt(p)
    if (!b) out.unseen++
    else if (!FENCE_CLOSED.test(b.name) && b.boundingBox !== 'block') out.fence.push(p)
  }
  if (fac.layout.gate) {
    const g = fac.layout.gate
    const b = bot.blockAt(new Vec3(g.x, g.y, g.z))
    if (!b) out.unseen++
    else if (!FENCE_GATE_ITEM.test(b.name)) out.gate = new Vec3(g.x, g.y, g.z)
  }
  // 水のマス（畑）: 水が置いてあれば済み。掘って水を置くマスなので、耕すマスには数えない
  for (const c of water) {
    const p = new Vec3(c.x, c.y, c.z)
    const b = bot.blockAt(p)
    if (!b) out.unseen++
    else if (b.name === 'water') out.done++
    else out.water.push(p)
  }
  for (const c of fac.layout.cells) {
    const p = new Vec3(c.x, c.y, c.z)
    const b = bot.blockAt(p)
    if (!b) { out.unseen++; continue }
    if (fac.kind === 'farm') {
      const above = bot.blockAt(p.offset(0, 1, 0))
      if (b.name === 'farmland' && cropByBlock(above?.name) === fac.layout.crop) { out.done++; out.planted++ }
      else if (b.name === 'farmland') out.sow.push(p)
      else out.till.push(p)
    } else {
      if (SAPLING.test(b.name) || isLog(b.name)) { out.done++; out.planted++ }
      else out.plant.push(p)
    }
  }
  out.started = out.planted >= out.quota
  return out
}

// 設計した施設の状態を、今のマスから直す（planned → building → done）。変わったら真
export function updateStatus (bot, fac) {
  if (!fac.layout) return false
  const s = fac.kind === 'mine' ? mineStatus(bot, fac) : layoutStatus(bot, fac)
  if (s.unseen) return false
  const status = s.done === s.total && !s.fence?.length && !s.gate ? 'done' : s.done > 0 ? 'building' : 'planned'
  if (status === fac.status) return false
  fac.status = status
  return true
}

// 次に作業するマス（ボットに近い順）: kind は till / sow / plant
export function nextCell (bot, fac, kind) {
  const me = bot.entity.position
  return layoutStatus(bot, fac)[kind].sort((a, b) => a.distanceTo(me) - b.distanceTo(me))[0] ?? null
}

// ---- 作物と植林は施設で数える（設計書 47 §14） ----
// 数えるのは、その作物の設計した畑（設計のマス）と、家のまわりの設計でない耕地（home_farm: 前から
// ある畑）の合計。ボットの位置によらない（近い畑だけを数えると、歩くたびに残りが変わる）。畑が
// 読み込まれていなければ、最後に見た数（state.memory.farms[施設][作物]）。新しく耕す・植えるのは
// 設計した施設のマスだけ

// 設計した畑（crop を省くと全部）
export function designedFarms (state, crop = null) {
  return Object.values(state.facilities ?? {}).filter((f) => f.layout && f.kind === 'farm' && (!crop || f.layout.crop === crop))
}

// その苗木を植えられる、設計した植林場（sapling はグループか 1 種類）
export function designedTreeFarms (state, sapling = 'sapling') {
  return Object.values(state.facilities ?? {}).filter((f) => f.layout && f.kind === 'tree_farm' &&
    (sapling === 'sapling' || f.layout.sapling === 'sapling' || f.layout.sapling === sapling))
}

const sameCell = (c, p) => c.x === p.x && c.y === p.y && c.z === p.z

// 設計した施設のうち、そのマスを持つもの（耕すマスは地面、植えるマスは地面の上）
export function layoutAt (state, kind, pos) {
  return Object.values(state.facilities ?? {}).find((f) => f.layout && f.kind === kind && f.layout.cells.some((c) => sameCell(c, pos))) ?? null
}

// 畑の水のマス（掘って水を置く地面のブロック）を持つ畑
export function waterAt (state, pos) {
  return Object.values(state.facilities ?? {}).find((f) => f.layout && f.kind === 'farm' && (f.layout.water ?? []).some((c) => sameCell(c, pos))) ?? null
}

// 家のまわりの、設計した畑の外の、上が空いた耕地（前からある畑の空き）
function homeFreeFarmland (bot, state) {
  const home = state.home
  const id = bot.registry?.blocksByName?.farmland?.id
  if (!home || id == null || !bot.blockAt(home.inside)) return []
  const designed = designedFarms(state)
  return bot.findBlocks({ point: home.inside, matching: id, maxDistance: FARM_SEARCH, count: 256 })
    .filter((p) => bot.blockAt(p.offset(0, 1, 0))?.name === 'air' && !designed.some((d) => inBox(d.box, p)))
}

// 1 つの畑の、その作物: 植わった位置（作物のブロック）から { planted, ripe, ages, pos, free, sow }。
// sow は free のうち、耕し済みでまだ何も植わっていないマス
function measureCrops (bot, crop, positions, free, sow = free, water = 0) {
  const mature = CROPS[crop].mature
  const ages = positions.map((p) => ageOf(bot.blockAt(p)))
  const ripe = positions.filter((_, i) => ages[i] >= mature)
  const pos = ripe[0] ?? positions[0]
  return {
    planted: positions.length,
    ripe: ripe.length,
    dry: positions.filter((p) => isDry(bot.blockAt(p.offset(0, -1, 0)))).length,
    ages: ages.length ? [Math.min(...ages), Math.max(...ages)] : null,
    free,
    sow,
    water,
    pos: pos ? { x: pos.x, y: pos.y, z: pos.z } : null
  }
}

// その作物の畑の合計: { planted, ripe, dry, ages, mature, free, sow, water, pos, remembered, farms: [{ name, planted,
// ripe, dry, free, sow, water, remembered }] }。free は、まだまける・耕せるマス（設計した畑の残りと、家の畑の空いた耕地）、
// sow はそのうち耕し済みでまだまいていないマス、water はまだ水を置いていない水のマス。
// dry は、植わっているマスのうち下の耕地が湿っていないもの（知らせるだけ。目標の判定には使わない）。
// pos は熟したもの（なければ植わったもの）がある、ボットに一番近い畑の 1 マス
export function cropFarms (bot, state, crop) {
  const memory = state.memory ? (state.memory.farms ??= {}) : {}
  const farms = []
  const keep = (name, m) => {
    memory[name] ??= {}
    if (m.planted || m.free) memory[name][crop] = m
    else delete memory[name][crop]
    if (m.planted || m.free) farms.push({ name, ...m, remembered: false })
  }
  const recall = (name) => {
    const m = memory[name]?.[crop]
    if (m) farms.push({ name, ...m, remembered: true })
  }
  for (const fac of designedFarms(state, crop)) {
    const s = layoutStatus(bot, fac)
    if (s.unseen === s.all) { recall(fac.name); continue }
    const positions = fac.layout.cells.map((c) => new Vec3(c.x, c.y + 1, c.z))
      .filter((p) => bot.blockAt(p.offset(0, -1, 0))?.name === 'farmland' && cropByBlock(bot.blockAt(p)?.name) === crop)
    keep(fac.name, measureCrops(bot, crop, positions, s.till.length + s.sow.length, s.sow.length, s.water.length))
  }
  if (state.home) {
    if (!bot.blockAt(state.home.inside)) recall(HOME_FARM)
    else {
      const designed = designedFarms(state)
      const positions = cropStatus(bot, state, crop).positions.filter((p) => !designed.some((d) => inBox(d.box, p)))
      keep(HOME_FARM, measureCrops(bot, crop, positions, homeFreeFarmland(bot, state).length))
    }
  }
  const me = bot.entity.position
  const planted = farms.filter((f) => f.planted)
  const near = (list) => list.filter((f) => f.pos).sort((a, b) => Math.hypot(a.pos.x - me.x, a.pos.z - me.z) - Math.hypot(b.pos.x - me.x, b.pos.z - me.z))[0]
  const at = near(planted.filter((f) => f.ripe)) ?? near(planted)
  return {
    planted: farms.reduce((n, f) => n + f.planted, 0),
    ripe: farms.reduce((n, f) => n + f.ripe, 0),
    dry: farms.reduce((n, f) => n + (f.dry ?? 0), 0),
    ages: planted.length ? [Math.min(...planted.map((f) => f.ages[0])), Math.max(...planted.map((f) => f.ages[1]))] : null,
    mature: CROPS[crop].mature,
    free: farms.reduce((n, f) => n + f.free, 0),
    sow: farms.reduce((n, f) => n + (f.sow ?? f.free), 0),
    water: farms.reduce((n, f) => n + (f.water ?? 0), 0),
    pos: at?.pos ?? null,
    remembered: farms.length > 0 && farms.every((f) => f.remembered),
    farms: farms.map(({ name, planted, ripe, dry, free, sow, water, remembered }) => ({ name, planted, ripe, dry: dry ?? 0, free, sow: sow ?? free, water: water ?? 0, ...(remembered ? { remembered } : {}) }))
  }
}

// 畑の作物ごとのまとめ（観測の farm）: { wheat: { planted, ripe, ages, mature, free, farms } }（植わっているか、
// 設計した畑がある作物だけ）
export function farmSummary (bot, state) {
  const out = {}
  for (const crop of Object.keys(CROPS)) {
    const f = cropFarms(bot, state, crop)
    if (!f.planted && !designedFarms(state, crop).length) continue
    out[crop] = { planted: f.planted, ripe: f.ripe, dry: f.dry, ages: f.ages, mature: f.mature, free: f.free, water: f.water, farms: f.farms.map((x) => x.name), ...(f.remembered ? { remembered: true } : {}) }
  }
  return out
}

// 設計した施設（畑、植林場）の範囲（柵の輪を含む）に p があれば、その施設の名前。穴を掘って潜る（burrow）などが、
// 畑を壊さないために見る（2026-10-04: 昼に畑の中へ縦穴を掘って、畑を壊した）。採掘場は onMineCell
export function facilityAt (state, p, margin = 0) {
  for (const fac of Object.values(state.facilities ?? {})) {
    if (!fac?.box || fac.kind === 'mine') continue
    const b = fac.box
    if (p.x >= b.min.x - margin && p.x <= b.max.x + margin && p.z >= b.min.z - margin && p.z <= b.max.z + margin) return fac.name
  }
  return null
}

// 長方形・高さ・柵の輪を持たない古い畑（設計書 75 より前に設計した畑）に、それを補う（2026-10-04、user: 「畑が
// きれいな長方形じゃなくて段差があります。段差は埋めるようにできますか」）。長方形はマスと水の外接四角、高さは
// 一番多い地面の高さ（同数なら低い方）。マスと水の高さをそれにそろえる（低いマスは整地で埋め上げる）。柵の輪は、
// 家や建物にかかるなら付けない（畑はそのまま）。何度呼んでも同じ（補ってあれば何もしない）。補ったら true
export function ensureFarmShape (bot, state, fac) {
  const layout = fac?.kind === 'farm' ? fac.layout : null
  if (!layout || layout.rect || !layout.cells?.length) return false
  const all = [...layout.cells, ...(layout.water ?? [])]
  const [x0, x1] = [Math.min(...all.map((c) => c.x)), Math.max(...all.map((c) => c.x))]
  const [z0, z1] = [Math.min(...all.map((c) => c.z)), Math.max(...all.map((c) => c.z))]
  const counts = new Map()
  for (const c of all) counts.set(c.y, (counts.get(c.y) ?? 0) + 1)
  const y = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0][0]
  const rect = { x0, z0, w: x1 - x0 + 1, d: z1 - z0 + 1 }
  for (const c of all) c.y = y
  layout.y = y
  layout.rect = rect
  try {
    Object.assign(layout, fenceRing(bot, state, fac.name, rect, y))
  } catch (e) {
    if (!(e instanceof FacilityError)) throw e
    console.log(`[bridge] 畑 ${fac.name} の柵の輪は付けない: ${e.message}`)
  }
  fac.box = boxOf([...all, ...(layout.fence ?? []), ...(layout.gate ? [layout.gate] : [])])
  console.log(`[bridge] 畑 ${fac.name} に長方形 ${rect.w}x${rect.d} と高さ y ${y} を補った`)
  return true
}

// 耕す・まく・植える次のマス（ボットに近い順）: { pos, facility } か null。
//   facility: その施設だけ。なければ、till はその作物（省けば全部）の設計した畑、sow はそれに家の畑の空いた
//   耕地も、plant はその苗木を植えられる設計した植林場
export function workCell (bot, state, kind, { facility = null, crop = null, sapling = 'sapling' } = {}) {
  const me = bot.entity.position
  const cells = []
  const facs = facility
    ? [state.facilities?.[facility]].filter((f) => f?.layout)
    : kind === 'plant' ? designedTreeFarms(state, sapling) : designedFarms(state, crop)
  for (const fac of facs) {
    // 高さがそろっていない畑は、耕さない（そろえるときに掘り返される。設計書 75）
    ensureFarmShape(bot, state, fac)
    if (kind === 'till' && fac.layout.rect && farmLevel(bot, state, fac).off) continue
    for (const p of layoutStatus(bot, fac)[kind] ?? []) cells.push({ pos: p, facility: fac.name })
  }
  if (kind === 'sow' && !facility) {
    for (const p of homeFreeFarmland(bot, state)) cells.push({ pos: p, facility: HOME_FARM })
  }
  // さっき断られたマス（耕せなかった、まけなかった）は選ばない。別のマスがあればそちらへ
  return cells.filter((c) => !refusalAt(state, kind, c.pos)).sort((a, b) => a.pos.distanceTo(me) - b.pos.distanceTo(me))[0] ?? null
}

// ---- 採掘場（設計書 47 §16）: 4 x 4 の輪を回って下りるらせん階段、下の部屋、そこから横へ延びる坑道 ----
// セルは掘って空ける順に並ぶ: { x, y, z, h, part }（y は足の高さ、h は空ける高さ: 階段 3、部屋と坑道 2）。
// 階段は 1 周 12 段なので、上の段と下の段の空ける所は重ならない（床が残る）

export const MINE_BOTTOM = { min: -58, max: 100 } // 1.21.4 の岩盤は y -64〜-60
export const MINE_MIN_STEPS = 4
export const TUNNELS = { min: 0, max: 4 }
export const TUNNEL_LENGTH = { min: 4, max: 32 }
// ブランチマイニング（設計書 68 §6、86）: 坑道（幹）の脇に、3 マスおきに左右へ枝を掘る（枝の間に岩が 2 マス残る:
// 枝の壁が 1 マスおきに見え、掘る量のわりに鉱石の面が一番多い）。枝の長さ 0 は、枝なし（LLM が設計する採掘場）
export const BRANCH = { length: 10, spacing: 3, max: 16 }
const MINE_OFFSETS = 25
const MINE_SITES = 60 // 自分で設計するとき: 家の外周の輪の、試す入口の数
const RING = [[0, 0], [1, 0], [2, 0], [3, 0], [3, 1], [3, 2], [3, 3], [2, 3], [1, 3], [0, 3], [0, 2], [0, 1]] // 4 x 4 の外周
const FALLING = /(^|_)(sand|gravel)$|concrete_powder$/
const FLUID = /water|lava/
const SIX = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
const DIRECTIONS = { east: [1, 0], south: [0, 1], west: [-1, 0], north: [0, -1] }

const ROOM_HEIGHT = 2
export const MINE_GEOMETRY = 2 // 階段の床を残す形（1 は、部屋が最後の 2 段の床を消す形）

// 階段の最後の段の手前の、足場の床が部屋の高さに入る段の列（"x,z"）。部屋は階段の輪と同じ 4 x 4 で、底の高さから
// ROOM_HEIGHT 掘る。最後から 2 段目と 3 段目の床は部屋の中にあたるので、そこまで掘ると床が消え、部屋の床から階段へ
// 1 段ずつ上がれなくなる（2026-10-04: mine_1 の底に下りたあと、3 ブロックの壁で上がれず家に帰れなかった）。その列は
// 部屋として掘らない
export function stairSupport (stairs, bottom) {
  return new Set(stairs.slice(0, -1).filter((s) => s.y - 1 >= bottom && s.y - 1 <= bottom + ROOM_HEIGHT - 1).map((s) => `${s.x},${s.z}`))
}

const isOpen = (b) => !!b && b.boundingBox === 'empty' && !FLUID.test(b.name)
const blocksOf = (c) => Array.from({ length: c.h }, (_, i) => new Vec3(c.x, c.y + i, c.z))

// そのセルを掘ると危ない理由（なし: ''）。planned は空ける予定のマス（床や隣が予定の空きなら洞窟ではない）
export function mineDanger (bot, c, planned = new Set(), sealable = false) {
  for (const p of blocksOf(c)) {
    const b = bot.blockAt(p)
    if (!b) return 'not loaded'
    if (b.name === 'bedrock') return `bedrock at ${p.x},${p.y},${p.z}`
    for (const [x, y, z] of [[0, 0, 0], ...SIX]) {
      const n = bot.blockAt(p.offset(x, y, z))
      if (n && FLUID.test(n.name)) return `${n.name.includes('lava') ? 'lava' : 'water'} at ${n.position.x},${n.position.y},${n.position.z}`
    }
  }
  const above = bot.blockAt(new Vec3(c.x, c.y + c.h, c.z))
  if (FALLING.test(above?.name ?? '')) return `${above.name} above ${c.x},${c.y + c.h - 1},${c.z} would fall in`
  const floor = new Vec3(c.x, c.y - 1, c.z)
  const below = bot.blockAt(floor)
  // sealable: 坑道と枝は、床の空洞を丸石で埋めて通る（sealing.mjs）。水や溶岩は上で断っている
  if (!sealable && !planned.has(`${floor.x},${floor.y},${floor.z}`) && below?.boundingBox !== 'block') return `a cave or a drop under ${c.x},${c.y},${c.z}`
  return ''
}

// 設計の形（向きと場所なし）から、x0,z0 の輪で入口の足 y0 から bottom まで下りるセルの並び。
// branch > 0 なら、坑道の 3 マスおき（BRANCH.spacing）に左右へ長さ branch の枝（高さ 2）。枝は坑道を全部掘ってから、
// 入口に近い順に左右ひとつずつ、付け根から先へ: どのセルも、先に空けた隣のセルに立って掘れる
function mineCells (x0, z0, y0, bottom, tunnels, length, branch = 0) {
  const cells = []
  const steps = y0 - bottom
  for (let i = 0; i <= steps; i++) {
    const [dx, dz] = RING[i % RING.length]
    cells.push({ x: x0 + dx, y: y0 - i, z: z0 + dz, h: 3, part: 'stairs' })
  }
  // 下の部屋（4 x 4、高さ 2）: 最後の段から近い順（幅優先）。どのセルも、先に空けた隣に立って掘れる。階段の床になる列は
  // 掘らない（stairSupport）
  const last = cells[cells.length - 1]
  const support = stairSupport(cells, bottom)
  const seen = new Set([`${last.x},${last.z}`, ...support])
  const queue = [{ x: last.x, z: last.z }]
  while (queue.length) {
    const p = queue.shift()
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const q = { x: p.x + dx, z: p.z + dz }
      const key = `${q.x},${q.z}`
      if (seen.has(key) || q.x < x0 || q.x > x0 + 3 || q.z < z0 || q.z > z0 + 3) continue
      seen.add(key)
      queue.push(q)
      cells.push({ ...q, y: bottom, h: 2, part: 'room' })
    }
  }
  // 坑道: 部屋の各辺の、部屋に掘る列から外へまっすぐ（真ん中の 2 列を先に。床の列に塞がれていたら端の列）
  const taken = new Set()
  const branches = []
  for (const [name, [dx, dz]] of Object.entries(DIRECTIONS)) {
    if (!tunnels.includes(name)) continue
    const lane = [1, 2, 0, 3].find((k) => !support.has(dx !== 0 ? `${dx > 0 ? x0 + 3 : x0},${z0 + k}` : `${x0 + k},${dz > 0 ? z0 + 3 : z0}`)) ?? 1
    const sx = dx > 0 ? x0 + 4 : dx < 0 ? x0 - 1 : x0 + lane
    const sz = dz > 0 ? z0 + 4 : dz < 0 ? z0 - 1 : z0 + lane
    for (let i = 0; i < length; i++) {
      const c = { x: sx + dx * i, y: bottom, z: sz + dz * i, h: 2, part: `tunnel_${name}` }
      taken.add(`${c.x},${c.z}`)
      cells.push(c)
    }
    // 枝: 最後の 1 マス手前まで、spacing おき。坑道に直角に左右へ
    for (let i = BRANCH.spacing - 1; branch > 0 && i < length - 1; i += BRANCH.spacing) {
      for (const side of [1, -1]) {
        const [px, pz] = dx !== 0 ? [0, side] : [side, 0]
        const part = `branch_${name}_${i}_${side > 0 ? 'a' : 'b'}`
        branches.push({ i, order: branches.length, cells: Array.from({ length: branch }, (_, j) => ({ x: sx + dx * i + px * (j + 1), y: bottom, z: sz + dz * i + pz * (j + 1), h: 2, part })) })
      }
    }
  }
  // 別の坑道や枝とぶつかるセルは、そこで枝を打ち切る（同じマスを 2 度数えない）
  for (const b of branches.sort((a, c) => a.i - c.i || a.order - c.order)) {
    for (const c of b.cells) {
      const key = `${c.x},${c.z}`
      if (taken.has(key)) break
      taken.add(key)
      cells.push(c)
    }
  }
  return cells
}

// 空ける予定のマス（"x,y,z"）
export const plannedBlocks = (cells) => new Set(cells.flatMap((c) => blocksOf(c).map((p) => `${p.x},${p.y},${p.z}`)))
export const mineBlocks = blocksOf

// 危なくないセルの並びの長さ（最初の危ないセルまで）と、その理由。from より前のセルは確かめ済み
function safePrefix (bot, cells, from = 0) {
  const planned = plannedBlocks(cells)
  for (let i = from; i < cells.length; i++) {
    const why = mineDanger(bot, cells[i], planned)
    if (why) return { n: i, why }
  }
  return { n: cells.length, why: '' }
}

// 採掘場の設計: 区画の中で、入口から bottom_y まで安全に下りられる輪（中心に近い順）。坑道は安全な向きから。
// sites（[x0, z0] の並び）があれば、区画でなくそこを順に試す（採掘場を自分で設計するとき: 家の外周の輪）。
// design.min_tunnels があれば、坑道は短くして（4 本 → その数まで）でも、その本数が掘れる輪を選ぶ
function mineLayout (bot, state, area, name, design, sites = null) {
  const bottom = Number(design.bottom_y)
  if (!Number.isInteger(bottom) || bottom < MINE_BOTTOM.min || bottom > MINE_BOTTOM.max) throw new FacilityError(`bottom_y must be ${MINE_BOTTOM.min}-${MINE_BOTTOM.max}`)
  const count = Number(design.tunnels ?? 0)
  if (!Number.isInteger(count) || count < TUNNELS.min || count > TUNNELS.max) throw new FacilityError(`tunnels must be ${TUNNELS.min}-${TUNNELS.max}`)
  const length = Number(design.tunnel_length ?? 16)
  if (count && (!Number.isInteger(length) || length < TUNNEL_LENGTH.min || length > TUNNEL_LENGTH.max)) throw new FacilityError(`tunnel_length must be ${TUNNEL_LENGTH.min}-${TUNNEL_LENGTH.max}`)
  const branch = Number(design.branch_length ?? 0)
  if (!Number.isInteger(branch) || branch < 0 || branch > BRANCH.max) throw new FacilityError(`branch_length must be 0-${BRANCH.max}`)
  // 枝のある採掘場は、一直線の幹（東西、だめなら南北）に左右へ枝。枝なしは今までの順に、通れる向きから
  const directionSets = branch ? [['east', 'west'], ['south', 'north']] : [Object.keys(DIRECTIONS)]
  const y0ref = state.home ? state.home.inside.y : Math.floor(bot.entity.position.y)
  const others = Object.values(state.facilities ?? {}).filter((x) => x.name !== name && x.kind !== 'home')
  const offsets = sites ? [...sites] : []
  if (!sites) {
    const cx = (area.min_x + area.max_x) / 2
    const cz = (area.min_z + area.max_z) / 2
    for (let x0 = area.min_x; x0 + 3 <= area.max_x; x0++) for (let z0 = area.min_z; z0 + 3 <= area.max_z; z0++) offsets.push([x0, z0])
    offsets.sort((a, b) => Math.hypot(a[0] + 1.5 - cx, a[1] + 1.5 - cz) - Math.hypot(b[0] + 1.5 - cx, b[1] + 1.5 - cz))
  }
  if (!offsets.length) throw new FacilityError('the zones are smaller than the 4 x 4 shaft')
  // ほかの採掘場が空ける所（坑道が別の採掘場の階段や坑道に突き当たらないように）
  const taken = new Set(others.filter((o) => o.kind === 'mine' && o.layout).flatMap((o) => [...plannedBlocks(o.layout.cells)]))
  const clear = (cells) => !taken.size || cells.every((c) => blocksOf(c).every((p) => !taken.has(`${p.x},${p.y},${p.z}`)))
  const min = design.min_tunnels
  // 試すのは中心に近い MINE_OFFSETS か所まで（全部の位置で深い縦穴を読むと、ボットの処理が長く止まる）
  offsets.splice(sites ? MINE_SITES : MINE_OFFSETS)
  let deepest = null
  let unloaded = 0
  for (const [x0, z0] of offsets) {
    if (!bot.blockAt(new Vec3(x0, y0ref, z0))) { unloaded++; continue }
    const s = surfaceAt(bot, x0, z0, y0ref)
    if (!s) continue
    const y0 = s.cell.y
    if (y0 - bottom < MINE_MIN_STEPS) throw new FacilityError(`bottom_y ${bottom} is not below the ground (y ${y0 - 1}) by ${MINE_MIN_STEPS} or more`)
    if (RING.some(([dx, dz]) => farmRefusal(state, new Vec3(x0 + dx, y0, z0 + dz)) || others.some((o) => inBox(o.box, new Vec3(x0 + dx, y0, z0 + dz))))) continue
    const shaft = mineCells(x0, z0, y0, bottom, [], 0)
    if (!clear(shaft)) continue
    const safe = safePrefix(bot, shaft)
    if (safe.n < shaft.length) {
      const y = shaft[Math.max(0, safe.n - 1)].y
      if (!deepest || y < deepest.y) deepest = { y, why: safe.why }
      continue
    }
    // 坑道: 安全に掘り切れる向きを count 本（東、南、西、北の順に試す）。min_tunnels があれば、足りないとき
    // 坑道を短くして（16、12、8）試し、それでも足りなければ min_tunnels 本まで減らす
    const shorter = min !== undefined ? [24, 16, 12, 8].filter((n) => n < length && n >= TUNNEL_LENGTH.min) : []
    const lengths = count ? [length, ...shorter] : [0]
    const attempts = [...lengths.map((len) => [len, count]), ...(min !== undefined && min < count ? lengths.map((len) => [len, min]) : [])]
    let picked = null
    for (const directions of directionSets) {
      for (const [len, need] of attempts) {
        const ok = directions.filter((d) => {
          const t = mineCells(x0, z0, y0, bottom, [d], len).filter((c) => c.part.startsWith('tunnel'))
          return clear(t) && safePrefix(bot, [...shaft, ...t], shaft.length).n === shaft.length + t.length
        })
        if (ok.length >= need) { picked = { ok: ok.slice(0, count), len }; break }
      }
      if (picked) break
    }
    if (!picked) {
      if (!deepest || bottom <= deepest.y) deepest = { y: bottom, why: `fewer than ${min ?? count} tunnels of ${length} are safe here` }
      continue
    }
    // 枝は、ほかの採掘場の空ける所に当たったら、そこから先を外す（危ないかは掘るときに見る: 設計では読み込めない所もある）
    const cells = mineCells(x0, z0, y0, bottom, picked.ok, picked.len, branch)
    return { geometry: MINE_GEOMETRY, bottom_y: bottom, tunnels: picked.ok, tunnel_length: count ? picked.len : 0, branch_length: count ? branch : 0, ...(branch ? { sealing: true } : {}), cells: trimBranches(cells, clear) }
  }
  if (unloaded > offsets.length / 2) throw new FacilityError('the area is not loaded (too far from the bot): go near it before designing', { world: true })
  if (deepest) throw new FacilityError(`no safe shaft down to y ${bottom} in these zones (the deepest safe one reaches y ${deepest.y}: ${deepest.why}): choose bottom_y ${deepest.y} or higher, fewer or shorter tunnels, or other zones`)
  throw new FacilityError('no ground for the mine entrance in these zones (houses, builds, other facilities or no solid ground)')
}

// 採掘場の今: 空いたセル、次に掘るセル（並びの最初の空いていないもの）、見えていないセル
// 枝のある採掘場（layout.sealing）は、空いているだけでは済みでない: 空洞に出たセルは、丸石で囲まれて（sealed）から済み
// （階段は除く。sealing.mjs。dig_mine が点検して印を付ける）
export function mineStatus (bot, fac) {
  const out = { total: fac.layout.cells.length, done: 0, next: null, index: -1, unseen: 0 }
  const sealing = !!fac.layout.sealing
  fac.layout.cells.forEach((c, i) => {
    const bs = blocksOf(c).map((p) => bot.blockAt(p))
    if (bs.some((b) => !b)) { out.unseen++; return }
    if (bs.every(isOpen) && (!sealing || c.part === 'stairs' || c.sealed)) { out.done++; return }
    if (!out.next) { out.next = c; out.index = i }
  })
  return out
}

// 採掘場ごとの、空ける所（"x,y,z"）と、らせん階段の縦の範囲（輪の 4 x 4、底の床から入口の上まで）。
// 経路探しが 1 回に何千回も聞くので、セルの並びごとに作っておく（stopMine で並びが変わると作り直す）
const mineIndexes = new WeakMap()
function mineIndex (fac) {
  let index = mineIndexes.get(fac.layout.cells)
  if (!index) {
    const cells = fac.layout.cells
    const stairs = cells.filter((c) => c.part === 'stairs')
    index = {
      open: plannedBlocks(cells),
      body: cells.length ? { ...fac.box, y0: (stairs.at(-1) ?? cells[0]).y - 1, y1: cells[0].y + 2 } : null
    }
    mineIndexes.set(fac.layout.cells, index)
  }
  return index
}
const mines = (state) => Object.values(state.facilities ?? {}).filter((f) => f.kind === 'mine' && f.layout)

// 設計した採掘場の名前と状態（planned / building / done）。地下の鉱石が見つからないとき、作るか続けるかを言う材料
export const mineSummaries = (state) => mines(state).map((f) => ({ name: f.name, status: f.status }))

// 街: 家・建物・施設の範囲と、そのまわり TOWN_MARGIN マス（施設の box は家・建物・畑・植林場・採掘場を含む）。土を集めるときは
// 街の外から取る（街の中を掘ると、畑や道に穴があく。設計書 115）
export const TOWN_MARGIN = 6
export function inTown (state, p) {
  if (inHouse(state, p, TOWN_MARGIN)) return true
  return Object.values(state.facilities ?? {}).some((f) => f.box && p.x >= f.box.min.x - TOWN_MARGIN && p.x <= f.box.max.x + TOWN_MARGIN && p.z >= f.box.min.z - TOWN_MARGIN && p.z <= f.box.max.z + TOWN_MARGIN)
}

// 設計した畑の作物のマスの地面を掘る経路探しの費用（0 は無料、100 以上は掘れない。このマスを掘ると、畑に穴があいて直す手間が
// 増える。禁止にしないのは、直す手を妨げないため: user 「守っちゃうと修正が効きにくくなるので掘るコストを高くして欲しい」）
export const FARM_DIG_COST = 30
const farmGround = new WeakMap()
export function onFarmGround (state, p) {
  const facs = state.facilities
  if (!facs) return false
  const farms = Object.values(facs).filter((f) => f.kind === 'farm' && f.layout?.rect)
  const size = farms.reduce((n, f) => n + f.layout.cells.length, 0)
  let index = farmGround.get(facs)
  if (!index || index.size !== size || index.count !== farms.length) {
    index = { size, count: farms.length, keys: new Set(farms.flatMap((f) => f.layout.cells.map((c) => `${c.x},${c.y},${c.z}`))) }
    farmGround.set(facs, index)
  }
  return index.keys.has(`${p.x},${p.y},${p.z}`)
}

// 採掘場のセルか（空けておく所: 足場も土も置かない、整地で埋めない）
export function onMineCell (state, p) {
  return mines(state).some((f) => mineIndex(f).open.has(`${p.x},${p.y},${p.z}`))
}

// らせん階段の柱と段の床（空けない所）か: 経路探しに壊させない（階段が崩れる）
export function inMineBody (state, p) {
  return mines(state).some((f) => {
    const { open, body } = mineIndex(f)
    return body && p.x >= body.min.x && p.x <= body.max.x && p.z >= body.min.z && p.z <= body.max.z &&
      p.y >= body.y0 && p.y <= body.y1 && !open.has(`${p.x},${p.y},${p.z}`)
  })
}

// 階段の柱と段の床を掘ってよくない理由（掘ってよければ null）。直接掘る手（dig、find_blocks、around）が使う:
// dig_mine の「壁に石が見える」を掘って階段の床と柱を崩し、底から上がれなくなった（2026-10-04）
export const MINE_BODY_REASON = 'it is the pillar or a step of a mine staircase: digging it leaves no way back up'
export const mineBodyReason = (state, p) => inMineBody(state, p) ? MINE_BODY_REASON : null

// 枝のセルのうち、clear（ほかの採掘場の空ける所に当たらない）でなくなったところから、その枝の先を外す
function trimBranches (cells, clear) {
  const cut = new Set()
  return cells.filter((c) => {
    if (!c.part.startsWith('branch')) return true
    if (cut.has(c.part)) return false
    if (clear([c])) return true
    cut.add(c.part)
    return false
  })
}

// 採掘の途中で見つけた危ないもの: そのセルから先を設計から外し、理由を残す（黙って浅くしない）。
// 枝のセルなら、その枝の先だけを外す（ほかの枝は掘れる）。階段・部屋・坑道なら、その先すべて
export function stopMine (fac, index, reason) {
  const c = fac.layout.cells[index]
  fac.layout.stopped = { x: c.x, y: c.y, z: c.z, reason }
  fac.layout.cells = c.part.startsWith('branch')
    ? fac.layout.cells.filter((x, i) => i < index || x.part !== c.part)
    : fac.layout.cells.slice(0, index)
}

// 採掘場を自分で設計する（設計書 68）: sites（[x0, z0] の並び）の中で、bottom_y まで安全に下りられる所。
// 東西の坑道（長さ 32、危なければ短く、本数も減らして）に 3 マスおきの枝（長さ 10）。名前は mine_1、mine_2…
export function designMine (bot, state, bottom, sites) {
  state.facilities ??= {}
  let n = 1
  while (state.facilities[`mine_${n}`]) n++
  const name = `mine_${n}`
  const layout = mineLayout(bot, state, null, name, { bottom_y: bottom, tunnels: 2, tunnel_length: TUNNEL_LENGTH.max, branch_length: BRANCH.length, min_tunnels: 1 }, sites)
  return storeFacility(state, name, 'mine', { purpose: 'stone and ore below the ground: branch mining (stairs down, a main tunnel east and west, branches every 3 blocks on both sides)' }, layout)
}

// 採掘場の底（階段の最後のセル）。設計より浅く切れた採掘場は、切れた所
export const mineBottom = (fac) => {
  const stairs = fac.layout.cells.filter((c) => c.part === 'stairs')
  const b = stairs[stairs.length - 1] ?? fac.layout.cells[0]
  return { x: b.x, y: b.y, z: b.z }
}

// 底が y の上下 tolerance の内にある採掘場（y が null なら全部）: ボットに近い順。{ fac, bottom, d }
export function minesAt (bot, state, y = null, tolerance = 16) {
  const me = bot.entity.position
  return mines(state)
    .filter((f) => f.layout.cells.length && !f.layout.exhausted) // 掘り尽くして鉱石が出なかった採掘場は、もう当てにしない
    .map((fac) => { const bottom = mineBottom(fac); return { fac, bottom, d: Math.hypot(bottom.x - me.x, bottom.z - me.z) } })
    .filter((m) => y === null || Math.abs(m.bottom.y - y) <= tolerance)
    .sort((a, b) => a.d - b.d)
}

// 下りきった採掘場（ボットに近い順の最初の 1 つ）: { name, bottom: {x,y,z}, above（ボットが底より上） } か null。
// y があれば、その高さの鉱石に合う（底が上下 16 の内の）もの
export function finishedMine (bot, state, y = null) {
  const me = bot.entity.position
  const m = minesAt(bot, state, y).find((x) => x.fac.status === 'done')
  return m ? { name: m.fac.name, bottom: m.bottom, above: me.y > m.bottom.y + 2, d: m.d } : null
}
