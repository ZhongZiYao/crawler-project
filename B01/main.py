
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
import hashlib
import random
from copy import deepcopy
from math import ceil
from datetime import datetime
from urllib.parse import quote, urljoin

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

# ============================================================
# 用户配置区（【需手动修改】）
# ============================================================

INSTITUTE_NAME = "招银理财"  # 【需手动修改】机构名称
HOME_URL = "https://www.cmbchinawm.com/"  # 【需手动修改】首页
NOTICE_URL = "https://www.cmbchinawm.com/notice"  # 【需手动修改】目标页

# 只抓以下 6 个公告模块（按你的要求）
ENABLE_NOTICE_ISSUE = True             # 发行公告
ENABLE_NOTICE_PERIODIC_REPORT = True   # 定期报告
ENABLE_NOTICE_MATURITY = True          # 到期公告
ENABLE_NOTICE_SPEC = True              # 产品说明书
ENABLE_NOTICE_AGREEMENT = True         # 产品相关协议
ENABLE_NOTICE_OTHER = True            # 其他产品公告

# 仅测试单个模块时填写名称（为空表示按上方6个开关组合执行）
# 可填："发行公告"/"定期报告"/"到期公告"/"产品说明书"/"产品相关协议"/"其他产品公告"
TEST_ONLY_NOTICE_TYPE = ""

NOTICE_TYPE_SWITCHES = {
    "发行公告": ENABLE_NOTICE_ISSUE,
    "定期报告": ENABLE_NOTICE_PERIODIC_REPORT,
    "到期公告": ENABLE_NOTICE_MATURITY,
    "产品说明书": ENABLE_NOTICE_SPEC,
    "产品相关协议": ENABLE_NOTICE_AGREEMENT,
    "其他产品公告": ENABLE_NOTICE_OTHER,
}

LIST_API_BY_NOTICE_TYPE = {
    "产品说明书": "qryInstructionList/01",
    "产品相关协议": "qryAgreementList/01",
}

DEFAULT_LIST_API = "qryAnnouncementList/01"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
PROGRESS_FILE = os.path.join(DOWNLOAD_ROOT, "downloaded.txt")
STATE_FILE = os.path.join(DOWNLOAD_ROOT, "crawl_state.json")
LOG_CSV_PATH = os.path.join(SCRIPT_DIR, f"{INSTITUTE_NAME}_日志记录.csv")

# 重试与等待（可按机构调参）
REQUEST_RETRY = 3
RETRY_WAIT_SECONDS = 3
PAGE_FALLBACK_WAIT_SECONDS = 6
STATE_WRITE_RETRY = 3
STATE_WRITE_RETRY_WAIT_SECONDS = 0.25

# 速度参数（稳中偏快，优先保证抓取成功率）
OPEN_HOME_WAIT_SECONDS = 1.5
OPEN_NOTICE_WAIT_SECONDS = 4.2
SWITCH_NOTICE_GROUP_WAIT_SECONDS = 2.2
WAIT_AFTER_TAB_CLICK_SECONDS = 3.0
WAIT_AFTER_SEARCH_CLICK_SECONDS = 2.0
WAIT_AFTER_NEXT_CLICK_SECONDS = 0.75
WAIT_AFTER_ROW_CLICK_SECONDS = 0.8
CONVERTER_RENDER_WAIT_SECONDS = 0.7
LIST_PACKET_TIMEOUT_SECONDS = 12
DOWNLOAD_PACKET_TIMEOUT_SECONDS = 13
PACKET_POLL_SLICE_SECONDS = 0.45

ITEM_RANDOM_SLEEP_MIN = 0.05
ITEM_RANDOM_SLEEP_MAX = 0.14
PAGE_RANDOM_SLEEP_MIN = 0.16
PAGE_RANDOM_SLEEP_MAX = 0.35

# 控制台输出配置
LOG_VERBOSE = True
ENABLE_EMOJI = True

# 文件名筛选开关：False=下载全部文件（不做关键词过滤），True=仅下载含关键词的文件
ENABLE_KEYWORD_FILTER = True

# 文件名筛选关键词：仅下载文件名包含这些关键字的文件（仅 ENABLE_KEYWORD_FILTER=True 时生效）
DOWNLOAD_FILENAME_KEYWORDS = ("费", "费率", "业绩比较基准", "新设", "增设")

# 按栏目单独配置关键词筛选（仅对这些栏目生效，其他栏目不筛选）
# 格式: {"栏目名": ("关键词1", "关键词2", ...)}
KEYWORD_FILTER_BY_NOTICE_TYPE = {
    "其他产品公告": ("费", "费率", "业绩比较基准", "新设", "增设"),
}

# CSV/日志状态展示统一使用中文
STATUS_SUCCEED = "成功"
STATUS_FAILED = "失败"
STATUS_SKIPPED = "已跳过"
STATUS_FILTERED = "已过滤"

# 页面参数
PAGE_SIZE = 10
MAX_PAGE_LIMIT = 8000

# 起始页参数（支持断点续跑）
START_PAGE = 1

# 可按模块单独指定起始页（留空则使用 START_PAGE）
# 示例：{"发行公告": 120, "定期报告": 35}
START_PAGE_BY_NOTICE_TYPE = {}

# 是否跳过已下载（按来源链接去重）
SKIP_DOWNLOADED = True

# 失败优先 + 断点续跑 + 模块完成跳过
ENABLE_PERSISTENT_STATE = True

