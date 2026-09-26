import { test } from 'node:test'
import assert from 'node:assert/strict'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { Knowledge } from '../src/knowledge.mjs'
import { solve } from '../src/solver.mjs'
import { PRIMITIVES } from '../src/primitives.mjs'

const { Vec3 } = vec3Pkg
const v = (x, y, z) => new Vec3(x, y, z)
const md = minecraftData('1.21.4')
const k = new Knowledge(md)

// 木のツルハシを持ち、近くに見える石はない。石は 4 段下に埋まっている
const world = (digDepth, buried = [{ pos: v(3, 66, 0), depth: 4 }]) => ({
  inventory: { wooden_pickaxe: 1 },
  stored: {},
  blocks: {},
  mobs: {},
  table: 'near',
  unlocked: () => true,
  buried: (name) => (name === 'stone' ? buried : []),
  digDepth
})
const cobble = [{ spec: 'cobblestone', count: 3 }]

test('許された深さに埋まった石があれば、探すより階段で掘り下げる', () => {
  const r = solve(k, world(8), cobble)
  assert.deepEqual(r.leaves.map((l) => [l.kind, l.sources, l.depth]), [['dig_down', ['stone'], 4]])
  assert.ok(r.lines.some((l) => l.includes('dig stairs down to stone (4 blocks down)')), r.lines.join('\n'))
})

test('深さが足りなければ探し、何段下にあるかと、掘り下げるのに要る深さを言う', () => {
  for (const depth of [0, 3]) {
    const r = solve(k, world(depth), cobble)
    assert.equal(r.leaves[0].kind, 'explore')
    assert.match(r.blocked.join('\n'), /stone is buried 4 blocks down \(the goal's dig_depth [03] is shallower: leave it out to dig down\)/)
  }
  // 埋まったものもなければ、今までどおり探すだけ
  const none = solve(k, world(8, []), cobble)
  assert.equal(none.leaves[0].kind, 'explore')
  assert.doesNotMatch(none.blocked.join('\n'), /buried/)
})

// 階段掘りのボット: y 70 が地面（上は空気）。blocks で個別のブロックを上書き。掘ると空気になり、
// 歩く先へすぐ移る
function stairBot (blocks = {}) {
  const world = new Map(Object.entries(blocks))
  const key = (p) => `${p.x},${p.y},${p.z}`
  const bot = {
    entity: { position: v(0.5, 70, 0.5), onGround: true },
    entities: {},
    registry: md,
    dug: [],
    blockAt: (p) => {
      const name = world.get(key(p)) ?? (p.y < 70 ? 'dirt' : 'air')
      return { name, position: p, boundingBox: name === 'air' || name === 'water' || name === 'lava' ? 'empty' : 'block' }
    },
    dig: async (b) => { bot.dug.push(key(b.position)); world.set(key(b.position), 'air') },
    equip: async () => {},
    waitForTicks: async () => {},
    findBlocks: () => [],
    on: () => {},
    off: () => {},
    inventory: { items: () => [], emptySlotCount: () => 30 },
    pathfinder: {
      bestHarvestTool: () => null,
      goto: async (g) => { bot.entity.position = v(g.x + 0.5, g.y, g.z + 0.5) },
      setGoal: () => {}
    }
  }
  return bot
}
const stateFor = (depth) => ({ home: null, plan: null, unreachableDrops: new Set(), unreachableBlocks: new Set(), goal: { surfaceY: 70, spec: { dig_depth: depth } } })
const toward = { verb: 'dig_down', target: 'stone', pos: v(20, 60, 0) }
const { signal } = new AbortController()

test('階段は前に 1、下に 1 ずつ掘り、真下は掘らない。許された深さで止まる', async () => {
  const bot = stairBot()
  const r = await PRIMITIVES.dig_down(bot, stateFor(3), toward, signal)
  assert.match(r, /^dug 3 steps down toward stone \(3 blocks below where the goal started\); stopped: dig_depth 3 reached/)
  // 石のある東へ。地面の上（頭と足のマス）は空いているので、1 段目は下の (1,69) だけ、2 段目は (2,69) と (2,68)
  assert.deepEqual(bot.dug.slice(0, 3), ['1,69,0', '2,69,0', '2,68,0'])
  assert.deepEqual(bot.entity.position.floored(), v(3, 67, 0))
  assert.ok(!bot.dug.includes('0,69,0')) // 立っている所の真下
})

test('溶岩・水のそば、砂利の下は避け、どの向きも下りられなければ理由を言う', async () => {
  // 東の 1 段下の隣に溶岩: 東には下りず、ほかの向きへ
  const lava = stairBot({ '2,69,0': 'lava' })
  await PRIMITIVES.dig_down(lava, stateFor(1), toward, signal)
  assert.ok(!lava.dug.includes('1,70,0'), lava.dug.join(' '))

  const blocks = {}
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) blocks[`${dx},72,${dz}`] = 'gravel'
  const gravel = stairBot(blocks)
  const state = stateFor(8)
  await assert.rejects(PRIMITIVES.dig_down(gravel, state, toward, signal), /sand or gravel above would fall in/)
  assert.ok(state.unreachableBlocks.has('20,60,0')) // その石はもう候補に出さない
})

test('足元の下が空洞なら、そこで止まる（洞窟の壁で石が見える）', async () => {
  const bot = stairBot({ '1,68,0': 'air', '-1,68,0': 'air', '0,68,1': 'air', '0,68,-1': 'air' })
  await assert.rejects(PRIMITIVES.dig_down(bot, stateFor(8), toward, signal), /a cave or a drop below the next step/)
})
