// 施設の登録（設計書 47 §5）: 家、名前付きの建物、畑、植林場、採掘場…。施設の座標を持つのはここだけ
// （Python の街の理想図は区画の範囲だけ）。state.facilities に保存する。
//
// 今ある物は施設として取り込む（adoptExisting）: 家、state.builds、家のまわりの耕地（home_farm）、
// 植えた苗木のかたまり（tree_farm_N）。取り込まないと、理想図が今の畑の上に別の区画を描き、畑を忘れる。
import vec3Pkg from 'vec3'
import { round, bearing, isLog } from './observe.mjs'
import { buildBox } from './builds.mjs'
import { CROPS, cropByBlock, cropOf, cropStatus, ageOf, isRipe, SAPLING, PLANT_RING, TILLABLE, CLEARABLE, SAPLING_GROUND, TREE_GAP, farmRefusal, nearName, surfaceAt } from './farming.mjs'

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
  if (state.home && !f.home) {
    const h = state.home
    f.home = { name: 'home', kind: 'home', box: { min: xyz(h.min.offset(-1, 0, -1)), max: xyz(h.max.offset(1, 0, 1)) }, entrance: xyz(h.outside), status: 'done', adopted: true }
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
  return JSON.stringify(f) !== before
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
    const work = fac.kind === 'farm' ? { to_till: s.till.length, to_sow: s.sow.length } : { to_plant: s.plant.length }
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
          crops[crop] ??= { planted: 0, ripe: 0, ja: jaOf(crop) }
          crops[crop].planted++
          if (isRipe(b)) crops[crop].ripe++
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
export const TREES = { min: 1, max: 32 }
export const SPACING = { min: TREE_GAP, max: 6 } // 苗木どうしの間（farming.mjs の TREE_GAP 以上: 植える規則と同じ）
const USABLE_SHARE = 0.6 // 畑の四角のうち耕せるマスがこれ未満なら、そこには作れない
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

// 畑: 幅 x 奥行きの四角（向きは両方試す）のうち、耕せる地面が同じ高さ（±1）に一番多く並ぶ所
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
        if (!best || level.length > best.cells.length) best = { cells: level, size: w * d }
      }
    }
  }
  if (!best || best.cells.length < best.size * USABLE_SHARE) {
    const most = best ? best.cells.length : 0
    throw new FacilityError(`not enough flat dirt or grass for a ${width}x${depth} farm in these zones (at most ${most} of ${width * depth} cells): choose a smaller size or other zones`)
  }
  return best.cells.map((c) => ({ x: c.cell.x, y: c.cell.y - 1, z: c.cell.z })) // 耕すのは地面のブロック
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
  if (best.length < count) throw new FacilityError(`only ${best.length} places for saplings ${spacing} apart in these zones (asked ${count}): plant fewer, closer (spacing ${SPACING.min}), or choose more zones`)
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
    layout = { crop, cells: farmLayout(bot, cells, area, side('width'), side('depth')) }
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
  // 採掘場の範囲は地表の輪（4 x 4）。坑道は地下なので、上の区画はほかの施設に使える
  const e = layout.cells[0]
  const box = kind === 'mine' ? boxOf(RING.map(([dx, dz]) => ({ x: e.x + dx, y: e.y, z: e.z + dz }))) : boxOf(layout.cells)
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
export function layoutStatus (bot, fac) {
  const out = { total: fac.layout.cells.length, done: 0, till: [], sow: [], plant: [], unseen: 0 }
  for (const c of fac.layout.cells) {
    const p = new Vec3(c.x, c.y, c.z)
    const b = bot.blockAt(p)
    if (!b) { out.unseen++; continue }
    if (fac.kind === 'farm') {
      const above = bot.blockAt(p.offset(0, 1, 0))
      if (b.name === 'farmland' && cropByBlock(above?.name) === fac.layout.crop) out.done++
      else if (b.name === 'farmland') out.sow.push(p)
      else out.till.push(p)
    } else {
      if (SAPLING.test(b.name) || isLog(b.name)) out.done++
      else out.plant.push(p)
    }
  }
  return out
}

