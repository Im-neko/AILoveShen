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
import { rockBetween } from './reach.mjs'
import { watchDoor, reportDoor } from './door-report.mjs'
import { FOUND_BASE_NAME } from './landmarks.mjs'
import { assessRisk, atLeast, describeRisk, SHELTER_LEVEL } from './risk.mjs'
import { nearbyEntities, isHostile, EXPLODES, isLog, isPlanks, dayPhase, fillItem, standingCell } from './observe.mjs'

const { Vec3 } = vec3Pkg
const { goals } = pathfinderPkg
// STATE_FILE で場所を変えられる（ベンチマークが配信のブリッジの state.json に触れずに、別の状態で走るため。docs/design/83）
const DATA_FILE = process.env.STATE_FILE || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data', 'state.json')
const DOOR_TOGGLE_TIMEOUT_MS = 1000 // 1 回の手で、ドアが動くのを待つ時間（手は DOOR_TRIES の数だけ）
const DOOR_BREAK_MS = 10000
const WALK_THROUGH_TIMEOUT_MS = 3000
const DOORSTEP_DEPTH = 3 // ドアの前の床の穴は、ここまでの深さなら下から埋める
const PLACE_REACH = 4
const JUMP_OUT_MS = 1500
// ドアにここまで近い敵対モブがいたらボットは中にとどまる: クリーパーが家までついて来てドアの前で
// 待ち、朝ボットが外に出たところで入口（と家の角）を吹き飛ばした。
const DOOR_DANGER_RADIUS = 8

const toVec = (p) => new Vec3(p.x, p.y, p.z)
const plain = (v) => ({ x: v.x, y: v.y, z: v.z })

export const homeToJSON = (h) => ({
  name: h.name ?? null,
  design: h.design ?? null,
  door: plain(h.door),
  inside: plain(h.inside),
  outside: plain(h.outside),
  min: plain(h.min),
  max: plain(h.max),
  bed: h.bed ? plain(h.bed) : null,
  breach: h.breach,
  cells: h.cells ?? null, // 増築で広げた室内（docs/design/25_builds.md）
  found: !!h.found // 見つけた拠点（自分で建てていない仮の宿。docs/design/62_found_base.md）
})

export const homeFromJSON = (h) => ({ name: h.name ?? null, design: h.design ?? null, door: toVec(h.door), inside: toVec(h.inside), outside: toVec(h.outside), min: toVec(h.min), max: toVec(h.max), bed: h.bed ? toVec(h.bed) : null, breach: h.breach ?? [], ...(h.cells ? { cells: h.cells } : {}), ...((h.found ?? h.name === FOUND_BASE_NAME) ? { found: true } : {}) })

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
    facilities: state.facilities ?? {}, // 施設（facilities.mjs。設計書 47）
    // 自分で置いた物と、最近掘った跡（ledger.mjs。docs/design/55）
    placed: state.placed ?? {},
    dug: state.dug ?? [],
    // 採掘の場所と階段（quarry.mjs。設計書 64）
    quarry: state.quarry ?? null,
    // 整地する（した）土地（leveling.mjs。土を取るために掘らない所）
    leveling: state.leveling ?? null,
    // 捨てた物（spare.mjs。5 分は拾い直さない。再起動でも忘れない）
    tossed: state.tossed ?? null
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
  if (data.placed) state.placed = data.placed
  if (data.dug) state.dug = data.dug
  if (data.quarry) state.quarry = data.quarry
  if (data.leveling) state.leveling = data.leveling
  if (data.tossed) state.tossed = data.tossed
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

// ドアが開いている（ドアのマスが空いていてもよい）: ドアが壊れて、なくなっていても、そこは開いている
export function isDoorOpen (bot, home) {
  const block = bot.blockAt(home.door)
  if (!block) return false
  if (!block.name.endsWith('_door')) return block.boundingBox !== 'block'
  return isTrue(block.getProperties().open)
}

export class DoorStuckError extends Error {
  constructor (open) {
    super(`door did not ${open ? 'open' : 'close'}`)
    this.name = 'DoorStuckError'
  }
}

