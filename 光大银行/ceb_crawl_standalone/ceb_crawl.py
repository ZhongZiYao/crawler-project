"""
光大银行产品公告 PDF 下载器 — 真实点击 + 浏览器内下载 (standalone 版)

直接遍历 list 全量, 详情页内再筛"发行公告"or"产品说明书"。
下载走 requests (拿 PDF URL 后直接 GET 下载, 比 expect_download 更稳)。

用法:
  python ceb_crawl.py                  # 跑全量 (从 last_completed_page + 1 续抓)
  python ceb_crawl.py --reset          # 强制从头开始
  python ceb_crawl.py --max-page=10    # 只跑 10 页测试
  set CEB_USE_CHROME_PROFILE=1 & python ceb_crawl.py  # 复用系统 Chrome cookies (需先关 Chrome)

所有路径基于脚本所在目录, 可直接复制到任意位置运行。
"""
import os
import re
import csv
import json
import time
import requests
from typing import Optional
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# 路径全部相对化 (基于脚本所在目录)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SCRIPT_DIR, 'download_files', '产品公告', '发行公告_产品说明书')
LOG_PATH = os.path.join(SCRIPT_DIR, 'ceb_crawl_log.csv')
STATE_DIR = os.path.join(SCRIPT_DIR, 'state')
PROGRESS_PATH = os.path.join(STATE_DIR, 'ceb_progress.json')
PROFILE_DIR = os.path.join(STATE_DIR, 'ceb_chrome_profile')

# 备选: 复用系统 Chrome 的 user-data-dir (用户已访问过 cebwm, cookies 已在)
# 用法: 在 Chrome 里访问一次 cebwm 过反爬, 关 Chrome, 然后 set CEB_USE_CHROME_PROFILE=1 再跑
USE_SYSTEM_CHROME = os.environ.get('CEB_USE_CHROME_PROFILE', '0') == '1'

LIST_URL = 'https://www.cebwm.com/wealth/gywm49/cpgg93/index.html'
DL_BASE = 'https://www.cebwm.com'

# 详情页内要下载的文件名关键词
TARGET_KEYWORDS = ['发行公告', '产品说明书']

# 日期范围配置
START_DATE = '2025-10-01'  # 起始日期 (含)
END_DATE = ''              # 截止日期 (空=今天)


def normalize_date(text: str) -> str:
    """提取 YYYY-MM-DD 格式"""
    m = re.search(r'(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})', str(text or ''))
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ''


def is_too_old(date_text: str) -> bool:
    """日期早于 START_DATE 返回 True (触发早停)"""
    d = normalize_date(date_text)
    if not d or not START_DATE:
        return False
    return d < START_DATE

# 浏览器
BROWSER_CANDIDATES = ['chrome', 'msedge', 'chromium']
HEADLESS = False

DOWNLOAD_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': LIST_URL,
}


def safe_filename(name: str, max_len: int = 100) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = name.strip().rstrip('.')
    if len(name) > max_len:
        name = name[:max_len]
    return name


def launch_browser(p):
    last_err = None
    # 模式 1: 用系统 Chrome 默认 profile (复用用户已过反爬的 cookies)
    if USE_SYSTEM_CHROME:
        chrome_user_data = os.path.join(
            os.environ.get('LOCALAPPDATA', r'C:\Users\ziyao\AppData\Local'),
            'Google', 'Chrome', 'User Data'
        )
        if os.path.exists(chrome_user_data):
            try:
                ctx = p.chromium.launch_persistent_context(
                    chrome_user_data,
                    headless=HEADLESS,
                    channel='chrome',
                    args=['--window-position=-2000,0', '--disable-blink-features=AutomationControlled'],
                    locale='zh-CN',
                    extra_http_headers={'Accept-Language': 'zh-CN,zh;q=0.9'},
                )
                ctx.add_init_script("""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
""")
                print(f'[BROWSER] 启动成功 (系统 Chrome profile): {chrome_user_data}')
                return ctx
            except Exception as e:
                print(f'[BROWSER] 系统 Chrome 启动失败: {e}')
                print('  请先关闭 Chrome 或用独立 profile (取消 CEB_USE_CHROME_PROFILE 环境变量)')

    # 模式 2: 独立 profile
    os.makedirs(PROFILE_DIR, exist_ok=True)
    for cand in BROWSER_CANDIDATES:
        try:
            kwargs = {
                'headless': HEADLESS,
                'args': [
                    '--disable-blink-features=AutomationControlled',
                    '--window-position=-2000,0',
                ],
                'locale': 'zh-CN',
                'extra_http_headers': {'Accept-Language': 'zh-CN,zh;q=0.9'},
            }
            if cand in ('chrome', 'msedge'):
                kwargs['channel'] = cand
            ctx = p.chromium.launch_persistent_context(PROFILE_DIR, **kwargs)
            ctx.add_init_script("""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {}, csi: () => {}, loadTimes: () => {} };
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'mimeTypes', { get: () => [1, 2] });
const _q = window.navigator.permissions.query;
window.navigator.permissions.query = (p) => (
  p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : _q(p)
);
""")
            print(f'[BROWSER] 启动成功: {cand}')
            return ctx
        except Exception as e:
            print(f'[BROWSER] {cand} 启动失败: {e}')
            last_err = e
    raise RuntimeError(f'所有浏览器候选启动失败: {last_err}')


