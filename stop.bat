@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "MODE=%~1"
if "%~1"=="" set "SHOULD_PAUSE=1"

if /I "%MODE%"=="-h" set "MODE=help"
if /I "%MODE%"=="--help" set "MODE=help"

if not exist "pyproject.toml" (
  echo This script must be run from the root of the downloaded repository.
  goto :fail
)

if "%MODE%"=="" goto :choose_mode
if /I "%MODE%"=="help" goto :show_help
if /I "%MODE%"=="docker" goto :docker_mode
if /I "%MODE%"=="local" goto :local_mode

echo Unknown option: %MODE%
goto :show_help

:choose_mode
call :print_header "Geekatplay Studio Stopper"
echo 1. Stop Docker version
echo 2. Stop local Python version
echo 3. Quit
choice /C 123 /N /M "Select an option: "
if errorlevel 3 goto :success
if errorlevel 2 goto :local_mode
if errorlevel 1 goto :docker_mode

:show_help
call :print_header "stop.bat usage"
echo stop.bat docker
echo stop.bat local
echo.
echo docker: stops the Docker services.
echo local : stops the local Python API window started by start.bat or install.bat.
goto :success

:docker_mode
call :print_header "Stopping Docker version"
docker --version >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not installed on this PC.
  goto :success
)

docker compose down
if errorlevel 1 (
  echo Failed to stop the Docker services.
  goto :fail
)

echo Docker services stopped.
goto :success

:local_mode
call :print_header "Stopping local Python version"
set "STOP_PID="

if exist ".local-api.pid" (
  set /p STOP_PID=<".local-api.pid"
)

if defined STOP_PID (
  taskkill /PID !STOP_PID! /T /F >nul 2>nul
  if not errorlevel 1 (
    del /q ".local-api.pid" >nul 2>nul
    echo Local Python API stopped.
    goto :success
  )
  del /q ".local-api.pid" >nul 2>nul
)

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$repo = (Resolve-Path '.').Path; $proc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn app.main:app*' -and $_.CommandLine -like ('*' + $repo + '*') } | Select-Object -First 1 -ExpandProperty ProcessId; if ($proc) { Write-Output $proc }"`) do set "STOP_PID=%%I"

if defined STOP_PID (
  taskkill /PID !STOP_PID! /T /F >nul 2>nul
  if not errorlevel 1 (
    echo Local Python API stopped.
    goto :success
  )
)

echo No local Python API process was found.
goto :success

:print_header
echo.
echo ============================================================
echo %~1
echo ============================================================
exit /b 0

:success
if defined SHOULD_PAUSE pause
exit /b 0

:fail
if defined SHOULD_PAUSE pause
exit /b 1