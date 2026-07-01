"""
Portfolios Tracker — Dashboard ติดตามพอร์ตนักลงทุนระดับโลก
รัน:  streamlit run app.py
"""
import re
import pandas as pd
import plotly.express as px
import streamlit as st

from config import INVESTORS, HOLDINGS_SOURCES, DB_PATH
from store import init_db, get_periods, get_holdings, get_trades
from analysis.changes import compute_changes, summary_counts
from analysis.consensus import build_consensus, overlap_matrix
from analysis.model import build_model, suggest_portfolio
from analysis.backtest import run_backtest, compare_portfolios
from analysis.adaptive import run_adaptive, compare_adaptive
from analysis.insights import quant_metrics, qualitative_narrative, ai_analysis
from trackers.edgar_13f import search_13f_managers

st.set_page_config(page_title="Portfolios Tracker", layout="wide", page_icon="📊")


# ---------------------------------------------------------------- ระบบรหัสผ่าน
def check_password():
    """ล็อกแอปด้วยรหัสผ่านจาก st.secrets (APP_PASSWORD). เว้นว่าง = ไม่ล็อก (รันในเครื่อง)"""
    from config import APP_PASSWORD
    if not APP_PASSWORD:
        return True
    if st.session_state.get("auth_ok"):
        return True
    st.title("🔒 Portfolios Tracker")
    pw = st.text_input("ใส่รหัสผ่านเพื่อเข้าใช้งาน", type="password")
    if pw:
        if pw == APP_PASSWORD:
            st.session_state.auth_ok = True
            st.rerun()
        else:
            st.error("รหัสผ่านไม่ถูกต้อง")
    st.stop()


check_password()
init_db()

CHANGE_COLORS = {"NEW": "#16a34a", "ADD": "#65a30d", "HOLD": "#64748b",
                 "TRIM": "#ea580c", "EXIT": "#dc2626"}


# ---- cache ผลคำนวณหนัก (กัน timeout/คำนวณซ้ำบน cloud) — TTL 6 ชม. ----
@st.cache_data(ttl=21600, show_spinner=False)
def cached_backtest(top_n, cost_bps, compute_sectors):
    return run_backtest(top_n=top_n, cost_bps=cost_bps, compute_sectors=compute_sectors)


@st.cache_data(ttl=21600, show_spinner=False)
def cached_compare_portfolios(top_n, cost_bps):
    return compare_portfolios(top_n=top_n, cost_bps=cost_bps)


@st.cache_data(ttl=21600, show_spinner=False)
def cached_compare_adaptive(top_n, lookback, regime_scale):
    return compare_adaptive(top_n=top_n, lookback=lookback, regime_scale=regime_scale)


def fmt_usd(v):
    if pd.isna(v):
        return "-"
    for unit, div in [("B", 1e9), ("M", 1e6), ("K", 1e3)]:
        if abs(v) >= div:
            return f"${v/div:.2f}{unit}"
    return f"${v:.0f}"


def rnd(series, n=2):
    """ปัดเศษอย่างปลอดภัย — กันคอลัมน์ที่มี None/NA ทำให้ .round() พัง"""
    return pd.to_numeric(series, errors="coerce").round(n)


ARCHIVE_AGE_DAYS = 365   # พอร์ตที่ข้อมูลล่าสุดเก่ากว่านี้ = เก็บเข้าคลัง (ซ่อนจากทุกหน้าหลัก ไว้ศึกษา)


def _latest_age_days(inv):
    ps = get_periods(inv)
    if not ps:
        return None
    return (pd.Timestamp.today().normalize() - pd.Timestamp(ps[0])).days


def is_archived(inv):
    """True ถ้าข้อมูลล่าสุดเก่ากว่า 1 ปี (เช่นกองที่เลิกยื่น 13F เช่น Melvin/Greenlight)"""
    age = _latest_age_days(inv)
    return age is not None and age > ARCHIVE_AGE_DAYS


from pathlib import Path as _Path
_ASSET_DIR = _Path(__file__).resolve().parent / "assets"


@st.cache_data(show_spinner=False)
def _xavier_img():
    try:
        from PIL import Image
        p = _ASSET_DIR / "xavier.webp"
        return Image.open(p) if p.exists() else None
    except Exception:
        return None