def go_to_list_page(page, page_num: int) -> bool:
    """翻到指定页: page 1 直接 navigate, 之后用 .page_right 持续点下一页"""
    if page_num == 1:
        try:
            page.goto(LIST_URL, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            print(f'  [ERR] goto 首页失败: {e}')
            return False
    else:
        # 持续点 .page_right 直到页号到 page_num
        for _ in range(page_num - 1):
            try:
                next_btn = page.locator('.page_right').first
                if next_btn.count() == 0:
                    print(f'  [WARN] .page_right 不存在, 没法翻到第 {page_num} 页')
                    return False
                if not next_btn.is_enabled():
                    print(f'  [WARN] .page_right 已 disable, 没法翻到第 {page_num} 页')
                    return False
                next_btn.click()
                time.sleep(1.2)
                page.wait_for_selector('li.cpgg_li a.fl', timeout=15000)
            except Exception as e:
                print(f'  [ERR] 翻页失败: {e}')
                return False
    # 等列表加载 (反爬节奏)
    time.sleep(1.2)
    # 第一次失败可能是反爬 challenge, 提示用户手动过
    try:
        page.wait_for_selector('li.cpgg_li a.fl', timeout=15000)
    except PWTimeoutError:
        body = page.evaluate('document.body ? document.body.innerText.slice(0, 200) : ""')
        if not body:
            print(f'\n⚠️  [反爬挑战] 页面被反爬拦截 (body 空)')
            print(f'  请在弹出的浏览器窗口里手动通过反爬验证 (滑动/点选/输入)')
            print(f'  等待列表出现 (最多 60s)...')
            for i in range(60):
                time.sleep(1)
                try:
                    if page.locator('li.cpgg_li').count() > 0:
                        print(f'  ✓ 列表已加载, 继续')
                        return True
                except:
                    pass
            print(f'  ✗ 60s 内列表仍未出现, 跳过本轮')
        return False
    return True


def click_next_page(page) -> bool:
    """点 .page_right 翻下一页 (返回是否成功)"""
    try:
        next_btn = page.locator('.page_right').first
        if not next_btn or not next_btn.is_enabled():
            return False
        next_btn.click()
        time.sleep(1.2)
        page.wait_for_selector('li.cpgg_li a.fl', timeout=15000)
        time.sleep(0.5)
        return True
    except Exception:
        return False


def get_list_items(page) -> list:
    """拿当前 list 页所有 li 元素 + 它们的标题文字 + 日期"""
    items = []
    try:
        lis = page.locator('li.cpgg_li').all()
        for li in lis:
            try:
                a = li.locator('a.fl').first
                title = a.inner_text().strip()
                date = ''
                try:
                    date_el = li.locator('span.li_showdate').first
                    if date_el.count():
                        date = date_el.inner_text().strip()
                except:
                    pass
                if title:
                    items.append((title, date))
            except:
                continue
    except Exception as e:
        print(f'  [ERR] 拿 list items 失败: {e}')
    return items


def click_to_detail(page, ctx, title: str) -> Optional:
    """点 list 里的标题 -> 拿新详情页 tab"""
    try:
        li = page.locator('li.cpgg_li').filter(has_text=title).first
        a = li.locator('a.fl').first
        # 不 scroll, 直接 click; scroll_into_view_if_needed 在 list 第二个 li 后会 timeout (?)
        # 改用 evaluate 直接 click
        try:
            with ctx.expect_page(timeout=8000) as info:
                a.click()
            return info.value
        except PWTimeoutError:
            return 'no_tab'
    except Exception as e:
        return f'click_err: {str(e)[:80]}'


def download_pdfs_in_detail(detail_page, ctx, list_title: str, log_writer, log_file) -> int:
    """详情页里遍历所有 a 链接, 筛'发行公告'或'产品说明书' -> click -> 拿 PDF url -> 下载"""
    # 等详情页加载
    try:
        detail_page.wait_for_load_state('domcontentloaded', timeout=15000)
    except:
        pass
    time.sleep(1.0)

    # 找所有 a 链接, 文字里含目标关键词
    found = []
    try:
        # 等至少有一个 a 加载
        detail_page.wait_for_selector('a', timeout=10000)
        candidates = detail_page.locator('a').all()
        for a in candidates:
            try:
                t = a.inner_text().strip()
                if not t or len(t) < 5 or len(t) > 200:
                    continue
                if any(kw in t for kw in TARGET_KEYWORDS):
                    found.append(t)
            except:
                continue
    except Exception as e:
        print(f'    [ERR] 详情页找 a 失败: {str(e)[:60]}')
        return 0

    if not found:
        return 0

    # 去重 (按文字)
    found = list(dict.fromkeys(found))
    print(f'    [DETAIL] {len(found)} 个待下载')

    downloaded = 0
    for sub_title in found:
        # 找匹配的 a 元素
        try:
            a_candidates = detail_page.locator(f'a:has-text("{sub_title}")').all()
            redbox = None
            for c in a_candidates:
                try:
                    ct = c.inner_text().strip()
                    if ct == sub_title or (sub_title in ct and 5 < len(ct) < 200):
                        redbox = c
                        break
                except:
                    continue
            if not redbox:
                # 模糊找
                redbox = detail_page.locator(f'a:has-text("{sub_title}")').first
        except Exception as e:
            print(f'      [ERR] 找 a 失败: {str(e)[:60]}')
            continue

        # click -> 可能是新 tab 也可能是当前 detail_page 跳 PDF
        pdf_url = None
        original_detail_url = detail_page.url
        try:
            try:
                with ctx.expect_page(timeout=6000) as info:
                    redbox.click()
                pdf_page = info.value
                try:
                    pdf_page.wait_for_load_state('domcontentloaded', timeout=6000)
                except:
                    pass
                pdf_url = pdf_page.url
                # 关 PDF tab
                try: pdf_page.close()
                except: pass
            except PWTimeoutError:
                # 可能是当前 detail_page navigate 到 PDF
                time.sleep(0.5)
                try:
                    detail_page.wait_for_load_state('domcontentloaded', timeout=5000)
                except:
                    pass
                if '.pdf' in detail_page.url.lower():
                    pdf_url = detail_page.url
                    # 详情页已经跳走, 这个标题的其它 a 没法再找
                    # 兜底: 拿完这一个就 break
                    if download_one(pdf_url, sub_title, log_writer, log_file, list_title):
                        downloaded += 1
                    break
        except Exception as e:
            print(f'      [ERR] click 红框失败: {str(e)[:60]}')
            continue

        if pdf_url and '.pdf' in pdf_url.lower():
            if download_one(pdf_url, sub_title, log_writer, log_file, list_title):
                downloaded += 1

    return downloaded


def download_one(pdf_url: str, sub_title: str, log_writer, log_file, list_title: str) -> bool:
    """下载一个 PDF + 写日志"""
    # 文件名: <date>_<safe_sub_title>.pdf
    today = time.strftime('%Y-%m-%d')
    fn = f"{today}_{safe_filename(sub_title)}.pdf"
    out_path = os.path.join(OUT_DIR, fn)
    if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
        print(f'      [SKIP] 已存在: {fn}')
        log_writer.writerow({
            'row_id': '', 'title': sub_title, 'date': today,
            'pdf_url': pdf_url, 'status': 'skipped_exists',
            'local_path': out_path, 'size': os.path.getsize(out_path),
            'list_title': list_title,
        })
        log_file.flush()
        return True
    try:
        r = requests.get(pdf_url, headers=DOWNLOAD_HEADERS, timeout=60)
        if r.status_code == 200 and r.content[:4] == b'%PDF':
            with open(out_path, 'wb') as f:
                f.write(r.content)
            print(f'      [OK] {fn} ({len(r.content)} bytes)')
            log_writer.writerow({
                'row_id': '', 'title': sub_title, 'date': today,
                'pdf_url': pdf_url, 'status': 'downloaded',
                'local_path': out_path, 'size': len(r.content),
                'list_title': list_title,
            })
            log_file.flush()
            return True
        else:
            print(f'      [FAIL] {r.status_code} or not PDF')
            log_writer.writerow({
                'row_id': '', 'title': sub_title, 'date': today,
                'pdf_url': pdf_url, 'status': f'fail: {r.status_code}',
                'local_path': '', 'size': 0,
                'list_title': list_title,
            })
            log_file.flush()
            return False
    except Exception as e:
        print(f'      [ERR] requests: {str(e)[:60]}')
        log_writer.writerow({
            'row_id': '', 'title': sub_title, 'date': today,
            'pdf_url': pdf_url, 'status': f'fail: {str(e)[:60]}',
            'local_path': '', 'size': 0,
            'list_title': list_title,
        })
        log_file.flush()
        return False


def main():
    import sys
    max_page = None
    for arg in sys.argv[1:]:
        if arg.startswith('--max-page='):
            max_page = int(arg.split('=')[1])
        elif arg == '--reset':
            if os.path.exists(PROGRESS_PATH):
                os.remove(PROGRESS_PATH)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

    progress = {}
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH, 'r', encoding='utf-8') as f:
            progress = json.load(f)
    last_completed = progress.get('last_completed_page', 0)
    start_page = last_completed + 1
    if start_page > 1:
        print(f'起始页: {start_page} (断点续抓: 已完成 {last_completed} 页)')
    else:
        print(f'起始页: {start_page} (从头开始)')

    # 写日志
    log_exists = os.path.exists(LOG_PATH)
    log_file = open(LOG_PATH, 'a' if log_exists else 'w', encoding='utf-8-sig', newline='')
    writer = csv.DictWriter(log_file, fieldnames=['row_id', 'title', 'date', 'pdf_url', 'status', 'local_path', 'size', 'list_title'])
    if not log_exists:
        writer.writeheader()
        log_file.flush()

    t0 = time.time()
    total_downloaded = 0

    with sync_playwright() as p:
        ctx = launch_browser(p)
        pages = list(getattr(ctx, 'pages', None) or [])
        page = pages[0] if pages else ctx.new_page()

        page_num = start_page
        empty_pages = 0
        while True:
            if max_page and page_num > max_page:
                print(f'已达 max_page={max_page}, 停止')
                break

            print(f'\n=== 第 {page_num} 页 ===')
            if not go_to_list_page(page, page_num):
                empty_pages += 1
                if empty_pages >= 3:
                    print('连续 3 页加载失败, 停止')
                    break
                page_num += 1
                continue
            empty_pages = 0

            items = get_list_items(page)
            print(f'  [LIST] {len(items)} 条')
            if not items:
                # 可能是最后一页了
                print('  [LIST] 空, 停止')
                break

            page_downloaded = 0
            early_stop = False
            for i, (title, date) in enumerate(items):
                # 日期早停: list 倒序, 遇到早于 START_DATE 就 break
                if date and is_too_old(date):
                    print(f'  ⏹ [早停] li {i+1} 日期 {date} < {START_DATE}, 停止翻页')
                    early_stop = True
                    break
                # list 页先筛: 标题含"发行公告"or"产品说明书"才点
                if not any(kw in title for kw in TARGET_KEYWORDS):
                    continue
                print(f'  [{i+1}/{len(items)}] [{date}] {title[:40]}...')
                detail_page = click_to_detail(page, ctx, title)
                if not detail_page or isinstance(detail_page, str):
                    print(f'    [ERR] 详情页打开失败: {detail_page}')
                    continue
                n = download_pdfs_in_detail(detail_page, ctx, title, writer, log_file)
                page_downloaded += n
                total_downloaded += n
                # 关详情页
                try: detail_page.close()
                except: pass

            # 整页所有 li 都处理完, 才更新进度 (避免重处理已完成的页)
            progress['last_completed_page'] = page_num
            with open(PROGRESS_PATH, 'w', encoding='utf-8') as f:
                json.dump(progress, f, ensure_ascii=False, indent=2)

            print(f'  [PAGE] 本页下载 {page_downloaded} 个 (累计完成 {page_num} 页)')
            page_num += 1

            if early_stop:
                print(f'⏹ 早停触发, 总下载 {total_downloaded}')
                break

            elapsed = time.time() - t0
            print(f'  [TOTAL] {total_downloaded} PDF 用时 {elapsed:.0f}s')

        ctx.close()

    log_file.close()
    print(f'\n完成! 总下载 {total_downloaded}, 总耗时 {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
