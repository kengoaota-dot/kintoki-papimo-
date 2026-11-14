"""Runtime Excel export for GitHub runners. Append revisions; never reset old rows."""
import hashlib
import json
import shutil
from pathlib import Path
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill,Alignment


MACHINE_COLUMNS={
 'snapshot_id':'記録ID','business_date':'営業日','machine':'台番号','machine_name':'機種名',
 'machine_code':'機種コード','bb':'BB','rb':'RB','total_start':'総スタート','final_start':'最終スタート',
 'max_payout':'最大出メダル（差枚ではない）','bb_probability':'BB確率原表示',
 'rb_probability':'RB確率原表示','combined_probability':'合成確率原表示',
 'signed_difference':'符号付き差枚（推定）','highest_difference':'最高差枚（推定）',
 'lowest_difference':'最低差枚（推定）','max_range':'最大上下差枚（推定）',
 'estimated_error':'推定誤差枚','rounding_unit':'読取単位枚','sign_judgment':'符号判定',
 'difference_source':'値の出所','reading_status':'読取方法','history_count':'履歴件数',
 'history_count_matches':'BB・RB件数一致','updated_at':'ページ更新日時',
 'graph_updated_at_jst':'グラフ更新日時（表示なしは空欄）','collected_at_jst':'取得日時JST',
 'source_url':'台ページURL','graph_url':'当日グラフURL','graph_sha256':'画像SHA256',
 'history_id':'履歴系列ID','status':'データ品質','collection_mode':'収集モード'}
HISTORY_COLUMNS={'key':'履歴行ID','history_id':'履歴系列ID','business_date':'営業日','machine':'台番号',
 'machine_code':'機種コード','row':'表示順','count':'原回数','time':'時刻','start':'G数原表示',
 'payout':'出メダル原表示','type':'種別'}
EXCEPTION_COLUMNS={'key':'例外ID','snapshot_id':'記録ID','business_date':'営業日','machine':'台番号',
 'item':'項目','row':'履歴行','reason':'理由'}
RUN_COLUMNS={'run_id':'実行ID','business_date':'営業日','required_machines':'対象台数',
 'automatically_read':'自動画像推定台数','work_confirmed_difference':'目視確認済み台数',
 'source_missing_fields':'元サイト空欄件数','uncertain_signs':'符号保留台数','data_complete':'全項目充足',
 'note':'備考'}


def safe(value):
    if isinstance(value,(list,dict)):value=json.dumps(value,ensure_ascii=False)
    if isinstance(value,str) and value.startswith(('=','+','-','@')):return "'"+value
    return value


def append_rows(wb,title,columns,records,key):
    if title not in wb.sheetnames:
        ws=wb.create_sheet(title);ws.append(list(columns.values()));ws.freeze_panes='D2'
        ws.row_dimensions[1].height=36
        for cell in ws[1]:
            cell.fill=PatternFill('solid',fgColor='22344D');cell.font=Font(color='FFFFFF',bold=True)
            cell.alignment=Alignment(wrap_text=True,vertical='center')
        for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=21
    ws=wb[title];headers=[c.value for c in ws[1]]
    if headers!=list(columns.values()):raise ValueError('Workbook header changed; refusing destructive remap: '+title)
    key_index=list(columns).index(key)
    seen={str(row[key_index]) for row in ws.iter_rows(min_row=2,values_only=True)}
    added=0
    for record in records:
        identity=str(record[key])
        if identity in seen:continue
        seen.add(identity);ws.append([safe(record.get(k)) for k in columns]);added+=1
        for ix,k in enumerate(columns,1):
            if k in ['signed_difference','highest_difference','lowest_difference']:
                ws.cell(ws.max_row,ix).number_format='+#,##0;-#,##0;0'
    ws.auto_filter.ref=ws.dimensions
    return added


