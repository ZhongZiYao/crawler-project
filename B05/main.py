
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
from urllib.parse import urljoin, urlparse

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

# TODO[手动修改]：机构标准名称（请保持与机构信息披露标准一致）
INSTITUTE_NAME = "浦银理财"

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
    START_DATE = PROJECT_START_DATE.get("B05", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("B05", True)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = True
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")

HOME_URL = "https://www.spdb-wm.com/"
DISCLOSURE_URL = "https://www.spdb-wm.com/xxpl/"
MASTER_JSON_URL = "https://www.spdb-wm.com/financialProducts/XXPL/XXPL.json"
COMPLEMENT_JSON_URL = "https://www.spdb-wm.com/financialProducts/xxpl/index.json"

# TODO[手动修改]：模块开关（使用中文名称，True=开启，False=关闭）
ENABLE_MODULES = {
    "公司公告": False,
    "发行公告": True,
    "采购成交结果公告": False,
    "采购信息公告": False,
    "询证函业务公告": False,
    "产品定期报告": True,
    "理财业务半年度情况": False,
    "临时公告": True,
    "到期公告": False,
    "代理销售机构公告": False,
    "托管机构公告": False,
    "其他信息披露": False,
    "封闭式公募理财产品报告": False,
}

# TODO[手动修改]：单模块测试名单（中文名称）；为空时按 ENABLE_MODULES 执行
RUN_ONLY_MODULE_KEYS = []

# TODO[手动修改]：单条公告测试（用于联调）
TEST_ONE_NOTICE_MODE = False
TEST_NOTICE_KEYWORD = ""

# TODO[手动修改]：重试与节奏参数（不同站点可调整）
REQUEST_TIMEOUT = 45
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
PAGE_NO_FILE_WAIT_SECONDS = 10
REQUEST_INTERVAL_SECONDS = (0.6, 1.2)
PAGE_INTERVAL_SECONDS = (0.8, 1.4)
LOCAL_PAGE_SIZE = 10

# TODO[手动修改]：下载去重开关（按来源链接去重）
SKIP_DOWNLOADED = True

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

# 13 个模块映射（来源于前端 xxpl.js）
MODULES = {
    "company_notice": {
        "notice_type": "公司公告",
        "folder": "公司公告",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/zdsxgg/index.json",
        "type_codes": [],
    },
    "issue_notice": {
        "notice_type": "发行公告",
        "folder": "发行公告",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/lccpfxgg/index.json",
        "type_codes": ["0301"],
    },
    "procurement_result_notice": {
        "notice_type": "采购成交结果公告",
        "folder": "采购成交结果公告",
        "direct_json_url": "https://www.spdb-wm.com/gsdtt/cgcj/index.json",
        "type_codes": [],
    },
    "procurement_notice": {
        "notice_type": "采购信息公告",
        "folder": "采购信息公告",
        "direct_json_url": "https://www.spdb-wm.com/gsdtt/cgxx/index.json",
        "type_codes": [],
    },
    "inquiry_letter_notice": {
        "notice_type": "询证函业务公告",
        "folder": "询证函业务公告",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/xzhywgg/index.json",
        "type_codes": [],
    },
    "periodic_report": {
        "notice_type": "产品定期报告",
        "folder": "产品定期报告",
        "direct_json_url": "",
        "type_codes": ["0402", "0403", "0404"],
    },
    "semiannual_business": {
        "notice_type": "理财业务半年度情况",
        "folder": "理财业务半年度情况",
        "direct_json_url": "",
        "type_codes": ["0601"],
    },
    "temporary_notice": {
        "notice_type": "临时公告",
        "folder": "临时公告",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/lsxxxpl/index.json",
        "type_codes": [str(i) for i in range(1001, 1013)],
    },
    "maturity_notice": {
        "notice_type": "到期公告",
        "folder": "到期公告",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/lccpdqgg/index.json",
        "type_codes": ["1101"],
    },
    "agency_sales_notice": {
        "notice_type": "代理销售机构公告",
        "folder": "代理销售机构公告",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/dlxsjggg/index.json",
        "type_codes": [],
    },
    "custodian_notice": {
        "notice_type": "托管机构公告",
        "folder": "托管机构公告",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/tgjggg/index.json",
        "type_codes": [],
    },
    "other_disclosure": {
        "notice_type": "其他信息披露",
        "folder": "其他信息披露",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/qtxxpl/index.json",
        "type_codes": ["1099"],
    },
    "closed_end_public_report": {
        "notice_type": "封闭式公募理财产品报告",
        "folder": "封闭式公募理财产品报告",
        "direct_json_url": "https://www.spdb-wm.com/xxpl/fblcgg/index.json",
        "type_codes": [],
    },
}

CHINESE_NAME_TO_KEY = {v["notice_type"]: k for k, v in MODULES.items()}
KEY_TO_CHINESE = {k: v["notice_type"] for k, v in MODULES.items()}


# =====================================
# 工具函数
# =====================================


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def random_sleep(seconds_range):
    time.sleep(random.uniform(seconds_range[0], seconds_range[1]))


def sanitize_text(text: str, max_len: int = 180) -> str:
    text = re.sub(r"[\\/*?:\"<>|]", "_", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] if text else "未命名"


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


def pubdate_to_sortable_int(date_text: str) -> int:
    text = str(date_text or "")
    numbers = re.sub(r"[^0-9]", "", text)
    if len(numbers) >= 8:
        try:
            return int(numbers[:14].ljust(14, "0"))
        except ValueError:
            return 0
    return 0


def now_time_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_product_name_and_code(title: str):
    cleaned_title = sanitize_text(title, max_len=240)

    code_match = re.search(r"(?:产品代码|销售代码|代码)[：:]\s*([A-Za-z0-9_-]{4,30})", cleaned_title)
    sales_code = code_match.group(1).strip() if code_match else ""

    product_name = re.sub(r"[（(]\s*(?:产品代码|销售代码|代码)[：:][^）)]+[）)]", "", cleaned_title)
    product_name = re.sub(r"\s*[（(][^）)]*公告[^）)]*[）)]", "", product_name)
    product_name = product_name.strip(" _-")
    if not product_name:
        product_name = cleaned_title

    return sanitize_text(product_name, 120), sanitize_text(sales_code, 60)


def guess_extension_from_headers(response: requests.Response, default_ext: str = ".pdf") -> str:
    content_type = (response.headers.get("Content-Type") or "").lower()
    disposition = response.headers.get("Content-Disposition") or ""

    filename_match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.IGNORECASE)
    if filename_match:
        name = filename_match.group(1).strip().strip('"')
        ext = os.path.splitext(name)[1].lower()
        if ext:
            return ext

    if "pdf" in content_type:
        return ".pdf"
    if "msword" in content_type:
        return ".doc"
    if "officedocument.wordprocessingml.document" in content_type:
        return ".docx"
    if "excel" in content_type or "spreadsheetml" in content_type:
        return ".xlsx"
    if "zip" in content_type:
        return ".zip"
    if "text/html" in content_type:
        return ".html"

    parsed = urlparse(response.url or "")
    path_ext = os.path.splitext(parsed.path)[1].lower()
    if path_ext:
        return path_ext

    return default_ext


