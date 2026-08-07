
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
from urllib.parse import quote

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

# =====================================
# 用户配置区
# =====================================

# TODO[手动修改]: 机构标准名称
INSTITUTE_NAME = "吉林银行"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_links.txt")
FINGERPRINT_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded_fingerprints.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
FAILED_FILE = os.path.join(DOWNLOAD_ROOT, "failed_records.csv")
FAILED_TASKS_FILE = os.path.join(DOWNLOAD_ROOT, "failed_tasks.json")

BASE_URL = "http://www.jlbank.com.cn"
HOME_URL = f"{BASE_URL}/jlbank/index/index.html"
LIST_PAGE_URL = f"{BASE_URL}/jlbank/grjr/tzlc/lccp/index.html"
LIST_API = f"{BASE_URL}/eportal/ui?moduleId=5&portal.url=/portlet/licai!queryListByPage.portlet"
DETAIL_URL_TEMPLATE = (
    f"{BASE_URL}/eportal/ui?pageId=68144c6858244926bd2ed9cc9e20f216"
    "&articleKey={article_key}&columnId=5293be7a0e5541aeafc050ff793e11c3"
    "&tab={tab_index}&saleStatus={sale_status}&isShowTong={is_show_tong}"
    "&tacode={tacode}&pdCdGrp={pd_cd_grp}"
)
PDF_URL_TEMPLATE = f"{BASE_URL}/eportal/fileDir/licaiAtt/{{ver_no}}_{{file_name}}"

NOTICE_TYPE_BOOK = "销售协议书"
CHART_TYPE_NAME_MAP = {
    "1": "吉行理财",
    "2": "代销理财",
}

# TODO[手动修改]: 选项卡控制，可选 chartType: "1" / "2" 1-吉行理财 2-代销理财
# 默认全跑两类产品，若只跑单个可填 RUN_ONLY_CHART_TYPE
RUN_ONLY_CHART_TYPE = ""
CHART_TYPES_TO_RUN = ["1", "2"]

# TODO[手动修改]: 联调模式，仅抓单个产品 articleKey（空字符串为全量）
TEST_ONLY_ARTICLE_KEY = ""

# TODO[手动修改]: 是否强制访问详情页获取“产品全称”与“销售协议书按钮链接”
FETCH_DETAIL_PAGE_FOR_NAME = True

# TODO[手动修改]: 请求与下载重试
REQUEST_TIMEOUT = 45
REQUEST_RETRY = 3
DOWNLOAD_RETRY = 3
RETRY_WAIT_SECONDS = 3
PAGE_NO_FILE_WAIT_SECONDS = 10

# TODO[手动修改]: 节奏控制（稳中求稳）
REQUEST_INTERVAL_SECONDS = (1.0, 2.0)
PAGE_INTERVAL_SECONDS = (1.6, 3.2)
PRODUCT_INTERVAL_SECONDS = (1.0, 2.4)

# TODO[手动修改]: 分页参数
LIST_PAGE_SIZE = 10

# TODO[手动修改]: 去重与断点开关
ENABLE_CHECKPOINT_RESUME = True
SKIP_DOWNLOADED = True

