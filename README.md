# kintoki-papimo-

金時京急蒲田店（PAPIMO店舗ID 00031691）の収集システム。
現行の「PAPIMO収集専用定義 v1.1」「差分収集最適化 v1.2」に合わせた実装です。

## 日次の実行

- 毎日23:10日本時間（UTC 14:10）。GitHub側の混雑で実開始が遅れる場合があります。
- 現在の非ジャグラー102台（301〜373、433〜461）。機種一覧と設置台数の変化を確認し、入替時は機種名で対象を再判定します。
- 通常は当日グラフだけを使います。3日・7日グラフの読取、予測、SIM、AT、有利区間分析は実行しません。
- TinyFish、有料API、追加課金は使用しません。収集タスクの時刻・有効状態、他タスク、分析定義は保持します。
- 既存PAPIMOタスクの実行本文には、GitHubの取得結果を再利用し、Workの画像確認・既存正本への反映を続ける手順を接続しました。

Actions → Kintoki PAPIMO Collection → Run workflow で手動実行できます。
scope=testは305・306・307、scope=allは全対象台。business_dateはYYYY-MM-DD形式で、空欄なら午前10時より前は前営業日、それ以降は当日です。

## 完了の意味

GitHub Actionsの画像処理は**候補抽出**です。OCR/CVだけでWorkの画像確認を済ませたことにはしません。
候補には右端の符号付き差枚、最高・最低、最大上下差枚、時系列最大上昇、軸目盛、0線、端点、誤差、丸め単位を保存します。
右端が0枚近傍なら符号未確定、軸不明や画像端で切れる曲線は例外です。最大出メダルは差枚に流用しません。

以下がそろうまで完全収集にはしません。

1. 基本値・機種コード・表示確率・更新/取得日時の保存。
2. 全履歴のBB/RB別照合。サイトの空欄はnull/空欄で保持し、項目別理由を記録。
3. Workが画像の0線・軸・右端・最高最低を確認した差枚の保存。
4. 軽量索引・数値原簿・既存Excel正本への保存と、その版の照合。

未完了が残ると終了コード2です。これは未確認を成功扱いしないための判定です。
audit.jsonのdefinition_complete、work_confirmed_difference、fully_completeを確認してください。
取得通信エラー、元サイトの欠測、画像確認待ち、Excel反映待ちは分けて記録します。

## 保存・差分・再開

SQLite原簿に訂正前のスナップショット、完全履歴系列、再確認の観測記録を保持します。
履歴の同時刻だけで重複削除せず、回数・時刻・G・出メダル・種別の系列で接続します。
接続不能や訂正は旧系列を残して新系列を保存。台・営業日・機種が違う履歴は混ぜません。
各台を永続原簿へ記録し、約10台ごとに再開位置を保存します。

毎回、直前の状態Artifactsを復元してから追記します。旧方式のJSON/CSVも削除せず移行します。
復元できない場合に空の原簿へ勝手に初期化しません。Artifactsは90日保管で、実行のたびに累積状態を引き継ぎます。
長期間実行されず復元元が失効した場合は、既存の保存データを戻してから再開してください。

新しい実行ではグラフ画像をメモリ上で処理し、PNG・縮小画像・画像ZIPを保存しません。
すでに存在する旧画像Artifactsは削除しません。

主な出力：daily_machines.csv、graph_features.csv、collection_summary.json、lightweight_index.json、
exception_queue.json、work_review_queue.json、checkpoint.json、audit.json、excel_input.json。
台別のhistory.csvとhistory_delta.csv、累積原簿state/papimo.sqlite3も保存します。
CSVの正の差枚には＋符号を付け、Excelでは数値型と表示書式で扱います。
ページ更新時刻をグラフ更新時刻へ転記しません。グラフ更新時刻が不明なら不明理由を残します。

過去補完は2026-11-03以降の不足のみ。Workで既存正本・軽量索引を確認したbackfill_plan.jsonがある場合に限り、現在日の巡回後、最大10台日を補完します。
各計画行のbusiness_date、machine、confirmed_missing=true、canonical_index_checked_at_jstを確認します。
GitHub原簿に行がないことだけでは既存Libraryでも未取得とはみなさず、計画がなければ重複した過去取得は行いません。
公開期間の目安（当日グラフ13日・履歴3日）を別々に記録し、範囲外を取得済みにしません。
backfill_queue.jsonとbackfill_audit.jsonで日次の分母と分けて報告します。

## Workで確認を完了する手順

GitHub Actions単体ではChatGPTの画像確認やLibrary正本の置換を実行できません。
既存のWork収集処理で以下を行います。新しいAPIキーは不要です。

1. 最新の数値Artifactsとwork_review_queue.jsonを確認。必要な当日グラフだけをURLから小分けで表示。
2. 確認結果をreviews/YYYY-MM-DD.jsonへ保存。1台につきmachine、business_date、machine_code、graph_sha256、
   signed_difference、highest_difference、lowest_difference、estimated_error、rounding_unit、sign_judgment、
   zero_line_confirmed、axis_confirmed、right_endpoint_confirmed、extrema_confirmed、reviewed_at_jst、reviewerが必須。
   確認フラグは実際に確認したものだけtrueにする。画像ハッシュや機種/営業日が一致しない確認結果は拒否します。
3. complete_saved.pyで保存済み数値原簿へ反映。PAPIMOを全台再取得する必要はありません。

```bash
python complete_saved.py --artifact-dir extracted --date 2026-11-13 --reviews-dir reviews --out reviewed-output
```

4. Workのartifact-tool環境でbuild_workbook.mjsを使い、既存正本をテンプレートとして追記。
   既存日本語ヘッダーへ対応させる明示的な列マップを必須とし、他シート・既存行・数式を消しません。
   既存表の列構造が対応しない場合は安全に停止します。新しい同名正本を勝手に作りません。

```bash
"$CODEX_PRIMARY_RUNTIME_NODE" build_workbook.mjs --input reviewed-output/excel_input.json \
  --template canonical.xlsx --map column-map.json --output updated-canonical.xlsx
```

5. 既存Libraryファイルを同じIDのまま更新して保存版を確認した後、workbook_export_receipt.jsonを元に
   reviews/receipts/YYYY-MM-DD.jsonへbusiness_date、snapshot_ids、writeback_verified=true、library_file_id、version_idを記録。
   snapshot_idsは出力された全対象台のソート済みIDと一致する必要があります。
   ローカルExcelの書き出し成功だけでwriteback_verifiedをtrueにしません。
   complete_saved.pyを再実行すると、差枚・欠測・正本反映をまとめて再判定します。

## 検証記録

- 旧方式Actions #16：11月13日分の基本値102台、履歴1,399件、PNG102枚を取得。元サイトの出メダル空欄200件。
  **現行定義全体の完全収集を意味する記録ではありません。**
- 新方式：履歴接続、訂正保存、台別分離、元値への復帰、画像確認の識別子照合、未完了判定をテスト。
- 保存済み11月13日の102画像で候補抽出を検算し、保存されたWorkの右端差枚と102台とも申告誤差内で一致。
  この比較結果を新しい画像の確認済みフラグへ流用することはありません。
- 実行結果はActionsのSummaryとaudit.jsonを正とし、未確認を完了扱いしません。
