// 道具（設計書 21）: Gemini が名指しで呼ぶ行動と調べもの。
//
// 行動の道具は、候補と同じ実行の経路（runner.mjs の run）を通る。引数はそのまま信じない: 世界と
// 知識から導き（掘るブロックの名前、作業台が要るか、かまどと燃料）、存在と距離を確かめる。安全の
// 制約はここで守り、断るときは理由を返す: 家に避難している間（夜、ドアの前の敵）は外に出る道具を
// 断る、今の家と建てている家は掘らない・置かない。調べものの道具はすぐ返り、履歴に残さない。
import vec3Pkg from 'vec3'
import { round, inventoryCounts, isHostile, bearing } from './observe.mjs'
import { shelterOf, needsOutside } from './candidates.mjs'
import { protectedReason, hasBed } from './home.mjs'
import { findTable, findFurnace, nearbyDrops } from './primitives.mjs'
import { smeltingProduct, FUELS } from './knowledge.mjs'
import { homeChests, chestWith } from './memory.mjs'
import { exposed } from './world.mjs'

const { Vec3 } = vec3Pkg

const MAX_TOOL_DISTANCE = 256 // goto は 1 回 48m ずつ歩く
const MAX_BLOCK_DISTANCE = 32 // 掘る・置く対象
const MAX_ENTITY_DISTANCE = 24
const MAX_CRAFT_TIMES = 16
const FIND_RADIUS = 64
const FIND_SHOWN = 8
const DROP_RADIUS = 16
const REPLACEABLE = new Set(['air', 'cave_air', 'water', 'short_grass', 'tall_grass', 'fern', 'large_fern', 'snow', 'dead_bush'])

export const ACTION_TOOLS = ['do_suggestion', 'goto', 'dig', 'place', 'craft', 'pickup', 'attack', 'flee', 'eat', 'equip',
  'smelt', 'deposit', 'withdraw', 'go_home', 'sleep', 'build_next', 'wait']
export const QUERY_TOOLS = ['find_blocks', 'recipe_of', 'how_to_get']

class Refused extends Error {}
const refuse = (why) => { throw new Refused(why) }
const fmt = (p) => `${p.x},${p.y},${p.z}`

function position (args) {
  const [x, y, z] = [args.x, args.y, args.z].map(Number)
  if (![x, y, z].every(Number.isFinite)) refuse('x, y and z must be numbers')
  return new Vec3(Math.floor(x), Math.floor(y), Math.floor(z))
}

function within (bot, p, max, what) {
  const d = bot.entity.position.distanceTo(p.offset(0.5, 0.5, 0.5))
  if (d > max) refuse(`${what} is ${round(d)}m away (at most ${max}m)`)
  return round(d)
}

function holding (bot, item) {
  if (typeof item !== 'string' || !item) refuse('item is required')
  if (!(inventoryCounts(bot)[item] > 0)) refuse(`you have no ${item}`)
}

function entity (bot, args) {
  const e = bot.entities[Number(args.entity)]
  if (!e || e === bot.entity || e.type === 'player') refuse(`no mob with id ${args.entity} nearby (use the ids in mobs)`)
  within(bot, e.position.floored(), MAX_ENTITY_DISTANCE, e.name)
  return e
}

