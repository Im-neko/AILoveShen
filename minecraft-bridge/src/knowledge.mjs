// Game knowledge for the solver: item groups, how items are obtained, and corrections to the data.
//
// Recipes, block drops and mob drops come from minecraft-data. The drop data needs care:
// - entries flagged silkTouch need an enchanted tool, so they are not sources (glass has none)
// - leaves list sticks and apples with dropChance 1 although they are rare, so only natural
//   blocks on NATURAL_BLOCKS count as sources (leaves are not on it)
// - sheep have no wool in entityLoot (the color is entity metadata): EXTRA_MOB_DROPS adds it
// - only the animals on HUNTABLE are hunted (zombies drop rotten_flesh, which counts as food;
//   rabbits outrun the bot)
// Smelting is not in minecraft-data: SMELTING lists what the furnace makes (one input per item).

// Groups: any member satisfies a need for the group (e.g. a house wall takes any planks)
const GROUP_PATTERNS = {
  planks: /_planks$/,
  log: /^(?!stripped_).*_log$/,
  door: /^(?!iron_).*_door$/,
  bed: /_bed$/,
  wool: /_wool$/,
  sword: /_sword$/
}
const FOOD_EXCLUDED = new Set(['rotten_flesh', 'spider_eye', 'poisonous_potato', 'pufferfish', 'suspicious_stew',
  'chorus_fruit', 'ominous_bottle'])

// Blocks found in nature that may be dug for their drops (never a player's builds: a house wall is
// made of planks, so planks are not listed; logs are, and the dig candidates exclude the house)
const NATURAL_BLOCKS = [/^(?!stripped_).*_log$/, /^(stone|granite|diorite|andesite|deepslate|tuff)$/,
  /^(dirt|grass_block|coarse_dirt|podzol|sand|red_sand|gravel|clay)$/, /_ore$/, /^(sugar_cane|pumpkin|melon)$/]

// What a furnace makes from what (an item or a group), one input per output, 10s each
const SMELTING = {
  iron_ingot: 'raw_iron',
  charcoal: 'log',
  cooked_beef: 'beef',
  cooked_porkchop: 'porkchop',
  cooked_mutton: 'mutton',
  cooked_chicken: 'chicken'
}
// What a furnace makes from an input item (an item name), or null
export function smeltingProduct (input) {
  return Object.keys(SMELTING).find((out) => {
    const from = SMELTING[out]
    return from === input || GROUP_PATTERNS[from]?.test(input)
  }) ?? null
}

// Items burnt per item smelted: coal and charcoal burn 8 items, planks and logs 1.5
export const FUELS = [{ spec: 'coal', per: 8 }, { spec: 'charcoal', per: 8 }, { spec: 'planks', per: 1.5 }, { spec: 'log', per: 1.5 }]

export const HUNTABLE = new Set(['cow', 'pig', 'sheep', 'chicken', 'mooshroom'])
const EXTRA_MOB_DROPS = { sheep: ['white_wool'] }

// Recipes that turn a member of a group into another member (dyeing wool or beds) never help
const sameGroup = (a, b) => Object.values(GROUP_PATTERNS).some((re) => re.test(a) && re.test(b))

export class Knowledge {
  constructor (md) {
    this.md = md
    this.blockSources = new Map() // item -> [block names]
    for (const loot of md.blockLootArray) {
      if (!NATURAL_BLOCKS.some((re) => re.test(loot.block))) continue
      for (const d of loot.drops) {
        if (d.silkTouch || !md.itemsByName[d.item]) continue
        push(this.blockSources, d.item, loot.block)
      }
    }
    this.mobSources = new Map() // item -> [entity names]
    for (const loot of md.entityLootArray) {
      if (!HUNTABLE.has(loot.entity)) continue
      for (const d of loot.drops) push(this.mobSources, d.item, loot.entity)
    }
    for (const [mob, items] of Object.entries(EXTRA_MOB_DROPS)) for (const item of items) push(this.mobSources, item, mob)
  }

  // A need's item: a group name or an item name -> { label, members: [item names] }
  resolve (spec) {
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

  // Crafting recipes for item: [{ result count, ingredients {name: count}, needsTable }]
  recipes (item) {
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
    return out
  }

  // The furnace input for item (an item or a group), or null
  smeltingInput (item) {
    return SMELTING[item] ?? null
  }

  // Tools that can harvest the block, or null when a bare hand does
  harvestTools (block) {
    const tools = this.md.blocksByName[block]?.harvestTools
    return tools ? Object.keys(tools).map((id) => this.md.items[id].name) : null
  }
}

function push (map, key, value) {
  if (!map.has(key)) map.set(key, [])
  if (!map.get(key).includes(value)) map.get(key).push(value)
}
