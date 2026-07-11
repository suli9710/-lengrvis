from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = REPO_ROOT / "docs" / "industry-best-practices-audit-2026-07.md"


def _table_rows(text: str, start_heading: str, end_heading: str) -> list[list[str]]:
    section = text.split(start_heading, 1)[1].split(end_heading, 1)[0]
    rows: list[list[str]] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] == "领域" or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    return rows


def test_primary_maturity_matrix_keeps_every_control_domain_at_mid_high_or_better() -> None:
    text = AUDIT_PATH.read_text(encoding="utf-8")
    rows = _table_rows(text, "## 3. 当前成熟度矩阵", "## 4. 做得好的实践")

    assert len(rows) == 17
    assert all(row[1].startswith(("中上", "强")) for row in rows)
    assert "17/17 个控制领域均达到“中上”或更高" in text


def test_extended_maturity_matrix_has_no_below_mid_high_domain() -> None:
    text = AUDIT_PATH.read_text(encoding="utf-8")
    rows = _table_rows(text, "### 11.2 扩展成熟度矩阵", "### P0-6")

    assert len(rows) == 12
    assert all(row[4].startswith(("中上", "强")) for row in rows)
