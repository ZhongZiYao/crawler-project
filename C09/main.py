
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

# =====================================
# 用户配置区
# =====================================

# TODO[手动修改]：机构标准名称（请使用统一标准全称）
INSTITUTE_NAME = "渝农商理财"

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
    START_DATE = PROJECT_START_DATE.get("C09", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("C09", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False
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

# TODO[手动修改]：模块开关（中文名，True=开启，False=关闭）
ENABLE_MODULES = {
    "发行公告": False,
    "运作公告": True,
    "到期公告": False,
    "净值公告": False,
    "产品说明书": True,
}

# TODO[手动修改]：单模块测试名单（中文模块名）；为空时按 ENABLE_MODULES 执行
# 示例：RUN_ONLY_MODULES = ["发行公告"]
RUN_ONLY_MODULES: List[str] = []

# TODO[手动修改]：重试与等待参数（可按站点稳定性调整）
REQUEST_TIMEOUT = 25
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
NO_PDF_WAIT_SECONDS = 10
REQUEST_INTERVAL_SECONDS = (0.8, 1.6)
PAGE_INTERVAL_SECONDS = (1.2, 2.0)
MAX_PAGES_PER_MODULE: Optional[int] = None

BASE_URL = "http://wm.cqrcb.com"

CATEGORIES: Dict[str, Dict[str, str]] = {
    "fxgg": {
        "name": "发行公告",
        "url": "http://wm.cqrcb.com/cn/disclosure/xxcpgg/fxgg/index.html",
    },
    "yzgg": {
        "name": "运作公告",
        "url": "http://wm.cqrcb.com/cn/disclosure/xxcpgg/yzgg/index.html",
    },
    "dqgg": {
        "name": "到期公告",
        "url": "http://wm.cqrcb.com/cn/disclosure/xxcpgg/dqgg/index.html",
    },
    "jzgg": {
        "name": "净值公告",
        "url": "http://wm.cqrcb.com/cn/disclosure/xxcpgg/tgxxgg/index.html",
    },
    "cpsms": {
        "name": "产品说明书",
        "url": "http://wm.cqrcb.com/cn/disclosure/cpsms/index.html",
    },
}

CHINESE_NAME_TO_KEY = {
    "发行公告": "fxgg",
    "运作公告": "yzgg",
    "到期公告": "dqgg",
    "净值公告": "jzgg",
    "产品说明书": "cpsms",
}

ROOT = Path(__file__).resolve().parent
DOWNLOAD_ROOT = ROOT / "download_files"
LOG_CSV = ROOT / f"{INSTITUTE_NAME}_下载记录.csv"
PROGRESS_FILE = DOWNLOAD_ROOT / "downloaded_links.txt"
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")

LIST_ITEM_PATTERN = re.compile(r"location\s*=\s*['\"]([^'\"]+)['\"]")

# ============ 产品说明书（cpsms）模块专用常量 ============
# 需要排除的 PDF 关键词（页脚固定链接，非产品说明书；来自补丁脚本 download_prod_manual.py）
EXCLUDE_PDF_KEYWORDS = ["客户投诉处理流程", "投诉处理"]

# 兜底页数：仅当站点未提供总页数（#totalPage / page-wrap 解析失败）时使用。
# 正常优先使用站点总页数 + [START_DATE, END_DATE] 日期过滤（列表页遇到早于 START_DATE 的条目即早停翻页）。
CPSMS_FALLBACK_TOTAL_PAGES = 28


@dataclass
class ListItem:
    category_key: str
    category_name: str
    title: str
    publish_date: str
    detail_url: str
    list_page_url: str


def random_sleep(seconds_range: Tuple[float, float]) -> None:
    time.sleep(random.uniform(seconds_range[0], seconds_range[1]))


def now_time_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sanitize_text(text: str, max_len: int = 240) -> str:
    text = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        return "未命名"
    return text[:max_len]


def normalize_title(title: str) -> str:
    return sanitize_text(title, max_len=240)


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


def parse_product_name_and_sales_code(title: str, notice_type: str) -> Tuple[str, str]:
    clean = normalize_title(title)
    code_patterns = [
        r"(?:销售代码|产品代码|代码)[：:\s]*([A-Za-z0-9_-]+)",
        r"第\s*([A-Za-z0-9]{4,})\s*期",
    ]

    sales_code = ""
    for ptn in code_patterns:
        m = re.search(ptn, clean)
        if m:
            sales_code = sanitize_text(m.group(1), max_len=60)
            break

    product_name = clean
    product_name = re.sub(re.escape(notice_type), "", product_name)
    product_name = re.sub(r"(发行公告|运作公告|到期公告|净值公告)$", "", product_name)
    product_name = re.sub(r"[（(]\s*(销售代码|产品代码|代码)[：:][^)）]+[)）]", "", product_name)
    product_name = product_name.strip(" _-")
    if not product_name:
        product_name = clean

    return sanitize_text(product_name, max_len=150), sales_code


def build_unique_key(institute_name: str, notice_type: str, title: str, disclose_date: str) -> str:
    return f"{institute_name}+{notice_type}+{normalize_title(title)}+{disclose_date}"


def build_base_filename(institute_name: str, product_name: str, notice_type: str, sales_code: str) -> str:
    parts = [
        sanitize_text(institute_name, 80),
        sanitize_text(product_name, 150),
        sanitize_text(notice_type, 40),
    ]
    if sales_code:
        parts.append(sanitize_text(sales_code, 60))
    return "_".join(parts)


def build_unique_save_path(folder: Path, base_name: str, extension: str) -> Path:
    ext = extension if extension.startswith(".") else f".{extension}"
    ext = ext.lower()

    idx = 0
    while True:
        filename = f"{base_name}{ext}" if idx == 0 else f"{base_name}_{idx}{ext}"
        save_path = folder / filename
        if not save_path.exists():
            return save_path
        idx += 1


def canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""

    absolute = urljoin(BASE_URL, raw)
    parts = urlsplit(absolute)
    path = quote(unquote(parts.path or ""), safe="/%:@!$&'()*+,;=-._~")
    query = quote(unquote(parts.query or ""), safe="=&:%@!$&'()*+,;/-._~")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def guess_extension(url: str, content_type: str, default_ext: str = ".pdf") -> str:
    ct = (content_type or "").lower()
    parsed = urlparse(url)
    path_ext = Path(unquote(parsed.path)).suffix.lower()

    if "pdf" in ct:
        return ".pdf"
    if "officedocument.wordprocessingml.document" in ct:
        return ".docx"
    if "msword" in ct or "application/doc" in ct:
        return ".doc"
    if path_ext:
        return path_ext
    return default_ext


class CQRCBCrawler:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
        )

        self._prepare_dirs()
        self._ensure_log_header()
        self.downloaded_links = self._load_downloaded_links()

    def _prepare_dirs(self) -> None:
        DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        for cfg in CATEGORIES.values():
            (DOWNLOAD_ROOT / cfg["name"]).mkdir(parents=True, exist_ok=True)

    def _ensure_log_header(self) -> None:
        if LOG_CSV.exists():
            return
        with LOG_CSV.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
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
            )

    def _load_downloaded_links(self) -> Set[str]:
        links: Set[str] = set()

        if PROGRESS_FILE.exists():
            for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
                link = canonicalize_url(line.strip())
                if link:
                    links.add(link)

        if LOG_CSV.exists():
            try:
                with LOG_CSV.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if str(row.get("状态") or "").strip().upper() == "SUCCEED":
                            src = canonicalize_url(str(row.get("来源链接") or "").strip())
                            if src:
                                links.add(src)
            except Exception:
                pass

        return links

    def _save_downloaded_link(self, source_link: str) -> None:
        source_link = canonicalize_url(source_link)
        if not source_link:
            return
        with PROGRESS_FILE.open("a", encoding="utf-8") as f:
            f.write(source_link + "\n")

    def _add_dedupe_links(self, *links: str) -> None:
        for link in links:
            norm = canonicalize_url(link)
            if not norm:
                continue
            if norm not in self.downloaded_links:
                self.downloaded_links.add(norm)
                self._save_downloaded_link(norm)

    def _is_already_downloaded(self, *links: str) -> bool:
        for link in links:
            norm = canonicalize_url(link)
            if norm and norm in self.downloaded_links:
                return True
        return False

    def _log_record(
        self,
        notice_title: str,
        notice_type: str,
        disclose_date: str,
        status: str,
        source_link: str,
        save_path: str,
    ) -> None:
        unique_key = build_unique_key(INSTITUTE_NAME, notice_type, notice_title, disclose_date)
        with LOG_CSV.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    INSTITUTE_NAME,
                    normalize_title(notice_title),
                    notice_type,
                    normalize_date(disclose_date),
                    now_time_str(),
                    status,
                    canonicalize_url(source_link),
                    save_path,
                    unique_key,
                ]
            )

    def _request_text(self, url: str) -> Optional[str]:
        last_error = None
        for attempt in range(1, REQUEST_RETRY + 1):
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                if not resp.encoding or resp.encoding.lower() in {"iso-8859-1", "ascii"}:
                    resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text
            except Exception as err:
                last_error = err
                if attempt < REQUEST_RETRY:
                    print(f"⚠️ 请求失败（{attempt}/{REQUEST_RETRY}）：{url}")
                    print(f"   ↪️ {RETRY_WAIT_SECONDS}s 后重试，错误：{err}")
                    time.sleep(RETRY_WAIT_SECONDS)
                else:
                    print(f"❌ 请求最终失败：{url} | {last_error}")
        return None

    def _request_binary(self, url: str) -> Tuple[Optional[bytes], str, str]:
        last_error = None
        for attempt in range(1, DOWNLOAD_RETRY + 1):
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                resp.raise_for_status()
                return resp.content, resp.url, (resp.headers.get("Content-Type") or "")
            except Exception as err:
                last_error = err
                if attempt < DOWNLOAD_RETRY:
                    print(f"⚠️ 下载失败（{attempt}/{DOWNLOAD_RETRY}）：{url}")
                    print(f"   ↪️ {RETRY_WAIT_SECONDS}s 后重试，错误：{err}")
                    time.sleep(RETRY_WAIT_SECONDS)
                else:
                    print(f"❌ 下载最终失败：{url} | {last_error}")

        return None, canonicalize_url(url), ""

    @staticmethod
    def _make_page_url(index_url: str, page_no: int) -> str:
        if page_no <= 1:
            return index_url
        return index_url.replace("index.html", f"index_{page_no}.html")

    @staticmethod
    def _parse_total_pages(soup: BeautifulSoup) -> int:
        total = soup.select_one("#totalPage")
        if total and total.get("value", "").isdigit():
            return int(total["value"])

        page_text = soup.select_one(".page-wrap .page")
        if page_text:
            m = re.search(r"\d+\s*/\s*(\d+)", page_text.get_text(strip=True))
            if m:
                return int(m.group(1))
        return 1

    def _parse_list_items(self, html: str, list_url: str, category_key: str, category_name: str) -> List[ListItem]:
        soup = BeautifulSoup(html, "lxml")
        items: List[ListItem] = []
        for div in soup.select("div.list-item"):
            onclick = div.get("onclick", "")
            m = LIST_ITEM_PATTERN.search(onclick)
            if not m:
                continue

            detail_path = m.group(1).strip()
            detail_url = urljoin(BASE_URL, detail_path)
            title_el = div.select_one("span.text")
            date_el = div.select_one("span.time")

            title = (title_el.get("title") or title_el.get_text(strip=True)) if title_el else ""
            publish_date = normalize_date(date_el.get_text(strip=True) if date_el else "")
            if not detail_url:
                continue

            items.append(
                ListItem(
                    category_key=category_key,
                    category_name=category_name,
                    title=title,
                    publish_date=publish_date,
                    detail_url=detail_url,
                    list_page_url=list_url,
                )
            )
        return items

    @staticmethod
    def _extract_pdf_link(detail_html: str, detail_url: str) -> Optional[str]:
        soup = BeautifulSoup(detail_html, "lxml")
        for a in soup.select("div.product-text a[href], .product-text a[href], a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            abs_url = canonicalize_url(urljoin(detail_url, href))
            lower = abs_url.lower()
            if lower.endswith((".pdf", ".doc", ".docx")) or "/data/tosend/resource/upload/" in lower:
                return abs_url
        return None

    @staticmethod
    def _extract_manual_pdf_link(detail_html: str, detail_url: str) -> Optional[str]:
        """产品说明书（cpsms）专用：在 div.product-text 内找 PDF 直链。

        - 只接受 .pdf 链接
        - 排除页脚固定链接（EXCLUDE_PDF_KEYWORDS / 渝农商理财客户投诉）
        - product-text 未命中时兜底全文搜索
        """
        soup = BeautifulSoup(detail_html, "lxml")

        product_text = soup.select_one("div.product-text")
        if product_text:
            for a in product_text.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                lower = href.lower()
                if not (lower.endswith(".pdf") or ".pdf" in lower):
                    continue
                if any(kw in href for kw in EXCLUDE_PDF_KEYWORDS):
                    continue
                if "渝农商理财客户投诉" in href:
                    continue
                return canonicalize_url(urljoin(detail_url, href))

        # 兜底：全文搜索 PDF 链接（同样排除页脚固定链接）
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href.lower().endswith(".pdf"):
                continue
            if any(kw in href for kw in EXCLUDE_PDF_KEYWORDS):
                continue
            if "渝农商理财客户投诉" in href:
                continue
            return canonicalize_url(urljoin(detail_url, href))

        return None

    def _resolve_pdf_with_retry(self, item: ListItem) -> Tuple[Optional[str], bool]:
        """返回 (pdf_url, detail_ok)。"""
        detail_ok = False
        for attempt in range(1, REQUEST_RETRY + 1):
            detail_html = self._request_text(item.detail_url)
            if not detail_html:
                if attempt < REQUEST_RETRY:
                    print(f"   🔁 详情页拉取失败，{RETRY_WAIT_SECONDS}s 后重试")
                    time.sleep(RETRY_WAIT_SECONDS)
                continue

            detail_ok = True
            if item.category_key == "cpsms":
                pdf_url = self._extract_manual_pdf_link(detail_html, item.detail_url)
            else:
                pdf_url = self._extract_pdf_link(detail_html, item.detail_url)
            if pdf_url:
                return pdf_url, True

            if attempt < REQUEST_RETRY:
                print(f"   ⏳ 本轮未找到可下载文件（PDF/DOC/DOCX），等待 {NO_PDF_WAIT_SECONDS}s 后继续尝试")
                time.sleep(NO_PDF_WAIT_SECONDS)

        return None, detail_ok

    def process_one_notice(self, item: ListItem) -> str:
        notice_type = item.category_name
        title = normalize_title(item.title)
        disclose_date = normalize_date(item.publish_date)

        _in_range, _too_old = is_in_date_range(disclose_date)
        if not _in_range:
            tag = "过早(早停)" if _too_old else "过晚"
            print(f"  ⏭️ [日期跳过] 披露日期 {disclose_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
            return "too_old" if _too_old else "skip"

        product_name, sales_code = parse_product_name_and_sales_code(title, notice_type)
        base_name = build_base_filename(INSTITUTE_NAME, product_name, notice_type, sales_code)
        save_folder = DOWNLOAD_ROOT / notice_type

        print(f"  📌 公告：{title}")
        print(f"     🗓️ 披露日期：{disclose_date}")

        pdf_url, detail_ok = self._resolve_pdf_with_retry(item)
        source_link = canonicalize_url(pdf_url or item.detail_url)

        if self._is_already_downloaded(source_link, pdf_url, item.detail_url):
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="SUCCEED",
                source_link=source_link,
                save_path=f"已去重跳过；期望路径：{expected}",
            )
            print(f"  ⏭️ 去重跳过：{source_link}")
            return "skipped"

        if not pdf_url:
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            reason_link = item.detail_url if detail_ok else source_link
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="FAILED",
                source_link=reason_link,
                save_path=expected,
            )
            print("  ❌ 未找到可下载文件（PDF/DOC/DOCX），已记录失败")
            return "failed"

        binary_data, final_url, content_type = self._request_binary(pdf_url)
        if not binary_data:
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="FAILED",
                source_link=pdf_url,
                save_path=expected,
            )
            print("  ❌ 文件下载失败，已记录")
            return "failed"

        ext = guess_extension(final_url, content_type, default_ext=".pdf")
        allowed_exts = {".pdf", ".doc", ".docx"}
        if ext not in allowed_exts:
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="FAILED",
                source_link=final_url,
                save_path=expected,
            )
            print(f"  ❌ 下载结果后缀不支持（{ext}），已记录失败")
            return "failed"

        save_path = build_unique_save_path(save_folder, base_name, ext)
        save_path.write_bytes(binary_data)

        self._add_dedupe_links(final_url, pdf_url, item.detail_url)
        self._log_record(
            notice_title=title,
            notice_type=notice_type,
            disclose_date=disclose_date,
            status="SUCCEED",
            source_link=final_url,
            save_path=str(save_path.resolve()),
        )
        print(f"  ✅ 下载成功：{save_path.name}")
        return "succeed"

    def process_one_manual(self, item: ListItem) -> str:
        """产品说明书（cpsms）专用处理分支。

        与 process_one_notice 的差异：
        - 先按详情页 URL 去重，命中则不再请求详情页（沿用补丁脚本行为）
        - 详情页仅在 div.product-text 内找 PDF，排除投诉类页脚固定链接
        - 只接受 .pdf 结果，且文件不小于 100 字节
        - 命名规则：{机构}_{标题}_产品说明书_{披露日期}.pdf
        """
        notice_type = item.category_name
        title = normalize_title(item.title)
        disclose_date = normalize_date(item.publish_date)

        _in_range, _too_old = is_in_date_range(disclose_date)
        if not _in_range:
            tag = "过早(早停)" if _too_old else "过晚"
            print(f"  ⏭️ [日期跳过] 披露日期 {disclose_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
            return "too_old" if _too_old else "skip"

        base_name = "_".join(
            [
                sanitize_text(INSTITUTE_NAME, 80),
                sanitize_text(title, 180),
                "产品说明书",
                disclose_date,
            ]
        )
        save_folder = DOWNLOAD_ROOT / notice_type

        print(f"  📌 产品说明书：{title}")
        print(f"     🗓️ 披露日期：{disclose_date}")

        # 先按详情页 URL 去重（补丁脚本行为），避免重复请求详情页
        if self._is_already_downloaded(item.detail_url):
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="SUCCEED",
                source_link=item.detail_url,
                save_path=f"已去重跳过；期望路径：{expected}",
            )
            print(f"  ⏭️ 去重跳过：{item.detail_url}")
            return "skipped"

        pdf_url, detail_ok = self._resolve_pdf_with_retry(item)
        source_link = canonicalize_url(pdf_url or item.detail_url)

        if self._is_already_downloaded(source_link, pdf_url):
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="SUCCEED",
                source_link=source_link,
                save_path=f"已去重跳过；期望路径：{expected}",
            )
            print(f"  ⏭️ 去重跳过：{source_link}")
            return "skipped"

        if not pdf_url:
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            reason_link = item.detail_url if detail_ok else source_link
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="FAILED",
                source_link=reason_link,
                save_path=expected,
            )
            print("  ❌ 未找到产品说明书 PDF，已记录失败")
            return "failed"

        binary_data, final_url, content_type = self._request_binary(pdf_url)
        if not binary_data:
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="FAILED",
                source_link=pdf_url,
                save_path=expected,
            )
            print("  ❌ 文件下载失败，已记录")
            return "failed"

        if len(binary_data) < 100:
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="FAILED",
                source_link=final_url,
                save_path=expected,
            )
            print(f"  ❌ 下载内容过小（{len(binary_data)} bytes），疑似无效文件，已记录失败")
            return "failed"

        ext = guess_extension(final_url, content_type, default_ext=".pdf")
        if ext != ".pdf":
            expected = str(build_unique_save_path(save_folder, base_name, ".pdf").resolve())
            self._log_record(
                notice_title=title,
                notice_type=notice_type,
                disclose_date=disclose_date,
                status="FAILED",
                source_link=final_url,
                save_path=expected,
            )
            print(f"  ❌ 产品说明书仅接受 PDF（实际后缀 {ext}），已记录失败")
            return "failed"

        save_path = build_unique_save_path(save_folder, base_name, ext)
        save_path.write_bytes(binary_data)

        self._add_dedupe_links(final_url, pdf_url, item.detail_url)
        self._log_record(
            notice_title=title,
            notice_type=notice_type,
            disclose_date=disclose_date,
            status="SUCCEED",
            source_link=final_url,
            save_path=str(save_path.resolve()),
        )
        print(f"  ✅ 下载成功：{save_path.name}")
        return "succeed"

    def crawl_category(self, category_key: str, max_pages: Optional[int], checkpoint: dict = None) -> Tuple[int, int, int, int]:
        conf = CATEGORIES[category_key]
        category_name = conf["name"]
        index_url = conf["url"]

        print("\n" + "=" * 72)
        print(f"🚀 开始抓取模块：{category_name}（{category_key}）")
        print("=" * 72)

        first_html = self._request_text(index_url)
        if not first_html:
            print(f"❌ 模块启动失败：{category_name} 首页请求失败")
            return 0, 0, 1, 0

        total_pages = self._parse_total_pages(BeautifulSoup(first_html, "lxml"))
        if category_key == "cpsms" and total_pages <= 1:
            total_pages = CPSMS_FALLBACK_TOTAL_PAGES
            print(f"📄 站点未提供总页数，产品说明书模块使用兜底页数：{total_pages}")
        if max_pages:
            total_pages = min(total_pages, max_pages)
        print(f"📄 计划抓取页数：{total_pages}")

        found = 0
        succeed = 0
        failed = 0
        skipped = 0

        # 断点续传：读取上次爬到的页码
        start_page = 1
        if checkpoint is not None:
            mod_cp = checkpoint.get("module_pages", {}).get(category_key, {})
            start_page = int(mod_cp.get("page_no", 1))
            if mod_cp.get("done"):
                print(f"[断点续传] {category_key} 已完成，跳过")
                return 0, 0, 0, 0
            if start_page > 1:
                print(f"[断点续传] {category_key} 从第 {start_page} 页继续")

        page_no = start_page
        stop_paging = False
        for page_no in range(start_page, total_pages + 1):
            list_url = self._make_page_url(index_url, page_no)
            html = first_html if page_no == 1 else self._request_text(list_url)
            if not html:
                failed += 1
                print(f"  ❌ 第{page_no}页获取失败")
                print(f"  ⏳ 翻页等待 {NO_PDF_WAIT_SECONDS}s 后继续")
                time.sleep(NO_PDF_WAIT_SECONDS)
                continue

            items = self._parse_list_items(html, list_url, category_key, category_name)
            if category_key == "cpsms" and not items:
                print(f"  第{page_no}页无数据，提前结束翻页")
                break
            print(f"\n📚 第 {page_no}/{total_pages} 页，共 {len(items)} 条")

            page_succeed = 0
            page_failed = 0
            page_skipped = 0

            for idx, item in enumerate(items, start=1):
                print(f"\n  🧾 处理 {idx}/{len(items)}")

                _in_range, _too_old = is_in_date_range(item.publish_date)
                if not _in_range:
                    skipped += 1
                    page_skipped += 1
                    if _too_old:
                        print(f"  ⏭️ [日期跳过] {item.title} 披露日期 {item.publish_date} < {START_DATE}")
                        if EARLY_STOP or category_key == "cpsms":
                            print(f"  ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                            stop_paging = True
                            break
                    else:
                        print(f"  ⏭️ [日期跳过] {item.title} 披露日期 {item.publish_date} > {END_DATE or '今天'}")
                    continue

                if category_key == "cpsms":
                    result = self.process_one_manual(item)
                else:
                    result = self.process_one_notice(item)
                found += 1

                if result == "succeed":
                    succeed += 1
                    page_succeed += 1
                elif result in ("skipped", "skip", "too_old"):
                    skipped += 1
                    page_skipped += 1
                else:
                    failed += 1
                    page_failed += 1

                random_sleep(REQUEST_INTERVAL_SECONDS)

            # 断点续传：每页处理完后保存进度
            if checkpoint is not None:
                checkpoint.setdefault("module_pages", {})[category_key] = {"page_no": page_no + 1, "done": False}
                checkpoint["current_module"] = category_key
                checkpoint["updated_at"] = now_str()
                save_checkpoint(CHECKPOINT_FILE, checkpoint)

            if stop_paging:
                break

            if page_succeed == 0 and page_skipped == 0 and page_failed > 0:
                print(f"  ⏳ 当前页未成功下载，等待 {NO_PDF_WAIT_SECONDS}s 后继续翻页")
                time.sleep(NO_PDF_WAIT_SECONDS)

            print(f"  📊 第{page_no}页汇总：成功={page_succeed} 失败={page_failed} 跳过={page_skipped}")
            random_sleep(PAGE_INTERVAL_SECONDS)

        print(f"✅ 模块完成：{category_name} | 发现={found} 成功={succeed} 失败={failed} 跳过={skipped}")

        # 断点续传：模块完成后标记 done
        if checkpoint is not None:
            checkpoint.setdefault("module_pages", {})[category_key] = {"page_no": page_no, "done": True}
            completed = checkpoint.setdefault("completed_modules", [])
            if category_key not in completed:
                completed.append(category_key)
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)

        return found, succeed, failed, skipped