// 設計した施設の状態を、今のマスから直す（planned → building → done）。変わったら真
export function updateStatus (bot, fac) {
  if (!fac.layout) return false
  const s = fac.kind === 'mine' ? mineStatus(bot, fac) : layoutStatus(bot, fac)
  if (s.unseen) return false
  const status = s.done === s.total ? 'done' : s.done > 0 ? 'building' : 'planned'
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

// 家のまわりの、設計した畑の外の、上が空いた耕地（前からある畑の空き）
function homeFreeFarmland (bot, state) {
  const home = state.home
  const id = bot.registry?.blocksByName?.farmland?.id
  if (!home || id == null || !bot.blockAt(home.inside)) return []
  const designed = designedFarms(state)
  return bot.findBlocks({ point: home.inside, matching: id, maxDistance: FARM_SEARCH, count: 256 })
    .filter((p) => bot.blockAt(p.offset(0, 1, 0))?.name === 'air' && !designed.some((d) => inBox(d.box, p)))
}

// 1 つの畑の、その作物: 植わった位置（作物のブロック）から { planted, ripe, ages, pos, free }
function measureCrops (bot, crop, positions, free) {
  const mature = CROPS[crop].mature
  const ages = positions.map((p) => ageOf(bot.blockAt(p)))
  const ripe = positions.filter((_, i) => ages[i] >= mature)
  const pos = ripe[0] ?? positions[0]
  return {
    planted: positions.length,
    ripe: ripe.length,
    ages: ages.length ? [Math.min(...ages), Math.max(...ages)] : null,
    free,
    pos: pos ? { x: pos.x, y: pos.y, z: pos.z } : null
  }
}

// その作物の畑の合計: { planted, ripe, ages, mature, free, pos, remembered, farms: [{ name, planted, ripe,
// free, remembered }] }。free は、まだまける・耕せるマス（設計した畑の残りと、家の畑の空いた耕地）。
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
    if (s.unseen === s.total) { recall(fac.name); continue }
    const positions = fac.layout.cells.map((c) => new Vec3(c.x, c.y + 1, c.z))
      .filter((p) => bot.blockAt(p.offset(0, -1, 0))?.name === 'farmland' && cropByBlock(bot.blockAt(p)?.name) === crop)
    keep(fac.name, measureCrops(bot, crop, positions, s.till.length + s.sow.length))
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
    ages: planted.length ? [Math.min(...planted.map((f) => f.ages[0])), Math.max(...planted.map((f) => f.ages[1]))] : null,
    mature: CROPS[crop].mature,
    free: farms.reduce((n, f) => n + f.free, 0),
    pos: at?.pos ?? null,
    remembered: farms.length > 0 && farms.every((f) => f.remembered),
    farms: farms.map(({ name, planted, ripe, free, remembered }) => ({ name, planted, ripe, free, ...(remembered ? { remembered } : {}) }))
  }
}

// 畑の作物ごとのまとめ（観測の farm）: { wheat: { planted, ripe, ages, mature, free, farms } }（植わっているか、
// 設計した畑がある作物だけ）
export function farmSummary (bot, state) {
  const out = {}
  for (const crop of Object.keys(CROPS)) {
    const f = cropFarms(bot, state, crop)
    if (!f.planted && !designedFarms(state, crop).length) continue
    out[crop] = { planted: f.planted, ripe: f.ripe, ages: f.ages, mature: f.mature, free: f.free, farms: f.farms.map((x) => x.name), ...(f.remembered ? { remembered: true } : {}) }
  }
  return out
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
    for (const p of layoutStatus(bot, fac)[kind] ?? []) cells.push({ pos: p, facility: fac.name })
  }
  if (kind === 'sow' && !facility) {
    for (const p of homeFreeFarmland(bot, state)) cells.push({ pos: p, facility: HOME_FARM })
  }
  return cells.sort((a, b) => a.pos.distanceTo(me) - b.pos.distanceTo(me))[0] ?? null
}

// ---- 採掘場（設計書 47 §16）: 4 x 4 の輪を回って下りるらせん階段、下の部屋、そこから横へ延びる坑道 ----
// セルは掘って空ける順に並ぶ: { x, y, z, h, part }（y は足の高さ、h は空ける高さ: 階段 3、部屋と坑道 2）。
// 階段は 1 周 12 段なので、上の段と下の段の空ける所は重ならない（床が残る）

export const MINE_BOTTOM = { min: -58, max: 100 } // 1.21.4 の岩盤は y -64〜-60
export const MINE_MIN_STEPS = 4
export const TUNNELS = { min: 0, max: 4 }
export const TUNNEL_LENGTH = { min: 4, max: 32 }
const RING = [[0, 0], [1, 0], [2, 0], [3, 0], [3, 1], [3, 2], [3, 3], [2, 3], [1, 3], [0, 3], [0, 2], [0, 1]] // 4 x 4 の外周
const FALLING = /(^|_)(sand|gravel)$|concrete_powder$/
const FLUID = /water|lava/
const SIX = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
const DIRECTIONS = { east: [1, 0], south: [0, 1], west: [-1, 0], north: [0, -1] }

