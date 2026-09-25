// 家（完成した建物）と、建築計画・家・目標の永続化。
//
// これらはふだんメモリにしかなく、ブリッジはよく再起動する: data/state.json に保存し、再起動しても
// 家、建築の途中経過、ボットがしていることを忘れないようにする。
//
// Mineflayer-pathfinder はドアを扱えない（canOpenDoors はおかしな動きをするので切っている）ので、
// 出入りは明示的に行う: ドアの前のセルまで歩き、開け、通り抜け、後ろで閉める。

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vec3Pkg from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
import { BuildPlan, placeOne } from './build.mjs'
import { walkTo } from './move.mjs'
import { nearbyEntities, isHostile, isLog, isPlanks } from './observe.mjs'

const { Vec3 } = vec3Pkg
const { goals } = pathfinderPkg
const DATA_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data', 'state.json')
const DOOR_TOGGLE_TIMEOUT_MS = 2000
const WALK_THROUGH_TIMEOUT_MS = 3000
// ドアにここまで近い敵対モブがいたらボットは中にとどまる: クリーパーが家までついて来てドアの前で
// 待ち、朝ボットが外に出たところで入口（と家の角）を吹き飛ばした。
const DOOR_DANGER_RADIUS = 8

const toVec = (p) => new Vec3(p.x, p.y, p.z)
const plain = (v) => ({ x: v.x, y: v.y, z: v.z })

const homeToJSON = (h) => ({
  name: h.name ?? null,
  design: h.design ?? null,
  door: plain(h.door),
  inside: plain(h.inside),
  outside: plain(h.outside),
  min: plain(h.min),
  max: plain(h.max),
  bed: h.bed ? plain(h.bed) : null,
  breach: h.breach
})

const homeFromJSON = (h) => ({ name: h.name ?? null, design: h.design ?? null, door: toVec(h.door), inside: toVec(h.inside), outside: toVec(h.outside), min: toVec(h.min), max: toVec(h.max), bed: h.bed ? toVec(h.bed) : null, breach: h.breach ?? [] })

export function saveState (state) {
  const data = {
    plan: state.plan && { ...state.plan.toJSON() },
    home: state.home && homeToJSON(state.home),
    formerHomes: (state.formerHomes ?? []).map(homeToJSON),
    survey: state.survey ?? null,
    goal: state.goal,
    memory: state.memory
  }
  fs.mkdirSync(path.dirname(DATA_FILE), { recursive: true })
  fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 1))
}

export function loadState (state) {
  if (!fs.existsSync(DATA_FILE)) return
  const data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'))
  if (data.plan) state.plan = BuildPlan.fromJSON(data.plan)
  if (data.home) state.home = homeFromJSON(data.home)
  if (data.formerHomes) state.formerHomes = data.formerHomes.map(homeFromJSON)
  if (data.survey) state.survey = data.survey
  if (data.goal) state.goal = data.goal
  if (data.memory) state.memory = { ...state.memory, ...data.memory }
}

// 完成した計画が家になる: ドアの下半分、そのすぐ内側と外側のセル、内部（敷地から壁を除いた部分）。
export function homeFromPlan (plan) {
  const doors = plan.blocks.filter((b) => b.block === 'door').sort((a, b) => a.y - b.y)
  if (!doors.length) return null
  const d = doors[0]
  const { width, depth } = plan.size
  const inward = d.x === 0 ? [1, 0] : d.x === width - 1 ? [-1, 0] : d.z === 0 ? [0, 1] : [0, -1]
  const door = plan.worldPos(d)
  return {
    name: plan.design?.name ?? null,
    design: plan.design ?? null,
    door,
    inside: door.offset(inward[0], 0, inward[1]),
    outside: door.offset(-inward[0], 0, -inward[1]),
    min: plan.origin.offset(1, 0, 1),
    max: plan.origin.offset(width - 2, 0, depth - 2),
    bed: null,
    breach: [] // 出口のために掘ってまだ戻していない壁のブロック: { x, y, z, block }
  }
}

// 計画が建ち終わったら、それが家になる。別の場所に建てた家なら引っ越し: 今の家は前の家として
// 残す（壊さない。チェストの中身は記憶に残り、取り出す元になる）。家が変わったら true
export function settleHome (state, bot) {
  const plan = state.plan
  if (!plan?.origin || !plan.status(bot).complete) return false
  const built = homeFromPlan(plan)
  if (!built || (state.home && state.home.door.equals(built.door))) return false
  if (state.home) {
    state.formerHomes ??= []
    state.formerHomes.push(state.home)
  }
  state.home = built
  return true
}

export function isInside (bot, home) {
  if (!home) return false
  const p = bot.entity.position.floored()
  return p.x >= home.min.x && p.x <= home.max.x && p.z >= home.min.z && p.z <= home.max.z &&
    p.y >= home.min.y && p.y <= home.min.y + 1
}

// ボットが中にいる家（今の家か前の家）。前の家のベッドで寝たことがあると、死んだ後はそこで
// 生き返る（town4d: 閉じた前の家から出られず、どこへも経路がなかった）
export function houseAround (bot, state) {
  return [state.home, ...(state.formerHomes ?? [])].find((h) => isInside(bot, h)) ?? null
}

