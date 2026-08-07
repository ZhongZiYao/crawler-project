
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
import base64
import atexit
from datetime import datetime
from email.utils import decode_rfc2231
from typing import Dict, List, Set, Tuple
from urllib.parse import unquote, urljoin

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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =====================================
# 用户配置区
# =====================================

INSTITUTE_NAME = "温州银行"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_links.txt")
FINGERPRINT_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_fingerprints.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
FAILED_FILE = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")

BASE_URL = "http://www.wzbank.cn"
HOME_URL = f"{BASE_URL}/index.php/home"
LIST_URL_TEMPLATE = f"{BASE_URL}/personal/announcementlist/menu_item_id/{{menu_id}}/page/{{page}}?keywords="
LIST_URL_FIRST_PAGE = f"{BASE_URL}/personal/announcementlist/menu_item_id/{{menu_id}}"

MENU_MAP = {
    "产品说明书": "2458",
    "发行(成立)公告": "310",
    "到期(运行)公告": "311",
    "定期公告": "5458",
    "其他公告": "5461",
    # "代销理财产品": "312",
}

# TODO[手动修改]: 空字符串表示全量抓取全部栏目
RUN_ONLY_MENU_NAME = "产品说明书,发行(成立)公告,到期(运行)公告,定期公告"

# TODO[手动修改]: 单页联调（仅抓某栏目某页）
TEST_MODE = False
TEST_MENU_NAME = "产品说明书"
TEST_PAGE_NO = 1

# TODO[手动修改]: 网络与重试
REQUEST_TIMEOUT = 40
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3

# TODO[手动修改]: 稳中求稳抓取节奏
REQUEST_INTERVAL_SECONDS = (0.8, 1.8)
DETAIL_INTERVAL_SECONDS = (0.8, 1.6)
PAGE_INTERVAL_SECONDS = (1.4, 2.8)

