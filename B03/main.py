
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
from urllib.parse import urlparse

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
INSTITUTE_NAME = "中信理财"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, "中信理财_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")

BASE_SEARCH_URL = "https://www.citic-wealth.com/was5/web/search"
BASE_DOCUMENT_URL = "https://www.citic-wealth.com/was5/web/document?columnname=file_path_new&multino=1&downloadtype=open&channelid="
HOME_URL = "https://www.citic-wealth.com/"
DISCLOSURE_REFERER = "https://www.citic-wealth.com/yymk/xxpl/"

# TODO[手动修改]：模块开关（使用中文名称，True=开启，False=关闭）
# 使用中文配置更方便：键为中文公告类型名称，值为是否启用
ENABLE_MODULES = {
    "发行公告": False,
    "产品净值公告": False,
    "产品定期公告": False,
    "分红公告": False,
    "产品到期报告": False,
    "其他产品公告": True,
    "产品说明书": True,
    "风险揭示书": False,
    "投资协议书": False,
}

# TODO[手动修改]：单模块测试名单（使用中文名称）；为空时按 ENABLE_MODULES 执行全部开启模块
# 示例：RUN_ONLY_MODULE_KEYS = ["发行公告"]
RUN_ONLY_MODULE_KEYS = []  # 这里可以直接使用中文名称，也可以使用内部键（不推荐）

# 中文名称到内部模块键的映射（无需修改）
CHINESE_NAME_TO_KEY = {
    "发行公告": "issue_notice",
    "产品净值公告": "nav_notice",
    "产品定期公告": "periodic_notice",
    "分红公告": "dividend_notice",
    "产品到期报告": "maturity_notice",
    "其他产品公告": "other_notice",
    "产品说明书": "prospectus",
    "风险揭示书": "risk_disclosure",
    "投资协议书": "investment_agreement",
}

# 内部键到中文名（便于打印显示）
KEY_TO_CHINESE = {v: k for k, v in CHINESE_NAME_TO_KEY.items()}

# TODO[手动修改]：访问节奏和重试参数（不同站点可以调整）
REQUEST_TIMEOUT = 45
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
REQUEST_INTERVAL_SECONDS = (0.8, 1.6)
PAGE_INTERVAL_SECONDS = (1.2, 2.0)
PAGE_NO_FILE_WAIT_SECONDS = 10
MAX_PAGES_PER_MODULE = 3000
MAX_CONSECUTIVE_EMPTY_PAGE = 2

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

