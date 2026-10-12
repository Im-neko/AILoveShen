# 173. 今いるバイオームを状況に載せる

2026-10-12、user: 「今いるバイオームって情報として渡してる？草原バイオームを探して。というのが伝わるのかどうかを気になっています」→「1からやって」

## 1. 事実
- ブリッジにも LLM のプロンプトにも、バイオームの情報は一切なかった（`biome` は設計書 01 に出るだけ）。羊毛3個（ベッドの材料）の小目標が、羊を一匹も見つけられないまま難航していた（2026-10-12、10/4 から32回・今日だけで6回やり直し、探索は北・西に偏り、`find_blocks("sheep")` を3回断られた）。LLM は「草原へ」と考えても、今どこがどのバイオームか、どちらが草原か、を知らない
- Mineflayer のブロックには `block.biome`（`{ id, name }`）がある。ただし 1.21.4 の実機では、サーバーが群系の表（registry data）を送ったあと `block.biome.name` が空文字になる（id は 21 など正しい）。`bot.registry.biomes[id].name` で引くと `forest` と読めた

## 2. 変更（段階 1: 今いる所のバイオーム）
- `observe.mjs` `biomeName` / `biomeNameAt`: 足元のブロックの `biome.name`、空なら `bot.registry.biomes[id]` / `biomesArray[id]` の名前、読めなければ null。`/observe` の `self.biome` と `/state` の `self.biome`、`inspect` の `biome: { id, name }`
- Python `stream_context.py`: 状況（`_format_situation`）に `- 今いるバイオーム: 森（forest）` を足す。日本語は主なものだけ（`BIOME_JA`。`deep_lukewarm_ocean` のように末尾が合うものは長く合うほう）、ないものは英語名のまま。読めなければ行を出さない

## 3. 作っていないもの（段階 2・3）
- 世界の地図（`worldmap`）にバイオームを持たせ、探した所・探していない所のバイオームを覚える
- 探索の候補に「羊のいる草原（plains / meadow / savanna / forest）のうち探していない方へ」を出す
- 羊（`HUNTABLE`）を `find_blocks` で探そうとする誤りを道具の説明で減らす
- 実機の確認: ブリッジの再起動後、`/state` の `self.biome` が `forest`（家のまわり）と読めた。Python の再起動後にプロンプトに出るかは未確認
