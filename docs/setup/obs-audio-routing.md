# OBS 音声ルーティング設定

AILoveShenのTTS音声をOBSのマイク入力として取り込むための設定ガイドです。

## 概要

```
AILoveShen TTS → 仮想オーディオデバイス → OBS (音声入力キャプチャ)
```

仮想オーディオデバイスを使用して、アプリケーションの音声出力をOBSのマイク入力として認識させます。

## Windows: VB-Audio Cable

### インストール

1. [VB-Audio Cable](https://vb-audio.com/Cable/) からダウンロード
2. `VBCABLE_Setup_x64.exe` を**管理者として実行**
3. インストール完了後、**PCを再起動**

### デバイス確認

再起動後、以下のデバイスが追加されます：
- **CABLE Input (VB-Audio Virtual Cable)** - 入力側（アプリがここに出力）
- **CABLE Output (VB-Audio Virtual Cable)** - 出力側（OBSがここから取得）

### AILoveShen設定

`config/default.yaml` を編集：

```yaml
tts:
  audio:
    device: "CABLE Input (VB-Audio Virtual Cable)"
    blocksize: 1024
```

または、デバイスIDで指定：

```python
# デバイス一覧を確認
import sounddevice as sd
print(sd.query_devices())
```

```yaml
tts:
  audio:
    device: 5  # デバイスID（環境により異なる）
```

### OBS設定

1. **ソース** パネルで「+」をクリック
2. **音声入力キャプチャ** を選択
3. 新規作成で名前を付ける（例: "AILoveShen TTS"）
4. デバイス: **CABLE Output (VB-Audio Virtual Cable)** を選択
5. **OK** をクリック

### 動作確認

1. AILoveShenでTTSを実行
2. OBSの音声ミキサーで「AILoveShen TTS」のレベルメーターが動くことを確認
3. 録画/配信で音声が入っていることを確認

## macOS: BlackHole

### インストール

```bash
brew install blackhole-2ch
```

または [BlackHole公式サイト](https://existential.audio/blackhole/) からダウンロード

### AILoveShen設定

`config/default.yaml` を編集：

```yaml
tts:
  audio:
    device: "BlackHole 2ch"
    blocksize: 1024
```

### OBS設定

1. **ソース** → **+** → **音声入力キャプチャ**
2. デバイス: **BlackHole 2ch** を選択
3. **OK** をクリック

### 自分でも音声を聞きたい場合

BlackHoleは音声をOBSに送るだけで、自分では聞こえません。
同時に聞きたい場合は「複数出力装置」を作成します：

1. **Audio MIDI設定** を開く（Spotlight で検索）
2. 左下の「+」→「複数出力装置を作成」
3. チェックを入れる：
   - ✅ MacBook Proのスピーカー（または使用中のスピーカー）
   - ✅ BlackHole 2ch
4. この複数出力装置をシステムの出力デバイスに設定

```yaml
tts:
  audio:
    device: "複数出力装置"  # 作成した装置名
```

## 高度な設定

### 音量調整

OBSの音声ミキサーでゲインを調整できます：
1. 音声入力キャプチャの歯車アイコンをクリック
2. **フィルタ** を選択
3. **+** → **ゲイン** を追加
4. 音量を調整（-30dB 〜 +30dB）

### ノイズ除去

TTS音声にノイズが乗る場合：
1. フィルタに **ノイズ抑制** を追加
2. 方式: **RNNoise** を選択（軽量で効果的）

### 遅延調整

音声と映像のズレを修正：
1. 音声入力キャプチャのプロパティ
2. **同期オフセット** を調整（ミリ秒単位）

## トラブルシューティング

### 音声が出ない

1. **デバイス名を確認**
   ```python
   import sounddevice as sd
   print(sd.query_devices())
   ```

2. **デバイスが正しく選択されているか確認**
   - AILoveShenのconfig.yamlの`device`設定
   - OBSの音声入力キャプチャのデバイス設定

3. **仮想オーディオデバイスが正常か確認**
   - Windows: サウンド設定で「CABLE Input」が表示されるか
   - macOS: Audio MIDI設定で「BlackHole 2ch」が表示されるか

### 音声が途切れる

1. **blocksize を増加**
   ```yaml
   tts:
     audio:
       blocksize: 2048  # デフォルト1024から増加
   ```

2. **バッファサイズを調整**（Windows）
   - VB-Audio Cableのコントロールパネルでバッファを増加

### 音声が歪む

1. **サンプルレートを確認**
   - TTS出力: 44100Hz
   - 仮想デバイス: 44100Hzに設定されているか確認

2. **ビット深度を確認**
   - 16bit推奨

## 参考リンク

- [VB-Audio Cable 公式](https://vb-audio.com/Cable/)
- [BlackHole GitHub](https://github.com/ExistentialAudio/BlackHole)
- [OBS Studio 公式ドキュメント](https://obsproject.com/wiki/)
