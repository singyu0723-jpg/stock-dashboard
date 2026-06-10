# -*- coding: utf-8 -*-
"""
每日盤後抓證交所官方資料 → 產生 data.json（供 index.html 讀取）。
在 GitHub Actions 上執行（GitHub 伺服器能直連 twse.com.tw）。只用標準庫。
資料來源全為台灣證交所官方 API：
  - STOCK_DAY  個股日線（高/低/收，整月回傳）→ 三關價、月/季線、最近交易日
  - T86        三大法人買賣超日報（抓最近5個交易日做圖）
  - MI_MARGN   融資融券彙總（抓最近5個交易日做圖）
抓不到的欄位一律留 None（頁面顯示 ⚪），絕不臆造。
"""
import json, urllib.request, datetime, time

STOCKS = {"hh": "2317", "ly": "2330"}   # 鴻海 / 台積電
UA = {"User-Agent": "Mozilla/5.0 (compatible; dashboard-bot/1.0)"}
TZ = datetime.timezone(datetime.timedelta(hours=8))

def get_json(url, retries=5):
    for i in range(retries):
        time.sleep(3.0)                       # 證交所限流，每次請求前等待
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.loads(r.read().decode("utf-8"))
            if isinstance(j, dict) and j.get("stat") and j.get("stat") != "OK":
                print("  ! stat=%s 重試 %s" % (j.get("stat"), url[-30:]))
                time.sleep(4); continue
            return j
        except Exception as e:
            print("  ! fetch fail", url[-40:], e); time.sleep(5)
    return None

def num(x):
    try:
        return float(str(x).replace(",", "").replace("+", "").strip())
    except Exception:
        return None

def roc_to_ad(d):
    try:
        y, m, day = d.split("/")
        return "%04d%02d%02d" % (int(y) + 1911, int(m), int(day))
    except Exception:
        return None

def stock_day_month(stockno, yyyymm01):
    url = ("https://www.twse.com.tw/exchangeReport/STOCK_DAY"
           "?response=json&date=%s&stockNo=%s" % (yyyymm01, stockno))
    return get_json(url)

def closes_and_dates(stockno, today):
    rows_all = []
    for back in range(0, 4):                  # 當月+前3月，湊滿60交易日
        y, m = today.year, today.month - back
        while m <= 0:
            m += 12; y -= 1
        j = stock_day_month(stockno, "%04d%02d01" % (y, m))
        if not j or not j.get("data"):
            continue
        for row in j["data"]:
            c = num(row[6])
            if c is not None:
                rows_all.append((roc_to_ad(row[0]), num(row[4]), num(row[5]), c))
    rows_all = [r for r in rows_all if r[0]]
    rows_all.sort(key=lambda r: r[0])
    print("  %s 收盤資料 %d 天" % (stockno, len(rows_all)))
    return rows_all

def san_guan(high, low):
    if high is None or low is None:
        return (None, None, None)
    rng = high - low
    return (round(low + rng * 1.382, 2), round((high + low) / 2, 2),
            round(high - rng * 1.382, 2))

def ma(vals, n):
    return round(sum(vals[-n:]) / n, 2) if len(vals) >= n else None

def _span(d1, d2):
    a = datetime.date(int(d1[:4]), int(d1[4:6]), int(d1[6:]))
    b = datetime.date(int(d2[:4]), int(d2[4:6]), int(d2[6:]))
    return (b - a).days

def ma_safe(rows, n, max_span):
    """rows=[(addate,h,l,c)...]；最近 n 筆若日期跨度過大(代表抓漏某月有缺口)→回 None，不算錯誤均線。"""
    if len(rows) < n:
        print("    MA%d: 資料不足(%d天)→⚪" % (n, len(rows)))
        return None
    win = rows[-n:]
    sp = _span(win[0][0], win[-1][0])
    if sp > max_span:
        print("    MA%d: 近%d筆跨度%d天>上限%d(疑似缺口)→⚪" % (n, n, sp, max_span))
        return None
    return round(sum(r[3] for r in win) / n, 2)

# ---- 三大法人 T86 ----
def idx_of(fields, *keys):
    for i, f in enumerate(fields):
        for k in keys:
            if k in f:
                return i
    return None

def parse_t86_row(fields, data, stockno):
    if not data:
        return None
    fi = idx_of(fields, "外陸資買賣超股數(不含外資自營商)", "外資買賣超")
    ti = idx_of(fields, "投信買賣超股數", "投信買賣超")
    di = idx_of(fields, "自營商買賣超股數")
    si = idx_of(fields, "三大法人買賣超股數")
    for row in data:
        if str(row[0]).strip() == stockno:
            def lots(i):
                if i is None or i >= len(row):
                    return None
                v = num(row[i])
                return None if v is None else round(v / 1000)
            return {"fg": lots(fi), "trust": lots(ti),
                    "dealer": lots(di), "total": lots(si)}
    return None

