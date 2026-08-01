@echo off
chcp 65001 >nul
echo ============================================================
echo 🌐 多端口Web应用启动器
echo ============================================================
echo.

echo 🎯 启动多个端口的Web服务...
echo.

echo 方法一：使用多端口启动器（推荐）
echo python multi_port_web.py 8080 8081 8082 8083
echo.

echo 方法二：使用多实例启动器
echo python start_multi_instances.py 8080 8081 8082 8083
echo.

echo 方法三：手动启动多个实例
echo start "端口8080" python web_app.py 8080
echo start "端口8081" python web_app.py 8081
echo start "端口8082" python web_app.py 8082
echo start "端口8083" python web_app.py 8083
echo.

echo 📱 访问地址：
echo   本地访问：http://localhost:8080
echo   本地访问：http://localhost:8081
echo   本地访问：http://localhost:8082
echo   本地访问：http://localhost:8083
echo.

echo 请选择启动方式：
echo 1. 多端口启动器（单进程多端口）
echo 2. 多实例启动器（多进程多端口）
echo 3. 手动启动（每个端口独立窗口）
echo 4. 退出
echo.

set /p choice=请输入选择 (1-4): 

if "%choice%"=="1" (
    echo 启动多端口服务...
    python multi_port_web.py 8080 8081 8082 8083
) else if "%choice%"=="2" (
    echo 启动多实例服务...
    python start_multi_instances.py 8080 8081 8082 8083
) else if "%choice%"=="3" (
    echo 启动多个独立实例...
    start "Web服务-端口8080" python web_app.py 8080
    timeout /t 2 /nobreak >nul
    start "Web服务-端口8081" python web_app.py 8081
    timeout /t 2 /nobreak >nul
    start "Web服务-端口8082" python web_app.py 8082
    timeout /t 2 /nobreak >nul
    start "Web服务-端口8083" python web_app.py 8083
    echo ✅ 所有服务已启动！
    echo 按任意键关闭此窗口...
    pause >nul
) else if "%choice%"=="4" (
    echo 退出
    exit
) else (
    echo 无效选择
)

pause
