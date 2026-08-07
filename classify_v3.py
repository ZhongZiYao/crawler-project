# -*- coding: utf-8 -*-
"""
分类脚本 v3 —— 在 classify.py 基础上增加：
1. 排除光大银行
2. 临时公告四类关键词扫描扩展到所有非定期、非产品说明书文件夹
   （其他公告/其他产品公告/运作公告/到期公告/公司公告等也扫描）
3. 产品说明书&发行公告：同一产品同时存在两种时只保留产品说明书
4. 清空旧分类结果后重新分类
5. 集成 reclassify_temp 逻辑（产品说明书&发行公告中也扫描临时公告）

用法:
    python classify_v3.py --dry-run     # 预览，不复制
    python classify_v3.py               # 正式分类
    python classify_v3.py --only A01    # 只分类指定项目
    python classify_v3.py --verbose     # 显示每个文件详情
"""

import argparse
import csv
import os
import re
import shutil
import sys
import warnings
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime

# Windows GBK 控制台兼容
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

warnings.filterwarnings("ignore")
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from project_meta import PROJECT_VENV, PROJECT_ORG  # noqa: E402

# ============================================================
# 配置
# ============================================================

OUTPUT_ROOT = os.path.join(SCRIPT_DIR, "分类结果")

# 排除的项目
EXCLUDE_PROJECTS = {"光大银行"}

# 三大一级分类
CAT_PERIODIC = "定期报告"
CAT_TEMP = "临时报告"
CAT_PRODUCT = "产品说明书&发行公告"
CAT_OTHER = "其他"

# 定期报告仅保留以下机构（规则文档指定6家，当前项目含4家）
PERIODIC_ORG_WHITELIST = {
    "建信理财", "交银理财", "浦银理财", "恒丰理财", "浙银理财", "工银理财",
}

# 定期报告二级分类关键字
PERIODIC_KEYWORDS = {
    "季度报告": ["季度", "季报", "一季度", "二季度", "三季度", "四季度", "第一季", "第二季", "第三季", "第四季"],
    "半年度报告": ["半年度", "半年报", "中期报告", "中期"],
    "年度报告": ["年度", "年报", "投资报告"],
}

# 临时公告分类关键字
TEMP_KEYWORDS = {
    "费率调整及费率优惠": [
        "托管费", "固定管理费", "浮动管理费", "销售服务费",
        "认购费", "申购费", "赎回费", "费率优惠", "费率调整", "费率", "费用",
    ],
    "业绩比较基准调整": [
        "业绩比较基准",
    ],
    "新设份额": [
        "增设", "新增份额", "类份额",
    ],
}
NEW_SHARE_COOCCUR = ["新增", "份额"]

# 这些公告类型虽然是"其他"类，但不扫描临时关键词
# 因为净值公告/业绩公告/到期公告等全文天然含"费率""业绩比较基准"等词，
# 扫描会产生大量误匹配。用户要求的是从"其他公告/其他产品公告/运作公告"等中筛选。
NO_SCAN_TYPES = {
    "净值公告", "产品净值公告", "估值日公告", "净值",
    "业绩公告", "业绩",
    "到期公告", "到期", "兑付公告",
    "公司公告",
    "产品定期公告", "定期公告",
    "清算公告",
}

# 公告类型 -> 一级分类 的映射
NOTICE_TYPE_MAP = {
    "定期报告": CAT_PERIODIC,
    "perReport": CAT_PERIODIC,
    "季度报告": CAT_PERIODIC,
    "半年度报告": CAT_PERIODIC,
    "年度报告": CAT_PERIODIC,
    "产品定期公告": CAT_PERIODIC,
    "临时性信息披露": CAT_TEMP,
    "临时公告": CAT_TEMP,
    "临时报告": CAT_TEMP,
    "tempInfoDisclosure": CAT_TEMP,
    "产品说明书": CAT_PRODUCT,
    "发行公告": CAT_PRODUCT,
    "issuReport": CAT_PRODUCT,
    "说明书": CAT_PRODUCT,
    "销售协议书": CAT_PRODUCT,
    "成立公告": CAT_PRODUCT,
    "到期公告": CAT_OTHER,
    "公司公告": CAT_OTHER,
    "expireNotice": CAT_OTHER,
    "companyNotice": CAT_OTHER,
    "产品公告": CAT_OTHER,  # 产品公告默认归Other，但会扫描临时关键词
    "净值公告": CAT_OTHER,
    "产品净值公告": CAT_OTHER,
    "其他公告": CAT_OTHER,
    "其他产品公告": CAT_OTHER,
    "运作公告": CAT_OTHER,
    "法律文本": CAT_PRODUCT,  # 法律文本=风险揭示书/产品协议等，归入产品说明书
    "业绩公告": CAT_OTHER,  # 业绩公告归Other，但NO_SCAN_TYPES会阻止扫描
}

