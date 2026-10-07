// 依存関係のソルバー: 必要なアイテムの一覧に対して、まだ足りないものと、いまできること。
//
// 純粋（ボットに触れない）: ワールドのスナップショットを読む。すでに持っているアイテムは、共有の
// 台帳から必要なものへ順に割り当てる。例えば家の角の柱にする原木は、残りを板材にする前に取っておく。
// 足りないアイテムは、クラフト（レシピごと）、精錬、自然のブロックを掘る、動物を狩る、のうち一番安い
// 方法で手に入れる。チェストの中身（最後に見たもの）とかまどが作っているものは、集めたり
// クラフトしたりする前に取る: 近くにあり、確実だから。結果は、進み具合つきの小目標の木、いま
// 行動できる末端、行動できない理由、残りの作業量。
//
// world: { inventory: {name: count}, stored: {name: count}（チェストの中）, blocks: {name: count}, mobs: {name: count},
//          remembered: Set(name), table: 'reach' | 'near' | null, furnace: 'near' | null,
//          smelting: {product: count}（かまどの中。できたものとこれからできるもの）, unlocked: (item) => bool }
//   blocks と mobs は近くの入手元（家の外の、届く自然のブロック。動物）。
//   remembered: 前に見た、いまは見えていない入手元（memory.mjs）。探すより戻るほうが安い
//   buried: (name) => [{ pos, depth }]（空気に面していない、足元より下のブロック）、digDepth: 小目標が許す
//   掘り下げの深さ。許す深さにあれば、探すより階段で掘り下げるほうが安い（17_dig_down.md）

import { FUELS } from './knowledge.mjs'
import { CROPS, cropByBlock } from './farming.mjs'

const MAX_DEPTH = 10 // 何もない状態から鉄の剣まで: インゴット、鉄の原石、石のツルハシ、丸石、木のツルハシ、板材、原木
const EXPLORE_COST = 100 // 見えていない入手元はまず探さないといけない
const RECALL_COST = 50 // 前に見たもの: あった場所まで戻る
const DIG_DOWN_COST = 30 // 埋まったもの: 階段で掘り下げる（深さ 1 段ごとに 1 足す）
const MINE_COST = 40 // 採掘場を下りる（前に見た場所へ戻るより少し安く、掘り下げるよりは高い）
const MINE_DEPTH = 8 // これより深いものは、採掘場があればそこから
const UNDERGROUND = /^(stone|deepslate|tuff|granite|diorite|andesite)$|_ore$/
// 地下へ下りる手と、そのとき持つ松明の数（trail.mjs の torchesNeeded で 30 m 先まで: 8 本）
export const UNDERGROUND_LEAVES = new Set(['go_mine', 'dig_mine', 'make_mine', 'dig_down'])
const UNDERGROUND_TORCHES = 8
// 斧も武器（goals.mjs の armed と同じ）。道具の入手は sword の木製が最も安い
const armedInWorld = (world) => Object.entries(world.inventory ?? {}).some(([n, c]) => c > 0 && /_(sword|axe)$/.test(n))
const IMPOSSIBLE = 1e6
const KILL_COST = 2
const WILD_ONLY = /^(melon|carrots|potatoes|beetroots)$/
const WILD_CROP_COST = 1000 // 動物を探す方が、野生の作物を探すより必ず安い（それしかなければ、それを探す）
const WITHDRAW_COST = 0.5
const SMELT_COST = 1 // クラフトと同じく1個あたり（ボットがほかのことをしている間もかまどは動く）

export function solve (knowledge, world, needs) {
  const ledger = { items: { ...world.inventory }, stored: { ...(world.stored ?? {}) }, smelting: { ...(world.smelting ?? {}) }, table: !!world.table, furnace: !!world.furnace }
  const nodes = needs.map(({ spec, count }) => need(knowledge, world, spec, count, ledger, [], 0).node)
  const leaves = []
  const blocked = []
  const impossible = []
  for (const n of nodes) collect(n, leaves, blocked, impossible)
  return {
    met: nodes.every((n) => n.have >= n.need),
    nodes,
    leaves,
    blocked,
    impossible, // ボットに何をしても手に入らないもの（blocked にも入る）
    remaining: nodes.reduce((s, n) => s + n.units, 0),
    lines: nodes.flatMap((n) => lines(n, 0))
  }
}

