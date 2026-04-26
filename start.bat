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
call :print_header "Geekatplay Studio Starter"
echo 1. Start Docker version ^(recommended^)
echo 2. Start local Python version
echo 3. Quit
choice /C 123 /N /M "Select an option: "
if errorlevel 3 goto :success
if errorlevel 2 goto :local_mode
if errorlevel 1 goto :docker_mode

:show_help
call :print_header "start.bat usage"
echo start.bat docker
echo start.bat local
echo.
echo docker: starts the Docker version and opens the wizard.
echo local : starts the local Python version and opens the wizard.
goto :success

:docker_mode
call :print_header "Starting Docker version"
call :ensure_docker_app || goto :fail
call :ensure_docker_running || goto :fail
docker compose up -d
if errorlevel 1 (
  echo Failed to start the Docker services.
  goto :fail
)
call :show_urls
start "" "http://127.0.0.1:8000/wizard"
goto :success

:local_mode
call :print_header "Starting local Python version"

if not exist ".venv\Scripts\python.exe" (
  echo Local Python files were not found.
  echo Run install.bat local first.
  goto :fail
)

if exist ".local-api.pid" (
  set /p EXISTING_PID=<".local-api.pid"
  if defined EXISTING_PID (
    tasklist /FI "PID eq !EXISTING_PID!" | findstr /R /C:" !EXISTING_PID! " >nul
    if not errorlevel 1 (
      echo The local API is already running.
      call :show_urls
      start "" "http://127.0.0.1:8000/wizard"
      goto :success
    )
  )
  del /q ".local-api.pid" >nul 2>nul
)

echo Opening a new terminal window for the API...
powershell -NoProfile -Command "$repo = (Resolve-Path '.').Path; $python = Join-Path $repo '.venv\Scripts\python.exe'; $command = '/k cd /d ""' + $repo + '"" && ""' + $python + '"" -m uvicorn app.main:app --host 127.0.0.1 --port 8000'; $process = Start-Process -FilePath 'cmd.exe' -ArgumentList $command -PassThru; Set-Content -Path (Join-Path $repo '.local-api.pid') -Value $process.Id"
if errorlevel 1 (
  echo Failed to launch the local API terminal.
  goto :fail
)

echo Keep the new API window open while you use the app.
call :show_urls
start "" "http://127.0.0.1:8000/wizard"
goto :success

:ensure_docker_app
docker --version >nul 2>nul && exit /b 0
echo Docker Desktop was not found. Run install.bat docker first.
exit /b 1

:ensure_docker_running
docker info >nul 2>nul && exit /b 0

if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
  echo Starting Docker Desktop...
  start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
)

echo Waiting for Docker Desktop to finish starting...
set /a attempts=0
:wait_for_docker
docker info >nul 2>nul && exit /b 0
set /a attempts+=1
if !attempts! geq 24 (
  echo Docker Desktop is installed but still not ready.
  echo Wait until Docker Desktop says it is running, then run start.bat docker again.
  exit /b 1
)
timeout /t 5 /nobreak >nul
goto :wait_for_docker

:show_urls
echo.
echo Wizard: http://127.0.0.1:8000/wizard
echo Ops console: http://127.0.0.1:8000/app
echo Catalog admin: http://127.0.0.1:8000/catalog/admin
echo Prometheus: http://127.0.0.1:9090
exit /b 0

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