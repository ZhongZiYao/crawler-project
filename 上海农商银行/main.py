
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
import json
import os
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup
from DrissionPage import ChromiumPage, ChromiumOptions

# =====================================
# 用户配置区
# =====================================

# TODO[手动修改]: 机构标准名称
INSTITUTE_NAME = "上海农商银行"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_links.txt")
FINGERPRINT_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_fingerprints.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
FAILED_FILE = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")
FAILED_TASKS_FILE = os.path.join(DOWNLOAD_ROOT, "failed_tasks.json")

BASE_URL = "https://www.shrcb.com"
HOME_URL = f"{BASE_URL}/shrcb/index/index.html"

LIST_API = (
    f"{BASE_URL}/eportal/ui?portal.url=/portlet/shrcb-personal-financing!list.portlet&moduleId=5"
)
DETAIL_API = (
    f"{BASE_URL}/eportal/ui?portal.url=/portlet/shrcb-personal-financing!detail.portlet&moduleId=5"
)
NOTICE_API = (
    f"{BASE_URL}/eportal/ui?portal.url=/portlet/shrcb-personal-financing!prdannclist.portlet&moduleId=5"
)

# TODO[手动修改]: 是否只跑单个板块，可选: "鑫意理财" / "代销理财" / ""
RUN_ONLY_BOARD = "鑫意理财"

# TODO[手动修改]: 是否只跑单个产品代码，空字符串表示全量
TEST_ONLY_PRODUCT_CODE = ""

# TODO[手动修改]: 单条公告联调
TEST_ONE_NOTICE_MODE = False
TEST_NOTICE_KEYWORD = ""
TEST_STOP_AFTER_FIRST_MATCH = True

# TODO[手动修改]: 请求与下载重试
REQUEST_TIMEOUT = 40
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
PAGE_NO_FILE_WAIT_SECONDS = 10

# TODO[手动修改]: 节奏控制（稳中求稳）
REQUEST_INTERVAL_SECONDS = (1.0, 2.2)
PAGE_INTERVAL_SECONDS = (1.8, 3.4)
PRODUCT_INTERVAL_SECONDS = (1.2, 2.6)

# TODO[手动修改]: 分页参数
LIST_PAGE_SIZE = 15
NOTICE_PAGE_SIZE = 10

# TODO[手动修改]: 断点续传与去重开关
ENABLE_CHECKPOINT_RESUME = True
SKIP_DOWNLOADED = True

