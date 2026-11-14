"""Finish every acquisition/export step on GitHub; external Work/Library is optional."""
import json
import os
from pathlib import Path
from github_workbook import build_workbook


def finalize_export(collector,store,day,output,results,run_id,legacy_audit):
    accepted={'work_visual_confirmed','automatic_image_estimate'}
    failures=[]
    source_history_mismatches=[]
    for r in results:
        # BB/RB can advance before the site's history row is published (e.g. an active bonus).
        # Preserve both source values and the mismatch; never fabricate the absent row.
        fatal=[e for e in r.get('errors',[]) if e!='history_count_or_type_mismatch']
        if fatal:failures.append({'machine':r['machine'],'errors':fatal})
        if not r.get('history_count_matches'):
            source_history_mismatches.append({'machine':r['machine'],'bb':r.get('bb'),'rb':r.get('rb'),
                'history_count':r.get('history_count'),'history_bb_count':r.get('history_bb_count'),
                'history_rb_count':r.get('history_rb_count'),'reason':'source_summary_history_disagree'})
        if r.get('reading_status') not in accepted:failures.append({'machine':r['machine'],'error':'graph_numeric_validation_failed'})
        for key in ['bb','rb','total_start','final_start','max_payout','machine_name','machine_code']:
            if r.get(key) is None:failures.append({'machine':r['machine'],'error':'required_basic_missing:'+key})
    if not results:failures.append({'error':'no_machines'})
    # Kept for compatibility with old consumers; accepted automatic estimates need no Work queue.
    collector.save_json(output/'work_review_queue.json',[])
    audit={**legacy_audit,'mode':'github_automatic_v2',
       'source_history_mismatches':source_history_mismatches,
       'graph_candidates':sum(r.get('reading_status') in accepted for r in results),
       'automatically_read':sum(r.get('reading_status')=='automatic_image_estimate' for r in results),
       'uncertain_signs':sum(r.get('sign_judgment')=='uncertain_near_zero' for r in results),
       'data_complete':all(r.get('status')=='complete' for r in results) and bool(results),
       'technical_failures':failures,'automation_complete':False,'excel_status':'pending_github_export'}
    try:
        receipt=build_workbook(store,day,run_id,audit,store.path.parent)
        collector.save_json(store.path.parent/'workbook_receipt.json',receipt)
        collector.save_json(output/'workbook_receipt.json',receipt)
        audit.update(excel_status='github_workbook_verified',workbook={k:v for k,v in receipt.items() if k!='snapshot_ids'})
    except Exception as exc:
        failures.append({'error':'workbook_export_failed','detail':str(exc)})
        audit['excel_status']='github_export_failed'
    audit['automation_complete']=not failures and audit['excel_status']=='github_workbook_verified'
    audit['definition_complete']=audit['automation_complete'] and audit['data_complete']
    audit['completion_reason']='GitHub automation and data completeness are reported separately; source blanks and uncertain signs remain explicit.'
    collector.save_json(output/'audit.json',audit)
    collector.save_json(store.path.parent/'latest_audit.json',audit)
    report=(f"Business date: {day}\n\nGitHub automation: {'SUCCESS' if audit['automation_complete'] else 'FAILED'}\n\n"
       f"Processed machines: {len(results)}\n\nAutomatic image estimates: {audit['automatically_read']}\n\n"
       f"Previously Work-confirmed images: {audit['work_confirmed_difference']}\n\n"
       f"Excel: {audit['excel_status']} (state/papimo.xlsx)\n\n"
       f"History BB/RB matches: {audit['history_count_matches']}/{len(results)}\n\n"
       f"Source summary/history discrepancies: {len(source_history_mismatches)}\n\n"
       f"Source-side missing fields: {audit['source_missing_fields']}\n\nNear-zero signs held open: {audit['uncertain_signs']}\n\n"
       f"Data completeness: {'COMPLETE' if audit['data_complete'] else 'PARTIAL — exceptions are saved; no fabricated values'}\n\n"
       f"Technical failures: {len(failures)}\n\nNo Work execution, human review, API key or Library writeback is required. "
       "Download the artifact for cumulative SQLite, Excel, CSV, revision history and exception queues. Graph images are not persisted.\n")
    (output/'validation_report.md').write_text(report,encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write(report)
    if audit['source_missing_fields'] or audit['uncertain_signs'] or source_history_mismatches:
        print('::warning::Source blanks, summary/history discrepancies or near-zero signs remain in the saved exception queue; automation success does not mean complete source data.')
    return audit
