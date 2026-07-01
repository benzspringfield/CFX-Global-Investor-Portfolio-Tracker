"""
บทวิเคราะห์การเปลี่ยนแปลงพอร์ต (Hybrid)
- เชิงปริมาณ (quant)      : คำนวณเมตริกจาก diff งวด (turnover, ความกระจุก, add/trim/new/exit)
- เชิงคุณภาพ (qualitative): ตีความ 'ไอเดียเจ้าของพอร์ต' ผ่านเลนส์สไตล์รายคน
                            (อิง skill druckenmiller-trader/minervini-trader + สไตล์ที่รู้จัก)
- AI เสริม (optional)     : ถ้ามี ANTHROPIC_API_KEY ใน secrets -> วิเคราะห์เชิงลึกด้วย Claude

⚠️ บทวิเคราะห์คือ 'การตีความ' ไม่ใช่ข้อเท็จจริงหรือคำแนะนำ — 13F ดีเลย์ ~45 วัน
"""
import pandas as pd

# ---------------------------------------------------------------------------
# เลนส์สไตล์รายนักลงทุน (เข้ารหัสจากเสาความรู้: skill + สไตล์ที่รู้จัก)
# ---------------------------------------------------------------------------
STYLE_LENS = {
    "druckenmiller": {
        "headline": "มาโคร + คอนเซนเทรตสูง · ride winners, cut losers เร็ว",
        "principles": [
            "ให้น้ำหนักภาพใหญ่ (สภาพคล่อง/ดอกเบี้ย/เศรษฐกิจ) มากกว่ารายตัว",
            "กล้าลงหนักไม่กี่ตัวเมื่อมั่นใจ (concentration)",
            "ถือตัวที่ถูก เพิ่มตัวชนะ ตัดตัวแพ้ไว — ไม่ยึดติด",
            "สลับพอร์ตเร็วเมื่อมุมมองมาโครเปลี่ยน ('quiver of arrows')",
        ],
        "read_buy": "การเพิ่ม/เปิดใหม่ = มุมมอง risk-on หรือธีมมาโครใหม่ที่มั่นใจ",
        "read_sell": "การลด/ขายออก = cut loss, ล็อกกำไร, หรือมองภาพมาโครเปลี่ยน (ไม่ใช่แค่รายตัว)",
    },
    "situational_awareness": {
        "headline": "ธีสิส AGI/AI เข้มข้น · คอนเซนเทรตในธีมเดียว",
        "principles": [
            "เดิมพันจากมุมมองว่า AI/AGI จะเปลี่ยนโลก (thesis-driven)",
            "พอร์ตกระจุกในห่วงโซ่ AI (ชิป, compute, โครงสร้างพื้นฐาน)",
            "กองใหม่ ขนาดพอร์ตโตเร็ว = เพิ่มความเชื่อมั่นในธีสิส",
        ],
        "read_buy": "เพิ่ม/เปิดใหม่ในกลุ่ม AI = ตอกย้ำ/ขยายธีสิส AGI",
        "read_sell": "การลด = rebalance ภายในธีม หรือย้ายไปจุดที่ conviction สูงกว่า",
    },
    "ark": {
        "headline": "Disruptive Innovation · conviction สูง, สวนตลาด",
        "principles": [
            "เน้นเทคโนโลยีเปลี่ยนโลก (genomics, AI, fintech, robotics)",
            "มักซื้อเพิ่มตอนราคาย่อ (buy the dip) ในตัวที่ conviction สูง",
            "กล้าถือทั้งที่ผันผวนสูง — โฟกัสระยะยาว 5+ ปี",
        ],
        "read_buy": "เพิ่ม/เปิดใหม่ = conviction ในธีมนวัตกรรม (อาจซื้อสวนตอนย่อ)",
        "read_sell": "การลด = บริหารความเสี่ยง/สภาพคล่อง หรือหมุนเข้าตัว conviction สูงกว่า",
    },
    "two_sigma": {
        "headline": "ควอนต์ systematic · พอร์ตกว้างหลายพันตัว, turnover สูง",
        "principles": [
            "ตัดสินใจด้วยโมเดลเชิงปริมาณ/สถิติ ไม่ใช่มุมมองรายตัว",
            "พอร์ตกระจายมาก แต่ละตัวน้ำหนักเล็ก",
            "การเปลี่ยนแปลงรายไตรมาสส่วนใหญ่คือ noise ของโมเดล",
        ],
        "read_buy": "อย่าตีความรายตัวเชิง narrative — ดูภาพรวม/ธีม sector ดีกว่า",
        "read_sell": "เช่นกัน เป็นผลของโมเดล ไม่ใช่ conviction ส่วนบุคคล",
    },
}