const isOpen = (b) => !!b && b.boundingBox === 'empty' && !FLUID.test(b.name)
const blocksOf = (c) => Array.from({ length: c.h }, (_, i) => new Vec3(c.x, c.y + i, c.z))

// そのセルを掘ると危ない理由（なし: ''）。planned は空ける予定のマス（床や隣が予定の空きなら洞窟ではない）
export function mineDanger (bot, c, planned = new Set()) {
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
  if (!planned.has(`${floor.x},${floor.y},${floor.z}`) && below?.boundingBox !== 'block') return `a cave or a drop under ${c.x},${c.y},${c.z}`
  return ''
}

// 設計の形（向きと場所なし）から、x0,z0 の輪で入口の足 y0 から bottom まで下りるセルの並び
function mineCells (x0, z0, y0, bottom, tunnels, length) {
  const cells = []
  const steps = y0 - bottom
  for (let i = 0; i <= steps; i++) {
    const [dx, dz] = RING[i % RING.length]
    cells.push({ x: x0 + dx, y: y0 - i, z: z0 + dz, h: 3, part: 'stairs' })
  }
  // 下の部屋（4 x 4、高さ 2）: 最後の段から蛇行して、隣り合う順に
  const last = cells[cells.length - 1]
  const rows = [0, 1, 2, 3].map((dz) => [0, 1, 2, 3].map((dx) => ({ x: x0 + dx, z: z0 + dz })))
  const snake = rows.flatMap((row, i) => (i % 2 ? [...row].reverse() : row))
  const start = snake.findIndex((p) => p.x === last.x && p.z === last.z)
  const ordered = start <= snake.length / 2 ? snake : [...snake].reverse()
  for (const p of ordered) if (!(p.x === last.x && p.z === last.z)) cells.push({ ...p, y: bottom, h: 2, part: 'room' })
  // 坑道: 部屋の各辺の真ん中から外へまっすぐ
  for (const [name, [dx, dz]] of Object.entries(DIRECTIONS)) {
    if (!tunnels.includes(name)) continue
    const sx = dx > 0 ? x0 + 4 : dx < 0 ? x0 - 1 : x0 + 1
    const sz = dz > 0 ? z0 + 4 : dz < 0 ? z0 - 1 : z0 + 1
    for (let i = 0; i < length; i++) cells.push({ x: sx + dx * i, y: bottom, z: sz + dz * i, h: 2, part: `tunnel_${name}` })
  }
  return cells
}

// 空ける予定のマス（"x,y,z"）
export const plannedBlocks = (cells) => new Set(cells.flatMap((c) => blocksOf(c).map((p) => `${p.x},${p.y},${p.z}`)))
export const mineBlocks = blocksOf

// 危なくないセルの並びの長さ（最初の危ないセルまで）と、その理由
function safePrefix (bot, cells) {
  const planned = plannedBlocks(cells)
  for (let i = 0; i < cells.length; i++) {
    const why = mineDanger(bot, cells[i], planned)
    if (why) return { n: i, why }
  }
  return { n: cells.length, why: '' }
}

