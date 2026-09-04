
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
import base64
import random
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs, unquote
from typing import Dict, List, Optional, Set, Tuple

import requests
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

# ╔══════════════════════════════════════════════════════════╗
# ║                      用户配置区                            ║
# ╚══════════════════════════════════════════════════════════╝

# 站点首页（用于获取 Cookie）
HOME_URL = "http://www.gzcb.com.cn/"
INSTITUTE_NAME = "广州银行"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 公告列表分类：可选 fxgg, cxqgg, dqgg
CATEGORY_MAP = {
    "fxgg": {
        "topic_type": "产品发行公告",
        "list_base": "http://www.gzcb.com.cn/sy/jrcs/grlccs/lcgg/fxgg/",
    },
    "cxqgg": {
        "topic_type": "产品存续期公告",
        "list_base": "http://www.gzcb.com.cn/sy/jrcs/grlccs/lcgg/cxqgg/",
    },
    "dqgg": {
        "topic_type": "产品到期公告",
        "list_base": "http://www.gzcb.com.cn/sy/jrcs/grlccs/lcgg/dqgg/",
    },
}
# 
# 抓取哪些分类（逗号分隔，默认全抓）
CATEGORY_CODES = "fxgg"

# 连续空页阈值：达到后停止该分类
MAX_CONSECUTIVE_EMPTY = 3

# 下载设置
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_pdf")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, "广州银行_日志记录.csv")
FAILED_CSV_PATH = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")

# 是否跳过已下载过的来源链接
SKIP_DOWNLOADED = True
ENABLE_CHECKPOINT_RESUME = True

# 请求与反爬
TIMEOUT = 30
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
REQUEST_DELAY_RANGE = (0.5, 1.5)
LIST_PAGE_DELAY_RANGE = (0.8, 1.8)
PDF_RENDER_WAIT = 2.0
PAGE_NO_FILE_WAIT_SECONDS = 10

# 控制台输出
ENABLE_EMOJI_LOG = True

# 每个分类最大翻页数（保险阈值，避免无限翻页）
MAX_PAGES_PER_CATEGORY = 3000

