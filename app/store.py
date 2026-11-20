import sqlite3, json
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS withdrawals (run_id TEXT NOT NULL, instruction_ref TEXT NOT NULL, result TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY(run_id, instruction_ref))")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def save_withdrawal(path, run_id, instruction_ref, result):
    """同一批次+同一撤关指令只保留一套结果：重试不新增第二套，只累加尝试次数。"""
    with connect(path) as con:
        if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
            raise BusinessError('NOT_FOUND','批次不存在',404)
        con.execute("INSERT INTO withdrawals(run_id,instruction_ref,result) VALUES(?,?,?) ON CONFLICT(run_id,instruction_ref) DO UPDATE SET attempts=attempts+1",
                    (run_id,instruction_ref,json.dumps(result,ensure_ascii=False)))
        row=con.execute('SELECT result,attempts,created_at FROM withdrawals WHERE run_id=? AND instruction_ref=?',(run_id,instruction_ref)).fetchone()
    stored=json.loads(row['result']);stored['attempts']=row['attempts'];stored['created_at']=row['created_at']
    return stored

def get_withdrawal(path, run_id, instruction_ref=None):
    with connect(path) as con:
        if instruction_ref is None:
            rows=con.execute('SELECT instruction_ref,result,attempts,created_at FROM withdrawals WHERE run_id=? ORDER BY rowid',(run_id,)).fetchall()
            out=[]
            for r in rows:
                item=json.loads(r['result']);item['attempts']=r['attempts'];item['created_at']=r['created_at'];out.append(item)
            return out
        row=con.execute('SELECT result,attempts,created_at FROM withdrawals WHERE run_id=? AND instruction_ref=?',(run_id,instruction_ref)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','该撤关指令尚未形成证明',404)
    stored=json.loads(row['result']);stored['attempts']=row['attempts'];stored['created_at']=row['created_at']
    return stored
