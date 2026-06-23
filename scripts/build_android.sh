#!/usr/bin/env bash
set -e

echo "============================================"
echo "  SHIMS Personal AI — Android Build Script"
echo "============================================"
echo ""

cd "$(dirname "$0")/.."

# Check prerequisites
if ! command -v java &> /dev/null; then
    echo "[ERROR] Java not found. Install JDK 17+ and set JAVA_HOME."
    exit 1
fi

if [ -z "$ANDROID_SDK_ROOT" ] && [ -z "$ANDROID_HOME" ]; then
    echo "[WARNING] ANDROID_SDK_ROOT or ANDROID_HOME not set."
    echo "  Install Android Studio and set the environment variable."
    echo "  Download: https://developer.android.com/studio"
fi

# Check for llama.cpp submodule
if [ ! -f "android_app/app/src/main/cpp/llama.cpp/src/llama.cpp" ]; then
    echo "[INFO] llama.cpp submodule not found. Cloning..."
    git clone --depth 1 https://github.com/ggerganov/llama.cpp.git android_app/app/src/main/cpp/llama.cpp
fi

# Build APK
echo "[INFO] Building APK..."
cd android_app

if [ -f "./gradlew" ]; then
    ./gradlew assembleRelease
else
    echo "[ERROR] Gradle wrapper not found. Run 'gradle wrapper' in android_app/ first."
    exit 1
fi

echo ""
echo "============================================"
echo "  BUILD SUCCESS"
echo "============================================"
echo "APK location: android_app/app/build/outputs/apk/release/"
echo ""
echo "Next steps:"
echo "  1. Install on device: adb install -r app/build/outputs/apk/release/app-release.apk"
echo "  2. Sign for Play Store: Use Android Studio > Build > Generate Signed Bundle"
echo "  3. Upload to Google Play Console"
