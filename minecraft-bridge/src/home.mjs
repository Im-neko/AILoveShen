// 家（完成した建物）と、建築計画・家・目標の永続化。
//
// これらはふだんメモリにしかなく、ブリッジはよく再起動する: data/state.json に保存し、再起動しても
// 家、建築の途中経過、ボットがしていることを忘れないようにする。
//
// 家（と前の家）の出入りは明示的に行う: ドアの前のセルまで歩き、開け、通り抜け、後ろで閉める（夜に
// 閉じこもる、ドアの前の敵を見るため。ほかのドアは歩きながら開け閉めする: doors.mjs）。

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vec3Pkg from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
import { BuildPlan, placeOne } from './build.mjs'
import { buildAllowsDig, onBuildCell, isHomeCell, buildBox } from './builds.mjs'
import { walkTo } from './move.mjs'
import { nearbyEntities, isHostile, isLog, isPlanks, dayPhase, fillItem, standingCell } from './observe.mjs'

const { Vec3 } = vec3Pkg
const { goals } = pathfinderPkg
const DATA_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data', 'state.json')
const DOOR_TOGGLE_TIMEOUT_MS = 2000
const WALK_THROUGH_TIMEOUT_MS = 3000
const DOORSTEP_DEPTH = 3 // ドアの前の床の穴は、ここまでの深さなら下から埋める
const PLACE_REACH = 4
const JUMP_OUT_MS = 1500
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
  breach: h.breach,
  cells: h.cells ?? null // 増築で広げた室内（docs/design/25_builds.md）
})

const homeFromJSON = (h) => ({ name: h.name ?? null, design: h.design ?? null, door: toVec(h.door), inside: toVec(h.inside), outside: toVec(h.outside), min: toVec(h.min), max: toVec(h.max), bed: h.bed ? toVec(h.bed) : null, breach: h.breach ?? [], ...(h.cells ? { cells: h.cells } : {}) })

export function saveState (state) {
  const data = {
    plan: state.plan && { ...state.plan.toJSON() },
    builds: Object.fromEntries(Object.entries(state.builds ?? {}).map(([name, p]) => [name, p.toJSON()])),
    home: state.home && homeToJSON(state.home),
    formerHomes: (state.formerHomes ?? []).map(homeToJSON),
    survey: state.survey ?? null,
    goal: state.goal,
    memory: state.memory,
    conditions: state.conditions ?? {}, // Gemini が書いた判定（conditions.mjs。設計書 42）
    // 植えた場所（設計書 33）。前は保存しておらず、再起動で planted が 0 から数え直しになった
    plantings: state.plantings ?? [],
    facilities: state.facilities ?? {} // 施設（facilities.mjs。設計書 47）
  }
  fs.mkdirSync(path.dirname(DATA_FILE), { recursive: true })
  fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 1))
}

