# -*- coding: utf-8 -*-
"""
B09 广银理财 - 产品说明书下载脚本

从网站 prodManual 页面下载产品说明书（含变更公告）。
API 链路:
  1. noticeQuery/queryNoticeAllList.fun  → 列表（belong=prodManual）
  2. manageProdQuery/queryProdNoticeContent.fun  → 详情（filePath/fileName）
  3. wmpcext/news/downloadFile.fun?path=...&name=...  → 下载PDF

用法:
    python download_prod_manual.py              # 下载全部（4月9日至今）
    python download_prod_manual.py --dry-run    # 仅预览，不下载
"""

import argparse
import csv
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime

import requests

# Windows GBK 控制台兼容
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================
# 配置
# ============================================================

BASE = "https://www.cgbwmc.com.cn"
API_BASE = f"{BASE}/wmpcext/noSessionServlet"
LIST_API = f"{API_BASE}/noticeQuery/queryNoticeAllList.fun"
DETAIL_API = f"{API_BASE}/manageProdQuery/queryProdNoticeContent.fun"
DOWNLOAD_BASE = f"{BASE}/wmpcext/news/downloadFile.fun"

# 日期范围
START_DATE = "2026-04-09"
END_DATE = datetime.now().strftime("%Y-%m-%d")

# 下载目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "download_files", "产品说明书")

# 日志文件
LOG_CSV = os.path.join(SCRIPT_DIR, "广银理财_日志记录.csv")
DOWNLOADED_TXT = os.path.join(SCRIPT_DIR, "download_files", "downloaded.txt")

# 请求配置
FETCH_NUM = 100      # 每页条数（加大减少翻页次数）
REQUEST_RETRY = 2    # 重试次数
REQUEST_TIMEOUT = (5, 15)  # 超时秒数 (连接, 读取)
REQUEST_INTERVAL = (0.3, 0.6)  # 请求间隔（秒）
PAGE_INTERVAL = (0.5, 1.0)     # 翻页间隔（秒）

ORG_NAME = "广银理财"
NOTICE_TYPE = "产品说明书"


# ============================================================
# 工具函数
# ============================================================

def log(msg):
    """打印并立即刷新"""
    print(msg, flush=True)


def random_sleep(interval):
    import random
    t = random.uniform(*interval)
    time.sleep(t)


def sanitize_text(text, max_len=200):
    """清理文件名中的非法字符"""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = text.strip('. ')
    if len(text) > max_len:
        text = text[:max_len]
    return text


def make_session():
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/146.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": f"{BASE}/",
        "Origin": BASE,
    })
    # 连接池和重试配置
    retry = Retry(total=1, backoff_factor=0.5,
                  status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=5)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def wait_for_api(sess, max_wait=300):
    """等待API恢复，指数退避，最多等max_wait秒"""
    wait = 5
    total = 0
    while total < max_wait:
        try:
            r = sess.post(LIST_API, data={
                "belong": "prodManual", "startDate": START_DATE,
                "endDate": END_DATE, "beginNum": "0", "fetchNum": "1",
            }, timeout=(5, 10))
            if r.status_code == 200:
                log(f"  API 已恢复 (等待了 {total}s)")
                return True
        except Exception:
            pass
        log(f"  API 不可达，{wait}s 后重试... (已等待 {total}s)")
        time.sleep(wait)
        total += wait
        wait = min(wait * 2, 60)
    return False


def load_downloaded():
    """加载已下载记录（downloaded.txt）"""
    downloaded = set()
    if os.path.exists(DOWNLOADED_TXT):
        with open(DOWNLOADED_TXT, "r", encoding="utf-8") as f:
            for line in f:
                downloaded.add(line.strip())
    return downloaded


def append_downloaded(url):
    """追加已下载记录"""
    with open(DOWNLOADED_TXT, "a", encoding="utf-8") as f:
        f.write(url + "\n")


def append_log(row):
    """追加CSV日志"""
    file_exists = os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not file_exists or os.path.getsize(LOG_CSV) == 0:
            writer.writerow([
                "机构名称", "公告名称", "公告类型", "披露日期",
                "下载时间", "状态", "来源链接", "保存路径", "unique_key"
            ])
        writer.writerow(row)


