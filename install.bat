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
if /I "%MODE%"=="check" goto :check_mode

echo Unknown option: %MODE%
goto :show_help

:choose_mode
call :print_header "Geekatplay Studio Installer"
echo 1. Docker Desktop path ^(recommended for most people^)
echo 2. Local Python path
echo 3. Check this PC only
echo 4. Quit
choice /C 1234 /N /M "Select an option: "
if errorlevel 4 goto :success
if errorlevel 3 goto :check_mode
if errorlevel 2 goto :local_mode
if errorlevel 1 goto :docker_mode

:show_help
call :print_header "install.bat usage"
echo install.bat docker
echo install.bat local
echo install.bat check
echo.
echo docker: installs Docker Desktop with winget if needed, starts the containers,
echo         and opens the wizard in your browser.
echo local : installs Python 3.12 with winget if needed, creates .venv,
echo         installs the app, starts the API, and opens the wizard.
echo check : reports whether winget, Python, and Docker are available.
echo.
echo After the first install, use start.bat and stop.bat for daily use.
echo.
echo Download or clone the GitHub repository first, then run this file from the
echo extracted project folder.
goto :success

:check_mode
call :print_header "Checking this PC"
call :report_command winget "winget package manager"
call :report_python
call :report_docker
goto :success

:docker_mode
call :print_header "Docker setup"
call :ensure_docker_app || goto :fail
call :ensure_docker_running || goto :fail
echo Building and starting the application. The first run can take several minutes.
docker compose up --build -d
if errorlevel 1 (
  echo Docker Compose failed.
  goto :fail
)
echo.
echo The application is starting.
echo Wizard: http://127.0.0.1:8000/wizard
echo Ops console: http://127.0.0.1:8000/app
echo Catalog admin: http://127.0.0.1:8000/catalog/admin
echo Prometheus: http://127.0.0.1:9090
echo.
echo Next time you can use start.bat docker and stop.bat docker.
start "" "http://127.0.0.1:8000/wizard"
goto :success

:local_mode
call :print_header "Local Python setup"
call :ensure_python || goto :fail

if not exist ".venv\Scripts\python.exe" (
  echo Creating the virtual environment...
  call %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :fail
)

echo Installing the application into .venv...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
call ".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :fail

call "%SCRIPT_DIR%start.bat" local
if errorlevel 1 goto :fail
echo.
echo Next time you can use start.bat local and stop.bat local.
goto :success

:ensure_python
set "PYTHON_CMD="
py -3.12 --version >nul 2>nul && set "PYTHON_CMD=py -3.12"
if not defined PYTHON_CMD (
  python --version >nul 2>nul && set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo Python 3.12 was not found. Attempting to install it with winget...
  call :ensure_winget || exit /b 1
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo Python installation failed.
    exit /b 1
  )
  call :refresh_path
  py -3.12 --version >nul 2>nul && set "PYTHON_CMD=py -3.12"
  if not defined PYTHON_CMD (
    python --version >nul 2>nul && set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  echo Python is still not available in this terminal.
  echo Close this window, open a new one, and run install.bat local again.
  exit /b 1
)

for /f "delims=" %%I in ('%PYTHON_CMD% --version 2^>^&1') do echo Using %%I
exit /b 0

:ensure_docker_app
docker --version >nul 2>nul && exit /b 0
echo Docker Desktop was not found. Attempting to install it with winget...
call :ensure_winget || exit /b 1
winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
  echo Docker Desktop installation failed.
  exit /b 1
)
call :refresh_path
docker --version >nul 2>nul && exit /b 0
echo Docker was installed, but this terminal cannot see it yet.
echo Close this window, start Docker Desktop once, and run install.bat docker again.
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
  echo Wait until Docker Desktop says it is running, then run install.bat docker again.
  exit /b 1
)
timeout /t 5 /nobreak >nul
goto :wait_for_docker

:ensure_winget
winget --version >nul 2>nul && exit /b 0
echo winget is not available on this PC.
echo Install App Installer from the Microsoft Store or install the missing tools manually.
exit /b 1

:refresh_path
set "PATH=%PATH%;%LocalAppData%\Microsoft\WindowsApps;%ProgramFiles%\Docker\Docker\resources\bin;%ProgramFiles%\Git\cmd"
exit /b 0

:report_command
%~1 --version >nul 2>nul
if errorlevel 1 (
  echo [MISSING] %~2
) else (
  for /f "delims=" %%I in ('%~1 --version 2^>^&1') do echo [FOUND] %~2: %%I
)
exit /b 0

:report_python
py -3.12 --version >nul 2>nul
if not errorlevel 1 (
  for /f "delims=" %%I in ('py -3.12 --version 2^>^&1') do echo [FOUND] Python 3.12: %%I
  exit /b 0
)

python --version >nul 2>nul
if not errorlevel 1 (
  for /f "delims=" %%I in ('python --version 2^>^&1') do echo [FOUND] Python: %%I
  exit /b 0
)

echo [MISSING] Python 3.12+
exit /b 0

:report_docker
docker --version >nul 2>nul
if errorlevel 1 (
  echo [MISSING] Docker Desktop
  exit /b 0
)

for /f "delims=" %%I in ('docker --version 2^>^&1') do echo [FOUND] Docker: %%I
docker info >nul 2>nul
if errorlevel 1 (
  echo [WAITING] Docker Desktop is installed but not running yet.
) else (
  echo [READY] Docker Desktop is running.
)
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