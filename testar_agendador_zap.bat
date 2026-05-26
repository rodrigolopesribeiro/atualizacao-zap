@echo off
setlocal

set "PROJECT_DIR=C:\Users\user\Desktop\Atualizacao_ZAP"
set "LOG_DIR=%PROJECT_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\scheduler_run.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
cd /d "%PROJECT_DIR%"

>> "%LOG_FILE%" echo ============================================================
>> "%LOG_FILE%" echo [%DATE% %TIME%] TESTE AGENDADOR: launcher acionado com sucesso. (SEM rodar automacao principal)
>> "%LOG_FILE%" echo [%DATE% %TIME%] TESTE AGENDADOR: ERRORLEVEL=0
>> "%LOG_FILE%" echo ============================================================

echo TESTE AGENDADOR concluido. Log em: %LOG_FILE%
endlocal & exit /b 0

