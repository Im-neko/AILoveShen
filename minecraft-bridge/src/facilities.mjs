// 施設の登録（設計書 47 §5）: 家、名前付きの建物、畑、植林場、採掘場…。施設の座標を持つのはここだけ
// （Python の街の理想図は区画の範囲だけ）。state.facilities に保存する。
//
// 今ある物は施設として取り込む（adoptExisting）: 家、state.builds、家のまわりの耕地（home_farm）、
// 植えた苗木のかたまり（tree_farm_N）。取り込まないと、理想図が今の畑の上に別の区画を描き、畑を忘れる。
import vec3Pkg from 'vec3'
import { round, bearing, isLog } from './observe.mjs'
import { buildBox } from './builds.mjs'
import { cropByBlock, isRipe, SAPLING, PLANT_RING } from './farming.mjs'

const { Vec3 } = vec3Pkg

export const FACILITY_KINDS = ['home', 'build', 'farm', 'tree_farm', 'mine', 'pen', 'trap']
const TREE_GAP = 8 // 植えた苗木がこの距離より離れていれば、別の植林場
const FARM_SEARCH = PLANT_RING.max + 8 // 家から、耕地を探す範囲
const HOME_FARM = 'home_farm'

const xyz = (v) => ({ x: v.x, y: v.y, z: v.z })
const boxOf = (points, pad = 0) => ({
  min: { x: Math.min(...points.map((p) => p.x)) - pad, y: Math.min(...points.map((p) => p.y)), z: Math.min(...points.map((p) => p.z)) - pad },
  max: { x: Math.max(...points.map((p) => p.x)) + pad, y: Math.max(...points.map((p) => p.y)), z: Math.max(...points.map((p) => p.z)) + pad }
})
const inBox = (box, p) => p.x >= box.min.x && p.x <= box.max.x && p.z >= box.min.z && p.z <= box.max.z
const center = (box) => new Vec3((box.min.x + box.max.x) / 2, box.min.y, (box.min.z + box.max.z) / 2)

// 植えた苗木を、近いものどうしでかたまりにする（1 つのかたまり = 1 つの植林場）
export function plantingGroups (plantings, gap = TREE_GAP) {
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
  const farmland = farmlandAroundHome(bot, state)
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
