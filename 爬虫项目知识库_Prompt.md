# 爬虫项目知识库 Prompt

## 项目概述

本项目是一个批量爬虫框架，用于从 22 家中国金融机构官网抓取理财产品信息披露公告（PDF 文件）。项目路径：`E:\Program Files\PythonProject\crawler project\zzy_crawler\`

项目由 22 个子项目组成，每个子项目对应一家金融机构，有独立的 `main.py`、独立的 `.venv`、独立的 `download_files/` 目录。所有子项目共享一个 `project_meta.py` 全局配置文件和一个 `run_all.py` 总调度脚本。

---

## 一、项目目录结构

```
zzy_crawler/
├── project_meta.py          # 全局配置（日期范围、早停开关、venv映射、共享工具函数）
├── run_all.py               # 总调度脚本，逐个运行22个爬虫
├── start_crawl.bat          # Windows 启动入口
├── requirements_classify.txt # 下游分类依赖
├── README.md                # 项目需求文档
├── A01/                     # 工银理财
├── A03/                     # 中银理财
├── A05/                     # 交银理财
├── B01/                     # 招银理财（最复杂，DrissionPage 网络包拦截）
├── B03/                     # 中信理财
├── B05/                     # 浦银理财
├── B07/                     # 民生理财（反限流最完善）
├── B09/                     # 广银理财（Playwright + PS1 调度器）
├── B12/                     # 浙商银行
├── C09/                     # 重庆农商行
├── c03/                     # 南银理财
├── c05/                     # 北银理财（纯 requests+bs4，最简）
├── c07/                     # 渤银理财（纯 requests+bs4）
├── 上海农商银行/
├── 中原银行/
├── 光大银行/                 # 最复杂：有独立子项目 ceb_crawl_standalone + Playwright
├── 吉林银行/
├── 广州银行/                 # 有补充分支 广州银行-补/
├── 杭州联合银行/
├── 桂林银行/
├── 浙银理财/                 # 浙银理财(子公司)，与B12(浙商银行)是两个项目
└── 温州银行/
```

每个子项目的标准结构：
```
<B##>/
├── main.py                  # 爬虫入口
├── requirements.txt         # 项目依赖
├── .venv-<B##>/             # 独立 Python 虚拟环境
├── download_files/          # 下载输出目录
│   ├── <公告类型>/           # 按公告类型分子目录
│   ├── downloaded.txt       # 去重记录（每行一个 progress key）
│   ├── crawl_state.json     # 或 checkpoint.json（断点续跑状态）
│   └── _unmatched_temp/     # （B01）不符合关键词的文件暂存
├── <机构名>_日志记录.csv     # 下载日志
└── .tmp/                    # 临时文件
```

---

## 二、全局配置 project_meta.py

这是所有子项目共享的配置文件，包含：

### 2.1 共享工具函数（9个项目使用）
```python
def load_checkpoint(filepath)   # 加载 checkpoint.json，失败返回 {}
def save_checkpoint(filepath, data)  # 原子写入（tmp + os.replace），重试5次
def now_str()                   # 返回 "YYYY-MM-DD HH:MM:SS"
```

### 2.2 PROJECT_VENV 字典
映射每个项目ID到其 venv 相对路径，例如：
```python
"B01": "B01/.venv-B01"
"A05": "A05/.venv-A05"
"c03": "c03/.venv"
```

### 2.3 PROJECT_ORG 字典
映射项目ID到中文机构名，例如 `"B01": "招银理财"`

### 2.4 日期范围配置
```python
PROJECT_START_DATE = {
    "B01": "2026-04-09",
    "B09": "2025-04-09",     # 广银理财起始日期更早
    "广州银行": "2026-04-02",
    "光大银行": "2026-05-23",
    # 大部分项目是 "2026-04-09"
}
PROJECT_END_DATE = ""  # 空字符串 = 到今天
```

### 2.5 早停配置
```python
EARLY_STOP_ENABLED = True  # 全局开关
EARLY_STOP_BY_PROJECT = {
    "A01": True, "A05": True, "B01": True, "B03": True,
    "B05": True, "B07": True, "B09": True,
    "吉林银行": True, "桂林银行": True, "浙银理财": True,
    # API 按时间倒序排列的项目可以安全早停
    # 静态 HTML 或无序列表的项目设为 False
}
```

早停逻辑：当 `EARLY_STOP=True` 且列表项的日期早于 `START_DATE` 时，立即停止翻页。仅适用于 API 按时间倒序返回的项目。

---

## 三、调度系统

### 3.1 总调度 run_all.py

核心功能：逐个启动 22 个爬虫，每个用自己的 venv python.exe 执行 main.py。

**CLI 参数：**
- `--only A01,B03` — 只运行指定项目
- `--skip A01` — 跳过指定项目
- `--timeout 3600` — 单项目超时时间（默认 14400 秒）

**关键实现：**
- 设置子进程环境变量 `PYTHONIOENCODING=utf-8` 和 `PYTHONUTF8=1` 防止 GBK 编码崩溃
- 捕获 stdout/stderr，生成 `run_all_报告.txt` 汇总
- 单个项目失败不阻塞后续项目

### 3.2 start_crawl.bat

```bat
cd /d "%~dp0"
python -X utf8 -u run_all.py --timeout 3600
```

### 3.3 B09 专用调度器 run_download_loop.ps1

路径：`B09\run_download_loop.ps1`，用于批量下载产品说明书。

**参数：** `-BatchSize 20`, `-WaitMinutes 5`, `-MaxRounds 100`, `-DelayMin 5`, `-DelayMax 8`

**逻辑：**
- 调用 `download_prod_manual.py --batch $BatchSize --delay-min $DelayMin --delay-max $DelayMax`
- 解析输出获取 成功/跳过/失败 计数
- 统计 `download_files\产品说明书\*.pdf` 磁盘文件数
- 连续 2 轮 0 新下载时自动停止
- 全失败轮次（>10 失败、0 成功）时三倍等待（反限流）
- 设置 `PYTHONIOENCODING=utf-8` 和 `[Console]::OutputEncoding` 解决中文管道编码

**注意：** PowerShell 脚本在中文 Windows 上必须保存为 UTF-8 BOM 编码，否则会解析失败。

### 3.4 光大银行 run_batch.ps1

无限循环运行 main.py，每批 `BATCH_MAX_SUCCESS_PRODUCTS=20`，批间休息 5 分钟。

### 3.5 A05 启动脚本 .tmp\start_main.ps1

先杀孤儿 Edge 进程、清理日志，再启动 main.py，stdout/stderr 重定向到日志文件，PID 保存到 main.pid。

---

## 四、各子项目技术栈与特点

### 技术栈分类

| 技术类型 | 项目 |
|---------|------|
| DrissionPage (Chromium) | A01, A03, B01, B03, B05, B07, B12, C09, c03, 上海农商银行, 中原银行, 吉林银行, 广州银行, 杭州联合银行, 桂林银行, 浙银理财, 温州银行 |
| DrissionPage (Edge) | A05（交银理财，用 Edge 避免与用户 Chrome 冲突） |
| Playwright | B09（广银理财）, 光大银行 |
| 纯 requests + bs4 | c05（北银理财）, c07（渤银理财） |

### 各项目关键配置速查

| 项目 | 机构 | 公告类型数 | 早停 | 关键词筛选 | 特殊 |
|------|------|----------|------|-----------|------|
| A01 | 工银理财 | 5 | ✅ | ❌ | Cookie 同步 |
| A03 | 中银理财 | - | ✅ | ❌ | 静态HTML分页 |
| A05 | 交银理财 | - | ✅ | ❌ | Edge 浏览器 |
| B01 | 招银理财 | 6 | ✅ | ✅ | 网络包拦截, 富文本转PDF |
| B03 | 中信理财 | 9 | ✅ | ❌ | 搜索API分页 |
| B05 | 浦银理财 | 12 | ✅ | ❌ | 静态JSON数据 |
| B07 | 民生理财 | 6 | ✅ | ✅ | 反限流最完善 |
| B09 | 广银理财 | 4 | ✅ | ❌ | Playwright + PS1调度 |
| B12 | 浙商银行 | 5 | ❌ | ❌ | BeautifulSoup |
| C09 | 重庆农商行 | - | - | ❌ | 多线程下载 |
| c03 | 南银理财 | - | - | ❌ | ThreadPoolExecutor |
| c05 | 北银理财 | - | - | ❌ | 最简，纯HTTP |
| c07 | 渤银理财 | - | - | ❌ | 纯HTTP |
| 光大银行 | 光大银行 | - | - | ❌ | 独立子项目 + Playwright |

---

## 五、B01 招银理财（最复杂的爬虫）

### 5.1 架构

使用 DrissionPage 的 ChromiumPage，两个浏览器实例：
- `self.page` — 主浏览器，浏览公告页面、点击 tab、拦截 API 响应
- `self.converter` — 专用浏览器，将富文本 HTML 转为 PDF（通过 CDP `Page.printToPDF`）

核心机制是**网络包拦截**：通过 `self.page.listen.start(api_path)` 开始监听，点击页面元素触发 API 请求，然后用 `self.page.listen.wait()` 捕获 JSON 响应或二进制下载字节流。

### 5.2 公告类型与 API 映射

6 个公告类型，但共用 3 个 API：
```python
LIST_API_BY_NOTICE_TYPE = {
    "产品说明书": "qryInstructionList/01",
    "产品相关协议": "qryAgreementList/01",
}
DEFAULT_LIST_API = "qryAnnouncementList/01"  # 发行公告、定期报告、到期公告、其他产品公告 共用
```

### 5.3 关键词筛选

**配置（仅对"其他产品公告"生效）：**
```python
ENABLE_KEYWORD_FILTER = True
KEYWORD_FILTER_BY_NOTICE_TYPE = {
    "其他产品公告": ("费", "费率", "业绩比较基准", "新设", "增设"),
}
```

**筛选逻辑：**
```python
def should_download_file(file_name: str, notice_type: str = "") -> bool:
    if notice_type and notice_type in KEYWORD_FILTER_BY_NOTICE_TYPE:
        keywords = KEYWORD_FILTER_BY_NOTICE_TYPE[notice_type]
        return any(kw in file_name for kw in keywords)
    return True  # 不在配置中的栏目不筛选
