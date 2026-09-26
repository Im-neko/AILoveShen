// ソルバーのためのゲームの知識: アイテムのグループ、アイテムの入手方法、データの補正。
//
// レシピ、ブロックのドロップ、モブのドロップは minecraft-data から取る。ドロップのデータには注意がいる:
// - silkTouch の印がついた項目はエンチャントした道具が要るので、入手元にしない（ガラスは入手元がない）
// - 葉は棒とリンゴをまれにしか落とさないのに dropChance 1 になっているので、NATURAL_BLOCKS にある
//   自然のブロックだけを入手元に数える（葉は入っていない）
// - 羊は entityLoot に羊毛がない（色はエンティティのメタデータ）: EXTRA_MOB_DROPS で足す
// - 狩るのは HUNTABLE の動物だけ（ゾンビは食料に数えられる rotten_flesh を落とす。ウサギは
//   ボットより速く逃げる）
// - 染める: minecraft-data は染色のレシピを 1 通りしか載せない（青い羊毛は黒い羊毛＋青の染料）。
//   同じ種類からのレシピは外している（ベッドからベッドを作らない）ので、色つきの羊毛が作れなかった:
//   DYED で白い羊毛＋染料を足す。色つきのベッドはその色の羊毛 3 つから作る
// - 花（染料の材料）は自然のブロックに入れる（ヤグルマギクから青、ケシから赤…）
// 精錬は minecraft-data にない: SMELTING にかまどで作るものを並べる（アイテムごとに材料は1つ）。

// グループ: グループの必要はどのメンバーでも満たせる（例: 家の壁はどの板材でもよい）
const GROUP_PATTERNS = {
  planks: /_planks$/,
  log: /^(?!stripped_).*_log$/,
  door: /^(?!iron_).*_door$/,
  bed: /_bed$/,
  wool: /_wool$/,
  sword: /_sword$/,
  sapling: /(_sapling|^mangrove_propagule)$/,
  hoe: /_hoe$/
}
// 確率で落ちるもの（設計書 33）: 葉からは苗木だけ（棒とリンゴの数字は当てにならない）、草からは種。
// 掘っても落ちないことが多い（葉 5%、草 12.5%）ので、掘るのは失敗に数えない（primitives の dig）
export const CHANCE_DROP_BLOCKS = /(_leaves|^short_grass|^tall_grass|^fern)$/
const SEED_GRASS = ['short_grass', 'tall_grass', 'fern']
const FOOD_EXCLUDED = new Set(['rotten_flesh', 'spider_eye', 'poisonous_potato', 'pufferfish', 'suspicious_stew',
  'chorus_fruit', 'ominous_bottle'])

// ドロップのために掘ってよい、自然にあるブロック（プレイヤーの建てたものは含めない: 家の壁は
// 板材なので板材は入れない。原木は入れ、掘る候補からは家を除く）
const NATURAL_BLOCKS = [/^(?!stripped_).*_log$/, /^(stone|granite|diorite|andesite|deepslate|tuff)$/,
  /^(dirt|grass_block|coarse_dirt|podzol|sand|red_sand|gravel|clay)$/, /_ore$/, /^(sugar_cane|pumpkin|melon)$/,
  /^(dandelion|poppy|blue_orchid|allium|azure_bluet|(red|orange|white|pink)_tulip|oxeye_daisy|cornflower|lily_of_the_valley|sunflower|lilac|rose_bush|peony)$/]

// 染めた羊毛: 白い羊毛＋その色の染料（上の注意）
const DYED = /^(orange|magenta|light_blue|yellow|lime|pink|gray|light_gray|cyan|purple|blue|brown|green|red|black)_(wool)$/

// かまどが何（アイテムかグループ）から何を作るか。出力1つに材料1つ、それぞれ 10 秒
const SMELTING = {
  iron_ingot: 'raw_iron',
  charcoal: 'log',
  cooked_beef: 'beef',
  cooked_porkchop: 'porkchop',
  cooked_mutton: 'mutton',
  cooked_chicken: 'chicken'
}
// バニラのレシピ全部（recipes.mjs、設計書 29）。index.mjs が読み込んで渡す。なければ minecraft-data だけ
let INDEX = null
export function useRecipes (index) { INDEX = index }

// かまどが材料のアイテム（アイテム名）から作るもの。なければ null
export function smeltingProduct (input) {
  const fromIndex = () => INDEX?.all.find((r) => r.station === 'furnace' && r.ingredients[0].alts.includes(input))?.result ?? null
  return Object.keys(SMELTING).find((out) => {
    const from = SMELTING[out]
    return from === input || GROUP_PATTERNS[from]?.test(input)
  }) ?? fromIndex()
}

// 同じ種類から作るレシピ（ベッド→ベッド、羊毛→羊毛）の材料: 白いものがあれば白だけに絞る（染める）。
// 白がなければ、そのレシピは使わない（染め直しの遠回りと循環を避ける）
function sameKindAlts (item, alts) {
  const group = Object.entries(GROUP_PATTERNS).find(([, re]) => re.test(item))?.[0]
  const same = alts.filter((a) => a === item || (group && GROUP_PATTERNS[group].test(a)))
  if (!same.length) return alts
  const white = alts.find((a) => a.startsWith('white_') && a !== item)
  return white ? [white] : null
}

// 材料の候補 → solver の spec（アイテム名、#タグ、any:a|b）
function specOf (alts, tag) {
  if (alts.length === 1) return alts[0]
  return tag ? `#${tag}` : `any:${alts.join('|')}`
}

