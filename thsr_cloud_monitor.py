#!/usr/bin/env python3
"""
台灣高鐵餘位監控程式 ── 雲端版 v4
改用 TDX 政府開放資料 API 查詢即時剩餘座位
- 不需要爬官網，穩定不會被擋
- StandardSeatStatus: O=有位, L=剩少量, X=售完

套件需求：pip install requests schedule
"""

import os, time, json, requests, schedule
from datetime import datetime

# ============================================================
# 站名 → TDX StationID 對照
# ============================================================
STATION_ID = {
    "南港": "0990",
    "台北": "1000",
    "板橋": "1010",
    "桃園": "1020",
    "新竹": "1030",
    "苗栗": "1035",
    "台中": "1040",
    "彰化": "1043",
    "雲林": "1047",
    "嘉義": "1050",
    "台南": "1060",
    "左營": "1070",
}

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
TDX_BASE      = "https://tdx.transportdata.tw/api/basic/v2/Rail/THSR"

# ============================================================
# 讀取設定檔
# ============================================================
def load_config():
    path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ============================================================
# TDX Token（免費申請，每天 50000 次）
# 申請：https://tdx.transportdata.tw/register
# 不填也可用，但有流量限制
# ============================================================
def get_tdx_headers():
    client_id     = os.getenv("TDX_CLIENT_ID", "")
    client_secret = os.getenv("TDX_CLIENT_SECRET", "")
    headers = {"Accept": "application/json"}

    if client_id and client_secret:
        try:
            token_resp = requests.post(
                "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token",
                data={"grant_type": "client_credentials",
                      "client_id": client_id,
                      "client_secret": client_secret},
                timeout=10,
            )
            if token_resp.status_code == 200:
                token = token_resp.json().get("access_token", "")
                headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass  # 沒 token 還是可以用，只是有流量限制

    return headers

# ============================================================
# LINE 通知
# ============================================================
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

# ============================================================
# 查詢即時剩餘座位（TDX API）
# ============================================================
def fetch_available_trains(from_station, to_station, date):
    """
    使用 TDX AvailableSeatStatus API 查詢指定日期起迄站的即時剩餘座位
    回傳有位班次清單：[{"train": "0601", "depart": "07:00", "status": "O"}]
    """
    from_id = STATION_ID.get(from_station)
    to_id   = STATION_ID.get(to_station)
    if not from_id or not to_id:
        print(f"  ⚠️  找不到站名：{from_station} / {to_station}")
        return []

    # 格式化日期 YYYY-MM-DD
    date_fmt = date.replace("/", "-")

    # 先取時刻表（含出發時間）
    timetable_url = (
        f"{TDX_BASE}/DailyTimetable/OD/{from_id}/to/{to_id}/{date_fmt}"
        f"?$format=JSON"
    )
    # 取即時剩餘座位
    seat_url = (
        f"{TDX_BASE}/AvailableSeatStatus/Train/OD/{from_id}/to/{to_id}"
        f"/TrainDate/{date_fmt}?$format=JSON"
    )

    headers = get_tdx_headers()

    try:
        tt_resp   = requests.get(timetable_url, headers=headers, timeout=15)
        seat_resp = requests.get(seat_url,      headers=headers, timeout=15)

        if tt_resp.status_code != 200:
            print(f"  ⚠️  時刻表 API 失敗({tt_resp.status_code})")
            return []
        if seat_resp.status_code != 200:
            print(f"  ⚠️  座位 API 失敗({seat_resp.status_code})")
            return []

        timetable = tt_resp.json()
        seat_data = seat_resp.json()

    except Exception as e:
        print(f"  ⚠️  API 請求錯誤：{e}")
        return []

    # 建立車次 → 出發時間對照
    depart_map = {}
    for item in timetable:
        train_no = item.get("DailyTrainInfo", {}).get("TrainNo", "")
        stops    = item.get("StopTimes", [])
        for stop in stops:
            if stop.get("StationID") == from_id:
                depart_map[train_no] = stop.get("DepartureTime", "?")
                break

    # 建立車次 → 到達時間對照
    arrive_map = {}
    for item in timetable:
        train_no = item.get("DailyTrainInfo", {}).get("TrainNo", "")
        stops    = item.get("StopTimes", [])
        for stop in stops:
            if stop.get("StationID") == to_id:
                arrive_map[train_no] = stop.get("ArrivalTime", "?")
                break

    # 解析座位狀態
    # StandardSeatStatus: O=有位, L=少量剩餘, X=售完
    available = []
    for train in seat_data:
        train_no = train.get("TrainNo", "")
        stops    = train.get("StopStations", [])
        for stop in stops:
            if stop.get("StationID") == from_id:
                status = stop.get("StandardSeatStatus", "X")
                if status in ("O", "L"):   # O=有位 L=少量
                    available.append({
                        "train":  train_no,
                        "depart": depart_map.get(train_no, "?"),
                        "arrive": arrive_map.get(train_no, "?"),
                        "status": status,
                    })
                break

    return available

