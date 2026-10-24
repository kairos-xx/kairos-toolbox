@echo off
REM @@NAME@@-pyz.cmd - run the bundled .pyz with a guaranteed Python.
REM Rendered by tools.stationer.installer_support (@@NAME@@, @@PYZ@@).
setlocal
set "PRIVATE=%~dp0python\tools\python.exe"
if exist "%PRIVATE%" (
    "%PRIVATE%" "%~dp0@@PYZ@@" %*
    exit /b %ERRORLEVEL%
)
where py >nul 2>nul && (
    py -3 "%~dp0@@PYZ@@" %*
    exit /b %ERRORLEVEL%
)
where python >nul 2>nul && (
    python "%~dp0@@PYZ@@" %*
    exit /b %ERRORLEVEL%
)
echo @@NAME@@-pyz: no Python found; reinstall to bootstrap one >&2
exit /b 127
