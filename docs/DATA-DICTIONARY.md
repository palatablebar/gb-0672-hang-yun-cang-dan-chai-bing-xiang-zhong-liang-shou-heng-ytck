# 舱单数据定义

id是主单、分组单或箱货明细唯一字符串编号，parent为空表示主单。节点申报件数、净重和毛重是其完整子树的申报总量，校验时只加直接子节点，避免多层重复计数。包装carton=1件，pallet=10件；重量kg与t统一为kg。件数必须非负整数，净重非负且毛重不得低于净重。
拆并结构采用每节点最多一个父级的有向图；自环、长环、缺父级均阻断封存，保留环路径。不会以平面求和替代逐层校验。
危险品按明确分类与UN编号检查，3类与5.1类禁止同箱；未知分类和缺失编号需复核。此演示规则只覆盖文中三类，不替代运输合规审批。
报关总量只汇总主单，与context.json内的申报件数、净重和毛重逐项比较。重复同值折叠，重复编号冲突全部隔离。原始计数=采纳+隔离+空行+重复表头+重复同值行。
封存需所有图、重量、危险品和采信问题处理完毕。数值均Decimal，对外字符串保留精度，编号不转整数。

## 撤关证明（withdrawal）

撤关在已封存批次上旁路裁决，不回写原批次。每个主单带一份原放行快照 `snapshot{id,release_ref,released{pieces,net_kg,gross_kg},container}`，`snapshot_seal` 为快照内容哈希，恢复时必须对回同一快照。

台账 `amendments` 只追加不改写，事件类型 `correction`（补正，按增减量）、`split`（拆出，减余额）、`merge`（并入，增余额）、`restow`（换箱，货量不变只留箱号轨迹），每个事件必须带 `evidence_ref` 来源证据号。从快照逐事件重算出 `effective_balance`（当前有效余额）、覆盖量 `covered`（当前在账且有来源证据的货量，恒不超过有效余额）和 `trace`（逐事件前后余额）。任一事件导致负向或越界余额，记 `broken_ref` 并停止重算。

裁决 `restore` 依据余额证明（重算不失衡）与来源证明（证据号齐备且覆盖不越界）能否同时成立：整票覆盖判 `FULL_SNAPSHOT/已恢复`，恢复量取原放行快照；部分覆盖判 `PARTIAL_EVIDENCE/部分恢复`，恢复量取 `min(covered, effective_balance)`，其余进 `retained_balance`；任一证明不成立判 `NONE/确认失败`，原因码 `BALANCE_UNPROVEN`、`BROKEN_LEDGER:<证据号>` 或 `SOURCE_UNPROVEN`，旧版余额与原证明保留。`certificate_seal` 为裁决封印，同指令+同快照+同证据链必相同，用于重试幂等校验。持久层 `withdrawals(run_id,instruction_ref)` 主键约束同一批次同一指令只有一套结果，重试仅累加 `attempts`。
