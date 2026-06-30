"""
Backtest แบบ point-in-time ของ Smart-Money Model (เวอร์ชันน่าเชื่อถือขึ้น)

กัน lookahead bias:
  - 13F เปิดเผยช้า ~45 วัน -> พอร์ตไตรมาส Q ลงมือได้จริงที่ Q+45 วัน
  - ณ จุด rebalance ใช้เฉพาะ holdings ที่เปิดเผยแล้ว
  - ราคาจริงจาก yfinance เทียบ SPY · ใช้เฉพาะกอง 13F (มีประวัติรายไตรมาส)

เพิ่มความสมจริง:
  #2 ต้นทุนธุรกรรม (commission + slippage) คิดตาม turnover ทุก rebalance
  #3 เทียบ equal-weight + แยก sector exposure (alpha มาจากเลือกหุ้น หรือโดนธีมพาไป)
     + Max Drawdown / CAGR

*** เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน — ผลย้อนหลังไม่การันตีอนาคต ***
"""
import json
import pandas as pd
from datetime import datetime, timedelta

from store import connect
from config import DATA_DIR
from trackers.common import map_cusips_to_tickers

FILING_LAG_DAYS = 45
BENCHMARK = "SPY"
COST_BPS_PER_SIDE = 20          # ค่าคอม+slippage ต่อด้าน (0.20%) สำหรับหุ้นสภาพคล่องสูง
W = {"consensus": 0.45, "conviction": 0.25, "buying": 0.30}
_SECTOR_CACHE = DATA_DIR / "sector.json"


def _quarters():
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT period_date FROM holdings "
            "WHERE source='edgar_13f' ORDER BY period_date").fetchall()]


def _holdings_for_quarter(q):
    with connect() as c:
        return pd.read_sql_query(
            "SELECT * FROM holdings WHERE source='edgar_13f' AND period_date=?",
            c, params=[q])


def _norm(s):
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-9:
        return pd.Series(50.0, index=s.index)
    return 100.0 * (s - lo) / (hi - lo)


def score_quarter(q, prev_q, top_n=15):
    """พอร์ตคะแนน ณ ไตรมาส q (ใช้ prev_q คำนวณแรงซื้อ)"""
    cur = _holdings_for_quarter(q)
    if cur.empty:
        return pd.DataFrame()
    g = cur.groupby("cusip").agg(
        issuer=("issuer", "first"),
        ticker=("ticker", "first"),
        n_investors=("investor", "nunique"),
        avg_weight=("weight_pct", "mean"),
        shares_cur=("shares", "sum"),
    )
    if prev_q is not None:
        prev = _holdings_for_quarter(prev_q)
        pg = prev.groupby("cusip")["shares"].sum().rename("shares_prev")
        g = g.join(pg)
    else:
        g["shares_prev"] = pd.NA
    g["shares_prev"] = g["shares_prev"].fillna(0)

    def buy_sig(r):
        if r["shares_prev"] == 0 and r["shares_cur"] > 0:
            return 1.0
        if r["shares_cur"] > r["shares_prev"] * 1.02:
            return 0.5
        if r["shares_cur"] < r["shares_prev"] * 0.98:
            return -0.5
        return 0.0
    g["buying"] = g.apply(buy_sig, axis=1)
    g["score"] = (W["consensus"] * _norm(g["n_investors"]) +
                  W["conviction"] * _norm(g["avg_weight"].fillna(0)) +
                  W["buying"] * _norm(g["buying"]))
    g = g.sort_values("score", ascending=False).head(top_n).reset_index()
    g["weight"] = g["score"] / g["score"].sum()
    return g[["cusip", "issuer", "ticker", "score", "weight", "n_investors"]]


def _action_date(q):
    return datetime.strptime(q, "%Y-%m-%d") + timedelta(days=FILING_LAG_DAYS)


def _max_drawdown(navs):
    peak, mdd = -1e9, 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return 100 * mdd


def _cagr(nav_end, start_dt, end_dt):
    yrs = max((end_dt - start_dt).days / 365.25, 1e-6)
    return 100 * (nav_end ** (1 / yrs) - 1)