# TODO[手动修改]: 去重和断点续跑
SKIP_DOWNLOADED = True
ENABLE_CHECKPOINT_RESUME = True

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("温州银行", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("温州银行", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-09"
# END_DATE   = "2026-06-30"

# TODO[手动修改]: 无附件公告保存策略
# True=优先富文本转PDF（保持网页版式）；False=保存TXT
SAVE_NO_ATTACHMENT_AS_PDF = True
PDF_RENDER_TIMEOUT_MS = 60000

_PDF_PAGE: ChromiumPage = None

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

DATE_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
DETAIL_LINK_RE = re.compile(r"/personal/announcementview/page_id/(\d+)", re.I)
DOWNLOAD_LINK_RE = re.compile(r"/uploadfiledownload/downloadfile/upload_file_id/(\d+)", re.I)


# =====================================
# 工具函数
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
    m = DATE_RE.search(str(text or ""))
    if not m:
        return today_str()
    y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{y:04d}-{mm:02d}-{dd:02d}"


def sanitize_text(text: str, max_len: int = 300) -> str:
    val = re.sub(r"[\\/*?:\"<>|]", "_", str(text or ""))
    val = re.sub(r"\s+", " ", val).strip()
    if max_len > 0:
        val = val[:max_len]
    return val


def normalize_notice_title(text: str) -> str:
    # 日志中的公告名称需要保留完整标题，仅做空白折叠与首尾清理。
    return re.sub(r"\s+", " ", str(text or "")).strip()


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


def extract_page_id(detail_url: str) -> str:
    m = DETAIL_LINK_RE.search(detail_url or "")
    return m.group(1) if m else ""


def extract_upload_id(download_url: str) -> str:
    m = DOWNLOAD_LINK_RE.search(download_url or "")
    return m.group(1) if m else ""


def parse_content_disposition_filename(header_val: str) -> str:
    text = str(header_val or "")
    if not text:
        return ""

    m_star = re.search(r"filename\*\s*=\s*([^;]+)", text, re.I)
    if m_star:
        raw = m_star.group(1).strip().strip('"')
        if "''" in raw:
            charset, _, encoded = raw.partition("''")
            try:
                if charset:
                    return unquote(encoded, encoding=charset, errors="replace")
                return unquote(encoded)
            except Exception:
                return unquote(encoded)
        try:
            parts = decode_rfc2231(raw)
            if isinstance(parts, tuple) and len(parts) == 3:
                charset, _, encoded = parts
                if charset:
                    return encoded.encode("latin-1", errors="ignore").decode(charset, errors="replace")
        except Exception:
            pass

    m_plain = re.search(r"filename\s*=\s*\"?([^\";]+)\"?", text, re.I)
    if m_plain:
        return unquote(m_plain.group(1).strip())

    return ""


def ensure_ext(filename: str, fallback: str = ".bin") -> str:
    _, ext = os.path.splitext(str(filename or ""))
    return ext.lower() if ext else fallback


def _find_first_group(patterns: List[str], text: str) -> str:
    src = str(text or "")
    for p in patterns:
        m = re.search(p, src, flags=re.I)
        if m:
            return sanitize_text(m.group(1), 120)
    return ""


def extract_codes_from_text(text: str) -> Dict[str, str]:
    src = str(text or "")
    product_code = _find_first_group(
        [
            r"产品编号\s*[:：]\s*([A-Za-z0-9_\-]+)",
            r"产品代码\s*[:：]\s*([A-Za-z0-9_\-]+)",
        ],
        src,
    )
    sales_code = _find_first_group([r"销售代码\s*[:：]\s*([A-Za-z0-9_\-]+)"], src)
    product_code_extra = _find_first_group(
        [
            r"理财登记编码\s*[:：]\s*([A-Za-z0-9_\-]+)",
            r"登记编码\s*[:：]\s*([A-Za-z0-9_\-]+)",
        ],
        src,
    )
    if product_code_extra == product_code:
        product_code_extra = ""
    return {
        "product_code": product_code,
        "sales_code": sales_code,
        "product_code_extra": product_code_extra,
    }


def build_unique_key(notice_type: str, title: str, disclose_date: str) -> str:
    return f"{INSTITUTE_NAME}+{notice_type}+{normalize_notice_title(title)}+{normalize_date(disclose_date)}"


def build_fingerprint_key(unique_key: str, source: str) -> str:
    # 内部去重需区分不同来源链接，避免同一公告多附件被误判为重复。
    return f"{unique_key}+{sanitize_text(source, 300)}"


def build_base_filename(notice_type: str, title: str, disclose_date: str, codes: Dict[str, str]) -> str:
    product_code = sanitize_text(codes.get("product_code", ""), 80)
    sales_code = sanitize_text(codes.get("sales_code", ""), 80)
    product_code_extra = sanitize_text(codes.get("product_code_extra", ""), 80)

    parts = [
        sanitize_text(INSTITUTE_NAME, 40),
        sanitize_text(title, 180),
        sanitize_text(notice_type, 40),
    ]

    # 仅拼接存在的代码字段，避免输出空标签。
    if product_code:
        parts.append(f"产品代码：{product_code}")
    if sales_code:
        parts.append(f"销售代码：{sales_code}")
    if product_code_extra:
        parts.append(f"登记编码：{product_code_extra}")

    parts.append(f"披露日期：{normalize_date(disclose_date)}")
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
# 状态文件
# =====================================


def read_line_set(file_path: str) -> Set[str]:
    if not os.path.exists(file_path):
        return set()
    with open(file_path, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def append_line(file_path: str, val: str):
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(val.strip() + "\n")


def load_checkpoint() -> Dict:
    if not os.path.exists(CHECKPOINT_FILE):
        return {"menus": {}, "updated_at": now_time_str()}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"menus": {}, "updated_at": now_time_str()}
        data.setdefault("menus", {})
        return data
    except Exception:
        return {"menus": {}, "updated_at": now_time_str()}


def save_checkpoint(data: Dict):
    data["updated_at"] = now_time_str()
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_checkpoint_menu(
    checkpoint: Dict,
    menu_name: str,
    menu_id: str,
    next_page: int,
    next_item_index: int,
    current_page: int,
    current_item_index: int,
    done: bool = False,
):
    checkpoint.setdefault("menus", {})
    checkpoint["menus"][menu_name] = {
        "menu_id": menu_id,
        "next_page": int(next_page),
        "next_item_index": int(next_item_index),
        "current_page": int(current_page),
        "current_item_index": int(current_item_index),
        "done": bool(done),
        "updated_at": now_time_str(),
    }


# =====================================
# 日志
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
    notice_type: str,
    disclose_date: str,
    status: str,
    source_link: str,
    save_path: str,
    unique_key: str,
):
    status = "SUCCEED" if str(status).upper() == "SUCCEED" else "FAILED"
    row = [
        INSTITUTE_NAME,
        normalize_notice_title(notice_title),
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


def write_failed_row(
    menu_name: str,
    page_no: int,
    item_index: int,
    detail_url: str,
    notice_title: str,
    reason: str,
):
    exists = os.path.exists(FAILED_FILE)
    with open(FAILED_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["time", "menu", "page_no", "item_index", "detail_url", "title", "reason"])
        writer.writerow([now_time_str(), menu_name, page_no, item_index, detail_url, notice_title, reason])


# =====================================
# 请求层
# =====================================


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=REQUEST_RETRY,
        read=REQUEST_RETRY,
        connect=REQUEST_RETRY,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Referer": HOME_URL,
        }
    )
    return session


