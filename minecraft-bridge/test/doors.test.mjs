import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import prismarineBlock from 'prismarine-block'
import pathfinderPkg from 'mineflayer-pathfinder'
import { teachDoors, doorOpener } from '../src/doors.mjs'

const { Vec3 } = vec3Pkg
const { Movements } = pathfinderPkg
const md = minecraftData('1.21.4')
const Block = prismarineBlock('1.21.4')

// y 63 が石の床、その上は空気。walls の位置は石、doors は { 'x,z': { name, facing, open } }（y 64 と 65）
function world ({ walls = [], doors = {} } = {}) {
  const set = new Map()
  const put = (x, y, z, name, props = {}) => {
    const b = Block.fromProperties(name, props, 0)
    b.position = new Vec3(x, y, z)
    set.set(`${x},${y},${z}`, b)
  }
  for (const [x, z] of walls) { put(x, 64, z, 'stone'); put(x, 65, z, 'stone') }
  for (const [k, d] of Object.entries(doors)) {
    const [x, z] = k.split(',').map(Number)
    const props = { facing: d.facing, open: !!d.open }
    if (d.name.endsWith('_door')) {
      put(x, 64, z, d.name, { ...props, half: 'lower', hinge: 'left', powered: false })
      put(x, 65, z, d.name, { ...props, half: 'upper', hinge: 'left', powered: false })
    } else put(x, 64, z, d.name, { ...props, in_wall: false, powered: false })
  }
  const blockAt = (p) => {
    const q = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
    const b = set.get(`${q.x},${q.y},${q.z}`) ?? Block.fromProperties(q.y <= 63 ? 'stone' : 'air', {}, 0)
    b.position = q
    return b
  }
  return { set, blockAt }
}

function movements (w, state = {}) {
  const bot = { registry: md, version: '1.21.4', blockAt: w.blockAt, entity: { position: new Vec3(0.5, 64, 0.5), effects: [] }, inventory: { items: () => [] }, pathfinder: { bestHarvestTool: () => null } }
  const m = new Movements(bot)
  m.exclusionAreasBreak.push((block) => block.position && block.name !== 'stone' ? 100 : 0)
  m.canDig = false
  teachDoors(m, { home: null, formerHomes: [], ...state })
  return m
}

const forward = (m, from, dir) => {
  const out = []
  m.getMoveForward({ x: from.x, y: from.y, z: from.z, remainingBlocks: 0 }, dir, out)
  return out
}

test('木のドアはその向きに沿ってまっすぐなら通れる（開いていても閉じていても）。横からは通れない', () => {
  const w = world({ walls: [[-1, 1], [1, 1]], doors: { '0,1': { name: 'oak_door', facing: 'north' } } })
  const m = movements(w)
  const through = forward(m, new Vec3(0, 64, 0), { x: 0, z: 1 })
  assert.equal(through.length, 1)
  assert.deepEqual([through[0].x, through[0].z], [0, 1])
  assert.equal(through[0].toPlace.length, 0) // pathfinder に切り替えさせない（開けるのは doorOpener）
  assert.equal(forward(m, new Vec3(-1, 64, 1).offset(0, 0, 0), { x: 1, z: 0 }).length, 0)
  // 開いたドアも、壊して通るものとは扱わない
  const open = movements(world({ doors: { '0,1': { name: 'spruce_door', facing: 'north', open: true } } }))
  assert.equal(forward(open, new Vec3(0, 64, 0), { x: 0, z: 1 })[0].toBreak.length, 0)
})

test('斜めには通れない。鉄のドアと家・前の家のドアは今まで通り通れない（家はコードが出入りする）', () => {
  const door = { '0,1': { name: 'oak_door', facing: 'north' } }
  const m = movements(world({ doors: door }))
  const diagonal = []
  m.getMoveDiagonal({ x: -1, y: 64, z: 0, remainingBlocks: 0 }, { x: 1, z: 1 }, diagonal)
  assert.equal(diagonal.length, 0)
  const iron = movements(world({ doors: { '0,1': { name: 'iron_door', facing: 'north' } } }))
  assert.equal(forward(iron, new Vec3(0, 64, 0), { x: 0, z: 1 }).length, 0)
  const home = movements(world({ doors: door }), { home: { door: new Vec3(0, 64, 1) } })
  assert.equal(forward(home, new Vec3(0, 64, 0), { x: 0, z: 1 }).length, 0)
  const former = movements(world({ doors: door }), { formerHomes: [{ door: new Vec3(0, 64, 1) }] })
  assert.equal(forward(former, new Vec3(0, 64, 0), { x: 0, z: 1 }).length, 0)
})

test('柵のゲートも、向きに沿ってまっすぐなら通れる', () => {
  const m = movements(world({ doors: { '0,1': { name: 'oak_fence_gate', facing: 'south' } } }))
  assert.equal(forward(m, new Vec3(0, 64, 0), { x: 0, z: 1 }).length, 1)
})

