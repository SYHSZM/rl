# Task 1 指标数据血缘证据采集报告

本报告只用于指标可信度审计，不形成交通研究结论。

## 修改/新增了哪些文件

本轮只在 `outputs/audit_0_3/` 内新增或更新以下文件；未修改生产代码、tests、docs，也未删除或恢复 network 文件。fix round 1 的非隔离 pytest incident 单独在风险段披露：

- `audit.add.xml`
- `audit.rou.xml`
- `command.txt`
- `current_summary.csv`
- `current_windows.csv`
- `experiment_result.json`
- `metric_trace_runner.py`
- `native/det_bottleneck_down.xml`
- `native/det_main_0.xml`
- `native/det_main_1.xml`
- `native/det_main_2.xml`
- `native/det_main_3.xml`
- `native/det_main_4.xml`
- `native/det_ramp_arrival.xml`
- `native/det_ramp_queue.xml`
- `production_hash_guard.json`
- `production_path/main1200_ramp120_none_seed0_9587c551/audit.add.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/config.json`
- `production_path/main1200_ramp120_none_seed0_9587c551/merge.rou.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/native/det_bottleneck_down.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/native/det_main_0.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/native/det_main_1.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/native/det_main_2.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/native/det_main_3.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/native/det_main_4.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/native/det_ramp_arrival.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/native/det_ramp_queue.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/production_sumo_command.json`
- `production_path/main1200_ramp120_none_seed0_9587c551/summary.csv`
- `production_path/main1200_ramp120_none_seed0_9587c551/sumo.log`
- `production_path/main1200_ramp120_none_seed0_9587c551/sumo_error.log`
- `production_path/main1200_ramp120_none_seed0_9587c551/tripinfo.xml`
- `production_path/main1200_ramp120_none_seed0_9587c551/windows.csv`
- `production_path/monkeypatch_scope.json`
- `production_summary_reconciliation.csv`
- `production_windows_reconciliation.csv`
- `pytest_junit.xml`
- `pytest_nonisolated_incident/incident_record.json`
- `pytest_nonisolated_incident/pytest_junit.xml`
- `pytest_nonisolated_incident/pytest_result.json`
- `pytest_nonisolated_incident/pytest_stderr.log`
- `pytest_nonisolated_incident/pytest_stdout.log`
- `pytest_real_workspace_hash_guard.json`
- `pytest_result.json`
- `pytest_sandbox/copy_manifest.json`
- `pytest_sandbox/project/.pytest_cache/.gitignore`
- `pytest_sandbox/project/.pytest_cache/CACHEDIR.TAG`
- `pytest_sandbox/project/.pytest_cache/README.md`
- `pytest_sandbox/project/.pytest_cache/v/cache/nodeids`
- `pytest_sandbox/project/build_network.py`
- `pytest_sandbox/project/classify.py`
- `pytest_sandbox/project/controllers.py`
- `pytest_sandbox/project/env.py`
- `pytest_sandbox/project/experiment_config.py`
- `pytest_sandbox/project/metrics.py`
- `pytest_sandbox/project/network/det_bottleneck_down.xml`
- `pytest_sandbox/project/network/det_main_0.xml`
- `pytest_sandbox/project/network/det_main_1.xml`
- `pytest_sandbox/project/network/det_main_2.xml`
- `pytest_sandbox/project/network/det_main_3.xml`
- `pytest_sandbox/project/network/det_main_4.xml`
- `pytest_sandbox/project/network/det_ramp_arrival.xml`
- `pytest_sandbox/project/network/det_ramp_queue.xml`
- `pytest_sandbox/project/network/merge.add.xml`
- `pytest_sandbox/project/network/merge.con.xml`
- `pytest_sandbox/project/network/merge.edg.xml`
- `pytest_sandbox/project/network/merge.net.xml`
- `pytest_sandbox/project/network/merge.nod.xml`
- `pytest_sandbox/project/network/merge.tll.xml`
- `pytest_sandbox/project/rou_generate.py`
- `pytest_sandbox/project/scan_demand.py`
- `pytest_sandbox/project/tests/test_build_network.py`
- `pytest_sandbox/project/tests/test_classify.py`
- `pytest_sandbox/project/tests/test_controllers.py`
- `pytest_sandbox/project/tests/test_env_runner.py`
- `pytest_sandbox/project/tests/test_experiment_config.py`
- `pytest_sandbox/project/tests/test_metrics.py`
- `pytest_sandbox/project/tests/test_rou_generate.py`
- `pytest_sandbox/project/tests/test_scan_demand.py`
- `pytest_sandbox/tmp/test_build_network_creates_net0/merge.net.xml`
- `pytest_sandbox/tmp/test_generate_route_has_three_0/merge.rou.xml`
- `pytest_sandbox/tmp/test_generate_route_uses_merge0/merge.rou.xml`
- `pytest_sandbox/tmp/test_short_sumo_run_produces_w0/main1200_ramp120_none_seed0_1e3043f3/config.json`
- `pytest_sandbox/tmp/test_short_sumo_run_produces_w0/main1200_ramp120_none_seed0_1e3043f3/merge.rou.xml`
- `pytest_sandbox/tmp/test_short_sumo_run_produces_w0/main1200_ramp120_none_seed0_1e3043f3/summary.csv`
- `pytest_sandbox/tmp/test_short_sumo_run_produces_w0/main1200_ramp120_none_seed0_1e3043f3/tripinfo.xml`
- `pytest_sandbox/tmp/test_short_sumo_run_produces_w0/main1200_ramp120_none_seed0_1e3043f3/windows.csv`
- `pytest_sandbox/tmp/test_write_window_csv_uses_sta0/windows.csv`
- `pytest_stderr.log`
- `pytest_stdout.log`
- `software_versions.txt`
- `step_trace.csv`
- `sumo.log`
- `sumo_command.json`
- `sumo_error.log`
- `task-1-r1-review-package.md`
- `task-1-review-package.md`
- `tripinfo.xml`
- `tripinfo_reconciliation.csv`
- `window_reconciliation.csv`
- `manifest.json`
- `task-1-report.md`

