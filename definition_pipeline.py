"""Definition v1.1 + optimization v1.2. Separate acquisition from completion."""
import argparse
import csv
import hashlib
import json
import os
import shutil
import time
from datetime import datetime,timedelta
from pathlib import Path
from collection_store import Store,write_csv
from graph_features import extract_candidates,apply_review,automatic_estimate,GRAPH_METHOD_VERSION


BASIC_FIELDS=['bb','rb','total_start','final_start','max_payout','machine_name','machine_code']
DAILY_FIELDS=['business_date','machine','machine_name','machine_code','bb','rb','bb_probability',
    'rb_probability','combined_probability','total_start','final_start','max_payout',
    'signed_difference','highest_difference','lowest_difference','max_range',
    'rounding_unit','estimated_error','sign_judgment','difference_source','reading_status',
    'updated_at','graph_updated_at_jst','collected_at_jst','source_url','graph_url',
    'history_count','history_count_matches','source_missing_fields','missing_items','status','snapshot_id']
HISTORY_FIELDS=['business_date','machine','machine_code','row','count','time','start','payout','type']


def reusable_graph(old,data,graph_hash):
    if not old or not data.get('machine_code') or not data.get('updated_at'):return False
    # PAPIMO's page timestamp advances globally even for historical dates.
    # Re-read the page metadata, but an exact image hash proves the graph content is unchanged.
    invariant=['business_date','machine','machine_code','graph_url','bb','rb','total_start','final_start','max_payout']
    features=old.get('graph_features') or {}
    version_ok=(features.get('reading_status')=='work_visual_confirmed' or features.get('method')==GRAPH_METHOD_VERSION)
    return bool(version_ok and old.get('graph_sha256')==graph_hash and all(old.get(k)==data.get(k) for k in invariant))


