# 工银理财公告爬虫使用说明

本项目用于抓取工银理财公开披露公告（发行公告、定期报告、临时性信息披露、到期公告），按公告类型分目录下载文件，并记录 CSV 日志与去重进度。

## 1. 项目结构

- `main.py`：主脚本
- `requirements.txt`：依赖列表
- `download_files/`：下载目录（运行时自动创建）
- `download_files/downloaded.txt`：去重进度文件（运行时自动创建）
- `工银理财_日志记录.csv`：运行日志（运行时自动创建）

## 2. 抓取目标

脚本支持以下四类公告：

- `issuReport`：发行公告（`/issuReport`）
- `perReport`：定期报告（`/perReport`）
- `tempInfoDisclosure`：临时性信息披露（`/tempInfoDisclosure`）
- `expireNotice`：到期公告（`/expireNotice`）

对应接口：

- `https://wm.icbc.com.cn/clt/info/112501`
- `https://wm.icbc.com.cn/clt/info/112601`
- `https://wm.icbc.com.cn/clt/info/112801`
- `https://wm.icbc.com.cn/clt/info/112701`

下载接口：

- `https://wm.icbc.com.cn/file/platform/downloadfile?resource_id=...`

Cookie 获取首页：

- `https://wm.icbc.com.cn/`

## 3. 环境准备

在 `A01` 目录执行：

```powershell
pip install -r requirements.txt
```

## 4. 运行方式

在 `A01` 目录执行：

```powershell
python main.py
```

运行流程：

1. 打开首页 `https://wm.icbc.com.cn/` 获取 Cookie
2. 创建 `requests.Session` 并带 Cookie 请求列表接口
3. 按分页遍历接口返回 `rows`
4. 提取 `resource_id` 与文件名，访问下载接口保存文件
5. 将下载结果写入 `工银理财_日志记录.csv`
6. 使用 `downloaded.txt` 做增量去重

## 5. 参数说明（main.py 顶部）

### 抓取范围

- `CATEGORY_CODES = "issuReport,perReport,tempInfoDisclosure,expireNotice"`
  - 抓全部分类
  - 若只抓发行公告可改为：`"issuReport"`

- `PAGE_SIZE = 10`
  - 每页抓取条数

- `MAX_PAGES_PER_CATEGORY = 3000`
  - 每类最大页数保护阈值

- `MAX_CONSECUTIVE_EMPTY = 3`
  - 连续空页/失败达到阈值后停止该分类

### 下载与去重

- `DOWNLOAD_ROOT = os.path.join(SCRIPT_DIR, "download_files")`
- `LOG_CSV_PATH = os.path.join(SCRIPT_DIR, "工银理财_日志记录.csv")`
- `SKIP_DOWNLOADED = True`
  - `True`：跳过已下载记录（推荐）
  - `False`：允许重复下载（重名会自动追加后缀）

### 反爬与稳定性

- `REQUEST_RETRY = 3`
- `TIMEOUT = 30`
- `REQUEST_DELAY_RANGE = (0.5, 1.5)`
- `LIST_PAGE_DELAY_RANGE = (0.8, 1.8)`
- `COOKIE_READY_WAIT = 4`

脚本已内置 403/412 的 Cookie 刷新与重试。

## 6. 输出说明

### 6.1 按公告类型分目录

下载目录结构如下：

```text
download_files/
  发行公告/
  定期报告/
  临时性信息披露/
  到期公告/
```

### 6.2 文件命名规则

```text
工银理财_披露日期_公告类型_公告标题.pdf
```

说明：

- 实际扩展名以 `resource_id` 或 `file_name1` 推断为准，不一定都是 `.pdf`
- 如返回 `.docx` 等类型，也会按原扩展名保存
- 重名时自动追加 `_1`、`_2`...

### 6.3 CSV 字段

日志文件 `工银理财_日志记录.csv` 字段：

- `机构名称`
- `公告名称`
- `公告类型`
- `披露日期`
- `下载时间`
- `状态`（`SUCCEED` / `FAILED`）
- `来源链接`
- `保存路径`
- `unique_key`

## 7. 常见问题

### Q1：为什么会出现 403/412？

可能触发站点风控。脚本会自动重新访问首页刷新 Cookie 并重试。

### Q2：如何做增量抓取？

保持 `SKIP_DOWNLOADED = True`，程序会根据 `download_files/downloaded.txt` 自动跳过已下载记录。

### Q3：如何全量重跑？

两种方式：

1. 将 `SKIP_DOWNLOADED = False`
2. 删除 `download_files/downloaded.txt` 后再运行

## 8. 注意事项

- 首次运行会启动 Chromium 浏览器用于 Cookie 获取。
- 请确保本机已安装可用的 Chrome/Chromium 内核环境。
- 本脚本仅访问公开披露信息并保存本地文件。