export function loadState (state) {
  if (!fs.existsSync(DATA_FILE)) return
  const data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'))
  if (data.plan) state.plan = BuildPlan.fromJSON(data.plan)
  if (data.builds) state.builds = Object.fromEntries(Object.entries(data.builds).map(([name, p]) => [name, BuildPlan.fromJSON(p)]))
  if (data.home) state.home = homeFromJSON(data.home)
  if (data.formerHomes) state.formerHomes = data.formerHomes.map(homeFromJSON)
  if (data.survey) state.survey = data.survey
  if (data.goal) state.goal = data.goal
  if (data.memory) state.memory = { ...state.memory, ...data.memory }
  if (data.conditions) state.conditions = data.conditions
  if (data.plantings) state.plantings = data.plantings
  if (data.facilities) state.facilities = data.facilities
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
  return isHomeCell(home, p) && p.y >= home.min.y && p.y <= home.min.y + 1
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

// 家の前のセルの様子（行けないときの理由に添える）。ボットの足の高さも（ドアの前の床が抜けていると
// 1 段下に立ち、ドアへ登れない）
export function describeDoorstep (bot, home) {
  const name = (p) => bot.blockAt(p)?.name ?? 'unloaded'
  const o = home.outside
  const door = isDoorOpen(bot, home) ? 'open' : 'closed'
  const me = standingCell(bot)
  return `door ${door}; in front of the door: floor ${name(o.offset(0, -1, 0))}, feet ${name(o)}, head ${name(o.offset(0, 1, 0))}; ` +
    `you stand at ${me.x},${me.y},${me.z} (the doorstep is ${o.x},${o.y},${o.z})`
}

// 今の家のドアの前の床の列（外に立つセルの真下から DOORSTEP_DEPTH まで）。ここだけは家のまわりでも
// ブロックを置いてよい（足と頭のセルは置かない: ドアがふさがる）
export function isDoorstepFloor (state, p) {
  const o = state.home?.outside
  return !!o && p.x === o.x && p.z === o.z && p.y < o.y && p.y >= o.y - DOORSTEP_DEPTH
}

// ドアの前の床の抜けたセル（下から順）。空なら床はある
export function doorstepHoles (bot, home) {
  const holes = []
  for (let dy = 1; dy <= DOORSTEP_DEPTH; dy++) {
    const p = toVec(home.outside).offset(0, -dy, 0)
    const b = bot.blockAt(p)
    if (!b || b.boundingBox === 'block') break
    holes.unshift(p)
  }
  return holes
}

// 跳んで、今立っているセルに足場を置く（穴の中から 1 段上がる）
async function jumpAndPlace (bot, cell, item) {
  await bot.equip(item, 'hand')
  await bot.look(bot.entity.yaw, -Math.PI / 2, true)
  bot.setControlState('jump', true)
  try {
    const start = Date.now()
    while (bot.entity.position.y < cell.y + 1) {
      if (Date.now() - start > JUMP_OUT_MS) throw new Error(`could not jump up from the hole at ${cell}`)
      await bot.waitForTicks(1)
    }
    await bot.placeBlock(bot.blockAt(cell.offset(0, -1, 0)), new Vec3(0, 1, 0))
  } finally {
    bot.setControlState('jump', false)
  }
}

// ドアの前の床の穴を下から埋める（2026-09-28: 床が 1 つ抜けて、穴の中からドアへ登れず、家に入れな
// かった。穴の中は「ドアの近く」に数えられていて、失敗の理由も出なかった）
async function fillDoorstep (bot, home, signal) {
  const holes = doorstepHoles(bot, home)
  if (!holes.length) return
  if (bot.blockAt(holes[0].offset(0, -1, 0))?.boundingBox !== 'block') {
    throw new Error(`the floor in front of the door is a hole deeper than ${DOORSTEP_DEPTH} blocks (${describeDoorstep(bot, home)})`)
  }
  for (const cell of holes) {
    const item = fillItem(bot)
    if (!item) {
      throw new Error(`the floor in front of the door is missing at ${cell.x},${cell.y},${cell.z} and you carry nothing to fill it (dirt or cobblestone) (${describeDoorstep(bot, home)})`)
    }
    if (!standingCell(bot).equals(cell) && bot.entity.position.distanceTo(cell.offset(0.5, 0.5, 0.5)) > PLACE_REACH) {
      await walkTo(bot, new goals.GoalNear(cell.x, cell.y, cell.z, 2), signal)
    }
    if (standingCell(bot).equals(cell)) await jumpAndPlace(bot, cell, item)
    else {
      await bot.equip(item, 'hand')
      await bot.placeBlock(bot.blockAt(cell.offset(0, -1, 0)), new Vec3(0, 1, 0))
    }
    signal?.throwIfAborted()
    if (bot.blockAt(cell)?.boundingBox !== 'block') throw new Error(`could not fill the hole in front of the door at ${cell.x},${cell.y},${cell.z}`)
  }
}

// ドアの前（外のセル）の高さに立っているか（1 段下の穴の中は、近くても前ではない）
const onDoorstepLevel = (bot, home) => {
  const p = standingCell(bot)
  return p.y === home.outside.y && Math.abs(p.x - home.outside.x) + Math.abs(p.z - home.outside.z) <= 1
}

export async function enterHome (bot, home, signal) {
  if (!isInside(bot, home)) {
    await fillDoorstep(bot, home, signal)
    try {
      await walkTo(bot, new goals.GoalBlock(home.outside.x, home.outside.y, home.outside.z), signal)
    } catch (e) {
      if (!/no path|too long/i.test(String(e?.message))) throw e
      // ドアの真ん前に立てない（何か置いてある）: その隣まで行き、そこからドアへ歩く
      try {
        await walkTo(bot, new goals.GoalNear(home.outside.x, home.outside.y, home.outside.z, 1), signal)
      } catch (e2) {
        if (!/no path|too long/i.test(String(e2?.message))) throw e2
        throw new Error(`No path to the home's door (${describeDoorstep(bot, home)})`)
      }
      if (!onDoorstepLevel(bot, home)) throw new Error(`could not stand in front of the door (${describeDoorstep(bot, home)})`)
    }
    await setDoor(bot, home, true)
    try {
      await walkInto(bot, home.door)
      await walkInto(bot, home.inside)
    } catch (e) {
      throw new Error(`${e.message} (${describeDoorstep(bot, home)})`)
    }
  }
  await setDoor(bot, home, false)
}

// 外のドアの近くで待っている敵対モブ
// 家に避難しているか: 家の中にいて、夜か、ドアの前に敵対モブがいる。避難中は外での行動を出さない
// （候補）・断る（道具、設計書 21）。作業台は家の中に置く（夜の待ち時間にクラフトできるように）
export function shelterOf (bot, home) {
  const inside = isInside(bot, home)
  const day = dayPhase(bot.time.timeOfDay) === 'day'
  const danger = dangerOutside(bot, home)
  return { inside, day, danger, sheltering: inside && (!day || danger.length > 0) }
}

export function dangerOutside (bot, home) {
  if (!home) return []
  return nearbyEntities(bot).filter(({ e }) => isHostile(bot, e) && e.position.distanceTo(home.outside) <= DOOR_DANGER_RADIUS &&
    !(e.position.x >= home.min.x && e.position.x < home.max.x + 1 && e.position.z >= home.min.z && e.position.z < home.max.z + 1))
}

const inHome = (home, p) => isHomeCell(home, p)

// 家、前の家、または計画中の家（`margin` だけ広げる）の一部: 掘らないし、何も置かない。
// すべてを確かめる: 新しい計画や引っ越しのせいで、建てた家が守られなくなってはいけない。
const HOME_HEIGHT = 5 // 壁は高さ 4 まで、その上に屋根
export function inHouse (state, p, margin = 0) {
  if ([state.home, ...(state.formerHomes ?? [])].some((h) => h && inBuilt(h, p, margin))) return true
  if (onBuildCell(state, p, margin)) return true
  const o = state.plan?.origin
  if (!o) return false
  const { width, depth, height } = state.plan.size
  return p.x >= o.x - margin && p.x < o.x + width + margin && p.z >= o.z - margin && p.z < o.z + depth + margin &&
    p.y >= o.y - 1 && p.y <= o.y + height
}

// 建っているもの（今の家、前の家、完成した建物）のまわりか。仮設の作業台・かまどは、これ以外なら
// 建築予定地にも置いてよい（その場所に建てる番が来たら、建てる側が壊して置く）
export function inStandingBuilding (bot, state, p, margin = 0) {
  if ([state.home, ...(state.formerHomes ?? [])].some((h) => h && inBuilt(h, p, margin))) return true
  return Object.values(state.builds ?? {}).some((plan) => plan.origin && plan.status(bot).complete && inBuildBox(plan, p, margin))
}

function inBuildBox (plan, p, margin) {
  const box = buildBox(plan)
  return p.x >= box.min.x - margin && p.x <= box.max.x + margin && p.z >= box.min.z - margin && p.z <= box.max.z + margin &&
    p.y >= box.min.y && p.y <= box.max.y
}

// 仮設してよい物（建築予定地にも置く。建てる番が来たら壊す）
export const TEMPORARY = /^(crafting_table|furnace)$/

// 道具（設計書 21）で名指しされても壊さない・置かない所: 今の家と、建てている家。前の家は
// 名指しされれば掘ってよい（town4d: 置いてきたベッドを取りに戻れなかった）。理由か null を返す
export function protectedReason (state, p, margin = 0, block = null) {
  // 建物が「空ける」と書いた家の壁は掘ってよい（docs/design/25_builds.md）
  if (block && buildAllowsDig(state, p, block)) return null
  if (state.home && inBuilt(state.home, p, margin)) return 'it is part of the current home'
  if (onBuildCell(state, p, margin)) return 'it is part of a build (use build_next for it)'
  const o = state.plan?.origin
  if (!o) return null
  const { width, depth, height } = state.plan.size
  const inPlan = p.x >= o.x - margin && p.x < o.x + width + margin && p.z >= o.z - margin && p.z < o.z + depth + margin &&
    p.y >= o.y - 1 && p.y <= o.y + height
  return inPlan ? 'it is part of the house being built' : null
}

function inBuilt (h, p, margin) {
  return p.x >= h.min.x - 1 - margin && p.x <= h.max.x + 1 + margin && p.z >= h.min.z - 1 - margin &&
      p.z <= h.max.z + 1 + margin && p.y >= h.min.y - 1 && p.y <= h.min.y + HOME_HEIGHT
}

// ベッドを置く場所: 室内の空いた 2 マス（足側 foot と頭側 foot + dir）と、その手前でボットが立つ
// マス（stand、足側の反対）。ベッドの頭はボットの向いた方に伸びるので、stand から foot を向いて置く。
// ドアの内側のセル（inside）はふさがない。前はドアからまっすぐ奥の 1 か所だけを見ていて、
// 最小の家（室内 3x3）ではその列にたいまつが 1 本あるだけで「置く場所がない」になり、ベッドを置く
// 小目標で詰まった。ドアからまっすぐ奥を先に、次にドアから遠い順
const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]]
export function bedSpot (bot, home) {
  const free = (p) => inHome(home, p) && !p.equals(home.inside) && bot.blockAt(p)?.name === 'air' &&
    bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block'
  const standable = (p) => inHome(home, p) && bot.blockAt(p)?.boundingBox === 'empty' &&
    bot.blockAt(p.offset(0, 1, 0))?.boundingBox === 'empty' && bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block'
  const inward = home.inside.minus(home.door)
  const spots = []
  for (let x = home.min.x; x <= home.max.x; x++) {
    for (let z = home.min.z; z <= home.max.z; z++) {
      const foot = home.min.offset(x - home.min.x, 0, z - home.min.z)
      for (const [dx, dz] of DIRS) {
        const dir = new Vec3(dx, 0, dz)
        const head = foot.plus(dir)
        const stand = foot.minus(dir)
        if (!free(foot) || !free(head) || !(stand.equals(home.inside) || standable(stand))) continue
        const straight = dir.equals(inward) && stand.equals(home.inside)
        spots.push({ foot, inward: dir, stand, rank: straight ? -Infinity : -foot.distanceTo(home.door) })
      }
    }
  }
  const best = spots.sort((a, b) => a.rank - b.rank)[0]
  return best ? { foot: best.foot, inward: best.inward, stand: best.stand } : null
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
    .filter((p) => isHomeCell(home, p) && !line.some((l) => l.equals(p)) && bot.blockAt(p)?.name === 'air' && bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block')
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

// 家にベッドがあるか。記録した位置（place_bed で置いたところ）だけでなく、家の中のどこかにある
// ベッドも数え、見つけたら記録する: 手で置いたベッド、道具（place）で置いたベッド、記録の前に
// 置いたベッドを「ない」と判定し、ベッドを置く小目標を立て続けていた。記録した位置が読み込まれて
// いない（ボットが遠い）ときは記録を信じる
export function hasBed (bot, home) {
  if (!home) return false
  if (home.bed) {
    const block = bot.blockAt(home.bed)
    if (!block || block.name.endsWith('_bed')) return true
  }
  const found = bedInHome(bot, home)
  home.bed = found
  return !!found
}

function bedInHome (bot, home) {
  for (let x = home.min.x; x <= home.max.x; x++) {
    for (let z = home.min.z; z <= home.max.z; z++) {
      const p = home.min.offset(x - home.min.x, 0, z - home.min.z)
      if (bot.blockAt(p)?.name.endsWith('_bed')) return p
    }
  }
  return null
}

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
