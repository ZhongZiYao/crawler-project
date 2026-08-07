# -*- coding: utf-8 -*-
"""
从「产品说明书&发行公告」中二次筛选临时公告并移动到「临时报告」目录。

部分费率调整/业绩比较基准调整/新设份额的公告未发布在临时公告栏目，
而是被归入了产品说明书&发行公告。本脚本按与 classify.py 完全一致的
关键字匹配规则，将这些文件移动到临时报告目录下。

用法:
    python reclassify_temp.py              # dry-run 预览
    python reclassify_temp.py --execute    # 实际移动
    python reclassify_temp.py --only A01   # 只处理指定机构（逗号分隔）
    python reclassify_temp.py --verbose    # 显示每个文件的处理详情
"""

import argparse
import os
import re
import shutil
import sys
import time
import warnings
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# Windows GBK 控制台兼容
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 抑制 pdfplumber / pdfminer 的大量警告信息
warnings.filterwarnings("ignore")
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 直接复用 classify.py 中的分类逻辑
from classify import (
    OUTPUT_ROOT,
    CAT_TEMP,
    CAT_PRODUCT,
    TEMP_KEYWORDS,
    NEW_SHARE_COOCCUR,
    _match_keywords,
    sanitize_folder,
)

# 模块级线程池（避免每个文件创建/销毁线程池的开销）
_text_pool = ThreadPoolExecutor(max_workers=1)

def _extract_text_fast(filepath: str, max_pages: int = 3,
                        max_chars: int = 8000) -> str:
    """快速提取 PDF 前几页文本（用 PyMuPDF/fitz，比 pdfplumber 快很多）。"""
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
        elif ext == ".txt":
            with open(filepath, encoding="utf-8", errors="replace") as f:
                return f.read(max_chars)
        else:
            return ""
    except Exception:
        return ""


def _extract_text_with_timeout(filepath: str, timeout: float = 3.0) -> str:
    """在子线程中提取文本，超时则返回空字符串。"""
    future = _text_pool.submit(_extract_text_fast, filepath)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        return ""
    except Exception:
        return ""


PRODUCT_DIR = os.path.join(OUTPUT_ROOT, "产品说明书&发行公告")
TEMP_DIR = os.path.join(OUTPUT_ROOT, "临时报告")

# 全文提取超时秒数
TEXT_EXTRACT_TIMEOUT = 3.0


# ============================================================
# 标题提取
# ============================================================

def extract_title_from_filename(filename: str) -> str:
    """
    从已分类的文件名中提取公告标题（用于关键字匹配）。
    文件名格式通常为: 机构名_日期_类型_标题.pdf
    或: 机构名_标题_类型_日期.pdf
    直接返回整个文件名（去掉扩展名）作为匹配文本即可。
    """
    name = os.path.splitext(filename)[0]
    return name


# ============================================================
# 临时公告分类（与 classify.py 完全一致）
# ============================================================

def classify_as_temp(title: str, filepath: str):
    """
    判断文件是否属于临时公告的 4 类之一。
    与 classify.py 的 classify_temp_announcement 完全一致：
    先标题匹配，标题无命中则全文匹配。

    返回: (category, match_source)
      category: 费率调整及费率优惠 / 业绩比较基准调整 / 新设份额 / 特殊案例 / ""
      match_source: "title" / "fulltext" / "timeout"
    """
    # 第一轮：标题匹配
    title_hits = _match_keywords(title or "")

    if any(title_hits.values()):
        hits = title_hits
        match_source = "title"
    else:
        # 第二轮：全文匹配（带超时保护）
        full_text = _extract_text_with_timeout(filepath, timeout=TEXT_EXTRACT_TIMEOUT)
        if full_text:
            full_hits = _match_keywords(full_text)
            hits = full_hits
            match_source = "fulltext"
        else:
            hits = title_hits  # 全 0
            match_source = "timeout"

    matched = [cat for cat, count in hits.items() if count > 0]

    if len(matched) >= 2:
        return "特殊案例", match_source
    elif len(matched) == 1:
        return matched[0], match_source
    else:
        return "", match_source


# ============================================================
# 目标路径构建
# ============================================================

def build_temp_dest_path(org_label: str, sub_category: str) -> str:
    """构建临时报告目标路径: 临时报告/{机构}/{子类}/"""
    return os.path.join(
        TEMP_DIR,
        sanitize_folder(org_label),
        sanitize_folder(sub_category),
    )


