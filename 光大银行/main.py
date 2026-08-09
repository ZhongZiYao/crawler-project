
# --- UTF-8 stdout fix (Windows GBK emoji crash) ---
import io as _io, sys as _sys
if _sys.platform == 'win32':
    try:
        _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        _sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')
        _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace')
# --- end UTF-8 fix ---

import os
import re
import csv
import json
import time
import random
import shutil
import tempfile
import base64
import socket
import socketserver
import threading
import select
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests
from playwright.sync_api import Page, sync_playwright

# ============================================================
# 用户配置区
# ============================================================

# 机构名称（与台账统一）
INSTITUTE_NAME = "光大理财"

# ============================================================
# 紧急退出信号 (供 health_check.ps1 感知)
# ============================================================
# 退出码约定:
#   0 = 正常 (达到批次上限 / 自然结束)
#   10 = 网站被风控 (连续大量下载失败, 需要停止所有爬虫)
#   11 = 网络/DNS 异常
import atexit
import sys as _exit_sys

EXIT_CODE_BLOCKED = 10
EXIT_CODE_NETWORK = 11

# 紧急刹车文件: health_check.ps1 创建此文件, main.py 每爬一个产品检查一次
# (注意: 路径在 STATE_DIR 定义后再赋值, 见下方 _init_emergency_paths)
EMERGENCY_STOP_FILE = ""


def emergency_exit(reason: str, exit_code: int):
    """紧急退出, 写入状态文件供 health_check.ps1 后续观察"""
    try:
        flag = {
            "timestamp": now_time_str(),
            "exit_code": exit_code,
            "reason": reason,
        }
        with open(os.path.join(STATE_DIR, "emergency_exit.json"), "w", encoding="utf-8") as f:
            json.dump(flag, f, ensure_ascii=False, indent=2)
        print(f"\n🚨 [EMERGENCY] 紧急退出: {reason} (exit={exit_code})")
    except Exception:
        pass
    _exit_sys.exit(exit_code)


def schedule_6h_retry():
    """风控时自动注册 6 小时后重启 run_batch 的计划任务

    通过 schtasks 注册一个 ONCE 任务, 6 小时后启动 run_batch.ps1
    即使电脑睡眠/关机, Windows 计划任务服务也会到点触发 (休眠唤醒)

    如果当前进程没有管理员权限, 用 -Verb RunAs 重新启动一个提权版 PowerShell 来注册
    """
    import subprocess
    task_name = "CebwmRestart6h"
    main_py = os.path.join(SCRIPT_DIR, "run_batch.ps1")
    start_time = datetime.now() + timedelta(hours=6)
    time_str = start_time.strftime("%H:%M")
    date_str = start_time.strftime("%Y-%m-%d")

    # 写一个状态文件, 记录自动重试时间
    try:
        retry_info = {
            "scheduled_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "scheduled_at": now_time_str(),
            "reason": "auto-scheduled due to anti-crawler detection",
        }
        retry_path = os.path.join(STATE_DIR, "auto_retry.json")
        with open(retry_path, "w", encoding="utf-8") as f:
            json.dump(retry_info, f, ensure_ascii=False, indent=2)
        print(f"   [AUTO_RETRY] 状态已写入: {retry_path}")
    except Exception as e:
        print(f"   [AUTO_RETRY] 写状态失败: {e}")

    # 构造一个临时 PowerShell 脚本去注册任务
    ps_script = os.path.join(STATE_DIR, "_schedule_6h.ps1")
    ps_content = f'''chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$taskName = "{task_name}"
$mainPy = "{main_py}"
$timeStr = "{time_str}"
$dateStr = "{date_str}"
# 检查管理员
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {{
    Write-Host "NEED_ADMIN"
    Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$MyInvocation.MyCommand.Path) -Verb RunAs -Wait
    exit $LASTEXITCODE
}}
schtasks /Delete /TN $taskName /F 2>$null | Out-Null
$tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $mainPy + '"'
$out = schtasks /Create /SC ONCE /TN $taskName /TR $tr /ST $timeStr /SD $dateStr /F 2>&1
if ($LASTEXITCODE -eq 0) {{
    Write-Host "TASK_OK: $taskName scheduled at $timeStr $dateStr"
    exit 0
}} else {{
    Write-Host "TASK_FAILED: $out"
    exit 1
}}
'''
    try:
        with open(ps_script, "w", encoding="utf-8") as f:
            f.write(ps_content)
    except Exception as e:
        print(f"   [AUTO_RETRY] 写 PS 脚本失败: {e}")
        return

    # 用提权方式运行 PS 脚本
    try:
        # Start-Process -Verb RunAs 会弹 UAC 提示, 用户点"是"即可
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_script],
            capture_output=True, timeout=30, text=True,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if "TASK_OK" in output:
            print(f"   ✅ 已注册 6h 后重启任务: {task_name} (启动时间: {date_str} {time_str})")
        elif "NEED_ADMIN" in output:
            # 用户没点 UAC, 重试一次, 这时 UAC 会弹出来
            print("   ⚠️ 需要管理员权限, 弹 UAC 提示...")
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", ps_script],
                timeout=60,
            )
        else:
            print(f"   ⚠️ 注册计划任务失败: {output.strip()[:200]}")
    except Exception as e:
        print(f"   ⚠️ 注册计划任务异常: {e}")
    finally:
        # 清理临时脚本
        try:
            os.remove(ps_script)
        except Exception:
            pass

