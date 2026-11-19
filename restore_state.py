"""Restore cumulative state without selecting a regressed retry artifact."""
import json
import os
import subprocess
import sqlite3
import shutil
import tempfile
from pathlib import Path


def inspect_state(folder):
    paths=list(folder.rglob('papimo.sqlite3'))
    if len(paths)!=1:raise ValueError('expected one SQLite ledger')
    with sqlite3.connect(str(paths[0])) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise ValueError('SQLite integrity failure')
        coverage=set(db.execute('SELECT DISTINCT day,machine FROM snapshots'))
        if not coverage:raise ValueError('empty ledger')
        ids=set(db.execute('SELECT id FROM snapshots'))
    return coverage,ids


def main():
    repository=os.environ['GITHUB_REPOSITORY']
    current=int(os.environ['GITHUB_RUN_ID'])
    attempt=int(os.environ.get('GITHUB_RUN_ATTEMPT','1'))
    artifacts=json.loads(subprocess.check_output(['gh','api','--paginate','--slurp',
        f'repos/{repository}/actions/artifacts?per_page=100'],text=True))
    artifacts=[a for page in artifacts for a in page['artifacts']]
    candidates=[a for a in artifacts if not a['expired'] and (
        a.get('workflow_run',{}).get('id')!=current or
        (a['name'].startswith(f'papimo-state-{current}-') and
         a['name'].rsplit('-',1)[-1].isdigit() and int(a['name'].rsplit('-',1)[-1])<attempt))]
    states=sorted([a for a in candidates if a['name'].startswith('papimo-state-')],
        key=lambda a:a['created_at'],reverse=True)
    legacy=sorted([a for a in candidates if a['name'].startswith('papimo-') and not a['name'].startswith('papimo-state-')],
        key=lambda a:a['created_at'],reverse=True)
    destination=Path('restored')
    if destination.exists():raise RuntimeError('restore destination already exists')
    def download(a,folder):
        subprocess.run(['gh','run','download',str(a['workflow_run']['id']),'--repo',repository,
            '--name',a['name'],'--dir',str(folder)],check=True)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        valid=[]
        for a in states:
            folder=root/str(a['id'])
            try:
                download(a,folder)
                coverage,ids=inspect_state(folder)
                valid.append((a,folder,coverage,ids))
            except Exception as exc:
                print(f"WARNING: rejected artifact {a['id']}: {exc}",flush=True)
        if states:
            if not valid:raise RuntimeError('No valid cumulative SQLite artifact')
            union_coverage=set().union(*(v[2] for v in valid))
            union_ids=set().union(*(v[3] for v in valid))
            eligible=[v for v in valid if v[2]==union_coverage and v[3]==union_ids]
            if not eligible:
                raise RuntimeError('Divergent cumulative ledgers; refusing data loss; explicit merge required')
            a,folder,coverage,ids=max(eligible,key=lambda v:v[0]['created_at'])
            shutil.copytree(folder,destination)
            mode='state'
            detail={'latest_business_date':max(d for d,m in coverage),'machine_days':len(coverage),
                    'snapshots':len(ids),'valid_candidates':len(valid)}
        elif legacy:
            a=legacy[0]
            download(a,destination)
            mode='legacy_bootstrap'
            detail={}
        else:raise RuntimeError('No restorable prior artifact; refusing initialization')
    (destination/'restore_manifest.json').write_text(json.dumps({'artifact_id':a['id'],
        'source_run_id':a['workflow_run']['id'],'mode':mode,**detail},indent=2))
    print('Restored cumulative state: '+str(a['id']),flush=True)


if __name__=='__main__':main()