// 台帳にあるグループのぶんを取り、足りないぶんを一番安い方法で手に入れる。
function need (k, world, spec, count, ledger, path, depth) {
  const { label, members } = k.resolve(spec)
  let have = 0
  for (const m of [...members].sort((a, b) => (ledger.items[b] ?? 0) - (ledger.items[a] ?? 0))) {
    const take = Math.min(ledger.items[m] ?? 0, count - have)
    if (take <= 0) continue
    ledger.items[m] -= take
    have += take
  }
  const node = { label: `have ${count} ${label}`, spec, have, need: count, units: 0, children: [], leaf: null }
  if (have >= count) return { node, cost: 0 }

  // チェストから取り出す: アイテムごとに子を1つ作り、残りだけを手に入れる
  const fromChests = []
  let taken = 0
  for (const m of members) {
    const take = Math.min(ledger.stored[m] ?? 0, count - have - taken)
    if (take <= 0) continue
    ledger.stored[m] -= take
    taken += take
    fromChests.push({ label: `take ${take} ${m} from a chest`, have: 0, need: take, units: take, children: [], leaf: { kind: 'withdraw', item: m, count: take } })
  }
  // かまどでできた（またはできている途中の）もの: できたら取り出す
  for (const m of members) {
    const take = Math.min(ledger.smelting[m] ?? 0, count - have - taken)
    if (take <= 0) continue
    ledger.smelting[m] -= take
    taken += take
    fromChests.push({ label: `take ${take} ${m} from the furnace`, have: 0, need: take, units: take, children: [], leaf: { kind: 'smelt', item: m, count: 0 } })
  }
  if (taken) {
    node.children = fromChests
    node.units = taken
    if (have + taken >= count) {
      node.method = 'withdraw'
      return { node, cost: taken * WITHDRAW_COST }
    }
  }

  let best = null
  for (const m of members) {
    const trial = cloneLedger(ledger)
    const option = acquire(k, world, m, count - have - taken, trial, path, depth)
    if (!best || option.cost < best.cost) best = { ...option, ledger: trial }
  }
  Object.assign(ledger, best.ledger)
  node.units += best.units
  node.children = [...fromChests, ...best.children]
  node.leaf = best.leaf
  node.method = best.method
  return { node, cost: best.cost + taken * WITHDRAW_COST }
}

function acquire (k, world, item, n, ledger, path, depth) {
  const none = { cost: IMPOSSIBLE, units: n, children: [], leaf: { kind: 'none', item, reason: `no way to get ${item}` }, method: 'none' }
  if (depth >= MAX_DEPTH || path.includes(item)) return none
  const options = []
  for (const r of k.recipes(item)) options.push((l) => craftOption(k, world, item, n, r, l, [...path, item], depth))
  const input = k.smeltingInput(item)
  if (input) options.push((l) => smeltOption(k, world, item, n, input, l, [...path, item], depth))
  const blocks = k.blockSources.get(item)
  if (blocks) options.push((l) => gatherOption(k, world, item, n, 'dig', blocks, world.blocks, 1, l, path, depth))
  const mobs = k.mobSources.get(item)
  if (mobs) options.push((l) => gatherOption(k, world, item, n, 'kill', mobs, world.mobs, KILL_COST, l, path, depth))
  if (item === 'water_bucket') options.push((l) => fillOption(k, world, item, n, l, [...path, item], depth))
  let best = none
  let bestLedger = null
  for (const make of options) {
    const trial = cloneLedger(ledger)
    const option = make(trial)
    if (option.cost < best.cost) { best = option; bestLedger = trial }
  }
  if (bestLedger) Object.assign(ledger, bestLedger)
  return best
}