const doorOpenNow = (bot, home) => isTrue(bot.blockAt(home.door)?.getProperties?.().open)

async function waitDoor (bot, home, open, ms) {
  const start = Date.now()
  while (doorOpenNow(bot, home) !== open) {
    if (Date.now() - start > ms) return false
    await bot.waitForTicks(1)
  }
  return true
}

// ドアを押す手。既定の右クリックで動かないとき、変えて試す（どれが効くかは door-toggles.jsonl に残る）:
//   1. 既定: ドアの真ん中を見て、上の面をクリック
//   2. スニークを外し、手を空にして、ボットのいる側の面を指定してクリック
//   3. 上の段をクリック
const DOOR_TRIES = [
  async (bot, home, block) => { await bot.lookAt(home.door.offset(0.5, 0.5, 0.5), true); await bot.activateBlock(block) },
  async (bot, home, block) => {
    for (const c of ['sneak', 'sprint', 'forward', 'back', 'left', 'right']) bot.setControlState(c, false)
    if (bot.heldItem) await bot.unequip('hand')
    const me = bot.entity.position
    const dx = me.x - (home.door.x + 0.5)
    const dz = me.z - (home.door.z + 0.5)
    const face = Math.abs(dx) > Math.abs(dz) ? new Vec3(Math.sign(dx), 0, 0) : new Vec3(0, 0, Math.sign(dz) || 1)
    const cursor = new Vec3(0.5 + face.x * 0.5, 0.5, 0.5 + face.z * 0.5)
    await bot.lookAt(home.door.offset(0.5 + face.x * 0.5, 0.5, 0.5 + face.z * 0.5), true)
    await bot.waitForTicks(2)
    await bot.activateBlock(block, face, cursor)
  },
  async (bot, home) => {
    const upper = bot.blockAt(home.door.offset(0, 1, 0))
    if (!upper?.name.endsWith('_door')) throw new Error('no upper half')
    await bot.lookAt(home.door.offset(0.5, 1.5, 0.5), true)
    await bot.activateBlock(upper)
  }
]

export async function setDoor (bot, home, open) {
  const block = bot.blockAt(home.door)
  if (!block || !block.name.endsWith('_door')) throw new Error(`no door at ${home.door}`)
  if (isDoorOpen(bot, home) === open) return
  const watch = watchDoor(bot, home.door)
  const tries = []
  try {
    for (const [i, press] of DOOR_TRIES.entries()) {
      try {
        await press(bot, home, bot.blockAt(home.door))
      } catch (e) {
        tries.push({ try: i + 1, error: String(e?.message ?? e).slice(0, 120) })
        continue
      }
      const done = await waitDoor(bot, home, open, DOOR_TOGGLE_TIMEOUT_MS)
      tries.push({ try: i + 1, done })
      if (done) {
        // 最初の手で動いたときは記録しない（毎回のログが増える）。2 手目以降で動いたのは残す
        if (i > 0) reportDoor(bot, home.door, open, tries, watch.updates, true)
        return
      }
    }
  } finally {
    watch.stop()
  }
  reportDoor(bot, home.door, open, tries, watch.updates, false)
  throw new DoorStuckError(open)
}

// ドアが開かなければ壊して通る（2026-10-04: 夜に玄関の前で 28 回開かず、家に入れなかった。原因は
// door-toggles.jsonl を見て決める）。通ったあとに置き直す（restoreDoor）。壊したら true
// 家のドアのマスがドアでなくなっている（壊れた、爆発で飛んだ）: 読み込まれているときだけ真
export function doorGone (bot, home) {
  const block = home?.door ? bot.blockAt(home.door) : null
  return !!block && !block.name.endsWith('_door')
}

