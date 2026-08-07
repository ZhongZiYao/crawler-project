
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
try:
    import pdfplumber
except Exception:
    pdfplumber = None

# =====================================
# 用户配置区
# =====================================

INSTITUTE_NAME = "杭州联合银行"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_links.txt")
FINGERPRINT_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_fingerprints.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
FAILED_FILE = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")
FAILED_TASKS_FILE = os.path.join(DOWNLOAD_ROOT, "failed_tasks.json")

BASE_URL = "https://www.urcb.com"
HOME_URL = f"{BASE_URL}/urcb/index/index.shtml"

CATEGORIES = {
    "clgg": {
        "name": "成立公告",
        "url": f"{BASE_URL}/urcb/lm1/cfcp1/zylc/cpgg/clgg/index.shtml",
        "folder": "成立公告",
    },
    "dqgg": {
        "name": "到期公告",
        "url": f"{BASE_URL}/urcb/lm1/cfcp1/zylc/cpgg/dqgg/index.shtml",
        "folder": "到期公告",
    },
    "yzgg": {
        "name": "运作公告",
        "url": f"{BASE_URL}/urcb/lm1/cfcp1/zylc/cpgg/yzgg/index.shtml",
        "folder": "运作公告",
    },
    "lsgg": {
        "name": "历史公告",
        "url": f"{BASE_URL}/urcb/lm1/cfcp1/zylc/cpgg/lsgg/index.shtml",
        "folder": "历史公告",
    },
}

# 分页 token 固定映射（来自首页分页链接）
CATEGORY_PAGE_TOKENS = {
    "clgg": "c72b6ceb",
    "dqgg": "bce2c888",
    "yzgg": "c0705fea",
    "lsgg": "eaf37e4e",
}

# TODO[手动修改]: 联调模式，仅抓单个详情页（空字符串为全量）
TEST_ONLY_DETAIL_URL = ""

# TODO[手动修改]: 单模块测试（可填: clgg / dqgg / yzgg / lsgg），空列表表示全跑
RUN_ONLY_CATEGORIES: List[str] = []

# TODO[手动修改]: 重试与等待
REQUEST_TIMEOUT = 45
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
PAGE_NO_FILE_WAIT_SECONDS = 10

# TODO[手动修改]: 节奏控制（稳中求稳）
REQUEST_INTERVAL_SECONDS = (1.0, 2.0)
PAGE_INTERVAL_SECONDS = (1.6, 3.2)
ITEM_INTERVAL_SECONDS = (1.0, 2.4)

