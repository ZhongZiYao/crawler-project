
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
import time
import csv
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from typing import Optional

# ╔══════════════════════════════════════════════════════════╗
# ║                      用户配置区                          ║
# ╚══════════════════════════════════════════════════════════╝

# 格式：(栏目名, 列表页URL, 模式, 起始序号, 终止序号)
# 模式：
#   "pdf"  → 列表直接是PDF链接，直接下载
#   "html" → 列表→详情页→附件PDF，下载PDF
#   "txt"  → 列表→详情页→正文TXT + 附件（pdf/docx等），全部保存
# 起始/终止序号：按列表顺序（从上到下）从1开始，None表示不限制
PDF_TARGETS = [
    # 以下栏目已按需求关闭，如需恢复去掉行首 # 即可
    ("产品公告_发行公告",   "https://www.beijingbobwealth.com.cn/xxpl/cpgg/fxgg/index.html",    "pdf",  1, None),
    # ("产品公告_到期公告",   "https://www.beijingbobwealth.com.cn/xxpl/cpgg/daoqgg/index.html",  "pdf",  1, 5),
    # ("产品公告_净值公告",   "https://www.beijingbobwealth.com.cn/xxpl/cpgg/jzgg/index.html",    "pdf",  1, 5),
    # ("产品公告_定期公告",   "https://www.beijingbobwealth.com.cn/xxpl/cpgg/dqgg/index.html",    "pdf",  1, 5),
    ("产品公告_临时公告",   "https://www.beijingbobwealth.com.cn/xxpl/cpgg/lsgg/index.html",    "pdf",  1, None),
    # ("产品公告_分红公告",   "https://www.beijingbobwealth.com.cn/xxpl/cpgg/fhgg/index.html",    "pdf",  1, 5),
    # ("产品公告_重大事项",   "https://www.beijingbobwealth.com.cn/xxpl/cpgg/zdsxgg/index.html",  "pdf",  1, 5),
    # ("公司公告",           "https://www.beijingbobwealth.com.cn/xxpl/gsgg/index.html",          "html", 1, 5),
    # ("其他公告",           "https://www.beijingbobwealth.com.cn/xxpl/qtgg/index.html",          "txt",  1, 5),
]

# 锚定到脚本自身目录，避免从其他目录启动时文件散落在根目录
SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR   = os.path.join(SCRIPT_DIR, "downloaded_files")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_DIR, "checkpoint.json")
REQUEST_DELAY  = 0.5
TIMEOUT        = 30

# ╚══════════════════════════════════════════════════════════╝

ORG_NAME       = "北银理财"

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
    START_DATE = PROJECT_START_DATE.get("c05", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("c05", False)
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
# START_DATE = "2026-04-09"
# END_DATE   = "2026-06-30"

CSV_LOGFILE    = os.path.join(SCRIPT_DIR, "download_log.csv")
CSV_FIELDNAMES = [
    "机构名称", "公告名称", "公告类型", "披露日期",
    "下载时间", "状态", "来源链接", "保存路径", "unique_key"
]

SITE_ROOT = "https://www.beijingbobwealth.com.cn"

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip()[:150]

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": SITE_ROOT,
    })
    return s

def to_abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return SITE_ROOT + href
    return SITE_ROOT + "/" + href

def get_soup(session, url) -> BeautifulSoup:
    resp = session.get(url, timeout=TIMEOUT)
    resp.encoding = resp.apparent_encoding or "utf-8"
    return BeautifulSoup(resp.text, "html.parser")

def get_total_pages(soup: BeautifulSoup) -> int:
    # 尝试找分页按钮最大页码
    max_page = 1
    pagings = soup.select('.Paging a, ul.pagination a, .m-page a')
    page_nums = []
    for el in pagings:
        txt = el.get_text(strip=True)
        if txt.isdigit():
            page_nums.append(int(txt))
    if page_nums:
        max_page = max(page_nums)
        return max_page

    # 或尝试从所有 script 查找 var maxIdx
    for script in soup.find_all('script'):
        m = re.search(r'var\s+maxIdx\s*=\s*(\d+)', script.string or '')
        if m:
            return int(m.group(1))

    # 无法找到则默认1页
    return 1

