// アイテムとブロックの知識の検索（設計書 47 §4）: item_info の一覧と中身、uses_of（逆引き）。
//
// プレイする LLM は Minecraft の知識が少ない（取り方、要る道具、何に使えるかを知らない）。ここで
// minecraft-data・レシピ全部（recipes.mjs）・ソルバーの知識（knowledge.mjs）からまとめて返す。
// 一覧から検索語に合う名前を選ぶのは Python（Jev）。日本語名はゲームの ja_jp.json から
// （data-static/names-ja-<版>.json、npm run recipes:fetch）。
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describeRecipe } from './recipes.mjs'
import { HUNTABLE, CHANCE_DROP_BLOCKS } from './knowledge.mjs'
import { CROPS } from './farming.mjs'

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data-static')
export const USES_SHOWN = 20 // uses_of で返すレシピの数

export function loadNames (version = '1.21.4', dir = DIR) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, `names-ja-${version}.json`), 'utf8'))
  } catch {
    return { items: {}, entities: {} }
  }
}

// GET /items: アイテム（ブロックのアイテムも）の名前と日本語名。作物のブロック（wheat と carrots…）は
// アイテムにないものも足す（search_memory の作物と同じ名前で引けるように）
export function itemList (md, names) {
  const out = md.itemsArray.map((i) => ({ name: i.name, ja: names.items[i.name] ?? '' }))
  const known = new Set(out.map((i) => i.name))
  for (const c of Object.values(CROPS)) if (!known.has(c.block)) out.push({ name: c.block, ja: names.items[c.block] ?? '' })
  return out
}

// 取り方と使い方。知らない名前は null
export function itemInfo (name, { md, knowledge, index, names }) {
  const item = md.itemsByName[name]
  const block = md.blocksByName[name]
  if (!item && !block) return null
  const ja = (n) => names.items[n] ?? ''
  const info = { name, ja: ja(name) }
  if (item) info.stack_size = item.stackSize
  const get = {}
  const recipes = index ? index.recipesFor(name) : []
  const crafts = recipes.filter((r) => r.station === 'crafting').map(describeRecipe)
  if (crafts.length) get.craft = crafts
  // 高炉・燻製器・焚き火・石切台・鍛冶台（まだ使えないものもある。かまどで同じ物が作れることが多い）
  const others = recipes.filter((r) => r.station !== 'crafting' && r.station !== 'furnace').map(describeRecipe)
  if (others.length) get.other_stations = others.slice(0, 4)
  const smelt = recipes.filter((r) => r.station === 'furnace')
  if (smelt.length) get.smelt_from = [...new Set(smelt.flatMap((r) => r.ingredients[0].tag ? [`any ${r.ingredients[0].tag}`] : r.ingredients[0].alts))].slice(0, 8)
  const blocks = knowledge.blockSources.get(name) ?? []
  if (blocks.length) {
    get.dig = blocks.slice(0, 8).map((b) => ({
      block: b,
      ja: ja(b),
      tool: knowledge.harvestTools(b)?.slice(0, 1)?.[0] ?? 'any (hand is fine)',
      ...(CHANCE_DROP_BLOCKS.test(b) ? { chance: true } : {})
    }))
  }
  // モブ: minecraft-data の落とす物（確率が低いものは rare）と、ソルバーの補正（羊の羊毛）
  const mobs = new Map()
  for (const l of md.entityLootArray) {
    const d = l.drops.find((x) => x.item === name)
    if (d) mobs.set(l.entity, (d.dropChance ?? 1) < 0.5)
  }
  for (const m of knowledge.mobSources.get(name) ?? []) mobs.set(m, false)
  if (mobs.size) {
    get.mob_drop = [...mobs].sort(([a, ra], [b, rb]) => Number(ra) - Number(rb) || Number(HUNTABLE.has(b)) - Number(HUNTABLE.has(a)))
      .slice(0, 8)
      .map(([m, rare]) => ({ mob: m, ja: names.entities[m] ?? '', ...(HUNTABLE.has(m) ? { huntable: true } : { hostile_or_other: true }), ...(rare ? { rare: true } : {}) }))
  }
  const crop = Object.entries(CROPS).find(([, c]) => c.block === name || c.seed === name)
  if (crop) get.farm = `sow ${crop[1].seed} on farmland (till dirt with a hoe), dig the ripe ${crop[1].block} (goal farmed(${crop[0]}, n))`
  if (/(_sapling|^mangrove_propagule)$/.test(name)) get.plant = `plant it on dirt or grass (goal planted(sapling, n)); saplings drop from leaves by chance`
  info.how_to_get = Object.keys(get).length ? get : 'no known way (not craftable, not dropped by natural blocks or mobs)'
  if (block) {
    info.block = {
      placeable: true,
      dig_with: knowledge.harvestTools(name) ?? 'any (hand is fine)',
      drops: knowledge.dropsOf(name).length ? knowledge.dropsOf(name) : [name]
    }
  }
  const food = md.foodsByName[name]
  if (food) info.food = { hunger: food.foodPoints, saturation: food.saturation }
  const uses = index ? usedIn(index, name).length : 0
  if (uses) info.used_in = `${uses} recipes (ask uses_of for the list)`
  return info
}

// name を材料に使うレシピ（タグは展開されている: ingredients の alts）
function usedIn (index, name) {
  return index.all.filter((r) => r.result !== name && r.ingredients.some((i) => i.alts.includes(name)))
}