# 全文提取超时（秒）
TEXT_EXTRACT_TIMEOUT = 3.0
TEXT_EXTRACT_MAX_PAGES = 3
TEXT_EXTRACT_MAX_CHARS = 8000

# 线程池（用于超时控制）
_text_pool = ThreadPoolExecutor(max_workers=1)


# ============================================================
# 工具函数
# ============================================================

def normalize_date(date_text) -> str:
    text = str(date_text or "").strip()
    m = re.search(r"(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", text)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mm:02d}-{dd:02d}"
    return ""


def sanitize_folder(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", str(name or "")).strip()


def get_institution_label(proj_id: str) -> str:
    org = PROJECT_ORG.get(proj_id, proj_id)
    if proj_id and all(ord(c) < 128 for c in proj_id):
        return f"{proj_id}{org}"
    return org


# ============================================================
# 全文提取
# ============================================================

def _extract_text_fast(filepath: str, max_pages: int = TEXT_EXTRACT_MAX_PAGES,
                       max_chars: int = TEXT_EXTRACT_MAX_CHARS) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".pdf":
            import fitz
            doc = fitz.open(filepath)
            parts = []
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                parts.append(page.get_text())
                if sum(len(x) for x in parts) > max_chars:
                    break
            doc.close()
            return "\n".join(parts)[:max_chars]
        elif ext in (".doc", ".docx"):
            try:
                import docx
                doc = docx.Document(filepath)
                return "\n".join(p.text for p in doc.paragraphs)[:max_chars]
            except Exception:
                return ""
        elif ext in (".html", ".htm"):
            with open(filepath, encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars)
            text = re.sub(r"<[^>]+>", " ", content)
            return re.sub(r"\s+", " ", text)[:max_chars]
        elif ext == ".txt":
            with open(filepath, encoding="utf-8", errors="replace") as f:
                return f.read(max_chars)
        else:
            return ""
    except Exception:
        return ""


def _extract_text_with_timeout(filepath: str, timeout: float = TEXT_EXTRACT_TIMEOUT) -> str:
    future = _text_pool.submit(_extract_text_fast, filepath)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        return ""
    except Exception:
        return ""


# ============================================================
# 临时公告关键词匹配
# ============================================================

def _match_keywords(text: str) -> dict:
    """在文本中匹配各类别关键字，返回 {类别: 命中次数}。"""
    hits = {}
    for cat, keywords in TEMP_KEYWORDS.items():
        count = 0
        for kw in keywords:
            count += text.count(kw)
        if cat == "新设份额":
            if all(text.count(kw) > 0 for kw in NEW_SHARE_COOCCUR):
                count += 1
        hits[cat] = count
    return hits


def classify_temp_announcement(title: str, filepath: str):
    """
    临时公告分类：先标题匹配，标题不行再全文匹配。
    返回: (category, match_source)
      category: 费率调整及费率优惠 / 业绩比较基准调整 / 新设份额 / 特殊案例 / ""
      match_source: "title" / "fulltext" / "timeout"
    """
    title_hits = _match_keywords(title or "")
    if any(title_hits.values()):
        hits = title_hits
        match_source = "title"
    else:
        full_text = _extract_text_with_timeout(filepath)
        if full_text:
            full_hits = _match_keywords(full_text)
            hits = full_hits
            match_source = "fulltext"
        else:
            hits = title_hits
            match_source = "timeout"

    matched = [cat for cat, count in hits.items() if count > 0]
    if len(matched) >= 2:
        return "特殊案例", match_source
    elif len(matched) == 1:
        return matched[0], match_source
    else:
        return "", match_source


# ============================================================
# 下载目录探测
# ============================================================

KNOWN_DOWNLOAD_DIRS = [
    "download_files", "downloaded_pdfs", "downloaded_files",
    "download_pdf", "downloads", "output", "outputs",
]


def detect_download_dir(proj_dir: str) -> str:
    for name in KNOWN_DOWNLOAD_DIRS:
        d = os.path.join(proj_dir, name)
        if os.path.isdir(d):
            return d
    try:
        for entry in os.listdir(proj_dir):
            full = os.path.join(proj_dir, entry)
            if not os.path.isdir(full):
                continue
            if entry.startswith(".venv") or entry in (
                "__pycache__", ".idea", "logs", "分类结果", ".git", "state",
            ):
                continue
            lower = entry.lower()
            if "pdf" in lower or "download" in lower:
                return full
            try:
                for fn in os.listdir(full):
                    if fn.lower().endswith((".pdf", ".docx")):
                        return full
            except Exception:
                pass
    except Exception:
        pass
    return ""


# ============================================================
# CSV 日志查找与解析
# ============================================================

def find_csv_logs(proj_dir: str, dl_dir: str) -> list:
    logs = []
    seen = set()

    def _add(path):
        if path not in seen:
            logs.append(path)
            seen.add(path)

    logs_dir = os.path.join(proj_dir, "logs")
    if os.path.isdir(logs_dir):
        for fn in os.listdir(logs_dir):
            if fn.endswith(".csv"):
                _add(os.path.join(logs_dir, fn))

    if dl_dir and os.path.isdir(dl_dir):
        for fn in os.listdir(dl_dir):
            if fn.endswith(".csv"):
                _add(os.path.join(dl_dir, fn))

    for fn in os.listdir(proj_dir):
        fp = os.path.join(proj_dir, fn)
        if os.path.isfile(fp) and fn.endswith(".csv"):
            _add(fp)

    state_dir = os.path.join(proj_dir, "state")
    if os.path.isdir(state_dir):
        for fn in os.listdir(state_dir):
            if fn.endswith(".csv"):
                _add(os.path.join(state_dir, fn))

    # A03 特殊：download_files/产品净值公告/ 下有大量数据CSV，不能当日志
    # 通过排除来避免：只取已知日志文件名
    return logs


def resolve_csv_path(path: str, proj_dir: str) -> str:
    if not path:
        return ""
    path = path.strip().replace("/", "\\")
    if os.path.isabs(path) and os.path.exists(path):
        return path
    candidate = os.path.join(proj_dir, path)
    if os.path.exists(candidate):
        return os.path.normpath(candidate)
    basename = os.path.basename(path)
    if not basename:
        return ""
    dl_dir = detect_download_dir(proj_dir)
    if dl_dir:
        for dirpath, dirnames, filenames in os.walk(dl_dir):
            if basename in filenames:
                return os.path.join(dirpath, basename)
    for dirpath, dirnames, filenames in os.walk(proj_dir):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".venv") and d not in ("__pycache__", ".idea", ".git", "分类结果")
        ]
        if basename in filenames:
            return os.path.join(dirpath, basename)
    return ""


