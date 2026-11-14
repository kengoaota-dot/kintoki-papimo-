"""Prepare an idempotent GitHub -> existing Japanese analysis-store merge.

Run in Work after materializing the current Library files. No credentials,
Library IDs, uploads, predictions, source collection or public-data writes here.
Publish prepared data first, then the index; keep Library version guards.
"""
import argparse
import collections
import copy
import csv
import hashlib
import json
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime,timezone,timedelta
from pathlib import Path
from collection_store import Store

JST=timezone(timedelta(hours=9))
WORKBOOK='金時京急蒲田_20261107_13時取得.xlsx'
INDEX='金時京急蒲田_高速分析索引.json'
STATUS='金時京急蒲田_PAPIMO148台_取得状況_最新.json'
RAW='金時京急蒲田_PAPIMO148台_再開用原記録.json'


def stamp(row):
    for key in ['集計取得JST','グラフ取得JST','差枚読取JST','取得JST','collected_at_jst']:
        v=row.get(key)
        if not v:continue
        try:
            d=datetime.fromisoformat(str(v).replace('/','-'))
            return (d if d.tzinfo else d.replace(tzinfo=JST)).timestamp()
        except (ValueError,TypeError):pass
    return 0


def numeric(value):
    if value is None or value=='':return None
    try:
        x=float(str(value).replace(',',''))
        return int(x) if math.isfinite(x) and x.is_integer() else x if math.isfinite(x) else None
    except (TypeError,ValueError):return None


def mapped(r):
    datetime.strptime(r['business_date'],'%Y-%m-%d')
    uncertain=r.get('sign_judgment')=='uncertain_near_zero'
    accepted=r.get('reading_status') in ['automatic_image_estimate','work_visual_confirmed']
    value=r.get('signed_difference') if accepted and not uncertain else None
    day=r['business_date'].replace('-','')
    f=r.get('graph_features') or {}
    reason=list(r.get('missing_items',[]))
    if uncertain:reason.append({'item':'分析用符号付き差枚','reason':'符号未確定のため参考値と分離'})
    return {'営業日':day,'台番号':r['machine'],'機種名':r.get('machine_name'),
      'PAPIMO機種コード':r.get('machine_code'),'PAPIMO更新JST':r.get('updated_at'),
      'グラフ更新JST':r.get('graph_updated_at_jst'),
      '差枚読取JST':r.get('reviewed_at_jst') or r.get('graph_collected_at_jst') or r.get('collected_at_jst'),
      '当日実差枚_推定':value,'最高差枚_推定':r.get('highest_difference'),
      '最低差枚_推定':r.get('lowest_difference'),'最大上下差枚_推定':r.get('max_range'),
      '当日符号付き差枚':f'{value:+g}' if value is not None else '',
      '符号判定':'保留' if uncertain else 'プラス' if value is not None and value>0 else 'マイナス' if value is not None and value<0 else '未確定',
      '差枚読取精度':('画像自動推定' if r.get('reading_status')=='automatic_image_estimate' else
                    '画像推定（既存目視確認）' if r.get('reading_status')=='work_visual_confirmed' else '未取得'),
      '差枚読取単位枚':r.get('rounding_unit'),'差枚読取誤差目安枚':r.get('estimated_error'),
      'BB':r.get('bb'),'RB':r.get('rb'),'BB確率':r.get('bb_probability'),'RB確率':r.get('rb_probability'),
      '合成確率':r.get('combined_probability'),'総スタート':r.get('total_start'),'最終スタート':r.get('final_start'),
      '最大出メダル_実差枚ではない':r.get('max_payout'),
      '集計取得JST':r.get('page_collected_at_jst') or r.get('collected_at_jst'),
      'グラフ取得JST':r.get('graph_collected_at_jst') or r.get('collected_at_jst'),
      '履歴照合':'一致' if r.get('history_count_matches') else '不一致',
      '履歴BIG':r.get('history_bb_count'),'履歴REG':r.get('history_rb_count'),
      'PAPIMO台URL':r.get('source_url'),'当日グラフURL':r.get('graph_url'),
      '掲載差枚_実測':None,'掲載差枚状態':'サイト実測ではなく画像からの推定',
      '取得方法':'GitHub自動収集 v2（Work目視確認とは区別）',
      '備考':'GitHub記録ID='+r['snapshot_id']+'。符号保留値は参考値欄に保持。',
      '未取得理由':json.dumps(reason,ensure_ascii=False) if reason else '',
      'GitHub記録ID':r['snapshot_id'],'画像SHA256':r.get('graph_sha256'),
      '履歴系列ID':r.get('history_id'),'値の出所':r.get('difference_source'),
      '自動読取差枚_参考':r.get('signed_difference'),
      '分析利用可':accepted and not uncertain,'全項目充足':r.get('status')=='complete',
      '時系列最大上昇_推定':f.get('candidate_max_chronological_rise'),
      '最高最低順序':f.get('extrema_order'),'原グラフ更新不明理由':r.get('graph_update_reason')}


