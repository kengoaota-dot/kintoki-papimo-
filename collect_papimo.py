
import csv
import json
import re
import time
import urllib.request
import urllib.parse
from pathlib import Path
from html import unescape
from datetime import datetime, timezone, timedelta

# 金時京急蒲田店
HALL_ID = "00031691"

# 最初は3台でテスト
MACHINES = [305, 306, 307]

# 本番用102台（検証後に切り替え）
ALL_MACHINES = list(range(301, 374)) + list(range(433, 462))

JST = timezone(timedelta(hours=9))
OUTPUT = Path("papimo_collection")
OUTPUT.mkdir(exist_ok=True)

def fetch(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()

def clean(text):
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).replace("\xa0", "").strip()

def get_value(html, label):
    pattern = re.escape(label) + r"\s*<p[^>]*>(.*?)</p>"
    match = re.search(pattern, html, re.S)
    return clean(match.group(1)) if match else None

def get_history(html, machine):
    result = []
    match = re.search(
        r'<table class="history"[^>]*>.*?<tbody>(.*?)</tbody>',
        html, re.S
    )
    if not match:
        return result

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.S)

    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) != 5:
            continue

        result.append({
            "machine": machine,
            "count": clean(cells[0]),
            "time": clean(cells[1]),
            "start": clean(cells[2]),
            "payout": clean(cells[3]),
            "type": clean(cells[4]),
        })

    return result

def collect(machine):
    url = f"https://papimo.jp/h/{HALL_ID}/hit/view/{machine}"
    raw = fetch(url)
    html = raw.decode("utf-8", errors="replace")

    folder = OUTPUT / str(machine)
    folder.mkdir(exist_ok=True)

    (folder / "page.html").write_bytes(raw)

    title = re.search(r"<title>(.*?)</title>", html, re.S)
    updated = re.search(r"データ更新日時：([^<]+)", html)
    graph = re.search(
        r'id="tab-graph-today".*?<img[^>]+src="([^"]+)"',
        html, re.S
    )

    updated_text = clean(updated.group(1)) if updated else None

    data = {
        "hall_id": HALL_ID,
        "machine": machine,
        "page_title": clean(title.group(1)) if title else None,
        "business_date": (
            updated_text[:10] if updated_text else None
        ),
        "updated_at": updated_text,
        "collected_at_jst": datetime.now(JST).isoformat(),
        "bb": get_value(html, "BB回数"),
        "rb": get_value(html, "RB回数"),
        "total_start": get_value(html, "総スタート"),
        "final_start": get_value(html, "最終スタート"),
        "max_payout": get_value(html, "最大出メダル"),
        "signed_difference": None,
        "difference_source": "not_analyzed",
        "graph_status": "not_found",
        "errors": [],
    }

    history = get_history(html, machine)

    with open(
        folder / "history.csv",
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "machine", "count", "time",
                "start", "payout", "type"
            ]
        )
        writer.writeheader()
        writer.writerows(history)

    data["history_count"] = len(history)

    if not history:
        data["errors"].append("history_not_found")

    if graph:
        graph_url = urllib.parse.urljoin(
            url, unescape(graph.group(1))
        )
        data["graph_url"] = graph_url

        try:
            image = fetch(graph_url)
            if image.startswith(b"\x89PNG\r\n\x1a\n"):
                (folder / "graph.png").write_bytes(image)
                data["graph_status"] = "saved"
            else:
                data["graph_status"] = "invalid_png"
                data["errors"].append("invalid_graph_image")
        except Exception as e:
            data["graph_status"] = "error"
            data["errors"].append(str(e))
    else:
        data["errors"].append("graph_url_not_found")

    if data["bb"] is not None and data["rb"] is not None:
        try:
            expected = int(data["bb"].replace(",", "")) + int(
                data["rb"].replace(",", "")
            )
            data["history_count_matches"] = (
                expected == len(history)
            )
            if expected != len(history):
                data["errors"].append("history_count_mismatch")
        except ValueError:
            data["history_count_matches"] = None
    else:
        data["history_count_matches"] = None

    (folder / "machine.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(
        f"{machine}: 履歴{len(history)}件, "
        f"グラフ={data['graph_status']}, "
        f"エラー={data['errors']}"
    )

    return data

results = []

for machine in MACHINES:
    try:
        results.append(collect(machine))
    except Exception as e:
        print(f"{machine}: 取得失敗: {e}")
        results.append({
            "machine": machine,
            "status": "failed",
            "error": str(e)
        })

    time.sleep(2)

(OUTPUT / "collection_summary.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print("3台の収集テスト完了")