// ドアがなくて、そのマスの床もない（爆発で地面ごと飛んだ）: 歩いて入ると穴に落ちる。先に家の計画（built）が足元を埋める。
// 理由を返す（入れるなら null）
export function homeEntryBlocked (bot, home) {
  if (!doorGone(bot, home)) return null
  const floor = bot.blockAt(home.door.offset(0, -1, 0))
  return floor && floor.boundingBox !== 'block'
    ? 'the door is gone and the floor under it too: walking in drops you into a hole; the ground under the house is filled first (built)'
    : null
}

export async function openDoorOrBreak (bot, home, signal) {
  // ドアがもうない: 開ける必要はなく、そのまま入れる（入ったあとで置き直す）
  if (doorGone(bot, home)) return true
  try {
    await setDoor(bot, home, true)
    return false
  } catch (e) {
    if (!(e instanceof DoorStuckError)) throw e
  }
  signal?.throwIfAborted()
  const block = bot.blockAt(home.door)
  if (!block?.name.endsWith('_door')) return true // もう壊れている
  console.log(`[door] ドアが開かないので壊して通る: ${block.name} at ${home.door}`)
  const axe = bot.inventory.items().find((i) => i.name.endsWith('_axe'))
  if (axe) await bot.equip(axe, 'hand')
  await bot.lookAt(home.door.offset(0.5, 0.5, 0.5), true)
  let timer
  try {
    await Promise.race([
      bot.dig(block, true),
      new Promise((resolve, reject) => { timer = setTimeout(() => { bot.stopDigging(); reject(new Error(`could not break the stuck door in ${DOOR_BREAK_MS / 1000}s`)) }, DOOR_BREAK_MS) })
    ])
  } finally {
    clearTimeout(timer)
  }
  return true
}

const doorItem = (bot) => bot.inventory.items().find((i) => i.name.endsWith('_door') && i.name !== 'iron_door')

// 壊したドアを、通ったあとのこちら側から元の場所に置き直す（落ちたのを拾うのを少し待つ）。
// 置けなかったら理由を返す（家は開いたまま）。置けたら ''
export async function restoreDoor (bot, home) {
  for (let i = 0; i < 40 && !doorItem(bot); i++) await bot.waitForTicks(1)
  const item = doorItem(bot)
  if (!item) return 'the broken door was not picked up'
  try {
    await bot.equip(item, 'hand')
    await bot.lookAt(home.door.offset(0.5, 0, 0.5), true)
    await bot.waitForTicks(2)
    await bot.placeBlock(bot.blockAt(home.door.offset(0, -1, 0)), new Vec3(0, 1, 0))
    for (let i = 0; i < 20 && !bot.blockAt(home.door)?.name.endsWith('_door'); i++) await bot.waitForTicks(1)
  } catch (e) {
    return `could not put the door back: ${e.message}`
  }
  return bot.blockAt(home.door)?.name.endsWith('_door') ? '' : 'the door was not placed back'
}

// まっすぐ歩いて進めなかったときの様子（2026-10-08: 開いたドアの 1 マス手前で 3 秒進めなかったが、同じ道が次は 1.2 秒で通れ、
// 理由がログから分からなかった。設計書 114）。位置、速度、押しているキー、経路探しの目標、行き先のセルの床・足・頭
export function describeWalkStall (bot, cell) {
  const f = (n) => Number(n).toFixed(2)
  const p = bot.entity.position
  const vel = bot.entity.velocity
  const name = (q) => {
    const b = bot.blockAt(q)
    if (!b) return 'unloaded'
    const props = b.name.endsWith('_door') ? b.getProperties?.() : null
    return props ? `${b.name}(${props.half} open=${props.open} facing=${props.facing})` : b.name
  }
  const keys = Object.entries(bot.controlState ?? {}).filter(([, on]) => on).map(([k]) => k)
  return `from ${f(p.x)},${f(p.y)},${f(p.z)} velocity ${f(vel?.x ?? 0)},${f(vel?.y ?? 0)},${f(vel?.z ?? 0)} on_ground ${bot.entity.onGround} ` +
    `controls [${keys.join(' ')}] pathfinder goal ${bot.pathfinder?.goal ? 'set' : 'none'} yaw ${f(bot.entity.yaw)}; ` +
    `at the cell: floor ${name(cell.offset(0, -1, 0))}, feet ${name(cell)}, head ${name(cell.offset(0, 1, 0))}`
}