function walker (w, m) {
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0.5, 64, -1.5) }
  bot.blockAt = w.blockAt
  bot.pathfinder = { movements: m }
  bot.activated = []
  bot.waitForTicks = async () => {}
  bot.activateBlock = async (b) => {
    bot.activated.push(`${b.position.x},${b.position.y},${b.position.z}`)
    // 両方の半分を切り替える
    const lower = b.getProperties().half === 'upper' ? b.position.offset(0, -1, 0) : b.position
    for (const dy of [0, 1]) {
      const k = `${lower.x},${lower.y + dy},${lower.z}`
      const cur = w.set.get(k)
      if (!cur) continue
      const next = Block.fromProperties(cur.name, { ...cur.getProperties(), open: !cur.getProperties().open }, 0)
      next.position = cur.position
      w.set.set(k, next)
    }
  }
  return bot
}

// pathfinder の経路のノードはセルの真ん中
const nodes = (...ps) => ps.map(([x, z]) => ({ x: x + 0.5, y: 64, z: z + 0.5 }))

test('歩いている間: 経路の先の閉じたドアは近づいたら開け、通り過ぎたら閉める', async () => {
  const w = world({ doors: { '0,1': { name: 'oak_door', facing: 'north' } } })
  const bot = walker(w, movements(w))
  const doors = doorOpener(bot)
  bot.emit('path_update', { path: nodes([0, -1], [0, 0], [0, 1], [0, 2], [0, 3]) })
  await doors.tick()
  assert.deepEqual(bot.activated, [])  // まだ遠い（3m より先）
  bot.entity.position = new Vec3(0.5, 64, 0.5)
  bot.emit('path_update', { path: nodes([0, 1], [0, 2], [0, 3]) })
  await doors.tick()
  assert.deepEqual(bot.activated, ['0,64,1'])
  assert.equal(w.blockAt(new Vec3(0, 65, 1)).getProperties().open, true)
  // ドアの中ではまだ閉めない。抜けて離れたら閉める
  bot.entity.position = new Vec3(0.5, 64, 1.5)
  bot.emit('path_update', { path: nodes([0, 2], [0, 3]) })
  await doors.tick()
  assert.equal(bot.activated.length, 1)
  bot.entity.position = new Vec3(0.5, 64, 3.5)
  bot.emit('path_update', { path: nodes([0, 4]) })
  await doors.tick()
  assert.deepEqual(bot.activated, ['0,64,1', '0,64,1'])
  assert.equal(w.blockAt(new Vec3(0, 64, 1)).getProperties().open, false)
  await doors.stop()
})

test('初めから開いていたドアは閉めない。家のドアは開けない。歩き終わったら、開けたままの届くドアを閉める', async () => {
  const w = world({ doors: { '0,1': { name: 'oak_door', facing: 'north', open: true }, '0,3': { name: 'birch_door', facing: 'north' } } })
  const home = world({ doors: { '0,1': { name: 'oak_door', facing: 'north' } } })
  const homeBot = walker(home, movements(home, { home: { door: new Vec3(0, 64, 1) } }))
  const homeDoors = doorOpener(homeBot)
  homeBot.entity.position = new Vec3(0.5, 64, 0.5)
  homeBot.emit('path_update', { path: nodes([0, 1], [0, 2]) })
  await homeDoors.tick()
  assert.deepEqual(homeBot.activated, [])
  await homeDoors.stop()

  const bot = walker(w, movements(w))
  const doors = doorOpener(bot)
  bot.entity.position = new Vec3(0.5, 64, 1.5)
  bot.emit('path_update', { path: nodes([0, 2], [0, 3], [0, 4]) })
  await doors.tick()
  assert.deepEqual(bot.activated, ['0,64,3'])
  bot.entity.position = new Vec3(0.5, 64, 2.5)
  await doors.stop()
  assert.deepEqual(bot.activated, ['0,64,3', '0,64,3'])
  assert.equal(w.blockAt(new Vec3(0, 64, 1)).getProperties().open, true)
})

test('経路のドアのノードは、扉の板の上（pathfinder の直し）から床の上のセルの真ん中に戻す', async () => {
  const w = world({ doors: { '0,1': { name: 'oak_door', facing: 'north' } } })
  const bot = walker(w, movements(w))
  const doors = doorOpener(bot)
  // 実機で見た形: ドアのセルが y+1、板の真ん中の x になっていた
  const path = [{ x: 0.5, y: 64, z: 0.5 }, { x: 0.703125, y: 65, z: 1.5 }, { x: 0.5, y: 64, z: 2.5 }]
  bot.emit('path_update', { path })
  assert.deepEqual(path.map((n) => [n.x, n.y, n.z]), [[0.5, 64, 0.5], [0.5, 64, 1.5], [0.5, 64, 2.5]])
  await doors.stop()
})
