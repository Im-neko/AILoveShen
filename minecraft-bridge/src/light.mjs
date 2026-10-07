// サーバーが送る光（ブロックの光・空の光）を、正しい位置で読む（docs/design/104）。
//
// prismarine-chunk 1.41 の loadParsedLight は、光の欄（1 バイトに 2 マス分、x が 1 ずつ並ぶ）を、
// ブロックの種類と同じ「大きな数（long）の並び」として読むため、x が 16 マスごとに鏡のように
// 入れ替わって読める（松明のあるマス x=71 の 14 が x=73 に、隣の 13 が x=72 に出る。2026-10-07、
// 実機で松明のまわりを 1 マスずつ測って確かめた）。block.light / block.skyLight はそのまま信用できない。
// ここは、同じ欄から、入れ替わりを戻した位置を読む。ライブラリが直ったら、test/light.test.mjs が落ちる
// （そのときはこの戻しを外す）。

// 光がこれ未満のマスは暗すぎる。モブが湧くのは 0 だけ（1.18 以降のオーバーワールド）だが、読みの遅れや、
// 松明が壊されたときの余裕として 1 つ余分に見る（松明は置いたマスで 14、1 ブロックごとに 1 下がる）
export const MIN_LIGHT = 2

// 欄の中の x（0〜15）の、入れ替わった先。8 バイト（16 マス）の中で、バイトの並びが逆になる（組の中の
// 上位・下位の別はそのまま）。2 回かけると元に戻る
export const mirrorX = (x) => 14 - (x & 14) + (x & 1)

function lightAt (bot, pos, key) {
  const x = Math.floor(pos.x)
  const y = Math.floor(pos.y)
  const z = Math.floor(pos.z)
  const column = bot.world?.getColumnAt?.({ x, y, z })
  if (!column) return null
  const section = column[key]?.[Math.floor((y - column.minY) / 16) + 1]
  // 欄がない: サーバーが（まだ）送っていない。0（暗い）とは違うので、分かるまで null
  if (!section) return null
  return section.get((((y - column.minY) & 15) << 8) | ((z & 15) << 4) | mirrorX(x & 15))
}

// そのマスのブロックの光（0〜15）。分からなければ null
export const blockLightAt = (bot, pos) => lightAt(bot, pos, 'blockLightSections')
// そのマスの空の光（0〜15。昼夜では変わらない）。分からなければ null
export const skyLightAt = (bot, pos) => lightAt(bot, pos, 'skyLightSections')

// 暗すぎる（MIN_LIGHT 未満）と分かっているか。分からなければ false（暗いと決めつけない）
export const tooDarkAt = (bot, pos) => {
  const light = blockLightAt(bot, pos)
  return light !== null && light < MIN_LIGHT
}
