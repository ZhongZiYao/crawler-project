# 交银理财公告爬虫（A05）

本项目用于抓取交银理财“个人理财”产品下的信息披露公告，并下载文件到本地。

目标站点：

- 首页：https://www.bocommwm.cn/BankCommSite/shtml/jylc/cn/2503329/list.shtml?channelId=2503329
- 个人理财：https://www.bocommwm.cn/BankCommSite/shtml/jylc/cn/2503328/list.shtml?channelId=2503328

抓取模块（7 类）：

- 成立公告
- 业绩公告
- 定期公告
- 临时公告
- 重大事项公告
- 法律文本
- 到期公告

## 1. 目录结构

- main.py：主脚本
- requirements.txt：依赖
- download_files/：下载目录（运行自动创建）
- download_files/downloaded.txt：增量去重记录（运行自动创建）
- 交银理财_日志记录.csv：运行日志（运行自动创建）

## 2. 环境准备

在 A05 目录中激活虚拟环境（PowerShell）：

```powershell
.\.venv-A05\Scripts\Activate.ps1
```

安装依赖：

```powershell
pip install -r requirements.txt
```

## 3. 运行

```powershell
python main.py
```

## 4. 实现说明（关键点）

脚本复用了你之前项目的风格（日志字段、去重机制、命名规则），并按交银站点前端真实逻辑实现：

1. 产品分页：
   - `queryJylcProductInfoCount.do`
   - `queryJylcProductInfo.do`
2. 单产品公告分页：
   - `getAjaxInfoDisclosureList.do`
3. 文件下载：
   - `fileDownload.do` 先取 `filePath`
   - 按站点前端逻辑将 `filePath` 中 `cmsdata` 替换为 `BankCommSite`
   - 再用同会话 Cookie 下载二进制文件

请求报文格式与前端一致：

- `Content-Type: application/x-www-form-urlencoded`
- 表单字段：`REQ_MESSAGE={"REQ_HEAD":...,"REQ_BODY":...}`

## 5. 命名规则与日志

下载文件名：

```text
交银理财_披露日期_公告类型_公告标题.扩展名
```

重名会自动追加 `_1`、`_2`。

CSV 字段与既有项目保持一致：

- 机构名称
- 公告名称
- 公告类型
- 披露日期
- 下载时间
- 状态（SUCCEED / FAILED）
- 来源链接
- 保存路径
- unique_key

## 6. 常见问题

1. 出现 403/412：
   - 程序内置 Cookie 刷新，会自动重试。
2. 个别文件下载失败：
   - 可能是站点风控、临时链接失效或验证码策略导致。
3. 如何增量运行：
   - 保持 `SKIP_DOWNLOADED = True`，程序会根据 `download_files/downloaded.txt` 自动跳过已下载记录。

## 7. 注意事项

- 本脚本会启动 Chromium（DrissionPage）用于获取初始 Cookie。
- 若站点后续强化验证码（如行为验证码），自动下载可能失败，需要补充人工验证或浏览器态下载方案。
