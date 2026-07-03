@echo off
set "SOURCE_DIR=%~dp0..\custom_components\atmos_energy"
set "DEST_DIR=H:\custom_components\atmos_energy"

echo Deploying Atmos Energy integration to Home Assistant Dev Environment...
echo Source: %SOURCE_DIR%
echo Destination: %DEST_DIR%
echo.

robocopy "%SOURCE_DIR%" "%DEST_DIR%" /E /PURGE /IS /IT /XD __pycache__

if %ERRORLEVEL% GEQ 8 (
    echo.
    echo Deployment FAILED with robocopy exit code %ERRORLEVEL%
    exit /b 1
) else (
    echo.
    echo Deployment SUCCESSFUL!
    exit /b 0
)
