"""
配信者が自分について話したこと（docs/design/40_small_talk.md）。

雑談で自分のこと（好きなもの、使っているキャラ、得意・苦手、経験）を聞かれたら、キャラクターらしく
その場で決めてよい。決めたことは返答の self_facts で覚え、次からの返答のプロンプトに出して守らせる
（前と違うことを言わない）。話題（topic）ごとに 1 つで、同じ話題は新しいもので置き換える。
ワールドを作り直しても残す（配信者の人となりはワールドのものではない）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from loguru import logger

from ailoveshen.application.ports.output.self_fact_store import ISelfFactStore, SelfFact

MAX_FACTS = 120  # 超えたら、いちばん長く話していないものから忘れる
MAX_TOPIC = 30
MAX_FACT = 120


def _key(topic: str) -> str:
    return "".join(topic.split()).casefold()


class SelfFacts:
    """話題 → 自分について話したこと。変わるたびに保存する。"""

    def __init__(
        self,
        store: ISelfFactStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_facts: int = MAX_FACTS,
    ) -> None:
        """
        Args:
            store: 保存先
            clock: 時計（テストで差し替える）
            max_facts: 覚えておく数
        """
        self._store = store
        self._clock = clock
        self._max = max_facts
        self._facts: dict[str, SelfFact] = {_key(f.topic): f for f in store.load()}

    def all(self) -> tuple[SelfFact, ...]:
        """全部（話した順: 古いものから）。"""
        return tuple(sorted(self._facts.values(), key=lambda f: f.updated_at))

    def remember(self, items: object, said_to: str) -> tuple[SelfFact, ...]:
        """
        返答の self_facts（[{topic, fact}]）を覚える。覚えた（置き換えた）ものを返す。
        形の合わないもの・長すぎるものは覚えない。
        """
        if not isinstance(items, list):
            return ()
        learned = []
        for item in items:
            if not isinstance(item, dict):
                continue
            topic = str(item.get("topic", "")).strip()
            fact = str(item.get("fact", "")).strip()
            if not topic or not fact or len(topic) > MAX_TOPIC or len(fact) > MAX_FACT:
                logger.info(f"自分の話として覚えられない: {item!r}")
                continue
            old = self._facts.get(_key(topic))
            if old is not None and old.fact == fact:
                continue
            new = SelfFact(
                topic=topic, fact=fact, said_to=said_to, updated_at=self._clock().isoformat()
            )
            self._facts[_key(topic)] = new
            learned.append(new)
            if old is None:
                logger.info(f"自分の話を覚えた: {topic}: {fact}（{said_to}さんに）")
            else:
                logger.info(f"自分の話を変えた: {topic}: {old.fact} → {fact}（{said_to}さんに）")
        if not learned:
            return ()
        for dropped in self.all()[: max(0, len(self._facts) - self._max)]:
            del self._facts[_key(dropped.topic)]
            logger.info(f"自分の話を忘れた（多すぎる）: {dropped.topic}: {dropped.fact}")
        self._store.save(self.all())
        return tuple(learned)
