"""配信のチャットに書き込む出力ポート。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IChatSink(ABC):
    """
    配信のチャット（Twitch など）に 1 行書き込む出力ポート。

    書き込めない（トークンがない、ログインに失敗した、つながっていない）ときは例外を出さずに
    偽を返す。呼ぶ側は、声で言うなどほかの手段に切り替える。
    """

    @property
    @abstractmethod
    def can_post(self) -> bool:
        """今書き込めるか。"""
        ...

    @abstractmethod
    async def post(self, text: str) -> bool:
        """1 行書き込む。書き込めたら真。"""
        ...