# ============================================================
# API 调用
# ============================================================

def fetch_all_items(sess):
    """获取全部产品说明书列表"""
    all_items = []
    begin = 0

    while True:
        for retry in range(1, REQUEST_RETRY + 1):
            try:
                resp = sess.post(LIST_API, data={
                    "belong": "prodManual",
                    "startDate": START_DATE,
                    "endDate": END_DATE,
                    "beginNum": str(begin),
                    "fetchNum": str(FETCH_NUM),
                }, timeout=REQUEST_TIMEOUT)
                data = resp.json()
                items = data.get("retPage", {}).get("list", [])
                total = data.get("retPage", {}).get("totalNum", 0)

                if not items:
                    log(f"  列表结束，共 {len(all_items)} 条")
                    return all_items

                all_items.extend(items)
                log(f"  列表 page {begin // FETCH_NUM + 1}: "
                    f"获取 {len(items)} 条, 累计 {len(all_items)}/{total}")

                begin += len(items)
                if begin >= total:
                    return all_items

                random_sleep(PAGE_INTERVAL)
                break

            except Exception as e:
                err_str = str(e).lower()
                is_conn = any(kw in err_str for kw in
                              ['connection', 'remote', 'timed out', 'refused', 'reset'])
                log(f"  列表请求失败 (retry {retry}/{REQUEST_RETRY}): {e}")
                if is_conn:
                    log(f"  连接异常，等待API恢复...")
                    if not wait_for_api(sess):
                        log(f"  API 长时间不可达，中断")
                        return all_items
                    break  # API恢复了，重新请求当前页
                elif retry < REQUEST_RETRY:
                    time.sleep(3)
                else:
                    log(f"  列表请求彻底失败，从 begin={begin} 中断")
                    return all_items

    return all_items


def fetch_detail(sess, item):
    """获取产品说明书详情（filePath/fileName）"""
    for retry in range(1, REQUEST_RETRY + 1):
        try:
            resp = sess.post(DETAIL_API, data={
                "id": str(item["id"]),
                "title": item.get("title", ""),
                "publishDate": item.get("publishDate", ""),
            }, timeout=REQUEST_TIMEOUT)
            data = resp.json()
            ret_data = data.get("retData", {})

            if not isinstance(ret_data, dict):
                return None, "retData 不是字典"

            file_list = ret_data.get("fileList", [])
            if file_list:
                return file_list, None

            # 单文件情况
            file_name = ret_data.get("fileName", "")
            file_path = ret_data.get("filePath", "")
            if file_path and file_name:
                return [{"fileName": file_name, "filePath": file_path}], None

            return None, "详情中无文件信息"

        except Exception as e:
            err_str = str(e).lower()
            is_conn = any(kw in err_str for kw in
                          ['connection', 'remote', 'timed out', 'refused', 'reset'])
            if is_conn:
                log(f"    详情连接异常，等待恢复...")
                if wait_for_api(sess):
                    continue  # 恢复了，重试
                else:
                    return None, "API 长时间不可达"
            elif retry < REQUEST_RETRY:
                time.sleep(2)
            else:
                return None, str(e)

    return None, "重试耗尽"


