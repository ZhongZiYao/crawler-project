
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
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ╔══════════════════════════════════════════════════════╗
# ║                    用户配置区                        ║
# ╚══════════════════════════════════════════════════════╝

TARGETS = [
    # (栏目名, URL, 起始序号, 终止序号)
    # 以下栏目已按需求关闭，如需恢复去掉行首 # 即可
    ("发行公告", "https://www.bqd-wm.com/eportal/ui?pageId=71b994290ccb49cbbc6bdcfda2ca5d4c", 1, None),
    # ("到期公告", "https://www.bqd-wm.com/eportal/ui?pageId=99cf7abe92124bfaa635ee3ea5f43787", 1, 3),
    # ("净值公告", "https://www.bqd-wm.com/eportal/ui?pageId=6f40cd057ed648ceaf2a1f523db30298", 1, 3),
    # ("定期公告", "https://www.bqd-wm.com/eportal/ui?pageId=c4474374201c475ab1b54121a62f9e23", 1, 3),
    ("其他公告", "https://www.bqd-wm.com/eportal/ui?pageId=30b9f1566b9541ce8d14e9456d6f505d", 1, None),
    # 发售公告：列表为详情页链接（/qylc/xxpl/fsgg/...index.html），需进详情页再取 PDF 附件
    # ("发售公告", "https://www.bqd-wm.com/eportal/ui?pageId=d744d0fa9ee14e6e837c15f191fb0aaa", 1, None),
]

# 发售公告栏目专用解析标记（列表结构与 viewer.html 直链栏目不同）
DETAIL_PAGE_CATEGORIES = {"发售公告"}
# 发售公告列表页链接特征
FSGG_LIST_HREF_MARK = "/qylc/xxpl/fsgg/"

# 锚定到脚本自身目录，避免从其他目录启动时文件散落在根目录
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR  = os.path.join(SCRIPT_DIR, "青银理财pdf")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_DIR, "checkpoint.json")
REQUEST_DELAY = 0.5
TIMEOUT       = 30

INSTITUTION   = "青银理财"

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
    START_DATE = PROJECT_START_DATE.get("c07", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("c07", False)
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
# START_DATE = "2026-03-27"
# END_DATE   = "2026-06-30"

CSV_PATH      = os.path.join(SCRIPT_DIR, "青银理财_下载日志.csv")

BASE          = "https://www.bqd-wm.com"
VIEWER_PREFIX = "/eportal/uiFramework/js/pdfjs/web/viewer.html?file="

CSV_HEADER = [
    "机构名称",
    "公告名称",
    "公告类型",
    "披露日期",
    "下载时间",
    "状态",
    "来源链接",
    "保存路径",
    "unique_key"
]

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def sanitize(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": BASE,
    })
    return s

def extract_date_prefix(title: str) -> str:
    """从标题前缀提取 'YYYY.MM.DD' 日期并标准化为 YYYY-MM-DD"""
    date_match = re.match(r"(\d{4}\.\d{2}\.\d{2})", title or "")
    return date_match.group(1).replace(".", "-") if date_match else ""

def parse_items(soup: BeautifulSoup):
    """
    Returns: List of (title, pdf_url, pub_date)
    title: 公告标题（去掉末尾.pdf）
    pdf_url: PDF直链（viewer.html 解包后）
    pub_date: 从标题前缀提取(YYYY-MM-DD)
    """
    items = []

    for row in soup.find_all("tr"):
        a = row.find("a", href=True)
        if not a or VIEWER_PREFIX not in a['href']:
            continue

        pdf_path = a['href'].split(VIEWER_PREFIX, 1)[1]
        pdf_url = BASE + pdf_path
        raw_title = a.get_text(strip=True).removesuffix("详情").strip()
        # 去除末尾.pdf
        title = raw_title[:-4] if raw_title.lower().endswith('.pdf') else raw_title
        pub_date = extract_date_prefix(title)

        items.append((title, pdf_url, pub_date))

    # 若页面没tr结构，回退为原始方式
    if not items:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if VIEWER_PREFIX not in href:
                continue
            pdf_path = href.split(VIEWER_PREFIX, 1)[1]
            pdf_url = BASE + pdf_path
            raw_text = a.get_text(strip=True).removesuffix("详情").strip()
            # 去除末尾.pdf
            title = raw_text[:-4] if raw_text.lower().endswith('.pdf') else raw_text
            pub_date = extract_date_prefix(title)

            items.append((title, pdf_url, pub_date))
    return items

