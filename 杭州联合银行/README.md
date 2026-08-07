# 杭州联合银行 爬虫说明

## 项目简介
- 本项目用于抓取杭州联合银行官网的公告（列表页 → 详情页 → PDF/HTML 下载），支持断点续爬、重试、PDF 文本抽取并根据产品/销售代码重命名文件。控制台输出为 emoji 丰富的进度提示，方便人工监控。

## 目录（关键文件）
- 主脚本：[杭州联合银行/main.py](杭州联合银行/main.py)
- 依赖：[杭州联合银行/requirements.txt](杭州联合银行/requirements.txt)
- 下载与检查点目录：`download_files/`（脚本运行后生成，用于保存已下载文件与 checkpoint）

## 主要特性
- 支持分页和多策略分页 token 检测（DOM / onclick / 固定映射）
- 每条记录处理后保存 checkpoint（包含 next_page、next_index、last_detail_url）以便续爬
- 自动下载 PDF/HTML，并用从标题/详情页或 PDF 文本中抽取到的 `产品代码` 与 `销售代码` 重命名文件
- 使用 `pdfplumber` 做 PDF 文本抽取（已替换早期不可用的 pypdf）
- 可配置只运行单个公告模块以便调试（single-module 运行）

## 环境与依赖
建议在项目专用虚拟环境中运行（示例使用 PowerShell）：

```powershell
# 创建并激活虚拟环境（若你已使用已有 venv，可跳过）
python -m venv .venv-hangzhou_bank
& .\.venv-hangzhou_bank\Scripts\Activate.ps1

# 安装依赖
pip install -r "杭州联合银行\requirements.txt"
```

说明：项目已改用 `pdfplumber` 做 PDF 文本抽取，如果遇到 `pypdf` 导入错误，不需要处理，直接使用仓库中的 `requirements.txt` 安装即可。

## 快速开始
在激活的虚拟环境中运行：

```powershell
python "杭州联合银行\main.py"
```

脚本会按配置抓取所有已启用的公告模块（例如 成立公告、变更公告 等），中途可通过生成的 checkpoint 文件续爬。

### 单模块调试
如果只想测试某一类公告（单模块运行），打开 [杭州联合银行/main.py](杭州联合银行/main.py)，找到变量 `RUN_ONLY_CATEGORIES` 并设置为你想运行的模块键（例如 `['clgg']`）。保存后重新运行 `python 杭州联合银行\main.py` 即可只抓该模块，便于快速调试。

## 重要配置说明
- `RUN_ONLY_CATEGORIES`：列表，指定要运行的模块 key（空或 None 表示运行全部）。
- `PDF_SCAN_PAGES`：控制从 PDF 中扫描页数以查找产品/销售代码，默认为前若干页；可调整以增加扫描深度。
- checkpoint（断点文件）：脚本会在运行目录下或 `download_files/` 中写入 checkpoint（JSON），保存当前每个模块的 next_page/next_index/last_detail_url，脚本重启会优先读取该文件续爬。

## 输出位置
- 下载的公告 PDF/HTML 保存在 `download_files/`，按公告类型和时间组织子目录。重命名后文件名会包含 `产品代码:xxx` 和 `销售代码:yyy`（如果能抽取到）。

## 常见问题
- Q: 运行时报错找不到 `pypdf`？
  - A: 已改用 `pdfplumber`，请确保用 `requirements.txt` 安装依赖；若仍报错，请检查虚拟环境是否正确激活。
- Q: PDF 中没有识别到产品/销售代码怎么办？
  - A: 默认只扫描 PDF 前几页以提高速度。如需更深度扫描，可调整 `PDF_SCAN_PAGES` 或修改 `extract_codes_from_pdf()` 为“逐页直至匹配或 EOF”。

## 调试建议
- 先用 `RUN_ONLY_CATEGORIES` 只运行一个模块观察控制台输出与 checkpoint；确认无误后再放开全部模块运行。
- 如果发现分页无法继续，请检查日志中输出的分页 token 与页面样例，脚本内实现了多策略检测与固定映射作为回退。




