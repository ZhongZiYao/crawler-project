
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
import json
import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from typing import List, Tuple
from DrissionPage import ChromiumPage, ChromiumOptions
from queue import Queue

# 用户配置区

PDF_TARGETS = [
    # 以下栏目已按需求关闭，如需恢复去掉行首 # 即可
    # ("公募_产品销售文件", "https://www.nanyinwealth.com/nanyinwealth/xxpl/cpgg--cpxswj/cpxswj/index.html", "gm", 5, 7),
    # ("公募_运作公告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/cpgg--cpxswj/yxgg/index.html", "gm", 5, 7),
    ("公募_发行公告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/cpgg--cpxswj/fxgg/index.html", "gm", 1, 5),
    # ("公募_到期公告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/cpgg--cpxswj/dqgg/index.html", "gm", 1, 2),
    ("公募_临时公告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/cpgg--cpxswj/lsgg/index.html", "gm", 1, 7),
    # ("公募_其他公告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/cpgg--cpxswj/qtgg/index.html", "gm", 1, 2),
    # ("公募_定期报告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/cpgg--cpxswj/dqbg/index.html", "gm", 1, 2),
    # ("公司公告_其他公告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/gsgg/qtgg/index.html", "static_req", 1, 2),
    # ("公司公告_临时公告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/gsgg/lsgg/index.html", "txt", 1, 2),
    # ("销售公告_产品代销合作机构公告", "https://www.nanyinwealth.com/nanyinwealth/xxpl/xsgg/cpdxhzjggg/index.html", "txt", 1, None),
]

# 锚定到脚本自身目录，避免从其他目录启动时文件散落在根目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "downloaded_pdfs")
FAST_MODE = True
MIN_PAGE_WAIT = 1.2
MAX_PAGE_WAIT = 3.0
MIN_REQUEST_DELAY = 0.4
MAX_REQUEST_DELAY = 1.0
MAX_DOWNLOAD_WORKERS = 3
MAX_DETAIL_WORKERS = 2
SESSION_POOL_SIZE = 5
TIMEOUT = 30
RETRY_COUNT = 3
SITE_ROOT = "https://www.nanyinwealth.com"
BROWSER_INIT = "https://www.nanyinwealth.com/nanyinwealth/zcdl/dl/index.html"
DOWNLOAD_API = (
    SITE_ROOT +
    "/eportal/ui?moduleId=5&portal.url=/portlet/public-product-announcement!downloadAttach.portlet&attachmentId={}"
)
DETAIL_API_JS = """
return new Promise((resolve) => {{
    try {{
        let key = aesUtil.genKey();
        let data = {{'articleId': '{article_id}'}};
        let timeStamp = aesUtil.encrypt(new Date().getTime(), key);
        let aesKey = rsaUtil.encrypt(key, localStorage.getItem('serverPublicKey'));
        let encData = aesUtil.encrypt(data, key);
        let sendData = {{data: encData, aesKey: aesKey, timeStamp: timeStamp}};
        $.ajax({{
            type: 'post',
            dataType: 'json',
            url: '/eportal/ui?moduleId=5&portal.url=/portlet/public-product-announcement!queryArticleDetail.portlet',
            contentType: 'application/json;charset=utf-8',
            data: JSON.stringify(sendData),
            async: true,
            success: function(resp) {{
                try {{
                    let dec = aesUtil.decrypt(resp.data.data, key);
                    resolve(JSON.stringify(dec));
                }} catch(e) {{
                    resolve('DECRYPT_ERROR:' + e);
                }}
            }},
            error: function(e) {{
                resolve('AJAX_ERROR:' + JSON.stringify(e.status));
            }}
        }});
    }} catch(e) {{
        resolve('JS_ERROR:' + e);
    }}
}});
"""

INST_NAME = "南银理财"

# ============ 日期区间配置（集中管理，可本地覆盖）===========
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT
    START_DATE = PROJECT_START_DATE.get("c03", "2024-01-01")
    END_DATE   = PROJECT_END_DATE or ""
    EARLY_STOP = EARLY_STOP_BY_PROJECT.get("c03", False)
except Exception:
    START_DATE = "2024-01-01"
    END_DATE   = ""
    EARLY_STOP = False

# 断点续传工具
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