// `cell`（1ブロック先、開いたドアの向こう）の中心までまっすぐ歩く。
export async function walkInto (bot, cell) {
  const target = cell.offset(0.5, 0, 0.5)
  await bot.lookAt(target.offset(0, bot.entity.eyeHeight, 0), true)
  bot.setControlState('forward', true)
  const start = Date.now()
  try {
    while (bot.entity.position.xzDistanceTo(target) > 0.35) {
      if (Date.now() - start > WALK_THROUGH_TIMEOUT_MS) {
        const why = describeWalkStall(bot, cell)
        console.log(`[door] 歩いて通れない: ${cell} ${why}`)
        throw new Error(`could not walk to ${cell} (${why})`)
      }
      await bot.lookAt(target.offset(0, bot.entity.eyeHeight, 0), true)
      await bot.waitForTicks(1)
    }
  } finally {
    bot.clearControlStates()
  }
}

// 家の前のセルの様子（行けないときの理由に添える）。ボットの足の高さも（ドアの前の床が抜けていると
// 1 段下に立ち、ドアへ登れない）
const NEAR_DOOR = 8 // ドアの状態を失敗の理由に書くのは、ドアからこの距離（水平）と 3 ブロック（高さ）の内にいるとき

// 家に入れない理由に添える、ドアのまわりの様子。ドアから離れているときは、ドアの状態ではなく自分の位置（離れ具合、
// 閉じた空間か）を先に書く（2026-10-04: 家の 14 ブロック下の採掘場の底から go_home が失敗したのに「door closed」が
// 先に出て、モデルがドアが開かないと思い込み、ドアを調べ続けた）
export function describeDoorstep (bot, home) {
  const name = (p) => bot.blockAt(p)?.name ?? 'unloaded'
  const o = home.outside
  const me = standingCell(bot)
  const away = Math.round(Math.hypot(me.x - o.x, me.z - o.z))
  const dy = me.y - o.y
  const where = `you stand at ${me.x},${me.y},${me.z} (the doorstep is ${o.x},${o.y},${o.z})`
  if (away > NEAR_DOOR || Math.abs(dy) > 3) {
    const rock = rockBetween(bot, o, false)
    const height = dy ? ` and ${Math.abs(dy)} blocks ${dy < 0 ? 'below' : 'above'} it` : ''
    const closed = rock
      ? `; you are in a closed space (${rock.cells} cells you can walk in) ${rock.gap} blocks from the door: the way out is the problem, not the door. Climb out first (the candidate "climb out of the closed space")`
      : '; no walkable route leads from here to the door'
    return `you are ${away} blocks away from the door${height}, so the door itself is not the problem; ${where}${closed}`
  }
  const door = isDoorOpen(bot, home) ? 'open' : 'closed'
  return `door ${door}; in front of the door: floor ${name(o.offset(0, -1, 0))}, feet ${name(o)}, head ${name(o.offset(0, 1, 0))}; ${where}`
}

// 今の家のドアの前の床の列（外に立つセルの真下から DOORSTEP_DEPTH まで）。ここだけは家のまわりでも
// ブロックを置いてよい（足と頭のセルは置かない: ドアがふさがる）
export function isDoorstepFloor (state, p) {
  const o = state.home?.outside
  return !!o && p.x === o.x && p.z === o.z && p.y < o.y && p.y >= o.y - DOORSTEP_DEPTH
}