# ---- 融資融券 MI_MARGN ----
def parse_margn_row(j, stockno):
    if not j:
        return None
    rows = []
    for t in (j.get("tables") or []):
        d = t.get("data") or []
        if d and len(d[0]) >= 13:
            rows = d; break
    if not rows:
        rows = j.get("data") or []
    for row in rows:
        if str(row[0]).strip() == stockno and len(row) >= 13:
            mbal, mprev = num(row[6]), num(row[5])
            sbal, sprev = num(row[12]), num(row[11])
            return {"mBal": None if mbal is None else round(mbal),
                    "mChg": None if (mbal is None or mprev is None) else round(mbal - mprev),
                    "sBal": None if sbal is None else round(sbal),
                    "sChg": None if (sbal is None or sprev is None) else round(sbal - sprev)}
    return None

def streak_text(fg):
    seq = [v for v in fg if v is not None]
    if not seq:
        return None
    last = seq[-1]
    sign = 1 if last > 0 else (-1 if last < 0 else 0)
    if sign == 0:
        return "持平"
    c = 0
    for v in reversed(seq):
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            c += 1
        else:
            break
    return "連%d日%s" % (c, "買" if sign > 0 else "賣")

def main():
    now = datetime.datetime.now(TZ)
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "tradeDate": None}
    per, trade_dates = {}, []

    for k, sn in STOCKS.items():
        print("== %s %s ==" % (k, sn))
        rows = closes_and_dates(sn, now)
        if not rows:
            per[k] = {}; continue
        last = rows[-1]
        up, mid, dn = san_guan(last[1], last[2])
        per[k] = {"close": last[3], "high": last[1], "low": last[2],
                  "up": up, "mid": mid, "dn": dn,
                  "ma20": ma_safe(rows, 20, 45),
                  "ma60": ma_safe(rows, 60, 115)}
        dates5 = [r[0] for r in rows[-5:]]
        if len(dates5) > len(trade_dates):
            trade_dates = dates5

    td = trade_dates[-1] if trade_dates else None
    out["tradeDate"] = (("%s-%s-%s" % (td[:4], td[4:6], td[6:])) if td
                        else now.strftime("%Y-%m-%d"))

    # 最近5個交易日的法人 / 融資券（每天的 T86、MI_MARGN 各抓一次，兩檔一起解析）
    hist = {k: {"dates": [], "fg": [], "trust": [], "mBal": [], "sBal": []} for k in STOCKS}
    for ad in trade_dates:
        t86 = get_json("https://www.twse.com.tw/fund/T86?response=json&date=%s&selectType=ALL" % ad)
        mar = get_json("https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date=%s&selectType=ALL" % ad)
        fields = t86.get("fields", []) if t86 else []
        t86data = t86.get("data", []) if t86 else []
        for k, sn in STOCKS.items():
            if not per.get(k):
                continue
            ii = parse_t86_row(fields, t86data, sn) or {}
            mg = parse_margn_row(mar, sn) or {}
            hist[k]["dates"].append("%s/%s" % (ad[4:6], ad[6:]))
            hist[k]["fg"].append(ii.get("fg"))
            hist[k]["trust"].append(ii.get("trust"))
            hist[k]["mBal"].append(mg.get("mBal"))
            hist[k]["sBal"].append(mg.get("sBal"))

    for k in STOCKS:
        if not per.get(k):
            continue
        H = hist[k]
        per[k]["hist"] = H
        fg5 = [v for v in H["fg"] if v is not None]
        tr5 = [v for v in H["trust"] if v is not None]
        # 最新一日（給摘要/相容舊欄位）
        per[k]["ii"] = {"fg": (H["fg"][-1] if H["fg"] else None),
                        "trust": (H["trust"][-1] if H["trust"] else None),
                        "f5": (sum(fg5) if fg5 else None),
                        "trust5": (sum(tr5) if tr5 else None),
                        "streak": streak_text(H["fg"])}
        mb = [v for v in H["mBal"] if v is not None]
        sb = [v for v in H["sBal"] if v is not None]
        mchg = (mb[-1] - mb[-2]) if len(mb) >= 2 else None
        schg = (sb[-1] - sb[-2]) if len(sb) >= 2 else None
        per[k]["margin"] = {"mBal": (mb[-1] if mb else None), "mChg": mchg,
                            "sBal": (sb[-1] if sb else None), "sChg": schg}

    out["hh"], out["ly"] = per.get("hh", {}), per.get("ly", {})

    def fnum(x):
        return "{:,}".format(x) if isinstance(x, (int, float)) else "—"
    hh, ly = out["hh"], out["ly"]
    out["checks"] = [
        "① 今晚美股：NQ那指期 / 費半SOX / 美債10Y(TVC:US10Y) / VIX(VX1!)",
        "② 今晚三大法人：外資對鴻海、台積電是否續買",
        "③ 開盤30分不動手；鴻海守 %s 月線、台積電守 %s 月線"
        % (fnum(hh.get("ma20")), fnum(ly.get("ma20"))),
        "④ 三關價　鴻海 上%s/中%s/下%s；台積電 上%s/中%s/下%s"
        % (fnum(hh.get("up")), fnum(hh.get("mid")), fnum(hh.get("dn")),
           fnum(ly.get("up")), fnum(ly.get("mid")), fnum(ly.get("dn"))),
        "⑤ 鴻海加碼＝三確認到齊；台積電波段為主、跌破月線減碼",
    ]

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("written data.json"); print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
