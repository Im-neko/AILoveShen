"""
返答が何をしたか（決まった行動）と、返答の文が合っているか（docs/design/159）。

返答の文は、行動（受けた・断った・いつやるか）を決める生成と同じ 1 回で書かれていた。文を先に書き、
行動を後から決めて、さらにコードが直すので、「朝になったら置くね」と言いながら今やる、「今やるね」と
言いながら夜明けまで待つ、という食い違いが出た。行動が決まってから、その結果（`ReplyOutcome`）を
事実として文を書き直させ、文が結果と合っているかを確かめる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 受けたときの、いつやるか
NOW = "now"  # 今の小目標を区切って今やる
LATER = "later"  # 後でやる（夜が明けてから、今の作業のあと）
ALREADY = "already"  # もう今していること
MET = "met"  # 頼まれた条件が、もう満たされている（新しくやることがない）

# 後でやると言う言い回し / 今すぐやると言う言い回し
LATER_WORDS = re.compile(
    r"朝になっ|朝に(?:なれば|なる)|夜が明け|夜明け|あとで|後で|あとから|後から|終わったら|できたら|"
    r"済んだら|(?:終わっ|済ん|でき|明け)てから|のあと|の後|そのあと|その後|次に|そのうち|落ち着いたら|ひと段落|明日|あした|"
    r"あとにする|後にする|順番"
)
NOW_WORDS = re.compile(r"今すぐ|すぐに|すぐ置|すぐ作|さっそく|早速|今から|今やる|いまやる|いますぐ|今置く|今作る")
# 断ったり何もしなかったりしたのに、やると言う言い回し（Jev の約束の確かめの前の、すぐ分かるもの）
PROMISE_WORDS = re.compile(r"(?:やる|する|置く|作る|建てる|探す|集める|取る|行く|持ってくる)(?:ね|よ|から|ので|予定)")

DECLINE_PHRASES = {
    "cannot": "ゲームの中で、できたかを確かめられる形に書けない",
    "wrong": "今の様子と合わない（事実が違う）",
    "unsafe": "危ない",
    "off_mission": "今の目標から外れすぎている",
    "limit": "頼みの上限でもう入らない",
}


@dataclass(frozen=True)
class ReplyOutcome:
    """
    返答で決まったこと。`handling`: accepted（大目標・中目標にした）/ declined / withdrawn /
    noted（教訓を覚えた・考え直す。大目標にはしない）/ none（何もしない）。
    `timing`: accepted のとき、コードが決めた実際の時期。`when_note`: その言い方（日本語）。
    """

    handling: str
    title: str = ""
    what: str = ""  # 完了条件
    level: str = ""  # objective / job
    timing: str = ""
    when_note: str = ""
    decline_reason: str = ""
    detail: str = ""  # 断った・考え直す理由の、返答を決めた生成の言葉
    lesson: str = ""
    rethink: str = ""

    def facts(self) -> str:
        """返答を書き直させるときの事実（箇条書き）。"""
        lines: list[str] = []
        if self.handling == "accepted":
            kind = "中目標" if self.level == "job" else "大目標"
            lines.append(f"頼みを受けた: {kind}「{self.title}」（完了条件: {self.what}）")
            if self.timing == NOW:
                lines.append("いつ: 今すぐやる（今の小目標を区切って、これを先にやる）")
            elif self.timing == ALREADY:
                lines.append("いつ: もう今していること（そのまま続ける）")
            elif self.timing == MET:
                lines.append(f"いつ: 新しくやることはない。この条件は、今もう満たしている。{self.when_note}")
            else:
                lines.append(f"いつ: 今すぐではない。{self.when_note}")
        elif self.handling == "declined":
            reason = DECLINE_PHRASES.get(self.decline_reason, self.decline_reason)
            lines.append(f"断った。理由: {reason}")
            if self.detail:
                lines.append(f"理由の詳しい説明: {self.detail}")
            lines.append("やるとは言わない。理由を事実のとおり言う（ここに書いていない理由を作らない）")
        elif self.handling == "withdrawn":
            lines.append("取り下げを受けて、その人の頼みをリストから外した")
        elif self.handling == "noted":
            if self.lesson:
                lines.append(f"助言を教訓として覚えた: {self.lesson}")
            if self.rethink:
                lines.append(f"今の小目標を次の切れ目で考え直す: {self.rethink}")
            lines.append("大目標・中目標にはしていない。今すぐ何かをするとは言わない")
        else:
            lines.append("何も登録していない。やると約束しない（覚えておく・今していることを話すだけ）")
        return "\n".join(f"- {line}" for line in lines)

    def mismatch(self, text: str) -> str:
        """文が結果と合わないとき、書き直させる理由（英語。合っていれば空文字列）。"""
        if self.handling == "accepted":
            if self.timing == MET:
                found = NOW_WORDS.search(text)
                if found:
                    return (
                        f"The reply says {found.group(0)!r} (do it now), but it is already the case "
                        "and there is nothing to do: say that it is already so."
                    )
                return ""
            if self.timing in (NOW, ALREADY):
                found = LATER_WORDS.search(text)
                if found:
                    return (
                        f"The reply says {found.group(0)!r} (later), but it is done right now: "
                        "say it is done now, and do not mention waiting or a later time for it."
                    )
            else:
                found = NOW_WORDS.search(text)
                if found:
                    return (
                        f"The reply says {found.group(0)!r} (right now), but it is not done now: "
                        f"{self.when_note} Say that, and do not say it is done right away."
                    )
            return ""
        found = PROMISE_WORDS.search(text)
        if found and self.handling in ("declined", "withdrawn", "none"):
            return (
                f"The reply says {found.group(0)!r}, but nothing was registered to do (see the facts): "
                "do not promise an action."
            )
        return ""

    def fallback(self, user_name: str) -> str:
        """文が結果と合わないまま直らないときに話す、結果だけから作った一言。"""
        who = f"{user_name}さん、" if user_name else ""
        if self.handling == "accepted":
            if self.timing == NOW:
                return f"{who}「{self.title}」、今やるね！"
            if self.timing == ALREADY:
                return f"{who}「{self.title}」、ちょうど今やってるところだよ！"
            if self.timing == MET:
                return f"{who}「{self.title}」は、もうできてるよ！"
            return f"{who}「{self.title}」は{self.when_note.rstrip('。')}。"
        if self.handling == "declined":
            reason = DECLINE_PHRASES.get(self.decline_reason, "今はできない")
            return f"{who}ごめんね、{reason}から、今回はやらないでおくね。"
        if self.handling == "withdrawn":
            return f"{who}取り下げたね、リストから外したよ！"
        if self.handling == "noted":
            return f"{who}覚えておくね！"
        return f"{who}ありがとう！"
