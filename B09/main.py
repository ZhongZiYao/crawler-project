# -*- coding: utf-8 -*-
"""
B09 广银理财 信息披露爬虫（2026-08 重写版）

新网站: https://www.cgbwmc.com.cn （Vue SPA，统一网关 /wmpcext/noSessionServlet）
抓取三类:
  1. 产品说明书  (#/prodManual)      -> belong=prodManual
  2. 临时公告    (#/tempNotice)      -> belongFirst=tempNotice
  3. 定期报告    (#/regularReport)   -> belong=regularReport

接口（均为 POST application/x-www-form-urlencoded，网关统一转发）:
  列表: {GATEWAY}/noticeQuery/queryNoticeAllList.fun
        参数: belong/belongFirst, startDate, endDate, fetchNum, beginNum
        返回: retPage.list=[{id, publishDate, title}], retPage.totalNum
  详情: {GATEWAY}/manageProdQuery/queryProdNoticeContent.fun
        参数: id (列表行字段)
        返回: retData={isHis, fileList:[{fileName, filePath, showStatus}], belongContent}
  下载: GET {BASE}/wmpcext/news/downloadFile.fun?path=<filePath>&name=<fileName>

纯 requests 实现，无需浏览器。支持断点续跑、去重、失败重试、日志 CSV。
"""

import argparse
import csv
import html as html_lib
import json
import os
import random
import re
import sys
import time
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 控制台 UTF-8，防止 GBK 下 print 中文/emoji 崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ============================================================
# 日期区间（集中配置在父目录 project_meta.py）
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_KEY = "B09"
try:
    sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE
    START_DATE = PROJECT_START_DATE.get(PROJECT_KEY, "2026-07-01")
    END_DATE = PROJECT_END_DATE or datetime.now().strftime("%Y-%m-%d")
except Exception:
    START_DATE = "2026-07-01"
    END_DATE = "2026-08-01"

# ============================================================
# 用户配置区
# ============================================================
BASE = "https://www.cgbwmc.com.cn"
GATEWAY = f"{BASE}/wmpcext/noSessionServlet"
LIST_API = f"{GATEWAY}/noticeQuery/queryNoticeAllList.fun"
DETAIL_API = f"{GATEWAY}/manageProdQuery/queryProdNoticeContent.fun"
DOWNLOAD_API = f"{BASE}/wmpcext/news/downloadFile.fun"

ORG_NAME = "广银理财"

# 三个栏目: param=列表接口使用的表单字段名, code=栏目代码
CATEGORIES = {
    "产品说明书": {"param": "belong", "code": "prodManual"},
    "临时公告":   {"param": "belongFirst", "code": "tempNotice"},
    "定期报告":   {"param": "belong", "code": "regularReport"},
}

# 栏目开关（新一轮全部打开）
ENABLE_CATEGORIES = {
    "产品说明书": True,
    "临时公告": True,
    "定期报告": False,
}

# 临时公告子栏目过滤：只下载这4个子栏目的公告（按标题关键词匹配）
# 网站前端也是按标题关键词分类的，API 不支持 belongSecond 参数
TEMP_NOTICE_SUB_CATEGORIES = {
    "阶段性费率优惠公告": {
        # 标题同时含"费率"和"优惠"（兼容"费率 优惠"带空格的情况）
        "keywords_all": ["费率", "优惠"],
    },
    "费率调整公告": {
        # 标题含"费率调整"
        "keywords_any": ["费率调整"],
    },
    "业绩比较基准调整公告": {
        # 标题含"业绩比较基准"
        "keywords_any": ["业绩比较基准"],
    },
    "新增份额公告": {
        # 标题含"增加产品份额"或"增加份额"或"新增份额"或"新设份额"
        "keywords_any": ["增加产品份额", "增加份额", "新增份额", "新设份额"],
    },
}

# 单栏目测试：填栏目名则只跑该栏目，空字符串=按开关执行
RUN_ONLY_CATEGORY = ""

# 测试限制：每栏目最多处理 N 条（0=不限制）
TEST_LIMIT_ITEMS = 0
# 测试限制：每栏目最多翻页数（None=不限制）
MAX_PAGES_PER_CATEGORY = None

