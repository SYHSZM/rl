# train_ppo.py
import csv
import os
from datetime import datetime

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, CallbackList
from stable_baselines3.common.vec_env import DummyVecEnv

from env import SingleAgentHighwayVSLEnv


EXPERIMENT_PRESETS = {
    "safety_default": {
        "reward": {
            "reward_w_var": 0.01,
            "reward_w_brake": 1.0,
            "reward_w_low": 0.0,
            "reward_w_speed": 0.0,
            "reward_w_smooth": 0.0,
        },
        "ppo": {
            "n_steps": 120,
            "batch_size": 60,
            "n_epochs": 10,
            "learning_rate": 1e-4,
            "gamma": 0.99,
            "gae_lambda": 0.95,
            "clip_range": 0.2,
            "ent_coef": 0.02,
        },
    },
    "brake_strong": {
        "reward": {
            "reward_w_var": 0.005,
            "reward_w_brake": 2.0,
            "reward_w_low": 0.0,
            "reward_w_speed": 0.0,
            "reward_w_smooth": 0.0,
        },
        "ppo": {
            "n_steps": 120,
            "batch_size": 60,
            "n_epochs": 10,
            "learning_rate": 5e-5,
            "gamma": 0.995,
            "gae_lambda": 0.95,
            "clip_range": 0.15,
            "ent_coef": 0.01,
        },
    },
    "reward_speed_smooth": {
        "reward": {
            "reward_w_var": 0.0,
            "reward_w_brake": 1.5,
            "reward_w_low": 0.0,
            "reward_w_speed": 15.0,
            "reward_w_smooth": 0.03,
        },
        "ppo": {
            "n_steps": 120,
            "batch_size": 60,
            "n_epochs": 10,
            "learning_rate": 1e-4,
            "gamma": 0.99,
            "gae_lambda": 0.95,
            "clip_range": 0.2,
            "ent_coef": 0.01,
        },
    },
}

SELECTED_PRESET = os.environ.get("VSL_PRESET", "reward_speed_smooth")
NUM_AGENTS = int(os.environ.get("VSL_NUM_AGENTS", "2"))
PRECHECK_EPISODES = int(os.environ.get("VSL_PRECHECK_EPISODES", "2"))


def make_env():
    base_dir = os.path.dirname(__file__)
    preset = EXPERIMENT_PRESETS[SELECTED_PRESET]

    return SingleAgentHighwayVSLEnv(
        config_path=os.path.join(base_dir, "check.sumocfg"),
        route_output_path=os.path.join(base_dir, "osm_check.rou.xml"),
        use_gui=False,
        control_interval_s=300,
        reward_interval_s=20,
        control_duration_s=3600,
        num_agents=NUM_AGENTS,
        seed=42,
        **preset["reward"],
    )


class EpisodeRewardLogger(BaseCallback):
    def __init__(self, csv_path: str, verbose: int = 1):
        super().__init__(verbose=verbose)
        self.csv_path = csv_path
        self.current_ep_reward = 0.0
        self.episode_count = 0
        self.last_total_var = 0.0
        self.last_total_hard_brakes = 0
        self.last_total_low_speed_penalty = 0.0
        self.current_ep_reward_safety = 0.0
        self.current_ep_reward_efficiency = 0.0
        self.sum_mean_speed = 0.0
        self.sum_limit_change = 0.0
        self.sum_jump = 0.0
        self.step_in_ep = 0
        self.prev_action = None

        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "episode",
                    "episode_reward",
                    "timestamp",
                    "total_var",
                    "total_hard_brakes",
                    "total_low_speed_penalty",
                    "episode_reward_safety",
                    "episode_reward_efficiency",
                    "mean_speed_mps",
                    "mean_limit_change",
                    "mean_action_jump",
                ]
            )

    @staticmethod
    def _first(x, default=None):
        if x is None:
            return default
        try:
            return x[0]
        except Exception:
            return x

    def _on_step(self) -> bool:
        rewards = self.locals.get("rewards", None)
        dones = self.locals.get("dones", None)
        infos = self.locals.get("infos", None)

        if rewards is None or dones is None:
            return True

        reward = float(self._first(rewards, 0.0))
        done = bool(self._first(dones, False))
        info = self._first(infos, {}) if infos is not None else {}

        self.current_ep_reward += reward
        self.step_in_ep += 1

        if isinstance(info, dict):
            self.last_total_var = float(info.get("total_var", self.last_total_var))
            self.last_total_hard_brakes = int(info.get("total_hard_brakes", self.last_total_hard_brakes))
            self.last_total_low_speed_penalty = float(
                info.get("total_low_speed_penalty", self.last_total_low_speed_penalty)
            )
            self.current_ep_reward_safety += float(info.get("step_reward_safety", 0.0))
            self.current_ep_reward_efficiency += float(info.get("step_reward_efficiency", 0.0))
            self.sum_mean_speed += float(info.get("mean_speed_mps", 0.0))
            self.sum_limit_change += float(info.get("limit_change", 0.0))

            applied_limits = info.get("applied_limits_kmh", None)
            if isinstance(applied_limits, dict):
                action = np.array(list(applied_limits.values()), dtype=float)
                if self.prev_action is not None:
                    self.sum_jump += float(np.sum(np.abs(action - self.prev_action)))
                self.prev_action = action

        if done:
            self.episode_count += 1
            mean_jump = (self.sum_jump / max(self.step_in_ep - 1, 1)) / 300.0
            mean_speed = self.sum_mean_speed / max(self.step_in_ep, 1)
            mean_limit_change = self.sum_limit_change / max(self.step_in_ep, 1)

            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        self.episode_count,
                        self.current_ep_reward,
                        datetime.now().isoformat(timespec="seconds"),
                        self.last_total_var,
                        self.last_total_hard_brakes,
                        self.last_total_low_speed_penalty,
                        self.current_ep_reward_safety,
                        self.current_ep_reward_efficiency,
                        mean_speed,
                        mean_limit_change,
                        mean_jump,
                    ]
                )

            if self.verbose > 0:
                print(
                    f"[Episode {self.episode_count}] "
                    f"reward={self.current_ep_reward:.3f} | "
                    f"total_var={self.last_total_var:.3f} "
                    f"hard_brakes={self.last_total_hard_brakes} "
                    f"low_speed_penalty={self.last_total_low_speed_penalty:.3f} "
                    f"safety={self.current_ep_reward_safety:.3f} "
                    f"efficiency={self.current_ep_reward_efficiency:.3f} "
                    f"mean_speed={mean_speed:.3f} "
                    f"mean_jump={mean_jump:.3f}"
                )

            self.current_ep_reward = 0.0
            self.last_total_var = 0.0
            self.last_total_hard_brakes = 0
            self.last_total_low_speed_penalty = 0.0
            self.current_ep_reward_safety = 0.0
            self.current_ep_reward_efficiency = 0.0
            self.sum_mean_speed = 0.0
            self.sum_limit_change = 0.0
            self.sum_jump = 0.0
            self.step_in_ep = 0
            self.prev_action = None

        return True


