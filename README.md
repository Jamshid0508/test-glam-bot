# GLAM Gemini MVP

Juda sodda MVP:

Telegram voice -> Gemini transcription -> structured JSON extraction -> validated inventory command -> SQLite (local) / PostgreSQL (Vercel)

## 1. O'rnatish

Python 3.11+ tavsiya qilinadi.

```bash
pip install -r requirements.txt
```

## 2. .env

`.env.example` faylidan `.env` yarating va o'zingizning Telegram hamda Gemini
qiymatlaringizni kiriting. `TELEGRAM_ADMIN_ID` bot va web paneldan foydalanishga
ruxsat berilgan yagona Telegram akkauntini belgilaydi; uni o'zingizning raqamli
Telegram foydalanuvchi ID'ingizga almashtiring. Bot boshqa akkauntlarning buyruq,
matn, ovoz va tugma so'rovlarini qabul qilmaydi.
Mahalliy ishga tushirishda `DATABASE_URL` bo'sh qolsa, SQLite ishlatiladi.

`GEMINI_API_KEYS` ichidagi kalitlarni vergul, nuqtali vergul yoki bo'sh joy bilan
ajrating. Birinchi kalit asosiy hisoblanadi; Gemini undan 503/429 yoki kalit
ruxsati xatosini qaytarsa, bot navbatdagi kalitni sinaydi. Agar eski `.env`
faylingizda faqat `GEMINI_API_KEY` bo'lsa, u hamon ishlaydi; ikkala sozlama
berilsa, eski kalit ham zaxira ro'yxatiga qo'shiladi.

## 3. Ishga tushirish

```bash
python bot.py
```

Windows:

```text
run.bat
```

## Web panelni lokal sinash

Panelni xavfsiz lokal rejimda ochish uchun PowerShell'da loyiha papkasidan:

```powershell
$env:LOCAL_WEBAPP_DEV = "1"
.\.venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

So'ng brauzerda `http://127.0.0.1:8000/panel` ni oching. Bu rejim Telegram
ulanishi yoki webhook talab qilmaydi, ammo bot bilan bir xil ombor bazasidan
foydalanadi: `DATABASE_URL` sozlangan bo'lsa shu PostgreSQL, aks holda
`data/glam.db` SQLite fayli. Web panelda qilingan o'zgarishlar bot omboriga
darhol ta'sir qiladi. Lokal sinov autentifikatsiyasi faqat
`LOCAL_WEBAPP_DEV=1` va loopback manzilda ishlaydi; Vercel'da bu rejim bloklanadi.

Telegram ichidagi **Web panel** menyu tugmasi uchun `.env` faylida
`WEB_APP_URL` ga panelning ochiq HTTPS manzilini, masalan
`https://example.com/panel`, belgilang va botni qayta ishga tushiring.
Telegram Web App `localhost` manzilini qabul qilmaydi. Panelni Telegram'dan
tashqarida lokal tekshirishda yuqoridagi `uvicorn` serveri va bot bir xil
`.env` hamda ma'lumotlar bazasidan foydalanishi kerak.

