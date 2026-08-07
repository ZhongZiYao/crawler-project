import csv

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

import json
import math
import os
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote, urljoin, urlparse

import requests
from DrissionPage import ChromiumPage, ChromiumOptions


def _new_page() -> ChromiumPage:
    """启动浏览器：直接在屏幕外打开并最小化，避免弹窗打扰用户。"""
    _co = ChromiumOptions()
    _co.auto_port()  # 自动选用空闲端口，避免与其他浏览器实例端口冲突
    _co.set_argument('--window-position=-32000,-32000')
    _p = ChromiumPage(_co)
    try:
        _p.set.window.mini()
    except Exception:
        pass
    return _p

# =====================================
# 用户配置区
# =====================================

# TODO[手动修改]：机构标准名称（与台账统一）
INSTITUTE_NAME = "民生理财"

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("B07", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("B07", True)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = True
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-09"
# END_DATE   = "2026-06-30"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint_tasks.txt")
PRODUCT_RESUME_FILE = os.path.join(DOWNLOAD_ROOT, "product_resume_state.json")
FAILED_PRODUCTS_FILE = os.path.join(DOWNLOAD_ROOT, "failed_products_state.json")
FINGERPRINT_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_fingerprints.txt")

HOME_URL = "https://www.cmbcwm.com.cn/sy/index.htm"
TARGET_URL = "https://www.cmbcwm.com.cn/xxpl/cpgg/gmcp/index.htm#tab1"
API_BASE = "https://www.cmbcwm.com.cn/gw/po_web/"
GLOBAL_FILE_PREFIX = "https://www.cmbc.com.cn/wwwroot/cmbc/upload/mb/samj/"

# TODO[手动修改]：抓取关键词（空字符串=全量）
PRODUCT_LIST_KEYWORD = ""

# TODO[手动修改]：可按产品编码定向调试，空字符串表示全量
TEST_ONLY_PRODUCT_CODE = ""

# TODO[手动修改]：公告类型开关
ENABLE_NOTICE_TYPES = {
    "发行公告": True,
    "到期公告": False,
    "净值公告": False,
    "定期报告": False,
    "重大事项公告": False,
    "其他公告": True,
}

# TODO[手动修改]：单模块测试名单（中文名称）；为空时按 ENABLE_NOTICE_TYPES 执行
RUN_ONLY_NOTICE_TYPES: List[str] = []

# TODO[手动修改]：单条公告联调（按关键字匹配公告标题或链接）
TEST_ONE_NOTICE_MODE = False
TEST_NOTICE_KEYWORD = ""
TEST_STOP_AFTER_FIRST_MATCH = True

# TODO[手动修改]：重试与节奏参数
REQUEST_TIMEOUT = 40
REQUEST_RETRY = 4
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 5
PAGE_NO_FILE_WAIT_SECONDS = 18
REQUEST_INTERVAL_SECONDS = (1.0, 2.2)
PAGE_INTERVAL_SECONDS = (1.8, 3.2)
API_PAGE_SIZE = 10
RESUME_LOCATE_MAX_OFFSET = 6

# TODO[手动修改]：风控/频率限制应对参数
FREQUENT_ERROR_CODE = "IGW1001"
FREQUENT_RETRY_WAIT_SECONDS = 15
FREQUENT_ERROR_COOLDOWN_SECONDS = 40
PRODUCT_LIST_FAILED_PAGE_ROUNDS = 3
PRODUCT_LIST_BATCH_COOLDOWN_EVERY = 20
PRODUCT_LIST_BATCH_COOLDOWN_SECONDS = 12

# TODO[手动修改]：流式分页与“模拟浏览”参数
SIMULATE_USER_BROWSING = True
SIMULATE_BROWSING_EVERY_PAGES = 3
SIMULATE_BROWSING_WAIT_SECONDS = (2.0, 4.5)
PRODUCT_BETWEEN_INTERVAL_SECONDS = (1.5, 3.0)
CONSECUTIVE_FAILED_PAGES_COOLDOWN_THRESHOLD = 2
CONSECUTIVE_FAILED_PAGES_COOLDOWN_SECONDS = 80

# TODO[手动修改]：文件列表频控止损策略
SKIP_REMAINING_TYPES_ON_FREQUENT = True
FILE_API_FREQUENT_COOLDOWN_SECONDS = 120
FILE_API_FREQUENT_RETRY_BASE_SECONDS = 45
FILE_API_FREQUENT_RETRY_STEP_SECONDS = 25
BLOCKED_PRODUCT_STREAK_THRESHOLD = 2
GLOBAL_BLOCKED_COOLDOWN_SECONDS = 420
DEFERRED_BLOCKED_RETRY_ROUNDS = 3
DEFERRED_BLOCKED_RETRY_COOLDOWN_SECONDS = 300

# TODO[手动修改]：自动调速 + 熔断参数
AUTO_THROTTLE_ENABLED = True
THROTTLE_WINDOW_SECONDS = 600
THROTTLE_TRIGGER_COUNT = 4
THROTTLE_STEP_EVERY_HITS = 2
THROTTLE_MAX_MULTIPLIER = 6.0
BREAKER_TRIGGER_COUNT = 10
BREAKER_COOLDOWN_SECONDS = 1800

# TODO[手动修改]：去重与断点续传开关
SKIP_DOWNLOADED = True
ENABLE_CHECKPOINT_RESUME = True

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
]

# 公告中文 -> 接口 SAMJ_TYPE 映射
NOTICE_TYPE_TO_SAMJ = {
    "发行公告": "1",
    "到期公告": "3",
    "净值公告": "8",
    "定期报告": "2",
    "重大事项公告": "z",
    "其他公告": "4",
}


class ApiBusinessError(RuntimeError):
    def __init__(self, code: str, message: str, payload: Dict):
        self.code = str(code or "")
        self.message = str(message or "")
        self.payload = payload
        super().__init__(f"接口业务返回错误[{self.code}]: {self.message}")


class FrequentLimitExhausted(RuntimeError):
    pass


AUTO_STATE = {
    "frequent_hits": [],
    "delay_multiplier": 1.0,
    "breaker_until": 0.0,
}


# =====================================
# 基础工具
# =====================================


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)



def random_sleep(seconds_range: Tuple[float, float]):
    sleep_with_throttle(random.uniform(seconds_range[0], seconds_range[1]))


def _now_ts() -> float:
    return time.time()


def _prune_frequent_hits(now_ts: Optional[float] = None):
    now_ts = now_ts if now_ts is not None else _now_ts()
    start = now_ts - THROTTLE_WINDOW_SECONDS
    AUTO_STATE["frequent_hits"] = [x for x in AUTO_STATE["frequent_hits"] if x >= start]


def _refresh_throttle_state():
    if not AUTO_THROTTLE_ENABLED:
        AUTO_STATE["delay_multiplier"] = 1.0
        return

    _prune_frequent_hits()
    hit_count = len(AUTO_STATE["frequent_hits"])
    if hit_count < THROTTLE_TRIGGER_COUNT:
        AUTO_STATE["delay_multiplier"] = 1.0
        return

    steps = 1 + (hit_count - THROTTLE_TRIGGER_COUNT) // max(1, THROTTLE_STEP_EVERY_HITS)
    AUTO_STATE["delay_multiplier"] = min(THROTTLE_MAX_MULTIPLIER, float(steps))


def sleep_with_throttle(seconds: float, reason: str = ""):
    sec = max(0.0, float(seconds))
    _refresh_throttle_state()
    mul = AUTO_STATE["delay_multiplier"]
    final_seconds = sec * mul
    if reason and mul > 1.0 and final_seconds >= 3:
        print(f"   🐢 [自动调速] {reason}，倍率={mul:.1f}，等待 {int(final_seconds)}s")
    time.sleep(final_seconds)


