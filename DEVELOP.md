# 🛠️ คู่มือพัฒนาต่อ (Development Guide)

คู่มือนี้บอกวิธี **แก้โค้ด → อัปเดตขึ้น GitHub → แอปออนไลน์อัปเดตเอง** แบบละเอียดทีละขั้น
เปิดอ่านได้ทุกครั้งที่ลืม 👍

---

## 🗺️ ภาพรวม: อะไรเชื่อมกับอะไร

```
   เครื่องคุณ (แก้โค้ด)
        │  git push
        ▼
   GitHub repo  ◄──────── GitHub Action (commit ข้อมูลใหม่ทุกวัน 16:00 น.)
        │  เห็น commit ใหม่
        ▼
   Streamlit Cloud  ──► redeploy แอปอัตโนมัติ (~1-2 นาที)
        │
        ▼
   แอปออนไลน์อัปเดต (เปิดในมือถือ/คอมได้เลย)
```

**จุดสำคัญ:** คุณแค่ `git push` — ที่เหลือ Streamlit ทำเองหมด ไม่ต้องกดอะไรบนเว็บ

---

## 🔄 วงจรพัฒนา 5 ขั้น (ใช้ทุกครั้งที่จะแก้อะไร)

เปิด **PowerShell** ที่โฟลเดอร์โปรเจกต์ก่อนเสมอ:
```powershell
cd "C:\Users\benzs\OneDrive\Desktop\Stock\Portfolios tracker"
```

### ขั้นที่ 1 — ดึงงานล่าสุดก่อนเสมอ ⭐
```powershell
git pull --rebase
```
**ทำไมต้องทำก่อนทุกครั้ง?**
เพราะหุ่นยนต์ (GitHub Action) แอบ commit ไฟล์ข้อมูล `data/portfolios.db` เข้า repo **ทุกวัน**
→ ของบน GitHub จะใหม่กว่าในเครื่องคุณเสมอ
→ ถ้าไม่ดึงมาก่อนแล้วรีบ push จะเจอ error `rejected (fetch first)`
**สรุป: pull ก่อน = ไม่มีปัญหา**

---

### ขั้นที่ 2 — แก้โค้ด
เปิดไฟล์ที่อยากแก้ (เช่น `app.py`, `analysis/model.py`) แก้ตามต้องการ

> 📁 ไฟล์ไหนทำอะไร ดูได้ใน `README.md` หัวข้อ "สถาปัตยกรรม"

---

### ขั้นที่ 3 — ทดสอบในเครื่องก่อน (ห้ามข้าม!)
```powershell
streamlit run app.py
```
- เบราว์เซอร์จะเปิดที่ `http://localhost:8501`
- ลองกดดูว่าสิ่งที่แก้ทำงานถูก **ไม่มี error สีแดง**
- พอใจแล้วปิดได้ (กด `Ctrl+C` ใน PowerShell)

> ⚠️ ในเครื่องจะ **ไม่ถามรหัสผ่าน** (เพราะรหัสอยู่บน cloud เท่านั้น) — เป็นเรื่องปกติ

**ทำไมต้องทดสอบก่อน?** เพื่อไม่ให้โค้ดพังขึ้นไปบนแอปจริงที่ใช้อยู่

---

### ขั้นที่ 4 — บันทึกการแก้ (commit)
```powershell
git add -A
git commit -m "อธิบายสั้นๆ ว่าแก้อะไร"
```
ตัวอย่างข้อความ commit ที่ดี:
- `"เพิ่มกราฟ sector ในหน้า backtest"`
- `"แก้บั๊กตารางพอร์ตแสดงผิด"`
- `"เพิ่มนักลงทุน Bill Ackman"`

---

### ขั้นที่ 5 — ส่งขึ้น GitHub (push)
```powershell
git push
```
เสร็จแล้ว! รอ ~1-2 นาที → เปิดแอปออนไลน์ดู จะเห็นการเปลี่ยนแปลง

---

## 📝 ตัวอย่างจริง (แก้สีปุ่มในแอป)