// 水のバケツ: 空のバケツで水源の水を汲む（クラフトでも掘るでもないので、専用の手）。バケツは鉄 3 つのクラフト
function fillOption (k, world, item, n, ledger, path, depth) {
  const bucket = need(k, world, 'bucket', n, ledger, path, depth + 1)
  const pos = world.water?.() ?? null
  const ready = bucket.node.have >= bucket.node.need
  return {
    cost: bucket.cost + n + (pos ? 0 : EXPLORE_COST),
    units: n + bucket.node.units,
    children: [bucket.node],
    method: 'fill',
    leaf: !ready
      ? null
      : pos ? { kind: 'fill', item, count: n, pos } : { kind: 'explore', item, sources: ['water'], reason: `no water source nearby to fill a bucket for ${item}` }
  }
}

function craftOption (k, world, item, n, recipe, ledger, path, depth) {
  const times = Math.ceil(n / recipe.count)
  const children = []
  let cost = times
  let units = times
  for (const [ing, per] of Object.entries(recipe.ingredients)) {
    const r = need(k, world, ing, per * times, ledger, path, depth + 1)
    children.push(r.node)
    cost += r.cost
    units += r.node.units
    if (cost >= IMPOSSIBLE) return { cost: IMPOSSIBLE, units, children, leaf: null, method: 'craft' }
  }
  let tableReady = !recipe.needsTable || !!world.table
  if (recipe.needsTable && !world.table) {
    const t = station(k, world, ledger, path, depth)
    if (t) {
      children.push(t.node)
      cost += t.cost
      units += t.node.units
    }
    tableReady = false
  }
  const surplus = times * recipe.count - n
  if (surplus > 0) ledger.items[item] = (ledger.items[item] ?? 0) + surplus
  const ready = children.every((c) => c.have >= c.need) && tableReady
  return {
    cost,
    units,
    children,
    method: 'craft',
    leaf: { kind: 'craft', item, times, needsTable: recipe.needsTable, ready, unlocked: world.unlocked(item), makes: recipe.count, ingredients: recipe.ingredients }
  }
}

// 歩いて行ける範囲の作業台かかまど。solve 1回につき一度だけ計画する（台帳が覚えている）
function station (k, world, ledger, path, depth, item = 'crafting_table', flag = 'table') {
  if (ledger[flag]) return null
  ledger[flag] = true
  const r = need(k, world, item, 1, ledger, path, depth + 1)
  const node = { label: `a ${item.replace('_', ' ')} nearby`, have: 0, need: 1, units: r.node.units + 1, children: [r.node], leaf: null }
  if (r.node.have >= r.node.need) node.leaf = { kind: 'place', item }
  return { node, cost: r.cost + 1 }
}

// かまどで n 個作る: 材料、n 個ぶんの燃料（持っているものを優先）、近くのかまど
function smeltOption (k, world, item, n, input, ledger, path, depth) {
  const children = []
  let cost = n * SMELT_COST
  let units = n
  const held = (spec) => k.resolve(spec).members.reduce((s, m) => s + (ledger.items[m] ?? 0), 0)
  const fuels = FUELS.map((f) => ({ ...f, count: Math.ceil(n / f.per) })).filter((f) => f.spec !== input)
  const fuel = fuels.find((f) => held(f.spec) >= f.count) ?? fuels.find((f) => f.spec === 'planks')
  for (const [spec, count] of [[input, n], [fuel.spec, fuel.count]]) {
    const r = need(k, world, spec, count, ledger, path, depth + 1)
    children.push(r.node)
    cost += r.cost
    units += r.node.units
    if (cost >= IMPOSSIBLE) return { cost: IMPOSSIBLE, units, children, leaf: null, method: 'smelt' }
  }
  let furnaceReady = !!world.furnace
  if (!world.furnace && world.furnaceAway) {
    // 近くにはないが、覚えているかまど（家のかまど）がある: 新しく置くより、そこまで歩くほうが安い
    // （松明の石炭を探し回らず、木炭を焼ける。2026-09-29）
    cost += RECALL_COST
    furnaceReady = true
  } else if (!world.furnace) {
    const f = station(k, world, ledger, path, depth, 'furnace', 'furnace')
    if (f) {
      children.push(f.node)
      cost += f.cost
      units += f.node.units
    }
    furnaceReady = false
  }
  const ready = children.every((c) => c.have >= c.need) && furnaceReady
  return {
    cost,
    units,
    children,
    method: 'smelt',
    leaf: { kind: 'smelt', item, input, count: n, inputs: k.resolve(input).members, fuels: k.resolve(fuel.spec).members, fuelCount: fuel.count, ready }
  }
}