// ドアが閉まっていれば（壁に穴もなければ）、壁の外のモブは中のボットに届かない。
export function shelteredFrom (bot, home, entity) {
  if (!isInside(bot, home) || isDoorOpen(bot, home) || home.breach.length) return false
  const p = entity.position.floored()
  return !(p.x >= home.min.x && p.x <= home.max.x && p.z >= home.min.z && p.z <= home.max.z)
}

const isTrue = (v) => v === true || v === 'true'

export function isDoorOpen (bot, home) {
  const block = bot.blockAt(home.door)
  return !!block && isTrue(block.getProperties().open)
}

async function setDoor (bot, home, open) {
  const block = bot.blockAt(home.door)
  if (!block || !block.name.endsWith('_door')) throw new Error(`no door at ${home.door}`)
  if (isDoorOpen(bot, home) === open) return
  await bot.lookAt(home.door.offset(0.5, 0.5, 0.5), true)
  await bot.activateBlock(block)
  const start = Date.now()
  while (isDoorOpen(bot, home) !== open) {
    if (Date.now() - start > DOOR_TOGGLE_TIMEOUT_MS) throw new Error(`door did not ${open ? 'open' : 'close'}`)
    await bot.waitForTicks(1)
  }
}

// `cell`（1ブロック先、開いたドアの向こう）の中心までまっすぐ歩く。
async function walkInto (bot, cell) {
  const target = cell.offset(0.5, 0, 0.5)
  await bot.lookAt(target.offset(0, bot.entity.eyeHeight, 0), true)
  bot.setControlState('forward', true)
  const start = Date.now()
  try {
    while (bot.entity.position.xzDistanceTo(target) > 0.35) {
      if (Date.now() - start > WALK_THROUGH_TIMEOUT_MS) throw new Error(`could not walk to ${cell}`)
      await bot.lookAt(target.offset(0, bot.entity.eyeHeight, 0), true)
      await bot.waitForTicks(1)
    }
  } finally {
    bot.clearControlStates()
  }
}

export async function enterHome (bot, home, signal) {
  if (!isInside(bot, home)) {
    await walkTo(bot, new goals.GoalBlock(home.outside.x, home.outside.y, home.outside.z), signal)
    await setDoor(bot, home, true)
    await walkInto(bot, home.door)
    await walkInto(bot, home.inside)
  }
  await setDoor(bot, home, false)
}

// 外のドアの近くで待っている敵対モブ
export function dangerOutside (bot, home) {
  if (!home) return []
  return nearbyEntities(bot).filter(({ e }) => isHostile(bot, e) && e.position.distanceTo(home.outside) <= DOOR_DANGER_RADIUS &&
    !(e.position.x >= home.min.x && e.position.x < home.max.x + 1 && e.position.z >= home.min.z && e.position.z < home.max.z + 1))
}

const inHome = (home, p) => p.x >= home.min.x && p.x <= home.max.x && p.z >= home.min.z && p.z <= home.max.z

// 家、前の家、または計画中の家（`margin` だけ広げる）の一部: 掘らないし、何も置かない。
// すべてを確かめる: 新しい計画や引っ越しのせいで、建てた家が守られなくなってはいけない。
const HOME_HEIGHT = 5 // 壁は高さ 4 まで、その上に屋根
export function inHouse (state, p, margin = 0) {
  if ([state.home, ...(state.formerHomes ?? [])].some((h) => h && inBuilt(h, p, margin))) return true
  const o = state.plan?.origin
  if (!o) return false
  const { width, depth, height } = state.plan.size
  return p.x >= o.x - margin && p.x < o.x + width + margin && p.z >= o.z - margin && p.z < o.z + depth + margin &&
    p.y >= o.y - 1 && p.y <= o.y + height
}

function inBuilt (h, p, margin) {
  return p.x >= h.min.x - 1 - margin && p.x <= h.max.x + 1 + margin && p.z >= h.min.z - 1 - margin &&
      p.z <= h.max.z + 1 + margin && p.y >= h.min.y - 1 && p.y <= h.min.y + HOME_HEIGHT
}

// ベッドはドアからまっすぐ奥に置く: 足側は内側のセルの1つ先、頭側はさらに1つ先
export function bedSpot (bot, home) {
  const inward = home.inside.minus(home.door)
  const foot = home.inside.plus(inward)
  const head = foot.plus(inward)
  const free = (p) => inHome(home, p) && bot.blockAt(p)?.name === 'air' && bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block'
  return free(foot) && free(head) ? { foot, inward } : null
}

