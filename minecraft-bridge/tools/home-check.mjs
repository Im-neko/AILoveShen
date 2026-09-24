// Enter and leave a house with a separate bot. Args: door x y z and the inward direction (e.g. 0 -1).
import { execFileSync } from 'node:child_process'
import mineflayer from 'mineflayer'
import pathfinderPkg from 'mineflayer-pathfinder'
import vec3Pkg from 'vec3'
import { configureMovements } from '../src/actions.mjs'
import { enterHome, leaveHome, isInside, isDoorOpen } from '../src/home.mjs'

const { Vec3 } = vec3Pkg
const [x, y, z, dx, dz] = process.argv.slice(2).map(Number)
const door = new Vec3(x, y, z)
const home = { door, inside: door.offset(dx, 0, dz), outside: door.offset(-dx, 0, -dz) }
// interior of a 5x5 house: 3x3 behind the door wall
const c = door.offset(dx * 2, 0, dz * 2)
home.min = c.offset(-1, 0, -1)
home.max = c.offset(1, 0, 1)
const rcon = (cmd) => execFileSync('docker', ['exec', 'ailoveshen-minecraft', 'rcon-cli', cmd]).toString().trim()
const name = 'HomeCheck'
const bot = mineflayer.createBot({ host: 'localhost', port: 25565, username: name, version: '1.21.4', auth: 'offline' })
bot.loadPlugin(pathfinderPkg.pathfinder)
bot.once('spawn', async () => {
  configureMovements(bot, { plan: null })
  rcon(`gamemode survival ${name}`)
  rcon(`effect give ${name} minecraft:resistance 120 4`)
  rcon(`tp ${name} ${x + 0.5 - dx * 6} ${y} ${z + 0.5 - dz * 6}`)
  await bot.waitForTicks(60)
  const report = (label) => console.log(`${label}: pos=${bot.entity.position.floored()} inside=${isInside(bot, home)} doorOpen=${isDoorOpen(bot, home)} rcon=${rcon(`execute if block ${x} ${y} ${z} #minecraft:wooden_doors[open=false]`)}`)
  report('start')
  try { await enterHome(bot, home); report('entered') } catch (e) { console.log('enter failed:', e.message); report('after enter failure') }
  try { await leaveHome(bot, home); report('left') } catch (e) { console.log('leave failed:', e.message); report('after leave failure') }
  bot.quit()
})
