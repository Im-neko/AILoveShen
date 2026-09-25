// Gemini が設計した名前付きの建物（docs/design/25_builds.md）。
//
// ブロックの並び（Python の BuildDesign が形から展開したもの）と置き場所（アンカー）を受け取り、
// 原点を決め、守るもの（家のドア、ベッド、チェスト、家の室内と床、ほかの建物）に掛かっていないかを
// 確かめてから、`state.builds[name]` に持つ。家の計画（state.plan）とは別: できても引っ越しにならない。
//
// 座標: 建物の y=0 は床の層（家の床と同じ高さ、地面の一番上のブロックの高さ）。立つ高さは y=1。

import vec3Pkg from 'vec3'
import { BuildPlan } from './build.mjs'

const { Vec3 } = vec3Pkg

export const MAX_BUILD_DISTANCE = 48 // 家からの距離（水平）
const NEAR_HOME_RADIUS = 24
const NEAR_HOME_GAP = 3 // 家や建物とのすき間
const HOME_HEIGHT = 5
const PROTECTED = /(_door|_bed|chest|furnace|crafting_table|barrel)$/
const WALL = /(_planks|_log|_wood|cobblestone)$/
const NATURAL_GROUND = /^(dirt|grass_block|coarse_dirt|podzol|stone|granite|diorite|andesite|gravel|sand|clay|mud|tuff|deepslate)$/
const ANCHORS = ['home:east', 'home:west', 'home:north', 'home:south', 'near_home']

export class BuildError extends Error {}

// 名前付きの建物を登録する。原点を決めて確かめ、state.builds[name] に入れる。理由つきの BuildError
export function registerBuild (bot, state, { name, blocks, size, anchor, purpose = '' }) {
  if (!/^[a-z0-9_]{1,32}$/.test(name ?? '')) throw new BuildError(`bad build name ${JSON.stringify(name)}`)
  if (!ANCHORS.includes(anchor)) throw new BuildError(`unknown anchor ${anchor} (one of ${ANCHORS.join(', ')})`)
  if (!state.home) throw new BuildError('there is no home yet: build the house first')
  state.builds ??= {}
  if (state.builds[name]) throw new BuildError(`a build named ${name} already exists`)
  const plan = new BuildPlan({ blocks, width: size.width, depth: size.depth, height: size.height, design: { name, purpose, anchor }, kind: 'build' })
  plan.origin = anchor === 'near_home' ? nearHomeOrigin(bot, state, plan) : homeSideOrigin(state.home, plan, anchor.slice(5))
  checkPlacement(bot, state, plan, anchor)
  state.builds[name] = plan
  return plan
}

// 増築: 建物の端の面を家の外壁の列に重ね、壁に沿って中央をそろえる。床の高さは家の床
export function homeSideOrigin (home, plan, side) {
  const { width, depth } = plan.size
  const wallMin = home.min.offset(-1, 0, -1)
  const wallMax = home.max.offset(1, 0, 1)
  const y = home.min.y - 1
  const midX = Math.floor((wallMin.x + wallMax.x) / 2) - Math.floor((width - 1) / 2)
  const midZ = Math.floor((wallMin.z + wallMax.z) / 2) - Math.floor((depth - 1) / 2)
  switch (side) {
    case 'east': return new Vec3(wallMax.x, y, midZ)
    case 'west': return new Vec3(wallMin.x - (width - 1), y, midZ)
    case 'south': return new Vec3(midX, y, wallMax.z)
    case 'north': return new Vec3(midX, y, wallMin.z - (depth - 1))
  }
  throw new BuildError(`unknown side ${side}`)
}