def build_headers() -> dict:
    return {
        "accept": "application/json,text/javascript,*/*;q=0.01",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": DISCLOSURE_URL,
        "user-agent": random.choice(USER_AGENTS),
        "x-requested-with": "XMLHttpRequest",
    }


def get_browser_cookies_dict(browser: ChromiumPage) -> dict:
    cookies = browser.cookies() or []
    cookie_dict = {}
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


def warmup_and_sync_cookies(session: requests.Session, browser: ChromiumPage):
    print("🍪 正在预热页面并同步 Cookie...")
    for url in [HOME_URL, DISCLOSURE_URL]:
        print(f"   🌐 访问: {url}")
        browser.get(url)
        time.sleep(2)
    count = sync_session_cookies_from_browser(session, browser)
    print(f"   ✅ Cookie 同步完成，同步数量: {count}")


def refresh_session_cookies(session: requests.Session, browser: ChromiumPage, reason: str = ""):
    reason_text = f"（原因: {reason}）" if reason else ""
    print(f"🍪 正在刷新 Cookie {reason_text}")
    browser.get(DISCLOSURE_URL)
    time.sleep(2)
    count = sync_session_cookies_from_browser(session, browser)
    print(f"   ✅ Cookie 刷新完成，同步数量: {count}")


def is_cookie_related_error(err: Exception) -> bool:
    if isinstance(err, requests.HTTPError):
        status = getattr(err.response, "status_code", None)
        return status in (401, 403, 412)
    return False


