# AILoveShen Docker Setup

Style-Bert-VITS2 TTSサーバーをDockerで実行するための設定です。

## 必要要件

- Docker Desktop (WSL2バックエンド推奨)
- 8GB以上のRAM
- BERTモデルとTTSモデル (Style-Bert-VITS2/bert/, model_assets/)

## クイックスタート

### 1. 環境設定

```bash
cd docker
cp .env.example .env
```

### 2. Windows用設定 (Windowsの場合のみ)

```powershell
Copy-Item docker-compose.override.yml.windows docker-compose.override.yml
```

必要に応じて `docker-compose.override.yml` のパスを編集してください。

### 3. 起動

```bash
docker compose up -d
```

BERTモデルの読み込みに約80-120秒かかります。

### 4. 状態確認

```bash
docker compose ps      # STATUS: healthy を確認
docker compose logs -f # ログを監視
```

### 5. 動作確認

```bash
# APIエンドポイント確認
curl http://localhost:5001/models/info

# 音声合成テスト
curl "http://localhost:5001/voice?text=test&model_id=0" -o test.wav
```

### 6. 停止

```bash
docker compose down
```

## OBS音声ルーティング

### Windows (VB-Audio Cable)

1. **VB-Audio Cableをインストール**
   - https://vb-audio.com/Cable/ からダウンロード
   - インストール後、再起動

2. **AILoveShenの設定**
   ```yaml
   # config/default.yaml
   tts:
     audio:
       device: "CABLE Input"  # VB-Cableの入力デバイス
   ```

3. **OBSの設定**
   - ソース → 音声入力キャプチャ → 追加
   - デバイス: "CABLE Output (VB-Audio Virtual Cable)"

### macOS (BlackHole)

1. **BlackHoleをインストール**
   ```bash
   brew install blackhole-2ch
   ```

2. **AILoveShenの設定**
   ```yaml
   tts:
     audio:
       device: "BlackHole 2ch"
   ```

3. **OBSの設定**
   - ソース → 音声入力キャプチャ → 追加
   - デバイス: "BlackHole 2ch"

## トラブルシューティング

### コンテナが起動しない

```bash
docker compose logs
```

よくある原因:
- ポート5001が使用中 → `.env`でTTS_PORTを変更
- メモリ不足 → Docker Desktopのメモリ制限を増加

### 音声合成が遅い

CPUモードで動作しているため、GPU比で2-5倍遅くなります。
1リクエストあたり1-3秒が目安です。

### ボリュームマウントエラー (Windows)

WSL2の場合、パスは以下の形式を使用:
- 相対パス: `../Style-Bert-VITS2/bert`
- 絶対パス: `/mnt/c/Users/.../bert` または `C:/Users/.../bert`

## ファイル構成

```
docker/
├── Dockerfile.tts-server          # TTSサーバーイメージ
├── docker-compose.yml             # メイン設定
├── docker-compose.override.yml.windows  # Windows用オーバーライド
├── config.yml                     # TTS設定 (CPU用)
├── .env.example                   # 環境変数テンプレート
└── README.md                      # このファイル
```

## 設定オプション

### .env

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| TTS_PORT | 5001 | APIポート |
| BERT_PATH | ../Style-Bert-VITS2/bert | BERTモデルパス |
| MODEL_ASSETS_PATH | ../Style-Bert-VITS2/model_assets | TTSモデルパス |

### config.yml

主要な設定:
- `server.port`: APIポート (5001)
- `server.device`: cpu (固定)
- `server.limit`: テキスト最大長 (500文字)
