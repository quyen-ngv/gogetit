@echo off
REM Build and push Docker image to Docker Hub (Windows)
REM Usage: build-and-push.bat <version>
REM Example: build-and-push.bat v1.0.0

setlocal

REM Configuration
set DOCKER_USERNAME=luxofons
set IMAGE_NAME=google-maps-bot
set VERSION=%1
if "%VERSION%"=="" set VERSION=latest

echo ======================================
echo Building Docker Image
echo ======================================
echo Image: %DOCKER_USERNAME%/%IMAGE_NAME%:%VERSION%
echo.

REM Build image
echo Building image...
docker build -t %DOCKER_USERNAME%/%IMAGE_NAME%:%VERSION% .

if errorlevel 1 (
    echo Error building image
    exit /b 1
)

REM Also tag as latest if version is specified
if not "%VERSION%"=="latest" (
    echo Tagging as latest...
    docker tag %DOCKER_USERNAME%/%IMAGE_NAME%:%VERSION% %DOCKER_USERNAME%/%IMAGE_NAME%:latest
)

echo.
echo Build completed!
echo.

REM Ask for confirmation before pushing
set /p PUSH="Push to Docker Hub? (y/n): "
if /i not "%PUSH%"=="y" (
    echo Push cancelled
    exit /b 0
)

echo ======================================
echo Pushing to Docker Hub
echo ======================================

REM Login to Docker Hub
echo Logging in to Docker Hub...
docker login

if errorlevel 1 (
    echo Login failed
    exit /b 1
)

REM Push versioned tag
echo Pushing %VERSION%...
docker push %DOCKER_USERNAME%/%IMAGE_NAME%:%VERSION%

REM Push latest tag
if not "%VERSION%"=="latest" (
    echo Pushing latest...
    docker push %DOCKER_USERNAME%/%IMAGE_NAME%:latest
)

echo.
echo ======================================
echo Successfully pushed!
echo ======================================
echo Image: %DOCKER_USERNAME%/%IMAGE_NAME%:%VERSION%
echo Image: %DOCKER_USERNAME%/%IMAGE_NAME%:latest
echo.
echo Pull command:
echo   docker pull %DOCKER_USERNAME%/%IMAGE_NAME%:%VERSION%
echo.

endlocal
