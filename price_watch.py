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
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

KST        = timezone(timedelta(hours=9))
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG     = os.path.join(BASE_DIR, "config.json")
PRICES     = os.path.join(BASE_DIR, "prices.json")
STATE_FILE = os.path.join(BASE_DIR, "watch_state.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
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
    c.setdefault("move_pct", 10)
    if "watchlists" not in c:        # 구버전(단일 watchlist) 마이그레이션
        old = c.get("watchlist", [])
        c["watchlists"] = [{"id": "w1", "name": "관심종목", "items": old}] if old else []
    c.setdefault("watchlists", [])
    if "report_times" not in c:
        rt = c.get("report_time", "08:00")
        c["report_times"] = [rt] if rt else ["08:00"]
    if not c["report_times"]:
        c["report_times"] = ["08:00"]
    ma = c.setdefault("market_alerts", {})
    for k in ("kr_open", "kr_close", "us_open", "us_close"):
        ma.setdefault(k, False)
    for f in c["favorites"]:
        f.setdefault("alert", True)
    for wl in c["watchlists"]:
        wl.setdefault("alert", False)
    return c


def all_watch_items(cfg):
    out = []
    for wl in cfg["watchlists"]:
        out += wl.get("items", [])
    return out


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
    for it in cfg["favorites"] + all_watch_items(cfg):
        k = item_key(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


YF = "https://query1.finance.yahoo.com/v8/finance/chart/"


def _yahoo_symbols(it):
    t = it.get("type")
    if t == "us":
        tv = it.get("tv", "")
        base = tv.split(":")[-1] if ":" in tv else it["code"].split(".")[0]
        return [base]
    if t == "exchange":
        return ["KRW=X"]                       # USD/KRW
    if t == "kr":
        c = it["code"]
        return [f"{c}.KS", f"{c}.KQ"]          # 코스피 먼저, 안 되면 코스닥
    return []


def _yf_series(sym, interval, rng):
    url = f"{YF}{urllib.parse.quote(sym)}?interval={interval}&range={rng}"
    req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace"))
        res = d["chart"]["result"][0]
    except Exception:
        return []
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    o, h, l, c = q.get("open", []), q.get("high", []), q.get("low", []), q.get("close", [])
    usd = res["meta"].get("currency") == "USD"
    rnd = (lambda x: round(x, 2)) if usd else (lambda x: round(x))
    out = []
    for i, t in enumerate(ts):
        try:
            if None in (o[i], h[i], l[i], c[i]):
                continue
            out.append([t, rnd(o[i]), rnd(h[i]), rnd(l[i]), rnd(c[i])])
        except Exception:
            continue
    return out


# 타임프레임 코드 → (야후 interval, range)
TIMEFRAMES = {
    "1":  ("1m",  "1d"),
    "30": ("30m", "5d"),
    "60": ("60m", "1mo"),
    "d":  ("1d",  "1y"),
    "w":  ("1wk", "5y"),
    "mo": ("1mo", "max"),
}


def fetch_item_history(it):
    for sym in _yahoo_symbols(it):
        day = _yf_series(sym, "1d", "1y")
        if not day:
            continue
        data = {"sym": sym, "d": day}
        for tf, (interval, rng) in TIMEFRAMES.items():
            if tf == "d":
                continue
            s = _yf_series(sym, interval, rng)
            if s:
                data[tf] = s
        return data
    return None


def run_history():
    cfg = load_config()
    out = {}
    for it in all_price_items(cfg):
        if it.get("type") == "metal":
            continue
        h = fetch_item_history(it)
        if h:
            out[item_key(it)] = h
    save_json(HISTORY_FILE, {"updated": now_kst().isoformat(timespec="seconds"), "items": out})
    log(f"→ 차트 데이터 {len(out)}개 갱신")


def alert_items(cfg):
    out, keys = [], set()
    for it in cfg["favorites"]:
        if it.get("alert", True):
            out.append(it)
            keys.add(item_key(it))
    for wl in cfg["watchlists"]:
        if wl.get("alert"):
            for it in wl.get("items", []):
                if item_key(it) not in keys:
                    keys.add(item_key(it))
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
            f = float(floor)
            d = it.get("floorDir", "below")
            prev = st.get("last_price")
            now_cond = (price <= f) if d == "below" else (price >= f)       # 현재 기준 안쪽
            prev_out = prev is not None and ((prev > f) if d == "below" else (prev < f))  # 1분전엔 기준 바깥
            if now_cond and prev_out:                                       # 바깥→안쪽 교차 순간만 알림
                word = "이하로 내려왔어요" if d == "below" else "이상으로 올라왔어요"
                emo = "\U0001F4C9" if d == "below" else "\U0001F4C8"
                send_telegram(f"{emo} {it['name']} 가격 알림\n\n기준가 {word}\n"
                              f"현재: {fmt_price(it, price)} ({fmt_chg(pct)})\n"
                              f"기준: {f:,.0f}{it.get('unit','')} {'이하' if d=='below' else '이상'}\n"
                              f"{now_kst():%m-%d %H:%M}\n{link_for(it)}")
                log(f"→ {it['name']} 가격 알림 ({d})")
        st["last_price"] = price                                            # 다음 비교용으로 현재가 저장

        if pct is not None and abs(pct) >= move_pct and st.get("move_date") != today:
            send_telegram(f"\u26A1 {it['name']} 급변동 ({fmt_chg(pct)})\n\n전일대비 {move_pct:.0f}% 이상 움직였어요.\n"
                          f"현재: {fmt_price(it, price)}\n{now_kst():%m-%d %H:%M}\n{link_for(it)}")
            log(f"→ {it['name']} 급변동 알림")
            st["move_date"] = today

    rep = state.get("report") or {}
    if rep.get("date") != today:
        rep = {"date": today, "done": []}
    now_min = now_kst().hour * 60 + now_kst().minute
    for t in cfg.get("report_times", []):
        if t in rep["done"]:
            continue
        try:
            hh, mm = map(int, t.split(":"))
        except Exception:
            continue
        tmin = hh * 60 + mm
        if now_min >= tmin and (now_min - tmin) <= 120:   # 예정 시각 이후 2시간 내 1회
            try:
                run_report()
                rep["done"].append(t)
                log(f"→ 리포트 전송 ({t})")
            except Exception as e:
                log(f"리포트 오류({t}): {e}")
    state["report"] = rep
    check_market_alerts(cfg, state)
    if now_kst().timestamp() - state.get("hist_at", 0) > 1200:   # 차트 데이터는 20분마다
        try:
            run_history()
            state["hist_at"] = now_kst().timestamp()
        except Exception as e:
            log(f"차트데이터 오류: {e}")
    save_json(STATE_FILE, state)


def _send_group(title, items):
    lines = []
    for it in items:
        try:
            price, pct = get_quote(it)
            lines.append(f"\u2022 {it['name']}: {fmt_price(it, price)} ({fmt_chg(pct)})")
        except Exception as e:
            log(str(e))
            lines.append(f"\u2022 {it['name']}: 조회 실패")
    if not lines:
        return
    send_telegram(f"{title}\n{now_kst():%Y-%m-%d (%a) %H:%M}\n\n" + "\n".join(lines))


def run_report(label="아침 시세"):
    cfg = load_config()
    _send_group(f"\U0001F4CA {label} — 메인", cfg["favorites"])
    for wl in cfg["watchlists"]:
        if wl.get("alert"):
            _send_group(f"\U0001F4CA {label} — {wl['name']}", wl.get("items", []))
    log(f"→ 리포트 전송: {label}")


MARKETS = {
    "kr_open":  ("Asia/Seoul",        9,  0, "\U0001F1F0\U0001F1F7 한국 장 시작"),
    "kr_close": ("Asia/Seoul",       15, 30, "\U0001F1F0\U0001F1F7 한국 장 마감"),
    "us_open":  ("America/New_York",  9, 30, "\U0001F1FA\U0001F1F8 미국 장 시작"),
    "us_close": ("America/New_York", 16,  0, "\U0001F1FA\U0001F1F8 미국 장 마감"),
}


def check_market_alerts(cfg, state):
    ma = cfg.get("market_alerts") or {}
    if not any(ma.values()) or ZoneInfo is None:
        return
    mk = state.get("mkt") or {}
    today = f"{now_kst():%Y-%m-%d}"
    if mk.get("date") != today:
        mk = {"date": today, "done": []}
    for key, (tz, hh, mm, label) in MARKETS.items():
        if not ma.get(key) or key in mk["done"]:
            continue
        try:
            local = datetime.now(ZoneInfo(tz))
        except Exception:
            continue
        if local.weekday() >= 5:      # 주말(현지 기준) 제외
            continue
        tgt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        delta = (local - tgt).total_seconds()
        if 0 <= delta <= 3600:        # 해당 시각 이후 1시간 내 1회
            try:
                run_report(label)
                mk["done"].append(key)
            except Exception as e:
                log(f"장알림 오류({key}): {e}")
    state["mkt"] = mk


def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "monitor"
    if mode in ("id", "getid", "chatid"):
        print_chat_id()
    elif mode == "report":
        run_report()
    elif mode == "history":
        run_history()
    else:
        run_monitor()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"오류: {e}")
        sys.exit(1)
