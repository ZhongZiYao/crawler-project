# --- UTF-8 stdout fix (Windows GBK emoji/unicode crash) ---
import io as _io, sys as _sys
if _sys.platform == 'win32':
    try:
        _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        _sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')
        _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace')
# --- end UTF-8 fix ---

"""
光大银行产品公告 PDF 下载器 — DrissionPage 版本

修复说明:
  - 原版本使用 Playwright 被反爬拦截（返回空HTML）
  - 改用 DrissionPage 绕过反爬检测
  - 修复分页逻辑：使用 goPage() JS 函数替代点击 .page_right

流程:
  1. 打开产品公告 list 第 1 页
  2. 遍历当前页所有 li.cpgg_li (list 页先筛: 含"发行公告"or"产品说明书"才点)
     a. 点 li 的 a.fl 标题 -> 开新 tab (详情页)
     b. 详情页里遍历所有 a 链接, 筛选"发行公告"or"产品说明书"
     c. 对每个符合条件的 a: click -> 开 PDF tab -> 拿 url -> requests 下载
     d. 关 PDF tab
     e. 详情页所有都下完 -> 关详情页
  3. 翻下一页 (使用 goPage() JS 函数)
  4. 直到没有下一页或达到最大页数

下载: requests.get(url)
存储: <date>_<safe_title>.pdf
进度: state/ceb_progress.json 存 last_completed_page (断点续抓)
日志: ceb_crawl_log.csv
"""
import os
import re
import csv
import json
import time
import tempfile
import shutil
import requests
from typing import Optional, List, Tuple

# DrissionPage 替代 Playwright
from DrissionPage import ChromiumPage, ChromiumOptions

PROJECT_ROOT = r'E:\Program Files\PythonProject\crawler project\zzy_crawler\光大银行'
OUT_DIR = os.path.join(PROJECT_ROOT, 'download_files', '产品公告', '发行公告_产品说明书')
LOG_PATH = os.path.join(PROJECT_ROOT, 'ceb_crawl_log.csv')
STATE_DIR = os.path.join(PROJECT_ROOT, 'state')
PROGRESS_PATH = os.path.join(STATE_DIR, 'ceb_progress.json')

LIST_URL = 'https://www.cebwm.com/wealth/gywm49/cpgg93/index.html'
DL_BASE = 'https://www.cebwm.com'

# 详情页内要下载的文件名关键词
TARGET_KEYWORDS = ['发行公告', '产品说明书']

# 日期范围配置
START_DATE = '2025-10-01'  # 起始日期 (含), list 倒序遇到早于此的就停
END_DATE = ''              # 截止日期 (空=今天)

# 浏览器配置
HEADLESS = False
TEMP_PROFILE_DIR = None  # 运行时创建


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


def safe_filename(name: str, max_len: int = 100) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = name.strip().rstrip('.')
    if len(name) > max_len:
        name = name[:max_len]
    return name


def create_browser() -> ChromiumPage:
    """创建 DrissionPage 浏览器实例"""
    global TEMP_PROFILE_DIR
    
    # 使用临时 profile 避免与已打开的 Chrome 冲突
    TEMP_PROFILE_DIR = tempfile.mkdtemp(prefix='ceb_crawl_')
    
    co = ChromiumOptions()
    co.set_user_data_path(TEMP_PROFILE_DIR)
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    
    if HEADLESS:
        co.headless()
    
    page = ChromiumPage(co)
    print(f'[BROWSER] DrissionPage 启动成功, profile: {TEMP_PROFILE_DIR}')
    return page


def cleanup_browser(page: ChromiumPage):
    """清理浏览器和临时 profile"""
    global TEMP_PROFILE_DIR
    try:
        page.quit()
    except:
        pass
    if TEMP_PROFILE_DIR and os.path.exists(TEMP_PROFILE_DIR):
        try:
            shutil.rmtree(TEMP_PROFILE_DIR, ignore_errors=True)
        except:
            pass


def go_to_list_page(page: ChromiumPage, page_num: int, need_init: bool = True) -> bool:
    """翻到指定页: 使用 goPage() JS 函数
    
    need_init: 是否需要先加载首页（断点续抓时需要）
    """
    # 如果页面还没加载（如断点续抓），先加载首页
    if need_init and '产品公告' not in (page.title or ''):
        print(f'  [INIT] 先加载首页...')
        try:
            page.get(LIST_URL)
            time.sleep(8)
            if '产品公告' not in (page.title or ''):
                print(f'  [ERR] 首页加载失败: {page.title}')
                return False
        except Exception as e:
            print(f'  [ERR] 首页加载异常: {e}')
            return False
    
    if page_num == 1:
        # 如果已经在首页，直接返回
        if '产品公告' in (page.title or ''):
            print(f'  [OK] 已在首页: {page.title}')
            return True
        # 否则加载首页
        try:
            page.get(LIST_URL)
            time.sleep(8)
            title = page.title
            if '产品公告' in title:
                print(f'  [OK] 首页加载成功: {title}')
                return True
            else:
                print(f'  [ERR] 首页加载异常: {title}')
                return False
        except Exception as e:
            print(f'  [ERR] goto 首页失败: {e}')
            return False
    else:
        # 使用 goPage() JS 函数翻页
        try:
            # 先检查当前页码
            current_page = page.run_js('''
                var el = document.querySelector('.page_left_no');
                if (el) {
                    var text = el.textContent || el.innerText;
                    var match = text.match(/\\d+/);
                    return match ? parseInt(match[0]) : 0;
                }
                return 0;
            ''')
            print(f'  [DEBUG] 当前页码: {current_page}')
            
            # 调用 goPage 翻到目标页
            page.run_js(f'goPage({page_num})')
            time.sleep(3)
            
            # 验证翻页成功
            lis = page.eles('tag:li@@class:cpgg_li')
            if len(lis) > 0:
                print(f'  [OK] 翻到第 {page_num} 页, {len(lis)} 条记录')
                return True
            else:
                print(f'  [ERR] 翻页后无记录')
                return False
        except Exception as e:
            print(f'  [ERR] 翻页失败: {e}')
            return False


