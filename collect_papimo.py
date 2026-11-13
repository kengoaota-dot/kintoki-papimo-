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
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except Exception:
            if attempt == 2:
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
            "errors": [], "graph_status": "not_found", "history_method": "all_html_rows_including_hidden"}
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
        if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", row["time"]) or any(number(row[k]) is None for k in ("start", "payout")):
            errors.append("invalid_history_values")
            break
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


def collect(machine, business_date, output):
    folder = output / str(machine)
    folder.mkdir(parents=True, exist_ok=True)
    url = f"https://papimo.jp/h/{HALL_ID}/hit/view/{machine}/{business_date.replace('-', '')}"
    data = {"machine": machine, "business_date": business_date, "source_url": url,
            "collected_at_jst": datetime.now(JST).isoformat(), "errors": []}
    try:
        raw = fetch(url)
        (folder / "page.html").write_bytes(raw)
        data, history = parse_page(raw, machine, business_date)
        data["source_url"] = url
        data["page_sha256"] = hashlib.sha256(raw).hexdigest()
        with (folder / "history.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(history)
        if data["graph_url"]:
            data["graph_url"] = urljoin(url, data["graph_url"])
            image = fetch(data["graph_url"])
            (folder / "graph_download.bin").write_bytes(image)
            data["graph_dimensions"] = validate_png(image)
            (folder / "graph.png").write_bytes(image)
            data["graph_sha256"] = hashlib.sha256(image).hexdigest()
            data["graph_status"] = "saved_and_verified"
        else:
            data["errors"].append("today_graph_missing")
    except Exception as exc:
        data["errors"].append(type(exc).__name__ + ": " + str(exc))
    data["status"] = "complete" if not data["errors"] else "incomplete"
    save_json(folder / "machine.json", data)
    print(json.dumps({k: data.get(k) for k in ["machine", "business_date", "bb", "rb", "history_count", "history_complete", "graph_status", "status", "errors"]}, ensure_ascii=False), flush=True)
    return data


def self_test():
    fixture = '<title>金時京急蒲田 - テスト機 - 大当り情報</title><select id="display-date"><option selected value="20261113">2026/11/13</option></select>0305番台 データ更新日時：2026/11/13 23:01<div id="tab-data-today">'
    for label, value in [("BB回数", 1), ("RB回数", 1), ("総スタート", 100), ("最終スタート", 5), ("最大出メダル", 200)]:
        fixture += f'<td>{label}<p>{value}</p></td>'
    fixture += '</div><div id="tab-history-index"><table class="history"><tbody><tr style="display:none"><td>1</td><td>12:00</td><td>10</td><td>100</td><td>BIG</td></tr><tr><td></td><td>11:00</td><td>90</td><td>10</td><td>REG</td></tr></tbody></table></div>'
    data, rows = parse_page(fixture, 305, "2026-11-13")
    assert data["history_complete"] and len(rows) == 2 and data["bb"] == 1 and not data["errors"], data
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["test", "all"], default=DEFAULT_SCOPE)
    parser.add_argument("--date", default=os.getenv("PAPIMO_BUSINESS_DATE", ""))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    now = datetime.now(JST)
    # Before opening, collect the previous operating date, never an empty next-day page.
    business_date = args.date or (now - timedelta(days=int(now.hour < 10))).date().isoformat()
    datetime.strptime(business_date, "%Y-%m-%d")
    machines = TEST_MACHINES if args.scope == "test" else ALL_MACHINES
    assert len(ALL_MACHINES) == len(set(ALL_MACHINES)) == 102
    run_id = os.getenv("GITHUB_RUN_ID", now.strftime("%Y%m%dT%H%M%S%f"))
    run_id += "-" + os.getenv("GITHUB_RUN_ATTEMPT", "1")
    output = Path("papimo_collection") / business_date / run_id
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "run_manifest.json", {"business_date": business_date, "scope": args.scope,
        "machines": machines, "started_at_jst": now.isoformat(), "code_sha": os.getenv("GITHUB_SHA"),
        "finalized": False, "note": "Snapshot at collection time, not a guarantee of final closing figures"})
    results = []
    for machine in machines:
        results.append(collect(machine, business_date, output))
        save_json(output / "collection_summary.json", results)
        time.sleep(2)
    complete = sum(r["status"] == "complete" for r in results)
    report = f"Business date: {business_date} | Scope: {args.scope} | Complete: {complete}/{len(machines)}\n"
    report += "\n|Machine|BB|RB|History|Graph|Status|Errors|\n|---|---:|---:|---:|---|---|---|\n"
    for r in results:
        report += "|" + "|".join(str(r.get(k, "")) for k in ["machine", "bb", "rb", "history_count", "graph_status", "status", "errors"]) + "|\n"
    (output / "validation_report.md").write_text(report, encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report)
    print(f"Validated {complete}/{len(machines)} machines", flush=True)
    return 0 if complete == len(machines) else 1


if __name__ == "__main__":
    raise SystemExit(main())