def get_page_url(base_url: str, page: int) -> str:
    """第1页=index.html，第N页=indexN.html（如 index2.html、index3.html ...）"""
    if page == 1:
        return base_url
    return re.sub(r'index\.html$', f'index{page}.html', base_url)

def parse_list_items(soup: BeautifulSoup):
    items = []
    for a in soup.select("ul.category-content-list li a.info-list-item"):
        title = (a.get("title") or a.select_one(".title").get_text(strip=True))
        href  = a.get("href", "")
        date  = a.select_one(".date")
        date  = date.get_text(strip=True) if date else ""
        if href:
            items.append((title, href, date))
    return items

def collect_all_items(session, start_url: str,
                      idx_start: int, idx_end: Optional[int]):
    soup = get_soup(session, start_url)
    total_pages = get_total_pages(soup)
    all_items = []
    act_start = max(1, idx_start)
    act_end = idx_end if idx_end is not None else float('inf')
    current_page = 1
    while len(all_items) < act_end and current_page <= total_pages:
        page_url = get_page_url(start_url, current_page)
        items = parse_list_items(get_soup(session, page_url))
        print(f"  第{current_page}页：{len(items)} 条")
        all_items.extend(items)
        current_page += 1
        time.sleep(REQUEST_DELAY)
    total = len(all_items)
    # 有可能超出max页，只取实际范围
    act_end = min(total, act_end if act_end is not None else total)
    sliced = all_items[act_start - 1 : act_end]
    print(f"  总条数：{total}，下载范围：第{act_start}~{act_end}条（共{len(sliced)}条）")
    return sliced, act_start, act_end

