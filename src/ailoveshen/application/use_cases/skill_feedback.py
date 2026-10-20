"""
技が失敗したときの案内（設計書 194）: 直すか、やり直すかを選べる材料を並べる。

前は、失敗のたびに「原因を探して、すぐ直せ」と言っていた。やり直せば通る失敗（敵が近くにいた、直前に状況が変わった）も
書き直しに向かい、逆に、直さずに同じ技を動かし続けても（2026-10-18: 止まった技を 10 分 x 2 回）止める材料がなかった。
ここでは、決めつけず、事実を並べる: この技の成績（何回中何回成功、最後の成功から何回前、同じ理由が何回続いたか）、
今回の失敗の種類（例外、自分で降りた、終わったが条件を満たさない、止められた）と、その種類ごとの目安。
"""

from __future__ import annotations

from dataclasses import dataclass

from ailoveshen.application.use_cases.same_cause import reason_key
from ailoveshen.domain.value_objects import SkillRun

# 時間の上限や待ちのない繰り返しで止められた理由（ブリッジの文）
_TIMEOUT = ("ran longer than", "kept running without waiting")
_DARK = ("dusk", "night")  # 敵が湧き続ける時間帯: 止められた技を同じ場所でやり直しても、また止められる
NEVER_SUCCEEDED_RUNS = 3  # この回数動かして一度も成功していなければ、直すほうへ倒す
WORKED_BEFORE_RATIO = 0.5  # 成功がこの割合以上なら、状況のせいかもしれないと言う


@dataclass
class _Track:
    runs: int = 0
    successes: int = 0
    since_success: int = 0  # 最後の成功から数えた実行の数（成功したことがなければ runs と同じ）
    same: int = 0  # 同じ理由で続けて失敗した回数
    last_key: str = ""


class SkillHistory:
    """技ごとの成績。保存した技も、保存しない試しも同じ名前で数える（直しながら試す流れを通して見る）。"""

    def __init__(self) -> None:
        self._tracks: dict[str, _Track] = {}

    def rewritten(self, name: str) -> None:
        """
        技のコードを書き直した。成績は新しいコードの分だけ数え直す（前のコードの 3 of 15 を引きずらない）。
        同じ理由で続けて失敗した回数は残す（書き直しても同じ理由で失敗しているのが、見せたい事実）。
        """
        track = self._tracks.get(name)
        if track:
            track.runs = track.successes = track.since_success = 0

    def feedback(self, name: str, run: SkillRun, trial: bool, phase: str = "day") -> str:
        """
        実行の結果を数え、失敗なら案内の文（先頭に " | "）を返す。成功と、動かさなかった断り（refused）は空。
        断りは実行ではないので数えない。`phase` は終わったときの時間帯（day / dusk / night / dawn）。
        """
        if run.ended == "refused":
            return ""
        track = self._tracks.setdefault(name, _Track())
        track.runs += 1
        if run.ok:
            track.successes += 1
            track.since_success = 0
            track.same = 0
            track.last_key = ""
            return ""
        track.since_success += 1
        key = reason_key(run.reason)
        track.same = track.same + 1 if key == track.last_key else 1
        track.last_key = key
        return _failure_text(name, run, track, trial, phase)


def _failure_text(name: str, run: SkillRun, track: _Track, trial: bool, phase: str) -> str:
    fix = (
        f"try_skill again with the same name {name} (what = the cause and the fix)"
        if trial
        else f"write_skill with the same name {name} (what = the cause and the fix)"
    )
    if run.ended == "error":
        kind, advice = "the code threw an exception (the reason above)", f"That is a bug in the code: fix it with {fix}."
    elif run.ended == "stopped" and any(t in run.reason for t in _TIMEOUT):
        kind = "it hit the time limit (it ran too long or looped without waiting)"
        advice = f"Fix it with {fix}: every wait needs a limit, and it must give up with t.fail."
    elif run.ended == "stopped" and phase in _DARK:
        kind = "it was stopped from outside (a reflex against an enemy, or the watcher)"
        advice = (
            f"That is not a bug in the code, but it is {phase}: enemies keep coming while you are outside, so running it "
            "again will be stopped again. Get out of danger first (shelter, dig in, or wait for the day), then run it."
        )
    elif run.ended == "stopped":
        kind = "it was stopped from outside (a reflex against an enemy, or the watcher)"
        advice = "That is not a bug in the code. Deal with the cause (an enemy nearby), then run it again as it is."
    elif run.ended == "fail":
        kind = "the skill gave up by itself (read its log and the scene)"
        advice = (
            "If they show the situation (an enemy, a missing item, no free cell), change the situation or the "
            f"arguments and run it again. If it gave up wrongly, fix the code with {fix}."
        )
    else:
        kind = "it finished, but the world did not match expects (see the expects line)"
        advice = f"If it did not do the job, fix the code with {fix}. If it did the job and expects says otherwise, fix expects."
    last = (
        f"last success {track.since_success} run{'s' if track.since_success != 1 else ''} ago"
        if track.successes
        else "no success yet"
    )
    text = f" | {name}: {track.successes} of {track.runs} runs succeeded ({last}). This failure: {kind}. {advice}"
    if track.same >= 2:
        text += (
            f" The same failure came {track.same} times in a row: running it again unchanged will not change the "
            "result. Change something."
        )
    elif track.successes == 0 and track.runs >= NEVER_SUCCEEDED_RUNS:
        text += f" It has never succeeded in {track.runs} runs: fix it before running it again."
    elif track.successes / track.runs >= WORKED_BEFORE_RATIO and track.same == 1:
        text += " It worked before, so this may be the situation: changing the situation and running it again is reasonable."
    return text
