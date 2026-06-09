# -*- coding: utf-8 -*-
"""
每日盤後抓證交所官方資料 → 產生 data.json（供 index.html 讀取）。
在 GitHub Actions 上執行（GitHub 伺服器能直連 twse.com.tw）。
只用標準庫，無需 pip install。
資料來源全部為台灣證交所官方 API：
  - STOCK_DAY  個股日線（高/低/收，整月回傳）
  - T86        三大法人買賣超日報
  - MI_MARGN   融資融券彙總
抓不到的欄位一律留 None（頁面顯示 ⚪），絕不臆造。
"""
import json, urllib.request, datetime, time

STOCKS = {"hh": "2317", "ly": "2330"}   # 鴻海 / 台積電
UA = {"User-Agent": "Mozilla/5.0 (compatible; dashboard-bot/1.0)"}
TZ = datetime.timezone(datetime.timedelta(hours=8))  # 台北

def get_json(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print("  ! fetch fail", url[:80], e)
            time.sleep(2)
    return None

def num(x):
    try:
        return float(str(x).replace(",", "").replace("+", "").strip())
    except Exception:
        return None

def roc_to_ad(d):
    # "115/06/09" -> "20260609"
    try:
        y, m, day = d.split("/")
        return "%04d%02d%02d" % (int(y) + 1911, int(m), int(day))
    except Exception:
        return None

def stock_day_month(stockno, yyyymm01):
    url = ("https://www.twse.com.tw/exchangeReport/STOCK_DAY"
           "?response=json&date=%s&stockNo=%s" % (yyyymm01, stockno))
    return get_json(url)

def closes_and_last(stockno, today):
    """回傳 (最近交易日資訊 dict, 最近N日收盤 list 由舊到新)"""
    closes = []
    last = None
    # 抓當月與前3個月，湊滿 60 個交易日做季線
    for back in range(0, 4):
        y = today.year
        m = today.month - back
        while m <= 0:
            m += 12; y -= 1
        j = stock_day_month(stockno, "%04d%02d01" % (y, m))
        if not j or j.get("stat") != "OK" or not j.get("data"):
            continue
        rows = j["data"]
        for row in rows:
            c = num(row[6])
            if c is not None:
                closes.append((row[0], num(row[4]), num(row[5]), c))  # 日期,高,低,收
    closes.sort(key=lambda r: roc_to_ad(r[0]) or "0")
    if closes:
        d0 = closes[-1]
        last = {"date": roc_to_ad(d0[0]), "high": d0[1], "low": d0[2], "close": d0[3]}
    closevals = [r[3] for r in closes]
    return last, closevals

def san_guan(high, low):
    if high is None or low is None:
        return (None, None, None)
    rng = high - low
    up = round(low + rng * 1.382, 2)
    mid = round((high + low) / 2, 2)
    dn = round(high - rng * 1.382, 2)
    return (up, mid, dn)

def ma(vals, n):
    if len(vals) < n:
        return None
    return round(sum(vals[-n:]) / n, 2)

def fetch_t86(ad_date):
    url = ("https://www.twse.com.tw/fund/T86"
           "?response=json&date=%s&selectType=ALL" % ad_date)
    j = get_json(url)
    if not j or j.get("stat") != "OK":
        return {}, []
    return {"fields": j.get("fields", [])}, j.get("data", [])

def idx_of(fields, *keys):
    for i, f in enumerate(fields):
        for k in keys:
            if k in f:
                return i
    return None

def parse_t86(fields, data, stockno):
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
            return {"foreign": lots(fi), "trust": lots(ti),
                    "dealer": lots(di), "total": lots(si),
                    "f5": None, "streak": None}
    return None

def fetch_margn(ad_date):
    url = ("https://www.twse.com.tw/exchangeReport/MI_MARGN"
           "?response=json&date=%s&selectType=ALL" % ad_date)
    return get_json(url)

def parse_margn(j, stockno):
    if not j:
        return None
    # MI_MARGN 個股表：欄位多為
    # 0代號 1名稱 2融資買進 3賣出 4現償 5前日餘額 6今日餘額 7限額
    # 8融券買進 9賣出 10現償 11前日餘額 12今日餘額 13限額 14資券互抵 15註記
    tables = j.get("tables") or []
    rows = []
    for t in tables:
        d = t.get("data") or []
        if d and len(d[0]) >= 13:
            rows = d; break
    if not rows:
        rows = j.get("data") or []
    for row in rows:
        if str(row[0]).strip() == stockno and len(row) >= 13:
            mbal = num(row[6]); mprev = num(row[5])
            sbal = num(row[12]); sprev = num(row[11])
            mchg = None if (mbal is None or mprev is None) else round(mbal - mprev)
            schg = None if (sbal is None or sprev is None) else round(sbal - sprev)
            ratio = None
            if mbal and sbal is not None and mbal > 0:
                ratio = "%.1f%%" % (sbal / mbal * 100)
            return {"mBal": None if mbal is None else round(mbal),
                    "mChg": mchg,
                    "sBal": None if sbal is None else round(sbal),
                    "sChg": schg, "ratio": ratio}
    return None

def main():
    now = datetime.datetime.now(TZ)
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "tradeDate": None}
    trade_date = None
    per = {}
    for k, sn in STOCKS.items():
        print("== %s %s ==" % (k, sn))
        last, closes = closes_and_last(sn, now)
        if not last:
            print("  STOCK_DAY 無資料，跳過"); per[k] = {}; continue
        up, mid, dn = san_guan(last["high"], last["low"])
        per[k] = {"close": last["close"], "high": last["high"], "low": last["low"],
                  "up": up, "mid": mid, "dn": dn,
                  "ma20": ma(closes, 20), "ma60": ma(closes, 60)}
        trade_date = trade_date or last["date"]
    out["tradeDate"] = (("%s-%s-%s" % (trade_date[:4], trade_date[4:6], trade_date[6:]))
                        if trade_date else now.strftime("%Y-%m-%d"))

    if trade_date:
        meta, t86 = fetch_t86(trade_date)
        margn = fetch_margn(trade_date)
        for k, sn in STOCKS.items():
            if not per.get(k):
                continue
            ii = parse_t86(meta.get("fields", []), t86, sn) if t86 else None
            per[k]["ii"] = ii
            per[k]["margin"] = parse_margn(margn, sn) if margn else None

    out["hh"] = per.get("hh", {})
    out["ly"] = per.get("ly", {})

    def fnum(x):
        return ("{:,}".format(x) if isinstance(x, (int, float)) else "—")
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
    print("written data.json:")
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