# ============================================================

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("B01", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("B01", True)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = True
# 本地覆盖示例（取消注释即生效）：
# START_DATE = "2026-04-09"
# END_DATE   = "2026-06-30"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def random_sleep(min_s: float = 0.2, max_s: float = 0.8):
    time.sleep(random.uniform(min_s, max_s))


def icon(name: str) -> str:
    if not ENABLE_EMOJI:
        return ""

    table = {
        "start": "🚀",
        "page": "📄",
        "item": "📌",
        "ok": "✅",
        "skip": "⏭️",
        "warn": "⚠️",
        "retry": "🔁",
        "fail": "❌",
        "done": "🏁",
        "link": "🔗",
        "save": "💾",
    }
    return table.get(name, "")


def log_info(message: str):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


def log_verbose(message: str):
    if LOG_VERBOSE:
        log_info(message)


def get_target_notice_types() -> list:
    if TEST_ONLY_NOTICE_TYPE:
        target = TEST_ONLY_NOTICE_TYPE.strip()
        if target not in NOTICE_TYPE_SWITCHES:
            raise ValueError(f"TEST_ONLY_NOTICE_TYPE 配置无效：{target}")
        return [target]

    enabled = [name for name, is_enabled in NOTICE_TYPE_SWITCHES.items() if is_enabled]
    if not enabled:
        raise ValueError("6个公告模块开关均为 False，至少开启 1 个")
    return enabled


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name or "")
    name = re.sub(r"\s+", " ", name)
    return name.strip()[:200] or "未命名"


def sanitize_filename_part(name: str) -> str:
    """用于文件名片段：允许返回空字符串，不使用占位词。"""
    text = re.sub(r'[\\/*?:"<>|]', "_", name or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:200]


def match_download_filename_keywords(file_name: str, notice_type: str = "") -> bool:
    text = (file_name or "").strip()
    if not text:
        return False
    # 优先使用按栏目配置的关键词
    if notice_type and notice_type in KEYWORD_FILTER_BY_NOTICE_TYPE:
        keywords = KEYWORD_FILTER_BY_NOTICE_TYPE[notice_type]
        return any(keyword in text for keyword in keywords)
    # 全局关键词
    return any(keyword in text for keyword in DOWNLOAD_FILENAME_KEYWORDS)


def should_download_file(file_name: str, notice_type: str = "") -> bool:
    # 只有明确配置在 KEYWORD_FILTER_BY_NOTICE_TYPE 中的栏目才做关键词筛选
    if notice_type and notice_type in KEYWORD_FILTER_BY_NOTICE_TYPE:
        return match_download_filename_keywords(file_name, notice_type)
    # 其他栏目不筛选，直接下载
    return True


def format_download_filename_keywords() -> str:
    return "、".join(DOWNLOAD_FILENAME_KEYWORDS)


def normalize_date(date_text: str) -> str:
    text = (date_text or "").strip()
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
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


def append_progress(progress_file: str, key: str):
    with open(progress_file, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_state(state_file: str) -> dict:
    default_state = {
        "modules": {},
        "failed_items": [],
    }
    if not ENABLE_PERSISTENT_STATE:
        return default_state
    if not os.path.exists(state_file):
        return default_state

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return default_state
            modules = data.get("modules")
            failed_items = data.get("failed_items")
            if not isinstance(modules, dict):
                modules = {}
            if not isinstance(failed_items, list):
                failed_items = []
            return {
                "modules": modules,
                "failed_items": failed_items,
            }
    except Exception:
        return default_state


def save_state(state_file: str, state_data: dict):
    if not ENABLE_PERSISTENT_STATE:
        return
    ensure_dir(os.path.dirname(state_file))
    tmp_file = state_file + ".tmp"

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)

    last_error = None
    for attempt in range(1, STATE_WRITE_RETRY + 1):
        try:
            os.replace(tmp_file, state_file)
            return
        except PermissionError as e:
            last_error = e
            if attempt < STATE_WRITE_RETRY:
                time.sleep(STATE_WRITE_RETRY_WAIT_SECONDS)
                continue
            break
        except Exception as e:
            last_error = e
            break

    try:
        # 尝试一次非原子降级写入，避免目标文件被临时占用时整次任务中断。
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        last_error = e

    try:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
    except Exception:
        pass

    if last_error:
        log_info(f"{icon('warn')} 保存状态失败，已降级处理：{last_error}")


def build_progress_key(source_url: str, file_name: str, product_code: str, sales_code: str) -> str:
    """按 下载链接 + 文件名 + 产品代码 + 销售代码 去重。"""
    normalized_source_url = (source_url or "").strip() or "UNKNOWN_SOURCE"
    normalized_file_name = sanitize_filename(file_name or "") or "UNKNOWN_FILE"
    normalized_product_code = sanitize_filename((product_code or "").strip()) or "NO_PRODUCT_CODE"
    normalized_sales_code = sanitize_filename((sales_code or "").strip()) or "NO_SALES_CODE"
    return (
        f"{normalized_source_url}||{normalized_file_name}"
        f"||{normalized_product_code}||{normalized_sales_code}"
    )


def build_progress_legacy_key(source_url: str, file_name: str, sales_code: str) -> str:
    normalized_source_url = (source_url or "").strip() or "UNKNOWN_SOURCE"
    normalized_file_name = sanitize_filename(file_name or "") or "UNKNOWN_FILE"
    normalized_sales_code = sanitize_filename((sales_code or "").strip()) or "NO_SALES_CODE"
    return f"{normalized_source_url}||{normalized_file_name}||{normalized_sales_code}"


def has_progress_hit(progress_set: set, source_url: str, file_name: str, product_code: str, sales_code: str) -> bool:
    key_new = build_progress_key(source_url, file_name, product_code, sales_code)
    key_old = build_progress_legacy_key(source_url, file_name, sales_code)
    key_min = f"{(source_url or '').strip() or 'UNKNOWN_SOURCE'}||{sanitize_filename(file_name or '') or 'UNKNOWN_FILE'}"
    return key_new in progress_set or key_old in progress_set or key_min in progress_set


