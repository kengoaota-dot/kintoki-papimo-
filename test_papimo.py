

import urllib.request
import urllib.parse
import re
import csv
import json
from pathlib import Path
from html import unescape
from datetime import datetime, timezone, timedelta

MACHINE = 305
URL = f"https://papimo.jp/h/00031691/hit/view/{MACHINE}"
OUT = Path("papimo_data")
OUT.mkdir(exist_ok=True)

def fetch(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()

def clean(value):
    value = re.sub(r"<[^>]+>", "", value)
    return unescape(value).strip()

def number(value):
    return int(value.replace(",", ""))

def extract(pattern, html):
    match = re.search(pattern, html, re.S)
    return number(clean(match.group(1))) if match else None

print("PAPIMO 305番台 収集開始")

raw = fetch(URL)
html = raw.decode("utf-8", errors="replace")

# 元のHTMLも保存
Path("papimo_305.html").write_bytes(raw)

title = re.search(r"<title>(.*?)</title>", html, re.S)
update = re.search(r"データ更新日時：([^<]+)", html)
graph = re.search(
    r'id="tab-graph-today".*?<img[^>]+src="([^"]+)"',
    html, re.S
)

data = {
    "machine": MACHINE,
    "page_title": clean(title.group(1)) if title else "",
    "updated_at": clean(update.group(1)) if update else "",
    "bb": extract(r'BB回数.*?<p[^>]*>(.*?)</p>', html),
    "rb": extract(r'RB回数.*?<p[^>]*>(.*?)</p>', html),
    "total_start": extract(r'総スタート<p[^>]*>(.*?)</p>', html),
    "final_start": extract(r'最終スタート<p[^>]*>(.*?)</p>', html),
    "max_payout": extract(r'最大出メダル<p[^>]*>(.*?)</p>', html),
    "collected_at_jst": datetime.now(
        timezone(timedelta(hours=9))
    ).isoformat(),
}

# 大当たり履歴
history = []
table = re.search(
    r'<table class="history">(.*?)</table>',
    html, re.S
)

if table:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(1), re.S)
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) == 5:
            history.append({
                "machine": MACHINE,
                "count": clean(cells[0]),
                "time": clean(cells[1]),
                "start": clean(cells[2]),
                "payout": clean(cells[3]),
                "type": clean(cells[4]),
            })

# 台データ保存
(OUT / "machine_305.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

# 履歴保存
with open(OUT / "history_305.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["machine", "count", "time", "start", "payout", "type"]
    )
    writer.writeheader()
    writer.writerows(history)

# スランプグラフ画像
graph_status = "not_found"

if graph:
    graph_url = urllib.parse.urljoin(URL, unescape(graph.group(1)))
    data["graph_url"] = graph_url
    try:
        image = fetch(graph_url)
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            (OUT / "graph_305.png").write_bytes(image)
            graph_status = "saved"
        else:
            graph_status = "not_png"
    except Exception as e:
        graph_status = f"error: {e}"

data["graph_status"] = graph_status

(OUT / "machine_305.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print("台データ:", data)
print("大当たり履歴:", len(history), "件")
print("グラフ:", graph_status)
print("収集完了")