def build_unique_key(institute_name: str, notice_type: str, title: str, disclose_date: str) -> str:
    # TODO[手动修改]：若后续决定 unique_key 改规则，可在此统一调整
    return f"{institute_name}+{notice_type}+{sanitize_text(title, 120)}+{disclose_date}"


def build_base_filename(institute_name: str, product_name: str, notice_type: str, sales_code: str) -> str:
    parts = [
        sanitize_text(institute_name, 60),
        sanitize_text(product_name, 120),
        sanitize_text(notice_type, 40),
    ]

    clean_parts = []
    for part in parts:
        value = str(part or "").strip()
        if value and value not in {"未命名", "未识别产品名"}:
            clean_parts.append(value)

    safe_sales_code = str(sales_code or "").strip()
    if safe_sales_code and safe_sales_code not in {"未命名", "未识别产品名"}:
        clean_parts.append(sanitize_text(safe_sales_code, 60))

    if not clean_parts:
        clean_parts = [sanitize_text(institute_name, 60), sanitize_text(notice_type, 40)]

    parts = clean_parts
    return "_".join(parts)


def build_unique_save_path(folder: str, base_name: str, extension: str):
    ext = extension if extension.startswith(".") else f".{extension}"
    ext = ext.lower()
    idx = 0
    while True:
        file_name = f"{base_name}{ext}" if idx == 0 else f"{base_name}_{idx}{ext}"
        save_path = os.path.join(folder, file_name)
        if not os.path.exists(save_path):
            return save_path, file_name
        idx += 1


def _log_header():
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
    download_time = now_time_str()
    log_to_csv(
        institute_name=institute_name,
        notice_title=notice_title,
        notice_type=notice_type,
        disclose_date=disclose_date,
        download_time=download_time,
        status=status,
        source_link=source_link,
        save_path=save_path,
        unique_key=unique_key,
    )


