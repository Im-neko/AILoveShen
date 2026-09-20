# Docker セットアップガイド

AILoveShenのTTSサーバーをDockerで実行するためのガイドです。

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│                         Host PC                             │
│  ┌──────────────────┐     ┌────────────────────────────┐   │
│  │ AILoveShen Client│     │     Docker Container       │   │
│  │   (Python)       │◄────┤  Style-Bert-VITS2 Server   │   │
│  │                  │HTTP │  (server_fastapi.py)       │   │
│  │ - TTS Client     │5001 │  - /voice endpoint         │   │
│  │ - Audio Playback │     │  - /models/info            │   │
│  └────────┬─────────┘     └────────────────────────────┘   │
│           │                           ▲                     │
│           ▼                           │ Volume Mounts       │
│    ┌──────────────┐            ┌──────┴───────┐            │
│    │ Virtual Audio│            │ bert/        │            │
│    │   Device     │            │ model_assets/│            │
│    └──────┬───────┘            └──────────────┘            │
│           │                                                 │
│           ▼                                                 │
│    ┌──────────────┐                                        │
│    │     OBS      │                                        │
│    │ (Audio Input)│                                        │
│    └──────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

**設計理由:**
- Dockerコンテナからはホストのオーディオデバイスにアクセスできないため、TTSサーバーのみをDocker化
- AILoveShenクライアントはホストで実行し、仮想オーディオデバイス経由でOBSに音声を送信
- モデルファイルはボリュームマウントで外部から指定（イメージサイズ削減、モデル更新容易化）

## 必要要件

### ハードウェア
- CPU: 4コア以上推奨
- RAM: 8GB以上（Docker Desktopに4GB以上割り当て）
- ストレージ: 10GB以上の空き容量

### ソフトウェア
- Docker Desktop
  - Windows: WSL2バックエンド推奨
  - macOS: Apple Silicon / Intel両対応
- モデルファイル
  - `Style-Bert-VITS2/bert/deberta-v2-large-japanese-char-wwm/` (~500MB)
  - `Style-Bert-VITS2/model_assets/shen/` (~240MB)

## クイックスタート

### 1. 環境設定

```bash
cd docker
cp .env.example .env
```

### 2. Windowsの場合（追加設定）

```powershell
Copy-Item docker-compose.override.yml.windows docker-compose.override.yml
```

### 3. ビルド & 起動

```bash
docker compose up -d
```

### 4. 状態確認

```bash
# コンテナ状態確認（healthyになるまで80-120秒）
docker compose ps

# ログ監視
docker compose logs -f
```

### 5. 動作確認

```bash
# APIエンドポイント
curl http://localhost:5001/models/info

# 音声合成テスト（URLエンコード済み日本語）
curl "http://localhost:5001/voice?text=%E3%83%86%E3%82%B9%E3%83%88&model_id=0" -o test.wav
```

### 6. 停止

```bash
docker compose down
```

## 詳細設定

### 環境変数 (.env)

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `TTS_PORT` | 5001 | APIポート番号 |
| `BERT_PATH` | ../Style-Bert-VITS2/bert | BERTモデルのパス |
| `MODEL_ASSETS_PATH` | ../Style-Bert-VITS2/model_assets | TTSモデルのパス |

### TTS設定 (docker/config.yml)

```yaml
server:
  port: 5001        # APIポート
  device: "cpu"     # 推論デバイス（Docker内はCPU固定）
  language: "JP"    # デフォルト言語
  limit: 500        # テキスト最大長
  origins:
    - "*"           # CORS設定
```

### ボリュームマウント

```yaml
volumes:
  # BERTモデル（読み取り専用）
  - ../Style-Bert-VITS2/bert:/app/bert:ro
  # TTSモデル（読み取り専用）
  - ../Style-Bert-VITS2/model_assets:/app/model_assets:ro
  # 設定ファイル
  - ./config.yml:/app/config.yml:ro
```

## プラットフォーム別設定

### Windows (Docker Desktop + WSL2)

**パス形式:**
```yaml
# 方法1: 相対パス（推奨、WSL2で動作）
- ../Style-Bert-VITS2/bert:/app/bert:ro

# 方法2: Windowsパス
- C:/Users/USERNAME/apps/AILoveShen/Style-Bert-VITS2/bert:/app/bert:ro

# 方法3: WSLパス
- /mnt/c/Users/USERNAME/apps/AILoveShen/Style-Bert-VITS2/bert:/app/bert:ro
```

**Docker Desktop設定:**
1. Settings → Resources → WSL Integration
2. 使用するWSLディストリビューションを有効化
3. Settings → Resources → Memory: 4GB以上を割り当て

### macOS (Docker Desktop)

**パス形式:**
```yaml
# 相対パス
- ../Style-Bert-VITS2/bert:/app/bert:ro

# 絶対パス
- /Users/USERNAME/apps/AILoveShen/Style-Bert-VITS2/bert:/app/bert:ro
```

**Docker Desktop設定:**
1. Settings → Resources → Memory: 4GB以上を割り当て
2. Settings → Resources → File Sharing: プロジェクトディレクトリを追加

## トラブルシューティング

### コンテナが起動しない

```bash
docker compose logs
```

**よくある原因:**

| エラー | 原因 | 解決策 |
|--------|------|--------|
| `port already in use` | ポート5001が使用中 | `.env`で`TTS_PORT`を変更 |
| `out of memory` | メモリ不足 | Docker Desktopのメモリ制限を増加 |
| `volume mount failed` | パスが不正 | パス形式を確認、絶対パスを試す |

### ヘルスチェックが失敗する

BERTモデルの読み込みに80-120秒かかります。`start_period: 120s`を超えても起動しない場合:

```bash
# 詳細ログを確認
docker compose logs --tail=100

# コンテナ内でデバッグ
docker compose exec tts-server bash
```

### 音声合成が遅い

CPUモードでの推論は、GPU比で2-5倍遅くなります。

| テキスト長 | 目安時間 (CPU) |
|-----------|----------------|
| 10文字以下 | 0.5-1秒 |
| 50文字 | 1-2秒 |
| 100文字 | 2-4秒 |

### Windowsでボリュームマウントエラー

```
Error: path not found
```

1. Docker Desktop → Settings → Resources → File Sharing でパスを追加
2. WSL2の場合は `/mnt/c/...` 形式を試す
3. パスにスペースや日本語が含まれていないか確認

## 開発者向け情報

### イメージの再ビルド

```bash
# キャッシュなしで再ビルド
docker compose build --no-cache

# 特定のサービスのみ
docker compose build tts-server
```

### コンテナ内でのデバッグ

```bash
# シェルに入る
docker compose exec tts-server bash

# Pythonで確認
docker compose exec tts-server python -c "import torch; print(torch.__version__)"
```

### ログの確認

```bash
# リアルタイムログ
docker compose logs -f

# 最新100行
docker compose logs --tail=100

# 特定サービスのみ
docker compose logs tts-server
```

## ファイル構成

```
docker/
├── Dockerfile.tts-server              # TTSサーバーイメージ定義
├── docker-compose.yml                 # メインCompose設定
├── docker-compose.override.yml.windows # Windows用オーバーライド
├── config.yml                         # TTS設定（CPU用）
├── .env.example                       # 環境変数テンプレート
└── README.md                          # クイックリファレンス
```

## 関連ドキュメント

- [OBS音声ルーティング設定](./obs-audio-routing.md)
- [AILoveShen設定リファレンス](../configuration.md)
