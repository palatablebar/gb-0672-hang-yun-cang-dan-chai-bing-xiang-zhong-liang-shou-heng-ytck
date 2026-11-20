from fastapi import APIRouter, Request, Body
from app.web import render
from app import store
from app.engine import evaluate
from app.errors import BusinessError
from app.modules.withdrawal.rules import apply as apply_withdrawal

router=APIRouter()

def _db():
    from app.main import DB_PATH
    return DB_PATH

@router.get('/withdrawal')
def page(request:Request):
    return render(request,'withdrawal')

@router.post('/api/withdrawal')
def demo(payload:dict=Body(default={})):
    """与其它模块一致的无参演示入口：建档→封存→在其上映射撤关裁决。"""
    db=_db()
    state=store.save(db,evaluate({'dataset':payload.get('dataset','dirty')}))
    instruction_ref=str(payload.get('instruction_ref') or 'WDL-BATCH-001')
    result=apply_withdrawal(state,{'instruction_ref':instruction_ref})['withdrawal']
    return store.save_withdrawal(db,state['manifest']['run_id'],instruction_ref,result)

@router.post('/api/withdrawal/{run_id}')
def withdraw(run_id:str,payload:dict=Body(default={})):
    db=_db()
    state=store.get(db,run_id)
    instruction_ref=str(payload.get('instruction_ref') or 'WDL-BATCH-001')
    # 在已封存批次的不可变结果上派生撤关裁决，不回写原批次
    result=apply_withdrawal(state,{'instruction_ref':instruction_ref})['withdrawal']
    # 同一指令重试：持久层保证只保留一套结果，仅累加尝试次数
    return store.save_withdrawal(db,run_id,instruction_ref,result)

@router.get('/api/withdrawal/{run_id}')
def list_withdrawals(run_id:str):
    return {'run_id':run_id,'withdrawals':store.get_withdrawal(_db(),run_id)}

@router.get('/api/withdrawal/{run_id}/{instruction_ref}')
def get_withdrawal(run_id:str,instruction_ref:str):
    return store.get_withdrawal(_db(),run_id,instruction_ref)

@router.post('/api/withdrawal/{run_id}/{instruction_ref}/retry')
def retry_withdrawal(run_id:str,instruction_ref:str):
    db=_db()
    # 重试同一撤关指令：重算后与原证明比对，封印一致则不新增第二套结果
    previous=store.get_withdrawal(db,run_id,instruction_ref)
    state=store.get(db,run_id)
    result=apply_withdrawal(state,{'instruction_ref':instruction_ref})['withdrawal']
    current=store.save_withdrawal(db,run_id,instruction_ref,result)
    seals=[r['certificate_seal'] for r in previous['rulings']]
    now=[r['certificate_seal'] for r in current['rulings']]
    current['idempotent']=(seals==now)
    if not current['idempotent']:
        raise BusinessError('WITHDRAWAL_DRIFT','重试结果与原证明不一致，已保留旧版余额与原证明',409)
    return current
