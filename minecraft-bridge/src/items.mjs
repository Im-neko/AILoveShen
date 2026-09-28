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
