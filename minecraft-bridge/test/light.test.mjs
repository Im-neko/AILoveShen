// サーバーが送る光の読み方（docs/design/104）。ライブラリ（prismarine-chunk）が x の並びを入れ替えて読むのを戻す
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import vec3Pkg from 'vec3'
import minecraftData from 'minecraft-data'
import { blockLightAt, skyLightAt, tooDarkAt, mirrorX, MIN_LIGHT } from '../src/light.mjs'
import { isDark, burningInDaylight } from '../src/observe.mjs'
import { lightWorld } from './light-world.mjs'

const { Vec3 } = vec3Pkg
const md = minecraftData('1.21.4')
const require = createRequire(import.meta.url)
const Chunk = require('prismarine-chunk')('1.21.4')

test('mirrorX: 16 マスの中で、バイトの並びだけが逆になる。2 回かけると元に戻る', () => {
  assert.deepEqual([0, 1, 2, 3, 14, 15].map(mirrorX), [14, 15, 12, 13, 0, 1])
  for (let x = 0; x < 16; x++) assert.equal(mirrorX(mirrorX(x)), x)
})

test('本物のライブラリは、光の x を入れ替えて読む（松明の 14 が 2 マス横に出る）。light.mjs は本当の位置で読む', () => {
  const column = new Chunk({ minY: -64, worldHeight: 384 })
  // サーバーが送る形: 1 バイトに 2 マス（偶数の x が下の 4 ビット）、x が 1 ずつ並ぶ。(x 7, y 59, z 5) が松明、両隣は 13、その隣は 12
  const buf = Buffer.alloc(2048)
  const set = (x, ly, z, v) => {
    const i = (ly << 8) | (z << 4) | x
    buf[i >> 1] = i & 1 ? (buf[i >> 1] & 0x0f) | (v << 4) : (buf[i >> 1] & 0xf0) | v
  }
  set(7, 11, 5, 14); set(6, 11, 5, 13); set(8, 11, 5, 13); set(9, 11, 5, 12)
  const none = [[0, 0]]
  column.loadParsedLight([], [buf], none, [[0, 1 << 8]], none, none) // 59 → 欄 (59 + 64) >> 4 = 7、下に 1 つ余分で 8
  const bot = { world: { getColumnAt: () => column } }
  const read = (x) => ({ lib: column.getBlockLight({ x, y: 59, z: 5 }), fixed: blockLightAt(bot, { x, y: 59, z: 5 }) })
  // ライブラリの読み（これが直ったらこのテストが落ちる: light.mjs の戻しを外す）
  assert.deepEqual([6, 7, 8, 9].map((x) => read(x).lib), [13, 12, 13, 14])
  assert.deepEqual([6, 7, 8, 9].map((x) => read(x).fixed), [13, 14, 13, 12])
  assert.equal(blockLightAt(bot, { x: 7.9, y: 59.5, z: 5.2 }), 14, '小数の座標は切り捨てて読む')
})

test('光の欄がない（まだ届いていない）マスは null で、暗いと決めつけない。足りないのは MIN_LIGHT 未満と分かるときだけ', () => {
  assert.equal(blockLightAt({}, new Vec3(0, 60, 0)), null, '世界がない')
  assert.equal(blockLightAt({ world: { getColumnAt: () => null } }, new Vec3(0, 60, 0)), null, '柱がない')
  const noLight = { world: { getColumnAt: () => ({ minY: -64, blockLightSections: [] }) } }
  assert.equal(blockLightAt(noLight, new Vec3(0, 60, 0)), null, '欄がない')
  assert.equal(skyLightAt(noLight, new Vec3(0, 60, 0)), null)
  assert.equal(tooDarkAt(noLight, new Vec3(0, 60, 0)), false)
  const at = (light) => ({ world: lightWorld(() => light) })
  assert.deepEqual([0, 1, 2, 14].map((l) => tooDarkAt(at(l), new Vec3(0, 60, 0))), [true, true, false, false])
  assert.equal(MIN_LIGHT, 2)
})

test('世界の座標をそのまま読む: 柱の境（x, z が負、16 の倍数）も、高さの境も', () => {
  const lit = new Set(['-1,59,-1', '16,60,-17', '-16,-64,0', '0,319,0'])
  const bot = { world: lightWorld((x, y, z) => (lit.has(`${x},${y},${z}`) ? 9 : 0)) }
  for (const k of lit) assert.equal(blockLightAt(bot, new Vec3(...k.split(',').map(Number))), 9, k)
  assert.equal(blockLightAt(bot, new Vec3(0, 59, 0)), 0)
})

// 0,70,0 に立つ。頭上 73 が石（屋根）の洞窟
function cave ({ light, torchAt = null } = {}) {
  return {
    entity: { position: new Vec3(0.5, 70, 0.5) },
    registry: md,
    world: light === undefined ? undefined : lightWorld(() => light),
    blockAt: (p) => (p.y < 70 || p.y === 73 ? { name: 'stone', boundingBox: 'block' } : { name: 'air', boundingBox: 'empty' }),
    findBlock: ({ maxDistance }) => (torchAt !== null && torchAt <= maxDistance ? { name: 'torch' } : null)
  }
}

test('isDark: 足元の光が分かればそれで決める。壁の向こうの松明では明るくならず、遠くの松明の光が届いていれば暗くない', () => {
  assert.equal(isDark(cave({ light: 0, torchAt: 6 })), true, '松明が 6 先にあっても、光が 0 なら暗い（壁の向こう）')
  assert.equal(isDark(cave({ light: 1 })), true, 'MIN_LIGHT 未満')
  assert.equal(isDark(cave({ light: 2 })), false)
  assert.equal(isDark(cave({ light: 8 })), false, '光源が近くになくても、光が届いていれば暗くない')
  assert.equal(isDark(cave({ light: 0, torchAt: 1 })), false, 'いま置いた松明の光がまだ届いていない')
})

test('isDark: 光が分からなければ、近く（8 マス）の光源で見る', () => {
  assert.equal(isDark(cave()), true)
  assert.equal(isDark(cave({ torchAt: 6 })), false)
  assert.equal(isDark(cave({ torchAt: 9 })), true)
})

test('burningInDaylight: 日光で燃えるモブの頭の高さの空の光が 15 のときだけ（入れ替わっていない光で読む）', () => {
  const at = (sky) => ({ time: { timeOfDay: 1000 }, world: lightWorld(() => 0, sky) })
  const zombie = { name: 'zombie', height: 1.95, position: new Vec3(3.5, 70, 5.5) }
  assert.equal(burningInDaylight(at((x, y) => (y >= 71 ? 15 : 0)), zombie), true, '頭の高さ（71.95 → 71）から上が空の光 15')
  assert.equal(burningInDaylight(at(() => 14), zombie), false, '15 未満')
  assert.equal(burningInDaylight({ time: { timeOfDay: 1000 } }, zombie), false, '光が届いていない')
  assert.equal(burningInDaylight(at(() => 15), { ...zombie, name: 'husk' }), false, '燃えないモブ')
  assert.equal(burningInDaylight({ ...at(() => 15), time: { timeOfDay: 18000 } }, zombie), false, '夜')
})
