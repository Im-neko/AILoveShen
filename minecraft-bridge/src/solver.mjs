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

const MAX_DEPTH = 10 // 何もない状態から鉄の剣まで: インゴット、鉄の原石、石のツルハシ、丸石、木のツルハシ、板材、原木
const EXPLORE_COST = 100 // 見えていない入手元はまず探さないといけない
const RECALL_COST = 50 // 前に見たもの: あった場所まで戻る
const DIG_DOWN_COST = 30 // 埋まったもの: 階段で掘り下げる（深さ 1 段ごとに 1 足す）
const IMPOSSIBLE = 1e6
const KILL_COST = 2
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
  const node = { label: `have ${count} ${label}`, have, need: count, units: 0, children: [], leaf: null }
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
    leaf: { kind: 'craft', item, times, needsTable: recipe.needsTable, ready, unlocked: world.unlocked(item) }
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
  if (!world.furnace) {
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
  const digDown = buried && buried.t.depth <= (world.digDepth ?? 0) &&
    DIG_DOWN_COST + buried.t.depth < (known.length ? RECALL_COST : EXPLORE_COST)
  let leaf
  if (inSight.length) {
    leaf = { kind, item, count: n, sources: inSight }
  } else if (digDown) {
    leaf = { kind: 'dig_down', item, sources: [buried.s], depth: buried.t.depth }
    cost += DIG_DOWN_COST + buried.t.depth
  } else {
    const hint = buried && buried.t.depth > (world.digDepth ?? 0)
      ? `; ${buried.s} is buried ${buried.t.depth} blocks down (set dig_depth to ${buried.t.depth} or more to dig stairs down)`
      : ''
    leaf = { kind: 'explore', item, sources: sought, reason: `no ${sought.join('/')} nearby for ${item}${known.length ? ' (seen before)' : ''}${hint}` }
    cost += known.length ? RECALL_COST : EXPLORE_COST
  }
  const units = n + children.reduce((s, c) => s + c.units, 0)
  return { cost, units, children, leaf: children.every((c) => c.have >= c.need) ? leaf : null, method: kind }
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
  if (leaf.kind === 'none') {
    blocked.push(leaf.reason)
    impossible.push(leaf.reason)
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
    case 'craft': return `craft ${leaf.item} x${leaf.times}${leaf.needsTable ? ' at a crafting table' : ''}`
    case 'dig': return `dig ${leaf.sources.join('/')}`
    case 'kill': return `hunt ${leaf.sources.join('/')}`
    case 'explore': return `find ${leaf.sources.join('/')}`
    case 'dig_down': return `dig stairs down to ${leaf.sources.join('/')} (${leaf.depth} blocks down)`
    case 'place': return `place ${leaf.item}`
    case 'withdraw': return 'take it out of the chest'
    case 'smelt': return leaf.count ? `smelt ${leaf.input} in a furnace` : 'take it out of the furnace'
    default: return leaf.reason ?? ''
  }
}

const cloneLedger = (l) => ({ items: { ...l.items }, stored: { ...l.stored }, smelting: { ...l.smelting }, table: l.table, furnace: l.furnace })
