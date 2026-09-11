@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   creator-insight v0.1.0
echo   博主预测验证与可信度追踪系统
echo ============================================

REM 检查 venv
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] 首次运行，创建虚拟环境...
    "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM 检查配置文件
if not exist "config\config.json" (
    echo [WARN] 未找到 config\config.json
    echo        请复制 config\config.example.json 为 config\config.json 并填写 AI 密钥
    copy "config\config.example.json" "config\config.json" >nul
    echo [WARN] 已生成默认配置，请打开 config\config.json 填写 ai.cloud.api_key
)

echo [INFO] 启动服务: http://127.0.0.1:8781
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8781

pause
