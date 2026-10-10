"""
外国語の文を、日本語の声が読めるカタカナ読みにする（docs/design/152）。

声のモデル（Style-Bert-VITS2 の JP-Extra、Irodori-TTS）は日本語専用で、英字や外国語の文字をそのまま
渡すと、一文字ずつ読んだり漢字の日本語読みで読んだりする（`Hello, thank you for watching!` は
カタカナの 2 倍以上の長さになった）。外国語の部分だけを、聞こえるとおりのカタカナにして渡す。
画面の字幕は元の文のまま。日本語なまりの発音になるが、一生懸命の外国語として読める。
変換できなかったときは、元の文のまま読む（黙るよりは読む）。
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict

from loguru import logger

from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.domain.exceptions import AILoveShenError

PURPOSE = "reading"
CACHE_SIZE = 64

# ラテン文字の単語（英語、フランス語、スペイン語、ドイツ語、ベトナム語など。アクセント付きの文字を含む）
LATIN_WORD = re.compile(r"[A-Za-zÀ-ɏḀ-ỿ]{2,}")
# 日本語の声が読めない文字種（ハングル、キリル、ギリシャ、ヘブライ、アラビア、タイ、デーバナーガリー、グルジアなど）
OTHER_SCRIPT = re.compile(
    "[Ͱ-ϿЀ-ԯ԰-֏֐-׿؀-ۿऀ-ॿ"
    "฀-๿Ⴀ-ჿᄀ-ᇿ㄰-㆏가-힯]"
)
KANA = re.compile("[぀-ゟ゠-ヿ]")
HANZI_RUN = re.compile("[一-鿿]{3,}")
CLAUSE = re.compile(r"[。、！？!?,.;:；：…\s「」『』（）()]+")

SYSTEM = (
    "You rewrite text for a Japanese text-to-speech voice that can only read Japanese. Replace every "
    "part that is written in another language (English, Chinese, Korean, French, Spanish, German, "
    "Russian, Thai, Arabic, ...) with katakana that sounds like how it is pronounced, the way a "
    "Japanese speaker would say it aloud, trying hard to be close to the original sound. Keep all "
    "Japanese text exactly as it is. Keep the punctuation and the meaning. Write names and numbers in "
    "foreign parts in katakana too. Output only the rewritten text, nothing else.\n\n"
    "Examples:\n"
    "Hello, thank you for watching!\n→ ハロー、サンキュー フォー ウォッチング！\n"
    "你好，谢谢你来看我直播。\n→ ニーハオ、シエシエ ニー ライ カン ウォー ジーボー。\n"
    "안녕하세요, 시청해 주셔서 감사합니다.\n→ アンニョンハセヨ、シチョンヘ ジュショソ カムサハムニダ。\n"
    "Bonjour, merci d'être venu.\n→ ボンジュール、メルシー デートル ヴニュ。\n"
    "¡Hola! Gracias por venir.\n→ オラ！ グラシアス ポル ベニール。\n"
    "今日はいい天気だね、good morning!\n→ 今日はいい天気だね、グッドモーニング！\n"
)


def needs_katakana(text: str) -> bool:
    """日本語の声がそのままでは読めない部分（英字の単語、日本語以外の文字、仮名のない漢字の並び）があるか。"""
    if LATIN_WORD.search(text) or OTHER_SCRIPT.search(text):
        return True
    # 中国語: 仮名を含まない句に、漢字が 3 つ以上続く（日本語の文は仮名が混ざる）
    return any(HANZI_RUN.search(clause) and not KANA.search(clause) for clause in CLAUSE.split(text))


class ForeignSpeech:
    """外国語の部分をカタカナ読みにして、日本語の声に渡す。"""

    def __init__(
        self, text_generator: ITextGenerator, timeout_seconds: float = 8.0
    ) -> None:
        """
        Args:
            text_generator: カタカナ読みを書かせる生成器（用途 "reading"）
            timeout_seconds: 待つ上限（過ぎたら元の文のまま読む）
        """
        self._text_generator = text_generator
        self._timeout = timeout_seconds
        self._cache: OrderedDict[str, str] = OrderedDict()

    async def pronounce(self, text: str) -> str:
        """読み上げに渡す文。外国語がなければ、そのまま。"""
        if not needs_katakana(text):
            return text
        if text in self._cache:
            self._cache.move_to_end(text)
            return self._cache[text]
        try:
            written = await asyncio.wait_for(
                self._text_generator.generate(text, system_instruction=SYSTEM, purpose=PURPOSE),
                timeout=self._timeout,
            )
        except (AILoveShenError, TimeoutError) as e:
            logger.warning(f"[tts] 外国語のカタカナ読みを作れないので、そのまま読む（{type(e).__name__}）: {text}")
            return text
        written = written.strip()
        if not written or needs_katakana(written):
            # 空、または外国語が残っている: 変換が効いていない
            logger.warning(f"[tts] 外国語のカタカナ読みが外国語のまま: {text} → {written}")
            return written or text
        logger.info(f"[tts] 外国語をカタカナ読みにする: {text} → {written}")
        self._cache[text] = written
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return written
