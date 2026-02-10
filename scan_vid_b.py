import os
import sys
import time
import json
import random
import re
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
from cf_db import CF_VID, CF_TOKEN

# ================= 配置 =================
API_KEY = os.environ.get("API_KEY", "leaflow")
TARGET_PATTERN = os.environ.get("TARGET_PATTERN", "2PAAf74aG3D61qvfKUM5dxUssJQ9")
WORKER_VID_URL = os.environ.get("WORKER_VID_URL", "https://vid.zshyz.us.ci")
WORKER_TOKEN_URL = os.environ.get("WORKER_TOKEN_URL", "https://token.zshyz.us.ci")
RUN_DURATION_MINUTES = int(os.environ.get("RUN_DURATION_MINUTES", 10))

MAX_CONSECUTIVE_ERRORS = 3
# =======================================

# ===== UA 池（真实 JD m 端）=====
UA_POOL = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.7 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-G9910) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; M2012K11AC) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
]

stats = {
    "total": 0,
    "success": 0,
    "hit": 0,
    "error": 0,
    "context_switch": 0,
}

def log(msg, level="INFO"):
    icons = {
        "INFO": "ℹ️",
        "SUCCESS": "✅",
        "WARN": "⚠️",
        "ERROR": "❌",
        "STATS": "📊",
    }
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {icons.get(level,'•')} {msg}", flush=True)

# ===== Header 构造 =====
def build_headers(ua, vid):
    return {
        "user-agent": ua,
        "accept": "application/json,text/plain,*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://shop.m.jd.com",
        "referer": f"https://shop.m.jd.com/shop/home?venderId={vid}",
    }

# ===== 新建 Context（UA 固定）=====
def new_context(browser):
    ua = random.choice(UA_POOL)
    log(f"🔁 新建 Context | UA={ua[:50]}...", "INFO")

    ctx = browser.new_context(
        user_agent=ua,
        viewport={"width": 390, "height": 844},
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )

    # ✅ 正确做法：自己保存 UA
    ctx._ua = ua

    # 轻量“养环境”（只一次）
    try:
        page = ctx.new_page()
        page.goto("https://shop.m.jd.com", wait_until="domcontentloaded", timeout=15000)
        page.close()
    except Exception:
        pass

    stats["context_switch"] += 1
    return ctx

# ===== JD 接口请求（核心）=====
def fetch_shop_info(context, vid):
    body = (
        "functionId=whx_getShopHomeActivityInfo"
        f"&body=%7B%22venderId%22%3A%22{vid}%22%2C%22source%22%3A%22m-shop%22%7D"
        "&appid=shop_m_jd_com"
        "&clientVersion=11.0.0"
        "&client=wh5"
    )

    res = context.request.post(
        "https://api.m.jd.com/client.action",
        headers=build_headers(context._ua, vid),
        data=body,
        timeout=15000,
    )

    return res.json()

# ===== 主任务 =====
def run_task():
    db_vid = CF_VID(WORKER_VID_URL, API_KEY)
    db_token = CF_TOKEN(WORKER_TOKEN_URL, API_KEY)

    bj_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    log(f"⏰ 北京时间 {bj_now.strftime('%Y-%m-%d %H:%M:%S')}")

    vids = db_vid.get_data_slice(copy=bj_now.hour, copies=24).get("data", [])
    if not vids:
        log("无任务，退出")
        return

    start_time = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )

        context = new_context(browser)
        vid_in_ctx = 0
        error_streak = 0
        max_vid_per_ctx = random.randint(20, 30)

        for vid in vids:
            if (time.time() - start_time) / 60 >= RUN_DURATION_MINUTES:
                log("达到运行时长，停止")
                break

            stats["total"] += 1

            try:
                data = fetch_shop_info(context, vid)

                if data.get("code") == "0":
                    stats["success"] += 1
                    error_streak = 0
                    vid_in_ctx += 1

                    isv_url = data.get("result", {}).get("signStatus", {}).get("isvUrl", "")
                    if TARGET_PATTERN in isv_url:
                        token = re.search(r"token=([^&]+)", isv_url).group(1)
                        stats["hit"] += 1
                        log(f"🎯 命中店铺 {vid} | token={token}", "SUCCESS")
                        db_token.upload({"vid": vid, "token": token})
                    else:
                        log(f"店铺 {vid} 正常无活动")
                else:
                    raise RuntimeError(data.get("msg", "接口异常"))

            except Exception as e:
                stats["error"] += 1
                error_streak += 1
                log(f"店铺 {vid} 异常 ({error_streak}): {e}", "WARN")

            # 🚨 Context 更换条件
            if error_streak >= MAX_CONSECUTIVE_ERRORS or vid_in_ctx >= max_vid_per_ctx:
                context.close()
                context = new_context(browser)
                vid_in_ctx = 0
                error_streak = 0
                max_vid_per_ctx = random.randint(20, 30)

            time.sleep(random.uniform(2.8, 6.3))

        browser.close()

    log(
        f"扫描={stats['total']} | 成功={stats['success']} | 命中={stats['hit']} | "
        f"异常={stats['error']} | Context切换={stats['context_switch']}",
        "STATS",
    )

if __name__ == "__main__":
    run_task()