function gatherOption (k, world, item, n, kind, sources, nearby, unitCost, ledger, path, depth) {
  const inSight = sources.filter((s) => (nearby?.[s] ?? 0) > 0)
  const children = []
  let cost = n * unitCost
  if (kind === 'dig') {
    // 一番安く掘れるブロック: 道具が要らないもの。なければ道具が手元にあるか作れるもの
    const handy = (inSight.length ? inSight : sources).map((b) => ({ b, tools: k.harvestTools(b) }))
    const bare = handy.filter((h) => !h.tools)
    if (!bare.length && handy.length) {
      const tools = handy[0].tools
      const r = needAny(k, world, tools, ledger, path, depth)
      children.push(r.node)
      cost += r.cost
    }
  }
  const known = sources.filter((s) => world.remembered?.has(s))
  const sought = known.length ? known : sources
  // 埋まっているもの: 一番浅いもの。小目標が許す深さにあれば掘り下げる
  const buried = kind === 'dig' && !inSight.length
    ? sources.map((s) => ({ s, t: world.buried?.(s)?.[0] })).filter((b) => b.t).sort((a, b) => a.t.depth - b.t.depth)[0]
    : null
  // 家があるとき、地下の石・鉱石は階段掘りでなく、家のそばの採掘場から（設計書 68）
  const plan = kind === 'dig' && !inSight.length && world.home && sources.some((s) => UNDERGROUND.test(s))
    ? world.minePlan?.(sources) ?? null
    : null
  const digDown = !plan && buried && buried.t.depth <= (world.digDepth ?? 0) &&
    DIG_DOWN_COST + buried.t.depth < (known.length ? RECALL_COST : EXPLORE_COST)
  // 下りきった採掘場があれば、深いもの（MINE_DEPTH 段より下）や見えないものはそこから下りて探す（設計書 47 §16）。
  // 浅い石は今までどおりその場で掘り下げる
  const mine = kind === 'dig' && !inSight.length && world.mine?.above && sources.some((s) => UNDERGROUND.test(s)) &&
    (!digDown || buried.t.depth > MINE_DEPTH)
    ? world.mine
    : null
  // 畑にある作物: 熟していれば畑へ戻る。育ち途中なら遠出しない（作物はプレイヤーの近くでしか
  // 育たない。2026-09-27: 家の小麦が育ち途中のまま、261 m 先の小麦へ遠征しようとした）
  const farm = kind === 'dig' && !inSight.length
    ? sources.map((s) => ({ s, f: world.farm?.(s) })).find((x) => x.f)
    : null
  let leaf
  if (inSight.length) {
    leaf = { kind, item, count: n, sources: inSight }
  } else if (farm && farm.f.ripe > 0) {
    leaf = { kind: 'explore', item, sources: [farm.s], farm: farm.f, reason: `the farms have ${farm.f.ripe} ripe ${farm.s} for ${item}: go there and harvest` }
    cost += RECALL_COST
  } else if (farm) {
    const { planted, ages, mature, free } = farm.f
    const crop = cropByBlock(farm.s)
    const seed = CROPS[crop].seed
    const meal = (world.inventory?.bone_meal ?? 0) > 0 && farm.f.pos ? `; bone meal grows it at once (use_item bone_meal on the ${farm.s} at ${farm.f.pos.x},${farm.f.pos.y},${farm.f.pos.z})` : ''
    // 育つ間に畑の空いたマスを埋める（次の収穫が増える）: 種があればまく、クワがあれば耕す。ついでの作業（also）。
    // 空きがなければ、別の名前の畑を足す（設計してから。施設は広げられない: 設計書 47 §14）
    const also = `fill the ${crop} farms while it grows (more ${farm.s} next harvest)`
    // 種かクワが手元になければ、それを手に入れる手も出す（草を掘って種、クワを作る）。出さないと、クワが
    // 壊れた・種を使い切った畑で「待つ」しか残らず、育つのを 10 秒ずつ待ち続けた（2026-09-29）
    const seeds = world.inventory?.[seed] ?? 0
    const hasHoe = Object.entries(world.inventory ?? {}).some(([n, c]) => n.endsWith('_hoe') && c > 0)
    // 探しに出る手（explore）は出さない: 作物が育つ間は畑のそばにいる
    const getting = (spec, count) => {
      const sub = []
      collect(need(k, world, spec, count, cloneLedger(ledger), [...path, item], depth + 1).node, sub, [], [])
      return sub.filter((l) => l.kind !== 'explore').map((l) => ({ ...l, also }))
    }
    // 耕すのは、まく種が耕し済みのマス（tilled）より多いときだけ。水が近くにない畑は、耕しただけで何も
    // 植わっていない地面が土に戻る（2026-09-29: 種がないまま耕し続け、最初に耕した所が土に戻っていた）ので、
    // 耕す → まく を 1 マスずつ順にする
    const tilled = farm.f.sow ?? 0
    const tillable = seeds > tilled
    const expand = free > 0
      ? [
          ...(seeds > 0 ? [{ kind: 'sow', item: seed, crop, also }] : getting(seed, Math.min(free, 16))),
          ...(!hasHoe ? getting('hoe', 1) : tillable ? [{ kind: 'till', crop, also }] : [])
        ]
      : []
    const more = free > 0
      ? `or fill the farms' ${free} free cells (till and sow ${seed}; a step farmed(${crop}, ${planted + free})` +
        `${seeds > 0 ? '' : `; no ${seed} carried: dig grass for seeds`}${hasHoe ? '' : '; no hoe carried: make one'}` +
        `${tillable ? '' : `; do not till more than you can sow: tilled ground with nothing planted turns back to dirt without water`})`
      : `or, for more, add another ${crop} farm with a new name (facility(<new name>) in an objective, e.g. ${crop}_farm_2: it is designed on the town map first)`
    leaf = {
      kind: 'growing',
      item,
      expand,
      reason: `the ${farm.s} on the farms for ${item} is still growing (${planted} planted, none ripe, age ${ages[0]}-${ages[1]} of ${mature}); ` +
        `it grows only while you stay near it: do other steps near the farms meanwhile and harvest it when ripe, instead of going far for it; ` +
        `${more}${meal}`
    }
    cost += EXPLORE_COST * 2
  } else if (plan?.go) {
    leaf = { kind: 'go_mine', item, mine: plan.go, sources }
    cost += MINE_COST
  } else if (plan?.dig) {
    leaf = { kind: 'dig_mine', item, facility: plan.dig, sources }
    cost += MINE_COST * 2
  } else if (plan?.make !== undefined) {
    leaf = { kind: 'make_mine', item, bottom_y: plan.make, sources, ...(plan.exhausted ? { exhausted: plan.exhausted } : {}) }
    cost += MINE_COST * 3
  } else if (mine) {
    leaf = { kind: 'go_mine', item, mine, sources }
    cost += MINE_COST
  } else if (digDown) {
    leaf = { kind: 'dig_down', item, sources: [buried.s], depth: buried.t.depth }
    cost += DIG_DOWN_COST + buried.t.depth
  } else {
    const hint = (buried && buried.t.depth > (world.digDepth ?? 0)
      ? `; ${buried.s} is buried ${buried.t.depth} blocks down (the goal's dig_depth ${world.digDepth} is shallower: leave it out to dig down)`
      : '') + mineAdvice(world, kind, sources, item)
    leaf = { kind: 'explore', item, sources: sought, reason: `no ${sought.join('/')} nearby for ${item}${known.length ? ' (seen before)' : ''}${hint}` }
    cost += known.length ? RECALL_COST : EXPLORE_COST
    // 野生の作物（メロン、にんじん、じゃがいも、ビートルート）は、ジャングルや村にしかない。何も見えていない
    // ときに「掘る方が安い」で選ぶと、見つからないメロンを探し続ける（2026-10-03）。動物（どこにでもいる）を先にする
    if (kind === 'dig' && !known.length && sources.every((s) => WILD_ONLY.test(s))) cost += WILD_CROP_COST
  }
  // 地下（採掘場、階段掘り）へは、松明を持ってから行く（暗い所の湧きを潰して進む。松明がないまま
  // 採掘場へ下りて、骷髏に撃たれ続けた: 2026-10-01）。松明は石炭か木炭と棒から作る手が先に出る
  // 武器も持ってから行く（武器なしで地下に出て、骷髏に撃たれ続けて死んだ: 2026-10-01 19:44。剣は木で作れる）
  if (UNDERGROUND_LEAVES.has(leaf.kind)) {
    // 松明が（輪で）どうしても手に入らないときは、松明なしで下りる: 松明の石炭も、丸石で作るかまどの木炭も地下の
    // 石からで、松明を要求し続けると何も手に入らなくなる（2026-10-07: 死んで松明を失い、石が「手立てなし」で 17 分止まった）
    const trial = cloneLedger(ledger)
    const torches = need(k, world, 'torch', UNDERGROUND_TORCHES, trial, path, depth + 1)
    if (torches.cost < IMPOSSIBLE) {
      Object.assign(ledger, trial)
      children.push(torches.node)
      cost += torches.cost
    }
    // 予備の木のツルハシ（掘る道具のほかに 1 本）: 採掘場の底で道具が壊れると、作業台も置けず岩も掘れず、出られなくなった
    // （2026-10-07: 石と木のツルハシが 72 マスで 2 本とも壊れ、約 40 分閉じ込められた）。木でも岩は掘れるので出られる
    const spareTrial = cloneLedger(ledger)
    const spare = need(k, world, 'wooden_pickaxe', 1, spareTrial, path, depth + 1)
    if (spare.cost < IMPOSSIBLE) {
      Object.assign(ledger, spareTrial)
      children.push(spare.node)
      cost += spare.cost
    }
    if (!armedInWorld(world)) {
      const sword = need(k, world, 'sword', 1, ledger, path, depth + 1)
      children.push(sword.node)
      cost += sword.cost
    }
  }
  const units = n + children.reduce((s, c) => s + c.units, 0)
  return { cost, units, children, leaf: children.every((c) => c.have >= c.need) ? leaf : null, method: kind }
}