def parse_fsgg_detail_links(soup: BeautifulSoup):
    """
    发售公告列表页解析：提取详情页链接
    Returns: List of (title, detail_url, pub_date)
    列表页 <tr> 行内链接形如 /qylc/xxpl/fsgg/xxx/index.html，
    标题形如 'YYYY.MM.DD标题'，需进详情页再找 PDF 附件。
    """
    detail_links = []

    def _collect(anchor):
        href = anchor.get("href", "")
        text = anchor.get_text(strip=True).removesuffix("详情").strip()
        title = text[:-4] if text.lower().endswith(".pdf") else text
        if FSGG_LIST_HREF_MARK in href and href.endswith("index.html") and title:
            pub_date = extract_date_prefix(text)
            detail_links.append((title, BASE + href, pub_date))

    for row in soup.find_all("tr"):
        a = row.find("a", href=True)
        if not a:
            continue
        _collect(a)
    # 若页面没tr结构，回退为全链接扫描
    if not detail_links:
        for a in soup.find_all("a", href=True):
            _collect(a)
    return detail_links

def extract_fsgg_pdf_links(soup: BeautifulSoup):
    """发售公告详情页解析：提取 PDF 附件相对路径列表"""
    pdf_links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if FSGG_LIST_HREF_MARK in href and href.endswith(".pdf"):
            pdf_links.append(href)
    return pdf_links

def get_unique_pdf_path(cat_dir, pub_date, title):
    """构建保存路径；同名文件自动追加 _1/_2 后缀避免覆盖"""
    safe_title = sanitize(title)
    fn_date = pub_date if re.match(r"\d{4}-\d{2}-\d{2}", pub_date) else "未知日期"
    filename = f"{INSTITUTION}_{fn_date}_{safe_title}.pdf"
    fpath = os.path.join(cat_dir, filename)
    seq = 1
    while os.path.exists(fpath):
        filename = f"{INSTITUTION}_{fn_date}_{safe_title}_{seq}.pdf"
        fpath = os.path.join(cat_dir, filename)
        seq += 1
    return fpath

def load_downloaded_urls():
    """从主日志 CSV 读取已成功下载的 URL（unique_key），用于跨运行去重"""
    urls = set()
    if os.path.exists(CSV_PATH):
        try:
            with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("状态") or "").strip().upper() == "SUCCEED":
                        u = str(row.get("unique_key") or "").strip()
                        if u:
                            urls.add(u)
        except Exception:
            pass
    return urls

def is_in_date_range(disclose_date: str):
    """返回 (是否在区间内, 是否过早可早停)"""
    d = (disclose_date or "").strip()
    if not d:
        return True, False
    upper = (END_DATE.strip() if END_DATE and END_DATE.strip()
             else datetime.now().strftime("%Y-%m-%d"))
    if START_DATE and d < START_DATE:
        return False, True
    if d > upper:
        return False, False
    return True, False

def write_log_csv(row: list):
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(CSV_HEADER)
        writer.writerow(row)

def download_file(session, url: str, save_path: str, title, cat_name, pub_date):
    """
    返回状态 'SUCCEED', 'FAILED', 并写日志
    """
    result = ""
    status = ""
    if os.path.exists(save_path):
        result = "SUCCEED"
        status = "SUCCEED"
    else:
        try:
            r = session.get(url, timeout=TIMEOUT, stream=True)
            r.raise_for_status()
            if "text/html" in r.headers.get("Content-Type", ""):
                print(f"        ⚠️  返回HTML，跳过")
                result = "FAILED"
            else:
                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                result = "SUCCEED"
                kb = os.path.getsize(save_path) / 1024
                print(f"        ✅ {os.path.basename(save_path)} ({kb:.1f} KB)")
        except Exception as e:
            print(f"        ❌ {e}")
            result = "FAILED"
    status = "SUCCEED" if result == "SUCCEED" else "FAILED"
    download_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # unique_key 改为 pdf_url，即 url
    unique_key = url
    log_row = [
        INSTITUTION,
        title,
        cat_name,
        pub_date,
        download_time,
        status,
        url,
        save_path,
        unique_key
    ]
    write_log_csv(log_row)
    return status