// 燃料1個で精錬できる数: 石炭と木炭は 8 個、板材と原木は 1.5 個
export const FUELS = [{ spec: 'coal', per: 8 }, { spec: 'charcoal', per: 8 }, { spec: 'planks', per: 1.5 }, { spec: 'log', per: 1.5 }]

export const HUNTABLE = new Set(['cow', 'pig', 'sheep', 'chicken', 'mooshroom'])
const EXTRA_MOB_DROPS = { sheep: ['white_wool'] }

// グループのメンバーを別のメンバーに変えるレシピ（羊毛やベッドの染色）は役に立たない
const sameGroup = (a, b) => Object.values(GROUP_PATTERNS).some((re) => re.test(a) && re.test(b))

export class Knowledge {
  constructor (md, index = INDEX) {
    this.md = md
    this.index = index
    this.blockSources = new Map() // アイテム -> [ブロック名]
    for (const loot of md.blockLootArray) {
      if (!NATURAL_BLOCKS.some((re) => re.test(loot.block))) continue
      for (const d of loot.drops) {
        if (d.silkTouch || !md.itemsByName[d.item]) continue
        push(this.blockSources, d.item, loot.block)
      }
    }
    for (const loot of md.blockLootArray) {
      if (!loot.block.endsWith('_leaves')) continue
      for (const d of loot.drops) if (GROUP_PATTERNS.sapling.test(d.item)) push(this.blockSources, d.item, loot.block)
    }
    for (const b of SEED_GRASS) if (md.blocksByName[b]) push(this.blockSources, 'wheat_seeds', b)
    this.mobSources = new Map() // アイテム -> [エンティティ名]
    for (const loot of md.entityLootArray) {
      if (!HUNTABLE.has(loot.entity)) continue
      for (const d of loot.drops) push(this.mobSources, d.item, loot.entity)
    }
    for (const [mob, items] of Object.entries(EXTRA_MOB_DROPS)) for (const item of items) push(this.mobSources, item, mob)
  }

  // 必要なもののアイテム: グループ名かアイテム名 -> { label, members: [アイテム名] }
  resolve (spec) {
    if (spec.startsWith('#') && this.index) return { label: spec.slice(1), members: this.index.tagMembers(spec.slice(1)) }
    if (spec.startsWith('any:')) return { label: spec.slice(4).split('|').join(' or '), members: spec.slice(4).split('|') }
    if (spec === 'food') {
      return { label: 'food', members: this.md.foodsArray.map((f) => f.name).filter((n) => !FOOD_EXCLUDED.has(n)) }
    }
    const re = GROUP_PATTERNS[spec]
    if (re) return { label: spec, members: this.md.itemsArray.map((i) => i.name).filter((n) => re.test(n)) }
    if (this.md.itemsByName[spec]) return { label: spec, members: [spec] }
    throw new Error(`unknown item or group: ${spec}`)
  }

  isMember (spec, name) {
    return this.resolve(spec).members.includes(name)
  }

  // item のクラフトのレシピ: [{ 結果の個数, 材料 {name: count}, needsTable }]
  recipes (item) {
    if (this.index) return this.recipesFromIndex(item)
    const id = this.md.itemsByName[item]?.id
    const out = []
    for (const r of this.md.recipes[id] ?? []) {
      const cells = r.inShape ? r.inShape.flat() : r.ingredients
      const ingredients = {}
      for (const c of cells) {
        if (c == null) continue
        const name = this.md.items[c]?.name
        if (!name) continue
        ingredients[name] = (ingredients[name] ?? 0) + 1
      }
      if (Object.keys(ingredients).some((n) => n === item || sameGroup(n, item))) continue
      const needsTable = r.inShape ? r.inShape.length > 2 || r.inShape.some((row) => row.length > 2) : cells.length > 4
      out.push({ count: r.result.count, ingredients, needsTable })
    }
    const dyed = item.match(DYED)
    if (dyed && this.md.itemsByName[`${dyed[1]}_dye`]) {
      out.push({ count: 1, ingredients: { [`white_${dyed[2]}`]: 1, [`${dyed[1]}_dye`]: 1 }, needsTable: false })
    }
    return out
  }

  // バニラのレシピ全部から（設計書 29）: 作業台・手持ちのクラフトだけ（石切台・鍛冶台はまだ使えない）
  recipesFromIndex (item) {
    const out = []
    for (const r of this.index.recipesFor(item)) {
      if (r.station !== 'crafting') continue
      const ingredients = {}
      let usable = true
      for (const ing of r.ingredients) {
        const alts = sameKindAlts(item, ing.alts)
        if (!alts) { usable = false; break }
        const spec = specOf(alts, alts.length === ing.alts.length ? ing.tag : null)
        ingredients[spec] = (ingredients[spec] ?? 0) + ing.count
      }
      if (usable) out.push({ count: r.count, ingredients, needsTable: r.needsTable })
    }
    return out
  }

  // item（アイテムかグループ）のかまどの材料。なければ null
  smeltingInput (item) {
    if (SMELTING[item]) return SMELTING[item]
    const r = this.index?.recipesFor(item).find((x) => x.station === 'furnace')
    return r ? specOf(r.ingredients[0].alts, r.ingredients[0].tag) : null
  }

  // そのブロックを回収できる道具。素手でよければ null
  harvestTools (block) {
    const tools = this.md.blocksByName[block]?.harvestTools
    return tools ? Object.keys(tools).map((id) => this.md.items[id].name) : null
  }
}

function push (map, key, value) {
  if (!map.has(key)) map.set(key, [])
  if (!map.get(key).includes(value)) map.get(key).push(value)
}
