// 水や溶岩をせき止めているブロック（設計書 54 の外: docs/design/55_liquid_and_trees.md）。
//
// 村の畑の縁の原木を、木と同じに集めて壊し、畑の水が流れ出した（2026-09-30）。守る基準を「名前」（家、
// 建物、原木）ではなく「役割」にする: 掘ると水や溶岩が流れ出るブロックは、自動の作業も道具の dig も
// 壊さない（demolish でも。流れ出すことを目的に壊すことはない）。

const HOLDS = [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0]] // 横と上（下の液体は、掘っても出てこない）
const LIQUIDS = new Set(['water', 'lava'])

export const isLiquid = (name) => LIQUIDS.has(name)

// 横か上に水・溶岩があるか（その位置のブロック自身は見ない）
export function holdsBackLiquid (bot, pos) {
  return HOLDS.some(([x, y, z]) => LIQUIDS.has(bot.blockAt(pos.offset(x, y, z))?.name))
}

// 掘れない理由か null。固いブロックだけ（草花・空気・液体そのものは水を止めていない）
export function liquidReason (bot, pos, block) {
  if (!block || block.boundingBox !== 'block') return null
  return holdsBackLiquid(bot, pos) ? 'it holds back water or lava: digging it would let the liquid run out' : null
}
