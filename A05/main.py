
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
from math import ceil
from datetime import datetime
from urllib.parse import urljoin, urlparse, quote

import requests
from DrissionPage import ChromiumPage, ChromiumOptions

# 2026-07-23: 改用 Edge, 避开用户正在运行的 Chrome
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_edge_path() -> str:
    for p in EDGE_PATHS:
        if os.path.exists(p):
            return p
    raise RuntimeError("未找到 Edge 浏览器")

# ╔══════════════════════════════════════════════════════════╗
# ║                      用户配置区                            ║
# ╚══════════════════════════════════════════════════════════╝

INSTITUTE_NAME = "交银理财"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 页面入口
HOME_URL = "https://www.bocommwm.cn/BankCommSite/shtml/jylc/cn/2503329/list.shtml?channelId=2503329"
PRODUCT_PAGE_URL = "https://www.bocommwm.cn/BankCommSite/jylc/cn/Product.html"
PERSONAL_LIST_URL = "https://www.bocommwm.cn/BankCommSite/shtml/jylc/cn/2503328/list.shtml?channelId=2503328"
INFO_DISCLOSURE_URL_TMPL = "https://www.bocommwm.cn/BankCommSite/infoDisclosure/infoDisclosureList.html?proCode={pro_code}"

BASE_SITE = "https://www.bocommwm.cn"
API_BASE = "https://www.bocommwm.cn/SITE"

# 公告分类映射（与站点前端 switchDisType 一致）
# 2026-07-23: 开关全开, 按 5.txt 规则后筛
ANNOUNCEMENT_TYPES = {
    "0": "成立公告",
    "1": "业绩公告",
    "2": "定期公告",
    "3": "临时公告",
    "4": "重大事项公告",
    "5": "法律文本",
    "6": "到期公告",
}

# 下载与日志
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "download_files")
PROGRESS_FILE = os.path.join(DOWNLOAD_DIR, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_DIR, "checkpoint.json")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, "交银理财_日志记录.csv")

# 是否跳过已下载记录
SKIP_DOWNLOADED = True

# 翻页与请求控制
PRODUCT_PAGE_SIZE = 15
MAX_PRODUCT_PAGES = 2000
MAX_ANNOUNCEMENT_PAGES = 1500
MAX_CONSECUTIVE_EMPTY_ANNOUNCE = 2

TIMEOUT = 30
REQUEST_RETRY = 3
REQUEST_DELAY_RANGE = (0.25, 0.8)
PRODUCT_PAGE_DELAY_RANGE = (0.35, 1.0)
ANNOUNCEMENT_PAGE_DELAY_RANGE = (0.25, 0.8)

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
    ),
]

# ╚══════════════════════════════════════════════════════════╝

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
    START_DATE = PROJECT_START_DATE.get("A05", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("A05", True)
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
# START_DATE = "2026-03-30"
# END_DATE   = "2026-06-30"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def random_sleep(delay_range):
    time.sleep(random.uniform(delay_range[0], delay_range[1]))


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name or "")
    name = re.sub(r"\s+", " ", name)
    return name.strip()[:180] or "未命名"


