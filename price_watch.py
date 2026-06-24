# -*- coding: utf-8 -*-
"""
price_watch.py — 워치리스트 통합 시세 감시 + 텔레그램 알림

기능
  1) 하한가 알림   : 종목 가격이 'floor'(원하는 금액) 밑으로 내려오면 (기준선 통과 시 1회)
  2) 급변동 알림   : 전일대비 |등락률| >= MOVE_PCT(기본 10%) 이면 (하루 1회)
  3) 아침 브리핑   : 워치리스트 전체 가격+등락률을 한 메시지로 (report 모드)

실행
  python price_watch.py            # 감시 1회 (하한가/급변동 체크)
  python price_watch.py report     # 아침 브리핑 발송
  python price_watch.py id         # 텔레그램 chat_id 확인

설정 우선순위: 환경변수 > 파일 기본값.  표준 라이브러리만 사용(설치 불필요).
"""

import sys, os, json, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

# ===== 기본 설정 (환경변수로도 주입 가능) =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "여기에_봇토큰_붙여넣기")
CHAT_ID   = os.environ.get("CHAT_ID",   "여기에_챗ID_붙여넣기")
MOVE_PCT  = float(os.environ.get("MOVE_PCT", "10"))   # 급변동 기준 (%)

# ===== 워치리스트: 여기만 본인 종목으로 채우면 됨 =====
#  type : "metal"(금) | "exchange"(환율) | "kr"(국내주식) | "us"(해외주식)
#  code : 금=M04020000 / 환율=FX_USDKRW 등 / 국내=6자리(삼성 005930) / 해외=NVDA.O, AAPL.O 등(.O=나스닥,.N=뉴욕)
#  floor: 이 가격 밑이면 알림. 알림 필요없으면 None
#  unit : 표시 단위
WATCHLIST = [
    {"name": "금(국내)",   "type": "metal",    "code": "M04020000", "floor": 200000, "unit": "원/g"},
    {"name": "원/달러",    "type": "exchange", "code": "FX_USDKRW", "floor": None,   "unit": "원"},
    {"name": "삼성전자",   "type": "kr",       "code": "005930",    "floor": None,   "unit": "원"},
    {"name": "엔비디아",   "type": "us",       "code": "NVDA.O",    "floor": None,   "unit": "$"},
    # 예) {"name": "SK하이닉스", "type": "kr", "code": "000660", "floor": 150000, "unit": "원"},
    # 예) {"name": "애플",       "type": "us", "code": "AAPL.O", "floor": 200,    "unit": "$"},
]
# ====================================================

KST        = timezone(timedelta(hours=9))
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "watch_state.json")
LOG_FILE   = os.path.join(BASE_DIR, "price_watch.log")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}

PRICE_KEYS = ["closePrice", "nv", "nowVal", "close_val", "closeVal"]
RATIO_KEYS = ["fluctuationsRatio", "cr", "changeRate", "fluctuationRate"]
AMT_KEYS   = ["compareToPreviousClosePrice", "fluctuations", "cv", "change_val", "changePrice"]


def now_kst():
    return datetime.now(KST)


def log(msg):
    line = f"[{now_kst():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _to_float(x):
    return float(str(x).replace(",", "").replace("%", "").strip())


def _find_key(obj, keys):
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return obj[k]
        for v in obj.values():
            r = _find_key(v, keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, keys)
            if r is not None:
                return r
    return None


def _candidate_urls(item):
    t, code = item["type"], item["code"]
    if t == "metal":
        return [f"https://api.stock.naver.com/marketindex/metals/{code}/prices?pageSize=2"]
    if t == "exchange":
        return [f"https://api.stock.naver.com/marketindex/exchange/{code}/prices?pageSize=2"]
    if t == "kr":
        return [f"https://m.stock.naver.com/api/stock/{code}/basic",
                f"https://api.stock.naver.com/stock/{code}/basic",
                f"https://m.stock.naver.com/api/stock/{code}/integration"]
    if t == "us":
        return [f"https://api.stock.naver.com/stock/{code}/basic",
                f"https://m.stock.naver.com/api/stock/{code}/basic"]
    return []


def _parse_quote(data):
    p = _find_key(data, PRICE_KEYS)
    if p is None:
        return None
    price = _to_float(p)
    pct = None
    ratio_raw = _find_key(data, RATIO_KEYS)
    if ratio_raw is not None:
        try:
            pct = abs(_to_float(ratio_raw))
            if _to_float(ratio_raw) < 0:      # 등락률 자체가 부호를 가진 경우
                pct = -pct
        except Exception:
            pct = None
    amt_raw = _find_key(data, AMT_KEYS)        # 등락폭 부호로 방향 보정
    if pct is not None and amt_raw is not None:
        try:
            if _to_float(amt_raw) < 0:
                pct = -abs(pct)
            elif _to_float(amt_raw) > 0:
                pct = abs(pct)
        except Exception:
            pass
    return price, pct