def render_infographic(port, total_value, title="BenzSpringfield",
                       subtitle="Charles Francis Xavier Portfolio"):
    """การ์ดโดนัทนำเสนอสไตล์ Leverage Shares + รูปตรงกลาง"""
    import plotly.graph_objects as go
    labels = port["ticker"].fillna(port["issuer"]).tolist()
    values = port["alloc_pct"].tolist()
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.63, sort=False, direction="clockwise",
        textinfo="label+percent", textposition="outside",
        marker=dict(line=dict(color="white", width=2)),
        hovertemplate="%{label}: %{value:.1f}%<extra></extra>",
    ))
    img = _xavier_img()
    if img is not None:
        fig.add_layout_image(dict(source=img, xref="paper", yref="paper",
            x=0.5, y=0.5, sizex=0.26, sizey=0.40,
            xanchor="center", yanchor="middle", layer="above"))
    fig.update_layout(
        showlegend=False, height=660,
        title=dict(
            text=f"<b>{title}</b><br><span style='font-size:15px'>{subtitle}</span>",
            x=0.5, xanchor="center", y=0.97),
        margin=dict(t=115, b=65, l=90, r=90),
        annotations=[
            dict(text=f"<b>Portfolio Value ≈ {fmt_usd(total_value)}</b>", showarrow=False,
                 x=0.5, y=1.07, xref="paper", yref="paper", font=dict(size=15)),
            dict(text="Capital at risk.", showarrow=False, x=0.0, y=-0.09,
                 xref="paper", yref="paper", xanchor="left", font=dict(size=13)),
            dict(text="Source: SEC 13F · Smart-Money consensus model", showarrow=False,
                 x=1.0, y=-0.09, xref="paper", yref="paper", xanchor="right",
                 font=dict(size=11, color="gray")),
        ],
    )
    return fig


# ---------------------------------------------------------------- sidebar
st.sidebar.title("📊 Portfolios Tracker")
st.sidebar.caption("ถอดรื้อพอร์ตนักลงทุนระดับโลก")
page = st.sidebar.radio("เมนู", [
    "🏠 ภาพรวม", "👤 รายนักลงทุน", "🔁 การเปลี่ยนแปลง",
    "🤝 Consensus", "🧠 Model & พอร์ตแนะนำ", "📈 Backtest",
    "🧬 Adaptive Model", "🏛️ Congressional",
])

if not DB_PATH.exists() or DB_PATH.stat().st_size < 5000:
    st.sidebar.warning("ยังไม่มีข้อมูล — รัน `python update_data.py` ก่อน")


