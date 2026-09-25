"""ブリッジの地図のデータを、マス目のラベルつきの PNG にする（Pillow）。"""

from __future__ import annotations

import io
from typing import Any, Optional

from loguru import logger

from ailoveshen.application.ports.output.map_renderer import IMapRenderer
from ailoveshen.application.use_cases.builds import CELL, column_label, row_label
from ailoveshen.domain.value_objects import Screenshot

PX = 8  # 1 ブロックの画素
MARGIN = 26  # ラベルの余白
COLORS = {
    "ground": (106, 170, 84),
    "stone": (140, 140, 140),
    "sand": (222, 208, 150),
    "water": (64, 110, 220),
    "lava": (230, 90, 20),
    "leaves": (40, 110, 40),
    "tree": (110, 75, 40),
    "built": (205, 160, 100),
    "snow": (240, 240, 245),
    "other": (120, 110, 100),
}
UNLOADED = (30, 30, 30)
Color = tuple[int, int, int]
HOME = (220, 40, 60)
BUILD = (250, 150, 20)


class PilMapRenderer(IMapRenderer):
    """
    真上から見た地図（docs/design/25_builds.md §3）。北が上、東が右。

    - 地面の種類で色分けし、高いほど明るく、低いほど暗くする（家の床と同じ高さが基準）
    - 4x4 ブロックのマス目に、列 A.. と行 1.. のラベル
    - 家（赤い枠と HOME、ドアの点）と、登録した建物（橙の枠と名前）
    """

    def render(self, map_data: dict[str, Any]) -> Optional[Screenshot]:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("Pillow がないので地図を描けない（pip install -r requirements.txt）")
            return None
        cells = map_data.get("cells") or []
        if not cells:
            return None
        size = len(cells)
        radius = int(map_data["radius"])
        cx, cz = map_data["center"]["x"], map_data["center"]["z"]
        x0, z0 = cx - radius, cz - radius
        side = size * PX
        image = Image.new("RGB", (side + 2 * MARGIN, side + 2 * MARGIN), (250, 250, 250))
        draw = ImageDraw.Draw(image)
        font = _font(ImageFont, 14)
        small = _font(ImageFont, 11)

        for z, row in enumerate(cells):
            for x, cell in enumerate(row):
                if cell is None:
                    color = UNLOADED
                else:
                    color = _shade(COLORS.get(cell[0], COLORS["other"]), cell[1])
                left, top = MARGIN + x * PX, MARGIN + z * PX
                draw.rectangle([left, top, left + PX - 1, top + PX - 1], fill=color)

        def box(bmin: dict[str, int], bmax: dict[str, int], color: Color, label: str) -> None:
            left = MARGIN + (bmin["x"] - x0) * PX
            top = MARGIN + (bmin["z"] - z0) * PX
            right = MARGIN + (bmax["x"] - x0 + 1) * PX - 1
            bottom = MARGIN + (bmax["z"] - z0 + 1) * PX - 1
            draw.rectangle([left, top, right, bottom], outline=color, width=3)
            # 名前は枠の上に、白い下地で（枠の中に書くと色が重なって読めない）
            tl = draw.textbbox((left, top - 14), label, font=small)
            draw.rectangle([tl[0] - 2, tl[1] - 1, tl[2] + 2, tl[3] + 1], fill=(255, 255, 255))
            draw.text((left, top - 14), label, fill=color, font=small)

        home = map_data.get("home")
        if home:
            box(home["min"], home["max"], HOME, "HOME")
            door = home["door"]
            dx, dz = MARGIN + (door["x"] - x0) * PX, MARGIN + (door["z"] - z0) * PX
            draw.rectangle([dx + 1, dz + 1, dx + PX - 2, dz + PX - 2], fill=(255, 255, 255))
        for b in map_data.get("builds") or []:
            box(b["min"], b["max"], BUILD, b["name"])

        cells_per_side = size // CELL
        for i in range(cells_per_side + 1):
            at = MARGIN + i * CELL * PX
            draw.line([(at, MARGIN), (at, MARGIN + side)], fill=(0, 0, 0), width=1)
            draw.line([(MARGIN, at), (MARGIN + side, at)], fill=(0, 0, 0), width=1)
        for i in range(cells_per_side):
            mid = MARGIN + i * CELL * PX + CELL * PX // 2
            for y in (4, MARGIN + side + 6):
                draw.text((mid - 4, y), column_label(i), fill=(0, 0, 0), font=font)
            for x in (3, MARGIN + side + 4):
                draw.text((x, mid - 7), row_label(i), fill=(0, 0, 0), font=font)
        # 方角（フォントに矢印がないことがあるので文字だけ）
        draw.rectangle(
            [MARGIN + side - 34, MARGIN + 2, MARGIN + side - 3, MARGIN + 18], fill=(255, 255, 255)
        )
        draw.text((MARGIN + side - 32, MARGIN + 3), "N up", fill=(0, 0, 0), font=small)

        out = io.BytesIO()
        image.save(out, format="PNG")
        return Screenshot(data=out.getvalue(), mime_type="image/png", width=image.width)


def _shade(color: tuple[int, int, int], dy: int) -> tuple[int, int, int]:
    factor = max(0.45, min(1.45, 1 + dy * 0.07))
    return tuple(max(0, min(255, int(c * factor))) for c in color)  # type: ignore[return-value]


def _font(module: Any, size: int) -> Any:
    try:
        return module.load_default(size=size)
    except TypeError:
        return module.load_default()
