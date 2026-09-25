// 別のボットで狩り（動物への attack プリミティブ）を再現する: 木の剣を渡し、羊を召喚して追う。
import { execFileSync } from 'node:child_process'
import mineflayer from 'mineflayer'
import pathfinderPkg from 'mineflayer-pathfinder'
import { configureMovements, PRIMITIVES } from '../src/primitives.mjs'
import { round } from '../src/observe.mjs'

const rcon = (cmd) => execFileSync('docker', ['exec', 'ailoveshen-minecraft', 'rcon-cli', cmd]).toString().trim()
const name = 'HuntCheck'
const bot = mineflayer.createBot({ host: 'localhost', port: 25565, username: name, version: '1.21.4', auth: 'offline' })
bot.loadPlugin(pathfinderPkg.pathfinder)
const state = { plan: null, home: null, unreachableDrops: new Set() }
bot.once('spawn', async () => {
  configureMovements(bot, state)
  rcon(`gamemode survival ${name}`)
  rcon(`clear ${name}`)
  rcon(`give ${name} minecraft:wooden_sword`)
  rcon(`effect give ${name} minecraft:resistance 60 4`)
  await bot.waitForTicks(40)
  rcon(`execute at ${name} run summon minecraft:sheep ^ ^ ^4 {Tags:["probe"]}`)
  await bot.waitForTicks(10)
  const sheep = Object.values(bot.entities).find((e) => e.name === 'sheep' && e.position.distanceTo(bot.entity.position) < 8)
  let swings = 0
  bot._client.on('packet', (d, m) => { if (m.name === 'damage_event' && d.entityId === sheep?.id) console.log(`[hurt] 羊 ${round(sheep.position.distanceTo(bot.entity.position))}m`) })
  const origAttack = bot.attack.bind(bot)
  bot.attack = (t) => { swings++; console.log(`[attack] dist=${round(t.position.distanceTo(bot.entity.position))} eyeDist=${round(t.position.offset(0, t.height / 2, 0).distanceTo(bot.entity.position.offset(0, bot.entity.eyeHeight, 0)))}`); return origAttack(t) }
  const trace = setInterval(() => sheep && console.log(`t dist=${round(sheep.position.distanceTo(bot.entity.position))} moving=${bot.pathfinder.isMoving()}`), 1000)
  try { console.log('狩り:', await PRIMITIVES.attack(bot, state, { entityId: sheep.id, target: 'sheep', hostile: false }, new AbortController().signal)) } catch (e) { console.log('狩りに失敗:', e.message) }
  clearInterval(trace)
  console.log('攻撃回数', swings, 'インベントリ', bot.inventory.items().map((i) => `${i.name}x${i.count}`).join(','))
  rcon('kill @e[type=minecraft:sheep,tag=probe]')
  bot.quit()
})