def fetch_sectors(tickers):
    """ดึง sector ต่อ ticker (yfinance, cache ลงไฟล์) — best effort"""
    cache = json.loads(_SECTOR_CACHE.read_text()) if _SECTOR_CACHE.exists() else {}
    todo = [t for t in set(tickers) if t and t not in cache]
    if todo:
        import yfinance as yf
        for t in todo:
            try:
                info = yf.Ticker(t).info
                cache[t] = info.get("sector") or "Unknown"
            except Exception:
                cache[t] = "Unknown"
        _SECTOR_CACHE.write_text(json.dumps(cache))
    return {t: cache.get(t, "Unknown") for t in tickers}


def run_backtest(top_n=15, cost_bps=COST_BPS_PER_SIDE, compute_sectors=True):
    quarters = _quarters()
    if len(quarters) < 2:
        return {"error": "ข้อมูลไตรมาสไม่พอ (ต้อง >= 2) — รัน fetch_history.py ก่อน"}

    books = []
    for i, q in enumerate(quarters):
        prev_q = quarters[i - 1] if i > 0 else None
        b = score_quarter(q, prev_q, top_n=top_n)
        if not b.empty:
            books.append((q, b))

    # resolve ticker เฉพาะตัวที่ถูกเลือก (lazy, cache)
    all_cusips = pd.concat([b for _, b in books])["cusip"].dropna().unique().tolist()
    cmap = map_cusips_to_tickers(all_cusips)
    for _, b in books:
        b["ticker"] = b.apply(lambda r: r["ticker"] or cmap.get(r["cusip"]), axis=1)

    tickers = sorted({t for _, b in books for t in b["ticker"].dropna().tolist()
                      if isinstance(t, str) and t})
    start = _action_date(books[0][0]) - timedelta(days=5)

    import yfinance as yf
    px = yf.download(tickers + [BENCHMARK], start=start.strftime("%Y-%m-%d"),
                     interval="1d", progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame()
    px = px.sort_index()

    def price_on(t, dt):
        if t not in px.columns:
            return None
        s = px[t].dropna()
        s = s[s.index <= pd.Timestamp(dt)]
        return float(s.iloc[-1]) if len(s) else None

    today = pd.Timestamp(datetime.today().date())
    rows = []
    nav_s = nav_e = nav_b = 1.0
    curve = [{"date": _action_date(books[0][0]).strftime("%Y-%m-%d"),
              "Model": 1.0, "EqualWeight": 1.0, "SPY": 1.0}]
    prev_w_score, prev_w_eq = {}, {}     # น้ำหนักงวดก่อน (ไว้คิด turnover)
    first_dt = None

    for i, (q, book) in enumerate(books):
        t0 = _action_date(q)
        t1 = _action_date(books[i + 1][0]) if i + 1 < len(books) else today
        t1 = min(t1, today)
        if pd.Timestamp(t0) >= today:
            continue

        # เก็บเฉพาะตัวที่มีราคาทั้งต้น-ปลายงวด
        priced = []
        for _, r in book.iterrows():
            tk = r["ticker"]
            p0, p1 = (price_on(tk, t0), price_on(tk, t1)) if tk else (None, None)
            if tk and p0 and p1 and p0 > 0:
                priced.append((tk, r["weight"], p1 / p0 - 1))
        if not priced:
            continue

        wsum = sum(w for _, w, _ in priced)
        w_score = {tk: w / wsum for tk, w, _ in priced}            # score-weight (renorm)
        w_eq = {tk: 1.0 / len(priced) for tk, _, _ in priced}      # equal-weight
        ret_by_tk = {tk: r for tk, _, r in priced}

        gross_s = sum(w_score[tk] * ret_by_tk[tk] for tk in w_score)
        gross_e = sum(w_eq[tk] * ret_by_tk[tk] for tk in w_eq)

        # ---- ต้นทุน: turnover = ครึ่งหนึ่งของผลรวม |Δw|, คิดทั้งซื้อและขาย ----
        def turnover(new_w, old_w):
            keys = set(new_w) | set(old_w)
            return 0.5 * sum(abs(new_w.get(k, 0) - old_w.get(k, 0)) for k in keys)
        to_s = turnover(w_score, prev_w_score)
        to_e = turnover(w_eq, prev_w_eq)
        cost_s = 2 * to_s * cost_bps / 10000.0      # ขาย+ซื้อ
        cost_e = 2 * to_e * cost_bps / 10000.0

        net_s = gross_s - cost_s
        net_e = gross_e - cost_e

        b0, b1 = price_on(BENCHMARK, t0), price_on(BENCHMARK, t1)
        ret_b = (b1 / b0 - 1) if (b0 and b1) else 0.0

        nav_s *= (1 + net_s); nav_e *= (1 + net_e); nav_b *= (1 + ret_b)
        if first_dt is None:
            first_dt = t0
        curve.append({"date": t1.strftime("%Y-%m-%d"),
                      "Model": nav_s, "EqualWeight": nav_e, "SPY": nav_b})
        rows.append({
            "quarter": q, "hold_from": t0.strftime("%Y-%m-%d"),
            "hold_to": t1.strftime("%Y-%m-%d"), "n_holdings": len(priced),
            "turnover_%": round(100 * to_s, 1), "cost_%": round(100 * cost_s, 2),
            "model_net_%": round(100 * net_s, 2),
            "eqw_net_%": round(100 * net_e, 2),
            "spy_%": round(100 * ret_b, 2),
            "alpha_%": round(100 * (net_s - ret_b), 2),
        })
        prev_w_score, prev_w_eq = w_score, w_eq

    periods = pd.DataFrame(rows)
    if periods.empty:
        return {"error": "คำนวณผลไม่ได้ (ไม่มีราคา)"}

    cdf = pd.DataFrame(curve)
    last_dt = pd.Timestamp(periods["hold_to"].iloc[-1])
    summary = {
        "model_net_total_%": round(100 * (nav_s - 1), 2),
        "eqw_net_total_%": round(100 * (nav_e - 1), 2),
        "spy_total_%": round(100 * (nav_b - 1), 2),
        "alpha_vs_spy_%": round(100 * (nav_s - nav_b), 2),
        "model_cagr_%": round(_cagr(nav_s, first_dt, last_dt), 1),
        "spy_cagr_%": round(_cagr(nav_b, first_dt, last_dt), 1),
        "model_max_dd_%": round(_max_drawdown(cdf["Model"].tolist()), 1),
        "spy_max_dd_%": round(_max_drawdown(cdf["SPY"].tolist()), 1),
        "total_cost_drag_%": round(periods["cost_%"].sum(), 2),
        "win_rate_vs_spy_%": round(100 * (periods["alpha_%"] > 0).mean(), 1),
        "n_rebalances": len(periods),
        "avg_holdings": round(periods["n_holdings"].mean(), 1),
        "period": f"{first_dt.strftime('%Y-%m')} → {last_dt.strftime('%Y-%m')}",
    }

    sectors = pd.DataFrame()
    if compute_sectors:
        try:
            secmap = fetch_sectors(tickers)
            # exposure เฉลี่ยถ่วงเวลา: รวมน้ำหนัก score-weight ของแต่ละ sector ทุกงวด
            acc = {}
            n = 0
            for q, book in books:
                bb = book.dropna(subset=["ticker"])
                if bb.empty:
                    continue
                wsum = bb["weight"].sum() or 1
                for _, r in bb.iterrows():
                    sec = secmap.get(r["ticker"], "Unknown")
                    acc[sec] = acc.get(sec, 0) + r["weight"] / wsum
                n += 1
            if n:
                sectors = (pd.DataFrame(
                    [{"sector": s, "avg_weight_%": round(100 * v / n, 1)}
                     for s, v in acc.items()])
                    .sort_values("avg_weight_%", ascending=False)
                    .reset_index(drop=True))
        except Exception as e:
            print("sector exposure ข้าม:", e)

    return {"periods": periods, "summary": summary,
            "curve": cdf, "sectors": sectors}


# ===========================================================================
# Backtest รายคน — "ถ้าลอกพอร์ตของนักลงทุนคนนี้เป๊ะ จะได้เท่าไหร่"
# ===========================================================================
def _investor_quarters(investor):
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT period_date FROM holdings "
            "WHERE investor=? AND source='edgar_13f' ORDER BY period_date",
            (investor,)).fetchall()]


