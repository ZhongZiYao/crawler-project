r"""项目目录 -> venv 目录 真实映射表（从工作区扫出来的）

22 个项目的 venv 全部位于各自项目子目录内，例如：
  E:\Program Files\PythonProject\crawler project\zzy_crawler\A01\.venv-A01\Scripts\python.exe
  E:\Program Files\PythonProject\crawler project\zzy_crawler\c05\.venv\Scripts\python.exe
"""
import json
import os
import time
from datetime import datetime


# ============ 断点续传通用工具函数 ============
# 供 A01/A03/A05/B03/B05/C09/c03/c05/c07 等 9 个项目共用。
# 已有断点续传的 13 个项目不依赖这些函数（各自实现）。

def load_checkpoint(filepath):
    """加载 checkpoint.json，不存在或损坏时返回空 dict。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_checkpoint(filepath, data):
    """原子写入 checkpoint.json（先写 .tmp 再 os.replace，防止崩溃时文件损坏）。"""
    dir_path = os.path.dirname(filepath)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    tmp = filepath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # Windows 上可能被杀软/索引占用，重试几次
    for _ in range(5):
        try:
            os.replace(tmp, filepath)
            return
        except PermissionError:
            time.sleep(0.15)
    # 最后一次直接尝试
    try:
        os.replace(tmp, filepath)
    except Exception:
        pass


def now_str():
    """返回当前时间的标准格式字符串，用于 checkpoint 的 updated_at 字段。"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


PROJECT_VENV = {
    "A01": "A01/.venv-A01",
    "A03": "A03/.venv-A03",
    "A05": "A05/.venv-A05",
    "B01": "B01/.venv-B01",
    "B03": "B03/.venv-B03",
    "B05": "B05/.venv-B05",
    "B07": "B07/.venv-B07",
    "B09": "B09/.venv-B09",
    "C09": "C09/.venv-C09",
    "c03": "c03/.venv",
    "c05": "c05/.venv",
    "c07": "c07/.venv",
    "上海农商银行": "上海农商银行/.venv-shanghai_bank",
    "中原银行": "中原银行/.venv-zhongyuan_bank",
    "光大银行": "光大银行/.venv",
    "吉林银行": "吉林银行/.venv-jilin_bank",
    "广州银行": "广州银行/.venv-gz",
    "杭州联合银行": "杭州联合银行/.venv-hangzhou_bank",
    "桂林银行": "桂林银行/.venv-guilin_bank",
    "浙银理财": "浙银理财/.venv",
    "温州银行": "温州银行/.venv-wenzhou_bank",
}

# 项目目录 -> 发行机构
PROJECT_ORG = {
    "A01": "工银理财",
    "A03": "中银理财",
    "A05": "交银理财",
    "B01": "招银理财",
    "B03": "中信理财",
    "B05": "浦银理财",
    "B07": "民生理财",
    "B09": "广银理财",
    "C09": "渝农商理财",
    "c03": "南银理财",
    "c05": "北银理财",
    "c07": "青银理财",
    "上海农商银行": "上海农商银行",
    "中原银行": "中原银行",
    "光大银行": "光大银行",
    "吉林银行": "吉林银行",
    "广州银行": "广州银行",
    "杭州联合银行": "杭州联合银行",
    "桂林银行": "桂林银行",
    "浙银理财": "浙银理财",
    "温州银行": "温州银行",
}

# 项目目录 -> 默认起始日期（Excel 推断值）
PROJECT_START_DATE = {
    "A01": "2026-07-01",
    "A03": "2026-07-01",
    "A05": "2026-07-01",
    "B01": "2026-07-01",
    "B03": "2026-07-01",
    "B05": "2026-07-01",
    "B07": "2026-07-01",
    "B09": "2026-07-01",
    "C09": "2026-07-01",
    "c03": "2026-07-01",
    "c05": "2026-07-01",
    "c07": "2026-07-01",
    "上海农商银行": "2026-07-01",
    "中原银行": "2026-07-01",
    "光大银行": "2026-06-01",
    "吉林银行": "2026-07-01",
    "广州银行": "2026-07-01",
    "杭州联合银行": "2026-07-01",
    "桂林银行": "2026-07-01",
    "浙银理财": "2026-07-01",
    "温州银行": "2026-07-01",
}

# ============ 日期区间集中配置 ============
# 截止日期：空字符串 = 今天（运行当天）。
# 用户可在此统一修改，所有项目自动生效。
# 新一轮：2026-07-01 ~ 2026-08-01
PROJECT_END_DATE = "2026-08-01"

# 全局早停开关：遇到早于 START_DATE 的条目立即停止翻页。
# 仅对列表按日期倒序排列的项目安全启用（见下方逐项目开关）。
EARLY_STOP_ENABLED = True

# 逐项目早停开关：True=可安全早停（API倒序列表），False=仅跳过不break（静态HTML/无序）
# 根据各项目列表排序特性配置
EARLY_STOP_BY_PROJECT = {
    "A01": True,       # API POST 分页，最新在前
    "A03": False,      # 静态 HTML 分页，顺序不保证
    "A05": True,       # DrissionPage 监听数据包，最新在前
    "B01": True,       # API POST 分页
    "B03": True,       # 搜索 API，最新在前
    "B05": True,       # API，明确按 PUBDATE 倒序
    "B07": True,       # API 分页
    "B09": True,       # API 年份分页，年内倒序
    "C09": False,      # 浏览器翻页，顺序不确定
    "c03": False,      # 静态 HTML
    "c05": False,      # 单页索引切片
    "c07": False,      # 单页无分页
    "上海农商银行": False,  # 浏览器分页
    "中原银行": False,     # 产品列表先行
    "光大银行": False,     # 多标签导航
    "吉林银行": True,      # API 分页
    "广州银行": False,     # 静态 HTML 分页
    "杭州联合银行": False, # token 静态 HTML
    "桂林银行": False,     # 链接按产品编码排序非日期倒序，早停会误杀，仅跳过
    "浙银理财": True,      # API 模式倒序
    "温州银行": False,     # URL 分页静态
}
