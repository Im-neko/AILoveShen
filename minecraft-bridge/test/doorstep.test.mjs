import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
import { enterHome, doorstepHoles, isDoorstepFloor } from '../src/home.mjs'
import { columnWork } from '../src/leveling.mjs'
import { arrivalReport } from '../src/primitives.mjs'
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

test('整地は、同じ高さならドアの前の穴を埋める列に数える（ほかの家のまわりは今までどおり外す）', () => {
  const { bot } = world({ holes: [new Vec3(0, 64, 2)] })
  const work = columnWork(bot, state(), 0, 2, 65)
  assert.equal(work.off, true)
  assert.deepEqual(work.fills.map(key), ['0,64,2'])
  assert.equal(columnWork(bot, state(), 1, 2, 65), 'house or build')
  assert.equal(columnWork(bot, state(), 0, 2, 66), 'house or build') // 別の高さに整地するなら触らない
})

test('goto は歩いた後の実際の位置で着いたかを言う（穴の中で「着いた」と言わない）', () => {
  const bot = { entity: { position: new Vec3(-75.5, 64, -149.7), onGround: true }, blockAt: () => ({ name: 'air', boundingBox: 'empty' }) }
  const target = new Vec3(-76, 65, -151)
  assert.throws(() => arrivalReport(bot, new goals.GoalNear(target.x, target.y, target.z, 1), target), /did not reach -76,65,-151: stopped at -75.5,64,-149.7 .*height -1/)
  bot.entity.position = new Vec3(-75.5, 65, -150.5)
  assert.match(arrivalReport(bot, new goals.GoalNear(target.x, target.y, target.z, 1), target), /arrived near -76,65,-151/)
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