// 地下の鉱石が見えず、下りられる採掘場もないとき: 歩き回って探すより、採掘場を作ってそこから集める
// （設計書 47 §16）。探索の候補は残す（採掘場ができるまで、最初の 1 個が手に入らないことがないように）
function mineAdvice (world, kind, sources, item) {
  if (kind !== 'dig' || !sources.some((s) => UNDERGROUND.test(s)) || world.mine) return ''
  const open = (world.mines ?? []).filter((m) => m.status !== 'done')
  if (open.length) {
    const names = open.map((m) => `${m.name} (${m.status})`).join(', ')
    return `; the mine ${names} is not finished: finish it (a step facility(<its name>)), then "go down the mine" fetches ${item} from it, better than walking around`
  }
  return `; instead of walking around for it, add a mine: facility(<new name>, e.g. mine_1) in an objective (it is designed on the town map first: a mine near stone, and once it is done "go down the mine" fetches ${item} and other ores from it)`
}

// 集めに出る手（掘る・狩る・探す・下りる・チェストから出す）。クラフトと焼くのは自動で、出かけない
// （node.leaf は前の材料がそろうまで null なので、入手の方法 node.method で見る）
const TRIPS = new Set(['dig', 'kill', 'withdraw'])
const needsTrip = (node) => node.have < node.need && (TRIPS.has(node.method) || node.children.some(needsTrip))