// チェストを置く場所: ドアからベッドまでの列（歩いて寝られるよう空けておく）から外れた内部のセル。
// ドアから遠い順
export function chestSpot (bot, home) {
  const inward = home.inside.minus(home.door)
  const line = [0, 1, 2].map((i) => home.inside.plus(inward.scaled(i)))
  const cells = []
  for (let x = home.min.x; x <= home.max.x; x++) {
    for (let z = home.min.z; z <= home.max.z; z++) cells.push(home.min.offset(x - home.min.x, 0, z - home.min.z))
  }
  return cells
    .filter((p) => !line.some((l) => l.equals(p)) && bot.blockAt(p)?.name === 'air' && bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block')
    .sort((a, b) => b.distanceTo(home.door) - a.distanceTo(home.door))[0] ?? null
}

const isWallBlock = (block) => !!block && (isPlanks(block.name) || isLog(block.name))
const standable = (bot, p) => bot.blockAt(p)?.boundingBox === 'empty' && bot.blockAt(p.offset(0, 1, 0))?.boundingBox === 'empty' &&
  bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block'

// 壁を掘って出口にできる場所。各辺で一番よいセル: 壁のブロックが2つともあり、内側と外側に立つ
// 場所があり、ドアではない。与えられた敵対モブから遠い順。
export function exitSpots (bot, home, hostiles) {
  const y = home.min.y
  const span = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i)
  const sides = [
    { side: 'west', dx: -1, dz: 0, cells: span(home.min.z, home.max.z).map((z) => [home.min.x - 1, z]) },
    { side: 'east', dx: 1, dz: 0, cells: span(home.min.z, home.max.z).map((z) => [home.max.x + 1, z]) },
    { side: 'north', dx: 0, dz: -1, cells: span(home.min.x, home.max.x).map((x) => [x, home.min.z - 1]) },
    { side: 'south', dx: 0, dz: 1, cells: span(home.min.x, home.max.x).map((x) => [x, home.max.z + 1]) }
  ]
  const out = []
  for (const { side, dx, dz, cells } of sides) {
    let best = null
    for (const [x, z] of cells) {
      const wall = new Vec3(x, y, z)
      if (x === home.door.x && z === home.door.z) continue
      if (!isWallBlock(bot.blockAt(wall)) || !isWallBlock(bot.blockAt(wall.offset(0, 1, 0)))) continue
      const step = wall.offset(dx, 0, dz)
      if (!standable(bot, wall.offset(-dx, 0, -dz)) || !standable(bot, step)) continue
      const center = step.offset(0.5, 0, 0.5)
      const distance = Math.min(Infinity, ...hostiles.map((h) => h.position.distanceTo(center)))
      if (!best || distance > best.distance) best = { side, wall, step, distance }
    }
    if (best) out.push(best)
  }
  return out.sort((a, b) => b.distance - a.distance)
}

// `spot`（exitSpots から）の壁を内側から掘って出口にする。掘るブロックは先に記録するので、中断で
// 残った穴もわかり、直される（repairWall）。
export async function digExit (bot, home, spot, signal) {
  const inside = spot.wall.plus(spot.wall.minus(spot.step))
  if (bot.entity.position.floored().xzDistanceTo(inside) > 0) {
    await walkTo(bot, new goals.GoalBlock(inside.x, inside.y, inside.z), signal)
  }
  for (const p of [spot.wall.offset(0, 1, 0), spot.wall]) {
    signal.throwIfAborted()
    const block = bot.blockAt(p)
    if (!isWallBlock(block)) continue
    home.breach.push({ x: p.x, y: p.y, z: p.z, block: block.name })
    await bot.dig(block, true)
  }
}

// 掘った出口から歩いて出る
export async function stepOut (bot, spot, signal) {
  signal.throwIfAborted()
  await walkInto(bot, spot.wall)
  await walkInto(bot, spot.step)
}

// 出口のために掘った壁のブロックを戻す（下から: 上のブロックはその上に載る）
export async function repairWall (bot, home, signal) {
  for (const b of [...home.breach].sort((a, c) => a.y - c.y)) {
    const pos = new Vec3(b.x, b.y, b.z)
    if (!isWallBlock(bot.blockAt(pos))) {
      const kind = isLog(b.block) ? 'log' : 'planks'
      if (!bot.inventory.items().some((i) => (kind === 'log' ? isLog : isPlanks)(i.name))) throw new Error(`no ${kind} to close the wall at ${pos}`)
      await placeOne(bot, { worldPos: () => pos }, { block: kind }, signal)
    }
    home.breach.splice(home.breach.indexOf(b), 1)
  }
}

export const hasBed = (bot, home) => !!home?.bed && !!bot.blockAt(home.bed)?.name.endsWith('_bed')

// `confront`: ドアの前で待つものと戦いに出る（cleared の目標）ので、それらがいても止めない
export async function leaveHome (bot, home, signal, { confront = false } = {}) {
  if (!isInside(bot, home)) return
  const danger = dangerOutside(bot, home)
  if (danger.length && !confront) throw new Error(`staying inside: ${danger.map(({ e }) => e.name).join(', ')} waiting outside the door`)
  if (bot.entity.position.floored().xzDistanceTo(home.inside) > 0) {
    // 閉じた家の中の経路移動は問題ない: 内部に障害物はない
    await walkTo(bot, new goals.GoalBlock(home.inside.x, home.inside.y, home.inside.z), signal)
  }
  await setDoor(bot, home, true)
  await walkInto(bot, home.door)
  await walkInto(bot, home.outside)
  await setDoor(bot, home, false)
}
