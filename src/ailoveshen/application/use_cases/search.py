"""
検索: 一覧から、検索語に合うキーを Jev が選ぶ（docs/design/47 §3）。

プレイする LLM（ローカル、考えない）はプロンプトの知識や決まりを読み落とすが、道具は崩さずに呼ぶ。
そこで施設・覚えた場所・アイテム・やり方を検索の道具で引かせる。一覧（キーと短い説明）はコードが
作り、多ければ語の重なりで絞り、どれが検索語に合うかは Jev が選ぶ（ユーザーの指定）。選ばれたキーの
中身（コードが書いたもの）を LLM に返す。Jev が答えない・迷うときは、語の重なりの上位を「確かでは
ない候補」として返す（何も返さないより、LLM が選べる）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ailoveshen.application.ports.output.fast_judge import IFastJudge
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.application.use_cases.tool_catalog import SEARCH_FACILITY, SEARCH_MEMORY
from ailoveshen.domain.exceptions import AILoveShenError, GameBridgeError
from ailoveshen.domain.value_objects import FastQuestion, FastQuestionKind

SEARCH_CHOICES = 16  # Jev に見せる候補の数（多ければ語の重なりで絞る）
FALLBACK_SHOWN = 3  # Jev が答えないときに返す候補の数
NONE = "none"
_WORD = re.compile(r"[a-z0-9]+")
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]+")


@dataclass(frozen=True)
class SearchEntry:
    """検索の一覧の 1 件: キー、Jev に見せる短い説明、選ばれたら返す中身。"""

    key: str
    summary: str
    detail: str


@dataclass(frozen=True)
class SearchResult:
    """検索の答え: LLM に返す文、返したキー、決め方（jev / none / fallback / empty）。"""

    text: str
    keys: tuple[str, ...]
    by: str
    confidence: float = 0.0


def _terms(text: str) -> set[str]:
    """語の集まり: 英数字の語（_ で区切る）と、日本語の文字の 2-gram。"""
    lower = text.lower().replace("_", " ")
    terms = set(_WORD.findall(lower))
    for run in _JAPANESE.findall(lower):
        terms |= {run[i : i + 2] for i in range(len(run) - 1)} or {run}
    return terms


def rank(query: str, entries: Sequence[SearchEntry]) -> list[SearchEntry]:
    """語の重なりが多い順（同じなら一覧の順）。検索語の語が 1 つもない一覧でも、全部を返す。"""
    q = _terms(query)
    scored = [(len(q & _terms(f"{e.key} {e.summary}")), i, e) for i, e in enumerate(entries)]
    return [e for _, _, e in sorted(scored, key=lambda t: (-t[0], t[1]))]


class KeySearch:
    """一覧から、検索語に合うキーを Jev に選ばせる。"""

    def __init__(
        self,
        judge: IFastJudge | None,
        recorder: IWatchRecorder | None = None,
        timeout_seconds: float = 3.0,
        min_confidence: float = 0.5,
        choices: int = SEARCH_CHOICES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            judge: Jev（None ならいつも語の重なりの上位を返す）
            recorder: 検索の記録（logs/search）
            timeout_seconds: Jev の答えを待つ上限
            min_confidence: Jev の選んだキーを使う確信度の下限
            choices: Jev に見せる候補の数
            clock: 時計（テストで差し替える）
        """
        self._judge = judge
        self._recorder = recorder
        self._timeout = timeout_seconds
        self._min = min_confidence
        self._choices = choices
        self._clock = clock

    async def find(
        self, tool: str, query: str, entries: Sequence[SearchEntry], what: str
    ) -> SearchResult:
        """
        検索語 `query` に合う 1 件を選ぶ。`what` は一覧が何か（例: facility）。答えの文を返す。
        """
        started = self._clock()
        if not entries:
            return self._done(
                tool,
                query,
                [],
                None,
                0.0,
                started,
                SearchResult(f"no {what} is known yet", (), "empty"),
            )
        shown = rank(query, entries)[: self._choices]
        picked, confidence = await self._ask(query, shown, what)
        if picked is not None and picked != NONE:
            entry = shown[ord(picked) - ord("A")]
            result = SearchResult(entry.detail, (entry.key,), "jev", confidence)
        elif picked == NONE:
            near = ", ".join(e.key for e in shown[:FALLBACK_SHOWN])
            result = SearchResult(
                f"no {what} matches {query!r} (the nearest by name: {near})", (), "none", confidence
            )
        else:
            top = shown[:FALLBACK_SHOWN]
            text = f"not sure which {what} matches {query!r}; the closest by words:\n" + "\n".join(
                f"- {e.detail}" for e in top
            )
            result = SearchResult(text, tuple(e.key for e in top), "fallback", confidence)
        return self._done(tool, query, shown, picked, confidence, started, result)

    async def _ask(
        self, query: str, shown: list[SearchEntry], what: str
    ) -> tuple[str | None, float]:
        """Jev の答え（A, B, … か none）と確信度。答えない・迷うときは (None, 確信度)。"""
        if self._judge is None:
            return None, 0.0
        labels = [chr(ord("A") + i) for i in range(len(shown))]
        criteria = {label: f"{e.key}: {e.summary}" for label, e in zip(labels, shown)}
        criteria[NONE] = f"none of these {what} entries matches the query"
        question = FastQuestion(
            name="match",
            kind=FastQuestionKind.CHOICE,
            instructions=(
                f"The streamer searches the {what} list with `query` (Japanese or English; a name, "
                "a purpose or what they want, e.g. 'where do I get wheat' → a wheat farm). Which "
                "entry matches best? Choose none only when no entry fits."
            ),
            criteria=criteria,
        )
        try:
            verdict = await asyncio.wait_for(
                self._judge.ask({"query": query}, (question,)), timeout=self._timeout
            )
        except (AILoveShenError, TimeoutError) as e:
            logger.debug(f"検索: Jev が答えなかった（{type(e).__name__}）")
            return None, 0.0
        answer = verdict.answers.get("match")
        if answer is None:
            return None, 0.0
        value = str(answer.value)
        if value not in criteria or answer.confidence < self._min:
            return None, float(answer.confidence)
        return value, float(answer.confidence)

    def _done(
        self,
        tool: str,
        query: str,
        shown: Sequence[SearchEntry],
        picked: str | None,
        confidence: float,
        started: float,
        result: SearchResult,
    ) -> SearchResult:
        logger.info(f"検索 {tool}({query!r}): {result.by} {list(result.keys)}（{confidence:.2f}）")
        if self._recorder is not None:
            entry: dict[str, Any] = {
                "tool": tool,
                "query": query,
                "shown": [e.key for e in shown],
                "jev": picked,
                "confidence": confidence,
                "by": result.by,
                "keys": list(result.keys),
                "seconds": round(self._clock() - started, 3),
            }
            try:
                self._recorder.record(entry)
            except Exception as e:  # noqa: BLE001 - 記録の失敗で検索を止めない
                logger.warning(f"検索を記録できなかった: {e}")
        return result