# TODO[手动修改]: 去重与断点开关
ENABLE_CHECKPOINT_RESUME = True
SKIP_DOWNLOADED = True

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("杭州联合银行", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("杭州联合银行", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-09"
# END_DATE   = "2026-06-30"

# TODO[手动修改]: PDF 文本扫描页数（用于提取产品/销售代码）
PDF_SCAN_PAGES = 8

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

TOTAL_PAGES_RE = re.compile(
    r"easysite-total-page.*?<b>\s*\d+\s*</b>\s*/\s*<b>\s*(\d+)\s*</b>",
    re.S,
)


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
        return False, True
    if d > upper:
        return False, False
    return True, False


def build_unique_key(institute_name: str, notice_type: str, title: str, disclose_date: str, source_link: str) -> str:
    return (
        f"{institute_name}+{notice_type}+{sanitize_text(title, 300)}+"
        f"{disclose_date}+{_normalize(source_link)}"
    )


def extract_codes_from_text(text: str) -> Tuple[str, str]:
    content = str(text or "")
    product_code = ""
    sales_code = ""

    patterns = [
        r"产品代码[:：\s]*([A-Za-z0-9_-]+)",
        r"产品编号[:：\s]*([A-Za-z0-9_-]+)",
        r"登记编码[:：\s]*([A-Za-z0-9_-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, content)
        if m:
            product_code = m.group(1).strip()
            break

    m = re.search(r"销售代码[:：\s]*([A-Za-z0-9_-]+)", content)
    if m:
        sales_code = m.group(1).strip()

    return product_code, sales_code


def extract_codes_from_pdf(path: str) -> Tuple[str, str]:
    if pdfplumber is None:
        return "", ""
    try:
        with pdfplumber.open(path) as pdf:
            texts = []
            max_pages = min(len(pdf.pages), max(1, int(PDF_SCAN_PAGES)))
            for idx in range(max_pages):
                page = pdf.pages[idx]
                text = page.extract_text() or ""
                if text:
                    texts.append(text)
            return extract_codes_from_text("\n".join(texts))
    except Exception:
        return "", ""


def resolve_categories_to_run() -> List[str]:
    if not RUN_ONLY_CATEGORIES:
        return list(CATEGORIES.keys())
    result: List[str] = []
    for key in RUN_ONLY_CATEGORIES:
        k = str(key or "").strip()
        if not k:
            continue
        if k in CATEGORIES and k not in result:
            result.append(k)
    return result


def build_base_filename(
    institute_name: str,
    notice_title: str,
    notice_type: str,
    product_code: str,
    sales_code: str,
    disclose_date: str,
) -> str:
    date_token = re.sub(r"[^0-9]", "", disclose_date)

    title = sanitize_text(notice_title, 180) or "公告"
    product_part = f"产品代码:{product_code}" if product_code else ""
    sales_part = f"销售代码:{sales_code}" if sales_code else ""

    parts = [
        sanitize_text(institute_name, 80),
        title,
        sanitize_text(notice_type, 60),
        sanitize_text(product_part, 80),
        sanitize_text(sales_part, 80),
        date_token,
    ]
    filename = "_".join([p for p in parts if p])
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


def try_rename_with_codes(
    save_path: str,
    notice_title: str,
    notice_type: str,
    disclose_date: str,
    product_code: str,
    sales_code: str,
) -> Tuple[str, str, str, str]:
    ext = os.path.splitext(save_path)[1].lower()
    if ext != ".pdf":
        return save_path, product_code, sales_code, os.path.basename(save_path)

    if product_code or sales_code:
        return save_path, product_code, sales_code, os.path.basename(save_path)

    pdf_product, pdf_sales = extract_codes_from_pdf(save_path)
    if not pdf_product and not pdf_sales:
        return save_path, product_code, sales_code, os.path.basename(save_path)

    new_product = product_code or pdf_product
    new_sales = sales_code or pdf_sales
    new_base = build_base_filename(
        INSTITUTE_NAME,
        notice_title,
        notice_type,
        new_product,
        new_sales,
        disclose_date,
    )

    folder = os.path.dirname(save_path)
    new_path, new_file = build_unique_save_path(folder, new_base, ext)
    os.replace(save_path, new_path)
    return new_path, new_product, new_sales, new_file


def build_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": HOME_URL,
    }


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
            if not isinstance(data, dict):
                return {}
            if "categories" not in data:
                data["categories"] = {}
            return data
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


# =====================================
# 解析与下载
# =====================================


def fetch_html(session: requests.Session, url: str) -> str:
    last_error = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            resp = session.get(url, headers=build_headers(), timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as err:
            last_error = err
            if attempt < REQUEST_RETRY:
                print(f"    🔁 [重试] {attempt}/{REQUEST_RETRY} {url}")
                time.sleep(RETRY_WAIT_SECONDS)
            else:
                raise last_error


def parse_total_pages(html: str) -> Optional[int]:
    soup = BeautifulSoup(html, "html.parser")
    input_tag = soup.find("input", attrs={"name": "article_paging_list_hidden"})
    if input_tag:
        total_raw = input_tag.get("totalpage")
        if total_raw:
            try:
                return int(total_raw)
            except ValueError:
                pass

    match = TOTAL_PAGES_RE.search(html)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def extract_module_id(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    input_tag = soup.find("input", attrs={"name": "article_paging_list_hidden"})
    return str(input_tag.get("moduleid") or "").strip() if input_tag else ""


def parse_list_items(html: str, category: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []

    for li in soup.select("li.easysite-article-li"):
        link = li.find("a", href=True)
        date_tag = li.find("span", class_="article-time")
        if not link or not date_tag:
            continue

        href = link.get("href", "").strip()
        if f"/cpgg/{category}/" not in href:
            continue

        title = link.get_text(strip=True)
        date = date_tag.get_text(strip=True)
        items.append(
            {
                "title": title,
                "date": date,
                "detail_url": urljoin(BASE_URL, href),
            }
        )

    return items


def detect_page_token(html: str, category: str) -> Optional[str]:
    token_pattern = rf"/urcb/lm1/cfcp1/zylc/cpgg/{category}/([a-z0-9]+)-2\\.shtml"

    soup = BeautifulSoup(html, "html.parser")
    link = soup.find(
        "a",
        attrs={"tagname": re.compile(token_pattern, re.I)},
    )
    if link:
        tagname = link.get("tagname") or ""
        match = re.search(token_pattern, tagname, re.I)
        if match:
            return match.group(1)

    onclick_match = re.search(
        rf"queryArticleByCondition\(this,'({token_pattern})'\)",
        html,
        re.I,
    )
    if onclick_match:
        return onclick_match.group(2)

    match = re.search(token_pattern, html, re.I)
    if match:
        return match.group(1)

    module_input = soup.find("input", attrs={"name": "article_paging_list_hidden"})
    if module_input:
        module_id = str(module_input.get("moduleid") or "").strip()
        if module_id and len(module_id) >= 8:
            return module_id[:8]

    fixed_token = CATEGORY_PAGE_TOKENS.get(category)
    if fixed_token:
        return fixed_token

    return None


def build_page_url(category: str, token: str, page: int) -> str:
    base_path = f"/urcb/lm1/cfcp1/zylc/cpgg/{category}/"
    return urljoin(BASE_URL, f"{base_path}{token}-{page}.shtml")


def extract_pdf_links(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if ".pdf" in href.lower() or "/attachdir/" in href.lower():
            links.append(urljoin(BASE_URL, href))
    deduped = []
    seen = set()
    for url in links:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def guess_extension_from_headers(response: requests.Response) -> str:
    ct = (response.headers.get("Content-Type") or "").lower()
    if "pdf" in ct:
        return ".pdf"
    if "msword" in ct:
        return ".doc"
    if "wordprocessingml" in ct:
        return ".docx"
    if "excel" in ct:
        return ".xls"
    if "spreadsheetml" in ct:
        return ".xlsx"
    if "zip" in ct:
        return ".zip"
    if "html" in ct:
        return ".html"
    return ".bin"


def extract_document_link_from_html(html_text: str, base_url: str) -> str:
    pattern = r"href\s*=\s*[\"']([^\"']+\.(?:pdf|doc|docx|xls|xlsx|zip))(?:\?[^\"']*)?[\"']"
    match = re.search(pattern, html_text, re.IGNORECASE)
    if not match:
        return ""
    href = match.group(1)
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(base_url, href)


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


# =====================================
# 任务处理
# =====================================


def process_one_detail(
    session: requests.Session,
    browser: ChromiumPage,
    category_key: str,
    item: dict,
    downloaded_links: set,
    downloaded_fps: set,
):
    notice_type = CATEGORIES[category_key]["name"]
    folder_path = os.path.join(DOWNLOAD_ROOT, CATEGORIES[category_key]["folder"])
    ensure_dir(folder_path)

    title = str(item.get("title") or "").strip() or "公告"
    disclose_date = normalize_date(str(item.get("date") or ""))
    detail_url = str(item.get("detail_url") or "").strip()
    product_code, sales_code = extract_codes_from_text(title)

    _in_range, _too_old = is_in_date_range(disclose_date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"   ⏭️ [日期跳过] 披露日期 {disclose_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
        return {"succeed": 0, "skipped": 1, "too_old": _too_old}

    if TEST_ONLY_DETAIL_URL and detail_url != TEST_ONLY_DETAIL_URL:
        return {"succeed": 0, "skipped": 1}

    print(f"  🔎 [详情] {disclose_date} {title}")

    detail_html = fetch_html(session, detail_url)
    html_product, html_sales = extract_codes_from_text(detail_html)
    if html_product:
        product_code = html_product
    if html_sales:
        sales_code = html_sales
    pdf_links = extract_pdf_links(detail_html)

    if not pdf_links:
        source_link = detail_url
        unique_key = build_unique_key(INSTITUTE_NAME, notice_type, title, disclose_date, source_link)
        if SKIP_DOWNLOADED:
            if _normalize(link_key(source_link)) in downloaded_links:
                print("    ⏭️ [跳过] HTML 详情已下载")
                return {"succeed": 0, "skipped": 1}
            if _normalize(unique_key_fingerprint(unique_key)) in downloaded_fps:
                print("    ⏭️ [跳过] 指纹已存在")
                return {"succeed": 0, "skipped": 1}

        base_file_name = build_base_filename(
            INSTITUTE_NAME,
            title,
            notice_type,
            product_code,
            sales_code,
            disclose_date,
        )
        save_path, file_name = build_unique_save_path(folder_path, base_file_name, ".pdf")
        print(f"    🖨️ [渲染] HTML 转 PDF -> {file_name}")
        render_html_to_pdf(browser, detail_url, save_path)
        if os.path.getsize(save_path) < 1024:
            raise RuntimeError("HTML 转 PDF 结果过小，疑似失败")

        save_path, product_code, sales_code, file_name = try_rename_with_codes(
            save_path,
            title,
            notice_type,
            disclose_date,
            product_code,
            sales_code,
        )

        write_log_row(
            INSTITUTE_NAME,
            title,
            notice_type,
            disclose_date,
            "SUCCEED",
            source_link,
            save_path,
            unique_key,
        )
        save_downloaded_link(source_link)
        save_fingerprint(unique_key)
        downloaded_links.add(_normalize(link_key(source_link)))
        downloaded_fps.add(_normalize(unique_key_fingerprint(unique_key)))
        print(f"    ✅ [完成] {save_path}")
        return {"succeed": 1, "skipped": 0}

    succeed = 0
    skipped = 0
    for idx, pdf_link in enumerate(pdf_links, start=1):
        source_link = pdf_link
        unique_key = build_unique_key(INSTITUTE_NAME, notice_type, title, disclose_date, source_link)
        if SKIP_DOWNLOADED:
            if _normalize(link_key(source_link)) in downloaded_links:
                skipped += 1
                print(f"    ⏭️ [跳过] 附件{idx} 已下载")
                continue
            if _normalize(unique_key_fingerprint(unique_key)) in downloaded_fps:
                skipped += 1
                print(f"    ⏭️ [跳过] 附件{idx} 指纹已存在")
                continue

        suffix = f"_附件{idx}" if len(pdf_links) > 1 else ""
        base_file_name = build_base_filename(
            INSTITUTE_NAME,
            f"{title}{suffix}",
            notice_type,
            product_code,
            sales_code,
            disclose_date,
        )
        print(f"    📥 [下载] 附件{idx}/{len(pdf_links)}")
        final_url, save_path, _ = download_file_with_fallback(
            session, browser, pdf_link, folder_path, base_file_name
        )

        save_path, product_code, sales_code, _ = try_rename_with_codes(
            save_path,
            title,
            notice_type,
            disclose_date,
            product_code,
            sales_code,
        )

        write_log_row(
            INSTITUTE_NAME,
            title,
            notice_type,
            disclose_date,
            "SUCCEED",
            final_url,
            save_path,
            unique_key,
        )
        save_downloaded_link(final_url)
        save_fingerprint(unique_key)
        downloaded_links.add(_normalize(link_key(final_url)))
        downloaded_fps.add(_normalize(unique_key_fingerprint(unique_key)))
        succeed += 1
        print(f"    ✅ [完成] {save_path}")
        random_sleep(ITEM_INTERVAL_SECONDS)

    return {"succeed": succeed, "skipped": skipped}


def process_failed_tasks(session: requests.Session, browser: ChromiumPage):
    tasks = load_failed_tasks()
    if not tasks:
        return

    print(f"\n🧯 [失败任务] 发现 {len(tasks)} 条，开始重试...")
    remaining = []
    downloaded_links = load_downloaded_links() if SKIP_DOWNLOADED else set()
    downloaded_fps = load_downloaded_fingerprints() if SKIP_DOWNLOADED else set()

    for task in tasks:
        try:
            process_one_detail(
                session=session,
                browser=browser,
                category_key=task["category"],
                item=task["item"],
                downloaded_links=downloaded_links,
                downloaded_fps=downloaded_fps,
            )
        except Exception as err:
            task["last_error"] = str(err)
            remaining.append(task)

    save_failed_tasks(remaining)
    if remaining:
        print(f"🧯 [失败任务] 剩余 {len(remaining)} 条未完成")


# =====================================
# 主流程
# =====================================


def main():
    ensure_dir(DOWNLOAD_ROOT)

    session = requests.Session()
    session.headers.update(build_headers())

    browser = _new_page()

    process_failed_tasks(session, browser)

    downloaded_links = load_downloaded_links() if SKIP_DOWNLOADED else set()
    downloaded_fps = load_downloaded_fingerprints() if SKIP_DOWNLOADED else set()

    checkpoint = load_checkpoint()
    category_states = checkpoint.get("categories", {})

    categories_to_run = resolve_categories_to_run()
    if not categories_to_run:
        print("⚠️ [配置] RUN_ONLY_CATEGORIES 未匹配任何有效模块，程序结束")
        return

    print(f"🧭 [模块] 本次运行: {', '.join(categories_to_run)}")

    for category_key in categories_to_run:
        conf = CATEGORIES[category_key]
        print(f"\n🚀 [开始] {conf['name']}")
        base_url = conf["url"]

        page1_html = fetch_html(session, base_url)
        total_pages = parse_total_pages(page1_html) or 1
        token = detect_page_token(page1_html, category_key)
        module_id = extract_module_id(page1_html)

        print(f"  ℹ️  [信息] 总页数: {total_pages}")
        print(f"  ℹ️  [信息] 分页 token: {token or '未识别'}")
        if module_id:
            print(f"  ℹ️  [信息] moduleid: {module_id}")

        state = category_states.get(category_key, {})
        start_page = max(1, int(state.get("next_page") or 1))
        start_index = max(0, int(state.get("next_index") or 0))
        last_detail_url = str(state.get("last_detail_url") or "").strip()
        print(f"  ℹ️  [信息] 断点页: {start_page}")
        if start_index:
            print(f"  ℹ️  [信息] 断点条目: {start_index}")
        if last_detail_url:
            print(f"  ℹ️  [信息] 上次详情: {last_detail_url}")

        category_succeed = 0
        category_skipped = 0
        category_failed = 0
        early_stop_triggered = False

        if start_page == 1:
            page_items = parse_list_items(page1_html, category_key)
            print(f"  📄 [列表] 第1页条目数: {len(page_items)}")
            for idx, item in enumerate(page_items):
                if start_index and idx < start_index:
                    continue
                try:
                    result = process_one_detail(
                        session, browser, category_key, item, downloaded_links, downloaded_fps
                    )
                    category_succeed += result.get("succeed", 0)
                    category_skipped += result.get("skipped", 0)
                    if result.get("too_old") and EARLY_STOP:
                        print(f"      ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stop_triggered = True
                        break
                except Exception as err:
                    category_failed += 1
                    write_failed_row(str(err), item.get("title", ""), item.get("detail_url", ""), "")
                    tasks = load_failed_tasks()
                    tasks.append({"category": category_key, "item": item, "last_error": str(err)})
                    save_failed_tasks(tasks)
                    print(f"    ❌ [失败] {err}")
                random_sleep(ITEM_INTERVAL_SECONDS)

                checkpoint["categories"] = category_states
                category_states[category_key] = {
                    "next_page": 1,
                    "next_index": idx + 1,
                    "last_detail_url": str(item.get("detail_url") or ""),
                    "updated_at": now_time_str(),
                }
                save_checkpoint(checkpoint)

            checkpoint["categories"] = category_states
            category_states[category_key] = {
                "next_page": 2,
                "next_index": 0,
                "updated_at": now_time_str(),
            }
            save_checkpoint(checkpoint)

        if early_stop_triggered:
            print(
                f"  📊 [统计] 成功: {category_succeed}  跳过: {category_skipped}  失败: {category_failed}"
            )
            continue

        if total_pages <= 1:
            print(
                f"  📊 [统计] 成功: {category_succeed}  跳过: {category_skipped}  失败: {category_failed}"
            )
            continue

        if not token:
            print("  ⚠️  [提示] 未识别分页 token，仅抓取第一页")
            print(
                f"  📊 [统计] 成功: {category_succeed}  跳过: {category_skipped}  失败: {category_failed}"
            )
            continue

        for page in range(max(2, start_page), total_pages + 1):
            page_url = build_page_url(category_key, token, page)
            try:
                html = fetch_html(session, page_url)
            except Exception as err:
                print(f"  ❌ [失败] 第{page}页请求异常: {err}")
                time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
                continue

            items = parse_list_items(html, category_key)
            print(f"  📄 [列表] 第{page}/{total_pages}页条目数: {len(items)}")
            if not items:
                print("  ✅ [完成] 翻到空页，判定已结束，停止翻页")
                break

            for idx, item in enumerate(items):
                if page == start_page and start_index and idx < start_index:
                    continue
                try:
                    result = process_one_detail(
                        session, browser, category_key, item, downloaded_links, downloaded_fps
                    )
                    category_succeed += result.get("succeed", 0)
                    category_skipped += result.get("skipped", 0)
                    if result.get("too_old") and EARLY_STOP:
                        print(f"      ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                        early_stop_triggered = True
                        break
                except Exception as err:
                    category_failed += 1
                    write_failed_row(str(err), item.get("title", ""), item.get("detail_url", ""), "")
                    tasks = load_failed_tasks()
                    tasks.append({"category": category_key, "item": item, "last_error": str(err)})
                    save_failed_tasks(tasks)
                    print(f"    ❌ [失败] {err}")
                random_sleep(ITEM_INTERVAL_SECONDS)

                checkpoint["categories"] = category_states
                category_states[category_key] = {
                    "next_page": page,
                    "next_index": idx + 1,
                    "last_detail_url": str(item.get("detail_url") or ""),
                    "updated_at": now_time_str(),
                }
                save_checkpoint(checkpoint)

            checkpoint["categories"] = category_states
            category_states[category_key] = {
                "next_page": page + 1,
                "next_index": 0,
                "updated_at": now_time_str(),
            }
            save_checkpoint(checkpoint)

            random_sleep(PAGE_INTERVAL_SECONDS)

            if early_stop_triggered:
                break

        print(
            f"  📊 [统计] 成功: {category_succeed}  跳过: {category_skipped}  失败: {category_failed}"
        )

    print("\n🎉 ✅ 全部任务完成")


if __name__ == "__main__":
    main()
