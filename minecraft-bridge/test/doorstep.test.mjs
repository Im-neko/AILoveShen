import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
import { enterHome, doorstepHoles, isDoorstepFloor } from '../src/home.mjs'
import { columnWork } from '../src/leveling.mjs'
import { arrivalReport, PRIMITIVES } from '../src/primitives.mjs'
import { shouldAutoJump } from '../src/move.mjs'

const { Vec3 } = vec3Pkg
const { goals } = pathfinderPkg

// 家: 室内 (0,65,0)、ドア (0,65,1)、ドアの前 (0,65,2)。床は y 64、その下も土。上は空気
const home = {
  inside: new Vec3(0, 65, 0),
  door: new Vec3(0, 65, 1),
  outside: new Vec3(0, 65, 2),
  min: new Vec3(-1, 65, -2),
  max: new Vec3(1, 65, 0),
  breach: []
}
const state = () => ({ home, formerHomes: [], builds: {}, plan: null })
const key = (p) => `${p.x},${p.y},${p.z}`

// 歩く・跳ぶ・置く・ドアを開けるだけの小さな世界。holes は空気にするセル
function world ({ holes = [], at = new Vec3(0.5, 65, 4.5), items = [['dirt', 4]] } = {}) {
  const cells = new Map()
  for (const h of holes) cells.set(key(h), 'air')
  let open = false
  const inv = new Map(items)
  const nameAt = (p) => {
    const c = cells.get(key(p))
    if (c) return c
    if (p.x === home.door.x && p.z === home.door.z && (p.y === 65 || p.y === 66)) return 'oak_door'
    return p.y <= 64 ? 'dirt' : 'air'
  }
  let look = null
  const controls = {}
  const bot = {
    entity: { position: at.clone(), yaw: 0, onGround: true, eyeHeight: 1.62 },
    controlState: controls,
    blockAt: (p) => {
      const name = nameAt(p)
      const block = name === 'oak_door' ? 'door' : name === 'air' ? 'empty' : 'block'
      return { name, position: p, boundingBox: block, getProperties: () => (name === 'oak_door' ? { open, half: p.y === 65 ? 'lower' : 'upper' } : {}) }
    },
    inventory: { items: () => [...inv].filter(([, n]) => n > 0).map(([name, count]) => ({ name, count })) },
    equip: async () => {},
    look: async () => {},
    lookAt: async (p) => { look = p },
    activateBlock: async () => { open = !open },
    on: () => {},
    removeListener: () => {},
    setControlState: (k, v) => { controls[k] = v },
    clearControlStates: () => { for (const k of Object.keys(controls)) controls[k] = false },
    waitForTicks: async () => {
      const p = bot.entity.position
      if (controls.jump) p.y += 1.2
      if (controls.forward && look) {
        const dx = look.x - p.x
        const dz = look.z - p.z
        const d = Math.hypot(dx, dz)
        const next = new Vec3(p.x + dx / d * Math.min(0.5, d), p.y, p.z + dz / d * Math.min(0.5, d))
        // 1 段上のセルへは歩いて入れない（跳ばないと）
        if (bot.blockAt(next.floored()).boundingBox !== 'block') bot.entity.position = next
      }
    },
    placeBlock: async (ref, face) => {
      const p = ref.position.plus(new Vec3(face.x, face.y, face.z))
      const me = bot.entity.position
      if (me.floored().equals(p)) throw new Error('the bot is in the way')
      cells.set(key(p), 'dirt')
      inv.set('dirt', inv.get('dirt') - 1)
    },
    pathfinder: {
      goto: async (goal) => {
        const floorOk = (x, y, z) => bot.blockAt(new Vec3(x, y - 1, z)).boundingBox === 'block' && nameAt(new Vec3(x, y, z)) === 'air'
        if (goal.rangeSq === undefined) {
          if (!floorOk(goal.x, goal.y, goal.z)) throw new Error('No path to the goal!')
          bot.entity.position = new Vec3(goal.x + 0.5, goal.y, goal.z + 0.5)
          return
        }
        if (goal.isEnd(bot.entity.position.floored())) return
        bot.entity.position = new Vec3(goal.x + 0.5, goal.y, goal.z + 1.5)
      },
      setGoal: () => {},
      isMoving: () => false
    }
  }
  return { bot, cells, isOpen: () => open }
}
const signal = new AbortController().signal

test('ドアの前の床の穴の中に立っていても、跳んで土を置いてから家に入る', async () => {
  const hole = new Vec3(0, 64, 2)
  const { bot, cells, isOpen } = world({ holes: [hole], at: new Vec3(0.5, 64, 2.5) })
  assert.deepEqual(doorstepHoles(bot, home).map(key), ['0,64,2'])
  await enterHome(bot, home, signal)
  assert.equal(cells.get('0,64,2'), 'dirt')
  assert.deepEqual(bot.entity.position.floored(), new Vec3(0, 65, 0))
  assert.equal(isOpen(), false)
})

