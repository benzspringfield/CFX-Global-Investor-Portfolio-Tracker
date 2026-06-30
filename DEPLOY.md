# 🚀 คู่มือ Deploy ขึ้น Streamlit Community Cloud (ฟรี)

แอปจะเปิดได้ทุกที่ 24/7 (มือถือ/คอม) · ล็อกด้วยรหัสผ่าน · อัปเดตข้อมูลอัตโนมัติรายวัน

> ผมเตรียมไฟล์ฝั่งโค้ดให้หมดแล้ว เหลือขั้นตอนที่ต้องใช้บัญชีของคุณตามด้านล่าง

---

## ขั้นที่ 1 — สร้าง GitHub repo + push โค้ด

1. สมัคร/ล็อกอิน [github.com](https://github.com) → กด **New repository** → ตั้งชื่อ เช่น `portfolios-tracker` → เลือก **Private** → Create
   (อย่าเพิ่งติ๊ก add README — repo ว่างๆ)

2. ในเครื่อง เปิด PowerShell ที่โฟลเดอร์โปรเจกต์ แล้วรัน (ผม `git init` + commit แรกให้แล้ว เหลือเชื่อม remote + push):
   ```powershell
   git remote add origin https://github.com/<ชื่อคุณ>/portfolios-tracker.git
   git branch -M main
   git push -u origin main
   ```
   (ครั้งแรก GitHub จะให้ล็อกอิน — ใช้ browser หรือ Personal Access Token)

---

## ขั้นที่ 2 — Deploy บน Streamlit Cloud

1. ไป [share.streamlit.io](https://share.streamlit.io) → **Sign in with GitHub**
2. กด **Create app** → **Deploy a public app from GitHub** (private repo ก็ได้ ระบบจะขอสิทธิ์)
3. เลือก:
   - Repository: `<ชื่อคุณ>/portfolios-tracker`
   - Branch: `main`
   - Main file path: `app.py`
4. กด **Advanced settings** → Python version: **3.12**
5. กด **Deploy**

---

## ขั้นที่ 3 — ใส่ Secrets (รหัสผ่าน + อีเมล)

ในหน้าแอปบน Streamlit Cloud → เมนู **⚙️ Settings → Secrets** → วางข้อความนี้ (แก้ค่าตามจริง):

```toml
APP_PASSWORD = "ตั้งรหัสผ่านที่เดายาก"
SEC_USER_AGENT = "Portfolios-Tracker your-email@example.com"
OPENFIGI_API_KEY = ""
```

กด **Save** → แอปจะรีสตาร์ทแล้วถามรหัสผ่านก่อนเข้า ✅

---

## ขั้นที่ 4 — เปิดอัปเดตข้อมูลอัตโนมัติรายวัน (GitHub Action)

1. ในหน้า repo บน GitHub → **Settings → Secrets and variables → Actions → New repository secret**
   เพิ่ม 2 อัน:
   | Name | Value |
   |---|---|
   | `SEC_USER_AGENT` | `Portfolios-Tracker your-email@example.com` |
   | `OPENFIGI_API_KEY` | (เว้นว่าง หรือใส่ key ถ้ามี) |

2. ไปแท็บ **Actions** → ถ้าถามให้เปิดใช้งาน workflow กด **Enable**
3. Workflow `Update data daily` จะรันเองทุกวัน ~16:00 น. (ไทย) — หรือกด **Run workflow** ทดสอบเองได้
4. เมื่อมีข้อมูลใหม่ มันจะ commit กลับ repo → Streamlit Cloud redeploy ให้อัตโนมัติ

---

## 📱 ใช้บนมือถือให้เหมือนแอป
เปิดลิงก์แอป (`https://<ชื่อ>.streamlit.app`) ในมือถือ → เมนูเบราว์เซอร์ → **Add to Home Screen** → ได้ไอคอนเหมือนแอป

---

## ⚠️ ข้อควรรู้
- **ฐานข้อมูล 55MB ถูก commit เข้า repo** — repo จะโตขึ้นเรื่อยๆ จากการ commit รายวัน (ยอมรับได้สำหรับใช้ส่วนตัว); อนาคตถ้าใหญ่ไปค่อยย้ายไป Git LFS หรือ external storage
- **อย่า commit ไฟล์ `.streamlit/secrets.toml` จริง** (มีใน .gitignore แล้ว) — ค่าลับใส่ผ่านหน้าเว็บเท่านั้น
- หน้า Backtest/Adaptive ครั้งแรกจะช้า (ดึงราคา yfinance) แต่ผลถูก cache ไว้ 6 ชม. แล้ว
- ถ้าลืมรหัสผ่าน: ไปแก้ที่ Streamlit Cloud → Settings → Secrets
