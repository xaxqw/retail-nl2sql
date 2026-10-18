# -*- coding: utf-8 -*-
"""文件接入：把上传的 CSV / XLSX 转成可查询的 SQLite 数据集

不依赖 pandas / openpyxl：
- CSV 用标准库 csv
- XLSX 本质是 zip + xml，用 zipfile + ElementTree 手写解析

产出：data/uploads/<id>.db（含一张数据表）+ <id>.json（元数据）
"""

import csv
import io
import json
import os
import re
import sqlite3
import time
import uuid
import zipfile
from xml.etree import ElementTree as ET

from . import config

UPLOAD_DIR = os.path.join(config.BASE_DIR, "data", "uploads")
MAX_ROWS = 200_000
MAX_BYTES = 20 * 1024 * 1024

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


# ---------------- 通用工具 ----------------

def _clean_header(h, idx):
    h = (h or "").strip().strip('"').strip("'")
    h = re.sub(r"\s+", "_", h)
    h = re.sub(r"[^\w\u4e00-\u9fff]", "_", h)
    h = h.strip("_")
    if not h or h[0].isdigit():
        h = f"col_{idx + 1}"
    return h[:40]


def _dedup(headers):
    seen, out = {}, []
    for h in headers:
        if h in seen:
            seen[h] += 1
            out.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            out.append(h)
    return out


def _is_int(v):
    try:
        int(str(v).replace(",", ""))
        return True
    except (ValueError, TypeError):
        return False


def _is_float(v):
    try:
        float(str(v).replace(",", ""))
        return True
    except (ValueError, TypeError):
        return False


TYPE_ORDER = {"INTEGER": 0, "REAL": 1, "TEXT": 2}


def infer_types(headers, rows):
    """按列采样推断类型：整数 < 浮点 < 文本"""
    types = []
    for i in range(len(headers)):
        col = [r[i] for r in rows if i < len(r) and str(r[i]).strip() != ""]
        if not col:
            types.append("TEXT")
            continue
        sample = col[:500]
        if all(_is_int(v) for v in sample):
            types.append("INTEGER")
        elif all(_is_float(v) for v in sample):
            types.append("REAL")
        else:
            types.append("TEXT")
    return types


def _cast(v, t):
    s = ("" if v is None else str(v)).strip()
    if s == "":
        return None
    if t == "INTEGER":
        try:
            return int(s.replace(",", ""))
        except ValueError:
            return None
    if t == "REAL":
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return None
    return s


# ---------------- CSV ----------------

def parse_csv(raw: bytes):
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别文件编码，请另存为 UTF-8 后重试")

    sample = text[:8000]
    delimiter = max([",", ";", "\t", "|"], key=lambda d: sample.count(d))

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for i, row in enumerate(reader):
        if i > MAX_ROWS:
            break
        if not row or all(not str(c).strip() for c in row):
            continue
        rows.append(row)

    if len(rows) < 2:
        raise ValueError("文件内容太少，至少需要表头 + 1 行数据")

    headers = [_clean_header(h, i) for i, h in enumerate(rows[0])]
    headers = _dedup(headers)
    body = [r + [""] * (len(headers) - len(r)) if len(r) < len(headers)
            else r[:len(headers)] for r in rows[1:]]
    return headers, body


# ---------------- XLSX ----------------