def register_frequent_hit(api_desc: str):
    if not AUTO_THROTTLE_ENABLED:
        return

    now_ts = _now_ts()
    AUTO_STATE["frequent_hits"].append(now_ts)
    _refresh_throttle_state()
    hit_count = len(AUTO_STATE["frequent_hits"])
    if hit_count >= BREAKER_TRIGGER_COUNT:
        until = now_ts + BREAKER_COOLDOWN_SECONDS
        if until > AUTO_STATE["breaker_until"]:
            AUTO_STATE["breaker_until"] = until
            print(
                f"   🚦 [熔断开启] 10分钟内频控命中 {hit_count} 次，"
                f"暂停到 {datetime.fromtimestamp(until).strftime('%H:%M:%S')}"
            )


def wait_if_breaker_open(session: requests.Session, browser: ChromiumPage, api_desc: str):
    if not AUTO_THROTTLE_ENABLED:
        return

    now_ts = _now_ts()
    until = float(AUTO_STATE.get("breaker_until") or 0)
    if until <= now_ts:
        return

    remain = int(until - now_ts)
    print(f"   🚫 [熔断中] {api_desc} 暂停 {remain}s 后再继续")
    simulate_user_browsing(browser, reason="熔断冷却")
    refresh_session_cookies(session, browser, reason="熔断中刷新")
    time.sleep(max(1, remain))



def now_time_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



def normalize_date(date_text: str) -> str:
    text = str(date_text or "").strip()
    match = re.search(r"(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", text)
    if match:
        y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return f"{y:04d}-{m:02d}-{d:02d}"
    return datetime.now().strftime("%Y-%m-%d")


def is_in_date_range(disclose_date: str):
    """返回 (是否在区间内, 是否过早可早停)"""
    d = normalize_date(disclose_date)
    upper = (END_DATE.strip() if END_DATE and END_DATE.strip()
             else datetime.now().strftime("%Y-%m-%d"))
    if START_DATE and d < START_DATE:
        return False, True
    if d > upper:
        return False, False
    return True, False



def sanitize_text(text: str, max_len: int = 180) -> str:
    text = re.sub(r"[\\/*?:\"<>|]", "_", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if max_len > 0:
        text = text[:max_len]
    return text



def build_unique_key(
    institute_name: str,
    product_code: str,
    notice_type: str,
    title: str,
    disclose_date: str,
) -> str:
    # TODO[手动修改]：若 unique_key 规则变更，在此统一调整
    return (
        f"{institute_name}+{sanitize_text(product_code, 80)}+{notice_type}+"
        f"{sanitize_text(title, 240)}+{disclose_date}"
    )



def build_base_filename(
    institute_name: str,
    product_name: str,
    notice_type: str,
    sales_code: str,
) -> str:
    parts = [
        sanitize_text(institute_name, 60),
        sanitize_text(product_name, 120),
        sanitize_text(notice_type, 40),
    ]
    if sales_code:
        parts.append(sanitize_text(sales_code, 60))

    clean_parts = [p for p in parts if p]
    if not clean_parts:
        clean_parts = [sanitize_text(institute_name, 60), sanitize_text(notice_type, 40)]
    return "_".join(clean_parts)



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
    ]

def log_to_csv(
    institute_name: str,
    notice_title: str,
    notice_type: str,
    disclose_date: str,
    download_time: str,
    status: str,
    source_link: str,
    save_path: str,
    unique_key: str,
):
    row = [
        institute_name,
        sanitize_text(notice_title, 240),
        notice_type,
        disclose_date,
        download_time,
        status,
        source_link,
        save_path,
        unique_key,
    ]

    exists = os.path.exists(LOG_CSV_PATH)
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(_log_header())
        writer.writerow(row)



def log_download_result(
    institute_name: str,
    notice_title: str,
    notice_type: str,
    disclose_date: str,
    status: str,
    source_link: str,
    save_path: str,
    unique_key: str,
):
    log_to_csv(
        institute_name=institute_name,
        notice_title=notice_title,
        notice_type=notice_type,
        disclose_date=disclose_date,
        download_time=now_time_str(),
        status=status,
        source_link=source_link,
        save_path=save_path,
        unique_key=unique_key,
    )



def build_product_link_key(product_code: str, source_link: str) -> str:
    code = _normalize_fingerprint(product_code)
    link = _normalize_fingerprint(source_link)
    return f"pl:{code}|{link}" if code and link else ""


def _extract_product_code_from_unique_key(unique_key: str) -> str:
    parts = [str(x).strip() for x in str(unique_key or "").split("+")]
    if len(parts) < 5:
        return ""
    candidate = parts[1]
    if re.fullmatch(r"[A-Za-z0-9]+", candidate):
        return candidate
    return ""


