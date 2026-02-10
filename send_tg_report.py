import os
import requests
from datetime import datetime, timedelta, timezone
# 假设上面的 DataWorkerClient 代码保存在 cf_db.py 中
from cf_db import CF_TOKEN 

def send_tg_msg(text):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        print("❌ 缺失 TG_BOT_TOKEN 或 TG_CHAT_ID")
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        print(f"📡 TG 发送状态: {res.status_code}")
    except Exception as e:
        print(f"❌ 发送异常: {e}")

def run_report():
    # 从变量读取配置
    API_KEY = os.environ.get("API_KEY", "leaflow")
    WORKER_TOKEN_URL = os.environ.get("WORKER_TOKEN_URL", "https://token.zshyz.us.ci")
    
    client = CF_TOKEN(WORKER_TOKEN_URL, API_KEY)
    
    # 1. 获取数据
    res_yesterday = client.get_yesterday_data()  # 昨天的
    res_today = client.get_today_data()          # 今天的（包含刚刚扫描出的）

    y_list = res_yesterday.get("data", []) if isinstance(res_yesterday, dict) else res_yesterday
    t_list = res_today.get("data", []) if isinstance(res_today, dict) else res_today

    # 2. 计算数量与新增
    count_yesterday = len(y_list)
    count_today = len(t_list)
    y_tokens = {item['token'] for item in y_list if 'token' in item}
    t_tokens = {item['token'] for item in t_list if 'token' in item}
    count_new = len(t_tokens - y_tokens)

    # 3. 根据最后一个元素判断已执行批次
    batch_info = "0"
    if t_list:
        try:
            last_item_ts = t_list[-1].get('ts_bj', '')
            # 自动处理不同长度的时间格式
            fmt = "%Y/%m/%d %H:%M:%S" if ":" in last_item_ts else "%Y/%m/%d %H:%M"
            last_dt = datetime.strptime(last_item_ts, fmt)
            
            # 计算批次：1-46 (对应半小时)
            current_batch = (last_dt.hour * 2) + (1 if last_dt.minute >= 30 else 0) + 1
            batch_info = f"{current_batch}"
        except Exception as e:
            batch_info = "计算中"

    # 4. 构造消息
    bj_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    
    msg =  f"📊 *VID 扫描任务汇总报表*\n"
    msg += f"---"
    msg += f"\n⏰ *汇报时间*: `{bj_now.strftime('%H:%M:%S')}`"
    msg += f"\n📅 *昨日 Token 总数*: `{count_yesterday}`"
    msg += f"\n📅 *今日 Token 总数*: `{count_today}`"
    msg += f"\n✨ *今日新增 Token*: `+{count_new}`"
    msg += f"\n---"
    msg += f"\n🔢 *任务进度*: 已执行 `{batch_info}/46` 批次"

    # 5. 执行打印并发送
    print(msg)
    send_tg_msg(msg) 

if __name__ == "__main__":
    run_report()
