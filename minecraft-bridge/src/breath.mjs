// 息: 頭が水の中にいて息が続かなくなりそうなら、息のできる所へ泳ぐ（docs/design/69_breath_reflex.md）。
//
// 反射（reflex.mjs）が使う。経路探索器（pathfinder）は水の中から上がる手を作らないので、水と空気のセルの
// 幅優先探索で最短の道を見つけ、向きとジャンプ・スニークを直接押して泳ぐ。

import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg

export const MAX_AIR = 20 // 満タンの気泡（300 ティック = 15 秒）
export const TICKS_PER_BUBBLE = 15
export const PLAN_AT = 16 // 気泡がこれ以下で頭が水の中なら、出る道を調べる
export const STRAIGHT_UP_AT = 6 // 道がなくても、これ以下になれば真上へ泳ぐ
const SWIM_BLOCKS_PER_S = 1.5 // 泳ぐ速さ（仮の値。実機で測って直す）
const MARGIN_TICKS = 60 // 着いても息が残るように、3 秒の余裕
const MAX_CELLS = 4000
const SURFACE_MS = 20000
const REFILL_AT = 16 // 着いたら、息がここまで戻るまで（最長 REFILL_MS）浮いている
const REFILL_MS = 3000
const EYE_HEIGHT = 1.62

const WET = /^(water|bubble_column|kelp|kelp_plant|seagrass|tall_seagrass)$/
const HAZARD = /lava|fire|cobweb|powder_snow|sweet_berry_bush|magma/

export function holdsWater (block) {
  if (!block) return false
  if (WET.test(block.name)) return true
  const w = block.getProperties?.().waterlogged
  return w === true || w === 'true'
}

// 通れるセル: 当たり判定がなく（水も空気も昆布も）、溶岩や火でない
export const passable = (block) => !!block && block.boundingBox === 'empty' && !HAZARD.test(block.name)
const solid = (block) => !!block && block.boundingBox === 'block'

export const airLevel = (bot) => bot.oxygenLevel ?? MAX_AIR

export function headUnderwater (bot) {
  const p = bot.entity?.position
  if (!p) return false
  return holdsWater(bot.blockAt(p.offset(0, bot.entity.eyeHeight ?? EYE_HEIGHT, 0)))
}

// 水中呼吸・潮流の力（コンジット）の効果が付いているか（効果の名前は、データにより WaterBreathing など）
export function breathesWater (bot) {
  const effects = bot.entity?.effects ?? {}
  return (bot.registry?.effectsArray ?? []).some((e) => /^(water_?breathing|conduit_?power)$/i.test(e.name) && effects[e.id])
}

const key = (c) => `${c.x},${c.y},${c.z}`

// 立てる（浮ける）セル: 足のセルと頭のセルが通れる
function roomFor (bot, c) {
  return passable(bot.blockAt(c)) && passable(bot.blockAt(c.offset(0, 1, 0)))
}

// 息のできるセル: 足のセルが水で頭のセルが空気（水面）、または足も頭も空気で、下が固いか水（着地できる）
function breathable (bot, c) {
  const feet = bot.blockAt(c)
  const head = bot.blockAt(c.offset(0, 1, 0))
  if (!passable(feet) || !passable(head) || holdsWater(head)) return false
  if (holdsWater(feet)) return true
  const below = bot.blockAt(c.offset(0, -1, 0))
  return solid(below) || holdsWater(below)
}

const MOVES = [[0, 1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, -1, 0]] // 上を先に

// いまの足のセルから、息のできるセルまでの最短の道（足のセルの並び。今いるセルは含まない）。
// すでに息のできるセルなら []。見つからなければ null
export function findAir (bot, from = bot.entity.position, maxCells = MAX_CELLS) {
  const start = new Vec3(Math.floor(from.x), Math.floor(from.y), Math.floor(from.z))
  if (breathable(bot, start)) return []
  const prev = new Map([[key(start), null]])
  const queue = [start]
  for (let i = 0; i < queue.length && prev.size < maxCells; i++) {
    const c = queue[i]
    for (const [dx, dy, dz] of MOVES) {
      const n = c.offset(dx, dy, dz)
      const k = key(n)
      if (prev.has(k) || !roomFor(bot, n)) continue
      prev.set(k, c)
      if (breathable(bot, n)) {
        const path = []
        for (let p = n; p && key(p) !== key(start); p = prev.get(key(p))) path.unshift(p)
        return path
      }
      queue.push(n)
    }
  }
  return null
}

