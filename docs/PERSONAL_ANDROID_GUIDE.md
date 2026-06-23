# SHIMS Personal AI — Android Build & Release Guide

## Overview

SHIMS Personal AI is a **sellable, offline-first AI assistant** for Android.
It runs a local LLM directly on the user's phone via **llama.cpp JNI** — no cloud required, no data leaves the device.

### Key Selling Points

- 🔒 **100% Private** — Everything runs on-device. No API keys. No data collection.
- 🌐 **Works Offline** — Chat, voice, reminders, notes, and memory work without internet.
- 🧠 **Local LLM** — Download quantized GGUF models (Gemma 2B, Qwen 3B, Phi 4 Mini, Llama 3.2 3B).
- 🎙️ **Voice First** — Wake-word free voice mode with Android native STT/TTS.
- 📱 **Google Play Ready** — Proper Android app with monetization hooks.

---

## Architecture

```
┌─────────────────────────────────────────┐
│         Android App (APK)               │
│  ┌─────────────────────────────────┐   │
│  │  WebView SPA (shims_personal/)  │   │
│  │  - Chat, Memory, Reminders      │   │
│  │  - Notes, Settings, Model Mgr   │   │
│  └─────────────────────────────────┘   │
│              ↓                          │
│  ┌─────────────────────────────────┐   │
│  │  Native Bridges (JNI)           │   │
│  │  - LlamaBridge (llama.cpp)      │   │
│  │  - ModelManager (downloads)     │   │
│  │  - NotificationHelper (alarms)  │   │
│  │  - NativeBridge (STT/TTS)       │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

### Two Operating Modes

1. **Standalone Mode** (default) — Uses JNI + local GGUF model. No backend needed.
2. **Bridge Mode** (optional) — Connects to `shims_personal/app.py` backend for advanced features (RAG, evolution, etc.).

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Android Studio | Latest | IDE + SDK manager |
| Android SDK | API 35 | Build target |
| Android NDK | r26+ | Compile llama.cpp JNI |
| CMake | 3.22+ | Native build |
| Java JDK | 17+ | Gradle build |
| Git | Any | Clone llama.cpp |

---

## Build Instructions

### 1. Clone llama.cpp

```bash
cd android_app/app/src/main/cpp
git clone --depth 1 https://github.com/ggerganov/llama.cpp.git
```

### 2. Build APK

**Windows:**
```bat
scripts\build_android.bat
```

**macOS / Linux:**
```bash
chmod +x scripts/build_android.sh
./scripts/build_android.sh
```

The APK will be at:
```
android_app/app/build/outputs/apk/release/app-release.apk
```

### 3. Install on Device

```bash
adb install -r android_app/app/build/outputs/apk/release/app-release.apk
```

### 4. Sign for Google Play

1. Open Android Studio
2. **Build → Generate Signed App Bundle / APK**
3. Create or select a keystore
4. Choose **AAB** (Android App Bundle) for Play Store upload
5. Upload the `.aab` to Google Play Console

---

## Monetization Strategy

### Freemium Model

| Feature | Free | Premium (₹399/mo or ₹3,999/yr) |
|---------|------|-------------------------------|
| Local chat | ✅ Unlimited | ✅ Unlimited |
| Voice mode | ✅ Basic | ✅ Enhanced (wake word, custom voice) |
| Model size | Up to 3B params | Up to 7B+ params |
| Memory entries | 50 | Unlimited |
| Cloud sync | ❌ | ✅ Cross-device |
| Document RAG | 3 docs | Unlimited |
| Reminders | 10 | Unlimited |
| Custom agents | ❌ | ✅ |
| Priority support | ❌ | ✅ |

### Implementation

Premium checks use Google Play Billing Library v7:

```java
// In MainActivity or BillingManager.java
billingClient.queryProductDetailsAsync(params, (result, details) -> {
    // Show/hide premium features based on purchase state
});
```

Placeholder is in `js/app.js`:
```javascript
function showPremiumDialog() {
    // Integrate Google Play In-App Billing here
}
```

---

## App Store Assets Checklist

- [ ] **App Icon** — 512x512 PNG (create in `android_app/app/src/main/res/mipmap-xxxhdpi/`)
- [ ] **Feature Graphic** — 1024x500 PNG
- [ ] **Screenshots** — Phone (1080x1920) + Tablet
- [ ] **Privacy Policy** — See `docs/PRIVACY_POLICY.md`
- [ ] **Terms of Service** — See `docs/TERMS_OF_SERVICE.md`
- [ ] **Content Rating** — Everyone / Teen (depends on model safety)
- [ ] **App Category** — Productivity / Tools

---

## Model Recommendations

| Model | Size | Speed | Quality | Best For |
|-------|------|-------|---------|----------|
| Gemma 2B IT Q4 | ~1.6 GB | ⚡⚡⚡ Very fast | ⭐⭐ Good | Entry-level phones, free tier |
| Qwen 2.5 3B Q4 | ~2.0 GB | ⚡⚡ Fast | ⭐⭐⭐ Very good | Multilingual (Hindi + English) |
| Phi-4 Mini Q4 | ~2.2 GB | ⚡⚡ Fast | ⭐⭐⭐⭐ Excellent | Reasoning, coding |
| Llama 3.2 3B Q4 | ~2.0 GB | ⚡⚡ Fast | ⭐⭐⭐ Very good | General purpose |

**Note:** Models are downloaded on first run, NOT bundled in the APK.

---

## Testing

### Unit Tests
```bash
pytest tests/test_v15_*.py -v
```

### Device Tests
```bash
# Install debug APK
adb install -r android_app/app/build/outputs/apk/debug/app-debug.apk

# Check logs
adb logcat -s SHIMS_LlamaJNI:D SHIMS:D
```

### Manual QA Checklist
- [ ] App launches without crash
- [ ] Model download starts and completes
- [ ] Model loads successfully
- [ ] Chat responds within 5 seconds on mid-range phone
- [ ] Voice input works (mic button)
- [ ] TTS speaks the response
- [ ] Reminder fires at correct time
- [ ] Note saves and is searchable
- [ ] Memory persists across sessions
- [ ] App works in airplane mode

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `llama.cpp not found` | Run `git clone` in `cpp/` folder |
| NDK not found | Set `ANDROID_NDK_HOME` env var |
| Gradle sync fails | Update Android Studio to latest |
| Model load fails | Check file path, ensure GGUF format |
| JNI crash | Check `adb logcat` for native stack trace |
| Out of memory | Use smaller model (Gemma 2B) or reduce `n_ctx` |

---

## Next Steps (v1.1+)

1. **Wake Word Detection** — Integrate Porcupine or custom VAD
2. **Pipecat Integration** — Realtime streaming voice kernel
3. **Vision** — Local image understanding with multimodal models
4. **Plugins** — Allow third-party tool integrations
5. **Wear OS** — Smartwatch companion app