MODULES = {
    "issue_notice": {"notice_type": "发行公告", "channelid": 266663, "folder": "发行公告"},
    "nav_notice": {"notice_type": "产品净值公告", "channelid": 281355, "folder": "产品净值公告"},
    "periodic_notice": {"notice_type": "产品定期公告", "channelid": 267968, "folder": "产品定期公告"},
    "dividend_notice": {"notice_type": "分红公告", "channelid": 228929, "folder": "分红公告"},
    "maturity_notice": {"notice_type": "产品到期报告", "channelid": 282695, "folder": "产品到期报告"},
    "other_notice": {"notice_type": "其他产品公告", "channelid": 218216, "folder": "其他产品公告"},
    "prospectus": {"notice_type": "产品说明书", "channelid": 204182, "folder": "产品说明书"},
    "risk_disclosure": {"notice_type": "风险揭示书", "channelid": 294143, "folder": "风险揭示书"},
    "investment_agreement": {"notice_type": "投资协议书", "channelid": 218562, "folder": "投资协议书"},
}

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
    START_DATE = PROJECT_START_DATE.get("B03", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("B03", True)
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


# =====================================
# 通用工具
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
        return False, True      # 过早 → 可触发早停
    if d > upper:
        return False, False     # 过晚 → 仅跳过
    return True, False



def now_time_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



def parse_product_name_and_code(title: str):
    cleaned_title = sanitize_text(title, max_len=240)
    code_match = re.search(r"产品代码[：:]\s*([A-Za-z0-9_-]+)", cleaned_title)
    sales_code = code_match.group(1).strip() if code_match else ""

    product_name = re.sub(r"[（(]\s*产品代码[：:][^)）]+[)）]", "", cleaned_title)
    product_name = re.sub(r"公告\d*$", "公告", product_name)
    product_name = product_name.strip(" _-")
    if not product_name:
        product_name = "未识别产品名"
    safe_sales_code = sanitize_text(sales_code, max_len=60) if sales_code else ""
    return sanitize_text(product_name, max_len=120), safe_sales_code



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
        "accept": "application/json, text/javascript, */*; q=0.01",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "origin": "https://www.citic-wealth.com",
        "referer": DISCLOSURE_REFERER,
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
    print("🍪 正在预热页面并获取 Cookie...")
    warmup_urls = [HOME_URL, DISCLOSURE_REFERER]
    for url in warmup_urls:
        print(f"   🌐 访问: {url}")
        browser.get(url)
        time.sleep(2)
    count = sync_session_cookies_from_browser(session, browser)
    print(f"   ✅ Cookie 同步完成，当前同步数量: {count}")


def refresh_session_cookies(session: requests.Session, browser: ChromiumPage, reason: str = ""):
    reason_text = f"（原因: {reason}）" if reason else ""
    print(f"🍪 正在刷新 Cookie {reason_text}")
    browser.get(DISCLOSURE_REFERER)
    time.sleep(2)
    count = sync_session_cookies_from_browser(session, browser)
    print(f"   ✅ Cookie 刷新完成，同步数量: {count}")


def is_cookie_related_error(err: Exception) -> bool:
    if isinstance(err, requests.HTTPError):
        status = getattr(err.response, "status_code", None)
        return status in (401, 403, 412)
    return False



def build_document_url(url_part: str) -> str:
    return BASE_DOCUMENT_URL + str(url_part or "").strip()



def build_unique_key(institute_name: str, notice_type: str, title: str, disclose_date: str) -> str:
    return f"{institute_name}|{notice_type}|{sanitize_text(title, 120)}|{disclose_date}"



def build_base_filename(
    institute_name: str,
    product_name: str,
    notice_type: str,
    sales_code: str,
    disclose_date: str,
) -> str:
    parts = [sanitize_text(institute_name, 60), sanitize_text(product_name, 120), sanitize_text(notice_type, 40)]
    if sales_code:
        parts.append(sanitize_text(sales_code, 60))
    parts.append(sanitize_text(disclose_date, 20))
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
            writer.writerow(header)
        writer.writerow(row)



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



def fetch_list_page(session: requests.Session, browser: ChromiumPage, channelid: int, page: int):
    params = {
        "channelid": str(channelid),
        "page": str(page),
        "searchword": "",
    }

    url = BASE_SEARCH_URL
    last_error = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            response = session.post(
                url,
                params=params,
                data="",
                headers=build_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except Exception as err:
            last_error = err
            if attempt < REQUEST_RETRY:
                if is_cookie_related_error(err):
                    refresh_session_cookies(
                        session=session,
                        browser=browser,
                        reason=f"列表接口第{page}页返回疑似风控",
                    )
                print(f"    🔁 [列表重试] 第{page}页，第{attempt}次失败，{RETRY_WAIT_SECONDS}s 后重试")
                time.sleep(RETRY_WAIT_SECONDS)
            else:
                raise last_error



def render_html_to_pdf(browser: ChromiumPage, page_url: str, save_path: str):
    browser.get(page_url)
    time.sleep(2.0)
    result = browser.run_cdp(
        "Page.printToPDF",
        printBackground=True,
        preferCSSPageSize=True,
        marginTop=0.4,
        marginBottom=0.4,
        marginLeft=0.35,
        marginRight=0.35,
    )
    # 采用标准 base64 解码，避免额外依赖
    import base64

    pdf_bytes = base64.b64decode(result["data"])
    with open(save_path, "wb") as f:
        f.write(pdf_bytes)



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

        # 遇到 HTML 页面时，按要求转 PDF 下载
        if ext == ".html":
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

    title = str(item.get("title") or "").strip() or "未命名公告"
    disclose_date = normalize_date(str(item.get("date") or ""))

    _in_range, _too_old = is_in_date_range(disclose_date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"    ⏭️ [日期跳过] 披露日期 {disclose_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return "skipped"

    url_part = str(item.get("url") or "").strip()
    source_link = build_document_url(url_part)

    product_name, sales_code = parse_product_name_and_code(title)
    base_file_name = build_base_filename(
        INSTITUTE_NAME,
        product_name,
        notice_type,
        sales_code,
        disclose_date,
    )
    unique_key = build_unique_key(INSTITUTE_NAME, notice_type, title, disclose_date)

    if source_link in downloaded_links:
        print(f"    ⏭️ [跳过] 已下载过：{title}")
        return "skipped"

    expected_save_path, _ = build_unique_save_path(folder_path, base_file_name, ".pdf")

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
            log_to_csv(
                institute_name=INSTITUTE_NAME,
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                download_time=now_time_str(),
                status="SUCCEED",
                source_link=final_url or source_link,
                save_path=os.path.abspath(save_path),
                unique_key=unique_key,
            )
            print(f"    ✅ [下载成功] {file_name}")
            return "succeed"
        except Exception as err:
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
                log_to_csv(
                    institute_name=INSTITUTE_NAME,
                    notice_title=title,
                    notice_type=notice_type,
                    disclose_date=disclose_date,
                    download_time=now_time_str(),
                    status="FAILED",
                    source_link=source_link,
                    save_path=os.path.abspath(expected_save_path),
                    unique_key=unique_key,
                )
                print(f"    ❌ [下载失败] {title} | {err}")
                return "failed"



def resolve_modules_to_run() -> list:
    result = []

    # 如果指定了单模块测试名单，优先使用
    if RUN_ONLY_MODULE_KEYS:
        for key in RUN_ONLY_MODULE_KEYS:
            # 支持用户直接写内部键或中文名
            if key in MODULES:
                result.append(key)
                continue
            if key in CHINESE_NAME_TO_KEY:
                mapped = CHINESE_NAME_TO_KEY[key]
                if mapped in MODULES:
                    result.append(mapped)
                    continue
            print(f"[警告] RUN_ONLY_MODULE_KEYS 存在未知模块: {key}")
        return result

    # 否则根据中文配置的 ENABLE_MODULES 开关来生成要跑的模块
    for cname, enabled in ENABLE_MODULES.items():
        if not enabled:
            continue
        mapped = CHINESE_NAME_TO_KEY.get(cname)
        if mapped and mapped in MODULES:
            result.append(mapped)
        else:
            # 兼容用户也可能直接填写内部键到 ENABLE_MODULES（不推荐）
            if cname in MODULES:
                result.append(cname)
            else:
                print(f"[警告] ENABLE_MODULES 存在未知模块: {cname}")

    return result



def crawl_one_module(session: requests.Session, browser: ChromiumPage, module_key: str, downloaded_links: set, checkpoint: dict = None):
    conf = MODULES[module_key]
    notice_type = conf["notice_type"]
    channelid = conf["channelid"]

    print("\n" + "=" * 70)
    print(f"🚀 开始抓取模块：{notice_type}（{module_key}，channelid={channelid}）")
    print("=" * 70)

    found = 0
    succeed = 0
    failed = 0
    skipped = 0
    consecutive_empty = 0
    early_stop_triggered = False

    # 断点续传：读取上次爬到的页码
    start_page = 1
    if checkpoint is not None:
        mod_cp = checkpoint.get("module_pages", {}).get(module_key, {})
        start_page = int(mod_cp.get("page", 1))
        if mod_cp.get("done"):
            print(f"[断点续传] {module_key} 已完成，跳过")
            return 0, 0, 0, 0
        if start_page > 1:
            print(f"[断点续传] {module_key} 从第 {start_page} 页继续")

    for page in range(start_page, MAX_PAGES_PER_MODULE + 1):
        try:
            print(f"\n📄 正在抓取第 {page} 页...")
            payload = fetch_list_page(session=session, browser=browser, channelid=channelid, page=page)
        except Exception as err:
            print(f"  ❌ [页失败] 第{page}页请求异常: {err}")
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGE:
                print("  ⛔ [结束] 连续空页/失败达到阈值")
                break
            time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
            continue

        if str(payload.get("msg") or "").lower() != "success":
            print(f"  ❌ [页失败] 第{page}页接口返回异常，msg={payload.get('msg')}")
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGE:
                print("  ⛔ [结束] 连续空页/失败达到阈值")
                break
            time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
            continue

        data = payload.get("data") or []
        if not data:
            consecutive_empty += 1
            print(f"  ⚪ [空页] 第{page}页无数据，连续空页={consecutive_empty}/{MAX_CONSECUTIVE_EMPTY_PAGE}")
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGE:
                print("  ⛔ [结束] 连续空页达到阈值")
                break
            time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
            continue

        consecutive_empty = 0
        page_succeed = 0
        page_skipped = 0
        page_failed = 0

        print(f"  🧾 [页处理] 第{page}页，共 {len(data)} 条")
        for idx, item in enumerate(data, start=1):
            title = str(item.get("title") or "").strip() or "未命名公告"
            _disclose_date = normalize_date(str(item.get("date") or ""))
            _in_range, _too_old = is_in_date_range(_disclose_date)
            if not _in_range:
                skipped += 1
                page_skipped += 1
                if _too_old:
                    print(f"  ⏭️ [日期跳过] {title} 披露日期 {_disclose_date} < {START_DATE}")
                    if EARLY_STOP:
                        print(f"  ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stop_triggered = True
                        break
                else:
                    print(f"  ⏭️ [日期跳过] {title} 披露日期 {_disclose_date} > {END_DATE or '今天'}")
                continue
            print(f"  📌 [文件{idx}/{len(data)}] {title}")
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
            elif result == "skipped":
                skipped += 1
                page_skipped += 1
            else:
                failed += 1
                page_failed += 1

            random_sleep(REQUEST_INTERVAL_SECONDS)

        # 断点续传：每页处理完后保存进度
        if checkpoint is not None:
            checkpoint.setdefault("module_pages", {})[module_key] = {"page": page + 1, "done": False}
            checkpoint["current_module"] = module_key
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)

        if early_stop_triggered:
            print(f"  ⏹️ [早停] 模块 {notice_type} 早停触发，停止翻页")
            break

        # 按要求：翻页后如果该页都没拿到可下载文件，等待约10秒再继续
        if page_succeed == 0 and page_skipped == 0 and page_failed > 0:
            print(f"  ⏳ [等待] 第{page}页未成功下载文件，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
            time.sleep(PAGE_NO_FILE_WAIT_SECONDS)

        print(
            f"  📊 [第{page}页汇总] 成功={page_succeed} 失败={page_failed} 跳过={page_skipped}"
        )
        random_sleep(PAGE_INTERVAL_SECONDS)

    print(f"✅ [模块完成] {notice_type} | 发现={found} 成功={succeed} 失败={failed} 跳过={skipped}")

    # 断点续传：模块完成后标记 done
    if checkpoint is not None:
        checkpoint.setdefault("module_pages", {})[module_key] = {"page": page, "done": True}
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

    if not modules:
        print("⚠️ 未选择任何可运行模块，程序结束。")
        return

    # 断点续传：加载 checkpoint
    checkpoint = load_checkpoint(CHECKPOINT_FILE)
    completed_modules = set(checkpoint.get("completed_modules", []))
    if completed_modules:
        print(f"[断点续传] 已完成模块：{', '.join(sorted(completed_modules))}")

    # 断点续传：跳过已完成的模块
    pending_modules = [m for m in modules if m not in completed_modules]
    if len(pending_modules) < len(modules):
        print(f"[断点续传] 跳过已完成 {len(modules) - len(pending_modules)} 个模块，本次抓取 {len(pending_modules)} 个")
    modules = pending_modules

    print(f"📚 已记录下载链接数: {len(downloaded_links)}")
    # 打印中文显示名（若有），否则显示内部键
    display_names = [KEY_TO_CHINESE.get(k, k) for k in modules]
    print(f"🧭 本次执行模块顺序: {', '.join(display_names)}")

    session = requests.Session()
    session.headers.update({"user-agent": random.choice(USER_AGENTS)})

    browser = _new_page()
    try:
        warmup_and_sync_cookies(session=session, browser=browser)

        total_found = 0
        total_succeed = 0
        total_failed = 0
        total_skipped = 0

        try:
            for module_key in modules:
                found, succeed, failed, skipped = crawl_one_module(
                    session=session,
                    browser=browser,
                    module_key=module_key,
                    downloaded_links=downloaded_links,
                    checkpoint=checkpoint,
                )
                total_found += found
                total_succeed += succeed
                total_failed += failed
                total_skipped += skipped
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
        print(f"🧾 日志文件: {os.path.abspath(LOG_CSV_PATH)}")
        print("=" * 70)
    finally:
        print("🛑 关闭浏览器会话")
        browser.quit()



if __name__ == "__main__":
    crawl()