def get_quote(item):
    last_err = None
    for url in _candidate_urls(item):
        try:
            data = json.loads(_get(url))
            q = _parse_quote(data)
            if q is not None:
                log(f"{item['name']}: {q[0]:,} (등락 {q[1]}) ← {url.split('//')[1].split('/')[0]}")
                return q
        except Exception as e:
            last_err = e
    raise RuntimeError(f"시세 조회 실패 ({item['name']}): {last_err}")


def link_for(item):
    t, code = item["type"], item["code"]
    if t in ("metal", "exchange"):
        kind = "metals" if t == "metal" else "exchange"
        return f"https://m.stock.naver.com/marketindex/{kind}/{code}"
    if t == "kr":
        return f"https://m.stock.naver.com/domestic/stock/{code}/total"
    if t == "us":
        return f"https://m.stock.naver.com/worldstock/stock/{code}/total"
    return "https://m.stock.naver.com"


def fmt_price(item, price):
    if item["type"] == "us":
        return f"${price:,.2f}"
    if item["type"] == "exchange":
        return f"{price:,.2f}{item.get('unit', '')}"
    return f"{price:,.0f}{item.get('unit', '')}"


def fmt_chg(pct):
    if pct is None:
        return "등락 N/A"
    arrow = "\u25B2" if pct > 0 else ("\u25BC" if pct < 0 else "\u2013")
    return f"{arrow}{abs(pct):.2f}%"


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {resp}")
    return resp


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("items", {})
    s.setdefault("error_notified", False)
    return s


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0)


def item_key(item):
    return f"{item['type']}:{item['code']}"


def print_chat_id():
    data = json.loads(_get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"))
    found = set()
    for u in data.get("result", []):
        chat = (u.get("message") or u.get("edited_message") or {}).get("chat", {})
        if chat.get("id") is not None:
            found.add((chat["id"], chat.get("first_name") or chat.get("title") or ""))
    if found:
        print("찾은 chat_id:")
        for cid, name in found:
            print(f"  {cid}   ({name})")
    else:
        print("업데이트 없음. 봇에게 메시지를 먼저 보낸 뒤 다시 실행하세요.")


def run_monitor():
    state = load_state()
    today = f"{now_kst():%Y-%m-%d}"
    results, ok = [], 0
    for item in WATCHLIST:
        try:
            price, pct = get_quote(item)
            results.append((item, price, pct))
            ok += 1
        except Exception as e:
            log(str(e))
            results.append((item, None, None))

    if ok == 0:
        log("전체 조회 실패 — IP 차단 가능성")
        if not state["error_notified"]:
            try:
                send_telegram("⚠️ 시세 감시 봇: 네이버 조회에 전부 실패했어요. "
                              "(IP 차단/구조 변경 가능) 실행 로그를 확인하세요.")
            except Exception:
                pass
            state["error_notified"] = True
            save_state(state)
        sys.exit(1)
    state["error_notified"] = False

    for item, price, pct in results:
        if price is None:
            continue
        st = state["items"].setdefault(item_key(item), {})

        floor = item.get("floor")
        if floor:
            if price < floor and st.get("floor_state") != "below":
                send_telegram(f"\U0001F4C9 {item['name']} 하한가 알림\n\n"
                              f"기준가 아래로 내려왔어요.\n"
                              f"현재: {fmt_price(item, price)} ({fmt_chg(pct)})\n"
                              f"기준: {floor:,.0f}{item.get('unit','')}\n"
                              f"{now_kst():%m-%d %H:%M}\n{link_for(item)}")
                log(f"→ {item['name']} 하한가 알림 전송")
                st["floor_state"] = "below"
            elif price >= floor:
                st["floor_state"] = "above"

        if pct is not None and abs(pct) >= MOVE_PCT and st.get("move_date") != today:
            send_telegram(f"\u26A1 {item['name']} 급변동 ({fmt_chg(pct)})\n\n"
                          f"전일대비 {MOVE_PCT:.0f}% 이상 움직였어요.\n"
                          f"현재: {fmt_price(item, price)}\n"
                          f"{now_kst():%m-%d %H:%M}\n{link_for(item)}")
            log(f"→ {item['name']} 급변동 알림 전송")
            st["move_date"] = today

    save_state(state)


def run_report():
    lines = []
    for item in WATCHLIST:
        try:
            price, pct = get_quote(item)
            lines.append(f"\u2022 {item['name']}: {fmt_price(item, price)} ({fmt_chg(pct)})")
        except Exception as e:
            log(str(e))
            lines.append(f"\u2022 {item['name']}: 조회 실패")
    msg = (f"\U0001F4CA 아침 시세 브리핑\n{now_kst():%Y-%m-%d (%a) %H:%M}\n\n"
           + "\n".join(lines))
    send_telegram(msg)
    log("→ 아침 브리핑 전송 완료")


def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "monitor"
    if mode in ("id", "getid", "chatid"):
        print_chat_id()
    elif mode == "report":
        run_report()
    else:
        run_monitor()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"오류: {e}")
        sys.exit(1)
