// 木の原木か、人が置いた原木か（docs/design/55_liquid_and_trees.md）。
//
// 自動の採集（掘る候補、ソルバー、覚える場所）は、木の幹だけを原木として数える。村の畑の縁や家の柱の
// 原木は、葉につながっていない人工物で、集める対象ではない（畑の水をせき止めていた）。木の判定は、
// つながった原木の塊のどれかの近くに葉があるか。ボットが自分で置いた原木（ledger.mjs）は、間違えて
// 置いたものを片付けられるよう、集めてよい。座標を指す dig は、これで断らない。
//
// 取り残し: 下の原木を切ったあとの浮いた幹や、葉が散った幹は、葉が近くになく、木と見なされずに残った。
// 何にも触れていない（下も横も空気か葉か原木だけで、分からない所も無い）原木の塊は、人が建てた物ではなく
// 木の取り残しなので、集めてよい。地面や板材や石に触れる塊（村の柱や畑の縁）は今までどおり集めない

import vec3Pkg from 'vec3'
import { placedByMe } from './ledger.mjs'

const { Vec3 } = vec3Pkg

const MAX_CLUSTER = 96 // これより大きい塊は、大木として木にする
const LEAF_REACH = 2 // 原木のこの範囲（立方体）に葉があれば木
const CACHE_MS = 60 * 1000
const cache = new Map()

const key = (p) => `${p.x},${p.y},${p.z}`
const isTreeLogName = (name) => !!name && name.endsWith('_log') && !name.startsWith('stripped_')
const isLeafName = (name) => !!name && name.endsWith('_leaves')

// 塊が触れていても「何にも触れていない」ことを崩さないもの
const NOT_SOLID = /^(air|cave_air|void_air|water|lava|vine|short_grass|tall_grass|fern|large_fern|snow|dead_bush|bee_nest|beehive|cocoa|glow_lichen|hanging_roots|moss_carpet|brown_mushroom|red_mushroom)$|_leaves$|_log$|_wood$|_sapling$|_propagule$/

const NEIGHBORS = []
for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) if (x || y || z) NEIGHBORS.push([x, y, z])

export const isNaturalLogName = isTreeLogName

// 原木の塊が木か（nameAt(x, y, z) はブロックの名前。未知なら null）。つながった原木の塊のどれかの近くに
// 葉があれば木。unknownIsTree: 未知の所が塊に接していれば木として扱う（分からないものを人工物にしない。
// 読み込まれていないチャンクのとき）。視界の地図は見えた面しか持たず、幹の裏はいつも未知なので、地図では偽
export function logClusterIsTree (nameAt, x, y, z, unknownIsTree = true) {
  const start = `${x},${y},${z}`
  const seen = new Map([[start, [x, y, z]]])
  const queue = [[x, y, z]]
  let unknown = false
  let touches = false // 原木・葉・空気以外に触れている
  while (queue.length && seen.size < MAX_CLUSTER) {
    const [px, py, pz] = queue.pop()
    for (const [dx, dy, dz] of NEIGHBORS) {
      const q = [px + dx, py + dy, pz + dz]
      const k = q.join(',')
      if (seen.has(k)) continue
      const name = nameAt(q[0], q[1], q[2])
      if (name === null) { unknown = true; continue }
      if (isTreeLogName(name)) { seen.set(k, q); queue.push(q) } else if (name.startsWith('stripped_') || !NOT_SOLID.test(name)) touches = true
    }
  }
  let tree = (unknownIsTree && unknown) || seen.size >= MAX_CLUSTER
  for (const [px, py, pz] of seen.values()) {
    if (tree) break
    for (let dx = -LEAF_REACH; dx <= LEAF_REACH && !tree; dx++) {
      for (let dy = -LEAF_REACH; dy <= LEAF_REACH && !tree; dy++) {
        for (let dz = -LEAF_REACH; dz <= LEAF_REACH && !tree; dz++) tree = isLeafName(nameAt(px + dx, py + dy, pz + dz))
      }
    }
  }
  return { tree, members: [...seen.keys()], isolated: !tree && !unknown && !touches && seen.size < MAX_CLUSTER }
}

// 原木の塊の判定（60 秒覚える）: { tree, isolated }。原木でなければ null
function clusterOf (bot, pos, now) {
  if (!isTreeLogName(bot.blockAt(pos)?.name)) return null
  const hit = cache.get(key(pos))
  if (hit && now - hit.at < CACHE_MS) return hit
  const { tree, isolated, members } = logClusterIsTree((x, y, z) => bot.blockAt(new Vec3(x, y, z))?.name ?? null, pos.x, pos.y, pos.z)
  const entry = { tree, isolated, at: now }
  for (const k of members) cache.set(k, entry)
  if (cache.size > 4000) cache.clear()
  return entry
}

// 木の幹か
export const isTreeLog = (bot, pos, now = Date.now()) => clusterOf(bot, pos, now)?.tree ?? false

// 木の取り残しか（葉がなく、何にも触れていない原木の塊）
export const isLeftoverLog = (bot, pos, now = Date.now()) => clusterOf(bot, pos, now)?.isolated ?? false

// 自動の採集の対象にしてよい原木か: 木の幹、木の取り残し、またはボットが自分で置いたもの
export const gatherableLog = (bot, state, pos) => placedByMe(bot, state, pos) || !!clusterOf(bot, pos, Date.now())?.tree || isLeftoverLog(bot, pos)

// テスト用
export const forgetTrees = () => cache.clear()
