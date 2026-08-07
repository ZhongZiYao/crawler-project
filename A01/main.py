
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
import os
import random
import re
import time
from datetime import datetime
from urllib.parse import urlencode
import base64

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

# ╔══════════════════════════════════════════════════════════╗
# ║                      用户配置区                            ║
# ╚══════════════════════════════════════════════════════════╝

# 站点首页（用于获取 Cookie）
HOME_URL = "https://wm.icbc.com.cn/"
INSTITUTE_NAME = "工银理财"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 接口与分类配置（按你的抓包信息整理）
CATEGORY_MAP = {
    "issuReport": {
        "topic_type": "发行公告",
        "route": "/issuReport",
        "api_url": "https://wm.icbc.com.cn/clt/info/112501",
        "head_trans_code": "112501",
        "folder_name": "发行公告",
    },
    "perReport": {
        "topic_type": "定期报告",
        "route": "/perReport",
        "api_url": "https://wm.icbc.com.cn/clt/info/112601",
        "head_trans_code": "112601",
        "folder_name": "定期报告",
    },
    "tempInfoDisclosure": {
        "topic_type": "临时性信息披露",
        "route": "/tempInfoDisclosure",
        "api_url": "https://wm.icbc.com.cn/clt/info/112801",
        "head_trans_code": "112801",
        "folder_name": "临时性信息披露",
    },
    "expireNotice": {
        "topic_type": "到期公告",
        "route": "/expireNotice",
        "api_url": "https://wm.icbc.com.cn/clt/info/112701",
        "head_trans_code": "112701",
        "folder_name": "到期公告",
    },
    "companyNotice": {
        "topic_type": "公司公告",
        "route": "/companyNotice",
        "api_url": "https://wm.icbc.com.cn/clt/info/112101",
        "head_trans_code": "112101",
        "folder_name": "公司公告",
    },
}

# 抓取哪些分类（逗号分隔，默认全抓）
CATEGORY_CODES = "issuReport,perReport,tempInfoDisclosure,expireNotice,companyNotice"

# 单独抓取的情况
# CATEGORY_CODES = "issuReport"
# CATEGORY_CODES = "perReport"
# CATEGORY_CODES = "tempInfoDisclosure"
# CATEGORY_CODES = "expireNotice"
# CATEGORY_CODES = "companyNotice"

# 接口分页参数
PAGE_SIZE = 10
MAX_PAGES_PER_CATEGORY = 3000
MAX_CONSECUTIVE_EMPTY = 3

# 接口公共请求负载参数
MENU_ID = "information_disclosure"
HEAD_SYSTEM_ID = "gtpoints"
HEAD_CHANNEL_ID = "0"
HEAD_JSESSION_ID = ""
BATCH_PARAM = ""
HEAD_OSNUMBER = "37F699716632AC"
ANNOUCEMENT_NAME = ""

# 下载接口
DOWNLOAD_API_BASE = "https://wm.icbc.com.cn/file/platform/downloadfile"

# 下载与日志
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, "工银理财_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
WARN_LOG_PATH = os.path.join(SCRIPT_DIR, "crawler_warnings.log")

# 是否跳过已下载
SKIP_DOWNLOADED = True

# 请求与反爬
TIMEOUT = 30
REQUEST_RETRY = 3
REQUEST_DELAY_RANGE = (0.5, 1.5)
LIST_PAGE_DELAY_RANGE = (0.8, 1.8)
COOKIE_READY_WAIT = 4
PDF_RENDER_WAIT = 2.0

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

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".rar",
    ".csv",
    ".txt",
    ".ppt",
    ".pptx",
}

