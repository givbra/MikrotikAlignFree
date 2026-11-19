@echo off
rem Бърз старт без инсталация (нужен е Python 3.8 или по-нов).
cd /d "%~dp0"
where py >nul 2>nul && (py -3 mikrotikalignfree.py & goto :eof)
where python >nul 2>nul && (python mikrotikalignfree.py & goto :eof)
echo Python не е намерен. Инсталирайте Python 3.8 от python.org или ползвайте инсталатора MikrotikAlignFree-Setup.
pause