def normalize_date(date_text: str) -> str:
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", date_text or "")
    if not m:
        return "未知日期"
    y, mm, dd = m.group(1), int(m.group(2)), int(m.group(3))
    return f"{y}-{mm:02d}-{dd:02d}"


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
    date: str,
    download_time: str,
    status: str,
    source_url: str,
    save_path: str,
):
    unique_key = source_url
    row = [
        institute_name,
        title,
        topic_type,
        date,
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

    exists = os.path.exists(LOG_CSV_PATH)
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow(row)


def build_unique_file_path(
    institute_name: str,
    date_text: str,
    topic_type: str,
    title: str,
    ext: str,
):
    base_title = sanitize_filename(title)
    topic_part = sanitize_filename(topic_type)
    ext = ext if ext.startswith(".") else f".{ext}"
    suffix = None

    while True:
        if suffix is None:
            filename = f"{institute_name}_{date_text}_{topic_part}_{base_title}{ext}"
        else:
            filename = f"{institute_name}_{date_text}_{topic_part}_{base_title}_{suffix}{ext}"

        filepath = os.path.join(DOWNLOAD_DIR, filename)
        if not os.path.exists(filepath):
            return filepath, filename
        suffix = 1 if suffix is None else suffix + 1


def get_browser_cookies(browser: ChromiumPage) -> dict:
    print("打开首页获取 Cookie...")
    browser.get(HOME_URL)
    time.sleep(3)
    browser.get(PERSONAL_LIST_URL)
    time.sleep(2)
    cookies = {c.get("name"): c.get("value") for c in browser.cookies() if c.get("name")}
    print(f"  获取到 Cookie 数量：{len(cookies)}")
    return cookies


def build_json_headers(referer: str) -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": BASE_SITE,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }


def build_file_headers(referer: str) -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
        "Origin": BASE_SITE,
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
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


def make_edge_browser() -> ChromiumPage:
    """启动一个独立 user-data-dir 的 Edge 实例, 不与用户正在运行的 Chrome 冲突"""
    edge_path = find_edge_path()
    opts = ChromiumOptions()
    opts.set_browser_path(edge_path)
    # 独立 user-data-dir, 避免和系统 Chrome / 其他 Edge 实例冲突
    user_data = os.path.join(SCRIPT_DIR, ".tmp", "edge_userdata")
    ensure_dir(user_data)
    opts.set_user_data_path(user_data)
    # 远程调试端口固定, 方便复用 (避免每次分配新端口)
    opts.set_local_port(9333)
    # 窗口直接在屏幕外打开, 避免弹窗打扰用户
    opts.set_argument('--window-position=-32000,-32000')
    _page = ChromiumPage(opts)
    try:
        _page.set.window.mini()
    except Exception:
        pass
    return _page


def refresh_cookies(session: requests.Session, browser: ChromiumPage):
    print("\n  [Cookie 刷新] 重新获取 Cookie...")
    cookies = get_browser_cookies(browser)
    session.cookies.update(cookies)


def _post_site_api_once(
    session: requests.Session,
    endpoint: str,
    req_body: dict,
    referer: str,
    timeout: int = TIMEOUT,
):
    req_message = {
        "REQ_HEAD": {"TRAN_PROCESS": "", "TRAN_ID": ""},
        "REQ_BODY": req_body,
    }
    data = {
        "REQ_MESSAGE": json.dumps(req_message, ensure_ascii=False, separators=(",", ":"))
    }

    url = f"{API_BASE}/{endpoint}"
    headers = build_json_headers(referer)
    resp = session.post(url, data=data, headers=headers, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()

    rsp_head = payload.get("RSP_HEAD", {})
    if str(rsp_head.get("TRAN_SUCCESS", "1")) != "1":
        raise RuntimeError(f"接口返回失败: {rsp_head}")

    return payload.get("RSP_BODY", {})


def post_site_api(
    session: requests.Session,
    browser: ChromiumPage,
    endpoint: str,
    req_body: dict,
    referer: str,
    timeout: int = TIMEOUT,
):
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            return _post_site_api_once(session, endpoint, req_body, referer, timeout=timeout)
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (403, 412):
                refresh_cookies(session, browser)
                random_sleep((1.0, 2.0))
                continue
            if attempt == REQUEST_RETRY:
                raise
            random_sleep((1.0, 2.0))
        except Exception:
            if attempt == REQUEST_RETRY:
                raise
            random_sleep((1.0, 2.0))


def get_product_total_count(session: requests.Session, browser: ChromiumPage) -> int:
    body = {
        "pageStart": "1",
        "pageEnd": str(PRODUCT_PAGE_SIZE),
        "product": "personal",
        "keyWord": "",
    }
    rsp = post_site_api(
        session=session,
        browser=browser,
        endpoint="queryJylcProductInfoCount.do",
        req_body=body,
        referer=PRODUCT_PAGE_URL,
    )
    total = rsp.get("result")
    try:
        return int(total)
    except Exception:
        return 0


def query_product_page(session: requests.Session, browser: ChromiumPage, page_index: int) -> list:
    page_start = 1 + (page_index - 1) * PRODUCT_PAGE_SIZE
    page_end = page_start + PRODUCT_PAGE_SIZE - 1
    body = {
        "pageStart": str(page_start),
        "pageEnd": str(page_end),
        "product": "personal",
        "keyWord": "",
    }

    rsp = post_site_api(
        session=session,
        browser=browser,
        endpoint="queryJylcProductInfo.do",
        req_body=body,
        referer=PRODUCT_PAGE_URL,
    )

    result = rsp.get("result") or []
    return result if isinstance(result, list) else []


def collect_all_products(session: requests.Session, browser: ChromiumPage) -> list:
    total = get_product_total_count(session, browser)
    if total <= 0:
        print("[产品] 总数为 0，未获取到产品。")
        return []

    pages = ceil(total / PRODUCT_PAGE_SIZE)
    pages = min(pages, MAX_PRODUCT_PAGES)

    print(f"[产品] 总数：{total}，预计页数：{pages}")

    products = []
    seen_codes = set()

    for page_index in range(1, pages + 1):
        try:
            rows = query_product_page(session, browser, page_index)
        except Exception as e:
            print(f"[产品] 第 {page_index} 页获取失败：{e}")
            continue

        print(f"[产品] 第 {page_index} 页：{len(rows)} 条")
        for row in rows:
            pro_code = str(row.get("c_fundcode") or "").strip()
            if not pro_code or pro_code in seen_codes:
                continue

            product_name = str(row.get("c_fundname") or "").strip()
            detail_path = (
                f"/BankCommSite/shtml/jylc/cn/2503328/2503403/list.shtml?"
                f"channelId=2503328&c_fundcode={pro_code}"
            )
            detail_url = urljoin(BASE_SITE, detail_path)

            products.append(
                {
                    "pro_code": pro_code,
                    "product_name": product_name,
                    "detail_url": detail_url,
                }
            )
            seen_codes.add(pro_code)

        random_sleep(PRODUCT_PAGE_DELAY_RANGE)

    print(f"[产品] 去重后共 {len(products)} 个")
    return products


def query_product_code(session: requests.Session, browser: ChromiumPage, pro_code: str) -> str:
    rsp = post_site_api(
        session=session,
        browser=browser,
        endpoint="queryJylcProductDetail.do",
        req_body={"c_fundcode": pro_code, "c_productcode": ""},
        referer=INFO_DISCLOSURE_URL_TMPL.format(pro_code=pro_code),
    )

    jylc = rsp.get("jylcProductBo") or {}
    code = str(jylc.get("c_productcode") or "").strip()
    return code


def query_info_disclosure_page(
    session: requests.Session,
    browser: ChromiumPage,
    pro_code: str,
    product_code: str,
    anno_type: str,
    page: int,
):
    body = {
        "annoType": str(anno_type),
        "page": str(page),
        "proCode": pro_code,
        "productCode": product_code,
    }

    rsp = post_site_api(
        session=session,
        browser=browser,
        endpoint="getAjaxInfoDisclosureList.do",
        req_body=body,
        referer=INFO_DISCLOSURE_URL_TMPL.format(pro_code=pro_code),
    )

    info_list = rsp.get("infoDisclosureList")

    if isinstance(info_list, str):
        info_list = info_list.strip()
        if not info_list:
            return []
        try:
            return json.loads(info_list)
        except Exception:
            return []

    if isinstance(info_list, list):
        return info_list

    return []


def query_file_download_meta(
    session: requests.Session,
    browser: ChromiumPage,
    file_id: str,
    referer: str,
):
    return post_site_api(
        session=session,
        browser=browser,
        endpoint="fileDownload.do",
        req_body={"fileId": file_id},
        referer=referer,
    )


def normalize_download_path(file_path: str) -> str:
    # 站点前端下载逻辑：将 cmsdata 替换成 BankCommSite 后作为相对路径下载
    path = (file_path or "").strip()
    if not path:
        return ""
    return path.replace("cmsdata", "BankCommSite")


def quote_url_path(url: str) -> str:
    parsed = urlparse(url)
    safe_path = quote(parsed.path, safe="/%")
    if parsed.query:
        return f"{parsed.scheme}://{parsed.netloc}{safe_path}?{parsed.query}"
    return f"{parsed.scheme}://{parsed.netloc}{safe_path}"


def download_binary(
    session: requests.Session,
    browser: ChromiumPage,
    url: str,
    save_path: str,
    referer: str,
):
    headers = build_file_headers(referer)
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            with session.get(url, headers=headers, timeout=TIMEOUT, stream=True) as resp:
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (403, 412):
                refresh_cookies(session, browser)
                headers = build_file_headers(referer)
                random_sleep((1.0, 2.0))
                continue
            if attempt == REQUEST_RETRY:
                raise
            random_sleep((1.0, 2.0))
        except Exception:
            if attempt == REQUEST_RETRY:
                raise
            random_sleep((1.0, 2.0))


def download_notice_file(
    session: requests.Session,
    browser: ChromiumPage,
    pro_code: str,
    anno_type_name: str,
    notice_item: dict,
):
    title = str(notice_item.get("TITLE") or "").strip() or "未命名公告"
    create_ts = normalize_date(str(notice_item.get("CREATE_TS") or ""))

    _in_range, _too_old = is_in_date_range(create_ts)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"    ⏭️ [日期跳过] 披露日期 {create_ts} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return False

    file_id = str(notice_item.get("DOWNLOADID") or "").strip()

    source_url = INFO_DISCLOSURE_URL_TMPL.format(pro_code=pro_code)
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not file_id:
        log_to_csv(
            institute_name=INSTITUTE_NAME,
            title=title,
            topic_type=anno_type_name,
            date=create_ts,
            download_time=now_time,
            status="FAILED",
            source_url=source_url,
            save_path="",
        )
        return False

    meta = query_file_download_meta(
        session=session,
        browser=browser,
        file_id=file_id,
        referer=source_url,
    )

    file_name = str(meta.get("fileName") or "").strip()
    file_path = normalize_download_path(str(meta.get("filePath") or "").strip())

    if not file_path:
        log_to_csv(
            institute_name=INSTITUTE_NAME,
            title=title,
            topic_type=anno_type_name,
            date=create_ts,
            download_time=now_time,
            status="FAILED",
            source_url=source_url,
            save_path="",
        )
        return False

    parsed_ext = os.path.splitext(file_name)[1].lower() if file_name else ""
    ext = parsed_ext or ".pdf"
    save_path, filename = build_unique_file_path(
        institute_name=INSTITUTE_NAME,
        date_text=create_ts,
        topic_type=anno_type_name,
        title=title,
        ext=ext,
    )

    candidates = []
    candidates.append(urljoin(BASE_SITE + "/", file_path.lstrip("/")))

    if not file_path.startswith("/"):
        candidates.append(urljoin(BASE_SITE + "/", "/" + file_path))

    raw_path = str(meta.get("filePath") or "").strip()
    if raw_path:
        candidates.append(urljoin(BASE_SITE + "/", raw_path.lstrip("/")))

    ok = False
    last_error = None

    for file_url in candidates:
        try:
            file_url = quote_url_path(file_url)
            print(f"    [下载] {filename}")
            download_binary(
                session=session,
                browser=browser,
                url=file_url,
                save_path=save_path,
                referer=source_url,
            )

            if os.path.getsize(save_path) == 0:
                raise RuntimeError("下载文件为空")

            ok = True
            break
        except Exception as e:
            last_error = e
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except OSError:
                    pass

    log_to_csv(
        institute_name=INSTITUTE_NAME,
        title=title,
        topic_type=anno_type_name,
        date=create_ts,
        download_time=now_time,
        status="SUCCEED" if ok else "FAILED",
        source_url=source_url,
        save_path=os.path.abspath(save_path) if ok else "",
    )

    if not ok and last_error:
        print(f"    [失败] {title} - {last_error}")

    return ok


def crawl_product_announcements(
    session: requests.Session,
    browser: ChromiumPage,
    product: dict,
    progress_set: set,
    checkpoint: dict = None,
):
    pro_code = product["pro_code"]
    product_name = product.get("product_name") or ""

    print(f"\n{'=' * 62}")
    print(f"产品：{pro_code} {product_name}")
    print(f"详情：{product.get('detail_url')}")
    print(f"{'=' * 62}")

    try:
        product_code = query_product_code(session, browser, pro_code)
    except Exception as e:
        print(f"  [跳过] 查询 c_productcode 失败：{e}")
        return 0, 0

    if not product_code:
        print("  [提示] c_productcode 为空，后续接口可能减少返回。")

    found = 0
    success = 0

    for anno_type, anno_type_name in ANNOUNCEMENT_TYPES.items():
        combo_key = f"{pro_code}|{anno_type}"
        if checkpoint is not None and combo_key in checkpoint.get("completed_combos", []):
            print(f"  [断点] 跳过已完成组合 {combo_key}")
            continue

        print(f"  [模块] {anno_type_name}")
        consecutive_empty = 0
        early_stop_triggered = False

        start_page = 1
        if checkpoint is not None and checkpoint.get("current_product") == pro_code and str(checkpoint.get("current_anno_type")) == anno_type:
            start_page = int(checkpoint.get("current_page", 1))
            print(f"  [断点] 从第 {start_page} 页续传")

        for page in range(start_page, MAX_ANNOUNCEMENT_PAGES + 1):
            try:
                items = query_info_disclosure_page(
                    session=session,
                    browser=browser,
                    pro_code=pro_code,
                    product_code=product_code,
                    anno_type=anno_type,
                    page=page,
                )
            except Exception as e:
                print(f"    [页 {page}] 获取失败：{e}")
                break

            if not items:
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_ANNOUNCE:
                    break
                random_sleep(ANNOUNCEMENT_PAGE_DELAY_RANGE)
                continue

            consecutive_empty = 0
            print(f"    [页 {page}] {len(items)} 条")

            for item in items:
                _disclose_date = normalize_date(str(item.get("CREATE_TS") or ""))
                _in_range, _too_old = is_in_date_range(_disclose_date)
                if not _in_range:
                    if _too_old:
                        print(f"    ⏭️ [日期跳过] {str(item.get('TITLE') or '')[:40]} 披露日期 {_disclose_date} < {START_DATE}")
                        if EARLY_STOP:
                            print(f"    ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                            early_stop_triggered = True
                            break
                    else:
                        print(f"    ⏭️ [日期跳过] {str(item.get('TITLE') or '')[:40]} 披露日期 {_disclose_date} > {END_DATE or '今天'}")
                    continue

                file_id = str(item.get("DOWNLOADID") or "").strip()
                unique_key = f"{pro_code}|{anno_type}|{file_id}"

                if SKIP_DOWNLOADED and unique_key in progress_set:
                    continue

                found += 1
                ok = download_notice_file(
                    session=session,
                    browser=browser,
                    pro_code=pro_code,
                    anno_type_name=anno_type_name,
                    notice_item=item,
                )
                if ok:
                    success += 1
                    save_progress(PROGRESS_FILE, unique_key)
                    progress_set.add(unique_key)

                random_sleep(REQUEST_DELAY_RANGE)

            if early_stop_triggered:
                print(f"    ⏹️ [早停] 模块 {anno_type_name} 早停触发，停止翻页")
                break

            if checkpoint is not None:
                checkpoint["current_product"] = pro_code
                checkpoint["current_anno_type"] = anno_type
                checkpoint["current_page"] = page + 1
                checkpoint["updated_at"] = now_str()
                save_checkpoint(CHECKPOINT_FILE, checkpoint)

            random_sleep(ANNOUNCEMENT_PAGE_DELAY_RANGE)

        if checkpoint is not None:
            completed_combos = checkpoint.setdefault("completed_combos", [])
            if combo_key not in completed_combos:
                completed_combos.append(combo_key)
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)

    if checkpoint is not None:
        completed_products = checkpoint.setdefault("completed_products", [])
        if pro_code not in completed_products:
            completed_products.append(pro_code)
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)

    return found, success


def crawl():
    ensure_dir(DOWNLOAD_DIR)

    progress_set = load_progress(PROGRESS_FILE)
    print(f"已有下载记录：{len(progress_set)} 条")

    checkpoint = load_checkpoint(CHECKPOINT_FILE)
    completed_products = checkpoint.get("completed_products", []) if checkpoint else []
    print(f"断点续传：已完成产品 {len(completed_products)} 个")

    # 2026-07-23: products 列表缓存到 checkpoint, 避免每次重启都翻 247 页
    cached_products = checkpoint.get("products", []) if checkpoint else []
    if cached_products:
        print(f"使用缓存的产品列表: {len(cached_products)} 个 (跳过 247 页 API 翻页)")

    browser = make_edge_browser()
    try:
        browser.set.window.mini()
    except Exception:
        pass
    try:
        cookies = get_browser_cookies(browser)
        session = make_session(cookies)

        if cached_products:
            products = cached_products
        else:
            products = collect_all_products(session, browser)
            if not products:
                print("未获取到产品列表，程序结束。")
                return
            # 保存到 checkpoint
            checkpoint["products"] = products
            save_checkpoint(CHECKPOINT_FILE, checkpoint)
            print(f"已缓存产品列表到 checkpoint ({len(products)} 个)")

        grand_found = 0
        grand_success = 0

        try:
            for idx, product in enumerate(products, start=1):
                pro_code = product["pro_code"]
                if pro_code in completed_products:
                    print(f"\n[{idx}/{len(products)}] 跳过已完成产品 {pro_code}")
                    continue
                print(f"\n[{idx}/{len(products)}] 处理产品 {pro_code}")
                found, success = crawl_product_announcements(
                    session=session,
                    browser=browser,
                    product=product,
                    progress_set=progress_set,
                    checkpoint=checkpoint,
                )
                grand_found += found
                grand_success += success
        except KeyboardInterrupt:
            print("\n[中断] 收到 Ctrl+C，断点已保存，下次运行将从断点处继续")
            raise

        print(f"\n{'=' * 62}")
        print("交银理财抓取完成")
        print(f"产品数量：{len(products)}")
        print(f"尝试下载：{grand_found}")
        print(f"成功下载：{grand_success}")
        print(f"下载目录：{os.path.abspath(DOWNLOAD_DIR)}")
        print(f"日志文件：{os.path.abspath(LOG_CSV_PATH)}")
        print(f"{'=' * 62}")

    finally:
        browser.quit()


if __name__ == "__main__":
    crawl()
