# -*- coding: utf-8 -*-
"""
批量调度脚本 —— 顺序运行全部爬虫项目（光大银行单独运行，B12 已删除）。

用法:
    python run_all.py                  # 跑全部项目
    python run_all.py --only A01,B03   # 只跑指定项目
    python run_all.py --skip A01       # 跳过指定项目
    python run_all.py --timeout 3600   # 每个项目超时秒数（默认不设超时，跑完为止）

每个项目使用各自 venv 的 python.exe 运行其 main.py，
失败的项目不阻塞后续项目，最后输出汇总报告。
"""

import argparse
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

# --- Windows GBK 控制台防崩溃：强制 UTF-8 输出（含 ▶ ⏰ ❌ 等符号）---
if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
# --- end ---

# 确保能 import project_meta
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from project_meta import PROJECT_VENV, PROJECT_ORG, PROJECT_START_DATE, PROJECT_END_DATE  # noqa: E402

# 不参与串行调度的项目：
#   光大银行 —— 用户单独手动运行（Playwright + run_batch.ps1）
#   B12      —— 项目目录已删除
EXCLUDED_PROJECTS = {"光大银行", "B12"}


def find_venv_python(root: str, venv_rel: str) -> str:
    """根据 venv 相对路径找到 python.exe（Windows）或 python（Linux/Mac）。"""
    venv_abs = os.path.join(root, venv_rel)
    if sys.platform == "win32":
        py = os.path.join(venv_abs, "Scripts", "python.exe")
    else:
        py = os.path.join(venv_abs, "bin", "python")
    return py


# ---------------- Ctrl+C 处理 ----------------
# 规则：5 秒内按两次 Ctrl+C = 确认退出；只按一次 = 忽略（防误触/杂散信号）。
#
# Windows 实现说明：直接用控制台处理程序（SetConsoleCtrlHandler）对每次
# Ctrl+C 事件计数。这样做有两个原因：
#   1. CPython 默认处理程序会把「快速连按」的多次 Ctrl+C 合并成 *一个*
#      KeyboardInterrupt —— 连按两次只被当成按了一次，双击确认逻辑永远
#      无法触发（这就是之前"连按两次 Ctrl+C 中断不了"的根因）。
#   2. 处理程序返回 True 后，Python 不会在任意代码位置抛出 KeyboardInterrupt，
#      中断判定完全由调度器轮询 _CTRL_TIMES 决定，不会被随机打断。
_CTRL_TIMES = []        # 每次 Ctrl+C 的时间戳
_LAST_WARN_AT = [0.0]   # 上次打印"已忽略"提示的时间

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _CTRL_C_EVENT = 0

    def _on_ctrl_event(ctrl_type):
        if ctrl_type == _CTRL_C_EVENT:
            _CTRL_TIMES.append(time.time())
            return True   # 我们已处理，不再传给 Python 默认处理程序
        return False      # 关闭窗口等其他事件走默认处理

    _HANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    _CTRL_HANDLER = _HANDLER_ROUTINE(_on_ctrl_event)  # 保持引用，防止被 GC 回收
    try:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_CTRL_HANDLER, True)
    except Exception:
        _CTRL_HANDLER = None


def _recent_ctrl_count(now=None):
    """最近 5 秒内的 Ctrl+C 次数（顺便清理过期时间戳）。"""
    if now is None:
        now = time.time()
    _CTRL_TIMES[:] = [t for t in _CTRL_TIMES if now - t <= 5.0]
    return len(_CTRL_TIMES)


def _kill_process_tree(pid):
    """强制结束进程及其全部子进程（包括项目拉起的 Chrome/Edge）。"""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            pass
    else:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def kill_leaked_browsers():
    """项目进程被中断后，清理仍占用远程调试端口的 Chrome/Edge 残留实例（尽力而为）。

    这些实例是 DrissionPage 拉起的，父进程被杀后不会自动退出，
    若不清理会占住端口，影响下一个浏览器项目启动。
    """
    if sys.platform != "win32":
        return
    ps_cmd = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "($_.Name -eq 'chrome.exe' -or $_.Name -eq 'msedge.exe') -and "
        "($_.CommandLine -like '*--remote-debugging-port*') } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            timeout=60,
        )
    except Exception:
        pass


