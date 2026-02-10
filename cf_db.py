import requests
import json
from datetime import datetime, timedelta, timezone

class CF_VID:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        auth_val = f"Bearer {api_key}" if not api_key.startswith("Bearer ") else api_key
        self.session.headers.update({"Authorization": auth_val, "Content-Type": "application/json"})

    def get_data_slice(self, copy: int, copies: int):
        url = f"{self.base_url}/get"
        try:
            res = self.session.post(url, json={"copy": copy, "copies": copies}, timeout=15)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            print(f"❌ VID获取异常: {e}")
            return {"data": []}

class CF_TOKEN:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        auth_val = f"Bearer {api_key}" if not api_key.startswith("Bearer ") else api_key
        self.session.headers.update({"Authorization": auth_val, "Content-Type": "application/json"})
        self.beijing_tz = timezone(timedelta(hours=8))

    def upload(self, data: dict):
        # 注意：这里请根据你 Worker 的逻辑改为 /add 或 /update
        url = f"{self.base_url}/upload" 
        try:
            res = self.session.post(url, json=data, timeout=15)
            return {"code": res.status_code, "body": res.text, "ok": res.status_code == 200}
        except Exception as e:
            return {"code": 500, "body": str(e), "ok": False}

    def _get_bj_now(self):
        """无论系统时区是什么，始终返回当前的北京时间对象"""
        return datetime.now(timezone.utc).astimezone(self.beijing_tz)

    def _format_date(self, dt_obj):
        """格式化为 MM_DD"""
        return dt_obj.strftime("%m_%d")

    def get_today_data(self):
        """获取北京时间的今天数据"""
        date_str = self._format_date(self._get_bj_now())
        return self._fetch(date_str)

    def get_yesterday_data(self):
        """获取北京时间的昨天数据"""
        yesterday_obj = self._get_bj_now() - timedelta(days=1)
        date_str = self._format_date(yesterday_obj)
        return self._fetch(date_str)

    def _fetch(self, date_str):
        """底层请求函数"""
        url = f"{self.base_url}/get"
        try:
            print(f"🔍 正在查询北京时间 {date_str} 的数据...")
            response = self.session.get(url, params={"date": date_str}, timeout=10)
            return response.json() if response.status_code == 200 else []
        except Exception as e:
            print(f"Get Error: {e}")
            return []
