# -*- coding: utf-8 -*-
"""
price_watch.py — config.json(대시보드가 저장) 기반 시세 감시 + 텔레그램 알림

  python price_watch.py monitor   # 5분마다: 시세 기록(prices.json) + 하한가/급변동 알림
  python price_watch.py report    # 아침 브리핑(즐겨찾기)
  python price_watch.py id        # 텔레그램 chat_id 확인

알림 대상: 즐겨찾기 전체 + 관심종목 중 alert=true 인 것
prices.json 기록 대상: 즐겨찾기 + 관심종목 전체 (대시보드 표시용)
표준 라이브러리만 사용.
"""

import sys, os, json, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

KST        = timezone(timedelta(hours=9))
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG     = os.path.join(BASE_DIR, "config.json")
PRICES     = os.path.join(BASE_DIR, "prices.json")
STATE_FILE = os.path.join(BASE_DIR, "watch_state.json")
LOG_FILE   = os.path.join(BASE_DIR, "price_watch.log")

HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
           "Accept": "application/json, text/plain, */*"}

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
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
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


def _candidate_urls(t, code):
    if t == "metal":
        return [f"https://api.stock.naver.com/marketindex/metals/{code}/prices?pageSize=2"]
    if t == "exchange":
        return [f"https://api.stock.naver.com/marketindex/exchange/{code}/prices?pageSize=2"]
    if t == "kr":
        return [f"https://m.stock.naver.com/api/stock/{code}/basic",
                f"https://api.stock.naver.com/stock/{code}/basic"]
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
            if _to_float(ratio_raw) < 0:
                pct = -pct
        except Exception:
            pct = None
    amt_raw = _find_key(data, AMT_KEYS)
    if pct is not None and amt_raw is not None:
        try:
            a = _to_float(amt_raw)
            pct = -abs(pct) if a < 0 else (abs(pct) if a > 0 else pct)
        except Exception:
            pass
    return price, pct


def get_quote(item):
    last = None
    for url in _candidate_urls(item["type"], item["code"]):
        try:
            q = _parse_quote(json.loads(_get(url)))
            if q is not None:
                return q
        except Exception as e:
            last = e
    raise RuntimeError(f"{item['name']} 조회 실패: {last}")


def fmt_price(item, price):
    if item["type"] == "us":
        return f"${price:,.2f}"
    if item["type"] == "exchange":
        return f"{price:,.2f}{item.get('unit', '')}"
    return f"{price:,.0f}{item.get('unit', '')}"


def fmt_chg(pct):
    if pct is None:
        return "등락 N/A"
    if pct > 0:
        return f"\U0001F534 \u25B2{abs(pct):.2f}%"
    if pct < 0:
        return f"\U0001F535 \u25BC{abs(pct):.2f}%"
    return f"\u2013 {abs(pct):.2f}%"


def link_for(item):
    t, c = item["type"], item["code"]
    if t in ("metal", "exchange"):
        return f"https://m.stock.naver.com/marketindex/{'metals' if t=='metal' else 'exchange'}/{c}"
    if t == "kr":
        return f"https://m.stock.naver.com/domestic/stock/{c}/total"
    if t == "us":
        return f"https://m.stock.naver.com/worldstock/stock/{c}/total"
    return "https://m.stock.naver.com"


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {resp}")


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        c = {}
    c.setdefault("favorites", [])
    c.setdefault("watchlist", [])
    c.setdefault("move_pct", 10)
    return c


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("items", {})
    s.setdefault("error_notified", False)
    return s


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def item_key(it):
    return f"{it['type']}:{it['code']}"


def all_price_items(cfg):
    seen, out = set(), []
    for it in cfg["favorites"] + cfg["watchlist"]:
        k = item_key(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def alert_items(cfg):
    out = list(cfg["favorites"])
    fav = {item_key(i) for i in cfg["favorites"]}
    for it in cfg["watchlist"]:
        if it.get("alert") and item_key(it) not in fav:
            out.append(it)
    return out


def print_chat_id():
    data = json.loads(_get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"))
    found = set()
    for u in data.get("result", []):
        ch = (u.get("message") or u.get("edited_message") or {}).get("chat", {})
        if ch.get("id") is not None:
            found.add((ch["id"], ch.get("first_name") or ch.get("title") or ""))
    print("찾은 chat_id:" if found else "메시지 없음 — 봇에게 먼저 메시지를 보내세요.")
    for cid, nm in found:
        print(f"  {cid}  ({nm})")


def run_monitor():
    cfg = load_config()
    state = load_state()
    today = f"{now_kst():%Y-%m-%d}"

    quotes, ok = {}, 0
    for it in all_price_items(cfg):
        try:
            price, pct = get_quote(it)
            quotes[item_key(it)] = {"name": it["name"], "price": price, "pct": pct, "type": it["type"]}
            ok += 1
        except Exception as e:
            log(str(e))

    # 대시보드용 시세 파일 기록
    save_json(PRICES, {"updated": now_kst().isoformat(timespec="seconds"), "items": quotes})

    if ok == 0 and all_price_items(cfg):
        if not state["error_notified"]:
            try:
                send_telegram("⚠️ 시세 봇: 네이버 조회에 전부 실패했어요. 로그를 확인하세요.")
            except Exception:
                pass
            state["error_notified"] = True
            save_json(STATE_FILE, state)
        log("전체 조회 실패")
        return
    state["error_notified"] = False

    move_pct = float(cfg.get("move_pct", 10))
    for it in alert_items(cfg):
        q = quotes.get(item_key(it))
        if not q:
            continue
        price, pct = q["price"], q["pct"]
        st = state["items"].setdefault(item_key(it), {})

        floor = it.get("floor")
        if floor:
            if price < float(floor) and st.get("floor_state") != "below":
                send_telegram(f"\U0001F4C9 {it['name']} 하한가 알림\n\n기준가 아래로 내려왔어요.\n"
                              f"현재: {fmt_price(it, price)} ({fmt_chg(pct)})\n"
                              f"기준: {float(floor):,.0f}{it.get('unit','')}\n{now_kst():%m-%d %H:%M}\n{link_for(it)}")
                log(f"→ {it['name']} 하한가 알림")
                st["floor_state"] = "below"
            elif price >= float(floor):
                st["floor_state"] = "above"

        if pct is not None and abs(pct) >= move_pct and st.get("move_date") != today:
            send_telegram(f"\u26A1 {it['name']} 급변동 ({fmt_chg(pct)})\n\n전일대비 {move_pct:.0f}% 이상 움직였어요.\n"
                          f"현재: {fmt_price(it, price)}\n{now_kst():%m-%d %H:%M}\n{link_for(it)}")
            log(f"→ {it['name']} 급변동 알림")
            st["move_date"] = today

    save_json(STATE_FILE, state)


def run_report():
    cfg = load_config()
    lines = []
    for it in cfg["favorites"]:
        try:
            price, pct = get_quote(it)
            lines.append(f"\u2022 {it['name']}: {fmt_price(it, price)} ({fmt_chg(pct)})")
        except Exception as e:
            log(str(e))
            lines.append(f"\u2022 {it['name']}: 조회 실패")
    if not lines:
        lines = ["(즐겨찾기가 비어 있어요. 대시보드에서 종목을 추가하세요.)"]
    send_telegram(f"\U0001F4CA 아침 시세 브리핑\n{now_kst():%Y-%m-%d (%a) %H:%M}\n\n" + "\n".join(lines))
    log("→ 아침 브리핑 전송")


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
    except Exception as e:
        log(f"오류: {e}")
        sys.exit(1)