def resolve_modules_to_run() -> List[str]:
    if RUN_ONLY_MODULES:
        keys = []
        for name in RUN_ONLY_MODULES:
            key = CHINESE_NAME_TO_KEY.get(name)
            if key and key in CATEGORIES:
                keys.append(key)
            else:
                print(f"⚠️ RUN_ONLY_MODULES 存在未知模块：{name}")
        return keys

    keys = []
    for cname, enabled in ENABLE_MODULES.items():
        if not enabled:
            continue
        mapped = CHINESE_NAME_TO_KEY.get(cname)
        if mapped:
            keys.append(mapped)
        else:
            print(f"⚠️ ENABLE_MODULES 存在未知模块：{cname}")
    return keys


def main() -> None:
    modules = resolve_modules_to_run()
    if not modules:
        raise SystemExit("未选择到可运行模块，请检查 ENABLE_MODULES 或 RUN_ONLY_MODULES。")

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

    print("=" * 72)
    print("🧭 渝农商理财公告爬虫启动（精简版）")
    print(f"🏛️ 机构名称：{INSTITUTE_NAME}")
    print(f"📦 执行模块：{','.join([CATEGORIES[k]['name'] for k in modules])}")
    print(f"📏 页数上限：{MAX_PAGES_PER_MODULE if MAX_PAGES_PER_MODULE else '按站点总页数'}")
    print(f"⏱️ 超时={REQUEST_TIMEOUT}s | 🔁 重试={REQUEST_RETRY}次 | ⌛ 失败等待={RETRY_WAIT_SECONDS}s")
    print(f"🕙 缺PDF等待={NO_PDF_WAIT_SECONDS}s | 🐢 请求节奏={REQUEST_INTERVAL_SECONDS}")
    print(f"🗂️ 下载目录：{DOWNLOAD_ROOT}")
    print(f"📝 记录文件：{LOG_CSV}")
    print("=" * 72)

    crawler = CQRCBCrawler()
    print(f"📚 历史成功链接去重池：{len(crawler.downloaded_links)}")

    total_found = 0
    total_succeed = 0
    total_failed = 0
    total_skipped = 0

    try:
        for key in modules:
            found, succeed, failed, skipped = crawler.crawl_category(key, max_pages=MAX_PAGES_PER_MODULE, checkpoint=checkpoint)
            total_found += found
            total_succeed += succeed
            total_failed += failed
            total_skipped += skipped
    except KeyboardInterrupt:
        print("\n[中断] 收到 Ctrl+C，断点已保存，下次运行将从断点处继续")
        raise

    print("\n" + "=" * 72)
    print("🎉 全部任务完成")
    print(f"📈 总发现：{total_found}")
    print(f"✅ 总成功：{total_succeed}")
    print(f"❌ 总失败：{total_failed}")
    print(f"⏭️ 总跳过：{total_skipped}")
    print(f"📁 下载目录：{DOWNLOAD_ROOT.resolve()}")
    print(f"🧾 下载记录：{LOG_CSV.resolve()}")
    print("=" * 72)


if __name__ == "__main__":
    main()
