import urllib.request
from datetime import datetime

url = "https://papimo.jp/h/00031691/hit/view/305"

print("PAPIMO 305番台 接続テスト")
print("開始時刻:", datetime.now())

request = urllib.request.Request(
    url,
    headers={"User-Agent": "Mozilla/5.0"}
)

with urllib.request.urlopen(request, timeout=30) as response:
    html = response.read()
    print("HTTPステータス:", response.status)
    print("取得サイズ:", len(html), "bytes")

print("接続テスト完了")
