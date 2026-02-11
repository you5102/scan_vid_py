import os
import sys
import time
import json
import random
import requests
import re
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
from cf_db import CF_VID, CF_TOKEN

# 尝试导入 stealth
try:
    from playwright_stealth import stealth_sync
except ImportError:
    def stealth_sync(page): pass

# ================= 配置 =================
API_KEY = os.environ.get("API_KEY", "leaflow")
TARGET_PATTERN = os.environ.get("TARGET_PATTERN", "2PAAf74aG3D61qvfKUM5dxUssJQ9")
WORKER_VID_URL = os.environ.get("WORKER_VID_URL", "https://vid.zshyz.us.ci")
WORKER_TOKEN_URL = os.environ.get("WORKER_TOKEN_URL", "https://token.zshyz.us.ci")
RUN_DURATION_MINUTES = int(os.environ.get("RUN_DURATION_MINUTES", 10))
MAX_CONSECUTIVE_ERRORS = 10
COPIES = int(os.environ.get("COPIES", 46))
NUM_PARTS = int(os.environ.get("NUM_PARTS", 20))
REPO = int(os.environ.get("REPO", 1))
MAX_RETRY_ROUNDS = 3  # 失败重试次数上限
# =========================================

stats = {"success": 0, "hit": 0, "blocked": 0, "error": 0, 'total_scanned': 0}
import random

def generate_device_profile():
    # 定义真实设备的物理参数库 (逻辑分辨率)
    # 格式: { "设备名": (width, height, pixel_ratio) }
    device_configs = {
        "iPhone 15/14/13 Pro": {"width": 390, "height": 844, "ratio": 3},
        "iPhone 15/14 Pro Max": {"width": 430, "height": 932, "ratio": 3},
        "Pixel 7": {"width": 412, "height": 915, "ratio": 2.6},
        "Samsung Galaxy S23": {"width": 360, "height": 800, "ratio": 3},
        "Xiaomi 13": {"width": 393, "height": 873, "ratio": 3}
    }
    
    device_name = random.choice(list(device_configs.keys()))
    config = device_configs[device_name]
    
    # 稍微给尺寸加一点“波动”（模拟某些浏览器UI占用导致的差异，可选）
    # 但通常直接使用逻辑分辨率是最稳妥的
    viewport = {
        "width": config["width"],
        "height": config["height"]
    }
    
    # 匹配对应的 UA
    if "iPhone" in device_name:
        ua = f"Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    else:
        chrome_ver = f"{random.randint(140, 146)}.0.{random.randint(6000, 7000)}.100"
        ua = f"Mozilla/5.0 (Linux; Android 14; {device_name}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Mobile Safari/537.36"
        
    return {
        "device": device_name,
        "ua": ua,
        "viewport": viewport,
        "deviceScaleFactor": config["ratio"] # 很多人会漏掉这个关键的缩放因子
    }

# 生成示例
profile = generate_device_profile()
device_scale_factor=profile['deviceScaleFactor']
print(f"模拟设备: {profile['device']}")
Viewport=profile['viewport']
print(f"Viewport: {Viewport}")
user_agent=profile['ua']
print(f"User-Agent: {user_agent}")

def log(msg, level="INFO"):
    timestamp = time.strftime("%H:%M:%S", time.localtime())
    icons = {"INFO": "ℹ️", "SUCCESS": "✅", "ERROR": "❌", "WARN": "⚠️", "STATS": "📊", "SYNC": "📡", "RAW": "📝","RISK": "🧠"}
    print(f"[{timestamp}] {icons.get(level, '•')} {msg}", flush=True)

def split_and_get_my_part(data_list):
    file_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    match = re.search(r'(\d+)$', file_name)
    script_idx = int(match.group(1)) if match else 0
    avg = len(data_list) / NUM_PARTS
    parts = [data_list[int(i * avg): int((i + 1) * avg)] for i in range(NUM_PARTS)]
    idx = (script_idx - 1) if script_idx > 0 else 0
    return parts[idx] if idx < len(parts) else []
    
