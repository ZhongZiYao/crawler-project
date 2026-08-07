# 温州银行公告爬虫

本脚本用于抓取温州银行理财信息披露的 6 个栏目，支持附件下载与无附件公告富文本转PDF保存。

## 抓取范围

- 产品说明书（2458）
- 发行(成立)公告（310）
- 到期(运行)公告（311）
- 定期公告（5458）
- 其他公告（5461）
- 代销理财产品（312）

## 功能特性

- 全量分页抓取，支持断点续跑
- 详情页附件提取并下载（pdf/docx/zip/7z 等）
- 无附件公告自动按网页版式转 PDF（保留公告整体阅读效果）
- 去重机制：下载链接去重 + 指纹去重
- 日志记录：成功、失败、来源链接、保存路径

## 输出文件

- 日志：`温州银行_日志记录.csv`
- 下载目录：`download_files/`（按栏目分子目录）
- 去重文件：
  - `download_files/downloaded_links.txt`
  - `download_files/downloaded_fingerprints.txt`
- 断点：`download_files/checkpoint.json`（含页码+页内第几条）
- 失败记录：`download_files/failed_records.csv`

## 运行方式

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 运行脚本：

```bash
python main.py
```

## 常用开关（main.py）

- `RUN_ONLY_MENU_NAME`：仅抓某一栏目（空字符串为全量）
- `TEST_MODE`：调试模式
- `TEST_MENU_NAME`：调试栏目
- `TEST_PAGE_NO`：调试页码
- `SKIP_DOWNLOADED`：是否启用去重跳过
- `ENABLE_CHECKPOINT_RESUME`：是否启用断点续跑

## 说明

- 站点附件下载路径规则：`/uploadfiledownload/downloadfile/upload_file_id/{id}`
- 部分公告无附件，脚本会使用 ChromiumPage 将公告页富文本转为 PDF 保存，便于阅读。
- 文件命名规则：机构名_产品名(公告全称)_公告类型_产品代码：xxx_销售代码：xxx_产品代码补充：xxx_披露日期：yyyy-mm-dd