Bot va panel turli joylarda ishga tushirilgan bo‘lsa (masalan, bot kompyuterda,
panel Vercel'da), ikkalasiga ham aynan bir xil `DATABASE_URL` qiymatini sozlang.
Lokal botda bu qiymat bo‘sh qolsa, bot `data/glam.db` SQLite bazasiga yozadi,
Vercel esa PostgreSQL'dan foydalanadi — ma'lumotlar sinxronlashmaydi.
`data/glam.db` dagi eski yozuvlar PostgreSQL'ga o‘z-o‘zidan ko‘chmaydi.

`.env` ichida `BOT_TOKEN`, `GEMINI_API_KEYS` va `TELEGRAM_ADMIN_ID` qiymatlari
bo'lishi kerak. Lokal web panel uchun Gemini kalitlari ishlatilmaydi. Sinov
jarayonini tugatish uchun server terminalida `Ctrl+C` bosing; keyingi PowerShell
oynasida flag alohida belgilanmagan bo'ladi.

## Vercel'ga deploy qilish

Vercel serverless ishlaydi, shuning uchun u yerda bot `run_polling()` bilan
doimiy ishlab turolmaydi. `app.py` FastAPI webhook entrypointidan foydalanadi;
ombor va Telegram foydalanuvchisining kutilayotgan tahrirlash holati PostgreSQL'da
saqlanadi. Mahalliy `python bot.py` polling rejimida ishlaydi; `.env` dagi
`DATABASE_URL` to'ldirilgan bo'lsa, u ham shu PostgreSQL bazasidan foydalanadi.
Faqat `DATABASE_URL` bo'sh bo'lgandagina `data/glam.db` SQLite ishlatiladi.

1. Neon'da PostgreSQL baza yarating va **pooled connection string** nusxalang.
   Lokal bot ham shu bazaga ulanishi kerak: kompyuterdagi `.env` faylida ham
   Vercel'dagi bilan **aynan bir xil** `DATABASE_URL` ni belgilang. Lokal
   `data/glam.db` yozuvlari avtomatik ravishda PostgreSQL'ga ko'chirilmaydi.
2. Vercel Project → **Settings → Environment Variables** bo'limida quyidagilarni
   qo'shing:

   - `BOT_TOKEN` — Telegram bot tokeni
   - `GEMINI_API_KEYS` — Gemini API kalitlari, vergul bilan ajratilgan
   - `DATABASE_URL` — Neon PostgreSQL pooled connection string
   - `WEBHOOK_SECRET` — Telegram webhook uchun maxfiy matn; 1–256 ta
     `A-Z`, `a-z`, `0-9`, `_` yoki `-` belgilaridan tuzilsin
   - `TELEGRAM_ADMIN_ID` — bot va web paneldan foydalanishga ruxsat berilgan Telegram ID
   - `WEB_APP_URL` — ixtiyoriy; Vercel production domeni mavjud bo'lsa, bot
     panel manzilini avtomatik aniqlaydi
   - `GEMINI_MODEL` — ixtiyoriy, berilmasa `gemini-2.5-flash` ishlatiladi

3. O'zgaruvchilarni qo'shgach, Vercel'da qayta deploy qiling. Vercel production
   domeni `/panel` sahifasini taqdim etadi. Bot ishga tushganda pastdagi Telegram
   **Menu** tugmasi **Web panel** web-app menyu tugmasiga aylanadi va bosilganda
   panelni ochadi. Vercel `VERCEL_PROJECT_PRODUCTION_URL` bermasa, `WEB_APP_URL`
   ga `https://<deploy-domeni>/panel` ni kiriting.
4. Deploy URL'i ma'lum bo'lgach, Telegram webhook'ni bir marta o'rnating.
   PowerShell'da token, webhook URL (`https://<deploy-url>/webhook`) va
   `WEBHOOK_SECRET` qiymatlarini so'ralganda kiriting:

   ```powershell
   $token = Read-Host "Telegram bot token"
   $url = Read-Host "Webhook URL"
   $secret = Read-Host "WEBHOOK_SECRET"
   Invoke-RestMethod -Method Post `
     -Uri "https://api.telegram.org/bot$token/setWebhook" `
     -Body @{ url = $url; secret_token = $secret }
   ```

   `{"ok":true,"result":true,...}` javobi webhook o'rnatilganini bildiradi.
   Vercel'dagi `WEBHOOK_SECRET` bilan Telegram'ga yuborilgan `secret_token`
   aynan bir xil bo'lishi kerak.

Root URL (`https://<deploy-url>/`) sog'liq tekshiruvi uchun `{"status":"ok"}`
qaytaradi. Mahalliy polling rejimiga qaytishdan avval webhook'ni o'chiring:

```powershell
Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot<token>/deleteWebhook"
```

## 4. Telegramda sinash

Ovoz:

- "Elen gilami, 3 dona keldi"
- "Yangi model gilamdan 2 dona keldi"
- "Elen gilami, 1 dona sotdim"
- "Katta Qizil Gul gilamidan 3 metr keldi"
- "Katta Qizil Gul gilamidan 1.5 metr sotdim"

Qo'shish buyruqlaridan keyin bot avval nom va miqdorni ko'rsatadi. **O'zgartirish**
tugmasini bosib yangi ma'lumot yuborish, **Saqlash** bilan omborga yozish yoki
**O'chirish** bilan kiritmani bekor qilish mumkin. Gilamlar artikulsiz,
normallashtirilgan nomi bo'yicha saqlanadi; bir xil nom ikkinchi marta
yaratilmaydi.

Ovozli buyruqdagi mahsulot nomini yaxshiroq tanishi uchun bot ombordagi gilam
nomlarini Gemini transkripsiya so'roviga lug'at sifatida qo'shadi. Ko'p so'zli,
uzun nomlar ham to'liq uzatiladi. Transkripsiyadagi nom ombordagi nomga yaqin
bo'lsa, bot uni avtomatik ravishda bazadagi to'liq nomga moslaydi. Sotuvni gilam
nomi bilan aytish kifoya. Gemini vaqtincha 503/429 xato bersa, bot qayta urinadi.

Ovozdan noto'g'ri tanilgan so'zlarni `voice_corrections.json` faylida
`"noto'g'ri eshitilgan": "to'g'ri nom"` ko'rinishida belgilang. Masalan:

```json
{
  "replacements": {
    "Sank": "Sag"
  }
}
```

Bot bu almashtirishlarni Gemini transkripsiyasidan keyin va buyruqni tahlil
qilishdan oldin, so'z chegaralarida qo'llaydi. Masalan, `Sank gilamdan` matni
`Sag gilamdan` bo'ladi. Almashtirish bo'lsa, asl va tuzatilgan matnni Telegramda
ko'rsatadi. JSONni o'zgartirgandan keyin botni qayta ishga tushirish shart emas.
Yangi qoidalarni faqat to'g'ri ekanini tekshirib qo'shing: noto'g'ri qoida boshqa
mahsulot nomini ham almashtirib yuborishi mumkin.

Tekshirish:

```text
/ombor
```

Bot menyusidagi **➕ Gilam qo‘shish** tugmasi bitta gilam kiritish uchun,
**📥 Ko‘p gilam kiritish** esa boshlang‘ich ombor ro‘yxatini birdaniga kiritish
uchun. Har bir gilamni yangi qatorda `Nomi | miqdor | dona yoki metr` ko‘rinishida
yuboring. Masalan:

```text
Sag | 7 | metr
Katta Qizil Gul | 10 | dona
```

Omborda oldindan bor nomlar qayta qo‘shilmaydi; bot borligini va joriy qoldiqni
ko‘rsatadi.

`/ombor` ro'yxatidagi gilam tugmasini bosib nom, qoldiq va birlikni
tahrirlash mumkin. Ma'lumotni `Nomi | miqdor | dona yoki metr` shaklida
yuboring; o'zgarish tasdiqlashdan keyin saqlanadi. Omborda mavjud nomni takroran
saqlashga yo'l qo'yilmaydi. Matn orqali tahrirlashda joriy nom, o'lcham,
miqdor va narxlar ko'rsatiladi; faqat nomni tuzatish uchun yangi nomning o'zini
yuborish kifoya, qolgan parametrlar saqlanadi.

## Web panel

Telegram ichida **🌐 Web panel** tugmasini oching. Kirish Telegram `initData`
imzosi orqali tekshiriladi va faqat `TELEGRAM_ADMIN_ID` dagi akkauntga ruxsat
beriladi. Oddiy brauzerdan ochilganda ma'lumotlar ko'rsatilmaydi.

- Kun yoki tanlangan sana oralig'i bo'yicha sotuv soni, tushum va foyda.
- Ombordagi gilamlarni qidirish, qo'shish, tahrirlash va arxivlash.
- Arxivlangan gilamlarni ko'rish va qayta tiklash; tarixiy yozuvlar saqlanadi.
- Har bir gilam uchun alohida sotuv, qoldiq, foyda, narxlar va kirim/sotuv tarixi.
- Sotuvni sana, miqdor yoki haqiqiy sotish narxi bo'yicha tahrirlash va o'chirish.
  Bunda ombor qoldig'i qayta moslanadi.
- Har bir sotuvda kelish narxi va sotish narxi saqlanadi. Narxlar o'zgarsa,
  narx tarixi yuritiladi; sotuv foydasi sotuv sanasidagi narxlardan hisoblanadi.
- Narxi belgilanmagan eski yoki yangi sotuvlar alohida ko'rsatiladi va foyda
  ko'rsatkichi to'liq emasligi haqida ogohlantiriladi.

Mahsulot kirimini ovoz yoki matn bilan kiritayotganda narxlarni ham aytish mumkin:

```text
Yangi Elen gilami, 3 dona keldi, kelish narxi 500000, sotish narxi 650000
Katta Qizil Gul 3 ga 4, 5 dona keldi, kelish narxi 3 dollardan kvadratiga
```

Ovozli xabar avval Gemini bilan matnga aylantiriladi, keyin shu matn Gemini'ga
alohida so'rovda JSON Schema bo'yicha tahlil uchun yuboriladi. Bot nom, o'lcham,
miqdor va narxlarni tekshirib, saqlashdan oldin alohida maydonlarda ko'rsatadi.
Maydon va kvadrat narxidan bir gilamning jami kelish narxini kodning o'zi hisoblaydi.
Yuqoridagi misolda 3 × 4 = 12 m²
bo'lgani uchun kelish narxi avtomatik ravishda $36 (kvadratiga $3) qilib
hisoblanadi; so'm ekvivalenti joriy dollar kursidan olinadi. Ovozda kirim yoki
sotuv amali aniq aytilmagan bo'lsa, bot
hisobni ko'rsatib, **Omborga qo'shish** yoki **Sotish** tugmasini chiqaradi;
sotuv faqat tugma bosilgandan keyin bajariladi. Miqdor aniq aytilmaganida
amalni tanlash tugmalari berilmaydi. Narx aytilmasa, uni paneldagi gilam
tahrirlash formasidan kiritish mumkin. Ko'p so'zli nomlar (masalan,
"Katta Qizil Gul") saqlanadi va bot ularni to'liq nom bo'yicha solishtiradi.

Sinovlar:

```bash
python -m unittest discover -s tests -v
```

## 5. Filial, foydalanuvchilar va qarzlar (yangi)

Tizim endi ikkita do'kon bilan ishlaydi: **Asosiy do'kon** (gilamlar miqdor
bilan, raqamsiz) va **Qamashi Glam** filiali (har bir gilamga qog'oz yorliq
raqami biriktiriladi va shu raqam tizimga kiritiladi).

- **Kirish:** bot va web panelga login/parol bilan kiriladi. Birinchi admin
  `.env` dagi `ADMIN_LOGIN` / `ADMIN_PASSWORD` orqali avtomatik yaratiladi.
  Keyin admin botdagi «👥 Foydalanuvchilar» yoki paneldagi shu bo'limdan
  sotuvchilar qo'shadi. Foydalanuvchi botda /start bosib login/parolni bir
  marta kiritsa, Telegram akkaunti bog'lanadi. `TELEGRAM_ADMIN_ID` egasi
  (asosiy admin) avvalgidek to'g'ridan-to'g'ri kiradi.
- **Sotuvchi** o'z do'konida: kirim qilish (raqamlar bilan), raqam orqali
  sotish, kunlik savdosini ko'rish huquqiga ega. Boshqa do'kon va admin
  bo'limlari unga yopiq.
- **Kunlik hisob-kitob:** admin botda «💰 Hisob-kitob» yoki panelda shu
  bo'limda sotuvchining kunlik savdosini ko'radi va topshirilgan summani
  kiritadi. Kam topshirilsa, yetishmagan summa **sana bilan qarz** bo'lib
  saqlanadi («📕 Qarzlar»). Ortiqcha topshirilsa, avval eski qarzlar
  yopiladi, qolgani keyingi kunlarga avans bo'lib o'tadi.
- **Panel:** brauzerda ham login/parol bilan kiriladi (Telegram shart emas).
  Ombor bo'limida do'kon tanlovi bor; filialda mahsulot yaratishda yorliq
  raqamlari ham kiritiladi.
