@echo off
chcp 65001 >nul
cd /d "%~dp0"
set LOG=deploy_log.txt

echo ========== GIT TEKSHIRUV ==========> %LOG%
echo.>> %LOG%
echo --- papka --->> %LOG%
cd>> %LOG% 2>&1
echo.>> %LOG%
echo --- git versiyasi --->> %LOG%
git --version>> %LOG% 2>&1
echo.>> %LOG%
echo --- remote --->> %LOG%
git remote -v>> %LOG% 2>&1
echo.>> %LOG%
echo --- branch --->> %LOG%
git branch>> %LOG% 2>&1
echo.>> %LOG%
echo --- status --->> %LOG%
git status>> %LOG% 2>&1
echo.>> %LOG%
echo --- add --->> %LOG%
git add -A>> %LOG% 2>&1
echo.>> %LOG%
echo --- commit --->> %LOG%
git commit -m "Word eksport va takrorlash eslatmalari">> %LOG% 2>&1
echo.>> %LOG%
echo --- push --->> %LOG%
git push>> %LOG% 2>&1
echo.>> %LOG%
echo --- oxirgi commitlar --->> %LOG%
git log --oneline -5>> %LOG% 2>&1

echo.
echo ==========================================
echo   TEKSHIRUV TUGADI
echo.
echo   Shu papkada deploy_log.txt fayli paydo boldi.
echo   Oshani Claudega tashlang.
echo ==========================================
echo.
pause