test('穴の外からは縁から置く。2 段の穴は下から埋める', async () => {
  const { bot, cells } = world({ holes: [new Vec3(0, 64, 2), new Vec3(0, 63, 2)] })
  assert.deepEqual(doorstepHoles(bot, home).map(key), ['0,63,2', '0,64,2'])
  await enterHome(bot, home, signal)
  assert.equal(cells.get('0,63,2'), 'dirt')
  assert.equal(cells.get('0,64,2'), 'dirt')
})

test('埋める物がなければ、理由（穴の位置と立っている高さ）を添えて失敗する', async () => {
  const { bot } = world({ holes: [new Vec3(0, 64, 2)], at: new Vec3(0.5, 64, 2.5), items: [] })
  await assert.rejects(enterHome(bot, home, signal), /floor in front of the door is missing at 0,64,2 and you carry nothing to fill it.*you stand at 0,64,2/)
})

test('埋めるのは今の家のドアの前の床の列だけ（足と頭のセル、隣の列は置かない）', () => {
  const s = state()
  assert.equal(isDoorstepFloor(s, new Vec3(0, 64, 2)), true)
  assert.equal(isDoorstepFloor(s, new Vec3(0, 62, 2)), true)
  assert.equal(isDoorstepFloor(s, new Vec3(0, 65, 2)), false)
  assert.equal(isDoorstepFloor(s, new Vec3(1, 64, 2)), false)
  assert.equal(isDoorstepFloor({ home: null }, new Vec3(0, 64, 2)), false)
})

test('整地は、同じ高さならドアの前の穴を埋める列と、壁のすぐ外の列に数える（別の高さなら家のまわりは外す）', () => {
  const { bot } = world({ holes: [new Vec3(0, 64, 2)] })
  const work = columnWork(bot, state(), 0, 2, 65)
  assert.equal(work.off, true)
  assert.deepEqual(work.fills.map(key), ['0,64,2'])
  assert.equal(typeof columnWork(bot, state(), 1, 2, 65), 'object')
  assert.equal(columnWork(bot, state(), 0, 2, 66), 'house or build') // 別の高さに整地するなら触らない
})

test('goto は歩いた後の実際の位置で着いたかを言う（穴の中で「着いた」と言わない）', () => {
  // 目的地（ドアの前の床の上）は立てるセル: 下の 64 が床。ボットはその隣の穴の中にいる
  const floor = (p) => p.x === -76 && p.z === -151 && p.y === 64
  const bot = { entity: { position: new Vec3(-75.5, 64, -149.7), onGround: true }, blockAt: (p) => ({ name: floor(p) ? 'stone' : 'air', boundingBox: floor(p) ? 'block' : 'empty' }) }
  const target = new Vec3(-76, 65, -151)
  assert.throws(() => arrivalReport(bot, new goals.GoalNear(target.x, target.y, target.z, 1), target), /did not reach -76,65,-151: stopped at -75.5,64,-149.7 .*height -1/)
  bot.entity.position = new Vec3(-75.5, 65, -150.5)
  assert.match(arrivalReport(bot, new goals.GoalNear(target.x, target.y, target.z, 1), target), /arrived near -76,65,-151/)
})

test('goto: 目的地が空中で、水平には着いていれば着いた（y は地面の推測。2 ブロック上の空中を指していた）。穴の中や横にずれていれば失敗のまま', () => {
  const ground = 63 // y 62 までが地面。63 から上は空気
  const bot = (x, y, z) => ({
    entity: { position: new Vec3(x, y, z), onGround: true },
    blockAt: (p) => ({ name: p.y < ground ? 'grass_block' : 'air', boundingBox: p.y < ground ? 'block' : 'empty' })
  })
  const target = new Vec3(-103, 65, -78)
  const goal = new goals.GoalNear(target.x, target.y, target.z, 1)
  assert.match(arrivalReport(bot(-102.7, 63, -77.5), goal, target), /arrived near -103,65,-78 .*the target is in the air, the ground there is at y 63/)
  assert.throws(() => arrivalReport(bot(-100.5, 63, -77.5), goal, target), /did not reach -103,65,-78: stopped at .*2 m away horizontally, height -2/, '横に 2 m ずれている')
  const walled = { ...bot(-102.7, 63, -77.5), blockAt: (p) => ({ name: p.y === 64 && p.x === -103 ? 'stone' : 'air', boundingBox: p.y === 64 && p.x === -103 ? 'block' : p.y < ground ? 'block' : 'empty' }) }
  assert.throws(() => arrivalReport(walled, goal, target), /did not reach/, '目的地の列に間に石があれば空中ではない')
})