# ---------------------------------------------------------------- ภาพรวม
if page == "🏠 ภาพรวม":
    st.title("ภาพรวมนักลงทุนที่ติดตาม")
    st.caption("ข้อมูลฟรีจาก SEC 13F · arkfunds.io · House Clerk — เป็นเครื่องมือศึกษา ไม่ใช่คำแนะนำลงทุน")

    # ค้นหา + ปุ่มแสดง/ซ่อน
    fc1, fc2 = st.columns([3, 2])
    query = fc1.text_input("🔍 ค้นหาพอร์ต (ชื่อนักลงทุน / สไตล์)", "").strip().lower()
    show_hidden = fc2.toggle("แสดงกองที่ซ่อนไว้ (ไม่มีข้อมูล / เก่ากว่า 1 ปี)", value=False)

    # 📌 ปักหมุดพอร์ตที่สนใจ (ขึ้นก่อนเสมอในเซสชันนี้)
    pin_opts = [k for k in INVESTORS if not is_archived(k) and get_periods(k)]
    pinned = st.multiselect(
        "📌 ปักหมุดพอร์ตที่สนใจ (ขึ้นก่อน)", pin_opts, default=[],
        format_func=lambda k: INVESTORS[k]["display"].split("(")[0].strip())

    def _is_hidden(k):
        return is_archived(k) or not get_periods(k)   # เก่ากว่า 1 ปี หรือ ไม่มี holdings

    def _match(cfg):
        if not query:
            return True
        return query in (cfg["display"] + " " + cfg.get("style", "")).lower()

    def render_card(key, cfg, col):
        with col:
            periods = get_periods(key)
            tag = " 🗄️" if is_archived(key) else ""
            st.markdown(f"**{cfg['display'].split('(')[0]}**{tag}")
            st.caption(cfg["style"])
            if periods:
                df = get_holdings(key, periods[0])
                st.metric("มูลค่าพอร์ต", fmt_usd(df["value_usd"].sum()),
                          f"{len(df)} ตำแหน่ง")
                age = _latest_age_days(key)
                st.caption(f"งวด {periods[0]}" +
                           (f" · เก่า ~{age/365.25:.1f} ปี" if is_archived(key) else ""))
            elif cfg["source"] == "methodology":
                st.info(f"ใช้ระบบสัญญาณ\n`{cfg.get('skill')}`")
            elif cfg["source"] == "congress":
                st.caption("ดูรายละเอียดในหน้า 🏛️ Congressional")
            else:
                st.caption("— ยังไม่มีข้อมูล —")
            st.caption(f"📌 {cfg['notes']}")

    def render_cards(items, per_row=4):
        for i in range(0, len(items), per_row):
            row = items[i:i + per_row]
            cols = st.columns(len(row))
            for col, (k, c) in zip(cols, row):
                render_card(k, c, col)

    active = [(k, c) for k, c in INVESTORS.items() if not _is_hidden(k)]
    hidden = [(k, c) for k, c in INVESTORS.items() if _is_hidden(k)]
    # เรียงพอร์ตที่ปักหมุดขึ้นก่อน
    if pinned:
        active.sort(key=lambda kc: (kc[0] not in pinned))

    if query:
        # โหมดค้นหา — ค้นทั้งหมด (รวมกองที่ซ่อน) ให้เจอสิ่งที่สนใจ
        matches = [(k, c) for k, c in INVESTORS.items() if _match(c)]
        st.caption(f"ผลค้นหา “{query}”: {len(matches)} พอร์ต")
        if matches:
            render_cards(matches)
        else:
            st.info("ไม่พบพอร์ตที่ค้นหา — ลองคำอื่น หรือเคลียร์ช่องค้นหา")
    else:
        render_cards(active)
        if hidden:
            if show_hidden:
                st.markdown("##### 🗄️ กองที่ซ่อนไว้ (ไม่มีข้อมูล / เก่ากว่า 1 ปี — ไว้ศึกษา)")
                st.caption("ไม่ถูกนำมาคำนวณ Consensus/Model/พอร์ตแนะนำ")
                render_cards(hidden)
            else:
                with st.popover(f"🗄️ คลังพอร์ตที่ซ่อนไว้ ({len(hidden)})"):
                    st.caption("กดปุ่ม 'แสดงกองที่ซ่อนไว้' ด้านบนเพื่อกางเป็นการ์ด "
                               "หรือดูสรุปสั้นๆ ที่นี่ · ไม่ถูกนำมาคำนวณโมเดล/พอร์ตแนะนำ")
                    for key, cfg in hidden:
                        ps = get_periods(key)
                        st.divider()
                        st.markdown(f"**{cfg['display']}**")
                        st.caption(cfg["style"])
                        if ps:
                            age = _latest_age_days(key)
                            st.write(f"📅 ข้อมูลล่าสุด **{ps[0]}** (เก่า ~{age/365.25:.1f} ปี)")
                        else:
                            st.write(f"แหล่ง: {cfg['source']} (ไม่มี holdings)")
                        st.caption(f"📌 {cfg['notes']}")

    # ➕ ค้นหา/เพิ่มพอร์ตใหม่จาก SEC (ฟรี)
    with st.expander("➕ ค้นหาพอร์ตใหม่จาก SEC (เพิ่มนักลงทุนที่สนใจ)"):
        st.caption("ค้นชื่อกองทุน/ผู้จัดการที่ยื่น 13F ในระบบ SEC — เจอแล้วก็อปโค้ดไปเพิ่มใน `config.py`")
        qname = st.text_input("ชื่อกอง/ผู้จัดการ (อังกฤษ)",
                              placeholder="เช่น Pershing Square, Tiger Global, Bridgewater")
        if qname:
            with st.spinner("ค้นหาใน SEC..."):
                results = search_13f_managers(qname, limit=6)
            if not results:
                st.info("ไม่พบ — ลองพิมพ์ชื่อให้ตรงขึ้น (เป็นภาษาอังกฤษ)")
            for r in results:
                fresh = (r["last_report"] or "") >= "2025-01-01"
                st.markdown(f"**{r['name']}** · CIK `{r['cik']}` · "
                            f"13F ล่าสุด {r['last_report'] or '—'} "
                            f"{'🟢 ยังยื่นอยู่' if fresh else '🔴 อาจเลิกยื่น'}")
                slug = re.sub(r'[^a-z0-9]+', '_', r['name'].lower()).strip('_')[:20] or "new_fund"
                st.code(f'''    "{slug}": {{
        "display": "{r['name']}",
        "source": "edgar_13f",
        "cik": "{r['cik']}",
        "style": "—",
        "notes": "เพิ่มจาก SEC finder",
        "skill": None,
    }},''', language="python")
            if results:
                st.caption("วิธีเพิ่ม: ก็อปบล็อกข้างบนไปวางใน `INVESTORS` ใน config.py → "
                           "รัน `python fetch_history.py` (หรือ `update_data.py`) → push → แอปอัปเดตเอง "
                           "(ดู [DEVELOP.md](DEVELOP.md))")

    st.divider()
    st.subheader("ความเสี่ยง/ข้อจำกัดของข้อมูล")
    st.markdown("""
- **13F** เห็นเฉพาะ long หุ้น US, รายไตรมาส, ดีเลย์ ~45 วัน — ไม่เห็น short/options/จังหวะเข้า-ออก
- **Two Sigma** เป็นควอนต์ พอร์ตกระจายมาก → ใช้ดูธีม ไม่เหมาะถอดจังหวะ
- **ARK** รายวัน = ละเอียดสุด · **Pelosi** ได้แค่รายการยื่น (รายละเอียดอยู่ใน PDF)
- **Minervini** ไม่มี filing → ใช้ระบบสัญญาณแทน
""")