def _investor_book(investor, q, top_n, weight_mode):
    """top_n ตำแหน่งใหญ่สุดของนักลงทุนคนนี้ ณ ไตรมาส q"""
    with connect() as c:
        df = pd.read_sql_query(
            "SELECT cusip, ticker, issuer, value_usd, weight_pct FROM holdings "
            "WHERE investor=? AND source='edgar_13f' AND period_date=?",
            c, params=[investor, q])
    if df.empty:
        return df
    g = df.groupby("cusip").agg(
        issuer=("issuer", "first"), ticker=("ticker", "first"),
        weight_pct=("weight_pct", "sum"), value_usd=("value_usd", "sum"))
    g = g.sort_values("weight_pct", ascending=False).head(top_n).reset_index()
    if weight_mode == "equal":
        g["weight"] = 1.0 / len(g)
    else:                                   # actual = ตามน้ำหนักจริงในพอร์ตเขา
        g["weight"] = g["weight_pct"] / g["weight_pct"].sum()
    return g[["cusip", "issuer", "ticker", "weight"]]


def _simulate_single(books, cost_bps=COST_BPS_PER_SIDE):
    """
    engine กลาง: books = list ของ (quarter, df[cusip,ticker,weight])
    คืน periods/summary/curve เทียบ SPY (กลยุทธ์เดียว ตามน้ำหนักใน book)
    """
    if len(books) < 2:
        return {"error": "ไตรมาสไม่พอ (ต้อง >= 2)"}
    cusips = pd.concat([b for _, b in books])["cusip"].dropna().unique().tolist()
    cmap = map_cusips_to_tickers(cusips)
    for _, b in books:
        b["ticker"] = b.apply(lambda r: r["ticker"] or cmap.get(r["cusip"]), axis=1)
    tickers = sorted({t for _, b in books for t in b["ticker"].dropna().tolist()
                      if isinstance(t, str) and t})
    if not tickers:
        return {"error": "ไม่มี ticker ที่ใช้ได้"}

    import yfinance as yf
    start = _action_date(books[0][0]) - timedelta(days=5)
    px = yf.download(tickers + [BENCHMARK], start=start.strftime("%Y-%m-%d"),
                     interval="1d", progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame()
    px = px.sort_index()

    def price_on(t, dt):
        if t not in px.columns:
            return None
        s = px[t].dropna()
        s = s[s.index <= pd.Timestamp(dt)]
        return float(s.iloc[-1]) if len(s) else None

    today = pd.Timestamp(datetime.today().date())
    rows, curve = [], []
    nav_s = nav_b = 1.0
    prev_w = {}
    first_dt = None
    for i, (q, book) in enumerate(books):
        t0 = _action_date(q)
        t1 = _action_date(books[i + 1][0]) if i + 1 < len(books) else today
        t1 = min(t1, today)
        if pd.Timestamp(t0) >= today:
            continue
        priced = []
        for _, r in book.iterrows():
            tk = r["ticker"]
            p0, p1 = (price_on(tk, t0), price_on(tk, t1)) if tk else (None, None)
            if tk and p0 and p1 and p0 > 0:
                priced.append((tk, r["weight"], p1 / p0 - 1))
        if not priced:
            continue
        wsum = sum(wt for _, wt, _ in priced)
        w = {tk: wt / wsum for tk, wt, _ in priced}
        ret_by = {tk: r for tk, _, r in priced}
        gross = sum(w[tk] * ret_by[tk] for tk in w)
        to = 0.5 * sum(abs(w.get(k, 0) - prev_w.get(k, 0))
                       for k in set(w) | set(prev_w))
        cost = 2 * to * cost_bps / 10000.0
        net = gross - cost
        b0, b1 = price_on(BENCHMARK, t0), price_on(BENCHMARK, t1)
        ret_b = (b1 / b0 - 1) if (b0 and b1) else 0.0
        nav_s *= (1 + net); nav_b *= (1 + ret_b)
        if first_dt is None:
            first_dt = t0
            curve.append({"date": t0.strftime("%Y-%m-%d"), "Strategy": 1.0, "SPY": 1.0})
        curve.append({"date": t1.strftime("%Y-%m-%d"),
                      "Strategy": nav_s, "SPY": nav_b})
        rows.append({
            "quarter": q, "hold_to": t1.strftime("%Y-%m-%d"),
            "n_holdings": len(priced), "turnover_%": round(100 * to, 1),
            "net_%": round(100 * net, 2), "spy_%": round(100 * ret_b, 2),
            "alpha_%": round(100 * (net - ret_b), 2)})
        prev_w = w

    periods = pd.DataFrame(rows)
    if periods.empty:
        return {"error": "คำนวณไม่ได้"}
    cdf = pd.DataFrame(curve)
    last_dt = pd.Timestamp(periods["hold_to"].iloc[-1])
    summary = {
        "total_%": round(100 * (nav_s - 1), 2),
        "cagr_%": round(_cagr(nav_s, first_dt, last_dt), 1),
        "spy_total_%": round(100 * (nav_b - 1), 2),
        "spy_cagr_%": round(_cagr(nav_b, first_dt, last_dt), 1),
        "alpha_cagr_%": round(_cagr(nav_s, first_dt, last_dt) -
                              _cagr(nav_b, first_dt, last_dt), 1),
        "max_dd_%": round(_max_drawdown(cdf["Strategy"].tolist()), 1),
        "spy_max_dd_%": round(_max_drawdown(cdf["SPY"].tolist()), 1),
        "win_rate_%": round(100 * (periods["alpha_%"] > 0).mean(), 1),
        "n_quarters": len(periods),
        "period": f"{first_dt.strftime('%Y-%m')} → {last_dt.strftime('%Y-%m')}",
    }
    return {"periods": periods, "summary": summary, "curve": cdf}


def backtest_investor(investor, top_n=15, cost_bps=COST_BPS_PER_SIDE,
                      weight_mode="actual"):
    """backtest พอร์ตของนักลงทุนคนเดียว (ลอกตามน้ำหนักจริง หรือ equal-weight)"""
    quarters = _investor_quarters(investor)
    books = []
    for q in quarters:
        b = _investor_book(investor, q, top_n, weight_mode)
        if not b.empty:
            books.append((q, b))
    if len(books) < 2:
        return {"error": f"{investor}: ไตรมาสไม่พอ"}
    return _simulate_single(books, cost_bps=cost_bps)


def compare_portfolios(top_n=15, cost_bps=COST_BPS_PER_SIDE, weight_mode="actual"):
    """
    เทียบผล backtest ทุกพอร์ต (รายคน + consensus model)
    คืน {table: DataFrame, results: {name: res}}
    *ระวัง: แต่ละคนช่วงเวลาต่างกัน -> เทียบที่ CAGR/alpha ไม่ใช่ total*
    """
    from config import INVESTORS
    investors = [k for k, v in INVESTORS.items() if v["source"] == "edgar_13f"]
    rows, results = [], {}
    for inv in investors:
        res = backtest_investor(inv, top_n=top_n, cost_bps=cost_bps,
                                weight_mode=weight_mode)
        if "error" in res:
            continue
        results[inv] = res
        s = res["summary"]
        rows.append({
            "portfolio": INVESTORS[inv]["display"].split("(")[0].strip(),
            "period": s["period"], "ไตรมาส": s["n_quarters"],
            "CAGR_%": s["cagr_%"], "SPY_CAGR_%": s["spy_cagr_%"],
            "alpha_CAGR_%": s["alpha_cagr_%"], "total_%": s["total_%"],
            "max_dd_%": s["max_dd_%"], "win_%": s["win_rate_%"]})
    # consensus model
    cm = run_backtest(top_n=top_n, cost_bps=cost_bps, compute_sectors=False)
    if "error" not in cm:
        s = cm["summary"]
        results["consensus"] = cm
        rows.append({
            "portfolio": "★ Consensus Model (รวมทุกคน)", "period": s["period"],
            "ไตรมาส": s["n_rebalances"], "CAGR_%": s["model_cagr_%"],
            "SPY_CAGR_%": s["spy_cagr_%"],
            "alpha_CAGR_%": round(s["model_cagr_%"] - s["spy_cagr_%"], 1),
            "total_%": s["model_net_total_%"], "max_dd_%": s["model_max_dd_%"],
            "win_%": s["win_rate_vs_spy_%"]})
    table = (pd.DataFrame(rows).sort_values("alpha_CAGR_%", ascending=False)
             .reset_index(drop=True))
    return {"table": table, "results": results}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    res = run_backtest(top_n=15)
    if "error" in res:
        print(res["error"])
    else:
        print(res["periods"].to_string())
        print("\n=== สรุป ===")
        for k, v in res["summary"].items():
            print(f"  {k}: {v}")
        if not res["sectors"].empty:
            print("\n=== Sector exposure (เฉลี่ย) ===")
            print(res["sectors"].to_string())
