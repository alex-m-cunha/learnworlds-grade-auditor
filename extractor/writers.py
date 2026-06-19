"""Output writers: raw JSON + CSV.

CSV rule: arrays/objects are serialized as JSON strings (so nested structures
like answerData/downloads are preserved, never flattened away). Scalars and
null pass through unchanged. None -> empty cell.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


def _cell(value):
    """Serialize one cell value for CSV.

    - dict / list  -> compact JSON string (UTF-8 preserved)
    - None         -> "" (empty cell)
    - everything else -> str(value)
    """
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def save_raw_response(
    raw, out_dir: Path, timestamp: str, prefix: str = "raw_response"
) -> Path:
    """Persist the full raw payload (all pages) before any transformation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{prefix}_{timestamp}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(raw, handle, ensure_ascii=False, indent=2)
    return path


def write_csv(
    rows: list[dict],
    columns: list[str],
    out_dir: Path,
    timestamp: str,
    filename_stem: str = "submissions_export",
) -> Path:
    """Write rows to <filename_stem>_<timestamp>.csv (UTF-8 BOM for Excel)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{filename_stem}_{timestamp}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(row.get(col)) for col in columns])
    return path


def write_report(report: dict, out_dir: Path, timestamp: str) -> Path:
    """Write the extraction report JSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"extraction_report_{timestamp}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return path


# Excel cell hard limit; CSV stays the loss-less canonical output.
_XLSX_MAX = 32767


def _xlsx_cell(value, illegal_re):
    """Like _cell, but Excel-safe: strip illegal control chars, cap length."""
    out = _cell(value)
    if isinstance(out, str):
        out = illegal_re.sub("", out)
        if len(out) > _XLSX_MAX:
            out = out[: _XLSX_MAX - 25] + "…[truncated, see CSV]"
    return out


def write_xlsx(
    rows: list[dict],
    columns: list[str],
    out_dir: Path,
    timestamp: str,
    filename_stem: str = "submissions_export",
) -> Path:
    """Write rows to <filename_stem>_<timestamp>.xlsx (same data/columns as CSV).

    Arrays/objects are serialized as JSON strings, matching the CSV output.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "Missing dependency 'openpyxl' (needed for XLSX). Run "
            "pip install -r requirements.txt"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{filename_stem}_{timestamp}.xlsx"
    wb = Workbook(write_only=True)  # memory-friendly for large extracts
    ws = wb.create_sheet()
    ws.append(list(columns))
    for row in rows:
        ws.append([_xlsx_cell(row.get(col), ILLEGAL_CHARACTERS_RE) for col in columns])
    wb.save(path)
    return path
