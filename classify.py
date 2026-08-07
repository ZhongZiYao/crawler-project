# -*- coding: utf-8 -*-
"""
分类脚本 v2 —— 下载完成后按业务规则将全部文件分类到统一目录。

v2 改进点：
1. 自动探测各项目下载目录（download_files / downloaded_pdfs / downloaded_files / download_pdf / *pdf 等）
2. CSV 日志搜索范围扩展（项目根目录 + logs/ 子目录 + 下载目录）
3. 保存路径相对路径自动解析（c03/c05/c07 等项目使用相对路径）
4. 定期报告仅保留规则指定的 6 家机构（建信/交银/浦银/恒丰/浙银/工银理财）
5. CSV 列匹配更健壮（兼容 B01 唯一键、B12 列顺序不同、广州银行引号、光大银行扩展列）
6. 统计 xlsx 增加"跳过统计"工作表

用法:
    python classify.py              # 分类全部项目
    python classify.py --only A01   # 只分类指定项目（逗号分隔）
    python classify.py --dry-run    # 只打印分类结果，不实际复制
    python classify.py --verbose    # 显示每个文件的详细信息

分类规则来源：历史信息披露文件下载规则.docx
输出目录：分类结果/（复制文件，不移动原始文件）
统计文件：分类结果/分类统计.xlsx
"""

import argparse
import csv
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime

# Windows 控制台默认 GBK 编码，遇到罕见 Unicode 字符会崩溃
# 强制 stdout/stderr 使用 UTF-8 + 替换不可编码字符
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from project_meta import PROJECT_VENV, PROJECT_ORG  # noqa: E402

# ============================================================
# 分类规则配置
# ============================================================

OUTPUT_ROOT = os.path.join(SCRIPT_DIR, "分类结果")

# 三大一级分类
CAT_PERIODIC = "定期报告"
CAT_TEMP = "临时报告"
CAT_PRODUCT = "产品说明书&发行公告"
CAT_OTHER = "其他"

# 定期报告仅保留以下机构（规则文档指定）
# 建信理财、恒丰理财不在当前 22 个项目中，但保留以便将来扩展
PERIODIC_ORG_WHITELIST = {
    "建信理财", "交银理财", "浦银理财", "恒丰理财", "浙银理财", "工银理财",
}

# 定期报告二级分类关键字
PERIODIC_KEYWORDS = {
    "季度报告": ["季度", "季报", "一季度", "二季度", "三季度", "四季度", "第一季", "第二季", "第三季", "第四季"],
    "半年度报告": ["半年度", "半年报", "中期报告", "中期"],
    "年度报告": ["年度", "年报", "投资报告"],
}

# 临时公告分类关键字（5个txt取并集，最大集匹配）
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
# 新设份额共现关键字：文本中同时出现"新增"和"份额"才算命中
NEW_SHARE_COOCCUR = ["新增", "份额"]

# 公告类型 -> 一级分类 的映射
NOTICE_TYPE_MAP = {
    "定期报告": CAT_PERIODIC,
    "perReport": CAT_PERIODIC,
    "季度报告": CAT_PERIODIC,
    "半年度报告": CAT_PERIODIC,
    "年度报告": CAT_PERIODIC,
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
    "产品公告": CAT_PRODUCT,
    "净值公告": CAT_OTHER,
}


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
    """生成机构文件夹标签，如 'A01工银理财' 或 '上海农商银行'。"""
    org = PROJECT_ORG.get(proj_id, proj_id)
    if proj_id and all(ord(c) < 128 for c in proj_id):
        return f"{proj_id}{org}"
    return org


# ============================================================
# 下载目录自动探测
# ============================================================

# 已知的下载目录名（按常见度排序）
KNOWN_DOWNLOAD_DIRS = [
    "download_files", "downloaded_pdfs", "downloaded_files",
    "download_pdf", "downloads", "output", "outputs",
]