## 实现和执行了什么

- 固定证据场景为 360 s、mainline 1200 veh/h、ramp 120 veh/h、controller `none`、seed 0、单阶段 0–360 s × 1.0、1 s SUMO 步长和 30 s 报告窗口。
- 审计复现路径逐秒记录 loaded/departed/arrived/pending/in-network/teleport、每个 induction loop 的 last-step count/speed/occupancy，以及 ramp lane-area vehicle/halting count；得到 360 条 step rows 和 12 条 windows。
- 直接调用了真实生产入口 `env.run_experiment(config, demand, "none", 0, output_root=production_path)`。只在这一次调用的 `try/finally` 作用域内，把 `env.build_network` 替换为校验现有 `network/merge.net.xml` 后直接返回的 no-op，并把 `env._sumo_command` 替换为仅重定向 additional XML、detector XML、tripinfo 和 SUMO 日志到 `outputs/audit_0_3/production_path/` 的命令构造器；随后已恢复原函数。准确调用、命令、输出目录和 restored 状态见 `production_path/monkeypatch_scope.json`。
- 生产入口返回 valid=True、12 条 windows。生产入口与审计复现路径逐字段对账：windows 192 项、summary 15 项；不一致分别为 0 和 0。证据见 `production_windows_reconciliation.csv` 与 `production_summary_reconciliation.csv`。
- production network XML 的调用前后 SHA-256 守卫见 `production_hash_guard.json`；monkeypatch 已恢复：True。
- 全量 pytest 从本轮起只在 `pytest_sandbox/project/` 的精简隔离副本运行，`--basetemp` 也位于 `pytest_sandbox/tmp/`。复制范围为8个所需 production Python modules、全部现有 tests 和5个 network source XML，不复制 outputs 或大文档；21 个源/目标文件的 SHA-256 见 `pytest_sandbox/copy_manifest.json`。
- pytest 前后分别哈希真实工作区 tracked 文件及全部关键 `network/*.xml`；`pytest_real_workspace_hash_guard.json` 记录 tracked_unchanged=True、critical_network_xml_unchanged=True、unchanged=True。

### current windows / 30 s step / native XML 逐字段对账

`YES/NO/NA n/12` 表示数值一致、数值不一致、来源不适用或无 detector exposure 的窗口数。native 速度及 lane-area `meanVehicleNumber` 只按 XML 输出精度使用 ±0.0051 容差；占有率 fraction 使用 ±0.0001；计数要求精确一致。数值一致不覆盖语义差异。

| 字段 | current↔30 s step | 30 s step↔native | current↔native | current↔interval语义 | step↔native独立语义/状态 |
|---|---|---|---|---|---|
| experiment_id | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| controller | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| seed | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| time_s | YES 12/12 | YES 12/12 | YES 12/12 | SAME | SAME_INTERVAL_END; CONSISTENT 12/12 |
| mainline_vph | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| ramp_vph | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| mean_speed_mps | NO 12/12 | YES 8/12; NO 4/12 | NO 12/12 | DIFFERENT | DIFFERENT_POPULATION_AND_BOUNDARY; INCONSISTENT 12/12 |
| upstream_speed_mps | NO 9/12; NA 3/12 | YES 6/12; NO 3/12; NA 3/12 | NO 9/12; NA 3/12 | DIFFERENT | DIFFERENT_POPULATION_AND_BOUNDARY; INCONSISTENT 12/12 |
| bottleneck_flow_veh | YES 4/12; NO 8/12 | YES 9/12; NO 3/12 | YES 4/12; NO 8/12 | DIFFERENT | DIFFERENT_POPULATION_AND_BOUNDARY; INCONSISTENT 12/12 |
| bottleneck_occupancy | YES 4/12; NO 8/12 | YES 9/12; NO 3/12 | YES 4/12; NO 8/12 | DIFFERENT | DIFFERENT_EXPOSURE_AND_BOUNDARY; INCONSISTENT 12/12 |
| ramp_queue_veh | NO 12/12 | YES 12/12 | NO 12/12 | DIFFERENT | SAME_TIME_MEAN_WITH_NATIVE_ROUNDING; CONSISTENT 12/12 |
| alinea_requested_rate_vph | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| alinea_applied_rate_vph | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| departed_veh | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| arrived_veh | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |
| teleports | YES 12/12 | NA 12/12 | NA 12/12 | SAME | NOT_APPLICABLE; NA 12/12 |

