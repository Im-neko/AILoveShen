# vendor（アバターのページが読むライブラリ）

OBS のブラウザソースがネットにつながらなくても動くように、CDN ではなくここから配る
（目標ボードの `/vendor/...`）。どれも MIT ライセンス（各ディレクトリの LICENSE）。

| ライブラリ | バージョン | ファイル |
|---|---|---|
| three | 0.186.1 | `three/build/three.module.js`、`three.core.js`、`examples/jsm/loaders/GLTFLoader.js` と、それが読む `utils/` の 2 つ |
| @pixiv/three-vrm | 3.5.5 | `three-vrm/three-vrm.module.min.js` |

更新するとき: `npm install three@<版> @pixiv/three-vrm@<版>` をして、同じファイルをここに写し、この表を直す。