def request_text(session: requests.Session, url: str) -> str:
    for i in range(1, REQUEST_RETRY + 1):
        try:
            random_sleep(REQUEST_INTERVAL_SECONDS)
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            if not resp.encoding:
                resp.encoding = "utf-8"
            return resp.text
        except Exception as e:
            if i >= REQUEST_RETRY:
                raise
            print(f"   ⚠️ 请求重试 {i}/{REQUEST_RETRY}: {url} | {e}")
            time.sleep(RETRY_WAIT_SECONDS)
    return ""


def request_stream(session: requests.Session, url: str) -> requests.Response:
    last_error = None
    for i in range(1, DOWNLOAD_RETRY + 1):
        try:
            random_sleep(REQUEST_INTERVAL_SECONDS)
            resp = session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_error = e
            if i < DOWNLOAD_RETRY:
                print(f"   ⚠️ 下载重试 {i}/{DOWNLOAD_RETRY}: {url} | {e}")
                time.sleep(RETRY_WAIT_SECONDS)
    raise RuntimeError(str(last_error))


# =====================================
# 解析层
# =====================================


def parse_max_page(list_html: str, menu_id: str) -> int:
    nums = [int(x) for x in re.findall(rf"/menu_item_id/{menu_id}/page/(\d+)\?keywords=", list_html)]
    return max(nums) if nums else 1


def find_nearest_date_text(node) -> str:
    parent = node
    for _ in range(6):
        if parent is None:
            break
        text = parent.get_text(" ", strip=True)
        m = DATE_RE.search(text)
        if m:
            return m.group(0)
        parent = parent.parent
    return ""