def parse_csv_logs(log_paths: list, proj_dir: str) -> dict:
    records = {}
    for lp in log_paths:
        try:
            content = None
            for enc, err_mode in [
                ("utf-8-sig", "strict"),
                ("utf-8-sig", "replace"),
                ("gbk", "strict"),
                ("gbk", "replace"),
                ("latin-1", "replace"),
            ]:
                try:
                    with open(lp, encoding=enc, errors=err_mode) as f:
                        content = f.read()
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            if content is None:
                continue

            reader = csv.reader(content.splitlines())
            header = next(reader, None)
            if not header:
                continue

            h = [c.strip() for c in header]
            col = {}
            for i, c in enumerate(h):
                cl = c.lower()
                if "机构" in c and "名称" in c:
                    col["inst"] = i
                elif "公告名称" in c or ("标题" in c and "公告" not in c and "子" not in c):
                    col["title"] = i
                elif "公告类型" in c or c == "类型":
                    col["type"] = i
                elif "日期" in c:
                    col["date"] = i
                elif "保存路径" in c or "路径" in c or cl == "path":
                    col["path"] = i
                elif "状态" in c or cl == "status":
                    col["status"] = i

            for row in reader:
                if not row:
                    continue
                status_idx = col.get("status")
                if status_idx is not None and status_idx < len(row):
                    status = row[status_idx].strip().upper()
                    if status and status not in ("SUCCEED", "SUCCESS", "成功", ""):
                        continue

                title = row[col["title"]] if "title" in col and col["title"] < len(row) else ""
                ntype = row[col["type"]] if "type" in col and col["type"] < len(row) else ""
                inst = row[col["inst"]] if "inst" in col and col["inst"] < len(row) else ""
                date_val = row[col["date"]] if "date" in col and col["date"] < len(row) else ""
                path_val = row[col["path"]] if "path" in col and col["path"] < len(row) else ""

                abs_path = resolve_csv_path(path_val, proj_dir)
                basename = ""
                if abs_path:
                    basename = os.path.basename(abs_path)
                elif path_val:
                    basename = os.path.basename(path_val)
                elif title:
                    basename = title

                if basename:
                    records[basename] = {
                        "inst": inst,
                        "title": title,
                        "type": ntype,
                        "date": normalize_date(date_val),
                        "path": abs_path or path_val,
                    }
        except Exception as e:
            print(f"  [!] 读取CSV失败 {os.path.basename(lp)}: {e}")
    return records