def build_workbook(store,day,run_id,audit,state_dir,restore_dir='restored'):
    state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True)
    dest=state_dir/'papimo.xlsx'
    if not dest.exists():
        previous=list(Path(restore_dir).rglob('papimo.xlsx')) if Path(restore_dir).exists() else []
        if len(previous)>1:raise ValueError('Multiple canonical GitHub workbooks; refusing arbitrary overwrite')
        if previous:shutil.copy2(previous[0],dest)
    wb=load_workbook(dest) if dest.exists() else Workbook()
    if not dest.exists():wb.remove(wb.active)
    # Bootstrap every saved revision, including superseded snapshots and history series.
    all_records=[json.loads(r[0]) for r in store.db.execute('SELECT payload FROM snapshots ORDER BY rowid')]
    current_records=[r for d in store.days() for r in store.current(d)]
    histories=[];exceptions=[]
    for record in all_records:
        for ix,entry in enumerate(store.history(record),1):
            histories.append({**entry,'key':record['history_id']+':'+str(ix),'history_id':record['history_id'],
                'business_date':record['business_date'],'machine':record['machine'],
                'machine_code':record.get('machine_code'),'row':entry.get('row',ix)})
        for ix,entry in enumerate(record.get('missing_items',[])):
            exceptions.append({**entry,'key':record['snapshot_id']+':'+str(ix),
                'snapshot_id':record['snapshot_id'],'business_date':record['business_date'],'machine':record['machine']})
    stats={
      '台データ':append_rows(wb,'台データ',MACHINE_COLUMNS,all_records,'snapshot_id'),
      '大当たり履歴':append_rows(wb,'大当たり履歴',HISTORY_COLUMNS,histories,'key'),
      '取得状況':append_rows(wb,'取得状況',EXCEPTION_COLUMNS,exceptions,'key'),
      '実行履歴':append_rows(wb,'実行履歴',RUN_COLUMNS,[{**audit,'run_id':run_id,'business_date':day,
          'note':'自動推定は目視確認・サイト実測と区別。空欄と符号保留は例外として保存。Library既存Excelは変更しない。'}],'run_id')}
    if '運用定義' not in wb.sheetnames:
        ws=wb.create_sheet('運用定義')
        for row in [('項目','定義'),('収集方式','GitHub自動収集 v2：収集・軸校正・画像推定・Excel・検証・保存'),
          ('差枚','0枚基準の当日グラフ画像推定。最大出メダルを差枚に流用しない。'),
          ('自動処理成功','基本値・履歴照合・グラフ数値化・Excel検証・状態保存が終了。全項目充足とは別判定。'),
          ('欠測','元サイト空欄は空欄のまま保持。0で補完しない。符号が誤差内なら保留。'),
          ('旧記録','既存SQLiteと本Excelの行・シートを保持して新しい記録IDを追記。訂正前は削除しない。'),
          ('保存先','GitHub Actions Artifacts。既存Library Excel・分析定義・予測履歴は変更しない。')]:ws.append(row)
        ws.column_dimensions['A'].width=20;ws.column_dimensions['B'].width=100
    before={ws.title:ws.max_row for ws in wb}
    temp=dest.with_suffix('.tmp.xlsx');wb.save(temp);wb.close()
    check=load_workbook(temp,read_only=True,data_only=False)
    if {ws.title:ws.max_row for ws in check}!=before:raise ValueError('Workbook roundtrip row counts differ')
    ids={r[0] for r in check['台データ'].iter_rows(min_row=2,values_only=True)}
    if not all(r['snapshot_id'] in ids for r in all_records):raise ValueError('Workbook snapshot coverage mismatch')
    history_ids={r[0] for r in check['大当たり履歴'].iter_rows(min_row=2,values_only=True)}
    if not all(r['key'] in history_ids for r in histories):raise ValueError('Workbook history coverage mismatch')
    check.close();temp.replace(dest)
    return {'file':'state/papimo.xlsx','sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),
            'verified':True,'snapshot_ids':sorted(r['snapshot_id'] for r in all_records),
            'rows':before,'appended':stats,
            'current_history_rows':sum(len(store.history(r)) for r in current_records)}