# ============ 日期区间配置（集中管理，可本地覆盖）===========
# 从根目录 project_meta.py 集中读取；如需单独调整，取消下方注释
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("广州银行", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("广州银行", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-02"
# END_DATE   = "2026-06-30"

# 常见浏览器 UA 池（每次请求随机选择）
USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
]

ATTACHMENT_EXTENSIONS = {
    ".pdf",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".zip",
    ".rar",
    ".csv",
    ".txt",
    ".ppt",
    ".pptx",
}

# ╚══════════════════════════════════════════════════════════╝


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def random_sleep(delay_range):
    time.sleep(random.uniform(delay_range[0], delay_range[1]))


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()[:180]


def build_unique_key(topic_type: str, title: str, disclose_date: str) -> str:
    return f"{INSTITUTE_NAME}+{topic_type}+{sanitize_filename(title)}+{normalize_date(disclose_date)}"


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


def get_extension_from_url(file_url: str, default_ext: str = "") -> str:
    path = urlparse(file_url).path
    ext = os.path.splitext(path)[1].lower()
    return ext or default_ext


def resolve_attachment_url_and_ext(detail_url: str, href: str, oldsrc: str = ""):
    # 先基于详情页URL做相对路径拼接，确保保留 /202312 这类目录
    candidates = [href, oldsrc]

    for raw in candidates:
        raw = (raw or "").strip()
        if not raw:
            continue

        parsed = urlparse(raw)
        if "officeapps.live.com" in parsed.netloc.lower():
            qs = parse_qs(parsed.query)
            src_list = qs.get("src") or []
            if src_list:
                src_url = unquote(src_list[0])
                ext = get_extension_from_url(src_url)
                if ext in ATTACHMENT_EXTENSIONS:
                    return src_url, ext
            continue

        resolved = urljoin(detail_url, raw)
        ext = get_extension_from_url(resolved)
        if ext in ATTACHMENT_EXTENSIONS:
            return resolved, ext

    return "", ""


def normalize_date(date_text: str) -> str:
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", date_text)
    if not m:
        return sanitize_filename(date_text) or "未知日期"
    y, mm, dd = m.group(1), int(m.group(2)), int(m.group(3))
    return f"{y}-{mm:02d}-{dd:02d}"


def load_progress(progress_file: str) -> set:
    done = set()
    if os.path.exists(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(line)

    if os.path.exists(LOG_CSV_PATH):
        try:
            with open(LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    status = str(row.get("状态") or "").strip().upper()
                    if status != "SUCCEED":
                        continue
                    source_url = str(row.get("来源链接") or "").strip()
                    if source_url:
                        done.add(source_url)
        except Exception:
            pass
    return done


def save_progress(progress_file: str, unique_key: str):
    ensure_dir(os.path.dirname(progress_file))
    with open(progress_file, "a", encoding="utf-8") as f:
        f.write(unique_key + "\n")


def init_log_csv():
    if os.path.exists(LOG_CSV_PATH):
        return
    ensure_dir(os.path.dirname(LOG_CSV_PATH))
    with open(LOG_CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "机构名称",
            "公告名称",
            "公告类型",
            "披露日期",
            "下载时间",
            "状态",
            "来源链接",
            "保存路径",
            "unique_key",
        ])


def init_failed_csv():
    if os.path.exists(FAILED_CSV_PATH):
        return
    ensure_dir(os.path.dirname(FAILED_CSV_PATH))
    with open(FAILED_CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "下载时间",
            "机构名称",
            "公告名称",
            "公告类型",
            "披露日期",
            "状态",
            "来源链接",
            "保存路径",
            "unique_key",
            "失败原因",
        ])


def log_success(title: str, topic_type: str, date: str, download_time: str, source_url: str, save_path: str):
    init_log_csv()
    unique_key = build_unique_key(topic_type, title, date)
    row = [
        INSTITUTE_NAME,
        title,
        topic_type,
        date,
        download_time,
        "SUCCEED",
        source_url,
        save_path,
        unique_key,
    ]
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def log_failure(title: str, topic_type: str, date: str, download_time: str, source_url: str, save_path: str, reason: str):
    init_failed_csv()
    unique_key = build_unique_key(topic_type, title, date)
    row = [
        download_time,
        INSTITUTE_NAME,
        title,
        topic_type,
        date,
        "FAILED",
        source_url,
        save_path,
        unique_key,
        reason,
    ]
    with open(FAILED_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def load_checkpoint() -> Dict:
    if not os.path.exists(CHECKPOINT_FILE):
        return {"categories": {}, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"categories": {}, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        data.setdefault("categories", {})
        return data
    except Exception:
        return {"categories": {}, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def save_checkpoint(data: Dict):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ensure_dir(os.path.dirname(CHECKPOINT_FILE))
    tmp_path = CHECKPOINT_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CHECKPOINT_FILE)


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


def get_browser_cookies(browser: ChromiumPage) -> dict:
    print_info("正在打开首页获取 Cookie...")
    browser.get(HOME_URL)
    time.sleep(4)
    cookies = {c.get("name"): c.get("value") for c in browser.cookies() if c.get("name")}
    print_ok(f"获取到 Cookie 数量：{len(cookies)}")
    return cookies


def build_request_headers(referer: str) -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }


def make_session(cookies: dict) -> requests.Session:
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
            "DNT": "1",
        }
    )
    return session


def refresh_cookies(session: requests.Session, browser: ChromiumPage):
    print_warn("重新获取 Cookie...")
    cookies = get_browser_cookies(browser)
    session.cookies.update(cookies)


def fetch_url_text(
    session: requests.Session,
    url: str,
    referer: str,
    timeout: int = TIMEOUT,
    browser: Optional[ChromiumPage] = None,
) -> str: # pyright: ignore[reportReturnType]
    last_error = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            headers = build_request_headers(referer)
            resp = session.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            if attempt > 1:
                print_ok(f"请求重试成功: {url} (第 {attempt}/{REQUEST_RETRY} 次)")
            return resp.text
        except requests.HTTPError as e:
            last_error = e
            status = getattr(e.response, "status_code", None)
            if status in (403, 412) and browser is not None:
                refresh_cookies(session, browser)
            if attempt < REQUEST_RETRY:
                print_warn(f"请求失败: {url}, 尝试 {attempt}/{REQUEST_RETRY}: {e}")
                time.sleep(RETRY_WAIT_SECONDS)
                continue
            raise
        except Exception as e:
            last_error = e
            if attempt < REQUEST_RETRY:
                print_warn(f"请求失败: {url}, 尝试 {attempt}/{REQUEST_RETRY}: {e}")
                time.sleep(RETRY_WAIT_SECONDS)
                continue
            raise last_error


