@echo off
setlocal
rem Изгражда MikrotikAlignFree.exe и инсталатора. Пускайте го на Windows с инсталиран Python 3.8
rem (последната версия на Python, която работи на Windows 7) и Inno Setup 6.
cd /d "%~dp0"

set PY=py -3.8
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Не е намерен Python 3.8. Инсталирайте 32-битов Python 3.8.10 от python.org и стартирайте отново.
  echo Новите версии на Python дават програма, която НЕ работи на Windows 7/8.
  pause
  exit /b 1
)

if not exist .venv (
  %PY% -m venv .venv || goto :fail
)
.venv\Scripts\python -m pip install --upgrade pip || goto :fail
.venv\Scripts\python -m pip install "pyinstaller==5.13.2" || goto :fail

rd /s /q ..\dist 2>nul
rd /s /q work 2>nul
.venv\Scripts\pyinstaller --noconfirm --clean --onedir --noconsole --name MikrotikAlignFree ^
  --distpath ..\dist --workpath work --specpath work ..\mikrotikalignfree.py || goto :fail

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" (
  echo Inno Setup 6 не е намерен. Програмата е в ..\dist\MikrotikAlignFree\MikrotikAlignFree.exe
  echo За инсталатор изтеглете безплатния Inno Setup 6 от jrsoftware.org и стартирайте отново.
  pause
  exit /b 0
)
"%ISCC%" mikrotikalignfree.iss || goto :fail
echo.
echo Готово: ..\installer\MikrotikAlignFree-Setup-*.exe
pause
exit /b 0

:fail
echo Грешка при изграждането.
pause
exit /b 1