```

只有明确在 `KEYWORD_FILTER_BY_NOTICE_TYPE` 中配置的栏目才做关键词筛选，其他栏目全量下载。

### 5.4 Tab 切换机制（重要）

招银理财网站的 tab CSS 类名是 `.tittleItem`（注意拼写），且**不使用** `is-active`、`active`、`aria-selected` 等标准激活态 CSS 类。

因此 tab 切换采用简化流程：
1. `self.page.listen.start(list_api)` — 开始监听列表 API
2. 找到匹配的 `.tittleItem` 并点击（5 次重试，有 JS 兜底）
3. 等待 `WAIT_AFTER_TAB_CLICK_SECONDS = 3.0` 秒（用户反馈手动切 tab 要好几秒）
4. 点击搜索按钮触发新的列表请求
5. 等待 `WAIT_AFTER_SEARCH_CLICK_SECONDS = 2.0` 秒
6. 捕获 API 响应，如果捕获到则认为 tab 切换成功

**不要依赖 CSS 类判断激活态**，否则会因找不到激活元素而反复重试失败。

### 5.5 状态文件

**crawl_state.json** 格式：
```json
{
  "modules": {
    "<公告类型>": {
      "completed": false,
      "next_page": 22,
      "next_row": 0,
      "updated_at": "2026-07-23 16:36:25"
    }
  },
  "failed_items": [
    {
      "failed_id": "<md5>",
      "notice_type": "其他产品公告",
      "page_no": 61,
      "row_index": 8,
      "title": "...",
      "pub_date": "2026-07-16",
      "source_url": "...",
      "file_id": "...",
      "item": { ... },
      "last_error": "未获取到下载文件字节流",
      "updated_at": "...",
      "retry_count": 1
    }
  ]
}
```

**downloaded.txt** 格式（每行一条）：
```
<source_url>||<filename>||<product_code>||<sales_code>
```

### 5.6 下载流程

每个公告项的下载分两条路径：

1. **富文本路径**（`richText == "1"`）：取 HTML 内容，通过 `self.converter` 浏览器调用 CDP `Page.printToPDF` 转为 PDF
2. **二进制下载路径**：点击行项触发下载，拦截两个网络事件：
   - `getPrdDocumentDownloadUrl` — 获取真实下载 URL 和服务器文件名
   - `/prodDoc/download/post/` — 捕获实际二进制字节流

### 5.7 文件命名

```
{机构名}_{标题}_{公告类型}_{产品代码}_{销售代码}.pdf
```

---

## 六、B09 广银理财（Playwright + PS1 调度）

### 6.1 双脚本架构

- `main.py` — 公告爬虫，使用 Playwright（非 DrissionPage）
- `download_prod_manual.py` — 产品说明书专用下载器，纯 requests，轻量快速

### 6.2 PS1 调度器注意事项

`run_download_loop.ps1` 必须在中文 Windows 上以 UTF-8 BOM 编码保存。关键编码设置：
```powershell
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

