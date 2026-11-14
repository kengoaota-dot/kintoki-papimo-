"""Apply Work-confirmed readings to saved numeric state without fetching PAPIMO again."""
import argparse
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
import collect_papimo as collector
from collection_store import Store
from definition_pipeline import quality,exports,read_reviews
from graph_features import apply_review


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact-dir',required=True)
    parser.add_argument('--date',required=True)
    parser.add_argument('--reviews-dir',default='reviews')
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    source=list(Path(args.artifact_dir).rglob('papimo.sqlite3'))
    if len(source)!=1:raise ValueError('Exactly one saved numeric ledger is required')
    output=Path(args.out)
    output.mkdir(parents=True,exist_ok=False)
    copied=output/'state'/'papimo.sqlite3'
    copied.parent.mkdir()
    shutil.copy2(source[0],copied)
    store=Store(copied)
    rows=store.current(args.date)
    if not rows:raise ValueError('Requested business date absent; no initialization performed')
    reviews=read_reviews(args.reviews_dir,args.date)
    run_id='work-review-'+uuid.uuid4().hex
    results=[]
    for data in rows:
        data=quality(dict(data))
        review=reviews.get(str(data['machine']))
        if review:
            # A confirmation must bind to the exact image fingerprint and machine/date/model.
            features=apply_review(data.get('graph_features') or {'graph_sha256':data.get('graph_sha256')},
                review,data['machine'],args.date,data.get('machine_code'))
            data={**data,**features,'graph_features':features,
                  'collected_at_jst':datetime.now(collector.JST).isoformat()}
            data=quality(data)
            data,_=store.put(data,store.history(data),run_id)
        results.append(data)
    audit=exports(collector,store,args.date,output,results,run_id,args.reviews_dir)
    store.close()
    print(json.dumps(audit,ensure_ascii=False))
    return 0 if audit['definition_complete'] else 2


if __name__=='__main__':raise SystemExit(main())
