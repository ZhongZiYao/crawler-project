
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
import base64
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import os
import importlib
try:
    _tqdm_mod = importlib.import_module("tqdm")
    tqdm = getattr(_tqdm_mod, "tqdm", None)
except Exception:
    tqdm = None
from urllib.parse import urljoin

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

INSTITUTE_NAME = "中银理财"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://www.bocwm.cn/"
HOME_URL = BASE_URL

CATEGORY_MAP = {
    "product_notice": {
        "topic_type": "产品公告",
        "folder_name": "产品公告",
        "list_base": "https://www.bocwm.cn/html/1//198/197/",
        "kind": "detail_pdf",
    },
    "specification": {
        "topic_type": "产品说明书",
        "folder_name": "产品说明书",
        "list_base": "https://www.bocwm.cn/html/1//198/200/",
        "kind": "direct_file",
    },
    "net_worth": {
        "topic_type": "产品净值表现",
        "folder_name": "产品净值表现",
        "list_url": "https://www.bocwm.cn/html/1//198/201/index.html",
        "kind": "net_worth",
    },
    "periodic_report": {
        "topic_type": "产品定期运作报告",
        "folder_name": "产品定期运作报告",
        "list_base": "https://www.bocwm.cn/html/1//198/202/",
        "kind": "direct_file",
    },
    "other_notice": {
        "topic_type": "其他公告",
        "folder_name": "其他公告",
        "list_base": "https://www.bocwm.cn/html/1//198/234/",
        "kind": "direct_file",
    },
}
# 默认抓取全部
CATEGORY_CODES = "product_notice,specification,net_worth,periodic_report,other_notice"

# 需要抓取哪个就取消注释即可
# CATEGORY_CODES = "product_notice"             #产品公告
# CATEGORY_CODES = "specification"              #产品说明书
# CATEGORY_CODES = "net_worth"                  #产品净值表现
# CATEGORY_CODES = "periodic_report"            #产品定期运作报告
# CATEGORY_CODES = "other_notice"               #其他公告

MAX_PAGES_PER_CATEGORY = 5000
MAX_CONSECUTIVE_EMPTY = 3
MAX_NETWORTH_PAGES_PER_PRODUCT = 2000

DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, "中银理财_日志记录.csv")

SKIP_DOWNLOADED = True

TIMEOUT = 30
REQUEST_RETRY = 3
REQUEST_DELAY_RANGE = (0.5, 1.5)
LIST_PAGE_DELAY_RANGE = (0.8, 1.8)
PDF_RENDER_WAIT = 2.2
PDF_FAST_MODE = os.getenv("PDF_FAST_MODE", "0").strip() == "1"
COOKIE_READY_WAIT = 4
NETWORTH_PAGE_SIZE = int(os.getenv("NETWORTH_PAGE_SIZE", "10"))

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

COOKIE_FALLBACK_URLS = [
    HOME_URL,
    "https://www.bocwm.cn/html/1//198/197/index.html",
    "https://www.bocwm.cn/html/1//198/200/index.html",
    "https://www.bocwm.cn/html/1//198/201/index.html",
    "https://www.bocwm.cn/html/1//198/202/index.html",
    "https://www.bocwm.cn/html/1//198/234/index.html",
]

