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

%PYTHON_CMD% -m pip install -e .
%PYTHON_CMD% -m pip install pyinstaller

%PYTHON_CMD% -m PyInstaller --clean --noconfirm "KmoeMangaDownloader-windows.spec"

set "LOCAL_DIST=%USERPROFILE%\Desktop\Kmoe Manga Downloader"
if exist "%LOCAL_DIST%" rmdir /s /q "%LOCAL_DIST%"
robocopy "%CD%\dist\Kmoe Manga Downloader" "%LOCAL_DIST%" /MIR >nul
if errorlevel 8 (
    echo Failed to copy build output to "%LOCAL_DIST%".
    popd
    exit /b 1
)

echo.
echo Windows build output:
echo %CD%\dist\Kmoe Manga Downloader
echo.
echo Local runnable copy:
echo %LOCAL_DIST%
echo.
echo Run this file:
echo %LOCAL_DIST%\Kmoe Manga Downloader.exe
popd
endlocal
