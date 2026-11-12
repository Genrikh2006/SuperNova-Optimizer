"""Работа с папкой выгрузок на компьютере пользователя."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .io_import import _read_raw
from .supernova_report import report_meta, universe_from_meta

APP_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = APP_DIR / "settings.json"
DEFAULT_FOLDER = APP_DIR / "SuperNova Data"
EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".csv"}


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**values) -> None:
    data = load_settings()
    data.update(values)
    try:
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def find_reports(folder: Path, max_depth: int = 4) -> list[Path]:
    """Excel/CSV-файлы в папке (и подпапках), новые — первыми. Файлы самой базы SuperNova не трогаем."""
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        return []
    out = []
    for p in folder.rglob("*"):
        try:
            rel = p.relative_to(folder)
        except ValueError:
            continue
        if len(rel.parts) > max_depth or any(part.startswith((".", "~$")) for part in rel.parts):
            continue
        if p.is_file() and p.suffix.lower() in EXTENSIONS:
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def file_kind(path: Path) -> str:
    """'price', 'aqh' или 'other' — по названиям листов и заголовкам."""
    try:
        sheets = _read_raw(path)
    except Exception:  # noqa: BLE001
        return "other"
    service = ("прайс", "цены", "price", "prices", "охват", "reach", "как заполнять")
    heads = [" ".join(str(v).lower() for v in raw.head(15).to_numpy().ravel() if isinstance(v, str))
             for n, raw in sheets.items() if str(n).strip().lower() not in service]
    if any("aqh" in h or "база данных" in h or "rtg" in h for h in heads):
        return "aqh"
    if any(str(n).strip().lower() in ("прайс", "цены", "price", "prices") for n in sheets) or \
            any("цена" in h or "стоимость" in h or "price" in h for h in heads):
        return "price"
    return "other"


def report_info(path: Path) -> dict:
    """Шапка выгрузки: база, целевая группа, её размер, дата файла."""
    info = {"Файл": path.name, "Изменён": datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")}
    try:
        for raw in _read_raw(path).values():
            meta = report_meta(raw)
            if meta:
                info["База"] = meta.get("База данных", "")
                info["Целевая группа"] = meta.get("Целевая группа", "")
                size: Optional[float] = universe_from_meta(meta)
                if size:
                    info["Размер ЦА, тыс."] = size
                break
    except Exception:  # noqa: BLE001
        pass
    return info