### 6.3 年度导航

B09 使用年度按钮翻页，配置 `TARGET_YEARS = [2026, 2025, 2024]`。

---

## 七、B07 民生理财（反限流最完善）

关键反限流机制：
```python
FREQUENT_ERROR_CODE = "IGW1001"          # 频繁访问错误码
FREQUENT_RETRY_WAIT_SECONDS = 15          # 被限流后等待
FREQUENT_ERROR_COOLDOWN_SECONDS = 40      # 限流冷却期
PRODUCT_LIST_BATCH_COOLDOWN_EVERY = 20    # 每20个产品列表请求后冷却
PRODUCT_LIST_BATCH_COOLDOWN_SECONDS = 12  # 冷却12秒
SIMULATE_USER_BROWSING = True             # 模拟用户浏览（每3页停顿2-4.5秒）
# 连续失败冷却：阈值2页触发80秒冷却
# 文件API频繁冷却：120秒基础 + 25秒步进
```

进度文件最复杂，有 5 个：`checkpoint_tasks.txt`, `product_resume_state.json`, `failed_products_state.json`, `downloaded_fingerprints.txt`, `downloaded.txt`

---

## 八、通用代码模式

### 8.1 UTF-8 stdout 修复（每个 main.py 开头都有）
```python
import sys, io
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )
```

