# kintoki-papimo-

金時京急蒲田店（PAPIMO店舗ID 00031691）の非ジャグラー102台を収集します。
「全工程をGitHubで実行」の指定に合わせた **GitHub自動収集 v2** です。

## 自動実行

毎日23:10日本時間（UTC14:10、cron `10 14 * * *`）。
Actions → Kintoki PAPIMO Collection → Run workflow でも実行できます。
scope=testは305・306・307、scope=allは全対象台（現在301〜373、433〜461）。
business_dateはYYYY-MM-DD。空欄なら午前10時より前は前営業日、それ以降は当日。
GitHubの混雑で開始が遅れる場合があります。営業時間中の手動テストは途中値です。

1. 直前の累積SQLite・ExcelをArtifactsから復元。復元不能時に空へ初期化しません。
2. 機種一覧を照合し、非ジャグラーの台データ・全履歴を取得してBB/RB件数を照合。
3. 当日グラフの軸・0線・赤線・右端・最高最低・誤差・符号を自動数値化。
4. 履歴系列・訂正前後のスナップショットをSQLiteへ保持。
5. 累積Excelへ追記し、再読込で記録ID・全履歴行ID・行数を検証。
6. Excel、SQLite、CSV、数値特徴量、軽量索引、例外キュー、再開位置をArtifactsへ保存。

Workの実行・画像の手動確認・Library反映・追加APIキーは不要。
TinyFish、有料APIは使用しません。画像はメモリ上で処理し新規PNGや画像ZIPは保存しません。
通常は当日グラフだけを使い、3日/7日グラフや予測・SIM・AT/有利区間分析は実行しません。
23:10の値は閉店確定値とは限らず、営業日・ページ更新・グラフ更新・取得時刻を区別します。

## 成功と欠測を別判定

- audit.json の automation_complete: 基本値・履歴照合・グラフ数値化・Excel検証・状態保存が正常終了。
- data_complete: 元サイト空欄や符号保留などの必須項目欠測がない。
- definition_complete: 上記の両方がtrue。

元サイトの空欄は空欄のまま保存し、0で補完しません。
0枚近傍で誤差内の符号は保留。これらは警告と例外キューに残して自動処理を正常終了できます。
通信失敗、履歴件数不一致、軸校正不能、グラフ欠落、基本値欠落、Excel検証失敗は処理失敗です。

自動推定は reading_status=automatic_image_estimate、difference_source=image_estimate_automatic。
目視確認やサイト実測とは区別します。過去のハッシュ一致のWork確認値は出所を変えず再利用します。

## 保存と引き継ぎ

実行ページのArtifactsにある papimo-state-実行ID-試行番号 をダウンロードしてください。

- state/papimo.xlsx: 累積Excel（台データ、大当たり履歴、取得状況、実行履歴、運用定義）
- state/papimo.sqlite3: 訂正前後を含む累積原簿
- state/latest_audit.json、state/workbook_receipt.json: 実行判定、ExcelのSHA256・保存行数
- papimo_collection/営業日/実行ID/: CSV、履歴、画像数値、欠測、再開位置、要約

既存LibraryのExcel・分析定義・予測履歴は保持し、公開リポジトリへコピーしません。
GitHubの累積Excelは既存GitHub原簿の全スナップショット・全履歴系列から引き継ぎます。
Excelは同じ記録ID・履歴行IDを重複追記せず、既存行・他シート・数式を保持します。
正差枚はCSVでは＋付き、Excelでは数値型と符号付き表示です。
Artifacts保管は90日で、毎回累積状態を引き継ぎます。長期停止により復元元が失効したときは保存状態を戻して再開します。
同時実行は順番待ちとし、失敗時も保存可能な途中状態を残します。旧データ・旧画像は削除しません。

過去補完は2026-11-03以降の確認済み不足だけ。
backfill_plan.json の business_date、machine、confirmed_missing=true、canonical_index_checked_at_jst がある場合に限り当日巡回後に最大10台日。
計画なしにGitHub未登録をLibraryでも未取得とみなして過去を全取得しません。
公開期間（グラフ約13日・履歴約3日）の外は欠測として記録します。

旧Work Confirmationワークフロー・complete_saved.py・build_workbook.mjs・確認記録は保存していますが、日次自動収集には不要です。
既存の定期タスクは削除・停止せず、GitHub結果を確認する役割へ引き継ぎます。他の分析・予測タスクは変更しません。

## 検証

`python collect_papimo.py --self-test`
`python -m unittest test_definition test_github_automatic -v`

履歴接続、訂正、台/営業日分離、元値復帰、画像識別子、自動推定と目視の区別、
Excelの重複防止・既存数式保持、欠測警告と真のエラーを検証します。
