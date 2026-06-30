"""
Adaptive Smart-Money Model — model ที่ปรับตัวตามฝีมือและสภาพตลาด (point-in-time)

2 กลไกหลัก (ทุกอย่างใช้ข้อมูลที่รู้ ณ จุด rebalance เท่านั้น — กัน look-ahead):

  1) SKILL-WEIGHTING แบบ trailing
     ทุกไตรมาส Q: ดู alpha ย้อนหลัง `lookback` ไตรมาสของนักลงทุนแต่ละคน (เฉพาะที่จบไปแล้ว)
     ให้น้ำหนักทุน ∝ max(0, trailing_alpha) -> คนฝีมือดีล่าสุดได้ทุนมาก, คนแพ้ดัชนีโดนตัด
     ถ้าทุกคน alpha<=0 -> ถอยเป็น equal-weight (และมักตรงกับ regime ขาลงพอดี)

  2) REGIME GATE (รับความผันผวน)
     ดู SPY เทียบเส้นค่าเฉลี่ย `regime_ma` วัน ณ วันลงมือ:
       ขาขึ้น (SPY >= MA) -> ลงเต็ม (exposure 100%)
       ขาลง  (SPY <  MA)  -> ลดพอร์ตเหลือ `regime_scale` ที่เหลือถือเงินสด (กด drawdown)

*** เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน ***
"""
import pandas as pd
from datetime import datetime, timedelta

from store import connect
from config import INVESTORS
from trackers.common import map_cusips_to_tickers
from analysis.backtest import (_action_date, _cagr, _max_drawdown,
                               _investor_book, BENCHMARK, COST_BPS_PER_SIDE)


def _all_quarters(investors):
    with connect() as c:
        qs = set()
        for inv in investors:
            for r in c.execute("SELECT DISTINCT period_date FROM holdings "
                               "WHERE investor=? AND source='edgar_13f'", (inv,)):
                qs.add(r[0])
    return sorted(qs)


