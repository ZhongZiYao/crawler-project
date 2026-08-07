# 中原银行产品说明书爬虫

目标：抓取中原银行理财产品中的“产品说明书相关文件”，包括：

- 风险揭示及产品说明书
- 产品说明书

程序入口：main.py

## 已实现规则

1. 只抓“产品说明书相关文件”，优先抓“风险揭示及产品说明书”，失败后兜底抓“产品说明书”。
2. 如遇 HTML，自动转 Word(.doc) 保存（优先尝试从 HTML 中抽取可下载文档链接）。
3. 失败不中断，默认重试 3 次，每次间隔约 3 秒。
4. 若候选链接均失败，额外等待约 10 秒后继续下一产品。
5. 文件命名：机构名+产品名+公告类型+销售代码+披露日期。
6. 成功/失败都写主日志，便于后续回溯补全。
7. 去重优先来源链接，来源链接无法去重时再用 unique_key 指纹兜底。
8. 同名文件自动加后缀 _1、_2……
9. 支持产品级断点续传与失败任务优先回补。
10. 使用 requests + DrissionPage 同步 Cookie，降低风控导致的空响应概率。

## 目录结构

运行后会自动创建：

- download_files/产品说明书

以及：

- 中原银行_日志记录.csv
- download_files/failed_records.csv
- download_files/downloaded_links.txt
- download_files/downloaded_fingerprints.txt
- download_files/checkpoint.json
- download_files/failed_tasks.json

## 日志字段

主日志 CSV 字段与规则：

- 机构名称：中原银行
- 公告名称：完整标题，去除多余空格
- 公告类型：产品说明书
- 披露日期：YYYY-MM-DD
- 下载时间：YYYY-MM-DD HH:MM:SS
- 状态：SUCCEED / FAILED
- 来源链接：最终下载链接或详情页链接
- 保存路径：成功为实际路径，失败为期望路径
- unique_key：机构名称+公告类型+公告名称+披露日期

## 需要手动调整的位置

main.py 里已标注 TODO[手动修改]，重点包括：

- INSTITUTE_NAME
- PRODUCT_TYPE_FILTER
- TEST_ONLY_ARTICLE_KEY
- REQUEST_RETRY / DOWNLOAD_RETRY / RETRY_WAIT_SECONDS
- PAGE_NO_FILE_WAIT_SECONDS
- ENABLE_CHECKPOINT_RESUME / SKIP_DOWNLOADED
- build_unique_key（若台账规则后续调整）

## 依赖安装

推荐使用 uv：

```powershell
uv pip install -r 中原银行\requirements.txt
```

## 运行

```powershell
python 中原银行\main.py
```

或指定解释器运行：

```powershell
.\中原银行\.venv-zhongyuan_bank\Scripts\python.exe .\中原银行\main.py
```