```powershell
cd "C:\Users\benzs\OneDrive\Desktop\Stock\Portfolios tracker"
git pull --rebase                          # 1. ดึงล่าสุด
# ...เปิด app.py แก้สี...                    # 2. แก้
streamlit run app.py                        # 3. ทดสอบ (ดูว่าสีเปลี่ยนถูก)
git add -A                                  # 4a. เตรียม
git commit -m "เปลี่ยนสีปุ่มเป็นน้ำเงิน"      # 4b. บันทึก
git push                                    # 5. ส่งขึ้น -> แอป update เอง
```

---

## 🧩 สถานการณ์พิเศษ

### เพิ่ม library ใหม่ (เช่นใช้ `import scipy`)
ต้องเพิ่มชื่อใน `requirements.txt` ด้วย ไม่งั้นแอปบน cloud จะ error หา module ไม่เจอ
```
# เปิด requirements.txt เพิ่มบรรทัด:
scipy>=1.11
```
แล้ว commit + push ตามปกติ

### แก้รหัสผ่าน หรือค่าลับ (email/key)
**อย่าใส่ในโค้ด!** ไปแก้ที่ Streamlit → ⋮ → Settings → **Secrets** เท่านั้น
(โค้ดในเครื่องไม่ต้องแตะ)

### แก้แล้วพัง อยากย้อนกลับ
```powershell
git revert HEAD        # ย้อน commit ล่าสุด (ปลอดภัย)
git push
```

### อยากเช็คว่า deploy ใหม่สำเร็จไหม / ดู error บน cloud
เปิดแอป → เมนู **⋮** (ขวาบน) → **Manage app** → ดู log ด้านล่าง

---

## 🚨 Error ที่เจอบ่อย + วิธีแก้

| Error ตอน push/รัน | สาเหตุ | วิธีแก้ |
|---|---|---|
| `rejected (fetch first)` | ลืม pull ก่อน (บอทอัปเดตข้อมูลไปแล้ว) | `git pull --rebase` แล้ว `git push` ใหม่ |
| แอป cloud error `ModuleNotFoundError` | เพิ่ม library แต่ลืมใส่ requirements.txt | เพิ่มใน `requirements.txt` → push |
| แก้แล้วแอปไม่เปลี่ยน | ยังไม่ push / redeploy ไม่เสร็จ | เช็คว่า push แล้ว + รอ 1-2 นาที + refresh |
| `streamlit: command not found` | รันในเครื่องไม่เจอ streamlit | ใช้ `python -m streamlit run app.py` แทน |

---

## 🏆 กฎทอง 5 ข้อ

1. **pull ก่อนเสมอ** — กันชนกับข้อมูลที่บอทอัปเดต
2. **ทดสอบในเครื่องก่อน push** — อย่าปล่อยโค้ดพังขึ้นแอปจริง
3. **ค่าลับอยู่ใน Secrets เท่านั้น** — ห้าม commit รหัสผ่าน/key ลงโค้ด
4. **commit ทีละเรื่อง** เขียนข้อความให้เข้าใจ — เผื่อย้อนดูภายหลัง
5. **library ใหม่ = ต้องอยู่ใน requirements.txt** — ไม่งั้น cloud พัง

---

## ⚡ Cheat Sheet (คัดลอกใช้ได้เลย)

```powershell
# วงจรมาตรฐาน
cd "C:\Users\benzs\OneDrive\Desktop\Stock\Portfolios tracker"
git pull --rebase
# ...แก้โค้ด...
streamlit run app.py
git add -A
git commit -m "สิ่งที่แก้"
git push

# ดูสถานะ / ประวัติ
git status                 # ดูว่าแก้ไฟล์อะไรไปบ้าง
git log --oneline -5       # ดู commit ล่าสุด 5 อัน

# ย้อนกลับ
git revert HEAD            # ย้อน commit ล่าสุด
git checkout -- <ไฟล์>     # ทิ้งการแก้ไฟล์นั้น (ยังไม่ commit)
```