# ============ 日期区间配置（集中管理，可本地覆盖）===========
# 从根目录 project_meta.py 集中读取；如需单独调整，取消下方注释
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("上海农商银行", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("上海农商银行", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False
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

# 两大板块：按页面与 taCode 分流
BOARD_CONFIGS = {
    "鑫意理财": {
        "list_page_url": f"{BASE_URL}/shrcb/gryw/tzlc/lc/zylc/cpxx/index.html",
        "list_referer": f"{BASE_URL}/shrcb/gryw/tzlc/lc/zylc/cpxx/index.html",
        "detail_page_prefix": f"{BASE_URL}/shrcb/gryw/tzlc/lc/zylc/cpxx/ckcpxq/index.html?id=",
        "ta_code": "NS",
    },
    "代销理财": {
        "list_page_url": f"{BASE_URL}/shrcb/gryw/tzlc/lc/sylc/cpxx/index.html",
        "list_referer": f"{BASE_URL}/shrcb/gryw/tzlc/lc/sylc/cpxx/index.html",
        "detail_page_prefix": f"{BASE_URL}/shrcb/gryw/tzlc/lc/sylc/cpxx/ckcpxq/index.html?id=",
        "ta_code": "",
    },
}

NOTICE_TYPE_BOOK = "产品说明书"
NOTICE_TYPE_NOTICE = "产品公告"


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


def normalize_date(date_text: str) -> str:
    text = str(date_text or "").strip()
    m = re.search(r"(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", text)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mm:02d}-{dd:02d}"
    return today_str()


def sanitize_text(text: str, max_len: int = 240) -> str:
    text = re.sub(r"[\\/*?:\"<>|]", "_", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if max_len > 0:
        text = text[:max_len]
    return text


def build_unique_key(institute_name: str, notice_type: str, title: str, disclose_date: str) -> str:
    # TODO[手动修改]: 如需调整 unique_key 规则，在此函数统一修改
    return f"{institute_name}+{notice_type}+{sanitize_text(title, 300)}+{disclose_date}"


def build_base_filename(
    institute_name: str,
    product_name: str,
    notice_type: str,
    sales_code: str,
    disclose_date: str,
) -> str:
    # 要求: 机构名+产品名+公告类型+销售代码(可空)+披露日期
    date_token = re.sub(r"[^0-9]", "", disclose_date)
    parts = [
        sanitize_text(institute_name, 80),
        sanitize_text(product_name, 160),
        sanitize_text(notice_type, 60),
        sanitize_text(sales_code, 80),
        date_token,
    ]
    # 销售代码为空时保持空字符串，不写“未命名”。
    filename = "_".join(parts)
    return sanitize_text(filename, 260)


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


def _normalize(text: str) -> str:
    return str(text or "").strip().lower()


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


# =====================================
# 日志与去重
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
    institute_name: str,
    notice_title: str,
    notice_type: str,
    disclose_date: str,
    status: str,
    source_link: str,
    save_path: str,
    unique_key: str,
):
    row = [
        institute_name,
        sanitize_text(notice_title, 300),
        notice_type,
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


def write_failed_row(reason: str, board: str, title: str, source_link: str, expected_path: str):
    exists = os.path.exists(FAILED_FILE)
    with open(FAILED_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["time", "reason", "board", "title", "source_link", "expected_path"])
        writer.writerow([now_time_str(), reason, board, title, source_link, expected_path])


def link_key(link: str) -> str:
    return f"link:{_normalize(link)}" if link else ""


def unique_key_fingerprint(unique_key: str) -> str:
    return f"uk:{_normalize(unique_key)}"


def load_downloaded_links() -> Set[str]:
    items: Set[str] = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                v = _normalize(line)
                if v:
                    items.add(v)

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

    return items


def save_downloaded_link(link: str):
    key = link_key(link)
    if not key:
        return
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def load_downloaded_fingerprints() -> Set[str]:
    items: Set[str] = set()
    if os.path.exists(FINGERPRINT_FILE):
        with open(FINGERPRINT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                v = _normalize(line)
                if v:
                    items.add(v)

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

    return items


def save_fingerprint(fp: str):
    norm = _normalize(fp)
    if not norm:
        return
    with open(FINGERPRINT_FILE, "a", encoding="utf-8") as f:
        f.write(norm + "\n")


# =====================================
# 断点
# =====================================


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
    tmp_path = CHECKPOINT_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CHECKPOINT_FILE)


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
    tmp_path = FAILED_TASKS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, FAILED_TASKS_FILE)


def failed_task_id(board: str, notice_type: str, source_link: str, unique_key: str) -> str:
    return _normalize(f"{board}|{notice_type}|{source_link}|{unique_key}")


def add_or_update_failed_task(task: Dict):
    task_id = task.get("task_id") or failed_task_id(
        str(task.get("board") or ""),
        str(task.get("notice_type") or ""),
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


def remove_failed_task(board: str, notice_type: str, source_link: str, unique_key: str):
    task_id = failed_task_id(board, notice_type, source_link, unique_key)
    tasks = load_failed_tasks()
    new_tasks = [t for t in tasks if str(t.get("task_id") or "") != task_id]
    if len(new_tasks) != len(tasks):
        save_failed_tasks(new_tasks)


def infer_notice_type_from_expected_path(expected_path: str) -> str:
    p = str(expected_path or "")
    if NOTICE_TYPE_BOOK in p:
        return NOTICE_TYPE_BOOK
    return NOTICE_TYPE_NOTICE


def infer_disclose_date_from_expected_path(expected_path: str) -> str:
    base = os.path.basename(str(expected_path or ""))
    m = re.search(r"(\d{8})(?:_\d+)?\.[^.]+$", base)
    if m:
        s = m.group(1)
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return today_str()


def import_legacy_failed_csv_to_tasks(board: str) -> int:
    if not os.path.exists(FAILED_FILE):
        return 0
    count = 0
    try:
        with open(FAILED_FILE, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_board = str(row.get("board") or "").strip()
                if row_board != board:
                    continue
                source_link = str(row.get("source_link") or "").strip()
                title = str(row.get("title") or "").strip() or "失败记录"
                expected_path = str(row.get("expected_path") or "").strip()
                notice_type = infer_notice_type_from_expected_path(expected_path)
                disclose_date = infer_disclose_date_from_expected_path(expected_path)
                unique_key = build_unique_key(INSTITUTE_NAME, notice_type, title, disclose_date)

                add_or_update_failed_task(
                    {
                        "board": board,
                        "product_name": "",
                        "notice_type": notice_type,
                        "sales_code": "",
                        "title": title,
                        "disclose_date": disclose_date,
                        "source_link": source_link,
                        "referer": BOARD_CONFIGS[board]["list_referer"],
                        "unique_key": unique_key,
                        "expected_path": expected_path,
                        "last_error": str(row.get("reason") or "legacy_failed_record"),
                    }
                )
                count += 1
    except Exception:
        return 0
    return count


# =====================================
# 会话与请求
# =====================================


def build_headers(referer: str = "") -> Dict[str, str]:
    return {
        "Accept": "application/json,text/javascript,*/*;q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Origin": BASE_URL,
        "Referer": referer or HOME_URL,
        "User-Agent": random.choice(USER_AGENTS),
        "X-Requested-With": "XMLHttpRequest",
    }


def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(build_headers())
    return s


def create_browser() -> ChromiumPage:
    co = ChromiumOptions()
    co.auto_port()
    # 窗口直接在屏幕外打开，避免弹窗打扰用户
    co.set_argument('--window-position=-32000,-32000')
    for _dp_attempt in range(3):
        try:
            _page = ChromiumPage(co)
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
        _page.set.window.mini()
    except Exception:
        pass
    return _page


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


def warmup_session_and_cookies(session: requests.Session, browser: ChromiumPage):
    print("🍪 预热页面并同步 Cookie...")
    for url in [HOME_URL, BOARD_CONFIGS["鑫意理财"]["list_page_url"], BOARD_CONFIGS["代销理财"]["list_page_url"]]:
        try:
            browser.get(url)
            time.sleep(1.8)
        except Exception as e:
            print(f"   ⚠️ 页面预热失败: {url} | {e}")

    count = sync_cookies_from_browser(session, browser)
    print(f"   ✅ Cookie 同步完成: {count}")


def refresh_session_cookies(session: requests.Session, browser: ChromiumPage, page_url: str, reason: str = ""):
    reason_text = f"（{reason}）" if reason else ""
    print(f"🍪 刷新 Cookie {reason_text}")
    try:
        browser.get(page_url)
        time.sleep(2)
    except Exception:
        pass
    cnt = sync_cookies_from_browser(session, browser)
    session.headers.update({"User-Agent": random.choice(USER_AGENTS)})
    print(f"   ✅ Cookie 刷新完成: {cnt}")


def _decode_response_text(resp: requests.Response) -> str:
    # 某些接口返回的是字节内容，requests.text 可能乱码；统一 UTF-8 解码。
    try:
        return resp.content.decode("utf-8", errors="ignore")
    except Exception:
        return resp.text


def post_json_with_retry(
    session: requests.Session,
    url: str,
    data: Dict,
    referer: str,
    api_desc: str,
    browser: Optional[ChromiumPage] = None,
) -> Optional[Dict]:
    last_err = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            headers = build_headers(referer=referer)
            resp = session.post(url, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            text = _decode_response_text(resp)
            obj = json.loads(text)
            return obj
        except Exception as e:
            last_err = e
            if attempt < REQUEST_RETRY:
                print(f"    🔁 [请求重试] {api_desc} 第{attempt}次失败，{RETRY_WAIT_SECONDS}s 后重试")
                time.sleep(RETRY_WAIT_SECONDS)
                if browser is not None:
                    refresh_session_cookies(session, browser, referer, reason=f"{api_desc}重试")
            else:
                print(f"    ❌ [请求失败] {api_desc} | {last_err}")
    return None


# =====================================
# 文件下载
# =====================================


def guess_extension(response: requests.Response, default_ext: str = ".pdf") -> str:
    content_type = (response.headers.get("Content-Type") or "").lower()
    dispo = response.headers.get("Content-Disposition") or ""

    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", dispo, re.IGNORECASE)
    if m:
        name = m.group(1).strip().strip('"')
        ext = os.path.splitext(name)[1].lower()
        if ext:
            return ext

    if "pdf" in content_type:
        return ".pdf"
    if "msword" in content_type or "wordprocessingml" in content_type:
        return ".docx"
    if "text/html" in content_type:
        return ".html"

    path_ext = os.path.splitext(response.url.split("?")[0])[1].lower()
    if path_ext:
        return path_ext

    return default_ext


def extract_doc_link_from_html(html_text: str, base_url: str) -> str:
    # 先用 BeautifulSoup，避免纯正则漏掉相对路径
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if re.search(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip)(\?|$)", href, re.IGNORECASE):
            if href.startswith("http://") or href.startswith("https://"):
                return href
            return requests.compat.urljoin(base_url, href) # pyright: ignore[reportAttributeAccessIssue]
    return ""


def html_to_word_doc(html_text: str, save_folder: str, base_name: str) -> Tuple[str, str]:
    save_path, file_name = build_unique_save_path(save_folder, base_name, ".doc")
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return save_path, file_name


def download_file_with_fallback(
    session: requests.Session,
    source_link: str,
    referer: str,
    save_folder: str,
    base_name: str,
) -> Tuple[str, str, str]:
    with session.get(
        source_link,
        timeout=REQUEST_TIMEOUT,
        stream=True,
        allow_redirects=True,
        headers={"User-Agent": random.choice(USER_AGENTS), "Referer": referer},
    ) as resp:
        resp.raise_for_status()
        ext = guess_extension(resp)

        if ext == ".html":
            html_text = _decode_response_text(resp)
            doc_link = extract_doc_link_from_html(html_text, resp.url)
            if doc_link:
                with session.get(
                    doc_link,
                    timeout=REQUEST_TIMEOUT,
                    stream=True,
                    allow_redirects=True,
                    headers={"User-Agent": random.choice(USER_AGENTS), "Referer": referer},
                ) as doc_resp:
                    doc_resp.raise_for_status()
                    doc_ext = guess_extension(doc_resp)
                    if doc_ext == ".html":
                        save_path, file_name = html_to_word_doc(_decode_response_text(doc_resp), save_folder, base_name)
                        if os.path.getsize(save_path) < 64:
                            raise RuntimeError("HTML 转 Word 结果过小")
                        return doc_resp.url, save_path, file_name

                    save_path, file_name = build_unique_save_path(save_folder, base_name, doc_ext)
                    with open(save_path, "wb") as f:
                        for chunk in doc_resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    if os.path.getsize(save_path) < 64:
                        raise RuntimeError("下载文件过小")
                    return doc_resp.url, save_path, file_name

            # 找不到可下载文档时，按规则转 Word 保存
            save_path, file_name = html_to_word_doc(html_text, save_folder, base_name)
            if os.path.getsize(save_path) < 64:
                raise RuntimeError("HTML 转 Word 结果过小")
            return resp.url, save_path, file_name

        save_path, file_name = build_unique_save_path(save_folder, base_name, ext)
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        if os.path.getsize(save_path) < 64:
            raise RuntimeError("下载文件过小")
        return resp.url, save_path, file_name


# =====================================
# 业务抓取
# =====================================


def list_boards_to_run() -> List[str]:
    if RUN_ONLY_BOARD:
        if RUN_ONLY_BOARD in BOARD_CONFIGS:
            return [RUN_ONLY_BOARD]
        print(f"⚠️ RUN_ONLY_BOARD 配置无效: {RUN_ONLY_BOARD}，将改为全量")
    return list(BOARD_CONFIGS.keys())


def board_subfolders(board: str) -> Dict[str, str]:
    board_root = os.path.join(DOWNLOAD_ROOT, board)
    folder_book = os.path.join(board_root, NOTICE_TYPE_BOOK)
    folder_notice = os.path.join(board_root, NOTICE_TYPE_NOTICE)
    ensure_dir(folder_book)
    ensure_dir(folder_notice)
    return {
        NOTICE_TYPE_BOOK: folder_book,
        NOTICE_TYPE_NOTICE: folder_notice,
    }


def build_list_payload(ta_code: str, page_no: int) -> Dict:
    payload = {
        "prdName": "",
        "orderBy": "0",
        "order": "",
        "taCode": ta_code,
        "type": "",
        "buyStatus": "",
        "savestartsum": "",
        "riskLevel": "",
        "timelimit": "",
        "page.pageNo": str(page_no),
        # TODO[手动修改]: 如页面后续要求固定 pageSize，可放开此参数
        # "page.pageSize": str(LIST_PAGE_SIZE),
    }
    return payload


def fetch_product_list_page(
    session: requests.Session,
    browser: ChromiumPage,
    board: str,
    page_no: int,
) -> Optional[Dict]:
    cfg = BOARD_CONFIGS[board]
    payload = build_list_payload(cfg["ta_code"], page_no)
    return post_json_with_retry(
        session=session,
        url=LIST_API,
        data=payload,
        referer=cfg["list_referer"],
        api_desc=f"{board} 列表第{page_no}页",
        browser=browser,
    )


def fetch_product_detail(
    session: requests.Session,
    browser: ChromiumPage,
    detail_page_url: str,
    product_id: str,
) -> Optional[Dict]:
    resp = post_json_with_retry(
        session=session,
        url=DETAIL_API,
        data={"id": product_id},
        referer=detail_page_url,
        api_desc=f"详情接口 id={product_id}",
        browser=browser,
    )
    if not resp:
        return None
    return resp.get("data") or {}


def fetch_notice_page(
    session: requests.Session,
    browser: ChromiumPage,
    detail_page_url: str,
    product_code: str,
    page_no: int,
) -> Optional[Dict]:
    resp = post_json_with_retry(
        session=session,
        url=NOTICE_API,
        data={
            "productCode": product_code,
            "currentPage": str(page_no),
            "queryNum": str(NOTICE_PAGE_SIZE),
        },
        referer=detail_page_url,
        api_desc=f"公告接口 {product_code} 第{page_no}页",
        browser=browser,
    )
    if not resp:
        return None
    return ((resp.get("data") or {}).get("msp_response_body") or {})


def resolve_book_url(detail_obj: Dict) -> str:
    prd_code = str(detail_obj.get("prdCode") or "").strip()
    ta_code = str(detail_obj.get("taCode") or "").strip()
    if not prd_code:
        return ""

    # 站点脚本规则: taCode == NS 使用 licai；否则 finance
    if ta_code == "NS":
        return f"https://msp.srcb.com/licai/{prd_code}.pdf"
    return f"https://msp.srcb.com/finance/{prd_code}lccpsms.pdf"


def resolve_book_disclose_date(detail_obj: Dict) -> str:
    # TODO[手动修改]: 如后续明确说明书披露字段，优先替换本函数字段优先级
    candidates = [
        detail_obj.get("updateTime"),
        detail_obj.get("updateDate"),
        detail_obj.get("createTime"),
        detail_obj.get("prdStartDate"),
        detail_obj.get("navDate"),
    ]
    for c in candidates:
        if str(c or "").strip():
            return normalize_date(str(c))
    return today_str()


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


def download_notice_item(
    session: requests.Session,
    board: str,
    product_name: str,
    notice_type: str,
    sales_code: str,
    title: str,
    disclose_date: str,
    source_link: str,
    referer: str,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    record_failed_task: bool = True,
) -> str: # pyright: ignore[reportReturnType]
    disclose_date = normalize_date(disclose_date)
    _in_range, _too_old = is_in_date_range(disclose_date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"   ⏭️ [日期跳过] 披露日期 {disclose_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return "too_old" if _too_old else "skip"

    folders = board_subfolders(board)
    save_folder = folders[notice_type]

    base_name = build_base_filename(
        institute_name=INSTITUTE_NAME,
        product_name=product_name,
        notice_type=notice_type,
        sales_code=sales_code,
        disclose_date=disclose_date,
    )
    unique_key = build_unique_key(INSTITUTE_NAME, notice_type, title, disclose_date)

    task_id_source = source_link

    if should_skip_by_dedup(source_link, unique_key, downloaded_links, downloaded_fingerprints):
        print(f"      ⏭️ [跳过] 已下载: {title}")
        return "skipped"

    if not source_link:
        expected_path, _ = build_unique_save_path(save_folder, base_name, ".pdf")
        write_log_row(
            institute_name=INSTITUTE_NAME,
            notice_title=title,
            notice_type=notice_type,
            disclose_date=disclose_date,
            status="FAILED",
            source_link="",
            save_path=os.path.abspath(expected_path),
            unique_key=unique_key,
        )
        write_failed_row("empty_source_link", board, title, "", os.path.abspath(expected_path))
        if record_failed_task:
            add_or_update_failed_task(
                {
                    "board": board,
                    "product_name": product_name,
                    "notice_type": notice_type,
                    "sales_code": sales_code,
                    "title": title,
                    "disclose_date": disclose_date,
                    "source_link": "",
                    "referer": referer,
                    "unique_key": unique_key,
                    "expected_path": os.path.abspath(expected_path),
                    "last_error": "empty_source_link",
                }
            )
        print(f"      ❌ [失败] 缺少下载链接: {title}")
        return "failed"

    expected_path, _ = build_unique_save_path(save_folder, base_name, ".pdf")

    last_err = None
    for attempt in range(1, DOWNLOAD_RETRY + 1):
        try:
            final_link, save_path, file_name = download_file_with_fallback(
                session=session,
                source_link=source_link,
                referer=referer,
                save_folder=save_folder,
                base_name=base_name,
            )

            remember_download_success(
                source_link=source_link,
                final_link=final_link,
                unique_key=unique_key,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
            )

            write_log_row(
                institute_name=INSTITUTE_NAME,
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="SUCCEED",
                source_link=final_link or source_link,
                save_path=os.path.abspath(save_path),
                unique_key=unique_key,
            )
            remove_failed_task(board, notice_type, task_id_source, unique_key)
            print(f"      ✅ [成功] {file_name}")
            return "succeed"
        except Exception as e:
            last_err = e
            if attempt < DOWNLOAD_RETRY:
                print(f"      🔁 [下载重试] {title} 第{attempt}次失败，{RETRY_WAIT_SECONDS}s 后重试")
                time.sleep(RETRY_WAIT_SECONDS)
            else:
                write_log_row(
                    institute_name=INSTITUTE_NAME,
                    notice_title=title,
                    notice_type=notice_type,
                    disclose_date=disclose_date,
                    status="FAILED",
                    source_link=source_link,
                    save_path=os.path.abspath(expected_path),
                    unique_key=unique_key,
                )
                write_failed_row("download_failed", board, title, source_link, os.path.abspath(expected_path))
                if record_failed_task:
                    add_or_update_failed_task(
                        {
                            "board": board,
                            "product_name": product_name,
                            "notice_type": notice_type,
                            "sales_code": sales_code,
                            "title": title,
                            "disclose_date": disclose_date,
                            "source_link": source_link,
                            "referer": referer,
                            "unique_key": unique_key,
                            "expected_path": os.path.abspath(expected_path),
                            "last_error": str(last_err),
                        }
                    )
                print(f"      ❌ [失败] {title} | {last_err}")
                return "failed"


# =====================================
# 抓取主流程
# =====================================


def process_product(
    session: requests.Session,
    browser: ChromiumPage,
    board: str,
    product_row: Dict,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    counters: Dict[str, int],
) -> Tuple[bool, bool]:
    product_id = str(product_row.get("id") or "").strip()
    product_name = str(product_row.get("prdName") or "").strip() or ""
    product_code = str(product_row.get("prdCode") or "").strip() or ""

    if TEST_ONLY_PRODUCT_CODE.strip() and product_code != TEST_ONLY_PRODUCT_CODE.strip():
        return False, False

    detail_page = BOARD_CONFIGS[board]["detail_page_prefix"] + product_id

    print(f"    🧾 产品: {product_name} | {product_code}")

    detail_obj = fetch_product_detail(
        session=session,
        browser=browser,
        detail_page_url=detail_page,
        product_id=product_id,
    )
    if not detail_obj:
        counters["failed"] += 1
        write_failed_row("detail_api_failed", board, product_name or product_code, detail_page, "")
        return False, False

    prd_code = str(detail_obj.get("prdCode") or product_code).strip()
    prd_name = str(detail_obj.get("prdName") or product_name).strip() or product_name

    # 1) 产品说明书
    book_url = resolve_book_url(detail_obj)
    book_date = resolve_book_disclose_date(detail_obj)
    book_title = f"{prd_name} 产品说明书"

    counters["found"] += 1
    r1 = download_notice_item(
        session=session,
        board=board,
        product_name=prd_name,
        notice_type=NOTICE_TYPE_BOOK,
        sales_code=prd_code,
        title=book_title,
        disclose_date=book_date,
        source_link=book_url,
        referer=detail_page,
        downloaded_links=downloaded_links,
        downloaded_fingerprints=downloaded_fingerprints,
    )
    if r1 == "succeed":
        counters["succeed"] += 1
    elif r1 == "skipped":
        counters["skipped"] += 1
    else:
        counters["failed"] += 1

    random_sleep(REQUEST_INTERVAL_SECONDS)

    # 2) 产品公告
    page_no = 1
    notice_total = None
    while True:
        body = fetch_notice_page(
            session=session,
            browser=browser,
            detail_page_url=detail_page,
            product_code=prd_code,
            page_no=page_no,
        )

        if body is None:
            # 接口失败不中断产品流程
            counters["failed"] += 1
            write_failed_row("notice_api_failed", board, prd_name, detail_page, "")
            break

        rows = body.get("prodAnnc_list") or []
        total_num = int(body.get("total_num") or 0)
        if notice_total is None:
            notice_total = total_num
            total_pages = max(1, (total_num + NOTICE_PAGE_SIZE - 1) // NOTICE_PAGE_SIZE)

        if not rows:
            # 要求: 翻页后找不到 PDF 也等 10s 左右再继续尝试
            if page_no <= max(1, (notice_total + NOTICE_PAGE_SIZE - 1) // NOTICE_PAGE_SIZE):
                print(f"      ⏳ 公告页空列表，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
                time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
            break

        print(f"      📄 公告页 {page_no}，数量 {len(rows)}")

        for idx, item in enumerate(rows, start=1):
            ann_title = str(item.get("annc_title") or item.get("file_name") or "").strip() or f"公告_{idx}"
            ann_link = str(item.get("annc_url") or "").strip()
            ann_date = normalize_date(str(item.get("publish_time") or ""))

            _in_range, _too_old = is_in_date_range(ann_date)
            if not _in_range:
                counters["skipped"] += 1
                if _too_old:
                    print(f"      ⏭️ [日期跳过] {ann_title} 披露日期 {ann_date} < {START_DATE}")
                    if EARLY_STOP:
                        print(f"      ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        break
                else:
                    print(f"      ⏭️ [日期跳过] {ann_title} 披露日期 {ann_date} > {END_DATE or '今天'}")
                continue

            # 公告内销售代码优先取 prod_info
            sales_code = prd_code
            prod_info = item.get("prod_info") or []
            if isinstance(prod_info, list) and prod_info:
                sales_code = str((prod_info[0] or {}).get("prod_code") or prd_code).strip()

            if TEST_ONE_NOTICE_MODE and TEST_NOTICE_KEYWORD.strip():
                kw = TEST_NOTICE_KEYWORD.strip().lower()
                if kw not in ann_title.lower() and kw not in ann_link.lower():
                    continue

            print(f"        📌 [{idx}/{len(rows)}] {ann_title}")
            counters["found"] += 1

            r2 = download_notice_item(
                session=session,
                board=board,
                product_name=prd_name,
                notice_type=NOTICE_TYPE_NOTICE,
                sales_code=sales_code,
                title=ann_title,
                disclose_date=ann_date,
                source_link=ann_link,
                referer=detail_page,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
            )

            if r2 == "succeed":
                counters["succeed"] += 1
                if TEST_ONE_NOTICE_MODE and TEST_STOP_AFTER_FIRST_MATCH:
                    return True, True
            elif r2 in ("skipped", "skip", "too_old"):
                counters["skipped"] += 1
            else:
                counters["failed"] += 1

            random_sleep(REQUEST_INTERVAL_SECONDS)

        if page_no >= max(1, (notice_total + NOTICE_PAGE_SIZE - 1) // NOTICE_PAGE_SIZE):
            break

        page_no += 1
        random_sleep(PAGE_INTERVAL_SECONDS)

    random_sleep(PRODUCT_INTERVAL_SECONDS)
    return False, True


def update_product_checkpoint(
    checkpoint: Dict,
    board: str,
    page_no: int,
    product_id: str,
    product_name: str,
    product_code: str,
):
    if not ENABLE_CHECKPOINT_RESUME:
        return
    board_cp = checkpoint.setdefault("boards", {}).setdefault(board, {})
    board_cp["next_page"] = page_no
    board_cp["resume_product_id"] = product_id
    board_cp["last_success_product_id"] = product_id
    board_cp["last_success_product_name"] = product_name
    board_cp["last_success_product_code"] = product_code
    board_cp["last_success_page"] = page_no
    checkpoint["updated_at"] = now_time_str()
    save_checkpoint(checkpoint)


def update_page_checkpoint(checkpoint: Dict, board: str, next_page: int):
    if not ENABLE_CHECKPOINT_RESUME:
        return
    board_cp = checkpoint.setdefault("boards", {}).setdefault(board, {})
    board_cp["next_page"] = next_page
    board_cp["resume_product_id"] = ""
    checkpoint["updated_at"] = now_time_str()
    save_checkpoint(checkpoint)


def retry_failed_tasks_for_board(
    session: requests.Session,
    board: str,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    counters: Dict[str, int],
):
    tasks = load_failed_tasks()
    board_tasks = [t for t in tasks if str(t.get("board") or "").strip() == board]

    if not board_tasks:
        imported = import_legacy_failed_csv_to_tasks(board)
        if imported > 0:
            tasks = load_failed_tasks()
            board_tasks = [t for t in tasks if str(t.get("board") or "").strip() == board]

    if not board_tasks:
        return

    other_tasks = [t for t in tasks if str(t.get("board") or "").strip() != board]
    save_failed_tasks(other_tasks)

    print(f"🔁 {board} 失败任务优先重试: {len(board_tasks)} 条")
    for idx, task in enumerate(board_tasks, start=1):
        title = str(task.get("title") or "失败任务").strip()
        notice_type = str(task.get("notice_type") or "").strip() or NOTICE_TYPE_NOTICE
        disclose_date = normalize_date(str(task.get("disclose_date") or ""))
        print(f"   ↩️ [{idx}/{len(board_tasks)}] {title}")

        counters["found"] += 1
        result = download_notice_item(
            session=session,
            board=board,
            product_name=str(task.get("product_name") or ""),
            notice_type=notice_type,
            sales_code=str(task.get("sales_code") or ""),
            title=title,
            disclose_date=disclose_date,
            source_link=str(task.get("source_link") or "").strip(),
            referer=str(task.get("referer") or BOARD_CONFIGS[board]["list_referer"]),
            downloaded_links=downloaded_links,
            downloaded_fingerprints=downloaded_fingerprints,
            record_failed_task=True,
        )
        if result == "succeed":
            counters["succeed"] += 1
        elif result == "skipped":
            counters["skipped"] += 1
        else:
            counters["failed"] += 1

        random_sleep(REQUEST_INTERVAL_SECONDS)


def crawl_board(
    session: requests.Session,
    browser: ChromiumPage,
    board: str,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    checkpoint: Dict,
    counters: Dict[str, int],
) -> bool:
    cfg = BOARD_CONFIGS[board]
    board_cp = (checkpoint.get("boards") or {}).get(board, {}) if ENABLE_CHECKPOINT_RESUME else {}
    start_page = max(1, int(board_cp.get("next_page") or 1))
    resume_product_id = str(board_cp.get("resume_product_id") or "").strip()

    print("\n" + "=" * 90)
    print(f"🚀 开始板块: {board} | 起始页: {start_page}")
    if resume_product_id:
        print(f"🎯 产品断点: {resume_product_id}")
    print("=" * 90)

    retry_failed_tasks_for_board(
        session=session,
        board=board,
        downloaded_links=downloaded_links,
        downloaded_fingerprints=downloaded_fingerprints,
        counters=counters,
    )

    # 访问板块列表页，尽量拿到真实 Cookie
    try:
        browser.get(cfg["list_page_url"])
        time.sleep(1.5)
        sync_cookies_from_browser(session, browser)
    except Exception:
        pass

    first = fetch_product_list_page(session, browser, board, start_page)
    if not first:
        print(f"❌ 板块首页获取失败: {board}")
        return False

    total_pages = int(first.get("totalPages") or 1)
    total_count = int(first.get("totalCount") or 0)
    print(f"📚 {board} 产品总数: {total_count}，总页数: {total_pages}")

    for page_no in range(start_page, total_pages + 1):
        if page_no == start_page:
            page_obj = first
        else:
            page_obj = fetch_product_list_page(session, browser, board, page_no)
            if not page_obj:
                print(f"   ⚠️ 列表第{page_no}页失败，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
                time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
                continue

        rows = page_obj.get("result") or []
        print(f"\n📄 {board} 第 {page_no}/{total_pages} 页，产品数: {len(rows)}")

        skip_until_resume_hit = bool(resume_product_id and page_no == start_page)

        for product in rows:
            current_pid = str(product.get("id") or "").strip()
            if skip_until_resume_hit:
                if current_pid != resume_product_id:
                    continue
                print(f"    📍 命中产品断点: {current_pid}，从此产品继续")
                skip_until_resume_hit = False

            stop_early, product_ok = process_product(
                session=session,
                browser=browser,
                board=board,
                product_row=product,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
                counters=counters,
            )

            if product_ok and ENABLE_CHECKPOINT_RESUME:
                update_product_checkpoint(
                    checkpoint=checkpoint,
                    board=board,
                    page_no=page_no,
                    product_id=current_pid,
                    product_name=str(product.get("prdName") or "").strip(),
                    product_code=str(product.get("prdCode") or "").strip(),
                )

            if stop_early:
                # 单条联调模式命中后，记录当前位置并退出
                if ENABLE_CHECKPOINT_RESUME:
                    update_page_checkpoint(checkpoint, board, page_no)
                return True

        # 每页成功后刷新断点到下一页
        if ENABLE_CHECKPOINT_RESUME:
            update_page_checkpoint(checkpoint, board, page_no + 1)

        random_sleep(PAGE_INTERVAL_SECONDS)

    # 板块完成后把断点重置为 1，便于下一轮全量巡检
    if ENABLE_CHECKPOINT_RESUME:
        update_page_checkpoint(checkpoint, board, 1)

    return False


def prepare_dirs():
    ensure_dir(DOWNLOAD_ROOT)
    ensure_dir(os.path.dirname(LOG_CSV_PATH))
    for board in BOARD_CONFIGS:
        board_subfolders(board)


# =====================================
# 入口
# =====================================


def crawl():
    prepare_dirs()

    boards = list_boards_to_run()
    downloaded_links = load_downloaded_links()
    downloaded_fingerprints = load_downloaded_fingerprints()
    checkpoint = load_checkpoint()

    print(f"📚 已记录来源链接去重数: {len(downloaded_links)}")
    print(f"🧬 已记录指纹去重数: {len(downloaded_fingerprints)}")
    print(f"🧭 本次板块: {', '.join(boards)}")

    session = create_session()
    browser = create_browser()

    counters = {"found": 0, "succeed": 0, "failed": 0, "skipped": 0}

    try:
        warmup_session_and_cookies(session, browser)

        stop_early = False
        for board in boards:
            stop_early = crawl_board(
                session=session,
                browser=browser,
                board=board,
                downloaded_links=downloaded_links,
                downloaded_fingerprints=downloaded_fingerprints,
                checkpoint=checkpoint,
                counters=counters,
            )
            if stop_early:
                break

        if stop_early:
            print("\n🧪 单条联调命中，按配置提前结束。")

        print("\n" + "=" * 90)
        print("🎉 抓取结束")
        print(f"📈 扫描条目: {counters['found']}")
        print(f"✅ 成功: {counters['succeed']}")
        print(f"❌ 失败: {counters['failed']}")
        print(f"⏭️ 跳过: {counters['skipped']}")
        print(f"📁 下载目录: {os.path.abspath(DOWNLOAD_ROOT)}")
        print(f"🧾 日志文件: {os.path.abspath(LOG_CSV_PATH)}")
        print(f"🧯 失败清单: {os.path.abspath(FAILED_FILE)}")
        print("=" * 90)
    finally:
        print("🛑 关闭浏览器会话")
        browser.quit()


if __name__ == "__main__":
    crawl()