def build_list_page_candidates(list_base: str, page_num: int) -> list:
    # 该站点分页规则：index.html -> index_1.html -> index_2.html ...
    if page_num == 1:
        return [urljoin(list_base, "index.html")]
    return [urljoin(list_base, f"index_{page_num - 1}.html")]


def parse_list_items(list_html: str, list_url: str) -> list:
    soup = BeautifulSoup(list_html, "html.parser")
    results = []

    for li in soup.select("li.clearfix"):
        a_tag = li.select_one("a.news_tit")
        t_tag = li.select_one("span.time")
        if not a_tag:
            continue

        title = a_tag.get_text(strip=True)
        href = (a_tag.get("href") or "").strip()
        date_text = t_tag.get_text(strip=True) if t_tag else ""

        if not title or not href:
            continue

        detail_url = urljoin(list_url, href)
        date_norm = normalize_date(date_text)
        results.append((title, detail_url, date_norm))

    return results


def build_unique_pdf_path(folder: str, inst: str, date_text: str, topic_type: str, title: str, ext: str = ".pdf"):
    base_title = sanitize_filename(title)
    topic_part = sanitize_filename(topic_type)
    date_part = normalize_date(date_text)
    suffix = None

    while True:
        if suffix is None:
            filename = f"{inst}_{base_title}_{topic_part}_{date_part}{ext}"
        else:
            filename = f"{inst}_{base_title}_{topic_part}_{date_part}_{suffix}{ext}"

        filepath = os.path.join(folder, filename)
        if not os.path.exists(filepath):
            return filepath, filename
        suffix = 1 if suffix is None else suffix + 1


def build_unique_asset_path(folder: str, inst: str, date_text: str, topic_type: str, title: str, ext: str):
    return build_unique_pdf_path(folder, inst, date_text, topic_type, title, ext)


def build_default_asset_path(folder: str, inst: str, date_text: str, topic_type: str, title: str, ext: str):
    filename = f"{inst}_{sanitize_filename(title)}_{sanitize_filename(topic_type)}_{normalize_date(date_text)}{ext}"
    return os.path.join(folder, filename), filename


def extract_detail_info(session: requests.Session, detail_url: str, referer: str, browser: Optional[ChromiumPage] = None):
    """抓详情页文本，提取标题、日期和附件链接。"""
    try:
        html = fetch_url_text(session, detail_url, referer=referer, timeout=TIMEOUT, browser=browser)
        soup = BeautifulSoup(html, "html.parser")

        title_tag = (
            soup.select_one(".article h1")
            or soup.select_one("h1")
            or soup.select_one(".news_title")
            or soup.select_one("title")
        )
        title = title_tag.get_text(strip=True) if title_tag else ""

        text = soup.get_text(" ", strip=True)
        m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
        date_text = normalize_date(m.group(1)) if m else ""

        attachments = []
        seen_urls = set()
        for a_tag in soup.select("a[href]"):
            href = (a_tag.get("href") or "").strip()
            oldsrc = (a_tag.get("oldsrc") or "").strip()
            if not href or href.startswith("#") or href.lower().startswith("javascript:"):
                continue

            attachment_url, ext = resolve_attachment_url_and_ext(detail_url, href, oldsrc)
            if not attachment_url:
                continue

            if attachment_url in seen_urls:
                continue

            seen_urls.add(attachment_url)
            attachments.append(
                {
                    "url": attachment_url,
                    "ext": ext,
                    "text": a_tag.get_text(" ", strip=True),
                }
            )

        return title, date_text, attachments
    except Exception:
        return "", "", []


def save_detail_as_pdf(browser: ChromiumPage, detail_url: str, save_path: str):
    browser.get(detail_url)
    time.sleep(PDF_RENDER_WAIT)

    # 使用 Chromium DevTools 的 printToPDF，尽量保留原网页排版
    result = browser.run_cdp(
        "Page.printToPDF",
        printBackground=True,
        preferCSSPageSize=True,
        marginTop=0.4,
        marginBottom=0.4,
        marginLeft=0.35,
        marginRight=0.35,
    )

    pdf_bytes = base64.b64decode(result["data"])
    with open(save_path, "wb") as f:
        f.write(pdf_bytes)


