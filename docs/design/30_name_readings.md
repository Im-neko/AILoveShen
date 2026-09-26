# 視聴者の名前の読みを覚えて、辞書で引けるようにする

作成: 2026-09-26。

## 1. 目的

ユーザー（2026-09-26）: 「リスナーの名前の読み方も覚えておけるようにして、辞書で引けるようにしたい」

Twitch の名前は英字や漢字が多く、読み上げ（Style-Bert-VITS2）が読めない・読み違える。返答で名前を呼ぶたびに変な読みになる。

## 2. 形

- **辞書**: 名前 → 読み（ひらがな・カタカナ、30 字まで）と、誰が決めたか（`viewer`: 本人、`guess`: Gemini の推測）と日時。`data/readings.json`（`twitch.readings_path`、手で直してもよい）。名前の大文字小文字は区別しない
- **使う所は読み上げだけ**: `SpeakTextUseCase` が合成に渡すテキストだけ、名前を読みに置き換える（長い名前から。英数字の途中は置き換えない）。ログ、イベント、表示は元の名前のまま
- **覚え方**
  - 本人のコマンド `!yomi よみ`（`!読み` / `!よみ`）: 覚えて「〇〇さん、読み方覚えたよ！」と応える（返事の順番は待たない）。かなでなければ何もしない
  - 返答のとき（プレイ中の JSON の返答）: プロンプトのユーザー名に「（読み: …）」を出す。読みがなければ `name_reading` に推測を出させる（`guess`）。本人が読み方を言ったら（コメントに「読み」「よみ」「呼んで」）その読み（`viewer`）
  - 推測は本人の読みを上書きしない。本人の読みは何でも上書きする
- **引く**: 目標ボードの `GET /api/readings`（全部）、`?name=` で 1 人

## 3. 部品

- `application/ports/output/reading_store.py`: `NameReading`、`IReadingStore`
- `application/use_cases/readings.py`: `NameReadings`（get / all / learn / apply）、`valid_reading`、`tells_reading`、`COMMAND`
- `infrastructure/adapters/storage/json_reading_store.py`: `JsonReadingStore`
- つなぐ所: `GenerateResponseUseCase(readings=)`、`ChatResponder(readings=)`、`SpeakTextUseCase(pronounce=)`、`GoalBoard(readings=)`、`factories/stream.py`

## 4. やらないこと

- 読みの編集画面（JSON を手で直すか、本人がコマンドで直す）
- 実況の中の名前（実況は視聴者の名前をあまり言わない。読み上げの置き換えは実況にも効く）

## 5. 検証

- 単体テスト: かなだけを受ける、推測が本人の読みを上書きしない、置き換え（長い名前から、英数字の途中は除く）、JSON の保存と読み込み、合成には読み・イベントには名前、返答での推測と本人の読み、`!yomi`、`/api/readings`
- 実機: 英字の名前で返答したときの読み、`!yomi` の後の読み
