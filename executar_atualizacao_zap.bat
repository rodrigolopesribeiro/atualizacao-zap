@echo off
setlocal

set "PROJECT_DIR=C:\Users\user\Desktop\Atualizacao_ZAP"
set "PYTHON_EXE=C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe"
set "SCRIPT_PATH=%PROJECT_DIR%\atualizacao_zap.py"
set "LOG_DIR=%PROJECT_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\scheduler_run.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

>> "%LOG_FILE%" echo ============================================================
>> "%LOG_FILE%" echo [%DATE% %TIME%] INICIO execu??o agendada/manual - Atualizar Imoveis
>> "%LOG_FILE%" echo PROJECT_DIR=%PROJECT_DIR%
>> "%LOG_FILE%" echo PYTHON_EXE=%PYTHON_EXE%
>> "%LOG_FILE%" echo SCRIPT_PATH=%SCRIPT_PATH%

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
  >> "%LOG_FILE%" echo [%DATE% %TIME%] ERRO: falha ao entrar no diret?rio do projeto.
  >> "%LOG_FILE%" echo [%DATE% %TIME%] FIM com ERRORLEVEL=1
  endlocal & exit /b 1
)

if not exist "%PYTHON_EXE%" (
  >> "%LOG_FILE%" echo [%DATE% %TIME%] ERRO: Python n?o encontrado no caminho configurado.
  >> "%LOG_FILE%" echo [%DATE% %TIME%] FIM com ERRORLEVEL=2
  endlocal & exit /b 2
)

if not exist "%SCRIPT_PATH%" (
  >> "%LOG_FILE%" echo [%DATE% %TIME%] ERRO: Script principal n?o encontrado.
  >> "%LOG_FILE%" echo [%DATE% %TIME%] FIM com ERRORLEVEL=3
  endlocal & exit /b 3
)

"%PYTHON_EXE%" "%SCRIPT_PATH%" >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

>> "%LOG_FILE%" echo [%DATE% %TIME%] FIM execu??o - ERRORLEVEL=%EXIT_CODE%
>> "%LOG_FILE%" echo ============================================================

endlocal & exit /b %EXIT_CODE%