# ╚══════════════════════════════════════════════════════════╝

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
    START_DATE = PROJECT_START_DATE.get("A01", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("A01", True)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = True
    # 断点续传统用函数回退（project_meta 不可用时）
    try:
        from project_meta import load_checkpoint, save_checkpoint, now_str
    except Exception:
        import json as _json
        def load_checkpoint(_fp):
            try:
                with open(_fp, 'r', encoding='utf-8') as _f:
                    return _json.load(_f)
            except Exception:
                return {}
        def save_checkpoint(_fp, _data):
            import os as _os
            _d = _os.path.dirname(_fp)
            if _d: _os.makedirs(_d, exist_ok=True)
            with open(_fp, 'w', encoding='utf-8') as _f:
                _json.dump(_data, _f, ensure_ascii=False, indent=2)
        def now_str():
            from datetime import datetime as _dt
            return _dt.now().strftime('%Y-%m-%d %H:%M:%S')
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-09"
# END_DATE   = "2026-06-30"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def random_sleep(delay_range):
    time.sleep(random.uniform(delay_range[0], delay_range[1]))


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()[:180]


def normalize_date(date_text: str) -> str:
    date_text = str(date_text or "").strip()
    m = re.search(r"(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", date_text)
    if m:
        y, mm, dd = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mm:02d}-{dd:02d}"

    m2 = re.search(r"(\d{4})(\d{2})(\d{2})", date_text)
    if m2:
        y, mm, dd = m2.group(1), int(m2.group(2)), int(m2.group(3))
        return f"{y}-{mm:02d}-{dd:02d}"

    return sanitize_filename(date_text) or "未知日期"


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


def get_extension(resource_id: str, fallback_name: str = "") -> str:
    resource_id = str(resource_id or "")
    fallback_name = str(fallback_name or "")

    ext = os.path.splitext(resource_id)[1].lower()
    if ext in ALLOWED_EXTENSIONS:
        return ext

    ext2 = os.path.splitext(fallback_name)[1].lower()
    if ext2 in ALLOWED_EXTENSIONS:
        return ext2

    return ".bin"