def load_downloaded_links() -> set:
    links = set()

    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                link = line.strip()
                if link:
                    links.add(link)

    if os.path.exists(LOG_CSV_PATH):
        try:
            with open(LOG_CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() == "SUCCEED":
                        src = str(row.get("来源链接") or "").strip()
                        if src:
                            links.add(src)
        except Exception:
            pass

    return links


def save_downloaded_link(source_link: str):
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(source_link + "\n")


def fetch_json_with_retry(session: requests.Session, browser: ChromiumPage, url: str, name: str):
    last_error = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            resp = session.get(url, headers=build_headers(), timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as err:
            last_error = err
            if attempt < REQUEST_RETRY:
                if is_cookie_related_error(err):
                    refresh_session_cookies(session, browser, reason=f"获取 {name} 失败")
                print(f"    🔁 [请求重试] {name} 第{attempt}次失败，{RETRY_WAIT_SECONDS}s 后重试")
                time.sleep(RETRY_WAIT_SECONDS)
            else:
                print(f"    ❌ [请求失败] {name} | {err}")
    return None


def normalize_direct_item(item: dict, notice_type: str) -> dict:
    title = str(item.get("FILENAME") or item.get("title") or "").strip()
    href = str(item.get("HREF") or item.get("href") or item.get("outline") or "").strip()
    pubdate = str(item.get("PUBDATE") or item.get("date") or "").strip()

    if notice_type == "封闭式公募理财产品报告":
        if not title:
            title = str(item.get("title") or "").strip()
        if not href:
            href = str(item.get("outline") or "").strip()

    return {
        "FILENAME": title,
        "PUBDATE": pubdate,
        "HREF": href,
        "TYPE": str(item.get("TYPE") or "").strip(),
        "attribute": str(item.get("attribute") or "").strip(),
        "PDFFILE": str(item.get("PDFFILE") or "").strip(),
    }


def get_master_and_complement_data(session: requests.Session, browser: ChromiumPage):
    master = []
    complement = []

    master_json = fetch_json_with_retry(session, browser, MASTER_JSON_URL, "主数据XXPL.json")
    if isinstance(master_json, dict):
        data = master_json.get("data") or []
        if isinstance(data, list):
            master = data

    complement_json = fetch_json_with_retry(session, browser, COMPLEMENT_JSON_URL, "补充数据index.json")
    if isinstance(complement_json, dict):
        data = complement_json.get("data") or []
        if isinstance(data, list):
            complement = data

    return master, complement


def collect_module_items(
    session: requests.Session,
    browser: ChromiumPage,
    module_conf: dict,
    master_data: list,
    complement_data: list,
):
    notice_type = module_conf["notice_type"]
    data = []

    direct_json_url = str(module_conf.get("direct_json_url") or "").strip()
    if direct_json_url:
        direct_json = fetch_json_with_retry(session, browser, direct_json_url, f"{notice_type}栏目JSON")
        if isinstance(direct_json, dict):
            rows = direct_json.get("data") or []
            if isinstance(rows, list):
                for item in rows:
                    data.append(normalize_direct_item(item, notice_type))

    type_codes = {str(x) for x in (module_conf.get("type_codes") or [])}
    if type_codes:
        for src in (complement_data or []):
            src_type = str(src.get("TYPE") or "").strip()
            if src_type in type_codes:
                data.append(src)
        for src in (master_data or []):
            src_type = str(src.get("TYPE") or "").strip()
            if src_type in type_codes:
                data.append(src)

    dedup = {}
    for item in data:
        key = str(item.get("HREF") or item.get("PDFFILE") or item.get("attribute") or "").strip()
        if not key:
            key = f"{item.get('FILENAME', '')}|{item.get('PUBDATE', '')}"
        old = dedup.get(key)
        if old is None or pubdate_to_sortable_int(item.get("PUBDATE")) > pubdate_to_sortable_int(old.get("PUBDATE")):
            dedup[key] = item

    merged = list(dedup.values())
    merged.sort(key=lambda x: pubdate_to_sortable_int(x.get("PUBDATE")), reverse=True)
    return merged


def build_source_link(item: dict, notice_type: str) -> str:
    source = ""

    item_type = str(item.get("TYPE") or "").strip()
    attr = str(item.get("attribute") or "").strip()
    pdffile = str(item.get("PDFFILE") or "").strip()

    if item_type:
        if attr:
            source = attr
        elif pdffile:
            source = urljoin("https://www.spdb-wm.com/financialProducts/XXPL/", pdffile)

    if not source:
        href = str(item.get("HREF") or item.get("href") or "").strip()
        if href:
            if href.startswith("http://") or href.startswith("https://"):
                source = href
            elif notice_type == "封闭式公募理财产品报告":
                source = urljoin("https://www.spdb-wm.com/xxpl/fblcgg/", href)
            else:
                source = urljoin("https://www.spdb-wm.com/", href)

    return source.strip()


def render_html_to_pdf(browser: ChromiumPage, page_url: str, save_path: str):
    browser.get(page_url)
    time.sleep(2.0)

    browser.run_cdp("Emulation.setEmulatedMedia", media="screen")

    result = browser.run_cdp(
        "Page.printToPDF",
        printBackground=True,
        preferCSSPageSize=True,
        marginTop=0.4,
        marginBottom=0.4,
        marginLeft=0.35,
        marginRight=0.35,
    )

    import base64

    pdf_bytes = base64.b64decode(result["data"])
    with open(save_path, "wb") as f:
        f.write(pdf_bytes)


def extract_document_link_from_html(html_text: str, base_url: str) -> str:
    # 若详情页里有明确文件链接，优先直连下载原始文件（pdf/doc/docx等）
    pattern = r"href\s*=\s*[\"']([^\"']+\.(?:pdf|doc|docx|xls|xlsx|zip))(?:\?[^\"']*)?[\"']"
    match = re.search(pattern, html_text, re.IGNORECASE)
    if not match:
        return ""
    href = match.group(1)
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(base_url, href)


def download_file_with_fallback(
    session: requests.Session,
    browser: ChromiumPage,
    source_link: str,
    save_folder: str,
    base_file_name: str,
):
    with session.get(
        source_link,
        headers=build_headers(),
        timeout=REQUEST_TIMEOUT,
        stream=True,
        allow_redirects=True,
    ) as response:
        response.raise_for_status()
        ext = guess_extension_from_headers(response)

        if ext == ".html":
            html_text = response.text
            doc_link = extract_document_link_from_html(html_text, response.url)
            if doc_link:
                with session.get(
                    doc_link,
                    headers=build_headers(),
                    timeout=REQUEST_TIMEOUT,
                    stream=True,
                    allow_redirects=True,
                ) as doc_resp:
                    doc_resp.raise_for_status()
                    doc_ext = guess_extension_from_headers(doc_resp)
                    if doc_ext == ".html":
                        doc_ext = ".pdf"
                    save_path, file_name = build_unique_save_path(save_folder, base_file_name, doc_ext)
                    with open(save_path, "wb") as f:
                        for chunk in doc_resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    if os.path.getsize(save_path) < 128:
                        raise RuntimeError("文档下载结果过小，疑似失败")
                    return doc_resp.url, save_path, file_name

            # 页面无直接文件链接时，按要求转 PDF
            save_path, file_name = build_unique_save_path(save_folder, base_file_name, ".pdf")
            render_html_to_pdf(browser, response.url, save_path)
            if os.path.getsize(save_path) < 1024:
                raise RuntimeError("HTML 转 PDF 结果过小，疑似失败")
            return response.url, save_path, file_name

        save_path, file_name = build_unique_save_path(save_folder, base_file_name, ext)
        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        if os.path.getsize(save_path) < 128:
            raise RuntimeError("下载文件过小，疑似失败")

        return response.url, save_path, file_name


def resolve_modules_to_run() -> list:
    result = []

    if RUN_ONLY_MODULE_KEYS:
        for key in RUN_ONLY_MODULE_KEYS:
            if key in MODULES:
                result.append(key)
                continue
            mapped = CHINESE_NAME_TO_KEY.get(key)
            if mapped and mapped in MODULES:
                result.append(mapped)
                continue
            print(f"[警告] RUN_ONLY_MODULE_KEYS 存在未知模块: {key}")
        return result

    for cname, enabled in ENABLE_MODULES.items():
        if not enabled:
            continue
        mapped = CHINESE_NAME_TO_KEY.get(cname)
        if mapped and mapped in MODULES:
            result.append(mapped)
        else:
            if cname in MODULES:
                result.append(cname)
            else:
                print(f"[警告] ENABLE_MODULES 存在未知模块: {cname}")

    return result


def process_one_notice(
    session: requests.Session,
    browser: ChromiumPage,
    module_conf: dict,
    item: dict,
    downloaded_links: set,
):
    notice_type = module_conf["notice_type"]
    folder_path = os.path.join(DOWNLOAD_ROOT, module_conf["folder"])
    ensure_dir(folder_path)

    title = str(item.get("FILENAME") or item.get("title") or "").strip() or "未命名公告"
    disclose_date = normalize_date(str(item.get("PUBDATE") or item.get("date") or ""))

    _in_range, _too_old = is_in_date_range(disclose_date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"   ⏭️ [日期跳过] 披露日期 {disclose_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return "too_old" if _too_old else "skip"

    source_link = build_source_link(item, notice_type)

    product_name, sales_code = parse_product_name_and_code(title)
    base_file_name = build_base_filename(INSTITUTE_NAME, product_name, notice_type, sales_code)
    unique_key = build_unique_key(INSTITUTE_NAME, notice_type, title, disclose_date)

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
        print(f"    ❌ [下载失败] 缺少来源链接: {title}")
        return "failed"

    if SKIP_DOWNLOADED and source_link in downloaded_links:
        print(f"    ⏭️ [跳过] 已下载过：{title}")
        return "skipped"

    expected_save_path, _ = build_unique_save_path(folder_path, base_file_name, ".pdf")

    last_err = None
    for attempt in range(1, DOWNLOAD_RETRY + 1):
        try:
            print(f"    ⬇️ [开始下载] {title}")
            final_url, save_path, file_name = download_file_with_fallback(
                session=session,
                browser=browser,
                source_link=source_link,
                save_folder=folder_path,
                base_file_name=base_file_name,
            )

            downloaded_links.add(source_link)
            save_downloaded_link(source_link)

            log_download_result(
                institute_name=INSTITUTE_NAME,
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="SUCCEED",
                source_link=final_url or source_link,
                save_path=os.path.abspath(save_path),
                unique_key=unique_key,
            )
            print(f"    ✅ [下载成功] {file_name}")
            return "succeed"
        except Exception as err:
            last_err = err
            if attempt < DOWNLOAD_RETRY:
                if is_cookie_related_error(err):
                    refresh_session_cookies(
                        session=session,
                        browser=browser,
                        reason=f"下载失败触发重试: {title[:30]}",
                    )
                print(f"    🔁 [下载重试] {title} 第{attempt}次失败，{RETRY_WAIT_SECONDS}s 后重试")
                time.sleep(RETRY_WAIT_SECONDS)
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
                print(f"    ❌ [下载失败] {title} | {last_err}")
                return "failed"


def crawl_one_module(
    session: requests.Session,
    browser: ChromiumPage,
    module_key: str,
    downloaded_links: set,
    master_data: list,
    complement_data: list,
    checkpoint: dict = None,
):
    conf = MODULES[module_key]
    notice_type = conf["notice_type"]

    print("\n" + "=" * 70)
    print(f"🚀 开始抓取模块：{notice_type}（{module_key}）")
    print("=" * 70)

    items = collect_module_items(
        session=session,
        browser=browser,
        module_conf=conf,
        master_data=master_data,
        complement_data=complement_data,
    )

    if TEST_ONE_NOTICE_MODE and TEST_NOTICE_KEYWORD.strip():
        kw = TEST_NOTICE_KEYWORD.strip().lower()
        filtered = []
        for item in items:
            title = str(item.get("FILENAME") or "").lower()
            src = build_source_link(item, notice_type).lower()
            if kw in title or kw in src:
                filtered.append(item)
        items = filtered[:1]
        print(f"🧪 [单条测试] 关键字={TEST_NOTICE_KEYWORD}，命中={len(items)}")

    if not items:
        print(f"⚪ [模块空数据] {notice_type} 没有可处理公告")
        return 0, 0, 0, 0

    found = 0
    succeed = 0
    failed = 0
    skipped = 0
    early_stopped = False

    start_item_index = 0
    if checkpoint is not None:
        mod_state = checkpoint.get("module_items", {}).get(module_key)
        if mod_state and not mod_state.get("done"):
            start_item_index = int(mod_state.get("item_index", 0))
            if start_item_index > 0:
                print(f"  [断点] 从第 {start_item_index + 1} 条续传")

    global_idx = 0
    pages = [items[i:i + LOCAL_PAGE_SIZE] for i in range(0, len(items), LOCAL_PAGE_SIZE)]
    for page_no, page_items in enumerate(pages, start=1):
        page_succeed = 0
        page_failed = 0
        page_skipped = 0

        print(f"\n📄 正在处理第 {page_no}/{len(pages)} 页（本地分页），共 {len(page_items)} 条")
        for idx, item in enumerate(page_items, start=1):
            if global_idx < start_item_index:
                global_idx += 1
                continue

            title = str(item.get("FILENAME") or item.get("title") or "").strip() or "未命名公告"
            print(f"  📌 [文件{idx}/{len(page_items)}] {title}")

            _publish_date = normalize_date(str(item.get("PUBDATE") or item.get("date") or ""))
            _in_range, _too_old = is_in_date_range(_publish_date)
            if not _in_range:
                skipped += 1
                page_skipped += 1
                if _too_old:
                    print(f"      ⏭️ [日期跳过] {title} 披露日期 {_publish_date} < {START_DATE}")
                    if EARLY_STOP:
                        print(f"      ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stopped = True
                        break
                else:
                    print(f"      ⏭️ [日期跳过] {title} 披露日期 {_publish_date} > {END_DATE or '今天'}")
                global_idx += 1
                continue

            found += 1

            result = process_one_notice(
                session=session,
                browser=browser,
                module_conf=conf,
                item=item,
                downloaded_links=downloaded_links,
            )

            if result == "succeed":
                succeed += 1
                page_succeed += 1
            elif result in ("skipped", "skip"):
                skipped += 1
                page_skipped += 1
            elif result == "too_old":
                skipped += 1
                page_skipped += 1
                if EARLY_STOP:
                    early_stopped = True
                    break
            else:
                failed += 1
                page_failed += 1

            if checkpoint is not None:
                checkpoint.setdefault("module_items", {})[module_key] = {"item_index": global_idx + 1, "done": False}
                checkpoint["current_module"] = module_key
                checkpoint["updated_at"] = now_str()
                save_checkpoint(CHECKPOINT_FILE, checkpoint)

            global_idx += 1
            random_sleep(REQUEST_INTERVAL_SECONDS)

        if early_stopped:
            break

        if page_succeed == 0 and page_skipped == 0 and page_failed > 0:
            print(f"  ⏳ [等待] 该页未成功下载，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
            time.sleep(PAGE_NO_FILE_WAIT_SECONDS)

        print(f"  📊 [第{page_no}页汇总] 成功={page_succeed} 失败={page_failed} 跳过={page_skipped}")
        random_sleep(PAGE_INTERVAL_SECONDS)

    print(f"✅ [模块完成] {notice_type} | 发现={found} 成功={succeed} 失败={failed} 跳过={skipped}")

    if checkpoint is not None:
        checkpoint.setdefault("module_items", {})[module_key] = {"item_index": len(items), "done": True}
        completed = checkpoint.setdefault("completed_modules", [])
        if module_key not in completed:
            completed.append(module_key)
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)

    return found, succeed, failed, skipped


def prepare_directories():
    ensure_dir(DOWNLOAD_ROOT)
    for module in MODULES.values():
        ensure_dir(os.path.join(DOWNLOAD_ROOT, module["folder"]))


def crawl():
    prepare_directories()
    downloaded_links = load_downloaded_links()
    modules = resolve_modules_to_run()

    checkpoint = load_checkpoint(CHECKPOINT_FILE)
    completed_modules = checkpoint.get("completed_modules", []) if checkpoint else []
    print(f"断点续传：已完成模块 {len(completed_modules)} 个")

    if not modules:
        print("⚠️ 未选择任何可运行模块，程序结束。")
        return

    print(f"📚 已记录下载链接数: {len(downloaded_links)}")
    print(f"🧭 本次执行模块顺序: {', '.join([KEY_TO_CHINESE.get(m, m) for m in modules])}")

    session = requests.Session()
    session.headers.update({"user-agent": random.choice(USER_AGENTS)})

    browser = _new_page()
    try:
        warmup_and_sync_cookies(session=session, browser=browser)

        master_data, complement_data = get_master_and_complement_data(session=session, browser=browser)
        print(f"🧾 主数据条数: {len(master_data)} | 补充数据条数: {len(complement_data)}")

        total_found = 0
        total_succeed = 0
        total_failed = 0
        total_skipped = 0

        try:
            for module_key in modules:
                if module_key in completed_modules:
                    print(f"⏭️ [断点] 跳过已完成模块 {KEY_TO_CHINESE.get(module_key, module_key)}")
                    continue
                try:
                    found, succeed, failed, skipped = crawl_one_module(
                        session=session,
                        browser=browser,
                        module_key=module_key,
                        downloaded_links=downloaded_links,
                        master_data=master_data,
                        complement_data=complement_data,
                        checkpoint=checkpoint,
                    )
                    total_found += found
                    total_succeed += succeed
                    total_failed += failed
                    total_skipped += skipped
                except Exception as err:
                    print(f"❌ [模块异常] {module_key} 处理异常，但程序继续: {err}")
        except KeyboardInterrupt:
            print("\n[中断] 收到 Ctrl+C，断点已保存，下次运行将从断点处继续")
            raise

        print("\n" + "=" * 70)
        print("🎉 全部模块处理完成")
        print(f"📈 总发现: {total_found}")
        print(f"✅ 总成功: {total_succeed}")
        print(f"❌ 总失败: {total_failed}")
        print(f"⏭️ 总跳过: {total_skipped}")
        print(f"📁 下载目录: {os.path.abspath(DOWNLOAD_ROOT)}")
        print(f"🧾 CSV日志: {os.path.abspath(LOG_CSV_PATH)}")
        print("=" * 70)
    finally:
        print("🛑 关闭浏览器会话")
        browser.quit()


if __name__ == "__main__":
    crawl()
