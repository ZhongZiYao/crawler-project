import os
import time
import json
from DrissionPage import ChromiumPage, ChromiumOptions

# 配置目标页面，输出文件路径
BROWSER_INIT = "https://www.nanyinwealth.com/nanyinwealth/zcdl/dl/index.html"
OUTPUT_PATH = "mac_chrome_session_dump.txt"

def main():
    # 初始化无头Chromium
    opt = ChromiumOptions()
    opt.headless(True)
    opt.set_argument("--disable-images")
    # 也可添加更多 opt.set_argument，用于模拟真实环境

    browser = ChromiumPage(addr_or_opts=opt)
    try:
        print("加载页面...")
        browser.get(BROWSER_INIT)
        time.sleep(3)
        # 抓取cookie
        cookies = browser.cookies()
        cookies_dict = {c["name"]: c["value"] for c in cookies}

        # 抓取User-Agent
        user_agent = browser.run_js('return navigator.userAgent;')

        # 抓取localStorage/sessionStorage所有内容
        local_storage = browser.run_js(
            'return Object.assign({}, window.localStorage);'
        )
        session_storage = browser.run_js(
            'return Object.assign({}, window.sessionStorage);'
        )

        # 列出所有document.cookie（有时候cookie通过document.cookie设置而不是set-cookie响应头）
        cookie_str = browser.run_js('return document.cookie;')

        # 抓取页面HTML（必要时，便于分析反爬信息）
        html_content = browser.html

        # 输出所有信息到txt文件
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write("== COOKIE DUMP ==\n")
            f.write(json.dumps(cookies_dict, ensure_ascii=False, indent=2))
            f.write("\n\n== User-Agent ==\n")
            f.write(str(user_agent))
            f.write("\n\n== localStorage ==\n")
            f.write(json.dumps(local_storage, ensure_ascii=False, indent=2))
            f.write("\n\n== sessionStorage ==\n")
            f.write(json.dumps(session_storage, ensure_ascii=False, indent=2))
            f.write("\n\n== document.cookie ==\n")
            f.write(str(cookie_str))
            f.write("\n\n== HTML HEAD (first 2000 chars) ==\n")
            f.write(html_content[:2000])
        print(f"所有会话数据已输出至 {OUTPUT_PATH}")
    finally:
        browser.quit()

if __name__ == "__main__":
    main()