# 请求参数
FETCH_NUM = 100                 # 每页条数
REQUEST_RETRY = 3               # 单请求重试次数
REQUEST_TIMEOUT = (8, 30)       # (连接, 读取) 超时秒数
# 间隔加长以规避风控（2026-07 产品说明书用 5~8s 间隔成功下载，0.4~0.8s 会触发风控）
REQUEST_INTERVAL = (5.0, 8.0)   # 请求间隔（秒）
PAGE_INTERVAL = (6.0, 10.0)     # 翻页间隔（秒）
API_WAIT_MAX_SECONDS = 300      # API 不可达时最长等待秒数

# 风控规避：分批下载 + 批间长休息 + 连续失败长冷却（参考 2026-07 成功经验）
BATCH_SIZE = 30                 # 每处理 N 条详情后长休息一次（0=不分批）
BATCH_PAUSE = (120, 230)        # 批间休息时长（秒）
FAIL_STREAK_LIMIT = 5           # 连续失败 N 条视为疑似触发风控
FAIL_STREAK_PAUSE = (300, 480)  # 疑似风控后的长冷却（秒）

# 文件与状态
DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")
DOWNLOADED_TXT = os.path.join(DOWNLOAD_ROOT, "downloaded.txt")
CHECKPOINT_FILE = os.path.join(DOWNLOAD_ROOT, "checkpoint.json")
LOG_CSV = os.path.join(SCRIPT_DIR, f"{ORG_NAME}_日志记录.csv")
LOG_HEADER = [
    "机构名称", "公告名称", "公告类型", "披露日期",
    "下载时间", "状态", "来源链接", "保存路径", "unique_key",
]


# ============================================================
# 工具函数
# ============================================================

def log(msg):
    print(msg, flush=True)


def random_sleep(interval):
    time.sleep(random.uniform(*interval))


def sanitize_text(text, max_len=200):
    """清理文件名中的非法字符"""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text))
    text = text.strip(". ")
    if len(text) > max_len:
        text = text[:max_len]
    return text


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def in_date_range(date_str):
    """披露日期是否在 [START_DATE, END_DATE] 闭区间内（解析失败时放行，交给接口过滤兜底）"""
    if not date_str:
        return True
    d = str(date_str)[:10]
    try:
        return START_DATE <= d <= END_DATE
    except Exception:
        return True


def load_checkpoint():
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_checkpoint(data):
    os.makedirs(DOWNLOAD_ROOT, exist_ok=True)
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for _ in range(5):
        try:
            os.replace(tmp, CHECKPOINT_FILE)
            return
        except PermissionError:
            time.sleep(0.15)
    try:
        os.replace(tmp, CHECKPOINT_FILE)
    except Exception:
        pass