# ---------------------------------------------------------------- รายนักลงทุน
elif page == "👤 รายนักลงทุน":
    st.title("พอร์ตรายนักลงทุน")
    holders = [k for k in INVESTORS if get_periods(k) and not is_archived(k)]
    if not holders:
        st.warning("ยังไม่มีข้อมูล — รัน `python update_data.py`")
    else:
        key = st.selectbox("เลือกนักลงทุน", holders,
                           format_func=lambda k: INVESTORS[k]["display"])
        periods = get_periods(key)
        period = st.selectbox("งวด", periods)
        df = get_holdings(key, period).sort_values("value_usd", ascending=False)
        c1, c2, c3 = st.columns(3)
        c1.metric("มูลค่ารวม", fmt_usd(df["value_usd"].sum()))
        c2.metric("จำนวนตำแหน่ง", len(df))
        c3.metric("Top 10 กระจุก", f"{df.head(10)['weight_pct'].sum():.1f}%")

        top = df.head(20)
        label = top["ticker"].fillna(top["issuer"])
        fig = px.bar(top, x="value_usd", y=label, orientation="h",
                     labels={"value_usd": "มูลค่า (USD)", "y": ""},
                     title=f"Top 20 — {INVESTORS[key]['display']}")
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=600)
        st.plotly_chart(fig, use_container_width=True)

        show = df[["ticker", "issuer", "value_usd", "shares", "weight_pct", "fund"]].copy()
        show["value_usd"] = show["value_usd"].map(fmt_usd)
        show["weight_pct"] = rnd(show["weight_pct"], 2)
        st.dataframe(show, use_container_width=True, height=400)


# ---------------------------------------------------------------- การเปลี่ยนแปลง
elif page == "🔁 การเปลี่ยนแปลง":
    st.title("การเปลี่ยนแปลงพอร์ต (งวดล่าสุด vs งวดก่อน)")
    holders = [k for k in HOLDINGS_SOURCES
               if len(get_periods(k)) >= 1 and not is_archived(k)]
    if not holders:
        st.warning("ยังไม่มีข้อมูล")
    else:
        key = st.selectbox("เลือกนักลงทุน", holders,
                           format_func=lambda k: INVESTORS[k]["display"])
        ch = compute_changes(key)
        if ch.empty:
            st.info("ไม่มีข้อมูลเทียบ")
        else:
            counts = summary_counts(ch)
            cols = st.columns(len(CHANGE_COLORS))
            for col, k in zip(cols, CHANGE_COLORS):
                col.metric(k, counts.get(k, 0))

            view = st.radio("กรอง", ["ทั้งหมด", "เฉพาะที่เคลื่อนไหว", "NEW", "EXIT", "ADD", "TRIM"],
                            horizontal=True)
            d = ch.copy()
            if view == "เฉพาะที่เคลื่อนไหว":
                d = d[d["change"] != "HOLD"]
            elif view != "ทั้งหมด":
                d = d[d["change"] == view]

            label_col = "ticker" if d["ticker"].notna().any() else "issuer"
            cols_show = [c for c in [label_col, "issuer", "change", "shares_old",
                         "shares_new", "pct_change_shares", "value_new"] if c in d]
            disp = d[cols_show].copy()
            if "value_new" in disp:
                disp["value_new"] = disp["value_new"].map(fmt_usd)
            if "pct_change_shares" in disp:
                disp["pct_change_shares"] = rnd(disp["pct_change_shares"], 1)
            st.dataframe(disp, use_container_width=True, height=500)
            st.caption(f"เทียบ {ch['period_old'].iloc[0]} → {ch['period_new'].iloc[0]}")

            # ---- บทวิเคราะห์ไอเดียเจ้าของพอร์ต (Hybrid) ----
            st.divider()
            st.subheader("🧠 บทวิเคราะห์ไอเดียเจ้าของพอร์ต")
            m = quant_metrics(ch)
            if m:
                q1, q2, q3, q4 = st.columns(4)
                q1.metric("ท่าทีสุทธิ", m["net_stance"])
                q2.metric("เปิดใหม่ / ขายออก", f"{m['new']} / {m['exit']}")
                q3.metric("Turnover", f"{m['turnover_pct']:.0f}%")
                q4.metric("กระจุก Top10",
                          f"{m['concentration_top10_pct']:.0f}%" if m.get("concentration_top10_pct") else "-")

                # ฟีเจอร์ AI (เสียเงิน) ถูกปิด/ซ่อนไว้ — จะโผล่อัตโนมัติเมื่อตั้ง ANTHROPIC_API_KEY ใน Secrets
                from config import ANTHROPIC_API_KEY
                if ANTHROPIC_API_KEY:
                    tab_q, tab_ai = st.tabs(["📋 เชิงคุณภาพ (สูตรฟรี)", "🤖 วิเคราะห์เชิงลึกด้วย AI"])
                    with tab_q:
                        st.markdown(qualitative_narrative(key, m, INVESTORS[key]["display"]))
                    with tab_ai:
                        st.caption("วิเคราะห์เชิงลึกด้วย Claude — มีค่าใช้จ่ายต่อครั้ง")
                        if st.button("🤖 วิเคราะห์เชิงลึกด้วย Claude", key="ai_btn"):
                            with st.spinner("Claude กำลังวิเคราะห์..."):
                                txt, err = ai_analysis(key, INVESTORS[key]["display"], m, ch,
                                                       ANTHROPIC_API_KEY)
                            if err:
                                st.error(err)
                            else:
                                st.markdown(txt)
                                st.caption("⚠️ สร้างโดย AI — เป็นการตีความ ไม่ใช่คำแนะนำลงทุน")
                else:
                    # ไม่มี key = ใช้บทวิเคราะห์สูตรฟรีอย่างเดียว (ปุ่ม AI ซ่อนไว้)
                    st.markdown(qualitative_narrative(key, m, INVESTORS[key]["display"]))


