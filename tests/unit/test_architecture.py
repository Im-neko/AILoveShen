"""クリーンアーキテクチャの依存の規則を守らせるテスト。"""

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ailoveshen"

# 各層が import してはいけない層（依存は内側にだけ向く）
FORBIDDEN_DEPENDENCIES = {
    "domain": {"application", "infrastructure", "presentation", "factories"},
    "application": {"infrastructure", "presentation", "factories"},
    "infrastructure": {"presentation", "factories"},
    "presentation": {"infrastructure", "factories"},
}


def _imported_layers(path: Path) -> set[str]:
    """モジュールが import している ailoveshen の最上位のサブパッケージを返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    layers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "ailoveshen" and len(parts) > 1:
                layers.add(parts[1])
    return layers


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_DEPENDENCIES))
def test_layer_dependencies_point_inward(layer):
    """どのモジュールも、許された向きの外の層から import しない。"""
    violations = []
    for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
        forbidden = _imported_layers(path) & FORBIDDEN_DEPENDENCIES[layer]
        if forbidden:
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {sorted(forbidden)}")

    assert not violations, "\n".join(violations)
