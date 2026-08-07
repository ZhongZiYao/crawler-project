# 上海农商银行信息披露爬虫

目标：抓取上海农商银行理财信息披露中的两个模块文件。

- 板块1：鑫意理财
- 板块2：代销理财
- 每个板块下抓取：产品说明书、产品公告

程序入口：main.py

## 已实现规则

1. 如遇 HTML 文件，自动转为 Word(.doc) 保存（优先尝试从 HTML 里提取文档链接下载）。
2. 请求/下载失败不中断，默认重试 3 次，每次间隔约 3 秒。
3. 公告分页结果为空时，额外等待约 10 秒后继续。
4. 文件命名规则：机构名+产品名+公告类型+销售代码+披露日期。
5. 成功和失败都写日志，便于回溯和补全。
6. 去重优先用来源链接，来源链接不可用时用 unique_key 指纹兜底。
7. 同名文件自动加后缀 _1、_2……
8. 断点续传：按板块记录下一页，从中断位置继续。
9. 使用 requests + DrissionPage 同步 Cookie，降低风控导致的空响应概率。

## 目录结构

运行后会自动创建：

- download_files/鑫意理财/产品说明书
- download_files/鑫意理财/产品公告
- download_files/代销理财/产品说明书
- download_files/代销理财/产品公告

以及：

- 上海农商银行_日志记录.csv
- download_files/failed_records.csv
- download_files/downloaded_links.txt
- download_files/downloaded_fingerprints.txt
- download_files/checkpoint.json

## 日志字段

主日志 CSV 字段与规则：

- 机构名称：上海农商银行
- 公告名称：完整标题，去除多余空格
- 公告类型：产品说明书 / 产品公告
- 披露日期：YYYY-MM-DD
- 下载时间：YYYY-MM-DD HH:MM:SS
- 状态：SUCCEED / FAILED
- 来源链接：最终下载链接或详情页链接
- 保存路径：成功为实际路径，失败为期望路径
- unique_key：机构名称+公告类型+公告名称+披露日期

## 需要手动调整的位置

main.py 里已标注 TODO[手动修改]，重点包括：

- INSTITUTE_NAME
- RUN_ONLY_BOARD / TEST_ONLY_PRODUCT_CODE
- TEST_ONE_NOTICE_MODE / TEST_NOTICE_KEYWORD
- REQUEST_RETRY / DOWNLOAD_RETRY / RETRY_WAIT_SECONDS
- PAGE_NO_FILE_WAIT_SECONDS
- LIST_PAGE_SIZE / NOTICE_PAGE_SIZE
- ENABLE_CHECKPOINT_RESUME / SKIP_DOWNLOADED
- build_unique_key（若台账规则后续调整）
- resolve_book_disclose_date（若后续明确说明书披露字段）

## 依赖安装

在项目根目录执行（推荐 uv）：

```powershell
uv pip install -r 上海农商银行\requirements.txt
```

## 运行

```powershell
python 上海农商银行\main.py
```

或指定解释器运行：

```powershell
.\上海农商银行\.venv-shanghai_bank\Scripts\python.exe .\上海农商银行\main.py
```