def _xlsx_shared_strings(zf):
    out = []
    try:
        with zf.open("xl/sharedStrings.xml") as f:
            root = ET.fromstring(f.read())
        for si in root.findall("m:si", NS):
            parts = [t.text or "" for t in si.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
            out.append("".join(parts))
    except KeyError:
        pass
    return out


def _col_index(ref):
    """A1 -> 0, B1 -> 1, AA1 -> 26"""
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return None
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def parse_xlsx(raw: bytes):
    zf = zipfile.ZipFile(io.BytesIO(raw))
    shared = _xlsx_shared_strings(zf)

    # 找第一个工作表
    sheet_path = None
    for name in zf.namelist():
        if re.match(r"xl/worksheets/sheet\d+\.xml$", name):
            sheet_path = name
            break
    if not sheet_path:
        raise ValueError("压缩包里找不到工作表，可能不是有效的 xlsx")

    with zf.open(sheet_path) as f:
        root = ET.fromstring(f.read())

    grid = []
    for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
        if len(grid) > MAX_ROWS:
            break
        cells = {}
        for c in row.findall("m:c", NS):
            idx = _col_index(c.get("r"))
            if idx is None:
                continue
            t = c.get("t")
            v = c.find("m:v", NS)
            if t == "inlineStr":
                is_el = c.find("m:is", NS)
                val = "".join(x.text or "" for x in is_el.iter()) if is_el is not None else ""
            elif v is None:
                val = ""
            elif t == "s":
                try:
                    val = shared[int(v.text)]
                except (ValueError, IndexError):
                    val = ""
            else:
                val = v.text or ""
            cells[idx] = val
        if cells:
            width = max(cells) + 1
            grid.append([cells.get(i, "") for i in range(width)])

    if len(grid) < 2:
        raise ValueError("工作表内容太少，至少需要表头 + 1 行数据")

    width = max(len(r) for r in grid[:50])
    headers = [_clean_header(h, i) for i, h in enumerate(grid[0][:width])]
    headers = _dedup(headers)
    body = [(r + [""] * width)[:width] for r in grid[1:]]
    return headers, body


# ---------------- 落库 ----------------

def _safe_table_name(name):
    base = os.path.splitext(os.path.basename(name))[0]
    base = re.sub(r"[^\w\u4e00-\u9fff]", "_", base).strip("_") or "data"
    if base[0].isdigit():
        base = "t_" + base
    return base[:32].lower()


def build_dataset(filename: str, raw: bytes):
    if len(raw) > MAX_BYTES:
        raise ValueError(f"文件超过 {MAX_BYTES // 1024 // 1024} MB 上限")

    ext = os.path.splitext(filename)[1].lower()
    if ext in (".csv", ".txt", ".tsv"):
        headers, rows = parse_csv(raw)
    elif ext in (".xlsx",):
        headers, rows = parse_xlsx(raw)
    elif ext == ".xls":
        raise ValueError("不支持旧版 .xls，请在 Excel 里另存为 .xlsx")
    else:
        raise ValueError("仅支持 .csv / .xlsx / .txt / .tsv")

    types = infer_types(headers, rows)
    table = _safe_table_name(filename)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ds_id = uuid.uuid4().hex[:10]
    db_path = os.path.join(UPLOAD_DIR, f"{ds_id}.db")

    conn = sqlite3.connect(db_path)
    try:
        cols = ", ".join(f'"{h}" {t}' for h, t in zip(headers, types))
        conn.execute(f'CREATE TABLE "{table}" ({cols})')
        placeholders = ",".join("?" * len(headers))
        payload = [
            tuple(_cast(r[i], types[i]) if i < len(r) else None
                  for i in range(len(headers)))
            for r in rows
        ]
        conn.executemany(
            f'INSERT INTO "{table}" VALUES ({placeholders})', payload)
        conn.commit()
    finally:
        conn.close()

    # 每列的样例值，帮助模型理解字段含义
    samples = {}
    for i, h in enumerate(headers):
        vals = [str(r[i]) for r in rows[:200] if i < len(r) and str(r[i]).strip()]
        uniq = list(dict.fromkeys(vals))[:5]
        samples[h] = {"type": types[i], "samples": uniq, "distinct_hint": len(set(vals))}

    meta = {
        "id": ds_id,
        "name": filename,
        "table": table,
        "db_path": db_path,
        "rows": len(rows),
        "cols": len(headers),
        "headers": headers,
        "types": types,
        "columns": samples,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(UPLOAD_DIR, f"{ds_id}.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def list_datasets():
    if not os.path.isdir(UPLOAD_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(UPLOAD_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(UPLOAD_DIR, fn), encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            continue
    return sorted(out, key=lambda d: d.get("created", ""), reverse=True)


def get_dataset(ds_id: str):
    path = os.path.join(UPLOAD_DIR, f"{ds_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def delete_dataset(ds_id: str):
    meta = get_dataset(ds_id)
    if not meta:
        return False
    for p in (os.path.join(UPLOAD_DIR, f"{ds_id}.json"), meta.get("db_path", "")):
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    return True
