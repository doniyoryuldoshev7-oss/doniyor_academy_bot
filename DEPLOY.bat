@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ==========================================
echo   RAILWAY GA YUBORISH
echo ==========================================
echo.

if not exist requirements.txt (
  echo XATO: bu fayl notogri papkada.
  echo app papkasi turgan joyga koching.
  echo.
  pause
  exit /b 1
)

echo [1/3] Ozgarishlar yigilmoqda...
git add -A
echo.

echo [2/3] Commit...
git commit -m "Word eksport va takrorlash eslatmalari"
echo.

echo [3/3] Push...
git push
echo.

echo ==========================================
echo   TUGADI
echo   Railway ozi yangi deploy boshlaydi.
echo   Shu oynadagi matnni Claudega korsating.
echo ==========================================
echo.
pause
