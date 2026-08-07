"""
光大银行产品公告 PDF 下载器 — 真实点击 + 浏览器内下载

流程:
  1. 打开产品公告 list 第 1 页
  2. 遍历当前页所有 li.cpgg_li (list 页先筛: 含"发行公告"or"产品说明书"才点)
     a. 点 li 的 a.fl 标题 -> 开新 tab (详情页)
     b. 详情页里遍历所有 a 链接, 筛选"发行公告"or"产品说明书"
     c. 对每个符合条件的 a: click -> 开 PDF tab -> 拿 url -> requests 下载
     d. 关 PDF tab
     e. 详情页所有都下完 -> 关详情页
  3. 翻下一页 (持续点 .page_right 按钮)
  4. 直到没有下一页按钮

下载: requests.get(url) (拿 URL 比 expect_download 更稳)
存储: <date>_<safe_title>.pdf
进度: state/ceb_progress.json 存 last_completed_page (断点续抓)
日志: ceb_crawl_log.csv
"""
import os
import re
import csv
import json
import time
import requests
from typing import Optional
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

PROJECT_ROOT = r'E:\Program Files\PythonProject\crawler project\zzy_crawler\光大银行'
OUT_DIR = os.path.join(PROJECT_ROOT, 'download_files', '产品公告', '发行公告_产品说明书')
LOG_PATH = os.path.join(PROJECT_ROOT, 'ceb_crawl_log.csv')
STATE_DIR = os.path.join(PROJECT_ROOT, 'state')
PROGRESS_PATH = os.path.join(STATE_DIR, 'ceb_progress.json')
# 优先用 mcp playwright 的 profile (里面已带 cebwm 反爬过的 localStorage token)
# mcp profile 路径: C:\Users\ziyao\AppData\Local\ms-playwright\mcp-chrome-XXXXX
MCP_PROFILE_DIR = r'C:\Users\ziyao\AppData\Local\ms-playwright\mcp-chrome-8cd7d58'
PROFILE_DIR = MCP_PROFILE_DIR if os.path.exists(MCP_PROFILE_DIR) else os.path.join(STATE_DIR, 'ceb_chrome_profile')

LIST_URL = 'https://www.cebwm.com/wealth/gywm49/cpgg93/index.html'
DL_BASE = 'https://www.cebwm.com'

# 详情页内要下载的文件名关键词
TARGET_KEYWORDS = ['发行公告', '产品说明书']

# 日期范围配置
START_DATE = '2025-10-01'  # 起始日期 (含), list 倒序遇到早于此的就停
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
    os.makedirs(PROFILE_DIR, exist_ok=True)
    last_err = None
    for cand in BROWSER_CANDIDATES:
        try:
            kwargs = {
                'headless': HEADLESS,
                'args': [
                    '--disable-blink-features=AutomationControlled',
                    '--window-position=-2000,0',
                ],
                'locale': 'zh-CN',
            }
            if cand in ('chrome', 'msedge'):
                kwargs['channel'] = cand
            ctx = p.chromium.launch_persistent_context(PROFILE_DIR, **kwargs)
            # 加反爬脚本 (参考 main.py)
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
        # cebwm 反爬是 JS 计算 + 写 localStorage, 需要等 5-10s
        # 第 1 页反爬未过, body 是空的, 不能用 wait_for_selector(li) -- 改用检测 body 长度变化
        print('  [反爬] 等待反爬 JS 计算 (最多 30s)...')
        body_text = 0
        for i in range(60):
            time.sleep(0.5)
            try:
                body_text = page.evaluate('document.body ? document.body.innerText.length : 0')
                if body_text and body_text > 100:
                    # body 有内容了, 反爬已过; 等 list items (可能 list API 还在请求)
                    print(f'  [反爬] 已过 (body={body_text} chars), 等 list items (最多 30s)...')
                    try:
                        page.wait_for_selector('li.cpgg_li a.fl', timeout=30000)
                        return True
                    except PWTimeoutError:
                        # body 有内容但 list 还没加载, 可能是 list API 卡了
                        print(f'  [DEBUG] body={body_text} 但 list items 没出现, reload 重试')
                        try:
                            page.reload(wait_until='domcontentloaded', timeout=30000)
                            time.sleep(8)
                            page.wait_for_selector('li.cpgg_li a.fl', timeout=30000)
                            return True
                        except:
                            return False
            except Exception:
                pass
        print(f'  [ERR] 30s 内反爬仍未解除 (body={body_text}), 跳过')
        return False
    else:
        # 持续点 .page_right 直到页号到 page_num
        for _ in range(page_num - 1):
            try:
                next_btn = page.locator('.page_right').first
                if not next_btn or not next_btn.is_enabled():
                    print(f'  [WARN] 已到末页, 没法翻到第 {page_num} 页')
                    return False
                next_btn.click()
                time.sleep(2.0)
                page.wait_for_selector('li.cpgg_li a.fl', timeout=20000)
            except Exception as e:
                print(f'  [ERR] 翻页失败: {e}')
                return False
    return True


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
        with ctx.expect_page(timeout=8000) as info:
            a.click()
        return info.value
    except PWTimeoutError:
        return 'no_tab'
    except Exception as e:
        return f'click_err: {str(e)[:80]}'


def download_pdfs_in_detail(detail_page, ctx, list_title: str, log_writer, log_file) -> int:
    """详情页里遍历所有 a 链接, 筛"发行公告"或"产品说明书" -> click -> 拿 PDF url -> 下载"""
    # 等详情页加载
    try:
        detail_page.wait_for_load_state('domcontentloaded', timeout=15000)
    except:
        pass
    time.sleep(1.5)

    # 找所有 a 链接, 文字里含目标关键词
    found = []
    try:
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

    # 去重
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
                redbox = detail_page.locator(f'a:has-text("{sub_title}")').first
        except Exception as e:
            print(f'      [ERR] 找 a 失败: {str(e)[:60]}')
            continue

        # click -> 可能是新 tab 也可能是当前 detail_page 跳 PDF
        pdf_url = None
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
                try: detail_page.close()
                except: pass

            # 整页所有 li 都处理完, 才更新进度
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
