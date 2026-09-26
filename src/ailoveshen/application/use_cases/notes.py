"""自分のメモの管理: 読み込み、寿命で消し、目標の決定の編集を反映し、保存する。"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Optional

from loguru import logger

from ailoveshen.application.ports.output.note_store import INoteStore
from ailoveshen.application.use_cases.goal_vocabulary import NoteChange, NoteOp
from ailoveshen.domain.entities import Notebook
from ailoveshen.domain.value_objects import NoteKind


class NoteKeeper:
    """
    メモ帳を変える唯一の経路（docs/design/18_notes.md）。

    ふだん書くのは小目標の切れ目の目標の決定。チャットの返答は、視聴者のアドバイスに納得した
    ときだけ、自分の言葉の教訓を 1 つ書ける（learn_from_viewer。視聴者の文はそのまま入れない）。
    根拠はプロンプトで示したものに限る: lesson は示した小目標（コメントの教訓はその視聴者）、
    viewer は示した会話にいる視聴者。編集は写しの上で試してから反映する。
    """

    def __init__(self, store: INoteStore) -> None:
        """
        依存を受け取って初期化する（依存性の注入）。

        Args:
            store: 再起動をまたいでメモを保つ
        """
        self._store = store

    def load(self, notebook: Notebook, day: Optional[int]) -> None:
        """
        保存したメモを戻す。今日より後の日に書いたメモがあれば、ワールドが作り直された
        ので、何も戻さない（そのメモは別の世界のこと）。
        """
        saved = self._store.load()
        if saved is None:
            return
        if day is not None and any(n.written_day > day for n in saved.notes):
            logger.info(f"メモは今日（{day} 日目）より後の日のもの: ワールドが変わったので捨てる")
            self._store.save(notebook)
            return
        notebook.restore(list(saved.notes), saved.next_id)

    def learn_from_viewer(
        self, notebook: Notebook, lesson: str, viewer: str, day: Optional[int]
    ) -> Optional[str]:
        """
        視聴者のアドバイスに配信者が納得したとき、教訓として書く（返答が自分の言葉で書いた 1 文。
        視聴者の文をそのまま入れない）。同じ教訓があれば寿命を延ばすだけ。いっぱいなら一番早く
        消えるメモを消す。書いた（延ばした）メモの id、書けなければ None。
        """
        text = lesson.strip()
        if not text or day is None:
            return None
        for note in notebook.notes:
            if note.kind == NoteKind.LESSON and _same(note.text, text):
                notebook.keep(note.id, day)
                self._store.save(notebook)
                logger.info(f"教訓をまた教わった（延ばした）: {note.id} {note.text}")
                return note.id
        try:
            _drop_for_room(notebook)
            note = notebook.add(
                NoteKind.LESSON, text, day, f"{VIEWER_LESSON_PREFIX}（{viewer}さん）"
            )
        except ValueError as e:
            logger.warning(f"コメントの教訓を書けなかった: {text}: {e}")
            return None
        self._store.save(notebook)
        logger.info(f"コメントの教訓を書いた: {note.id} {note.text}（{viewer}さん）")
        return note.id

    def expire(self, notebook: Notebook, day: Optional[int]) -> None:
        """寿命が過ぎたメモを消す（まだ時刻を知らなければ何もしない）。"""
        if day is None:
            return
        expired = notebook.expire(day)
        if expired:
            logger.info(f"メモの寿命が過ぎた: {', '.join(f'{n.id} {n.text}' for n in expired)}")
            self._store.save(notebook)

    def commit(
        self,
        notebook: Notebook,
        changes: Sequence[NoteChange],
        day: Optional[int],
        goals: Sequence[str],
        viewers: Sequence[str],
    ) -> None:
        """
        目標の決定のメモの編集を反映し、保存する。

        Args:
            notebook: 配信者のメモ帳
            changes: 編集（順に反映する）
            day: 今日（何日目か）
            goals: プロンプトで番号をつけて示した小目標（lesson の根拠）
            viewers: プロンプトで示した会話にいる視聴者

        Raises:
            ValueError: 編集が上限を破る、示していない根拠を使う、または今日がわからない
                とき（そのときは何も反映しない）
        """
        if not changes:
            return
        if day is None:
            raise ValueError("the day is not known yet, so notes cannot be written")
        _apply(copy.deepcopy(notebook), changes, day, goals, viewers)
        for line in _apply(notebook, changes, day, goals, viewers):
            logger.info(line)
        self._store.save(notebook)


VIEWER_LESSON_PREFIX = "コメントで教わった"


def _same(a: str, b: str) -> bool:
    return a.replace(" ", "").replace("。", "") == b.replace(" ", "").replace("。", "")


def _drop_for_room(notebook: Notebook) -> None:
    """いっぱいなら、一番早く消えるメモ（視聴者についてのメモ以外）を消して場所を空ける。"""
    if len(notebook.notes) < notebook.max_notes:
        return
    candidates = [n for n in notebook.notes if n.kind != NoteKind.VIEWER] or list(notebook.notes)
    oldest = min(candidates, key=lambda n: n.expires_day)
    notebook.drop(oldest.id)
    logger.info(f"メモがいっぱいなので消した: {oldest.id} {oldest.text}")


def _apply(
    notebook: Notebook,
    changes: Sequence[NoteChange],
    day: int,
    goals: Sequence[str],
    viewers: Sequence[str],
) -> list[str]:
    lines = []
    for change in changes:
        if change.op == NoteOp.DROP:
            note = notebook.drop(change.note_id)
            lines.append(f"メモを消した: {note.id} {note.text}")
        elif change.op == NoteOp.KEEP:
            note = notebook.keep(change.note_id, day)
            lines.append(f"メモを残した: {note.id} {note.text}（{note.expires_day} 日目まで）")
        else:
            assert change.kind is not None
            note = notebook.add(change.kind, change.text, day, _about(change, goals, viewers))
            about = f"（{note.about}）" if note.about else ""
            lines.append(f"メモを書いた: {note.id} [{note.kind.value}] {note.text}{about}")
    return lines


def _about(change: NoteChange, goals: Sequence[str], viewers: Sequence[str]) -> str:
    if change.kind == NoteKind.LESSON:
        if change.goal is None or not 1 <= change.goal <= len(goals):
            raise ValueError(f"a lesson needs the number of a small goal shown (1-{len(goals)})")
        return goals[change.goal - 1]
    if change.kind == NoteKind.VIEWER:
        if change.viewer not in viewers:
            raise ValueError(f"{change.viewer or 'no viewer'} is not in the conversation shown")
        return change.viewer
    return ""
