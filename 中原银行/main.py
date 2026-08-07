
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
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
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

# 中原银行站点证书链不完整（服务端未下发中间证书），certifi 严格校验会失败，
# 与浏览器行为对齐：关闭证书校验并静默告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =====================================
# 用户配置区
# =====================================

# TODO[手动修改]: 机构标准名称
INSTITUTE_NAME = "中原银行"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
BOOK_FOLDER = os.path.join(DOWNLOAD_ROOT, "产品说明书")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_links.txt")
FINGERPRINT_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_fingerprints.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
FAILED_FILE = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")
FAILED_TASKS_FILE = os.path.join(DOWNLOAD_ROOT, "failed_tasks.json")

BASE_URL = "https://www.zybank.com.cn"
HOME_URL = f"{BASE_URL}/zybank/index/index.html"
LIST_PAGE_URL = f"{BASE_URL}/zybank/grjr48/ydlc/lccp6/lccpsjlb43/index.html"
LIST_QUERY_URL = (
    f"{BASE_URL}/eportal/ui?pageId=438185&currentPage={{page}}&moduleId=ddb3e7f1249c4bd7a18f85afef985ed0"
)
DETAIL_URL_TEMPLATE = f"{BASE_URL}/eportal/ui?pageId=438107&articleKey={{article_key}}&columnId=438185"
DOWNLOAD_BASE = "https://download.zybank.com.cn/dzkhb/cfxz/ifm/ifm008/bta/"

NOTICE_TYPE_BOOK = "产品说明书"

# TODO[手动修改]: 按产品类型过滤，可选: "" / "自营" / "代销"
PRODUCT_TYPE_FILTER = "自营"

# TODO[手动修改]: 联调模式，仅抓单个产品 articleKey（空字符串为全量）
TEST_ONLY_ARTICLE_KEY = ""

# TODO[手动修改]: 重试与等待
REQUEST_TIMEOUT = 45
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
PAGE_NO_FILE_WAIT_SECONDS = 10

# TODO[手动修改]: 节奏控制（稳中求稳）
REQUEST_INTERVAL_SECONDS = (1.0, 2.0)
PAGE_INTERVAL_SECONDS = (1.6, 3.2)
PRODUCT_INTERVAL_SECONDS = (1.0, 2.4)

# TODO[手动修改]: 去重与断点开关
ENABLE_CHECKPOINT_RESUME = True
SKIP_DOWNLOADED = True

# ============ 日期区间配置（集中管理，可本地覆盖）===========
# 从根目录 project_meta.py 集中读取；如需单独调整，取消下方注释
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("中原银行", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("中原银行", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False
# 本地覆盖示例（取消注释即生效）：
START_DATE = "2026-07-01"
END_DATE   = "2026-08-07"

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
    m = re.search(r"(\d{4})[-/.年]?\s*(\d{1,2})[-/.月]?\s*(\d{1,2})", text)
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


def write_failed_row(reason: str, title: str, source_link: str, expected_path: str):
    exists = os.path.exists(FAILED_FILE)
    with open(FAILED_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["time", "reason", "title", "source_link", "expected_path"])
        writer.writerow([now_time_str(), reason, title, source_link, expected_path])


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
# 断点与失败任务
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
# 会话与请求
# =====================================


def build_headers(referer: str = "") -> Dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer or HOME_URL,
        "User-Agent": random.choice(USER_AGENTS),
        "Connection": "keep-alive",
    }


def create_session() -> requests.Session:
    s = requests.Session()
    s.verify = False  # 站点证书链不完整，关闭校验（见文件顶部说明）
    s.headers.update(build_headers())
    return s


def create_browser() -> ChromiumPage:
    _page = _new_page()
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
    for url in [HOME_URL, LIST_PAGE_URL]:
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


def request_text_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    referer: str,
    req_desc: str,
    browser: Optional[ChromiumPage] = None,
    data: Optional[Dict] = None,
) -> Optional[str]:
    last_err = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            headers = build_headers(referer=referer)
            if method.upper() == "POST":
                resp = session.post(url, data=data or {}, headers=headers, timeout=REQUEST_TIMEOUT)
            else:
                resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.content.decode("utf-8", errors="ignore")
        except Exception as e:
            last_err = e
            if attempt < REQUEST_RETRY:
                print(f"    🔁 [请求重试] {req_desc} 第{attempt}次失败，{RETRY_WAIT_SECONDS}s 后重试")
                time.sleep(RETRY_WAIT_SECONDS)
                if browser is not None:
                    refresh_session_cookies(session, browser, referer, reason=f"{req_desc}重试")
            else:
                print(f"    ❌ [请求失败] {req_desc} | {last_err}")
    return None