### 8.2 project_meta 导入（带 fallback）
```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from project_meta import PROJECT_START_DATE, PROJECT_END_DATE, EARLY_STOP_BY_PROJECT, load_checkpoint, save_checkpoint, now_str
except ImportError:
    # 内联 fallback 实现
    ...
```

### 8.3 日期范围过滤
```python
def is_in_date_range(pub_date: str) -> tuple:
    """返回 (in_range: bool, too_old: bool)"""
    # too_old=True 时配合 EARLY_STOP 停止翻页
```

### 8.4 去重机制
```python
def build_progress_key(source_url, filename, product_code, sales_code):
    return f"{source_url}||{filename}||{product_code}||{sales_code}"

def has_progress_hit(progress_set, ...):
    # 检查新 key、legacy key、minimal key 三种格式
```

### 8.5 Cookie 管理
大多数项目通过 DrissionPage 打开首页收集 Cookie，同步到 `requests.Session`。遇到 403/412 时刷新 Cookie。

### 8.6 CSV 日志
所有项目统一格式：
```
机构名称,公告名称,公告类型,披露日期,下载时间,状态,来源链接,保存路径,唯一键
```
状态值：`成功`, `失败`, `已跳过`, `已过滤`

---

## 九、已知问题与修复记录

### 9.1 B09 PowerShell 编码问题
**问题：** 中文 Windows 上 PowerShell 无法解析 UTF-8 无 BOM 编码的 .ps1 文件
**修复：** 必须将 .ps1 文件保存为 UTF-8 BOM 编码。可通过 Python 脚本添加 BOM：
```python
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()
with open(path, 'w', encoding='utf-8-sig') as f:
    f.write(content)
```