def run_one_project(root: str, proj_id: str, venv_rel: str, timeout) -> dict:
    """运行单个项目，返回结果字典。timeout 为 None 表示不限时，等项目自然跑完。"""
    org = PROJECT_ORG.get(proj_id, proj_id)
    py_exe = find_venv_python(root, venv_rel)
    main_py = os.path.join(root, proj_id, "main.py")

    result = {
        "project": proj_id,
        "org": org,
        "start": "",
        "end": "",
        "status": "",
        "duration_sec": 0,
        "output_tail": "",
    }

    if not os.path.isfile(py_exe):
        result["status"] = "NO_VENV"
        result["output_tail"] = f"venv python 不存在: {py_exe}"
        return result

    if not os.path.isfile(main_py):
        result["status"] = "NO_MAIN"
        result["output_tail"] = f"main.py 不存在: {main_py}"
        return result

    start_date = PROJECT_START_DATE.get(proj_id, "2024-01-01")
    end_date = PROJECT_END_DATE or datetime.now().strftime("%Y-%m-%d")
    result["start"] = start_date
    result["end"] = end_date

    print(f"\n{'='*60}")
    print(f"▶ [{proj_id}] {org}  日期区间: {start_date} ~ {end_date}")
    print(f"  venv: {py_exe}")
    print(f"  main: {main_py}")
    print(f"{'='*60}")

    t0 = time.time()
    result["start"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    proc = None
    try:
        # 构造子进程环境：强制 UTF-8 输出，避免 GBK 编码乱码
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"

        # Windows 下让子进程不挂接控制台（CREATE_NO_WINDOW）：
        # 终端里的 Ctrl+C（含误触）就不会波及正在跑的爬虫进程，
        # 只有调度器本身收到信号，由下面的双击确认逻辑决定是否退出。
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            [py_exe, main_py],
            cwd=os.path.join(root, proj_id),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            creationflags=creation_flags,
        )

        deadline = None if timeout is None else t0 + timeout

        # 读取线程持续抽干 stdout/stderr：
        #   - 防止子进程管道缓冲区写满导致阻塞（死锁）
        #   - 避免在循环里反复调用 communicate(timeout=0.5) 造成的线程堆积
        stdout_chunks, stderr_chunks = [], []

        def _drain(stream, sink):
            try:
                for line in stream:
                    sink.append(line)
            except Exception:
                pass

        t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
        t_out.start()
        t_err.start()

        timed_out = False
        while True:
            now = time.time()
            remaining = None if deadline is None else deadline - now
            if remaining is not None and remaining <= 0:
                timed_out = True
                break

            # --- 轮询 Ctrl+C：5 秒内两次 = 确认中断；一次 = 提示后忽略 ---
            n_press = _recent_ctrl_count(now)
            if n_press >= 2:
                print("  ⚠ 5 秒内收到两次 Ctrl+C，确认手动终止...")
                del _CTRL_TIMES[:]
                _kill_process_tree(proc.pid)
                raise KeyboardInterrupt
            if n_press == 1 and now - _LAST_WARN_AT[0] > 5.0:
                _LAST_WARN_AT[0] = now
                print("  ⚠ 收到一次 Ctrl+C，已忽略，当前项目继续运行")
                print("     （如确实想退出，请在 5 秒内再按一次 Ctrl+C）")

            if proc.poll() is not None:
                break
            try:
                time.sleep(0.5)
            except KeyboardInterrupt:
                # 兜底路径（控制台处理程序未生效/非 Windows）：记一次按键
                _CTRL_TIMES.append(time.time())

        # 等读取线程把剩余输出抽完
        t_out.join(2.0)
        t_err.join(2.0)
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)

        if timed_out:
            _kill_process_tree(proc.pid)
            raise subprocess.TimeoutExpired([py_exe, main_py], timeout)

        result["duration_sec"] = round(time.time() - t0, 1)
        result["end"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if proc.returncode == 0:
            result["status"] = "SUCCESS"
        else:
            result["status"] = f"FAILED(rc={proc.returncode})"

        # 合并 stdout + stderr，确保 traceback 可见
        all_lines = (stdout or "").splitlines() + (stderr or "").splitlines()
        result["output_tail"] = "\n".join(all_lines[-50:])

        # 打印实时输出的最后部分
        tail = result["output_tail"]
        if tail:
            print(tail[-3000:])

    except KeyboardInterrupt:
        # 走到这里说明双击已确认：终止当前项目并退出整体调度
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)
        result["duration_sec"] = round(time.time() - t0, 1)
        result["end"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result["status"] = "ABORTED"
        result["output_tail"] = "用户双击 Ctrl+C 确认终止"
        print("  ⚠ 正在清理残留浏览器进程...")
        kill_leaked_browsers()
        raise

    except subprocess.TimeoutExpired:
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)
        result["duration_sec"] = round(time.time() - t0, 1)
        result["end"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result["status"] = f"TIMEOUT({timeout}s)"
        result["output_tail"] = f"项目超时，已终止（{timeout}秒）"
        print(f"  ⏰ 超时终止")
        kill_leaked_browsers()

    except Exception as e:
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)
        result["duration_sec"] = round(time.time() - t0, 1)
        result["end"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result["status"] = f"ERROR({type(e).__name__})"
        result["output_tail"] = str(e)
        print(f"  ❌ 异常: {e}")

    return result


def main():
    parser = argparse.ArgumentParser(description="批量调度 22 个爬虫项目")
    parser.add_argument("--only", type=str, default="", help="只跑指定项目（逗号分隔，如 A01,B03）")
    parser.add_argument("--skip", type=str, default="", help="跳过指定项目（逗号分隔）")
    parser.add_argument("--timeout", type=int, default=None, help="每个项目超时秒数（默认不设超时，等项目自然跑完）")
    args = parser.parse_args()

    root = SCRIPT_DIR
    only_set = set()
    if args.only:
        only_set = {x.strip() for x in args.only.split(",") if x.strip()}
    skip_set = set()
    if args.skip:
        skip_set = {x.strip() for x in args.skip.split(",") if x.strip()}

    # 确定要跑的项目列表
    projects = []
    for proj_id in PROJECT_VENV:
        if proj_id in EXCLUDED_PROJECTS:
            continue
        if only_set and proj_id not in only_set:
            continue
        if proj_id in skip_set:
            continue
        projects.append(proj_id)

    if not projects:
        print("没有要运行的项目。检查 --only/--skip 参数。")
        return

    print(f"共 {len(projects)} 个项目待运行")
    print(f"日期区间: START={PROJECT_START_DATE}  END={PROJECT_END_DATE or '今天'}")
    print(f"每项目超时: {'不限时（跑完为止）' if args.timeout is None else str(args.timeout) + 's'}")
    print()

    results = []
    i = 0
    while i < len(projects):
        proj_id = projects[i]
        try:
            print(f"\n[{i+1}/{len(projects)}] 开始运行...")
            venv_rel = PROJECT_VENV[proj_id]
            try:
                r = run_one_project(root, proj_id, venv_rel, args.timeout)
            except KeyboardInterrupt:
                print("\n调度已中断，将为已完成部分生成汇总报告...")
                results.append({
                    "project": proj_id,
                    "org": PROJECT_ORG.get(proj_id, proj_id),
                    "start": "",
                    "end": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "ABORTED",
                    "duration_sec": "",
                    "output_tail": "用户双击 Ctrl+C 确认终止",
                })
                break
            results.append(r)
            # 简短状态
            print(f"  → [{proj_id}] {r['org']}  状态={r['status']}  耗时={r['duration_sec']}s")
            i += 1
        except KeyboardInterrupt:
            # 项目间隙收到的中断（Windows 下控制台处理程序已接管，此路径是兜底）
            _CTRL_TIMES.append(time.time())
            if _recent_ctrl_count() >= 2:
                print("\n5 秒内两次 Ctrl+C，确认退出调度...")
                break
            print("  ⚠ 收到 Ctrl+C 信号，已忽略，继续调度")

    # 汇总报告
    report_path = os.path.join(root, "run_all_报告.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write(f"批量调度报告  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"日期区间: START={PROJECT_START_DATE}  END={PROJECT_END_DATE or '今天'}\n")
        f.write("=" * 70 + "\n\n")

        success_count = sum(1 for r in results if r["status"] == "SUCCESS")
        fail_count = len(results) - success_count
        f.write(f"总计: {len(results)} 个项目  成功: {success_count}  失败/异常: {fail_count}\n\n")

        f.write("-" * 70 + "\n")
        f.write(f"{'项目':<12s} {'机构':<12s} {'状态':<16s} {'耗时(s)':<10s}\n")
        f.write("-" * 70 + "\n")
        for r in results:
            f.write(f"{r['project']:<12s} {r['org']:<12s} {r['status']:<16s} {r['duration_sec']:<10}\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write("各项目输出摘要（最后30行）\n")
        f.write("=" * 70 + "\n")
        for r in results:
            f.write(f"\n--- [{r['project']}] {r['org']}  状态={r['status']} ---\n")
            f.write(r["output_tail"] or "(无输出)")
            f.write("\n")

    print(f"\n{'='*60}")
    print(f"全部完成。成功 {success_count}/{len(results)}")
    print(f"报告已保存: {report_path}")


if __name__ == "__main__":
    main()