def detect_download_dir(proj_dir: str) -> str:
    """
    自动探测项目的下载目录。
    策略：1) 先匹配已知目录名  2) 再扫描一级子目录找含 PDF 的。
    返回绝对路径，找不到返回空字符串。
    """
    # 1. 尝试已知目录名
    for name in KNOWN_DOWNLOAD_DIRS:
        d = os.path.join(proj_dir, name)
        if os.path.isdir(d):
            return d

    # 2. 深度探测：扫描一级子目录
    try:
        for entry in os.listdir(proj_dir):
            full = os.path.join(proj_dir, entry)
            if not os.path.isdir(full):
                continue
            # 跳过 venv、缓存等
            if entry.startswith(".venv") or entry in (
                "__pycache__", ".idea", "logs", "分类结果", ".git", "state",
            ):
                continue
            # 目录名含 "pdf" 或 "download"
            lower = entry.lower()
            if "pdf" in lower or "download" in lower:
                return full
            # 目录内直接有 PDF 文件
            try:
                for fn in os.listdir(full):
                    if fn.lower().endswith(".pdf"):
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
    """
    查找项目目录下的 CSV 日志文件。
    搜索范围：项目根目录 + logs/ 子目录 + 下载目录。
    跳过 venv、__pycache__ 等。
    """
    logs = []
    seen = set()

    def _add(path):
        if path not in seen:
            logs.append(path)
            seen.add(path)

    # 1. logs/ 子目录（A01 使用）
    logs_dir = os.path.join(proj_dir, "logs")
    if os.path.isdir(logs_dir):
        for fn in os.listdir(logs_dir):
            if fn.endswith(".csv"):
                _add(os.path.join(logs_dir, fn))

    # 2. 下载目录中的 CSV
    if dl_dir and os.path.isdir(dl_dir):
        for fn in os.listdir(dl_dir):
            if fn.endswith(".csv"):
                _add(os.path.join(dl_dir, fn))

    # 3. 项目根目录的 CSV
    for fn in os.listdir(proj_dir):
        fp = os.path.join(proj_dir, fn)
        if os.path.isfile(fp) and fn.endswith(".csv"):
            _add(fp)

    # 4. state/ 子目录（光大银行使用）
    state_dir = os.path.join(proj_dir, "state")
    if os.path.isdir(state_dir):
        for fn in os.listdir(state_dir):
            if fn.endswith(".csv"):
                _add(os.path.join(state_dir, fn))

    # 5. 递归兜底（跳过 venv）
    if not logs:
        for dirpath, dirnames, filenames in os.walk(proj_dir):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".venv") and d not in ("__pycache__", ".idea", ".git", "分类结果")
            ]
            for fn in filenames:
                if fn.endswith(".csv"):
                    _add(os.path.join(dirpath, fn))

    return logs


def resolve_csv_path(path: str, proj_dir: str) -> str:
    """
    将 CSV 中的保存路径解析为绝对路径。
    处理相对路径：相对于 proj_dir 解析。
    """
    if not path:
        return ""
    path = path.strip().replace("/", "\\")

    # 绝对路径且存在
    if os.path.isabs(path) and os.path.exists(path):
        return path

    # 相对路径：相对于 proj_dir
    candidate = os.path.join(proj_dir, path)
    if os.path.exists(candidate):
        return os.path.normpath(candidate)

    # 只取文件名，在 proj_dir 下浅层搜索
    basename = os.path.basename(path)
    if not basename:
        return ""

    # 先在下载目录里找
    dl_dir = detect_download_dir(proj_dir)
    if dl_dir:
        for dirpath, dirnames, filenames in os.walk(dl_dir):
            if basename in filenames:
                return os.path.join(dirpath, basename)

    # 再在整个 proj_dir 下搜索（跳过 venv）
    for dirpath, dirnames, filenames in os.walk(proj_dir):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".venv") and d not in ("__pycache__", ".idea", ".git", "分类结果")
        ]
        if basename in filenames:
            return os.path.join(dirpath, basename)

    return ""


