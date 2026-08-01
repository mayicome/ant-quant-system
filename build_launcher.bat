@echo off
cd /d "%~dp0"

REM 说明：使用 ASCII-only 输出，避免 bat 文件编码导致的乱码命令（如 '��' is not recognized）。

echo Checking PyInstaller...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
)

echo.
echo Kill running launcher.exe if exists...
taskkill /IM launcher.exe /F >nul 2>nul

echo Clean dist\launcher.exe ...
del /Q "dist\launcher.exe" >nul 2>nul

echo Clean temp build output (dist_tmp/work_tmp) ...
rmdir /S /Q "dist_tmp" >nul 2>nul
rmdir /S /Q "build_tmp" >nul 2>nul

echo Building launcher.exe ...
pyinstaller --noconfirm --distpath dist_tmp --workpath build_tmp launcher.spec

if exist "dist_tmp\launcher.exe" (
    echo.
    echo Build finished: dist_tmp\launcher.exe
    echo Copy to project root: launcher.exe
    copy /Y "dist_tmp\launcher.exe" "launcher.exe" >nul
    echo Copy finished.
) else (
    echo Build failed. Please check PyInstaller logs.
)

pause