# ============ 日期区间配置（集中管理，可本地覆盖）===========
# 从根目录 project_meta.py 集中读取；如需单独调整，取消下方注释
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("吉林银行", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("吉林银行", True)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = True
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-09"
# END_DATE   = "2026-06-30"

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
    product_code: str,
    sales_code: str,
    disclose_date: str,
) -> str:
    # 要求: 机构名+产品名+公告类型+产品代码+销售代码(可空)+披露日期
    date_token = re.sub(r"[^0-9]", "", disclose_date)
    parts = [
        sanitize_text(institute_name, 80),
        sanitize_text(product_name, 160),
        sanitize_text(notice_type, 60),
        sanitize_text(product_code, 80),
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


def write_failed_row(
    reason: str,
    module_name: str,
    page_no: int,
    item_index: int,
    article_key: str,
    title: str,
    source_link: str,
    expected_path: str,
):
    exists = os.path.exists(FAILED_FILE)
    with open(FAILED_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(
                [
                    "time",
                    "reason",
                    "module",
                    "page_no",
                    "item_index",
                    "article_key",
                    "title",
                    "source_link",
                    "expected_path",
                ]
            )
        writer.writerow(
            [
                now_time_str(),
                reason,
                module_name,
                page_no,
                item_index,
                article_key,
                title,
                source_link,
                expected_path,
            ]
        )


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

            # 兼容旧格式:
            # {
            #   "chart_type": "2",
            #   "next_page": 19,
            #   "resume_article_key": "..."
            # }
            if "chart_states" not in data:
                chart_type = str(data.get("chart_type") or "").strip()
                if chart_type in ["1", "2"]:
                    data["chart_states"] = {
                        chart_type: {
                            "next_page": max(1, int(data.get("next_page") or 1)),
                            "resume_article_key": str(data.get("resume_article_key") or "").strip(),
                            "updated_at": str(data.get("updated_at") or now_time_str()),
                        }
                    }
                else:
                    data["chart_states"] = {}
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


def select_chart_types() -> List[str]:
    if RUN_ONLY_CHART_TYPE:
        if RUN_ONLY_CHART_TYPE in ["1", "2"]:
            return [RUN_ONLY_CHART_TYPE]
        print(f"⚠️ RUN_ONLY_CHART_TYPE 配置无效: {RUN_ONLY_CHART_TYPE}，改为默认全量")
    return [x for x in CHART_TYPES_TO_RUN if x in ["1", "2"]] or ["1"]


def chart_type_name(chart_type: str) -> str:
    return CHART_TYPE_NAME_MAP.get(str(chart_type), str(chart_type))


def get_book_folder(chart_type: str) -> str:
    return os.path.join(DOWNLOAD_ROOT, chart_type_name(chart_type), NOTICE_TYPE_BOOK)


# =====================================
# 会话与请求
# =====================================


def build_headers(referer: str = "") -> Dict[str, str]:
    return {
        "Accept": "application/json,text/javascript,*/*;q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Origin": BASE_URL,
        "Referer": referer or LIST_PAGE_URL,
        "User-Agent": random.choice(USER_AGENTS),
        "X-Requested-With": "XMLHttpRequest",
    }


def create_session() -> requests.Session:
    s = requests.Session()
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


def _decode_response_text(resp: requests.Response) -> str:
    try:
        return resp.content.decode("utf-8", errors="ignore")
    except Exception:
        return resp.text


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
            return _decode_response_text(resp)
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


def post_json_with_retry(
    session: requests.Session,
    url: str,
    data: Dict,
    referer: str,
    api_desc: str,
    browser: Optional[ChromiumPage] = None,
) -> Optional[Dict]:
    text = request_text_with_retry(
        session=session,
        method="POST",
        url=url,
        referer=referer,
        req_desc=api_desc,
        browser=browser,
        data=data,
    )
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception as e:
        print(f"    ❌ [响应解析失败] {api_desc} | {e}")
        return None


# =====================================
# 解析逻辑
# =====================================


def build_list_payload(chart_type: str, page_no: int) -> Dict[str, str]:
    return {
        "currentPage": str(page_no),
        "pagesize": str(LIST_PAGE_SIZE),
        "chartType": str(chart_type),
        "transWay": "",
        "perforComBase": "",
        "productDeadline": "",
        "channels": "",
        "riskLevel": "",
        "productManager": "",
        "prdCodeOrName": "",
    }


def parse_ext_clob_c(item: Dict) -> Dict:
    raw = item.get("ext_CLOB_C")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return {}


def normalize_detail_url(item: Dict, chart_type: str) -> str:
    article_key = str(item.get("businessPk") or "").strip()
    sale_status = str(item.get("saleStatus") or "").strip()
    is_show_tong = str(item.get("isShowTong") or "").strip()
    tacode = str(item.get("tacode") or "").strip()
    pd_cd_grp = str(item.get("ext_STR36") or "").strip()

    # 参数中包含中文/空格可能造成请求失败，统一编码
    return DETAIL_URL_TEMPLATE.format(
        article_key=quote(article_key, safe=""),
        tab_index=quote(str(chart_type), safe=""),
        sale_status=quote(sale_status, safe=""),
        is_show_tong=quote(is_show_tong, safe=""),
        tacode=quote(tacode, safe=""),
        pd_cd_grp=quote(pd_cd_grp, safe=""),
    )


def parse_product_name_and_pdf_from_detail_html(html: str) -> Tuple[str, str]:
    product_name = ""
    pdf_link = ""

    # 详情页中一般有: var str="<a ft='6' href='/eportal/fileDir/licaiAtt/00019001_xxx.pdf'>销售协议书</a>";
    m_pdf = re.search(r"href\s*=\s*['\"]([^'\"]*?/eportal/fileDir/licaiAtt/[^'\"]+\.pdf)[^'\"]*['\"]", html, re.I)
    if m_pdf:
        href = m_pdf.group(1).strip()
        if href.startswith("http://") or href.startswith("https://"):
            pdf_link = href
        else:
            pdf_link = BASE_URL + href

    # 详情页中一般有 var h = {..."pdNm":"xxx"...}
    m_h = re.search(r"var\s+h\s*=\s*(\{[\s\S]*?\});", html)
    if m_h:
        try:
            obj = json.loads(m_h.group(1))
            product_name = str(obj.get("pdNm") or obj.get("shrtNm") or "").strip()
        except Exception:
            pass

    if not product_name:
        # 兜底从 h2/h1 尝试提取
        soup = BeautifulSoup(html, "html.parser")
        title = soup.select_one("h1") or soup.select_one("h2")
        if title:
            product_name = title.get_text(" ", strip=True)

    return product_name, pdf_link


def resolve_pdf_url_from_item(item: Dict, ext_obj: Dict) -> str:
    data_array = ext_obj.get("dataArray") or []
    if isinstance(data_array, list) and data_array:
        first = data_array[0] if isinstance(data_array[0], dict) else {}
        ver_no = str(first.get("verNo") or "").strip()
        file_name = str(first.get("fileNm") or "").strip()
        if ver_no and file_name:
            return PDF_URL_TEMPLATE.format(ver_no=quote(ver_no, safe=""), file_name=quote(file_name, safe=""))

    # 少数字段可能落在 ext_CLOB_B，也做一次兜底
    raw_b = item.get("ext_CLOB_B")
    if raw_b:
        try:
            ext_b = json.loads(str(raw_b))
            data_array = ext_b.get("dataArray") or []
            if isinstance(data_array, list) and data_array:
                first = data_array[0] if isinstance(data_array[0], dict) else {}
                ver_no = str(first.get("verNo") or "").strip()
                file_name = str(first.get("fileNm") or "").strip()
                if ver_no and file_name:
                    return PDF_URL_TEMPLATE.format(ver_no=quote(ver_no, safe=""), file_name=quote(file_name, safe=""))
        except Exception:
            pass

    return ""


def resolve_product_name(item: Dict, ext_obj: Dict, detail_name: str) -> str:
    # 按“详情页全称”优先，再退回结构化字段
    for candidate in [
        detail_name,
        str(ext_obj.get("pdNm") or "").strip(),
        str(ext_obj.get("shrtNm") or "").strip(),
        str(item.get("productName") or "").strip(),
    ]:
        if candidate:
            return sanitize_text(candidate, 220)
    return ""


def resolve_disclose_date(item: Dict, ext_obj: Dict) -> str:
    # 优先协议文件日期，再用募集期/成立日
    data_array = ext_obj.get("dataArray") or []
    if isinstance(data_array, list) and data_array:
        first = data_array[0] if isinstance(data_array[0], dict) else {}
        file_dt = str(first.get("fileDt") or "").strip()
        if file_dt:
            return normalize_date(file_dt)

    for k in [
        "collectEndDate",
        "collectStartDate",
        "productFoundDate",
        "maturityDate",
    ]:
        v = str(item.get(k) or "").strip()
        if v:
            return normalize_date(v)

    return today_str()


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
            html_text = _decode_response_text(resp)
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
                        save_path, file_name = html_to_word_doc(_decode_response_text(doc_resp), save_folder, base_name)
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
    chart_type: str,
    save_folder: str,
    page_no: int,
    item_index: int,
    article_key: str,
    product_name: str,
    product_code: str,
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
        product_code=product_code,
        sales_code=sales_code,
        disclose_date=disclose_date,
    )
    unique_key = build_unique_key(INSTITUTE_NAME, NOTICE_TYPE_BOOK, announce_title, disclose_date)

    if should_skip_by_dedup(source_link, unique_key, downloaded_links, downloaded_fingerprints):
        print(f"      ⏭️ [跳过] 已下载: {announce_title}")
        return "skipped"

    expected_path, _ = build_unique_save_path(save_folder, base_name, ".pdf")

    last_err = None
    for attempt in range(1, DOWNLOAD_RETRY + 1):
        try:
            final_link, save_path, file_name = download_file_with_fallback(
                session=session,
                source_link=source_link,
                referer=referer,
                save_folder=save_folder,
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
                write_failed_row(
                    reason="download_failed",
                    module_name=chart_type_name(chart_type),
                    page_no=page_no,
                    item_index=item_index,
                    article_key=article_key,
                    title=announce_title,
                    source_link=source_link,
                    expected_path=os.path.abspath(expected_path),
                )

                if record_failed_task:
                    add_or_update_failed_task(
                        {
                            "chart_type": str(chart_type),
                            "module_name": chart_type_name(chart_type),
                            "page_no": int(page_no),
                            "item_index": int(item_index),
                            "article_key": article_key,
                            "product_name": product_name,
                            "product_code": product_code,
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


def fetch_list_page_json(
    session: requests.Session,
    browser: ChromiumPage,
    chart_type: str,
    page_no: int,
) -> Optional[Dict]:
    payload = build_list_payload(chart_type, page_no)
    return post_json_with_retry(
        session=session,
        url=LIST_API,
        data=payload,
        referer=LIST_PAGE_URL,
        api_desc=f"chartType={chart_type} 列表第{page_no}页",
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

    retryable_tasks = [t for t in tasks if not t.get("skip_retry")]
    skipped_tasks = [t for t in tasks if t.get("skip_retry")]
    for task in list(retryable_tasks):
        if str(task.get("last_error") or "").strip() == "no_pdf_link":
            task["skip_retry"] = True
            skipped_tasks.append(task)
            retryable_tasks.remove(task)
    print(f"🔁 失败任务优先重试: {len(retryable_tasks)} 条")
    if skipped_tasks:
        print(f"🧹 已移除无需重试任务: {len(skipped_tasks)} 条")
    save_failed_tasks([])

    for idx, task in enumerate(retryable_tasks, start=1):
        title = str(task.get("announce_title") or "失败任务").strip()
        task_chart_type = str(task.get("chart_type") or "1")
        task_module = str(task.get("module_name") or chart_type_name(task_chart_type))
        task_page_no = int(task.get("page_no") or 0)
        task_item_index = int(task.get("item_index") or 0)
        task_article_key = str(task.get("article_key") or "").strip()
        save_folder = get_book_folder(task_chart_type)
        print(
            f"   ↩️ [{idx}/{len(tasks)}] {title} | 模块={task_module} 页={task_page_no} 条={task_item_index} articleKey={task_article_key}"
        )

        counters["found"] += 1
        result = download_one_book(
            session=session,
            chart_type=task_chart_type,
            save_folder=save_folder,
            page_no=task_page_no,
            item_index=task_item_index,
            article_key=task_article_key,
            product_name=str(task.get("product_name") or ""),
            product_code=str(task.get("product_code") or ""),
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


def update_checkpoint(checkpoint: Dict, chart_type: str, next_page: int, resume_article_key: str):
    if not ENABLE_CHECKPOINT_RESUME:
        return

    chart_type = str(chart_type)
    chart_states = checkpoint.get("chart_states")
    if not isinstance(chart_states, dict):
        chart_states = {}
        checkpoint["chart_states"] = chart_states

    chart_states[chart_type] = {
        "next_page": max(1, int(next_page or 1)),
        "resume_article_key": str(resume_article_key or "").strip(),
        "updated_at": now_time_str(),
    }

    # 仅用于人眼查看“最近一次更新哪个模块”，不参与恢复逻辑
    checkpoint["last_active_chart_type"] = chart_type
    checkpoint["updated_at"] = now_time_str()
    save_checkpoint(checkpoint)


def get_checkpoint_state(checkpoint: Dict, chart_type: str) -> Tuple[int, str]:
    chart_states = checkpoint.get("chart_states") if isinstance(checkpoint, dict) else None
    if isinstance(chart_states, dict):
        state = chart_states.get(str(chart_type))
        if isinstance(state, dict):
            next_page = max(1, int(state.get("next_page") or 1))
            resume_article_key = str(state.get("resume_article_key") or "").strip()
            return next_page, resume_article_key
    return 1, ""


def process_product(
    session: requests.Session,
    browser: ChromiumPage,
    chart_type: str,
    page_no: int,
    item_index: int,
    item: Dict,
    downloaded_links: Set[str],
    downloaded_fingerprints: Set[str],
    counters: Dict[str, int],
) -> bool:
    article_key = str(item.get("businessPk") or "").strip()
    if not article_key:
        return False

    if TEST_ONLY_ARTICLE_KEY.strip() and article_key != TEST_ONLY_ARTICLE_KEY.strip():
        return True

    save_folder = get_book_folder(chart_type)

    ext_obj = parse_ext_clob_c(item)
    detail_url = normalize_detail_url(item, chart_type)

    detail_name = ""
    detail_pdf_link = ""
    if FETCH_DETAIL_PAGE_FOR_NAME:
        detail_html = fetch_detail_html(session, browser, detail_url, article_key)
        if detail_html:
            detail_name, detail_pdf_link = parse_product_name_and_pdf_from_detail_html(detail_html)
        else:
            print(f"      ⚠️ 详情页抓取失败，改用列表结构字段兜底 articleKey={article_key}")

    product_name = resolve_product_name(item, ext_obj, detail_name)
    product_code = str(item.get("productCode") or item.get("businessPk") or "").strip()
    sales_code = str(ext_obj.get("prdCd") or ext_obj.get("prdCode") or "").strip()
    disclose_date = resolve_disclose_date(item, ext_obj)

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

    source_link = detail_pdf_link or resolve_pdf_url_from_item(item, ext_obj)
    announce_title = f"{product_name} {NOTICE_TYPE_BOOK}".strip()

    print(f"    🧾 产品: {product_name} | articleKey={article_key} | chartType={chart_type}")

    if not source_link:
        counters["failed"] += 1
        unique_key = build_unique_key(INSTITUTE_NAME, NOTICE_TYPE_BOOK, announce_title, disclose_date)
        expected_path, _ = build_unique_save_path(
            save_folder,
            build_base_filename(
                INSTITUTE_NAME,
                product_name,
                NOTICE_TYPE_BOOK,
                product_code,
                sales_code,
                disclose_date,
            ),
            ".pdf",
        )
        write_log_row(
            institute_name=INSTITUTE_NAME,
            notice_title=announce_title,
            notice_type=NOTICE_TYPE_BOOK,
            disclose_date=disclose_date,
            status="FAILED",
            source_link=detail_url,
            save_path=os.path.abspath(expected_path),
            unique_key=unique_key,
        )
        write_failed_row(
            reason="no_pdf_link",
            module_name=chart_type_name(chart_type),
            page_no=page_no,
            item_index=item_index,
            article_key=article_key,
            title=announce_title,
            source_link=detail_url,
            expected_path=os.path.abspath(expected_path),
        )

        print("      ❌ [失败] 未解析到销售协议书链接")
        return False

    counters["found"] += 1
    result = download_one_book(
        session=session,
        chart_type=chart_type,
        save_folder=save_folder,
        page_no=page_no,
        item_index=item_index,
        article_key=article_key,
        product_name=product_name,
        product_code=product_code,
        sales_code=sales_code,
        announce_title=announce_title,
        disclose_date=disclose_date,
        source_link=source_link,
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
    print(f"      ⏳ 下载失败，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
    time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
    return False


def prepare_dirs():
    ensure_dir(DOWNLOAD_ROOT)
    for chart_type in ["1", "2"]:
        ensure_dir(get_book_folder(chart_type))


def crawl():
    prepare_dirs()

    downloaded_links = load_downloaded_links()
    downloaded_fingerprints = load_downloaded_fingerprints()
    checkpoint = load_checkpoint()

    chart_types = select_chart_types()

    print(f"📚 已记录来源链接去重数: {len(downloaded_links)}")
    print(f"🧬 已记录指纹去重数: {len(downloaded_fingerprints)}")
    print(f"🧭 运行 chartType: {chart_types}")
    for ct in chart_types:
        cp_page, cp_key = get_checkpoint_state(checkpoint, ct)
        cp_module = chart_type_name(ct)
        print(f"🧭 模块断点: {cp_module}(chartType={ct}) page={cp_page} articleKey={cp_key or '-'}")

    session = create_session()
    browser = create_browser()
    counters = {"found": 0, "succeed": 0, "failed": 0, "skipped": 0}

    try:
        warmup_session_and_cookies(session, browser)
        retry_failed_tasks(session, downloaded_links, downloaded_fingerprints, counters)

        for chart_type in chart_types:
            page_no, resume_article_key = get_checkpoint_state(checkpoint, chart_type)

            first_page = fetch_list_page_json(session, browser, chart_type, page_no)
            if not first_page or not first_page.get("success"):
                print(f"❌ chartType={chart_type} 首页获取失败，跳过该分组")
                continue

            total_pages = max(1, int(first_page.get("totalPages") or 1))
            print(f"\n🗂️ 开始抓取 chartType={chart_type}，总页数: {total_pages}")

            for current_page in range(page_no, total_pages + 1):
                if current_page == page_no:
                    page_obj = first_page
                else:
                    page_obj = fetch_list_page_json(session, browser, chart_type, current_page)

                if not page_obj or not page_obj.get("success"):
                    print(f"   ⚠️ chartType={chart_type} 第{current_page}页失败，等待 {PAGE_NO_FILE_WAIT_SECONDS}s 后继续")
                    time.sleep(PAGE_NO_FILE_WAIT_SECONDS)
                    continue

                items = page_obj.get("result") or []
                print(f"\n📄 chartType={chart_type} 第 {current_page}/{total_pages} 页，产品数: {len(items)}")

                skip_until_resume_hit = bool(resume_article_key and current_page == page_no)

                for item_idx, item in enumerate(items, start=1):
                    current_key = str(item.get("businessPk") or "").strip()

                    if skip_until_resume_hit:
                        if current_key != resume_article_key:
                            continue
                        print(f"    📍 命中断点 articleKey={current_key}，从此产品继续")
                        skip_until_resume_hit = False

                    process_product(
                        session=session,
                        browser=browser,
                        chart_type=chart_type,
                        page_no=current_page,
                        item_index=item_idx,
                        item=item,
                        downloaded_links=downloaded_links,
                        downloaded_fingerprints=downloaded_fingerprints,
                        counters=counters,
                    )

                    if counters.get("early_stop"):
                        break

                    if ENABLE_CHECKPOINT_RESUME:
                        update_checkpoint(checkpoint, chart_type, current_page, current_key)

                    random_sleep(PRODUCT_INTERVAL_SECONDS)

                if counters.get("early_stop"):
                    print(f"   ⏹️ [早停] chartType={chart_type} 第{current_page}页 因日期早停终止，停止翻页")
                    break

                if ENABLE_CHECKPOINT_RESUME:
                    update_checkpoint(checkpoint, chart_type, current_page + 1, "")

                random_sleep(PAGE_INTERVAL_SECONDS)

            if counters.get("early_stop"):
                print(f"   ⏹️ [早停] chartType={chart_type} 因日期早停终止")
                break

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