# ---------------------------------------------------------------- Consensus
elif page == "🤝 Consensus":
    st.title("หุ้นที่เงินใหญ่ถือซ้ำกัน (Consensus)")
    con = build_consensus(min_investors=1)
    if con.empty:
        st.warning("ยังไม่มีข้อมูล")
    else:
        min_inv = st.slider("ถือซ้ำกันอย่างน้อย (กี่เจ้า)", 1,
                            int(con["n_investors"].max()), 1)
        c = con[con["n_investors"] >= min_inv]
        st.metric("จำนวนหุ้นที่เข้าเกณฑ์", len(c))
        show = c[["issuer", "ticker", "n_investors", "investors",
                  "total_value", "avg_weight"]].copy()
        show["total_value"] = show["total_value"].map(fmt_usd)
        show["avg_weight"] = rnd(show["avg_weight"], 2)
        st.dataframe(show, use_container_width=True, height=420)

        st.subheader("ตารางถือซ้ำระหว่างนักลงทุน (overlap)")
        mat = overlap_matrix()
        if not mat.empty:
            st.dataframe(mat, use_container_width=True)


# ---------------------------------------------------------------- Model
elif page == "🧠 Model & พอร์ตแนะนำ":
    st.title("🧠 Smart-Money Model")
    st.caption("รวมสัญญาณ: consensus + conviction + แรงซื้อสุทธิ + โมเมนตัมราคา — *ไม่ใช่คำแนะนำการลงทุน*")

    # ---- ความโปร่งใส: ข้อมูลที่ป้อนเข้าโมเดลมาจาก ณ วันที่ใดบ้าง ----
    today = pd.Timestamp.today().normalize()
    asof = []
    for inv in HOLDINGS_SOURCES:
        ps = get_periods(inv)
        if not ps:
            continue
        d = pd.Timestamp(ps[0])
        lag = (today - d).days
        included = lag <= 200          # ให้ตรงกับ LIVE_MAX_AGE_DAYS ใน consensus.py
        asof.append({
            "นักลงทุน": INVESTORS[inv]["display"].split("(")[0].strip(),
            "แหล่ง": INVESTORS[inv]["source"],
            "ข้อมูล ณ วันที่": ps[0],
            "อายุข้อมูล (วัน)": lag,
            "สถานะ": ("🟢 สด" if lag <= 7 else "🟡 ไตรมาสล่าสุด") if included
                     else "🔴 ตัดออก (เก่า/เลิกยื่น)",
        })
    if asof:
        adf = pd.DataFrame(asof).sort_values("ข้อมูล ณ วันที่")
        oldest, newest = adf["ข้อมูล ณ วันที่"].min(), adf["ข้อมูล ณ วันที่"].max()
        st.info(f"📅 **ข้อมูลในโมเดลมาจากช่วง {oldest} → {newest}** "
                f"· วันนี้ {today.date()} · 13F ดีเลย์ตามกฎ SEC ~45 วัน "
                f"→ โมเดลสะท้อนพอร์ต ณ สิ้นไตรมาสล่าสุดที่เปิดเผย ไม่ใช่ ณ วันนี้")
        with st.expander("📆 ดูวันที่ข้อมูลของแต่ละนักลงทุน (อายุข้อมูลที่ใช้)"):
            st.dataframe(adf, use_container_width=True, hide_index=True)
            st.caption("🟢 สด = ภายใน 7 วัน (ARK รายวัน) · 🟡 = 13F ไตรมาสล่าสุด · "
                       "🔴 = เก่ากว่า 200 วัน **ถูกตัดออกจากคำแนะนำสด** (เช่นกองที่เลิกยื่น 13F) "
                       "แต่ยังใช้ใน Backtest แบบ point-in-time ได้ปกติ")

    c1, c2, c3 = st.columns(3)
    min_inv = c1.slider("ถือซ้ำขั้นต่ำ", 1, 4, 1)
    use_mom = c2.checkbox("ใช้โมเมนตัมราคา (yfinance, ช้าหน่อย)", value=False)
    top_n = c3.slider("จำนวนหุ้นในตาราง", 5, 40, 20)

    with st.spinner("กำลังคำนวณ..."):
        model = build_model(min_investors=min_inv, use_momentum=use_mom, top_n=top_n)
    if model.empty:
        st.warning("ยังไม่มีข้อมูล — รัน `python update_data.py`")
    else:
        disp = model.copy()
        disp["total_value"] = disp["total_value"].map(fmt_usd)
        for c in ["avg_weight", "buying_signal", "momentum_6m", "score"]:
            if c in disp:
                disp[c] = rnd(disp[c], 2)
        st.dataframe(disp, use_container_width=True, height=420)

        st.subheader("📦 พอร์ตแนะนำ (ถ่วงน้ำหนักตามคะแนน)")
        n_port = st.slider("จำนวนหุ้นในพอร์ต", 5, 20, 10)
        port = suggest_portfolio(model, n=n_port)

        # การ์ดนำเสนอสไตล์ infographic
        port_value = float(model.head(n_port)["total_value"].sum())
        info_fig = render_infographic(port, port_value)
        st.plotly_chart(info_fig, use_container_width=True)
        # ปุ่มดาวน์โหลดการ์ดเป็นรูป PNG (ฟรี ผ่าน kaleido)
        try:
            png = info_fig.to_image(format="png", width=1000, height=1000, scale=2)
            st.download_button("⬇️ ดาวน์โหลดการ์ดเป็นรูป PNG", png,
                               "charles_xavier_portfolio.png", "image/png")
        except Exception:
            st.caption("💡 ดาวน์โหลด PNG ต้องมีแพ็กเกจ kaleido (มีใน requirements แล้ว "
                       "— บน cloud ใช้ได้ ในเครื่องถ้ายังไม่ลง รัน `pip install kaleido`)")

        cc1, cc2 = st.columns([2, 1])
        with cc1:
            pshow = port.copy()
            pshow["alloc_pct"] = pshow["alloc_pct"].astype(str) + "%"
            st.dataframe(pshow, use_container_width=True, height=400)
        with cc2:
            fig = px.pie(port, values="alloc_pct",
                         names=port["ticker"].fillna(port["issuer"]),
                         title="สัดส่วนพอร์ตแนะนำ")
            st.plotly_chart(fig, use_container_width=True)
        st.info("⚠️ พอร์ตนี้สร้างจากข้อมูลในอดีตที่ดีเลย์ ใช้เพื่อการศึกษาเท่านั้น "
                "ไม่ใช่คำแนะนำซื้อขาย โปรดตัดสินใจด้วยตัวเอง/ปรึกษาผู้เชี่ยวชาญ")