// 仕事（have の条件）の次の一歩: 根の直接の子のうち、まだ足りず、集めに出るものを含む最初のもの
// { item, count }。なければ null（仕事そのものが 1 回の遠征）。子の数は台帳で引いた残りなので、持ち物が
// もう足りているものは飛ばす（足りているのに未達成の一歩を返して、同じ一歩を繰り返さない）
// 仕事（have の条件）そのものが素材の採集か: 根の入手の方法が掘る・狩る・チェストから出す。何を作る
// ための素材かは分からない（小目標と同じ 1 回の遠征になり、中目標にする意味が薄い。設計書 50 §14）
export const isGather = (root) => !!root && root.have < root.need && TRIPS.has(root.method)

export function nextStep (root, world, k) {
  if (!root || root.have >= root.need) return null
  const held = (spec) => k.resolve(spec).members.reduce((s, m) => s + (world.inventory?.[m] ?? 0), 0)
  for (const c of root.children) {
    if (!c.spec || !needsTrip(c) || held(c.spec) >= c.need) continue
    return { item: c.spec, count: c.need }
  }
  return null
}

// いくつかのアイテムのどれか1つ（例: 石を掘れるどれかのツルハシ）。道具は使っても減らない
function needAny (k, world, items, ledger, path, depth) {
  const held = items.find((i) => (ledger.items[i] ?? 0) > 0)
  if (held) return { node: { label: `have ${held}`, have: 1, need: 1, units: 0, children: [], leaf: null }, cost: 0 }
  let best = null
  for (const i of items) {
    const trial = cloneLedger(ledger)
    const r = need(k, world, i, 1, trial, path, depth + 1)
    if (!best || r.cost < best.cost) best = { ...r, ledger: trial }
  }
  Object.assign(ledger, best.ledger)
  return best
}