def log_to_csv(org_name, ann_title, ann_type, pub_date, status,
               src_url, save_path):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ann_title_clean = ann_title.strip()
    # unique_key改为来源链接字段（即src_url）
    unique_key = src_url
    row = {
        "机构名称": org_name,
        "公告名称": ann_title_clean,
        "公告类型": ann_type,
        "披露日期": pub_date,
        "下载时间": now,
        "状态": status,
        "来源链接": src_url,
        "保存路径": save_path,
        "unique_key": unique_key
    }
    write_header = not os.path.exists(CSV_LOGFILE)
    with open(CSV_LOGFILE, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

def download_file_with_log(session, url: str, save_path: str,
                           org_name, ann_title, ann_type, pub_date):
    status = "FAILED"
    try:
        if os.path.exists(save_path):
            status = "SUCCEED"
            log_to_csv(org_name, ann_title, ann_type, pub_date,
                       status, url, save_path)
            return status
        resp = session.get(url, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        if "text/html" in resp.headers.get("Content-Type", ""):
            print(f"        ⚠️  返回HTML，跳过")
            log_to_csv(org_name, ann_title, ann_type, pub_date,
                       status, url, save_path)
            return status
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        kb = os.path.getsize(save_path) / 1024
        print(f"        ✅ {os.path.basename(save_path)} ({kb:.1f}KB)")
        status = "SUCCEED"
    except Exception as e:
        print(f"        ❌ {e}")
    log_to_csv(org_name, ann_title, ann_type, pub_date,
               status, url, save_path)
    return status

def save_txt_with_log(content: str, save_path: str,
                      org_name, ann_title, ann_type, pub_date):
    status = "FAILED"
    try:
        if os.path.exists(save_path):
            status = "SUCCEED"
            log_to_csv(org_name, ann_title, ann_type, pub_date,
                       status, "", save_path)
            return status
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"        ✅ {os.path.basename(save_path)}")
        status = "SUCCEED"
    except Exception as e:
        print(f"        ❌ {e}")
    log_to_csv(org_name, ann_title, ann_type, pub_date,
               status, "", save_path)
    return status

def get_detail_attachments(soup: BeautifulSoup):
    results = []
    for a in soup.select(".main-content-content ul li a"):
        href = (a.get("href") or "").strip()
        name = a.get_text(strip=True)
        if href and ("/upload/" in href):
            ext  = os.path.splitext(href)[1] or ""
            fname = sanitize_filename(name) + (ext if not name.endswith(ext) else "")
            results.append((fname, to_abs(href)))
    return results

def get_detail_text(soup: BeautifulSoup) -> str:
    title_el = soup.select_one("h1")
    title    = title_el.get_text(strip=True) if title_el else ""
    time_el  = soup.select_one(".m-advisory-release-time")
    pub_time = time_el.get_text(strip=True) if time_el else ""
    body_el  = soup.select_one(".m-advisory-news")
    body     = body_el.get_text(separator="\n", strip=True) if body_el else ""
    body     = re.sub(r'\n{3,}', '\n\n', body)
    return f"{title}\n{pub_time}\n{'─'*50}\n\n{body}\n"

def get_file_date(date_str):
    m = re.search(r'\d{4}-\d{2}-\d{2}', date_str)
    if m:
        return m.group()
    return date_str.strip()

def is_in_date_range(disclose_date: str):
    """返回 (是否在区间内, 是否过早可早停)"""
    d = get_file_date(disclose_date)
    if not d:
        return True, False
    upper = (END_DATE.strip() if END_DATE and END_DATE.strip()
             else datetime.now().strftime("%Y-%m-%d"))
    if START_DATE and d < START_DATE:
        return False, True
    if d > upper:
        return False, False
    return True, False

def process_pdf(session, cat_dir, idx, title, href, date, cat_name):
    pub_date = get_file_date(date)
    _in_range, _too_old = is_in_date_range(date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"   ⏭️ [日期跳过] 披露日期 {date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title[:50]}")
        return "too_old" if _too_old else "skip"
    file_title = title.strip()
    filename = f"{ORG_NAME}_{pub_date}_{sanitize_filename(file_title)}.pdf"
    print(f"    [{idx}] {date}  {title[:50]}")
    url = to_abs(href)
    save_path = os.path.join(cat_dir, filename)
    return download_file_with_log(
        session, url, save_path,
        ORG_NAME, file_title, cat_name, pub_date
    )

def process_html(session, cat_dir, idx, title, href, date, cat_name):
    pub_date = get_file_date(date)
    _in_range, _too_old = is_in_date_range(date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"   ⏭️ [日期跳过] 披露日期 {date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title[:50]}")
        return "too_old" if _too_old else "skip", 0
    file_title = title.strip()
    print(f"    [{idx}] {date}  {title[:50]}")
    detail_url = to_abs(href)
    try:
        soup = get_soup(session, detail_url)
    except Exception as e:
        print(f"        ❌ 详情页失败：{e}")
        return "FAILED", 0
    atts = get_detail_attachments(soup)
    if not atts:
        print(f"        ⚠️  无附件")
        return "FAILED", 0
    n = 0
    for attname, url in atts:
        fname = f"{ORG_NAME}_{pub_date}_{sanitize_filename(file_title)}_{attname}"
        save_path = os.path.join(cat_dir, fname)
        status = download_file_with_log(
            session, url, save_path,
            ORG_NAME, file_title, cat_name, pub_date
        )
        if status == "SUCCEED":
            n += 1
        time.sleep(REQUEST_DELAY)
    return "SUCCEED" if n > 0 else "FAILED", n

def process_txt(session, cat_dir, idx, title, href, date, cat_name):
    pub_date = get_file_date(date)
    _in_range, _too_old = is_in_date_range(date)
    if not _in_range:
        tag = "过早(早停)" if _too_old else "过晚"
        print(f"   ⏭️ [日期跳过] 披露日期 {date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title[:50]}")
        return "too_old" if _too_old else "skip"
    file_title = title.strip()
    print(f"    [{idx}] {date}  {title[:50]}")
    detail_url = to_abs(href)
    try:
        soup = get_soup(session, detail_url)
    except Exception as e:
        print(f"        ❌ 详情页失败：{e}")
        return "FAILED"
    text = get_detail_text(soup)
    txt_name = f"{ORG_NAME}_{pub_date}_{sanitize_filename(file_title)}.txt"
    save_txt_with_log(
        text, os.path.join(cat_dir, txt_name),
        ORG_NAME, file_title, cat_name, pub_date
    )
    atts = get_detail_attachments(soup)
    for attname, url in atts:
        fname = f"{ORG_NAME}_{pub_date}_{sanitize_filename(file_title)}_{attname}"
        save_path = os.path.join(cat_dir, fname)
        download_file_with_log(
            session, url, save_path,
            ORG_NAME, file_title, cat_name, pub_date
        )
        time.sleep(REQUEST_DELAY)
    return "SUCCEED"

def scrape_category(session, cat_name, start_url, mode,
                    idx_start, idx_end, save_dir, target_index=0, checkpoint: dict = None):
    cat_dir = os.path.join(save_dir, cat_name)
    ensure_dir(cat_dir)
    # 断点续传：检查是否已完成
    if checkpoint is not None and target_index in checkpoint.get("completed_targets", []):
        print(f"  [断点续传] target {target_index} 已完成，跳过")
        return
    print(f"  收集列表...")
    items, act_start, act_end = collect_all_items(
        session, start_url, idx_start, idx_end)
    n_ok = n_skip = n_fail = 0
    # 断点续传：从上次的 item_index 继续
    start_item_index = 0
    if checkpoint is not None:
        target_cp = checkpoint.get("target_items", {}).get(str(target_index), {})
        start_item_index = int(target_cp.get("item_index", 0))
        if start_item_index > 0:
            print(f"[断点续传] target {target_index} 从第 {start_item_index + 1}/{len(items)} 条继续")
    for idx in range(start_item_index, len(items)):
        i = idx + act_start
        title, href, date = items[idx]
        time.sleep(REQUEST_DELAY)
        _in_range, _too_old = is_in_date_range(date)
        if not _in_range:
            n_skip += 1
            tag = "过早(早停)" if _too_old else "过晚"
            print(f"    ⏭️ [日期跳过] 披露日期 {date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title[:50]}")
            if _too_old and EARLY_STOP:
                print(f"    ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止处理")
                break
            continue
        if mode == "pdf":
            status = process_pdf(session, cat_dir, i, title, href, date, cat_name)
            if status == "SUCCEED":            n_ok += 1
            elif status in ("skip", "too_old"): n_skip += 1
            else:                              n_fail += 1
        elif mode == "html":
            status, n = process_html(session, cat_dir, i, title, href, date, cat_name)
            if status == "SUCCEED":            n_ok += n
            elif status in ("skip", "too_old"): n_skip += 1
            else:                              n_fail += 1
        else:
            status = process_txt(session, cat_dir, i, title, href, date, cat_name)
            if status == "SUCCEED":            n_ok += 1
            elif status in ("skip", "too_old"): n_skip += 1
            else:                              n_fail += 1
        # 断点续传：每个item处理完后保存进度
        if checkpoint is not None:
            checkpoint.setdefault("target_items", {})[str(target_index)] = {"item_index": idx + 1, "done": False}
            checkpoint["current_target"] = target_index
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)
    # 断点续传：target 完成标记 done
    if checkpoint is not None:
        checkpoint.setdefault("target_items", {})[str(target_index)] = {"item_index": len(items), "done": True}
        completed = checkpoint.setdefault("completed_targets", [])
        if target_index not in completed:
            completed.append(target_index)
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)
    print(f"\n  ✅{n_ok}  跳过:{n_skip}  失败:{n_fail}")
    print(f"  保存在：{os.path.abspath(cat_dir)}")

def crawl():
    ensure_dir(DOWNLOAD_DIR)
    session = make_session()
    # 断点续传：加载 checkpoint
    checkpoint = load_checkpoint(CHECKPOINT_FILE)
    completed_targets = set(checkpoint.get("completed_targets", []))
    # Filter out completed targets
    pending_indices = [i for i in range(len(PDF_TARGETS)) if i not in completed_targets]
    try:
        for target_index in pending_indices:
            cat_name, url, mode, s, e = PDF_TARGETS[target_index]
            print(f"\n{'='*60}")
            print(f"  【{cat_name}】模式:{mode}  范围:{s}~{e or '末条'}")
            print(f"{'='*60}")
            scrape_category(session, cat_name, url, mode, s, e, DOWNLOAD_DIR, target_index, checkpoint)
            time.sleep(REQUEST_DELAY)
    except KeyboardInterrupt:
        print("\n[中断] 收到 Ctrl+C，断点已保存，下次运行将从断点处继续")
        raise
    print(f"\n{'='*60}")
    print(f"全部完成，保存目录：{os.path.abspath(DOWNLOAD_DIR)}")
    print(f"{'='*60}")

if __name__ == "__main__":
    crawl()