def parse_list_items(list_html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(list_html, "html.parser")
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not DETAIL_LINK_RE.search(href):
            continue

        detail_url = urljoin(BASE_URL, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)

        title = sanitize_text(a.get_text(" ", strip=True), 300)
        if not title:
            title = f"公告_{extract_page_id(detail_url)}"
        date_text = find_nearest_date_text(a)
        items.append({"detail_url": detail_url, "title": title, "date": date_text})

    return items


def parse_detail(detail_html: str, detail_url: str) -> Dict:
    soup = BeautifulSoup(detail_html, "html.parser")

    content_node = soup.select_one("div.work-con.announcementview div.about-con.page_wrapper")
    if content_node is None:
        content_node = soup.select_one("div.about-con.page_wrapper")

    title = ""
    if content_node is not None:
        title_node = content_node.select_one("span.title")
        if title_node is not None:
            title = sanitize_text(title_node.get_text(" ", strip=True), 300)
    if not title:
        title_node = soup.select_one("span.title")
        if title_node is not None:
            title = sanitize_text(title_node.get_text(" ", strip=True), 300)
    if not title:
        h1 = soup.find("h1")
        if h1 is not None:
            title = sanitize_text(h1.get_text(" ", strip=True), 300)

    body_scope = content_node.get_text("\n", strip=True) if content_node is not None else soup.get_text("\n", strip=True)
    date_text = ""
    date_matches = DATE_RE.findall(body_scope)
    if date_matches:
        y, m, d = date_matches[-1]
        date_text = f"{int(y):04d}年{int(m):02d}月{int(d):02d}日"

    attachments: List[Dict[str, str]] = []
    attachment_scope = content_node if content_node is not None else soup
    for a in attachment_scope.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not DOWNLOAD_LINK_RE.search(href):
            continue
        name = sanitize_text(a.get_text(" ", strip=True), 260)
        download_url = urljoin(BASE_URL, href)
        attachments.append(
            {
                "download_url": download_url,
                "file_name": name,
                "upload_id": extract_upload_id(download_url),
            }
        )

    codes = extract_codes_from_text(body_scope)

    return {
        "detail_url": detail_url,
        "title": title,
        "date": date_text,
        "attachments": attachments,
        "text": body_scope,
        "codes": codes,
    }


# =====================================
# 下载与保存
# =====================================


def save_binary_file(resp: requests.Response, save_path: str):
    with open(save_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def save_text_file(content: str, save_path: str):
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(content)


def render_announcement_pdf(detail_url: str, save_path: str):
        global _PDF_PAGE

        hide_css = """
        #header, #menu, .con-left, .con-right, .web-path, .con-slide, #footer, .top-nav, .top-search { display: none !important; }
        #wrapper, body, html { background: #ffffff !important; }
        .con-center { width: 100% !important; margin: 0 !important; float: none !important; }
        .work-con.announcementview { margin: 0 auto !important; width: 100% !important; }
        .all-news-right { width: 100% !important; margin: 0 !important; }
        .about-con.page_wrapper {
            width: 100% !important;
            border: 1px solid #d9d9d9 !important;
            border-radius: 8px !important;
            box-sizing: border-box !important;
            padding: 28px 34px !important;
            margin: 0 !important;
            background: #fff !important;
        }
        .about-con.page_wrapper .title {
            display: block !important;
            font-size: 30px !important;
            line-height: 1.55 !important;
            color: #2f5f9c !important;
            font-weight: 700 !important;
            margin-bottom: 20px !important;
            word-break: break-word !important;
        }
        .about-con.page_wrapper p, .about-con.page_wrapper span, .about-con.page_wrapper div {
            font-size: 24px !important;
            line-height: 1.8 !important;
            color: #2b2b2b !important;
            font-family: "Microsoft YaHei", "PingFang SC", "SimSun", sans-serif !important;
            white-space: normal !important;
            word-break: break-word !important;
        }
        @page { size: A4; margin: 14mm 12mm 14mm 12mm; }
        """

        try:
                if _PDF_PAGE is None:
                        _PDF_PAGE = _new_page()
                        _PDF_PAGE.set.load_mode.none()

                        def _cleanup_pdf_page():
                                try:
                                        if _PDF_PAGE is not None:
                                                _PDF_PAGE.quit()
                                except Exception:
                                        pass

                        atexit.register(_cleanup_pdf_page)

                _PDF_PAGE.get(detail_url, timeout=max(5, int(PDF_RENDER_TIMEOUT_MS / 1000)))
                _PDF_PAGE.wait.ele_displayed("css:div.work-con.announcementview div.about-con.page_wrapper", timeout=20)

                # 注入打印样式，尽量保留公告区域版式并隐藏外围导航
                _PDF_PAGE.run_js(
                        """
                        (() => {
                            const style = document.createElement('style');
                            style.setAttribute('data-crawler-style', '1');
                            style.textContent = arguments[0];
                            document.head.appendChild(style);
                        })();
                        """,
                        hide_css,
                )
                time.sleep(0.6)

                pdf_data = _PDF_PAGE.run_cdp(
                    "Page.printToPDF",
                    printBackground=True,
                    preferCSSPageSize=True,
                    marginTop=0.2,
                    marginBottom=0.2,
                    marginLeft=0.2,
                    marginRight=0.2,
                )
                raw = pdf_data.get("data") if isinstance(pdf_data, dict) else ""
                if not raw:
                        raise RuntimeError("ChromiumPage打印PDF返回空数据")

                with open(save_path, "wb") as f:
                        f.write(base64.b64decode(raw))
        except Exception as e:
                raise RuntimeError(f"ChromiumPage富文本转PDF失败: {e}") from e


def download_attachment(
    session: requests.Session,
    download_url: str,
    notice_type: str,
    title: str,
    disclose_date: str,
    codes: Dict[str, str],
    fallback_name: str,
    save_folder: str,
) -> Tuple[str, str]:
    resp = request_stream(session, download_url)

    header_name = parse_content_disposition_filename(resp.headers.get("Content-Disposition", ""))
    final_name = sanitize_text(header_name, 220) if header_name else sanitize_text(fallback_name, 220)

    ext = ensure_ext(final_name, ".bin")
    base = build_base_filename(notice_type, title, disclose_date, codes)
    save_path, file_name = build_unique_save_path(save_folder, base, ext)
    save_binary_file(resp, save_path)
    return save_path, file_name


def save_notice_pdf_or_text(
    detail_url: str,
    notice_type: str,
    title: str,
    disclose_date: str,
    text: str,
    codes: Dict[str, str],
    save_folder: str,
) -> Tuple[str, str, str]:
    base = build_base_filename(notice_type, title, disclose_date, codes)

    if SAVE_NO_ATTACHMENT_AS_PDF:
        save_path, file_name = build_unique_save_path(save_folder, base, ".pdf")
        render_announcement_pdf(detail_url, save_path)
        return save_path, file_name, "无附件-富文本转PDF成功"

    save_path, file_name = build_unique_save_path(save_folder, base, ".txt")
    save_text_file(text, save_path)
    return save_path, file_name, "无附件-正文TXT已保存"


# =====================================
# 主流程
# =====================================


def iter_target_menus() -> List[Tuple[str, str]]:
    if RUN_ONLY_MENU_NAME and RUN_ONLY_MENU_NAME in MENU_MAP:
        return [(RUN_ONLY_MENU_NAME, MENU_MAP[RUN_ONLY_MENU_NAME])]
    return list(MENU_MAP.items())


def process_notice(
    session: requests.Session,
    menu_name: str,
    page_no: int,
    item_index: int,
    notice: Dict[str, str],
    downloaded_links: Set[str],
    fingerprints: Set[str],
):
    detail_url = notice["detail_url"]
    list_title = notice.get("title", "")
    list_date = notice.get("date", "")

    detail_html = request_text(session, detail_url)
    random_sleep(DETAIL_INTERVAL_SECONDS)
    detail = parse_detail(detail_html, detail_url)

    page_id = extract_page_id(detail_url)
    real_title = detail.get("title") or list_title or f"公告_{page_id}"
    real_date = detail.get("date") or list_date or today_str()
    codes = detail.get("codes", {})

    _in_range, _too_old = is_in_date_range(real_date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"   ⏭️ [日期跳过] 披露日期 {normalize_date(real_date)} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {real_title}")
        return "too_old" if _too_old else "skip"

    save_folder = os.path.join(DOWNLOAD_ROOT, sanitize_text(menu_name, 50))
    ensure_dir(save_folder)

    attachments = detail.get("attachments", [])
    if attachments:
        for att in attachments:
            download_url = att.get("download_url", "")
            fallback_name = att.get("file_name", "") or f"附件_{att.get('upload_id', '')}"
            unique_key = build_unique_key(menu_name, real_title, real_date)
            fingerprint_key = build_fingerprint_key(unique_key, download_url)

            fallback_ext = ensure_ext(fallback_name, ".bin")
            expected_base = build_base_filename(menu_name, real_title, real_date, codes)
            expected_path, _ = build_unique_save_path(save_folder, expected_base, fallback_ext)

            if SKIP_DOWNLOADED and (unique_key in fingerprints or download_url in downloaded_links):
                print(f"   ↪️ 已存在，跳过附件: {fallback_name}")
                continue

            if SKIP_DOWNLOADED and fingerprint_key in fingerprints:
                print(f"   ↪️ 指纹已存在，跳过附件: {fallback_name}")
                continue

            try:
                save_path, _ = download_attachment(
                    session=session,
                    download_url=download_url,
                    notice_type=menu_name,
                    title=real_title,
                    disclose_date=real_date,
                    codes=codes,
                    fallback_name=fallback_name,
                    save_folder=save_folder,
                )
                append_line(PROGRESS_FILE, download_url)
                append_line(FINGERPRINT_FILE, fingerprint_key)
                downloaded_links.add(download_url)
                fingerprints.add(fingerprint_key)

                write_log_row(
                    notice_title=real_title,
                    notice_type=menu_name,
                    disclose_date=real_date,
                    status="SUCCEED",
                    source_link=download_url,
                    save_path=save_path,
                    unique_key=unique_key,
                )
                print(f"   ✅ 附件下载成功: {os.path.basename(save_path)}")
            except Exception as e:
                write_failed_row(menu_name, page_no, item_index, detail_url, real_title, f"附件下载失败: {e}")
                write_log_row(
                    notice_title=real_title,
                    notice_type=menu_name,
                    disclose_date=real_date,
                    status="FAILED",
                    source_link=download_url,
                    save_path=expected_path,
                    unique_key=unique_key,
                )
                print(f"   ❌ 附件下载失败: {download_url} | {e}")
    else:
        unique_key = build_unique_key(menu_name, real_title, real_date)
        fingerprint_key = build_fingerprint_key(unique_key, detail_url)
        if SKIP_DOWNLOADED and fingerprint_key in fingerprints:
            print("   ↪️ 正文公告已存在，跳过")
            return

        expected_base = build_base_filename(menu_name, real_title, real_date, codes)
        expected_ext = ".pdf" if SAVE_NO_ATTACHMENT_AS_PDF else ".txt"
        expected_path, _ = build_unique_save_path(save_folder, expected_base, expected_ext)

        try:
            save_path, _, status = save_notice_pdf_or_text(
                detail_url=detail_url,
                notice_type=menu_name,
                title=real_title,
                disclose_date=real_date,
                text=detail.get("text", ""),
                codes=codes,
                save_folder=save_folder,
            )
            append_line(FINGERPRINT_FILE, fingerprint_key)
            fingerprints.add(fingerprint_key)

            write_log_row(
                notice_title=real_title,
                notice_type=menu_name,
                disclose_date=real_date,
                status="SUCCEED",
                source_link=detail_url,
                save_path=save_path,
                unique_key=unique_key,
            )
            print(f"   ✅ 无附件公告已保存: {os.path.basename(save_path)}")
        except Exception as e:
            # 兜底，至少保留正文文本
            try:
                base = build_base_filename(menu_name, real_title, real_date, codes)
                fallback_path, _ = build_unique_save_path(save_folder, base, ".txt")
                save_text_file(detail.get("text", ""), fallback_path)
                append_line(FINGERPRINT_FILE, fingerprint_key)
                fingerprints.add(fingerprint_key)
                write_log_row(
                    notice_title=real_title,
                    notice_type=menu_name,
                    disclose_date=real_date,
                    status="SUCCEED",
                    source_link=detail_url,
                    save_path=fallback_path,
                    unique_key=unique_key,
                )
                print(f"   ⚠️ PDF失败，已降级TXT: {os.path.basename(fallback_path)}")
            except Exception as e2:
                write_failed_row(menu_name, page_no, item_index, detail_url, real_title, f"正文保存失败: {e2}")
                write_log_row(
                    notice_title=real_title,
                    notice_type=menu_name,
                    disclose_date=real_date,
                    status="FAILED",
                    source_link=detail_url,
                    save_path=expected_path,
                    unique_key=unique_key,
                )
                print(f"   ❌ 正文保存失败: {detail_url} | {e2}")


def process_menu(
    session: requests.Session,
    menu_name: str,
    menu_id: str,
    checkpoint: Dict,
    downloaded_links: Set[str],
    fingerprints: Set[str],
):
    print(f"\n🏁 开始栏目: {menu_name} (menu_id={menu_id})")

    start_page = 1
    start_item_index = 1
    if TEST_MODE:
        start_page = TEST_PAGE_NO
        end_page = TEST_PAGE_NO
    else:
        if ENABLE_CHECKPOINT_RESUME:
            saved = checkpoint.get("menus", {}).get(menu_name, {})
            if str(saved.get("menu_id", "")) == str(menu_id):
                start_page = max(1, int(saved.get("next_page", 1)))
                start_item_index = max(1, int(saved.get("next_item_index", 1)))
        first_html = request_text(session, LIST_URL_FIRST_PAGE.format(menu_id=menu_id))
        end_page = parse_max_page(first_html, menu_id)

    print(f"   📄 页码范围: {start_page} -> {end_page}")
    if start_item_index > 1:
        print(f"   ⏭️ 断点续跑从第 {start_page} 页第 {start_item_index} 条开始")

    early_stop_triggered = False
    for page_no in range(start_page, end_page + 1):
        list_url = LIST_URL_TEMPLATE.format(menu_id=menu_id, page=page_no)
        try:
            print(f"\n   🔎 抓取列表页: 第 {page_no}/{end_page} 页")
            list_html = request_text(session, list_url)
            items = parse_list_items(list_html)
            print(f"   🧾 本页公告数: {len(items)}")

            page_start_idx = start_item_index if page_no == start_page else 1

            for idx, notice in enumerate(items, 1):
                if idx < page_start_idx:
                    continue

                print(f"   ▶️ 处理公告 {idx}/{len(items)}: {notice.get('title', '')[:34]}")
                try:
                    _notice_result = process_notice(
                        session=session,
                        menu_name=menu_name,
                        page_no=page_no,
                        item_index=idx,
                        notice=notice,
                        downloaded_links=downloaded_links,
                        fingerprints=fingerprints,
                    )
                    if _notice_result == "too_old" and EARLY_STOP:
                        print(f"   ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stop_triggered = True
                        break
                except Exception as e:
                    write_failed_row(
                        menu_name,
                        page_no,
                        idx,
                        notice.get("detail_url", ""),
                        notice.get("title", ""),
                        f"公告处理失败: {e}",
                    )
                    print(f"   ❌ 公告处理失败: {e}")

                if not TEST_MODE:
                    update_checkpoint_menu(
                        checkpoint=checkpoint,
                        menu_name=menu_name,
                        menu_id=menu_id,
                        next_page=page_no,
                        next_item_index=idx + 1,
                        current_page=page_no,
                        current_item_index=idx,
                        done=False,
                    )
                    save_checkpoint(checkpoint)

            if not TEST_MODE:
                update_checkpoint_menu(
                    checkpoint=checkpoint,
                    menu_name=menu_name,
                    menu_id=menu_id,
                    next_page=page_no + 1,
                    next_item_index=1,
                    current_page=page_no,
                    current_item_index=max(1, len(items)),
                    done=False,
                )
                save_checkpoint(checkpoint)

            random_sleep(PAGE_INTERVAL_SECONDS)

            if early_stop_triggered:
                break
        except Exception as e:
            write_failed_row(menu_name, page_no, 0, list_url, f"列表页{page_no}", f"列表页处理失败: {e}")
            print(f"   ❌ 列表页失败: {list_url} | {e}")
            continue

    if not TEST_MODE:
        update_checkpoint_menu(
            checkpoint=checkpoint,
            menu_name=menu_name,
            menu_id=menu_id,
            next_page=end_page + 1,
            next_item_index=1,
            current_page=end_page,
            current_item_index=0,
            done=True,
        )
        save_checkpoint(checkpoint)
    print(f"✅ 栏目完成: {menu_name}")


def bootstrap_files():
    ensure_dir(DOWNLOAD_ROOT)
    for menu_name in MENU_MAP.keys():
        ensure_dir(os.path.join(DOWNLOAD_ROOT, sanitize_text(menu_name, 50)))

    for f in [PROGRESS_FILE, FINGERPRINT_FILE]:
        if not os.path.exists(f):
            with open(f, "w", encoding="utf-8"):
                pass


def main():
    print("=" * 68)
    print(f"🚀 {INSTITUTE_NAME} 公告爬虫启动")
    print(f"🕒 开始时间: {now_time_str()}")
    print("=" * 68)

    bootstrap_files()
    session = build_session()

    downloaded_links = read_line_set(PROGRESS_FILE)
    fingerprints = read_line_set(FINGERPRINT_FILE)
    checkpoint = load_checkpoint()

    menus = iter_target_menus()
    if TEST_MODE:
        menus = [(TEST_MENU_NAME, MENU_MAP[TEST_MENU_NAME])]
        print(f"🧪 TEST_MODE 已开启，仅抓 {TEST_MENU_NAME} 第 {TEST_PAGE_NO} 页")

    print(f"📚 待处理栏目数: {len(menus)}")

    for menu_name, menu_id in menus:
        process_menu(
            session=session,
            menu_name=menu_name,
            menu_id=menu_id,
            checkpoint=checkpoint,
            downloaded_links=downloaded_links,
            fingerprints=fingerprints,
        )

    print("\n" + "=" * 68)
    print(f"🎉 任务结束: {INSTITUTE_NAME}")
    print(f"🕒 结束时间: {now_time_str()}")
    print("=" * 68)


if __name__ == "__main__":
    main()