// uses_of: name を使うレシピ。have なら今の持ち物（{name: count}）だけで作れるものだけ。
// 作れるものを先に、材料の種類が少ないものを先に
export function usesOf (index, name, inventory = {}, have = false) {
  const canMake = (r) => r.ingredients.every((i) => i.alts.reduce((n, a) => n + (inventory[a] ?? 0), 0) >= i.count)
  const all = usedIn(index, name).map((r) => ({ r, ok: canMake(r) }))
  const shown = (have ? all.filter((x) => x.ok) : all)
    .sort((a, b) => Number(b.ok) - Number(a.ok) || a.r.ingredients.length - b.r.ingredients.length)
  const seen = new Set()
  const out = []
  for (const { r, ok } of shown) {
    if (seen.has(r.result)) continue // 同じ物の別のレシピ（色違いの材料など）は 1 つだけ
    seen.add(r.result)
    out.push({ ...describeRecipe(r), ...(ok ? { can_make_now: true } : {}) })
    if (out.length >= USES_SHOWN) break
  }
  return { total: new Set(all.map((x) => x.r.result)).size, recipes: out }
}

// find_foods: 食べられる物と、その取り方（レシピ、かまど、畑、狩り、掘る）。今の持ち物で食べられる物・
// 作れる物を先に。プレイする LLM が食料の名前を知らず、小麦を食べようとして断られた
// （wheat is not food）ので、一覧から引けるようにする。
export const FOODS_SHOWN = 12
export const FOODS_MAX = 30
// 食べると悪い効果がある物（飢えているときだけ）
const RISKY = new Map([
  ['rotten_flesh', 'hunger effect: only when starving'],
  ['spider_eye', 'poison'],
  ['poisonous_potato', 'poison'],
  ['pufferfish', 'poison'],
  ['suspicious_stew', 'random effect'],
  ['chicken', 'raw: may cause hunger (cook it first)']
])

// レシピにも掘る・落とす表にも出ない取り方（ソルバーの知識にない物）
const FOOD_EXTRA = {
  apple: 'dig oak_leaves / dark_oak_leaves (small chance)',
  sweet_berries: 'pick from a sweet_berry_bush (taiga)',
  glow_berries: 'pick from cave vines (lush caves)',
  chorus_fruit: 'chorus plants in the End (out of reach for now)',
  enchanted_golden_apple: 'rare loot only'
}

export function foodsInfo (deps, inventory = {}, { query = '', have = false, limit = FOODS_SHOWN } = {}) {
  const { md, index, names } = deps
  const q = String(query ?? '').trim().toLowerCase()
  const count = (n) => inventory[n] ?? 0
  const has = (i) => i.alts.reduce((n, a) => n + count(a), 0) >= i.count
  const foods = []
  for (const f of md.foodsArray) {
    const recipes = index ? index.recipesFor(f.name) : []
    const craft = recipes.filter((r) => r.station === 'crafting')
    const smelt = recipes.filter((r) => r.station === 'furnace')
    const carried = count(f.name)
    const canCraft = craft.some((r) => r.ingredients.every(has))
    const canSmelt = smelt.some((r) => r.ingredients.every(has))
    const ja = names.items[f.name] ?? ''
    if (q) {
      const inRecipe = [...craft, ...smelt].some((r) => r.ingredients.some((i) => i.alts.some((a) => a.includes(q)) || (i.tag ?? '').includes(q)))
      if (!f.name.includes(q) && !ja.includes(q) && !inRecipe) continue
    }
    if (have && !carried && !canCraft && !canSmelt) continue
    const how = itemInfo(f.name, { ...deps, index }).how_to_get
    const ways = []
    if (typeof how === 'object') {
      if (how.craft) ways.push(`craft ${how.craft[0].ingredients.join(' + ')}${how.craft[0].station === 'crafting_table' ? ' (crafting table)' : ''}`)
      if (how.smelt_from) ways.push(`smelt ${how.smelt_from.slice(0, 3).join(' / ')} in a furnace`)
      if (how.farm) ways.push('grow it: farmed(crop) — till, sow, harvest')
      if (how.dig) ways.push(`dig ${how.dig.slice(0, 2).map((d) => d.block).join(' / ')}`)
      if (how.mob_drop) ways.push(`from ${how.mob_drop.slice(0, 3).map((m) => m.mob).join(' / ')}`)
    }
    foods.push({
      name: f.name,
      ...(ja ? { ja } : {}),
      hunger: f.foodPoints,
      saturation: f.saturation,
      ...(carried ? { carried } : {}),
      ...(canCraft ? { can_craft_now: true } : {}),
      ...(canSmelt ? { can_smelt_now: true } : {}),
      ...(RISKY.has(f.name) ? { caution: RISKY.get(f.name) } : {}),
      how: ways.length ? ways : [FOOD_EXTRA[f.name] ?? 'no known way']
    })
  }
  // 持っている物、今作れる・焼ける物、それ以外。悪い効果のある物は最後。それぞれ満腹度の大きい順
  const rank = (f) => f.caution ? 3 : f.carried ? 0 : (f.can_craft_now || f.can_smelt_now) ? 1 : 2
  foods.sort((a, b) => rank(a) - rank(b) || b.hunger - a.hunger || b.saturation - a.saturation)
  const shown = foods.slice(0, Math.max(1, Math.min(FOODS_MAX, Math.floor(limit))))
  return { total: foods.length, shown: shown.length, foods: shown }
}
