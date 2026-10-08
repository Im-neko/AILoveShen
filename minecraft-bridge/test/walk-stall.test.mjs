import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import { describeWalkStall, walkInto } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const CELL = v(44, 68, -199)

function fakeBot ({ at = v(44.5, 68, -199.44), blocks = {}, controls = { forward: true }, goal = null, moves = false } = {}) {
  const block = (p) => {
    const spec = blocks[`${p.x},${p.y},${p.z}`] ?? 'air'
    const name = typeof spec === 'string' ? spec : spec.name
    return { name, getProperties: () => (typeof spec === 'string' ? {} : spec.props) }
  }
  const bot = {
    entity: { position: at.clone(), velocity: v(0, 0, 0), yaw: -3.14, eyeHeight: 1.62, onGround: true },
    controlState: { ...controls },
    pathfinder: { goal },
    blockAt: block,
    lookAt: async () => {},
    setControlState: (k, on) => { bot.controlState[k] = on },
    clearControlStates: () => { bot.controlState = {} },
    waitForTicks: async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
      if (moves) bot.entity.position.z += 0.2
    }
  }
  return bot
}

const DOOR = { name: 'oak_door', props: { half: 'lower', open: true, facing: 'south' } }

test('進めなかったときの様子に、位置・速度・押しているキー・経路探しの目標・行き先の床足頭が入る', () => {
  const bot = fakeBot({ controls: { forward: true, sprint: false, sneak: false }, blocks: { '44,67,-199': 'grass_block', '44,68,-199': DOOR, '44,69,-199': { name: 'oak_door', props: { half: 'upper', open: true, facing: 'south' } } }, goal: {} })
  const why = describeWalkStall(bot, CELL)
  assert.match(why, /from 44\.50,68\.00,-199\.44/)
  assert.match(why, /velocity 0\.00,0\.00,0\.00 on_ground true/)
  assert.match(why, /controls \[forward\]/)
  assert.match(why, /pathfinder goal set/)
  assert.match(why, /floor grass_block, feet oak_door\(lower open=true facing=south\), head oak_door\(upper open=true facing=south\)/)
})

test('押しているキーがなく、経路探しの目標もなければ、そう書く', () => {
  const bot = fakeBot({ controls: {}, blocks: {} })
  const why = describeWalkStall(bot, CELL)
  assert.match(why, /controls \[\]/)
  assert.match(why, /pathfinder goal none/)
})

test('読み込まれていないセルは unloaded と書く', () => {
  const bot = fakeBot()
  bot.blockAt = () => null
  assert.match(describeWalkStall(bot, CELL), /floor unloaded, feet unloaded, head unloaded/)
})

test('歩いて通れなければ、理由つきで投げる（先頭の文言はいままでどおり）', async () => {
  const bot = fakeBot({ blocks: { '44,67,-199': 'grass_block' } })
  await assert.rejects(() => walkInto(bot, CELL), (e) => {
    assert.match(e.message, /^could not walk to \(44, 68, -199\) \(from 44\.50,68\.00,-199\.44 /)
    assert.match(e.message, /floor grass_block/)
    return true
  })
  assert.deepEqual(bot.controlState, {})
})

test('進めるときは、そのまま着く', async () => {
  const bot = fakeBot({ moves: true })
  await walkInto(bot, CELL)
  assert.ok(bot.entity.position.z > -198.9)
})