step↔native 保留共 13 个数值差异：mean speed 4、upstream speed 3、flow 3、occupancy 3。这些估计器不可互换：

- flow：逐步值是 30 个 `getLastStepVehicleNumber` 观察之和；native 主比较值是 interval `nVehEntered`，而 `nVehContrib` 只计完成 detector traversal 的车辆，窗口边界 population 可不同。
- speed：逐步估计用 last-step count 加权每秒 TraCI mean speed；native interval speed 以 `nVehContrib` 完成 traversal 的车辆为 population，进入但未在窗口内完成的边界车辆处理不同。
- occupancy：逐步估计是 30 个一秒 occupancy 样本的算术平均；native occupancy 是 detector interval exposure，并按 native population 处理窗口边界的部分占用。
- ramp queue：逐步算术平均和 native `meanVehicleNumber` 指向同一时间平均量，XML 两位小数舍入后 12/12 数值一致。

详细逐窗口值、独立语义列、状态与解释见 `window_reconciliation.csv`。

### TTS / tripinfo 对账

- current summary TTS：19530.0 s；1 s in-network integral：18817 s；1 s waiting/pending integral：0 s；1 s system TTS：18817 s。
- completed tripinfo duration：11302.0 s；timeLoss：530.26 s；360 s 时 incomplete active/pending：73/0。
- 车辆 ID 位于 `step_trace.csv` 的 loaded/departed/arrived/pending/active ID 列，以及 `experiment_result.json` 的 final active/pending ID 清单。`tripinfo_reconciliation.csv` 只保存 completed/incomplete 的汇总 exposure 和差值，不保存 ID 级明细。

## 运行了哪些测试与实验命令、结果如何

完整命令见 `command.txt`，Python/SUMO/netconvert/pytest 版本见 `software_versions.txt`，SUMO 原始日志见根审计目录和 `production_path/<experiment_id>/`，pytest stdout/stderr/JUnit/退出码均已保存。

- 审计复现路径：360 steps、12 windows、summary TTS=19530.0 s。
- 真实 `env.run_experiment` 路径：valid=True、12 windows，windows/summary 逐字段与审计复现路径完全一致。
- 全量 pytest：exit_code=0；..................                                                       [100%]
18 passed in 1.35s。
- pytest 隔离 cwd：`D:\python_install\py\rl\0325\0325\outputs\audit_0_3\pytest_sandbox\project`；真实工作区测试前后哈希 unchanged=True。

## 剩余风险、异常或疑问

- current windows 的 speed/flow/occupancy/queue 是窗口末 last-step 快照，与完整 30 s 估计器语义不同；数值偶合不能证明语义相同。
- step 聚合与 native XML 也是不同估计器；上述 13 个差异来自 population、窗口边界和 exposure 口径，Task 2 不应将二者当作可互换的完整窗口聚合。
- 生产入口证据依赖明确记录的两处临时 monkeypatch；它证明了 `env.run_experiment` 的其余完整执行路径与审计复现输出一致，但不证明未打补丁时 `build_network` 写文件副作用或静态 production additional 输出路径安全。
- fix round 1 的非隔离 pytest 曾在真实 PROJECT_ROOT 运行；现有 integration test 调用了未打补丁的 `env.run_experiment`，改写了预先存在的8个 `network/det_*.xml` 和 `network/merge.net.xml`。原始字节基线没有备份，无法证明已恢复，因此本轮不删除、不猜测恢复这些文件；原始非隔离 pytest stdout/stderr/JUnit/result 已原样保存在 `pytest_nonisolated_incident/`，说明见 `incident_record.json`。tracked production code 始终无 diff；从本轮起隔离测试证明真实工作区哈希稳定。
- tripinfo 只含已完成车辆；未完成车辆 ID/exposure 已分别保留。默认 Python 3.14 缺少 pytest/traci，本次使用 `software_versions.txt` 记录的 Codex Python 3.12，并通过 SUMO_HOME/tools 加载 TraCI。
