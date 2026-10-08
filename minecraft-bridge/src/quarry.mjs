// 採掘場: 石・土・砂利を掘る場所と、鉱石へ下りる階段を、毎回同じ所にする（docs/design/64_quarry.md）。
//
// 毎回その時いる所・いちばん近いブロックから掘るので、家のまわりが穴だらけになった（2026-09-30、
// user 「採掘に行くとき毎回ちがうところから掘ってるから家の周りがあなぼこだらけ」）。階段掘り（dig_down）は
// 立っている所から新しい階段を掘り、地表の石や土は近い順に選ばれていた。
//
//   - 採掘の場所（quarry.spot）: 最後に掘った自然の地形（TERRAIN）の場所。ここから QUARRY_RADIUS の内を、
//     近い順ではなく場所に近い順に掘る（掘った跡が広がって続く）。今いる所から QUARRY_REACH より遠ければ、
//     そこで掘った場所に移る（遠出した先でわざわざ戻らない）。掘る手（primitives の dig）が更新する
//   - 階段（quarry.shafts）: dig_down が下りた階段の入口と底。次に鉱石へ下りるときは、その底まで歩いて
//     続きから掘る（鉱石が底より上にあるときは使えない。階段は下へしか掘らない）
//
// 状態は state.quarry = { spot, shafts }（home.mjs が保存と読み込みをする）

import vec3Pkg from 'vec3'
import { TERRAIN } from './leveling.mjs'

const { Vec3 } = vec3Pkg

export const QUARRY_REACH = 48 // ボットがこの内にいれば、その採掘の場所と階段を使う
export const QUARRY_RADIUS = 12 // 採掘の場所からこの内の地形を先に掘る
const SHAFT_MERGE = 3 // 入口がこの内なら同じ階段（続きを掘った）
const MAX_SHAFTS = 4

// 掘る場所をまとめる種類: 自然の地形（石、土、砂利、鉱石など。leveling.mjs と同じ）。木や作物ではない
export const isQuarryKind = (name) => TERRAIN.test(name)

const xyz = (p) => ({ x: p.x, y: p.y, z: p.z })
const vec = (p) => new Vec3(p.x, p.y, p.z)

export function quarryOf (state) {
  state.quarry ??= { spot: null, shafts: [] }
  state.quarry.shafts ??= []
  return state.quarry
}

// ボットの近くの採掘の場所（なければ null）
export function quarrySpot (state, me) {
  const spot = state.quarry?.spot
  return spot && vec(spot).distanceTo(me) <= QUARRY_REACH ? vec(spot) : null
}

// 意図して掘った地形を覚える（歩きながら経路が掘ったものは数えない）。場所がなければ、遠ければ、
// 場所から離れた所を掘ったら、そこを新しい場所にする
export function noteDig (state, pos, name, me) {
  if (!isQuarryKind(name)) return
  const spot = quarrySpot(state, me)
  if (spot && spot.distanceTo(pos) <= QUARRY_RADIUS) return
  quarryOf(state).spot = xyz(pos)
}

// 採掘の場所に近い順（場所がなければ、そのまま）
export function byQuarry (points, spot) {
  return spot ? [...points].sort((a, b) => a.distanceTo(spot) - b.distanceTo(spot)) : points
}

// 採掘の場所の内か
export const inQuarry = (spot, p) => !!spot && spot.distanceTo(p) <= QUARRY_RADIUS

// 今もある階段: 入口が施設（畑・採掘場など）の中にあるものは、あとから施設が建って入口がなくなった記録なので除く
// （2026-10-08: 畑の水のマスにあった古い入口が、畑の穴を直す整地を止めた）
export function liveShafts (state) {
  const inBox = (p, b) => p.x >= b.min.x && p.x <= b.max.x && p.z >= b.min.z && p.z <= b.max.z
  const boxes = Object.values(state.facilities ?? {}).map((f) => f.box).filter(Boolean)
  return (state.quarry?.shafts ?? []).filter((s) => !boxes.some((b) => inBox(s.top, b)))
}

// 続きから下りられる階段: ボットの近くで、底が鉱石の高さ（targetY）以上のもの。底が鉱石に近いものを先に
export function usableShaft (state, me, targetY) {
  return liveShafts(state)
    .filter((s) => Math.hypot(s.top.x - me.x, s.top.z - me.z) <= QUARRY_REACH && s.bottom.y >= targetY)
    .sort((a, b) => (a.bottom.y - targetY) - (b.bottom.y - targetY))[0] ?? null
}

// 下りた階段を覚える。入口が同じ階段の続きなら、底だけ深くする
export function noteShaft (state, top, bottom) {
  const q = quarryOf(state)
  const same = q.shafts.find((s) => Math.hypot(s.top.x - top.x, s.top.z - top.z) <= SHAFT_MERGE)
  if (same) {
    if (bottom.y < same.bottom.y) same.bottom = xyz(bottom)
    return
  }
  q.shafts.push({ top: xyz(top), bottom: xyz(bottom) })
  if (q.shafts.length > MAX_SHAFTS) q.shafts.splice(0, q.shafts.length - MAX_SHAFTS)
}

// 階段の入口の列（埋めない: leveling が穴として埋めてしまうと、階段が使えなくなる）
export const onShaftTop = (state, x, z) =>
  liveShafts(state).some((s) => Math.abs(s.top.x - x) <= SHAFT_MERGE && Math.abs(s.top.z - z) <= SHAFT_MERGE)