def save_checkpoint_state(checkpoint, target_index, item_index, done):
    """保存断点续传进度（checkpoint 为 None 时静默跳过）"""
    if checkpoint is None:
        return
    checkpoint.setdefault("target_items", {})[str(target_index)] = {"item_index": item_index, "done": done}
    checkpoint["current_target"] = target_index
    checkpoint["updated_at"] = now_str()
    save_checkpoint(CHECKPOINT_FILE, checkpoint)

def scrape_category(session, name: str, list_url: str, idx_start: int, idx_end, save_dir: str, target_index=0, checkpoint: dict = None):
    cat_dir = os.path.join(save_dir, name)
    ensure_dir(cat_dir)
    # 断点续传：检查是否已完成
    if checkpoint is not None and target_index in checkpoint.get("completed_targets", []):
        print(f"  [断点续传] target {target_index} 已完成，跳过")
        return

    print(f"\n{'='*60}")
    print(f"  【{name}】范围: 第{idx_start}条 ~ 第{idx_end or '末条'}")
    print(f"{'='*60}")

    if name in DETAIL_PAGE_CATEGORIES:
        scrape_fsgg_category(session, name, list_url, idx_start, idx_end, cat_dir, target_index, checkpoint)
        return

    resp = session.get(list_url, timeout=TIMEOUT)
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup  = BeautifulSoup(resp.text, "html.parser")
    items = parse_items(soup)
    total = len(items)
    print(f"  共找到 {total} 条")

    # 切片（转为0-based索引）
    start = idx_start - 1                        # 包含
    end   = idx_end if idx_end is not None else total  # 不包含
    end   = min(end, total)
    subset = items[start:end]

    if not subset:
        print(f"  ⚠️  序号范围 [{idx_start}, {idx_end}] 内无数据，跳过")
        # 断点续传：空范围也标记 done
        save_checkpoint_state(checkpoint, target_index, 0, True)
        if checkpoint is not None:
            completed = checkpoint.setdefault("completed_targets", [])
            if target_index not in completed:
                completed.append(target_index)
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)
        return

    print(f"  实际下载: 第{start+1}条 ~ 第{start+len(subset)}条，共 {len(subset)} 条")

    n_dl = n_skip = n_fail = 0
    # 去重：从主日志读取已成功下载的 URL
    downloaded_urls = load_downloaded_urls()
    # 断点续传：从上次的 item_index 继续
    start_item_index = 0
    if checkpoint is not None:
        target_cp = checkpoint.get("target_items", {}).get(str(target_index), {})
        start_item_index = int(target_cp.get("item_index", 0))
        if start_item_index > 0:
            print(f"[断点续传] target {target_index} 从第 {start_item_index + 1}/{len(subset)} 条继续")
    for idx in range(start_item_index, len(subset)):
        i = idx + start + 1
        title, pdf_url, pub_date = subset[idx]
        _in_range, _too_old = is_in_date_range(pub_date)
        if not _in_range:
            n_skip += 1
            tag = "过早(早停)" if _too_old else "过晚"
            print(f"    ⏭️ [日期跳过] 披露日期 {pub_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title[:60]}")
            if _too_old and EARLY_STOP:
                print(f"    ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止处理")
                break
            continue
        print(f"\n    [{i}/{total}] {title[:60]}  日期:{pub_date}")
        ext      = os.path.splitext(pdf_url)[1] or ".pdf"
        # 去重：已成功下载过的 PDF 直接跳过
        if pdf_url in downloaded_urls:
            n_skip += 1
            print(f"    ⏭️ [已下载] 跳过: {title[:60]}")
            time.sleep(REQUEST_DELAY)
            save_checkpoint_state(checkpoint, target_index, idx + 1, False)
            continue
        safe_title = sanitize(title)
        fn_date = pub_date if re.match(r"\d{4}-\d{2}-\d{2}", pub_date) else "未知日期"
        # 修正文件命名顺序
        filename = f"{INSTITUTION}_{fn_date}_{safe_title}{ext}"
        # 文件名冲突自动追加 _1/_2 后缀，避免覆盖
        base_name, ext_split = os.path.splitext(filename)
        candidate = os.path.join(cat_dir, filename)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(cat_dir, f"{base_name}_{counter}{ext_split}")
            counter += 1
        save_path = candidate
        status = download_file(session, pdf_url, save_path, title, name, pub_date)
        if status == "SUCCEED":
            n_dl += 1
            downloaded_urls.add(pdf_url)
        else:
            n_fail += 1
        time.sleep(REQUEST_DELAY)
        # 断点续传：每个item处理完后保存进度
        save_checkpoint_state(checkpoint, target_index, idx + 1, False)
    # 断点续传：target 完成标记 done
    save_checkpoint_state(checkpoint, target_index, len(subset), True)
    if checkpoint is not None:
        completed = checkpoint.setdefault("completed_targets", [])
        if target_index not in completed:
            completed.append(target_index)
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)
    print(f"\n  ✅下载:{n_dl}  跳过:{n_skip}  失败:{n_fail}")
    print(f"  保存在：{os.path.abspath(cat_dir)}")