def choose_unique_path(folder: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(folder, filename)
    if not os.path.exists(candidate):
        return candidate

    idx = 1
    while True:
        candidate = os.path.join(folder, f"{base}_{idx}{ext}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "")).strip()


def extract_product_name(title: str, notice_type: str) -> str:
    t = clean_title(title)
    if t.startswith(INSTITUTE_NAME):
        t = t[len(INSTITUTE_NAME):].strip()

    # 去掉尾部公告类型词
    if notice_type and t.endswith(notice_type):
        t = t[: -len(notice_type)].strip()

    # 去掉中间的"（产品代码：xxx）"
    t = re.sub(r"（\s*产品代码\s*[:：]\s*[^）]+）", "", t)
    t = re.sub(r"销售代码\s*[:：]\s*[A-Za-z0-9_-]+", "", t)
    t = t.strip("-—_ ")
    return t


def extract_product_code(title: str) -> str:
    text = clean_title(title)
    m = re.search(r"产品代码\s*[:：]\s*([A-Za-z0-9_-]+)", text)
    if not m:
        return ""
    return m.group(1).strip()


def extract_product_code_from_item(item: dict, title: str) -> str:
    candidates = [
        item.get("productCode"),
        item.get("prodCode"),
        item.get("prdCode"),
        item.get("productNo"),
        item.get("prodNo"),
    ]
    for code in candidates:
        code_text = str(code or "").strip()
        if code_text:
            return code_text
    return extract_product_code(title)


def extract_sales_code_from_item(item: dict) -> str:
    candidates = [
        item.get("prodTradeCode"),
        item.get("salesCode"),
        item.get("saleCode"),
        item.get("tradeCode"),
    ]
    for code in candidates:
        code_text = str(code or "").strip()
        if code_text:
            return code_text

    text_candidates = [
        str(item.get("title") or ""),
        str(item.get("subTitle") or ""),
        str(item.get("name") or ""),
        str(item.get("content") or ""),
    ]
    merged = " ".join(text_candidates)
    merged = re.sub(r"<[^>]+>", " ", merged)
    m = re.search(r"销售代码\s*[:：]\s*([A-Za-z0-9_-]+)", merged)
    if m:
        return m.group(1).strip()

    return ""


def build_save_filename(
    product_name: str,
    notice_type: str,
    product_code: str,
    sales_code: str,
    ext: str,
) -> str:
    product_name = sanitize_filename_part(product_name)
    notice_type = sanitize_filename_part(notice_type)
    institute_name = sanitize_filename_part(INSTITUTE_NAME)
    raw_product_code = (product_code or "").strip()
    raw_sales_code = (sales_code or "").strip()
    normalized_product_code = sanitize_filename_part(raw_product_code) if raw_product_code else ""
    normalized_sales_code = sanitize_filename_part(raw_sales_code) if raw_sales_code else ""

    parts = [institute_name, product_name, notice_type]
    if normalized_product_code:
        parts.append(normalized_product_code)
    if normalized_sales_code:
        parts.append(normalized_sales_code)

    parts = [p for p in parts if p]
    if not parts:
        parts = [institute_name or "公告"]

    safe_ext = ext if (ext or "").startswith(".") else f".{ext or 'pdf'}"
    file_name = "_".join(parts) + safe_ext
    return sanitize_filename_part(file_name) or f"{institute_name or '公告'}{safe_ext}"


def get_start_page_for_notice_type(notice_type: str) -> int:
    raw = START_PAGE_BY_NOTICE_TYPE.get(notice_type, START_PAGE)
    try:
        page_no = int(raw)
    except Exception:
        page_no = 1
    return max(1, page_no)


def log_to_csv(
    title: str,
    notice_type: str,
    disclose_date: str,
    status: str,
    source_url: str,
    save_path: str,
):
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    unique_key = f"{INSTITUTE_NAME}_{notice_type}_{clean_title(title)}_{disclose_date}"

    row = [
        INSTITUTE_NAME,
        clean_title(title),
        notice_type,
        disclose_date,
        now_time,
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
        "唯一键",
    ]

    exists = os.path.exists(LOG_CSV_PATH)
    with open(LOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow(row)


class CMBWMSpider:
    def __init__(self):
        ensure_dir(DOWNLOAD_ROOT)
        self.progress_set = load_progress(PROGRESS_FILE)
        self.state = load_state(STATE_FILE)
        self.page = _new_page()
        # 富文本转 PDF 使用单独页面，避免打乱主流程页面状态
        self.converter = _new_page()

        # 禁用浏览器原生下载：只使用网络监听拿到二进制后自行保存，
        # 避免 DrissionPage 下载线程搬运临时文件时报 FileNotFoundError。
        try:
            self.page.run_cdp("Browser.setDownloadBehavior", behavior="deny")
            self.converter.run_cdp("Browser.setDownloadBehavior", behavior="deny")
        except Exception as e:
            log_info(f"{icon('warn')} 设置浏览器下载策略失败：{e}")

    def _save_state(self):
        save_state(STATE_FILE, self.state)

    def _ensure_module_state(self, notice_type: str) -> dict:
        modules = self.state.setdefault("modules", {})
        data = modules.get(notice_type)
        if not isinstance(data, dict):
            data = {}

        normalized = {
            "completed": bool(data.get("completed", False)),
            "next_page": max(1, int(data.get("next_page") or 1)),
            "next_row": max(0, int(data.get("next_row") or 0)),
            "updated_at": str(data.get("updated_at") or ""),
        }
        modules[notice_type] = normalized
        return normalized

    def _set_checkpoint(self, notice_type: str, next_page: int, next_row: int):
        module_state = self._ensure_module_state(notice_type)
        module_state["next_page"] = max(1, int(next_page))
        module_state["next_row"] = max(0, int(next_row))
        module_state["updated_at"] = now_str()
        module_state["completed"] = False
        self._save_state()

    def _mark_module_completed(self, notice_type: str):
        module_state = self._ensure_module_state(notice_type)
        module_state["completed"] = True
        module_state["next_page"] = 1
        module_state["next_row"] = 0
        module_state["updated_at"] = now_str()
        self._save_state()

    def _has_pending_failed_for_notice_type(self, notice_type: str) -> bool:
        failed_items = self.state.setdefault("failed_items", [])
        for x in failed_items:
            if str(x.get("notice_type") or "") == notice_type:
                return True
        return False

    def _failed_id(self, notice_type: str, source_url: str, title: str, pub_date: str, file_id: str) -> str:
        raw = f"{notice_type}|{source_url}|{title}|{pub_date}|{file_id}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _upsert_failed_item(self, failed_item: dict):
        failed_items = self.state.setdefault("failed_items", [])
        failed_id = failed_item.get("failed_id")
        if not failed_id:
            return

        for i, old in enumerate(failed_items):
            if str(old.get("failed_id") or "") == failed_id:
                merged = deepcopy(old)
                merged.update(failed_item)
                failed_items[i] = merged
                self._save_state()
                return

        failed_items.append(failed_item)
        self._save_state()

    def _remove_failed_item(self, failed_id: str):
        if not failed_id:
            return
        failed_items = self.state.setdefault("failed_items", [])
        new_failed_items = [x for x in failed_items if str(x.get("failed_id") or "") != failed_id]
        if len(new_failed_items) != len(failed_items):
            self.state["failed_items"] = new_failed_items
            self._save_state()

    def _record_failed_case(
        self,
        notice_type: str,
        page_no: int,
        row_index: int,
        item: dict,
        source_url: str,
        error_message: str,
    ):
        title = clean_title(item.get("title") or "")
        pub_date = normalize_date(item.get("pubDate") or "")
        file_id = str(item.get("fileId") or "").strip()
        failed_id = self._failed_id(notice_type, source_url, title, pub_date, file_id)

        payload = {
            "failed_id": failed_id,
            "notice_type": notice_type,
            "page_no": int(page_no),
            "row_index": int(row_index),
            "title": title,
            "pub_date": pub_date,
            "source_url": source_url,
            "file_id": file_id,
            "item": item,
            "last_error": str(error_message or ""),
            "updated_at": now_str(),
            "retry_count": 1,
        }

        old_items = self.state.setdefault("failed_items", [])
        for old in old_items:
            if str(old.get("failed_id") or "") == failed_id:
                payload["retry_count"] = int(old.get("retry_count") or 0) + 1
                break

        self._upsert_failed_item(payload)

    def _get_failed_items_for_notice_type(self, notice_type: str) -> list:
        failed_items = self.state.setdefault("failed_items", [])
        result = []
        for x in failed_items:
            if str(x.get("notice_type") or "") == notice_type:
                result.append(x)
        result.sort(key=lambda x: (int(x.get("page_no") or 1), int(x.get("row_index") or 0)))
        return result

    def _retry_failed_items_first(self, notice_type: str):
        failed_items = self._get_failed_items_for_notice_type(notice_type)
        if not failed_items:
            return 0, 0

        log_info(f"{icon('retry')} 模块[{notice_type}] 先处理历史失败 {len(failed_items)} 条")
        succeed = 0
        failed = 0
        current_retry_page = 1

        for x in failed_items:
            item = x.get("item") or {}
            page_no = int(x.get("page_no") or 1)
            row_index = int(x.get("row_index") or -1)
            failed_id = str(x.get("failed_id") or "")

            # 重试失败案例时，切到记录页后仍按原流程"行点击 + 监听字节流"下载。
            if page_no != current_retry_page:
                if page_no <= 1:
                    if not self._click_notice_type_and_get_first_page(notice_type):
                        failed += 1
                        updated = deepcopy(x)
                        updated["retry_count"] = int(updated.get("retry_count") or 0) + 1
                        updated["updated_at"] = now_str()
                        updated["last_error"] = "失败重试前无法回到第1页"
                        self._upsert_failed_item(updated)
                        continue
                else:
                    jumped = self._jump_to_page_and_get_packet(notice_type, page_no)
                    if not jumped:
                        failed += 1
                        updated = deepcopy(x)
                        updated["retry_count"] = int(updated.get("retry_count") or 0) + 1
                        updated["updated_at"] = now_str()
                        updated["last_error"] = f"失败重试跳页失败：第{page_no}页"
                        self._upsert_failed_item(updated)
                        continue
                current_retry_page = page_no

            result, err = self._download_one_item(
                notice_type=notice_type,
                page_no=page_no,
                row_index=row_index,
                item=item,
                record_failure=False,
            )

            if result in ("SUCCEED", "SKIPPED"):
                self._remove_failed_item(failed_id)
                succeed += 1
            else:
                failed += 1
                updated = deepcopy(x)
                updated["retry_count"] = int(updated.get("retry_count") or 0) + 1
                updated["updated_at"] = now_str()
                updated["last_error"] = str(err or "历史失败重试未成功")
                self._upsert_failed_item(updated)

        return succeed, failed

    def close(self):
        try:
            self.page.quit()
        except Exception:
            pass
        try:
            self.converter.quit()
        except Exception:
            pass

    def open_notice_page(self):
        log_info(f"{icon('start')} 打开首页：{HOME_URL}")
        self.page.get(HOME_URL)
        time.sleep(OPEN_HOME_WAIT_SECONDS)

        log_info(f"{icon('start')} 打开公告页：{NOTICE_URL}")
        self.page.get(NOTICE_URL)
        time.sleep(OPEN_NOTICE_WAIT_SECONDS)

        # 进入"公募产品公告"
        left_tabs = self.page.eles("css:.leftBox .item")
        for tab in left_tabs: # pyright: ignore[reportGeneralTypeIssues]
            text = (tab.text or "").strip()
            if "公募产品公告" in text:
                tab.click()
                time.sleep(SWITCH_NOTICE_GROUP_WAIT_SECONDS)
                log_info(f"{icon('ok')} 已进入「公募产品公告」区域")
                return

        # 兜底：按文字直接找
        ele = self.page.ele("text:公募产品公告", timeout=5)
        if ele:
            ele.click()
            time.sleep(SWITCH_NOTICE_GROUP_WAIT_SECONDS)
            log_info(f"{icon('ok')} 已通过文本定位进入「公募产品公告」区域")

    def _get_list_api_for_notice_type(self, notice_type: str) -> str:
        return LIST_API_BY_NOTICE_TYPE.get(notice_type, DEFAULT_LIST_API)

    def _iter_listen_packets(self, timeout: float):
        """分片轮询监听包，拿到目标包后可立刻返回，避免每次都等满超时。"""
        deadline = time.perf_counter() + max(0.1, float(timeout))
        while time.perf_counter() < deadline:
            remain = deadline - time.perf_counter()
            if remain <= 0:
                break
            slice_timeout = min(PACKET_POLL_SLICE_SECONDS, max(0.1, remain))
            for packet in self.page.listen.steps(timeout=slice_timeout):
                yield packet

    def _wait_list_packet(self, list_api: str, timeout: int = LIST_PACKET_TIMEOUT_SECONDS):
        for packet in self._iter_listen_packets(timeout=timeout):
            try:
                if list_api not in packet.request.url: # pyright: ignore[reportAttributeAccessIssue]
                    continue
                body = packet.response.body # pyright: ignore[reportAttributeAccessIssue]
                if isinstance(body, dict) and body.get("code") == 200:
                    return body
            except Exception:
                continue
        return None

    def _click_search_button(self) -> bool:
        selectors = [
            "css:.form .el-button--primary",
            "css:.form button.el-button--primary",
            "text:搜索",
        ]

        for selector in selectors:
            try:
                ele = self.page.ele(selector, timeout=1.5)
                if ele:
                    ele.click()
                    return True
            except Exception:
                continue

        try:
            result = self.page.run_js(
                """
const buttons = Array.from(document.querySelectorAll('button, .el-button'));
const target = buttons.find(btn => (btn.innerText || '').trim() === '搜索');
if (target) {
    target.click();
    return true;
}
const primary = document.querySelector('.form .el-button--primary');
if (primary) {
    primary.click();
    return true;
}
return false;
"""
            )
            return bool(result)
        except Exception:
            return False

    def _click_notice_type_and_get_first_page(self, notice_type: str):
        list_api = self._get_list_api_for_notice_type(notice_type)
        log_verbose(f"{icon('link')} 模块[{notice_type}] 监听列表接口：{list_api}")

        # 点击顶部公告类型 tab（按去空白后的文本匹配，兼容异常空格/换行）
        target_norm = re.sub(r"\s+", "", notice_type or "")

        # tab 切换最多重试 5 次（用户反馈页面加载慢，给更多机会）
        tab_max_retry = 5
        for attempt in range(1, tab_max_retry + 1):
            clicked = False
            tab_names_found = []
            try:
                self.page.listen.start(list_api)

                tabs = self.page.eles("css:.tittleItem")
                for tab in tabs: # pyright: ignore[reportGeneralTypeIssues]
                    raw_text = tab.text or ""
                    txt_norm = re.sub(r"\s+", "", raw_text)
                    tab_names_found.append(raw_text.strip())
                    if txt_norm == target_norm:
                        tab.click()
                        clicked = True
                        log_info(f"{icon('ok')} 已点击tab：{notice_type}（第{attempt}次）")
                        break

                # DrissionPage click 未命中 → JS 兜底
                if not clicked:
                    js_result = self.page.run_js(f"""
const target = '{target_norm}';
const tabs = document.querySelectorAll('.tittleItem');
for (const tab of tabs) {{
    const txt = (tab.innerText || '').replace(/\\s+/g, '');
    if (txt === target) {{
        tab.click();
        return 'clicked';
    }}
}}
return Array.from(tabs).map(t => (t.innerText||'').trim()).join('|');
""")
                    if js_result == 'clicked':
                        clicked = True
                        log_info(f"{icon('ok')} 通过 JS 点击 tab 成功：{notice_type}（第{attempt}次）")
                    elif js_result and '|' in str(js_result):
                        tab_names_found = str(js_result).split('|')
            except Exception as e:
                log_info(f"{icon('warn')} tab点击异常：{e}")
                clicked = False

            if not clicked:
                log_info(f"{icon('warn')} 未找到模块tab：{notice_type}（第{attempt}次），页面上的tab：{tab_names_found}")
                time.sleep(1.0)
                continue

            # 等待页面加载（用户反馈手动切tab需要好几秒，这里等 WAIT_AFTER_TAB_CLICK_SECONDS=3s）
            time.sleep(WAIT_AFTER_TAB_CLICK_SECONDS)

            # 点击搜索按钮触发新的列表请求
            try:
                if self._click_search_button():
                    time.sleep(WAIT_AFTER_SEARCH_CLICK_SECONDS)
                else:
                    log_info(f"{icon('warn')} 未找到搜索按钮：{notice_type}")
            except Exception:
                pass

            # 捕获列表 API 响应
            rsp = self._wait_list_packet(list_api=list_api, timeout=LIST_PACKET_TIMEOUT_SECONDS)
            if rsp:
                log_info(f"{icon('ok')} 模块[{notice_type}] tab切换成功，获取到列表数据")
                return rsp

            # 兜底：有些页面在 tab 切换后自动发请求
            try:
                rsp = self._wait_list_packet(list_api=list_api, timeout=max(5, LIST_PACKET_TIMEOUT_SECONDS // 2))
                if rsp:
                    log_info(f"{icon('ok')} 模块[{notice_type}] tab切换成功（兜底捕获）")
                    return rsp
            except Exception:
                pass

            log_info(f"{icon('retry')} 模块[{notice_type}] 第{attempt}次未捕获到列表响应，准备重试")
            time.sleep(RETRY_WAIT_SECONDS)

        return None

    def _goto_next_page_and_get_packet(self, notice_type: str):
        list_api = self._get_list_api_for_notice_type(notice_type)
        for _ in range(REQUEST_RETRY):
            self.page.listen.start(list_api)
            btn = self.page.ele("css:.btn-next", timeout=4)
            if not btn:
                return None

            cls = (btn.attr("class") or "").lower()
            if "disabled" in cls:
                return None

            btn.click()
            time.sleep(WAIT_AFTER_NEXT_CLICK_SECONDS)
            rsp = self._wait_list_packet(list_api=list_api, timeout=LIST_PACKET_TIMEOUT_SECONDS)
            if rsp:
                return rsp

            time.sleep(PAGE_FALLBACK_WAIT_SECONDS)
        return None

    def _jump_to_page_and_get_packet(self, notice_type: str, target_page: int):
        if target_page <= 1:
            return None

        list_api = self._get_list_api_for_notice_type(notice_type)

        # 优先通过分页输入框直接跳页，避免逐页翻。
        try:
            self.page.listen.start(list_api)
            jump_js = f"""
const target = {int(target_page)};
const input = document.querySelector('.el-pagination__jump input');
if (!input) return false;
input.focus();
input.value = String(target);
input.dispatchEvent(new Event('input', {{ bubbles: true }}));
input.dispatchEvent(new Event('change', {{ bubbles: true }}));
input.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', bubbles: true }}));
input.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Enter', code: 'Enter', bubbles: true }}));
return true;
"""
            jumped = self.page.run_js(jump_js)
            if jumped:
                time.sleep(WAIT_AFTER_NEXT_CLICK_SECONDS)
                rsp = self._wait_list_packet(list_api=list_api, timeout=LIST_PACKET_TIMEOUT_SECONDS)
                if rsp:
                    return rsp
        except Exception:
            pass

        # 兜底：逐页快进到目标页。
        rsp = None
        for _ in range(2, target_page + 1):
            rsp = self._goto_next_page_and_get_packet(notice_type)
            if not rsp:
                return None
        return rsp

    def _convert_html_to_pdf(self, html_content: str, save_path: str):
        html = (html_content or "").strip()
        if not html:
            raise RuntimeError("富文本内容为空")

        if "<html" not in html.lower():
            html = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
                f"<body>{html}</body></html>"
            )

        data_url = "data:text/html;charset=utf-8," + quote(html)
        self.converter.get(data_url)
        time.sleep(CONVERTER_RENDER_WAIT_SECONDS)

        result = self.converter.run_cdp(
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

    def _download_one_item(
        self,
        notice_type: str,
        page_no: int,
        row_index: int,
        item: dict,
        record_failure: bool = True,
    ):
        title = clean_title(item.get("title") or "")
        pub_date = normalize_date(item.get("pubDate") or "")

        _in_range, _too_old = is_in_date_range(pub_date)
        if not _in_range:
            tag = "过早(早停)" if _too_old else "过晚"
            print(f"   ⏭️ [日期跳过] 披露日期 {pub_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
            return "SKIPPED", ""

        sales_code = extract_sales_code_from_item(item)
        product_code = extract_product_code_from_item(item, title)
        rich_text = str(item.get("richText") or "0").strip()
        file_id = (item.get("fileId") or "").strip()
        content_html = item.get("content") or ""

        product_name = extract_product_name(title, notice_type)
        type_dir = os.path.join(DOWNLOAD_ROOT, sanitize_filename(notice_type))
        ensure_dir(type_dir)

        # 来源链接（用于去重）
        if file_id:
            source_url = urljoin(NOTICE_URL, f"/prod-api/prodDoc/download/post/{file_id}")
        else:
            # richText 没有 fileId 时，使用稳定摘要，避免跨进程 hash 变化
            richtext_sig = hashlib.md5(f"{notice_type}|{title}|{pub_date}".encode("utf-8")).hexdigest()[:16]
            source_url = f"{NOTICE_URL}#richtext-{richtext_sig}"

        if rich_text == "1":
            # HTML 富文本 -> PDF
            ext = ".pdf"
            file_name = build_save_filename(product_name, notice_type, product_code, sales_code, ext)
            if not should_download_file(file_name, notice_type):
                kw_display = "、".join(KEYWORD_FILTER_BY_NOTICE_TYPE.get(notice_type, DOWNLOAD_FILENAME_KEYWORDS))
                log_info(
                    f"{icon('skip')} 已过滤（文件名未命中关键字[{kw_display}]）：{title}"
                )
                log_to_csv(title, notice_type, pub_date, STATUS_FILTERED, source_url, "")
                return "SKIPPED", "文件名未命中关键字"
            progress_key = build_progress_key(source_url, file_name, product_code, sales_code)
            if SKIP_DOWNLOADED and has_progress_hit(self.progress_set, source_url, file_name, product_code, sales_code):
                return "SKIPPED", ""

            file_path = choose_unique_path(type_dir, file_name)
            log_verbose(f"{icon('item')} 富文本转PDF：{title}")

            try:
                self._convert_html_to_pdf(content_html, file_path)
                if os.path.getsize(file_path) == 0:
                    raise RuntimeError("生成 PDF 为空")

                log_to_csv(title, notice_type, pub_date, STATUS_SUCCEED, source_url, os.path.abspath(file_path))
                append_progress(PROGRESS_FILE, progress_key)
                self.progress_set.add(progress_key)
                log_verbose(f"{icon('ok')} 转换成功 {icon('save')} {os.path.basename(file_path)}")
                return "SUCCEED", ""
            except Exception as e:
                expected = os.path.abspath(file_path)
                log_to_csv(title, notice_type, pub_date, STATUS_FAILED, source_url, expected)
                log_info(f"{icon('fail')} 富文本转PDF失败：{title} - {e}")
                if record_failure:
                    self._record_failed_case(notice_type, page_no, row_index, item, source_url, str(e))
                return "FAILED", str(e)

        # 二进制文件下载（触发页面下载接口并从网络监听拿 bytes）
        ext = ".pdf"
        file_name = build_save_filename(product_name, notice_type, product_code, sales_code, ext)
        if not should_download_file(file_name, notice_type):
            kw_display = "、".join(KEYWORD_FILTER_BY_NOTICE_TYPE.get(notice_type, DOWNLOAD_FILENAME_KEYWORDS))
            log_info(
                f"{icon('skip')} 已过滤（文件名未命中关键字[{kw_display}]）：{title}"
            )
            log_to_csv(title, notice_type, pub_date, STATUS_FILTERED, source_url, "")
            return "SKIPPED", "文件名未命中关键字"
        progress_key = build_progress_key(source_url, file_name, product_code, sales_code)
        if SKIP_DOWNLOADED and has_progress_hit(self.progress_set, source_url, file_name, product_code, sales_code):
            return "SKIPPED", ""

        file_path = choose_unique_path(type_dir, file_name)
        log_verbose(f"{icon('item')} 触发下载：{title}")

        for attempt in range(1, REQUEST_RETRY + 1):
            try:
                rows = self.page.eles("css:.listBox .item")
                if row_index >= len(rows):
                    raise RuntimeError(f"当前页条目索引越界 row_index={row_index}, rows={len(rows)}")

                self.page.listen.start(["getPrdDocumentDownloadUrl", "/prodDoc/download/post/"])
                rows[row_index].click() # pyright: ignore[reportAttributeAccessIssue]
                time.sleep(WAIT_AFTER_ROW_CLICK_SECONDS)

                download_bytes = None
                dl_source_url = source_url
                server_file_name = ""

                got_any_packet = False
                for p in self._iter_listen_packets(timeout=DOWNLOAD_PACKET_TIMEOUT_SECONDS):
                    got_any_packet = True
                    req_url = p.request.url # pyright: ignore[reportAttributeAccessIssue]

                    if "getPrdDocumentDownloadUrl" in req_url:
                        try:
                            body = p.response.body # pyright: ignore[reportAttributeAccessIssue]
                            if isinstance(body, dict):
                                data = body.get("data") or {}
                                server_file_name = (data.get("fileName") or "").strip()
                                dl_url = (data.get("url") or "").strip()
                                if dl_url:
                                    dl_source_url = urljoin(NOTICE_URL, dl_url)
                        except Exception:
                            pass

                    if "/prodDoc/download/post/" in req_url:
                        try:
                            b = p.response.body # pyright: ignore[reportAttributeAccessIssue]
                            if isinstance(b, (bytes, bytearray)) and len(b) > 0:
                                download_bytes = bytes(b)
                                dl_source_url = req_url
                                break
                        except Exception:
                            pass

                if not got_any_packet:
                    raise RuntimeError("未捕获到下载链路请求")

                if not download_bytes:
                    # 翻页后或网络抖动时给站点一次更长等待
                    time.sleep(PAGE_FALLBACK_WAIT_SECONDS)
                    raise RuntimeError("未获取到下载文件字节流")

                # 若服务端给了更准确后缀，覆盖默认 .pdf
                if server_file_name:
                    server_ext = os.path.splitext(server_file_name)[1].lower()
                    if server_ext:
                        ext = server_ext

                        file_name = build_save_filename(product_name, notice_type, product_code, sales_code, ext)

                        progress_key = build_progress_key(source_url, file_name, product_code, sales_code)
                        if SKIP_DOWNLOADED and has_progress_hit(self.progress_set, source_url, file_name, product_code, sales_code):
                            return "SKIPPED", ""

                        file_path = choose_unique_path(type_dir, file_name)

                with open(file_path, "wb") as f:
                    f.write(download_bytes)

                if os.path.getsize(file_path) == 0:
                    raise RuntimeError("保存后文件为空")

                log_to_csv(title, notice_type, pub_date, STATUS_SUCCEED, dl_source_url, os.path.abspath(file_path))
                append_progress(PROGRESS_FILE, progress_key)
                self.progress_set.add(progress_key)
                log_verbose(f"{icon('ok')} 下载成功 {icon('save')} {os.path.basename(file_path)}")
                return "SUCCEED", ""

            except Exception as e:
                if attempt < REQUEST_RETRY:
                    log_info(
                        f"{icon('retry')} 第{attempt}次失败，{RETRY_WAIT_SECONDS}秒后重试：{title} - {e}"
                    )
                    time.sleep(RETRY_WAIT_SECONDS)
                    continue

                expected = os.path.abspath(file_path)
                log_to_csv(title, notice_type, pub_date, STATUS_FAILED, source_url, expected)
                log_info(f"{icon('fail')} 下载失败：{title} - {e}")
                if record_failure:
                    self._record_failed_case(notice_type, page_no, row_index, item, source_url, str(e))
                return "FAILED", str(e)

        return "FAILED", "未知错误"

    def crawl_one_notice_type(self, notice_type: str):
        log_info(f"\n{'=' * 68}")
        log_info(f"{icon('start')} 开始抓取模块：{notice_type}")
        log_info(f"{'=' * 68}")

        rsp = self._click_notice_type_and_get_first_page(notice_type)
        if not rsp:
            log_info(f"{icon('warn')} 无法触发模块请求：{notice_type}")
            return 0, 0, 0

        retry_ok, retry_fail = self._retry_failed_items_first(notice_type)

        # 失败重试过程中可能跳页，重置到模块首页以保证后续页数据与界面一致。
        refreshed_rsp = self._click_notice_type_and_get_first_page(notice_type)
        if refreshed_rsp:
            rsp = refreshed_rsp

        data = rsp.get("data") or {}
        total = int(data.get("total") or data.get("totalRecord") or 0)
        total_pages = min(MAX_PAGE_LIMIT, max(1, ceil(total / PAGE_SIZE)))

        log_info(f"{icon('page')} 总记录：{total}，估算页数：{total_pages}")

        module_state = self._ensure_module_state(notice_type)
        start_page = min(total_pages, get_start_page_for_notice_type(notice_type))
        start_row = 0

        if module_state.get("next_page", 1) > 1 or module_state.get("next_row", 0) > 0:
            start_page = min(total_pages, int(module_state.get("next_page") or 1))
            start_row = max(0, int(module_state.get("next_row") or 0))
            log_info(f"{icon('page')} 模块[{notice_type}] 读取断点：第{start_page}页 第{start_row + 1}条")

        current_rsp = rsp
        if start_page > 1:
            log_info(f"{icon('page')} 模块[{notice_type}] 从第 {start_page} 页开始")
            jumped_rsp = self._jump_to_page_and_get_packet(notice_type, start_page)
            if jumped_rsp:
                current_rsp = jumped_rsp
            else:
                log_info(f"{icon('warn')} 跳页失败，回退从第1页开始")
                start_page = 1
                start_row = 0

        found = 0
        succeed = retry_ok
        failed = retry_fail
        early_stop_triggered = False

        for page_no in range(start_page, total_pages + 1):
            d = current_rsp.get("data") or {}
            rows = d.get("list") or []
            if not isinstance(rows, list):
                rows = []

            if not rows:
                log_info(f"{icon('warn')} [第{page_no}页] 空页")
            else:
                log_info(f"{icon('page')} [第{page_no}页/{total_pages}] {len(rows)} 条")

            for i, row in enumerate(rows):
                if page_no == start_page and i < start_row:
                    continue

                _pub_date = normalize_date(row.get("pubDate") or "")
                _in_range, _too_old = is_in_date_range(_pub_date)
                if not _in_range:
                    if _too_old:
                        print(f"   ⏭️ [日期跳过] {clean_title(row.get('title') or '')[:40]} 披露日期 {_pub_date} < {START_DATE}")
                        if EARLY_STOP:
                            print(f"   ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止翻页")
                            early_stop_triggered = True
                            break
                    else:
                        print(f"   ⏭️ [日期跳过] {clean_title(row.get('title') or '')[:40]} 披露日期 {_pub_date} > {END_DATE or '今天'}")
                    continue

                found += 1
                title_preview = clean_title(row.get("title") or "")[:40]
                log_verbose(f"{icon('item')} 处理第{page_no}页第{i+1}条：{title_preview}")
                result, _ = self._download_one_item(notice_type, page_no, i, row)
                if result == "SUCCEED":
                    succeed += 1
                    log_verbose(f"{icon('ok')} 进度：成功{succeed} / 失败{failed} / 已处理{found}")
                elif result == "FAILED":
                    failed += 1
                    log_info(f"{icon('fail')} 进度：成功{succeed} / 失败{failed} / 已处理{found}")
                else:
                    log_verbose(f"{icon('skip')} 已跳过（历史已下载或已过滤）")

                self._set_checkpoint(notice_type, page_no, i + 1)

                random_sleep(ITEM_RANDOM_SLEEP_MIN, ITEM_RANDOM_SLEEP_MAX)

            if early_stop_triggered:
                print(f"   ⏹️ [早停] 模块[{notice_type}] 早停触发，停止翻页")
                break

            if page_no >= total_pages:
                break

            self._set_checkpoint(notice_type, page_no + 1, 0)

            next_rsp = self._goto_next_page_and_get_packet(notice_type)
            if not next_rsp:
                log_info(f"{icon('warn')} 第{page_no}页后未拿到下一页响应，结束当前模块。")
                break
            current_rsp = next_rsp
            random_sleep(PAGE_RANDOM_SLEEP_MIN, PAGE_RANDOM_SLEEP_MAX)

        if not self._has_pending_failed_for_notice_type(notice_type):
            self._mark_module_completed(notice_type)
        else:
            log_info(f"{icon('warn')} 模块[{notice_type}] 仍有失败案例，保留未完成状态")

        log_info(f"{icon('done')} 模块完成：发现 {found} | 成功 {succeed} | 失败 {failed}")
        return found, succeed, failed

    def crawl(self):
        log_info(f"已有去重记录：{len(self.progress_set)}")
        target_notice_types = get_target_notice_types()
        log_info(f"本次抓取模块：{', '.join(target_notice_types)}")
        self.open_notice_page()

        grand_found = 0
        grand_succeed = 0
        grand_failed = 0

        for notice_type in target_notice_types:
            module_state = self._ensure_module_state(notice_type)
            if module_state.get("completed") and not self._has_pending_failed_for_notice_type(notice_type):
                log_info(f"{icon('skip')} 模块[{notice_type}] 已完成且无失败遗留，直接跳过")
                continue

            f, s, fail = self.crawl_one_notice_type(notice_type)
            grand_found += f
            grand_succeed += s
            grand_failed += fail

        log_info(f"\n{'=' * 68}")
        log_info(f"{icon('done')} 全部模块抓取完成")
        log_info(f"尝试处理：{grand_found}")
        log_info(f"成功：{grand_succeed}")
        log_info(f"失败：{grand_failed}")
        log_info(f"{icon('save')} 下载目录：{os.path.abspath(DOWNLOAD_ROOT)}")
        log_info(f"{icon('save')} 日志文件：{os.path.abspath(LOG_CSV_PATH)}")
        log_info(f"{'=' * 68}")


def main():
    spider = CMBWMSpider()
    try:
        spider.crawl()
    finally:
        spider.close()


if __name__ == "__main__":
    main()