def download_attachment_file(
    session: requests.Session,
    browser: ChromiumPage,
    file_url: str,
    save_path: str,
    referer: str,
):
    headers = build_request_headers(referer)
    headers["Accept"] = "application/octet-stream,*/*"

    for attempt in range(1, DOWNLOAD_RETRY + 1):
        try:
            with session.get(file_url, headers=headers, timeout=TIMEOUT, stream=True) as resp:
                resp.raise_for_status()
                ensure_dir(os.path.dirname(save_path))
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (403, 412):
                refresh_cookies(session, browser)
                headers = build_request_headers(referer)
                headers["Accept"] = "application/octet-stream,*/*"
                random_sleep((1.0, 2.0))
                if attempt < DOWNLOAD_RETRY:
                    print_warn(f"附件下载失败，刷新 Cookie 后重试: {file_url}")
                    continue
            if attempt == DOWNLOAD_RETRY:
                raise
            print_warn(f"附件下载失败: {file_url}, 尝试 {attempt}/{DOWNLOAD_RETRY}")
            random_sleep((1.0, 2.0))
        except Exception:
            if attempt == DOWNLOAD_RETRY:
                raise
            print_warn(f"附件下载失败: {file_url}, 尝试 {attempt}/{DOWNLOAD_RETRY}")
            random_sleep((1.0, 2.0))


def download_notice_attachments(
    session: requests.Session,
    browser: ChromiumPage,
    attachments: list,
    title: str,
    date_text: str,
    topic_type: str,
    folder: str,
    referer: str,
) -> tuple:
    all_ok = True
    downloaded_count = 0

    for attachment in attachments:
        ext = attachment["ext"] or ".bin"
        default_path, default_name = build_default_asset_path(folder, INSTITUTE_NAME, date_text, topic_type, title, ext)
        if os.path.exists(default_path):
            print_info(f"附件跳过：{default_name}")
            continue

        save_path, filename = build_unique_asset_path(folder, INSTITUTE_NAME, date_text, topic_type, title, ext)
        print_info(f"附件下载：{filename}")
        try:
            download_attachment_file(session, browser, attachment["url"], save_path, referer)
            downloaded_count += 1
            print_ok(f"附件完成：{filename}")
        except Exception as e:
            print_error(f"附件失败：{attachment['url']} - {e}")
            all_ok = False

    return all_ok, downloaded_count


