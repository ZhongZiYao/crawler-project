# 浦银理财信息披露爬虫（B05）

本项目用于抓取浦银理财信息披露公告，覆盖以下 13 个模块：

- 公司公告
- 发行公告
- 采购成交结果公告
- 采购信息公告
- 询证函业务公告
- 产品定期报告
- 理财业务半年度情况
- 临时公告
- 到期公告
- 代理销售机构公告
- 托管机构公告
- 其他信息披露
- 封闭式公募理财产品报告

实现特性：

- 支持模块开关与单模块测试
- 支持单条公告关键字联调
- 请求和下载失败自动重试（默认 3 次，每次间隔约 3 秒）
- 某一分页全部失败时自动等待（默认约 10 秒）
- HTML 链接自动处理：优先尝试提取可下载文件链接；否则转 PDF 保存
- 下载文件同名自动追加后缀 _1、_2...
- 按来源链接去重（已下载自动跳过）
- 成功和失败都写日志，便于回溯补采
- 同时输出 CSV 总日志和 Excel 分 Sheet 日志

## 1. 安装依赖

在 B05 目录执行：

```powershell
uv pip install -r requirements.txt
```

或：

```powershell
pip install -r requirements.txt
```

## 2. 运行

```powershell
python main.py
```

## 3. 需要手动修改的地方

已在 main.py 中标注 TODO[手动修改]，主要包括：

- INSTITUTE_NAME：机构标准名称
- ENABLE_MODULES：13 模块开关
- RUN_ONLY_MODULE_KEYS：单模块测试清单
- TEST_ONE_NOTICE_MODE / TEST_NOTICE_KEYWORD：单条公告联调
- REQUEST_RETRY / DOWNLOAD_RETRY / RETRY_WAIT_SECONDS / PAGE_NO_FILE_WAIT_SECONDS：重试与等待策略
- build_unique_key：如后续确定 unique_key 最终规则可在函数里统一改

## 4. 数据来源策略说明

本脚本按浦银理财前端逻辑进行数据汇总：

- 直接栏目 JSON（如公司公告、发行公告、其他信息披露等）
- 主数据：
  - https://www.spdb-wm.com/financialProducts/XXPL/XXPL.json
- 补充数据：
  - https://www.spdb-wm.com/financialProducts/xxpl/index.json
- 对于“产品定期报告、理财业务半年度情况”等模块，使用 TYPE 码过滤主/补充数据

## 5. 文件命名与下载目录

文件名规则：

- 机构名 + 产品名 + 公告类型 + 销售代码（如有）

目录规则：

- download_files/公告类型/

同名文件处理：

- 自动追加 _1、_2...，避免覆盖

## 6. 去重规则

- 以来源链接作为下载去重键
- 已下载链接写入 download_files/downloaded.txt
- 历史 CSV 中状态为 SUCCEED 的来源链接也会参与去重

## 7. 日志输出规则

日志文件：

- CSV：浦银理财_日志记录.csv
- Excel：浦银理财_日志记录.xlsx

Excel Sheet 命名：

- 机构名称 + 报告类型（按 Excel 31 字符限制自动裁剪）

字段：

- 机构名称
- 公告名称（完整标题，去多余空格）
- 公告类型（固定分类）
- 披露日期（YYYY-MM-DD）
- 下载时间（YYYY-MM-DD HH:MM:SS）
- 状态（SUCCEED 或 FAILED）
- 来源链接（最终下载链接或详情链接）
- 保存路径（成功为实际路径，失败为期望路径）
- unique_key（当前规则：机构名称+公告类型+公告名称+披露日期）

## 8. 说明

- 程序设计为“失败不中断”，单条失败不会影响其他条目和模块。
- 如果目标站后续改版导致某个模块数据为空，可先检查该模块 direct_json_url，再检查 TYPE 码映射。