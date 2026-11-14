"""Append-only numeric ledger. Images never enter this database."""
import csv
import hashlib
import json
import sqlite3
import unicodedata
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def normalized_history(rows):
    return [[unicodedata.normalize('NFKC',str(r.get(k,''))).strip() for k in
             ['count','time','start','payout','type']] for r in rows]


def history_change(old, new):
    a,b=normalized_history(old),normalized_history(new)
    if a==b:
        return 'unchanged',[]
    if a and len(b)>=len(a) and b[len(b)-len(a):]==a:
        return 'append',new[:len(b)-len(a)]
    return ('first_full' if not a else 'full_correction'),new


class Store:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.executescript('''
          PRAGMA journal_mode=DELETE;
          CREATE TABLE IF NOT EXISTS snapshots(
            id TEXT PRIMARY KEY, day TEXT, machine INTEGER, machine_code TEXT,
            checked_at TEXT, payload TEXT NOT NULL, previous_id TEXT, change_reason TEXT);
          CREATE TABLE IF NOT EXISTS history_series(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS observations(run_id TEXT,day TEXT,machine INTEGER,
            snapshot_id TEXT,checked_at TEXT,reused INTEGER,PRIMARY KEY(run_id,day,machine));
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS snapshot_lookup ON snapshots(day,machine,checked_at);
        ''')
        self.db.commit()

    def latest(self,day,machine):
        row=self.db.execute('SELECT s.payload FROM observations o JOIN snapshots s ON s.id=o.snapshot_id WHERE o.day=? AND o.machine=? ORDER BY o.rowid DESC LIMIT 1',(day,machine)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self,record):
        if not record or not record.get('history_id'):
            return []
        row=self.db.execute('SELECT payload FROM history_series WHERE id=?',(record['history_id'],)).fetchone()
        return json.loads(row[0]) if row else []

    def put(self,data,history,run_id):
        data=dict(data)
        old=self.latest(data['business_date'],data['machine'])
        mode, additions=history_change(self.history(old),history)
        if old and old.get('machine_code')!=data.get('machine_code'):
            mode,additions='machine_change_full',history
        history_id=digest({'day':data['business_date'],'machine':data['machine'],
            'machine_code':data.get('machine_code'),'rows':normalized_history(history)})
        self.db.execute('INSERT OR IGNORE INTO history_series VALUES(?,?)',(history_id,json.dumps(history,ensure_ascii=False)))
        data['history_id']=history_id
        identity={k:v for k,v in data.items() if k not in ['collected_at_jst','checked_at_jst','page_collected_at_jst',
            'graph_collected_at_jst','graph_reuse_reason','page_sha256','snapshot_id','history_change','reused','previous_snapshot_id']}
        snapshot_id=digest(identity)
        reused=bool(old and old.get('snapshot_id')==snapshot_id)
        data.update(snapshot_id=snapshot_id,history_change=mode,reused=reused,
                    previous_snapshot_id=old.get('snapshot_id') if old and not reused else None)
        reason='unchanged_verified' if reused else mode
        self.db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?,?)',(
            snapshot_id,data['business_date'],data['machine'],data.get('machine_code'),data['collected_at_jst'],
            json.dumps(data,ensure_ascii=False),data['previous_snapshot_id'],reason))
        self.db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?)',(
            run_id,data['business_date'],data['machine'],snapshot_id,data['collected_at_jst'],int(reused)))
        self.db.commit()
        return data,additions

    def days(self):
        return [r[0] for r in self.db.execute('SELECT DISTINCT day FROM snapshots ORDER BY day')]

    def current(self,day):
        return [self.latest(day,r[0]) for r in self.db.execute('SELECT DISTINCT machine FROM snapshots WHERE day=? ORDER BY machine',(day,))]

    def bootstrap_legacy(self,directory):
        if self.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]:
            return 0
        total=0
        for path in sorted(Path(directory).rglob('machine.json')):
            data=json.loads(path.read_text(encoding='utf-8'))
            if not data.get('business_date') or not data.get('machine'):
                continue
            history_path=path.with_name('history.csv')
            rows=list(csv.DictReader(history_path.open(encoding='utf-8-sig',newline=''))) if history_path.exists() else []
            # Retain original flags as history; old PNG success is not definition completion.
            data['legacy_status']=data.pop('status',None)
            data['status']='partial_legacy_graph_not_read'
            data.setdefault('collected_at_jst','unknown')
            self.put(data,rows,'legacy-import')
            total+=1
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('legacy_import_count',str(total)))
        self.db.commit()
        return total

    def close(self):
        self.db.close()


def write_csv(path,rows,fields):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            converted={k:(json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v) for k,v in row.items()}
            for key in ['signed_difference','highest_difference','lowest_difference','candidate_signed_difference']:
                if isinstance(converted.get(key),(int,float)):
                    converted[key]=f'{converted[key]:+g}'
            # Prevent formula evaluation of source strings; signed numeric fields remain numeric text.
            for key,value in converted.items():
                if key not in ['signed_difference','highest_difference','lowest_difference','candidate_signed_difference'] and isinstance(value,str) and value[:1] in ['=','+','@']:
                    converted[key]="'"+value
            writer.writerow(converted)
