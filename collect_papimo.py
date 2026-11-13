"""PAPIMO collector: explicit business date, all HTML history rows, strict validation."""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from PIL import Image

HALL_ID = "00031691"
TEST_MACHINES = [305, 306, 307]
ALL_MACHINES = list(range(301, 374)) + list(range(433, 462))
DEFAULT_SCOPE = "test"
JST = timezone(timedelta(hours=9))
FIELDS = ["business_date", "machine", "row", "count", "time", "start", "payout", "type"]


def fetch(url):
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except Exception:
            if attempt == 1:
                raise
            time.sleep(2 * (attempt + 1))


def number(value):
    value = value.replace(",", "").strip()
    return int(value) if re.fullmatch(r"[0-9]+", value) else None


def parse_page(raw, machine, business_date):
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text(" ", strip=True)
    title = soup.title.get_text(strip=True) if soup.title else None
    date_option = soup.select_one("#display-date option[selected]")
    displayed = date_option.get("value") if date_option else None
    updated = re.search(r"データ更新日時[：:]\s*([0-9/]+\s+[0-9:]+)", text)
    data = {"hall_id": HALL_ID, "machine": machine, "page_title": title,
            "machine_name": title.split(" - ")[1] if title and len(title.split(" - ")) >= 3 else None,
            "business_date": business_date, "displayed_date": displayed,
            "updated_at": updated.group(1) if updated else None,
            "collected_at_jst": datetime.now(JST).isoformat(),
            "signed_difference": None, "difference_source": "not_analyzed",
            "errors": [], "source_missing_fields": [], "graph_status": "not_found", "history_method": "all_html_rows_including_hidden"}
    errors = data["errors"]
    if displayed != business_date.replace("-", ""):
        errors.append("business_date_mismatch")
    if not title or "金時京急蒲田" not in title:
        errors.append("wrong_hall_or_page")
    if not re.search(rf"0?{machine}番台", text):
        errors.append("machine_number_mismatch")
    if not data["machine_name"]:
        errors.append("machine_name_missing")
    if "ジャグラー" in (data["machine_name"] or ""):
        errors.append("juggler_excluded")
    if not updated:
        errors.append("updated_at_missing")
    panel = soup.select_one("#tab-data-today")
    for key, label in [("bb", "BB回数"), ("rb", "RB回数"), ("total_start", "総スタート"),
                       ("final_start", "最終スタート"), ("max_payout", "最大出メダル")]:
        value = None
        if panel:
            for cell in panel.select("td"):
                if cell.get_text(strip=True).startswith(label):
                    p = cell.find("p")
                    value = number(p.get_text(strip=True)) if p else None
                    break
        data[key] = value
        if value is None:
            errors.append(key + "_missing")
    history = []
    table = soup.select_one("#tab-history-index table.history")
    if table:
        for row in table.select("tbody tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) != 5:
                errors.append("malformed_history_row")
                continue
            values = [c.get_text(strip=True) for c in cells]
            history.append(dict(zip(FIELDS, [business_date, machine, len(history) + 1] + values)))
    data["history_count"] = len(history)
    data["history_bb_count"] = sum(r["type"].upper() in ("BIG", "BB") for r in history)
    data["history_rb_count"] = sum(r["type"].upper() in ("REG", "RB") for r in history)
    data["history_count_matches"] = (data["bb"] is not None and data["rb"] is not None
        and len(history) == data["bb"] + data["rb"]
        and data["history_bb_count"] == data["bb"] and data["history_rb_count"] == data["rb"])
    if not data["history_count_matches"]:
        errors.append("history_count_or_type_mismatch")
    for row in history:
        if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", row["time"]) or any(number(row[k]) is None and row[k] not in ("", "-", "—") for k in ("start", "payout")):
            errors.append("invalid_history_values")
            break
    for row in history:
        for key in ("start", "payout"):
            if number(row[key]) is None:
                data["source_missing_fields"].append({"row": row["row"], "time": row["time"], "field": key})
    # 'More' only reveals rows already in this HTML; no AJAX pages are discarded.
    data["history_complete"] = data["history_count_matches"] and not any("history" in e for e in errors)
    graph = soup.select_one("#tab-graph-today img")
    data["graph_url"] = graph.get("src") if graph else None
    return data, history


def validate_png(raw):
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "PNG" or min(image.size) < 100:
            raise ValueError("invalid_graph_format_or_size")
        size = image.size
        image.verify()
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.convert("RGB").getextrema() == ((255, 255), (255, 255), (255, 255)):
            raise ValueError("blank_graph")
    return size


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")




def self_test():
    fixture = '<title>金時京急蒲田 - テスト機 - 大当り情報</title><select id="display-date"><option selected value="20261113">2026/11/13</option></select>0305番台 データ更新日時：2026/11/13 23:01<div id="tab-data-today">'
    for label, value in [("BB回数", 1), ("RB回数", 1), ("総スタート", 100), ("最終スタート", 5), ("最大出メダル", 200)]:
        fixture += f'<td>{label}<p>{value}</p></td>'
    fixture += '</div><div id="tab-history-index"><table class="history"><tbody><tr style="display:none"><td>1</td><td>12:00</td><td>10</td><td>100</td><td>BIG</td></tr><tr><td></td><td>11:00</td><td>90</td><td>10</td><td>REG</td></tr></tbody></table></div>'
    data, rows = parse_page(fixture, 305, "2026-11-13")
    assert data["history_complete"] and len(rows) == 2 and data["bb"] == 1 and not data["errors"], data
    assert parse_page(fixture.replace("<td>100</td>", "<td></td>"), 305, "2026-11-13")[0]["source_missing_fields"]
    assert "business_date_mismatch" in parse_page(fixture, 305, "2026-11-12")[0]["errors"]
    assert not parse_page(fixture.replace('>REG<', '>BIG<'), 305, "2026-11-13")[0]["history_complete"]
    assert "bb_missing" in parse_page(fixture.replace('BB回数<p>1', 'BB回数<p>-'), 305, "2026-11-13")[0]["errors"]
    try:
        validate_png(b"not an image")
    except Exception:
        pass
    else:
        raise AssertionError("invalid image accepted")
    print("Self-tests passed: hidden rows, date, type counts, missing values, invalid image")




if __name__ == "__main__":
    import sys
    from definition_pipeline import run
    raise SystemExit(run(sys.modules[__name__]))