# ============================================================
# 時段過濾
# ============================================================
def in_time_range(depart_str, time_from, time_to):
    try:
        t      = datetime.strptime(depart_str[:5], "%H:%M").time()
        t_from = datetime.strptime(time_from,      "%H:%M").time()
        t_to   = datetime.strptime(time_to,         "%H:%M").time()
        return t_from <= t <= t_to
    except Exception:
        return True

# ============================================================
# 主監控邏輯
# ============================================================
def make_checker(task, line_token, user_id, notified):
    label     = task["label"]
    from_s    = task["from"]
    to_s      = task["to"]
    date      = task["date"]
    time_from = task.get("time_from", "00:00")
    time_to   = task.get("time_to",   "23:59")

    def check():
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] [{label}] 查詢中...", end=" ", flush=True)

        trains    = fetch_available_trains(from_s, to_s, date)
        in_range  = [t for t in trains if in_time_range(t["depart"], time_from, time_to)]

        if not in_range:
            print(f"時段 {time_from}~{time_to} 無可訂班次，繼續監控...")
            return

        print(f"🎉 {len(in_range)} 班有票！")
        for t in in_range:
            key = f"{label}_{t['train']}_{t['depart']}"
            if key not in notified:
                notified.add(key)
                qty_label = "⚡ 剩少量" if t["status"] == "L" else "✅ 有位"
                send_line_message(
                    f"🎉 高鐵有票啦！快搶！\n"
                    f"【{label}】{qty_label}\n"
                    f"日期：{date}\n"
                    f"路線：{from_s} → {to_s}\n"
                    f"車次：{t['train']}｜出發：{t['depart']}｜到達：{t['arrive']}\n"
                    f"立刻訂票 👉 https://irs.thsrc.com.tw/IMINT/",
                    line_token, user_id
                )
                print(f"  → 車次 {t['train']} ({t['depart']}) {qty_label} 已通知")

    return check

# ============================================================
def main():
    cfg      = load_config()
    tasks    = cfg.get("tasks", [])
    interval = cfg.get("check_interval", 60)
    token    = os.getenv("LINE_CHANNEL_TOKEN", "")
    user_id  = os.getenv("LINE_USER_ID", "")
    notified = set()

    print("=" * 55)
    print("🚄  台灣高鐵餘位監控（雲端版 v4 / TDX API）  🚄")
    print("=" * 55)
    for t in tasks:
        print(f"  ・{t['label']}  {t['date']}  {t['time_from']}~{t['time_to']}")
    print(f"  查詢間隔：每 {interval} 秒")
    print("=" * 55 + "\n")

    task_summary = "\n".join(
        f"・{t['label']} {t['time_from']}~{t['time_to']}" for t in tasks
    )
    send_line_message(
        f"🚄 高鐵監控已啟動！（v4 TDX API）\n{task_summary}\n每 {interval} 秒查一次，有票馬上通知！",
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
