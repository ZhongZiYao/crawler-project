# 中信理财公告爬虫（B03）

本项目用于抓取中信理财公开披露公告，支持 9 类公告模块开关、单模块测试、失败重试、HTML 转 PDF、下载去重和完整日志记录。

## 1. 安装依赖

在 B03 目录执行：

```powershell
pip install -r requirements.txt
```

如果你使用 `uv`，可执行：

```powershell
uv pip install -r requirements.txt
```

## 2. 运行

```powershell
python main.py
```

## 3. 你需要手动修改的地方

已在 `main.py` 中标注 `TODO[手动修改]`：

- `INSTITUTE_NAME`：机构名称（默认已设为“中信理财”）
- `REQUEST_INTERVAL_SECONDS`、`RETRY_WAIT_SECONDS`、`PAGE_NO_FILE_WAIT_SECONDS`：按目标站点情况调整
- `ENABLE_MODULES`：模块开关（True/False）
- `RUN_ONLY_MODULE_KEYS`：单模块测试名单（为空表示按开关跑全部）

示例（已支持中文配置，更方便使用）：

在 `main.py` 顶部将模块开关改为中文键：

```python
# 按中文名称开启或关闭模块（True=开启，False=关闭）
ENABLE_MODULES = {
	"发行公告": True,
	"产品净值公告": True,
	"产品定期公告": True,
	"分红公告": True,
	"产品到期报告": True,
	"其他产品公告": True,
	"产品说明书": True,
	"风险揭示书": True,
	"投资协议书": True,
}
```

单模块测试也可以使用中文名称：

```python
# 只运行发行公告模块用于测试；留空表示按 ENABLE_MODULES 运行所有已开启模块
RUN_ONLY_MODULE_KEYS = ["发行公告"]
```

说明：代码内部会把中文名称映射到程序使用的内部键（如 `发行公告` -> `issue_notice`），无需手动修改映射关系。

## 4. 公告模块

- 发行公告
- 产品净值公告
- 产品定期公告
- 分红公告
- 产品到期报告
- 其他产品公告
- 产品说明书
- 风险揭示书
- 投资协议书

## 5. 输出内容

- 下载目录：`download_files/<公告类型>/`
- 去重文件：`download_files/downloaded.txt`（按来源链接去重）
- 日志文件：`中信理财_日志记录.csv`

日志字段：

- 机构名称
- 公告名称
- 公告类型
- 披露日期（YYYY-MM-DD）
- 下载时间（YYYY-MM-DD HH:MM:SS）
- 状态（SUCCEED / FAILED）
- 来源链接
- 保存路径
- unique_key

## 6. 命名与规则

- 文件名：机构名+产品名+公告类型+销售代码（如有）
- 同名文件自动追加 `_1`、`_2` 等后缀
- 下载失败不会中断程序，失败和成功都会记录到日志
- 如果下载链接内容是 HTML，会自动转成 PDF 保存
