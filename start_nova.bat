@echo off
rem Double-click to start Nova. Picks a Python that has Nova's packages (3.14 down to 3.11).
cd /d "%~dp0"
for %%v in (3.14 3.13 3.12 3.11) do (
  py -%%v -c "import numpy, ollama" >nul 2>&1 && (
    py -%%v main.py %*
    exit /b
  )
)
echo.
echo Couldn't find a Python 3.11-3.14 with Nova's packages installed.
echo Install them with:   py -3.14 -m pip install -r requirements.txt
echo.
pause
