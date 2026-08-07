# 吉林银行 理财公告爬取

> 目标：抓取吉林银行理财产品的“销售协议书”并保存到本地，同时记录日志、断点与失败任务。

## 目录结构

- `main.py` 主程序
- `requirements.txt` 依赖列表
- `download_files/` 下载与运行产物
  - `checkpoint.json` 断点进度
  - `failed_records.csv` 失败记录（仅留痕，不用于重试）
  - `failed_tasks.json` 失败任务（用于重试）
  - `downloaded_links.txt` 来源链接去重
  - `downloaded_fingerprints.txt` 指纹去重
  - `吉行理财/`、`代销理财/` 业务分组下载目录

## 环境与依赖

推荐使用项目自带虚拟环境（示例路径：`.venv-jilin_bank`）。

安装依赖（优先 uv 工作流）：

```bash
uv pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 常用配置（main.py 顶部）

- `RUN_ONLY_CHART_TYPE`：只跑单个模块（"1" 或 "2"），为空则全跑
- `TEST_ONLY_ARTICLE_KEY`：只抓指定 articleKey（用于联调）
- `FETCH_DETAIL_PAGE_FOR_NAME`：是否访问详情页获取产品全称与协议链接
- `ENABLE_CHECKPOINT_RESUME`：断点续跑开关
- `SKIP_DOWNLOADED`：去重开关
- `LIST_PAGE_SIZE`：分页大小
- 请求/下载重试与节奏控制：`REQUEST_RETRY`、`DOWNLOAD_RETRY`、`REQUEST_INTERVAL_SECONDS` 等

## 断点机制说明

断点记录在 `download_files/checkpoint.json`，按 `chartType` 分模块保存。
中断后再次运行会从对应模块的断点继续。

## 失败重试逻辑

- 仅 `failed_tasks.json` 中的任务会被重试
- 解析不到 PDF 链接的失败不会加入 `failed_tasks.json`，只记录到 `failed_records.csv`

## 输出

- 日志：`吉林银行_日志记录.csv`
- 下载目录：`download_files/`

如需调整字段、文件命名规则或断点策略，请修改 `main.py` 中对应函数。
