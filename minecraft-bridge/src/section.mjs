// 縦の断面（設計書 190）: 自分の位置から 1 方向へ、高さごとの並びを文字で見る。
//
// 周囲の情報は真上から見た高さしかなく、湖の岸の高さ（3〜4 ブロック上）や、岩の厚み、頭の上や足元に何があるかは
// 横から見ないと分からない（2026-10-18: 水の中で岸の高さが分からず 17 分動けなかった）。
// 見えないところまで知っている前提では動かない（視聴者に違和感がある）ので、見える所だけ書く:
// 空気・水・溶岩と、それらに面した固いブロック（岸、洞窟の壁、地表）。岩の中は中身を言わず '#'（見えない岩）。

import vec3Pkg from 'vec3'

const { Vec3 } = vec3Pkg

const DIRECTIONS = { north: [0, -1], south: [0, 1], east: [1, 0], west: [-1, 0] }
const LENGTH = { default: 12, max: 24 }
const DOWN = { default: 12, max: 24 }
const UP = { default: 4, max: 12 }
const ORIGIN_REACH = 32 // 起点に選べる、自分からの距離

const WATER = /^(water|bubble_column|seagrass|tall_seagrass|kelp|kelp_plant)$/
const SYMBOLS = [
  [/^(stone|andesite|diorite|granite|deepslate|tuff|cobblestone|calcite|basalt|netherrack)$/, 'S'],
  [/^(dirt|grass_block|coarse_dirt|podzol|mud|rooted_dirt|dirt_path|farmland|mycelium|clay)$/, 'D'],
  [/^gravel$/, 'g'],
  [/^(sand|red_sand|sandstone|red_sandstone)$/, 's'],
  [/_log$|_wood$/, 'T'],
  [/_leaves$/, 'l'],
  [/_ore$/, 'O'],
  [/^(ice|packed_ice|blue_ice|snow_block|powder_snow|snow)$/, 'i'],
  [/^bedrock$/, 'B'],
  [/^lava$/, 'L']
]
const SPARE = 'abcdefhjkmnpqrtuvwxyz'.split('') // 表にない名前に順に当てる文字

const passable = (b) => b?.boundingBox === 'empty'
const facing = (bot) => {
  const yaw = bot.entity.yaw ?? 0
  const dx = -Math.sin(yaw)
  const dz = -Math.cos(yaw)
  return Math.abs(dx) > Math.abs(dz) ? (dx > 0 ? 'east' : 'west') : (dz > 0 ? 'south' : 'north')
}