// 家の近くの平らな場所: 足元が地面のブロックで、その上が空いている（植物・葉は刈る）。家とほかの建物から離す
function nearHomeOrigin (bot, state, plan) {
  const { width, depth, height } = plan.size
  const home = state.home
  const center = home.min.plus(home.max).scaled(0.5).floored()
  const taken = [homeBox(home), ...Object.values(state.builds ?? {}).map(buildBox)]
  const candidates = []
  for (let dx = -NEAR_HOME_RADIUS; dx <= NEAR_HOME_RADIUS; dx++) {
    for (let dz = -NEAR_HOME_RADIUS; dz <= NEAR_HOME_RADIUS; dz++) {
      const box = { min: new Vec3(center.x + dx, 0, center.z + dz), max: new Vec3(center.x + dx + width - 1, 0, center.z + dz + depth - 1) }
      if (taken.some((t) => overlaps2d(box, t, NEAR_HOME_GAP))) continue
      candidates.push({ x: center.x + dx, z: center.z + dz, d: dx * dx + dz * dz })
    }
  }
  candidates.sort((a, b) => a.d - b.d)
  for (const c of candidates) {
    for (let dy = -4; dy <= 4; dy++) {
      const floor = new Vec3(c.x, home.min.y - 1 + dy, c.z)
      if (flatAndFree(bot, floor, width, depth, height)) return floor
    }
  }
  throw new BuildError(`no flat ${width}x${depth} place within ${NEAR_HOME_RADIUS} blocks of the home`)
}

function flatAndFree (bot, floor, width, depth, height) {
  for (let x = 0; x < width; x++) {
    for (let z = 0; z < depth; z++) {
      const ground = bot.blockAt(floor.offset(x, 0, z))
      if (!ground || ground.boundingBox !== 'block' || /water|lava|_leaves|_log|_planks/.test(ground.name)) return false
      for (let y = 1; y < height; y++) {
        const b = bot.blockAt(floor.offset(x, y, z))
        if (!b || (b.boundingBox !== 'empty' && !b.name.endsWith('_leaves')) || /water|lava/.test(b.name)) return false
      }
    }
  }
  return true
}

// 守るものに掛かっていないか。理由つきの BuildError
function checkPlacement (bot, state, plan, anchor) {
  const home = state.home
  const box = buildBox(plan)
  const center = home.min.plus(home.max).scaled(0.5)
  const far = Math.max(Math.abs(box.min.x - center.x), Math.abs(box.max.x - center.x), Math.abs(box.min.z - center.z), Math.abs(box.max.z - center.z))
  if (far > MAX_BUILD_DISTANCE) throw new BuildError(`the build would reach ${Math.round(far)} blocks from the home (at most ${MAX_BUILD_DISTANCE})`)
  for (const [other, p] of Object.entries(state.builds ?? {})) {
    if (overlaps3d(box, buildBox(p))) throw new BuildError(`it would overlap the build ${other}`)
  }
  if (anchor === 'near_home' && overlaps2d(box, homeBox(home), 0)) throw new BuildError('it would overlap the home')
  for (const b of plan.blocks) {
    const p = plan.worldPos(b)
    const rel = `(${b.x},${b.y},${b.z})`
    if (inHomeRoom(home, p)) throw new BuildError(`the block ${rel} would be inside the home's room: keep the build outside the home's walls`)
    if (b.block === 'air' && inHomeFloor(home, p)) throw new BuildError(`the block ${rel} would dig the home's floor`)
    const found = bot.blockAt(p)
    if (found && PROTECTED.test(found.name) && !(b.block === 'door' && found.name.endsWith('_door'))) {
      throw new BuildError(`the block ${rel} would replace the ${found.name} at ${p.x},${p.y},${p.z}: move the build or change that block`)
    }
    if (!found) continue
    // ドアは掘って置けない（置く前に掘るのは植物と自然のブロックだけ）: 家の壁の中のドアは置けずに詰まる
    if (b.block === 'door' && found.boundingBox === 'block' && !NATURAL_GROUND.test(found.name)) {
      throw new BuildError(`the door ${rel} is inside the ${found.name} of the home's wall: open the wall with clear and put the door on an outside wall`)
    }
    // 木や水は建てる途中で掘れない・埋められない
    if (/_log$|water|lava/.test(found.name) && !(b.block === 'log' && found.name.endsWith('_log'))) {
      throw new BuildError(`there is ${found.name} at ${rel} on the ${anchor} side: choose another side or anchor`)
    }
  }
}

// 建物の中で掘ってよいマス: まだできていない建物が「空ける」と書いた、家の壁のブロック（板材・原木・丸石）
export function buildAllowsDig (state, p, block) {
  if (!block || !WALL.test(block.name)) return false
  return Object.values(state.builds ?? {}).some((plan) => plan.origin && plan.blocks.some((b) => b.block === 'air' && plan.worldPos(b).equals(p)))
}