# =====================================
# HTML 解析
# =====================================


def parse_total_pages(html: str) -> int:
    m = re.search(r'totalpage="(\d+)"', html)
    if m:
        return max(1, int(m.group(1)))
    m = re.search(r"/(\d+)页", html)
    if m:
        return max(1, int(m.group(1)))
    return 1


def normalize_detail_href(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("?"):
        return f"{BASE_URL}/eportal/ui{href}"
    return urljoin(BASE_URL, href)


def extract_article_key(detail_url: str) -> str:
    try:
        q = parse_qs(urlparse(detail_url).query)
        return (q.get("articleKey") or [""])[0].strip()
    except Exception:
        return ""


def parse_product_rows_from_list_html(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    ul = soup.select_one("ul.search-list")
    if not ul:
        return []

    rows: List[Dict] = []
    seen_keys: Set[str] = set()

    for li in ul.select("li"):
        cls = " ".join(li.get("class") or [])
        if "title-top" in cls or "no-data" in cls:
            continue

        a = li.select_one('a[href*="pageId=438107"][href*="articleKey="]')
        if not a:
            continue

        href = normalize_detail_href(str(a.get("href") or ""))
        article_key = extract_article_key(href)
        if not article_key or article_key in seen_keys:
            continue
        seen_keys.add(article_key)

        product_name = str(a.get("title") or a.get_text(" ", strip=True) or "").strip()
        spans = li.select("span")
        product_type = ""
        if len(spans) >= 2:
            txt = spans[1].get_text(" ", strip=True)
            product_type = txt.replace("类型:", "").strip()

        rows.append(
            {
                "article_key": article_key,
                "detail_url": DETAIL_URL_TEMPLATE.format(article_key=article_key),
                "product_name": product_name,
                "product_type": product_type,
            }
        )

    return rows


def parse_detail_codes_and_date(html: str) -> Dict[str, str]:
    codesqian = ""
    codeshou = ""
    create_date = ""

    m1 = re.search(r"var\s+codesqian\s*=\s*'([^']*)'", html)
    if m1:
        codesqian = m1.group(1).strip()

    m2 = re.search(r"var\s+codeshou\s*=\s*'([^']*)'", html)
    if m2:
        codeshou = m2.group(1).strip()

    # 页面中常见: <meta name="createDate" content="2026-03-29 17:22:28"/>
    m3 = re.search(r'<meta\s+name="createDate"\s+content="([^"]+)"', html)
    if m3:
        create_date = normalize_date(m3.group(1))

    return {
        "codesqian": codesqian,
        "codeshou": codeshou,
        "disclose_date": create_date or today_str(),
    }


def build_book_candidates(codesqian: str, codeshou: str) -> List[Tuple[str, str]]:
    # 只抓“产品说明书相关”文件：
    # 1) 风险揭示及产品说明书（combined）
    # 2) 产品说明书（chanpinshuomingshu）
    candidates: List[Tuple[str, str]] = []

    if codeshou:
        candidates.append(("风险揭示及产品说明书", f"{DOWNLOAD_BASE}{codeshou}.pdf"))

    if codesqian:
        candidates.append(("产品说明书", f"{DOWNLOAD_BASE}{codesqian}chanpinshuomingshu.pdf"))

    return candidates


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
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if re.search(r"\.(pdf|doc|docx)(\?|$)", href, re.IGNORECASE):
            if href.startswith("http://") or href.startswith("https://"):
                return href
            return requests.compat.urljoin(base_url, href)
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
            html_text = resp.content.decode("utf-8", errors="ignore")
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
                        save_path, file_name = html_to_word_doc(
                            doc_resp.content.decode("utf-8", errors="ignore"),
                            save_folder,
                            base_name,
                        )
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

            # 如无附件链接，按规则转 Word 保存
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
# 去重与单条下载
# =====================================


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


def download_one_book(
    session: requests.Session,
    product_name: str,
    sales_code: str,
    announce_title: str,
    disclose_date: str,
    source_link: str,
    referer: str,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    record_failed_task: bool = True,
) -> str:
    disclose_date = normalize_date(disclose_date)
    _in_range, _too_old = is_in_date_range(disclose_date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"   ⏭️ [日期跳过] 披露日期 {disclose_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {announce_title}")
        return "too_old" if _too_old else "skip"

    base_name = build_base_filename(
        institute_name=INSTITUTE_NAME,
        product_name=product_name,
        notice_type=NOTICE_TYPE_BOOK,
        sales_code=sales_code,
        disclose_date=disclose_date,
    )
    unique_key = build_unique_key(INSTITUTE_NAME, NOTICE_TYPE_BOOK, announce_title, disclose_date)

    if should_skip_by_dedup(source_link, unique_key, downloaded_links, downloaded_fingerprints):
        print(f"      ⏭️ [跳过] 已下载: {announce_title}")
        return "skipped"

    expected_path, _ = build_unique_save_path(BOOK_FOLDER, base_name, ".pdf")

    last_err = None
    for attempt in range(1, DOWNLOAD_RETRY + 1):
        try:
            final_link, save_path, file_name = download_file_with_fallback(
                session=session,
                source_link=source_link,
                referer=referer,
                save_folder=BOOK_FOLDER,
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
                notice_title=announce_title,
                notice_type=NOTICE_TYPE_BOOK,
                disclose_date=disclose_date,
                status="SUCCEED",
                source_link=final_link or source_link,
                save_path=os.path.abspath(save_path),
                unique_key=unique_key,
            )

            remove_failed_task(source_link, unique_key)
            print(f"      ✅ [成功] {file_name}")
            return "succeed"
        except Exception as e:
            last_err = e
            if attempt < DOWNLOAD_RETRY:
                print(f"      🔁 [下载重试] {announce_title} 第{attempt}次失败，{RETRY_WAIT_SECONDS}s 后重试")
                time.sleep(RETRY_WAIT_SECONDS)
            else:
                write_log_row(
                    institute_name=INSTITUTE_NAME,
                    notice_title=announce_title,
                    notice_type=NOTICE_TYPE_BOOK,
                    disclose_date=disclose_date,
                    status="FAILED",
                    source_link=source_link,
                    save_path=os.path.abspath(expected_path),
                    unique_key=unique_key,
                )
                write_failed_row("download_failed", announce_title, source_link, os.path.abspath(expected_path))

                if record_failed_task:
                    add_or_update_failed_task(
                        {
                            "product_name": product_name,
                            "sales_code": sales_code,
                            "announce_title": announce_title,
                            "disclose_date": disclose_date,
                            "source_link": source_link,
                            "referer": referer,
                            "unique_key": unique_key,
                            "expected_path": os.path.abspath(expected_path),
                            "last_error": str(last_err),
                        }
                    )

                print(f"      ❌ [失败] {announce_title} | {last_err}")
                return "failed"


# =====================================
# 业务流程
# =====================================


_browser_filter_ready = False   # 浏览器是否已点击过筛选标签并同步 Cookie


def _click_filter_tab(browser: ChromiumPage, filter_text: str) -> bool:
    """在浏览器中点击产品类型筛选标签（如"自营理财"），成功返回 True。"""
    try:
        # 优先用 val-input 属性精确定位
        el = browser.ele(f'css:span[val-input="{filter_text}"]', timeout=5)
        if el:
            el.click()
            return True
    except Exception:
        pass
    # 回退：按文本匹配
    try:
        el = browser.ele(f'text:{filter_text}理财', timeout=3)
        if el:
            el.click()
            return True
    except Exception:
        pass
    return False


def fetch_list_page_html(
    session: requests.Session,
    browser: ChromiumPage,
    page_no: int,
) -> Optional[str]:
    global _browser_filter_ready

    if PRODUCT_TYPE_FILTER.strip():
        # 首次：用浏览器点击筛选标签 + 同步 Cookie，确保后续 POST 过滤生效
        if not _browser_filter_ready:
            browser.get(LIST_PAGE_URL)
            time.sleep(3.0)
            ok = _click_filter_tab(browser, PRODUCT_TYPE_FILTER.strip())
            if ok:
                print(f"   ✅ 浏览器已点击「{PRODUCT_TYPE_FILTER.strip()}理财」标签")
            else:
                print(f"   ⚠️ 未找到「{PRODUCT_TYPE_FILTER.strip()}理财」标签，尝试继续")
            time.sleep(2.0)
            sync_cookies_from_browser(session, browser)
            _browser_filter_ready = True

        # 所有页面走 POST 请求（已验证可靠）
        url = LIST_QUERY_URL.format(page=page_no)
        data = {
            "filter_LIKE_main.ext_str4": PRODUCT_TYPE_FILTER.strip(),
            "filter_LIKE_main.TITLE": "",
            "filter_BTS_main.EXT_INT_D": "",
            "filter_BTE_main.EXT_INT_D": "",
        }
        return request_text_with_retry(
            session=session,
            method="POST",
            url=url,
            referer=LIST_PAGE_URL,
            req_desc=f"列表第{page_no}页(筛选)",
            browser=browser,
            data=data,
        )

    # ---- 无筛选 → 走原有 GET 方式 ----
    url = LIST_QUERY_URL.format(page=page_no)
    return request_text_with_retry(
        session=session,
        method="GET",
        url=url,
        referer=LIST_PAGE_URL,
        req_desc=f"列表第{page_no}页",
        browser=browser,
    )


def fetch_detail_html(
    session: requests.Session,
    browser: ChromiumPage,
    detail_url: str,
    article_key: str,
) -> Optional[str]:
    return request_text_with_retry(
        session=session,
        method="GET",
        url=detail_url,
        referer=LIST_PAGE_URL,
        req_desc=f"详情页 articleKey={article_key}",
        browser=browser,
    )


def retry_failed_tasks(
    session: requests.Session,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    counters: Dict[str, int],
):
    tasks = load_failed_tasks()
    if not tasks:
        return

    print(f"🔁 失败任务优先重试: {len(tasks)} 条")
    save_failed_tasks([])

    for idx, task in enumerate(tasks, start=1):
        title = str(task.get("announce_title") or "失败任务").strip()
        print(f"   ↩️ [{idx}/{len(tasks)}] {title}")

        counters["found"] += 1
        result = download_one_book(
            session=session,
            product_name=str(task.get("product_name") or ""),
            sales_code=str(task.get("sales_code") or ""),
            announce_title=title,
            disclose_date=normalize_date(str(task.get("disclose_date") or "")),
            source_link=str(task.get("source_link") or "").strip(),
            referer=str(task.get("referer") or LIST_PAGE_URL),
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


def update_checkpoint(checkpoint: Dict, next_page: int, resume_article_key: str):
    if not ENABLE_CHECKPOINT_RESUME:
        return
    checkpoint["next_page"] = next_page
    checkpoint["resume_article_key"] = resume_article_key
    checkpoint["updated_at"] = now_time_str()
    save_checkpoint(checkpoint)


def process_product(
    session: requests.Session,
    browser: ChromiumPage,
    product: Dict,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    counters: Dict[str, int],
) -> bool:
    article_key = str(product.get("article_key") or "").strip()
    detail_url = str(product.get("detail_url") or "").strip()
    product_name = str(product.get("product_name") or "").strip()

    if TEST_ONLY_ARTICLE_KEY.strip() and article_key != TEST_ONLY_ARTICLE_KEY.strip():
        return True

    print(f"    🧾 产品: {product_name} | articleKey={article_key}")

    detail_html = fetch_detail_html(
        session=session,
        browser=browser,
        detail_url=detail_url,
        article_key=article_key,
    )
    if not detail_html:
        counters["failed"] += 1
        write_failed_row("detail_page_failed", product_name, detail_url, "")
        return False

    parsed = parse_detail_codes_and_date(detail_html)
    codesqian = parsed["codesqian"]
    codeshou = parsed["codeshou"]
    disclose_date = parsed["disclose_date"]

    _in_range, _too_old = is_in_date_range(disclose_date)
    if not _in_range:
        counters["skipped"] += 1
        if _too_old:
            print(f"    ⏭️ [日期跳过] {product_name} 披露日期 {disclose_date} < {START_DATE}")
            if EARLY_STOP:
                print(f"    ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                counters["early_stop"] = 1
        else:
            print(f"    ⏭️ [日期跳过] {product_name} 披露日期 {disclose_date} > {END_DATE or '今天'}")
        return False

    sales_code = codesqian or codeshou or ""
    candidates = build_book_candidates(codesqian, codeshou)

    if not candidates:
        counters["failed"] += 1
        title = f"{product_name} 产品说明书"
        unique_key = build_unique_key(INSTITUTE_NAME, NOTICE_TYPE_BOOK, title, disclose_date)
        expected_path, _ = build_unique_save_path(
            BOOK_FOLDER,
            build_base_filename(INSTITUTE_NAME, product_name, NOTICE_TYPE_BOOK, sales_code, disclose_date),
            ".pdf",
        )
        write_log_row(
            institute_name=INSTITUTE_NAME,
            notice_title=title,
            notice_type=NOTICE_TYPE_BOOK,
            disclose_date=disclose_date,
            status="FAILED",
            source_link=detail_url,
            save_path=os.path.abspath(expected_path),
            unique_key=unique_key,
        )
        write_failed_row("no_book_candidate", title, detail_url, os.path.abspath(expected_path))
        print("      ❌ [失败] 未找到说明书候选链接")
        return False

    # 先尝试“风险揭示及产品说明书”，失败后再尝试“产品说明书”
    for candidate_type, candidate_link in candidates:
        announce_title = f"{product_name} {candidate_type}"
        counters["found"] += 1

        result = download_one_book(
            session=session,
            product_name=product_name,
            sales_code=sales_code,
            announce_title=announce_title,
            disclose_date=disclose_date,
            source_link=candidate_link,
            referer=detail_url,
            downloaded_links=downloaded_links,
            downloaded_fingerprints=downloaded_fingerprints,
            record_failed_task=True,
        )

        if result == "succeed":
            counters["succeed"] += 1
            return True
        if result in ("skipped", "skip", "too_old"):
            counters["skipped"] += 1
            return True

        counters["failed"] += 1

    # 两个候选都失败，按规则等待后继续下一个产品
    print(f"      ⏳ 候选链接都失败，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
    time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
    return False


def prepare_dirs():
    ensure_dir(DOWNLOAD_ROOT)
    ensure_dir(BOOK_FOLDER)


def crawl():
    prepare_dirs()

    downloaded_links = load_downloaded_links()
    downloaded_fingerprints = load_downloaded_fingerprints()
    checkpoint = load_checkpoint()

    start_page = max(1, int(checkpoint.get("next_page") or 1))
    resume_article_key = str(checkpoint.get("resume_article_key") or "").strip()

    print(f"📚 已记录来源链接去重数: {len(downloaded_links)}")
    print(f"🧬 已记录指纹去重数: {len(downloaded_fingerprints)}")
    print(f"🧭 起始页: {start_page}")
    if PRODUCT_TYPE_FILTER.strip():
        print(f"🔎 产品类型筛选: {PRODUCT_TYPE_FILTER}")
    if resume_article_key:
        print(f"🎯 产品断点 articleKey: {resume_article_key}")

    session = create_session()
    browser = create_browser()

    counters = {"found": 0, "succeed": 0, "failed": 0, "skipped": 0}

    try:
        warmup_session_and_cookies(session, browser)
        retry_failed_tasks(session, downloaded_links, downloaded_fingerprints, counters)

        first_html = fetch_list_page_html(session, browser, start_page)
        if not first_html:
            print("❌ 列表首页获取失败，程序结束")
            return

        total_pages = parse_total_pages(first_html)
        print(f"📚 列表总页数: {total_pages}")

        for page_no in range(start_page, total_pages + 1):
            if page_no == start_page:
                page_html = first_html
            else:
                page_html = fetch_list_page_html(session, browser, page_no)
                if not page_html:
                    print(f"   ⚠️ 列表第{page_no}页失败，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
                    time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
                    continue

            products = parse_product_rows_from_list_html(page_html)
            print(f"\n📄 第 {page_no}/{total_pages} 页，产品数: {len(products)}")

            skip_until_resume_hit = bool(resume_article_key and page_no == start_page)

            for p in products:
                current_key = str(p.get("article_key") or "")

                if skip_until_resume_hit:
                    if current_key != resume_article_key:
                        continue
                    print(f"    📍 命中断点 articleKey={current_key}，从此产品继续")
                    skip_until_resume_hit = False

                # 客户端产品类型二次过滤（防止服务端过滤失效）
                if PRODUCT_TYPE_FILTER.strip():
                    p_type = str(p.get("product_type") or "").strip()
                    if p_type and PRODUCT_TYPE_FILTER.strip() not in p_type:
                        print(f"    ⏭️ [类型跳过] {p.get('product_name', '')} (类型={p_type}，需要={PRODUCT_TYPE_FILTER})")
                        counters["skipped"] += 1
                        continue

                process_product(
                    session=session,
                    browser=browser,
                    product=p,
                    downloaded_links=downloaded_links,
                    downloaded_fingerprints=downloaded_fingerprints,
                    counters=counters,
                )

                if counters.get("early_stop"):
                    break

                if ENABLE_CHECKPOINT_RESUME:
                    update_checkpoint(checkpoint, page_no, current_key)

                random_sleep(PRODUCT_INTERVAL_SECONDS)

            if counters.get("early_stop"):
                print(f"   ⏹️ [早停] 页 {page_no} 因日期早停终止，停止翻页")
                break

            if ENABLE_CHECKPOINT_RESUME:
                update_checkpoint(checkpoint, page_no + 1, "")

            random_sleep(PAGE_INTERVAL_SECONDS)

        if ENABLE_CHECKPOINT_RESUME:
            update_checkpoint(checkpoint, 1, "")

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
