@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ==========================================
echo   DONIYOR ACADEMY - QOSHIMCHALAR
echo ==========================================
echo.

if not exist requirements.txt (
  echo XATO: bu fayl notogri papkada.
  echo requirements.txt va app papkasi turgan joyga koching.
  echo.
  pause
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (set PY=py) else (set PY=python)

echo [1/3] Word eksport ulanmoqda...
%PY% patch_word_export.py
echo.

echo [2/3] Takrorlash eslatmalari ulanmoqda...
%PY% patch_review_reminder.py
echo.

echo [3/3] Kutubxonalar ornatilmoqda...
%PY% -m pip install -r requirements.txt
echo.

echo ==========================================
echo   TUGADI
echo   Shu oynadagi matnni Claudega korsating.
echo ==========================================
echo.
pause
