@echo off
chcp 65001 >nul
echo ========================================
echo X Following 推文提取工具
echo ========================================
echo.

REM 检查Chrome是否以调试模式运行
echo 提示：请确保Chrome浏览器已以调试模式启动
echo 启动命令：
echo   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome_debug"
echo.
echo 按任意键开始运行程序...
pause >nul

python get_x_following.py

echo.
echo 程序执行完成
pause