def get_halved_array(data_list, repo_val):
    """
    根据 REPO 的值获取数组的前半部分或后半部分。
    repo_val: 1 表示前半部分，2 表示后半部分
    """
    length = len(data_list)
    
    # 使用整除 // 找到中点
    mid = length // 2
    
    if repo_val == 1:
        # 获取 [0, mid) 的元素
        return data_list[:mid]
    elif repo_val == 2:
        # 获取 [mid, length) 的元素
        return data_list[mid:]
    else:
        raise ValueError("REPO 的值必须是 1 或 2")
        
def cooldown_sleep(streak):
    if streak == 1:
        t = random.uniform(4, 6)
    elif streak == 2:
        t = random.uniform(8, 12)
    else:
        t = random.uniform(14, 18)
    #log(f"风控冷却 sleep {t:.1f}s", "RISK")
    time.sleep(t)

def run_task(): 
    global RUN_DURATION_MINUTES
    db_vid = CF_VID(WORKER_VID_URL, API_KEY)
    db_token = CF_TOKEN(WORKER_TOKEN_URL, API_KEY)

    # 1. 查询 IP
    try:
        current_ip = requests.get('https://api.ipify.org', timeout=10).text
        log(f"任务启动 IP: {current_ip}", "INFO")
    except: pass

    bj_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    
    # 核心：计算半小时分片
    slice_idx = bj_now.hour * 2 + (1 if bj_now.minute >= 30 else 0)
    
    log(f"⏰ 北京时间: {bj_now.strftime('%Y-%m-%d %H:%M:%S')} | 分片: {slice_idx}")

    # 这里建议确保你的环境变量 COPIES 设置为 48
    result = db_vid.get_data_slice(copy=slice_idx, copies=COPIES)
    vender_ids = split_and_get_my_part(result.get("data", []))
    vender_ids = get_halved_array(vender_ids, REPO)
    log(f"任务分配: 本分片({slice_idx}-{REPO})执行 {len(vender_ids)} 条", "INFO")
    # 最长运行时间校正，设定值减去当前超出0分或30分的分钟数，防止到0分或30分脚本不停。
    RUN_DURATION_MINUTES=RUN_DURATION_MINUTES-(bj_now.minute-30 if bj_now.minute >= 30 else bj_now.minute)
    log(f"修正分钟数({bj_now.minute-30 if bj_now.minute >= 30 else bj_now.minute})，将运行 {RUN_DURATION_MINUTES}分钟。", "INFO")

    if not vender_ids:
        return

    script_start_time = time.time()
    consecutive_errors = 0
    # 待处理队列，初始为全量，重试时仅保留失败部分
    pending_vids = vender_ids

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
            ]
        )

        context = browser.new_context(
            user_agent=user_agent,
            viewport=Viewport,
            device_scale_factor=device_scale_factor,
            is_mobile=True,
            has_touch=True,
            locale="zh-CN",
            timezone_id="Asia/Shanghai"
        )

        log("任务启动：已加载深度 Stealth 优化配置", "INFO")

        def scan_round(target_list, round_tag):
            nonlocal consecutive_errors
            round_failed = []
            
            for vid in target_list:
                stats['total_scanned'] += 1
                if (time.time() - script_start_time) / 60 >= RUN_DURATION_MINUTES:
                    log(f"达到时长上限，停止{round_tag}", "TIMER")
                    return False, round_failed

                page = context.new_page()
                stealth_sync(page)
                page.add_init_script("""
                    (() => {
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
                        Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                        const originalCanvasToDataURL = HTMLCanvasElement.prototype.toDataURL;
                        HTMLCanvasElement.prototype.toDataURL = function(type) {
                            if (type === 'image/png') {
                                const ctx = this.getContext('2d');
                                if (ctx) {
                                    ctx.fillStyle = 'rgba(255, 255, 255, 0.01)';
                                    ctx.fillRect(1, 1, 1, 1);
                                }
                            }
                            return originalCanvasToDataURL.apply(this, arguments);
                        };
                        const getParameter = WebGLRenderingContext.prototype.getParameter;
                        WebGLRenderingContext.prototype.getParameter = function(parameter) {
                            if (parameter === 37445) return 'Apple Inc.'; 
                            if (parameter === 37446) return 'Apple GPU';
                            return getParameter.apply(this, arguments);
                        };
                    })();
                """)

                try:
                    page.goto(f"https://m.jd.com", wait_until="domcontentloaded", timeout=20000)
                    page.mouse.move(random.randint(0, 100), random.randint(0, 100))
                    page.mouse.wheel(0, random.randint(500, 800))
                    time.sleep(random.uniform(1.5, 3))

                    fetch_script = f"""
                    async () => {{
                        try {{
                            const res = await fetch("https://api.m.jd.com/client.action", {{
                                "method": "POST",
                                "headers": {{ "content-type": "application/x-www-form-urlencoded" }},
                                "body": "functionId=whx_getShopHomeActivityInfo&body=%7B%22venderId%22%3A%22{vid}%22%2C%22source%22%3A%22m-shop%22%7D&appid=shop_m_jd_com&clientVersion=11.0.0&client=wh5"
                            }});
                            return await res.json();
                        }} catch (e) {{
                            return {{ code: "-1", msg: e.toString() }};
                        }}
                    }}
                    """
                    res_json = page.evaluate(fetch_script)

                    if res_json and res_json.get("code") == "0":
                        stats["success"] += 1
                        consecutive_errors = 0
                        isv_url = res_json.get("result", {}).get("signStatus", {}).get("isvUrl", "")
                        if TARGET_PATTERN in isv_url:
                            token = re.search(r'token=([^&]+)', isv_url).group(1) if "token=" in isv_url else "N/A"
                            log(f"{round_tag}{stats['total_scanned']}->🎯 命中 {vid} | Token: {token}", "SUCCESS")
                            db_token.upload({"vender": vid, "token": token})
                        else:
                            log(f"{round_tag}{stats['total_scanned']}->店铺 {vid} 正常", "INFO")
                    else:
                        stats["error"] += 1
                        consecutive_errors += 1
                        log(f"{round_tag}{stats['total_scanned']}->店铺 {vid} 异常 code {res_json.get('code') if res_json else 'None'}", "WARN")
                        round_failed.append(vid)
                        cooldown_sleep(consecutive_errors)
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            log(f"连续异常达上限，中断本轮", "ERROR")
                            return False, round_failed

                except Exception as e:
                    consecutive_errors += 1
                    stats["error"] += 1
                    log(f"{round_tag}{stats['total_scanned']}->页面崩溃 {vid}: {e}", "WARN")
                    round_failed.append(vid)
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS: return False, round_failed
                    cooldown_sleep(consecutive_errors)
                finally:
                    page.close()
                    time.sleep(random.uniform(4, 6))
            
            return True, round_failed

        # --- 核心循环重试逻辑 ---
        for attempt in range(MAX_RETRY_ROUNDS + 1):
            if not pending_vids:
                break
            
            tag = "[初次]" if attempt == 0 else f"[重试{attempt}]"
            if attempt > 0:
                log(f"🔄 开始 {tag} 扫描，剩余失败条数: {len(pending_vids)}", "STATS")
                time.sleep(5) # 轮次切换稍作休息

            is_ok, failed_list = scan_round(pending_vids, tag)
            pending_vids = failed_list # 下一轮只查这一轮失败的
            
            if not is_ok: # 如果因为时长或连续错误中断，跳出大循环
                break

        log(f"任务结束 | 总量: {len(vender_ids)} | 成功: {stats['success']} | 最终失败: {len(pending_vids)}", "STATS")
        browser.close()

if __name__ == "__main__":
    run_task()
