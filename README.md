# kintoki-papimo-

金時京急蒲田店（PAPIMO店舗ID 00031691）のデータ収集。

## 自動実行

- 毎日23:10 日本時間（UTC 14:10、cron: 10 14 * * *）。GitHub Actionsの混雑等で開始が遅れる場合があります。
- 非ジャグラー102台：301〜373、433〜461。各台の機種名でもジャグラー混入を検査します。
- 深夜〜午前9時台の手動再実行は前営業日を対象にします。任意の過去日は手動実行の business_date に YYYY-MM-DD を指定します。
- Actions → Kintoki PAPIMO Collection → Run workflow。scope=test は305・306・307、scope=all は102台です。通常の初期値はallです。

## 取得と検証

台データ（機種名、BB、RB、総スタート、最終スタート、最大出メダル）、表示営業日、更新日時、取得日時、全大当たり履歴、当日スランプグラフを保存します。
履歴の「もっと見る」はHTML内の非表示行を展開する方式のため、非表示行も含めて全行を読み取り、総件数とBIG/REG別件数をBB/RBと照合します。PNGは形式・寸法・完全読み込みを検証します。

サイトに元からある空欄は埋めず、source_missing_fieldsに行・時刻・項目を記録します。取得エラーと元サイトの欠測は別扱いです。signed_difference は未解析のためnullです。履歴の払出合計を符号付き差枚とみなしません。

## 保存

各実行のArtifactsに papimo-実行ID-試行番号 を保存（90日間）。
展開後の構成：papimo_collection / 営業日 / 実行ID-試行番号 / 台番号。
各台に page.html、machine.json、history.csv、graph.png。全体に run_manifest.json、collection_summary.json、validation_report.md。
営業日と実行IDで分離するため再実行は既存ファイルを上書きしません。途中までの結果も毎台保存し、失敗時もArtifactsへアップロードします。長期保管には期限前にArtifactsをダウンロードしてください。

取得に失敗した台、欠測、不一致はActionsのSummaryと検証レポートで確認できます。技術的な取得不備が1台でもあれば終了コード1です。収集完了は「サイトに掲載された値の取得完了」であり、元サイトの欠測解消や閉店確定値を保証しません。

## 検証記録

2026-11-14（日本時間）、11月13日営業日で3台の実収集を検証済み。

|台番号|BB|RB|履歴件数|当日PNG|
|---|---:|---:|---:|---|
|305|6|2|8|正常|
|306|15|5|20|正常|
|307|6|3|9|正常|

306の22:06 REGの出メダルは元サイトで空欄のため、空欄のまま保持しています。
[3台検証Actions #15](https://github.com/kengoaota-dot/kintoki-papimo-/actions/runs/43460152571)

既存データ、分析定義、予測履歴、ChatGPTの既存定期タスクの削除・初期化は行いません。TinyFishは使用しません。