// ドアの前の列: 外に立つセルとその下すべて（経路探索に掘らせない。ここに縦穴が開くと、家の戸口の床が抜ける）
export function doorstepColumn (state, p) {
  const o = state.home?.outside
  return !!o && p.x === o.x && p.z === o.z && p.y <= o.y
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
const FREE_CELL = new Set(['air', 'cave_air', 'void_air', 'water', 'lava'])
export async function jumpAndPlace (bot, cell, item) {
  // 立っているセルに松明・花など、通れるが置き換えられない物があると、足場を置けず上がれない（足元の松明で、約 5 分積めなかった:
  // 2026-10-07）。先に壊す（素手で一瞬）
  const here = bot.blockAt(cell)
  if (here && here.boundingBox === 'empty' && !FREE_CELL.has(here.name)) await bot.dig(here, true)
  await bot.equip(item, 'hand')
  await bot.look(bot.entity.yaw, -Math.PI / 2, true)
  bot.setControlState('jump', true)
  try {
    const start = Date.now()
    while (bot.entity.position.y < cell.y + 1) {
      if (Date.now() - start > JUMP_OUT_MS) throw new Error(`could not jump up from the hole at ${cell}`)
      await bot.waitForTicks(1)
    }
    const { block, face } = placeAgainst(bot, cell)
    await bot.placeBlock(block, face)
  } finally {
    bot.setControlState('jump', false)
  }
}

// cell に置くときに寄りかかるブロックと面: 下が固ければ下から、水の中などで下が固くなければ、横の固い壁から
// （水の中では下が水で、足場にならない。ほかになければ下に置く）
function placeAgainst (bot, cell) {
  const below = bot.blockAt(cell.offset(0, -1, 0))
  if (below?.boundingBox === 'block') return { block: below, face: new Vec3(0, 1, 0) }
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const wall = bot.blockAt(cell.offset(dx, 0, dz))
    if (wall?.boundingBox === 'block') return { block: wall, face: new Vec3(-dx, 0, -dz) }
  }
  return { block: below, face: new Vec3(0, 1, 0) }
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

// 家に入ってドアを閉める。ドアを壊して入ったときは、そのことを文で返す（ふつうは ''）
export async function enterHome (bot, home, signal) {
  let broke = false
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
    const gone = doorGone(bot, home)
    broke = await openDoorOrBreak(bot, home, signal)
    try {
      await walkInto(bot, home.door)
      await walkInto(bot, home.inside)
    } catch (e) {
      throw new Error(`${e.message} (${describeDoorstep(bot, home)})`)
    }
    // ドアが最初からなかった: 開けて壊したのではない。持っていれば置き直し、なければそのことを返す
    if (gone) {
      const trouble = doorItem(bot) ? await restoreDoor(bot, home) : 'you carry no door'
      return trouble ? `the house has no door (${trouble}): it is open until a door is placed` : 'the missing door was put back'
    }
    if (broke) return brokenDoorNote(await restoreDoor(bot, home))
  }
  if (!bot.blockAt(home.door)?.name.endsWith('_door')) {
    // ドアがない（壊れたまま）: 持っていれば置く
    const trouble = await restoreDoor(bot, home)
    return trouble ? `the house has no door: ${trouble}` : 'the missing door was put back'
  }
  await setDoor(bot, home, false)
  return ''
}

const brokenDoorNote = (trouble) => trouble
  ? `the door would not open, so it was broken to get in, and ${trouble}: the house is open`
  : 'the door would not open, so it was broken to get in and put back'

