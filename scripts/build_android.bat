@echo off
setlocal EnableDelayedExpansion

echo ============================================
echo   SHIMS Personal AI — Android Build Script
echo ============================================
echo.

:: Check prerequisites
cd /d %~dp0\..

where java >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Java not found. Install JDK 17+ and set JAVA_HOME.
    pause
    exit /b 1
)

:: Check for Android SDK
if "%ANDROID_SDK_ROOT%"=="" if "%ANDROID_HOME%"=="" (
    echo [WARNING] ANDROID_SDK_ROOT or ANDROID_HOME not set.
    echo   Install Android Studio and set the environment variable.
    echo   Download: https://developer.android.com/studio
    pause
)

:: Check for llama.cpp submodule
if not exist "android_app\app\src\main\cpp\llama.cpp\src\llama.cpp" (
    echo [INFO] llama.cpp submodule not found. Cloning...
    git clone --depth 1 https://github.com/ggerganov/llama.git android_app\app\src\main\cpp\llama.cpp
    if errorlevel 1 (
        echo [ERROR] Failed to clone llama.cpp. Check internet connection.
        pause
        exit /b 1
    )
)

:: Build APK
echo [INFO] Building APK...
cd android_app

if exist "gradlew.bat" (
    call gradlew.bat assembleRelease
) else (
    echo [ERROR] Gradle wrapper not found. Run 'gradle wrapper' in android_app/ first.
    pause
    exit /b 1
)

if errorlevel 1 (
    echo [ERROR] Build failed. Check logs above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   BUILD SUCCESS
echo ============================================
echo APK location: android_app\app\build\outputs\apk\release\
echo.
echo Next steps:
echo   1. Install on device: adb install -r app\build\outputs\apk\release\app-release.apk
echo   2. Sign for Play Store: Use Android Studio ^> Build ^> Generate Signed Bundle
echo   3. Upload to Google Play Console
echo.
pause
