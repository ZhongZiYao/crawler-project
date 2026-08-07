## 环境要求

- Python 3.8+
- 依赖库：

```bash
pip install requests beautifulsoup4
```

---

## 使用方式

### 1. 配置下载目标

编辑 `main.py` 顶部的 `PDF_TARGETS`，每条格式为：

```python
("栏目名称", "列表页URL", "模式", 起始序号, 终止序号)
```

| 字段 | 类型 | 说明 |
|------|------|------|
| 栏目名称 | `str` | 保存时的文件夹名，可自定义 |
| 列表页URL | `str` | 对应栏目第一页的地址 |
| 模式 | `str` | 见下方模式说明 |
| 起始序号 | `int` | 从第几条开始，从 `1` 开始计数 |
| 终止序号 | `int\|None` | 下载到第几条，`None` 表示下载到最后一条 |

> 序号按列表页从上到下的顺序排列，跨页连续计数。

**模式说明：**

| 模式 | 适用栏目 | 行为 |
|------|----------|------|
| `pdf` | 产品公告（发行/到期/净值/定期/临时/分红/重大事项） | 列表条目直接是 PDF 链接，直接下载 |
| `html` | 公司公告 | 进入详情页，提取"相关附件"中的 PDF 并下载 |
| `txt` | 其他公告 | 进入详情页，保存正文为 TXT，同时下载所有附件（PDF/DOCX 等） |

**示例：**

```python
PDF_TARGETS = [
    # 下载发行公告全部
    ("产品公告_发行公告", "https://.../fxgg/index.html",   "pdf",  1,    None),

    # 只下载净值公告前100条
    ("产品公告_净值公告", "https://.../jzgg/index.html",   "pdf",  1,    100),

    # 下载公司公告第51~100条
    ("公司公告",          "https://.../gsgg/index.html",   "html", 51,   100),

    # 下载其他公告全部
    ("其他公告",          "https://.../qtgg/index.html",   "txt",  1,    None),
]
```

### 2. 运行

```bash
python main.py
```

### 3. 输出结构

```
downloaded_files/
├── 产品公告_发行公告/
│   ├── 2026-02-27_北银理财京华远见春系列卓远固收封闭式294号理财产品-发行公告-....pdf
│   └── ...
├── 产品公告_净值公告/
│   └── ...
├── 公司公告/
│   ├── 2026-02-11_北银理财有限责任公司关于合作代销机构的公告（新增恒信农商银行）.pdf
│   └── ...
└── 其他公告/
    ├── 2025-05-29_关于理财账户信息查询流程的公告.txt
    └── 2025-05-29_关于理财账户信息查询流程的公告.docx
```

文件命名格式：`{日期}_{标题或附件原名}.{扩展名}`

---

## 实现原理

### 整体架构

全程使用 `requests` + `BeautifulSoup`，不需要浏览器，原因是三种类型的页面均为服务端直接渲染的静态 HTML，无需 JS 执行。

### 分页规律

所有栏目分页遵循统一规律，总页数从页面 JS 中读取：

```
第 1 页 → index.html
第 2 页 → index2.html
第 N 页 → indexN.html
总页数  → JS 中的 var maxIdx = N;
```

程序先收集全部分页的所有条目，再按起始/终止序号切片，跨页连续计数。

### 三种模式的技术路径

```
pdf 模式（产品公告）
─────────────────────────────────────────────────────
列表页 <ul.category-content-list> 中每个 <a.info-list-item>
  → href 直接是 /upload/pdf/...
  → requests 下载 PDF
  → 文件名取自 title 属性

html 模式（公司公告）
─────────────────────────────────────────────────────
列表页 → 详情页 /contents/yyyy/m/dd-HASH.html
  → 解析 .main-content-content ul li a[href*="/upload/"]
  → requests 下载所有附件（通常为 PDF）
  → 文件名取自链接文字

txt 模式（其他公告）
─────────────────────────────────────────────────────
列表页 → 详情页 /contents/yyyy/m/dd-HASH.html
  → 提取 h1 标题 + .m-advisory-release-time 时间
       + .m-advisory-news 正文 → 保存为 UTF-8 TXT
  → 同时提取 .main-content-content ul li a[href*="/upload/"]
  → requests 下载所有附件（PDF/DOCX 等）
```

### 详情页结构（公司公告 / 其他公告）

```html
<h1>公告标题</h1>
<div class="m-advisory-release-time">时间：2026-02-11</div>
<div class="m-advisory-news"><!-- 正文内容 --></div>

相关附件：
<ul>
  <li><a href="/upload/pdf/xxx.pdf">附件名称.pdf</a></li>
  <li><a href="/upload/docx/xxx.docx">附件名称.docx</a></li>
</ul>
```

---

## 注意事项

- 已下载的文件自动跳过，支持断点续跑
- 发行公告等栏目页数较多（约264页），建议用起始/终止序号分批下载
- `REQUEST_DELAY`（默认 0.5 秒）可适当调大，避免请求过于频繁