# VSL 单智能体固定事故/固定车流实验计划

## 当前目标

先完成可变限速 VSL 单智能体实验：固定事故车、固定车流文件，每个 episode 不重新随机生成 rou。信号灯阶段先保持不控制，VSL 智能体控制事故上游 5 个 edge 的限速。

## 已落地设置

- `env.py` 默认 `regenerate_routes=False`，每个 episode 复用 `osm_check.rou.xml`。
- 事故车辆仍为 `incidentVeh`，到达 `1060166417.562` 的 250 m 位置后强制减速停车。
- 动作空间保持 5 个 VSL edge，每个 edge 从 `[60, 70, 80, 90, 100, 110, 120]` 中选限速，并保持越靠近事故点限速不高于更上游限速。
- `check.sumocfg` 不需要改；后续新路网和新 rou 到位后，直接覆盖 `osm_road.net.xml` 和 `osm_check.rou.xml` 即可。

## 当前 reward 组合

每 20 s 计算一次 reward，300 s 聚合成一次动作回报：

- 惩罚速度方差：让事故上游车流更平稳。
- 惩罚急刹次数：安全项，权重较大。
- 惩罚过低限速：避免智能体长期全压到 60 km/h。
- 惩罚限速跳变：避免 60 到 120 这类大幅震荡。
- 少量奖励平均速度和通过量：给效率一个弱引导，避免只追求安全导致上游堵死。

默认权重在 `train_ppo.py`：

```python
reward_w_var=1.0
reward_w_brake=2.0
reward_w_low=0.2
reward_w_smooth=0.3
reward_w_speed=0.2
reward_w_throughput=0.1
```

## 建议测试组合

1. 安全优先：`brake=3.0, var=1.2, smooth=0.4, speed=0.1, throughput=0.05`
2. 平衡方案：`brake=2.0, var=1.0, smooth=0.3, speed=0.2, throughput=0.1`
3. 效率略强：`brake=1.5, var=0.8, smooth=0.2, speed=0.3, throughput=0.2`

训练参数先用当前 PPO 设置跑收敛趋势；如果曲线震荡，优先把 `ent_coef` 从 `0.02` 降到 `0.01`，或把 `learning_rate` 从 `2.5e-4` 降到 `1e-4`。

## 输出检查

每次训练会生成：

- `ppo_vsl_runs/<run_id>/episode_rewards.csv`
- `ppo_vsl_runs/<run_id>/tensorboard/`
- `ppo_vsl_runs/<run_id>/ppo_vsl_fixed_flow_single_agent.zip`

CSV 已记录 reward 分量，可用于判断到底是急刹、速度波动、限速跳变还是效率项导致不收敛。
