@echo off
echo ============================================
echo   KCash Queue System — Build EXE (PyQt6)
echo ============================================

python --version >nul 2>&1
if errorlevel 1 ( echo [ERROR] ไม่พบ Python & pause & exit /b 1 )

echo.
echo [1/3] ติดตั้ง dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [ERROR] pip install ล้มเหลว & pause & exit /b 1 )

echo [2/3] ติดตั้ง PyInstaller...
pip install pyinstaller --quiet
if errorlevel 1 ( echo [ERROR] pyinstaller install ล้มเหลว & pause & exit /b 1 )

REM — Backup config/users ก่อน build (กัน dist โดนลบ) —
set DIST=dist\KCash Queue System
set BAK=dist\_backup
if exist "%DIST%\kcash_config.json" (
    if not exist "%BAK%" mkdir "%BAK%"
    copy /Y "%DIST%\kcash_config.json" "%BAK%\" >nul
)
if exist "%DIST%\kcash_users.dat" (
    if not exist "%BAK%" mkdir "%BAK%"
    copy /Y "%DIST%\kcash_users.dat" "%BAK%\" >nul
)
for %%F in ("%DIST%\kcash_*.json") do (
    if not exist "%BAK%" mkdir "%BAK%"
    copy /Y "%%F" "%BAK%\" >nul
)
if exist "%DIST%\.cloud_log_migrated" copy /Y "%DIST%\.cloud_log_migrated" "%BAK%\" >nul
if exist "%DIST%\.cloud_users_migrated" copy /Y "%DIST%\.cloud_users_migrated" "%BAK%\" >nul

echo [3/3] Build EXE...
pyinstaller "KCash Queue System.spec" --noconfirm
if errorlevel 1 ( echo [ERROR] Build ล้มเหลว & pause & exit /b 1 )

REM — Restore config/users หลัง build —
if exist "%BAK%\kcash_config.json" copy /Y "%BAK%\kcash_config.json" "%DIST%\" >nul
if exist "%BAK%\kcash_users.dat" copy /Y "%BAK%\kcash_users.dat" "%DIST%\" >nul
for %%F in ("%BAK%\kcash_*.json") do copy /Y "%%F" "%DIST%\" >nul
if exist "%BAK%\.cloud_log_migrated" copy /Y "%BAK%\.cloud_log_migrated" "%DIST%\" >nul
if exist "%BAK%\.cloud_users_migrated" copy /Y "%BAK%\.cloud_users_migrated" "%DIST%\" >nul
echo [OK] คืนค่า config + users + logs เรียบร้อย

echo.
echo ============================================
echo   Build สำเร็จ!
echo   ไฟล์: dist\KCash Queue System\KCash Queue System.exe
echo ============================================
explorer "dist\KCash Queue System"
pause
