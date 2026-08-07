# 光大银行产品公告 PDF 下载器

下载光大银行 (https://www.cebwm.com) 产品公告页含"发行公告"或"产品说明书"的 PDF，日期范围 2025-10-01 至今。

## 目录结构

```
ceb_crawl_standalone/
├── ceb_crawl.py          # 主脚本
├── requirements.txt      # Python 依赖
├── setup.bat             # 首次运行: 建 venv + 装依赖
├── run.bat               # 跑爬取
├── README.md             # 本文件
├── .venv/                # (setup 后自动生成) Python 虚拟环境
├── state/                # (自动) 进度 + 浏览器 profile
│   ├── ceb_progress.json
│   └── ceb_chrome_profile/
├── download_files/       # (自动) PDF 输出
│   └── 产品公告/
│       └── 发行公告_产品说明书/
└── ceb_crawl_log.csv     # (自动) 下载日志
```

## 安装

**要求**: Windows + Python 3.9+

```cmd
cd ceb_crawl_standalone
setup.bat
```

会自动:
1. 建 `.venv` 虚拟环境
2. 装 playwright + requests
3. 装 chromium (playwright 内置, 实际跑用真实 Chrome)

## 运行

### 方式 A: 复用你 Chrome 的 cookies (推荐)

如果你 Chrome 已经访问过 cebwm + 过过反爬:

1. **关掉 Chrome** (避免 profile lock)
2. 运行:
   ```cmd
   set CEB_USE_CHROME_PROFILE=1
   run.bat --max-page=3
   ```
3. 测试 OK 后:
   ```cmd
   set CEB_USE_CHROME_PROFILE=1
   run.bat
   ```

### 方式 B: 独立 profile (首次需手动过反爬)

```cmd
run.bat --max-page=3
```

首次跑会:
1. 启 Chrome 弹 list 页
2. 触发反爬 challenge, 弹出验证码
3. 脚本会**等 60s** 让用户手动过 challenge
4. 过完后自动继续

之后跑**复用已过的 cookies**, 不再触发反爬。

### 中断续跑

直接再跑 `run.bat`, 自动从 `state/ceb_progress.json` 里的 `last_completed_page + 1` 开始。

## 指令

```cmd
run.bat                    # 跑全量 (从断点续)
run.bat --reset            # 强制从头开始
run.bat --max-page=10      # 只跑 10 页 (测试)
```

## 输出

- **PDF 目录**: `download_files/产品公告/发行公告_产品说明书/`
- **下载日志**: `ceb_crawl_log.csv` (csv, 含每条记录 row_id/title/date/pdf_url/status/local_path/size/list_title)
- **进度**: `state/ceb_progress.json`

## 关键参数 (改 ceb_crawl.py 顶部)

```python
TARGET_KEYWORDS = ['发行公告', '产品说明书']  # 详情页内要下载的文件名关键词
START_DATE = '2025-10-01'                    # 起始日期 (含)
END_DATE = ''                                # 截止日期 (空=今天)
HEADLESS = False                             # True=无头模式, 反爬更严
```

## 已知特性

1. **list 页先筛**——只点含"发行公告"/"产品说明书"的 li, 节省时间
2. **详情页再筛**——只下载符合关键词的 PDF
3. **PDF 文件 dedup**——同名文件已存在则跳过
4. **断点续抓**——整页完成才更新进度, 避免重处理已完成页
5. **日期早停**——list 是倒序, 遇到早于 `START_DATE` 的 li 自动停止翻页

## 注意事项

- **首次跑会触发反爬 challenge**——保持 Chrome 窗口开启, 手动过 challenge
- **不要手动翻页**——会和脚本冲突
- **9 小时跑完全量** (取决于网络 + 反爬节奏)
- **全量 831 页 × 平均 6 PDF/页 ≈ 5000 个 PDF**
