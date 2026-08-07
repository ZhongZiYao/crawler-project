# 民生理财信息披露爬虫（B07）

本项目用于抓取民生理财信息披露页面“产品公告-公募产品”中的公告文件，按产品逐条处理并下载。

目标页：
- https://www.cmbcwm.com.cn/xxpl/cpgg/gmcp/index.htm#tab1

## 功能特性

- 按产品逐条处理，控制台输出产品进度
- 产品列表采用流式分页：拉一页产品就处理一页，降低长链路高频请求风险
- 采用 requests + ChromiumPage 协同会话（浏览器预热、Cookie 同步、失败自动刷新 Cookie）
- 支持 6 类公告开关：发行公告、到期公告、净值公告、定期报告、重大事项公告、其他公告
- 支持单类型测试、单条公告关键字测试、单产品编码测试
- 下载失败不中断：默认重试 3 次，每次等待约 3 秒
- 分页无成功下载时额外等待：默认约 10 秒
- 频控专项处理：命中 IGW1001 时自动冷却、刷新 Cookie、重试；产品列表失败分页支持回补轮次
- 模拟用户浏览：按页间隔触发页面访问与滚动，配合随机等待降低风控命中概率
- HTML 资源处理：优先抽取页面内文档链接下载；否则转 Word(.doc) 保存
- 文件命名：机构名+产品名+公告类型+销售代码（如有）
- 来源链接去重：已下载链接会跳过
- 同名文件自动追加后缀：_1、_2...
- 断点续传：按“产品编码+公告类型”记录任务完成状态
- 成功与失败均记日志，便于回溯和补采

## 1. 安装依赖

在 B07 目录执行（推荐你常用的 uv 工作流）：

```powershell
uv pip install -r requirements.txt
```

## 2. 运行

```powershell
python main.py
```

如需明确使用你指定环境：

```powershell
.\.venv-B07\Scripts\python.exe .\main.py
```

## 3. 需要手动修改的地方

已在 main.py 中标注 TODO[手动修改]：

- INSTITUTE_NAME：机构标准名称
- PRODUCT_LIST_KEYWORD：产品查询关键词（空字符串=全量）
- TEST_ONLY_PRODUCT_CODE：只跑某个产品编码
- ENABLE_NOTICE_TYPES：公告类型开关
- RUN_ONLY_NOTICE_TYPES：只跑哪些公告类型
- TEST_ONE_NOTICE_MODE / TEST_NOTICE_KEYWORD：单条公告联调
- REQUEST_RETRY / DOWNLOAD_RETRY / RETRY_WAIT_SECONDS / PAGE_NO_FILE_WAIT_SECONDS：重试和等待参数
- FREQUENT_ERROR_CODE / FREQUENT_RETRY_WAIT_SECONDS / FREQUENT_ERROR_COOLDOWN_SECONDS：频控处理参数
- PRODUCT_LIST_FAILED_PAGE_ROUNDS / PRODUCT_LIST_BATCH_COOLDOWN_EVERY / PRODUCT_LIST_BATCH_COOLDOWN_SECONDS：分页回补与节流参数
- ENABLE_STREAM_PRODUCT_PAGING / SIMULATE_USER_BROWSING / SIMULATE_BROWSING_EVERY_PAGES：流式与模拟浏览参数
- CONSECUTIVE_FAILED_PAGES_COOLDOWN_THRESHOLD / CONSECUTIVE_FAILED_PAGES_COOLDOWN_SECONDS：连续失败冷却参数
- SKIP_DOWNLOADED / ENABLE_CHECKPOINT_RESUME：去重与断点续传策略
- build_unique_key：如 unique_key 规则后续调整，在此函数统一改

## 4. 日志字段与规则

CSV 文件：
- 民生理财_日志记录.csv

字段：
- 机构名称：统一标准名称
- 公告名称：完整标题，去多余空格
- 公告类型：固定分类
- 披露日期：YYYY-MM-DD
- 下载时间：YYYY-MM-DD HH:MM:SS
- 状态：SUCCEED 或 FAILED
- 来源链接：最终下载链接或公告链接
- 保存路径：成功为实际保存路径，失败为期望路径
- unique_key：机构名称+公告类型+公告名称+披露日期（可调整）

## 5. 去重与断点文件

- 下载去重文件：download_files/downloaded.txt
- 断点任务文件：download_files/checkpoint_tasks.txt

说明：
- 去重使用来源链接，若命中已下载链接则跳过
- 断点按“产品编码|公告类型”记录；下次运行会跳过已完成任务

## 6. 文件目录结构

下载目录：
- download_files/发行公告/
- download_files/到期公告/
- download_files/净值公告/
- download_files/定期报告/
- download_files/重大事项公告/
- download_files/其他公告/

## 7. 接口说明（当前实现）

- 产品列表：BTAProductListAll
- 文件列表：BTAFileQry
- 文件地址拼接：prefix(oldUrl/newUrl/兜底前缀) + encodeURIComponent(K_FILENAME)

## 8. 注意

- 程序设计为“失败不中断”，单条失败不会中止全局任务。
- 如果站点前端改版导致字段变化，优先检查 BTAFileQry 响应字段：
  - list
  - oldUrl/newUrl
  - URLFLAG
  - K_FILENAME
