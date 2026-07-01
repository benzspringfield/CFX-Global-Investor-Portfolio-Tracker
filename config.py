"""
ทะเบียนนักลงทุน + การตั้งค่ากลางของระบบ
แก้ไฟล์นี้ไฟล์เดียวเพื่อเพิ่ม/ลบนักลงทุนในอนาคต
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# พาธพื้นฐาน
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "portfolios.db"


def _secret(key, default=""):
    """
    อ่านค่าลับตามลำดับ: env var -> st.secrets (เฉพาะตอนรันใน Streamlit) -> default
    ทำให้ใช้ได้ทั้งใน GitHub Action (env) และบน Streamlit Cloud (secrets)
    โดยไม่ commit ค่าลับลง repo
    """
    v = os.environ.get(key)
    if v:
        return v
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


# SEC บังคับให้ใส่ User-Agent ที่ระบุตัวตน (ชื่อ + อีเมล) ไม่งั้นโดน 403
SEC_USER_AGENT = _secret("SEC_USER_AGENT", "Portfolios-Tracker benzspringfield@gmail.com")
HTTP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# OpenFIGI API key (ฟรี สมัครที่ openfigi.com) — ใช้แปลง CUSIP -> ticker
OPENFIGI_API_KEY = _secret("OPENFIGI_API_KEY", "")

# รหัสผ่านเข้าแอป (ตั้งใน st.secrets บน Streamlit Cloud) — เว้นว่าง = ไม่ล็อก (รันในเครื่อง)
APP_PASSWORD = _secret("APP_PASSWORD", "")

# Anthropic API key สำหรับปุ่ม "วิเคราะห์เชิงลึกด้วย AI" — เว้นว่าง = ซ่อนปุ่ม (ใช้แต่สูตรฟรี)
ANTHROPIC_API_KEY = _secret("ANTHROPIC_API_KEY", "")

# ---------------------------------------------------------------------------
# ทะเบียนนักลงทุน
# source:
#   "edgar_13f"  -> ดึงจาก SEC 13F (ต้องมี cik)
#   "ark_daily"  -> ดึง holdings รายวันจาก arkfunds.io (ต้องมี ark_symbols)
#   "congress"   -> ดึงจาก House/Senate disclosure (ต้องมี congress_name)
#   "methodology"-> ไม่มี filing สาธารณะ ใช้ระบบสัญญาณแทน (skill)
# ---------------------------------------------------------------------------
INVESTORS = {
    "druckenmiller": {
        "display": "Stanley Druckenmiller (Duquesne Family Office)",
        "source": "edgar_13f",
        "cik": "0001536411",
        "style": "Macro / concentrated growth",
        "notes": "เห็นเฉพาะ long หุ้น US รายไตรมาส ดีเลย์ ~45 วัน ไม่เห็น short/options",
        "skill": "druckenmiller-trader",
    },
    "situational_awareness": {
        "display": "Leopold Aschenbrenner (Situational Awareness LP)",
        "source": "edgar_13f",
        "cik": "0002045724",
        "style": "AGI / AI thesis — concentrated",
        "notes": "กองใหม่ (เปิด 2024) เน้นธีม AI/AGI มี 13F ย้อนหลังตั้งแต่ Q2/2025",
        "skill": None,
    },
    "two_sigma": {
        "display": "Two Sigma Investments",
        "source": "edgar_13f",
        "cik": "0001179392",
        "style": "Quant / systematic (พอร์ตกระจายมาก)",
        "notes": "3,000+ position — 13F เป็น noise ใช้ดูธีมรวมได้ ไม่เหมาะถอดจังหวะ",
        "skill": None,
    },
    "ark": {
        "display": "ARK Invest (Cathie Wood)",
        "source": "ark_daily",
        "cik": "0001697748",                 # ยังดึง 13F สำรองได้
        "ark_symbols": ["ARKK", "ARKW", "ARKG", "ARKQ", "ARKF", "ARKX"],
        "style": "High-growth / disruptive innovation",
        "notes": "เปิด holdings รายวัน — ข้อมูลละเอียดที่สุดในกลุ่มนี้",
        "skill": None,
    },
    # --- กองที่ "เลิกยื่น 13F" แล้ว (ใส่เพื่อแก้ survivorship bias ใน backtest) ---
    "melvin": {
        "display": "Melvin Capital (Gabe Plotkin) [defunct]",
        "source": "edgar_13f",
        "cik": "0001628110",
        "style": "Growth/short — เจ๊งจาก GME short squeeze 2021, ปิดกอง 2022",
        "notes": "ยื่น 13F 2014→2023 แล้วหยุด — เคส 'เงินใหญ่ที่เจ๊ง' สำหรับแก้ survivorship",
        "defunct": True,
        "skill": None,
    },
    "greenlight": {
        "display": "Greenlight Capital (David Einhorn)",
        "source": "edgar_13f",
        "cik": "0001079114",
        "style": "Value/short — ขาดทุนหนักช่วง 2015-2018",
        "notes": "value investor ดังที่เคยแพ้ตลาดยาว — กันอคติว่าเงินใหญ่ชนะเสมอ",
        "defunct": False,
        "skill": None,
    },
    "pelosi": {
        "display": "Nancy Pelosi (US House)",
        "source": "congress",
        "congress_chamber": "house",
        "congress_name": "Pelosi",
        "style": "Long-dated mega-cap tech options + stock",
        "notes": "Periodic Transaction Report ดีเลย์ได้ถึง 45 วัน บอกช่วงมูลค่า ไม่บอกจำนวนแน่นอน",
        "skill": None,
    },
    "minervini": {
        "display": "Mark Minervini (methodology only)",
        "source": "methodology",
        "style": "SEPA / VCP momentum",
        "notes": "เทรดเดอร์ส่วนตัว ไม่มี filing — ใช้ระบบสัญญาณ minervini-trader แทนพอร์ตจริง",
        "skill": "minervini-trader",
    },
}

# นักลงทุนที่ใช้ดึง holdings มาทำ consensus/model ได้จริง (มี holdings ตัวเลข)
HOLDINGS_SOURCES = [k for k, v in INVESTORS.items()
                    if v["source"] in ("edgar_13f", "ark_daily")]
