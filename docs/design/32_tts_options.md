# 音声モデルの選択肢（少量のサンプルから作る方法の調査）

作成: 2026-09-26。調査のメモ（まだ何も変えていない）。02（TTS）に関係する。

## 1. きっかけ

ユーザー（2026-09-26）: 「今は Style-Bert-VITS2 で作っているが、もっとよい方法はあるか。少量のサンプルデータから音声モデルを作る方法を調べて」

今の状態: Style-Bert-VITS2（JP-Extra）の `shen` モデル。スタイルは Neutral だけで、感情はアバターの表情で出している（WORKLOG 2026-09-24）。TTS サーバーは Docker の CPU 用設定。

## 2. 候補（2026-09 時点）

| 方式 | 要る音声 | 強み | 弱み・注意 |
|---|---|---|---|
| Style-Bert-VITS2 JP-Extra（今） | 数分〜数十分、学習あり | 日本語の読み・アクセントが最も安定（2026 年の比較記事でもローカルで最上位）。CPU でも速い | 感情はスタイルごとの学習データ頼み（今は Neutral だけ） |
| Irodori-TTS（Aratako、日本語向け、Flow Matching / RF-DiT） | 学習なし: 参照音声 約 30 秒で効果の大半（最大 120 秒）。LoRA の追加学習あり | 絵文字で感情・話し方・非言語音（笑いなど）を指定できる。OpenAI 互換の推論サーバー（Irodori-TTS-Server）。コードは MIT | 新しい（v4.1-Small、Large は予定）。重みのライセンスはモデルカードで要確認。GPU 向け（int8 量子化、4 ステップの蒸留あり） |
| Qwen3-TTS（Alibaba、0.6B / 1.7B） | 学習なし: 3〜15 秒（きれいな音声と正確な書き起こし）。1 人分の追加学習あり | Apache-2.0。自然文で感情・話し方を指示できる。最初の音まで 97 ms（ストリーミング） | 多言語モデル: 日本語のアクセントは日本語特化に劣るという声。GPU 向け |
| GPT-SoVITS（v2Pro / v4） | 学習なし 5 秒、または 1 分の音声で数分の学習 | MIT。少量データの定番。API サーバー（api_v2.py）。RTX 4060Ti で RTF 0.028 | 感情の指定は弱い（参照音声の雰囲気に従う）。CPU（M4）では RTF 0.5 程度。v3/v4 は学習データの音質に敏感 |
| AivisSpeech（Style-Bert-VITS2 と同じ方式） | 同じ | VOICEVOX 互換の API、ONNX で CPU でも軽い | 品質は同等（乗り換えても大きくはよくならない） |

## 3. この配信にとっての見どころ

- **感情**: `EmotionState` → スタイルの対応はあるが、声は Neutral しか出ない。Irodori-TTS（絵文字）か Qwen3-TTS（自然文）なら、感情を声に出せる見込みが一番大きい
- **速さと GPU**: 実況は短い文を次々に話す。Irodori-TTS / Qwen3-TTS / GPT-SoVITS は GPU で速い。Mac の CPU では遅すぎるかもしれない。どのマシンで動かすかで選ぶものが変わる
- **差し替え**: 合成は出力ポート `ISpeechSynthesizer` の後ろにある。別の方式はアダプター 1 つ（と設定での切り替え）で試せる。`SpeakTextUseCase` と配信側はそのまま。感情は `EmotionState` から各方式の指定（絵文字、指示文）に写す（今の `EmotionStyleService` と同じ位置）

## 4. 次にやるなら

1. 同じ声のサンプル（30 秒〜1 分、きれいな音声と書き起こし）で、Irodori-TTS と Qwen3-TTS の学習なしのクローンを作り、今の `shen` と聞き比べる（読み・アクセント、感情、1 文の合成時間）
2. よいほうのアダプターを足し（例: Irodori-TTS-Server の OpenAI 互換 API）、`tts.engine` のような設定で切り替える。Style-Bert-VITS2 は予備に残す

## 5. 限界

詳しい比較記事（Zenn の音響分析、Qiita の 10 選）とモデルカード（Hugging Face）は調査の環境から開けず、検索結果の要約と各 GitHub の README に基づく。実際の音は聞いていない。

## 出典

- ローカル日本語ボイスクローン 6 モデル比較（Zenn）: https://zenn.dev/fujinumagic/articles/local-japanese-tts-voice-clone
- 日本語TTSモデル徹底比較2026（Qiita）: https://qiita.com/0h-n0/items/8f78f7acd31000612d13
- ローカルTTSを5つ全部試した 2026年版（Qiita）: https://qiita.com/GeneLab_999/items/bc07147b589a93bf6114
- Irodori-TTS: https://github.com/Aratako/Irodori-TTS 、https://huggingface.co/Aratako/Irodori-TTS-500M-v3
- Qwen3-TTS: https://github.com/QwenLM/Qwen3-TTS 、技術報告 https://arxiv.org/html/2601.15621v1 、https://ocdevel.com/blog/20260302-qwen-tts-voice-cloning
- GPT-SoVITS: https://github.com/RVC-Boss/GPT-SoVITS 、https://pasqualepillitteri.it/en/news/7051/gpt-sovits-voice-cloning-guide
- Style-Bert-VITS2 と AivisSpeech の違い: https://bon-bon-tools.com/blog/style-bert-vits2/
- Best Local TTS Models 2026: https://localaimaster.com/blog/best-local-tts-models
