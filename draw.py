import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import glob  # 新增：用于搜索文件

# 如果没有 seaborn，可以注释掉下面两行，不影响运行
try:
    import seaborn as sns
    sns.set_theme(style="darkgrid")
except ImportError:
    pass

# 1. 自动搜索所有 episode_rewards.csv 文件
log_dir = "ppo_vsl_runs"
file_pattern = os.path.join(log_dir, "**", "episode_rewards.csv")
all_files = glob.glob(file_pattern, recursive=True)

if not all_files:
    print(f"未在 {log_dir} 中找到任何 episode_rewards.csv 文件。")
    if os.path.exists('results.csv'):
        all_files = ['results.csv']
    else:
        print("错误：找不到数据文件。")
        exit()

print(f"找到 {len(all_files)} 个数据文件，正在合并...")

# 2. 读取并合并所有数据
li = []
for filename in all_files:
    df_temp = pd.read_csv(filename, index_col=None, header=0)
    li.append(df_temp)

df = pd.concat(li, axis=0, ignore_index=True)

if 'episode_reward' in df.columns:
    df['episode_reward_total'] = df['episode_reward']

# 3. 创建序号并排序
df['episode_number'] = range(1, len(df) + 1)
df = df.set_index('episode_number').sort_index()

# 4. 获取数据
episodes = df.index
max_ep = len(df)

# -------------------- 图1：学习曲线 --------------------
fig, axes = plt.subplots(2, 1, figsize=(12, 8))

# 1. 总奖励
if 'episode_reward' in df.columns:
    reward_col = 'episode_reward'
elif 'episode_reward_total' in df.columns:
    reward_col = 'episode_reward_total'
else:
    reward_col = None

if reward_col:
    # --- 修改部分：绘制原始曲线（调低透明度 alpha） ---
    axes[0].plot(episodes, df[reward_col], '-',
                 linewidth=1, alpha=0.3, color='gray', label='raw')
    
    # --- 修改部分：增加滑动平均线 (窗口大小设为50) ---
    window_size = 10
    # rolling(window).mean() 计算滑动均值，min_periods=1 保证初期也有数据
    ma_reward = df[reward_col].rolling(window=window_size, min_periods=1).mean()
    axes[0].plot(episodes, ma_reward, '-', 
                 linewidth=2, color='red', label=f'MA (window={window_size})')
    
    axes[0].set_ylabel('Total Reward')
    axes[0].set_title('Learning Curve - Total Reward')
    axes[0].legend()
else:
    axes[0].text(0.5, 0.5, 'No Reward Data Found', ha='center')

# 2. 安全奖励和效率奖励
has_safety = 'episode_reward_safety' in df.columns
has_efficiency = 'episode_reward_efficiency' in df.columns

if has_safety:
    # 同样可以给子奖励增加滑动平均（可选），这里保持你原有的绘制方式
    axes[1].plot(episodes, df['episode_reward_safety'], '-',
                 linewidth=1, alpha=0.7, label='Safety', color='green')
if has_efficiency:
    axes[1].plot(episodes, df['episode_reward_efficiency'], '-',
                 linewidth=1, alpha=0.7, label='Efficiency', color='orange')

if has_safety or has_efficiency:
    axes[1].set_xlabel('Episode Number')
    axes[1].set_ylabel('Reward Components')
    axes[1].set_title('Safety & Efficiency Rewards')
    axes[1].legend()
else:
    axes[1].text(0.5, 0.5, 'Safety/Efficiency columns not found in CSV', ha='center')
    axes[1].set_title('Detailed Rewards (Not Available)')

# 设置 x 轴刻度
step = max(1, max_ep // 10)
for ax in axes:
    xticks = np.arange(0, max_ep + 1, step)
    ax.set_xticks(xticks)
    ax.set_xticklabels([str(i) for i in xticks])

plt.tight_layout()
plt.savefig('learning_curves_combined.png', dpi=150)
print("绘图完成：learning_curves_combined.png")
plt.show()