ALLOWED_DIRECT_EXT = {
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

NETWORTH_SYSTEM_KEYS = {
    "createBy",
    "createDate",
    "id",
    "remarks",
    "updateDate",
    "order",
    "status",
    "actResult",
    "startTime",
    "endTime",
    "instanceId",
    "defid",
    "startUserId",
    "code",
}

NETWORTH_FIELD_CN_MAP = {
    "productCode": "产品代码",
    "productName": "产品名称",
    "productType": "产品类型",
    "netAssetValue": "资产净值",
    "shareNetWorth": "份额净值",
    "subscriptionPrice": "申购价格",
    "applyPrice": "申购确认净值",
    "redemptionPrice": "赎回价格",
    "cumulativeNetWorth": "份额累计净值",
    "eachTenThousandProfit": "每万份收益",
    "sevenDayAnnualization": "七日年化收益率",
    "releaseDate": "净值日期",
}

# ╚══════════════════════════════════════════════════════════╝

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
    START_DATE = PROJECT_START_DATE.get("A03", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("A03", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False
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
# START_DATE = "2026-03-19"
# END_DATE   = "2026-06-30"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def random_sleep(delay_range: Tuple[float, float]):
    time.sleep(random.uniform(delay_range[0], delay_range[1]))


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", str(name or ""))
    name = re.sub(r"\s+", " ", name)
    name = name.strip().strip(".")
    return name[:180] if name else "未命名"


def normalize_date(date_text: str) -> str:
    date_text = str(date_text or "").strip()
    m = re.search(r"(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", date_text)
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


def get_file_ext_from_url(url: str, fallback: str = "") -> str:
    path = re.split(r"[?#]", url)[0]
    ext = os.path.splitext(path)[1].lower()
    if ext:
        return ext
    return fallback


def build_headers(referer: str, accept: Optional[str] = None) -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer,
    }


def make_session(cookies: Dict[str, str]) -> requests.Session:
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


def load_progress(progress_file: str) -> set:
    done = set()
    if os.path.exists(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            for line in f:
                key = line.strip()
                if key:
                    done.add(key)
    return done


def save_progress(progress_file: str, unique_key: str):
    with open(progress_file, "a", encoding="utf-8") as f:
        f.write(unique_key + "\n")


def log_to_csv(
    title: str,
    topic_type: str,
    date_text: str,
    status: str,
    source_url: str,
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
        INSTITUTE_NAME,
        title,
        topic_type,
        date_text,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        status,
        source_url,
        save_path,
        unique_key,
    ]

    exists = os.path.exists(LOG_CSV_PATH)
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow(row)


def category_dir(folder_name: str) -> str:
    path = os.path.join(DOWNLOAD_ROOT, sanitize_filename(folder_name))
    ensure_dir(path)
    return path


def build_unique_file_path(
    folder: str,
    date_text: str,
    topic_type: str,
    title: str,
    ext: str,
) -> Tuple[str, str]:
    base = (
        f"{sanitize_filename(INSTITUTE_NAME)}_"
        f"{sanitize_filename(date_text)}_"
        f"{sanitize_filename(topic_type)}_"
        f"{sanitize_filename(title)}"
    )
    suffix = None
    while True:
        filename = f"{base}{ext}" if suffix is None else f"{base}_{suffix}{ext}"
        save_path = os.path.join(folder, filename)
        if not os.path.exists(save_path):
            return save_path, filename
        suffix = 1 if suffix is None else suffix + 1


def build_networth_csv_path(folder: str, product_code: str, title: str) -> str:
    filename = (
        f"{sanitize_filename(INSTITUTE_NAME)}_"
        f"产品净值表现_"
        f"{sanitize_filename(product_code)}_"
        f"{sanitize_filename(title)}.csv"
    )
    return os.path.join(folder, filename)


# `maximize_browser_window` 已移除 —— 浏览器最大化逻辑在各处已通过更稳健的方式处理，
# 若未来需要可在此处重建相应 CDP 操作。


def get_browser_cookies(browser: ChromiumPage) -> Dict[str, str]:
    for url in COOKIE_FALLBACK_URLS:
        try:
            print(f"[Cookie] 尝试访问：{url}")
            browser.get(url)
            time.sleep(COOKIE_READY_WAIT)
            cookies = {c.get("name"): c.get("value") for c in browser.cookies() if c.get("name")}
            if cookies:
                print(f"[Cookie] 获取成功，数量：{len(cookies)}")
                return cookies
        except Exception as e:
            print(f"[Cookie] 获取失败：{url} -> {e}")

    print("[Cookie] 多入口尝试后仍未获取到 Cookie，继续以空 Cookie 运行")
    return {}


def refresh_cookies(session: requests.Session, browser: ChromiumPage):
    print("[Cookie] 刷新 Cookie...")
    cookies = get_browser_cookies(browser)
    session.cookies.clear()
    session.cookies.update(cookies)


def request_text(
    session: requests.Session,
    browser: ChromiumPage,
    url: str,
    referer: str,
    timeout: int = TIMEOUT,
) -> str:
    last_error = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            headers = build_headers(referer=referer)
            resp = session.get(url, headers=headers, timeout=timeout)
            if resp.status_code in (403, 412):
                refresh_cookies(session, browser)
                random_sleep((1.0, 2.0))
                continue
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:
            last_error = e
            if attempt < REQUEST_RETRY:
                random_sleep((1.0, 2.0))
            else:
                raise last_error


def request_binary(
    session: requests.Session,
    browser: ChromiumPage,
    url: str,
    save_path: str,
    referer: str,
):
    last_error = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            headers = build_headers(referer=referer, accept="application/octet-stream,*/*")
            with session.get(url, headers=headers, timeout=TIMEOUT, stream=True) as resp:
                if resp.status_code in (403, 412):
                    refresh_cookies(session, browser)
                    random_sleep((1.0, 2.0))
                    continue
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return
        except Exception as e:
            last_error = e
            if attempt < REQUEST_RETRY:
                random_sleep((1.0, 2.0))
            else:
                raise last_error


def request_json(
    session: requests.Session,
    browser: ChromiumPage,
    url: str,
    referer: str,
) -> Dict:
    last_error = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            headers = build_headers(referer=referer, accept="application/json,text/plain,*/*")
            resp = session.get(url, headers=headers, timeout=TIMEOUT)
            if resp.status_code in (403, 412):
                refresh_cookies(session, browser)
                random_sleep((1.0, 2.0))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            if attempt < REQUEST_RETRY:
                random_sleep((1.0, 2.0))
            else:
                raise last_error


def save_detail_html_as_pdf(browser: ChromiumPage, detail_url: str, save_path: str):
    browser.get(detail_url)

    # 等待文档 readyState 完成
    try:
        for _ in range(15):
            ready = browser.run_js("document.readyState")
            if ready and str(ready).lower() == "complete":
                break
            time.sleep(0.3)
    except Exception:
        time.sleep(1.0)

    # 分段滚动到页面底部，触发懒加载并等待高度稳定
    try:
        scroll_height = browser.run_js("return document.body.scrollHeight;")
        current_scroll = 0
        stable_round = 0
        last_height = scroll_height
        while current_scroll < scroll_height:
            browser.run_js(f"window.scrollTo(0, {current_scroll});")
            time.sleep(0.25)
            current_scroll += 700
            scroll_height = browser.run_js("return document.body.scrollHeight;")
            if scroll_height == last_height:
                stable_round += 1
            else:
                stable_round = 0
                last_height = scroll_height
            if stable_round >= 4 and current_scroll >= scroll_height:
                break
        browser.run_js("window.scrollTo(0, 0);")
    except Exception as e:
        print(f"⚠️ 滚动页面时出错: {e}")

    time.sleep(PDF_RENDER_WAIT)

    # 渲染页面为 PDF
    try:
        # 使用 screen 媒体样式，避免网站 print CSS 造成内容裁切
        try:
            browser.run_cdp("Emulation.setEmulatedMedia", media="screen")
        except Exception:
            pass

        # 展开页面中的内部滚动容器，避免只导出可见区域
        try:
            browser.run_js(
                """
                (() => {
                    const nodes = Array.from(document.querySelectorAll('*'));
                    for (const el of nodes) {
                        if (!el || !el.style) continue;
                        const sh = el.scrollHeight || 0;
                        const ch = el.clientHeight || 0;
                        if (sh <= ch + 8) continue;
                        const cs = window.getComputedStyle(el);
                        const oy = (cs.overflowY || '').toLowerCase();
                        if (oy === 'auto' || oy === 'scroll' || oy === 'overlay') {
                            el.style.overflowY = 'visible';
                            el.style.maxHeight = 'none';
                            el.style.height = 'auto';
                        }
                    }
                    window.scrollTo(0, 0);
                })();
                """
            )
            time.sleep(0.35)
        except Exception:
            pass

        # 读取页面真实尺寸，避免横向/纵向被截断
        page_size = browser.run_js(
            """
            const de = document.documentElement;
            const b = document.body;
            const width = Math.max(
                de ? de.scrollWidth : 0,
                de ? de.clientWidth : 0,
                b ? b.scrollWidth : 0,
                b ? b.clientWidth : 0,
                1200
            );
            const height = Math.max(
                de ? de.scrollHeight : 0,
                de ? de.clientHeight : 0,
                b ? b.scrollHeight : 0,
                b ? b.clientHeight : 0,
                1600
            );
            return {width, height};
            """
        )
        width_px = float((page_size or {}).get("width") or 1200)
        height_px = float((page_size or {}).get("height") or 1600)
        if PDF_FAST_MODE:
            paper_width = 8.27
            paper_height = 11.69
        else:
            paper_width = max(8.27, min(width_px / 96.0, 22.0))
            paper_height = max(11.69, min(height_px / 96.0, 200.0))

        result = browser.run_cdp(
            "Page.printToPDF",
            printBackground=True,
            preferCSSPageSize=False,
            paperWidth=paper_width,
            paperHeight=paper_height,
            marginTop=0.1,
            marginBottom=0.1,
            marginLeft=0.1,
            marginRight=0.1,
            scale=1,
        )
        pdf_bytes = base64.b64decode(result["data"])
        with open(save_path, "wb") as f:
            f.write(pdf_bytes)
        print(f"✅ PDF 已成功保存: {save_path}")
    except Exception as e:
        print(f"❌ PDF 渲染失败: {e}")


def build_static_page_url(list_base: str, page_num: int) -> str:
    if page_num <= 1:
        return urljoin(list_base, "index.html")
    return urljoin(list_base, f"list-{page_num}.html")


def parse_static_list_items(list_html: str, list_url: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(list_html, "html.parser")
    results = []

    for li in soup.select("li"):
        chosen_a = None
        chosen_href = ""
        for a_tag in li.select("a[href]"):
            href = (a_tag.get("href") or "").strip()
            if not href:
                continue
            if href.startswith("/upload/") or href.startswith("/html/"):
                chosen_a = a_tag
                chosen_href = href
                break

        if not chosen_a:
            continue

        title = (chosen_a.get("title") or chosen_a.get_text(" ", strip=True)).strip()
        date_tag = li.select_one("span.r")
        if not date_tag:
            continue

        raw_date_text = date_tag.get_text(" ", strip=True)
        if not re.search(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}", raw_date_text):
            continue
        date_text = normalize_date(raw_date_text)

        if not chosen_href or not title:
            continue

        abs_url = urljoin(BASE_URL, chosen_href)
        results.append(
            {
                "title": title,
                "url": abs_url,
                "date": date_text,
            }
        )

    return results


def resolve_category_codes() -> List[str]:
    codes = [c.strip() for c in CATEGORY_CODES.split(",") if c.strip()]
    valid = []
    for c in codes:
        if c in CATEGORY_MAP:
            valid.append(c)
        else:
            print(f"[警告] 未知分类编码：{c}，已忽略")
    return valid


def extract_embedded_pdf_from_notice_html(
    session: requests.Session,
    browser: ChromiumPage,
    detail_url: str,
) -> Optional[str]:
    """从其他公告详情页提取内嵌 PDF 地址。"""
    try:
        html = request_text(session, browser, detail_url, referer=BASE_URL)
    except Exception:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 优先匹配你给出的结构：icon_pdf.gif + 邻接 a[href]
    for img in soup.select('img[src*="icon_pdf.gif"]'):
        a_tag = None
        sib = img.next_sibling
        while sib is not None:
            if getattr(sib, "name", None) == "a":
                a_tag = sib
                break
            sib = getattr(sib, "next_sibling", None)
        if a_tag and a_tag.get("href"):
            href = a_tag.get("href").strip()
            if href:
                return urljoin(BASE_URL, href)

    # 兜底：任意 a[href] 指向 pdf
    for a_tag in soup.select("a[href]"):
        href = (a_tag.get("href") or "").strip()
        if href.lower().endswith(".pdf"):
            return urljoin(BASE_URL, href)

    return None


def download_direct_file_notice(
    session: requests.Session,
    browser: ChromiumPage,
    item: Dict[str, str],
    topic_type: str,
    folder_name: str,
    progress_set: set,
) -> bool:
    title = item["title"]
    source_url = item["url"]
    date_text = item["date"]

    _in_range, _too_old = is_in_date_range(date_text)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"  ⏭️ [日期跳过] 披露日期 {date_text} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return True

    unique_key = f"direct::{source_url}"
    if SKIP_DOWNLOADED and unique_key in progress_set:
        print(f"  [⏭️ 跳过] 已下载：{title}")
        return True

    ext = get_file_ext_from_url(source_url, fallback=".pdf")
    if ext.lower() not in ALLOWED_DIRECT_EXT:
        ext = ".pdf"

    folder = category_dir(folder_name)
    save_path, filename = build_unique_file_path(folder, date_text, topic_type, title, ext)

    print(f"  [⬇️ 下载] {filename}")
    try:
        request_binary(session, browser, source_url, save_path, referer=BASE_URL)
        if os.path.getsize(save_path) <= 0:
            raise RuntimeError("文件大小为 0")

        save_progress(PROGRESS_FILE, unique_key)
        progress_set.add(unique_key)
        print(f"  [✅ 成功] {filename}")
        log_to_csv(title, topic_type, date_text, "SUCCEED", source_url, os.path.abspath(save_path), unique_key)
        return True
    except Exception as e:
        print(f"  [❌ 失败] {source_url} -> {e}")
        log_to_csv(title, topic_type, date_text, "FAILED", source_url, os.path.abspath(save_path), unique_key)
        return False


def download_detail_pdf_notice(
    session: requests.Session,
    browser: ChromiumPage,
    item: Dict[str, str],
    topic_type: str,
    folder_name: str,
    progress_set: set,
) -> bool:
    title = item["title"]
    detail_url = item["url"]
    date_text = item["date"]

    _in_range, _too_old = is_in_date_range(date_text)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"  ⏭️ [日期跳过] 披露日期 {date_text} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return True

    unique_key = f"detail::{detail_url}"
    if SKIP_DOWNLOADED and unique_key in progress_set:
        print(f"  [⏭️ 跳过] 已下载：{title}")
        return True

    folder = category_dir(folder_name)
    save_path, filename = build_unique_file_path(folder, date_text, topic_type, title, ".pdf")

    print(f"  [🖨️ 渲染PDF] {filename}")
    try:
        save_detail_html_as_pdf(browser, detail_url, save_path)
        if os.path.getsize(save_path) < 1024:
            raise RuntimeError("生成 PDF 过小，疑似失败")

        save_progress(PROGRESS_FILE, unique_key)
        progress_set.add(unique_key)
        print(f"  [✅ 成功] {filename}")
        log_to_csv(title, topic_type, date_text, "SUCCEED", detail_url, os.path.abspath(save_path), unique_key)
        return True
    except Exception as e:
        print(f"  [❌ 失败] {detail_url} -> {e}")
        log_to_csv(title, topic_type, date_text, "FAILED", detail_url, os.path.abspath(save_path), unique_key)
        return False


def download_other_notice(
    session: requests.Session,
    browser: ChromiumPage,
    item: Dict[str, str],
    topic_type: str,
    folder_name: str,
    progress_set: set,
) -> bool:
    """
    其他公告：
    1) 直链文件直接下载；
    2) HTML 详情页先提取内嵌 PDF；
    3) 若无内嵌 PDF，则整页渲染为 PDF（复用产品公告渲染方式）。
    """
    title = item["title"]
    source_url = item["url"]
    date_text = item["date"]

    _in_range, _too_old = is_in_date_range(date_text)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"  ⏭️ [日期跳过] 披露日期 {date_text} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return True

    unique_key = f"other::{source_url}"
    if SKIP_DOWNLOADED and unique_key in progress_set:
        print(f"  [⏭️ 跳过] 已下载：{title}")
        return True

    folder = category_dir(folder_name)

    is_html_detail = source_url.lower().endswith(".html") or "/html/" in source_url
    if is_html_detail:
        save_path, filename = build_unique_file_path(folder, date_text, topic_type, title, ".pdf")
        try:
            pdf_url = extract_embedded_pdf_from_notice_html(session, browser, source_url)
            if pdf_url:
                print(f"  [🔗 解析PDF] {filename}")
                request_binary(session, browser, pdf_url, save_path, referer=source_url)
                source_for_log = pdf_url
            else:
                print(f"  [🖨️ 渲染PDF] {filename}")
                save_detail_html_as_pdf(browser, source_url, save_path)
                source_for_log = source_url

            if os.path.getsize(save_path) <= 0:
                raise RuntimeError("输出文件大小为 0")

            save_progress(PROGRESS_FILE, unique_key)
            progress_set.add(unique_key)
            print(f"  [✅ 成功] {filename}")
            log_to_csv(title, topic_type, date_text, "SUCCEED", source_for_log, os.path.abspath(save_path), unique_key)
            return True
        except Exception as e:
            print(f"  [❌ 失败] {source_url} -> {e}")
            log_to_csv(title, topic_type, date_text, "FAILED", source_url, os.path.abspath(save_path), unique_key)
            return False

    ext = get_file_ext_from_url(source_url, fallback=".pdf")
    if ext.lower() not in ALLOWED_DIRECT_EXT:
        ext = ".pdf"
    save_path, filename = build_unique_file_path(folder, date_text, topic_type, title, ext)

    print(f"  [⬇️ 下载] {filename}")
    try:
        request_binary(session, browser, source_url, save_path, referer=BASE_URL)
        if os.path.getsize(save_path) <= 0:
            raise RuntimeError("文件大小为 0")

        save_progress(PROGRESS_FILE, unique_key)
        progress_set.add(unique_key)
        print(f"  [✅ 成功] {filename}")
        log_to_csv(title, topic_type, date_text, "SUCCEED", source_url, os.path.abspath(save_path), unique_key)
        return True
    except Exception as e:
        print(f"  [❌ 失败] {source_url} -> {e}")
        log_to_csv(title, topic_type, date_text, "FAILED", source_url, os.path.abspath(save_path), unique_key)
        return False


def crawl_static_category(
    session: requests.Session,
    browser: ChromiumPage,
    code: str,
    progress_set: set,
    checkpoint: dict = None,
) -> Tuple[int, int]:
    conf = CATEGORY_MAP[code]
    topic_type = conf["topic_type"]
    folder_name = conf["folder_name"]
    list_base = conf["list_base"]
    kind = conf["kind"]

    print(f"\n{'=' * 70}")
    print(f"🚀 开始抓取：{topic_type}")
    print(f"🔗 列表入口：{build_static_page_url(list_base, 1)}")
    print(f"{'=' * 70}")

    total, success = 0, 0
    consecutive_empty = 0
    early_stop_triggered = False

    # 断点续传：读取上次爬到的页码
    start_page = 1
    if checkpoint is not None:
        code_cp = checkpoint.get("code_pages", {}).get(code, {})
        start_page = int(code_cp.get("page", 1))
        if code_cp.get("done"):
            print(f"[断点续传] {code} 已完成，跳过")
            return 0, 0
        if start_page > 1:
            print(f"[断点续传] {code} 从第 {start_page} 页继续")

    for page_num in range(start_page, MAX_PAGES_PER_CATEGORY + 1):
        list_url = build_static_page_url(list_base, page_num)

        try:
            html = request_text(session, browser, list_url, referer=list_base)
            if code == "product_notice":
                items = parse_product_notice_items(html, list_url)
            else:
                items = parse_static_list_items(html, list_url)
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status == 404 and page_num > 1:
                print(f"[⚠️ 分页] 第 {page_num} 页 404，停止该分类")
                break
            print(f"[⚠️ 分页] 第 {page_num} 页请求失败：{e}")
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print("[⛔ 分页] 连续异常页达到阈值，停止该分类")
                break
            continue
        except Exception as e:
            print(f"[⚠️ 分页] 第 {page_num} 页请求失败：{e}")
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print("[⛔ 分页] 连续异常页达到阈值，停止该分类")
                break
            continue

        if not items:
            consecutive_empty += 1
            print(f"[📭 分页] 第 {page_num} 页无数据（连续空页 {consecutive_empty}/{MAX_CONSECUTIVE_EMPTY}）")
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print("[⛔ 分页] 连续空页达到阈值，停止该分类")
                break
            random_sleep(LIST_PAGE_DELAY_RANGE)
            continue

        consecutive_empty = 0
        print(f"[📄 分页] 第 {page_num} 页，抓到 {len(items)} 条")

        for item in items:
            total += 1
            _in_range, _too_old = is_in_date_range(item["date"])
            if not _in_range:
                if _too_old:
                    print(f"  ⏭️ [日期跳过] {item['title']} 披露日期 {item['date']} < {START_DATE}")
                    if EARLY_STOP:
                        print(f"  ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stop_triggered = True
                        break
                else:
                    print(f"  ⏭️ [日期跳过] {item['title']} 披露日期 {item['date']} > {END_DATE or '今天'}")
                continue
            if code == "other_notice":
                ok = download_other_notice(session, browser, item, topic_type, folder_name, progress_set)
            elif kind == "detail_pdf":
                ok = download_detail_pdf_notice(session, browser, item, topic_type, folder_name, progress_set)
            else:
                ok = download_direct_file_notice(session, browser, item, topic_type, folder_name, progress_set)
            if ok:
                success += 1

            random_sleep(REQUEST_DELAY_RANGE)

        # 断点续传：每页处理完后保存进度
        if checkpoint is not None:
            checkpoint.setdefault("code_pages", {})[code] = {"page": page_num + 1, "done": False}
            checkpoint["current_code"] = code
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)

        if early_stop_triggered:
            print(f"[⛔ 分页] 早停触发，停止该分类")
            break

        random_sleep(LIST_PAGE_DELAY_RANGE)

    print(f"[✅ 分类完成] {topic_type}：发现 {total}，成功 {success}")

    # 断点续传：分类完成标记 done
    if checkpoint is not None:
        checkpoint.setdefault("code_pages", {})[code] = {"page": page_num, "done": True}
        completed = checkpoint.setdefault("completed_codes", [])
        if code not in completed:
            completed.append(code)
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)

    return total, success


def parse_networth_product_code(title: str) -> str:
    title = str(title or "").strip()
    # 形如：...-WHZQQD202102B
    m = re.search(r"-([A-Za-z][A-Za-z0-9]+)$", title)
    if m:
        return m.group(1).upper()
    # 兜底：最后一段连续英文数字
    m = re.search(r"([A-Za-z]{2,}[A-Za-z0-9]{2,})$", title)
    if m:
        return m.group(1).upper()
    return ""


def parse_networth_list_items(list_html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(list_html, "html.parser")
    results = []
    seen_codes = set()

    for a_tag in soup.select("li a[href]"):
        href = (a_tag.get("href") or "").strip()
        if "/netWorth/" not in href:
            continue

        title = (a_tag.get("title") or a_tag.get_text(" ", strip=True)).strip()
        if not title:
            continue

        code = parse_networth_product_code(title)
        if not code:
            continue
        if code in seen_codes:
            continue
        seen_codes.add(code)

        detail_url = urljoin(BASE_URL, href)

        results.append(
            {
                "title": title,
                "url": detail_url,
                "code": code,
            }
        )

    return results


def parse_product_notice_items(list_html: str, list_url: str) -> List[Dict[str, str]]:
    """专用解析：只提取产品公告目录下的条目（/html/.../197/xxx.html），确保能得到详情 HTML 链接与日期"""
    soup = BeautifulSoup(list_html, "html.parser")
    results = []

    for li in soup.select("li"):
        # 同一 li 可能包含两个 a：第一个 href 为空，第二个才是详情页。
        candidate_links = []
        for a_tag in li.select("a[href]"):
            href = (a_tag.get("href") or "").strip()
            if href and "/198/197/" in href:
                candidate_links.append((a_tag, href))

        if not candidate_links:
            continue

        a_tag, href = candidate_links[0]

        title = (a_tag.get("title") or a_tag.get_text(" ", strip=True)).strip()
        date_tag = li.select_one("span.r")
        if not date_tag:
            continue
        raw_date = date_tag.get_text(" ", strip=True)
        if not re.search(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}", raw_date):
            continue

        date_text = normalize_date(raw_date)
        abs_url = urljoin(BASE_URL, href)

        results.append({"title": title, "url": abs_url, "date": date_text})

    return results


def fetch_all_networth_rows(
    session: requests.Session,
    browser: ChromiumPage,
    product_code: str,
    show_progress: bool = False,
    page_size: int = 10,
) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []

    # 使用动态 tqdm 可视化每页的抓取进度（如果可用）。
    # 不预设总页数，改为动态更新进度条以避免显示相对于 MAX 上限的错误百分比。
    use_tqdm = bool(tqdm)
    pbar = None
    if use_tqdm:
        try:
            pbar = tqdm(total=0, desc=f"{product_code}", unit="page")
        except Exception:
            pbar = None

    page_no = 1
    while page_no <= MAX_NETWORTH_PAGES_PER_PRODUCT:
        api_url = (
            "https://www.bocwm.cn/webApi/cms/productNetWorth/getNetWorthByCode"
            f"?productCode={product_code}&pageNo={page_no}&pageSize={page_size}"
        )

        # 每页请求加重试，避免单页卡住
        page_attempt = 0
        data_list = []
        while page_attempt < 3:
            try:
                data_json = request_json(session, browser, api_url, referer=BASE_URL)
                data_list = data_json.get("data") or []
                break
            except Exception as e:
                page_attempt += 1
                print(f"    [净值][重试] {product_code} page {page_no} 请求失败 ({page_attempt}/3): {e}")
                time.sleep(0.8 + page_attempt * 0.5)

        if not data_list:
            if show_progress:
                msg = f"第{page_no}页无数据，停止翻页。 已抓取 {len(all_rows)} 条"
                if use_tqdm:
                    pbar.write(f"    [进度] {product_code}: {msg}")
                else:
                    print(f"    [进度] {product_code}: {msg}")
            break

        # 处理并追加
        for item in data_list:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if row.get("releaseDate") is not None:
                row["releaseDate"] = normalize_date(str(row.get("releaseDate") or ""))
            all_rows.append(row)

        if show_progress:
            msg = f"已抓取 {len(all_rows)} 条，当前页 {page_no} 获取 {len(data_list)} 条"
            if use_tqdm:
                pbar.update(1)
                pbar.write(f"    [进度] {product_code}: {msg}")
            else:
                print(f"    [进度] {product_code}: {msg}")

        random_sleep((0.2, 0.6))
        page_no += 1

    if show_progress:
        if use_tqdm and pbar:
            pbar.write(f"    [完成] {product_code}: 共抓取 {len(all_rows)} 条")
            try:
                pbar.close()
            except Exception:
                pass
        else:
            print(f"    [完成] {product_code}: 共抓取 {len(all_rows)} 条")

    return all_rows


def write_networth_csv(csv_path: str, rows: List[Dict[str, Any]]):
    if not rows:
        return

    def _is_empty(v: Any) -> bool:
        return v is None or (isinstance(v, str) and v.strip() == "")

    def _to_cn_header(k: str) -> str:
        if k in NETWORTH_FIELD_CN_MAP:
            return NETWORTH_FIELD_CN_MAP[k]
        # 兜底：避免直接使用裸英文表头
        return f"其他字段({k})"

    # 按首见顺序收集字段，并剔除系统字段
    key_order: List[str] = []
    key_seen = set()
    for row in rows:
        for k in row.keys():
            if k in NETWORTH_SYSTEM_KEYS:
                continue
            if k not in key_seen:
                key_seen.add(k)
                key_order.append(k)

    # 只保留至少有一行非空的字段
    selected_keys = []
    for k in key_order:
        if any(not _is_empty(row.get(k)) for row in rows):
            selected_keys.append(k)

    if not selected_keys:
        raise RuntimeError("净值数据无可用字段")

    header = [_to_cn_header(k) for k in selected_keys]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            values = []
            for k in selected_keys:
                v = row.get(k)
                if v is None:
                    values.append("")
                else:
                    values.append(str(v))
            writer.writerow(values)


def crawl_networth_category(
    session: requests.Session,
    browser: ChromiumPage,
    progress_set: set,
    checkpoint: dict = None,
) -> Tuple[int, int]:
    conf = CATEGORY_MAP["net_worth"]
    topic_type = conf["topic_type"]
    folder_name = conf["folder_name"]
    list_url = conf["list_url"]

    print(f"\n{'=' * 70}")
    print(f"开始抓取：{topic_type}")
    print(f"列表入口：{list_url}")
    print(f"{'=' * 70}")

    # 断点续传：检查是否已完成
    if checkpoint is not None:
        nw_cp = checkpoint.get("code_pages", {}).get("net_worth", {})
        if nw_cp.get("done"):
            print(f"[断点续传] net_worth 已完成，跳过")
            return 0, 0

    folder = category_dir(folder_name)
    total, success = 0, 0

    try:
        # 直接读取列表页源码（含 v-show 全量条目），不再翻前端分页与详情页
        list_html = request_text(session, browser, list_url, referer=list_url)
    except Exception as e:
        print(f"[净值列表失败] 请求列表页失败：{e}")
        return 0, 0

    items = parse_networth_list_items(list_html)
    if not items:
        print("[净值列表] 未解析到任何产品代码，停止")
        return 0, 0

    print(f"[净值列表] 从源码解析到产品代码数：{len(items)}")

    # 断点续传：从上次断点的产品索引继续
    start_item_index = 0
    if checkpoint is not None:
        start_item_index = int(nw_cp.get("item_index", 0))
        if start_item_index > 0:
            print(f"[断点续传] net_worth 从第 {start_item_index + 1}/{len(items)} 个产品继续")

    for idx in range(start_item_index, len(items)):
        item = items[idx]
        title = item["title"]
        detail_url = item["url"]
        product_code = item["code"]

        unique_key = f"networth::{product_code}"
        total += 1
        if SKIP_DOWNLOADED and unique_key in progress_set:
            print(f"  [净值跳过] 已处理产品代码：{product_code}")
            success += 1
            # 即使跳过也更新 checkpoint
            if checkpoint is not None:
                checkpoint.setdefault("code_pages", {})["net_worth"] = {"item_index": idx + 1, "done": False}
                checkpoint["current_code"] = "net_worth"
                checkpoint["updated_at"] = now_str()
                save_checkpoint(CHECKPOINT_FILE, checkpoint)
            continue

        print(f"  [净值抓取] {product_code} - {title}")
        try:
            rows = fetch_all_networth_rows(session, browser, product_code, show_progress=True)
            if not rows:
                raise RuntimeError("接口未返回净值数据")

            csv_path = build_networth_csv_path(folder, product_code, title)
            write_networth_csv(csv_path, rows)

            save_progress(PROGRESS_FILE, unique_key)
            progress_set.add(unique_key)
            success += 1

            date_text = rows[0].get("releaseDate") or "未知日期"
            log_to_csv(title, topic_type, date_text, "SUCCEED", detail_url, os.path.abspath(csv_path), unique_key)
        except Exception as e:
            print(f"  [净值失败] {product_code} -> {e}")
            log_to_csv(title, topic_type, "未知日期", "FAILED", detail_url, "", unique_key)

        # 断点续传：每个产品处理完后保存进度
        if checkpoint is not None:
            checkpoint.setdefault("code_pages", {})["net_worth"] = {"item_index": idx + 1, "done": False}
            checkpoint["current_code"] = "net_worth"
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)

        random_sleep(REQUEST_DELAY_RANGE)

    print(f"[分类完成] {topic_type}：发现 {total}，成功 {success}")

    # 断点续传：net_worth 分类完成标记 done
    if checkpoint is not None:
        checkpoint.setdefault("code_pages", {})["net_worth"] = {"item_index": len(items), "done": True}
        completed = checkpoint.setdefault("completed_codes", [])
        if "net_worth" not in completed:
            completed.append("net_worth")
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)

    return total, success


def crawl():
    ensure_dir(DOWNLOAD_ROOT)

    progress_set = load_progress(PROGRESS_FILE)
    print(f"已有去重记录：{len(progress_set)}")

    # 断点续传：加载 checkpoint
    checkpoint = load_checkpoint(CHECKPOINT_FILE)
    completed_codes = set(checkpoint.get("completed_codes", []))
    if completed_codes:
        print(f"[断点续传] 已完成分类：{', '.join(sorted(completed_codes))}")

    codes = resolve_category_codes()
    if not codes:
        print("未配置有效分类，程序结束")
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

        grand_total, grand_success = 0, 0

        try:
            for code in codes:
                kind = CATEGORY_MAP[code]["kind"]
                if kind == "net_worth":
                    total, success = crawl_networth_category(session, browser, progress_set, checkpoint=checkpoint)
                else:
                    total, success = crawl_static_category(session, browser, code, progress_set, checkpoint=checkpoint)

                grand_total += total
                grand_success += success
        except KeyboardInterrupt:
            print("\n[中断] 收到 Ctrl+C，断点已保存，下次运行将从断点处继续")
            raise

        print(f"\n{'=' * 70}")
        print("全部分类抓取完成")
        print(f"总发现：{grand_total}")
        print(f"总成功：{grand_success}")
        print(f"下载目录：{os.path.abspath(DOWNLOAD_ROOT)}")
        print(f"日志文件：{os.path.abspath(LOG_CSV_PATH)}")
        print(f"{'=' * 70}")
    finally:
        browser.quit()


if __name__ == "__main__":
    crawl()