# ============================================================
# 文件扫描
# ============================================================

def scan_download_files(dl_dir: str) -> list:
    if not dl_dir or not os.path.isdir(dl_dir):
        return []
    results = []
    for dirpath, dirnames, filenames in os.walk(dl_dir):
        if ".venv" in dirpath or "__pycache__" in dirpath or "debug" in dirpath:
            continue
        for fn in filenames:
            if fn.startswith("."):
                continue
            if fn.endswith((".zip", ".7z", ".rar", ".json")):
                continue
            if fn.startswith(("checkpoint", "failed_", "downloaded_", "fingerprint")):
                continue
            if fn.endswith(".csv") and ("failed" in fn or "log" in fn or "记录" in fn or "日志" in fn):
                continue
            # A03 产品净值公告下的数据CSV不是公告文件
            if fn.endswith(".csv") and "产品净值" in dirpath:
                continue
            # .txt 文件如果是 downloaded.txt 等状态文件则跳过
            if fn.endswith(".txt") and fn.startswith(("downloaded", "failed", "log")):
                continue
            abs_path = os.path.join(dirpath, fn)
            rel_path = os.path.relpath(abs_path, dl_dir)
            results.append((abs_path, rel_path, fn))
    return results


def parse_filename_for_metadata(filename: str, folder_path: str, proj_org: str) -> dict:
    info = {"inst": "", "type": "", "date": "", "title": filename}
    folder_parts = folder_path.replace("\\", "/").split("/")
    for part in folder_parts:
        for key in NOTICE_TYPE_MAP:
            if key in part:
                info["type"] = part
                break
    m = re.search(r"(\d{4})[-_年](\d{1,2})[-_月](\d{1,2})", filename)
    if m:
        info["date"] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    else:
        m2 = re.search(r"披露日期[：:]\s*(\d{4}-\d{2}-\d{2})", filename)
        if m2:
            info["date"] = m2.group(1)
    for key in NOTICE_TYPE_MAP:
        if key in filename:
            info["type"] = key
            break
    for org_name in PROJECT_ORG.values():
        if filename.startswith(org_name) or org_name in filename[:20]:
            info["inst"] = org_name
            break
    if not info["inst"] and proj_org:
        info["inst"] = proj_org
    return info


# ============================================================
# 分类逻辑
# ============================================================

def _map_notice_type_to_category(notice_type: str, filename: str, rel_path: str) -> str:
    for key, cat in NOTICE_TYPE_MAP.items():
        if key in notice_type:
            return cat
    combined = f"{filename} {rel_path}"
    for key, cat in NOTICE_TYPE_MAP.items():
        if key in combined:
            return cat
    return CAT_OTHER


def _classify_periodic(notice_type: str, filename: str, title: str) -> str:
    combined = f"{notice_type} {filename} {title}"
    for cat, keywords in PERIODIC_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return cat
    return "年度报告"