def load_downloaded():
    """已下载记录: 返回 (已处理条目集合[行格式 栏目|id], 已存文件filePath集合[行格式 file|path])"""
    done = set()
    files = set()
    if os.path.exists(DOWNLOADED_TXT):
        with open(DOWNLOADED_TXT, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("file|"):
                    files.add(line[5:])
                else:
                    done.add(line)
    return done, files


def append_downloaded(key):
    with open(DOWNLOADED_TXT, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def append_log(row):
    exists = os.path.exists(LOG_CSV) and os.path.getsize(LOG_CSV) > 0
    with open(LOG_CSV, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(LOG_HEADER)
        writer.writerow(row)


def make_session():
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36"),
        "Accept": "*/*",
        "Referer": f"{BASE}/",
        "Origin": BASE,
    })
    retry = Retry(total=1, backoff_factor=0.5,
                  status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=5)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def wait_for_api(sess, cat_conf=None, max_wait=API_WAIT_MAX_SECONDS):
    """API 不可达时指数退避等待恢复（探测用当前栏目的参数）"""
    probe = {
        "prodCd": "", "prodSerch": "", "accurateState": "",
        "parentProdCode": "", "title": "",
        "startDate": START_DATE, "endDate": END_DATE,
        "beginNum": "0", "fetchNum": "1",
    }
    if cat_conf:
        probe[cat_conf["param"]] = cat_conf["code"]
    else:
        probe["belong"] = "prodManual"
    wait = 5
    total = 0
    while total < max_wait:
        try:
            r = sess.post(LIST_API, data=probe, timeout=(8, 15))
            if r.status_code == 200:
                log(f"  API 已恢复（等待了 {total}s）")
                return True
        except Exception:
            pass
        log(f"  API 不可达，{wait}s 后重试...（已等待 {total}s）")
        time.sleep(wait)
        total += wait
        wait = min(wait * 2, 60)
    return False


def risk_pause(sess, pause_range, cat_conf=None, reason=""):
    """疑似风控时的长休息：随机等待 → 重建会话（丢弃旧连接指纹）→ 等待 API 恢复。
    返回新的 session。"""
    wait_sec = random.uniform(*pause_range)
    log(f"  ⚠ {reason}，风控规避休息 {wait_sec/60:.1f} 分钟...")
    time.sleep(wait_sec)
    try:
        sess.close()
    except Exception:
        pass
    new_sess = make_session()
    log("  ↻ 已重建网络会话，探测 API...")
    wait_for_api(new_sess, cat_conf)
    return new_sess


def is_network_error(err):
    s = str(err)
    return any(k in s for k in ("Connect", "connect", "Remote", "remote",
                                "timeout", "Timeout", "Connection", "connection",
                                "Reset", "reset", "aborted"))


# ============================================================
# API 调用
# ============================================================

def list_payload(cat_conf):
    """列表接口表单：栏目字段 + 空的筛选字段 + 日期区间 + 分页"""
    data = {
        "prodCd": "", "prodSerch": "", "accurateState": "",
        "parentProdCode": "", "title": "",
        "startDate": START_DATE, "endDate": END_DATE,
    }
    data[cat_conf["param"]] = cat_conf["code"]
    return data


def fetch_list_page(sess, cat_conf, begin):
    """抓取一页列表，返回 (items, totalNum)。失败抛异常。"""
    data = list_payload(cat_conf)
    data["beginNum"] = str(begin)
    data["fetchNum"] = str(FETCH_NUM)
    last_err = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            r = sess.post(LIST_API, data=data, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            j = r.json()
            if j.get("retCode") != "000000":
                raise RuntimeError(f"retCode={j.get('retCode')} retInfo={j.get('retInfo')}")
            rp = j.get("retPage") or {}
            return rp.get("list") or [], int(rp.get("totalNum") or 0)
        except Exception as e:
            last_err = e
            if is_network_error(e) and attempt < REQUEST_RETRY:
                time.sleep(1.5 * attempt)
                continue
            if attempt < REQUEST_RETRY:
                time.sleep(1.0 * attempt)
    raise last_err


def fetch_detail(sess, item):
    """抓取详情，返回 retData dict。失败抛异常。"""
    data = {k: v for k, v in item.items() if v is not None}
    last_err = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            r = sess.post(DETAIL_API, data=data, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            j = r.json()
            if j.get("retCode") != "000000":
                raise RuntimeError(f"retCode={j.get('retCode')} retInfo={j.get('retInfo')}")
            return j.get("retData") or {}
        except Exception as e:
            last_err = e
            if is_network_error(e) and attempt < REQUEST_RETRY:
                time.sleep(1.5 * attempt)
                continue
            if attempt < REQUEST_RETRY:
                time.sleep(1.0 * attempt)
    raise last_err


def download_file(sess, file_path, file_name, save_path):
    """下载单个附件。返回 (ok, url, err)。"""
    url = f"{DOWNLOAD_API}?path={requests.utils.quote(file_path, safe='')}" \
          f"&name={requests.utils.quote(file_name, safe='')}"
    last_err = None
    for attempt in range(1, REQUEST_RETRY + 1):
        try:
            with sess.get(url, timeout=REQUEST_TIMEOUT, stream=True) as r:
                r.raise_for_status()
                tmp_path = save_path + ".part"
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
            if os.path.getsize(tmp_path) < 64:
                os.remove(tmp_path)
                raise RuntimeError("返回内容过小，疑似错误页")
            if os.path.exists(save_path):
                os.remove(save_path)
            os.replace(tmp_path, save_path)
            return True, url, ""
        except Exception as e:
            last_err = e
            try:
                if os.path.exists(save_path + ".part"):
                    os.remove(save_path + ".part")
            except OSError:
                pass
            if attempt < REQUEST_RETRY:
                time.sleep(1.5 * attempt)
    return False, url, str(last_err)


# ============================================================
# 文件命名与保存
# ============================================================

def build_save_path(category, title, pub_date, file_name, idx, used_names):
    """
    保存文件名: 广银理财_{清理后的附件名}_{栏目}_披露日期：{日期}.{扩展名}
    附件名为空时回退为公告标题。重名自动加序号。
    """
    clean = re.sub(r"^附件\d+-\d+[：:]", "", str(file_name or "")).strip()
    ext = ".pdf"
    if clean:
        m = re.search(r"\.(pdf|zip|doc|docx|xls|xlsx|html|htm|png|jpg|jpeg|txt)$",
                      clean, re.I)
        if m:
            ext = "." + m.group(1).lower()
            clean = clean[:m.start()]
    base_name = clean if clean else sanitize_text(title, 120)
    if not base_name:
        base_name = f"无标题_{idx}"
    save_name = f"{ORG_NAME}_{base_name}_{category}_披露日期：{pub_date}{ext}"
    save_name = sanitize_text(save_name, 240)
    candidate = os.path.join(DOWNLOAD_ROOT, category, save_name)
    seq = 1
    while candidate.lower() in used_names or os.path.exists(candidate):
        stem, e = os.path.splitext(save_name)
        candidate = os.path.join(DOWNLOAD_ROOT, category, f"{stem}_{seq}{e}")
        seq += 1
    used_names.add(candidate.lower())
    return candidate


def unescape_html_content(text):
    """详情页 belongContent 可能做了实体转义（前端 Rtp.escape2Html 还原），这里同步还原"""
    if not text:
        return ""
    if "&lt;" in text or "&gt;" in text or "&amp;" in text:
        try:
            return html_lib.unescape(text)
        except Exception:
            return text
    return text


# ============================================================
# 单栏目爬取
# ============================================================

def match_temp_notice_sub_category(title):
    """判断临时公告标题是否属于用户要的4个子栏目之一，返回子栏目名或 None"""
    title = str(title or "")
    for sub_name, rules in TEMP_NOTICE_SUB_CATEGORIES.items():
        # keywords_all: 标题必须同时包含所有关键词
        if "keywords_all" in rules:
            if all(kw in title for kw in rules["keywords_all"]):
                return sub_name
        # keywords_any: 标题包含任一关键词即匹配
        if "keywords_any" in rules:
            if any(kw in title for kw in rules["keywords_any"]):
                return sub_name
    return None


def crawl_category(sess, category, cat_conf, downloaded, file_seen, checkpoint, used_names):
    log("")
    log("=" * 60)
    log(f"▶ 栏目: {category}  (参数 {cat_conf['param']}={cat_conf['code']})")
    log("=" * 60)

    save_dir = os.path.join(DOWNLOAD_ROOT, category)
    os.makedirs(save_dir, exist_ok=True)

    cat_state = checkpoint.setdefault("categories", {}).setdefault(category, {})
    if cat_state.get("done"):
        log("  该栏目 checkpoint 已完成，跳过")
        return 0, 0, 0

    begin = int(cat_state.get("begin") or 0)
    success = fail = skip = 0
    page_no = 0
    total_num = None
    batch_count = 0     # 本批已请求详情的条数（用于批间长休息）
    fail_streak = 0     # 连续失败条数（疑似风控指示器）
    finished = False    # 仅当列表真正取完才置 True（才会标记 done）

    while True:
        page_no += 1
        try:
            items, total_num = fetch_list_page(sess, cat_conf, begin)
        except Exception as e:
            if is_network_error(e):
                log(f"  ⚠ 列表请求网络异常，等待 API 恢复... ({e})")
                if wait_for_api(sess, cat_conf):
                    try:
                        items, total_num = fetch_list_page(sess, cat_conf, begin)
                    except Exception as e2:
                        sess = risk_pause(sess, FAIL_STREAK_PAUSE, cat_conf,
                                          f"恢复后列表仍失败: {e2}")
                        try:
                            items, total_num = fetch_list_page(sess, cat_conf, begin)
                        except Exception as e3:
                            log(f"  ✗ 长休息后仍失败: {e3}")
                            break
                else:
                    sess = risk_pause(sess, FAIL_STREAK_PAUSE, cat_conf,
                                      "API 长时间不可达")
                    try:
                        items, total_num = fetch_list_page(sess, cat_conf, begin)
                    except Exception as e2:
                        log(f"  ✗ 长休息后仍失败: {e2}")
                        break
            else:
                log(f"  ✗ 列表请求失败: {e}")
                break

        if not items:
            if total_num and begin < total_num:
                # 未取完就返回空页：疑似风控空返回，长休息后重试一次
                log(f"  ⚠ 第 {page_no} 页异常为空（总数 {total_num}, begin={begin}），疑似风控")
                sess = risk_pause(sess, FAIL_STREAK_PAUSE, cat_conf, "列表异常返回空页")
                try:
                    items, total_num2 = fetch_list_page(sess, cat_conf, begin)
                    if total_num2:
                        total_num = total_num2
                except Exception as e:
                    log(f"  ✗ 休息后仍失败: {e}")
                    break
                if not items:
                    log("  休息后仍为空页，本栏目中止（未完成，下次续跑）")
                    break
            else:
                log(f"  第 {page_no} 页无数据，列表已取完")
                finished = True
                break

        log(f"  第 {page_no} 页: {len(items)} 条 (总数 {total_num}, begin={begin})")

        # 临时公告：先统计本页匹配4个子栏目的条数
        if category == "临时公告":
            matched = sum(1 for it in items if match_temp_notice_sub_category(it.get("title", "")))
            log(f"    其中 {matched} 条匹配4个子栏目关键词（阶段性费率优惠/费率调整/业绩比较基准/新增份额）")

        for idx, item in enumerate(items, 1):
            item_id = str(item.get("id") or "")
            title = str(item.get("title") or "").strip()
            pub_date = str(item.get("publishDate") or "")[:10]
            dedup_key = f"{category}|{item_id}"

            if TEST_LIMIT_ITEMS and (success + fail + skip) >= TEST_LIMIT_ITEMS:
                log(f"  达到测试上限 {TEST_LIMIT_ITEMS} 条，停止本栏目（不标记完成，正式运行可继续）")
                save_checkpoint(checkpoint)
                return success, fail, skip

            if dedup_key in downloaded:
                skip += 1
                continue

            if not in_date_range(pub_date):
                skip += 1
                continue

            # 临时公告子栏目过滤：只下载4个子栏目的公告
            if category == "临时公告":
                sub_cat = match_temp_notice_sub_category(title)
                if not sub_cat:
                    skip += 1
                    continue
                # 记录匹配到的子栏目（用于日志展示）
                item_sub_cat = sub_cat
            else:
                item_sub_cat = category

            # Step 1: 详情（失败时等待恢复→长休息→重试一次，仍失败记入并累计连续失败）
            try:
                detail = fetch_detail(sess, item)
            except Exception as e:
                if is_network_error(e):
                    log(f"  ⚠ 详情网络异常，等待恢复... ({e})")
                    if not wait_for_api(sess, cat_conf):
                        sess = risk_pause(sess, FAIL_STREAK_PAUSE, cat_conf,
                                          "API 长时间不可达")
                try:
                    detail = fetch_detail(sess, item)
                except Exception as e2:
                    log(f"  [{idx}] ✗ {title[:40]} 详情失败: {e2}")
                    fail += 1
                    fail_streak += 1
                    append_log([ORG_NAME, title, category, pub_date, now_str(),
                                "FAILED", f"id={item_id}", "",
                                f"{ORG_NAME}+{category}+{item_id}"])
                    if fail_streak >= FAIL_STREAK_LIMIT:
                        sess = risk_pause(sess, FAIL_STREAK_PAUSE, cat_conf,
                                          f"连续失败 {fail_streak} 条，疑似触发风控")
                        fail_streak = 0
                    continue

            # 详情请求成功：计入批次，达到批量则长休息（防风控）
            batch_count += 1
            if BATCH_SIZE and batch_count >= BATCH_SIZE:
                sess = risk_pause(sess, BATCH_PAUSE, cat_conf,
                                  f"本批已处理 {batch_count} 条")
                batch_count = 0

            is_his = str(detail.get("isHis") or "0")
            file_list = detail.get("fileList") or []
            item_ok = True
            item_saved_any = False

            # Step 2a: 文件型公告
            if is_his == "0" or file_list:
                if not file_list:
                    # 无附件：记录失败（可能页面型内容缺失）
                    log(f"  [{idx}] ✗ {title[:40]} 无附件文件")
                    fail += 1
                    append_log([ORG_NAME, title, category, pub_date, now_str(),
                                "FAILED", f"id={item_id}", "",
                                f"{ORG_NAME}+{category}+{item_id}"])
                    random_sleep(REQUEST_INTERVAL)
                    continue

                # filePath 去重：所有附件文件都已存过（同一文件挂在多条记录上）则跳过下载
                item_paths = [str(f.get("filePath") or "") for f in file_list]
                real_paths = [p for p in item_paths if p]
                if real_paths and all(p in file_seen for p in real_paths):
                    log(f"  [{idx}] → {title[:40]} 文件已存过(filePath去重)，跳过")
                    skip += 1
                    downloaded.add(dedup_key)
                    append_downloaded(dedup_key)
                    continue

                for fi, finfo in enumerate(file_list, 1):
                    f_name = str(finfo.get("fileName") or "")
                    f_path = str(finfo.get("filePath") or "")
                    if not f_path:
                        continue
                    save_path = build_save_path(category, title, pub_date,
                                                f_name or title, fi, used_names)
                    dl_url = f"{DOWNLOAD_API}?path={f_path}&name={f_name}"

                    ok, real_url, err = download_file(sess, f_path, f_name, save_path)
                    if not ok and is_network_error(err):
                        log(f"  ⚠ 下载网络异常，等待恢复... ({err})")
                        if wait_for_api(sess, cat_conf):
                            ok, real_url, err = download_file(sess, f_path, f_name, save_path)

                    if ok:
                        size = os.path.getsize(save_path)
                        log(f"  [{idx}] ✓ [{item_sub_cat}] {title[:36]} 附件{fi}/{len(file_list)} ({size:,}B)")
                        success += 1
                        item_saved_any = True
                        file_seen.add(f_path)
                        append_downloaded(f"file|{f_path}")
                        append_log([ORG_NAME, title, item_sub_cat, pub_date, now_str(),
                                    "SUCCEED", real_url, save_path,
                                    f"{ORG_NAME}+{category}+{item_id}+{fi}"])
                    else:
                        log(f"  [{idx}] ✗ [{item_sub_cat}] {title[:36]} 附件{fi} 下载失败: {err}")
                        fail += 1
                        fail_streak += 1
                        item_ok = False
                        append_log([ORG_NAME, title, category, pub_date, now_str(),
                                    "FAILED", real_url, "",
                                    f"{ORG_NAME}+{category}+{item_id}+{fi}"])
                    random_sleep(REQUEST_INTERVAL)

            # Step 2b: 页面型公告（isHis=1，内容为 HTML）
            else:
                content = unescape_html_content(detail.get("belongContent") or "")
                if content.strip():
                    save_path = build_save_path(category, title, pub_date,
                                                "", 1, used_names)
                    save_path = os.path.splitext(save_path)[0] + ".html"
                    try:
                        with open(save_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        log(f"  [{idx}] ✓ {title[:40]} (HTML 内容)")
                        success += 1
                        item_saved_any = True
                        append_log([ORG_NAME, title, category, pub_date, now_str(),
                                    "SUCCEED", f"id={item_id}", save_path,
                                    f"{ORG_NAME}+{category}+{item_id}"])
                    except Exception as e:
                        log(f"  [{idx}] ✗ {title[:40]} HTML 保存失败: {e}")
                        fail += 1
                        item_ok = False
                else:
                    log(f"  [{idx}] ✗ {title[:40]} 既无附件也无内容")
                    fail += 1
                    item_ok = False
                    append_log([ORG_NAME, title, category, pub_date, now_str(),
                                "FAILED", f"id={item_id}", "",
                                f"{ORG_NAME}+{category}+{item_id}"])

            # Step 3: 本条全部成功才记入去重池
            if item_ok and item_saved_any:
                downloaded.add(dedup_key)
                append_downloaded(dedup_key)
                fail_streak = 0

            # 连续失败达到阈值：疑似风控，长休息 + 重建会话后继续
            if fail_streak >= FAIL_STREAK_LIMIT:
                sess = risk_pause(sess, FAIL_STREAK_PAUSE, cat_conf,
                                  f"连续失败 {fail_streak} 条，疑似触发风控")
                fail_streak = 0

            random_sleep(REQUEST_INTERVAL)

        # 翻页推进 + checkpoint
        begin += FETCH_NUM
        cat_state["begin"] = begin
        cat_state["updated_at"] = now_str()
        save_checkpoint(checkpoint)

        if total_num is not None and begin >= total_num:
            finished = True
            break
        if MAX_PAGES_PER_CATEGORY and page_no >= MAX_PAGES_PER_CATEGORY:
            log(f"  达到最大页数限制 {MAX_PAGES_PER_CATEGORY}，停止本栏目")
            break
        random_sleep(PAGE_INTERVAL)

    if finished:
        cat_state["done"] = True
        cat_state["updated_at"] = now_str()
        save_checkpoint(checkpoint)
        log(f"  ◀ 栏目 {category} 完成: 成功 {success}, 失败 {fail}, 跳过 {skip}")
    else:
        cat_state["updated_at"] = now_str()
        save_checkpoint(checkpoint)
        log(f"  ◀ 栏目 {category} 中止（不标记完成，下次从 begin={begin} 续跑）: "
            f"成功 {success}, 失败 {fail}, 跳过 {skip}")
    return success, fail, skip


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="广银理财信息披露爬虫（新网站）")
    parser.add_argument("--only", type=str, default="", help="只跑指定栏目名（如: 临时公告）")
    parser.add_argument("--test", type=int, default=0, help="测试模式：每栏目最多处理 N 条")
    args = parser.parse_args()

    global TEST_LIMIT_ITEMS
    if args.test > 0:
        TEST_LIMIT_ITEMS = args.test

    log("=" * 60)
    log(f"广银理财 信息披露爬虫（重写版）")
    log(f"日期区间: {START_DATE} ~ {END_DATE}")
    log(f"站点: {BASE}")
    log(f"下载目录: {DOWNLOAD_ROOT}")
    log("=" * 60)

    # 确定要跑的栏目
    if args.only:
        selected = [c.strip() for c in args.only.split(",") if c.strip() in CATEGORIES]
    elif RUN_ONLY_CATEGORY:
        selected = [RUN_ONLY_CATEGORY] if RUN_ONLY_CATEGORY in CATEGORIES else []
    else:
        selected = [c for c in CATEGORIES if ENABLE_CATEGORIES.get(c)]
    if not selected:
        log("没有可运行的栏目，退出")
        return

    log(f"本轮栏目: {', '.join(selected)}")

    os.makedirs(DOWNLOAD_ROOT, exist_ok=True)
    sess = make_session()

    # 预热：确认 API 可达
    log("检查 API 可达性...")
    if not wait_for_api(sess, max_wait=60):
        log("API 不可达，退出")
        sys.exit(1)

    downloaded, file_seen = load_downloaded()
    checkpoint = load_checkpoint()
    used_names = set()
    log(f"已加载去重记录 {len(downloaded)} 条, 已存文件 {len(file_seen)} 个")

    totals = {"success": 0, "fail": 0, "skip": 0}
    for category in selected:
        s, f_, sk = crawl_category(sess, category, CATEGORIES[category],
                                   downloaded, file_seen, checkpoint, used_names)
        totals["success"] += s
        totals["fail"] += f_
        totals["skip"] += sk

    log("")
    log("=" * 60)
    log(f"全部完成: 成功 {totals['success']}, 失败 {totals['fail']}, "
        f"跳过/去重 {totals['skip']}")
    log(f"日志: {LOG_CSV}")
    log("=" * 60)


if __name__ == "__main__":
    main()
