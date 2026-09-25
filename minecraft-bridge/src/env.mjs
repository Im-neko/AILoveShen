// リポジトリ直下の .env を環境変数として読む（MC_HOST、MC_PORT、BRIDGE_PORT など）。すでにある
// 環境変数（シェルで export したもの）が優先する。ファイルがなければ何もしない。
//
// 他のモジュールは読み込まれた時点で process.env を読む（mirror.mjs の MIRROR_PORT など）ので、
// index.mjs はこれを最初に import する。ENV_FILE で別のファイルを指定できる。
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const file = process.env.ENV_FILE ?? fileURLToPath(new URL('../../.env', import.meta.url))
if (existsSync(file)) {
  process.loadEnvFile(file)
  console.log(`[bridge] 環境変数を ${file} から読んだ`)
}