def classify_file(abs_path: str, rel_path: str, filename: str,
                  csv_records: dict, proj_id: str) -> dict:
    org_label = get_institution_label(proj_id)
    org_name = PROJECT_ORG.get(proj_id, proj_id)

    meta = csv_records.get(filename, {})
    if not meta:
        meta = parse_filename_for_metadata(filename, rel_path, org_name)

    notice_type = meta.get("type", "")
    title = meta.get("title", filename)
    date = meta.get("date", "")
    inst = meta.get("inst", "") or org_name

    cat1 = _map_notice_type_to_category(notice_type, filename, rel_path)

    result = {
        "proj_id": proj_id,
        "org": org_name,
        "org_label": org_label,
        "filename": filename,
        "abs_path": abs_path,
        "title": title,
        "notice_type": notice_type,
        "date": date,
        "cat1": cat1,
        "cat2": "",
        "cat3": "",
        "skip": False,
        "skip_reason": "",
        "temp_category": "",
        "temp_source": "",
    }

    # 定期报告：仅保留白名单机构
    if cat1 == CAT_PERIODIC:
        if org_name not in PERIODIC_ORG_WHITELIST:
            # 非白名单机构的定期报告 → 直接跳过（不做临时关键词扫描，
            # 因为定期报告全文天然含"费率""业绩比较基准"等词，会产生大量误匹配）
            result["skip"] = True
            result["skip_reason"] = f"定期报告非白名单机构({org_name})"
            return result
        cat2 = _classify_periodic(notice_type, filename, title)
        result["cat2"] = cat2
        result["cat3"] = org_label
        return result

    # 临时公告：扫描四类关键词
    if cat1 == CAT_TEMP:
        temp_cat, src = classify_temp_announcement(title, abs_path)
        result["cat2"] = org_label
        if not temp_cat:
            result["skip"] = True
            result["skip_reason"] = "临时公告不匹配四类关键字"
        else:
            result["cat3"] = temp_cat
            result["temp_category"] = temp_cat
            result["temp_source"] = src
        return result

    # 产品说明书&发行公告
    if cat1 == CAT_PRODUCT:
        # 不扫描临时关键词：产品说明书/发行公告全文天然含"费率""业绩比较基准"等词，
        # 扫描会产生大量误匹配。用户要求的是从"其他公告/运作公告"等文件夹中筛选临时公告。
        result["cat2"] = org_label
        # 标记是产品说明书还是发行公告（用于后续优先级处理）
        result["_doc_subtype"] = "产品说明书" if "产品说明书" in notice_type or "说明书" in filename else "发行公告"
        return result

    # 其他类型（到期/公司/运作/其他公告等）：判断是否需要扫描临时关键词
    if cat1 == CAT_OTHER:
        # 净值公告/业绩公告/到期公告等不扫描临时关键词（全文天然含"费率"等词，会误匹配）
        combined_text = f"{notice_type} {filename} {rel_path}"
        should_skip_scan = any(ns in combined_text for ns in NO_SCAN_TYPES)
        if not should_skip_scan:
            temp_cat, src = classify_temp_announcement(title, abs_path)
            if temp_cat:
                result["cat1"] = CAT_TEMP
                result["cat2"] = org_label
                result["cat3"] = temp_cat
                result["temp_category"] = temp_cat
                result["temp_source"] = src
                return result
        result["skip"] = True
        reason = f"非三大类({notice_type or '未知'})"
        if should_skip_scan:
            reason += "(净值/业绩/到期/公司公告不扫描临时关键词)"
        elif not temp_cat:
            reason += "，且不匹配临时关键词"
        result["skip_reason"] = reason
        return result

    return result


# ============================================================
# 产品说明书 vs 发行公告 优先级处理
# ============================================================

# 用于从标题/文件名中提取产品名的去除词
PRODUCT_NAME_STRIP_PATTERNS = [
    "产品说明书", "发行公告", "成立公告", "风险揭示书", "销售协议书",
    "说明书", "发行", "成立", "披露日期", "风险揭示",
]

# 提取产品关键字的正则
_PRODUCT_KEY_RE = re.compile(
    r"(产品说明书|发行公告|成立公告|风险揭示书|销售协议书|说明书|披露日期?[：:]?\s*\d{4}[-/]?\d{1,2}[-/]?\d{1,2})"
)