def parse_csv_logs(log_paths: list, proj_dir: str) -> dict:
    """
    解析 CSV 日志，返回 {filename_basename: {机构,公告名称,公告类型,披露日期,保存路径}} 字典。

    兼容不同项目的 CSV 列顺序和命名：
    - 标准列：机构名称,公告名称,公告类型,披露日期,披露时间,状态,来源链接,保存路径,unique_key
    - B01：unique_key → 唯一键
    - B12：列顺序不同（披露时间在前）
    - 广州银行：引号包裹的 CSV
    - 光大银行：扩展列（产品名称,文件大小）
    - A01：5 个 CSV 在 logs/ 子目录
    """
    records = {}
    for lp in log_paths:
        try:
            # 读取整个文件内容，尝试不同编码
            # 优先严格解码；失败后用 errors="replace" 容忍少量坏字节，
            # 避免回退到 latin-1 导致中文全部乱码（如 A03 CSV）。
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

            # 从字符串解析 CSV
            reader = csv.reader(content.splitlines())
            header = next(reader, None)
            if not header:
                continue

            h = [c.strip() for c in header]

            # 按关键字匹配列索引
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
                # unique_key / 唯一键 不需要匹配（它不是文件名）

            for row in reader:
                if not row:
                    continue

                # 检查状态列：只保留成功的记录
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

                # 解析路径（处理相对路径）
                abs_path = resolve_csv_path(path_val, proj_dir)

                # 确定 basename 作为 key
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
    """
    扫描下载目录，返回 [(abs_path, rel_path, filename)] 列表。
    跳过非文档文件和状态文件。
    """
    if not dl_dir or not os.path.isdir(dl_dir):
        return []
    results = []
    for dirpath, dirnames, filenames in os.walk(dl_dir):
        if ".venv" in dirpath or "__pycache__" in dirpath or "debug" in dirpath:
            continue
        for fn in filenames:
            if fn.startswith(".") or fn.endswith((".zip", ".7z", ".rar", ".txt", ".json")):
                continue
            if fn.startswith(("checkpoint", "failed_", "downloaded_", "fingerprint")):
                continue
            if fn.endswith(".csv") and ("failed" in fn or "log" in fn or "记录" in fn or "日志" in fn):
                continue
            abs_path = os.path.join(dirpath, fn)
            rel_path = os.path.relpath(abs_path, dl_dir)
            results.append((abs_path, rel_path, fn))
    return results


def parse_filename_for_metadata(filename: str, folder_path: str, proj_org: str) -> dict:
    """从文件名和所在文件夹解析机构、类型、日期信息。"""
    info = {"inst": "", "type": "", "date": "", "title": filename}

    # 从文件夹名提取类型
    folder_parts = folder_path.replace("\\", "/").split("/")
    for part in folder_parts:
        for key in NOTICE_TYPE_MAP:
            if key in part:
                info["type"] = part
                break

    # 从文件名提取日期
    m = re.search(r"(\d{4})[-_年](\d{1,2})[-_月](\d{1,2})", filename)
    if m:
        info["date"] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    else:
        m2 = re.search(r"披露日期[：:]\s*(\d{4}-\d{2}-\d{2})", filename)
        if m2:
            info["date"] = m2.group(1)

    # 从文件名提取类型
    for key in NOTICE_TYPE_MAP:
        if key in filename:
            info["type"] = key
            break

    # 机构名通常在文件名开头
    for org_name in PROJECT_ORG.values():
        if filename.startswith(org_name) or org_name in filename[:20]:
            info["inst"] = org_name
            break

    if not info["inst"] and proj_org:
        info["inst"] = proj_org

    return info


# ============================================================
# 全文提取（用于临时公告分类）
# ============================================================

def extract_text(filepath: str, max_chars: int = 50000) -> str:
    """从文件中提取文本（用于关键字匹配）。"""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".pdf":
            return _extract_pdf_text(filepath, max_chars)
        elif ext in (".doc", ".docx"):
            return _extract_docx_text(filepath, max_chars)
        elif ext in (".html", ".htm"):
            return _extract_html_text(filepath, max_chars)
        elif ext == ".txt":
            with open(filepath, encoding="utf-8", errors="replace") as f:
                return f.read(max_chars)
        else:
            return ""
    except Exception:
        return ""


def _extract_pdf_text(filepath: str, max_chars: int) -> str:
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text_parts.append(t)
                if sum(len(x) for x in text_parts) > max_chars:
                    break
        return "\n".join(text_parts)[:max_chars]
    except ImportError:
        pass
    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        text_parts = []
        for page in reader.pages:
            t = page.extract_text() or ""
            text_parts.append(t)
            if sum(len(x) for x in text_parts) > max_chars:
                break
        return "\n".join(text_parts)[:max_chars]
    except Exception:
        return ""


def _extract_docx_text(filepath: str, max_chars: int) -> str:
    try:
        import docx
        doc = docx.Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs)[:max_chars]
    except Exception:
        return ""


def _extract_html_text(filepath: str, max_chars: int) -> str:
    with open(filepath, encoding="utf-8", errors="replace") as f:
        content = f.read(max_chars)
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"\s+", " ", text)
    return text


# ============================================================
# 临时公告分类（5个txt并集，最大集匹配）
# ============================================================