def run_adaptive(top_n=15, lookback=4, cost_bps=COST_BPS_PER_SIDE,
                 regime=True, regime_ma=200, regime_scale=0.5):
    investors = [k for k, v in INVESTORS.items() if v["source"] == "edgar_13f"]
    quarters = _all_quarters(investors)
    if len(quarters) < lookback + 2:
        return {"error": "ไตรมาสไม่พอสำหรับ adaptive"}

    # ---- สร้าง book ของแต่ละคนทุกไตรมาส ----
    books = {inv: {} for inv in investors}      # inv -> {q: df[cusip,ticker,weight]}
    for inv in investors:
        for q in quarters:
            b = _investor_book(inv, q, top_n, "actual")
            if not b.empty:
                books[inv][q] = b

    # ---- resolve ticker + โหลดราคา ----
    cusips = pd.concat([b for inv in books for b in books[inv].values()]
                       )["cusip"].dropna().unique().tolist()
    cmap = map_cusips_to_tickers(cusips)
    for inv in books:
        for q, b in books[inv].items():
            b["ticker"] = b.apply(lambda r: r["ticker"] or cmap.get(r["cusip"]), axis=1)

    tickers = sorted({t for inv in books for b in books[inv].values()
                      for t in b["ticker"].dropna() if isinstance(t, str) and t})
    import yfinance as yf
    start = _action_date(quarters[0]) - timedelta(days=int(regime_ma * 1.6) + 10)
    px = yf.download(tickers + [BENCHMARK], start=start.strftime("%Y-%m-%d"),
                     interval="1d", progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame()
    px = px.sort_index()

    def price_on(t, dt):
        if t not in px.columns:
            return None
        s = px[t].dropna(); s = s[s.index <= pd.Timestamp(dt)]
        return float(s.iloc[-1]) if len(s) else None

    today = pd.Timestamp(datetime.today().date())

    # ---- ช่วงถือของแต่ละไตรมาส + ผลตอบแทนรายตัว ----
    holds = []                # (q, t0, t1)
    for i, q in enumerate(quarters):
        t0 = _action_date(q)
        t1 = _action_date(quarters[i + 1]) if i + 1 < len(quarters) else today
        t1 = min(t1, today)
        if pd.Timestamp(t0) < today:
            holds.append((q, t0, t1))

    ret_by_q = {}             # q -> {ticker: ret}
    spy_ret = {}              # q -> spy ret
    for q, t0, t1 in holds:
        rr = {}
        for t in tickers:
            p0, p1 = price_on(t, t0), price_on(t, t1)
            if p0 and p1 and p0 > 0:
                rr[t] = p1 / p0 - 1
        ret_by_q[q] = rr
        b0, b1 = price_on(BENCHMARK, t0), price_on(BENCHMARK, t1)
        spy_ret[q] = (b1 / b0 - 1) if (b0 and b1) else 0.0

    def book_weights(inv, q):
        """น้ำหนักของ inv ณ q เฉพาะ ticker ที่มีราคา (renorm)"""
        b = books[inv].get(q)
        if b is None:
            return {}
        rr = ret_by_q.get(q, {})
        w = {r["ticker"]: r["weight"] for _, r in b.iterrows()
             if r["ticker"] in rr}
        s = sum(w.values())
        return {k: v / s for k, v in w.items()} if s else {}

    def investor_ret(inv, q):
        w = book_weights(inv, q)
        rr = ret_by_q.get(q, {})
        return sum(w[t] * rr[t] for t in w) if w else None

    # alpha รายไตรมาสของแต่ละคน (ไว้ดู trailing skill)
    alpha = {inv: {} for inv in investors}
    for inv in investors:
        for q, _, _ in holds:
            r = investor_ret(inv, q)
            if r is not None:
                alpha[inv][q] = r - spy_ret[q]

    def ma_on(dt):
        s = px[BENCHMARK].dropna(); s = s[s.index <= pd.Timestamp(dt)]
        return float(s.tail(regime_ma).mean()) if len(s) >= regime_ma else None

    # ---- adaptive loop ----
    hq = [h[0] for h in holds]
    rows, curve = [], []
    nav, nav_b = 1.0, 1.0
    prev_w = {}
    first_dt = None
    weight_log = []
    for i, (q, t0, t1) in enumerate(holds):
        if i < lookback:
            continue                              # warm-up: ยังไม่มี trailing พอ
        prior = hq[max(0, i - lookback):i]        # ไตรมาสที่จบไปแล้ว (point-in-time)

        # ---- skill weight ----
        skill = {}
        for inv in investors:
            vals = [alpha[inv][pq] for pq in prior if pq in alpha[inv]]
            if vals:
                skill[inv] = sum(vals) / len(vals)
        pos = {inv: max(0.0, s) for inv, s in skill.items()}
        active = {inv: book_weights(inv, q) for inv in investors
                  if book_weights(inv, q)}
        if not active:
            continue
        tot = sum(pos.get(inv, 0) for inv in active)
        if tot > 0:
            iw = {inv: pos.get(inv, 0) / tot for inv in active}
        else:                                     # ทุกคนแพ้ดัชนี -> equal
            iw = {inv: 1.0 / len(active) for inv in active}

        # ---- รวมเป็นพอร์ตเดียว (blend ตามน้ำหนักนักลงทุน) ----
        combined = {}
        for inv, wgt in iw.items():
            for tk, w in active[inv].items():
                combined[tk] = combined.get(tk, 0) + wgt * w
        s = sum(combined.values())
        combined = {k: v / s for k, v in combined.items()} if s else {}

        # ---- regime gate ----
        exposure = 1.0
        if regime:
            ma = ma_on(t0); spx = price_on(BENCHMARK, t0)
            if ma and spx and spx < ma:
                exposure = regime_scale
        scaled = {k: v * exposure for k, v in combined.items()}

        rr = ret_by_q.get(q, {})
        gross = exposure * sum(combined[t] * rr.get(t, 0) for t in combined)
        to = 0.5 * sum(abs(scaled.get(k, 0) - prev_w.get(k, 0))
                       for k in set(scaled) | set(prev_w))
        cost = 2 * to * cost_bps / 10000.0
        net = gross - cost
        rb = spy_ret[q]
        nav *= (1 + net); nav_b *= (1 + rb)
        if first_dt is None:
            first_dt = t0
            curve.append({"date": t0.strftime("%Y-%m-%d"), "Adaptive": 1.0, "SPY": 1.0})
        curve.append({"date": t1.strftime("%Y-%m-%d"), "Adaptive": nav, "SPY": nav_b})
        top_inv = max(iw, key=iw.get) if iw else "-"
        rows.append({
            "quarter": q, "hold_to": t1.strftime("%Y-%m-%d"),
            "exposure": exposure, "lead_investor": INVESTORS[top_inv]["display"].split("(")[0].strip(),
            "n_holdings": len(combined), "net_%": round(100 * net, 2),
            "spy_%": round(100 * rb, 2), "alpha_%": round(100 * (net - rb), 2)})
        weight_log.append({"quarter": q, **{INVESTORS[k]["display"].split("(")[0].strip()[:12]: round(iw.get(k, 0), 2) for k in investors}})
        prev_w = scaled

    periods = pd.DataFrame(rows)
    if periods.empty:
        return {"error": "คำนวณ adaptive ไม่ได้"}
    cdf = pd.DataFrame(curve)
    last_dt = pd.Timestamp(periods["hold_to"].iloc[-1])
    summary = {
        "total_%": round(100 * (nav - 1), 2),
        "cagr_%": round(_cagr(nav, first_dt, last_dt), 1),
        "spy_total_%": round(100 * (nav_b - 1), 2),
        "spy_cagr_%": round(_cagr(nav_b, first_dt, last_dt), 1),
        "alpha_cagr_%": round(_cagr(nav, first_dt, last_dt) - _cagr(nav_b, first_dt, last_dt), 1),
        "max_dd_%": round(_max_drawdown(cdf["Adaptive"].tolist()), 1),
        "spy_max_dd_%": round(_max_drawdown(cdf["SPY"].tolist()), 1),
        "win_rate_%": round(100 * (periods["alpha_%"] > 0).mean(), 1),
        "pct_risk_off": round(100 * (periods["exposure"] < 1.0).mean(), 1),
        "n_quarters": len(periods),
        "period": f"{first_dt.strftime('%Y-%m')} → {last_dt.strftime('%Y-%m')}",
    }
    return {"periods": periods, "summary": summary, "curve": cdf,
            "weights": pd.DataFrame(weight_log)}


def _regime_score(px, date, ma_trend=200, ma_vol=50, ma_credit=100):
    """
    คะแนน risk-on 0..1 = สัดส่วนสัญญาณที่เป็นบวก (ดูได้ ณ วันนั้น point-in-time)
      trend  : SPY > MA200          (ตลาดขาขึ้น)
      vol    : VIX < MA50 ของตัวเอง (ความผันผวนต่ำลง)
      credit : HYG > MA100          (เครดิต high-yield แข็งแรง)
    """
    sig = []
    d = pd.Timestamp(date)
    for col, ma, bull_above in [("SPY", ma_trend, True),
                                ("^VIX", ma_vol, False),
                                ("HYG", ma_credit, True)]:
        if col not in px.columns:
            continue
        s = px[col].dropna(); s = s[s.index <= d]
        if len(s) < ma:
            continue
        cur, avg = s.iloc[-1], s.tail(ma).mean()
        ok = (cur > avg) if bull_above else (cur < avg)
        sig.append(1.0 if ok else 0.0)
    return sum(sig) / len(sig) if sig else 1.0


def run_adaptive_v2(top_n=15, lookback=4, cost_bps=COST_BPS_PER_SIDE,
                    defensive=("IEF", "GLD"), use_defensive=True):
    """
    Adaptive v2: skill-weighting + regime หลายสัญญาณรายเดือน + defensive sleeve
    - holdings (หุ้นไหน) อัปเดตรายไตรมาสตาม 13F
    - exposure (ลงเท่าไหร่) ปรับ "รายเดือน" จาก 3 สัญญาณ -> รับ V-recovery ได้ไวขึ้น
    - ส่วนที่ไม่ได้ลงหุ้น -> เข้า bond(IEF)+gold(GLD) แทนเงินสด
    """
    investors = [k for k, v in INVESTORS.items() if v["source"] == "edgar_13f"]
    quarters = _all_quarters(investors)
    if len(quarters) < lookback + 2:
        return {"error": "ไตรมาสไม่พอ"}

    books = {inv: {} for inv in investors}
    for inv in investors:
        for q in quarters:
            b = _investor_book(inv, q, top_n, "actual")
            if not b.empty:
                books[inv][q] = b
    cusips = pd.concat([b for inv in books for b in books[inv].values()]
                       )["cusip"].dropna().unique().tolist()
    cmap = map_cusips_to_tickers(cusips)
    for inv in books:
        for b in books[inv].values():
            b["ticker"] = b.apply(lambda r: r["ticker"] or cmap.get(r["cusip"]), axis=1)

    stock_tk = sorted({t for inv in books for b in books[inv].values()
                       for t in b["ticker"].dropna() if isinstance(t, str) and t})
    defensive = list(defensive) if use_defensive else []
    aux = ["SPY", "^VIX", "HYG"] + defensive

    import yfinance as yf
    start = _action_date(quarters[0]) - timedelta(days=320)
    px = yf.download(stock_tk + aux, start=start.strftime("%Y-%m-%d"),
                     interval="1d", progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame()
    px = px.sort_index()

    def price_on(t, dt):
        if t not in px.columns:
            return None
        s = px[t].dropna(); s = s[s.index <= pd.Timestamp(dt)]
        return float(s.iloc[-1]) if len(s) else None

    def ret(t, d0, d1):
        p0, p1 = price_on(t, d0), price_on(t, d1)
        return (p1 / p0 - 1) if (p0 and p1 and p0 > 0) else None

    today = pd.Timestamp(datetime.today().date())

    # ---- ผลตอบแทนรายไตรมาส (ไว้คิด skill alpha) ----
    qhold = []
    for i, q in enumerate(quarters):
        t0 = _action_date(q)
        t1 = _action_date(quarters[i + 1]) if i + 1 < len(quarters) else today
        t1 = min(t1, today)
        if pd.Timestamp(t0) < today:
            qhold.append((q, t0, t1))

    def book_w(inv, q):
        b = books[inv].get(q)
        if b is None:
            return {}
        w = {r["ticker"]: r["weight"] for _, r in b.iterrows()
             if r["ticker"] and price_on(r["ticker"], _action_date(q)) is not None}
        s = sum(w.values())
        return {k: v / s for k, v in w.items()} if s else {}

    spy_q = {}
    alpha = {inv: {} for inv in investors}
    for q, t0, t1 in qhold:
        spy_q[q] = ret("SPY", t0, t1) or 0.0
        for inv in investors:
            w = book_w(inv, q)
            if w:
                r = sum(w[t] * (ret(t, t0, t1) or 0) for t in w)
                alpha[inv][q] = r - spy_q[q]

    # ---- blended skill-weighted book ต่อไตรมาส (เริ่มหลัง warm-up) ----
    hq = [h[0] for h in qhold]
    combined_book = {}                      # q -> {ticker: weight}
    lead = {}
    for i, (q, t0, t1) in enumerate(qhold):
        if i < lookback:
            continue
        prior = hq[max(0, i - lookback):i]
        skill = {}
        for inv in investors:
            vals = [alpha[inv][pq] for pq in prior if pq in alpha[inv]]
            if vals:
                skill[inv] = sum(vals) / len(vals)
        active = {inv: book_w(inv, q) for inv in investors if book_w(inv, q)}
        if not active:
            continue
        pos = {inv: max(0.0, skill.get(inv, 0)) for inv in active}
        tot = sum(pos.values())
        iw = ({inv: pos[inv] / tot for inv in active} if tot > 0
              else {inv: 1.0 / len(active) for inv in active})
        comb = {}
        for inv, wgt in iw.items():
            for tk, w in active[inv].items():
                comb[tk] = comb.get(tk, 0) + wgt * w
        s = sum(comb.values())
        if s:
            combined_book[q] = {k: v / s for k, v in comb.items()}
            lead[q] = max(iw, key=iw.get)

    if not combined_book:
        return {"error": "ไม่มี book หลัง warm-up"}

    # ---- simulation รายเดือน ----
    bookq = sorted(combined_book)
    start_m = _action_date(bookq[0])
    months = list(pd.date_range(start=pd.Timestamp(start_m), end=today, freq="MS"))
    if not months or months[0] > pd.Timestamp(start_m):
        months = [pd.Timestamp(start_m)] + months
    bounds = []
    for j in range(len(months)):
        ms = months[j]
        me = months[j + 1] if j + 1 < len(months) else today
        if ms < today:
            bounds.append((ms, min(me, today)))

    def active_book(ms):
        cur = None
        for q in bookq:
            if _action_date(q) <= ms:
                cur = q
        return combined_book.get(cur, {}), cur

    nav = nav_b = 1.0
    prev_tgt = {}
    curve = [{"date": bounds[0][0].strftime("%Y-%m-%d"), "AdaptiveV2": 1.0, "SPY": 1.0}]
    rows = []
    for ms, me in bounds:
        book, q = active_book(ms)
        # equity return เดือนนี้
        w = {t: wt for t, wt in book.items() if ret(t, ms, me) is not None}
        s = sum(w.values()); w = {k: v / s for k, v in w.items()} if s else {}
        eq_ret = sum(w[t] * ret(t, ms, me) for t in w) if w else 0.0
        # map คะแนน -> exposure: คงพอร์ตเต็มถ้าสัญญาณส่วนใหญ่ดี
        # ลดพอร์ตจริงจังเฉพาะตอน >=2/3 สัญญาณ bearish (กันขี้ตกใจในตลาดกระทิง)
        score = _regime_score(px, ms)
        if score >= 0.65:        # >=2 สัญญาณ bullish -> ลงเต็ม
            exposure = 1.0
        elif score >= 0.32:      # 1 bullish -> ลดเล็กน้อย
            exposure = 0.6
        else:                    # ทุกสัญญาณ bearish -> ป้องกัน
            exposure = 0.3
        # defensive return
        if defensive:
            dr = [ret(d, ms, me) for d in defensive]
            dr = [x for x in dr if x is not None]
            def_ret = sum(dr) / len(dr) if dr else 0.0
        else:
            def_ret = 0.0
        # target weights ทุก sleeve (ไว้คิด turnover/cost)
        tgt = {t: exposure * wt for t, wt in w.items()}
        if defensive:
            for d in defensive:
                tgt[d] = tgt.get(d, 0) + (1 - exposure) / len(defensive)
        to = 0.5 * sum(abs(tgt.get(k, 0) - prev_tgt.get(k, 0))
                       for k in set(tgt) | set(prev_tgt))
        cost = 2 * to * cost_bps / 10000.0
        port = exposure * eq_ret + (1 - exposure) * def_ret - cost
        rb = ret("SPY", ms, me) or 0.0
        nav *= (1 + port); nav_b *= (1 + rb)
        curve.append({"date": me.strftime("%Y-%m-%d"), "AdaptiveV2": nav, "SPY": nav_b})
        rows.append({"month": ms.strftime("%Y-%m"), "exposure": round(exposure, 2),
                     "lead": INVESTORS[lead[q]]["display"].split("(")[0].strip()
                     if lead.get(q) else "-",
                     "port_%": round(100 * port, 2), "spy_%": round(100 * rb, 2)})
        prev_tgt = tgt

    periods = pd.DataFrame(rows)
    cdf = pd.DataFrame(curve)
    first_dt = pd.Timestamp(bounds[0][0]); last_dt = pd.Timestamp(bounds[-1][1])
    summary = {
        "total_%": round(100 * (nav - 1), 2),
        "cagr_%": round(_cagr(nav, first_dt, last_dt), 1),
        "spy_total_%": round(100 * (nav_b - 1), 2),
        "spy_cagr_%": round(_cagr(nav_b, first_dt, last_dt), 1),
        "alpha_cagr_%": round(_cagr(nav, first_dt, last_dt) - _cagr(nav_b, first_dt, last_dt), 1),
        "max_dd_%": round(_max_drawdown(cdf["AdaptiveV2"].tolist()), 1),
        "spy_max_dd_%": round(_max_drawdown(cdf["SPY"].tolist()), 1),
        "avg_exposure": round(periods["exposure"].mean(), 2),
        "pct_risk_off": round(100 * (periods["exposure"] < 1.0).mean(), 1),
        "win_rate_%": round(100 * (periods["port_%"] > periods["spy_%"]).mean(), 1),
        "n_months": len(periods),
        "period": f"{first_dt.strftime('%Y-%m')} → {last_dt.strftime('%Y-%m')}",
    }
    return {"periods": periods, "summary": summary, "curve": cdf}


def _rebased_stats(curve, col, common_start, label):
    """re-base NAV ที่ common_start แล้วคิด CAGR/maxDD จากจุดนั้น (เทียบแฟร์)"""
    df = curve.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] >= pd.Timestamp(common_start)].reset_index(drop=True)
    if len(df) < 2:
        return None
    base = df[col].iloc[0]
    nav = df[col] / base
    yrs = max((df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25, 1e-6)
    return {
        "strategy": label,
        "CAGR_%": round(100 * (nav.iloc[-1] ** (1 / yrs) - 1), 1),
        "total_%": round(100 * (nav.iloc[-1] - 1), 1),
        "max_dd_%": round(_max_drawdown(nav.tolist()), 1),
    }


def compare_adaptive(top_n=15, cost_bps=COST_BPS_PER_SIDE,
                     lookback=4, regime_scale=0.5):
    """เทียบ Adaptive vs Consensus vs Druckenmiller vs SPY บนช่วงเวลาเดียวกัน (rebased)"""
    from analysis.backtest import run_backtest, backtest_investor
    ad = run_adaptive(top_n=top_n, lookback=lookback, cost_bps=cost_bps,
                      regime=True, regime_scale=regime_scale)
    v2 = run_adaptive_v2(top_n=top_n, lookback=lookback, cost_bps=cost_bps)
    cons = run_backtest(top_n=top_n, cost_bps=cost_bps, compute_sectors=False)
    drk = backtest_investor("druckenmiller", top_n=top_n, cost_bps=cost_bps)
    if any("error" in r for r in [ad, v2, cons, drk]):
        return {"error": "บาง strategy คำนวณไม่ได้"}

    # จุดเริ่มร่วม = วันแรกที่ทุกกลยุทธ์มีข้อมูล (adaptive เริ่มช้าสุดเพราะ warm-up)
    common = max(pd.to_datetime(ad["curve"]["date"]).min(),
                 pd.to_datetime(v2["curve"]["date"]).min(),
                 pd.to_datetime(cons["curve"]["date"]).min(),
                 pd.to_datetime(drk["curve"]["date"]).min())
    rows = [
        _rebased_stats(v2["curve"], "AdaptiveV2", common, "★★ Adaptive v2 (regime รายเดือน+bond/gold)"),
        _rebased_stats(ad["curve"], "Adaptive", common, "★ Adaptive v1 (skill+regime ไตรมาส)"),
        _rebased_stats(cons["curve"], "Model", common, "Consensus (เท่ากันหมด)"),
        _rebased_stats(drk["curve"], "Strategy", common, "Druckenmiller เดี่ยว"),
        _rebased_stats(ad["curve"], "SPY", common, "SPY (ดัชนีอ้างอิง)"),
    ]
    table = pd.DataFrame([r for r in rows if r])
    table["alpha_CAGR_%"] = (table["CAGR_%"] -
                             table.loc[table["strategy"].str.contains("SPY"),
                                       "CAGR_%"].iloc[0]).round(1)
    return {"table": table.sort_values("CAGR_%", ascending=False).reset_index(drop=True),
            "common_start": common.strftime("%Y-%m"),
            "adaptive": ad}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    res = run_adaptive(top_n=15, lookback=4, regime=True, regime_scale=0.5)
    if "error" in res:
        print(res["error"])
    else:
        print(res["periods"].to_string())
        print("\n=== สรุป Adaptive ===")
        for k, v in res["summary"].items():
            print(f"  {k}: {v}")