SITE_ROOT = "https://www.cebwm.com"
HOME_URL = SITE_ROOT

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
STATE_DIR = os.path.join(SCRIPT_DIR, "state")
# 保持 download_files 仅用于保存实际下载的文件；日志/进度/失败清单存放在 state 目录，便于迁移
LOG_CSV_PATH = os.path.join(STATE_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(STATE_DIR, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(STATE_DIR, "checkpoint.json")
FAILED_FILE = os.path.join(STATE_DIR, "failed_records.csv")

# 紧急刹车文件路径 (STATE_DIR 已定义后再赋值)
EMERGENCY_STOP_FILE = os.path.join(STATE_DIR, "emergency_stop.flag")
EMERGENCY_EXIT_FILE = os.path.join(STATE_DIR, "emergency_exit.json")

# 目标配置
TARGETS: List[Tuple[str, str, int, Optional[int]]] = [
    ("个人理财", "https://www.cebwm.com/wealth/grlc/index.html", 1, None)
]

# 浏览器模式（False=显示浏览器，True=无头）
SHOW_BROWSER = True
DEBUG_MODE = False

# 浏览器候选顺序（按顺序尝试，前者异常或被风控可自动切换到后者）
# 可通过环境变量覆盖，例如：PLAYWRIGHT_BROWSER_CANDIDATES=msedge,chrome,chromium
PLAYWRIGHT_BROWSER_CANDIDATES = os.getenv("PLAYWRIGHT_BROWSER_CANDIDATES", "msedge,chrome,chromium")

# 会话轮换（用于降低单浏览器连续访问触发风控的概率）
# 每处理 N 个产品后自动重建浏览器会话并切换到下一候选浏览器
SESSION_RECYCLE_EVERY_PRODUCTS = int(os.getenv("SESSION_RECYCLE_EVERY_PRODUCTS", "8").strip() or "8")
# 连续失败达到阈值时，立即触发会话重建
MAX_CONSECUTIVE_PRODUCT_FAILS = int(os.getenv("MAX_CONSECUTIVE_PRODUCT_FAILS", "3").strip() or "3")
# 连续下载失败达到阈值时, 直接紧急退出 (通知 health_check.ps1 停止一切爬虫)
# 阈值: 累计连续 N 个产品都没有成功下载任何 PDF 就触发
MAX_CONSECUTIVE_DOWNLOAD_FAILS = int(os.getenv("MAX_CONSECUTIVE_DOWNLOAD_FAILS", "5").strip() or "5")
# 会话轮换后冷却秒数
COOLDOWN_AFTER_RECYCLE_SECONDS = float(os.getenv("COOLDOWN_AFTER_RECYCLE_SECONDS", "4").strip() or "4")
# 单产品失败后，是否立即触发一次会话重建并重试当前产品
RETRY_PRODUCT_AFTER_RECYCLE = os.getenv("RETRY_PRODUCT_AFTER_RECYCLE", "1").strip().lower() in ("1", "true", "yes", "on")
# 连续解析到空产品页次数上限（达到上限视为失败中断，不标记 done）
MAX_EMPTY_PRODUCT_PAGE_RETRIES = int(os.getenv("MAX_EMPTY_PRODUCT_PAGE_RETRIES", "3").strip() or "3")
# 详情页就绪等待秒数（未出现关键元素则认为详情页不可用）
DETAIL_PAGE_READY_WAIT_SECONDS = float(os.getenv("DETAIL_PAGE_READY_WAIT_SECONDS", "8").strip() or "8")
# 批次模式：单次运行成功处理多少个产品后主动退出
BATCH_MAX_SUCCESS_PRODUCTS = int(os.getenv("BATCH_MAX_SUCCESS_PRODUCTS", "30000").strip() or "30000")
# 分工模式: 多人协作时, 只爬取列表总页数的前 N 份 (0~1 之间的小数)
#   1.0 = 全部 (默认)
#   0.5 = 前一半 (与同事各爬一半)
#   0.33 = 前 1/3
# 通过环境变量 PAGE_SHARE 覆盖, 例如 PAGE_SHARE=0.5
PAGE_SHARE = float(os.getenv("PAGE_SHARE", "1.0").strip() or "1.0")
# 协作时自己的"份额序号" (1-based), 用于按顺序切分
# 例: WORKER_INDEX=1,WORKER_TOTAL=2 -> 爬第 1 份
#     WORKER_INDEX=2,WORKER_TOTAL=2 -> 爬第 2 份
# 与 PAGE_SHARE 二选一: 设了 WORKER_TOTAL 就用等分模式
WORKER_INDEX = int(os.getenv("WORKER_INDEX", "0").strip() or "0")
WORKER_TOTAL = int(os.getenv("WORKER_TOTAL", "0").strip() or "0")
# 达到批次上限后是否直接退出进程；否则只冷却后继续下一批
BATCH_EXIT_AFTER_LIMIT = os.getenv("BATCH_EXIT_AFTER_LIMIT", "1").strip().lower() in ("1", "true", "yes", "on")
# 批次结束后的冷却时间（秒）
BATCH_COOLDOWN_SECONDS = float(os.getenv("BATCH_COOLDOWN_SECONDS", "300").strip() or "300")
# 每次重建浏览器时是否重置 requests 会话，避免复用 cookies
RESET_REQUEST_SESSION_ON_REINIT = os.getenv("RESET_REQUEST_SESSION_ON_REINIT", "1").strip().lower() in ("1", "true", "yes", "on")
# 可选：复用/保存浏览器 storage_state（跨浏览器复用 cookies/localStorage）
STORAGE_STATE_PATH = os.getenv("STORAGE_STATE_PATH", os.path.join(STATE_DIR, "storage_state.json")).strip()

# 请求与超时配置
TIMEOUT = 25
LIST_WAIT = 15
REQUEST_DELAY = 1.0
REQUEST_JITTER = 0.6
DOWNLOAD_DELAY = 0.8
DOWNLOAD_JITTER = 0.7

# 重试策略
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3

# 去重开关
SKIP_DOWNLOADED = True

# ============ 日期区间配置（集中管理，可本地覆盖）===========
# 从根目录 project_meta.py 集中读取；如需单独调整，取消下方注释
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("光大银行", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("光大银行", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-05-23"
# END_DATE   = "2026-06-30"

# 文件名关键词过滤（仅下载命中关键词的公告文件）
# 设计思路: 覆盖"费率/份额/业绩比较基准"三大类公告 + 相关调整/新设场景
# 注意: 每个关键词独立判断 (OR 关系), 文件名包含任一即下载
NOTICE_FILE_KEYWORDS: List[str] = [
    # --- 费率相关 ---
    "费", "费率", 
    # --- 份额相关 ---
    "份额", "新设份额", "份额调整", 
    # --- 业绩比较基准 ---
    "业绩比较基准", "业绩基准", "比较基准", "基准调整", "业绩比较基准调整"
]

# ============================================================
# 环境变量辅助函数
# ============================================================
def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return bool(default)
    return v in ("true", "1", "yes", "on")


# ============================================================
# 隧道代理配置区
# ============================================================
ENABLE_TUNNEL_PROXY = _env_bool("ENABLE_TUNNEL_PROXY", True)
TUNNEL_PROXY_HOST = os.getenv("TUNNEL_PROXY_HOST", "").strip()
TUNNEL_PROXY_PORT = os.getenv("TUNNEL_PROXY_PORT", "").strip()
TUNNEL_PROXY_USER = os.getenv("TUNNEL_PROXY_USER", "").strip()
TUNNEL_PROXY_PASS = os.getenv("TUNNEL_PROXY_PASS", "").strip()
TUNNEL_USE_PROXY_FOR_REQUESTS = _env_bool("TUNNEL_USE_PROXY_FOR_REQUESTS", True)
TUNNEL_USE_PROXY_FOR_BROWSER = _env_bool("TUNNEL_USE_PROXY_FOR_BROWSER", False)
# ============================================================

ALLOWED_HOSTS = {"www.cebwm.com", "cebwm.com"}

BROWSER_UA_MAP = {
    "msedge": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "chromium": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
}

# ============================================================
# 工具函数
# ============================================================


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def now_time_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_date(date_text: Any) -> str:
    text = str(date_text or "").strip()
    m = re.search(r"(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", text)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mm:02d}-{dd:02d}"
    return datetime.now().strftime("%Y-%m-%d")


def is_in_date_range(disclose_date: str):
    """返回 (是否在区间内, 是否过早可早停)"""
    d = normalize_date(disclose_date)
    upper = (END_DATE.strip() if END_DATE and END_DATE.strip()
             else datetime.now().strftime("%Y-%m-%d"))
    if START_DATE and d < START_DATE:
        return False, True      # 过早 → 可触发早停
    if d > upper:
        return False, False     # 过晚 → 仅跳过
    return True, False


def sanitize_filename(name: str, max_len: int = 180) -> str:
    name = re.sub(r"[\\/*?:\"<>|]", "_", str(name or ""))
    name = re.sub(r"\s+", " ", name).strip()
    if max_len > 0:
        name = name[:max_len]
    return name or "未命名"


def sanitize_filename_part(name: str, max_len: int = 200) -> str:
    """用于文件名片段：允许返回空字符串"""
    text = re.sub(r"[\\/*?:\"<>|]", "_", name or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:max_len]


def sleep_with_jitter(base: float, jitter: float):
    delay = max(0.0, float(base) + random.uniform(-jitter, jitter))
    time.sleep(delay)


def random_sleep(sec_range: Tuple[float, float]):
    time.sleep(random.uniform(sec_range[0], sec_range[1]))


def print_debug(msg: str):
    if DEBUG_MODE:
        print(msg, flush=True)


def should_download_notice_file(file_title: str, keywords: List[str]) -> bool:
    text = str(file_title or "").strip()
    if not text:
        return False
    for kw in keywords or []:
        if kw and kw in text:
            return True
    return False


# ============================================================
# 日志与进度管理
# ============================================================


def _log_header() -> List[str]:
    return [
        "机构名称",
        "公告名称",
        "公告类型",
        "披露日期",
        "下载时间",
        "状态",
        "来源链接",
        "保存路径",
        "unique_key",
        "产品代码",
        "文件大小(字节)",
    ]


def write_log_row(
    institute_name: str,
    notice_title: str,
    notice_type: str,
    disclose_date: str,
    status: str,
    source_link: str,
    save_path: str,
    unique_key: str,
    product_code: str = "",
    file_size: int = 0,
):
    row = [
        institute_name,
        sanitize_filename(notice_title, 500),
        sanitize_filename(notice_type, 120),
        normalize_date(disclose_date),
        now_time_str(),
        status,
        source_link,
        save_path,
        unique_key,
        sanitize_filename(product_code, 60),
        file_size,
    ]

    ensure_dir(os.path.dirname(LOG_CSV_PATH))
    exists = os.path.exists(LOG_CSV_PATH)
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(_log_header())
        writer.writerow(row)


def write_failed_row(reason: str, row: Dict[str, Any]):
    ensure_dir(os.path.dirname(FAILED_FILE))
    exists = os.path.exists(FAILED_FILE)
    header = ["time", "reason", "section", "title", "product_code", "source_link", "expected_path", "disclose_date"]
    line = [
        now_time_str(),
        reason,
        row.get("section", ""),
        row.get("title", ""),
        row.get("product_code", ""),
        row.get("source_link", ""),
        row.get("expected_path", ""),
        row.get("disclose_date", ""),
    ]
    with open(FAILED_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow(line)


def _build_failed_row_identity(section: str, product_code: str, source_link: str) -> str:
    sec = sanitize_filename_part(section or "", 120)
    code = str(product_code or "").strip()
    src = str(source_link or "").strip()
    return f"{sec}|{code}|{src}"


def purge_recovered_failed_rows(recovered_identities: Set[str]) -> int:
    if not recovered_identities or not os.path.exists(FAILED_FILE):
        return 0

    retry_reasons = {"detail_failed", "html_to_pdf_failed", "no_file_clue", "FAILED_STATUS_404", "FAILED_TOO_SMALL", "FAILED_EXCEPTION"}
    header = ["time", "reason", "section", "title", "product_code", "source_link", "expected_path", "disclose_date"]
    kept_rows: List[Dict[str, Any]] = []
    removed = 0

    try:
        with open(FAILED_FILE, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not isinstance(row, dict):
                    continue

                reason = str(row.get("reason") or "").strip()
                identity = _build_failed_row_identity(
                    str(row.get("section") or "").strip(),
                    str(row.get("product_code") or "").strip(),
                    str(row.get("source_link") or "").strip(),
                )

                if reason in retry_reasons and identity in recovered_identities:
                    removed += 1
                    continue

                kept_rows.append({k: row.get(k, "") for k in header})

        ensure_dir(os.path.dirname(FAILED_FILE))
        with open(FAILED_FILE, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(kept_rows)
    except Exception as exc:
        print(f"⚠️ [失败回补] 清理失败清单异常: {exc}")
        return 0

    return removed


def load_failed_notices_for_retry(downloaded_links: Set[str]) -> List[Dict[str, Any]]:
    if not os.path.exists(FAILED_FILE):
        return []

    retry_reasons = {"detail_failed", "html_to_pdf_failed", "no_file_clue", "FAILED_STATUS_404", "FAILED_TOO_SMALL", "FAILED_EXCEPTION"}
    dedup: Dict[str, Dict[str, Any]] = {}

    try:
        with open(FAILED_FILE, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not isinstance(row, dict):
                    continue

                section = str(row.get("section") or "").strip()
                if not section:
                    continue

                source_link = str(row.get("source_link") or "").strip()
                if SKIP_DOWNLOADED and source_link and source_link in downloaded_links:
                    continue

                product_code = str(row.get("product_code") or "").strip()
                title = sanitize_filename(str(row.get("title") or "").strip(), 500) or f"历史失败记录_{product_code}"
                key = f"{section}|{product_code}|{source_link}"
                failed_identity = _build_failed_row_identity(section, product_code, source_link)

                dedup[key] = {
                    "section": section,
                    "title": title,
                    "product_code": product_code,
                    "source_link": source_link,
                    "disclose_date": str(row.get("disclose_date") or "").strip(),
                    "_failed_identity": failed_identity,
                }
    except Exception as exc:
        print(f"⚠️ [失败回补] 读取失败清单异常: {exc}")
        return []

    return list(dedup.values())


def build_unique_key(
    institute_name: str, notice_type: str, title: str, disclose_date: str, product_code: str = ""
) -> str:
    return f"{institute_name}|{notice_type}|{sanitize_filename(title, 240)}|{disclose_date}|{product_code}"


def load_downloaded_links() -> Set[str]:
    done: Set[str] = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(line)
    return done


def save_downloaded_link(source_link: str):
    ensure_dir(os.path.dirname(PROGRESS_FILE))
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(source_link + "\n")


def load_checkpoint() -> Dict[str, Any]:
    if not os.path.exists(CHECKPOINT_FILE):
        return {"sections": {}}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("sections", {})
                return data
    except Exception:
        pass
    return {"sections": {}}


def save_checkpoint(data: Dict[str, Any]):
    ensure_dir(os.path.dirname(CHECKPOINT_FILE))
    tmp_file = CHECKPOINT_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, CHECKPOINT_FILE)


def get_section_checkpoint(checkpoint: Dict[str, Any], section_name: str) -> Dict[str, Any]:
    sections = checkpoint.setdefault("sections", {})
    sec = sections.get(section_name)
    if not isinstance(sec, dict):
        sec = {"next_page": 1, "next_row": 0, "done": False, "updated_at": ""}
        sections[section_name] = sec
    return sec


def update_section_checkpoint(checkpoint: Dict[str, Any], section_name: str, next_page: int, next_row: int, done: bool):
    sec = get_section_checkpoint(checkpoint, section_name)
    sec["next_page"] = max(1, int(next_page))
    sec["next_row"] = max(0, int(next_row))
    sec["done"] = bool(done)
    sec["updated_at"] = now_time_str()
    save_checkpoint(checkpoint)


# ============================================================
# 文件命名与路径
# ============================================================


def build_base_filename(
    institute_name: str,
    product_name: str,
    notice_type: str,
    product_code: str,
    disclose_date: str,
    sales_code: str = "",
) -> str:
    parts = [
        sanitize_filename_part(institute_name, 60),
        sanitize_filename_part(product_name, 120),
        sanitize_filename_part(notice_type, 40),
    ]
    
    # 产品代码如果与产品名相同则不重复添加
    if product_code and product_code.strip() != product_name.strip():
        parts.append(sanitize_filename_part(product_code, 60))
    
    # 销售代码如果与产品代码或产品名相同则不重复添加
    if sales_code and sales_code.strip() != product_code.strip() and sales_code.strip() != product_name.strip():
        parts.append(sanitize_filename_part(sales_code, 60))
    
    parts.append(sanitize_filename_part(f"披露日期：{normalize_date(disclose_date)}", 40))
    
    parts = [p for p in parts if p]
    if not parts:
        parts = [sanitize_filename_part(institute_name, 60), sanitize_filename_part(notice_type, 40)]
    return "_".join(parts)


def build_unique_save_path(folder: str, base_name: str, extension: str) -> str:
    ext = extension if extension.startswith(".") else f".{extension}"
    ext = ext.lower()
    idx = 0
    while True:
        name = f"{base_name}{ext}" if idx == 0 else f"{base_name}_{idx}{ext}"
        save_path = os.path.join(folder, name)
        if not os.path.exists(save_path):
            return save_path
        idx += 1


# ============================================================
# 代理相关
# ============================================================


class _UpstreamTunnelProxyHandler(socketserver.StreamRequestHandler):
    def _read_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        while True:
            raw = self.rfile.readline().decode("iso-8859-1", errors="ignore")
            if not raw or raw in {"\r\n", "\n"}:
                break
            if ":" not in raw:
                continue
            key, value = raw.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return headers

    def _make_upstream_socket(self) -> socket.socket:
        sock = socket.create_connection((self.server.upstream_host, self.server.upstream_port), timeout=20)  # type: ignore[attr-defined]
        sock.settimeout(None)
        return sock

    def _upstream_auth_header(self) -> str:
        user = getattr(self.server, "upstream_user", "") or ""  # type: ignore[attr-defined]
        pwd = getattr(self.server, "upstream_pass", "") or ""  # type: ignore[attr-defined]
        token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
        return f"Proxy-Authorization: Basic {token}\r\n"

    def _relay(self, left: socket.socket, right: socket.socket):
        sockets = [left, right]
        try:
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 30)
                if exceptional:
                    break
                if not readable:
                    continue
                for src in readable:
                    try:
                        data = src.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    dst = right if src is left else left
                    try:
                        dst.sendall(data)
                    except OSError:
                        return
        finally:
            try:
                left.close()
            except Exception:
                pass
            try:
                right.close()
            except Exception:
                pass

    def _handle_connect(self, target: str, headers: Dict[str, str]):
        upstream = self._make_upstream_socket()
        request = (
            f"CONNECT {target} HTTP/1.1\r\n"
            f"Host: {target}\r\n"
            f"Proxy-Connection: keep-alive\r\n"
            f"{self._upstream_auth_header()}"
            "\r\n"
        )
        upstream.sendall(request.encode("iso-8859-1"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = upstream.recv(4096)
            if not chunk:
                break
            response += chunk
        status_line = response.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="ignore") if response else ""
        if "200" not in status_line:
            self.wfile.write(response or b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self.wfile.flush()
        self._relay(self.connection, upstream)

    def _handle_http(self, method: str, target: str, version: str, headers: Dict[str, str]):
        upstream = self._make_upstream_socket()
        body = b""
        content_length = int(headers.get("content-length", "0") or 0)
        if content_length > 0:
            body = self.rfile.read(content_length)
        request_lines = [f"{method} {target} {version}"]
        for key, value in headers.items():
            if key in {"proxy-connection", "proxy-authorization"}:
                continue
            request_lines.append(f"{key.title()}: {value}")
        request_lines.append("Connection: close")
        request_lines.append("Proxy-Connection: keep-alive")
        request_lines.append(self._upstream_auth_header().strip())
        request_lines.append("")
        request_lines.append("")
        upstream.sendall("\r\n".join(request_lines).encode("iso-8859-1") + body)
        while True:
            chunk = upstream.recv(65536)
            if not chunk:
                break
            self.wfile.write(chunk)
        self.wfile.flush()
        upstream.close()

    def handle(self):
        request_line = self.rfile.readline().decode("iso-8859-1", errors="ignore").strip()
        if not request_line:
            return
        parts = request_line.split(" ", 2)
        if len(parts) != 3:
            return
        method, target, version = parts
        headers = self._read_headers()
        if method.upper() == "CONNECT":
            self._handle_connect(target, headers)
            return
        self._handle_http(method, target, version, headers)


class _UpstreamTunnelProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, upstream_host: str, upstream_port: int, upstream_user: str = "", upstream_pass: str = ""):
        super().__init__(server_address, _UpstreamTunnelProxyHandler)
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.upstream_user = upstream_user
        self.upstream_pass = upstream_pass


def build_tunnel_proxy_addr() -> str:
    host = str(TUNNEL_PROXY_HOST or "").strip()
    port = str(TUNNEL_PROXY_PORT or "").strip()
    if not host or not port:
        return ""
    raw = f"{host}:{port}"
    if TUNNEL_PROXY_USER and TUNNEL_PROXY_PASS:
        return f"{TUNNEL_PROXY_USER}:{TUNNEL_PROXY_PASS}@{raw}"
    return raw


def get_request_proxies() -> Optional[Dict[str, str]]:
    if not ENABLE_TUNNEL_PROXY or not TUNNEL_USE_PROXY_FOR_REQUESTS:
        return None
    p = build_tunnel_proxy_addr()
    if not p:
        return None
    proxy_url = f"http://{p}"
    return {"http": proxy_url, "https": proxy_url}


# ============================================================
# URL 工具
# ============================================================


def is_allowed_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in ALLOWED_HOSTS or host.endswith(".cebwm.com")
    except Exception:
        return False


def is_probable_file_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower().strip()
    if re.search(r"\.(pdf|doc|docx|xls|xlsx|zip|rar)(?:$|\?)", u):
        return True
    if any(k in u for k in ["/filedir/", "/upload/", "/resource/cms/"]):
        if not re.search(r"\.html?(?:$|\?)", u):
            return True
    return False


def extract_pdf_links_from_html(html: str) -> List[str]:
    if not html:
        return []
    candidates: List[str] = []
    for m in re.findall(r"https?://[^\s\"'<>]+\.pdf", html, re.I):
        candidates.append(m)
    for m in re.findall(r"/[^\s\"'<>]+\.pdf", html, re.I):
        candidates.append(SITE_ROOT + m)

    out: List[str] = []
    seen = set()
    for u in candidates:
        u = (u or "").strip()
        if not u or not is_allowed_url(u):
            continue
        k = u.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(u)
    return out


# ============================================================
# 数据类
# ============================================================


@dataclass
class ProductItem:
    name: str
    code: str
    page: int = 1
    row_index: int = 0


# ============================================================
# 爬虫主类
# ============================================================


class CebwmCrawler:
    def __init__(self):
        self.playwright: Any = None
        self.pw_browser: Any = None
        self.context: Any = None
        self.browser: Any = None
        self.browser_profile_dir: Optional[str] = None
        self.local_proxy_server: Optional[_UpstreamTunnelProxyServer] = None
        self.local_proxy_thread: Optional[threading.Thread] = None
        self.local_proxy_addr: str = ""
        self.session = requests.Session()
        self.session.trust_env = False
        self._reset_request_session()
        self.downloaded_links = load_downloaded_links()
        self.checkpoint = load_checkpoint()
        self.browser_candidates = self._parse_browser_candidates()
        self.browser_idx = -1
        self.browser_name = ""
        self.storage_state_path = STORAGE_STATE_PATH or ""

    def _reset_request_session(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = BROWSER_UA_MAP.get("chromium") or BROWSER_UA_MAP["chrome"]
        self.session.headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"

    def _sync_browser_cookies_to_session(self):
        """把当前浏览器上下文里的 cookie 同步到 requests 会话。"""
        if not self.context:
            return
        try:
            cookies = self.context.cookies()
        except Exception:
            cookies = []
        if not cookies:
            return
        jar = self.session.cookies
        try:
            jar.clear()
        except Exception:
            pass
        for ck in cookies:
            try:
                jar.set(
                    ck.get("name", ""),
                    ck.get("value", ""),
                    domain=ck.get("domain") or "",
                    path=ck.get("path") or "/",
                )
            except Exception:
                continue

    def save_storage_state(self):
        if not self.context or not self.storage_state_path:
            return
        try:
            ensure_dir(os.path.dirname(self.storage_state_path))
            self.context.storage_state(path=self.storage_state_path)
            print(f"[STATE] 已保存 storage_state: {self.storage_state_path}")
        except Exception as e:
            print(f"[STATE] 保存 storage_state 失败: {e}")

    def _parse_browser_candidates(self) -> List[str]:
        raw = [x.strip().lower() for x in str(PLAYWRIGHT_BROWSER_CANDIDATES or "").split(",") if x.strip()]
        allowed = {"msedge", "chrome", "chromium"}
        out: List[str] = []
        seen: Set[str] = set()
        for item in raw:
            if item in allowed and item not in seen:
                out.append(item)
                seen.add(item)
        if not out:
            out = ["msedge", "chrome", "chromium"]
        return out

    def start_local_proxy(self) -> str:
        if not ENABLE_TUNNEL_PROXY:
            return ""
        host = str(TUNNEL_PROXY_HOST or "").strip()
        port = int(str(TUNNEL_PROXY_PORT or "0").strip() or "0")
        if not host or not port:
            return ""

        if self.local_proxy_server:
            return self.local_proxy_addr

        server = _UpstreamTunnelProxyServer(("127.0.0.1", 0), host, port, TUNNEL_PROXY_USER, TUNNEL_PROXY_PASS)
        thread = threading.Thread(target=server.serve_forever, name="cebwm-local-proxy", daemon=True)
        thread.start()
        self.local_proxy_server = server
        self.local_proxy_thread = thread
        self.local_proxy_addr = f"127.0.0.1:{server.server_address[1]}"
        return self.local_proxy_addr

    def init_browser(self, start_index: int = 0):
        if RESET_REQUEST_SESSION_ON_REINIT:
            self._reset_request_session()

        self.browser_profile_dir = tempfile.mkdtemp(prefix="cebwm_profile_")
        self.playwright = sync_playwright().start()

        launch_error: Optional[Exception] = None
        local_proxy = self.start_local_proxy()
        
        proxy_option = None
        if TUNNEL_USE_PROXY_FOR_BROWSER and local_proxy:
            proxy_option = {"server": f"http://{local_proxy}"}
            print(f"[PROXY] 浏览器代理已启用: {local_proxy}")
        elif ENABLE_TUNNEL_PROXY and not TUNNEL_USE_PROXY_FOR_BROWSER:
            print("[PROXY] 浏览器代理已禁用")

        if not self.browser_candidates:
            self.browser_candidates = ["msedge", "chrome", "chromium"]

        ordered_candidates = self.browser_candidates[start_index:] + self.browser_candidates[:start_index]

        for candidate in ordered_candidates:
            try:
                launch_kwargs: Dict[str, Any] = {
                    "headless": not SHOW_BROWSER,
                    "user_agent": BROWSER_UA_MAP.get(candidate, BROWSER_UA_MAP["chromium"]),
                }
                context_kwargs: Dict[str, Any] = {
                    "locale": "zh-CN",
                }
                browser_type = None

                if candidate in ("msedge", "chrome"):
                    browser_type = self.playwright.chromium
                    launch_kwargs["channel"] = candidate
                    launch_kwargs["args"] = ["--disable-blink-features=AutomationControlled", "--window-position=-32000,-32000"]
                elif candidate == "chromium":
                    browser_type = self.playwright.chromium
                    launch_kwargs["args"] = ["--disable-blink-features=AutomationControlled", "--window-position=-32000,-32000"]
                else:
                    continue

                if proxy_option:
                    launch_kwargs["proxy"] = proxy_option

                context = browser_type.launch_persistent_context(
                    self.browser_profile_dir, **launch_kwargs, **context_kwargs
                )
                self.pw_browser = None
                self.context = context
                self.browser_name = candidate
                self.browser_idx = self.browser_candidates.index(candidate)
                print(f"[BROWSER] 已启动: {candidate}")
                break
            except Exception as exc:
                launch_error = exc
                print(f"[BROWSER] 启动失败: {candidate} -> {exc}")

        if not self.context:
            raise RuntimeError(f"Playwright 浏览器启动失败: {launch_error}")

        try:
            self.context.set_default_timeout(TIMEOUT * 1000)
            self.context.set_default_navigation_timeout(max(60000, TIMEOUT * 1000))
        except Exception:
            pass

        self.browser = None
        try:
            pages = list(getattr(self.context, "pages", None) or [])
            self.browser = pages[0] if pages else self.context.new_page()
        except Exception:
            self.browser = self.context.new_page()

        try:
            self.browser.set_viewport_size({"width": 1366, "height": 900})
        except Exception:
            pass
        try:
            self.browser.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9"})
        except Exception:
            pass

        self._sync_browser_cookies_to_session()

    def switch_to_next_browser_and_reload(self, target_url: str) -> bool:
        """当前浏览器加载失败时，切换到下一个候选浏览器并重载目标页面。"""
        if not self.browser_candidates or len(self.browser_candidates) <= 1:
            return False

        start_idx = self.browser_idx if self.browser_idx >= 0 else 0
        for step in range(1, len(self.browser_candidates) + 1):
            next_idx = (start_idx + step) % len(self.browser_candidates)
            if next_idx == start_idx:
                break

            try:
                self.close()
            except Exception:
                pass

            try:
                self.init_browser(start_index=next_idx)
                self.browser.goto(target_url)
                self.browser.wait_for_load_state("domcontentloaded", timeout=60000)
                time.sleep(2.5)
                if self.wait_products_ready():
                    print(f"[BROWSER] 自动切换成功，当前使用: {self.browser_name}")
                    return True
                print(f"[BROWSER] 切换后仍未加载出列表: {self.browser_name}")
            except Exception as e:
                print(f"[BROWSER] 切换尝试失败: {e}")

        # 所有浏览器都失败, 返回 False 让调用方继续
        print("[BROWSER] 所有候选浏览器均无法加载列表")
        return False

    def recycle_browser_session(self, target_url: str, page_num: int, reason: str = "") -> bool:
        """重建浏览器会话并尽量恢复到指定产品分页。"""
        try:
            print(f"[BROWSER] 触发会话轮换: reason={reason or 'unknown'}")
            next_idx = 0
            if self.browser_candidates:
                if self.browser_idx >= 0:
                    next_idx = (self.browser_idx + 1) % len(self.browser_candidates)

            try:
                self.close()
            except Exception:
                pass

            self.init_browser(start_index=next_idx)
            self.browser.goto(target_url)
            self.browser.wait_for_load_state("domcontentloaded", timeout=60000)
            time.sleep(2.5)

            if not self.wait_products_ready():
                print(f"[BROWSER] 轮换后首屏仍失败，继续尝试候选浏览器")
                if not self.switch_to_next_browser_and_reload(target_url):
                    return False

            if page_num > 1 and not self.goto_product_page(page_num):
                print(f"[BROWSER] 轮换后恢复第 {page_num} 页失败")
                return False

            if COOLDOWN_AFTER_RECYCLE_SECONDS > 0:
                time.sleep(COOLDOWN_AFTER_RECYCLE_SECONDS)
            print(f"[BROWSER] 会话轮换完成，当前浏览器: {self.browser_name}")
            return True
        except Exception as e:
            print(f"[BROWSER] 会话轮换异常: {e}")
            return False

    def close(self):
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None
        if self.pw_browser:
            try:
                self.pw_browser.close()
            except Exception:
                pass
            self.pw_browser = None
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        if self.local_proxy_server:
            try:
                self.local_proxy_server.shutdown()
                self.local_proxy_server.server_close()
            except Exception:
                pass
            self.local_proxy_server = None
            self.local_proxy_thread = None
            self.local_proxy_addr = ""
        if self.browser_profile_dir:
            try:
                shutil.rmtree(self.browser_profile_dir, ignore_errors=True)
            except Exception:
                pass
            self.browser_profile_dir = None

    def get_tab_ids(self) -> List[str]:
        try:
            pages = list(getattr(self.context, "pages", None) or [])
            return [str(id(page)) for page in pages]
        except Exception:
            return []

    def wait_for_new_tab(
        self,
        old_ids: set,
        timeout: float = 10.0,
        title_contains: Optional[str] = None,
        url_contains: Optional[str] = None,
    ) -> Any:
        ddl = time.time() + timeout
        while time.time() < ddl:
            try:
                tabs = list(getattr(self.context, "pages", None) or [])
            except Exception:
                tabs = []
            for tab in tabs:
                try:
                    tab_id = str(id(tab))
                    if tab_id in old_ids:
                        continue
                    title = (tab.title() or "").strip()
                    url = (tab.url or "").strip()
                    if title_contains and title_contains not in title:
                        continue
                    if url_contains and url_contains not in url:
                        continue
                    return tab
                except Exception:
                    continue
            time.sleep(0.25)
        return None

    def wait_products_ready(self) -> bool:
        ddl = time.time() + LIST_WAIT
        while time.time() < ddl:
            try:
                rows = self.browser.locator("#finance_tb1 tbody.wealthlccp_list1 tr")
                count = rows.count()
                if count > 0:
                    print(f"[DEBUG] 找到 {count} 条产品记录")
                    
                    # 检查是否有实际数据行（非表头）
                    non_header_rows = self.browser.locator("#finance_tb1 tbody.wealthlccp_list1 tr:not(.first_tr)")
                    non_header_count = non_header_rows.count()
                    print(f"[DEBUG] 非表头行数: {non_header_count}")
                    
                    if non_header_count > 0:
                        self.save_storage_state()
                        return True
                    
                    # 只有表头，继续等待
                    print(f"[DEBUG] 只有表头，继续等待...")
            except Exception as e:
                print(f"[DEBUG] wait_products_ready 异常: {str(e)}")
            time.sleep(0.5)
        
        try:
            html = self.browser.content()
            if html and len(html) < 5000:
                print(f"[DEBUG] 页面内容过短 ({len(html)} 字符)，可能加载失败")
            print(f"[DEBUG] 页面标题: {self.browser.title()}")
            print(f"[DEBUG] 当前URL: {self.browser.url}")
            
            # 尝试触发页面滚动加载更多内容
            print("[DEBUG] 尝试触发页面滚动...")
            try:
                self.browser.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2.0)
                
                rows = self.browser.locator("#finance_tb1 tbody.wealthlccp_list1 tr:not(.first_tr)")
                count = rows.count()
                print(f"[DEBUG] 滚动后非表头行数: {count}")
                
                if count > 0:
                    return True
            except Exception as scroll_e:
                print(f"[DEBUG] 滚动触发失败: {str(scroll_e)}")
                
        except Exception as e:
            print(f"[DEBUG] 获取页面信息失败: {str(e)}")
        
        return False

    def parse_products_on_page(self, page_num: int) -> List[ProductItem]:
        out: List[ProductItem] = []
        try:
            all_rows = self.browser.locator("#finance_tb1 tbody.wealthlccp_list1 tr").evaluate_all(
                """
                rows => rows.map((tr, idx) => {
                    const cls = tr.className || '';
                    const tds = tr.querySelectorAll('td');
                    const name = (tds[0]?.innerText || '').trim();
                    const code = (tds[1]?.innerText || '').trim();
                    let detail_url = '';
                    tr.querySelectorAll('a').forEach(a => {
                        const href = (a.getAttribute('href') || '').trim();
                        if (href.includes('/wealth/lcxx/')) detail_url = href;
                    });
                    return {name, code, detail_url, row_index: idx + 1, has_first_tr: cls.includes('first_tr'), class_name: cls};
                })
                """
            )
            
            print(f"[DEBUG] 所有行数据: {all_rows}")
            
            data = [row for row in all_rows if not row.get('has_first_tr')]
        except Exception as e:
            print(f"[DEBUG] parse_products_on_page 异常: {str(e)}")
            return out

        print(f"[DEBUG] 过滤后数据: {data}")
        
        # 重新计算行索引（过滤掉 first_tr 后的实际行号）
        actual_row_index = 0
        for row in data or []:
            name = str(row.get("name") or "").strip()
            code = str(row.get("code") or "").strip()
            if name and code:
                actual_row_index += 1
                out.append(ProductItem(name=name, code=code, page=page_num, row_index=actual_row_index))
                print(f"[DEBUG] 产品解析: 行{actual_row_index} - {name} ({code})")
        return out

    def open_product_detail_tab(self, product: ProductItem):
        if not self.browser:
            return None, ""

        try:
            rows = self.browser.locator("#finance_tb1 tbody.wealthlccp_list1 tr")
        except Exception:
            return None, ""

        filtered_count = 0
        target_row = None
        total = rows.count()
        for idx in range(total):
            candidate = rows.nth(idx)
            try:
                cls = (candidate.get_attribute("class") or "").strip()
            except Exception:
                cls = ""
            if "first_tr" in cls:
                continue
            filtered_count += 1
            if filtered_count == product.row_index:
                target_row = candidate
                break

        if not target_row:
            return None, ""

        try:
            btn = target_row.locator("a.lccp_buybutton").first
        except Exception:
            btn = None
        if not btn:
            return None, ""

        try:
            with self.browser.expect_popup(timeout=10000) as popup_info:
                btn.click()
            tab = popup_info.value
        except Exception:
            return None, ""

        time.sleep(1.5)
        return tab, str(id(tab))

    def is_detail_tab_ready(self, detail_tab: Any) -> bool:
        """判断产品详情页是否真正加载成功（而非风控空页）。"""
        if not detail_tab:
            return False

        try:
            detail_tab.wait_for_load_state("domcontentloaded", timeout=int(DETAIL_PAGE_READY_WAIT_SECONDS * 1000))
        except Exception:
            pass

        # 正常详情页应含“产品公告”入口
        try:
            detail_tab.wait_for_selector("a.jjgg_li", timeout=int(DETAIL_PAGE_READY_WAIT_SECONDS * 1000))
            return True
        except Exception:
            pass

        try:
            html = (detail_tab.content() or "").lower()
        except Exception:
            html = ""

        block_keywords = [
            "访问过于频繁",
            "系统繁忙",
            "操作频繁",
            "异常",
            "稍后再试",
            "验证码",
            "forbidden",
            "access denied",
        ]
        if any(k in html for k in block_keywords):
            return False

        # 没有关键入口且页面内容很短，通常是空壳页
        if len(html) < 2500:
            return False

        return False

    def open_notice_tab(self, detail_tab: Any):
        if not detail_tab:
            return None, ""

        try:
            btn = detail_tab.locator("a.jjgg_li").first
        except Exception:
            btn = None
        if not btn:
            return None, ""

        try:
            with detail_tab.expect_popup(timeout=10000) as popup_info:
                btn.click()
            tab = popup_info.value
        except Exception:
            return None, ""

        time.sleep(1.2)
        return tab, str(id(tab))

    def get_notice_items(self, notice_tab: Any) -> List[Any]:
        if not notice_tab:
            return []
        try:
            locator = notice_tab.locator("li.jjgg_li a.gg_nr")
            count = locator.count()
            return [locator.nth(i) for i in range(count)]
        except Exception:
            return []

    def get_notice_total_pages(self, notice_tab: Any) -> int:
        """获取公告列表总页数"""
        if not notice_tab:
            return 1
        try:
            # 查找总页数 <span id="totalpage">4</span>
            total_page_elem = notice_tab.locator("#totalpage").first
            if total_page_elem:
                total_page_text = total_page_elem.inner_text().strip()
                if total_page_text.isdigit():
                    return int(total_page_text)
        except Exception:
            pass
        
        try:
            # 查找分页区域中的页数信息，格式如 "1 / 5"
            pagination_text = notice_tab.locator(".pageInfo").inner_text()
            match = re.search(r"/\s*(\d+)", pagination_text)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        
        return 1

    def click_notice_next_page(self, notice_tab: Any) -> bool:
        """点击公告列表的下一页"""
        if not notice_tab:
            return False
        try:
            # 查找下一页按钮 <span class="page_right" onclick="goPage(&quot;next&quot;)">&gt;</span>
            next_btn = notice_tab.locator(".page_right").first
            if next_btn:
                next_btn.click()
                time.sleep(1.5)
                return True
        except Exception:
            pass
        
        try:
            # 尝试其他选择器
            next_btn = notice_tab.locator("a.nextPage").first
            if next_btn and next_btn.is_enabled():
                next_btn.click()
                time.sleep(1.0)
                return True
        except Exception:
            pass
        
        try:
            next_btn = notice_tab.locator(".pageNext").first
            if next_btn and next_btn.is_enabled():
                next_btn.click()
                time.sleep(1.0)
                return True
        except Exception:
            pass
        
        return False

    def open_notice_detail_tab(self, notice_tab: Any, notice_item: Any):
        if not notice_tab or not notice_item:
            return None, ""

        try:
            with notice_tab.expect_popup(timeout=10000) as popup_info:
                notice_item.click()
            tab = popup_info.value
        except Exception:
            return None, ""

        time.sleep(1.2)
        return tab, str(id(tab))

    def get_notice_file_link(self, notice_detail_tab: Any):
        if not notice_detail_tab:
            return None
        try:
            locator = notice_detail_tab.locator("div.xilan_con a")
            if locator.count() > 0:
                return locator.first
            locator = notice_detail_tab.locator(".xilan_con a")
            if locator.count() > 0:
                return locator.first
        except Exception:
            return None
        return None

    def get_notice_file_links(self, notice_detail_tab: Any) -> List[Any]:
        """获取公告详情页中的所有文件链接"""
        if not notice_detail_tab:
            return []
        try:
            locator = notice_detail_tab.locator("div.xilan_con a")
            count = locator.count()
            if count > 0:
                return [locator.nth(i) for i in range(count)]
            locator = notice_detail_tab.locator(".xilan_con a")
            count = locator.count()
            if count > 0:
                return [locator.nth(i) for i in range(count)]
        except Exception:
            pass
        return []

    def click_notice_file(self, notice_detail_tab: Any, file_link: Any):
        if not notice_detail_tab or not file_link:
            return None, ""

        try:
            href = file_link.get_attribute("href")
            datas_ts = file_link.get_attribute("datas-ts")
            print(f"[DEBUG] 文件链接 href: {href}, datas-ts: {datas_ts}")
            
            original_url = notice_detail_tab.url
            
            file_link.click()
            time.sleep(2.0)
            
            current_url = notice_detail_tab.url
            if current_url != original_url and current_url.lower().endswith(".pdf"):
                print(f"[DEBUG] 页面直接导航到PDF: {current_url}")
                return notice_detail_tab, str(id(notice_detail_tab))
            
            pages = self.context.pages
            for page in pages:
                page_url = page.url
                if page_url.lower().endswith(".pdf") and page_url != original_url:
                    print(f"[DEBUG] 在新页面找到PDF: {page_url}")
                    return page, str(id(page))
            
            print(f"[DEBUG] 点击后URL: {current_url}")
            return notice_detail_tab, str(id(notice_detail_tab))
        except Exception as e:
            print(f"[DEBUG] click_notice_file 异常: {str(e)}")
            return None, ""

    def get_product_list_total_pages(self) -> int:
        """获取产品列表的总页数 (用于分工爬取前 N 份)
        
        光大银行信息披露页的实际分页元素:
          - #totalpage1    <span id="totalpage1">532</span>
          - .pageInfo     "1 / 5"
          - 文本 "/ 共 N 页" 或 "共 N 页"
          - JS 全局变量 totalPage / pageCount
        """
        if not self.browser:
            return 0
        # 方式 1: #totalpage1 元素 (光大银行实际使用的 ID)
        try:
            el = self.browser.locator("#totalpage1").first
            if el and el.is_visible():
                txt = (el.inner_text() or "").strip()
                if txt.isdigit():
                    return int(txt)
        except Exception:
            pass

        # 方式 2: #totalpage 元素 (与公告列表一致)
        try:
            el = self.browser.locator("#totalpage").first
            if el and el.is_visible():
                txt = (el.inner_text() or "").strip()
                if txt.isdigit():
                    return int(txt)
        except Exception:
            pass

        # 方式 3: .pageInfo "1 / 5"
        try:
            el = self.browser.locator(".pageInfo").first
            if el and el.is_visible():
                txt = (el.inner_text() or "").strip()
                m = re.search(r"/\s*(\d+)", txt)
                if m:
                    return int(m.group(1))
        except Exception:
            pass

        # 方式 4: JS 直接读全局变量 (部分网站有 totalPage / pageCount)
        try:
            for var_name in ["totalPage", "pageCount", "totalpage", "TOTAL_PAGE"]:
                val = self.browser.evaluate(f"typeof {var_name} !== 'undefined' ? {var_name} : null")
                if isinstance(val, (int, float)) and val > 0:
                    return int(val)
        except Exception:
            pass

        # 方式 5: 通过 HTML 文本搜 "共 N 页"
        try:
            html = (self.browser.content() or "")
            m = re.search(r"共\s*(\d+)\s*页", html)
            if m:
                return int(m.group(1))
            m = re.search(r"total\s*page[s]?\s*[:：]\s*(\d+)", html, re.I)
            if m:
                return int(m.group(1))
        except Exception:
            pass

        print(f"  [WARN] 无法自动获取产品列表总页数, 返回 0 (视为不分页)")
        return 0

    def compute_page_range(self, total_pages: int) -> Tuple[int, int]:
        """根据 PAGE_SHARE / WORKER_INDEX 计算 [start_page, end_page] 闭区间
        
        分工模式 (WORKER_TOTAL > 0):
          WORKER_INDEX=1, WORKER_TOTAL=2, total=10 -> (1, 5)
          WORKER_INDEX=2, WORKER_TOTAL=2, total=10 -> (6, 10)
        比例模式 (PAGE_SHARE < 1):
          PAGE_SHARE=0.5, total=10 -> (1, 5)
        """
        if total_pages <= 1:
            return (1, max(1, total_pages))

        if WORKER_TOTAL > 1 and 1 <= WORKER_INDEX <= WORKER_TOTAL:
            # 等分模式
            chunk = (total_pages + WORKER_TOTAL - 1) // WORKER_TOTAL  # 向上取整
            start = (WORKER_INDEX - 1) * chunk + 1
            end = min(WORKER_INDEX * chunk, total_pages)
            print(f"  [SPLIT] 等分模式: worker {WORKER_INDEX}/{WORKER_TOTAL}, chunk={chunk}")
            return (start, end)

        if 0 < PAGE_SHARE < 1.0:
            end = max(1, int(total_pages * PAGE_SHARE))
            print(f"  [SPLIT] 比例模式: PAGE_SHARE={PAGE_SHARE}, end={end}/{total_pages}")
            return (1, end)

        return (1, total_pages)

    def goto_product_page(self, page_num: int) -> bool:
        try:
            ret = self.browser.evaluate(
                """
                pageNum => {
                    try {
                        if (typeof goPage1 === 'function') {
                            goPage1(pageNum);
                            return 'OK';
                        }
                        return 'NO_FUNC';
                    } catch (e) {
                        return 'ERR:' + e;
                    }
                }
                """,
                int(page_num),
            )
            print_debug(f"[DEBUG] goPage1({page_num}) => {ret}")
        except Exception:
            return False
        time.sleep(0.8)
        return self.wait_products_ready()

    def download_one(self, url: str, save_path: str) -> Tuple[bool, str, int]:
        ensure_dir(os.path.dirname(save_path))
        req_headers = {"Referer": SITE_ROOT}
        proxies = get_request_proxies()
        file_size = 0
        
        for retry in range(1, DOWNLOAD_RETRY + 1):
            try:
                with self.session.get(url, timeout=TIMEOUT, stream=True, headers=req_headers, proxies=proxies) as r:
                    if r.status_code != 200:
                        if retry < DOWNLOAD_RETRY:
                            print(f"   ⚠️ [下载重试] 第{retry}次失败，状态码: {r.status_code}")
                            time.sleep(RETRY_WAIT_SECONDS)
                            continue
                        return False, f"FAILED_STATUS_{r.status_code}", 0
                    
                    content = r.content
                    file_size = len(content)
                    if file_size < 128:
                        if retry < DOWNLOAD_RETRY:
                            print(f"   ⚠️ [下载重试] 第{retry}次失败，文件过小({file_size}字节)")
                            time.sleep(RETRY_WAIT_SECONDS)
                            continue
                        return False, "FAILED_TOO_SMALL", file_size
                    
                    with open(save_path, "wb") as f:
                        f.write(content)
                    return True, "downloaded", file_size
            except Exception as e:
                if retry < DOWNLOAD_RETRY:
                    print(f"   ⚠️ [下载重试] 第{retry}次失败: {str(e)}")
                    time.sleep(RETRY_WAIT_SECONDS)
                    continue
                return False, f"FAILED_EXCEPTION_{str(e)}", 0
        return False, "FAILED_UNKNOWN", file_size

    def run(self):
        print("=" * 60)
        print("[INFO] 光大理财抓取下载（Playwright）")
        print("[INFO] 流程：列表 -> 详情 -> 产品公告 -> 公告详情 -> 文件")
        print("=" * 60)

        if ENABLE_TUNNEL_PROXY:
            if not (TUNNEL_PROXY_HOST and TUNNEL_PROXY_PORT):
                print("[WARN] ENABLE_TUNNEL_PROXY=True 但未提供 TUNNEL_PROXY_HOST / TUNNEL_PROXY_PORT")
            else:
                print(f"[代理] 隧道代理已启用: {TUNNEL_PROXY_HOST}:{TUNNEL_PROXY_PORT}")

        self.init_browser(start_index=0)
        ensure_dir(DOWNLOAD_ROOT)
        batch_success_count = 0
        stop_all = False

        try:
            for t_idx, cfg in enumerate(TARGETS, 1):
                try:
                    target_name, target_url, start_idx, end_idx = cfg
                except Exception:
                    print(f"[{t_idx}] 目标配置格式错误，跳过: {cfg}")
                    continue

                print("\n" + "-" * 60)
                print(f"\n[{t_idx}/{len(TARGETS)}] [TARGET] 目标: {target_name}")
                print(f"[URL] 列表页: {target_url}")
                print("-" * 60)

                section_checkpoint = get_section_checkpoint(self.checkpoint, target_name)
                resume_page = section_checkpoint.get("next_page", 1)
                resume_row = section_checkpoint.get("next_row", 0)
                if section_checkpoint.get("done", False):
                    print(f"[SKIP] 目标 {target_name} 已完成，跳过")
                    continue

                self.browser.goto(target_url)
                self.browser.wait_for_load_state("domcontentloaded", timeout=60000)
                time.sleep(3.0)
                print(f"[DEBUG] 页面加载完成，标题: {self.browser.title()}，浏览器: {self.browser_name}")

                if not self.wait_products_ready():
                    print(f"⚠️ 列表加载失败，准备自动切换浏览器（当前: {self.browser_name}）")
                    if not self.switch_to_next_browser_and_reload(target_url):
                        print("⚠️ 所有候选浏览器均无法加载列表, 判定为风控")
                        # 退出 main.py (exit=10), run_batch.ps1 收到后会自己注册 6h 后重启
                        self.close()
                        emergency_exit("forbidden: 所有浏览器均无法加载产品列表, run_batch 将调度 6h 后重试", EXIT_CODE_BLOCKED)
                        continue  # 不会执行, 上面 sys.exit 了

                # ============ 分工爬取: 计算本任务负责的页区间 ============
                total_pages = self.get_product_list_total_pages()
                share_start, share_end = self.compute_page_range(total_pages)
                print(f"\n📊 [PAGE_RANGE] 产品列表总页数: {total_pages}, 本任务负责: 第 {share_start} ~ {share_end} 页")
                if total_pages > 0 and share_start > 1:
                    # 跳到分工起点
                    if not self.goto_product_page(share_start):
                        print(f"⚠️ 跳到分工起点第 {share_start} 页失败, 回退到第 1 页")
                        share_start = 1
                        share_end = self.compute_page_range(0)[1] if False else total_pages  # 重新计算
                        share_start, share_end = self.compute_page_range(total_pages)
                # 如果 checkpoint 的 resume_page > share_end, 说明上一次已经爬完了
                if resume_page > share_end:
                    print(f"⏭️ [SKIP] 目标 {target_name} 分工区间[{share_start},{share_end}] 之前已爬完 (resume={resume_page}), 跳过")
                    continue

                page_num = max(share_start, int(resume_page))
                global_idx = 0
                current_row_idx = int(resume_row)
                last_success_page = page_num
                last_success_row = current_row_idx
                empty_products_retry = 0
                processed_since_recycle = 0
                consecutive_product_fails = 0
                consecutive_download_fails = 0  # 连续无下载成功的产品数 (用于检测被封)
                abort_target = False

                while True:
                    if page_num > 1 and not self.goto_product_page(page_num):
                        print(f"第{page_num}页加载失败，结束目标")
                        break

                    products = self.parse_products_on_page(page_num)
                    if not products:
                        empty_products_retry += 1
                        print(f"第{page_num}页无产品（第{empty_products_retry}/{MAX_EMPTY_PRODUCT_PAGE_RETRIES}次）")
                        if empty_products_retry >= MAX_EMPTY_PRODUCT_PAGE_RETRIES:
                            print("⚠️ 连续无内容返回，按失败中断；checkpoint 保持最后成功产品")
                            update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)
                            abort_target = True
                            break
                        ok = self.recycle_browser_session(
                            target_url=target_url,
                            page_num=page_num,
                            reason=f"empty_products_retry={empty_products_retry}",
                        )
                        if not ok:
                            print("⚠️ 空内容恢复失败，按失败中断；checkpoint 保持最后成功产品")
                            update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)
                            abort_target = True
                            break
                        continue
                    else:
                        empty_products_retry = 0

                    for p in products:
                        global_idx += 1
                        
                        if global_idx < max(1, int(start_idx)):
                            continue
                        if end_idx not in (None, 0) and global_idx > int(end_idx):
                            update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)
                            break
                        
                        if page_num == resume_page and global_idx <= current_row_idx:
                            continue

                        if SESSION_RECYCLE_EVERY_PRODUCTS > 0 and processed_since_recycle >= SESSION_RECYCLE_EVERY_PRODUCTS:
                            ok = self.recycle_browser_session(
                                target_url=target_url,
                                page_num=page_num,
                                reason=f"processed={processed_since_recycle}",
                            )
                            if not ok:
                                print("⚠️ 会话轮换失败，结束当前目标")
                                update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)
                                abort_target = True
                                break
                            processed_since_recycle = 0

                        print(f"\n[{global_idx}] [PRODUCT] 产品: {p.name} ({p.code})")
                        
                        # 日期检查
                        notice_date = ""  # 光大银行的日期需要从公告列表获取
                        if notice_date:
                            _in_range, _too_old = is_in_date_range(notice_date)
                            if not _in_range:
                                tag = "过早(早停)" if _too_old else "过晚"
                                print(f"   ⏭️ [日期跳过] 披露日期 {notice_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]")
                                continue

                        tab, detail_tab_id = self.open_product_detail_tab(p)
                        if (not tab) and RETRY_PRODUCT_AFTER_RECYCLE:
                            print("  [RETRY] 详情页首次失败，尝试会话重建后重试当前产品")
                            ok = self.recycle_browser_session(
                                target_url=target_url,
                                page_num=page_num,
                                reason=f"detail_retry_{p.code}",
                            )
                            if ok:
                                tab, detail_tab_id = self.open_product_detail_tab(p)

                        if tab and not self.is_detail_tab_ready(tab):
                            print("  [FAIL] 详情页疑似风控空页（未加载出公告入口）")
                            try:
                                tab.close()
                            except Exception:
                                pass
                            tab = None
                            detail_tab_id = ""
                            if RETRY_PRODUCT_AFTER_RECYCLE:
                                print("  [RETRY] 详情页疑似风控，尝试会话重建后重试当前产品")
                                ok = self.recycle_browser_session(
                                    target_url=target_url,
                                    page_num=page_num,
                                    reason=f"detail_blocked_{p.code}",
                                )
                                if ok:
                                    tab, detail_tab_id = self.open_product_detail_tab(p)
                                    if tab and not self.is_detail_tab_ready(tab):
                                        try:
                                            tab.close()
                                        except Exception:
                                            pass
                                        tab = None
                                        detail_tab_id = ""

                        if not tab:
                            consecutive_product_fails += 1
                            print("  [FAIL] 详情页打开失败")
                            write_failed_row(
                                "详情页打开失败",
                                {
                                    "section": target_name,
                                    "title": p.name,
                                    "product_code": p.code,
                                    "source_link": target_url,
                                    "expected_path": "",
                                    "disclose_date": "",
                                },
                            )
                            update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)

                            if consecutive_product_fails >= MAX_CONSECUTIVE_PRODUCT_FAILS:
                                ok = self.recycle_browser_session(
                                    target_url=target_url,
                                    page_num=page_num,
                                    reason=f"consecutive_fail={consecutive_product_fails}",
                                )
                                if not ok:
                                    print("⚠️ 连续失败且会话轮换失败，结束当前目标")
                                    update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)
                                    abort_target = True
                                    break
                                consecutive_product_fails = 0
                            continue

                        print(f"  [DETAIL] 详情页: {tab.title()}")

                        notice_tab, notice_tab_id = self.open_notice_tab(tab)
                        if (not notice_tab) and RETRY_PRODUCT_AFTER_RECYCLE:
                            print("  [RETRY] 公告页首次失败，尝试会话重建后重试当前产品")
                            ok = self.recycle_browser_session(
                                target_url=target_url,
                                page_num=page_num,
                                reason=f"notice_retry_{p.code}",
                            )
                            if ok:
                                tab, detail_tab_id = self.open_product_detail_tab(p)
                                if tab:
                                    notice_tab, notice_tab_id = self.open_notice_tab(tab)

                        if not notice_tab:
                            consecutive_product_fails += 1
                            print("  [FAIL] 产品公告页打开失败")
                            write_failed_row(
                                "产品公告页打开失败",
                                {
                                    "section": target_name,
                                    "title": p.name,
                                    "product_code": p.code,
                                    "source_link": target_url,
                                    "expected_path": "",
                                    "disclose_date": "",
                                },
                            )
                            if detail_tab_id and tab:
                                try:
                                    tab.close()
                                except Exception:
                                    pass
                            update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)

                            if consecutive_product_fails >= MAX_CONSECUTIVE_PRODUCT_FAILS:
                                ok = self.recycle_browser_session(
                                    target_url=target_url,
                                    page_num=page_num,
                                    reason=f"consecutive_fail={consecutive_product_fails}",
                                )
                                if not ok:
                                    print("⚠️ 连续失败且会话轮换失败，结束当前目标")
                                    update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)
                                    abort_target = True
                                    break
                                consecutive_product_fails = 0
                            continue

                        # 公告列表分页处理
                        notice_page_num = 1
                        notice_total_pages = self.get_notice_total_pages(notice_tab)
                        print(f"  [NOTICE] 公告分页: 共 {notice_total_pages} 页")
                        product_download_success = False  # 本产品是否成功下载了至少一个 PDF
                        
                        while notice_page_num <= notice_total_pages:
                            notice_items = self.get_notice_items(notice_tab)
                            if not notice_items:
                                print(f"  [EMPTY] 公告第 {notice_page_num} 页为空，尝试刷新后重试")
                                try:
                                    notice_tab.reload(wait_until="networkidle", timeout=60000)
                                    time.sleep(1.5)
                                except Exception as e:
                                    print(f"  [WARN] 公告页刷新失败: {e}")
                                notice_items = self.get_notice_items(notice_tab)
                                if not notice_items:
                                    print(f"  [EMPTY] 刷新后公告第 {notice_page_num} 页仍为空")
                                    break

                            print(f"  [NOTICE] 第 {notice_page_num}/{notice_total_pages} 页，公告条数: {len(notice_items)}")
                            
                            for notice_idx, notice_item in enumerate(notice_items, 1):
                                try:
                                    notice_title = re.sub(r"^[•\s]+", "", (notice_item.inner_text() or "").strip())
                                except Exception:
                                    notice_title = "未命名公告"

                                notice_date = ""
                                try:
                                    notice_date = (notice_item.evaluate(
                                        "el => { const p = el.parentElement; const s = p ? p.querySelector('span.sp2') : null; return s ? (s.innerText || '').trim() : ''; }"
                                    ) or "").strip()
                                except Exception:
                                    notice_date = ""

                                print(f"  [{notice_page_num}-{notice_idx}] [ITEM] 公告: {notice_title} {notice_date}")
                                
                                # 日期过滤
                                normalized_date = normalize_date(notice_date)
                                _in_range, _too_old = is_in_date_range(normalized_date)
                                if not _in_range:
                                    if _too_old:
                                        print(f"    ⏭️ [日期跳过] {notice_title} 披露日期 {normalized_date} < {START_DATE}")
                                        if EARLY_STOP:
                                            print(f"    ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                                            break
                                    else:
                                        print(f"    ⏭️ [日期跳过] {notice_title} 披露日期 {normalized_date} > {END_DATE or '今天'}")
                                    continue

                                detail_notice_tab, detail_notice_tab_id = self.open_notice_detail_tab(notice_tab, notice_item)
                                if not detail_notice_tab:
                                    print("    [FAIL] 公告详情打开失败")
                                    write_failed_row(
                                        "公告详情打开失败",
                                        {
                                            "section": target_name,
                                            "title": notice_title,
                                            "product_code": p.code,
                                            "source_link": "",
                                            "expected_path": "",
                                            "disclose_date": normalized_date,
                                        },
                                    )
                                    continue

                                print(f"    [DETAIL] 公告详情: {detail_notice_tab.title()}")

                                # 获取公告详情页中的所有文件链接
                                file_links = self.get_notice_file_links(detail_notice_tab)
                                if not file_links:
                                    print("    [EMPTY] 未找到文件链接")
                                    write_failed_row(
                                        "未找到文件链接",
                                        {
                                            "section": target_name,
                                            "title": notice_title,
                                            "product_code": p.code,
                                            "source_link": detail_notice_tab.url,
                                            "expected_path": "",
                                            "disclose_date": normalized_date,
                                        },
                                    )
                                    if detail_notice_tab_id:
                                        try:
                                            detail_notice_tab.close()
                                        except Exception:
                                            pass
                                    continue

                                print(f"    [FILES] 找到 {len(file_links)} 个文件")
                                total_files = len(file_links)
                                file_idx = 0
                                while file_idx < total_files:
                                    file_idx += 1
                                    # 重新获取文件链接，避免元素失效
                                    file_links = self.get_notice_file_links(detail_notice_tab)
                                    if not file_links or file_idx > len(file_links):
                                        print(f"    [WARN] 文件链接已失效，跳过第 {file_idx} 个文件")
                                        break
                                    
                                    file_link = file_links[file_idx - 1]
                                    try:
                                        file_title = re.sub(r"\s+", " ", (file_link.inner_text() or notice_title or f"公告文件{file_idx}")).strip()
                                    except Exception:
                                        file_title = notice_title or f"公告文件{file_idx}"
                                    print(f"    [{file_idx}] 文件: {file_title}")

                                    if not should_download_notice_file(file_title, NOTICE_FILE_KEYWORDS):
                                        print(f"    ⏭️ [关键词跳过] 文件名未命中关键词{NOTICE_FILE_KEYWORDS}: {file_title}")
                                        continue
                                    
                                    # 检查公告详情页是否仍然有效
                                    try:
                                        detail_notice_tab.title()  # 尝试获取标题验证页面是否存活
                                    except Exception:
                                        print(f"    [FAIL] 公告详情页已关闭")
                                        write_failed_row(
                                            "公告详情页已关闭",
                                            {
                                                "section": target_name,
                                                "title": file_title,
                                                "product_code": p.code,
                                                "source_link": "",
                                                "expected_path": "",
                                                "disclose_date": normalized_date,
                                            },
                                        )
                                        break
                                    
                                    pdf_tab, pdf_tab_id = self.click_notice_file(detail_notice_tab, file_link)
                                    if not pdf_tab:
                                        print("    [FAIL] 文件打开失败")
                                        write_failed_row(
                                            "文件打开失败",
                                            {
                                                "section": target_name,
                                                "title": file_title,
                                                "product_code": p.code,
                                                "source_link": detail_notice_tab.url,
                                                "expected_path": "",
                                                "disclose_date": normalized_date,
                                            },
                                        )
                                        continue

                                    pdf_url = (getattr(pdf_tab, "url", "") or "").strip()
                                    if not pdf_url:
                                        print("    [EMPTY] PDF 地址为空")
                                        write_failed_row(
                                            "PDF地址为空",
                                            {
                                                "section": target_name,
                                                "title": file_title,
                                                "product_code": p.code,
                                                "source_link": detail_notice_tab.url,
                                                "expected_path": "",
                                                "disclose_date": normalized_date,
                                            },
                                        )
                                        # 如果是新打开的页面则关闭，否则保留公告详情页
                                        if pdf_tab_id and pdf_tab != detail_notice_tab:
                                            try:
                                                pdf_tab.close()
                                            except Exception:
                                                pass
                                        continue

                                    ndate = normalized_date
                                    notice_type = "产品公告"  # 光大银行主要是产品公告
                                    
                                    # 构建唯一键
                                    unique_key = build_unique_key(INSTITUTE_NAME, notice_type, file_title, ndate, p.code)
                                    
                                    # 去重检查
                                    if SKIP_DOWNLOADED and pdf_url in self.downloaded_links:
                                        print(f"    ⏭️ [去重跳过] 已下载: {file_title}")
                                        # 如果是新打开的页面则关闭，否则保留公告详情页
                                        if pdf_tab_id and pdf_tab != detail_notice_tab:
                                            try:
                                                pdf_tab.close()
                                            except Exception:
                                                pass
                                        continue

                                    # 构建保存路径
                                    base_filename = build_base_filename(INSTITUTE_NAME, p.name, notice_type, p.code, ndate)
                                    if total_files > 1:
                                        base_filename = f"{base_filename}_{file_idx}"
                                    folder = os.path.join(DOWNLOAD_ROOT, sanitize_filename(notice_type, 80))
                                    ensure_dir(folder)
                                    save_path = build_unique_save_path(folder, base_filename, ".pdf")

                                    print(f"    [DOWNLOAD] 下载PDF: {pdf_url}")
                                    success, result, file_size = self.download_one(pdf_url, save_path)
                                    
                                    if success:
                                        status = "SUCCEED"
                                        save_downloaded_link(pdf_url)
                                        self.downloaded_links.add(pdf_url)
                                        product_download_success = True
                                        print(f"    [RESULT] {status}: {os.path.abspath(save_path)} ({file_size}字节)")
                                    else:
                                        status = result
                                        print(f"    [RESULT] {status}: {os.path.abspath(save_path)}")
                                        write_failed_row(
                                            status,
                                            {
                                                "section": target_name,
                                                "title": file_title,
                                                "product_code": p.code,
                                                "source_link": pdf_url,
                                                "expected_path": save_path,
                                                "disclose_date": ndate,
                                            },
                                        )

                                    write_log_row(
                                        INSTITUTE_NAME,
                                        file_title,
                                        notice_type,
                                        ndate,
                                        status,
                                        pdf_url,
                                        os.path.abspath(save_path),
                                        unique_key,
                                        p.code,
                                        file_size,
                                    )

                                    # 如果是新打开的PDF页面则关闭，否则返回上一页（公告详情页）
                                    if pdf_tab_id:
                                        try:
                                            if pdf_tab != detail_notice_tab:
                                                pdf_tab.close()
                                            else:
                                                # 在当前页面打开的PDF，需要返回上一页
                                                pdf_tab.go_back()
                                                time.sleep(1.0)
                                        except Exception as e:
                                            print(f"    [DEBUG] 关闭/返回页面异常: {str(e)}")
                                    sleep_with_jitter(DOWNLOAD_DELAY, DOWNLOAD_JITTER)

                                # 所有文件处理完成后再关闭公告详情页
                                if detail_notice_tab_id:
                                    try:
                                        detail_notice_tab.close()
                                    except Exception:
                                        pass

                            # 翻到下一页
                            if notice_page_num < notice_total_pages:
                                print(f"  [PAGE] 翻到公告第 {notice_page_num + 1} 页")
                                if not self.click_notice_next_page(notice_tab):
                                    print(f"  [WARN] 无法翻到下一页，结束公告分页")
                                    break
                            
                            notice_page_num += 1
                            sleep_with_jitter(REQUEST_DELAY, REQUEST_JITTER)

                        if notice_tab_id:
                            try:
                                notice_tab.close()
                            except Exception:
                                pass

                        if detail_tab_id and tab:
                            try:
                                tab.close()
                            except Exception:
                                pass

                        # 确保切换回产品列表页面
                        try:
                            pages = self.context.pages
                            for page in pages:
                                if "财富" in page.title() or "/wealth/" in page.url:
                                    self.browser = page
                                    break
                            print(f"[DEBUG] 切换回产品列表页面: {self.browser.title()}")
                        except Exception as e:
                            print(f"[DEBUG] 切换页面失败: {str(e)}")

                        last_success_page = page_num
                        last_success_row = p.row_index
                        update_section_checkpoint(self.checkpoint, target_name, last_success_page, last_success_row, False)
                        consecutive_product_fails = 0

                        # 紧急刹车 1: health_check 判定网站被风控 (有明确的 exit_reason 文件)
                        # 注意: 仅当 emergency_exit.json 存在, reason 是"被封", 且是 10 分钟内的才停止
                        if os.path.exists(EMERGENCY_STOP_FILE):
                            try:
                                with open(EMERGENCY_EXIT_FILE, 'r', encoding='utf-8') as _f:
                                    _e = json.load(_f)
                                _reason = str(_e.get('reason', '')).lower()
                                # 时间判断: 只相信最近 10 分钟内的 reason, 旧的忽略
                                _ts = str(_e.get('timestamp', ''))
                                _is_recent = False
                                try:
                                    from datetime import datetime as _dt
                                    _exit_time = _dt.strptime(_ts, '%Y-%m-%d %H:%M:%S')
                                    _delta_min = (_dt.now() - _exit_time).total_seconds() / 60
                                    _is_recent = _delta_min < 10
                                except Exception:
                                    _is_recent = True  # 时间解析失败, 默认信任

                                _blocked_keywords = ['forbidden', 'access denied', 'too many', 'rate limit', 'captcha', '访问过于频繁']
                                _is_blocked = any(kw in _reason for kw in _blocked_keywords)
                                if _is_blocked and _is_recent:
                                    print(f"\n🚨 [EMERGENCY] health_check 判定网站被封 (reason: {_e.get('reason')}, {int(_delta_min)}分钟前), 中止爬虫")
                                    self.close()
                                    emergency_exit(f"health_check blocked: {_e.get('reason')}", EXIT_CODE_BLOCKED)
                                else:
                                    # 旧 reason (超过 10 分钟) 或 reason 不是被封, 忽略并清理标记
                                    if not _is_recent:
                                        print(f"   ⚠️ [INFO] emergency_exit.json 已过期 ({int(_delta_min)}分钟前), 忽略并清理")
                                    else:
                                        print(f"   ⚠️ [INFO] emergency_stop.flag 残留但 reason='{_e.get('reason')}' 不是被封, 忽略")
                                    try:
                                        os.remove(EMERGENCY_STOP_FILE)
                                    except Exception:
                                        pass
                            except Exception:
                                # 解析失败, 忽略并清理
                                try:
                                    os.remove(EMERGENCY_STOP_FILE)
                                except Exception:
                                    pass

                        # 紧急刹车 2: 真正被风控的表现 = 详情页加载失败, 而不是"没有文件"
                        # product_download_success=False 只代表"日期/关键词未命中", 不算风控
                        if consecutive_product_fails >= MAX_CONSECUTIVE_PRODUCT_FAILS * 3:
                            # 连续很多个产品详情页都打不开 → 真的被封了
                            print(f"\n🚨 [EMERGENCY] 连续 {consecutive_product_fails} 个产品详情页打不开, 判定被风控")
                            self.close()
                            emergency_exit(
                                f"forbidden: consecutive_product_fails={consecutive_product_fails}",
                                EXIT_CODE_BLOCKED,
                            )

                        processed_since_recycle += 1
                        batch_success_count += 1

                        if BATCH_MAX_SUCCESS_PRODUCTS > 0 and batch_success_count >= BATCH_MAX_SUCCESS_PRODUCTS:
                            print(
                                f"[BATCH] 已达到单次运行上限 {batch_success_count}/{BATCH_MAX_SUCCESS_PRODUCTS}，"
                                "保存最后成功断点后主动退出，下一批请稍后再跑"
                            )
                            stop_all = True
                            abort_target = True
                            break

                        sleep_with_jitter(REQUEST_DELAY, REQUEST_JITTER)

                    if abort_target:
                        break

                    if end_idx not in (None, 0) and global_idx >= int(end_idx):
                        break

                    # ============ 分工爬取: 超出本任务负责页区间时停止 ============
                    if page_num >= share_end:
                        print(f"\n✅ [DONE] 已爬完分工区间第 {share_start}~{share_end} 页 (总 {total_pages} 页), 本任务结束")
                        update_section_checkpoint(self.checkpoint, target_name, share_end, 0, done=True)
                        abort_target = True
                        break

                    page_num += 1
                    sleep_with_jitter(REQUEST_DELAY, REQUEST_JITTER)

                if stop_all:
                    break

        finally:
            self.close()


if __name__ == "__main__":
    CebwmCrawler().run()
