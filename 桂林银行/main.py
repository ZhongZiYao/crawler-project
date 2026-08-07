
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

import base64
import csv
import json
import os
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from DrissionPage import ChromiumOptions, ChromiumPage
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =====================================
# 用户配置区
# =====================================

INSTITUTE_NAME = "桂林银行"
NOTICE_TYPE = "产品说明书"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
BOOK_DIR = os.path.join(DOWNLOAD_ROOT, NOTICE_TYPE)

LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
LINKS_JSON = os.path.join(DOWNLOAD_ROOT, "manual_links.json")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_links.txt")
FINGERPRINT_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_fingerprints.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
FAILED_FILE = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")
FAILED_TASKS_FILE = os.path.join(DOWNLOAD_ROOT, "failed_tasks.json")

BASE_URL = "https://www.guilinbank.com.cn"
HOME_URL = f"{BASE_URL}/"
LC_URL = f"{BASE_URL}/page-adapt/index/lc"
API_GET_EBANK = f"{BASE_URL}/portal-home/eBank/getEBank"

# TODO[手动修改]: 接口分页参数
LIST_PAGE_SIZE = 20

# TODO[手动修改]: 运行控制（None 表示全量）
MAX_PAGES = None
LIMIT = None
SHOW_BROWSER = False
RETRY_FAILED_ONLY = False
SKIP_COLLECT = False

# TODO[手动修改]: 请求与下载策略
REQUEST_TIMEOUT = 45
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 2.5

# TODO[手动修改]: 抓取节奏（稳中求稳）
REQUEST_INTERVAL_SECONDS = (0.8, 1.8)
PRODUCT_INTERVAL_SECONDS = (0.8, 1.6)

# TODO[手动修改]: 去重与断点开关
SKIP_DOWNLOADED = True
ENABLE_CHECKPOINT_RESUME = True

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("桂林银行", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("桂林银行", True)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = True
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-09"
# END_DATE   = "2026-06-30"

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
]

BINARY_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z"}


# =====================================
# 基础工具
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
    raw = str(text or "").strip()
    m = re.search(r"(\d{4})[-/.年]?\s*(\d{1,2})[-/.月]?\s*(\d{1,2})", raw)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mm:02d}-{dd:02d}"

    m2 = re.search(r"^(\d{4})(\d{2})(\d{2})$", raw)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"

    return today_str()


def sanitize_text(text: str, max_len: int = 240) -> str:
    val = re.sub(r"[\\/*?:\"<>|]", "_", str(text or ""))
    val = re.sub(r"\s+", " ", val).strip()
    if not val:
        val = "unnamed"
    if max_len > 0:
        val = val[:max_len]
    return val


def _normalize(text: str) -> str:
    return str(text or "").strip().lower()


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


def detect_ext(url: str, content_type: str = "") -> str:
    path = urlparse(url).path.lower()
    for ext in BINARY_EXTS:
        if path.endswith(ext):
            return ext

    ctype = (content_type or "").lower()
    if "pdf" in ctype:
        return ".pdf"
    if "word" in ctype or "msword" in ctype:
        return ".docx"
    if "excel" in ctype or "spreadsheet" in ctype:
        return ".xlsx"
    if "zip" in ctype:
        return ".zip"
    if "html" in ctype:
        return ".html"
    return ""


def build_unique_key(product_name: str, product_code: str, source_link: str, disclose_date: str) -> str:
    return (
        f"{INSTITUTE_NAME}+{NOTICE_TYPE}+{sanitize_text(product_name, 200)}+"
        f"{sanitize_text(product_code, 80)}+{normalize_date(disclose_date)}+"
        f"{sanitize_text(source_link, 320)}"
    )


def build_base_filename(product_name: str, product_code: str, disclose_date: str) -> str:
    date_token = re.sub(r"[^0-9]", "", normalize_date(disclose_date))
    parts = [
        sanitize_text(INSTITUTE_NAME, 60),
        sanitize_text(product_name, 160),
        sanitize_text(NOTICE_TYPE, 60),
        sanitize_text(product_code, 80),
        date_token,
    ]
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


# =====================================
# 日志与状态文件
# =====================================


def log_header() -> List[str]:
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