def extract_product_key(title: str, filename: str, notice_type: str) -> str:
    """
    从标题/文件名中提取产品关键字，用于匹配同一产品的不同文档类型。
    去掉文档类型词、日期、机构名、编码等，只保留产品名称核心。
    """
    text = f"{title} {filename}"
    # 去掉类型词（_PRODUCT_KEY_RE 是一个编译好的正则，直接 sub）
    text = _PRODUCT_KEY_RE.sub("", text)
    # 去掉日期
    text = re.sub(r"\d{4}[-_]?\d{1,2}[-_]?\d{1,2}", "", text)
    # 去掉机构名
    for org in PROJECT_ORG.values():
        text = text.replace(org, "")
    # 去掉常见前缀/后缀
    text = re.sub(r"[_\-－—（）()\[\]【】\s]+", "", text)
    # 去掉产品编码（字母+数字组合，如 PB300007, AMHQLXQT01B）
    text = re.sub(r"[A-Z]{2,}\d{3,}[A-Z0-9]*", "", text)
    text = re.sub(r"C\d{10,}[A-Z0-9\-]*", "", text)
    # 去掉纯数字编码
    text = re.sub(r"\d{5,}", "", text)
    return text.strip().lower()


def apply_product_priority(results: list) -> int:
    """
    对 CAT_PRODUCT 中的文件，如果同一产品同时有产品说明书和发行公告，
    只保留产品说明书，标记发行公告为跳过。
    返回跳过的文件数。
    """
    # 收集所有 CAT_PRODUCT 文件
    product_files = [r for r in results if r["cat1"] == CAT_PRODUCT and not r["skip"]]

    # 按机构分组
    by_org = defaultdict(list)
    for r in product_files:
        by_org[r["org_label"]].append(r)

    skipped = 0
    for org_label, files in by_org.items():
        # 分成产品说明书和发行公告两组
        manuals = []  # 产品说明书
        issuances = []  # 发行公告
        for f in files:
            subtype = f.get("_doc_subtype", "")
            nt = f["notice_type"]
            fn = f["filename"]
            if "产品说明书" in nt or "说明书" in fn:
                manuals.append(f)
            elif "发行公告" in nt or "发行" in fn or "成立公告" in nt or "成立" in fn:
                issuances.append(f)
            else:
                # 无法确定的，保留
                pass

        if not manuals or not issuances:
            continue

        # 提取产品说明书的产品关键字集合
        manual_keys = set()
        for m in manuals:
            key = extract_product_key(m["title"], m["filename"], m["notice_type"])
            if key and len(key) >= 4:  # 过滤太短的
                manual_keys.add(key)

        # 检查每个发行公告是否有对应的产品说明书
        for iss in issuances:
            key = extract_product_key(iss["title"], iss["filename"], iss["notice_type"])
            if key and len(key) >= 4 and key in manual_keys:
                iss["skip"] = True
                iss["skip_reason"] = "同产品已存在产品说明书，发行公告跳过"
                skipped += 1

    return skipped


# ============================================================
# 输出路径构建
# ============================================================

def build_dest_path(result: dict) -> str:
    cat1 = result["cat1"]
    parts = [OUTPUT_ROOT, cat1]

    if cat1 == CAT_PERIODIC:
        if result.get("cat2"):
            parts.append(sanitize_folder(result["cat2"]))
        if result.get("cat3"):
            parts.append(sanitize_folder(result["cat3"]))
    elif cat1 == CAT_TEMP:
        if result.get("cat2"):
            parts.append(sanitize_folder(result["cat2"]))
        if result.get("cat3"):
            parts.append(sanitize_folder(result["cat3"]))
    elif cat1 == CAT_PRODUCT:
        if result.get("cat2"):
            parts.append(sanitize_folder(result["cat2"]))

    return os.path.join(*parts)


# ============================================================
# 统计输出
# ============================================================

