"""
どの呼び出しにも入れる共通のシステム指示（設計書 175）。

「誰か・どこか・入力の約束・言語の約束」だけを、いつも同じ文で入れる（用途や状況で変えない）。
用途ごとの決まり（目標の決め方、返事の決まりなど）と性格・話し方は、システム指示でなく本文の先頭に置く
（`with_rules`）。システム指示が使われたり使われなかったり、状況で中身が違ったりしないように。
"""

from typing import Optional

COMMON_SYSTEM = """\
あなたは、Twitch で Minecraft（Java 1.21.4、サバイバル）を自分の手で遊びながら配信している AI の配信者です。
- 入力に書かれた座標・数・名前・時刻は、ブリッジ（ゲームの外から世界を測る仕組み）が測った事実です。推測で書き換えず、分からないことは分からないと言います。
- 声に出す文は日本語の話し言葉で書きます。識別子・道具名・座標・ブロックとアイテムの名前は原文のままにします。
- 指示された出力の形に従い、その外に文を足しません。
"""

# ゲームの文脈のいらない機械的な変換（読み上げ用のカタカナ変換、教訓の統合）には入れない
# （入れると、変換せずに配信者として答えかねない）
NO_COMMON_SYSTEM = frozenset({"reading", "lesson_compaction", "lesson_check"})


def with_common_system(system: Optional[str], purpose: Optional[str]) -> Optional[str]:
    """共通のシステム指示に、その呼び出し自身のシステム指示（機械的な変換の指示など）を続ける。"""
    if purpose in NO_COMMON_SYSTEM:
        return system
    return f"{COMMON_SYSTEM}\n{system}" if system else COMMON_SYSTEM.rstrip("\n")
