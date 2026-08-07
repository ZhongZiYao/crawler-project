# C09 - 渝农商理财公告爬虫（精简版）

## 当前版本说明

本版本已按需求精简：

- 只做静态网页抓取
- 支持下载 PDF / DOC / DOCX，按原始格式保存
- 不再包含 Word 相关依赖和转换逻辑
- 不再使用命令行参数配置
- 单模块测试只保留 `RUN_ONLY_MODULES`

## 核心功能

- 4 个模块抓取：发行公告、运作公告、到期公告、净值公告
- 分页抓取：`index.html`、`index_2.html`...
- 下载失败不终止
- 请求/下载默认重试 3 次，失败等待 3 秒
- 当前页未成功下载时等待 10 秒再翻页
- 链接去重（规范化 URL 后比较）
- 文件同名时自动 `_1`、`_2` 后缀
- 成功和失败都写入下载记录 CSV

## 手动配置

请在 [C09/main.py](C09/main.py) 顶部修改：

- `INSTITUTE_NAME`
- `ENABLE_MODULES`
- `RUN_ONLY_MODULES`
- `REQUEST_TIMEOUT`
- `REQUEST_RETRY`
- `DOWNLOAD_RETRY`
- `RETRY_WAIT_SECONDS`
- `NO_PDF_WAIT_SECONDS`
- `MAX_PAGES_PER_MODULE`

说明：

- 单模块测试只需设置：
  - `RUN_ONLY_MODULES = ["发行公告"]`
- 跑全部模块时：
  - `RUN_ONLY_MODULES = []`
  - 并使用 `ENABLE_MODULES` 控制开关

## 日志字段

下载记录文件：`{机构名称}_下载记录.csv`

字段：

- 机构名称
- 公告名称
- 公告类型
- 披露日期（YYYY-MM-DD）
- 下载时间（YYYY-MM-DD HH:MM:SS）
- 状态（SUCCEED / FAILED）
- 来源链接
- 保存路径（失败时记录期望路径）
- unique_key（机构名称+公告类型+公告名称+披露日期）

## 环境安装

```powershell
cd "E:\Program Files\PythonProject\crawler project\c01-c08\C09"
.\.venv-C09\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 运行

```powershell
python main.py
```
