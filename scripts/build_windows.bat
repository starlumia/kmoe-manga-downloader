@echo off
setlocal

pushd "%~dp0\.." || (
    echo Failed to enter project directory.
    exit /b 1
)

set "PYTHON_CMD="

py -3.12 -c "import sys" >nul 2>nul && set "PYTHON_CMD=py -3.12"
if not defined PYTHON_CMD py -3.11 -c "import sys" >nul 2>nul && set "PYTHON_CMD=py -3.11"
if not defined PYTHON_CMD py -3.14 -c "import sys" >nul 2>nul && set "PYTHON_CMD=py -3.14"
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

set "BUILD_ROOT=%TEMP%\kmoe-manga-downloader-build"
set "BUILD_WORK=%BUILD_ROOT%\build"
set "BUILD_DIST=%BUILD_ROOT%\dist"
set "PACKAGE_DIR=%BUILD_DIST%\Kmoe Manga Downloader"

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

%PYTHON_CMD% -m pip install -e .
if errorlevel 1 (
    echo Failed to install project dependencies.
    popd
    exit /b 1
)

%PYTHON_CMD% -m pip install pyinstaller
if errorlevel 1 (
    echo Failed to install PyInstaller.
    popd
    exit /b 1
)

%PYTHON_CMD% -m PyInstaller --clean --noconfirm --workpath "%BUILD_WORK%" --distpath "%BUILD_DIST%" "KmoeMangaDownloader-windows.spec"
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

if not exist "%PACKAGE_DIR%\kmdr-cli.exe" (
    echo Build finished, but the CLI executable was not found:
    echo %PACKAGE_DIR%\kmdr-cli.exe
    popd
    exit /b 1
)

set "LOCAL_DIST=%USERPROFILE%\Desktop\Kmoe Manga Downloader"
if exist "%LOCAL_DIST%" rmdir /s /q "%LOCAL_DIST%"
if exist "%LOCAL_DIST%" (
    echo Failed to replace the desktop package directory:
    echo %LOCAL_DIST%
    echo Close any running copy of Kmoe Manga Downloader and try again.
    popd
    exit /b 1
)

robocopy "%PACKAGE_DIR%" "%LOCAL_DIST%" /MIR >nul
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
