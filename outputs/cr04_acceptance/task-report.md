# 1. 交付范围

CR-04 仅实现真实的扫描恢复与配置匹配：确定性 `scan_id`、严格 `scan_manifest.json`、冻结网络复用、逐 attempt 的 `artifact_manifest.json`、完整性校验、只跳过完全有效且身份匹配的 attempt、损坏/缺失/异源 attempt 重试、从磁盘确定性重建索引与恢复决策。未运行研究扫描、情景、校准、扰动或 770 矩阵。

跟踪代码改动仅限 `scan_demand.py` 与 `tests/test_scan_demand.py`；验收材料仅写入 `outputs/cr04_acceptance/`。未修改 `env.py`、`metrics.py`、`classify.py`、`build_network.py`、`rou_generate.py`、协议文档、网络源文件或历史验收证据。

# 2. RED

- 初始聚焦 RED：`6 failed, 25 passed`，证明原实现缺少确定性 scan identity、严格 manifest、真实 resume/skip/retry 与稳定磁盘索引。
- Sol 预审 RED：`4 failed`，分别证明 artifact manifest 写失败会掩盖原异常、windows 时间网格不完整仍被接受、恢复 summary 丢失 CR-02 扩展字段。
- 完整 inventory RED：`1 failed`，证明 manifest 未拒绝后增的未列入文件。
- 每次 RED 的命令、标准输出和退出码均封存在本目录，退出码均为 `1`。

# 3. GREEN

- `python -m pytest tests/test_scan_demand.py -q`：`36 passed in 13.20s`，退出码 `0`。
- `python -m pytest tests -q`：`80 passed in 21.97s`，退出码 `0`。
- `scan_demand.py` 与 `tests/test_scan_demand.py` 均通过内存语法编译；CLI 帮助包含 `--scan-id`；`git diff --check` 无错误；CR-04 证据目录无残留 `*.tmp`。
- 历史验收目录最新缓存时间早于本轮误收集时间，确认未改写 CR-01/02/03/05 或 audit 历史证据。

# 4. 风险与提交

仓库根目录直接运行无路径限制的 `pytest` 会递归收集 `outputs/*/pytest_sandbox` 的历史测试副本并产生同名模块 collection 冲突；正式回归命令必须限定为 `pytest tests`。自动目录名使用完整身份 SHA-256 的前 16 个十六进制字符以避免 Windows 深层 attempt 路径超限，完整 SHA-256 仍保存在 scan manifest 并作为权威身份严格校验；极低概率前缀碰撞会被完整身份不匹配安全拒绝。

commit none
