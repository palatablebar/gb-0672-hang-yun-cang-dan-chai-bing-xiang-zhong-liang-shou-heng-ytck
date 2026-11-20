# 深度登记 航运舱单拆并箱与重量守恒

规模：中型。使用者：货代单证员。核心规则：主提单与分提单拆并形成有向无环关系，件数和净毛重逐层守恒。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 舱单采信 | 执行并复核舱单采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 包装映射 | 执行并复核包装映射 | `POST /api/packaging` | `app/modules/packaging/rules.py` | `tests/test_packaging.py` |
| M3 拆并编组 | 执行并复核拆并编组 | `POST /api/grouping` | `app/modules/grouping/rules.py` | `tests/test_grouping.py` |
| M4 危险品检查 | 执行并复核危险品检查 | `POST /api/hazard` | `app/modules/hazard/rules.py` | `tests/test_hazard.py` |
| M5 净毛重核验 | 执行并复核净毛重核验 | `POST /api/weight` | `app/modules/weight/rules.py` | `tests/test_weight.py` |
| M6 报关对账 | 执行并复核报关对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 版本封存 | 执行并复核版本封存 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |
| M8 撤关证明 | 船开后对已放行主单裁决撤关恢复 | `POST /api/withdrawal/{run_id}` | `app/modules/withdrawal/rules.py` | `tests/test_withdrawal.py`、`tests/test_withdrawal_api.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-31 | 撤关无证据整票恢复 | 仅凭余额等于快照就恢复整票 | 余额与来源须同时证明，无来源证据判 SOURCE_UNPROVEN | `app/modules/withdrawal/rules.py` | `tests/test_withdrawal.py::test_no_source_evidence_fails_even_when_balance_equals_snapshot` |
| TRAP-32 | 撤关覆盖量越界 | 把拆出货量也计成可恢复余额 | 覆盖量恒不超过当前有效余额，拆出递减、并入/补正递增 | `app/modules/withdrawal/rules.py` | `tests/test_withdrawal.py::test_split_cannot_cover_more_than_effective_balance` |
| TRAP-33 | 台账重算失衡仍恢复 | 补正打出负余额后照常恢复 | 捕获失衡事件，判 BALANCE_UNPROVEN 并保留旧版余额与原证明 | `app/modules/withdrawal/rules.py` | `tests/test_withdrawal.py::test_failure_keeps_old_balance_and_original_proof_when_ledger_breaks` |
| TRAP-34 | 重试产生第二套结果 | 同一撤关指令重复落库 | `withdrawals` 表按批次+指令幂等，重试仅累加尝试次数并比对封印 | `app/store.py`、`app/modules/withdrawal/handler.py` | `tests/test_withdrawal_api.py::test_retry_same_instruction_keeps_single_result` |
| TRAP-35 | 撤关污染原封存批次 | 在原 state 上回写余额与视图 | 撤关作为旁路派生，不改写原批次 views 与 manifest 哈希 | `app/modules/withdrawal/handler.py` | `tests/test_withdrawal_api.py::test_withdrawal_keeps_original_run_manifest_unchanged` |
| TRAP-01 | 旧编码回退 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_source` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_source` |
| TRAP-04 | 分段表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_segment_header` |
| TRAP-05 | 缺列隔离 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_column_isolation` |
| TRAP-14 | 编号冲突 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_conflict_all_isolated` |
| TRAP-18 | 空态三分 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_three_empty_states` |
| TRAP-15 | 吨与包装换算 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_pallet_real` |
| TRAP-07 | 空值哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_sentinel_real` |
| TRAP-06 | 全角千分位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_fullwidth_decimal` |
| TRAP-20 | 拆并依赖环 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/grouping/rules.py` | `tests/test_grouping.py::test_cycle_real` |
| TRAP-21 | 缺失父级 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/grouping/rules.py` | `tests/test_grouping.py::test_missing_parent_real` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。
