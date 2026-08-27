# 1. 修改了哪些文件

Task 5-B 只在 `outputs/stage_1/` 顶层创建或更新只读汇总脚本、测试、CSV、JSON、PNG 和本报告；未修改 `formal_scans/` 内原始证据，也未修改 production、tests、docs 或 network。commit none。

# 2. 实现/产出了什么

保留三个 CR-04 scan 的全部 16 行 attempt 历史（15 行 selected valid，1 行 medium 超时遗留 invalid），并对 15 个 selected attempt 逐项复用 CR-04 严格 artifact/identity/config/network/attempt/windows/summary 校验。生成 15 行质量汇总、360 行五 seed 聚合时序和低中高对齐核查图。日志事件按 `(vehicle,lane,time,type)` 去重，共 1 个唯一事件。

# 3. 运行了哪些测试/验证及原始结果

- attempt 历史：16 行；selected：15；invalid history：1。
- selected 有效性：15/15；每次完整窗口：15/15 × 120；原生 detector XML：15/15 × 8。
- hard invalid：0；Stage 1 safety quality flagged runs：1；唯一 emergency braking：1；collision：0；teleport：0；SUMO Error/Fatal：0。
- raw net SHA-256：`44f5c4363317ad0e0d0106719b0d7610868afa827346747a7c991585a7d573e3`、`fdaca3f89b7bb29d478bf8e00f83bd7a3cca9bf6b2f7fe81440655a997f33973`、`ff76f9acadcadf1d5af5ef741b902839fcb2dc41eb7140773d349d2210bfd189`（原样保留）。
- semantic net SHA-256：1 个唯一值，均为 `502e41585b7169e726bba5b1bd19393af2025c1480a7cf483c94002b6452752f`。

# 4. 剩余风险或疑问

三份 raw net 字节哈希不同，因为 netconvert XML 注释头包含生成时间和调用/输出路径；ElementTree 根元素序列化会排除注释，三份语义哈希一致。high seed 2 的一次 emergency braking 按 protocol 8.1 不构成硬无效，作为 Stage 1 safety quality flag 保留，并转交内容3；该事件不作为 collision，也未删 seed、改参数或重跑掩盖。本文不解释自由流、临界或拥堵边界。
