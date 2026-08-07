# 浙银理财公告爬虫

用于抓取浙银理财公募产品信息披露的 5 类公告：

- 发行公告
- 定期公告
- 到期公告
- 临时公告
- 其他公告

## 抓取结构

本项目站点有两种抓取方式：

- API 结构（发行/定期/到期）：
  - 列表接口：`/zylczzapi/inforDisclosure/queryPageList`
  - 下载接口：`/zylczzapi/inforDisclosure/download?id=...`
- HTML 结构（临时/其他）：
  - 直接解析列表页中的 PDF 直链并下载

接口请求固定使用公募参数 `productType = 1`。

## 依赖安装（uv）

在工作区根目录执行：

```powershell
uv pip install --python "浙银理财\.venv\Scripts\python.exe" -r "浙银理财\requirements.txt"
```

## 运行

```powershell
"浙银理财\.venv\Scripts\python.exe" "浙银理财\main.py"
```

## 常用配置（main.py）

- `RUN_ONLY_MENU_NAME`：只跑一个公告类型（推荐逐一联调）
- `TEST_MODE` / `TEST_MENU_NAME` / `TEST_PAGE_NO`：测试模式
- `MAX_PAGES`：最大页数限制（`0` 表示不限制）
- `ENABLE_CHECKPOINT_RESUME`：断点续跑
- `ENABLE_EMOJI_LOG`：控制台 emoji 输出开关

可选公告类型值：`发行公告`、`定期公告`、`到期公告`、`临时公告`、`其他公告`。

## 输出文件

- 下载目录：`download_files/`
- 成功日志：`浙银理财_日志记录.csv`
- 失败日志：`download_files/failed_records.csv`
- 去重记录：`download_files/downloaded_links.txt`、`download_files/downloaded_fingerprints.txt`
- 断点文件：`download_files/checkpoint.json`

成功日志表头：

`机构名称,公告名称,公告类型,披露日期,下载时间,状态,来源链接,保存路径,unique_key`
