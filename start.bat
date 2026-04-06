@echo off
echo.
echo  Pensionsraadgiver starter...
echo.

if "%ANTHROPIC_API_KEY%"=="" (
    echo  FEJL: ANTHROPIC_API_KEY er ikke sat!
    echo.
    echo  Saet din API-noegle med:
    echo    set ANTHROPIC_API_KEY=sk-ant-...
    echo.
    echo  Eller opret en .env fil med:
    echo    ANTHROPIC_API_KEY=sk-ant-...
    echo.
    pause
    exit /b 1
)

echo  API-noegle fundet. Starter server...
echo  Aaben http://127.0.0.1:8000 i din browser
echo.
py -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