def load_downloaded_links() -> Set[str]:
    links = set()

    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                v = line.strip()
                if not v:
                    continue
                # 新格式：pl:<product_code>|<source_link>
                if v.startswith("pl:"):
                    links.add(_normalize_fingerprint(v))

    if os.path.exists(LOG_CSV_PATH):
        try:
            with open(LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() == "SUCCEED":
                        src = str(row.get("来源链接") or "").strip()
                        if not src:
                            continue
                        product_code = _extract_product_code_from_unique_key(str(row.get("unique_key") or ""))
                        key = build_product_link_key(product_code, src)
                        if key:
                            links.add(key)
        except Exception:
            pass

    return links



def save_downloaded_link(product_code: str, source_link: str):
    key = build_product_link_key(product_code, source_link)
    if not key:
        return
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def _normalize_fingerprint(text: str) -> str:
    return str(text or "").strip().lower()


def load_downloaded_fingerprints() -> Set[str]:
    fingerprints: Set[str] = set()

    if os.path.exists(FINGERPRINT_FILE):
        with open(FINGERPRINT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                v = _normalize_fingerprint(line)
                if v:
                    fingerprints.add(v)

    # 兼容历史数据：把历史成功日志中的 unique_key 也纳入去重指纹
    if os.path.exists(LOG_CSV_PATH):
        try:
            with open(LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() != "SUCCEED":
                        continue
                    uk = _normalize_fingerprint(str(row.get("unique_key") or ""))
                    if uk:
                        fingerprints.add(f"uk:{uk}")
        except Exception:
            pass

    return fingerprints


def save_downloaded_fingerprint(fingerprint: str):
    fp = _normalize_fingerprint(fingerprint)
    if not fp:
        return
    with open(FINGERPRINT_FILE, "a", encoding="utf-8") as f:
        f.write(fp + "\n")



def load_checkpoints() -> Set[str]:
    if not ENABLE_CHECKPOINT_RESUME:
        return set()

    tasks = set()
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                v = line.strip()
                if v:
                    tasks.add(v)
    return tasks



def save_checkpoint(task_key: str):
    if not ENABLE_CHECKPOINT_RESUME:
        return
    with open(CHECKPOINT_FILE, "a", encoding="utf-8") as f:
        f.write(task_key + "\n")


def _notice_types_signature(notice_types: List[str]) -> str:
    return "|".join(sorted([str(x).strip() for x in notice_types if str(x).strip()]))


def load_product_resume_state() -> Dict:
    if not ENABLE_CHECKPOINT_RESUME:
        return {}
    if not os.path.exists(PRODUCT_RESUME_FILE):
        return {}

    try:
        with open(PRODUCT_RESUME_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_product_resume_state(
    next_page_no: int,
    next_prd_code: str,
    keyword: str,
    notice_types: List[str],
):
    if not ENABLE_CHECKPOINT_RESUME:
        return

    payload = {
        "next_page_no": max(1, int(next_page_no)),
        "next_prd_code": str(next_prd_code or "").strip(),
        "keyword": str(keyword or "").strip(),
        "notice_types_signature": _notice_types_signature(notice_types),
        "updated_at": now_time_str(),
    }

    tmp_path = PRODUCT_RESUME_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, PRODUCT_RESUME_FILE)


def clear_product_resume_state(reason: str = ""):
    if not ENABLE_CHECKPOINT_RESUME:
        return
    if os.path.exists(PRODUCT_RESUME_FILE):
        os.remove(PRODUCT_RESUME_FILE)
        if reason:
            print(f"🧹 清理产品断点状态: {reason}")


def load_failed_products_state(keyword: str, notice_types: List[str]) -> Dict[str, Dict]:
    if not ENABLE_CHECKPOINT_RESUME:
        return {}
    if not os.path.exists(FAILED_PRODUCTS_FILE):
        return {}

    expected_keyword = str(keyword or "").strip()
    expected_sig = _notice_types_signature(notice_types)

    try:
        with open(FAILED_PRODUCTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {}

            file_keyword = str(data.get("keyword") or "").strip()
            file_sig = str(data.get("notice_types_signature") or "").strip()
            if file_keyword != expected_keyword or file_sig != expected_sig:
                return {}

            items = data.get("items") or []
            result: Dict[str, Dict] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("prd_code") or "").strip()
                if not code:
                    continue
                result[code] = {
                    "prd_code": code,
                    "prd_name": str(item.get("prd_name") or "").strip(),
                    "page_no": max(1, int(item.get("page_no") or 1)),
                    "row_index": max(0, int(item.get("row_index") or 0)),
                    "updated_at": str(item.get("updated_at") or "").strip(),
                }
            return result
    except Exception:
        return {}


def save_failed_products_state(failed_map: Dict[str, Dict], keyword: str, notice_types: List[str]):
    if not ENABLE_CHECKPOINT_RESUME:
        return

    if not failed_map:
        if os.path.exists(FAILED_PRODUCTS_FILE):
            os.remove(FAILED_PRODUCTS_FILE)
        return

    items = sorted(
        list(failed_map.values()),
        key=lambda x: (int(x.get("page_no") or 1), int(x.get("row_index") or 0), str(x.get("prd_code") or "")),
    )

    payload = {
        "keyword": str(keyword or "").strip(),
        "notice_types_signature": _notice_types_signature(notice_types),
        "updated_at": now_time_str(),
        "items": items,
    }

    tmp_path = FAILED_PRODUCTS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, FAILED_PRODUCTS_FILE)


def mark_product_failed(
    failed_map: Dict[str, Dict],
    product: Dict,
    page_no: int,
    row_index: int,
):
    code = str(product.get("prdCode") or "").strip()
    if not code:
        return
    failed_map[code] = {
        "prd_code": code,
        "prd_name": str(product.get("prdName") or "").strip(),
        "page_no": max(1, int(page_no)),
        "row_index": max(0, int(row_index)),
        "updated_at": now_time_str(),
    }


def unmark_product_failed(failed_map: Dict[str, Dict], prd_code: str):
    code = str(prd_code or "").strip()
    if not code:
        return
    if code in failed_map:
        failed_map.pop(code, None)


def fetch_product_by_code(
    session: requests.Session,
    browser: ChromiumPage,
    prd_code: str,
) -> Optional[Dict]:
    code = str(prd_code or "").strip()
    if not code:
        return None

    payload = {
        "pageNo": 1,
        "pageSize": API_PAGE_SIZE,
        "keyWord": code,
    }
    data = post_api_with_retry(
        session=session,
        browser=browser,
        api_name="BTAProductListAll",
        data=payload,
        api_desc=f"BTAProductListAll 按编码查询 {code}",
    )
    if not data:
        return None

    rows = data.get("list") or []
    for row in rows:
        if str(row.get("prdCode") or "").strip() == code:
            return row
    return rows[0] if rows else None



def build_headers() -> Dict[str, str]:
    return {
        "accept": "application/json,text/javascript,*/*;q=0.01",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "origin": "https://www.cmbcwm.com.cn",
        "referer": TARGET_URL,
        "user-agent": random.choice(USER_AGENTS),
        "x-requested-with": "XMLHttpRequest",
    }



def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(build_headers())
    return s


def create_browser() -> ChromiumPage:
    # 保持与 B05 一致：使用 ChromiumPage 来获取/刷新 Cookie
    _page = _new_page()
    return _page


def get_browser_cookies_dict(browser: ChromiumPage) -> Dict[str, str]:
    cookies = browser.cookies() or []
    cookie_dict: Dict[str, str] = {}
    for item in cookies:
        name = item.get("name")
        value = item.get("value")
        if name:
            cookie_dict[name] = value
    return cookie_dict


def sync_session_cookies_from_browser(session: requests.Session, browser: ChromiumPage) -> int:
    cookie_dict = get_browser_cookies_dict(browser)
    if cookie_dict:
        session.cookies.update(cookie_dict)
    return len(cookie_dict)


def rotate_session_user_agent(session: requests.Session):
    session.headers.update({"user-agent": random.choice(USER_AGENTS)})



def warmup_session(session: requests.Session):
    print("🍪 预热会话并获取 Cookie...")
    for url in [HOME_URL, TARGET_URL]:
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            print(f"   🌐 {url} -> {resp.status_code}")
            sleep_with_throttle(1.2)
        except Exception as err:
            print(f"   ⚠️ 预热访问失败: {url} | {err}")


def warmup_and_sync_cookies(session: requests.Session, browser: ChromiumPage):
    print("🍪 正在预热页面并同步 Cookie...")
    for url in [HOME_URL, TARGET_URL]:
        print(f"   🌐 访问: {url}")
        browser.get(url)
        sleep_with_throttle(2)
    count = sync_session_cookies_from_browser(session, browser)
    print(f"   ✅ Cookie 同步完成，同步数量: {count}")


def refresh_session_cookies(session: requests.Session, browser: ChromiumPage, reason: str = ""):
    reason_text = f"（原因: {reason}）" if reason else ""
    print(f"🍪 正在刷新 Cookie {reason_text}")
    browser.get(TARGET_URL)
    sleep_with_throttle(2)
    count = sync_session_cookies_from_browser(session, browser)
    rotate_session_user_agent(session)
    print(f"   ✅ Cookie 刷新完成，同步数量: {count}")



def post_api_with_retry(
    session: requests.Session,
    browser: ChromiumPage,
    api_name: str,
    data: Dict,
    api_desc: str,
    raise_on_frequent_exhausted: bool = False,
) -> Optional[Dict]:
    url = urljoin(API_BASE, api_name)
    last_err = None

    for attempt in range(1, REQUEST_RETRY + 1):
        wait_if_breaker_open(session, browser, api_desc)
        try:
            resp = session.post(
                url,
                data=data,
                timeout=REQUEST_TIMEOUT,
                headers={"content-type": "application/x-www-form-urlencoded; charset=UTF-8"},
            )
            resp.raise_for_status()
            j = resp.json()
            rc_data = j.get("returnCode") or {}
            rc = rc_data.get("type")
            if rc == "E":
                raise ApiBusinessError(
                    code=str(rc_data.get("code") or ""),
                    message=str(rc_data.get("message") or ""),
                    payload=j,
                )
            return j
        except ApiBusinessError as err:
            last_err = err
            is_frequent = err.code == FREQUENT_ERROR_CODE
            is_file_api = api_name == "BTAFileQry"
            if attempt < REQUEST_RETRY:
                if is_frequent:
                    register_frequent_hit(api_desc)
                    if is_file_api:
                        wait_seconds = (
                            FILE_API_FREQUENT_RETRY_BASE_SECONDS
                            + max(0, attempt - 1) * FILE_API_FREQUENT_RETRY_STEP_SECONDS
                        )
                    else:
                        wait_seconds = FREQUENT_RETRY_WAIT_SECONDS + max(0, attempt - 1) * 3
                    print(
                        f"    🛡️ [频控命中] {api_desc} 返回 {err.code}，"
                        f"第{attempt}次重试前等待 {wait_seconds}s"
                    )
                    refresh_session_cookies(
                        session=session,
                        browser=browser,
                        reason=f"{api_desc} 频控 {err.code}",
                    )
                    sleep_with_throttle(wait_seconds, reason="频控重试等待")
                else:
                    print(
                        f"    🔁 [请求重试] {api_desc} 第{attempt}次失败，"
                        f"{RETRY_WAIT_SECONDS}s 后重试"
                    )
                    sleep_with_throttle(RETRY_WAIT_SECONDS)
            else:
                print(f"    ❌ [请求失败] {api_desc} | {err}")
        except Exception as err:
            last_err = err
            if attempt < REQUEST_RETRY:
                print(
                    f"    🔁 [请求重试] {api_desc} 第{attempt}次失败，"
                    f"{RETRY_WAIT_SECONDS}s 后重试"
                )
                sleep_with_throttle(RETRY_WAIT_SECONDS)
            else:
                print(f"    ❌ [请求失败] {api_desc} | {last_err}")

    if (
        raise_on_frequent_exhausted
        and isinstance(last_err, ApiBusinessError)
        and last_err.code == FREQUENT_ERROR_CODE
    ):
        raise FrequentLimitExhausted(f"{api_desc} 频控持续命中: {last_err}")

    return None



def resolve_notice_types_to_run() -> List[str]:
    if RUN_ONLY_NOTICE_TYPES:
        result = []
        for name in RUN_ONLY_NOTICE_TYPES:
            if name in NOTICE_TYPE_TO_SAMJ:
                result.append(name)
            else:
                print(f"[警告] RUN_ONLY_NOTICE_TYPES 存在未知类型: {name}")
        return result

    result = []
    for name, enabled in ENABLE_NOTICE_TYPES.items():
        if enabled and name in NOTICE_TYPE_TO_SAMJ:
            result.append(name)
    return result



def simulate_user_browsing(browser: ChromiumPage, reason: str = ""):
    if not SIMULATE_USER_BROWSING:
        return

    reason_text = f"（{reason}）" if reason else ""
    print(f"   🧭 [模拟浏览] 执行页面浏览与滚动 {reason_text}")
    try:
        browse_url = TARGET_URL if random.random() < 0.8 else HOME_URL
        browser.get(browse_url)
        time.sleep(random.uniform(*SIMULATE_BROWSING_WAIT_SECONDS))
        scroll_y = random.randint(280, 1600)
        browser.run_js(f"window.scrollTo(0, {scroll_y});")
        time.sleep(random.uniform(0.4, 1.2))
        if random.random() < 0.45:
            browser.run_js("window.scrollTo(0, 0);")
            time.sleep(random.uniform(0.2, 0.6))
    except Exception as err:
        print(f"   ⚠️ [模拟浏览失败] {err}")


def process_one_product(
    session: requests.Session,
    browser: ChromiumPage,
    product: Dict,
    product_index: int,
    total_hint: int,
    notice_types: List[str],
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    checkpoints: Set[str],
    counters: Dict[str, int],
) -> str:
    prd_name = str(product.get("prdName") or "").strip() or "未命名产品"
    prd_code = str(product.get("prdCode") or "").strip() or "无编码"
    prd_reg = str(product.get("prdRegistCode") or "").strip()

    # 产品级快速跳过：该产品所有公告类型都已写入 checkpoint 时，无需进入逐类型处理。
    if ENABLE_CHECKPOINT_RESUME and prd_code and notice_types:
        all_done = True
        for notice_type in notice_types:
            task_key = f"{prd_code}|{notice_type}"
            if task_key not in checkpoints:
                all_done = False
                break
        if all_done:
            print(f"   ⏭️ [产品级跳过] {prd_code} 全部公告类型已完成")
            return "ok"

    print("\n" + "=" * 80)
    print(f"🚀 正在处理产品 {product_index}/{total_hint}")
    print(f"   🏷️ 产品名称: {prd_name}")
    print(f"   🔢 产品编码: {prd_code}")
    if prd_reg:
        print(f"   🧾 产品登记编码: {prd_reg}")
    print("=" * 80)

    has_incomplete_type = False

    for notice_type in notice_types:
        status = crawl_product_notice_type(
            session=session,
            browser=browser,
            product=product,
            notice_type=notice_type,
            downloaded_links=downloaded_links,
            downloaded_fingerprints=downloaded_fingerprints,
            checkpoints=checkpoints,
            counters=counters,
        )
        if status == "stop":
            return "stop"

        if status == "incomplete":
            has_incomplete_type = True
            continue

        if status == "blocked":
            print(
                f"    🧊 [产品降载] 命中频控，先进行冷却 {FILE_API_FREQUENT_COOLDOWN_SECONDS}s，"
                "避免连续触发"
            )
            simulate_user_browsing(browser, reason="产品级频控冷却")
            refresh_session_cookies(
                session=session,
                browser=browser,
                reason=f"产品 {prd_code} 命中频控",
            )
            sleep_with_throttle(FILE_API_FREQUENT_COOLDOWN_SECONDS, reason="产品级频控冷却")

            if SKIP_REMAINING_TYPES_ON_FREQUENT:
                print("    ⏭️ [止损] 跳过该产品剩余公告类型，继续下一个产品")
                return "blocked"

    if has_incomplete_type:
        print("    🔁 [产品未完成] 存在失败或接口异常公告类型，保留下次继续")
        return "incomplete"

    random_sleep(PRODUCT_BETWEEN_INTERVAL_SECONDS)
    return "ok"


def process_products_stream(
    session: requests.Session,
    browser: ChromiumPage,
    notice_types: List[str],
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    checkpoints: Set[str],
    counters: Dict[str, int],
) -> bool:
    print("🧭 正在获取公募产品清单（流式分页处理）...")
    effective_keyword = TEST_ONLY_PRODUCT_CODE.strip() or PRODUCT_LIST_KEYWORD

    resume_enabled = ENABLE_CHECKPOINT_RESUME and not TEST_ONLY_PRODUCT_CODE.strip()
    resume_state = load_product_resume_state() if resume_enabled else {}
    failed_products_map = (
        load_failed_products_state(keyword=effective_keyword, notice_types=notice_types)
        if resume_enabled
        else {}
    )
    resume_notice_sig = _notice_types_signature(notice_types)
    start_page_no = 1
    start_prd_code = ""

    if resume_enabled and failed_products_map:
        print(f"   🧯 历史失败产品数: {len(failed_products_map)}")

    if resume_enabled and resume_state:
        state_keyword = str(resume_state.get("keyword") or "").strip()
        state_sig = str(resume_state.get("notice_types_signature") or "").strip()
        if state_keyword == effective_keyword and state_sig == resume_notice_sig:
            start_page_no = max(1, int(resume_state.get("next_page_no") or 1))
            start_prd_code = str(resume_state.get("next_prd_code") or "").strip()
            print(
                f"   🧷 [产品断点] 从第{start_page_no}页继续"
                + (f"，产品编码 {start_prd_code}" if start_prd_code else "")
            )
        else:
            clear_product_resume_state(reason="关键词或公告类型变更，重置产品断点")

    def fetch_product_page(page_no: int, api_desc: str) -> Optional[Dict]:
        payload = {
            "pageNo": page_no,
            "pageSize": API_PAGE_SIZE,
            "keyWord": effective_keyword,
        }
        return post_api_with_retry(
            session=session,
            browser=browser,
            api_name="BTAProductListAll",
            data=payload,
            api_desc=api_desc,
        )

    def find_code_index(rows: List[Dict], code: str) -> int:
        for idx, row in enumerate(rows):
            if str(row.get("prdCode") or "").strip() == code:
                return idx
        return -1

    first = fetch_product_page(start_page_no, f"BTAProductListAll 第{start_page_no}页")
    if not first:
        print("❌ 未获取到产品列表首页")
        return False

    total = int(first.get("totalSize") or 0)
    pages = max(1, math.ceil(total / API_PAGE_SIZE))
    print(f"   📚 产品总数: {total}，页数: {pages}")

    first_rows = first.get("list") or []
    start_row_index = 0
    if start_prd_code:
        start_row_index = find_code_index(first_rows, start_prd_code)
        if start_row_index < 0:
            # 列表有新增/删除时，断点产品可能滑动到邻近页；先做小范围定位。
            located = False
            visited_pages = {start_page_no}
            for offset in range(1, RESUME_LOCATE_MAX_OFFSET + 1):
                candidates = [start_page_no + offset, start_page_no - offset]
                for candidate_page_no in candidates:
                    if candidate_page_no < 1 or candidate_page_no > pages:
                        continue
                    if candidate_page_no in visited_pages:
                        continue
                    visited_pages.add(candidate_page_no)

                    page_data = fetch_product_page(
                        candidate_page_no,
                        f"BTAProductListAll 断点定位 第{candidate_page_no}页",
                    )
                    if not page_data:
                        continue
                    candidate_rows = page_data.get("list") or []
                    idx = find_code_index(candidate_rows, start_prd_code)
                    if idx >= 0:
                        start_page_no = candidate_page_no
                        first_rows = candidate_rows
                        start_row_index = idx
                        located = True
                        print(
                            f"   ✅ [产品断点] 已定位到第{start_page_no}页，"
                            f"产品编码 {start_prd_code}"
                        )
                        break
                if located:
                    break

            if not located:
                print("   ⚠️ [产品断点] 邻近页未找到断点产品编码，按页首继续")
                start_prd_code = ""
                start_row_index = 0

    seen_codes: Set[str] = set()
    failed_pages: List[int] = []
    processed_count = 0
    stop_early = False
    consecutive_failed_pages = 0
    blocked_product_streak = 0
    deferred_retry_products: List[Dict] = []
    has_unfinished_product = False
    resume_skip_active = bool(start_prd_code)

    def persist_failed_products_state():
        if not resume_enabled:
            return
        save_failed_products_state(
            failed_map=failed_products_map,
            keyword=effective_keyword,
            notice_types=notice_types,
        )

    def save_resume_to_position(page_no: int, prd_code: str):
        if not resume_enabled:
            return
        save_product_resume_state(
            next_page_no=page_no,
            next_prd_code=prd_code,
            keyword=effective_keyword,
            notice_types=notice_types,
        )

    def save_resume_to_next(page_no: int, rows: List[Dict], idx: int):
        if not resume_enabled:
            return

        next_page_no = page_no
        next_prd_code = ""
        if idx + 1 < len(rows):
            next_prd_code = str(rows[idx + 1].get("prdCode") or "").strip()
            next_page_no = page_no
        else:
            next_page_no = page_no + 1
            next_prd_code = ""

        save_product_resume_state(
            next_page_no=next_page_no,
            next_prd_code=next_prd_code,
            keyword=effective_keyword,
            notice_types=notice_types,
        )

    def set_product_failed_state(
        product: Dict,
        page_no: int,
        row_index: int,
        failed: bool,
    ):
        code = str(product.get("prdCode") or "").strip()
        if not code:
            return
        if failed:
            mark_product_failed(
                failed_map=failed_products_map,
                product=product,
                page_no=page_no,
                row_index=row_index,
            )
        else:
            unmark_product_failed(failed_products_map, code)
        persist_failed_products_state()

    def is_before_resume(item: Dict) -> bool:
        item_page = max(1, int(item.get("page_no") or 1))
        item_row = max(0, int(item.get("row_index") or 0))
        if item_page < start_page_no:
            return True
        if item_page > start_page_no:
            return False
        if start_prd_code:
            return item_row < start_row_index
        return False

    if resume_enabled and failed_products_map and (start_page_no > 1 or start_prd_code):
        pending_before = [x for x in failed_products_map.values() if is_before_resume(x)]
        pending_before.sort(
            key=lambda x: (
                int(x.get("page_no") or 1),
                int(x.get("row_index") or 0),
                str(x.get("prd_code") or ""),
            )
        )
        if pending_before:
            print(f"   🔁 [补旧账] 断点前待补跑失败产品数: {len(pending_before)}")

        for item in pending_before:
            code = str(item.get("prd_code") or "").strip()
            if not code:
                continue

            product = fetch_product_by_code(session=session, browser=browser, prd_code=code)
            if not product:
                print(f"   ⚠️ [补旧账失败] 未查询到产品: {code}")
                has_unfinished_product = True
                continue

            processed_count += 1
            result = process_one_product(
                session=session,
                browser=browser,
                product=product,
                product_index=processed_count,
                total_hint=max(total, processed_count),
                notice_types=notice_types,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
                checkpoints=checkpoints,
                counters=counters,
            )
            if result == "stop":
                return True

            if result in ("blocked", "incomplete"):
                has_unfinished_product = True
                set_product_failed_state(
                    product=product,
                    page_no=max(1, int(item.get("page_no") or start_page_no)),
                    row_index=max(0, int(item.get("row_index") or 0)),
                    failed=True,
                )
            else:
                set_product_failed_state(product=product, page_no=1, row_index=0, failed=False)

            random_sleep(PRODUCT_BETWEEN_INTERVAL_SECONDS)

        pending_before_after = [x for x in failed_products_map.values() if is_before_resume(x)]
        if pending_before_after:
            print(
                f"   ⛔ [补旧账未清零] 断点前仍有 {len(pending_before_after)} 个失败产品，"
                "本次不进入断点后流程"
            )
            return False

    def process_rows(rows: List[Dict], current_page_no: int) -> str:
        nonlocal has_unfinished_product
        nonlocal resume_skip_active
        nonlocal processed_count
        nonlocal blocked_product_streak
        for idx, row in enumerate(rows):
            code = str(row.get("prdCode") or "").strip()
            if not code:
                continue
            if code in seen_codes:
                continue
            if TEST_ONLY_PRODUCT_CODE.strip() and code != TEST_ONLY_PRODUCT_CODE.strip():
                continue

            if resume_skip_active and current_page_no == start_page_no:
                if code != start_prd_code:
                    continue
                resume_skip_active = False

            seen_codes.add(code)
            processed_count += 1
            result = process_one_product(
                session=session,
                browser=browser,
                product=row,
                product_index=processed_count,
                total_hint=max(total, processed_count),
                notice_types=notice_types,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
                checkpoints=checkpoints,
                counters=counters,
            )
            if result == "stop":
                return "stop"

            if result == "blocked":
                # 该产品已执行止损冷却，直接进入下一个产品
                deferred_retry_products.append(row)
                has_unfinished_product = True
                set_product_failed_state(
                    product=row,
                    page_no=current_page_no,
                    row_index=idx,
                    failed=True,
                )
                save_resume_to_position(current_page_no, code)
                blocked_product_streak += 1
                if blocked_product_streak >= BLOCKED_PRODUCT_STREAK_THRESHOLD:
                    print(
                        f"   🧊 [全局降载] 连续 {blocked_product_streak} 个产品被频控，"
                        f"执行全局冷却 {GLOBAL_BLOCKED_COOLDOWN_SECONDS}s"
                    )
                    simulate_user_browsing(browser, reason="连续产品被频控")
                    refresh_session_cookies(
                        session=session,
                        browser=browser,
                        reason=f"连续{blocked_product_streak}个产品频控",
                    )
                    sleep_with_throttle(GLOBAL_BLOCKED_COOLDOWN_SECONDS, reason="全局降载冷却")
                    blocked_product_streak = 0
                continue

            if result == "incomplete":
                deferred_retry_products.append(row)
                has_unfinished_product = True
                set_product_failed_state(
                    product=row,
                    page_no=current_page_no,
                    row_index=idx,
                    failed=True,
                )
                save_resume_to_position(current_page_no, code)
                blocked_product_streak = 0
                continue

            set_product_failed_state(product=row, page_no=current_page_no, row_index=idx, failed=False)
            save_resume_to_next(current_page_no, rows, idx)
            blocked_product_streak = 0
        return "ok"

    if process_rows(first_rows, start_page_no) == "stop":
        return True

    for page_no in range(start_page_no + 1, pages + 1):
        if SIMULATE_USER_BROWSING and page_no % SIMULATE_BROWSING_EVERY_PAGES == 0:
            simulate_user_browsing(browser, reason=f"处理前第{page_no}页")

        payload = {
            "pageNo": page_no,
            "pageSize": API_PAGE_SIZE,
            "keyWord": effective_keyword,
        }
        page_data = post_api_with_retry(
            session=session,
            browser=browser,
            api_name="BTAProductListAll",
            data=payload,
            api_desc=f"BTAProductListAll 第{page_no}页",
        )
        if not page_data:
            print(f"   ⚠️ 第{page_no}页拉取失败，记录为待回补分页")
            failed_pages.append(page_no)
            consecutive_failed_pages += 1
            sleep_with_throttle(PAGE_NO_FILE_WAIT_SECONDS)

            if consecutive_failed_pages >= CONSECUTIVE_FAILED_PAGES_COOLDOWN_THRESHOLD:
                print(
                    f"   🧊 [连续失败冷却] 连续失败页达到阈值，等待 "
                    f"{CONSECUTIVE_FAILED_PAGES_COOLDOWN_SECONDS}s"
                )
                simulate_user_browsing(browser, reason="连续失败后冷却")
                refresh_session_cookies(
                    session=session,
                    browser=browser,
                    reason=f"产品分页连续失败{consecutive_failed_pages}次",
                )
                sleep_with_throttle(CONSECUTIVE_FAILED_PAGES_COOLDOWN_SECONDS, reason="分页连续失败冷却")
                consecutive_failed_pages = 0
            continue

        consecutive_failed_pages = 0
        rows = page_data.get("list") or []
        if process_rows(rows, page_no) == "stop":
            stop_early = True
            break

        if page_no % PRODUCT_LIST_BATCH_COOLDOWN_EVERY == 0:
            print(
                f"   💤 [批次冷却] 已处理到第{page_no}页，"
                f"等待 {PRODUCT_LIST_BATCH_COOLDOWN_SECONDS}s 降低频控风险"
            )
            sleep_with_throttle(PRODUCT_LIST_BATCH_COOLDOWN_SECONDS)

        random_sleep(REQUEST_INTERVAL_SECONDS)

    if failed_pages and not stop_early:
        print(f"   🔄 待回补分页数量: {len(failed_pages)}")

    for round_idx in range(1, PRODUCT_LIST_FAILED_PAGE_ROUNDS + 1):
        if not failed_pages or stop_early:
            break

        print(
            f"   🔁 开始失败分页回补第 {round_idx}/{PRODUCT_LIST_FAILED_PAGE_ROUNDS} 轮，"
            f"当前待回补: {len(failed_pages)}"
        )
        simulate_user_browsing(browser, reason=f"回补前第{round_idx}轮")
        sleep_with_throttle(FREQUENT_ERROR_COOLDOWN_SECONDS)
        refresh_session_cookies(
            session=session,
            browser=browser,
            reason=f"产品列表回补第{round_idx}轮",
        )

        next_failed: List[int] = []
        for page_no in failed_pages:
            payload = {
                "pageNo": page_no,
                "pageSize": API_PAGE_SIZE,
                "keyWord": effective_keyword,
            }
            page_data = post_api_with_retry(
                session=session,
                browser=browser,
                api_name="BTAProductListAll",
                data=payload,
                api_desc=f"BTAProductListAll 回补第{page_no}页",
            )
            if not page_data:
                next_failed.append(page_no)
                continue

            rows = page_data.get("list") or []
            if process_rows(rows, page_no) == "stop":
                stop_early = True
                break
            random_sleep(REQUEST_INTERVAL_SECONDS)

        failed_pages = next_failed

    if failed_pages and not stop_early:
        print(f"   ⚠️ 最终仍有 {len(failed_pages)} 个分页回补失败，可能导致产品缺失")

    # 对被阻断/失败未完成的产品做延后重试，避免同一时间窗内反复触发
    if deferred_retry_products and not stop_early:
        print(f"   🔁 待延后重试的未完成产品数: {len(deferred_retry_products)}")

    for retry_round in range(1, DEFERRED_BLOCKED_RETRY_ROUNDS + 1):
        if not deferred_retry_products or stop_early:
            break

        print(
            f"   🧪 [延后重试] 第 {retry_round}/{DEFERRED_BLOCKED_RETRY_ROUNDS} 轮，"
            f"产品数: {len(deferred_retry_products)}"
        )
        simulate_user_browsing(browser, reason=f"延后重试前第{retry_round}轮")
        refresh_session_cookies(session, browser, reason=f"延后重试第{retry_round}轮")
        sleep_with_throttle(DEFERRED_BLOCKED_RETRY_COOLDOWN_SECONDS, reason="延后重试冷却")

        still_unfinished: List[Dict] = []
        for product in deferred_retry_products:
            code = str(product.get("prdCode") or "").strip()
            if not code:
                continue

            processed_count += 1
            result = process_one_product(
                session=session,
                browser=browser,
                product=product,
                product_index=processed_count,
                total_hint=max(total, processed_count),
                notice_types=notice_types,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
                checkpoints=checkpoints,
                counters=counters,
            )
            if result == "stop":
                stop_early = True
                break
            if result in ("blocked", "incomplete"):
                still_unfinished.append(product)
                has_unfinished_product = True
                set_product_failed_state(
                    product=product,
                    page_no=start_page_no,
                    row_index=0,
                    failed=True,
                )
                save_resume_to_position(start_page_no, code)
            else:
                set_product_failed_state(product=product, page_no=start_page_no, row_index=0, failed=False)

            random_sleep(PRODUCT_BETWEEN_INTERVAL_SECONDS)

        deferred_retry_products = still_unfinished

    if deferred_retry_products and not stop_early:
        print(
            f"   ⚠️ 延后重试后仍有 {len(deferred_retry_products)} 个产品未完成，"
            "建议下次继续断点续传"
        )

    if failed_pages and resume_enabled:
        save_resume_to_position(min(failed_pages), "")

    if (
        resume_enabled
        and not stop_early
        and not failed_pages
        and not deferred_retry_products
        and not has_unfinished_product
    ):
        clear_product_resume_state(reason="本轮产品全量处理完成")
        if failed_products_map:
            failed_products_map.clear()
            persist_failed_products_state()

    if resume_enabled and failed_products_map and (failed_pages or deferred_retry_products or has_unfinished_product):
        persist_failed_products_state()

    print(f"   ✅ 流式产品处理完成，已处理去重后产品数: {len(seen_codes)}")
    return stop_early



def build_notice_file_link(result: Dict, item: Dict) -> str:
    old_url = str(result.get("oldUrl") or "").strip()
    new_url = str(result.get("newUrl") or "").strip()
    urlflag = str(item.get("URLFLAG") or "").strip()
    file_name = str(item.get("K_FILENAME") or "").strip()

    if not file_name:
        return ""

    prefix = {"0": old_url, "1": new_url}.get(urlflag) or GLOBAL_FILE_PREFIX
    return prefix.rstrip("/") + "/" + quote(file_name)



def guess_extension_from_headers(response: requests.Response, default_ext: str = ".pdf") -> str:
    content_type = (response.headers.get("Content-Type") or "").lower()
    disposition = response.headers.get("Content-Disposition") or ""

    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.IGNORECASE)
    if m:
        name = m.group(1).strip().strip('"')
        ext = os.path.splitext(name)[1].lower()
        if ext:
            return ext

    if "pdf" in content_type:
        return ".pdf"
    if "officedocument.wordprocessingml.document" in content_type:
        return ".docx"
    if "msword" in content_type:
        return ".doc"
    if "excel" in content_type or "spreadsheetml" in content_type:
        return ".xlsx"
    if "zip" in content_type:
        return ".zip"
    if "text/html" in content_type:
        return ".html"

    path_ext = os.path.splitext(urlparse(response.url).path)[1].lower()
    if path_ext:
        return path_ext

    return default_ext



def extract_document_link_from_html(html_text: str, base_url: str) -> str:
    pattern = r"href\s*=\s*[\"']([^\"']+\.(?:pdf|doc|docx|xls|xlsx|zip|ppt|pptx))(?:\?[^\"']*)?[\"']"
    m = re.search(pattern, html_text, re.IGNORECASE)
    if not m:
        return ""
    href = m.group(1)
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(base_url, href)



def save_html_as_word(html_text: str, folder: str, base_name: str) -> Tuple[str, str]:
    save_path, file_name = build_unique_save_path(folder, base_name, ".doc")
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return save_path, file_name



def download_file_with_fallback(
    session: requests.Session,
    source_link: str,
    save_folder: str,
    base_file_name: str,
) -> Tuple[str, str, str]:
    with session.get(
        source_link,
        timeout=REQUEST_TIMEOUT,
        stream=True,
        allow_redirects=True,
        headers={"referer": TARGET_URL, "user-agent": random.choice(USER_AGENTS)},
    ) as response:
        response.raise_for_status()
        ext = guess_extension_from_headers(response)

        if ext == ".html":
            html_text = response.text
            doc_link = extract_document_link_from_html(html_text, response.url)
            if doc_link:
                with session.get(
                    doc_link,
                    timeout=REQUEST_TIMEOUT,
                    stream=True,
                    allow_redirects=True,
                    headers={"referer": TARGET_URL, "user-agent": random.choice(USER_AGENTS)},
                ) as doc_resp:
                    doc_resp.raise_for_status()
                    doc_ext = guess_extension_from_headers(doc_resp)
                    if doc_ext == ".html":
                        # 对 HTML 文件按规范转 Word 保存
                        html2 = doc_resp.text
                        save_path, file_name = save_html_as_word(html2, save_folder, base_file_name)
                        if os.path.getsize(save_path) < 128:
                            raise RuntimeError("HTML 转 Word 结果过小，疑似失败")
                        return doc_resp.url, save_path, file_name

                    save_path, file_name = build_unique_save_path(save_folder, base_file_name, doc_ext)
                    with open(save_path, "wb") as f:
                        for chunk in doc_resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    if os.path.getsize(save_path) < 128:
                        raise RuntimeError("文档下载结果过小，疑似失败")
                    return doc_resp.url, save_path, file_name

            # 页面里无可直连文档时，转 Word
            save_path, file_name = save_html_as_word(html_text, save_folder, base_file_name)
            if os.path.getsize(save_path) < 128:
                raise RuntimeError("HTML 转 Word 结果过小，疑似失败")
            return response.url, save_path, file_name

        save_path, file_name = build_unique_save_path(save_folder, base_file_name, ext)
        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        if os.path.getsize(save_path) < 128:
            raise RuntimeError("下载文件过小，疑似失败")

        return response.url, save_path, file_name



def process_one_notice(
    session: requests.Session,
    product: Dict,
    notice_type: str,
    result_bta: Dict,
    item: Dict,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
) -> str:
    folder_path = os.path.join(DOWNLOAD_ROOT, notice_type)
    ensure_dir(folder_path)

    product_name = sanitize_text(str(product.get("prdName") or "").strip(), 120)
    product_code = str(product.get("prdCode") or "").strip()

    title = str(item.get("K_INFNAME") or "").strip() or "未命名公告"
    disclose_date = normalize_date(str(item.get("BUSINESS_DATE") or ""))

    _in_range, _too_old = is_in_date_range(disclose_date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"      ⏭️ [日期跳过] 披露日期 {disclose_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return "too_old" if _too_old else "skip"

    # 销售代码：优先记录接口行内 PID，其次 REAL_PRD_CODE，再回退产品编码
    sales_code = (
        str(item.get("PID") or "").strip()
        or str(item.get("REAL_PRD_CODE") or "").strip()
        or product_code
    )

    source_link = build_notice_file_link(result_bta, item)
    base_file_name = build_base_filename(INSTITUTE_NAME, product_name, notice_type, sales_code)
    date_token = re.sub(r"[^0-9]", "", disclose_date)
    if date_token:
        base_file_name = f"{base_file_name}({date_token})"
    unique_key = build_unique_key(INSTITUTE_NAME, product_code, notice_type, title, disclose_date)

    file_name_raw = _normalize_fingerprint(str(item.get("K_FILENAME") or ""))
    fp_candidates: List[str] = []
    if file_name_raw:
        fp_candidates.append(f"file:{product_code}|{notice_type}|{file_name_raw}")
    fp_candidates.append(f"uk:{_normalize_fingerprint(unique_key)}")

    if SKIP_DOWNLOADED and any(fp in downloaded_fingerprints for fp in fp_candidates):
        print(f"      ⏭️ [跳过] 公告指纹已下载: {title}")
        return "skipped"

    if not source_link:
        expected_save_path, _ = build_unique_save_path(folder_path, base_file_name, ".pdf")
        log_download_result(
            institute_name=INSTITUTE_NAME,
            notice_title=title,
            notice_type=notice_type,
            disclose_date=disclose_date,
            status="FAILED",
            source_link="",
            save_path=os.path.abspath(expected_save_path),
            unique_key=unique_key,
        )
        print(f"      ❌ [下载失败] 缺少来源链接: {title}")
        return "failed"

    source_link_key = build_product_link_key(product_code, source_link)
    if SKIP_DOWNLOADED and source_link_key and source_link_key in downloaded_links:
        print(f"      ⏭️ [跳过] 链接已下载: {title}")
        return "skipped"

    expected_save_path, _ = build_unique_save_path(folder_path, base_file_name, ".pdf")

    last_err = None
    for attempt in range(1, DOWNLOAD_RETRY + 1):
        try:
            final_url, save_path, file_name = download_file_with_fallback(
                session=session,
                source_link=source_link,
                save_folder=folder_path,
                base_file_name=base_file_name,
            )

            if source_link_key:
                downloaded_links.add(source_link_key)
                save_downloaded_link(product_code, source_link)
            if final_url and final_url != source_link:
                final_key = build_product_link_key(product_code, final_url)
                if final_key:
                    downloaded_links.add(final_key)
                    save_downloaded_link(product_code, final_url)

            for fp in fp_candidates:
                downloaded_fingerprints.add(fp)
                save_downloaded_fingerprint(fp)

            final_source = final_url or source_link
            log_download_result(
                institute_name=INSTITUTE_NAME,
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="SUCCEED",
                source_link=final_source,
                save_path=os.path.abspath(save_path),
                unique_key=unique_key,
            )
            print(f"      ✅ [下载成功] {file_name}")
            return "succeed"
        except Exception as err:
            last_err = err
            if attempt < DOWNLOAD_RETRY:
                print(
                    f"      🔁 [下载重试] {title} 第{attempt}次失败，"
                    f"{RETRY_WAIT_SECONDS}s 后重试"
                )
                sleep_with_throttle(RETRY_WAIT_SECONDS)
            else:
                log_download_result(
                    institute_name=INSTITUTE_NAME,
                    notice_title=title,
                    notice_type=notice_type,
                    disclose_date=disclose_date,
                    status="FAILED",
                    source_link=source_link,
                    save_path=os.path.abspath(expected_save_path),
                    unique_key=unique_key,
                )
                print(f"      ❌ [下载失败] {title} | {last_err}")
                return "failed"



def crawl_product_notice_type(
    session: requests.Session,
    browser: ChromiumPage,
    product: Dict,
    notice_type: str,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    checkpoints: Set[str],
    counters: Dict[str, int],
) -> str:
    prd_code = str(product.get("prdCode") or "").strip()
    prd_name = str(product.get("prdName") or "").strip()
    samj_type = NOTICE_TYPE_TO_SAMJ[notice_type]

    task_key = f"{prd_code}|{notice_type}"
    if ENABLE_CHECKPOINT_RESUME and task_key in checkpoints:
        print(f"    ⏭️ [断点续传] 已完成任务，跳过: {task_key}")
        counters["skipped"] += 1
        return "ok"

    print(f"    📂 类型: {notice_type} (SAMJ_TYPE={samj_type})")

    page_no = 1
    total_pages = None
    had_api_fatal = False
    found_this_type = 0
    has_failed_notice = False
    early_stopped = False

    while True:
        payload = {
            "pageNo": page_no,
            "pageSize": API_PAGE_SIZE,
            "keyword": prd_code,
            "isSM": 1,
            "SAMJ_TYPE": samj_type,
            "real_prd_code": prd_code,
        }

        try:
            result = post_api_with_retry(
                session=session,
                browser=browser,
                api_name="BTAFileQry",
                data=payload,
                api_desc=f"BTAFileQry {prd_code} {notice_type} 第{page_no}页",
                raise_on_frequent_exhausted=True,
            )
        except FrequentLimitExhausted as err:
            print(f"      🧊 [类型阻断] {err}")
            return "blocked"

        if not result:
            had_api_fatal = True
            print(f"      ⚠️ 文件列表接口失败，跳过当前类型: {prd_name} | {notice_type}")
            break

        total_size = int(result.get("totalSize") or 0)
        if total_pages is None:
            total_pages = max(1, math.ceil(total_size / API_PAGE_SIZE))
            if total_size == 0:
                print("      ⚪ 本类型暂无公告")

        rows = result.get("list") or []
        found_this_type += len(rows)

        if rows:
            print(f"      📄 第 {page_no}/{total_pages} 页，公告数: {len(rows)}")

        page_succeed = 0
        page_failed = 0
        page_skipped = 0

        for idx, item in enumerate(rows, start=1):
            title = str(item.get("K_INFNAME") or "").strip() or "未命名公告"

            _publish_date = normalize_date(str(item.get("BUSINESS_DATE") or ""))
            _in_range, _too_old = is_in_date_range(_publish_date)
            if not _in_range:
                counters["skipped"] += 1
                page_skipped += 1
                if _too_old:
                    print(f"        ⏭️ [日期跳过] {title} 披露日期 {_publish_date} < {START_DATE}")
                    if EARLY_STOP:
                        print(f"        ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stopped = True
                        break
                else:
                    print(f"        ⏭️ [日期跳过] {title} 披露日期 {_publish_date} > {END_DATE or '今天'}")
                continue

            source_link_preview = build_notice_file_link(result, item)

            if TEST_ONE_NOTICE_MODE and TEST_NOTICE_KEYWORD.strip():
                kw = TEST_NOTICE_KEYWORD.strip().lower()
                if kw not in title.lower() and kw not in source_link_preview.lower():
                    continue

            print(f"        📌 [{idx}/{len(rows)}] {title}")
            counters["found"] += 1

            result_one = process_one_notice(
                session=session,
                product=product,
                notice_type=notice_type,
                result_bta=result,
                item=item,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
            )

            if result_one == "succeed":
                counters["succeed"] += 1
                page_succeed += 1
                if TEST_ONE_NOTICE_MODE and TEST_STOP_AFTER_FIRST_MATCH:
                    save_checkpoint(task_key)
                    checkpoints.add(task_key)
                    return "stop"
            elif result_one in ("skipped", "skip"):
                counters["skipped"] += 1
                page_skipped += 1
            elif result_one == "too_old":
                counters["skipped"] += 1
                page_skipped += 1
                if EARLY_STOP:
                    early_stopped = True
                    break
            else:
                counters["failed"] += 1
                page_failed += 1
                has_failed_notice = True

            random_sleep(REQUEST_INTERVAL_SECONDS)

        if early_stopped:
            break

        if rows and page_succeed == 0 and page_skipped == 0 and page_failed > 0:
            print(f"      ⏳ 本页无成功下载，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
            sleep_with_throttle(PAGE_NO_FILE_WAIT_SECONDS)

        if total_pages is not None and page_no >= total_pages:
            break

        page_no += 1
        random_sleep(PAGE_INTERVAL_SECONDS)

    if not had_api_fatal and not has_failed_notice:
        save_checkpoint(task_key)
        checkpoints.add(task_key)

    if had_api_fatal or has_failed_notice:
        print(f"      🔁 类型未完成，保留下次重试: {notice_type}")
        return "incomplete"

    if found_this_type == 0:
        print(f"      ⚪ 类型结束，无可处理公告: {notice_type}")
    else:
        print(f"      ✅ 类型结束，共扫描公告 {found_this_type} 条")

    return "ok"



def prepare_directories(notice_types: List[str]):
    ensure_dir(DOWNLOAD_ROOT)
    ensure_dir(os.path.dirname(LOG_CSV_PATH))
    for n in notice_types:
        ensure_dir(os.path.join(DOWNLOAD_ROOT, n))



def crawl():
    notice_types = resolve_notice_types_to_run()
    if not notice_types:
        print("⚠️ 未选择任何公告类型，程序结束。")
        return

    prepare_directories(notice_types)

    downloaded_links = load_downloaded_links()
    downloaded_fingerprints = load_downloaded_fingerprints()
    checkpoints = load_checkpoints()

    print(f"📚 已记录下载链接数: {len(downloaded_links)}")
    print(f"🧬 已记录公告指纹数: {len(downloaded_fingerprints)}")
    print(f"🧩 已完成断点任务数: {len(checkpoints)}")
    print(f"🧭 本次公告类型: {', '.join(notice_types)}")

    session = create_session()
    browser = create_browser()

    try:
        # 先 requests 预热，再走浏览器 Cookie 同步，兼顾效率和稳定性
        warmup_session(session)
        warmup_and_sync_cookies(session, browser)

        counters = {
            "found": 0,
            "succeed": 0,
            "failed": 0,
            "skipped": 0,
        }

        stop_early = process_products_stream(
            session=session,
            browser=browser,
            notice_types=notice_types,
            downloaded_links=downloaded_links,
            downloaded_fingerprints=downloaded_fingerprints,
            checkpoints=checkpoints,
            counters=counters,
        )

        if stop_early:
            print("\n🧪 单条测试命中并完成，按配置提前结束。")

        print("\n" + "=" * 80)
        print("🎉 抓取完成")
        print(f"📈 总处理公告数: {counters['found']}")
        print(f"✅ 成功: {counters['succeed']}")
        print(f"❌ 失败: {counters['failed']}")
        print(f"⏭️ 跳过: {counters['skipped']}")
        print(f"📁 下载目录: {os.path.abspath(DOWNLOAD_ROOT)}")
        print(f"🧾 日志文件: {os.path.abspath(LOG_CSV_PATH)}")
        print("=" * 80)
    finally:
        print("🛑 关闭浏览器会话")
        browser.quit()


if __name__ == "__main__":
    crawl()