def download_file(sess, file_path, file_name, save_path):
    """下载PDF文件"""
    encoded_name = urllib.parse.quote(file_name)
    url = f"{DOWNLOAD_BASE}?path={file_path}&name={encoded_name}"

    for retry in range(1, REQUEST_RETRY + 1):
        try:
            resp = sess.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")

            ct = resp.headers.get("content-type", "")
            if len(resp.content) < 100:
                raise RuntimeError(f"文件太小 ({len(resp.content)} bytes), "
                                   f"可能不是有效PDF")

            # 检查是否PDF
            if resp.content[:4] != b"%PDF":
                # 可能是错误响应
                text = resp.content[:200].decode("utf-8", errors="replace")
                raise RuntimeError(f"非PDF响应: {text[:100]}")

            with open(save_path, "wb") as f:
                f.write(resp.content)

            return True, url, ""

        except Exception as e:
            err_str = str(e).lower()
            is_conn = any(kw in err_str for kw in
                          ['connection', 'remote', 'timed out', 'refused', 'reset'])
            if is_conn:
                log(f"    下载连接异常，等待恢复...")
                if wait_for_api(sess):
                    continue
                else:
                    return False, url, "API 长时间不可达"
            elif retry < REQUEST_RETRY:
                time.sleep(2)
            else:
                return False, url, str(e)

    return False, url, "重试耗尽"


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="B09 产品说明书下载")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不下载")
    parser.add_argument("--list-only", action="store_true", help="仅获取列表并统计数量，不下载")
    parser.add_argument("--delay-min", type=float, default=REQUEST_INTERVAL[0],
                        help=f"请求最小间隔秒数（默认{REQUEST_INTERVAL[0]}）")
    parser.add_argument("--delay-max", type=float, default=REQUEST_INTERVAL[1],
                        help=f"请求最大间隔秒数（默认{REQUEST_INTERVAL[1]}）")
    parser.add_argument("--batch", type=int, default=0,
                        help="每轮最多下载数量（0=不限，适合分批定时运行）")
    args = parser.parse_args()

    # 应用自定义间隔
    req_interval = (args.delay_min, args.delay_max)
    page_interval = (args.delay_min * 1.5, args.delay_max * 1.5)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    log(f"{'='*60}")
    log(f"B09 广银理财 - 产品说明书下载")
    log(f"日期范围: {START_DATE} ~ {END_DATE}")
    log(f"下载目录: {DOWNLOAD_DIR}")
    mode = "dry-run（预览）" if args.dry_run else ("list-only（仅统计）" if args.list_only else "实际下载")
    log(f"模式: {mode}")
    log(f"请求间隔: {req_interval[0]}~{req_interval[1]}s")
    if args.batch > 0:
        log(f"每轮限制: {args.batch} 个文件")
    log(f"{'='*60}")

    sess = make_session()

    # 等待API可用
    log(f"\n[0] 检查API连通性...")
    if not wait_for_api(sess, max_wait=600):
        log("API 持续不可达，退出")
        return

    # Step 1: 获取列表
    log(f"\n[1] 获取产品说明书列表...")
    items = fetch_all_items(sess)
    if not items:
        log("未获取到任何产品说明书")
        return

    log(f"\n共 {len(items)} 个产品说明书")

    # --list-only: 仅统计
    if args.list_only:
        downloaded = load_downloaded()
        pending = [it for it in items if f"{DETAIL_API}?id={it['id']}" not in downloaded]
        log(f"\n[统计]")
        log(f"  API 返回列表总数: {len(items)}")
        log(f"  已处理（downloaded.txt）: {len(downloaded)}")
        log(f"  待处理: {len(pending)}")
        log(f"  磁盘已有文件: {len(os.listdir(DOWNLOAD_DIR))}")
        return

    # --dry-run: 显示待下载列表并退出
    if args.dry_run:
        downloaded = load_downloaded()
        pending = []
        for it in items:
            detail_url = f"{DETAIL_API}?id={it['id']}"
            if detail_url not in downloaded:
                pending.append(it)
        log(f"\n[dry-run] 待处理: {len(pending)} 个（已跳过 {len(items)-len(pending)} 个）")
        for i, it in enumerate(pending[:20]):
            log(f"  {i+1}. {it.get('title','')[:60]}  ({it.get('publishDate','')[:10]})")
        if len(pending) > 20:
            log(f"  ... 还有 {len(pending)-20} 个")
        log(f"\ndry-run 模式，不执行下载。")
        return

    # Step 2: 加载已下载记录
    downloaded = load_downloaded()
    log(f"已有 {len(downloaded)} 条下载记录")

    # Step 3: 逐个处理
    success_count = 0
    skip_count = 0
    fail_count = 0
    batch_count = 0  # 本轮已下载计数（--batch 用）
    seen_file_paths = set()  # 跟踪已处理的filePath，避免重复下载
    t_start = time.time()

    for idx, item in enumerate(items):
        # batch 限制检查
        if args.batch > 0 and batch_count >= args.batch:
            log(f"\n--- 已达本轮批次上限 {args.batch}，停止下载 ---")
            break
        item_id = str(item["id"])
        title = item.get("title", "")
        pub_date = item.get("publishDate", "")[:10]

        # 构建下载URL（用于去重）
        detail_url = f"{DETAIL_API}?id={item_id}"

        if detail_url in downloaded:
            skip_count += 1
            continue

        if (idx + 1) % 50 == 0:
            elapsed = time.time() - t_start
            log(f"\n--- 进度 {idx+1}/{len(items)}, "
                f"成功={success_count}, 跳过={skip_count}, 失败={fail_count}, "
                f"耗时={elapsed:.0f}s ---")

        # Step 3a: 获取详情
        file_list, err = fetch_detail(sess, item)
        if err:
            # 检测是否为网络错误（连接断开等），如果是则等待恢复
            if "Connect" in str(err) or "Remote" in str(err) or "timeout" in str(err).lower():
                log(f"  [{idx+1}] ⚠ 网络异常，等待恢复...")
                if not wait_for_api(sess, max_wait=300):
                    log(f"  API 持续不可达，从第 {idx+1} 条中断")
                    break
                # 恢复后重试当前条目
                file_list, err = fetch_detail(sess, item)
            if err:
                log(f"  [{idx+1}] ✗ {title[:50]}... 详情失败: {err}")
                fail_count += 1
                append_log([
                    ORG_NAME, title, NOTICE_TYPE, pub_date,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "FAILED", detail_url, "", f"{ORG_NAME}+{NOTICE_TYPE}+{title}+{pub_date}"
                ])
                random_sleep(req_interval)
                continue

        # Step 3b: 下载每个文件
        for fi, finfo in enumerate(file_list):
            f_name = finfo.get("fileName", "")
            f_path = finfo.get("filePath", "")

            if not f_path:
                continue

            # 构建保存文件名
            clean_name = re.sub(r'^附件\d+-\d+[：:]', '', f_name).strip()
            if not clean_name:
                clean_name = f_name
            safe_name = sanitize_text(clean_name, 150)

            save_filename = (
                f"{ORG_NAME}_{safe_name}_"
                f"{NOTICE_TYPE}_披露日期：{pub_date}.pdf"
            )
            save_filename = sanitize_text(save_filename, 240)
            save_path = os.path.join(DOWNLOAD_DIR, save_filename)

            # 如果文件已存在，跳过下载（但仍记录）
            if os.path.exists(save_path):
                skip_count += 1
                append_downloaded(detail_url)
                seen_file_paths.add(f_path)
                continue

            # 如果同一filePath已经下载过，直接复制/跳过
            if f_path in seen_file_paths:
                log(f"  [{idx+1}] → filePath已见过，跳过: {f_name[:50]}")
                skip_count += 1
                append_downloaded(detail_url)
                continue

            # 下载
            ok, dl_url, dl_err = download_file(sess, f_path, f_name, save_path)
            if not ok and ("Connect" in str(dl_err) or "Remote" in str(dl_err) or "timeout" in str(dl_err).lower()):
                log(f"  [{idx+1}] ⚠ 下载时网络异常，等待恢复...")
                if wait_for_api(sess, max_wait=300):
                    ok, dl_url, dl_err = download_file(sess, f_path, f_name, save_path)
            if ok:
                file_size = os.path.getsize(save_path)
                log(f"  [{idx+1}] ✓ {title[:50]}... ({file_size:,} bytes)")
                success_count += 1
                batch_count += 1
                append_downloaded(detail_url)
                seen_file_paths.add(f_path)
                append_log([
                    ORG_NAME, title, NOTICE_TYPE, pub_date,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "SUCCEED", dl_url, save_path,
                    f"{ORG_NAME}+{NOTICE_TYPE}+{title}+{pub_date}"
                ])
            else:
                log(f"  [{idx+1}] ✗ 下载失败: {dl_err}")
                fail_count += 1
                append_log([
                    ORG_NAME, title, NOTICE_TYPE, pub_date,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "FAILED", dl_url, save_path,
                    f"{ORG_NAME}+{NOTICE_TYPE}+{title}+{pub_date}"
                ])

            random_sleep(req_interval)

    # 汇总
    elapsed = time.time() - t_start
    log(f"\n{'='*60}")
    log(f"完成! 成功: {success_count}, 跳过: {skip_count}, 失败: {fail_count}")
    if args.batch > 0:
        log(f"本轮批次下载: {batch_count}/{args.batch}")
    log(f"耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
