# 声の録音の台本と、録音から声のモデルを作る流れ

作成: 2026-09-26。台本は `recording_script.tsv`（番号、区分、感情、文、メモ）。249 文。

## 台本の中身

| 区分 | 番号 | 数 | 感情 | 何のため |
|---|---|---|---|---|
| ふつう | N001–N060 | 60 | neutral | 声の土台。いろいろな音（ぴょ、ティ、ファ、ヴ、っ、ー）を含む |
| 楽しい | H001–H025 | 25 | happy | Style-Bert-VITS2 のスタイル Happy |
| 驚き | S001–S025 | 25 | surprised | スタイル Surprised |
| 悲しい | D001–D025 | 25 | sad | スタイル Sad |
| むっ | A001–A020 | 20 | angry | スタイル Angry（本気の怒りではなく、むっとした軽い怒り） |
| 怖い | F001–F020 | 20 | scared | スタイル Fear |
| 実況 | G001–G040 | 40 | neutral | アイテム名、数字、座標、英字（TNT、YouTube）、数え方 |
| 相づち | R001–R030 | 30 | neutral | 短い反応、笑い |
| 語り | L001–L004 | 4 | neutral | 1 つ 15 秒前後。Irodori-TTS の参照音声（学習には使わない） |

全部で録音はおよそ 30〜40 分（言い直しを含めると 1〜2 時間）。一度に全部でなくてよい: ふつう → 語り → 感情 → 実況・相づち の順に、何回かに分けて。

## 録り方（読む人へ）

- 1 文ずつ別のファイルに、ファイル名は番号（`N001.wav`）。言い直したら上書き
- 静かで響かない部屋（布団やカーテンの多い部屋）。同じマイク、口からの距離も毎回同じに
- 44.1kHz か 48kHz、WAV。大きな声の文（驚き、怒り）でも音が割れない音量に
- 文の前後に 0.3 秒ほどの無音。息を吸う音、紙をめくる音は入れない
- 感情の文は、配信で言うくらいの自然な大きさで。やりすぎなくてよい
- 読み方がわからないとき、書いてある字のとおりでなくても自然な言い方でよい（その場合は番号をメモしておく: 台本の文を直す）
- 数字は漢数字で書いてある（「六十四個」）。ふだんの読み方で（ろくじゅうよんこ）

## 録音のあと

```bash
# 足りない録音を確かめる
python tools/voice_dataset.py <録音のフォルダー> --dry-run

# Style-Bert-VITS2 の学習データにする（Style-Bert-VITS2/Data/shen/raw/<スタイル>/ と esd.list）
python tools/voice_dataset.py <録音のフォルダー> --model shen

# Irodori-TTS の参照音声（語り 4 つ）
python tools/irodori_voice.py <録音のフォルダー>/L001.wav <録音のフォルダー>/L002.wav \
  <録音のフォルダー>/L003.wav <録音のフォルダー>/L004.wav
```

Style-Bert-VITS2 は WebUI の学習の手順で、スライスと文字起こしを飛ばして（台本の文をそのまま使う）前処理から。2.5.0 以降は `raw/` のサブフォルダーごとにスタイルができる（Neutral / Happy / Surprised / Sad / Angry / Fear）。

学習が終わったら `config/development.yaml` で感情とスタイルを対応させる（今はすべて Neutral）:

```yaml
tts:
  emotion_style_map:
    neutral: "Neutral"
    happy: "Happy"
    excited: "Happy"
    surprised: "Surprised"
    sad: "Sad"
    angry: "Angry"
    scared: "Fear"
```

聞き比べは `python tools/tts_compare.py`（`docs/setup/irodori_tts.md`）。台本の G 区分と同じような文なので、本人の録音とも比べられる。

## 同意

声の本人から、この声を AI の配信者の声として使うこと（どの配信で、いつまで、声のモデルの扱い）の同意を、書面かメッセージで残しておく。
