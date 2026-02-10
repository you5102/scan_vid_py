import requests
import json
import os
import base64
import sys
from nacl import encoding, public

# 强制即时打印日志
def log(msg):
    print(f"{msg}", flush=True)

# ===================== 配置信息 =====================
BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
    "cache-control": "no-cache",
    "content-type": "text/plain;charset=UTF-8",
    "domain": "www.ip2free.com",
    "lang": "cn",
    "webname": "IP2FREE",
    "Referer": "https://www.ip2free.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
}

def update_github_secret(token, repo, secret_name, value):
    log(f"\n[Step 3] 准备同步到 GitHub Secrets...")
    auth_headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        # 获取公钥
        pk_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        pk_res = requests.get(pk_url, headers=auth_headers)
        if pk_res.status_code != 200:
            log(f"[-] 失败: 无法获取仓库公钥，代码: {pk_res.status_code}")
            return
        pk_data = pk_res.json()

        # 加密
        public_key = public.PublicKey(pk_data['key'].encode("utf-8"), encoding.Base64Encoder)
        sealed_box = public.SealedBox(public_key)
        encrypted_value = sealed_box.encrypt(value.encode("utf-8"))
        base64_value = base64.b64encode(encrypted_value).decode("utf-8")

        # 写入
        secret_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        data = {"encrypted_value": base64_value, "key_id": pk_data['key_id']}
        put_res = requests.put(secret_url, headers=auth_headers, data=json.dumps(data))
        
        if put_res.status_code in [201, 204]:
            log(f"[√] 成功！Secret '{secret_name}' 已同步更新。")
        else:
            log(f"[×] 写入失败: {put_res.status_code}")
    except Exception as e:
        log(f"[×] 更新过程崩溃: {str(e)}")

def fetch_proxies(email, password):
    proxies = []
    log(f"\n[Step 2] 正在处理账号: {email}")
    try:
        # 1. 登录
        login_res = requests.post("https://api.ip2free.com/api/account/login?", 
                                 headers=BASE_HEADERS, 
                                 data=json.dumps({"email": email, "password": password}), timeout=25)
        l_json = login_res.json()
        if l_json.get("code") != 0:
            log(f"    [-] 登录失败: {l_json.get('msg')}")
            return []
        
        token = l_json["data"]["token"]
        headers = BASE_HEADERS.copy()
        headers["x-token"] = token
        log(f"    [+] 登录成功")

        # 2. 签到逻辑
        log(f"    [*] 正在检查每日任务...")
        task_list_res = requests.post("https://api.ip2free.com/api/account/taskList?", headers=headers, data="{}", timeout=25)
        tasks = task_list_res.json().get("data", {}).get("list", [])
        
        for task in tasks:
            if "点击就送" in task.get("task_name", ""):
                if task.get("is_finished") == 0:
                    task_id = task.get("id")
                    log(f"    [*] 发现未完成签到任务: {task.get('task_name')} (ID: {task_id})")
                    finish_res = requests.post("https://api.ip2free.com/api/account/finishTask?", 
                                             headers=headers, data=json.dumps({"id": task_id}), timeout=25)
                    if finish_res.json().get("code") == 0:
                        log("    [√] 签到成功 ✅")
                    else:
                        log(f"    [×] 签到失败: {finish_res.json().get('msg')}")
                else:
                    log("    [i] 今日已签到 📅")
                break

        # 3. 抓取逻辑 (限额 + 无限)
        common_payload = json.dumps({"keyword": "", "country": "", "city": "", "page": 1, "page_size": 10})
        
        # 抓取限额列表
        log(f"    [*] 正在抓取限额列表...")
        f_res = requests.post("https://api.ip2free.com/api/ip/freeList?", headers=headers, data=common_payload, timeout=25)
        for item in f_res.json().get("data", {}).get("free_ip_list", []):
            proxies.append(f"{item.get('protocol')}://{item.get('username')}:{item.get('password')}@{item.get('ip')}:{item.get('port')}")

        # 抓取无限列表
        log(f"    [*] 正在抓取无限列表...")
        t_res = requests.post("https://api.ip2free.com/api/ip/taskIpList?", headers=headers, data=common_payload, timeout=25)
        # 注意: taskIpList 的结构层级通常比 freeList 深一层
        t_data = t_res.json().get("data", {})
        t_list = t_data.get("page", {}).get("list", []) if isinstance(t_data.get("page"), dict) else t_data.get("list", [])
        
        if t_list:
            for item in t_list:
                proxies.append(f"{item.get('protocol')}://{item.get('username')}:{item.get('password')}@{item.get('ip')}:{item.get('port')}")

        log(f"    [+] 账号处理完毕，获取到 {len(proxies)} 个代理")
        return proxies
    except Exception as e:
        log(f"    [!] 抓取过程发生异常: {str(e)}")
        return []

def main():
    log("==========================================")
    log("       IP2FREE 代理同步工具 (全功能版)     ")
    log("==========================================")
    
    gh_pat = os.environ.get("GH_PAT")
    repo = os.environ.get("GITHUB_REPOSITORY")
    acc_str = os.environ.get("IP2FREE_ACCOUNTS", "")

    log(f"[Step 1] 环境自检:")
    log(f"[*] 仓库: {repo}")
    log(f"[*] 配置账号数: {len(acc_str.split(',')) if acc_str else 0}")

    if not all([gh_pat, repo, acc_str]):
        log("[-] 错误: 关键环境变量缺失！")
        sys.exit(1)

    all_results = []
    for account in acc_str.split(","):
        if ":" in account:
            u, p = account.split(":", 1)
            all_results.extend(fetch_proxies(u.strip(), p.strip()))

    unique_list = list(set(all_results))
    log(f"\n[汇总] 抓取完成！原始总计: {len(all_results)}，去重后: {len(unique_list)}")

    if unique_list:
        # 将去重后的代理列表合并为逗号分隔的字符串
        update_github_secret(gh_pat, repo, "SOCKSPROXY", ",".join(unique_list))
    else:
        log("[-] 警告: 未获取到任何有效代理，不执行 Secret 更新。")

if __name__ == "__main__":
    main()
