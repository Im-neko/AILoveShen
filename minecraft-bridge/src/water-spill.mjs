// 畑の水を穴に置いたつもりが、穴の外（畑の面やその上）に水源ができて流れ出したときの後始末。流れたままだと、
// 作物を流し続ける（2026-10-07 19:56: 水源が穴の隣の畑の 2 段上にでき、植わった小麦が 47 から 31 株に減った。設計書 113）
const SPILL_RADIUS = 3 // 穴から横にこの距離まで探す
const SPILL_UP = 3 // 穴の高さからこの段数上まで探す
const SCOOP_MAX = 3
const SCOOP_REACH = 4.5

export const isSource = (b) => b?.name === 'water' && Number(b.getProperties?.().level ?? b.metadata ?? 1) === 0

// 穴のまわりにある、穴の中ではない水源（ボットに近い順）
export function straySources (bot, hole) {
  const me = bot.entity.position
  const found = []
  for (let dx = -SPILL_RADIUS; dx <= SPILL_RADIUS; dx++) {
    for (let dz = -SPILL_RADIUS; dz <= SPILL_RADIUS; dz++) {
      for (let dy = 0; dy <= SPILL_UP; dy++) {
        const p = hole.offset(dx, dy, dz)
        if (!p.equals(hole) && isSource(bot.blockAt(p))) found.push(p)
      }
    }
  }
  return found.sort((a, b) => a.distanceTo(me) - b.distanceTo(me))
}

// 空のバケツで汲み戻す。near(p): p に届く所へ歩く。バケツは 1 回汲むと満たされるので、汲めるのは 1 つだけ（残りは left）
export async function scoopBack (bot, hole, near) {
  let scooped = 0
  for (const p of straySources(bot, hole).slice(0, SCOOP_MAX)) {
    const bucket = bot.inventory.items().find((i) => i.name === 'bucket')
    if (!bucket) break
    if (bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0).distanceTo(p.offset(0.5, 0.5, 0.5)) > SCOOP_REACH) await near(p)
    await bot.equip(bucket, 'hand')
    await bot.lookAt(p.offset(0.5, 0.5, 0.5), true)
    bot.activateItem()
    await bot.waitForTicks(6)
    if (!isSource(bot.blockAt(p))) scooped++
  }
  return { scooped, left: straySources(bot, hole).length }
}