LOG_CSV_PATH = os.path.join(DOWNLOAD_DIR, "南银理财下载日志.csv")
LOG_HEADER = [
    "机构名称", "公告名称", "公告类型", "披露日期",
    "下载时间", "状态", "来源链接", "保存路径", "unique_key"
]
CHECKPOINT_FILE = os.path.join(DOWNLOAD_DIR, "checkpoint.json")

# 全局变量
_browser = None
_download_stats = {"success": 0, "failed": 0, "skipped": 0}
_response_times = []
_lock = threading.Lock()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def sanitize_filename(name):
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip()[:150]


def sanitize_csv_field(val):
    field = re.sub(r'\s+', ' ', str(val)).strip()
    field = field.replace('\n', ' ').replace('\r', ' ')
    field = field.replace('"', "'")
    return field


def build_pdf_filename(inst_name, publish_date, title):
    title_clean = sanitize_filename(title)
    return f"{inst_name}_{publish_date}_{title_clean}.pdf"


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


def log_pdf_download(
        notice_title,
        notice_type,
        publish_date,
        pdf_link,
        save_path,
        status,
):
    if not os.path.exists(os.path.dirname(LOG_CSV_PATH)):
        os.makedirs(os.path.dirname(LOG_CSV_PATH), exist_ok=True)

    with _lock:
        exists = os.path.isfile(LOG_CSV_PATH)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        notice_title_clean = sanitize_csv_field(notice_title)
        notice_type_clean = sanitize_csv_field(notice_type)
        publish_date_clean = sanitize_csv_field(publish_date)
        pdf_link_clean = sanitize_csv_field(pdf_link)
        save_path_clean = sanitize_csv_field(save_path)
        unique_key = pdf_link_clean
        row = [
            INST_NAME, notice_title_clean, notice_type_clean, publish_date_clean,
            now_str, status, pdf_link_clean, save_path_clean, unique_key
        ]
        with open(LOG_CSV_PATH, "a", newline='', encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(LOG_HEADER)
            writer.writerow(row)


def update_stats(status):
    with _lock:
        _download_stats[status] += 1


def record_response_time(response_time):
    with _lock:
        _response_times.append(response_time)
        if len(_response_times) > 100:
            _response_times.pop(0)


def get_dynamic_delay():
    if not _response_times:
        return MIN_REQUEST_DELAY

    avg_time = sum(_response_times) / len(_response_times)
    if avg_time < 1.0:
        return MIN_REQUEST_DELAY
    elif avg_time > 3.0:
        return MAX_REQUEST_DELAY
    else:
        ratio = (avg_time - 1.0) / 2.0
        return MIN_REQUEST_DELAY + (MAX_REQUEST_DELAY - MIN_REQUEST_DELAY) * ratio


def get_dynamic_page_wait():
    if not _response_times:
        return MIN_PAGE_WAIT

    avg_time = sum(_response_times) / len(_response_times)
    if avg_time < 1.0:
        return MIN_PAGE_WAIT
    elif avg_time > 3.0:
        return MAX_PAGE_WAIT
    else:
        ratio = (avg_time - 1.0) / 2.0
        return MIN_PAGE_WAIT + (MAX_PAGE_WAIT - MIN_PAGE_WAIT) * ratio


def print_progress():
    with _lock:
        total = sum(_download_stats.values())
        if total > 0:
            print(f"进度: 成功:{_download_stats['success']} 跳过:{_download_stats['skipped']} 失败:{_download_stats['failed']} 总计:{total}")


def to_absolute_url(href):
    if not href:
        return ""
    href = href.strip()
    if href.startswith("https://") or href.startswith("http://"):
        return href
    if href.startswith("/"):
        return SITE_ROOT + href
    return SITE_ROOT + "/" + href


def get_browser():
    global _browser
    if _browser is None:
        print("正在启动浏览器...")
        opt = ChromiumOptions()
        opt.auto_port()
        opt.headless(True)
        opt.set_argument("--no-sandbox")
        opt.set_argument("--disable-dev-shm-usage")
        opt.set_argument("--disable-web-security")
        opt.set_argument("--disable-features=VizDisplayCompositor")
        opt.set_argument("--disable-extensions")
        opt.set_argument("--disable-plugins")
        opt.set_argument("--disable-images")
        opt.set_argument("--disable-javascript-harmony-shipping")
        opt.set_argument("--memory-pressure-off")
        opt.set_argument("--max_old_space_size=4096")
        for _dp_attempt in range(3):
            try:
                _browser = ChromiumPage(addr_or_opts=opt)
                break
            except Exception as _dp_err:
                if _dp_attempt < 2:
                    import subprocess as _sp
                    _sp.run(['taskkill', '/F', '/IM', 'chrome.exe', '/T'], capture_output=True, timeout=10)
                    _sp.run(['taskkill', '/F', '/IM', 'chromium.exe', '/T'], capture_output=True, timeout=10)
                    import time as _t
                    _t.sleep(3)
                else:
                    raise
        try:
            _browser.set.window.mini()
        except Exception:
            pass
        _browser.get(BROWSER_INIT)
        time.sleep(3)
        print("浏览器已就绪")
    return _browser


def close_browser():
    global _browser
    if _browser is not None:
        _browser.quit()
        _browser = None


def get_browser_cookies():
    print("获取访问凭证...")
    opt = ChromiumOptions()
    opt.auto_port()
    opt.headless(True)
    opt.set_argument("--disable-images")
    for _dp_attempt in range(3):
        try:
            browser = ChromiumPage(addr_or_opts=opt)
            break
        except Exception as _dp_err:
            if _dp_attempt < 2:
                import subprocess as _sp
                _sp.run(['taskkill', '/F', '/IM', 'chrome.exe', '/T'], capture_output=True, timeout=10)
                _sp.run(['taskkill', '/F', '/IM', 'chromium.exe', '/T'], capture_output=True, timeout=10)
                import time as _t
                _t.sleep(3)
            else:
                raise
    try:
        browser.set.window.mini()
    except Exception:
        pass
    try:
        browser.get(BROWSER_INIT)
        time.sleep(3)
        cookies = {c["name"]: c["value"] for c in browser.cookies()}
        print(f"访问凭证获取成功，数量: {len(cookies)}\n")
    finally:
        browser.quit()
    return cookies


def make_session_pool(cookies, pool_size=SESSION_POOL_SIZE):
    sessions = []
    for _ in range(pool_size):
        session = requests.Session()
        session.cookies.update(cookies)
        session.headers.update({
            "User-Agent":
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36",
            "Referer": SITE_ROOT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        sessions.append(session)
    return sessions


def get_first_item_href(browser):
    items = browser.eles("css:ul.ul-title li a")
    if items:
        return items[0].attr("href") or ""
    return ""


def extract_items_from_browser(browser):
    results = []
    lis = browser.eles("css:ul.ul-title li")
    for li in lis:
        a = li.ele("css:a", timeout=0)
        if not a:
            continue
        href = (a.attr("href") or "").strip()
        title = (a.attr("title") or a.text or "").strip()
        if not href or not title:
            continue
        date = ""
        for sp in li.eles("css:span"):
            txt = sp.text.strip()
            if re.match(r'\d{4}-\d{2}-\d{2}', txt):
                date = txt
                break
        results.append((title, to_absolute_url(href), date))
    return results


def get_total_pages_from_browser(browser):
    el = browser.ele("css:span.total-pages", timeout=2)
    if el:
        txt = el.text.strip()
        if txt.isdigit():
            return int(txt)
    html = browser.ele("css:body").text
    m = re.search(r'共\s*(\d+)\s*页', html)
    if m:
        return int(m.group(1))
    return 1


def wait_for_items(browser, timeout=None):
    dynamic_timeout = timeout or get_dynamic_page_wait()
    deadline = time.time() + dynamic_timeout
    while time.time() < deadline:
        items = browser.eles("css:ul.ul-title li a")
        if items and len(items) > 0:
            time.sleep(0.3)
            return True
        time.sleep(0.2)
    return False


def load_url_with_retry(url):
    browser = get_browser()
    for attempt in range(1, RETRY_COUNT + 1):
        start_time = time.time()
        browser.get(url)
        ok = wait_for_items(browser)
        response_time = time.time() - start_time
        record_response_time(response_time)
        if ok:
            if attempt > 1:
                print(f"第{attempt}次加载成功 ({response_time:.1f}s)")
            return True
        print(f"列表为空，第{attempt}/{RETRY_COUNT}次，刷新会话...")
        browser.get(BROWSER_INIT)
        time.sleep(2)
    print(f"重试{RETRY_COUNT}次后仍为空")
    return False


def fetch_first_list_page(url):
    browser = get_browser()
    ok = load_url_with_retry(url)
    if not ok:
        return [], "", 1
    first_href = get_first_item_href(browser)
    items = extract_items_from_browser(browser)
    total_pages = get_total_pages_from_browser(browser)
    return items, first_href, total_pages


def get_page_signature(browser):
    try:
        items = browser.eles("css:ul.ul-title li a")[:3]
        signature = []
        for item in items:
            href = (item.attr("href") or "").strip()
            title = (item.attr("title") or item.text or "").strip()
            signature.append(f"{href}|{title}")
        return "|".join(signature)
    except:
        return ""


def get_current_page_info(browser):
    current_page = 1
    visible_pages = []

    try:
        page_btns = browser.eles("css:.page-btn")
        for btn in page_btns:
            data_page = btn.attr("data-page") or ""
            text = btn.text.strip()

            if "active" in btn.attr("class"):
                if data_page.isdigit():
                    current_page = int(data_page)
                elif text.isdigit():
                    current_page = int(text)
            if data_page.isdigit():
                visible_pages.append(int(data_page))
            elif text.isdigit():
                visible_pages.append(int(text))

    except Exception:
        pass

    visible_pages.sort()
    return current_page, visible_pages


def navigate_to_page_smart(target_page, max_retries=3):
    """翻页到目标页。note: 这个站点 page-btn 数字按钮 click 不可靠（不会触发翻页），
    但 next/prev 按钮可靠，所以统一用 next/prev 一步步翻。"""
    browser = get_browser()
    for retry in range(max_retries):
        try:
            current_page, _ = get_current_page_info(browser)
            if current_page == target_page:
                return True
            steps = target_page - current_page
            for _ in range(abs(steps)):
                if steps > 0:
                    if not click_next_page():
                        break
                else:
                    if not click_prev_page():
                        break
                time.sleep(get_dynamic_page_wait())
            new_cp, _ = get_current_page_info(browser)
            if new_cp == target_page:
                return True
        except Exception as e:
            pass
        time.sleep(0.8 + retry * 0.3)
    return False


def click_next_page():
    browser = get_browser()
    try:
        page_btns = browser.eles("css:.page-btn")
        for btn in page_btns:
            data_page = btn.attr("data-page") or ""
            text = btn.text.strip()
            if data_page == "next" or text in ["＞", ">", "»", "下一页"]:
                btn.click()
                return True
        return False
    except Exception:
        return False


def click_prev_page():
    browser = get_browser()
    try:
        page_btns = browser.eles("css:.page-btn")
        for btn in page_btns:
            data_page = btn.attr("data-page") or ""
            text = btn.text.strip()
            if data_page == "prev" or text in ["＜", "<", "«", "上一页"]:
                btn.click()
                return True
        return False
    except Exception:
        return False


def fetch_list_page_smart(target_page, prev_signature=""):
    if not navigate_to_page_smart(target_page):
        return [], ""
    browser = get_browser()
    # AJAX 列表可能慢，循环等待列表真正刷新（最多 6 秒）
    current_signature = ""
    for _ in range(15):
        time.sleep(0.4)
        current_signature = get_page_signature(browser)
        if current_signature and current_signature != prev_signature:
            items = extract_items_from_browser(browser)
            if len(items) > 0:
                return items, current_signature
    return [], ""


def extract_article_id(detail_url):
    m = re.search(r'[?&]Id=([a-zA-Z0-9]+)', detail_url)
    return m.group(1) if m else None


def extract_links_from_content(content_html, title):
    if not content_html:
        return []
    soup = BeautifulSoup(content_html, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        link_text = (a.get_text(strip=True).replace(".pdf", "").strip()) or title
        if not href:
            continue
        m = re.search(r'attachmentId=([a-zA-Z0-9]+)', href)
        if m:
            results.append((sanitize_filename(link_text) + ".pdf", DOWNLOAD_API.format(m.group(1))))
            continue
        if href.lower().endswith(".pdf"):
            results.append((sanitize_filename(link_text) + ".pdf", to_absolute_url(href)))
    seen, deduped = set(), []
    for fname, furl in results:
        if furl not in seen:
            seen.add(furl)
            deduped.append((fname, furl))
    return deduped


def get_gm_attachments_fast(article_id, detail_url):
    browser = get_browser()
    js_code = DETAIL_API_JS.format(article_id=article_id)
    try:
        result = browser.run_js(js_code, timeout=10)
    except Exception:
        return []
    result_str = str(result) if result else ""
    if not result_str or any(result_str.startswith(p) for p in
                             ["ERROR", "AJAX_ERROR", "JS_ERROR", "DECRYPT_ERROR"]):
        return []
    try:
        data = json.loads(result_str)
    except Exception:
        return []
    title = data.get("title", "附件")
    results = []
    for att in data.get("attachmentList", []):
        att_id = att.get("id", "")
        original_name = att.get("originalName", "") or att.get("newName", title)
        if not att_id:
            continue
        results.append((sanitize_filename(original_name) + ".pdf", DOWNLOAD_API.format(att_id)))
    if not results:
        content = data.get("content", "") or ""
        if content:
            links = extract_links_from_content(content, title)
            results.extend(links)
    return results


def download_file_fast(session, download_url, save_path, referer="", notice_title="", notice_type="", publish_date=""):
    if os.path.exists(save_path):
        update_stats("skipped")
        log_pdf_download(notice_title, notice_type, publish_date, download_url, save_path, "SUCCEED")
        return "skipped"
    if not download_url.startswith(SITE_ROOT):
        update_stats("failed")
        log_pdf_download(notice_title, notice_type, publish_date, download_url, save_path, "FAILED")
        return "failed"
    headers = {"Referer": SITE_ROOT}
    start_time = time.time()
    try:
        resp = session.get(download_url, headers=headers, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "")
        if "text/html" in ct:
            update_stats("failed")
            log_pdf_download(notice_title, notice_type, publish_date, download_url, save_path, "FAILED")
            return "failed"
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        download_time = time.time() - start_time
        record_response_time(download_time)
        size_kb = os.path.getsize(save_path) / 1024
        if size_kb >= 5:
            update_stats("success")
            log_pdf_download(notice_title, notice_type, publish_date, download_url, save_path, "SUCCEED")
            return "downloaded"
        else:
            update_stats("failed")
            os.remove(save_path)
            log_pdf_download(notice_title, notice_type, publish_date, download_url, save_path, "FAILED")
            return "failed"
    except Exception:
        update_stats("failed")
        log_pdf_download(notice_title, notice_type, publish_date, download_url, save_path, "FAILED")
        return "failed"


def get_detail_info(title, detail_url, date, mode):
    if mode == "gm":
        article_id = extract_article_id(detail_url)
        if not article_id:
            return title, date, []
        return title, date, get_gm_attachments_fast(article_id, detail_url)
    else:
        return title, date, []


def process_detail_batch(session_pool, tasks_queue, cat_dir, notice_type, mode):
    results = {"downloaded": 0, "skipped": 0, "failed": 0}

    detail_tasks = []
    while not tasks_queue.empty():
        try:
            task = tasks_queue.get_nowait()
            detail_tasks.append(task)
        except:
            break
    if not detail_tasks:
        return results

    download_tasks = []
    if mode == "gm":
        with ThreadPoolExecutor(max_workers=MAX_DETAIL_WORKERS) as detail_executor:
            detail_futures = []
            for title, detail_url, date in detail_tasks:
                future = detail_executor.submit(get_detail_info, title, detail_url, date, mode)
                detail_futures.append(future)
            for future in as_completed(detail_futures):
                try:
                    title, date, dl_links = future.result()
                    if dl_links:
                        for filename, dl_url in dl_links:
                            pdf_filename = build_pdf_filename(INST_NAME, date, title)
                            save_path = os.path.join(cat_dir, pdf_filename)
                            session_idx = len(download_tasks) % len(session_pool)
                            download_tasks.append((session_pool[session_idx], dl_url, save_path, "", title, notice_type, date))
                    else:
                        results["failed"] += 1
                except Exception:
                    results["failed"] += 1
    else:
        for title, detail_url, date in detail_tasks:
            results["failed"] += 1

    if download_tasks:
        with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as download_executor:
            download_futures = [
                download_executor.submit(download_file_fast, *task)
                for task in download_tasks
            ]
            for future in as_completed(download_futures):
                try:
                    result = future.result()
                    if result == "downloaded":
                        results["downloaded"] += 1
                    elif result == "skipped":
                        results["skipped"] += 1
                    else:
                        results["failed"] += 1
                except Exception:
                    results["failed"] += 1

    return results

def fetch_static_soup(session, url):
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return BeautifulSoup(resp.text, "html.parser")

def parse_total_pages(soup):
    tag = soup.select_one("span.total-pages")
    if tag and tag.get_text(strip=True).isdigit():
        return int(tag.get_text(strip=True))
    m = re.search(r'共\s*(\d+)\s*页', soup.get_text())
    if m:
        return int(m.group(1))
    return 1

def parse_list_items(soup):
    results = []
    for li in soup.select("ul.ul-title li"):
        a = li.select_one("a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        title = (a.get("title") or a.get_text(strip=True)).strip()
        if not href or not title:
            continue
        date = ""
        for sp in li.select("span"):
            txt = sp.get_text(strip=True)
            if re.match(r'\d{4}-\d{2}-\d{2}', txt):
                date = txt
                break
        results.append((title, to_absolute_url(href), date))
    return results

def build_static_page_url(base_url, page_num):
    if page_num == 1:
        return base_url
    return re.sub(r'index(\d*)\.html$', f'index{page_num}.html', base_url)

def fetch_static_req_all_items(session, start_url, checkpoint=None, target_index=None):
    # 断点续传：从保存的页码开始
    start_page = 1
    if checkpoint is not None and target_index is not None:
        tp = checkpoint.get("target_pages", {}).get(str(target_index))
        if tp and not tp.get("done"):
            start_page = tp.get("page_num", 1)
            if start_page > 1:
                print(f"[断点续传] 从第 {start_page} 页继续")
    first_url = start_url if start_page <= 1 else build_static_page_url(start_url, start_page)
    soup = fetch_static_soup(session, first_url)
    total_pages = parse_total_pages(soup)
    print(f"共 {total_pages} 页（静态请求模式）")
    all_items = parse_list_items(soup)
    print(f"第{start_page}页：{len(all_items)} 条")
    # 断点续传：保存首页进度
    if checkpoint is not None and target_index is not None:
        checkpoint.setdefault("target_pages", {})[str(target_index)] = {"page_num": start_page + 1, "done": False}
        checkpoint["current_target"] = target_index
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)
    for page_num in range(start_page + 1, total_pages + 1):
        page_url = build_static_page_url(start_url, page_num)
        try:
            page_soup = fetch_static_soup(session, page_url)
            items = parse_list_items(page_soup)
            print(f"第{page_num}页：{len(items)} 条")
            all_items.extend(items)
        except Exception as e:
            print(f"[第{page_num}页失败] {e}")
        # 断点续传：每页处理后保存
        if checkpoint is not None and target_index is not None:
            checkpoint.setdefault("target_pages", {})[str(target_index)] = {"page_num": page_num + 1, "done": False}
            checkpoint["current_target"] = target_index
            checkpoint["updated_at"] = now_str()
            save_checkpoint(CHECKPOINT_FILE, checkpoint)
        time.sleep(get_dynamic_delay())
    return all_items

def get_static_attachments(session, detail_url):
    try:
        soup = fetch_static_soup(session, detail_url)
    except Exception:
        return []
    title_tag = soup.select_one(".info_title")
    base_name = title_tag.get_text(strip=True) if title_tag else "附件"
    results = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href.lower().endswith(".pdf"):
            continue
        link_text = (a.get("title") or a.get_text(strip=True)).strip()
        results.append((sanitize_filename(link_text or base_name) + ".pdf", to_absolute_url(href)))
    seen, deduped = set(), []
    for fname, furl in results:
        if furl not in seen:
            seen.add(furl)
            deduped.append((fname, furl))
    return deduped

def get_txt_content(session, detail_url):
    try:
        soup = fetch_static_soup(session, detail_url)
    except Exception as e:
        return "", "", f"[抓取失败] {e}"
    title_el = soup.select_one(".info_title")
    title = title_el.get_text(strip=True) if title_el else ""
    date = ""
    center_el = soup.select_one(".info_center")
    if center_el:
        m = re.search(r'发布时间[：:]\s*(\d{4}年\d{2}月\d{2}日)', center_el.get_text())
        if m:
            date = (m.group(1).replace("年", "-").replace("月", "-").replace("日", ""))
    body = ""
    show_el = soup.select_one(".info_show")
    if show_el:
        for tag in show_el.find_all(["p", "br", "div"]):
            tag.insert_before("\n")
        body = show_el.get_text(separator="")
        body = re.sub(r'\n{3,}', '\n\n', body).strip()
    return title, date, body

def save_txt(title, date, body, detail_url, save_path):
    if os.path.exists(save_path):
        return "skipped"
    content = (f"标题：{title}\n日期：{date}\n来源：{detail_url}\n{'─' * 50}\n\n{body}\n")
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)
        return "saved"
    except Exception:
        return "failed"

def process_static_detail(session, mode, cat_dir, title, detail_url, date, notice_type):
    n_dl = n_sk = n_nl = 0
    if mode == "txt":
        art_title, art_date, body = get_txt_content(session, detail_url)
        final_date = date or art_date
        final_title = art_title or title
        if not body:
            return 0, 0, 1
        prefix = f"{final_date}_" if final_date else ""
        save_path = os.path.join(cat_dir, sanitize_filename(prefix + final_title) + ".txt")
        r = save_txt(final_title, final_date, body, detail_url, save_path)
        if r == "saved":
            n_dl += 1
        elif r == "skipped":
            n_sk += 1
        else:
            n_nl += 1
    elif mode == "static_req":
        dl_links = get_static_attachments(session, detail_url)
        if dl_links:
            for filename, dl_url in dl_links:
                pdf_filename = build_pdf_filename(INST_NAME, date, title)
                save_path = os.path.join(cat_dir, pdf_filename)
                r = download_file_fast(session, dl_url, save_path, referer=detail_url,
                                       notice_title=title, notice_type=notice_type, publish_date=date)
                if r == "downloaded":
                    n_dl += 1
                elif r == "skipped":
                    n_sk += 1
                else:
                    n_nl += 1
                time.sleep(get_dynamic_delay())
        else:
            n_nl += 1
    return n_dl, n_sk, n_nl

def scrape_category_optimized(session_pool, name, start_url, mode, save_dir, page_start=1, page_end=None, checkpoint=None, target_index=None):
    cat_dir = os.path.join(save_dir, name)
    ensure_dir(cat_dir)
    notice_type = name.split("_")[-1]
    total_downloaded = total_skipped = total_no_link = 0

    # 断点续传：检查是否已完成
    if checkpoint is not None and target_index is not None:
        if target_index in checkpoint.get("completed_targets", []):
            print(f"[断点续传] 目标 {target_index} ({name}) 已完成，跳过")
            return 0, 0, 0

    print(f"\n[{name}] 开始爬取")
    print(f"链接: {start_url}")
    print(f"模式: {mode}")

    if mode in ("static_req", "txt"):
        print("使用静态请求模式")
        session = session_pool[0]

        all_items = fetch_static_req_all_items(session, start_url, checkpoint=checkpoint, target_index=target_index)
        print(f"获取到 {len(all_items)} 条数据")

        for title, detail_url, date in all_items:
            _in_range, _too_old = is_in_date_range(date)
            if not _in_range:
                tag = "过早(早停)" if _too_old else "过晚"
                print(f"  ⏭️ [日期跳过] 披露日期 {date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
                total_skipped += 1
                continue
            dl, sk, nl = process_static_detail(session, mode, cat_dir, title, detail_url, date, notice_type)
            total_downloaded += dl
            total_skipped += sk
            total_no_link += nl
            time.sleep(get_dynamic_delay())
    else:
        print("使用浏览器模式")
        items, _, total_pages = fetch_first_list_page(start_url)
        actual_start = max(1, page_start)
        # 断点续传：从保存的页码开始
        if checkpoint is not None and target_index is not None:
            tp = checkpoint.get("target_pages", {}).get(str(target_index))
            if tp and not tp.get("done"):
                saved_page = tp.get("page_num", actual_start)
                actual_start = max(saved_page, actual_start)
                if actual_start > 1:
                    print(f"[断点续传] 从第 {actual_start} 页继续")
        actual_end = min(total_pages, page_end) if page_end else total_pages
        print(f"共 {total_pages} 页，处理第 {actual_start}~{actual_end} 页")
        global _download_stats
        _download_stats = {"success": 0, "failed": 0, "skipped": 0}
        browser = get_browser()
        current_signature = get_page_signature(browser)
        early_stopped = False
        for page_num in range(actual_start, actual_end + 1):
            tasks_queue = Queue()
            print(f"处理第{page_num}页", end="")
            if page_num == actual_start and page_num == 1:
                page_items = items
            else:
                page_items, new_signature = fetch_list_page_smart(page_num, current_signature)
                current_signature = new_signature
                time.sleep(get_dynamic_delay())
            print(f": {len(page_items)}条", end=" -> ")
            if not page_items:
                print("无数据，跳过")
                continue
            for title, detail_url, date in page_items:
                _in_range, _too_old = is_in_date_range(date)
                if not _in_range:
                    tag = "过早(早停)" if _too_old else "过晚"
                    print(f"  ⏭️ [日期跳过] 披露日期 {date} {tag} 区间[{START_DATE}~{END_DATE or '今天'}]: {title}")
                    total_skipped += 1
                    if _too_old and mode == "gm":
                        # gm 模式列表按日期倒序，遇到第一条早于 START_DATE 立即停页
                        print(f"  🛑 [早停] gm 模式倒序列表，遇到早于 {START_DATE} 的公告，本 category 停止翻页")
                        early_stopped = True
                        break
                    continue
                tasks_queue.put((title, detail_url, date))
            if early_stopped:
                break
            batch_results = process_detail_batch(session_pool, tasks_queue, cat_dir, notice_type, mode)
            print(f"下载:{batch_results['downloaded']} 跳过:{batch_results['skipped']} 失败:{batch_results['failed']}")
            total_downloaded += batch_results["downloaded"]
            total_skipped += batch_results["skipped"]
            total_no_link += batch_results["failed"]
            # 断点续传：每页处理后保存
            if checkpoint is not None and target_index is not None:
                checkpoint.setdefault("target_pages", {})[str(target_index)] = {"page_num": page_num + 1, "done": False}
                checkpoint["current_target"] = target_index
                checkpoint["updated_at"] = now_str()
                save_checkpoint(CHECKPOINT_FILE, checkpoint)

    # 断点续传：标记目标完成
    if checkpoint is not None and target_index is not None:
        tp_data = checkpoint.get("target_pages", {}).get(str(target_index), {})
        checkpoint.setdefault("target_pages", {})[str(target_index)] = {"page_num": tp_data.get("page_num", 0), "done": True}
        completed = checkpoint.setdefault("completed_targets", [])
        if target_index not in completed:
            completed.append(target_index)
        checkpoint["updated_at"] = now_str()
        save_checkpoint(CHECKPOINT_FILE, checkpoint)

    print(f"[{name}] 完成 - 下载:{total_downloaded} 跳过:{total_skipped} 失败:{total_no_link}")
    return total_downloaded, total_skipped, total_no_link


def main():
    print("=" * 60)
    print("南银理财批量下载")
    print("=" * 60)
    ensure_dir(DOWNLOAD_DIR)
    # 断点续传：加载 checkpoint
    checkpoint = load_checkpoint(CHECKPOINT_FILE)
    completed_targets = set(checkpoint.get("completed_targets", []))
    if completed_targets:
        print(f"[断点续传] 已完成目标：{sorted(completed_targets)}")
    cookies = get_browser_cookies()
    print(f"创建 {SESSION_POOL_SIZE} 个并发会话...")
    session_pool = make_session_pool(cookies)
    grand_total_downloaded = 0
    grand_total_skipped = 0
    grand_total_failed = 0
    # 过滤已完成的目标
    pending_indices = [i for i in range(len(PDF_TARGETS)) if i not in completed_targets]
    try:
        for idx, target_index in enumerate(pending_indices, 1):
            name, url, mode, pg_start, pg_end = PDF_TARGETS[target_index]
            print(f"\n{'=' * 60}")
            print(f"[{idx}/{len(pending_indices)}] {name} (目标 {target_index})")
            print(f"页码范围：{pg_start}~{pg_end or '末页'}")
            print(f"{'=' * 60}")
            downloaded, skipped, failed = scrape_category_optimized(
                session_pool, name, url, mode, DOWNLOAD_DIR,
                page_start=pg_start, page_end=pg_end,
                checkpoint=checkpoint, target_index=target_index
            )
            grand_total_downloaded += downloaded
            grand_total_skipped += skipped
            grand_total_failed += failed
            print(f"\n总计: 下载:{grand_total_downloaded} 跳过:{grand_total_skipped} 失败:{grand_total_failed}")
            if idx < len(pending_indices):
                time.sleep(get_dynamic_delay() * 2)
    except KeyboardInterrupt:
        print(f"\n[中断] 收到 Ctrl+C，断点已保存，下次运行将从断点处继续")
        raise
    except Exception as e:
        print(f"\n程序异常: {e}")
    finally:
        close_browser()
    print(f"\n{'=' * 60}")
    print("任务完成")
    print(f"保存目录：{os.path.abspath(DOWNLOAD_DIR)}")
    print("最终统计：")
    print(f"成功下载：{grand_total_downloaded} 个文件")
    print(f"跳过重复：{grand_total_skipped} 个文件")
    print(f"下载失败：{grand_total_failed} 个文件")
    print(f"详细日志：{LOG_CSV_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()