def write_statistics(results: list, output_path: str):
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        print("  [!] openpyxl 未安装，跳过统计文件生成")
        return

    wb = openpyxl.Workbook()

    # 工作表1：分类统计
    ws = wb.active
    ws.title = "分类统计"
    stats = defaultdict(lambda: defaultdict(int))
    for r in results:
        if r["skip"]:
            continue
        key = f"{r['org_label']}\t{r['cat1']}\t{r.get('cat2', '')}\t{r.get('cat3', '')}"
        stats[key]["count"] += 1

    headers = ["机构", "一级分类", "二级分类", "三级分类", "文件数"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        ws.cell(row=1, column=c).font = Font(bold=True)

    for key, val in sorted(stats.items()):
        parts = key.split("\t")
        ws.append(list(parts) + [val["count"]])
    ws.append([])
    ws.append(["合计", "", "", "", sum(v["count"] for v in stats.values())])

    # 工作表2：跳过统计
    ws2 = wb.create_sheet("跳过统计")
    skip_stats = defaultdict(lambda: defaultdict(int))
    for r in results:
        if not r["skip"]:
            continue
        key = f"{r['org_label']}\t{r['skip_reason']}"
        skip_stats[key]["count"] += 1

    ws2.append(["机构", "跳过原因", "文件数"])
    for c in range(1, 4):
        ws2.cell(row=1, column=c).font = Font(bold=True)
    for key, val in sorted(skip_stats.items()):
        parts = key.split("\t")
        ws2.append(list(parts) + [val["count"]])
    ws2.append([])
    ws2.append(["跳过合计", "", sum(v["count"] for v in skip_stats.values())])

    # 工作表3：项目明细
    ws3 = wb.create_sheet("项目明细")
    proj_stats = defaultdict(lambda: {"copied": 0, "skipped": 0})
    for r in results:
        if r["skip"]:
            proj_stats[r["proj_id"]]["skipped"] += 1
        else:
            proj_stats[r["proj_id"]]["copied"] += 1

    ws3.append(["项目", "机构", "已分类", "已跳过", "合计"])
    for c in range(1, 6):
        ws3.cell(row=1, column=c).font = Font(bold=True)
    for proj_id in sorted(proj_stats.keys()):
        s = proj_stats[proj_id]
        org = PROJECT_ORG.get(proj_id, proj_id)
        ws3.append([proj_id, org, s["copied"], s["skipped"], s["copied"] + s["skipped"]])
    ws3.append([])
    ws3.append([
        "合计", "",
        sum(s["copied"] for s in proj_stats.values()),
        sum(s["skipped"] for s in proj_stats.values()),
        sum(s["copied"] + s["skipped"] for s in proj_stats.values()),
    ])

    wb.save(output_path)
    print(f"  统计文件已保存: {output_path}")


# ============================================================
# 清空旧结果
# ============================================================

def clear_output_root():
    """清空分类结果目录（保留目录本身）。"""
    if not os.path.isdir(OUTPUT_ROOT):
        os.makedirs(OUTPUT_ROOT, exist_ok=True)
        return
    for item in os.listdir(OUTPUT_ROOT):
        item_path = os.path.join(OUTPUT_ROOT, item)
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        except Exception as e:
            print(f"  [!] 清理失败 {item}: {e}")


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="分类全部下载文件 v3（排除光大，全文件夹扫描临时公告）")
    parser.add_argument("--only", type=str, default="", help="只分类指定项目（逗号分隔）")
    parser.add_argument("--dry-run", action="store_true", help="只打印分类结果，不实际复制")
    parser.add_argument("--verbose", action="store_true", help="显示每个文件的详细信息")
    parser.add_argument("--no-clear", action="store_true", help="不清空旧分类结果")
    args = parser.parse_args()

    only_set = set()
    if args.only:
        only_set = {x.strip() for x in args.only.split(",") if x.strip()}

    print(f"分类输出目录: {OUTPUT_ROOT}")
    print(f"排除项目: {', '.join(sorted(EXCLUDE_PROJECTS))}")
    print(f"模式: {'dry-run（不复制）' if args.dry_run else '复制文件'}")
    print()

    # 清空旧结果
    if not args.no_clear and not args.dry_run:
        print("清空旧分类结果...")
        clear_output_root()
        print()

    all_results = []
    total_copied = 0
    total_skipped = 0
    t_start = time.time()

    for proj_id in PROJECT_VENV:
        if proj_id in EXCLUDE_PROJECTS:
            print(f"[{proj_id}] 已排除（光大银行不参与分类）")
            continue
        if only_set and proj_id not in only_set:
            continue

        org = PROJECT_ORG.get(proj_id, proj_id)
        proj_dir = os.path.join(SCRIPT_DIR, proj_id)
        if not os.path.isdir(proj_dir):
            continue

        print(f"[{proj_id}] {org}")

        dl_dir = detect_download_dir(proj_dir)
        if not dl_dir:
            print(f"  [!] 未找到下载目录，跳过")
            continue
        print(f"  下载目录: {os.path.relpath(dl_dir, SCRIPT_DIR)}")

        log_paths = find_csv_logs(proj_dir, dl_dir)
        csv_records = {}
        if log_paths:
            print(f"  读取 {len(log_paths)} 个 CSV 日志")
            csv_records = parse_csv_logs(log_paths, proj_dir)
            print(f"  CSV 记录: {len(csv_records)} 条")

        files = scan_download_files(dl_dir)
        if not files:
            print(f"  无下载文件，跳过")
            continue
        print(f"  扫描到 {len(files)} 个文件")

        proj_copied = 0
        proj_skipped = 0
        proj_temp_title = 0
        proj_temp_fulltext = 0

        for i, (abs_path, rel_path, filename) in enumerate(files):
            if (i + 1) % 500 == 0:
                elapsed = time.time() - t_start
                sys.stderr.write(f"  ... 进度 {i+1}/{len(files)} ({elapsed:.0f}s)\n")
                sys.stderr.flush()

            result = classify_file(abs_path, rel_path, filename, csv_records, proj_id)
            all_results.append(result)

            if result.get("temp_source") == "title":
                proj_temp_title += 1
            elif result.get("temp_source") == "fulltext":
                proj_temp_fulltext += 1

            if result["skip"]:
                total_skipped += 1
                proj_skipped += 1
                if args.verbose:
                    print(f"    [SKIP] {filename} ({result['skip_reason']})")
                continue

            if args.dry_run:
                if args.verbose or proj_copied < 5:
                    print(f"    {result['cat1']}/{result.get('cat2', '')}/{result.get('cat3', '')}  <-  {filename[:60]}")
                total_copied += 1
                proj_copied += 1
            else:
                dest_dir = build_dest_path(result)
                dest_path = os.path.join(dest_dir, filename)
                os.makedirs(dest_dir, exist_ok=True)
                if not os.path.exists(dest_path):
                    try:
                        shutil.copy2(abs_path, dest_path)
                        total_copied += 1
                        proj_copied += 1
                    except Exception as e:
                        print(f"    [X] 复制失败: {filename} -> {e}")
                else:
                    total_copied += 1
                    proj_copied += 1

        print(f"  完成: 分类 {proj_copied}, 跳过 {proj_skipped}", end="")
        if proj_temp_title or proj_temp_fulltext:
            print(f" (临时: 标题命中 {proj_temp_title}, 全文命中 {proj_temp_fulltext})", end="")
        print()
        print()

    # 产品说明书优先级处理
    if not args.dry_run:
        print("产品说明书 vs 发行公告 优先级处理...")
        priority_skipped = apply_product_priority(all_results)
        # 对于被优先级跳过的文件，需要从分类结果目录中删除已复制的发行公告
        if priority_skipped > 0:
            removed = 0
            for r in all_results:
                if r["skip"] and "同产品已存在产品说明书" in r["skip_reason"]:
                    dest_dir = build_dest_path(r)
                    dest_path = os.path.join(dest_dir, r["filename"])
                    if os.path.exists(dest_path):
                        try:
                            os.remove(dest_path)
                            removed += 1
                        except Exception:
                            pass
                    total_copied -= 1
            print(f"  优先级跳过: {priority_skipped} 个发行公告 (已删除 {removed} 个副本)")
            total_skipped += priority_skipped
        print()

    # 生成统计
    if all_results and not args.dry_run:
        stats_path = os.path.join(OUTPUT_ROOT, "分类统计.xlsx")
        write_statistics(all_results, stats_path)

    elapsed = time.time() - t_start
    print(f"{'=' * 60}")
    if args.dry_run:
        print(f"总计: 待分类 {total_copied} 个文件, 跳过 {total_skipped} 个 (dry-run)")
    else:
        print(f"总计: 分类 {total_copied} 个文件, 跳过 {total_skipped} 个")
    print(f"耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