class StopTrainingOnEpisodeCount(BaseCallback):
    def __init__(self, n_episodes: int, verbose: int = 1):
        super().__init__(verbose=verbose)
        self.n_episodes = int(n_episodes)
        self.episode_count = 0

    @staticmethod
    def _first(x, default=None):
        if x is None:
            return default
        try:
            return x[0]
        except Exception:
            return x

    def _on_step(self) -> bool:
        dones = self.locals.get("dones", None)
        if dones is None:
            return True

        if bool(self._first(dones, False)):
            self.episode_count += 1
            if self.verbose > 0:
                print(f"[Stopper] episode={self.episode_count}/{self.n_episodes}")
            if self.episode_count >= self.n_episodes:
                if self.verbose > 0:
                    print(f"[Stopper] Reached {self.n_episodes} episodes. Stopping training.")
                return False
        return True


def run_precheck_episodes(n_episodes: int):
    if n_episodes <= 0:
        return

    print(f"[Precheck] running {n_episodes} warm-up episode(s) before PPO training")
    print(
        "[Precheck] target: safety and efficiency agents both act at the 5m incident point; "
        "the goal is fast discharge without congestion, using reward_speed_smooth as the baseline."
    )

    env = make_env()
    try:
        for ep in range(1, n_episodes + 1):
            obs, info = env.reset()
            done = False
            episode_reward = 0.0
            episode_safety = 0.0
            episode_efficiency = 0.0
            sum_mean_speed = 0.0
            sum_limit_change = 0.0
            steps = 0
            last_info = info

            while not done:
                action = env.action_space.sample()
                obs, reward, terminated, truncated, step_info = env.step(action)
                done = terminated or truncated
                episode_reward += float(reward)
                episode_safety += float(step_info.get("step_reward_safety", 0.0))
                episode_efficiency += float(step_info.get("step_reward_efficiency", 0.0))
                sum_mean_speed += float(step_info.get("mean_speed_mps", 0.0))
                sum_limit_change += float(step_info.get("limit_change", 0.0))
                steps += 1
                last_info = step_info

            mean_speed = sum_mean_speed / max(steps, 1)
            mean_limit_change = sum_limit_change / max(steps, 1)
            print(
                f"[Precheck {ep}] reward={episode_reward:.3f} "
                f"safety={episode_safety:.3f} efficiency={episode_efficiency:.3f} "
                f"mean_speed={mean_speed:.3f}m/s "
                f"total_var={float(last_info.get('total_var', 0.0)):.3f} "
                f"hard_brakes={int(last_info.get('total_hard_brakes', 0))} "
                f"mean_limit_change={mean_limit_change:.3f} "
                f"last_agent_limits={last_info.get('agent_limits_kmh', {})}"
            )
    finally:
        env.close()


def main():
    seed = 42
    preset = EXPERIMENT_PRESETS[SELECTED_PRESET]

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(os.path.dirname(__file__), "ppo_vsl_runs", f"{SELECTED_PRESET}_{run_id}")
    tb_dir = os.path.join(log_dir, "tensorboard")
    csv_path = os.path.join(log_dir, "episode_rewards.csv")
    os.makedirs(tb_dir, exist_ok=True)

    run_precheck_episodes(PRECHECK_EPISODES)

    env = DummyVecEnv([make_env])
    n_episodes = 300
    total_timesteps = 300_000
    callback = CallbackList(
        [
            EpisodeRewardLogger(csv_path=csv_path, verbose=1),
            StopTrainingOnEpisodeCount(n_episodes=n_episodes, verbose=1),
        ]
    )

    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        tensorboard_log=tb_dir,
        seed=seed,
        **preset["ppo"],
    )

    print(f"[Train] preset={SELECTED_PRESET}")
    print(f"[Train] target_episodes={n_episodes}")
    print(f"[Train] total_timesteps(max)={total_timesteps} (will stop early by episode count)")
    print(f"[Logs] tensorboard={tb_dir}")
    print(f"[Logs] csv={csv_path}")

    model.learn(total_timesteps=total_timesteps, callback=callback)

    model_path = os.path.join(log_dir, f"ppo_vsl_{SELECTED_PRESET}")
    model.save(model_path)
    print(f"Model saved: {model_path}.zip")
    env.close()


if __name__ == "__main__":
    main()
