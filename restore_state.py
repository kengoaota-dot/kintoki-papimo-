"""Restore the latest persistent numeric state, or bootstrap existing v1 artifacts."""
import json
import os
import subprocess
from pathlib import Path


def main():
    repository=os.environ['GITHUB_REPOSITORY']
    current=int(os.environ['GITHUB_RUN_ID'])
    raw=subprocess.check_output(['gh','api',f'repos/{repository}/actions/artifacts?per_page=100'],text=True)
    artifacts=json.loads(raw)['artifacts']
    candidates=[a for a in artifacts if not a['expired'] and a.get('workflow_run',{}).get('id')!=current]
    states=sorted([a for a in candidates if a['name'].startswith('papimo-state-')],key=lambda a:a['created_at'],reverse=True)
    legacy=sorted([a for a in candidates if a['name'].startswith('papimo-') and not a['name'].startswith('papimo-state-')],key=lambda a:a['created_at'],reverse=True)
    selected=(states or legacy)
    if not selected:raise RuntimeError('No restorable prior artifact; refusing initialization without existing records')
    artifact=selected[0]
    destination=Path('restored')
    destination.mkdir(exist_ok=True)
    subprocess.run(['gh','run','download',str(artifact['workflow_run']['id']),'--repo',repository,
        '--name',artifact['name'],'--dir',str(destination)],check=True)
    (destination/'restore_manifest.json').write_text(json.dumps({'artifact_id':artifact['id'],
        'source_run_id':artifact['workflow_run']['id'],'mode':'state' if states else 'legacy_bootstrap'},indent=2))
    print('Restored previous numeric state' if states else 'Restored existing legacy records for non-destructive migration')


if __name__=='__main__':main()
