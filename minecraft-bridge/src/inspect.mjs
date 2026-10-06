// 行き詰まったときの実験の目（設計書 85）: 1 つのマスや自分の今を細かく見る inspect と、ブリッジが残した直近の
// 失敗の記録を読む diagnostics。どちらも世界を変えない。LLM は、同じ手を繰り返す代わりに、これで原因を調べ、
// 変えるものを 1 つだけ変えて（use_item の face / sneak / 素手）もう一度見る。
//
// ふだんの調べもの（find_blocks など）は種類で探す。ここは「この 1 マスが今どうなっているか」を座標で見る。

import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { round } from './observe.mjs'
import { protectedReason } from './home.mjs'
import { closedSpace, isolatedSpace, pillarUnder } from './reach.mjs'
import { liquidReason } from './liquid.mjs'
import { listRefusals } from './refusals.mjs'

const LOGS = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'logs')
const REACH = 4.5 // 右クリックと掘りが届く、目からの距離の目安
const NEAR_ENTITY = 3
const NEIGHBOURS = { up: [0, 1, 0], down: [0, -1, 0], north: [0, 0, -1], south: [0, 0, 1], east: [1, 0, 0], west: [-1, 0, 0] }

// 記録の種類 → [ファイル, 何の記録か]。場所は各記録を書くモジュールと同じ環境変数で変えられる
export const DIAGNOSTICS = {
  door: [() => process.env.DOOR_TOGGLES_FILE || path.join(LOGS, 'door-toggles.jsonl'), 'a home door that would not open or close (each way tried, the door state, block updates the server sent)'],
  place: [() => process.env.PLACE_REFUSALS_FILE ?? path.join(LOGS, 'place-refusals.jsonl'), 'a block the server refused to place (the cell, what is around it, where the bot stood)'],
  stuck: [() => process.env.STUCK_WALKS_FILE ?? path.join(LOGS, 'stuck-walks.jsonl'), 'a walk that got stuck (position, the path, the blocks around)']
}

const ago = (iso) => {
  const t = Date.parse(iso)
  return Number.isFinite(t) ? `${Math.max(0, Math.round((Date.now() - t) / 60000))} min ago` : null
}

// 自分の今: 位置・向き・スニーク・持ち物・視線の先
export function inspectSelf (bot, state = null) {
  const me = bot.entity.position
  const ray = bot.blockAtCursor?.(5)
  // 家のドアに入れないとき、ドアが開かないのか、そこへ行く道がないのかを分ける: 家のドアまでの距離と、閉じた所にいるか
  const door = state?.home?.outside
  const shut = closedSpace(bot)
  const cliff = shut ? null : isolatedSpace(bot)
  return {
    self: {
      ...(door ? { home_door: { x: door.x, y: door.y, z: door.z, horizontal_m: round(Math.hypot(me.x - door.x - 0.5, me.z - door.z - 0.5)), below_m: round(door.y - me.y) } } : {}),
      closed_space: shut ? { cells: shut.cells, how_out: 'you cannot walk out of here: the candidate "climb out of the closed space" piles up the dirt or cobblestone you carry (no pickaxe needed)' } : null,
      // 積んだ柱の頂上など、まわりが崖で歩いて出られない所: 積み上げても出られない。降りる
      over_a_drop: cliff ? { cells: cliff.cells, on_a_pillar: !!pillarUnder(bot), how_out: pillarUnder(bot) ? 'you stand on a free-standing pillar: the candidate "climb down the pillar" digs it away block by block' : 'every side drops more than 3 blocks: piling up does not help; walk or dig down to the ground' } : null,
      x: round(me.x), y: round(me.y), z: round(me.z),
      yaw: round(bot.entity.yaw), pitch: round(bot.entity.pitch),
      sneaking: !!bot.controlState?.sneak, sprinting: !!bot.controlState?.sprint,
      held: bot.heldItem?.name ?? null,
      on_ground: !!bot.entity.onGround, in_water: !!bot.entity.isInWater, riding: !!bot.vehicle,
      looking_at: ray ? { block: ray.name, x: ray.position.x, y: ray.position.y, z: ray.position.z } : null
    }
  }
}

// 1 マスの今: ブロックの名前・状態（開閉・向きなど）、6 方向の隣、届くか、間に何があるか、断られる理由
export function inspectCell (bot, state, pos) {
  const block = bot.blockAt(pos)
  if (!block) return { cell: { x: pos.x, y: pos.y, z: pos.z }, loaded: false }
  const eye = bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0)
  const center = pos.offset(0.5, 0.5, 0.5)
  const distance = eye.distanceTo(center)
  const neighbours = {}
  for (const [name, [dx, dy, dz]] of Object.entries(NEIGHBOURS)) neighbours[name] = bot.blockAt(pos.offset(dx, dy, dz))?.name ?? null
  // 目からそのマスの中心までの線上で、最初に当たるブロック（それがそのマスでなければ、間にある）
  let between = null
  try {
    const dir = center.minus(eye)
    const hit = bot.world.raycast(eye, dir.normalize(), Math.min(distance + 1, 8))
    if (hit && !hit.position.equals(pos)) between = { block: hit.name, x: hit.position.x, y: hit.position.y, z: hit.position.z }
  } catch {
    between = null
  }
  const entities = Object.values(bot.entities ?? {})
    .filter((e) => e !== bot.entity && e.position && e.position.distanceTo(center) <= NEAR_ENTITY)
    .map((e) => ({ name: e.name ?? e.type, id: e.id }))
  return {
    cell: { x: pos.x, y: pos.y, z: pos.z },
    block: block.name,
    props: block.getProperties?.() ?? null,
    solid: block.boundingBox === 'block',
    neighbours,
    eye_distance: round(distance),
    within_reach: distance <= REACH,
    in_the_way: between,
    protected: protectedReason(state, pos, 0, block) ?? null,
    liquid_holder: liquidReason(bot, pos, block) ?? null,
    // そのマスへの手が前に断られた（どの動詞でも）。なければ空
    refused_before: listRefusals(state).filter((r) => r.x === pos.x && r.y === pos.y && r.z === pos.z).map(({ tool, reason, minutes_ago: minutesAgo }) => ({ tool, reason, minutes_ago: minutesAgo })),
    entities
  }
}

// ブリッジが残した直近の失敗の記録。kind を省けば、種類ごとの最後の 1 件
export function readDiagnostics (kind, n = 1) {
  const kinds = kind ? [kind] : Object.keys(DIAGNOSTICS)
  if (kind && !DIAGNOSTICS[kind]) return `unknown kind ${kind} (one of ${Object.keys(DIAGNOSTICS).join(', ')})`
  const count = Math.max(1, Math.min(5, Number(n) || 1))
  const out = {}
  for (const k of kinds) {
    const [file, what] = DIAGNOSTICS[k]
    const f = file()
    const lines = existsSync(f) ? readFileSync(f, 'utf8').trim().split('\n').filter(Boolean) : []
    out[k] = {
      about: what,
      records: lines.slice(-count).map((l) => {
        try {
          const r = JSON.parse(l)
          return { ...(r.at || r.time ? { when: ago(r.at ?? r.time) } : {}), ...r }
        } catch {
          return { raw: l.slice(0, 300) }
        }
      })
    }
  }
  return out
}