TOP_ITEMS = 6  # 一覧の説明に書くチェストの中身の数


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _where(p: dict[str, Any]) -> str:
    return f"{p.get('distance_m', '?')}m {p.get('direction', '?')}"


def _items(counts: Any) -> str:
    """{item: n} を多い順に（Jev に見せる説明）。"""
    if not isinstance(counts, dict):
        return ""
    top = sorted(counts.items(), key=lambda kv: -(kv[1] if isinstance(kv[1], (int, float)) else 0))
    return ", ".join(f"{k} {v}" for k, v in top[:TOP_ITEMS])


def facility_entries(facilities: Sequence[dict[str, Any]]) -> list[SearchEntry]:
    """施設の一覧（GET /facilities）を検索の一覧にする。中身は施設の JSON そのまま。"""
    out = []
    for f in facilities:
        contents = f.get("contents") or {}
        what = ""
        if isinstance(contents.get("crops"), dict):
            what = (
                "crops: " + ", ".join(contents["crops"]) if contents["crops"] else "crops: none now"
            )
        elif "saplings" in contents:
            what = "saplings and trees (logs)"
        elif "total" in contents:
            what = f"{contents.get('placed')}/{contents.get('total')} blocks placed"
        summary = "; ".join(
            s
            for s in (
                f"{f.get('kind')} ({f.get('status')})",
                str(f.get("purpose") or ""),
                what,
                _where(f),
            )
            if s
        )
        out.append(SearchEntry(key=str(f.get("name")), summary=summary, detail=_json(f)))
    return out


def memory_entries(memory: dict[str, Any]) -> list[SearchEntry]:
    """覚えた場所（GET /memory）を検索の一覧にする。キーは「種類@x,y,z」。"""
    out = []

    def at(p: dict[str, Any]) -> str:
        return f"{p.get('x')},{p.get('y')},{p.get('z')}"

    for p in memory.get("places", []):
        gives = f"; gives {', '.join(p['gives'])}" if p.get("gives") else ""
        seen = f"seen {p.get('minutes_ago')} min ago"
        summary = f"{p.get('kind')} x{p.get('count')}{gives}; {_where(p)}, {seen}"
        out.append(SearchEntry(f"{p.get('kind')}@{at(p)}", summary, _json(p)))
    for c in memory.get("chests", []):
        summary = f"chest with {_items(c.get('contents')) or 'nothing'}; {_where(c)}"
        out.append(SearchEntry(f"chest@{at(c)}", summary, _json(c)))
    for f in memory.get("furnaces", []):
        summary = f"furnace making {_items(f.get('making'))}; {_where(f)}"
        out.append(SearchEntry(f"furnace@{at(f)}", summary, _json(f)))
    for d in memory.get("deaths", []):
        summary = (
            f"where I died (items may be dropped); {_where(d)}, {d.get('minutes_ago')} min ago"
        )
        out.append(SearchEntry(f"death@{at(d)}", summary, _json(d)))
    return out


class WorldSearch:
    """
    検索の道具（search_facility、search_memory）を実行する。一覧はブリッジから読み、どれが
    検索語に合うかは KeySearch（Jev）が選ぶ（docs/design/47 §4）。
    """

    def __init__(self, bridge: IMinecraftBridge, search: KeySearch) -> None:
        """
        Args:
            bridge: 一覧を読むブリッジ
            search: 一覧から選ぶもの
        """
        self._bridge = bridge
        self._search = search

    async def run(self, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
        """(ok, 結果の文)。一覧を読めないときは ok が偽（空の一覧は偽ではない: まだないと返す）。"""
        query = str(args.get("query") or "").strip()
        if not query:
            return False, "invalid call: query is empty"
        try:
            if tool == SEARCH_FACILITY:
                entries, what = facility_entries(await self._bridge.facilities()), "facility"
            elif tool == SEARCH_MEMORY:
                entries, what = memory_entries(await self._bridge.memory()), "remembered place"
            else:
                return False, f"invalid call: unknown search {tool!r}"
        except GameBridgeError as e:
            return False, f"could not read the list: {e}"
        result = await self._search.find(tool, query, entries, what)
        return True, result.text