# ---------------------------------------------------------------- Backtest
elif page == "📈 Backtest":
    st.title("📈 Backtest — Smart-Money Model vs SPY")
    st.caption("ทดสอบ point-in-time: พอร์ตของไตรมาส Q ลงมือได้จริงที่ Q+45 วัน (ตามดีเลย์ 13F) · "
               "เฉพาะกอง 13F · *ผลย้อนหลังไม่การันตีอนาคต*")
    c1, c2, c3 = st.columns(3)
    top_n = c1.slider("จำนวนหุ้นในพอร์ตแต่ละไตรมาส", 5, 30, 15)
    cost_bps = c2.slider("ต้นทุนต่อด้าน (bps)", 0, 100, 20,
                         help="ค่าคอม+slippage ต่อการซื้อหรือขาย (20 = 0.20%)")
    use_sec = c3.checkbox("วิเคราะห์ sector (ช้าครั้งแรก)", value=True)
    if st.button("▶️ รัน Backtest", type="primary"):
        with st.spinner("กำลังคำนวณ (ดึงราคาย้อนหลังจาก yfinance)..."):
            res = cached_backtest(top_n, cost_bps, use_sec)
        if "error" in res:
            st.error(res["error"])
        else:
            s = res["summary"]
            st.caption(f"ช่วงทดสอบ {s['period']} · {s['n_rebalances']} รอบ rebalance · "
                       f"เฉลี่ย {s['avg_holdings']} ตัว/พอร์ต")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Model (สุทธิ)", f"{s['model_net_total_%']}%",
                      delta=f"alpha {s['alpha_vs_spy_%']}%")
            c2.metric("Equal-Weight", f"{s['eqw_net_total_%']}%")
            c3.metric("SPY", f"{s['spy_total_%']}%")
            c4.metric("ชนะ SPY", f"{s['win_rate_vs_spy_%']}%")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("CAGR (Model)", f"{s['model_cagr_%']}%/ปี")
            c2.metric("CAGR (SPY)", f"{s['spy_cagr_%']}%/ปี")
            c3.metric("Max Drawdown", f"{s['model_max_dd_%']}%",
                      help=f"SPY = {s['spy_max_dd_%']}%")
            c4.metric("ต้นทุนสะสม", f"−{s['total_cost_drag_%']}%")

            curve_long = res["curve"].melt("date", var_name="กลยุทธ์", value_name="NAV")
            fig = px.line(curve_long, x="date", y="NAV", color="กลยุทธ์",
                          title="เติบโตของเงินลงทุน $1 (สุทธิหลังต้นทุน)", markers=True)
            st.plotly_chart(fig, use_container_width=True)

            cc1, cc2 = st.columns([3, 2])
            with cc1:
                st.subheader("ผลแต่ละไตรมาส")
                st.dataframe(res["periods"], use_container_width=True, height=380)
            with cc2:
                st.subheader("Sector exposure เฉลี่ย")
                if not res["sectors"].empty:
                    fig2 = px.pie(res["sectors"], values="avg_weight_%",
                                  names="sector", title=None)
                    st.plotly_chart(fig2, use_container_width=True)
                    top_sec = res["sectors"].iloc[0]
                    st.caption(f"กระจุกที่ {top_sec['sector']} {top_sec['avg_weight_%']}% "
                               "— ดูว่า alpha มาจากเลือกหุ้นหรือโดนธีมพาไป")
                else:
                    st.caption("ไม่ได้คำนวณ sector")
            st.info("⚠️ คิดต้นทุนค่าคอม+slippage ตาม turnover แล้ว แต่ยังไม่รวมภาษี/market impact "
                    "และใช้ราคาปิด — เพื่อการศึกษา ไม่ใช่ผลตอบแทนที่รับประกัน")
    else:
        st.info("กดปุ่มด้านบนเพื่อเริ่ม (ใช้เวลาสักครู่เพราะต้องดึงราคาย้อนหลัง)")

    st.divider()
    st.subheader("🆚 เทียบ backtest แยกรายพอร์ต")
    st.caption("ถ้าลอกพอร์ตของแต่ละคนเป๊ะ (ตามน้ำหนักจริง top-N) จะได้เท่าไหร่ — "
               "ดูว่า alpha มาจากใคร · *ระวัง: แต่ละคนช่วงเวลาต่างกัน เทียบที่ CAGR/alpha ไม่ใช่ total*")
    if st.button("▶️ รันเทียบทุกพอร์ต"):
        with st.spinner("กำลังคำนวณทุกพอร์ต..."):
            comp = cached_compare_portfolios(top_n, cost_bps)
        tbl = comp["table"]
        st.dataframe(tbl, use_container_width=True)
        fig = px.bar(tbl, x="portfolio", y="alpha_CAGR_%", color="alpha_CAGR_%",
                     color_continuous_scale="RdYlGn", title="Alpha ต่อปี เทียบ SPY (%)")
        st.plotly_chart(fig, use_container_width=True)
        st.info("ข้อสังเกต: Two Sigma (ควอนต์) มักแพ้ดัชนีเพราะ 13F เป็น noise · "
                "พอร์ตที่ช่วงสั้นและอยู่ในตลาดกระทิงจะดู CAGR สูงผิดปกติ (regime bias)")


