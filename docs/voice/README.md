# 声の録音の台本と、録音から声のモデルを作る流れ

作成: 2026-09-26。台本は `recording_script.tsv`（番号、区分、感情、文、メモ。書き下ろし 249 文）と `ita_corpus.tsv`（[ITA コーパス](https://github.com/mmorise/ita-corpus) 424 文、パブリックドメイン。ふりがな `ruby` と読み `kana` つき）。

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
| ITA EMOTION100 | EMOTION100_001–100 | 100 | neutral | 日本語の音を最低限まんべんなく（おすすめ） |
| ITA RECITATION324 | RECITATION324_001–324 | 324 | neutral | 音の組み合わせを増やす（任意） |

書き下ろしの 249 文でおよそ 30〜40 分、ITA EMOTION100 で 10 分ほど（言い直しを含めると合わせて 2 時間ほど）。一度に全部でなくてよい: ふつう → 語り → ITA EMOTION100 → 感情 → 実況・相づち の順に、何回かに分けて。RECITATION324 は時間があれば。

## 録音のページ（行ごとの録音ボタン）

```bash
python tools/voice_recorder.py            # → http://localhost:8770 をブラウザで開く
python tools/voice_recorder.py --out <保存先>   # 既定は data/voice_recordings/（git に入らない）
```

1 文ごとの「録音」ボタン（か Space）で録り、止めるとその行の番号のファイル名（`N001.wav`、`EMOTION100_001.wav`）で保存する。48kHz などマイクのまま、モノラル、16bit の WAV。ブラウザの自動音量・雑音除去・エコー除去は切る（学習データを加工しない）。押す直前の 0.2 秒も入れる（最初の音が切れない）。入力の大きさを上に表示し、音割れしそうな録音には印を付けて次へ進まない。録り直しは上書き（前のものは `_previous/` に 1 つ）。保存済みの行は聞き直し・消去ができる。マイクは localhost で開いたときだけ使える（Python の標準ライブラリだけで動くので、録る人のパソコンでもリポジトリと Python があれば動く）。

読むだけのページ（録音済みの印つき、スマホ向け）: https://claude.ai/artifact/UG76jN4QpA2ukaXvZZ1xX3 （このページからは録音できない: 閲覧側でマイクが使えない）

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
python tools/voice_dataset.py data/voice_recordings --dry-run

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

## Irodori-TTS が読んだ音声で Style-Bert-VITS2 を学習し直す

作成: 2026-10-02。配信の録音（`Data/shen/raw`、5063 本）から学習した重みは、元の録音の雑音（BGM、コメント読み上げの機械音声など）まで覚えていて、`style_vectors.npy` を作り直しても消えない（スタイルを変えても無音部分の雑音の量が同じだった）。台本をきれいな音声で読ませて、重みから学習し直す。

```bash
# 1. Irodori-TTS を起動しておく（scripts/irodori/start_mac.sh）。台本 673 文を、声 shen_sbv2 で、感情の説明なしで読ませる（1 文 6 秒ほど。途中から再開できる）
.venv/bin/python tools/irodori_from_sbv2.py generate --engine irodori --voice shen_sbv2 --name shen_irodori
# 2. 末尾のノイズを切る（下の注）。感情のフォルダーにも（shen_sbv2_neutral happy surprised sad angry scared）
.venv/bin/python tools/voice_trim_tail.py --name shen_irodori            # audio_trimmed/ に試し切り。耳で確かめて
.venv/bin/python tools/voice_trim_tail.py --name shen_irodori --apply    # audio/ を置き換える（元は audio_untrimmed/）
# 2b. 外れ値の確認（別の人の声に流れたものの目安。下の注）
.venv/bin/python tools/voice_screen.py --name shen_irodori
# 3. Style-Bert-VITS2 の学習データにする（Style-Bert-VITS2/Data/shen_clean/raw/<スタイル>/ と esd.list）
.venv/bin/python tools/voice_dataset.py data/irodori_train/shen_irodori/audio --model shen_clean --neutral-only \
  --extra Neutral=data/irodori_train/shen_sbv2_neutral/audio \
  --extra Happy=data/irodori_train/shen_sbv2_happy/audio --extra Surprised=data/irodori_train/shen_sbv2_surprised/audio \
  --extra Sad=data/irodori_train/shen_sbv2_sad/audio --extra Angry=data/irodori_train/shen_sbv2_angry/audio \
  --extra Fear=data/irodori_train/shen_sbv2_scared/audio
```

- **末尾のノイズ（2026-10-02）**: Irodori-TTS の音声は、発話が終わったあとに 0.3〜0.5 秒の低い雑音（-22〜-30dB、高域が多い）が付く（サーバーの `trim_tail` は潜在表現が平坦になった点しか切らない）。ほぼ全部のクリップに付く（音声の質の違いではなく Irodori-TTS の癖: 今の配信の Irodori-TTS にも出る）。そのまま学習すると Style-Bert-VITS2 が覚えるので、`voice_trim_tail.py` で切る（中央値 0.34 秒、最大 0.76 秒）。語尾の欠けは耳で確かめる（`--margin` を増やす）
- **外れ値の確認は目安**: `voice_screen.py` の類似度は話者だけでなく語り口（抑揚）も拾う。感情の文（H、S、A、D）と 1 秒台の相づち（R）が低く出るが、別の人の声とは限らない（2026-10-02: 657 本中 42 本が外れ、ほぼ全部これ）。機械では移さず、`screen.tsv` の低いものを耳で確かめ、だめなものだけ `audio_rejected/` に移す
- 主のフォルダーは全部ふつうの話し方で読ませたので Neutral（`--neutral-only`）。感情のスタイルは、感情の説明つきで読ませた既存のクリップ（`tools/irodori_from_sbv2.py emotions`）を `--extra` で足す: 学習のときに感情の幅も見せておかないと、あとから足したスタイルで声が崩れやすい（2026-09-28 に感情のスタイルの声が微妙で Neutral に戻した経緯）。感情の説明は声を参照から離すことがあるので、2 の外れの確認を感情のフォルダーにも
- 同じ番号のファイルは別のフォルダーに入っていても上書きしない（`<フォルダーの名前>_<番号>.wav` で写す）。足りなければ、別の seed で読ませた 2 回目（`--name shen_irodori_2`）を `--extra Neutral=…` で足せる
- **今動いている `shen` は上書きしない**: Colab では別の名前 `shen_clean` で学習する。ローカルの `Style-Bert-VITS2/model_assets/shen_clean/` に置き、`TTS_MODEL_NAME=shen_clean` で切り替える（TTS サーバーを再起動。戻すときは `shen`）

結果（2026-10-02）: 台本 673 文のうち 660 文を Irodori-TTS で読ませた（42.8 分。除いた 13 文は ITA の「ひぇーん」「あっあの。」のような短い特殊な文で、末尾の雑音で長くなり読みの速さの確かめにかかった）。耳で外した 3 本（R006、R007、R011）を除き、感情の既存クリップ 130 本と合わせて 783 本、45.9 分、259MB（Neutral 683、Happy / Surprised / Sad / Angry / Fear 各 20）。

### Colab で学習する

**「0. 環境構築」が失敗するとき（2026-10-02、Colab は Python 3.13）**: 上流のノートブック（2025-08 から更新なし）のままだと、`uv pip install` が `Failed to build pyopenjtalk-dict … CMake Error … Compatibility with CMake < 3.5 has been removed` で落ち、次の `initialize.py` が `No module named 'loguru'` で止まる（インストールが全部失敗しているため）。原因は 2 つ（Python 3.13 の Linux コンテナで再現、直しを確認）:

1. `pyopenjtalk-dict` は sdist だけで、CMake 4.x が古い `cmake_minimum_required` を拒否する → 環境変数 `CMAKE_POLICY_VERSION_MINIMUM=3.5`
2. `librosa==0.9.2`（前処理の再サンプリングで使う）が `pkg_resources` を要求するが、Python 3.13 では入っていないことがある → `setuptools<81`

`numpy<2` の固定（1.26.4）は 3.13 用の wheel が無いのでソースからビルドされるが、通る。「0. 環境構築」のコードセルを次に置き換える（`Style-Bert-VITS2/colab.ipynb` は上流の写しなので直していない）:

```python
import os

os.environ["PATH"] += ":/root/.cargo/bin"
os.environ["CMAKE_POLICY_VERSION_MINIMUM"] = "3.5"  # CMake 4 が pyopenjtalk-dict の古い CMakeLists を拒否する

!curl -LsSf https://astral.sh/uv/install.sh | sh
!git clone https://github.com/litagin02/Style-Bert-VITS2.git
%cd Style-Bert-VITS2/
!uv pip install --system -r requirements-colab.txt "setuptools<81" --no-progress  # librosa 0.9.2 が pkg_resources を使う
!python initialize.py --skip_default_models

exit()
```

実行後、学習の前に GPU が使えるか確かめる: `import torch; print(torch.cuda.is_available())` が `True` であること（`False` だと CPU で学習してしまう）。

**「3. 前処理」の Step 5（style_gen）が `AttributeError: module 'torchaudio' has no attribute 'AudioMetaData'` で落ちるとき（2026-10-02）**: Colab は新しい torch / torchaudio（2.9 以降）を持っていて、`pyannote.audio` 3.4.0（上流の要件が選ぶ版、スタイルの埋め込みに使う）が使う `torchaudio.AudioMetaData` が torchaudio 2.9 で削除された。直しても、その次に `huggingface_hub` 1 系以降が `hf_hub_download() got an unexpected keyword argument 'use_auth_token'` で落ちる（pyannote 3.4.0 が使う引数が削除された）。Python 3.13 の Linux コンテナで次の組み合わせでスタイルベクトル（256 次元）が取れることを確認した: `torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0`（CUDA は入っている torch に合わせて cu126 / cu128）、`huggingface_hub<1`、`transformers<5`。Step 1〜4（音声変換、テキスト、BERT）は Drive に残るので、前処理は最初からやり直さず `style_gen.py` だけを動かす:

```python
import torch
cuda = tuple(int(x) for x in torch.version.cuda.split(".")[:2])
tag = "cu128" if cuda >= (12, 8) else "cu126"   # torch 2.8.0 の CUDA ビルドは cu126 / cu128 / cu129
print(torch.__version__, torch.version.cuda, "->", tag)
!uv pip install --system --no-progress --index-url https://download.pytorch.org/whl/{tag} --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 "huggingface_hub<1" "transformers<5"
exit()
```

再起動したあと（「1. 初期設定」のセルを実行してから）。上流の `style_gen.py` には `torch.load` の `weights_only` を避けるパッチがなく（このリポジトリの `Style-Bert-VITS2/style_gen.py` は手元で足したもの）、torch 2.6 以降は pyannote のチェックポイントの読み込みが `Weights only load failed … TorchVersion` で落ちる。PyTorch の環境変数 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` で通る（コンテナで、なしは同じエラー、ありはスタイルベクトルが取れることを確認。pyannote の公式のチェックポイントなので信頼できる）。学習のセルでも同じ環境変数を使う:

```python
import os
os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
%cd /content/Style-Bert-VITS2
!python style_gen.py --config /content/drive/MyDrive/Style-Bert-VITS2/Data/shen_clean/config.json --num_processes 2
```

終わったら `import torch; print(torch.cuda.is_available())` が `True` であることを確かめて、「4. 学習」へ。

**「4. 学習」が `AttributeError: module 'ml_dtypes' has no attribute 'int2'`（TensorFlow の import）で落ちるとき（2026-10-02）**: `train_ms_jp_extra.py` の `from torch.utils.tensorboard import SummaryWriter` を TensorBoard が Colab 入りの TensorFlow で読もうとし、`numpy<2` の固定が選んだ `ml-dtypes 0.4.1`（Python 3.13 では `ml-dtypes` 0.5 以降が numpy 2.1 以降を要求する）が TensorFlow に古すぎて落ちる（`int2` や `float4_e2m1fn` がない。TensorFlow のバージョンで属性名が変わる）。SBV2 は TensorFlow を使わないので消す（コンテナで、あり: 同じ種類のエラー、削除後: SummaryWriter が書き込めることを確認）:

```python
!uv pip uninstall --system tensorflow jax jaxlib flax
```

同じ原因で、WavLM を読むところ（`WavLMLoss` → `transformers` の `image_transforms`）が Colab 入りの JAX を import して `ValueError: JAX requires ml_dtypes version 0.5 or newer; installed version is 0.4.1` で落ちる。JAX も要らないので、TensorFlow と一緒に消す（コンテナで、そのままは失敗、`USE_FLAX=0` を付けるか `jax jaxlib flax` を消すと `modeling_wavlm` が import できることを確認。`USE_JAX=0` は効かない）。これらは `numpy<2` の固定（Python 3.13 では `ml-dtypes` 0.4.1 になる）が、Colab 入りの numpy 2 系向けのライブラリ（TensorFlow、JAX など）と合わないために起きる。同種のエラーがまた出たら、その使わないライブラリを消す。

なお、「4. 学習」の最初のセル（`config.yml` を書くだけ）は何も表示しないのが正常。

**TTS サーバーが `Number of styles (7) does not match the number of style2id (6)` で落ち続けるとき（2026-10-03）**: Neutral の音声を `raw/Neutral/` のサブフォルダーに入れて学習すると、Style-Bert-VITS2 が自分で作る `Neutral`（全体の平均、0 行目）と同じ名前になり、`style_vectors.npy` は 7 行、`config.json` の `style2id` は 6 件になる（重みは無事: 学習が使うのは 1 本ごとの埋め込みだけ）。モデルのフォルダーに入れた時点で TTS サーバーが読み込みに失敗して再起動を繰り返し、**配信の声が止まる**。`tools/voice_dataset.py` は今は Neutral を `raw/` の直下に置く（正しい構成）。すでに学習したモデルは、スタイルの 2 ファイルだけ直す（フォルダーの名前が衝突した `Neutral` = 0 行目以外の行を `Neutral` として並べ直し、全体の平均の 0 行目を捨てる。`config.json.bak` / `style_vectors.npy.bak` に元を残す）:

```python
import json, numpy as np
c = json.load(open("config.json", encoding="utf-8")); v = np.load("style_vectors.npy")
old = c["data"]["style2id"]                      # {'Neutral': 4, 'Angry': 1, ...}: 7 行に対して 6 件
order = ["Neutral", "Angry", "Fear", "Happy", "Sad", "Surprised"]
np.save("style_vectors.npy", np.stack([v[old[n]] for n in order]))
c["data"]["style2id"] = {n: i for i, n in enumerate(order)}; c["data"]["num_styles"] = len(order)
json.dump(c, open("config.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
```

`model_assets/` に置いた新しいフォルダーは、TTS サーバーを再起動したときに全部読まれる。**置く前に `style2id` の件数と `style_vectors.npy` の行数が同じか確かめる**（合わないフォルダーが 1 つあるだけで、ほかのモデルも読めずサーバー全体が落ちる）。

`Style-Bert-VITS2/colab.ipynb`。Google Drive の `Style-Bert-VITS2/Data/shen_clean/`（`raw/` と `esd.list`）に、上のフォルダーを置く（Drive にある `Data/shen` と `model_assets/shen` は触らない）。

1. 0（環境構築）、1（初期設定）を実行。2（データ準備）は飛ばす: 書き起こしは台本の文のままで、Whisper は要らない
2. 3（前処理）: `model_name = "shen_clean"`、`use_jp_extra = True`、`batch_size = 4`、`save_every_steps = 500`、`normalize = True`（読ませた音声は 48kHz で、音量が文ごとに揺れる）、`trim = True`（前後の無音）、`yomi_error = "skip"`（読めずに飛ばした行数を見る）
3. 4（学習）: 公式の JP-Extra のベースから始まる（`Data/shen_clean/models/` が空のとき）。量が少なく声が 1 つ・雑音なしで過学習しやすいので、`epochs` は多くしすぎず、保存された `shen_clean_e*_s*.safetensors` を聞き比べて選ぶ（最後のものとは限らない）。同じ文を各世代で合成して比べる（Colab の最後のセルの `app.py --share` の推論タブ。ローカルでは世代ごとに `model_assets/shen_clean/` に 1 つずつ置いて TTS サーバーを再起動し、`python tools/tts_compare.py --engines style_bert_vits2`）
4. できた `model_assets/shen_clean/` をダウンロードして、ローカルの `Style-Bert-VITS2/model_assets/` に置く

学習のあと、Neutral 以外のスタイルを作り直したいときは `.venv/bin/python tools/sbv2_add_styles.py --model shen_clean`。

## 同意

声の本人から、この声を AI の配信者の声として使うこと（どの配信で、いつまで、声のモデルの扱い）の同意を、書面かメッセージで残しておく。