// 外のドアの近くで待っている敵対モブ
// 家に避難しているか: 家の中にいて、夜か、ドアの前に敵対モブがいる。避難中は外での行動を出さない
// （候補）・断る（道具、設計書 21）。作業台は家の中に置く（夜の待ち時間にクラフトできるように）
// 家の中で外に出る手を止めるのは、外に出たときの危なさ（risk.mjs）が SHELTER_LEVEL に届いたときだけ
// （夜、またはドアの前の敵、だけでは止めない: 持ち物も体力も敵の強さも見ずに、昼に敵 1 体で 1 時間以上
// 閉じこもった。2026-10-07、docs/design/94）。夜と敵の有無は、risk の理由として LLM に見せる
export function shelterOf (bot, home) {
  const inside = isInside(bot, home)
  const day = dayPhase(bot.time.timeOfDay) === 'day'
  const danger = dangerOutside(bot, home)
  const risk = assessRisk(bot, { home })
  // ドアの前のクリーパーは、リスクの数字に関わらず外に出さない（出ればドアのそばで爆発し、壁が壊れる）
  const creeper = danger.some(({ e }) => EXPLODES.has(e.name))
  const sheltering = inside && ((SHELTER_LEVEL !== 'none' && atLeast(risk, SHELTER_LEVEL)) || creeper)
  const why = creeper ? `a creeper is at the door (it explodes and breaks the walls); ${describeRisk(risk)}` : describeRisk(risk)
  return { inside, day, danger, risk, why, sheltering }
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

// 壁の材料: 板材・原木、仮の小屋の土（設計書 133。草のブロックに変わっていることもある）
const isDirt = (name) => name === 'dirt' || name === 'grass_block'
const isWallBlock = (block) => !!block && (isPlanks(block.name) || isLog(block.name) || isDirt(block.name))
const standable = (bot, p) => bot.blockAt(p)?.boundingBox === 'empty' && bot.blockAt(p.offset(0, 1, 0))?.boundingBox === 'empty' &&
  bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block'

// p に立てるか（足と頭が空いている）、足元の固いブロックまで drop 段以内か（0 なら足元がすぐ固い）
function standableWithin (bot, p, drop) {
  if (bot.blockAt(p)?.boundingBox !== 'empty' || bot.blockAt(p.offset(0, 1, 0))?.boundingBox !== 'empty') return false
  for (let k = 1; k <= 1 + drop; k++) if (bot.blockAt(p.offset(0, -k, 0))?.boundingBox === 'block') return true
  return false
}

// ドアから外へ出る道が使えるか: 内側のドアの前に立て、ドアのセルと外のセルの下に（落ちても）3 段以内に地面がある。
// クリーパーがドアの前で爆発して床・ドア・壁が吹き飛び、外が 7 ブロック下のクレーターになった
// （2026-10-07 14:14: ボットは家の中に閉じ込められ、「No path to the goal!」を 1 時間返し続けた。設計書 112）
export const EXIT_DROP = 3 // 出口の外が低くてもよい段数（落ちても 3 ブロックまでのダメージは軽い）
export function doorRouteOk (bot, home) {
  const solidBelow = (p) => bot.blockAt(p.offset(0, -1, 0))?.boundingBox === 'block'
  const ground = (p) => [1, 2, 3].some((k) => bot.blockAt(p.offset(0, -k, 0))?.boundingBox === 'block')
  return solidBelow(home.inside) && ground(home.door) && ground(home.outside)
}

// 壁を掘って出口にできる場所。各辺で一番よいセル: 壁のブロックが2つともあり、内側と外側に立つ
// 場所があり、ドアではない。与えられた敵対モブから遠い順。drop: 外の足元がこの段数まで低くてもよい
export function exitSpots (bot, home, hostiles, { drop = 0 } = {}) {
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
      if (!standable(bot, wall.offset(-dx, 0, -dz)) || !standableWithin(bot, step, drop)) continue
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
      const kind = isLog(b.block) ? 'log' : isDirt(b.block) ? 'dirt' : 'planks'
      const held = { log: isLog, dirt: isDirt, planks: isPlanks }[kind]
      if (!bot.inventory.items().some((i) => held(i.name))) throw new Error(`no ${kind} to close the wall at ${pos}`)
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
  const { sheltering, why } = shelterOf(bot, home)
  if (sheltering && !confront) throw new Error(`staying inside: ${why}`)
  if (bot.entity.position.floored().xzDistanceTo(home.inside) > 0) {
    // 閉じた家の中の経路移動は問題ない: 内部に障害物はない
    await walkTo(bot, new goals.GoalBlock(home.inside.x, home.inside.y, home.inside.z), signal)
  }
  const broke = await openDoorOrBreak(bot, home, signal)
  await walkInto(bot, home.door)
  await walkInto(bot, home.outside)
  if (broke) {
    const trouble = await restoreDoor(bot, home)
    if (trouble) console.log(`[door] ${brokenDoorNote(trouble)}`)
    return
  }
  await setDoor(bot, home, false)
}