# ---------------------------------------------------------------- Adaptive
elif page == "🧬 Adaptive Model":
    st.title("🧬 Adaptive Model — ปรับตามฝีมือ + สภาพตลาด")
    st.caption("Skill-weighting (trailing alpha, point-in-time) + Regime gate (SPY vs MA) "
               "เพื่อรับความผันผวน · *เพื่อการศึกษา*")
    c1, c2, c3, c4 = st.columns(4)
    top_n = c1.slider("หุ้น/พอร์ต", 5, 30, 15)
    lookback = c2.slider("Lookback (ไตรมาส)", 2, 8, 4,
                         help="ดูฝีมือย้อนหลังกี่ไตรมาสเพื่อถ่วงน้ำหนัก")
    regime_ma = c3.selectbox("เส้น Regime (วัน)", [100, 150, 200], index=2)
    regime_scale = c4.slider("พอร์ตช่วงขาลง", 0.0, 1.0, 0.5, 0.25,
                             help="0 = ออกเป็นเงินสดทั้งหมด, 1 = ไม่ลดพอร์ต")

    if st.button("▶️ รัน Adaptive + เทียบทุก Model", type="primary"):
        with st.spinner("กำลังคำนวณ (หลาย model ใช้เวลาสักครู่)..."):
            cmp = cached_compare_adaptive(top_n, lookback, regime_scale)
            ad = cmp.get("adaptive") if "error" not in cmp else None
        if "error" in cmp:
            st.error(cmp["error"])
        else:
            st.subheader(f"เทียบทุก Model (rebased จาก {cmp['common_start']})")
            tbl = cmp["table"].copy()
            tbl["Calmar"] = (tbl["CAGR_%"] / tbl["max_dd_%"].abs()).round(2)
            st.dataframe(tbl, use_container_width=True)
            cc1, cc2 = st.columns(2)
            with cc1:
                fig = px.bar(tbl, x="strategy", y="CAGR_%", color="strategy",
                             title="CAGR (%)")
                fig.update_layout(showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            with cc2:
                fig = px.bar(tbl, x="strategy", y="Calmar", color="Calmar",
                             color_continuous_scale="Greens",
                             title="Calmar (ผลตอบแทน/ความเสี่ยง) — ยิ่งสูงยิ่งดี")
                st.plotly_chart(fig, use_container_width=True)
            st.success("💡 บทเรียน: skill-weighting ไม่เพิ่มกำไรเหนือ consensus ธรรมดา "
                       "(กับดัก performance-chasing) แต่ regime gate ลด drawdown ชัด "
                       "→ Calmar ดีสุด = คุ้มความเสี่ยงสุด")

            if ad:
                s = ad["summary"]
                st.subheader("รายละเอียด Adaptive (skill+regime)")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("CAGR", f"{s['cagr_%']}%")
                m2.metric("Max DD", f"{s['max_dd_%']}%", help=f"SPY {s['spy_max_dd_%']}%")
                m3.metric("เวลาที่ risk-off", f"{s['pct_risk_off']}%")
                m4.metric("ชนะ SPY", f"{s['win_rate_%']}%")
                cv = ad["curve"].melt("date", var_name="กลยุทธ์", value_name="NAV")
                st.plotly_chart(px.line(cv, x="date", y="NAV", color="กลยุทธ์",
                                markers=True, title="NAV: Adaptive vs SPY"),
                                use_container_width=True)
                st.markdown("**ผู้นำพอร์ตแต่ละไตรมาส + exposure** (เห็น model สลับคน/ลดพอร์ตเอง)")
                st.dataframe(ad["periods"][["quarter", "lead_investor", "exposure",
                             "net_%", "spy_%", "alpha_%"]], use_container_width=True, height=300)
    else:
        st.info("กดปุ่มเพื่อรัน — จะเทียบ Adaptive vs Consensus vs Druckenmiller vs SPY")


# ---------------------------------------------------------------- Congressional
elif page == "🏛️ Congressional":
    st.title("🏛️ Congressional Trades")
    tr = get_trades("pelosi")
    if tr.empty:
        st.warning("ยังไม่มีข้อมูล — รัน `python update_data.py`")
    else:
        st.caption("รายการ Periodic Transaction Report (PTR) — รายละเอียดหุ้นอยู่ในไฟล์ PDF (กดลิงก์)")
        for _, r in tr.iterrows():
            st.markdown(f"- **{r['txn_date']}** · {r['issuer']} → "
                        f"[เปิด PDF]({r['raw']})")
        st.info("การแกะ ticker/จำนวน/ซื้อ-ขาย จาก PDF อัตโนมัติ = เฟสถัดไป (ต้อง OCR)")