def safe_move(src: str, dest_dir: str, filename: str) -> str:
    """安全移动文件，处理目标已存在的情况。返回最终路径。"""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        idx = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{base}_{idx}{ext}")
            idx += 1
    shutil.move(src, dest)
    return dest


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="从产品说明书&发行公告中筛选临时公告")
    parser.add_argument("--execute", action="store_true", help="实际移动文件（默认 dry-run）")
    parser.add_argument("--only", type=str, default="", help="只处理指定机构（逗号分隔）")
    parser.add_argument("--verbose", action="store_true", help="显示每个文件的详情")
    args = parser.parse_args()

    only_set = set()
    if args.only:
        only_set = {x.strip() for x in args.only.split(",") if x.strip()}

    mode_str = "实际移动" if args.execute else "dry-run（不移动）"
    print(f"源目录: {PRODUCT_DIR}")
    print(f"目标目录: {TEMP_DIR}")
    print(f"模式: {mode_str}")
    print(f"全文提取超时: {TEXT_EXTRACT_TIMEOUT}s/文件")
    print()

    if not os.path.isdir(PRODUCT_DIR):
        print("[X] 产品说明书&发行公告目录不存在")
        return

    # 统计
    total_scanned = 0
    total_moved = 0
    total_kept = 0
    stats_by_org = {}
    stats_by_category = defaultdict(int)
    match_title = 0
    match_fulltext = 0
    match_timeout = 0
    t_start = time.time()

    institutions = sorted(os.listdir(PRODUCT_DIR))

    for inst_name in institutions:
        inst_dir = os.path.join(PRODUCT_DIR, inst_name)
        if not os.path.isdir(inst_dir):
            continue
        if only_set and inst_name not in only_set:
            # 也支持按机构名匹配（如"工银理财"匹配"A01工银理财"）
            if not any(o in inst_name for o in only_set):
                continue

        # 收集该机构下所有文件
        files = []
        for root, dirs, filenames in os.walk(inst_dir):
            for fn in filenames:
                if fn.startswith("."):
                    continue
                abs_path = os.path.join(root, fn)
                files.append(abs_path)

        inst_scanned = len(files)
        inst_moved = 0
        inst_kept = 0
        inst_categories = defaultdict(int)

        print(f"[{inst_name}] 扫描 {inst_scanned} 个文件")

        for i, filepath in enumerate(files):
            filename = os.path.basename(filepath)
            total_scanned += 1

            # 进度输出（写到 stderr 并立即 flush）
            if (i + 1) % 200 == 0 or (i + 1) == inst_scanned:
                elapsed = time.time() - t_start
                sys.stderr.write(
                    f"  ... 进度 {i+1}/{inst_scanned}  "
                    f"({elapsed:.0f}s)\n")
                sys.stderr.flush()

            # 提取标题
            title = extract_title_from_filename(filename)

            # 分类
            category, match_source = classify_as_temp(title, filepath)

            # 统计匹配来源
            if match_source == "title":
                match_title += 1
            elif match_source == "fulltext":
                match_fulltext += 1
            else:
                match_timeout += 1

            if not category:
                # 不属于临时公告 4 类，保留
                total_kept += 1
                inst_kept += 1
                if args.verbose:
                    print(f"    [保留] {filename}")
                continue

            # 属于临时公告，构建目标路径
            dest_dir = build_temp_dest_path(inst_name, category)

            if args.execute:
                actual_dest = safe_move(filepath, dest_dir, filename)
                if args.verbose:
                    print(f"    [移动→{category}] {filename}")
            else:
                if args.verbose:
                    print(f"    [将移动→{category}] {filename}")

            total_moved += 1
            inst_moved += 1
            inst_categories[category] += 1
            stats_by_category[category] += 1

        # 机构统计
        stats_by_org[inst_name] = {
            "scanned": inst_scanned,
            "moved": inst_moved,
            "kept": inst_kept,
            "categories": dict(inst_categories),
        }

        cat_str = ", ".join(f"{k}:{v}" for k, v in sorted(inst_categories.items()))
        print(f"  结果: 移动 {inst_moved}, 保留 {inst_kept}")
        if cat_str:
            print(f"  分类: {cat_str}")
        print()

    # 汇总
    elapsed = time.time() - t_start
    print("=" * 60)
    print(f"总计: 扫描 {total_scanned}, 移动 {total_moved}, 保留 {total_kept}")
    print(f"耗时: {elapsed:.1f}s")
    print(f"匹配来源: 标题命中 {match_title}, 全文命中 {match_fulltext}, "
          f"超时跳过 {match_timeout}")
    if stats_by_category:
        print("各类别:")
        for cat, count in sorted(stats_by_category.items()):
            print(f"  {cat}: {count}")
    if not args.execute:
        print("\n（dry-run 模式，未实际移动。加 --execute 执行移动）")


if __name__ == "__main__":
    main()