def process_one_notice(
    session: requests.Session,
    browser: ChromiumPage,
    topic_type: str,
    title: str,
    detail_url: str,
    date_text: str,
    progress_set: set,
    referer: str,
):
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    unique_key = detail_url
    topic_folder = os.path.join(DOWNLOAD_ROOT, topic_type)
    attachment_folder = os.path.join(topic_folder, "附件")
    ensure_dir(topic_folder)
    ensure_dir(attachment_folder)

    # Some list items are direct file URLs (mostly .docx). Download them directly.
    direct_ext = get_extension_from_url(detail_url)
    if direct_ext in ATTACHMENT_EXTENSIONS:
        final_title = title
        final_date = date_text or "未知日期"
        _in_range, _too_old = is_in_date_range(final_date)
        if not _in_range:
            tag = "过早(早停)" if _too_old else "过晚"
            print_info(f"公告跳过：披露日期 {normalize_date(final_date)} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]")
            return False, True

        already_downloaded = SKIP_DOWNLOADED and unique_key in progress_set
        default_path, default_name = build_default_asset_path(
            topic_folder,
            INSTITUTE_NAME,
            final_date,
            topic_type,
            final_title,
            direct_ext,
        )
        if already_downloaded and os.path.exists(default_path):
            print_info(f"公告跳过：已下载 {default_name}")
            return False, True

        save_path, filename = build_unique_asset_path(
            topic_folder,
            INSTITUTE_NAME,
            final_date,
            topic_type,
            final_title,
            direct_ext,
        )
        try:
            print_info(f"公告下载（直链文件）：{filename}")
            download_attachment_file(session, browser, detail_url, save_path, referer)
            print_ok(f"公告完成：{filename}")
            save_progress(PROGRESS_FILE, unique_key)
            progress_set.add(unique_key)
            log_success(final_title, topic_type, final_date, now_time, detail_url, os.path.abspath(save_path))
            return True, False
        except Exception as e:
            print_error(f"公告失败：{detail_url} - {e}")
            log_failure(final_title, topic_type, final_date, now_time, detail_url, os.path.abspath(save_path), str(e))
            return False, False

    d_title, d_date, attachments = extract_detail_info(
        session,
        detail_url,
        referer=referer,
        browser=browser,
    )
    final_title = d_title or title
    final_date = d_date or date_text or "未知日期"
    _in_range, _too_old = is_in_date_range(final_date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print_info(f"公告跳过：披露日期 {normalize_date(final_date)} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]")
        return False, True

    already_downloaded = SKIP_DOWNLOADED and unique_key in progress_set
    pdf_success = True

    if not already_downloaded:
        save_path, filename = build_unique_pdf_path(topic_folder, INSTITUTE_NAME, final_date, topic_type, final_title)

        try:
            print_info(f"公告下载：{filename}")
            save_detail_as_pdf(browser, detail_url, save_path)

            if os.path.getsize(save_path) < 1024:
                raise RuntimeError("生成 PDF 过小，疑似失败")

            print_ok(f"公告完成：{filename}")
            save_progress(PROGRESS_FILE, unique_key)
            progress_set.add(unique_key)
        except Exception as e:
            print_error(f"公告失败：{detail_url} - {e}")
            log_failure(final_title, topic_type, final_date, now_time, detail_url, os.path.abspath(save_path), str(e))
            return False, False
    else:
        print_info(f"公告跳过：已下载 {final_title}")
        save_path = ""

    try:
        attachments_ok, downloaded_attachment_count = download_notice_attachments(
            session=session,
            browser=browser,
            attachments=attachments,
            title=final_title,
            date_text=final_date,
            topic_type=topic_type,
            folder=attachment_folder,
            referer=detail_url,
        )

        if already_downloaded and attachments_ok and downloaded_attachment_count == 0:
            return False, True

        if pdf_success and attachments_ok:
            log_success(final_title, topic_type, final_date, now_time, detail_url, os.path.abspath(save_path) if save_path else "")
        else:
            log_failure(final_title, topic_type, final_date, now_time, detail_url, os.path.abspath(save_path) if save_path else "", "附件下载失败")
        return pdf_success and attachments_ok, False

    except Exception as e:
        print_error(f"公告失败：{detail_url} - {e}")
        log_failure(final_title, topic_type, final_date, now_time, detail_url, os.path.abspath(save_path) if save_path else "", str(e))
        return False, False


def crawl_category(session: requests.Session, browser: ChromiumPage, code: str, progress_set: set):
    conf = CATEGORY_MAP[code]
    topic_type = conf["topic_type"]
    list_base = conf["list_base"]
    topic_folder = os.path.join(DOWNLOAD_ROOT, topic_type)
    ensure_dir(topic_folder)

    print_section(f"开始抓取分类：{topic_type} ({code})")
    print_info(f"列表入口：{list_base}")
    print_info(f"保存目录：{topic_folder}")

    total = 0
    success = 0
    skipped = 0
    consecutive_empty = 0
    checkpoint = load_checkpoint()
    saved_page = 0
    if ENABLE_CHECKPOINT_RESUME:
        saved_page = int(checkpoint.get("categories", {}).get(code, {}).get("last_page", 0) or 0)
        if saved_page > 0:
            print_info(f"断点续跑：从第 {saved_page + 1} 页开始")

    for page_num in range(saved_page + 1, MAX_PAGES_PER_CATEGORY + 1):
        page_items = []
        list_url_used = ""
        candidates = build_list_page_candidates(list_base, page_num)

        got_http_error = False
        for candidate_url in candidates:
            list_url_used = candidate_url

            list_html = None
            for attempt in range(1, REQUEST_RETRY + 1):
                try:
                    list_html = fetch_url_text(
                        session,
                        candidate_url,
                        referer=list_base,
                        timeout=TIMEOUT,
                        browser=browser,
                    )
                    break
                except requests.HTTPError as e:
                    status = getattr(e.response, "status_code", None)
                    if status in (403, 412):
                        refresh_cookies(session, browser)
                        random_sleep((1.0, 2.0))
                        continue
                    if status == 404:
                        got_http_error = True
                        break
                    print_warn(f"列表页失败：{candidate_url}，第 {attempt}/{REQUEST_RETRY} 次 - {e}")
                    random_sleep((1.0, 2.0))
                except Exception as e:
                    print_warn(f"列表页失败：{candidate_url}，第 {attempt}/{REQUEST_RETRY} 次 - {e}")
                    random_sleep((1.0, 2.0))

            if got_http_error:
                continue

            if list_html:
                page_items = parse_list_items(list_html, list_url_used)
                if page_items:
                    break

        if got_http_error and page_num > 1 and not page_items:
            print_info(f"第 {page_num} 页返回 404，结束该分类抓取")
            break

        if not page_items:
            consecutive_empty += 1
            print_warn(f"第 {page_num} 页无数据（连续空页 {consecutive_empty}/{MAX_CONSECUTIVE_EMPTY}）")
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print_warn("连续空页达到阈值，结束该分类")
                break
            random_sleep(LIST_PAGE_DELAY_RANGE)
            continue

        consecutive_empty = 0
        print_info(f"第 {page_num} 页，共 {len(page_items)} 条")

        early_stop_triggered = False
        for title, detail_url, date_text in page_items:
            _in_range, _too_old = is_in_date_range(date_text)
            if not _in_range:
                if _too_old:
                    print_info(f"公告跳过：{title} 披露日期 {normalize_date(date_text)} < {START_DATE}")
                    if EARLY_STOP:
                        print_info(f"早停：检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stop_triggered = True
                        break
                else:
                    print_info(f"公告跳过：{title} 披露日期 {normalize_date(date_text)} > {END_DATE or '今天'}")
                skipped += 1
                continue

            total += 1
            ok, is_skipped = process_one_notice(
                session=session,
                browser=browser,
                topic_type=topic_type,
                title=title,
                detail_url=detail_url,
                date_text=date_text,
                progress_set=progress_set,
                referer=list_url_used,
            )
            if ok:
                success += 1
            if is_skipped:
                skipped += 1

            random_sleep(REQUEST_DELAY_RANGE)

        if early_stop_triggered:
            print_info(f"早停：分类 {topic_type} 因日期早停终止，停止翻页")
            break

        if ENABLE_CHECKPOINT_RESUME:
            checkpoint.setdefault("categories", {})[code] = {
                "last_page": page_num,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_checkpoint(checkpoint)

        random_sleep(LIST_PAGE_DELAY_RANGE)

    print_ok(f"分类完成：发现 {total} | 成功 {success} | 跳过 {skipped}")
    return total, success, skipped


def resolve_category_codes() -> list:
    codes = [c.strip() for c in CATEGORY_CODES.split(",") if c.strip()]
    valid = []
    for code in codes:
        if code in CATEGORY_MAP:
            valid.append(code)
        else:
            print_warn(f"未知分类编码：{code}，已忽略")
    return valid


def crawl():
    ensure_dir(DOWNLOAD_ROOT)
    init_log_csv()
    init_failed_csv()
    progress_set = load_progress(PROGRESS_FILE)
    checkpoint = load_checkpoint()
    print_section("广州银行理财公告爬虫启动")
    print_info(f"机构名称：{INSTITUTE_NAME}")
    print_info(f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print_info(f"已下载记录：{len(progress_set)} 条")
    print_info(f"已有检查点栏目数：{len(checkpoint.get('categories', {}))}")
    print_info(f"下载根目录：{os.path.abspath(DOWNLOAD_ROOT)}")
    print_info(f"日志文件：{os.path.abspath(LOG_CSV_PATH)}")
    print_info(f"失败文件：{os.path.abspath(FAILED_CSV_PATH)}")

    codes = resolve_category_codes()
    if not codes:
        print_error("未配置有效分类，程序结束")
        return

    print_info(f"本次抓取分类：{', '.join(codes)}")

    browser = _new_page()
    try:
        cookies = get_browser_cookies(browser)
        session = make_session(cookies)

        grand_total = 0
        grand_success = 0
        grand_skipped = 0

        for code in codes:
            total, success, skipped = crawl_category(session, browser, code, progress_set)
            grand_total += total
            grand_success += success
            grand_skipped += skipped

        print_section("全部分类抓取完成")
        print_ok(f"共发现：{grand_total}")
        print_ok(f"成功下载：{grand_success}")
        print_info(f"跳过已下载：{grand_skipped}")
        print_info(f"PDF 保存目录：{os.path.abspath(DOWNLOAD_ROOT)}")
        print_info(f"日志文件：{os.path.abspath(LOG_CSV_PATH)}")
        print_info(f"失败文件：{os.path.abspath(FAILED_CSV_PATH)}")

    finally:
        browser.quit()


if __name__ == "__main__":
    crawl()
