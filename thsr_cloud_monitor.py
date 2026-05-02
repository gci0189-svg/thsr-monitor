#!/usr/bin/env python3
"""
台灣高鐵餘位監控程式 ── 雲端版 v3
- 從 config.json 讀取監控任務，支援多條路線、時段過濾
- 通知使用 LINE Messaging API

套件需求：pip install requests beautifulsoup4 schedule
"""

import os, time, json, requests, schedule
from bs4 import BeautifulSoup
from datetime import datetime

STATION_UUID = {
    "南港": "d3de6820-d1f5-4a82-a40b-638b46628ec1",
    "台北": "977abb69-413a-4ccf-a109-0272c24fd490",
    "板橋": "2f940836-cedc-4c89-b922-0db7a8741acc",
    "桃園": "d3d79b04-8a3e-4b87-96d5-50d2b4afe44e",
    "新竹": "d0346497-CBCA-4D6F-ae3f-789d6187d7d0",
    "苗栗": "d7cc55f5-d7d6-4d94-b676-a72e09dd3c5a",
    "台中": "f2519629-5973-4d08-913b-479cce78a356",
    "彰化": "d6f9ae57-c6df-4d9d-ab44-bd81399cde97",
    "雲林": "d60de246-1b53-4dca-b6fd-3ca63af0a0f0",
    "嘉義": "9c51e1dd-5500-4591-8aa3-3e9e08d3e9d1",
    "台南": "6ce89ee5-547a-4c35-9eb3-18e8f50e2e80",
    "左營": "1d0bf062-4f1c-4d3e-8d50-d4e1c2609b99",
}

THSR_URL      = "https://www.thsrc.com.tw/tw/TimeTable/SearchResult"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

def load_config():
    path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def send_line_message(text, token, user_id):
    if not token or not user_id:
        print("  ⚠️  LINE 設定未完成，跳過通知")
        return False
    try:
        resp = requests.post(
            LINE_PUSH_URL,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            data=json.dumps({"to": user_id,
                             "messages": [{"type": "text", "text": text}]}),
            timeout=10,
        )
        ok = resp.status_code == 200
        print("  ✅ LINE 通知成功" if ok else f"  ❌ 失敗({resp.status_code}): {resp.text}")
        return ok
    except Exception as e:
        print(f"  ❌ LINE 錯誤：{e}")
        return False

def fetch_trains(from_station, to_station, date, search_time="00:00"):
    from_uuid = STATION_UUID.get(from_station)
    to_uuid   = STATION_UUID.get(to_station)
    if not from_uuid or not to_uuid:
        print(f"  ⚠️  找不到站名：{from_station} / {to_station}")
        return []
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.thsrc.com.tw/ArticleContent/a3b630bb-1066-4352-a1ef-58c7b4e8ef7c",
        "Origin":  "https://www.thsrc.com.tw",
    }
    payload = {
        "StartStation": from_uuid, "EndStation": to_uuid,
        "SearchDate": date, "SearchTime": search_time,
        "SearchWay": "DepartureInMandarin", "RestTime": "", "EarlyOrLater": "",
    }
    try:
        resp = requests.post(THSR_URL, data=payload, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ⚠️  HTTP 失敗：{e}")
        return []

    soup   = BeautifulSoup(resp.text, "html.parser")
    trains = []
    rows   = soup.select("table.result-table tbody tr, .result tbody tr, #result tbody tr")
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 2:
            continue
        train_no = cols[0].get_text(strip=True)
        depart   = cols[1].get_text(strip=True)
        arrive   = cols[2].get_text(strip=True) if len(cols) > 2 else ""
        has_book = bool(row.find("a", string=lambda t: t and "訂位" in t))
        soldout  = any(kw in row.get_text() for kw in ["售完", "額滿", "候補", "SoldOut"])
        if train_no:
            trains.append({"train": train_no, "depart": depart,
                           "arrive": arrive, "available": has_book and not soldout})
    if not trains and soup.find("a", string=lambda t: t and "訂位" in t):
        trains.append({"train": "?", "depart": "?", "arrive": "?", "available": True})
    return trains

def in_time_range(depart_str, time_from, time_to):
    try:
        t      = datetime.strptime(depart_str[:5], "%H:%M").time()
        t_from = datetime.strptime(time_from,      "%H:%M").time()
        t_to   = datetime.strptime(time_to,         "%H:%M").time()
        return t_from <= t <= t_to
    except Exception:
        return True

def make_checker(task, token, user_id, notified):
    label     = task["label"]
    from_s    = task["from"]
    to_s      = task["to"]
    date      = task["date"]
    time_from = task.get("time_from", "00:00")
    time_to   = task.get("time_to",   "23:59")

    def check():
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] [{label}] 查詢中...", end=" ", flush=True)
        trains    = fetch_trains(from_s, to_s, date, time_from)
        in_range  = [t for t in trains if in_time_range(t["depart"], time_from, time_to)]
        available = [t for t in in_range if t["available"]]

        if not trains:
            print("查無結果")
            return
        if not in_range:
            print(f"時段 {time_from}~{time_to} 內無班次")
            return
        if available:
            print(f"🎉 {len(available)} 班有票！")
            for t in available:
                key = f"{label}_{t['train']}_{t['depart']}"
                if key not in notified:
                    notified.add(key)
                    send_line_message(
                        f"🎉 高鐵有票啦！快搶！\n"
                        f"【{label}】\n"
                        f"日期：{date}\n"
                        f"路線：{from_s} → {to_s}\n"
                        f"車次：{t['train']}｜出發：{t['depart']}｜到達：{t['arrive']}\n"
                        f"立刻訂票 👉 https://irs.thsrc.com.tw/IMINT/",
                        token, user_id
                    )
                    print(f"  → 車次 {t['train']} ({t['depart']}) 已通知")
        else:
            print(f"時段內 {len(in_range)} 班全售完，繼續監控...")

    return check

def main():
    cfg      = load_config()
    tasks    = cfg.get("tasks", [])
    interval = cfg.get("check_interval", 60)
    token    = os.getenv("LINE_CHANNEL_TOKEN", "")
    user_id  = os.getenv("LINE_USER_ID", "")
    notified = set()

    print("=" * 55)
    print("🚄  台灣高鐵餘位監控（雲端版 v3）  🚄")
    print("=" * 55)
    print(f"  監控任務數：{len(tasks)} 條")
    for t in tasks:
        print(f"  ・{t['label']}  {t['date']}  {t['time_from']}~{t['time_to']}")
    print(f"  查詢間隔：每 {interval} 秒")
    print("=" * 55 + "\n")

    task_summary = "\n".join(
        f"・{t['label']} {t['time_from']}~{t['time_to']}" for t in tasks
    )
    send_line_message(
        f"🚄 高鐵監控已啟動！\n{task_summary}\n每 {interval} 秒查一次，有票馬上通知！",
        token, user_id
    )

    for task in tasks:
        checker = make_checker(task, token, user_id, notified)
        checker()
        schedule.every(interval).seconds.do(checker)

    print("\n📡 監控中...（Ctrl+C 停止）\n")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
