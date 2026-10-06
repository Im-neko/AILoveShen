// 家のドア: 開かなければ手を変えて試す、それでも開かなければ壊して通り、置き直す。ドアがなければ開いている
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import vec3Pkg from 'vec3'
import { setDoor, openDoorOrBreak, restoreDoor, isDoorOpen, DoorStuckError } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const DOOR = v(10, 70, 5)
const home = { door: DOOR, inside: v(10, 70, 4), outside: v(10, 70, 6) }

process.env.DOOR_TOGGLES_FILE = path.join(os.tmpdir(), `door-toggles-${process.pid}.jsonl`)

// 世界: ドアの下の段・上の段・床。works(n): n 回目の押下でドアが動くか
function fake ({ works = () => false, items = [] } = {}) {
  const blocks = new Map()
  const key = (p) => `${p.x},${p.y},${p.z}`
  const put = (p, name, props = {}) => blocks.set(key(p), { name, position: p, boundingBox: name === 'air' ? 'empty' : 'block', getProperties: () => props, props })
  put(DOOR, 'oak_door', { open: 'false' })
  put(DOOR.offset(0, 1, 0), 'oak_door', { open: 'false' })
  put(DOOR.offset(0, -1, 0), 'grass_block')
  const listeners = []
  const log = { presses: [], dug: 0, placed: 0 }
  const bot = {
    entity: { position: v(10.5, 70, 6.5), yaw: 0, pitch: 0, onGround: true, eyeHeight: 1.62 },
    heldItem: { name: 'wooden_sword' },
    controlState: {},
    inventory: { items: () => items },
    blockAt: (p) => blocks.get(key(p)) ?? { name: 'air', position: p, boundingBox: 'empty', getProperties: () => ({}) },
    on: (e, f) => listeners.push([e, f]),
    removeListener: (e, f) => { const i = listeners.findIndex(([x, g]) => x === e && g === f); if (i >= 0) listeners.splice(i, 1) },
    waitForTicks: () => new Promise((resolve) => setTimeout(resolve, 2)),
    lookAt: async () => {},
    setControlState: () => {},
    unequip: async () => { bot.heldItem = null },
    equip: async (item) => { bot.heldItem = item },
    activateBlock: async (block) => {
      log.presses.push(block.position.y)
      if (works(log.presses.length)) {
        const open = block.props.open === 'true' ? 'false' : 'true'
        for (const p of [DOOR, DOOR.offset(0, 1, 0)]) put(p, 'oak_door', { open })
      }
    },
    dig: async (block) => { log.dug++; put(block.position, 'air'); put(block.position.offset(0, 1, 0), 'air') },
    stopDigging: () => {},
    placeBlock: async () => { log.placed++; put(DOOR, 'oak_door', { open: 'false' }); put(DOOR.offset(0, 1, 0), 'oak_door', { open: 'false' }) }
  }
  return { bot, log, put }
}

const lastRecord = () => JSON.parse(fs.readFileSync(process.env.DOOR_TOGGLES_FILE, 'utf8').trim().split('\n').at(-1))

test('ドアが最初の押下で開く', async () => {
  const { bot, log } = fake({ works: (n) => n === 1 })
  await setDoor(bot, home, true)
  assert.equal(log.presses.length, 1)
  assert.equal(isDoorOpen(bot, home), true)
})

test('最初の手で開かなくても、次の手で開けば進める（スニークを外し、素手で押す）。記録に残る', async () => {
  const { bot, log } = fake({ works: (n) => n === 2 })
  await setDoor(bot, home, true)
  assert.equal(log.presses.length, 2)
  assert.equal(bot.heldItem, null)
  const rec = lastRecord()
  assert.equal(rec.opened, true)
  assert.equal(rec.tries.length, 2)
})

test('どの手でも開かなければ DoorStuckError。記録に試した手とドアの状態が残る', async () => {
  const { bot, log } = fake()
  await assert.rejects(() => setDoor(bot, home, true), (e) => e instanceof DoorStuckError && /door did not open/.test(e.message))
  assert.equal(log.presses.length, 3)
  const rec = lastRecord()
  assert.equal(rec.opened, false)
  assert.equal(rec.door.name, 'oak_door')
  assert.equal(rec.tries.length, 3)
  assert.equal(rec.bot.sneak, false)
})

test('開かないドアは壊して通る。壊したら true。ドアがなければ、そこは開いている', async () => {
  const { bot, log } = fake()
  assert.equal(await openDoorOrBreak(bot, home, new AbortController().signal), true)
  assert.equal(log.dug, 1)
  assert.equal(isDoorOpen(bot, home), true)
})

test('開くドアは壊さない', async () => {
  const { bot, log } = fake({ works: () => true })
  assert.equal(await openDoorOrBreak(bot, home, new AbortController().signal), false)
  assert.equal(log.dug, 0)
})

test('壊したドアは、拾ってあれば元の場所に置き直す。拾っていなければ理由を返す', async () => {
  const { bot, put, log } = fake({ items: [{ name: 'oak_door', count: 1 }] })
  put(DOOR, 'air'); put(DOOR.offset(0, 1, 0), 'air')
  assert.equal(await restoreDoor(bot, home), '')
  assert.equal(log.placed, 1)
  assert.equal(bot.blockAt(DOOR).name, 'oak_door')

  const none = fake()
  none.put(DOOR, 'air')
  assert.match(await restoreDoor(none.bot, home), /not picked up/)
})
