@echo off
setlocal
cd /d "%~dp0"

set "UV=%USERPROFILE%\.local\bin\uv.exe"
set "PYTHON=%APPDATA%\uv\python\cpython-3.13.15-windows-x86_64-none\python.exe"

if not exist "%UV%" (
    echo uv was not found at %UV%
    exit /b 1
)

"%UV%" run --python "%PYTHON%" --with pyinstaller --with pillow pyinstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "Omni Meeting Recorder" ^
    --icon "assets\omr_icon.png" ^
    --add-data "assets\omr_icon.png;assets" ^
    --add-data "omr_gui\meter_worker.py;omr_gui" ^
    Main.py

if errorlevel 1 exit /b %errorlevel%
echo.
echo Built: dist\Omni Meeting Recorder.exe