def classify_temp_announcement(title: str, filepath: str) -> str:
    """
    临时公告分类：先标题匹配，标题不行再全文匹配，取最大集。
    返回四类之一：费率调整及费率优惠 / 业绩比较基准调整 / 新设份额 / 特殊案例
    如果都不匹配，返回空字符串（不归入四类）。
    """
    # 第一轮：标题匹配
    title_hits = _match_keywords(title or "")

    # 第二轮：如果标题没匹配到任何类别，全文匹配
    if not any(title_hits.values()):
        full_text = extract_text(filepath)
        full_hits = _match_keywords(full_text)
        hits = full_hits
    else:
        hits = title_hits

    # 统计命中的类别数
    matched_categories = [cat for cat, count in hits.items() if count > 0]

    if len(matched_categories) >= 2:
        return "特殊案例"
    elif len(matched_categories) == 1:
        return matched_categories[0]
    else:
        return ""  # 不归入四类


def _match_keywords(text: str) -> dict:
    """在文本中匹配各类别关键字，返回 {类别: 命中次数}。"""
    hits = {}
    for cat, keywords in TEMP_KEYWORDS.items():
        count = 0
        for kw in keywords:
            count += text.count(kw)
        # 新设份额共现检查："新增"和"份额"同时出现也算命中
        if cat == "新设份额":
            if all(text.count(kw) > 0 for kw in NEW_SHARE_COOCCUR):
                count += 1  # 共现算一次命中
        hits[cat] = count
    return hits


# ============================================================
# 主分类逻辑
# ============================================================

def classify_file(abs_path: str, rel_path: str, filename: str,
                  csv_records: dict, proj_id: str) -> dict:
    """
    对单个文件进行分类，返回分类信息。
    """
    org_label = get_institution_label(proj_id)
    org_name = PROJECT_ORG.get(proj_id, proj_id)

    # 优先从 CSV 日志获取元数据
    meta = csv_records.get(filename, {})
    if not meta:
        meta = parse_filename_for_metadata(filename, rel_path, org_name)

    notice_type = meta.get("type", "")
    title = meta.get("title", filename)
    date = meta.get("date", "")
    inst = meta.get("inst", "") or org_name

    # 确定一级分类
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
    }

    if cat1 == CAT_OTHER:
        result["skip"] = True
        result["skip_reason"] = "非三大类（到期/公司公告/净值公告等）"
        return result

    if cat1 == CAT_PERIODIC:
        # 定期报告：仅保留规则指定的 6 家机构
        if org_name not in PERIODIC_ORG_WHITELIST:
            result["skip"] = True
            result["skip_reason"] = f"定期报告仅保留指定机构，当前={org_name}"
            return result
        # 二级分类（季度/半年度/年度）
        cat2 = _classify_periodic(notice_type, filename, title)
        result["cat2"] = cat2
        result["cat3"] = org_label

    elif cat1 == CAT_TEMP:
        # 临时报告 → 机构 → 四类
        result["cat2"] = org_label
        cat3 = classify_temp_announcement(title, abs_path)
        if not cat3:
            result["skip"] = True
            result["skip_reason"] = "临时公告不匹配四类关键字"
        else:
            result["cat3"] = cat3

    elif cat1 == CAT_PRODUCT:
        # 产品说明书&发行公告 → 机构
        result["cat2"] = org_label

    return result


def _map_notice_type_to_category(notice_type: str, filename: str, rel_path: str) -> str:
    """将公告类型映射到一级分类。"""
    # 先从 notice_type 精确匹配
    for key, cat in NOTICE_TYPE_MAP.items():
        if key in notice_type:
            return cat
    # 从文件名 + 相对路径匹配
    combined = f"{filename} {rel_path}"
    for key, cat in NOTICE_TYPE_MAP.items():
        if key in combined:
            return cat
    return CAT_OTHER


def _classify_periodic(notice_type: str, filename: str, title: str) -> str:
    """定期报告二级分类：季度/半年度/年度。"""
    combined = f"{notice_type} {filename} {title}"
    for cat, keywords in PERIODIC_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return cat
    # 默认归入年度报告
    return "年度报告"


def build_dest_path(result: dict) -> str:
    """根据分类结果构建目标路径。"""
    cat1 = result["cat1"]
    parts = [OUTPUT_ROOT, cat1]

    if cat1 == CAT_PERIODIC:
        # 定期报告/季度报告/机构/
        if result["cat2"]:
            parts.append(sanitize_folder(result["cat2"]))
        if result["cat3"]:
            parts.append(sanitize_folder(result["cat3"]))
    elif cat1 == CAT_TEMP:
        # 临时报告/机构/子类/
        if result["cat2"]:
            parts.append(sanitize_folder(result["cat2"]))
        if result["cat3"]:
            parts.append(sanitize_folder(result["cat3"]))
    elif cat1 == CAT_PRODUCT:
        # 产品说明书&发行公告/机构/
        if result["cat2"]:
            parts.append(sanitize_folder(result["cat2"]))

    return os.path.join(*parts)