// 採掘場の設計: 区画の中で、入口から bottom_y まで安全に下りられる輪（中心に近い順）。坑道は安全な向きから
function mineLayout (bot, state, area, name, design) {
  const bottom = Number(design.bottom_y)
  if (!Number.isInteger(bottom) || bottom < MINE_BOTTOM.min || bottom > MINE_BOTTOM.max) throw new FacilityError(`bottom_y must be ${MINE_BOTTOM.min}-${MINE_BOTTOM.max}`)
  const count = Number(design.tunnels ?? 0)
  if (!Number.isInteger(count) || count < TUNNELS.min || count > TUNNELS.max) throw new FacilityError(`tunnels must be ${TUNNELS.min}-${TUNNELS.max}`)
  const length = Number(design.tunnel_length ?? 16)
  if (count && (!Number.isInteger(length) || length < TUNNEL_LENGTH.min || length > TUNNEL_LENGTH.max)) throw new FacilityError(`tunnel_length must be ${TUNNEL_LENGTH.min}-${TUNNEL_LENGTH.max}`)
  const y0ref = state.home ? state.home.inside.y : Math.floor(bot.entity.position.y)
  const others = Object.values(state.facilities ?? {}).filter((x) => x.name !== name && x.kind !== 'home')
  const cx = (area.min_x + area.max_x) / 2
  const cz = (area.min_z + area.max_z) / 2
  const offsets = []
  for (let x0 = area.min_x; x0 + 3 <= area.max_x; x0++) for (let z0 = area.min_z; z0 + 3 <= area.max_z; z0++) offsets.push([x0, z0])
  offsets.sort((a, b) => Math.hypot(a[0] + 1.5 - cx, a[1] + 1.5 - cz) - Math.hypot(b[0] + 1.5 - cx, b[1] + 1.5 - cz))
  if (!offsets.length) throw new FacilityError('the zones are smaller than the 4 x 4 shaft')
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
    const safe = safePrefix(bot, shaft)
    if (safe.n < shaft.length) {
      const y = shaft[Math.max(0, safe.n - 1)].y
      if (!deepest || y < deepest.y) deepest = { y, why: safe.why }
      continue
    }
    // 坑道: 安全に掘り切れる向きを count 本（東、南、西、北の順に試す）
    const ok = Object.keys(DIRECTIONS).filter((d) => {
      const t = mineCells(x0, z0, y0, bottom, [d], length).filter((c) => c.part.startsWith('tunnel'))
      return safePrefix(bot, [...shaft, ...t]).n === shaft.length + t.length
    })
    if (ok.length < count) {
      if (!deepest || bottom <= deepest.y) deepest = { y: bottom, why: `only ${ok.length} tunnels of ${length} are safe here` }
      continue
    }
    return { bottom_y: bottom, tunnels: ok.slice(0, count), tunnel_length: count ? length : 0, cells: mineCells(x0, z0, y0, bottom, ok.slice(0, count), length) }
  }
  if (unloaded > offsets.length / 2) throw new FacilityError('the area is not loaded (too far from the bot): go near it before designing', { world: true })
  if (deepest) throw new FacilityError(`no safe shaft down to y ${bottom} in these zones (the deepest safe one reaches y ${deepest.y}: ${deepest.why}): choose bottom_y ${deepest.y} or higher, fewer or shorter tunnels, or other zones`)
  throw new FacilityError('no ground for the mine entrance in these zones (houses, builds, other facilities or no solid ground)')
}

// 採掘場の今: 空いたセル、次に掘るセル（並びの最初の空いていないもの）、見えていないセル
export function mineStatus (bot, fac) {
  const out = { total: fac.layout.cells.length, done: 0, next: null, index: -1, unseen: 0 }
  fac.layout.cells.forEach((c, i) => {
    const bs = blocksOf(c).map((p) => bot.blockAt(p))
    if (bs.some((b) => !b)) { out.unseen++; return }
    if (bs.every(isOpen)) { out.done++; return }
    if (!out.next) { out.next = c; out.index = i }
  })
  return out
}

// 採掘場のセルか（空けておく所: 足場も土も置かない、整地で埋めない）
export function onMineCell (state, p) {
  return Object.values(state.facilities ?? {}).some((f) => f.kind === 'mine' && f.layout &&
    f.layout.cells.some((c) => c.x === p.x && c.z === p.z && p.y >= c.y && p.y < c.y + c.h))
}

// 採掘の途中で見つけた危ないもの: そのセルから先を設計から外し、理由を残す（黙って浅くしない）
export function stopMine (fac, index, reason) {
  const c = fac.layout.cells[index]
  fac.layout.stopped = { x: c.x, y: c.y, z: c.z, reason }
  fac.layout.cells = fac.layout.cells.slice(0, index)
}

// 下りきった採掘場（ボットに近い順の最初の 1 つ）: { name, bottom: {x,y,z}, above（ボットが底より上） } か null
export function finishedMine (bot, state) {
  const me = bot.entity.position
  return Object.values(state.facilities ?? {})
    .filter((f) => f.kind === 'mine' && f.layout && f.status === 'done' && f.layout.cells.length)
    .map((f) => {
      const stairs = f.layout.cells.filter((c) => c.part === 'stairs')
      const b = stairs[stairs.length - 1]
      return { name: f.name, bottom: { x: b.x, y: b.y, z: b.z }, above: me.y > b.y + 2, d: Math.hypot(b.x - me.x, b.z - me.z) }
    })
    .sort((a, b) => a.d - b.d)[0] ?? null
}
