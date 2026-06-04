@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "REBUILD_ENV=0"
if /I "%~1"=="--rebuild-env" set "REBUILD_ENV=1"
if /I "%~1"=="--clean-env" set "REBUILD_ENV=1"

pushd "%~dp0\.." || (
    echo Failed to enter project directory.
    exit /b 1
)

set "PYTHON_CMD="

py -3.14 -c "import sys" >nul 2>nul && set "PYTHON_CMD=py -3.14"
if not defined PYTHON_CMD py -3.12 -c "import sys" >nul 2>nul && set "PYTHON_CMD=py -3.12"
if not defined PYTHON_CMD py -3.11 -c "import sys" >nul 2>nul && set "PYTHON_CMD=py -3.11"
if not defined PYTHON_CMD python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 11), (3, 12), (3, 14)) else 1)" >nul 2>nul && set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    echo Python 3.11, 3.12, or 3.14 is required for the Windows package build, but none was found.
    echo.
    echo Install Python 3.12 or use your existing Python 3.14 from:
    echo https://www.python.org/downloads/windows/
    echo.
    echo During installation, enable "Add python.exe to PATH" or install the Python Launcher.
    echo Detected Python launchers:
    py -0p
    popd
    exit /b 1
)

echo Using Python:
%PYTHON_CMD% --version

for /f %%i in ('%PYTHON_CMD% -c "import sys; print(chr(112)+chr(121)+str(sys.version_info.major)+str(sys.version_info.minor))"') do set "PYTHON_KEY=%%i"
if not defined PYTHON_KEY (
    echo Failed to detect Python version key.
    popd
    exit /b 1
)

set "BUILD_ROOT=%TEMP%\kmoe-manga-downloader-build"
set "BUILD_WORK=%BUILD_ROOT%\build"
set "BUILD_DIST=%BUILD_ROOT%\dist"
if not defined LOCALAPPDATA set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "BUILD_ENV_ROOT=%LOCALAPPDATA%\KmoeMangaDownloader\build-env"
set "VENV_DIR=%BUILD_ENV_ROOT%\%PYTHON_KEY%"
set "BUILD_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "PACKAGE_DIR=%BUILD_DIST%\Kmoe Manga Downloader"
set "REQ_FILE=%CD%\scripts\windows-build-requirements.txt"
set "STAMP_FILE=%VENV_DIR%\.requirements.sha256"

if not exist "%REQ_FILE%" (
    echo Windows build requirements file was not found:
    echo %REQ_FILE%
    popd
    exit /b 1
)

if exist "%BUILD_ROOT%" rmdir /s /q "%BUILD_ROOT%"
if exist "%BUILD_ROOT%" (
    echo Failed to clean local build directory:
    echo %BUILD_ROOT%
    echo Close any running Kmoe Manga Downloader process and try again.
    popd
    exit /b 1
)
mkdir "%BUILD_WORK%" || (
    echo Failed to create local build directory:
    echo %BUILD_WORK%
    popd
    exit /b 1
)
mkdir "%BUILD_DIST%" || (
    echo Failed to create local dist directory:
    echo %BUILD_DIST%
    popd
    exit /b 1
)

if not exist "%BUILD_ENV_ROOT%" mkdir "%BUILD_ENV_ROOT%" || (
    echo Failed to create persistent build environment directory:
    echo %BUILD_ENV_ROOT%
    popd
    exit /b 1
)

if "%REBUILD_ENV%"=="1" (
    if exist "%VENV_DIR%" (
        echo Rebuilding persistent Windows build environment:
        echo %VENV_DIR%
        rmdir /s /q "%VENV_DIR%"
    )
)

if not exist "%BUILD_PYTHON%" (
    echo Creating persistent Windows build environment:
    echo %VENV_DIR%
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create persistent build virtual environment.
        popd
        exit /b 1
    )
)

if not exist "%BUILD_PYTHON%" (
    echo Build virtual environment is missing Python:
    echo %BUILD_PYTHON%
    popd
    exit /b 1
)

for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath $env:REQ_FILE).Hash"`) do set "REQ_HASH=%%i"
if not defined REQ_HASH (
    echo Failed to calculate requirements hash.
    popd
    exit /b 1
)

set "INSTALL_DEPS=0"
if not exist "%STAMP_FILE%" set "INSTALL_DEPS=1"
if exist "%STAMP_FILE%" (
    set "EXISTING_REQ_HASH="
    set /p EXISTING_REQ_HASH=<"%STAMP_FILE%"
    if /I not "!EXISTING_REQ_HASH!"=="%REQ_HASH%" set "INSTALL_DEPS=1"
)

if "%INSTALL_DEPS%"=="0" (
    "%BUILD_PYTHON%" -c "import PyInstaller, aiofiles, aiohttp, bs4, customtkinter, rich, yarl" >nul 2>nul
    if errorlevel 1 set "INSTALL_DEPS=1"
)

if "%INSTALL_DEPS%"=="1" (
    echo Installing Windows build dependencies from:
    echo %REQ_FILE%
    "%BUILD_PYTHON%" -m pip --disable-pip-version-check install -r "%REQ_FILE%"
    if errorlevel 1 (
        echo Failed to install build dependencies.
        popd
        exit /b 1
    )
    > "%STAMP_FILE%" echo %REQ_HASH%
) else (
    echo Persistent Windows build environment is up to date:
    echo %VENV_DIR%
)

set "SOURCE_PATH=%CD%\src"
if defined PYTHONPATH set "PYTHONPATH=%SOURCE_PATH%;%PYTHONPATH%"
if not defined PYTHONPATH set "PYTHONPATH=%SOURCE_PATH%"
set "PYTHONNOUSERSITE=1"

"%BUILD_PYTHON%" -m PyInstaller --clean --noconfirm --workpath "%BUILD_WORK%" --distpath "%BUILD_DIST%" "KmoeMangaDownloader-windows.spec"
if errorlevel 1 (
    echo Windows package build failed.
    popd
    exit /b 1
)

if not exist "%PACKAGE_DIR%\Kmoe Manga Downloader.exe" (
    echo Build finished, but the GUI executable was not found:
    echo %PACKAGE_DIR%\Kmoe Manga Downloader.exe
    popd
    exit /b 1
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "BUILD_ID=%%i"
set "LOCAL_DIST=%USERPROFILE%\Desktop\Kmoe Manga Downloader %BUILD_ID%"
if exist "%LOCAL_DIST%" (
    echo Desktop package directory already exists:
    echo %LOCAL_DIST%
    popd
    exit /b 1
)

robocopy "%PACKAGE_DIR%" "%LOCAL_DIST%" /E >nul
if errorlevel 8 (
    echo Failed to copy build output to "%LOCAL_DIST%".
    popd
    exit /b 1
)

echo.
echo Temporary build output:
echo %PACKAGE_DIR%
echo.
echo Local runnable copy:
echo %LOCAL_DIST%
echo.
echo Run this file:
echo %LOCAL_DIST%\Kmoe Manga Downloader.exe
popd
endlocal
