"""Анализ файлов баз SuperNova (или любых непонятных файлов данных).

Использование:
    python tools/inspect_supernova.py путь/к/файлу_или_папке [...]  > отчёт.txt

Скрипт ничего не изменяет. Для каждого файла выводит:
* размер, сигнатуру (первые байты) и распознанный контейнер (ZIP, OLE/CFB, Access, SQLite,
  dBase, gzip, 7z, RAR, XML и т.д.);
* энтропию (≈8 бит/байт → сжато или зашифровано; меньше → открытые структуры);
* найденные строки (ASCII, UTF-16LE, CP1251) — по ним видно названия станций, ЦА, поля;
* для ZIP — список вложенных файлов; для SQLite — таблицы; для dBase — поля.

Отчёт можно прислать разработчику, чтобы написать прямой импорт из этого формата.
"""
from __future__ import annotations

import math
import re
import sqlite3
import struct
import sys
import zipfile
from collections import Counter
from pathlib import Path

SIGNATURES = [
    (b"PK\x03\x04", "ZIP-архив (может быть .xlsx/.docx или упакованная база)"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE Compound File (старые .xls/.doc, MSI и др.)"),
    (b"\x00\x01\x00\x00Standard Jet DB", "Microsoft Access .mdb (Jet 3/4)"),
    (b"\x00\x01\x00\x00Standard ACE DB", "Microsoft Access .accdb (ACE)"),
    (b"SQLite format 3\x00", "SQLite"),
    (b"\x1f\x8b", "gzip"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip"),
    (b"Rar!", "RAR"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"<?xml", "XML"),
    (b"\xef\xbb\xbf<?xml", "XML (UTF-8 BOM)"),
    (b"\xff\xfe", "текст UTF-16LE (BOM)"),
    (b"MZ", "исполняемый файл Windows / DLL"),
    (b"\x78\x9c", "zlib-поток"),
    (b"\x78\xda", "zlib-поток"),
]
DBF_VERSIONS = {0x02, 0x03, 0x30, 0x31, 0x43, 0x63, 0x83, 0x8B, 0xCB, 0xF5, 0xFB}


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    c = Counter(data)
    n = len(data)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def detect(head: bytes) -> str:
    for sig, name in SIGNATURES:
        if head.startswith(sig):
            return name
    if len(head) > 32 and head[0] in DBF_VERSIONS and 1 <= head[2] <= 12 and 1 <= head[3] <= 31:
        return "вероятно dBase/FoxPro .dbf"
    if head[:1] in (b"{", b"["):
        return "вероятно JSON"
    return "неизвестный (собственный формат)"


def strings_ascii(data: bytes, min_len=5):
    return [m.group().decode("ascii") for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data)]


def strings_utf16(data: bytes, min_len=4):
    out = []
    for m in re.finditer(rb"(?:[\x20-\x7e\x00-\x04][\x00\x04]){%d,}" % min_len, data):
        try:
            s = m.group().decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if sum(ch.isalnum() for ch in s) >= min_len:
            out.append(s)
    return out


def strings_cp1251(data: bytes, min_len=5):
    out = []
    for m in re.finditer(rb"[\xc0-\xff\xa8\xb8\x20-\x7e]{%d,}" % min_len, data):
        b = m.group()
        if any(x >= 0xC0 for x in b):
            out.append(b.decode("cp1251", errors="replace"))
    return out


def inspect_file(path: Path, max_strings: int = 60) -> None:
    data = path.read_bytes()
    print("=" * 78)
    print(f"Файл: {path.name}")
    print(f"Размер: {len(data):,} байт")
    print(f"Первые 32 байта (hex): {data[:32].hex(' ')}")
    print(f"Тип: {detect(data[:64])}")
    ent = entropy(data[:1_000_000])
    verdict = ("сжато или зашифровано" if ent > 7.5 else
               "смешанные данные (частично сжато/двоичные числа)" if ent > 6 else
               "открытая структура — можно разбирать")
    print(f"Энтропия: {ent:.2f} бит/байт → {verdict}")

    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                print("Содержимое ZIP:")
                for i in z.infolist()[:100]:
                    print(f"   {i.filename}  ({i.file_size:,} байт, сжато {i.compress_size:,})"
                          + ("  [зашифрован]" if i.flag_bits & 0x1 else ""))
        except zipfile.BadZipFile as e:
            print(f"ZIP не читается: {e}")
    if data.startswith(b"SQLite format 3\x00"):
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            for (name, sql) in con.execute("select name, sql from sqlite_master where type='table'"):
                cnt = con.execute(f'select count(*) from "{name}"').fetchone()[0]
                print(f"   таблица {name}: {cnt} строк\n      {sql}")
        except sqlite3.Error as e:
            print(f"SQLite не открывается: {e}")
    if detect(data[:64]).startswith("вероятно dBase"):
        nrec, hlen = struct.unpack("<IH", data[4:10])
        print(f"   записей: {nrec}, длина заголовка: {hlen}")
        for off in range(32, hlen - 1, 32):
            if data[off] == 0x0D:
                break
            name = data[off:off + 11].split(b"\x00")[0].decode("cp1251", "replace")
            print(f"   поле {name} тип {chr(data[off + 11])} длина {data[off + 16]}")

    for title, fn in (("ASCII", strings_ascii), ("UTF-16LE", strings_utf16), ("CP1251", strings_cp1251)):
        found = fn(data[:5_000_000])
        uniq = list(dict.fromkeys(s.strip() for s in found if s.strip()))
        if uniq:
            print(f"Строки {title} (уникальных {len(uniq)}, показаны первые {min(len(uniq), max_strings)}):")
            for s in uniq[:max_strings]:
                print("   " + s[:120])

    # повторяющиеся блоки фиксированной длины → признак таблицы записей
    if ent < 7.5 and len(data) > 4096:
        import numpy as np
        arr = np.frombuffer(data[len(data) // 4: len(data) // 4 + 65536], dtype=np.uint8)
        best = []
        for rec in range(4, 257):
            best.append((float(np.mean(arr[:-rec] == arr[rec:])), rec))
        best.sort(reverse=True)
        print("Возможная длина записи (байт, доля совпадений): "
              + ", ".join(f"{r} ({s:.0%})" for s, r in best[:5]))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    for arg in argv:
        p = Path(arg)
        files = sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
        for f in files:
            try:
                inspect_file(f)
            except Exception as e:  # noqa: BLE001
                print(f"{f}: ошибка анализа: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