DEFAULT_LENS = {
    "headline": "สไตล์ทั่วไป",
    "principles": ["ตีความจากทิศทางการซื้อ-ขายสุทธิและความกระจุกของพอร์ต"],
    "read_buy": "การเพิ่ม/เปิดใหม่ = เพิ่มความเชื่อมั่นในตัวนั้น",
    "read_sell": "การลด/ขายออก = ลดความเสี่ยงหรือเปลี่ยนมุมมอง",
}


def quant_metrics(ch):
    """คำนวณเมตริกเชิงปริมาณจาก DataFrame ผลของ compute_changes"""
    if ch.empty or "change" not in ch:
        return {}
    counts = ch["change"].value_counts().to_dict()
    label = "ticker" if ch.get("ticker") is not None and ch["ticker"].notna().any() else "issuer"

    def _top(kind, by, n=5, asc=False):
        sub = ch[ch["change"] == kind].copy()
        if sub.empty or by not in sub:
            return []
        sub = sub.sort_values(by, ascending=asc).head(n)
        out = []
        for _, r in sub.iterrows():
            name = r.get(label) or r.get("issuer")
            out.append((str(name), r))
        return out

    total_new_value = ch.loc[ch["change"] == "NEW", "value_new"].sum() if "value_new" in ch else 0
    total_value = ch["value_new"].sum() if "value_new" in ch else 0

    # ความกระจุก: น้ำหนัก top-10 ของพอร์ตงวดใหม่
    conc = None
    if "value_new" in ch and total_value > 0:
        top10 = ch.sort_values("value_new", ascending=False).head(10)["value_new"].sum()
        conc = 100.0 * top10 / total_value

    buys = counts.get("NEW", 0) + counts.get("ADD", 0)
    sells = counts.get("TRIM", 0) + counts.get("EXIT", 0)
    active = buys + sells
    total_pos = len(ch[ch["change"] != "EXIT"]) or 1
    turnover_pct = 100.0 * active / (len(ch) or 1)

    return {
        "counts": counts,
        "new": counts.get("NEW", 0), "add": counts.get("ADD", 0),
        "trim": counts.get("TRIM", 0), "exit": counts.get("EXIT", 0),
        "hold": counts.get("HOLD", 0),
        "buys": buys, "sells": sells,
        "net_stance": "เพิ่มความเสี่ยง (risk-on)" if buys > sells * 1.2
                      else ("ลดความเสี่ยง (risk-off)" if sells > buys * 1.2 else "ทรงตัว/สมดุล"),
        "turnover_pct": turnover_pct,
        "concentration_top10_pct": conc,
        "new_value": total_new_value,
        "total_value": total_value,
        "top_new": _top("NEW", "value_new"),
        "top_add": _top("ADD", "value_new"),
        "top_trim": _top("TRIM", "pct_change_shares", asc=True),
        "top_exit": _top("EXIT", "shares_old"),
        "period_new": ch["period_new"].iloc[0] if "period_new" in ch else None,
        "period_old": ch["period_old"].iloc[0] if "period_old" in ch else None,
        "label_col": label,
    }


