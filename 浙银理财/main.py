#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
浙银理财公告/产品说明书爬虫
机构：浙银理财有限责任公司
说明：已合并补丁脚本 download_prod_manual.py —— 公告披露（MENU_MAP 各栏目）
      与"产品说明书"（产品管理系统 API，独立数据流，mode="manual"）统一在本脚本运行。
================================================================================
"""


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

import csv
import importlib
import json
import os
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_PDF_READER_CLASS = None
_PDF_READER_CHECKED = False


def get_pdf_reader_class():
    """运行时动态获取 pypdf.PdfReader，避免环境缺包时硬失败。"""
    global _PDF_READER_CLASS, _PDF_READER_CHECKED
    if _PDF_READER_CHECKED:
        return _PDF_READER_CLASS

    _PDF_READER_CHECKED = True
    try:
        module = importlib.import_module("pypdf")
        _PDF_READER_CLASS = getattr(module, "PdfReader", None)
    except Exception:
        _PDF_READER_CLASS = None
    return _PDF_READER_CLASS


# =====================================
# 用户配置区
# =====================================

INSTITUTE_NAME = "浙银理财"

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("浙银理财", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("浙银理财", True)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = True
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-10"
# END_DATE   = "2026-06-30"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_links.txt")
FINGERPRINT_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_fingerprints.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
FAILED_FILE = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")

BASE_URL = "https://www.czbank-wm.com"
HOME_URL = f"{BASE_URL}/wm/"
ZYLCCZZ_HOME = f"{BASE_URL}/zylczz/"
API_BASE_URL = f"{BASE_URL}/zylczzapi"

# ============ 产品说明书模块（独立 API，来自补丁脚本 download_prod_manual.py）============
MANUAL_MENU_NAME = "产品说明书"

# API 基地址（产品管理系统，与公告披露 API 不同域）
MANUAL_API_BASE = "https://cmp.czbank-wm.com/apicms/web"
MANUAL_PRODUCT_LIST_URL = f"{MANUAL_API_BASE}/product/v1/product/list"
MANUAL_PRODUCT_RECORD_URL = f"{MANUAL_API_BASE}/product/v1/product/record"
MANUAL_DOCUMENT_DOWNLOAD_URL = f"{MANUAL_API_BASE}/document/v1/document/download"

# 产品列表分页大小
MANUAL_PAGE_SIZE = 50

# 网络节奏（保留补丁脚本原配置）
MANUAL_REQUEST_INTERVAL = (0.8, 1.5)
MANUAL_RECORD_INTERVAL = (0.5, 1.0)
MANUAL_DOWNLOAD_INTERVAL = (0.3, 0.8)
MANUAL_DOWNLOAD_TIMEOUT = 60

# 独立状态文件与日志（与原补丁脚本路径一致，按 record_id 去重）
MANUAL_PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, MANUAL_MENU_NAME, "downloaded.txt")
MANUAL_LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_产品说明书下载日志.csv")

MENU_MAP = {
    # 已按需求关闭，如需恢复把 "enabled": False 改为 True 或删除该行
    "发行公告": {
        "enabled": False,
        "mode": "api",
        "list_url": f"{BASE_URL}/wm/disclosure/publicplacement/public1/",
        "route": "est",
        "page_url": f"{ZYLCCZZ_HOME}#/iframe/est",
        "notice_types": ["00", "01"],
    },
    "定期公告": {
        "mode": "api",
        "list_url": f"{BASE_URL}/wm/disclosure/publicplacement/public2/",
        "route": "reg",
        "page_url": f"{ZYLCCZZ_HOME}#/iframe/reg",
        "notice_types": ["02", "10"],
    },
    "到期公告": {
        "enabled": False,  # 已按需求关闭，如需恢复改为 True
        "mode": "api",
        "list_url": f"{BASE_URL}/wm/disclosure/publicplacement/public3/",
        "route": "exp",
        "page_url": f"{ZYLCCZZ_HOME}#/iframe/exp",
        "notice_types": ["05"],
    },
    "临时公告": {
        "mode": "html",
        "list_url": f"{BASE_URL}/wm/disclosure/publicplacement/public4/",
    },
    "其他公告": {
        "enabled": False,  # 已按需求关闭，如需恢复改为 True
        "mode": "html",
        "list_url": f"{BASE_URL}/wm/disclosure/publicplacement/public5/",
    },
    # 产品说明书：数据流与公告完全不同（产品列表→销售文件记录→PDF），独立分支处理
    MANUAL_MENU_NAME: {
        "mode": "manual",
        "enabled": True,
        "list_api": MANUAL_PRODUCT_LIST_URL,
        "record_api": MANUAL_PRODUCT_RECORD_URL,
        "download_api": MANUAL_DOCUMENT_DOWNLOAD_URL,
    },
}

# TODO[手动修改]: 空字符串表示全量抓取全部启用栏目；填写逗号分隔列表则只跑列出的栏目
# 合并补丁脚本后已加入"产品说明书"（如需临时只跑部分栏目，填逗号分隔列表即可）
RUN_ONLY_MENU_NAME = ""

# TODO[手动修改]: 单页联调（仅抓某栏目某页）
TEST_MODE = False
TEST_MENU_NAME = "发行公告"
TEST_PAGE_NO = 1

# TODO[手动修改]: 网络与重试
REQUEST_TIMEOUT = 45
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
PAGE_NO_FILE_WAIT_SECONDS = 10

# TODO[手动修改]: 稳中求稳抓取节奏
REQUEST_INTERVAL_SECONDS = (1.0, 2.0)
DETAIL_INTERVAL_SECONDS = (0.8, 1.6)
PAGE_INTERVAL_SECONDS = (1.6, 3.0)

# TODO[手动修改]: 去重和断点续跑
SKIP_DOWNLOADED = True
ENABLE_CHECKPOINT_RESUME = True

# TODO[手动修改]: 最大爬取页数（0表示不限制）
MAX_PAGES = 0

# TODO[手动修改]: 控制台输出 emoji 开关
ENABLE_EMOJI_LOG = True

# TODO[手动修改]: 最早披露日期（读取集中配置的 START_DATE，空字符串表示不过滤）
MIN_DISCLOSE_DATE = START_DATE

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
]

DATE_RE = re.compile(r"(\d{4})[-/.年]\s*(\d{1,2})[-/.月]\s*(\d{1,2})")
DATE_CN_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
PRODUCT_CODE_RE = re.compile(r"([A-Z]\d{10,})")
SALES_CODE_RE = re.compile(r"销售代码\s*[:：]\s*([A-Za-z0-9_\-]+)")
DOWNLOAD_EXTENSIONS = {".pdf", ".zip", ".xls", ".xlsx", ".doc", ".docx", ".rar", ".7z"}


# =====================================
# 工具函数
# =====================================


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def random_sleep(sec_range: Tuple[float, float]):
    time.sleep(random.uniform(sec_range[0], sec_range[1]))


def now_time_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def normalize_date(text: str) -> str:
    src = str(text or "")
    m = DATE_RE.search(src)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mm:02d}-{dd:02d}"

    m = DATE_CN_RE.search(src)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mm:02d}-{dd:02d}"

    return ""


def sanitize_text(text: str, max_len: int = 300) -> str:
    val = re.sub(r"[\\/*?:\"<>|]", "_", str(text or ""))
    val = re.sub(r"\s+", " ", val).strip()
    if max_len > 0:
        val = val[:max_len]
    return val


def normalize_key(text: str) -> str:
    return str(text or "").strip().lower()


def normalize_notice_title(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_in_date_range(disclose_date: str):
    """返回 (是否在区间内, 是否过早可早停)"""
    d = normalize_date(disclose_date)
    if not d:
        return True, False  # 无日期不过滤
    upper = (END_DATE.strip() if END_DATE and END_DATE.strip()
             else datetime.now().strftime("%Y-%m-%d"))
    if START_DATE and d < START_DATE:
        return False, True
    if d > upper:
        return False, False
    return True, False


def extract_codes_from_text(text: str) -> Dict[str, str]:
    src = str(text or "")
    product_code = ""
    m = PRODUCT_CODE_RE.search(src)
    if m:
        product_code = m.group(1)

    sales_code = ""
    m = SALES_CODE_RE.search(src)
    if m:
        sales_code = m.group(1)

    return {
        "product_code": product_code,
        "sales_code": sales_code,
    }


def build_unique_key(notice_type: str, title: str, disclose_date: str) -> str:
    parts = [INSTITUTE_NAME, notice_type, normalize_notice_title(title)]
    date_val = normalize_date(disclose_date)
    if date_val:
        parts.append(date_val)
    return "+".join(parts)


def build_fingerprint_key(unique_key: str, source: str) -> str:
    return f"{unique_key}+{sanitize_text(source, 300)}"


def build_base_filename(notice_type: str, title: str, disclose_date: str, codes: Dict[str, str]) -> str:
    product_code = sanitize_text(codes.get("product_code", ""), 80)
    sales_code = sanitize_text(codes.get("sales_code", ""), 80)

    parts = [
        sanitize_text(INSTITUTE_NAME, 40),
        sanitize_text(title, 180),
        sanitize_text(notice_type, 40),
    ]

    if product_code:
        parts.append(f"产品代码：{product_code}")
    if sales_code:
        parts.append(f"销售代码：{sales_code}")

    date_val = normalize_date(disclose_date)
    if date_val:
        parts.append(f"披露日期：{date_val}")

    return sanitize_text("_".join(parts), 250)


def build_unique_save_path(folder: str, base_name: str, extension: str) -> Tuple[str, str]:
    ext = extension if extension.startswith(".") else f".{extension}"
    ext = ext.lower()
    idx = 0
    while True:
        file_name = f"{base_name}{ext}" if idx == 0 else f"{base_name}_{idx}{ext}"
        save_path = os.path.join(folder, file_name)
        if not os.path.exists(save_path):
            return save_path, file_name
        idx += 1


def print_section(title: str):
    prefix = "🚀 " if ENABLE_EMOJI_LOG else ""
    print(f"\n{'=' * 60}")
    print(f"{prefix}{title}")
    print(f"{'=' * 60}")


def print_info(msg: str):
    if ENABLE_EMOJI_LOG:
        print(f"ℹ️ [信息] {msg}")
    else:
        print(f"[信息] {msg}")


def print_warn(msg: str):
    if ENABLE_EMOJI_LOG:
        print(f"⚠️ [警告] {msg}")
    else:
        print(f"[警告] {msg}")


def print_error(msg: str):
    if ENABLE_EMOJI_LOG:
        print(f"❌ [错误] {msg}")
    else:
        print(f"[错误] {msg}")


def print_ok(msg: str):
    if ENABLE_EMOJI_LOG:
        print(f"✅ [成功] {msg}")
    else:
        print(f"[成功] {msg}")


def is_download_link(url: str) -> bool:
    low = str(url or "").lower()
    return any(low.endswith(ext) for ext in DOWNLOAD_EXTENSIONS)


# =====================================
# 状态文件
# =====================================


def read_line_set(file_path: str) -> Set[str]:
    if not os.path.exists(file_path):
        return set()
    with open(file_path, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def append_line(file_path: str, val: str):
    ensure_dir(os.path.dirname(file_path))
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(val.strip() + "\n")


def load_downloaded_links() -> Set[str]:
    values: Set[str] = {normalize_key(x) for x in read_line_set(PROGRESS_FILE)}

    if os.path.exists(LOG_CSV_PATH):
        try:
            with open(LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() != "SUCCEED":
                        continue
                    source = str(row.get("来源链接") or "").strip()
                    if source:
                        values.add(normalize_key(source))
        except Exception:
            pass

    return values


def load_downloaded_fingerprints() -> Set[str]:
    values: Set[str] = {normalize_key(x) for x in read_line_set(FINGERPRINT_FILE)}

    if os.path.exists(LOG_CSV_PATH):
        try:
            with open(LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() != "SUCCEED":
                        continue
                    unique_key = str(row.get("unique_key") or "").strip()
                    source = str(row.get("来源链接") or "").strip()
                    if unique_key and source:
                        values.add(normalize_key(build_fingerprint_key(unique_key, source)))
        except Exception:
            pass

    return values


def load_checkpoint() -> Dict:
    if not os.path.exists(CHECKPOINT_FILE):
        return {"menus": {}, "updated_at": now_time_str()}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"menus": {}, "updated_at": now_time_str()}
        data.setdefault("menus", {})
        return data
    except Exception:
        return {"menus": {}, "updated_at": now_time_str()}


def save_checkpoint(data: Dict):
    data["updated_at"] = now_time_str()
    ensure_dir(os.path.dirname(CHECKPOINT_FILE))
    tmp_path = CHECKPOINT_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CHECKPOINT_FILE)


def update_checkpoint_menu(checkpoint: Dict, menu_name: str, last_page: int, done: bool = False):
    checkpoint.setdefault("menus", {})
    checkpoint["menus"][menu_name] = {
        "last_page": int(last_page),
        "done": bool(done),
        "updated_at": now_time_str(),
    }


# =====================================
# 日志
# =====================================


def log_header() -> List[str]:
    return ["机构名称", "公告名称", "公告类型", "披露日期", "下载时间", "状态", "来源链接", "保存路径", "unique_key"]


def init_log_csv():
    if os.path.exists(LOG_CSV_PATH):
        return
    ensure_dir(os.path.dirname(LOG_CSV_PATH))
    with open(LOG_CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(log_header())


def init_failed_csv():
    if os.path.exists(FAILED_FILE):
        return
    ensure_dir(os.path.dirname(FAILED_FILE))
    with open(FAILED_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["下载时间", "机构名称", "公告名称", "公告类型", "披露日期", "状态", "来源链接", "期望路径", "unique_key", "失败原因"])


def write_log_row(notice_title: str, notice_type: str, disclose_date: str, status: str, source_link: str, save_path: str, unique_key: str):
    init_log_csv()
    status = "SUCCEED" if str(status).upper() == "SUCCEED" else "FAILED"
    row = [
        INSTITUTE_NAME,
        normalize_notice_title(notice_title),
        notice_type,
        normalize_date(disclose_date),
        now_time_str(),
        status,
        source_link,
        save_path,
        unique_key,
    ]
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def write_failed_row(notice_type: str, title: str, disclose_date: str, source_link: str, expected_path: str, reason: str):
    init_failed_csv()
    unique_key = build_unique_key(notice_type, title, disclose_date)
    row = [
        now_time_str(),
        INSTITUTE_NAME,
        normalize_notice_title(title),
        notice_type,
        normalize_date(disclose_date),
        "FAILED",
        source_link,
        expected_path,
        unique_key,
        reason,
    ]
    with open(FAILED_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(row)


# =====================================
# 网络请求
# =====================================


def get_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=REQUEST_RETRY,
        read=REQUEST_RETRY,
        connect=REQUEST_RETRY,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Referer": HOME_URL,
        }
    )
    return session


def warmup_session(session: requests.Session):
    try:
        session.get(HOME_URL, timeout=REQUEST_TIMEOUT)
    except Exception:
        pass


def fetch_page(url: str, session: requests.Session, retry: int = REQUEST_RETRY) -> Optional[str]:
    for attempt in range(retry):
        try:
            random_sleep(REQUEST_INTERVAL_SECONDS)
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
            if attempt > 0:
                print_ok(f"请求重试成功: {url} (第 {attempt + 1}/{retry} 次)")
            return resp.text
        except Exception as e:
            print_warn(f"请求失败: {url}, 尝试 {attempt + 1}/{retry}: {e}")
            if attempt < retry - 1:
                time.sleep(RETRY_WAIT_SECONDS)
    return None


def fetch_json(url: str, session: requests.Session, payload: Dict, retry: int = REQUEST_RETRY) -> Optional[Dict]:
    for attempt in range(retry):
        try:
            random_sleep(REQUEST_INTERVAL_SECONDS)
            resp = session.post(
                url,
                data=json.dumps(payload, ensure_ascii=False),
                timeout=REQUEST_TIMEOUT,
                headers={"Content-Type": "application/json", "Referer": ZYLCCZZ_HOME},
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
            data = resp.json()
            if attempt > 0:
                print_ok(f"接口重试成功: {url} (第 {attempt + 1}/{retry} 次)")
            return data
        except Exception as e:
            print_warn(f"接口请求失败: {url}, 尝试 {attempt + 1}/{retry}: {e}")
            if attempt < retry - 1:
                time.sleep(RETRY_WAIT_SECONDS)
    return None


def is_pdf_password_protected(file_path: str) -> bool:
    if not str(file_path).lower().endswith(".pdf"):
        return False

    try:
        pdf_reader = get_pdf_reader_class()
        if pdf_reader is not None:
            reader = pdf_reader(file_path, strict=False)
            if not reader.is_encrypted:
                return False
            decrypt_result = 0
            try:
                decrypt_result = reader.decrypt("")
            except Exception:
                decrypt_result = 0
            return decrypt_result == 0

        with open(file_path, "rb") as f:
            data = f.read()
        return (b"/Encrypt" in data) and (b"/Filter /Standard" in data)
    except Exception:
        return False


def download_file(url: str, save_path: str, session: requests.Session, retry: int = DOWNLOAD_RETRY) -> Tuple[bool, str]:
    for attempt in range(retry):
        try:
            random_sleep(DETAIL_INTERVAL_SECONDS)
            resp = session.get(url, timeout=REQUEST_TIMEOUT, stream=True, headers={"Referer": HOME_URL})
            resp.raise_for_status()
            ensure_dir(os.path.dirname(save_path))
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            if is_pdf_password_protected(save_path):
                try:
                    os.remove(save_path)
                except Exception:
                    pass
                return False, "password_protected"

            if attempt > 0:
                print_ok(f"下载重试成功: {url} (第 {attempt + 1}/{retry} 次)")
            return True, ""
        except Exception as e:
            print_warn(f"下载失败: {url}, 尝试 {attempt + 1}/{retry}: {e}")
            if attempt < retry - 1:
                time.sleep(RETRY_WAIT_SECONDS)
    return False, "network_or_http"


# =====================================
# 页面解析
# =====================================


def parse_api_page(resp_json: Dict) -> List[Dict[str, str]]:
    if not isinstance(resp_json, dict):
        return []
    if str(resp_json.get("errCode") or "0") not in {"0", "200", "0000", "000000"}:
        return []

    items: List[Dict[str, str]] = []
    data = resp_json.get("data") or []
    if not isinstance(data, list):
        return []

    for row in data:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or "").strip()
        title = normalize_notice_title(row.get("title") or "")
        date_text = normalize_date(row.get("date") or "")
        if not item_id or not title:
            continue
        items.append({
            "id": item_id,
            "title": title,
            "date": date_text,
        })
    return items


def parse_static_page_items(list_html: str, base_url: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(list_html, "html.parser")
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    for block in soup.select("div.company-trends__item__content__article"):
        a_tag = block.find("a", href=True)
        if a_tag is None:
            continue

        href = str(a_tag.get("href") or "").strip()
        if not href:
            continue

        full_url = urljoin(base_url, href)
        if normalize_key(full_url) in seen:
            continue
        seen.add(normalize_key(full_url))

        title = normalize_notice_title(a_tag.get("title") or a_tag.get_text(" ", strip=True))
        if not title:
            continue

        date_node = block.select_one("div.company-trends__item__content__article__date")
        date_text = normalize_date(date_node.get_text(" ", strip=True) if date_node else "")
        items.append({
            "title": title,
            "date": date_text,
            "url": full_url,
        })

    return items


def parse_static_total_pages(list_html: str) -> int:
    m = re.search(r"pageCount\s*=\s*(\d+)", list_html)
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            return 1
    return 1


def build_static_page_url(menu_url: str, page_no: int) -> str:
    base = menu_url if menu_url.endswith("/") else menu_url + "/"
    if page_no <= 1:
        return base
    return urljoin(base, f"index_{page_no - 1}.shtml?page={page_no}")


def build_api_payload(page_no: int, notice_types: List[str]) -> Dict:
    return {
        "pageNum": page_no,
        "pageSize": 10,
        "productType": "1",
        "noticeType": notice_types,
    }


# =====================================
# 处理流程
# =====================================


def download_api_item(menu_name: str, item: Dict[str, str], session: requests.Session, downloaded_links: Set[str], downloaded_fingerprints: Set[str]):
    """下载单条 API 公告。返回值：True=成功/已下载跳过, False=失败/无效, "too_old"=过早可早停, "skip"=过晚跳过"""
    item_id = str(item.get("id") or "").strip()
    title = normalize_notice_title(item.get("title") or "")
    disclose_date = normalize_date(item.get("date") or "")
    if not item_id or not title:
        return False

    _in_range, _too_old = is_in_date_range(disclose_date)
    if not _in_range:
        if _too_old:
            print_info(f"跳过早于起始日期的公告(可早停): {title} | {disclose_date} 区间[{START_DATE}~{END_DATE or '今天'}]")
            return "too_old"
        print_info(f"跳过晚于截止日期的公告: {title} | {disclose_date} 区间[{START_DATE}~{END_DATE or '今天'}]")
        return "skip"

    download_url = f"{API_BASE_URL}/inforDisclosure/download?id={item_id}"
    unique_key = build_unique_key(menu_name, title, disclose_date)
    fingerprint = build_fingerprint_key(unique_key, download_url)
    source_key = normalize_key(download_url)

    if SKIP_DOWNLOADED and (source_key in downloaded_links or normalize_key(fingerprint) in downloaded_fingerprints):
        print_info(f"已下载，跳过: {title}")
        return True

    codes = extract_codes_from_text(title)
    base_name = build_base_filename(menu_name, title, disclose_date, codes)
    save_dir = os.path.join(DOWNLOAD_ROOT, menu_name)
    ensure_dir(save_dir)
    save_path, file_name = build_unique_save_path(save_dir, base_name, ".pdf")

    ok, reason = download_file(download_url, save_path, session)
    if ok:
        downloaded_links.add(source_key)
        downloaded_fingerprints.add(normalize_key(fingerprint))
        append_line(PROGRESS_FILE, download_url)
        append_line(FINGERPRINT_FILE, fingerprint)
        write_log_row(title, menu_name, disclose_date, "SUCCEED", download_url, save_path, unique_key)
        print_ok(f"已保存: {file_name}")
        return True

    write_failed_row(menu_name, title, disclose_date, download_url, save_path, reason)
    print_error(f"下载失败: {title} | 原因: {reason}")
    return False


def process_api_menu(menu_name: str, menu_conf: Dict, session: requests.Session, downloaded_links: Set[str], downloaded_fingerprints: Set[str], checkpoint: Dict):
    print_section(f"{menu_name}（接口列表）")
    notice_types = menu_conf.get("notice_types", [])
    page_no = 1
    menu_state = checkpoint.get("menus", {}).get(menu_name, {})
    if ENABLE_CHECKPOINT_RESUME:
        saved_page = int(menu_state.get("last_page") or 0)
        if saved_page > 0:
            page_no = saved_page + 1
            print_info(f"断点续跑: 从第 {page_no} 页开始")

    seen_page_signatures: Set[str] = set()
    total_pages = None
    _early_stop = False

    while True:
        if TEST_MODE and page_no > TEST_PAGE_NO:
            print_info(f"测试模式结束: {menu_name}")
            break

        if MAX_PAGES > 0 and page_no > MAX_PAGES:
            print_info(f"达到最大页数限制: {MAX_PAGES}")
            break

        payload = build_api_payload(page_no, notice_types)
        print_info(f"第 {page_no} 页: {menu_conf.get('page_url', '')}")
        resp_json = fetch_json(f"{API_BASE_URL}/inforDisclosure/queryPageList", session, payload)
        if not resp_json:
            print_warn(f"接口返回为空: {menu_name} 第 {page_no} 页")
            break

        items = parse_api_page(resp_json)
        if total_pages is None:
            try:
                total_pages = int(resp_json.get("totalPage") or 0) or None
            except Exception:
                total_pages = None

        page_sig = "|".join(sorted(normalize_key(f"{x.get('id','')}:{x.get('title','')}" ) for x in items[:20]))
        if page_sig and page_sig in seen_page_signatures:
            print_warn(f"检测到重复页签，提前结束: {menu_name} 第 {page_no} 页")
            break
        if page_sig:
            seen_page_signatures.add(page_sig)

        if not items:
            print_warn(f"第 {page_no} 页无数据，停止")
            break

        for idx, item in enumerate(items, start=1):
            if TEST_MODE and page_no == TEST_PAGE_NO and idx > 1:
                print_info(f"测试模式仅处理第 1 条记录")
                break
            try:
                _result = download_api_item(menu_name, item, session, downloaded_links, downloaded_fingerprints)
                if _result == "too_old" and EARLY_STOP:
                    print_info(f"⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                    _early_stop = True
                    break
            except Exception as e:
                title = item.get("title") or ""
                disclose_date = item.get("date") or ""
                print_error(f"处理公告失败: {title} | {e}")
                write_failed_row(menu_name, title, disclose_date, f"{API_BASE_URL}/inforDisclosure/download?id={item.get('id')}", "", str(e))

        update_checkpoint_menu(checkpoint, menu_name, page_no, done=False)
        save_checkpoint(checkpoint)

        if _early_stop:
            break

        if total_pages is not None and page_no >= total_pages:
            print_info(f"已到最后一页: {page_no}/{total_pages}")
            break

        random_sleep(PAGE_INTERVAL_SECONDS)
        page_no += 1

    update_checkpoint_menu(checkpoint, menu_name, max(page_no - 1, 1), done=True)
    save_checkpoint(checkpoint)
    print_ok(f"完成栏目: {menu_name}")


def process_static_menu(menu_name: str, menu_conf: Dict, session: requests.Session, downloaded_links: Set[str], downloaded_fingerprints: Set[str], checkpoint: Dict):
    print_section(f"{menu_name}（静态列表）")
    list_url = str(menu_conf.get("list_url") or "")
    page_no = 1
    menu_state = checkpoint.get("menus", {}).get(menu_name, {})
    if ENABLE_CHECKPOINT_RESUME:
        saved_page = int(menu_state.get("last_page") or 0)
        if saved_page > 0:
            page_no = saved_page + 1
            print_info(f"断点续跑: 从第 {page_no} 页开始")

    seen_page_signatures: Set[str] = set()
    page_count = None
    _early_stop = False

    while True:
        if TEST_MODE and page_no > TEST_PAGE_NO:
            print_info(f"测试模式结束: {menu_name}")
            break

        if MAX_PAGES > 0 and page_no > MAX_PAGES:
            print_info(f"达到最大页数限制: {MAX_PAGES}")
            break

        page_url = build_static_page_url(list_url, page_no)
        print_info(f"第 {page_no} 页: {page_url}")
        html = fetch_page(page_url, session)
        if not html:
            print_warn(f"获取页面失败: {page_url}")
            break

        if page_count is None:
            page_count = parse_static_total_pages(html)

        items = parse_static_page_items(html, page_url)
        page_sig = "|".join(sorted(normalize_key(x.get("url", "")) for x in items[:20]))
        if page_sig and page_sig in seen_page_signatures:
            print_warn(f"检测到重复页签，提前结束: {menu_name} 第 {page_no} 页")
            break
        if page_sig:
            seen_page_signatures.add(page_sig)

        if not items:
            print_warn(f"第 {page_no} 页无数据，停止")
            break

        for item in items:
            title = normalize_notice_title(item.get("title") or "")
            disclose_date = normalize_date(item.get("date") or "")
            source_url = str(item.get("url") or "").strip()
            if not title or not source_url:
                continue

            _in_range, _too_old = is_in_date_range(disclose_date)
            if not _in_range:
                if _too_old:
                    print_info(f"跳过早于起始日期的公告(可早停): {title} | {disclose_date} 区间[{START_DATE}~{END_DATE or '今天'}]")
                    if EARLY_STOP:
                        print_info(f"⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        _early_stop = True
                        break
                else:
                    print_info(f"跳过晚于截止日期的公告: {title} | {disclose_date} 区间[{START_DATE}~{END_DATE or '今天'}]")
                continue

            unique_key = build_unique_key(menu_name, title, disclose_date)
            fingerprint = build_fingerprint_key(unique_key, source_url)
            source_key = normalize_key(source_url)

            if SKIP_DOWNLOADED and (source_key in downloaded_links or normalize_key(fingerprint) in downloaded_fingerprints):
                print_info(f"已下载，跳过: {title}")
                continue

            codes = extract_codes_from_text(title)
            base_name = build_base_filename(menu_name, title, disclose_date, codes)
            save_dir = os.path.join(DOWNLOAD_ROOT, menu_name)
            ensure_dir(save_dir)
            save_path, file_name = build_unique_save_path(save_dir, base_name, ".pdf")

            ok, reason = download_file(source_url, save_path, session)
            if ok:
                downloaded_links.add(source_key)
                downloaded_fingerprints.add(normalize_key(fingerprint))
                append_line(PROGRESS_FILE, source_url)
                append_line(FINGERPRINT_FILE, fingerprint)
                write_log_row(title, menu_name, disclose_date, "SUCCEED", source_url, save_path, unique_key)
                print_ok(f"已保存: {file_name}")
            else:
                write_failed_row(menu_name, title, disclose_date, source_url, save_path, reason)
                print_error(f"下载失败: {title} | 原因: {reason}")

        update_checkpoint_menu(checkpoint, menu_name, page_no, done=False)
        save_checkpoint(checkpoint)

        if _early_stop:
            break

        if page_count is not None and page_no >= page_count:
            print_info(f"已到最后一页: {page_no}/{page_count}")
            break

        random_sleep(PAGE_INTERVAL_SECONDS)
        page_no += 1

    update_checkpoint_menu(checkpoint, menu_name, max(page_no - 1, 1), done=True)
    save_checkpoint(checkpoint)
    print_ok(f"完成栏目: {menu_name}")


# =====================================
# 产品说明书（独立数据流，合并自补丁脚本 download_prod_manual.py）
# 数据流：POST product/list 产品列表 → POST product/record 销售文件记录
#         （筛选标题含"产品说明书"）→ GET document/download?record_id=X 下载 PDF
# 去重：按 record_id（独立进度文件 + 独立日志 CSV）
# =====================================


def sanitize_filename(name: str, max_len: int = 200) -> str:
    val = re.sub(r'[\\/*?:"<>|]', "_", str(name or ""))
    val = re.sub(r"\s+", " ", val).strip()
    return val[:max_len]


def get_manual_session() -> requests.Session:
    """产品说明书模块专用会话（API 域与请求头均来自补丁脚本，勿与公告会话混用）"""
    sess = requests.Session()
    retry = Retry(
        total=REQUEST_RETRY,
        connect=REQUEST_RETRY,
        read=REQUEST_RETRY,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    sess.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://cmp.czbank-wm.com",
            "Referer": "https://cmp.czbank-wm.com/cmp-web/",
            "Connection": "keep-alive",
        }
    )
    return sess


def load_manual_downloaded_ids() -> Set[str]:
    """加载已下载的 record_id 集合（一个产品可能有多份说明书，对应不同份额）"""
    ids: Set[str] = set()
    if os.path.exists(MANUAL_PROGRESS_FILE):
        with open(MANUAL_PROGRESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                rid = line.strip()
                if rid:
                    ids.add(rid)

    # 也从日志 CSV 读取成功的
    if os.path.exists(MANUAL_LOG_CSV_PATH):
        try:
            with open(MANUAL_LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() == "SUCCEED":
                        rid = str(row.get("record_id") or "").strip()
                        if rid:
                            ids.add(rid)
        except Exception:
            pass

    return ids


def append_manual_progress(record_id: str):
    ensure_dir(os.path.dirname(MANUAL_PROGRESS_FILE))
    with open(MANUAL_PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(record_id.strip() + "\n")


MANUAL_LOG_HEADER = ["产品代码", "产品名称", "文件标题", "下载时间", "状态", "record_id", "保存路径"]


def init_manual_log_csv():
    if os.path.exists(MANUAL_LOG_CSV_PATH):
        return
    ensure_dir(os.path.dirname(MANUAL_LOG_CSV_PATH))
    with open(MANUAL_LOG_CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(MANUAL_LOG_HEADER)


def write_manual_log_row(product_code: str, product_name: str, title: str,
                         status: str, record_id: str, save_path: str):
    init_manual_log_csv()
    with open(MANUAL_LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            product_code, product_name, title,
            now_time_str(),
            status, record_id, save_path,
        ])


def fetch_manual_product_list(sess: requests.Session, page: int) -> Optional[Dict]:
    """获取产品列表（一页）"""
    payload = {"page": page, "size": MANUAL_PAGE_SIZE}
    for attempt in range(REQUEST_RETRY):
        try:
            random_sleep(MANUAL_REQUEST_INTERVAL)
            resp = sess.post(
                MANUAL_PRODUCT_LIST_URL,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", {})
            print_warn(f"产品说明书列表返回非0 code: {data.get('code')} msg={data.get('msg', '')}")
            return None
        except Exception as e:
            print_warn(f"产品说明书列表请求失败 (第{attempt + 1}次): {e}")
            if attempt < REQUEST_RETRY - 1:
                time.sleep(RETRY_WAIT_SECONDS)
    return None


def fetch_manual_product_records(sess: requests.Session, product_code: str,
                                 product_trade_code: str) -> Optional[List[Dict]]:
    """获取产品的销售文件记录"""
    payload = {
        "product_code": product_code,
        "product_trade_code": product_trade_code,
        "record_type": "1",
    }
    for attempt in range(REQUEST_RETRY):
        try:
            random_sleep(MANUAL_RECORD_INTERVAL)
            resp = sess.post(
                MANUAL_PRODUCT_RECORD_URL,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", [])
            return None
        except Exception as e:
            print_warn(f"销售文件记录请求失败 {product_code} (第{attempt + 1}次): {e}")
            if attempt < REQUEST_RETRY - 1:
                time.sleep(RETRY_WAIT_SECONDS)
    return None


def download_manual_pdf(sess: requests.Session, record_id: str, save_path: str) -> Tuple[bool, str]:
    """下载产品说明书 PDF 文件"""
    url = f"{MANUAL_DOCUMENT_DOWNLOAD_URL}?record_id={record_id}"
    for attempt in range(DOWNLOAD_RETRY):
        try:
            random_sleep(MANUAL_DOWNLOAD_INTERVAL)
            resp = sess.get(url, timeout=MANUAL_DOWNLOAD_TIMEOUT)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
                return False, f"unexpected content-type: {content_type}"
            if len(resp.content) < 100:
                return False, f"file too small: {len(resp.content)} bytes"
            ensure_dir(os.path.dirname(save_path))
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True, ""
        except Exception as e:
            print_warn(f"产品说明书下载失败 record_id={record_id} (第{attempt + 1}次): {e}")
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except Exception:
                    pass
            if attempt < DOWNLOAD_RETRY - 1:
                time.sleep(RETRY_WAIT_SECONDS)
    return False, "network_error"


def find_manual_records(records: List[Dict]) -> List[Dict]:
    """从销售文件记录中找到所有产品说明书（一个产品可能有多份，对应不同份额）"""
    result = []
    for rec in records or []:
        title = str(rec.get("title") or "")
        if "产品说明书" in title:
            result.append(rec)
    return result


def extract_manual_record_date(manual: Dict) -> str:
    """尝试从销售文件记录中提取披露/发布日期，归一化为 YYYY-MM-DD。

    补丁脚本原实现未使用任何日期字段过滤；这里对常见候选字段做防御性探测，
    若记录中不存在日期字段则返回空字符串（调用方保留补丁原有行为：不过滤直接下载）。
    """
    for key in ("disclosure_date", "disclose_date", "publish_date", "publish_time",
                "release_date", "release_time", "record_date",
                "create_time", "update_time", "date", "time"):
        raw = manual.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        norm = normalize_date(text)
        if norm:
            return norm
    return ""


def build_manual_save_path(product_code: str, product_name: str) -> str:
    """构建保存路径：产品代码_产品名称_产品说明书.pdf（保留补丁脚本命名规则与冲突避让）"""
    safe_code = sanitize_filename(product_code, 80)
    safe_name = sanitize_filename(product_name, 120)
    # 去掉产品名称中的"浙银理财"前缀避免冗余
    short_name = safe_name
    for prefix_str in ["浙银理财", "浙银"]:
        if short_name.startswith(prefix_str):
            short_name = short_name[len(prefix_str):]
            break
    if short_name:
        filename = f"{safe_code}_{short_name}_产品说明书.pdf"
    else:
        filename = f"{safe_code}_产品说明书.pdf"
    save_path = os.path.join(DOWNLOAD_ROOT, MANUAL_MENU_NAME, filename)

    # 避免文件名冲突
    if os.path.exists(save_path):
        base, ext = os.path.splitext(save_path)
        counter = 1
        while os.path.exists(f"{base}_{counter}{ext}"):
            counter += 1
        save_path = f"{base}_{counter}{ext}"
    return save_path


def process_manual_menu(menu_name: str, menu_conf: Dict, checkpoint: Dict):
    """产品说明书批量下载（独立数据流，不使用公告会话）"""
    print_section(f"{menu_name}（产品管理系统 API）")

    download_dir = os.path.join(DOWNLOAD_ROOT, menu_name)
    ensure_dir(download_dir)
    print_info(f"下载目录: {download_dir}")
    print_info(f"日期区间: [{START_DATE}~{END_DATE or '今天'}]（记录无日期字段时不过滤，沿用补丁脚本行为）")

    sess = get_manual_session()

    # 检查 API 连通性
    print_info("[0] 检查 API 连通性...")
    test_data = fetch_manual_product_list(sess, 1)
    if not test_data:
        print_error("产品说明书 API 不可达，跳过该栏目")
        return
    total_products = int(test_data.get("total", 0) or 0)
    print_ok(f"API 正常，共 {total_products} 个产品")

    # 加载已下载 record_id
    downloaded_ids = load_manual_downloaded_ids()
    print_info(f"已下载记录数: {len(downloaded_ids)}")

    if total_products <= 0:
        update_checkpoint_menu(checkpoint, menu_name, 0, done=True)
        save_checkpoint(checkpoint)
        print_ok(f"完成栏目: {menu_name}")
        return

    total_pages = (total_products + MANUAL_PAGE_SIZE - 1) // MANUAL_PAGE_SIZE
    print_info(f"总页数: {total_pages} (每页 {MANUAL_PAGE_SIZE} 条)")

    stats = {
        "scanned": 0,
        "skipped": 0,
        "skipped_date": 0,
        "no_manual": 0,
        "downloaded": 0,
        "failed": 0,
    }

    for page in range(1, total_pages + 1):
        if TEST_MODE and menu_name == TEST_MENU_NAME and page > TEST_PAGE_NO:
            print_info(f"测试模式结束: {menu_name}")
            break

        if MAX_PAGES > 0 and page > MAX_PAGES:
            print_info(f"达到最大页数限制: {MAX_PAGES}")
            break

        print_info(f"[页 {page}/{total_pages}] 获取产品列表...")
        if page == 1:
            products = test_data.get("list", [])
        else:
            page_data = fetch_manual_product_list(sess, page)
            if not page_data:
                print_error(f"获取第 {page} 页失败，跳过")
                continue
            products = page_data.get("list", [])

        if not products:
            print_warn(f"第 {page} 页无数据，结束")
            break

        for idx, prod in enumerate(products, start=1):
            product_code = str(prod.get("product_code") or "").strip()
            product_trade_code = str(prod.get("product_trade_code") or product_code).strip()
            product_name = str(prod.get("product_name") or "").strip()

            if not product_code:
                continue

            stats["scanned"] += 1
            global_idx = (page - 1) * MANUAL_PAGE_SIZE + idx
            prefix = f"  [{global_idx}/{total_products}]"

            # 获取销售文件记录
            records = fetch_manual_product_records(sess, product_code, product_trade_code)
            if records is None:
                print_warn(f"{prefix} 获取记录失败: {product_code} {product_name}")
                stats["failed"] += 1
                continue

            # 找所有产品说明书
            manuals = find_manual_records(records)
            if not manuals:
                stats["no_manual"] += 1
                continue

            handled_first = False
            for manual in manuals:
                if TEST_MODE and menu_name == TEST_MENU_NAME and handled_first:
                    print_info("测试模式仅处理第 1 条记录")
                    break
                handled_first = True

                record_id = str(manual.get("record_id") or "").strip()
                title = str(manual.get("title") or "").strip()
                if not record_id:
                    continue

                # 日期过滤：披露日期须在 [START_DATE, END_DATE] 区间内；
                # 记录无日期字段时沿用补丁脚本原有行为（不过滤，直接下载）
                disclose_date = extract_manual_record_date(manual)
                if disclose_date:
                    in_range, _too_old = is_in_date_range(disclose_date)
                    if not in_range:
                        print_info(f"{prefix} 跳过区间外说明书: {title} | {disclose_date} "
                                   f"区间[{START_DATE}~{END_DATE or '今天'}]")
                        stats["skipped_date"] += 1
                        continue

                # 用 record_id 去重
                if record_id in downloaded_ids:
                    stats["skipped"] += 1
                    continue

                save_path = build_manual_save_path(product_code, product_name)

                # 下载
                print_info(f"{prefix} 下载: {product_code} | {title}")
                ok, err = download_manual_pdf(sess, record_id, save_path)
                if ok:
                    file_size = os.path.getsize(save_path)
                    print_ok(f"{prefix} 已保存 ({file_size:,} bytes): {os.path.basename(save_path)}")
                    stats["downloaded"] += 1
                    append_manual_progress(record_id)
                    downloaded_ids.add(record_id)
                    write_manual_log_row(product_code, product_name, title,
                                         "SUCCEED", record_id, save_path)
                else:
                    print_error(f"{prefix} 下载失败: {err}")
                    stats["failed"] += 1
                    write_manual_log_row(product_code, product_name, title,
                                         "FAILED", record_id, "")

        # 每页完成后输出统计
        print_info(f"  --- 页 {page} 完成 | 扫描:{stats['scanned']} 去重跳过:{stats['skipped']} "
                   f"日期跳过:{stats['skipped_date']} 下载:{stats['downloaded']} "
                   f"无说明书:{stats['no_manual']} 失败:{stats['failed']}")

    # 最终统计
    print_ok(f"{menu_name}完成 | 扫描产品:{stats['scanned']} 已下载跳过:{stats['skipped']} "
             f"日期区间外:{stats['skipped_date']} 新下载:{stats['downloaded']} "
             f"无产品说明书:{stats['no_manual']} 失败:{stats['failed']}")

    update_checkpoint_menu(checkpoint, menu_name, total_pages, done=True)
    save_checkpoint(checkpoint)
    print_ok(f"完成栏目: {menu_name}")


def get_selected_menu_names() -> List[str]:
    # RUN_ONLY_MENU_NAME 支持逗号分隔的多栏目（兼容原单栏目写法）；空字符串表示全部启用栏目
    if RUN_ONLY_MENU_NAME:
        wanted = [name.strip() for name in str(RUN_ONLY_MENU_NAME).split(",") if name.strip()]
        selected = [name for name in MENU_MAP if name in wanted]
        if selected:
            return selected
        print_warn(f"RUN_ONLY_MENU_NAME 未匹配到任何栏目，回退为全部启用栏目: {RUN_ONLY_MENU_NAME}")
    if TEST_MODE and TEST_MENU_NAME in MENU_MAP:
        return [TEST_MENU_NAME]
    return [name for name, conf in MENU_MAP.items() if conf.get("enabled", True)]


def main():
    ensure_dir(DOWNLOAD_ROOT)
    for menu_name in MENU_MAP:
        ensure_dir(os.path.join(DOWNLOAD_ROOT, menu_name))

    downloaded_links = load_downloaded_links()
    downloaded_fingerprints = load_downloaded_fingerprints()
    checkpoint = load_checkpoint()

    print_section("浙银理财公告/产品说明书爬虫启动")
    print_info(f"工作目录: {SCRIPT_DIR}")
    print_info(f"下载目录: {DOWNLOAD_ROOT}")
    print_info(f"已下载链接数: {len(downloaded_links)}")
    print_info(f"已下载指纹数: {len(downloaded_fingerprints)}")

    session = get_session()
    warmup_session(session)

    selected_menu_names = get_selected_menu_names()
    print_info(f"运行栏目: {'、'.join(selected_menu_names)}")

    try:
        for menu_name in selected_menu_names:
            menu_conf = MENU_MAP[menu_name]
            if ENABLE_CHECKPOINT_RESUME and checkpoint.get("menus", {}).get(menu_name, {}).get("done"):
                if not TEST_MODE and not RUN_ONLY_MENU_NAME:
                    print_info(f"已完成，跳过栏目: {menu_name}")
                    continue

            if menu_conf.get("mode") == "api":
                process_api_menu(menu_name, menu_conf, session, downloaded_links, downloaded_fingerprints, checkpoint)
            elif menu_conf.get("mode") == "manual":
                # 产品说明书：独立数据流（产品列表→销售文件记录→PDF），单独调用
                process_manual_menu(menu_name, menu_conf, checkpoint)
            else:
                process_static_menu(menu_name, menu_conf, session, downloaded_links, downloaded_fingerprints, checkpoint)

            random_sleep(PAGE_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print_warn("用户中断，正在保存检查点")
        save_checkpoint(checkpoint)
        raise
    finally:
        save_checkpoint(checkpoint)

    print_ok("全部栏目处理完成")


if __name__ == "__main__":
    main()