function collect (node, leaves, blocked, impossible) {
  if (node.have >= node.need) return
  for (const c of node.children) collect(c, leaves, blocked, impossible)
  const leaf = node.leaf
  if (!leaf) return
  if (leaf.kind === 'none' || leaf.kind === 'growing') {
    blocked.push(leaf.reason)
    impossible.push(leaf.reason)
    if (leaf.expand) leaves.push(...leaf.expand)
  } else if (leaf.kind === 'explore') { blocked.push(leaf.reason); leaves.push(leaf) } else if (leaf.kind === 'craft') {
    if (!leaf.ready) return
    if (leaf.unlocked) leaves.push(leaf)
    else blocked.push(`the recipe for ${leaf.item} is not unlocked yet`)
  } else if (leaf.kind === 'smelt' && leaf.count > 0) {
    if (leaf.ready) leaves.push(leaf)
  } else leaves.push(leaf)
}

function lines (node, indent) {
  const pad = '  '.repeat(indent)
  const done = node.have >= node.need
  const how = done ? 'done' : describe(node.leaf, node.method)
  const out = [`${pad}${node.label} (${Math.min(node.have, node.need)}/${node.need})${how ? `: ${how}` : ''}`]
  if (!done) for (const c of node.children) out.push(...lines(c, indent + 1))
  return out
}

function describe (leaf, method) {
  if (!leaf) return method ?? ''
  switch (leaf.kind) {
    // 1 回のクラフトのレシピも書く（材料の数と、できる数。小麦 3 個でパン 1 個を、1 対 1 と思い込まれた）
    case 'craft': return `craft ${leaf.item} x${leaf.times}${leaf.ingredients ? ` (one craft: ${Object.entries(leaf.ingredients).map(([n, c]) => `${c} ${n}`).join(' + ')} -> ${leaf.makes} ${leaf.item})` : ''}${leaf.needsTable ? ' at a crafting table' : ''}`
    case 'fill': return `fill the bucket at the water (${leaf.pos.x},${leaf.pos.y},${leaf.pos.z})`
    case 'dig': return `dig ${leaf.sources.join('/')}`
    case 'kill': return `hunt ${leaf.sources.join('/')}`
    case 'explore': return `find ${leaf.sources.join('/')}`
    case 'dig_down': return `dig stairs down to ${leaf.sources.join('/')} (${leaf.depth} blocks down)`
    case 'go_mine': return `go down the mine ${leaf.mine.name} (bottom y ${leaf.mine.bottom.y}) for ${leaf.sources.join('/')}`
    case 'dig_mine': return `dig the mine ${leaf.facility} (stairs down, the main tunnel and its branches), then take ${leaf.item} from its walls`
    case 'make_mine': return `design a mine down to y ${leaf.bottom_y} near the home and dig it (branch mining), then take ${leaf.item} from its walls`
    case 'place': return `place ${leaf.item}`
    case 'withdraw': return 'take it out of the chest'
    case 'smelt': return leaf.count ? `smelt ${leaf.input} in a furnace` : 'take it out of the furnace'
    default: return leaf.reason ?? ''
  }
}

const cloneLedger = (l) => ({ items: { ...l.items }, stored: { ...l.stored }, smelting: { ...l.smelting }, table: l.table, furnace: l.furnace })