test('耕地やハーフブロックの上は pathfinder と同じく 1 つ上のセルに立っている（着いたのに失敗にしない）', () => {
  const bot = {
    entity: { position: new Vec3(0.5, 64.9375, 0.5), onGround: true },
    blockAt: (p) => ({ name: p.y === 64 ? 'farmland' : 'air', boundingBox: p.y === 64 ? 'block' : 'empty' })
  }
  const target = new Vec3(1, 65, 0)
  assert.match(arrivalReport(bot, new goals.GoalNear(target.x, target.y, target.z, 1), target), /arrived near 1,65,0 .*height \+0/)
})

test('オートジャンプ: 前へ歩いていて、進む先に 1 段の完全なブロックがあれば跳ぶ（ドア・柵、pathfinder の移動では跳ばない）', () => {
  const at = (names, extra = {}) => ({
    entity: { position: new Vec3(0.5, 65, 0.5), yaw: 0, onGround: true },
    controlState: { forward: true },
    pathfinder: { isMoving: () => false },
    blockAt: (p) => {
      const name = names[key(p)] ?? (p.y <= 64 ? 'dirt' : 'air')
      return { name, boundingBox: name === 'air' ? 'empty' : 'block', shapes: [[0, 0, 0, 1, 1, 1]] }
    },
    ...extra
  })
  // yaw 0 は -z を向く
  assert.equal(shouldAutoJump(at({ '0,65,-1': 'dirt' })), true)
  assert.equal(shouldAutoJump(at({})), false)
  assert.equal(shouldAutoJump(at({ '0,65,-1': 'oak_door' })), false)
  assert.equal(shouldAutoJump(at({ '0,65,-1': 'oak_fence' })), false)
  assert.equal(shouldAutoJump(at({ '0,65,-1': 'dirt', '0,66,-1': 'dirt' })), false) // 2 段
  assert.equal(shouldAutoJump(at({ '0,65,-1': 'dirt' }, { pathfinder: { isMoving: () => true } })), false)
  assert.equal(shouldAutoJump(at({ '0,65,-1': 'dirt' }, { controlState: { forward: false } })), false)
  // 耕地（15/16 の高さ）から隣の草へは段差ではない。耕地の上からは 1 つ上の段差で跳ぶ
  const onFarmland = { entity: { position: new Vec3(0.5, 64.9375, 0.5), yaw: 0, onGround: true } }
  assert.equal(shouldAutoJump(at({ '0,64,0': 'farmland' }, onFarmland)), false)
  assert.equal(shouldAutoJump(at({ '0,64,0': 'farmland', '0,65,-1': 'dirt' }, onFarmland)), true)
})

test('ドアから遠いとき、失敗の理由はドアの状態でなく自分の位置（閉じた空間）を先に言う', async () => {
  const { describeDoorstep } = await import('../src/home.mjs')
  // 14 ブロック下の、石に囲まれた 1 マス
  const here = new Vec3(10, 51, 10)
  const solid = (p) => !(p.x === 10 && p.z === 10 && (p.y === 51 || p.y === 52))
  const bot = {
    entity: { position: here.offset(0.5, 0, 0.5), onGround: true, isInWater: false },
    blockAt: (p) => ({ name: solid(p) ? 'stone' : 'air', position: p, boundingBox: solid(p) ? 'block' : 'empty', getProperties: () => ({}) })
  }
  const text = describeDoorstep(bot, home)
  assert.match(text, /you are \d+ blocks away from the door and 14 blocks below it/)
  assert.match(text, /the door itself is not the problem/)
  assert.match(text, /closed space \(1 cells/)
  assert.doesNotMatch(text, /door closed|door open/)
})

test('ドアの近くでは、これまでどおりドアの状態と前の床を言う', async () => {
  const { describeDoorstep } = await import('../src/home.mjs')
  const { bot } = world({ at: new Vec3(0.5, 65, 4.5) })
  assert.match(describeDoorstep(bot, home), /^door (closed|open); in front of the door: floor dirt/)
})

test('眠る前にベッドの隣まで歩く: ドアの内側からは届かない、奥に横向きのベッド（2026-10-09）', async () => {
  const { bot } = world({ at: new Vec3(0.5, 65, 0.5) })
  const bed = { name: 'white_bed', position: new Vec3(-1, 65, -2), boundingBox: 'block', getProperties: () => ({}) }
  const blockAt = bot.blockAt
  bot.blockAt = (p) => (p.equals(bed.position) ? bed : blockAt(p))
  let at = null
  bot.sleep = async () => { at = bot.entity.position.floored() }
  bot.isSleeping = false
  bot.time = { timeOfDay: 18000 }
  bot.once = () => {}
  const st = { ...state(), home: { ...home, bed: bed.position } }
  await PRIMITIVES.sleep(bot, st, {}, signal).catch(() => {})
  assert.ok(at, 'bot.sleep が呼ばれる')
  assert.ok(at.distanceTo(bed.position) <= 1, `ベッドの隣から眠る: ${at}`)
})