def load_progress(progress_file: str) -> set:
    done = set()
    if os.path.exists(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(line)
    return done


def save_progress(progress_file: str, unique_key: str):
    with open(progress_file, "a", encoding="utf-8") as f:
        f.write(unique_key + "\n")


def log_to_csv(
    institute_name: str,
    title: str,
    topic_type: str,
    date_text: str,
    download_time: str,
    status: str,
    source_url: str,
    save_path: str,
    unique_key: str,
):
    row = [
        institute_name,
        title,
        topic_type,
        date_text,
        download_time,
        status,
        source_url,
        save_path,
        unique_key,
    ]
    header = [
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
    # 按公告类型生成单独日志文件，放在 logs/ 目录
    ensure_dir(LOG_DIR)
    safe_topic = sanitize_filename(topic_type or "其他")
    csv_path = os.path.join(LOG_DIR, f"{safe_topic}_日志记录.csv")
    exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow(row)


def get_browser_cookies(browser: ChromiumPage) -> dict:
    print("正在打开首页获取 Cookie...")
    browser.get(HOME_URL)
    time.sleep(COOKIE_READY_WAIT)
    cookies = {c.get("name"): c.get("value") for c in browser.cookies() if c.get("name")}
    print(f"  获取到 Cookie 数量：{len(cookies)}")
    return cookies


def write_warning(message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(WARN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{ts} {message}\n")


def build_request_headers(referer: str = HOME_URL) -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Origin": "https://wm.icbc.com.cn",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
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
    print("\n  [Cookie 刷新] 重新获取 Cookie...")
    cookies = get_browser_cookies(browser)
    session.cookies.clear()
    session.cookies.update(cookies)


def build_list_payload(conf: dict, page_num: int, page_size: int = PAGE_SIZE) -> dict:
    return {
        "menu_id": MENU_ID,
        "route": conf["route"],
        "full_route": conf["route"],
        "page_num": page_num,
        "page_size": page_size,
        "annoucement_name": ANNOUCEMENT_NAME,
        "head_system_id": HEAD_SYSTEM_ID,
        "head_channel_id": HEAD_CHANNEL_ID,
        "head_trans_code": conf["head_trans_code"],
        "head_jsession_id": HEAD_JSESSION_ID,
        "batch_param": BATCH_PARAM,
        "head_osnumber": HEAD_OSNUMBER,
    }


def post_json_with_retry(
    session: requests.Session,
    browser: ChromiumPage,
    url: str,
    json_payload: dict,
    referer: str,
    timeout: int = TIMEOUT,
):
    last_error = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            headers = build_request_headers(referer=referer)
            resp = session.post(url, json=json_payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            last_error = e
            if status in (403, 412):
                refresh_cookies(session, browser)
                random_sleep((1.0, 2.0))
                continue
            if attempt == REQUEST_RETRY:
                raise
            random_sleep((1.0, 2.0))
        except Exception as e:
            last_error = e
            if attempt == REQUEST_RETRY:
                raise
            random_sleep((1.0, 2.0))
    if last_error:
        raise last_error


def build_download_source_url(resource_id: str) -> str:
    return f"{DOWNLOAD_API_BASE}?{urlencode({'resource_id': resource_id})}"


def build_unique_key(topic_type: str, ann_id: str, date_text: str, title: str, file_name1: str) -> str:
    """仅对“同一公告记录”去重，不以 resource_id 去重，避免误跳过同源文件。"""
    ann_part = ann_id.strip() if ann_id and ann_id.strip() else "NO_ANN_ID"
    title_part = sanitize_filename(title or "")
    file_part = sanitize_filename(file_name1 or "")
    date_part = date_text or "未知日期"
    return f"{topic_type}|{ann_part}|{date_part}|{title_part}|{file_part}"


def build_unique_file_path(category_dir: str, date_text: str, topic_type: str, title: str, ext: str):
    base_title = sanitize_filename(title)
    topic_part = sanitize_filename(topic_type)
    suffix = None

    while True:
        if suffix is None:
            filename = f"{INSTITUTE_NAME}_{date_text}_{topic_part}_{base_title}{ext}"
        else:
            filename = f"{INSTITUTE_NAME}_{date_text}_{topic_part}_{base_title}_{suffix}{ext}"

        filepath = os.path.join(category_dir, filename)
        if not os.path.exists(filepath):
            return filepath, filename
        suffix = 1 if suffix is None else suffix + 1


def save_detail_as_pdf(browser: ChromiumPage, detail_url: str, save_path: str):
    browser.get(detail_url)
    time.sleep(PDF_RENDER_WAIT)

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


def download_resource_file(
    session: requests.Session,
    browser: ChromiumPage,
    resource_id: str,
    display_name: str,
    save_path: str,
    referer: str,
):
    source_url = build_download_source_url(resource_id)
    payload = {
        "fileName": display_name,
    }

    headers = build_request_headers(referer=referer)
    headers["Accept"] = "application/octet-stream,*/*"

    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            with session.post(
                source_url,
                json=payload,
                headers=headers,
                timeout=TIMEOUT,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            if os.path.getsize(save_path) < 128:
                raise RuntimeError("下载文件过小，疑似失败")
            return source_url
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (403, 412):
                refresh_cookies(session, browser)
                headers = build_request_headers(referer=referer)
                headers["Accept"] = "application/octet-stream,*/*"
                random_sleep((1.0, 2.0))
                continue
            if attempt == REQUEST_RETRY:
                raise
            random_sleep((1.0, 2.0))
        except Exception:
            if attempt == REQUEST_RETRY:
                raise
            random_sleep((1.0, 2.0))


def process_one_row(
    session: requests.Session,
    browser: ChromiumPage,
    conf: dict,
    row: dict,
    progress_set: set,
):
    topic_type = conf["topic_type"]
    category_dir = os.path.join(DOWNLOAD_ROOT, conf["folder_name"])
    ensure_dir(category_dir)

    title = str(row.get("annoucement_name") or "未命名公告").strip()
    date_text = normalize_date(str(row.get("show_date_time") or ""))

    _in_range, _too_old = is_in_date_range(date_text)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"  ⏭️ [日期跳过] 披露日期 {date_text} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return False, True

    ann_id = str(row.get("annoucement_id") or "")
    resource_id = str(row.get("resource_id") or "").strip()
    file_name1 = str(row.get("file_name1") or "").strip()

    is_company = conf.get("route") == "/companyNotice" or conf.get("folder_name") == "公司公告"

    # companyNotice 详情页需要渲染为 PDF，resource_id 为附件可选；其他类型没有 resource_id 则跳过
    if not resource_id and not is_company:
        return False, False

    unique_key = build_unique_key(
        topic_type=topic_type,
        ann_id=ann_id,
        date_text=date_text,
        title=title,
        file_name1=file_name1,
    )
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if SKIP_DOWNLOADED and unique_key in progress_set:
        print(f"  [跳过] 已下载：{title}")
        return False, True

    referer = f"https://wm.icbc.com.cn{conf['route']}"

    if is_company:
        # 渲染详情页为 PDF
        detail_url = f"https://wm.icbc.com.cn/companyNoticeDetails?id={ann_id}"
        pdf_ext = ".pdf"
        detail_save_path, detail_filename = build_unique_file_path(
            category_dir=category_dir,
            date_text=date_text,
            topic_type=topic_type,
            title=title,
            ext=pdf_ext,
        )

        try:
            print(f"  [渲染PDF] {detail_filename}")
            save_detail_as_pdf(browser, detail_url, detail_save_path)
            if os.path.getsize(detail_save_path) < 1024:
                raise RuntimeError("生成 PDF 过小，疑似失败")
            print(f"  [完成] {detail_filename}")

            # 下载页面中可能存在的附件（resource_id）
            if resource_id:
                att_ext = get_extension(resource_id, file_name1)
                att_save_path, att_filename = build_unique_file_path(
                    category_dir=category_dir,
                    date_text=date_text,
                    topic_type=topic_type,
                    title=f"{title}_{file_name1}",
                    ext=att_ext,
                )
                display_name = file_name1 or f"{sanitize_filename(title)}{att_ext}"
                try:
                    print(f"  [附件] {att_filename}")
                    source_url = download_resource_file(
                        session=session,
                        browser=browser,
                        resource_id=resource_id,
                        display_name=display_name,
                        save_path=att_save_path,
                        referer=detail_url,
                    )
                    print(f"  [完成] {att_filename}")
                    log_to_csv(
                        institute_name=INSTITUTE_NAME,
                        title=title + " | 附件: " + file_name1,
                        topic_type=topic_type,
                        date_text=date_text,
                        download_time=now_time,
                        status="SUCCEED",
                        source_url=source_url,
                        save_path=os.path.abspath(att_save_path),
                        unique_key=unique_key,
                    )
                except Exception as e:
                    print(f"  [附件失败] {resource_id} - {e}")
                    log_to_csv(
                        institute_name=INSTITUTE_NAME,
                        title=title + " | 附件: " + file_name1,
                        topic_type=topic_type,
                        date_text=date_text,
                        download_time=now_time,
                        status="FAILED",
                        source_url=build_download_source_url(resource_id),
                        save_path=os.path.abspath(att_save_path),
                        unique_key=unique_key,
                    )

            # 记录已处理（详情页）
            save_progress(PROGRESS_FILE, unique_key)
            progress_set.add(unique_key)
            log_to_csv(
                institute_name=INSTITUTE_NAME,
                title=title,
                topic_type=topic_type,
                date_text=date_text,
                download_time=now_time,
                status="SUCCEED",
                source_url=detail_url,
                save_path=os.path.abspath(detail_save_path),
                unique_key=unique_key,
            )
            return True, False
        except Exception as e:
            print(f"  [失败] 详情页 {detail_url} - {e}")
            log_to_csv(
                institute_name=INSTITUTE_NAME,
                title=title,
                topic_type=topic_type,
                date_text=date_text,
                download_time=now_time,
                status="FAILED",
                source_url=detail_url,
                save_path=os.path.abspath(detail_save_path),
                unique_key=unique_key,
            )
            return False, False

    # 非公司公告：直接按 resource_id 下载文件
    ext = get_extension(resource_id, file_name1)
    save_path, filename = build_unique_file_path(
        category_dir=category_dir,
        date_text=date_text,
        topic_type=topic_type,
        title=title,
        ext=ext,
    )

    display_name = file_name1 or f"{sanitize_filename(title)}{ext}"

    try:
        print(f"  [下载] {filename}")
        source_url = download_resource_file(
            session=session,
            browser=browser,
            resource_id=resource_id,
            display_name=display_name,
            save_path=save_path,
            referer=referer,
        )
        print(f"  [完成] {filename}")

        save_progress(PROGRESS_FILE, unique_key)
        progress_set.add(unique_key)

        log_to_csv(
            institute_name=INSTITUTE_NAME,
            title=title,
            topic_type=topic_type,
            date_text=date_text,
            download_time=now_time,
            status="SUCCEED",
            source_url=source_url,
            save_path=os.path.abspath(save_path),
            unique_key=unique_key,
        )
        return True, False

    except Exception as e:
        print(f"  [失败] {resource_id} - {e}")
        log_to_csv(
            institute_name=INSTITUTE_NAME,
            title=title,
            topic_type=topic_type,
            date_text=date_text,
            download_time=now_time,
            status="FAILED",
            source_url=build_download_source_url(resource_id),
            save_path=os.path.abspath(save_path),
            unique_key=unique_key,
        )
        return False, False


def crawl_category(session: requests.Session, browser: ChromiumPage, code: str, progress_set: set, checkpoint: dict = None):
    conf = CATEGORY_MAP[code]
    topic_type = conf["topic_type"]
    api_url = conf["api_url"]

    print(f"\n{'=' * 62}")
    print(f"开始抓取分类：{topic_type} ({code})")
    print(f"接口地址：{api_url}")
    print(f"{'=' * 62}")

    total_found = 0
    success = 0
    skipped = 0
    consecutive_empty = 0
    expected_total = None
    early_stop_triggered = False

    # 断点续传：读取上次爬到的页码
    start_page = 1
    if checkpoint is not None:
        code_cp = checkpoint.get("code_pages", {}).get(code, {})
        start_page = int(code_cp.get("page", 1))
        if code_cp.get("done"):
            print(f"[断点续传] {code} 已完成，跳过")
            return 0, 0, 0
        if start_page > 1:
            print(f"[断点续传] {code} 从第 {start_page} 页继续")

    for page_num in range(start_page, MAX_PAGES_PER_CATEGORY + 1):
        payload = build_list_payload(conf=conf, page_num=page_num, page_size=PAGE_SIZE)
        referer = f"https://wm.icbc.com.cn{conf['route']}"

        try:
            data = post_json_with_retry(
                session=session,
                browser=browser,
                url=api_url,
                json_payload=payload,
                referer=referer,
                timeout=TIMEOUT,
            )
        except Exception as e:
            print(f"[分类 {code}] 第 {page_num} 页请求失败：{e}")
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print(f"[分类 {code}] 连续失败达到阈值，结束该分类。")
                break
            random_sleep(LIST_PAGE_DELAY_RANGE)
            continue

        if not bool(data.get("succ", False)):
            msg = data.get("head_ret_msg") or "接口返回失败"
            print(f"[分类 {code}] 第 {page_num} 页接口失败：{msg}")
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print(f"[分类 {code}] 连续失败达到阈值，结束该分类。")
                break
            random_sleep(LIST_PAGE_DELAY_RANGE)
            continue

        rows = data.get("rows") or []
        if expected_total is None:
            expected_total = int(data.get("total") or 0)
            print(f"[分类 {code}] 接口总条数：{expected_total}")

        if not rows:
            consecutive_empty += 1
            print(f"[分类 {code}] 第 {page_num} 页无数据（连续空页 {consecutive_empty}/{MAX_CONSECUTIVE_EMPTY}）")
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print(f"[分类 {code}] 连续空页达到阈值，结束该分类。")
                break
            random_sleep(LIST_PAGE_DELAY_RANGE)
            continue

        consecutive_empty = 0
        print(f"[分类 {code}] 第 {page_num} 页，共 {len(rows)} 条")

        for row in rows:
            total_found += 1
            _disclose_date = normalize_date(str(row.get("show_date_time") or ""))
            _in_range, _too_old = is_in_date_range(_disclose_date)
            if not _in_range:
                skipped += 1
                if _too_old:
                    print(f"  ⏭️ [日期跳过] {str(row.get('annoucement_name') or '')[:40]} 披露日期 {_disclose_date} < {START_DATE}")
                    if EARLY_STOP:
                        print(f"  ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stop_triggered = True
                        break
                else:
                    print(f"  ⏭️ [日期跳过] {str(row.get('annoucement_name') or '')[:40]} 披露日期 {_disclose_date} > {END_DATE or '今天'}")
                continue
            ok, is_skipped = process_one_row(
                session=session,
                browser=browser,
                conf=conf,
                row=row,
                progress_set=progress_set,
            )
            if ok:
                success += 1
            if is_skipped:
                skipped += 1
            random_sleep(REQUEST_DELAY_RANGE)

        # 断点续传：每页处理完后保存进度
        if checkpoint is not None:
            checkpoint.setdefault("code_pages", {})[code] = {"page": page_num + 1, "done": False}
            checkpoint["current_code"] = code
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)

        if early_stop_triggered:
            print(f"[分类 {code}] 早停触发，结束该分类。")
            break

        if expected_total and total_found >= expected_total:
            print(f"[分类 {code}] 已达到接口总条数，结束该分类。")
            break

        random_sleep(LIST_PAGE_DELAY_RANGE)

    print(f"\n[分类 {code}] 抓取结束：发现 {total_found} | 成功 {success} | 跳过 {skipped}")

    # 断点续传：分类完成后标记 done
    if checkpoint is not None:
        checkpoint.setdefault("code_pages", {})[code] = {"page": page_num, "done": True}
        completed = checkpoint.setdefault("completed_codes", [])
        if code not in completed:
            completed.append(code)
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)

    return total_found, success, skipped


def resolve_category_codes() -> list:
    codes = [c.strip() for c in CATEGORY_CODES.split(",") if c.strip()]
    valid = []
    for code in codes:
        if code in CATEGORY_MAP:
            valid.append(code)
        else:
            print(f"[警告] 未知分类编码：{code}，已忽略")
    return valid


def prepare_directories():
    ensure_dir(DOWNLOAD_ROOT)
    for conf in CATEGORY_MAP.values():
        ensure_dir(os.path.join(DOWNLOAD_ROOT, conf["folder_name"]))


def crawl():
    prepare_directories()
    progress_set = load_progress(PROGRESS_FILE)
    print(f"已有下载记录：{len(progress_set)} 条")

    # 断点续传：加载 checkpoint
    checkpoint = load_checkpoint(CHECKPOINT_FILE)
    completed_codes = set(checkpoint.get("completed_codes", []))
    if completed_codes:
        print(f"[断点续传] 已完成分类：{', '.join(sorted(completed_codes))}")

    codes = resolve_category_codes()
    if not codes:
        print("未配置有效分类，程序结束。")
        return

    # 断点续传：跳过已完成的分类
    pending_codes = [c for c in codes if c not in completed_codes]
    if len(pending_codes) < len(codes):
        print(f"[断点续传] 跳过已完成 {len(codes) - len(pending_codes)} 个分类，本次抓取 {len(pending_codes)} 个")
    codes = pending_codes

    print(f"本次抓取分类：{', '.join(codes)}")

    browser = _new_page()
    try:
        cookies = get_browser_cookies(browser)
        session = make_session(cookies)

        # 如果首页未取到 Cookie，尝试向第一个目标接口发起一次请求以收集 Cookie（降级策略）
        if not cookies:
            codes = resolve_category_codes()
            if codes:
                first = codes[0]
                conf = CATEGORY_MAP[first]
                payload = build_list_payload(conf, page_num=1, page_size=PAGE_SIZE)
                referer = f"https://wm.icbc.com.cn{conf['route']}"
                try:
                    print("首页未获取到 Cookie，尝试从首个接口收集 Cookie...")
                    for attempt in range(1, REQUEST_RETRY + 1):
                        try:
                            headers = build_request_headers(referer=referer)
                            resp = session.post(conf["api_url"], json=payload, headers=headers, timeout=TIMEOUT)
                            # 更新 session cookies（如果响应携带 Set-Cookie）
                            if resp.cookies:
                                session.cookies.update(resp.cookies)
                                print(f"  从接口获得 Cookie：{len(resp.cookies)} 条，已更新 session")
                            else:
                                print("  接口未返回 Cookie")
                            break
                        except requests.HTTPError as e:
                            status = getattr(e.response, "status_code", None)
                            if status in (403, 412):
                                print("  接口返回 403/412，尝试刷新首页 Cookie 再重试")
                                refresh_cookies(session, browser)
                                continue
                            if attempt == REQUEST_RETRY:
                                raise
                            random_sleep((1.0, 2.0))
                        except Exception:
                            if attempt == REQUEST_RETRY:
                                raise
                            random_sleep((1.0, 2.0))
                except Exception as e:
                    msg = f"首页未获取到 Cookie，接口降级采集失败：{e}"
                    print("  "+msg)
                    write_warning(msg)
            else:
                msg = "首页未获取到 Cookie，且未配置任何抓取分类以降级采集"
                print("  "+msg)
                write_warning(msg)

        grand_total = 0
        grand_success = 0
        grand_skipped = 0

        try:
            for code in codes:
                total, success, skipped = crawl_category(session, browser, code, progress_set, checkpoint=checkpoint)
                grand_total += total
                grand_success += success
                grand_skipped += skipped
        except KeyboardInterrupt:
            print("\n[中断] 收到 Ctrl+C，断点已保存，下次运行将从断点处继续")
            raise

        print(f"\n{'=' * 62}")
        print("全部分类抓取完成")
        print(f"共发现：{grand_total}")
        print(f"成功下载：{grand_success}")
        print(f"跳过已下载：{grand_skipped}")
        print(f"文件保存根目录：{os.path.abspath(DOWNLOAD_ROOT)}")
        print(f"日志文件：{os.path.abspath(LOG_CSV_PATH)}")
        print(f"{'=' * 62}")

    finally:
        browser.quit()


if __name__ == "__main__":
    crawl()