// deps: { bot, state, knowledge, run(c, label), candidates(): 今の候補（ソルバーの提案）,
//         check(specs): 条件の判定（goals.mjs の checkConditions） }
export function createTools (deps) {
  const { bot, state, knowledge } = deps

  // 行動の道具の引数から、実行するもの（PRIMITIVES の c）を作る
  const build = {
    do_suggestion ({ id }) {
      const c = deps.candidates().find((x) => x.id === id)
      if (!c) refuse(`${id} is not a current suggestion`)
      return { c, label: id }
    },
    goto (args) {
      const pos = position(args)
      within(bot, pos, MAX_TOOL_DISTANCE, `${fmt(pos)}`)
      const range = Math.max(0, Math.min(8, Number(args.range ?? 1)))
      return { c: { verb: 'goto_pos', target: fmt(pos), pos, range } }
    },
    dig (args) {
      const pos = position(args)
      within(bot, pos, MAX_BLOCK_DISTANCE, `the block at ${fmt(pos)}`)
      const block = bot.blockAt(pos)
      if (!block) refuse(`the block at ${fmt(pos)} is not loaded`)
      if (block.boundingBox !== 'block') refuse(`there is ${block.name} at ${fmt(pos)}, nothing to dig`)
      const why = protectedReason(state, pos, 0, block)
      if (why) refuse(`will not dig ${block.name} at ${fmt(pos)}: ${why}`)
      const tools = knowledge.harvestTools(block.name)
      if (tools && !tools.some((t) => inventoryCounts(bot)[t] > 0)) refuse(`${block.name} drops nothing without one of: ${tools.join(', ')}`)
      return { c: { verb: 'dig', target: block.name, block: block.name, pos } }
    },
    place (args) {
      holding(bot, args.item)
      const pos = position(args)
      within(bot, pos, MAX_BLOCK_DISTANCE, `${fmt(pos)}`)
      const cell = bot.blockAt(pos)
      if (!cell) refuse(`${fmt(pos)} is not loaded`)
      if (!REPLACEABLE.has(cell.name)) refuse(`${fmt(pos)} is taken by ${cell.name}`)
      const why = protectedReason(state, pos)
      if (why) refuse(`will not place at ${fmt(pos)}: ${why}`)
      const feet = bot.entity.position.floored()
      if (pos.equals(feet) || pos.equals(feet.offset(0, 1, 0))) refuse(`you are standing in ${fmt(pos)}; move first`)
      const against = [[0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]]
        .map(([x, y, z]) => pos.offset(x, y, z)).find((p) => bot.blockAt(p)?.boundingBox === 'block')
      if (!against) refuse(`nothing next to ${fmt(pos)} to place against`)
      return { c: { verb: 'place_at', target: args.item, item: args.item, pos, against } }
    },
    craft (args) {
      const item = args.item
      const recipes = typeof item === 'string' ? knowledge.recipes(item) : []
      if (!recipes.length) refuse(`${item} has no crafting recipe`)
      const times = Math.max(1, Math.min(MAX_CRAFT_TIMES, Math.floor(Number(args.times ?? 1))))
      const needsTable = recipes.every((r) => r.needsTable)
      if (needsTable && !findTable(bot, state)) refuse(`${item} needs a crafting table within reach; place one first`)
      return { c: { verb: 'craft', target: item, item, times, needsTable, inPlace: true } }
    },
    pickup () {
      const d = nearbyDrops(bot, state, DROP_RADIUS)[0]
      if (!d) refuse(`no item on the ground within ${DROP_RADIUS}m`)
      const item = d.e.getDroppedItem()?.name ?? 'item'
      return { c: { verb: 'pickup', target: item, entityId: d.e.id, pos: d.e.position } }
    },
    attack (args) {
      const e = entity(bot, args)
      return { c: { verb: 'attack', target: e.name, entityId: e.id, pos: e.position, hostile: isHostile(bot, e) } }
    },
    flee (args) {
      const e = entity(bot, args)
      return { c: { verb: 'flee', target: e.name, entityId: e.id, pos: e.position } }
    },
    eat (args) {
      holding(bot, args.item)
      if (!bot.registry.foodsByName[args.item]) refuse(`${args.item} is not food`)
      return { c: { verb: 'eat', target: args.item, item: args.item, inPlace: true } }
    },
    equip (args) {
      holding(bot, args.item)
      return { c: { verb: 'equip', target: args.item, item: args.item, inPlace: true } }
    },
    smelt (args) {
      holding(bot, args.input)
      const product = smeltingProduct(args.input)
      if (!product) refuse(`${args.input} cannot be smelted`)
      const furnace = findFurnace(bot)
      if (!furnace) refuse('no furnace within reach; place one first')
      const inv = inventoryCounts(bot)
      const count = Math.max(1, Math.min(inv[args.input], Math.floor(Number(args.count ?? inv[args.input]))))
      for (const { spec, per } of FUELS) {
        const fuel = knowledge.resolve(spec).members.find((m) => inv[m] > 0 && m !== args.input)
        if (!fuel) continue
        const fuelCount = Math.min(inv[fuel], Math.ceil(count / per))
        return { c: { verb: 'smelt', target: product, item: product, input: args.input, count, fuel, fuelCount, pos: furnace.position } }
      }
      return refuse('no fuel (coal, charcoal, planks or logs)')
    },
    deposit (args) {
      holding(bot, args.item)
      const me = bot.entity.position
      const chest = homeChests(state.memory ?? {}, state.home).sort((a, b) => me.distanceTo(new Vec3(a.x, a.y, a.z)) - me.distanceTo(new Vec3(b.x, b.y, b.z)))[0]
      if (!chest) refuse('no chest in the home')
      const count = Math.max(1, Math.floor(Number(args.count ?? inventoryCounts(bot)[args.item])))
      return { c: { verb: 'deposit', target: args.item, item: args.item, count, pos: new Vec3(chest.x, chest.y, chest.z) } }
    },
    withdraw (args) {
      if (typeof args.item !== 'string') refuse('item is required')
      const chest = chestWith(state.memory ?? {}, [args.item], bot.entity.position)
      if (!chest) refuse(`no chest is known to hold ${args.item}`)
      const count = Math.max(1, Math.floor(Number(args.count ?? 1)))
      return { c: { verb: 'withdraw', target: args.item, item: args.item, count, pos: new Vec3(chest.x, chest.y, chest.z) } }
    },
    go_home () {
      if (!state.home) refuse('there is no home yet')
      return { c: { verb: 'go_home', target: 'home', inPlace: true } }
    },
    sleep () {
      if (!state.home || !hasBed(bot, state.home)) refuse('there is no bed in the home')
      return { c: { verb: 'sleep', target: 'bed', inPlace: true } }
    },
    build_next (args) {
      // 名前付きの建物（docs/design/25_builds.md）: 名前か、今の目標の built(name)
      const name = args?.name ?? (state.goal?.spec?.predicate === 'built' ? state.goal.spec.name : null)
      if (name) {
        const plan = state.builds?.[name]
        if (!plan) refuse(`there is no build named ${name}`)
        if (plan.status(bot).complete) refuse(`the build ${name} is complete`)
        return { c: { verb: 'place_plan', build: name, target: `the next block of the build ${name}`, inPlace: true } }
      }
      if (!state.plan) refuse('there is no house plan')
      if (state.plan.status(bot).complete) refuse('the house plan is complete')
      return { c: { verb: 'place_plan', target: 'the next block of the house', inPlace: true } }
    },
    wait () {
      const inside = !!state.home && shelterOf(bot, state.home).inside
      return { c: { verb: 'wait', target: inside ? 'inside the house' : 'here', inPlace: true, inside } }
    }
  }

  const queries = {
    find_blocks (args) {
      const id = bot.registry.blocksByName[args.block]?.id
      if (id == null) refuse(`unknown block ${args.block}`)
      const radius = Math.max(4, Math.min(FIND_RADIUS, Number(args.radius ?? 32)))
      const me = bot.entity.position
      const found = bot.findBlocks({ matching: id, maxDistance: radius, count: 64 })
        .sort((a, b) => a.distanceTo(me) - b.distanceTo(me)).slice(0, FIND_SHOWN)
        .map((p) => ({ x: p.x, y: p.y, z: p.z, distance_m: round(p.distanceTo(me)), direction: bearing(me, p), exposed: exposed(bot, p) }))
      return found.length ? found : `no ${args.block} within ${radius}m`
    },
    recipe_of (args) {
      const recipes = knowledge.recipes(args.item).map((r) => ({ makes: r.count, ingredients: r.ingredients, needs_crafting_table: r.needsTable }))
      const smeltFrom = knowledge.smeltingInput(args.item)
      return { crafting: recipes, smelted_from: smeltFrom }
    },
    how_to_get (args) {
      const count = Math.max(1, Math.floor(Number(args.count ?? 1)))
      const [r] = deps.check([{ predicate: 'have', item: args.item, count }])
      return { met: r.met, steps: r.lines, impossible: r.impossible }
    }
  }

  // 1 つの道具を呼ぶ: { ok, result, seconds, refused? }
  return async function callTool (name, args = {}) {
    try {
      if (queries[name]) return { ok: true, result: queries[name](args), seconds: 0 }
      if (!build[name]) refuse(`unknown tool ${name}`)
      if (state.reflex) refuse('the reflex is handling a nearby threat')
      if (state.busy) refuse('another action is running')
      const { c, label } = build[name](args)
      const home = state.home
      if (home && needsOutside(c, home)) {
        const { sheltering, day } = shelterOf(bot, home)
        // 昼にドアの前で待つ敵とは戦ってよい（cleared と同じ）。夜は外に出ない
        const confront = day && c.verb === 'attack' && c.hostile
        if (sheltering && !confront) {
          refuse(day
            ? 'staying inside while hostile mobs wait near the door (attack them by day, or wait)'
            : 'staying inside for the night: actions outside the house wait until morning')
        }
        if (confront) c.confront = true
      }
      return await deps.run(c, label ?? `${name}(${JSON.stringify(args)})`)
    } catch (e) {
      // 実行の失敗は run() が結果として返す。ここに来るのは、断ったときと、引数が知識と合わない
      // とき（知らないアイテムなど）: どちらも理由を返し、プレイは止めない
      if (!(e instanceof Refused)) console.log(`[tool] ${name} の引数を使えなかった: ${e.stack}`)
      return { ok: false, refused: true, result: `refused: ${e.message}`, seconds: 0 }
    }
  }
}
