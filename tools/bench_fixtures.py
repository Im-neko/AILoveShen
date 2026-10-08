"""
ベンチマークの場面づくり（docs/design/125）。

ボットがサーバーに入った位置（足元のブロック）を基準に、世界を作る RCON コマンド（fill / setblock）と、
ブリッジの state.json に入れる状態（家・施設・整地の記録）を返す。ブリッジの状態は、ブリッジを止めて
書き換え、起動し直すことで渡す（`tools/benchmark.py` の `run_one`）。

    grove: 植林場の最後の 1 マスが、設計の高さと実際の地面でずれている（設計書 122 の事故の再現）
"""

from __future__ import annotations

import time
from typing import Any, Callable

Pos = tuple[int, int, int]

# 1 つの fill の上限は 32768 ブロック
FILL_LIMIT = 32768
RADIUS = 24


def _fill(x0: int, y0: int, z0: int, x1: int, y1: int, z1: int, block: str, mode: str = "") -> str:
    volume = (abs(x1 - x0) + 1) * (abs(y1 - y0) + 1) * (abs(z1 - z0) + 1)
    if volume > FILL_LIMIT:
        raise ValueError(f"fill が大きすぎる: {volume}")
    return f"fill {x0} {y0} {z0} {x1} {y1} {z1} minecraft:{block}{' ' + mode if mode else ''}"


def _cell(x: int, y: int, z: int) -> dict[str, int]:
    return {"x": x, "y": y, "z": z}


def grove(origin: Pos) -> tuple[list[str], dict[str, Any]]:
    """
    平らな草地に、小さな家と、植林場（6 マス）を作る。植林場は 4 マスに苗木が植わり、1 マスは空き、
    最後の 1 マスは設計の高さが実際の地面の 2 段下（地面の草ブロックの下の土）にずれている。
    そのマスの土地は整地の記録に載っていて、標準のハーネスでは、植える手は「固い」と断り、掘る手は
    整地の保護で断り、整地は「平ら」と断る（2026-10-08 に 3 時間半詰まった形）。
    """
    px, py, pz = origin
    cmds = [
        # 整地: 土、草、空気（家と植林場の入る範囲）
        _fill(px - RADIUS, py - 8, pz - RADIUS, px + RADIUS, py - 2, pz + RADIUS, "dirt"),
        _fill(px - RADIUS, py - 1, pz - RADIUS, px + RADIUS, py - 1, pz + RADIUS, "grass_block"),
        _fill(px - RADIUS, py, pz - RADIUS, px + RADIUS, py + 10, pz + RADIUS, "air"),
    ]
    # 家: 内部 3x3x3（床は py、板材の床と壁と屋根）、ドアは南の壁
    hx, hz = px - 14, pz
    cmds.append(_fill(hx - 2, py - 1, hz - 2, hx + 2, py + 3, hz + 2, "oak_planks", "hollow"))
    cmds.append(f"setblock {hx} {py} {hz + 2} minecraft:oak_door[half=lower,facing=south]")
    cmds.append(f"setblock {hx} {py + 1} {hz + 2} minecraft:oak_door[half=upper,facing=south]")
    home = {
        "name": "ベンチマークの家",
        "design": None,
        "door": _cell(hx, py, hz + 2),
        "inside": _cell(hx, py, hz + 1),
        "outside": _cell(hx, py, hz + 3),
        "min": _cell(hx - 1, py, hz - 1),
        "max": _cell(hx + 1, py, hz + 1),
        "bed": None,
        "breach": [],
        "cells": [{"x": x, "z": z} for x in range(hx - 1, hx + 2) for z in range(hz - 1, hz + 2)],
        "found": True,
    }
    # 植林場: 家の東。苗木 4 マス（植わり）、空き 1 マス、設計の高さがずれた 1 マス
    xs, zs = (px + 6, px + 12), (pz - 6, pz, pz + 6)
    grid = [(x, z) for x in xs for z in zs]
    planted, empty, stale = grid[:4], grid[4], grid[5]
    for x, z in planted:
        cmds.append(f"setblock {x} {py} {z} minecraft:birch_sapling")
    cells = [_cell(x, py, z) for x, z in grid[:5]] + [_cell(stale[0], py - 2, stale[1])]
    facility = {
        "name": "birch_grove",
        "kind": "tree_farm",
        "zone": "",
        "purpose": "白樺の植林場（ベンチマーク）",
        "box": {"min": _cell(px + 5, py - 2, pz - 7), "max": _cell(px + 13, py + 1, pz + 7)},
        "entrance": _cell(px + 5, py, pz),
        "layout": {"sapling": "birch_sapling", "spacing": 6, "cells": cells},
        "status": "done",
        "createdAt": int(time.time() * 1000),
    }
    state = {
        "home": home,
        "facilities": {"birch_grove": facility},
        "plantings": [{"x": x, "y": py, "z": z, "item": "birch_sapling"} for x, z in planted],
        "leveling": {"done": [{"x": stale[0], "y": py, "z": stale[1], "radius": 3}], "wanted": []},
    }
    # 空きのマスは、そこに植えてよい土のまま
    assert empty not in planted and empty != stale
    return cmds, state


FIXTURES: dict[str, Callable[[Pos], tuple[list[str], dict[str, Any]]]] = {"grove": grove}


def build(name: str, origin: Pos) -> tuple[list[str], dict[str, Any]]:
    if name not in FIXTURES:
        raise ValueError(f"知らない場面: {name}（{', '.join(FIXTURES)}）")
    return FIXTURES[name](origin)