// 道のりを泳ぐのに要るティック
export const ticksToSwim = (cells) => Math.ceil((cells / SWIM_BLOCKS_PER_S) * 20)

// 反射のきっかけ: 頭が水の中で息が続かなくなりそうなら { path, air }（path は null か []）、そうでなければ null。
// 探索は息が PLAN_AT 以下のときだけ（軽くはない）。呼ぶ側が間隔を空ける
export function breathTrigger (bot) {
  if (!bot.entity) return null
  const air = airLevel(bot)
  if (air > PLAN_AT || !headUnderwater(bot) || breathesWater(bot)) return null
  const path = findAir(bot)
  const left = air * TICKS_PER_BUBBLE
  if (path === null) return air <= STRAIGHT_UP_AT ? { path: null, air } : null
  if (left > ticksToSwim(path.length) + MARGIN_TICKS) return null
  return { path, air }
}

// 水の中の見え方（needs、観測）: 頭が水の中なら { air }、そうでなければ null
export function underwater (bot) {
  return headUnderwater(bot) ? { air: airLevel(bot) } : null
}

// 欲求（goals.mjs の needs）に出す文: 頭が水の中なら息を。息が減っていれば浮上を促す（なければ null）
export function waterNeed (bot) {
  const wet = underwater(bot)
  if (!wet) return null
  return `your head is underwater (air ${wet.air}/20)` +
    (wet.air <= PLAN_AT ? ': swim up to the surface now (the reflex does it when the air runs low)' : '')
}

// path を泳ぐ。次のセルへ向きを合わせ、上ならジャンプ、下ならスニーク、横なら前進。着いたら、息が戻るまで浮く。
// 道がなければ（null）真上へ。戻り値は結果の文
export async function surface (bot, path, signal) {
  const t0 = Date.now()
  const cells = path ?? []
  let i = 0
  let arrived = null
  const feet = () => bot.entity.position
  try {
    while (Date.now() - t0 < SURFACE_MS) {
      signal?.throwIfAborted()
      const me = feet()
      if (i >= cells.length) {
        // 着いた（または真上へ）: 頭が水から出て、息が戻るまで浮く
        arrived ??= Date.now()
        if (!headUnderwater(bot) && (airLevel(bot) >= REFILL_AT || Date.now() - arrived > REFILL_MS)) break
        bot.setControlState('forward', false)
        bot.setControlState('sneak', false)
        bot.setControlState('jump', true)
        await bot.waitForTicks(1)
        continue
      }
      const target = cells[i].offset(0.5, 0, 0.5)
      const dx = target.x - me.x
      const dz = target.z - me.z
      const flat = Math.hypot(dx, dz)
      const dy = target.y - me.y
      if (flat < 0.35 && Math.abs(dy) < 0.7) { i++; continue }
      if (flat > 0.25) await bot.lookAt(new Vec3(target.x, me.y + EYE_HEIGHT, target.z), true)
      bot.setControlState('forward', flat > 0.25)
      bot.setControlState('jump', dy > -0.2)
      bot.setControlState('sneak', dy < -0.6)
      await bot.waitForTicks(1)
    }
  } finally {
    for (const c of ['forward', 'jump', 'sneak']) bot.setControlState(c, false)
  }
  const out = headUnderwater(bot)
  return `${out ? 'still underwater' : 'surfaced'} after ${((Date.now() - t0) / 1000).toFixed(1)}s (air ${airLevel(bot)}/${MAX_AIR}, ${path ? `${path.length} cells` : 'straight up'})`
}