// 固いブロックが見えるか: 空気・水・溶岩・草花など、通れる所か液体に 1 面でも触れていれば見える
const exposed = (get, x, y, z) => [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
  .some(([a, b, c]) => {
    const n = get(x + a, y + b, z + c)
    return n && (passable(n) || n.name === 'lava')
  })

export function crossSection (bot, args = {}) {
  const me = bot.entity.position
  const floor = { x: Math.floor(me.x), y: Math.floor(me.y), z: Math.floor(me.z) }
  const origin = { x: args.x ?? floor.x, z: args.z ?? floor.z }
  const clean = (v, limits) => Math.max(0, Math.min(limits.max, Math.floor(Number(v ?? limits.default))))
  if (Math.abs(origin.x - floor.x) > ORIGIN_REACH || Math.abs(origin.z - floor.z) > ORIGIN_REACH) {
    throw new Error(`cannot take a section starting ${Math.round(Math.hypot(origin.x - floor.x, origin.z - floor.z))} blocks from you (at most ${ORIGIN_REACH}): go closer, or leave x and z out to start at your feet`)
  }
  const direction = args.direction ?? facing(bot)
  const step = DIRECTIONS[direction]
  if (!step) throw new Error(`unknown direction "${args.direction}": use north, south, east or west`)
  const length = Math.max(1, clean(args.length, LENGTH))
  const down = clean(args.down, DOWN)
  const up = clean(args.up, UP)
  const y0 = args.y ?? floor.y
  const top = y0 + up
  const bottom = y0 - down

  const cache = new Map()
  const get = (x, y, z) => {
    const k = `${x},${y},${z}`
    if (!cache.has(k)) cache.set(k, bot.blockAt(new Vec3(x, y, z)) ?? null)
    return cache.get(k)
  }
  const legend = new Map() // 文字 → 名前の集合
  const spare = [...SPARE]
  const assigned = new Map() // 表にない名前 → 文字
  const mark = (ch, name) => {
    if (!legend.has(ch)) legend.set(ch, new Set())
    legend.get(ch).add(name)
    return ch
  }
  const charOf = (name) => {
    if (WATER.test(name)) return '~'
    for (const [re, ch] of SYMBOLS) if (re.test(name)) return ch
    if (!assigned.has(name)) assigned.set(name, spare.shift() ?? '+')
    return assigned.get(name)
  }

  const cols = []
  for (let i = 0; i < length; i++) cols.push({ x: origin.x + step[0] * i, z: origin.z + step[1] * i })

  const rows = []
  const cell = (x, y, z, i) => {
    const b = get(x, y, z)
    if (!b) return '?'
    if (i === 0 && x === floor.x && z === floor.z && (y === floor.y || y === floor.y + 1)) return '@'
    if (b.name === 'air' || b.name === 'cave_air' || b.name === 'void_air') return '.'
    if (WATER.test(b.name)) return mark('~', b.name)
    if (passable(b)) return mark(charOf(b.name), b.name) // 草花・松明など（通れる）
    return exposed(get, x, y, z) ? mark(charOf(b.name), b.name) : '#'
  }
  for (let y = top; y >= bottom; y--) {
    rows.push(`y=${String(y).padStart(4)} ${cols.map((c, i) => cell(c.x, y, c.z, i)).join('')}`)
  }

  return {
    from: { x: origin.x, y: y0, z: origin.z },
    direction,
    along: `each column is 1 block further ${direction} (${direction === 'east' || direction === 'west' ? 'x' : 'z'} ${direction === 'east' || direction === 'south' ? 'increases' : 'decreases'}); column 0 is ${origin.x},${origin.z}`,
    rows,
    legend: Object.fromEntries([...legend].map(([ch, names]) => [ch, [...names].join(', ')]).concat([['.', 'air'], ['#', 'rock you cannot see into'], ['@', 'you'], ['?', 'not loaded']])),
    summary: surfaceSummary(get, cols, top, bottom, direction)
  }
}

// 各列の表面（上から見て最初の固い所か水面）を並べて、段差を言葉にする
function surfaceSummary (get, cols, top, bottom, direction) {
  const surfaces = cols.map((c) => {
    for (let y = top; y >= bottom; y--) {
      const b = get(c.x, y, c.z)
      if (!b) return { kind: 'unloaded' }
      if (b.name === 'air' || b.name === 'cave_air' || (passable(b) && !WATER.test(b.name))) continue
      if (WATER.test(b.name)) {
        let depth = 0
        for (let d = y; d >= bottom; d--) {
          const w = get(c.x, d, c.z)
          if (!w || !WATER.test(w.name)) break
          depth++
        }
        return { kind: 'water', y, depth }
      }
      return y === top ? { kind: 'solid_at_top' } : { kind: 'ground', y }
    }
    return { kind: 'open' } // 見た範囲に固い所がない: 下は深い穴か、見た範囲より下
  })
  const groups = []
  surfaces.forEach((s, i) => {
    const last = groups.at(-1)
    if (last && last.kind === s.kind && last.y === s.y) last.to = i
    else groups.push({ ...s, from: i, to: i })
  })
  const span = (g) => (g.from === g.to ? `${g.from}` : `${g.from}-${g.to}`)
  const parts = groups.map((g, k) => {
    const prev = groups[k - 1]
    let text
    if (g.kind === 'ground') text = `ground at y=${g.y}`
    else if (g.kind === 'water') text = `water, surface y=${g.y}, ${g.depth} deep${g.y - g.depth + 1 <= bottom ? ' or more' : ''}`
    else if (g.kind === 'solid_at_top') text = `solid all the way up past y=${top}`
    else if (g.kind === 'unloaded') text = 'not loaded'
    else text = `no solid block down to y=${bottom}`
    let note = ''
    if (prev && Number.isFinite(g.y) && Number.isFinite(prev.y)) {
      const diff = g.y - prev.y
      if (diff >= 1) note = prev.kind === 'water' && g.kind === 'ground' ? ` (the bank is ${diff} above the water surface)` : ` (${diff} higher than before)`
      else if (diff <= -1) note = ` (${-diff} lower than before)`
    }
    return `${span(g)} blocks ${direction}: ${text}${note}`
  })
  return parts.join('; ')
}
