"""python_script 插件:执行平台托管脚本(storage/scripts 下),注入区间环境变量。
stdout/stderr 转写到当前进程 stdout(task_runner 已重定向到任务日志文件)。"""
import os
import subprocess
import sys


def execute(params: dict, ctx: dict, env) -> dict:
    name = params["script"]
    script = (env.scripts_dir / name).resolve()
    if not str(script).startswith(str(env.scripts_dir.resolve())):
        raise ValueError(f"脚本路径越界: {name}")
    if not script.exists():
        raise FileNotFoundError(f"脚本不存在: {name}")
    env_vars = {**os.environ,
                "FP_DS": ctx["ds"], "FP_DS_NODASH": ctx["ds_nodash"],
                "FP_DATA_INTERVAL_START": ctx["data_interval_start"],
                "FP_DATA_INTERVAL_END": ctx["data_interval_end"]}
    proc = subprocess.run([sys.executable, str(script)], env=env_vars,
                          capture_output=True, text=True, encoding="utf-8")
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="")
    if proc.returncode != 0:
        raise RuntimeError(f"脚本退出码 {proc.returncode}")
    return {"returncode": 0}