def scrape_fsgg_category(session, name: str, list_url: str, idx_start: int, idx_end, cat_dir: str, target_index=0, checkpoint: dict = None):
    """
    发售公告专用爬取流程（原 发售公告抓取.py 逻辑合并）：
    列表页解析 <tr> 行内详情页链接（/qylc/xxpl/fsgg/...index.html）→
    日期过滤（与主流程一致的 START_DATE/END_DATE 区间与早停机制）→
    进详情页提取 PDF 附件（可能多个，文件名追加 _1/_2 序号）→
    下载并写主日志 CSV（与其他栏目统一）。
    """
    print("  🔍 正在获取条目列表...")
    resp = session.get(list_url, timeout=TIMEOUT)
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    detail_links = parse_fsgg_detail_links(soup)
    total = len(detail_links)
    print(f"  📋 共找到 {total} 条详情页")

    if not detail_links:
        print(f"  ⚠️  序号范围 [{idx_start}, {idx_end}] 内无数据，跳过")
        save_checkpoint_state(checkpoint, target_index, 0, True)
        if checkpoint is not None:
            completed = checkpoint.setdefault("completed_targets", [])
            if target_index not in completed:
                completed.append(target_index)
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)
        return

    # 切片（转为0-based索引）
    start = idx_start - 1
    end   = idx_end if idx_end is not None else total
    end   = min(end, total)
    subset = detail_links[start:end]

    if not subset:
        print(f"  ⚠️  序号范围 [{idx_start}, {idx_end}] 内无数据，跳过")
        save_checkpoint_state(checkpoint, target_index, 0, True)
        if checkpoint is not None:
            completed = checkpoint.setdefault("completed_targets", [])
            if target_index not in completed:
                completed.append(target_index)
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)
        return

    print(f"  实际处理: 第{start+1}条 ~ 第{start+len(subset)}条，共 {len(subset)} 条")

    n_dl = n_skip = n_fail = n_files = 0
    # 去重：从主日志读取已成功下载的 PDF URL
    downloaded_urls = load_downloaded_urls()
    # 断点续传：从上次的 item_index 继续
    start_item_index = 0
    if checkpoint is not None:
        target_cp = checkpoint.get("target_items", {}).get(str(target_index), {})
        start_item_index = int(target_cp.get("item_index", 0))
        if start_item_index > 0:
            print(f"[断点续传] target {target_index} 从第 {start_item_index + 1}/{len(subset)} 条继续")

    for idx in range(start_item_index, len(subset)):
        i = idx + start + 1
        title, detail_url, pub_date = subset[idx]
        _in_range, _too_old = is_in_date_range(pub_date)
        if not _in_range:
            n_skip += 1
            tag = "过早(早停)" if _too_old else "过晚"
            print(f"    ⏭️ [日期跳过] 披露日期 {pub_date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title[:60]}")
            if _too_old and EARLY_STOP:
                print(f"    ⏹️ [早停] 检测到早于起始日期 {START_DATE} 的条目，停止处理")
                break
            continue

        print(f"\n    [{i}/{total}] {title[:60]}  日期:{pub_date}")

        # 抓取详情页
        try:
            r = session.get(detail_url, timeout=TIMEOUT)
            r.encoding = r.apparent_encoding or "utf-8"
            ds = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print(f"        ❌ 详情页请求失败: {e}")
            write_log_csv([INSTITUTION, title, name, pub_date,
                           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "FAILED", detail_url, "", detail_url])
            n_fail += 1
            time.sleep(REQUEST_DELAY)
            save_checkpoint_state(checkpoint, target_index, idx + 1, False)
            continue

        pdf_links = extract_fsgg_pdf_links(ds)
        if not pdf_links:
            print("        ⚠️  未找到PDF附件")
            write_log_csv([INSTITUTION, title, name, pub_date,
                           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "FAILED", detail_url, "", detail_url])
            n_fail += 1
            time.sleep(REQUEST_DELAY)
            save_checkpoint_state(checkpoint, target_index, idx + 1, False)
            continue

        print(f"        📎 找到 {len(pdf_links)} 个附件")
        got_any = False
        for att_idx, pdf_path in enumerate(pdf_links, 1):
            pdf_url = BASE + pdf_path
            # 去重：已成功下载过的 PDF 直接跳过
            if pdf_url in downloaded_urls:
                n_skip += 1
                print(f"        ⏭️ [已下载] 跳过附件 {att_idx}")
                continue
            save_path = get_unique_pdf_path(cat_dir, pub_date, f"{title}_{att_idx}" if len(pdf_links) > 1 else title)
            status = download_file(session, pdf_url, save_path, title, name, pub_date)
            if status == "SUCCEED":
                n_files += 1
                got_any = True
                downloaded_urls.add(pdf_url)
            else:
                n_fail += 1
            time.sleep(REQUEST_DELAY)
        if got_any:
            n_dl += 1
        # 断点续传：每个item处理完后保存进度
        save_checkpoint_state(checkpoint, target_index, idx + 1, False)

    # 断点续传：target 完成标记 done
    save_checkpoint_state(checkpoint, target_index, len(subset), True)
    if checkpoint is not None:
        completed = checkpoint.setdefault("completed_targets", [])
        if target_index not in completed:
            completed.append(target_index)
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)
    print(f"\n  ✅条目:{n_dl}  跳过:{n_skip}  失败:{n_fail}  共下载文件:{n_files}")
    print(f"  保存在：{os.path.abspath(cat_dir)}")

def crawl():
    ensure_dir(DOWNLOAD_DIR)
    session = make_session()
    # 断点续传：加载 checkpoint
    checkpoint = load_checkpoint(CHECKPOINT_FILE)
    completed_targets = set(checkpoint.get("completed_targets", []))
    # Filter out completed targets
    pending_indices = [i for i in range(len(TARGETS)) if i not in completed_targets]
    try:
        for target_index in pending_indices:
            name, url, idx_start, idx_end = TARGETS[target_index]
            scrape_category(session, name, url, idx_start, idx_end, DOWNLOAD_DIR, target_index, checkpoint)
            time.sleep(REQUEST_DELAY)
    except KeyboardInterrupt:
        print("\n[中断] 收到 Ctrl+C，断点已保存，下次运行将从断点处继续")
        raise
    print(f"\n{'='*60}")
    print(f"全部完成保存目录：{os.path.abspath(DOWNLOAD_DIR)}")
    print(f"{'='*60}")

if __name__ == "__main__":
    crawl()