### 9.2 B09 Python→PowerShell 管道编码
**问题：** Python stdout 输出被 PowerShell 捕获时变成乱码
**修复：** 在 .ps1 文件中添加：
```powershell
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

### 9.3 B01 Tab 切换静默失败
**问题：** 点击 tab 后，原代码只 log 一个 warning 就继续了，实际 tab 没切换成功，抓到的是上一个 tab 的数据
**修复：** 增加重试次数到 5 次，添加 JS 点击兜底，增加等待时间到 3 秒

### 9.4 B01 CSS 激活态校验失败
**问题：** 招银理财网站不使用 `is-active`/`active`/`aria-selected` 标记激活 tab，导致 CSS 校验总返回 `no-active-found`
**修复：** 移除 CSS 类校验，改用简化流程：listen.start() → 点击 tab → 等待 → 点击搜索 → 捕获响应。如果捕获到响应就认为切换成功。

### 9.5 B01 智能引号问题
**问题：** Edit 工具在编辑 Python 文件时引入了 Unicode 智能引号（U+201C/U+201D），导致 SyntaxError
**修复：** 用 Python 脚本批量替换：
```python
content = content.replace('\u201c', '"').replace('\u201d', '"')
content = content.replace('\u2018', "'").replace('\u2019', "'")
```
注意：中文文本中的引号（如 `"公募产品公告"`）也会被误替换，需改为角括号 `「公募产品公告」`

### 9.6 B01 关键词筛选逻辑
**问题：** 早期版本的全局 `ENABLE_KEYWORD_FILTER` 会影响所有栏目
**修复：** 改为按栏目配置 `KEYWORD_FILTER_BY_NOTICE_TYPE`，只有明确配置的栏目才筛选：
```python
def should_download_file(file_name, notice_type=""):
    if notice_type and notice_type in KEYWORD_FILTER_BY_NOTICE_TYPE:
        keywords = KEYWORD_FILTER_BY_NOTICE_TYPE[notice_type]
        return any(kw in file_name for kw in keywords)
    return True
```

---

## 十、运行方式

### 运行全部项目
```bat
cd /d "E:\Program Files\PythonProject\crawler project\zzy_crawler"
python -X utf8 -u run_all.py --timeout 3600
```

### 运行单个项目
```bat
cd /d "E:\Program Files\PythonProject\crawler project\zzy_crawler"
python -X utf8 -u run_all.py --only B01
```

### 运行指定项目的单个公告类型（B01 示例）
在 B01/main.py 中设置：
```python
TEST_ONLY_NOTICE_TYPE = "其他产品公告"  # 只跑这一个类型
```
然后直接执行：
```bat
"B01\.venv-B01\Scripts\python.exe" "B01\main.py"
```

### 运行 B09 产品说明书批量下载
```powershell
powershell -ExecutionPolicy Bypass -File "B09\run_download_loop.ps1"
```

### 分类脚本（B01 其他产品公告）
```bat
"B01\.venv-B01\Scripts\python.exe" classify_b01.py
```
分类逻辑：文件名优先匹配，匹配不到再读 PDF 全文（前3页）。
关键词配置：
```python
"费率调整及费率优惠": ["费率优惠", "费率调整", "调整费率", "销售服务费费率", "优惠费率", "费率", "费"]
"新设份额": ["新设份额", "增设份额", "新设", "增设"]
"业绩比较基准调整": ["业绩比较基准调整", "调整业绩比较基准"]
"特殊案例": 匹配2个及以上不同类别关键词
```

---

## 十一、新增爬虫项目的标准步骤

1. 在 `zzy_crawler/` 下创建新目录（如 `B15/`）
2. 创建 `.venv-B15/` 虚拟环境并安装依赖
3. 编写 `main.py`，参考已有项目的模式：
   - 开头加 UTF-8 stdout 修复
   - 导入 project_meta 配置
   - 实现日期范围过滤、去重、断点续跑
   - 实现 CSV 日志记录
4. 在 `project_meta.py` 中注册：
   - `PROJECT_VENV["B15"] = "B15/.venv-B15"`
   - `PROJECT_ORG["B15"] = "某银行"`
   - `PROJECT_START_DATE["B15"] = "2026-04-09"`
   - `EARLY_STOP_BY_PROJECT["B15"] = True/False`
5. `run_all.py` 会自动识别新项目

---

## 十二、环境与依赖

- **Python**: 3.9.x（通过 uv 管理）
- **浏览器**: Chrome（大部分项目）、Edge（A05）、Chromium via Playwright（B09、光大银行）
- **核心库**: DrissionPage（浏览器自动化）、requests（HTTP）、beautifulsoup4（HTML 解析）、playwright（B09/光大银行）
- **辅助库**: openpyxl、PyMuPDF（PDF 文本提取）、pdfplumber、pypdf
- **操作系统**: Windows 10/11，中文区域，时区 Asia/Shanghai