def inventory(collector,store):
    from bs4 import BeautifulSoup
    import re
    url=f'https://papimo.jp/h/{collector.HALL_ID}/hit/index_machine/1-20-1684287/'
    soup=BeautifulSoup(collector.fetch(url),'html.parser')
    models=[]
    for a in soup.select('a[href*="index_sort/"]'):
        name=a.select_one('.name')
        count=a.select_one('.count')
        if name and count and 'ジャグラー' not in name.get_text():
            models.append({'name':name.get_text(strip=True),'count':int(re.sub(r'\D','',count.get_text())),
                           'url':collector.urljoin(url,a['href'])})
    if not models:raise ValueError('machine_inventory_unavailable')
    signature=hashlib.sha256(json.dumps(models,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    row=store.db.execute('SELECT value FROM meta WHERE key=?',('machine_inventory',)).fetchone()
    saved=json.loads(row[0]) if row else None
    if saved and saved['signature']==signature:return saved['machines']
    machines={}
    for model in models:
        page=BeautifulSoup(collector.fetch(model['url']),'html.parser')
        found=set()
        for a in page.select('a[href]'):
            match=re.search(r'/hit/view/(\d+)(?:/|$)',a['href'])
            if match:found.add(int(match.group(1)))
        if len(found)!=model['count']:raise ValueError('inventory_count_mismatch:'+model['name'])
        for m in found:
            if m in machines:raise ValueError('duplicate_machine_in_inventory')
            machines[m]=model['name']
        time.sleep(.2)
    record={'signature':signature,'models':models,'machines':sorted(machines),'checked_at_jst':datetime.now(collector.JST).isoformat()}
    store.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('machine_inventory',json.dumps(record,ensure_ascii=False)))
    store.db.commit()
    return record['machines']


def read_reviews(root,day):
    path=Path(root)/(day+'.json')
    if not path.exists():return {}
    rows=json.loads(path.read_text(encoding='utf-8'))
    return {str(r['machine']):r for r in rows}


def metadata(raw,data):
    from bs4 import BeautifulSoup
    import re,unicodedata
    soup=BeautifulSoup(raw,'html.parser')
    code=next((re.search(r'/(?:ps|index_sort)/(\d+)',a.get('href','')).group(1)
               for a in soup.select('a[href]') if re.search(r'/(?:ps|index_sort)/(\d+)',a.get('href',''))),None)
    data['machine_code']=code
    panel=soup.select_one('#tab-data-today')
    mapping={'BB確率':'bb_probability','RB確率':'rb_probability','合成確率':'combined_probability'}
    for key in mapping.values():data[key]=None
    if panel:
        for cell in panel.select('td'):
            p=cell.find('p')
            if not p:continue
            label=unicodedata.normalize('NFKC',cell.get_text(strip=True)).split(p.get_text(strip=True))[0]
            if label in mapping:data[mapping[label]]=p.get_text(strip=True)
    data['probability_status']={k:'page_display' if data[k] is not None else 'not_displayed' for k in mapping.values()}
    return data


def quality(data):
    missing=[]
    for key in BASIC_FIELDS:
        if data.get(key) is None:missing.append({'item':key,'reason':'not_available_in_page'})
    if not data.get('history_count_matches'):
        missing.append({'item':'history','reason':'BB_RB_or_type_count_mismatch'})
    for field in data.get('source_missing_fields',[]):
        missing.append({'item':'history.'+field['field'],'row':field['row'],'reason':'source_blank'})
    accepted=(data.get('reading_status')=='work_visual_confirmed' or
              (data.get('collection_mode')=='github_automatic' and data.get('reading_status')=='automatic_image_estimate'))
    if not accepted:
        missing.append({'item':'signed_difference','reason':'Work_visual_confirmation_pending'})
    if data.get('sign_judgment')=='uncertain_near_zero':
        missing.append({'item':'difference_sign','reason':'uncertain_near_zero_within_error'})
    if data.get('curve_clipped'):
        missing.append({'item':'graph_extrema','reason':'curve_touches_plot_limit; extrema_are_bounds'})
    for error in data.get('errors',[]):
        missing.append({'item':'collection','reason':error})
    data['missing_items']=missing
    data['status']='complete' if not missing else 'partial'
    data['field_sources']={k:('page_display' if data.get(k) is not None else 'unavailable') for k in BASIC_FIELDS}
    data['field_sources']['signed_difference']=data.get('difference_source','unavailable')
    return data


def one(collector,machine,day,store,reviews,run_id,output):
    started=datetime.now(collector.JST).isoformat()
    data={'hall_id':collector.HALL_ID,'machine':machine,'business_date':day,
          'collected_at_jst':started,'errors':[],'source_missing_fields':[],
          'signed_difference':None,'difference_source':'unavailable','reading_status':'unavailable'}
    history=[]
    data['source_url']=f'https://papimo.jp/h/{collector.HALL_ID}/hit/view/{machine}/{day.replace("-","")}'
    old=store.latest(day,machine)
    try:
        raw=collector.fetch(data['source_url'])
        parsed,history=collector.parse_page(raw,machine,day)
        data.update(parsed)
        metadata(raw,data)
        data['source_url']=f'https://papimo.jp/h/{collector.HALL_ID}/hit/view/{machine}/{day.replace("-","")}'
        data['page_sha256']=hashlib.sha256(raw).hexdigest()
        data['page_collected_at_jst']=started
        for row in history:row['machine_code']=data['machine_code']
        if data.get('graph_url'):
            image=collector.fetch(data['graph_url'])
            collector.validate_png(image)
            data['graph_collected_at_jst']=datetime.now(collector.JST).isoformat()
            graph_hash=hashlib.sha256(image).hexdigest()
            reusable=reusable_graph(old,data,graph_hash)
            if reusable and old.get('graph_features'):
                features=dict(old['graph_features'])
                data['graph_reuse_reason']='same_day_model_URL_counters_and_exact_image_sha256; page_update_checked_separately'
            else:
                try:features=extract_candidates(image)
                except Exception as exc:
                    features={'graph_sha256':graph_hash,'reading_status':'candidate_failed',
                              'difference_source':'unavailable','graph_candidate_error':str(exc)}
            # Image bytes are intentionally transient. No PNG, thumbnail, or ZIP copy is written.
            del image
            review=reviews.get(str(machine))
            if review:
                try:features=apply_review(features,review,machine,day,data['machine_code'])
                except ValueError as exc:
                    if os.getenv('PAPIMO_AUTOMATIC')=='1':data['review_note']='stale review ignored: '+str(exc)
                    else:data['errors'].append(str(exc))
            if os.getenv('PAPIMO_AUTOMATIC')=='1':
                features=automatic_estimate(features)
                data['collection_mode']='github_automatic'
            data.update(features)
            data['graph_features']=features
            data['graph_status']='downloaded_and_verified_transient'
        else:data['errors'].append('today_graph_missing')
    except Exception as exc:
        data['errors'].append(type(exc).__name__+': '+str(exc))
    data['checked_at_jst']=datetime.now(collector.JST).isoformat()
    quality(data)
    saved,additions=store.put(data,history,run_id)
    folder=output/str(machine)
    folder.mkdir(parents=True,exist_ok=True)
    collector.save_json(folder/'machine.json',saved)
    write_csv(folder/'history.csv',history,HISTORY_FIELDS)
    write_csv(folder/'history_delta.csv',additions,HISTORY_FIELDS)
    print(json.dumps({'machine':machine,'status':saved['status'],'history_count':saved.get('history_count'),
          'difference_read':saved.get('reading_status'),'source_missing':len(saved.get('source_missing_fields',[])),
          'errors':saved['errors']},ensure_ascii=False),flush=True)
    return saved


def exports(collector,store,day,output,results,run_id,reviews_dir='reviews'):
    write_csv(output/'daily_machines.csv',results,DAILY_FIELDS)
    features=[{k:r.get(k) for k in DAILY_FIELDS if k not in ['bb','rb','total_start','final_start','max_payout']} for r in results]
    write_csv(output/'graph_features.csv',features,list(features[0]) if features else [])
    index=[{k:r.get(k) for k in ['business_date','machine','machine_code','snapshot_id','updated_at','collected_at_jst',
        'bb','rb','history_count','history_id','graph_sha256','reading_status','signed_difference','status']} for r in results]
    collector.save_json(output/'lightweight_index.json',index)
    queue=[{'business_date':r['business_date'],'machine':r['machine'],'machine_code':r.get('machine_code'),
        'snapshot_id':r['snapshot_id'],
        'graph_sha256':r.get('graph_sha256'),'source_url':r.get('source_url'),'graph_url':r.get('graph_url'),
        'missing_items':r['missing_items'],'resume_items':[m['item'] for m in r['missing_items']]} for r in results if r['missing_items']]
    collector.save_json(output/'exception_queue.json',queue)
    collector.save_json(output/'collection_summary.json',results)
    reviews=[{k:r.get(k) for k in ['business_date','machine','machine_code','graph_sha256','graph_url',
        'candidate_signed_difference','candidate_highest','candidate_lowest','candidate_max_range',
        'estimated_error','rounding_unit','sign_candidate','curve_clipped','zero_line_y','axis_labels','last_point']} for r in results
        if r.get('reading_status')!='work_visual_confirmed']
    collector.save_json(output/'work_review_queue.json',reviews)
    all_days=[r for d in store.days() for r in store.current(d)]
    all_history=[]
    for record in all_days:
        all_history.extend([{**r,'snapshot_id':record['snapshot_id']} for r in store.history(record)])
    collector.save_json(output/'excel_input.json',{'business_date':day,'snapshot_ids':sorted(r['snapshot_id'] for r in results),
        'sheets':{'台データ集計':all_days,'履歴':all_history,'グラフ特徴量':features,'取得状況・エラーログ':queue},
        'note':'Numeric export input for the Work workbook adapter; does not overwrite the Library canonical workbook.'})
    signed=sum(r.get('reading_status')=='work_visual_confirmed' for r in results)
    complete=sum(r['status']=='complete' for r in results)
    receipt_path=Path(reviews_dir)/'receipts'/(day+'.json')
    receipt=json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
    workbook_saved=(receipt.get('business_date')==day and receipt.get('writeback_verified') is True
        and receipt.get('snapshot_ids')==sorted(r['snapshot_id'] for r in results)
        and bool(receipt.get('library_file_id')) and bool(receipt.get('version_id')))
    audit={'business_date':day,'required_machines':len(results),'work_confirmed_difference':signed,
        'fully_complete':complete,'history_count_matches':sum(bool(r.get('history_count_matches')) for r in results),
        'graph_candidates':sum(r.get('reading_status') in ['candidate_unreviewed','work_visual_confirmed'] for r in results),
        'clipped_graphs':sum(bool(r.get('curve_clipped')) for r in results),
        'source_missing_fields':sum(len(r.get('source_missing_fields',[])) for r in results),
        'graph_reused':sum(bool(r.get('graph_reuse_reason')) for r in results),
        'images_persisted':0,'numeric_state_saved':True,'excel_status':'writeback_verified' if workbook_saved else 'work_adapter_required',
        'definition_complete':complete==len(results) and signed==len(results) and workbook_saved,
        'completion_reason':'Work workbook writeback is a separate required phase; never implied by Actions success',
        'run_id':run_id}
    collector.save_json(output/'audit.json',audit)
    pending=[]
    if signed<len(results):pending.append('Work graph confirmation')
    if audit['source_missing_fields']:pending.append('source-side missing values')
    uncertain=sum(r.get('sign_judgment')=='uncertain_near_zero' for r in results)
    if uncertain:pending.append('near-zero sign uncertainty')
    if not workbook_saved:pending.append('canonical workbook writeback')
    if any(r.get('errors') for r in results):pending.append('collection errors')
    if complete<len(results) and not pending:pending.append('required field exceptions')
    status='COMPLETE' if audit['definition_complete'] else 'PARTIAL: '+', '.join(pending)
    report=f"Business date: {day}\n\nWork-confirmed signed differences: {signed}/{len(results)}\n\nCanonical Excel status: {audit['excel_status']}\n\nFully complete machine rows: {complete}/{len(results)}\n\nHistory BB/RB matches: {audit['history_count_matches']}/{len(results)}\n\nSource-side missing fields: {audit['source_missing_fields']}\n\nNear-zero signs held open: {uncertain}\n\nStatus: {status}\n\nNo graph images are persisted.\n"
    (output/'validation_report.md').write_text(report,encoding='utf-8')
    if os.getenv('PAPIMO_AUTOMATIC')=='1':
        from github_pipeline import finalize_export
        return finalize_export(collector,store,day,output,results,run_id,audit)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as stream:stream.write(report)
    return audit


def run(collector):
    parser=argparse.ArgumentParser()
    parser.add_argument('--scope',choices=['test','all'],default='all')
    parser.add_argument('--date',default=os.getenv('PAPIMO_BUSINESS_DATE',''))
    parser.add_argument('--state-dir',default='state')
    parser.add_argument('--restore-dir',default='restored')
    parser.add_argument('--reviews-dir',default='reviews')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--backfill-limit',type=int,default=10)
    parser.add_argument('--backfill-plan',default='backfill_plan.json')
    parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args()
    if args.self_test:
        collector.self_test()
        return 0
    now=datetime.now(collector.JST)
    day=args.date or (now-timedelta(days=int(now.hour<10))).date().isoformat()
    datetime.strptime(day,'%Y-%m-%d')
    run_id=os.getenv('GITHUB_RUN_ID',now.strftime('%Y%m%dT%H%M%S%f'))+'-'+os.getenv('GITHUB_RUN_ATTEMPT','1')
    output=Path('papimo_collection')/day/run_id
    output.mkdir(parents=True,exist_ok=False)
    state_path=Path(args.state_dir)/'papimo.sqlite3'
    restored=list(Path(args.restore_dir).rglob('papimo.sqlite3')) if Path(args.restore_dir).exists() else []
    if not state_path.exists() and restored:
        state_path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(restored[0],state_path)
    store=Store(state_path)
    if not store.days():
        imported=store.bootstrap_legacy(args.restore_dir)
        if not imported:
            raise RuntimeError('No prior state or legacy records restored; refusing silent reinitialization')
    collector.save_json(Path(args.state_dir)/'state_health.json',{'restored':True,'days':store.days(),'run_id':run_id})
    machines=collector.TEST_MACHINES if args.scope=='test' else inventory(collector,store)
    reviews=read_reviews(args.reviews_dir,day)
    results=[]
    for machine in machines:
        old=store.latest(day,machine)
        if args.resume and old and old.get('status')=='complete':
            results.append(old)
            continue
        results.append(one(collector,machine,day,store,reviews,run_id,output))
        if len(results)%10==0 or len(results)==len(machines):
            collector.save_json(output/'checkpoint.json',{'day':day,'run_id':run_id,'processed':[r['machine'] for r in results],
                'remaining':[m for m in machines if m not in [r['machine'] for r in results]],
                'exceptions':[{'machine':r['machine'],'items':r['missing_items']} for r in results if r['missing_items']]})
        time.sleep(2)
    backfill_queue=[]
    backfill_done=0
    plan_path=Path(args.backfill_plan)
    plan=json.loads(plan_path.read_text(encoding='utf-8')) if plan_path.exists() else []
    confirmed_missing={(entry['business_date'],int(entry['machine'])) for entry in plan
        if entry.get('confirmed_missing') is True and entry.get('canonical_index_checked_at_jst')}
    if args.scope=='all':
        first=datetime.strptime('2026-11-03','%Y-%m-%d').date()
        current=datetime.strptime(day,'%Y-%m-%d').date()
        for offset in range(1,max(0,(current-first).days)+1):
            past=(current-timedelta(days=offset)).isoformat()
            age=(now.date()-datetime.strptime(past,'%Y-%m-%d').date()).days
            for machine in machines:
                old=store.latest(past,machine)
                if old and old.get('status')=='complete':continue
                reason=('outside_daily_graph_public_window' if age>13 else
                    'Work_visual_confirmation_pending' if old and old.get('graph_sha256') else 'not_collected')
                backfill_queue.append({'business_date':past,'machine':machine,'reason':reason,
                    'history_expected_public':age<=3,'graph_expected_public':age<=13})
                if reason=='not_collected' and (past,machine) not in confirmed_missing:
                    backfill_queue[-1]['reason']='canonical_index_check_required; not_known_to_be_globally_missing'
                if reason=='not_collected' and (past,machine) in confirmed_missing and backfill_done<max(0,args.backfill_limit):
                    saved=one(collector,machine,past,store,read_reviews(args.reviews_dir,past),run_id,output/'backfill'/past)
                    backfill_queue[-1]['reason']='Work_visual_confirmation_pending' if saved.get('graph_sha256') else 'collection_failed'
                    backfill_queue[-1]['resume_items']=saved['missing_items']
                    backfill_done+=1
                    time.sleep(2)
    collector.save_json(output/'backfill_queue.json',backfill_queue)
    collector.save_json(output/'backfill_audit.json',{'processed_machine_days':backfill_done,
        'pending_machine_days':len(backfill_queue),'not_in_daily_completion_denominator':True})
    audit=exports(collector,store,day,output,results,run_id,args.reviews_dir)
    store.close()
    print(json.dumps(audit,ensure_ascii=False),flush=True)
    # Nonzero is intentional: candidates or source gaps must never look like complete collection.
    return 0 if audit.get('automation_complete',audit['definition_complete']) else 2
