"""
手引き（文章のスキル。設計書 192）: 「ブランチマイニングのやり方」のような、よく使う知識や手順の文。

技（JavaScript。設計書 22）が動かす手順なのに対し、手引きは LLM が読む文。`guides/<名前>/SKILL.md`（Claude の SKILL.md と同じ形:
frontmatter の name と description、本文）に置く。プロンプトには名前と一行の説明の一覧だけを載せ、本文は道具 `read_guide` で読ませる
（本文を載せないので、プロンプトは小さく保つ）。
"""

from __future__ import annotations

from typing import Sequence

from ailoveshen.domain.value_objects import Guide


class GuideBook:
    """手引きの一覧と、名前で本文を読む。"""

    def __init__(self, guides: Sequence[Guide] = ()) -> None:
        self._guides = {g.name: g for g in guides}

    @property
    def guides(self) -> tuple[Guide, ...]:
        return tuple(self._guides.values())

    def read(self, name: str) -> tuple[bool, str]:
        """(ok, 本文か断りの理由)。"""
        guide = self._guides.get(name.strip())
        if guide is None:
            known = ", ".join(self._guides) or "none yet"
            return False, f"invalid call: there is no guide named {name!r} (guides: {known})"
        return True, guide.body


def reference_of(guides: Sequence[Guide]) -> str:
    """手引きの本文を、確かな知識として LLM に見せる 1 つの文にする（なければ空）。"""
    return "\n\n".join(f"### {g.name}\n{g.body}" for g in guides)


PLANNING_HEADING = "## 計画のための要点"


def planning_notes(guides: Sequence[Guide]) -> str:
    """
    手引きの「計画のための要点」の節（次の `## ` まで）を、手引きの名前つきで 1 つの文にする（なければ空）。
    中目標を立てる計画のプロンプトの固定の部分に入れる（手引きを直したときだけ変わるので、キャッシュに乗る）。
    """
    out: list[str] = []
    for guide in guides:
        lines: list[str] = []
        inside = False
        for line in guide.body.splitlines():
            if line.startswith(PLANNING_HEADING):
                inside = True
            elif line.startswith("## "):
                inside = False
            elif inside and line.strip():
                lines.append(line)
        if lines:
            out.append(f"（手引き {guide.name} より）\n" + "\n".join(lines))
    return "\n".join(out)