// p が建物（どれか）の範囲か: 採掘や足場の対象から外す
export function inBuilds (state, p, margin = 0) {
  return Object.values(state.builds ?? {}).some((plan) => {
    if (!plan.origin) return false
    const box = buildBox(plan)
    return p.x >= box.min.x - margin && p.x <= box.max.x + margin && p.z >= box.min.z - margin && p.z <= box.max.z + margin &&
      p.y >= box.min.y && p.y <= box.max.y
  })
}

export function buildsStatus (bot, state) {
  return Object.entries(state.builds ?? {}).map(([name, plan]) => {
    const s = plan.status(bot)
    return { name, purpose: plan.design?.purpose ?? '', anchor: plan.design?.anchor ?? null, placed: s.placed, total: s.total, complete: s.complete, origin: s.origin }
  })
}

// 増築ができたら、家の室内を床の高さで塗りつぶして広げる（ドアの内側のセルから、壁で閉じている範囲）。
// 外に漏れたら（閉じていない）広げない。広げたら true
export function growHome (bot, home, limit = 32) {
  const y = home.min.y
  const start = home.inside
  const seen = new Set()
  const cells = []
  const queue = [start]
  const key = (p) => `${p.x},${p.z}`
  const passable = (p) => {
    const b = bot.blockAt(p)
    const above = bot.blockAt(p.offset(0, 1, 0))
    if (b?.name.endsWith('_door')) return false // ドアの外は家ではない
    return !!b && !!above && (b.boundingBox === 'empty' || b.name.endsWith('_bed') || PROTECTED.test(b.name) && !b.name.endsWith('_door')) &&
      above.boundingBox !== 'block' && bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block'
  }
  while (queue.length) {
    const p = queue.shift()
    if (seen.has(key(p))) continue
    seen.add(key(p))
    if (Math.abs(p.x - start.x) > limit || Math.abs(p.z - start.z) > limit) return false // 閉じていない
    if (!passable(p)) continue
    if (!roofed(bot, p)) return false // 屋根がない: 外
    cells.push(p)
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) queue.push(new Vec3(p.x + dx, y, p.z + dz))
  }
  if (cells.length <= (home.cells?.length ?? (home.max.x - home.min.x + 1) * (home.max.z - home.min.z + 1))) return false
  home.cells = cells.map((p) => ({ x: p.x, z: p.z }))
  home.min = new Vec3(Math.min(...cells.map((p) => p.x)), y, Math.min(...cells.map((p) => p.z)))
  home.max = new Vec3(Math.max(...cells.map((p) => p.x)), y, Math.max(...cells.map((p) => p.z)))
  return true
}

function roofed (bot, p) {
  for (let dy = 2; dy <= HOME_HEIGHT + 3; dy++) {
    if (bot.blockAt(p.offset(0, dy, 0))?.boundingBox === 'block') return true
  }
  return false
}

export function buildBox (plan) {
  const xs = plan.blocks.map((b) => b.x)
  const ys = plan.blocks.map((b) => b.y)
  const zs = plan.blocks.map((b) => b.z)
  const o = plan.origin
  return { min: o.offset(Math.min(...xs), Math.min(...ys), Math.min(...zs)), max: o.offset(Math.max(...xs), Math.max(...ys), Math.max(...zs)) }
}

function homeBox (home) {
  return { min: home.min.offset(-1, -1, -1), max: home.max.offset(1, HOME_HEIGHT, 1) }
}

function inHomeRoom (home, p) {
  return isHomeCell(home, p) && p.y >= home.min.y && p.y < home.min.y + HOME_HEIGHT - 1
}

function inHomeFloor (home, p) {
  return p.x >= home.min.x - 1 && p.x <= home.max.x + 1 && p.z >= home.min.z - 1 && p.z <= home.max.z + 1 && p.y < home.min.y
}

export function isHomeCell (home, p) {
  if (home.cells) return home.cells.some((c) => c.x === p.x && c.z === p.z)
  return p.x >= home.min.x && p.x <= home.max.x && p.z >= home.min.z && p.z <= home.max.z
}

const overlaps2d = (a, b, gap) => a.min.x <= b.max.x + gap && a.max.x >= b.min.x - gap && a.min.z <= b.max.z + gap && a.max.z >= b.min.z - gap
const overlaps3d = (a, b) => overlaps2d(a, b, 0) && a.min.y <= b.max.y && a.max.y >= b.min.y