def qualitative_narrative(investor, m, display_name):
    """สร้างบทวิเคราะห์เชิงคุณภาพจากเลนส์สไตล์ + เมตริกที่ตรวจพบ (rule-based)"""
    if not m:
        return "ไม่มีข้อมูลการเปลี่ยนแปลงให้วิเคราะห์"
    lens = STYLE_LENS.get(investor, DEFAULT_LENS)
    fmt = lambda v: (f"${v/1e9:.2f}B" if abs(v) >= 1e9 else
                     (f"${v/1e6:.0f}M" if abs(v) >= 1e6 else f"${v:.0f}"))
    parts = []
    parts.append(f"**สไตล์:** {lens['headline']}")
    parts.append("")

    # ภาพรวมทิศทาง
    parts.append(f"**ภาพรวมงวดนี้:** เปิดใหม่ {m['new']} · เพิ่ม {m['add']} · "
                 f"ลด {m['trim']} · ขายออก {m['exit']} → **ท่าที: {m['net_stance']}**")

    # ตีความตามเลนส์
    if investor == "two_sigma":
        parts.append(f"\n{lens['read_buy']} เพราะเป็นควอนต์ที่ถือหลายพันตัว "
                     f"(turnover ~{m['turnover_pct']:.0f}% ของรายการที่ติดตาม)")
    else:
        if m["buys"] > m["sells"]:
            parts.append(f"\n📈 โน้มเอียงไปทาง**เพิ่มสถานะ** — ตีความว่า: {lens['read_buy']}")
        elif m["sells"] > m["buys"]:
            parts.append(f"\n📉 โน้มเอียงไปทาง**ลดสถานะ** — ตีความว่า: {lens['read_sell']}")
        else:
            parts.append(f"\n⚖️ ปรับสมดุลสองทาง — ทั้งเพิ่มและลด")

        if m["top_new"]:
            names = ", ".join(f"{n} ({fmt(r['value_new'])})" for n, r in m["top_new"][:3])
            parts.append(f"\n**เดิมพันใหม่ที่ใหญ่สุด:** {names}\n→ {lens['read_buy']}")
        if m["top_exit"]:
            names = ", ".join(n for n, _ in m["top_exit"][:3])
            parts.append(f"\n**ขายออกทั้งหมด:** {names}\n→ {lens['read_sell']}")

    if m.get("concentration_top10_pct") is not None:
        c = m["concentration_top10_pct"]
        lvl = "กระจุกสูงมาก" if c > 70 else ("กระจุกปานกลาง" if c > 45 else "กระจายตัว")
        parts.append(f"\n**ความกระจุก:** Top 10 = {c:.0f}% ของพอร์ต ({lvl})")

    parts.append("\n**หลักคิดของสไตล์นี้:**")
    for p in lens["principles"]:
        parts.append(f"- {p}")

    parts.append("\n> ⚠️ นี่คือการ*ตีความ*ผ่านเลนส์สไตล์ ไม่ใช่คำพูดจริงของเจ้าของพอร์ต · "
                 "13F ดีเลย์ ~45 วัน · เพื่อการศึกษาเท่านั้น")
    return "\n".join(parts)


def build_ai_prompt(investor, display_name, m, ch):
    lens = STYLE_LENS.get(investor, DEFAULT_LENS)
    top_new = "; ".join(f"{n}" for n, _ in m.get("top_new", [])[:5]) or "-"
    top_exit = "; ".join(f"{n}" for n, _ in m.get("top_exit", [])[:5]) or "-"
    top_add = "; ".join(f"{n}" for n, _ in m.get("top_add", [])[:5]) or "-"
    return f"""คุณคือนักวิเคราะห์พอร์ตการลงทุน วิเคราะห์การเปลี่ยนแปลงพอร์ต 13F ของ {display_name}
ระหว่างงวด {m.get('period_old')} → {m.get('period_new')} โดยตีความ "ไอเดีย/มุมมอง" เบื้องหลังการตัดสินใจ

สไตล์ที่รู้จักของนักลงทุนคนนี้: {lens['headline']}
หลักการ: {'; '.join(lens['principles'])}

ข้อมูลการเปลี่ยนแปลง:
- ท่าทีสุทธิ: {m.get('net_stance')}
- เปิดใหม่ {m.get('new')} / เพิ่ม {m.get('add')} / ลด {m.get('trim')} / ขายออก {m.get('exit')}
- ความกระจุก Top10: {m.get('concentration_top10_pct')}%
- เปิดใหม่ใหญ่สุด: {top_new}
- เพิ่มน้ำหนัก: {top_add}
- ขายออกทั้งหมด: {top_exit}

เขียนบทวิเคราะห์ภาษาไทย ~4-6 ย่อหน้า ครอบคลุม:
1. ภาพรวม: เจ้าของพอร์ตกำลังคิดอะไร (มุมมองมาโคร/ธีม)
2. เดิมพันใหม่บอกอะไร
3. การขายออก/ลดบอกอะไร
4. ความเสี่ยง/ข้อสังเกต
ปิดท้ายด้วยคำเตือนว่าเป็นการตีความ ไม่ใช่คำแนะนำลงทุน และ 13F ดีเลย์"""


def ai_analysis(investor, display_name, m, ch, api_key, model="claude-sonnet-4-6"):
    """วิเคราะห์เชิงลึกด้วย Claude (ต้องมี api_key). คืน (text, error)"""
    try:
        import anthropic
    except ImportError:
        return None, "ยังไม่ได้ติดตั้ง anthropic (เพิ่มใน requirements.txt)"
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": build_ai_prompt(investor, display_name, m, ch)}],
        )
        return msg.content[0].text, None
    except Exception as e:
        return None, f"เรียก AI ไม่สำเร็จ: {e}"
