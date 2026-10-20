"""
自分用のメモ（設計書 196）: 判断なしの実験（設計書 191）で、LLM が自分で書いて、毎回のプロンプトに載る短い文。

直近の道具は数件で消え、技は動かす手順しか覚えない。死んだ理由や「この場所では木が遠い」といった学びは、書き残さない限り
次の選択には残らない。手引き（設計書 192）が人の書いた文なのに対し、メモは LLM 自身が失敗から書く文。
"""

from __future__ import annotations

MAX_NOTES = 12
MAX_NOTE_CHARS = 240


class NoteBook:
    """
    メモの一覧。いっぱいになったら足せず、どれを書き直すか（replace）を選ばせる。

    前は古いものから黙って消していた。すると、計画や教訓（昼に木を集める、など）が、新しい「死亡 N 回目の様子」に
    押し出された（2026-10-19 の実行 10: 36 回書いて、書き直しは 0 回）。残す・捨てるを選ぶのは書く側にする。
    """

    def __init__(self) -> None:
        self._notes: list[str] = []

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(self._notes)

    def write(self, text: str, replace: int | None = None) -> tuple[bool, str]:
        """(ok, 結果の文)。`replace` は書き直す番号（1 から）。"""
        text = " ".join(str(text or "").split())
        if not text:
            return False, "invalid call: text is empty"
        if len(text) > MAX_NOTE_CHARS:
            return False, f"invalid call: a note is at most {MAX_NOTE_CHARS} characters (yours is {len(text)}): make it shorter"
        if replace is not None:
            if not 1 <= replace <= len(self._notes):
                return False, f"invalid call: there is no note {replace} (notes: {len(self._notes)})"
            self._notes[replace - 1] = text
            return True, f"note {replace} rewritten"
        if text in self._notes:
            return True, "already noted"
        if len(self._notes) >= MAX_NOTES:
            return False, (
                f"invalid call: the notebook is full ({MAX_NOTES}). Merge or rewrite the least useful note with "
                "replace=<its number> (the current state is in the prompt already: keep only what outlives a death)"
            )
        self._notes.append(text)
        return True, f"noted as {len(self._notes)}"