# ============================================================
# 统计输出
# ============================================================

def write_statistics(results: list, output_path: str):
    """生成分类统计 xlsx，包含分类统计和跳过统计两个工作表。"""
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        print("  [!] openpyxl 未安装，跳过统计文件生成")
        return

    wb = openpyxl.Workbook()

    # ---- 工作表1：分类统计 ----
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

    # ---- 工作表2：跳过统计 ----
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

    # ---- 工作表3：项目明细 ----
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
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="分类全部下载文件 v2")
    parser.add_argument("--only", type=str, default="", help="只分类指定项目（逗号分隔）")
    parser.add_argument("--dry-run", action="store_true", help="只打印分类结果，不实际复制")
    parser.add_argument("--verbose", action="store_true", help="显示每个文件的详细信息")
    args = parser.parse_args()

    only_set = set()
    if args.only:
        only_set = {x.strip() for x in args.only.split(",") if x.strip()}

    print(f"分类输出目录: {OUTPUT_ROOT}")
    print(f"模式: {'dry-run（不复制）' if args.dry_run else '复制文件'}")
    print()

    all_results = []
    total_copied = 0
    total_skipped = 0

    for proj_id in PROJECT_VENV:
        if only_set and proj_id not in only_set:
            continue

        org = PROJECT_ORG.get(proj_id, proj_id)
        proj_dir = os.path.join(SCRIPT_DIR, proj_id)

        if not os.path.isdir(proj_dir):
            continue

        print(f"[{proj_id}] {org}")

        # 探测下载目录
        dl_dir = detect_download_dir(proj_dir)
        if not dl_dir:
            print(f"  [!] 未找到下载目录，跳过")
            continue
        print(f"  下载目录: {os.path.relpath(dl_dir, SCRIPT_DIR)}")

        # 读取 CSV 日志
        log_paths = find_csv_logs(proj_dir, dl_dir)
        csv_records = {}
        if log_paths:
            print(f"  读取 {len(log_paths)} 个 CSV 日志")
            csv_records = parse_csv_logs(log_paths, proj_dir)
            print(f"  CSV 记录: {len(csv_records)} 条")

        # 扫描下载文件
        files = scan_download_files(dl_dir)
        if not files:
            print(f"  无下载文件，跳过")
            continue
        print(f"  扫描到 {len(files)} 个文件")

        proj_copied = 0
        proj_skipped = 0

        for abs_path, rel_path, filename in files:
            result = classify_file(abs_path, rel_path, filename, csv_records, proj_id)
            all_results.append(result)

            if result["skip"]:
                total_skipped += 1
                proj_skipped += 1
                if args.verbose:
                    print(f"    [SKIP] [跳过] {filename} ({result['skip_reason']})")
                continue

            dest_dir = build_dest_path(result)
            dest_path = os.path.join(dest_dir, filename)

            if args.dry_run:
                print(f"    {result['cat1']}/{result.get('cat2', '')}/{result.get('cat3', '')}  <-  {filename}")
                total_copied += 1
                proj_copied += 1
            else:
                os.makedirs(dest_dir, exist_ok=True)
                if not os.path.exists(dest_path):
                    try:
                        shutil.copy2(abs_path, dest_path)
                        total_copied += 1
                        proj_copied += 1
                    except Exception as e:
                        print(f"    [X] 复制失败: {filename} -> {e}")
                else:
                    # 已存在，跳过
                    if args.verbose:
                        print(f"    [SKIP] [已存在] {filename}")

        print(f"  完成: 待分类 {proj_copied}, 跳过 {proj_skipped}")
        print()

    # 生成统计
    if all_results:
        stats_path = os.path.join(OUTPUT_ROOT, "分类统计.xlsx")
        os.makedirs(OUTPUT_ROOT, exist_ok=True)
        write_statistics(all_results, stats_path)

    print(f"{'=' * 50}")
    if args.dry_run:
        print(f"总计: 待分类 {total_copied} 个文件, 跳过 {total_skipped} 个")
        print("（dry-run 模式，未实际复制）")
    else:
        print(f"总计: 复制 {total_copied} 个文件, 跳过 {total_skipped} 个")


if __name__ == "__main__":
    main()