def get_list_items(page: ChromiumPage) -> List[Tuple[str, str, any]]:
    """获取当前 list 页所有 li 元素 + 标题 + 日期 + 元素引用"""
    items = []
    try:
        lis = page.eles('tag:li@@class:cpgg_li')
        for li in lis:
            try:
                a = li.ele('tag:a@@class:fl')
                if a:
                    title = a.text.strip()
                    date = ''
                    try:
                        date_el = li.ele('tag:span@@class:li_showdate')
                        if date_el:
                            date = date_el.text.strip()
                    except:
                        pass
                    if title:
                        items.append((title, date, li))
            except:
                continue
    except Exception as e:
        print(f'  [ERR] 获取 list items 失败: {e}')
    return items


def click_to_detail(page: ChromiumPage, title: str) -> Optional[ChromiumPage]:
    """点击 list 里的标题 -> 获取新详情页 tab"""
    try:
        # 找到匹配的 li 并点击
        lis = page.eles('tag:li@@class:cpgg_li')
        for li in lis:
            try:
                a = li.ele('tag:a@@class:fl')
                if a and title in a.text:
                    # 记录点击前的 tabs
                    tabs_before = set(page.get_tabs())
                    
                    a.click()
                    time.sleep(3)
                    
                    # 找到新打开的 tab
                    tabs_after = page.get_tabs()
                    new_tabs = [t for t in tabs_after if t not in tabs_before]
                    
                    if new_tabs:
                        detail_tab = page.get_tab(new_tabs[0])
                        time.sleep(2)
                        return detail_tab
                    else:
                        # 可能是在当前页打开的弹窗
                        print(f'    [WARN] 未检测到新 tab，可能是弹窗')
                        return None
            except:
                continue
    except Exception as e:
        print(f'    [ERR] 点击失败: {e}')
    return None


def download_pdfs_in_detail(detail_page: ChromiumPage, list_title: str, log_writer, log_file) -> int:
    """详情页里遍历所有 a 链接, 筛"发行公告"或"产品说明书" -> click -> 拿 PDF url -> 下载"""
    time.sleep(2)
    
    # 找所有 a 链接, 文字里含目标关键词
    found = []
    try:
        links = detail_page.eles('tag:a')
        for link in links:
            try:
                text = link.text.strip()
                if not text or len(text) < 5 or len(text) > 200:
                    continue
                if any(kw in text for kw in TARGET_KEYWORDS):
                    href = link.attr('href') or ''
                    found.append((text, link, href))
            except:
                continue
    except Exception as e:
        print(f'    [ERR] 详情页找 a 失败: {str(e)[:60]}')
        return 0
    
    if not found:
        return 0
    
    # 去重
    seen = set()
    unique_found = []
    for text, link, href in found:
        if text not in seen:
            seen.add(text)
            unique_found.append((text, link, href))
    found = unique_found
    
    print(f'    [DETAIL] {len(found)} 个待下载')
    
    downloaded = 0
    for sub_title, link, href in found:
        pdf_url = None
        
        # 尝试直接获取 href
        if href and '.pdf' in href.lower():
            pdf_url = href if href.startswith('http') else DL_BASE + href
        else:
            # 点击链接获取 PDF URL
            try:
                tabs_before = set(detail_page.get_tabs())
                link.click()
                time.sleep(2)
                
                tabs_after = detail_page.get_tabs()
                new_tabs = [t for t in tabs_after if t not in tabs_before]
                
                if new_tabs:
                    pdf_tab = detail_page.get_tab(new_tabs[0])
                    pdf_url = pdf_tab.url
                    try:
                        pdf_tab.close()
                    except:
                        pass
                else:
                    # 可能是在当前页导航
                    time.sleep(1)
                    if '.pdf' in detail_page.url.lower():
                        pdf_url = detail_page.url
                        detail_page.get(detail_page.url)  # 重置
            except Exception as e:
                print(f'      [ERR] 点击失败: {str(e)[:60]}')
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
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': LIST_URL,
        }
        r = requests.get(pdf_url, headers=headers, timeout=60)
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
    page = None
    
    try:
        page = create_browser()
        
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
            
            for i, (title, date, li) in enumerate(items):
                # 日期早停
                if date and is_too_old(date):
                    print(f'  ⏹ [早停] li {i+1} 日期 {date} < {START_DATE}, 停止翻页')
                    early_stop = True
                    break
                
                # list 页先筛: 标题含"发行公告"or"产品说明书"才点
                if not any(kw in title for kw in TARGET_KEYWORDS):
                    continue
                
                print(f'  [{i+1}/{len(items)}] [{date}] {title[:40]}...')
                detail_page = click_to_detail(page, title)
                
                if not detail_page:
                    print(f'    [ERR] 详情页打开失败')
                    continue
                
                n = download_pdfs_in_detail(detail_page, title, writer, log_file)
                page_downloaded += n
                total_downloaded += n
                
                try:
                    detail_page.close()
                except:
                    pass
            
            # 更新进度
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
    
    finally:
        if page:
            cleanup_browser(page)
        log_file.close()
    
    print(f'\n完成! 总下载 {total_downloaded}, 总耗时 {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
