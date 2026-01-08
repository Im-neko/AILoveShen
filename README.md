# AILoveShen - AI Streamer

[NitroGen](https://github.com/MineDojo/NitroGen)を使ってゲーム操作をしながら、学習した音声で会話する**Twitch向けAIストリーマー**プロジェクトです。

## プロジェクト概要

このプロジェクトは以下の技術を組み合わせています：

- **[NitroGen](https://github.com/MineDojo/NitroGen)** - Minecraftのプロシージャル生成とAIエージェント学習環境
- **[Style-Bert-VITS2](https://github.com/litagin02/Style-Bert-VITS2)** - 感情豊かな音声合成エンジン
- **Gemini 2.5** - メイン会話・対話生成
- **Gemini Flash** - コメントフィルタリング用軽量LLM（反応すべきか判断、コメント量に応じて閾値を動的調整）
- **MCP (Model Context Protocol)** - 記憶管理、表情操作、拡張機能
- **Twitch API** - チャット連携・配信制御

## 機能

- ゲーム内でのAIエージェント操作（NitroGen）
- ゲーム状況に応じた実況・考えの発話（メイン）
- コメントへの反応（サブ、コメントが来たら対応）
- 学習した音声モデルによるリアルタイム音声合成
- 感情・スタイルを制御可能な自然な会話

## アーキテクチャ

```
┌───────────────────────────────────────────────────────────────────────────┐
│                          AILoveShen Streamer                              │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  【メイン：ゲーム実況】                 【サブ：コメント対応】            │
│                                                                           │
│  ┌─────────────────────────────┐       ┌──────────────┐                  │
│  │         NitroGen            │       │ Twitch Chat  │                  │
│  │  ┌───────────────────────┐  │       └──────┬───────┘                  │
│  │  │    Game State Manager │  │              │                          │
│  │  │  ・位置/インベントリ  │  │              ▼                          │
│  │  │  ・周囲の状況         │  │       ┌──────────────┐                  │
│  │  │  ・イベント検知       │  │       │ Gemini Flash │                  │
│  │  └───────────┬───────────┘  │       │  (Filter)    │                  │
│  │              │              │       │ 動的閾値調整 │                  │
│  └──────────────│──────────────┘       └──────┬───────┘                  │
│                 │ ゲーム状態                  │ 反応すべきコメント       │
│                 ▼                             ▼                          │
│  ┌────────────────────────────────────────────────────┐                  │
│  │                    Gemini 2.5                      │                  │
│  │  ・ゲーム状況への実況・考え・反応（メイン）        │                  │
│  │  ・コメントへの返答（割り込み）                    │                  │
│  │  ・次の行動の意思決定                              │                  │
│  └────────────────────┬───────────────────────────────┘                  │
│                       │                                                   │
│         ┌─────────────┼─────────────┐                                    │
│         ▼             ▼             ▼                                    │
│  ┌───────────┐ ┌───────────┐ ┌───────────────────────┐                  │
│  │Style-Bert-│ │    MCP    │ │       NitroGen        │                  │
│  │  VITS2    │ │  Memory   │ │  ┌─────────────────┐  │                  │
│  │ (Voice)   │ │Expression │ │  │ Action Executor │  │                  │
│  │           │ │           │ │  │ LLMの意図を操作 │  │                  │
│  │           │ │           │ │  │ に変換して実行  │  │                  │
│  └───────────┘ └───────────┘ │  └─────────────────┘  │                  │
│         │                    └───────────────────────┘                  │
│         │                                                                 │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                        Twitch Stream                               │  │
│  │                    (Video + Audio + Chat)                          │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────┘
```

## セットアップ

### 必要要件

- Python 3.10+
- NVIDIA GPU（音声学習用、推論のみならCPU可）
- Minecraft Java Edition
- CUDA 11.8+

### インストール

```bash
# リポジトリをクローン
git clone https://github.com/YourUsername/AILoveShen.git
cd AILoveShen

# 依存関係のインストール
pip install -r requirements.txt

# 必要なモデルのダウンロード
python initialize.py
```

### NitroGenのセットアップ

```bash
# NitroGenのインストール（別途）
pip install nitrogen-ai

# Minecraft環境の設定
# 詳細は https://github.com/MineDojo/NitroGen を参照
```

## 使い方

### 音声合成（Style-Bert-VITS2）

音声合成エディターは`Editor.bat`をダブルクリックか、`python server_editor.py --inbrowser`すると起動します。

- CLIでの使い方は[こちら](/docs/CLI.md)を参照
- [よくある質問](/docs/FAQ.md)も参照
- API仕様は`python server_fastapi.py`起動後に`/docs`にて確認

### 音声学習

学習には2-14秒程度の音声ファイルが複数と、それらの書き起こしデータが必要です。

- `App.bat`または`python app.py`でWebUIを起動
- 「データセット作成」タブで音声ファイルをスライス・書き起こし
- 「学習」タブで学習を実行

詳細は[docs/CLI.md](docs/CLI.md)を参照してください。

### AIストリーマー起動（開発中）

```bash
# AILoveShen Streamerの起動
python streamer_main.py
```

## 今後の開発予定

- [ ] Twitch連携
  - [ ] チャット取得・送信
  - [ ] Gemini Flashによるコメントフィルタリング
  - [ ] コメント量に応じた動的閾値調整（少ない時は多く拾う）
- [ ] Gemini 2.5による会話システム実装
- [ ] MCP サーバー実装
  - [ ] 記憶管理（長期・短期メモリ）
  - [ ] 表情操作
  - [ ] 感情状態管理
- [ ] NitroGenとの統合
  - [ ] Game State Manager（ゲーム状態の取得・管理）
  - [ ] Action Executor（LLMの意図をゲーム操作に変換）
  - [ ] LLM ↔ NitroGen 双方向連携
- [ ] リアルタイム音声合成パイプライン
- [ ] OBS連携

## 参考プロジェクト

このプロジェクトは以下のオープンソースプロジェクトを基盤としています：

- [NitroGen](https://github.com/MineDojo/NitroGen) - Minecraft AI環境
- [Style-Bert-VITS2](https://github.com/litagin02/Style-Bert-VITS2) - 音声合成エンジン
- [Bert-VITS2](https://github.com/fishaudio/Bert-VITS2) - オリジナルTTSモデル
- [MineDojo](https://github.com/MineDojo/MineDojo) - Minecraft学習環境

## ライセンス

このリポジトリはGNU Affero General Public License v3.0でライセンスされています。詳細は[LICENSE](LICENSE)を参照してください。

音声合成部分（Style-Bert-VITS2）のライセンスについては、元のリポジトリの[利用規約](/docs/TERMS_OF_USE.md)も参照してください。
