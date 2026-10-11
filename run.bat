@echo off
echo === GLAM Gemini MVP - Lokal ishga tushirish ===
echo.

REM Dependencylarni o'rnatish
echo [1/3] Paketlar tekshirilmoqda...
python -m pip install -r requirements.txt --quiet

REM Eski cloudflared logni tozalash
if exist cloudflared.log del cloudflared.log

REM Cloudflare tunnel ishga tushirish (background)
echo [2/3] Cloudflare tunnel ishga tushirilmoqda...
start /b "" cloudflared tunnel --url http://localhost:8000 --logfile cloudflared.log

REM Tunnel URL ni o'qish uchun kutish (max 20 sekund)
set TUNNEL_URL=
set /a TRIES=0
:WAIT_LOOP
timeout /t 2 /nobreak >nul
set /a TRIES+=1
for /f "delims=" %%i in ('powershell -NoProfile -Command "try { $content = Get-Content cloudflared.log -ErrorAction Stop; $match = $content | Select-String -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1; if ($match) { $match.Matches[0].Value } } catch {}"') do set TUNNEL_URL=%%i
if defined TUNNEL_URL goto GOT_URL
if %TRIES% LSS 10 goto WAIT_LOOP

echo OGOHLANTIRISH: Tunnel URL topilmadi.
echo Bot polling orqali ishlaydi. WEB_APP_URL ni qo'lda sozlang.
goto START_SERVICES

:GOT_URL
echo Tunnel URL topildi: %TUNNEL_URL%
echo WEB_APP_URL yangilanmoqda...
powershell -NoProfile -Command "$env = Get-Content .env; $env = $env -replace '^WEB_APP_URL=.*', ('WEB_APP_URL=' + '%TUNNEL_URL%' + '/panel'); $env | Set-Content .env"
echo WEB_APP_URL=%TUNNEL_URL%/panel

:START_SERVICES
echo.
echo [3/3] Bot va web server ishga tushirilmoqda...

REM Web server (FastAPI) - alohida oynada
start "GLAM Web Server" cmd /k "python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload"

REM Bot - alohida oynada  
start "GLAM Bot (Polling)" cmd /k "python bot.py"

echo.
echo =============================================
echo  Hamma narsa ishga tushdi!
echo.
echo  Web panel (lokal): http://localhost:8000/panel
if defined TUNNEL_URL echo  Telegram Web App:  %TUNNEL_URL%/panel
echo.
echo  Login: admin
echo  Parol: admin123
echo =============================================
echo.
echo Yopish uchun ENTER bosing (ochilgan oynalar davom etadi)
pause >nul