def write_log_row(
    notice_title: str,
    disclose_date: str,
    status: str,
    source_link: str,
    save_path: str,
    unique_key: str,
):
    row = [
        INSTITUTE_NAME,
        sanitize_text(notice_title, 300),
        NOTICE_TYPE,
        normalize_date(disclose_date),
        now_time_str(),
        status,
        source_link,
        save_path,
        unique_key,
    ]
    exists = os.path.exists(LOG_CSV_PATH)
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(log_header())
        writer.writerow(row)


def write_failed_row(
    reason: str,
    page_no: int,
    item_index: int,
    product_code: str,
    product_name: str,
    source_link: str,
    expected_path: str,
):
    exists = os.path.exists(FAILED_FILE)
    with open(FAILED_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(
                [
                    "time",
                    "reason",
                    "page_no",
                    "item_index",
                    "product_code",
                    "product_name",
                    "source_link",
                    "expected_path",
                ]
            )
        writer.writerow(
            [
                now_time_str(),
                reason,
                page_no,
                item_index,
                product_code,
                sanitize_text(product_name, 220),
                source_link,
                expected_path,
            ]
        )


def read_line_set(file_path: str) -> Set[str]:
    if not os.path.exists(file_path):
        return set()
    with open(file_path, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def append_line(file_path: str, val: str):
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(val.strip() + "\n")


def link_key(link: str) -> str:
    return f"link:{_normalize(link)}" if link else ""


def unique_key_fingerprint(unique_key: str) -> str:
    return f"uk:{_normalize(unique_key)}"


def load_downloaded_links() -> Set[str]:
    items = read_line_set(PROGRESS_FILE)

    if os.path.exists(LOG_CSV_PATH):
        try:
            with open(LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() != "SUCCEED":
                        continue
                    src = str(row.get("来源链接") or "").strip()
                    if src:
                        items.add(_normalize(link_key(src)))
        except Exception:
            pass

    return {_normalize(x) for x in items if x}


def save_downloaded_link(link: str):
    lk = link_key(link)
    if lk:
        append_line(PROGRESS_FILE, lk)


def load_downloaded_fingerprints() -> Set[str]:
    items = read_line_set(FINGERPRINT_FILE)

    if os.path.exists(LOG_CSV_PATH):
        try:
            with open(LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() != "SUCCEED":
                        continue
                    uk = str(row.get("unique_key") or "").strip()
                    if uk:
                        items.add(_normalize(unique_key_fingerprint(uk)))
        except Exception:
            pass

    return {_normalize(x) for x in items if x}


def save_fingerprint(fp: str):
    if fp:
        append_line(FINGERPRINT_FILE, fp)


def load_checkpoint() -> Dict:
    if not ENABLE_CHECKPOINT_RESUME:
        return {}
    if not os.path.exists(CHECKPOINT_FILE):
        return {}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_checkpoint(data: Dict):
    if not ENABLE_CHECKPOINT_RESUME:
        return
    data["updated_at"] = now_time_str()
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHECKPOINT_FILE)


def load_failed_tasks() -> List[Dict]:
    if not os.path.exists(FAILED_TASKS_FILE):
        return []
    try:
        with open(FAILED_TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass
    return []


def save_failed_tasks(tasks: List[Dict]):
    tmp = FAILED_TASKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FAILED_TASKS_FILE)


def failed_task_id(source_link: str, unique_key: str) -> str:
    return _normalize(f"{source_link}|{unique_key}")


def add_or_update_failed_task(task: Dict):
    task_id = task.get("task_id") or failed_task_id(
        str(task.get("source_link") or ""),
        str(task.get("unique_key") or ""),
    )
    task["task_id"] = task_id

    tasks = load_failed_tasks()
    found = False
    for item in tasks:
        if str(item.get("task_id") or "") == task_id:
            item.update(task)
            item["fail_count"] = int(item.get("fail_count") or 0) + 1
            item["updated_at"] = now_time_str()
            found = True
            break

    if not found:
        task["fail_count"] = int(task.get("fail_count") or 0) + 1
        task["updated_at"] = now_time_str()
        tasks.append(task)

    save_failed_tasks(tasks)


def remove_failed_task(source_link: str, unique_key: str):
    task_id = failed_task_id(source_link, unique_key)
    tasks = load_failed_tasks()
    new_tasks = [t for t in tasks if str(t.get("task_id") or "") != task_id]
    if len(new_tasks) != len(tasks):
        save_failed_tasks(new_tasks)


# =====================================
# 浏览器与 Session
# =====================================


def new_browser(headless: bool) -> ChromiumPage:
    co = ChromiumOptions()
    co.auto_port()
    if headless:
        co.headless(True)
    for _dp_attempt in range(3):
        try:
            page = ChromiumPage(co)
            break
        except Exception as _dp_err:
            if _dp_attempt < 2:
                import subprocess as _sp
                _sp.run(['taskkill', '/F', '/IM', 'chrome.exe', '/T'], capture_output=True, timeout=10)
                _sp.run(['taskkill', '/F', '/IM', 'chromium.exe', '/T'], capture_output=True, timeout=10)
                import time as _t
                _t.sleep(3)
            else:
                raise
    try:
        page.set.window.mini()
    except Exception:
        pass
    return page


def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=REQUEST_RETRY,
        read=REQUEST_RETRY,
        connect=REQUEST_RETRY,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json,text/javascript,*/*;q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Origin": BASE_URL,
            "Referer": LC_URL,
        }
    )
    return s


def sync_cookies_from_browser(session: requests.Session, browser: ChromiumPage) -> int:
    cookie_list = browser.cookies() or []
    cookie_map: Dict[str, str] = {}
    for c in cookie_list:
        name = c.get("name")
        value = c.get("value")
        if name:
            cookie_map[name] = value
    if cookie_map:
        session.cookies.update(cookie_map)
    return len(cookie_map)


def warmup_browser_and_session(browser: ChromiumPage, session: requests.Session):
    print("🍪 浏览器预热并抓取真实 Cookie...")
    for url in [HOME_URL, LC_URL]:
        try:
            browser.get(url)
            time.sleep(1.4)
        except Exception as e:
            print(f"   ⚠️ 预热页面失败: {url} | {e}")

    try:
        all_btn = browser.ele("css:#bb", timeout=5)
        if all_btn:
            all_btn.click(by_js=True)
            time.sleep(1.2)
    except Exception:
        pass

    cnt = sync_cookies_from_browser(session, browser)
    print(f"   ✅ Cookie 同步完成: {cnt}")


def refresh_cookies(browser: ChromiumPage, session: requests.Session, reason: str = ""):
    reason_text = f"（{reason}）" if reason else ""
    print(f"🍪 刷新 Cookie {reason_text}")
    try:
        browser.get(LC_URL)
        time.sleep(1.8)
    except Exception:
        pass
    cnt = sync_cookies_from_browser(session, browser)
    session.headers.update({"User-Agent": random.choice(USER_AGENTS), "Referer": LC_URL})
    print(f"   ✅ Cookie 刷新完成: {cnt}")


# =====================================
# 接口与解析
# =====================================


def request_getebank_page(
    session: requests.Session,
    browser: Optional[ChromiumPage],
    page_no: int,
    page_size: int,
) -> Dict:
    payload = {
        "custTyp": "I",
        "prdctTyp": None,
        "pageNum": page_no,
        "pageSize": page_size,
        "prdctCd": None,
        "prdctNme": None,
    }

    last_err = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            random_sleep(REQUEST_INTERVAL_SECONDS)
            resp = session.post(
                API_GET_EBANK,
                data=json.dumps(payload, ensure_ascii=False),
                timeout=REQUEST_TIMEOUT,
                headers={"Content-Type": "application/json; charset=UTF-8"},
            )
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("code")) != "0000":
                raise RuntimeError(f"接口返回异常: code={data.get('code')}, msg={data.get('codeMsg')}")
            return data
        except Exception as e:
            last_err = e
            if attempt < REQUEST_RETRY:
                print(f"   🔁 第{attempt}/{REQUEST_RETRY}次分页请求失败，稍后重试: page={page_no} | {e}")
                time.sleep(RETRY_WAIT_SECONDS)
                if browser is not None:
                    refresh_cookies(browser, session, reason=f"分页{page_no}重试")
            else:
                break

    raise RuntimeError(f"分页请求失败: page={page_no} | {last_err}")


def collect_records_from_api(
    session: requests.Session,
    browser: Optional[ChromiumPage],
    max_pages: Optional[int],
) -> List[Dict[str, str]]:
    print("📚 开始通过 getEBank 接口采集产品说明书链接...")
    records: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()

    first = request_getebank_page(session, browser, page_no=1, page_size=LIST_PAGE_SIZE)
    first_list = first.get("data") or []
    page_info = first.get("page") or {}
    total_count = int(page_info.get("totalCount") or len(first_list) or 0)
    total_pages = max(1, (total_count + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)

    if max_pages is not None:
        total_pages = min(total_pages, max_pages)

    def consume_page(items: List[Dict], page_no: int):
        for item in items:
            product_name = str(item.get("prdctNme") or "").strip()
            product_code = str(item.get("prdctCd") or "").strip()
            show_url = str(item.get("showUrl") or "").strip()
            start_dt = normalize_date(str(item.get("startDt") or ""))
            if not show_url:
                continue
            manual_url = urljoin(BASE_URL, show_url)
            key = (product_code or product_name, manual_url)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "product_name": product_name or product_code or "unknown_product",
                    "product_code": product_code,
                    "manual_url": manual_url,
                    "source_page": str(page_no),
                    "disclose_date": start_dt,
                }
            )

    consume_page(first_list, page_no=1)
    print(f"   ✅ 第1页完成，累计链接 {len(records)}")

    for page_no in range(2, total_pages + 1):
        data = request_getebank_page(session, browser, page_no=page_no, page_size=LIST_PAGE_SIZE)
        consume_page(data.get("data") or [], page_no=page_no)
        print(f"   ✅ 第{page_no}页完成，累计链接 {len(records)}")

    print(f"📦 接口采集结束，共 {len(records)} 条说明书链接")
    return records


# =====================================
# 下载与转换
# =====================================


def is_likely_binary(url: str, content_type: str) -> bool:
    ext = detect_ext(url, content_type)
    return ext in BINARY_EXTS


def save_html_as_pdf(browser: ChromiumPage, url: str, out_pdf: str) -> bool:
    browser.get(url)
    time.sleep(1.6)
    data = browser.run_cdp(
        "Page.printToPDF",
        printBackground=True,
        paperWidth=8.27,
        paperHeight=11.69,
        marginTop=0.39,
        marginBottom=0.39,
        marginLeft=0.39,
        marginRight=0.39,
        preferCSSPageSize=True,
    )
    pdf_b64 = data.get("data", "") if isinstance(data, dict) else ""
    if not pdf_b64:
        return False
    with open(out_pdf, "wb") as f:
        f.write(base64.b64decode(pdf_b64))
    return os.path.getsize(out_pdf) >= 64


def download_binary_with_retry(
    session: requests.Session,
    url: str,
    referer: str,
    save_path: str,
) -> Tuple[bool, str, int, str]:
    last_err = ""
    last_status = 0
    last_ctype = ""

    for attempt in range(1, DOWNLOAD_RETRY + 1):
        try:
            random_sleep(REQUEST_INTERVAL_SECONDS)
            with session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                stream=True,
                allow_redirects=True,
                headers={"Referer": referer, "User-Agent": random.choice(USER_AGENTS)},
            ) as resp:
                last_status = resp.status_code
                last_ctype = resp.headers.get("Content-Type", "")
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")

                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=32768):
                        if chunk:
                            f.write(chunk)

            if os.path.getsize(save_path) < 64:
                raise RuntimeError("下载文件过小")

            return True, last_ctype, last_status, ""
        except Exception as e:
            last_err = str(e)
            if attempt < DOWNLOAD_RETRY:
                print(f"   🔁 文件下载重试 {attempt}/{DOWNLOAD_RETRY}: {url} | {e}")
                time.sleep(RETRY_WAIT_SECONDS)

    return False, last_ctype, last_status, last_err


def should_skip_by_dedup(
    source_link: str,
    unique_key: str,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
) -> bool:
    if not SKIP_DOWNLOADED:
        return False

    lk = _normalize(link_key(source_link)) if source_link else ""
    uk = _normalize(unique_key_fingerprint(unique_key))

    if lk and lk in downloaded_links:
        return True
    if uk and uk in downloaded_fingerprints:
        return True
    return False


def remember_download_success(
    source_link: str,
    final_link: str,
    unique_key: str,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
):
    for link in [source_link, final_link]:
        lk = link_key(link)
        if lk:
            downloaded_links.add(_normalize(lk))
            save_downloaded_link(link)

    uk = unique_key_fingerprint(unique_key)
    downloaded_fingerprints.add(_normalize(uk))
    save_fingerprint(uk)


def write_links_json(records: List[Dict[str, str]]):
    with open(LINKS_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def download_records(
    session: requests.Session,
    browser: ChromiumPage,
    records: List[Dict[str, str]],
    limit: Optional[int],
    retry_failed_only: bool,
):
    target_records = records[: (limit or len(records))]

    if retry_failed_only:
        failed_tasks = load_failed_tasks()
        print(f"🔁 失败重跑模式，待读取失败任务 {len(failed_tasks)} 条")
        by_task = []
        for t in failed_tasks:
            by_task.append(
                {
                    "product_name": str(t.get("product_name") or ""),
                    "product_code": str(t.get("product_code") or ""),
                    "manual_url": str(t.get("source_link") or ""),
                    "source_page": str(t.get("source_page") or "0"),
                    "disclose_date": str(t.get("disclose_date") or today_str()),
                }
            )
        target_records = by_task[: (limit or len(by_task))]

    checkpoint = load_checkpoint()
    start_idx = 0
    if ENABLE_CHECKPOINT_RESUME and not retry_failed_only:
        start_idx = int((checkpoint.get("download") or {}).get("next_index") or 0)
        if start_idx > 0:
            print(f"⏯️ 断点续传生效，从第 {start_idx + 1} 条开始")

    downloaded_links = load_downloaded_links()
    downloaded_fingerprints = load_downloaded_fingerprints()

    stats = {
        "total": len(target_records),
        "ok": 0,
        "fail": 0,
        "skip": 0,
        "binary": 0,
        "html_pdf": 0,
    }

    for idx, rec in enumerate(target_records):
        if idx < start_idx and not retry_failed_only:
            continue

        product_name = str(rec.get("product_name") or "").strip() or "unknown_product"
        product_code = str(rec.get("product_code") or "").strip()
        source_link = str(rec.get("manual_url") or "").strip()
        source_page = int(str(rec.get("source_page") or "0") or 0)
        disclose_date = normalize_date(str(rec.get("disclose_date") or ""))

        _in_range, _too_old = is_in_date_range(disclose_date)
        if not _in_range:
            stats["skip"] += 1
            if _too_old:
                print(f"   ⏭️ [日期跳过] {product_name} 披露日期 {disclose_date} < {START_DATE}")
                if EARLY_STOP:
                    print(f"   ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                    if ENABLE_CHECKPOINT_RESUME and not retry_failed_only:
                        checkpoint["download"] = {"next_index": idx + 1}
                        save_checkpoint(checkpoint)
                    break
            else:
                print(f"   ⏭️ [日期跳过] {product_name} 披露日期 {disclose_date} > {END_DATE or '今天'}")
            if ENABLE_CHECKPOINT_RESUME and not retry_failed_only:
                checkpoint["download"] = {"next_index": idx + 1}
                save_checkpoint(checkpoint)
            continue

        unique_key = build_unique_key(product_name, product_code, source_link, disclose_date)
        base_name = build_base_filename(product_name, product_code, disclose_date)
        expected_pdf_path = os.path.join(BOOK_DIR, f"{base_name}.pdf")

        print(f"\n📄 [{idx + 1}/{len(target_records)}] {product_name}")

        if should_skip_by_dedup(source_link, unique_key, downloaded_links, downloaded_fingerprints):
            stats["skip"] += 1
            print("   ⏭️ 已在去重池中，跳过")
            if ENABLE_CHECKPOINT_RESUME and not retry_failed_only:
                checkpoint["download"] = {"next_index": idx + 1}
                save_checkpoint(checkpoint)
            continue

        status = "FAILED"
        save_path = ""
        final_link = source_link
        err = ""

        try:
            random_sleep(PRODUCT_INTERVAL_SECONDS)

            # 先探测类型，优先文件直下
            head_ct = ""
            try:
                h = session.head(source_link, allow_redirects=True, timeout=REQUEST_TIMEOUT)
                head_ct = h.headers.get("Content-Type", "")
                if h.url:
                    final_link = h.url
            except Exception:
                pass

            ext = detect_ext(final_link, head_ct)

            if is_likely_binary(final_link, head_ct):
                if not ext:
                    ext = ".pdf"
                save_path, _ = build_unique_save_path(BOOK_DIR, base_name, ext)
                ok, ctype, code, dl_err = download_binary_with_retry(
                    session=session,
                    url=final_link,
                    referer=LC_URL,
                    save_path=save_path,
                )
                if not ok:
                    raise RuntimeError(f"binary下载失败: HTTP={code}, CT={ctype}, ERR={dl_err}")

                status = "SUCCEED"
                stats["binary"] += 1
                print(f"   ✅ 文件下载成功: {save_path}")
            else:
                save_path, _ = build_unique_save_path(BOOK_DIR, base_name, ".pdf")
                ok = save_html_as_pdf(browser, final_link, save_path)
                if not ok:
                    raise RuntimeError("富文本转PDF失败")
                status = "SUCCEED"
                stats["html_pdf"] += 1
                print(f"   ✅ HTML 转 PDF 成功: {save_path}")

            write_log_row(
                notice_title=product_name,
                disclose_date=disclose_date,
                status=status,
                source_link=source_link,
                save_path=save_path,
                unique_key=unique_key,
            )

            remember_download_success(
                source_link=source_link,
                final_link=final_link,
                unique_key=unique_key,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
            )
            remove_failed_task(source_link=source_link, unique_key=unique_key)
            stats["ok"] += 1

        except Exception as e:
            err = str(e)
            stats["fail"] += 1
            print(f"   ❌ 下载失败: {err}")

            write_log_row(
                notice_title=product_name,
                disclose_date=disclose_date,
                status="FAILED",
                source_link=source_link,
                save_path=save_path,
                unique_key=unique_key,
            )

            write_failed_row(
                reason=err,
                page_no=source_page,
                item_index=idx + 1,
                product_code=product_code,
                product_name=product_name,
                source_link=source_link,
                expected_path=expected_pdf_path,
            )

            add_or_update_failed_task(
                {
                    "source_page": source_page,
                    "item_index": idx + 1,
                    "product_name": product_name,
                    "product_code": product_code,
                    "source_link": source_link,
                    "disclose_date": disclose_date,
                    "unique_key": unique_key,
                    "last_error": err,
                    "last_try_at": now_time_str(),
                }
            )

        finally:
            if ENABLE_CHECKPOINT_RESUME and not retry_failed_only:
                checkpoint["download"] = {"next_index": idx + 1}
                save_checkpoint(checkpoint)

    return stats


# =====================================
# 主流程
# =====================================


def load_records_from_json() -> List[Dict[str, str]]:
    if not os.path.exists(LINKS_JSON):
        return []
    try:
        with open(LINKS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass
    return []


def main():
    ensure_dir(DOWNLOAD_ROOT)
    ensure_dir(BOOK_DIR)

    print("=" * 66)
    print(f"🚀 {INSTITUTE_NAME}{NOTICE_TYPE} 抓取启动")
    print(f"🕒 启动时间: {now_time_str()}")
    print(f"📁 输出目录: {BOOK_DIR}")
    print("=" * 66)

    records: List[Dict[str, str]] = []

    browser = new_browser(headless=not SHOW_BROWSER)
    session = build_session()

    try:
        warmup_browser_and_session(browser=browser, session=session)

        if RETRY_FAILED_ONLY:
            print("⚙️ 当前为失败重跑模式，将跳过链接采集")
        elif SKIP_COLLECT:
            print("⚙️ 已指定跳过采集，尝试读取历史 manual_links.json")
            records = load_records_from_json()
            print(f"   ✅ 读取历史链接 {len(records)} 条")
        else:
            records = collect_records_from_api(
                session=session,
                browser=browser,
                max_pages=MAX_PAGES,
            )
            write_links_json(records)
            print(f"💾 已保存链接清单: {LINKS_JSON}")

        if not records and not RETRY_FAILED_ONLY:
            print("⚠️ 本次未获得任何可下载记录，任务结束")
            return

        stats = download_records(
            session=session,
            browser=browser,
            records=records,
            limit=LIMIT,
            retry_failed_only=RETRY_FAILED_ONLY,
        )

        print("\n" + "=" * 66)
        print("✅ 任务完成")
        print(f"总任务数: {stats['total']}")
        print(f"成功: {stats['ok']} | 失败: {stats['fail']} | 跳过: {stats['skip']}")
        print(f"文件直下: {stats['binary']} | HTML转PDF: {stats['html_pdf']}")
        print(f"日志文件: {LOG_CSV_PATH}")
        print(f"失败记录: {FAILED_FILE}")
        print(f"失败任务: {FAILED_TASKS_FILE}")
        print(f"链接清单: {LINKS_JSON}")
        print(f"输出目录: {BOOK_DIR}")
        print("=" * 66)

    finally:
        try:
            browser.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
