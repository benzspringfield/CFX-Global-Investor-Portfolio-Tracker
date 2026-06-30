# 📊 Portfolios Tracker — ติดตามพอร์ตนักลงทุนระดับโลก

ถอดรื้อพอร์ตของนักลงทุนชั้นนำจากข้อมูลสาธารณะ (ฟรีล้วน) เพื่อหา consensus,
การเคลื่อนไหวรายงวด และสังเคราะห์เป็น "พอร์ตแนะนำ" ด้วย Smart-Money Model

> ⚠️ **เครื่องมือเพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน** ข้อมูล 13F ดีเลย์ ~45 วัน

## วิธีใช้

```bash
# 1. ติดตั้ง (ครั้งแรก)
pip install -r requirements.txt

# 2. ดึงข้อมูลล่าสุดเข้า DB
python update_data.py

# 3. เติม ticker ให้ 13F (OpenFIGI ฟรี ไม่ต้องมี key, แปลงครั้งเดียว cache ถาวร)
python backfill_tickers.py

# 4. เปิด dashboard
streamlit run app.py
```

เปิดเบราว์เซอร์ที่ http://localhost:8501

## นักลงทุนที่ติดตาม (แก้/เพิ่มได้ที่ `config.py`)

| นักลงทุน | แหล่งข้อมูล | หมายเหตุ |
|---|---|---|
| Druckenmiller (Duquesne) | SEC 13F | long หุ้น US รายไตรมาส |
| Leopold Aschenbrenner (Situational Awareness LP) | SEC 13F | กอง AI/AGI เข้มข้น (ไม่ใช่ "SharonAI") |
| Two Sigma | SEC 13F | ควอนต์ พอร์ตกว้าง ใช้ดูธีม |
| ARK Invest | arkfunds.io | **รายวัน** ละเอียดสุด |
| Nancy Pelosi | House Clerk | รายการ PTR + ลิงก์ PDF |
| Mark Minervini | — | ไม่มี filing → ใช้ skill `minervini-trader` |

## หน้าใน Dashboard
- **ภาพรวม** — มูลค่าพอร์ต/จำนวนตำแหน่งของทุกคน + ข้อจำกัดข้อมูล
- **รายนักลงทุน** — Top holdings + กราฟ + ตารางเต็ม เลือกงวดได้
- **การเปลี่ยนแปลง** — NEW / ADD / TRIM / EXIT เทียบงวดล่าสุด vs ก่อนหน้า
- **Consensus** — หุ้นที่หลายเจ้าถือซ้ำ + ตาราง overlap
- **Model & พอร์ตแนะนำ** — Smart-Money Score + สัดส่วนพอร์ต
- **Backtest** — ทดสอบ model ย้อนหลังเทียบ SPY (point-in-time, กัน lookahead) + เทียบรายพอร์ต
- **Adaptive Model** — skill-weighting (trailing alpha) + regime gate (SPY vs MA) เทียบทุก model
- **Congressional** — รายการ PTR ของ Pelosi พร้อมลิงก์ PDF

## Smart-Money Model
คะแนน 0–100 = ถ่วงน้ำหนัก 4 องค์ประกอบ:
1. **Consensus** (35%) — จำนวนนักลงทุนที่ถือซ้ำ
2. **Conviction** (20%) — น้ำหนักเฉลี่ยในพอร์ตเขา
3. **Net buying** (25%) — แรงซื้อสุทธิงวดล่าสุด (NEW/ADD บวก, TRIM/EXIT ลบ)
4. **Momentum** (20%) — ผลตอบแทนราคา 6 เดือน (yfinance, เปิด/ปิดได้)

## สถาปัตยกรรม
```
config.py            ทะเบียนนักลงทุน + การตั้งค่า (ศูนย์กลางการแก้ไข)
store.py             SQLite + schema กลาง (holdings / trades)
trackers/
  edgar_13f.py       ดึง+parse 13F (auto-detect หน่วย value)
  ark_daily.py       holdings รายวันจาก arkfunds.io
  congress.py        PTR จาก House Clerk ZIP
  common.py          HTTP + CUSIP->ticker (OpenFIGI)
analysis/
  changes.py         diff ระหว่างงวด
  consensus.py       หุ้นถือซ้ำข้ามนักลงทุน
  model.py           Smart-Money Model + พอร์ตแนะนำ
  backtest.py        Backtest point-in-time เทียบ SPY + per-portfolio + per-investor
  adaptive.py        Adaptive model: skill-weighting + regime gate + ตัวเทียบทุก model
app.py               Streamlit dashboard
update_data.py       ดึงข้อมูลทั้งหมดเข้า DB
backfill_tickers.py  เติม CUSIP->ticker ให้ 13F (OpenFIGI keyless)
```

## ข้อจำกัด / แผนเฟสถัดไป
- **CUSIP→ticker**: 13F ให้แค่ CUSIP ใส่ OpenFIGI API key (ฟรี) ใน `config.py` เพื่อได้ ticker ครบ
- **Pelosi**: ตอนนี้ได้แค่รายการยื่น — แกะ ticker/จำนวนจาก PDF ต้อง OCR (เฟส 2)
- **ARK net-buying**: เก็บ snapshot รายวันสะสมหลายวันจะเห็นการเคลื่อนไหวจริง (รัน `update_data.py` ทุกวัน)
- **Backtest**: ยังไม่มี — ขั้นต่อไปคือทดสอบว่า model ทำผลตอบแทนย้อนหลังได้จริงไหม
```