def equivalent(old,new):
    # Keep established reviewed values when every actual data field agrees.
    pairs=['営業日','台番号','PAPIMO機種コード','BB','RB','総スタート','最終スタート',
           '最大出メダル_実差枚ではない','当日実差枚_推定','最高差枚_推定','最低差枚_推定']
    def same(k):
        if k in ['営業日','PAPIMO機種コード']:return str(old.get(k,''))==str(new.get(k,''))
        v=new.get(k)
        if k=='当日実差枚_推定' and v is None and numeric(old.get(k)) is not None:v=new.get('自動読取差枚_参考')
        return numeric(old.get(k))==numeric(v)
    return all(same(k) for k in pairs)


def prefer(old,new):
    if not old:return True
    if str(old.get('営業日',''))>new['営業日']:return False
    if str(old.get('営業日',''))<new['営業日']:return True
    if stamp(old)>stamp(new):return False
    return not equivalent(old,new)


def workbook_rows(path,wanted):
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as z:
        shared=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)] if 'xl/sharedStrings.xml' in z.namelist() else []
        rels={x.attrib['Id']:x.attrib['Target'] for x in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        out={}
        for sh in ET.fromstring(z.read('xl/workbook.xml')).find('s:sheets',ns):
            if sh.attrib['name'] not in wanted:continue
            target=rels[sh.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            rows=[]
            for row in ET.fromstring(z.read(target)).findall('s:sheetData/s:row',ns):
                cells={}
                for c in row:
                    ix=0
                    for letter in re.sub(r'\d','',c.attrib['r']):ix=ix*26+ord(letter)-64
                    v=c.find('s:v',ns);typ=c.attrib.get('t')
                    value=(''.join(c.find('s:is',ns).itertext()) if typ=='inlineStr' else
                           shared[int(v.text)] if typ=='s' and v is not None else v.text if v is not None else None)
                    cells[ix-1]=value
                rows.append([cells.get(i) for i in range(max(cells,default=-1)+1)])
            headers=rows[0]
            out[sh.attrib['name']]={'headers':headers,'rows':[{h:r[i] if i<len(r) else None for i,h in enumerate(headers) if h} for r in rows[1:]]}
        return out


def save_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def save_csv(path,rows,headers=None):
    headers=headers or list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=headers,extrasaction='ignore');w.writeheader();w.writerows(rows)


def prepare(artifact,library,out,run_id):
    library=Path(library);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    dbs=list(Path(artifact).rglob('papimo.sqlite3'))
    if len(dbs)!=1:raise ValueError('Exactly one GitHub numeric state is required')
    store=Store(dbs[0]);index=json.loads((library/INDEX).read_text());original_index=copy.deepcopy(index)
    seen=set(index.get('GitHub同期済み記録ID',[]));incoming=[r for day in store.days() for r in store.current(day)]
    needed=[r for r in incoming if r['snapshot_id'] not in seen]
    wb=workbook_rows(library/WORKBOOK,['差枚時刻別追記','履歴時刻別追記','グラフ再読取追記'])
    daily=index.setdefault('日別符号付き差枚_収集索引',{});latest=index.setdefault('latest',{});snaps=index.setdefault('snapshots',{})
    changed=[];kept=0;history=[];reasons=[]
    for source in needed:
        row=mapped(source);key=row['営業日']+'|'+str(row['台番号']);old=daily.get(key)
        if old and not prefer(old,row):
            kept+=1;reasons.append({'day':row['営業日'],'machine':row['台番号'],'reason':'existing_newer_or_equivalent_kept'});continue
        changed.append((source,row));history.extend([{**h,'営業日':row['営業日'],'台番号':row['台番号'],
              '原表示順':h.get('row',ix),'原回数':h.get('count') or None,'時刻':h.get('time'),
              '当たりG':numeric(h.get('start')),'出メダル':numeric(h.get('payout')),'種別':h.get('type'),
              '取得JST':row['集計取得JST'],'ページ更新JST':row['PAPIMO更新JST'],
              'GitHub記録ID':source['snapshot_id'],'履歴系列ID':source['history_id']}
              for ix,h in enumerate(store.history(source),1)])
        daily[key]={**row,'符号付き差枚':row['当日実差枚_推定'],'区分':row['差枚読取精度']}
        snaps[row['営業日']+'|'+str(row['台番号'])+'|GitHub|'+source['snapshot_id']]=row
        if prefer(latest.get(str(row['台番号'])),row):latest[str(row['台番号'])]=row
    sync_id=hashlib.sha256(json.dumps(sorted(r['snapshot_id'] for r in needed)).encode()).hexdigest()
    now=datetime.now(JST).isoformat();days=sorted({row['営業日'] for _,row in changed})
    report={'sync_id':sync_id,'github_run_id':str(run_id),'generated_jst':now,'incoming_current_rows':len(incoming),
       'new_rows':len(changed),'existing_kept':kept,'already_seen':len(incoming)-len(needed),
       'history_rows':len(history),'analysis_usable':sum(row['分析利用可'] for _,row in changed),
       'uncertain_signs':sum(not row['分析利用可'] for _,row in changed),
       'source_history_mismatches':[row['台番号'] for _,row in changed if row['履歴照合']!='一致'],
       'source_blank_fields':sum(len(s.get('source_missing_fields',[])) for s,_ in changed),
       'source_acquisition_range':([min(row['集計取得JST'] for _,row in changed),max(row['集計取得JST'] for _,row in changed)] if changed else []),
       'note':'同期時刻と取得時刻は別。営業中データを閉店確定値にしない。符号保留値は分析用差枚を空欄にして参考値を保持。'}
    workbook_batches=[]
    drows=[];grows=[]
    for s,row in changed:
        drows.append({**row,'掲載差枚状態':'画像自動推定（実測ではない）'})
        grows.append({'基準営業日':row['営業日'],'台番号':row['台番号'],'機種名':row['機種名'],
          'グラフ区分':'当日グラフ','対象営業日':row['営業日'],'符号付き右端差枚':row['当日実差枚_推定'],
          '最高差枚':row['最高差枚_推定'],'最低差枚':row['最低差枚_推定'],'最大上下差枚':row['最大上下差枚_推定'],
          '時系列最大上昇':row['時系列最大上昇_推定'],'読取単位枚':row['差枚読取単位枚'],
          '精度':row['差枚読取精度'],'誤差目安枚':row['差枚読取誤差目安枚'],
          '画像取得JST':row['グラフ取得JST'],'元URL':row['当日グラフURL'],'備考':row['備考']})
    for name,rows in [('差枚時刻別追記',drows),('グラフ再読取追記',grows),('履歴時刻別追記',history)]:
        headers=wb[name]['headers'];existing=wb[name]['rows']
        def identity(r):
            return json.dumps([str(r.get(k) or '') for k in headers],ensure_ascii=False)
        # Full source snapshot identity, not time-only/event-only deduplication.
        keys={identity(r) for r in existing};additions=[]
        for row in rows:
            normalized={h:row.get(h) for h in headers};key=identity(normalized)
            if key not in keys:keys.add(key);additions.append([normalized[h] for h in headers])
        workbook_batches.append({'sheet':name,'headers':headers,'start_row':len(existing)+1,'values':additions,
             'numeric_columns':[i for i,h in enumerate(headers) if any(t in str(h) for t in ['差枚','最高','最低','最大上下','時系列最大上昇','誤差'])]})
    save_json(out/'workbook_patch.json',{'sync_id':sync_id,'sheets':workbook_batches})
    outputs=[];day_reports={}
    for day in days:
        pairs=[(s,r) for s,r in changed if r['営業日']==day];rows=[r for _,r in pairs];hs=[h for h in history if h['営業日']==day]
        day_reports[day]={'営業日':day,'対象台数':len(rows),'基本値取得台数':len(rows),
          '当日差枚取得台数':sum(r['分析利用可'] for r in rows),'自動読取参考値取得台数':len(rows),
          '差枚未取得台':[r['台番号'] for r in rows if not r['分析利用可']],
          '履歴取得行数':len(hs),'履歴一致台数':sum(r['履歴照合']=='一致' for r in rows),
          '履歴不一致':[{'台番号':r['台番号'],'BB':r['BB'],'RB':r['RB'],'履歴BIG':r['履歴BIG'],'履歴REG':r['履歴REG']} for r in rows if r['履歴照合']!='一致'],
          'PAPIMO更新JST':sorted(set(r['PAPIMO更新JST'] for r in rows if r['PAPIMO更新JST'])),
          'グラフ更新JST':sorted(set(r['グラフ更新JST'] for r in rows if r['グラフ更新JST'])),
          '値区分':'画像自動推定。符号保留は分析差枚空欄。営業中は途中値であり閉店確定値ではない。',
          '更新JST':max(r['集計取得JST'] for r in rows),'同期JST':now,'完全収集':all(s.get('status')=='complete' for s,_ in pairs),
          '履歴掲載空欄':{'当たりG':sum(x['field']=='start' for s,_ in pairs for x in s.get('source_missing_fields',[])),
                        '出メダル':sum(x['field']=='payout' for s,_ in pairs for x in s.get('source_missing_fields',[]))},
          '保存先台別CSV':f'/SlotData/金時京急蒲田_{day}_PAPIMO台別.csv',
          '保存先履歴CSV':f'/SlotData/金時京急蒲田_{day}_PAPIMO履歴.csv',
          '保存先Excel':'/SlotData/'+WORKBOOK,'取得元':'GitHub Actions','GitHub実行ID':str(run_id)}
        full_rows={str(r['台番号']):r for r in rows}
        existing_csv=library/f'金時京急蒲田_{day}_PAPIMO台別.csv'
        if existing_csv.exists():
            for r in csv.DictReader(existing_csv.open(encoding='utf-8-sig')):
                old=full_rows.get(str(r['台番号']))
                if old is None or stamp(r)>stamp(old):full_rows[str(r['台番号'])]=r
        filename=f'金時京急蒲田_{day}_PAPIMO台別.csv';save_csv(out/filename,sorted(full_rows.values(),key=lambda r:int(r['台番号'])));outputs.append(filename)
        filename=f'金時京急蒲田_{day}_PAPIMO履歴.csv'
        # Source series revisions are all retained in the original ledger and Excel snapshots.
        prior=list(csv.DictReader((library/filename).open(encoding='utf-8-sig'))) if (library/filename).exists() else []
        ids={json.dumps(r,sort_keys=True,ensure_ascii=False) for r in prior}
        for r in hs:
            rr={k:r.get(k) for k in ['営業日','台番号','原表示順','原回数','時刻','当たりG','出メダル','種別','取得JST','ページ更新JST','GitHub記録ID','履歴系列ID']}
            if json.dumps(rr,sort_keys=True,ensure_ascii=False) not in ids:prior.append(rr)
        save_csv(out/filename,prior or [{}],headers=['営業日','台番号','原表示順','原回数','時刻','当たりG','出メダル','種別','取得JST','ページ更新JST','GitHub記録ID','履歴系列ID']);outputs.append(filename)
        filename=f'金時_軽量差分索引_{day}.json';save_json(out/filename,{'schema_version':'1.2-github-sync','営業日':day,
             'generated_jst':now,'report':day_reports[day],'machines':rows});outputs.append(filename)
        filename=f'金時京急蒲田_{day}_収集原記録.json';old=json.loads((library/filename).read_text()) if (library/filename).exists() else {}
        old.setdefault('GitHub同期スナップショット',[]).append({'report':report,'台別':rows,'履歴':hs,'original_machine_records':[s for s,_ in pairs]})
        save_json(out/filename,old);outputs.append(filename)
    if needed:
        index['GitHub同期済み記録ID']=sorted(seen|{r['snapshot_id'] for r in needed})
        index.setdefault('GitHub同期履歴',[]).append(report)
        index['generated_jst']=now;index['GitHub同期_最新']=report
        index['row_count_snapshots']=len(snaps);index['row_count_latest_machines']=len(latest)
        index['observed_days']=sorted(set(index.get('observed_days',[]))|set(days))
        index['status']='GitHub同期済み（自動画像推定・欠測/符号保留は別管理）'
        if days:
            index.setdefault('GitHub同期前の警告',[]).append(index.get('warning'))
            index['warning']='画像自動推定を目視・実測と区別。符号保留は分析差枚に採用しない。元サイトの空欄と履歴不一致は保持。'
            index.setdefault('旧進捗記録',[]).append({'同期前の最新収集':index.get('最新収集')})
            index['最新収集']=day_reports[max(days)]
            index['再取得_最新']=day_reports[max(days)]
        for filename in [RAW,STATUS]:
            obj=json.loads((library/filename).read_text());obj.setdefault('GitHub同期履歴',[]).append(report)
            obj['GitHub同期_最新']=report;obj['generated_jst']=now
            if days:
                obj.setdefault('旧進捗記録',[]).append({'同期前の最新収集':obj.get('最新収集')})
                obj['最新収集']=copy.deepcopy(index['最新収集'])
                obj['再取得_最新']=copy.deepcopy(index['最新収集'])
                obj['当日グラフ最新再読取']=copy.deepcopy(index['最新収集'])
            if filename==RAW:obj.setdefault('GitHub収集記録',[]).append({'report':report,'台別':[r for _,r in changed],
                      '原台別':[s for s,_ in changed],'履歴':history})
            else:
                obj.setdefault('営業日別収集記録',{}).update(day_reports)
                obj['履歴不一致']=[{'営業日':r['営業日'],'台番号':r['台番号'],'理由':r['未取得理由']} for _,r in changed if r['履歴照合']!='一致']
                obj['有効対象の差枚未取得']=[{'営業日':r['営業日'],'台番号':r['台番号'],'理由':r['未取得理由']} for _,r in changed if not r['分析利用可']]
                obj['再開位置']='GitHubの例外キューを参照。符号保留・元サイト空欄・履歴不一致を保持。全台再取得はしない。'
            for day in days:obj.setdefault('Work収集記録',[]).append({'収集':day_reports[day],
                '台別':[r for _,r in changed if r['営業日']==day],
                '履歴':[h for h in history if h['営業日']==day],'出所':'GitHubデータをWorkで同期（目視確認の実施を意味しない）'})
            save_json(out/filename,obj);outputs.append(filename)
        save_json(out/INDEX,index)
    save_json(out/'sync_report.json',{**report,'outputs_before_index':outputs,'index_output':INDEX if needed else None,
        'workbook_patch_rows':{x['sheet']:len(x['values']) for x in workbook_batches},'kept_records':reasons})
    store.close();return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifact-dir',required=True);p.add_argument('--library-dir',required=True)
    p.add_argument('--out',required=True);p.add_argument('--run-id',required=True);a=p.parse_args()
    print(json.dumps(prepare(a.artifact_dir,a.library_dir,a.out,a.run_id),ensure_ascii=False))
