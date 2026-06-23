# SHIMS Feature Linkages — Cross-Platform Architecture

## Platform Matrix

| Feature | Web/Desktop (`frontend/`) | Enterprise (`shims_enterprise/`) | Personal Backend (`shims_personal/`) | Omni Backend (`backend/`) | Android (`shims_android/`) |
|---------|---------------------------|----------------------------------|--------------------------------------|---------------------------|----------------------------|
| **AI Chat** | `shims_omni.js` → `/brain/turn` | Executive AI panel | `/api/v15/turn` | `/brain/turn` | `MainActivity` → `/api/v15/turn` |
| **Voice Input** | Web Speech API + WakeWordEngine | — | `/api/v15/voice/transcribe` | `/voice/transcribe` | `SpeechRecognizer` + `WakeWordService` |
| **Wake Word** | `WakeWordEngine` class | — | `/api/v15/wakeword/detect` | `/voice/wakeword/detect` | JNI `WakeWordBridge` + `WakeWordService` |
| **TTS Output** | Browser speechSynthesis | — | `/api/v15/voice/speak` | `/voice/speak` | `TextToSpeech` + server fallback |
| **Onboarding** | `onboarding-overlay` DOM | — | — | — | `OnboardingActivity` (4 slides) |
| **Model Settings** | Settings modal (`set-provider`, `set-model`) | — | `/api/v15/settings/models` | `/api/v15/settings/models` | `ModelSettingsActivity` |
| **Subscriptions** | Stripe JS (future) | — | `/api/v15/subscription/*` | `/api/v15/subscription/*` | Google Play Billing (`BillingManager`) |
| **Crash Analytics** | — | — | — | — | Sentry Android SDK (`CrashReporter`) |
| **Support/Abuse** | `reportAbuse()` + mailto | Support template + `/api/support/*` | `/api/v15/support/*` | `/api/v15/support/*` | `SupportActivity` + `ApiClient` |
| **Privacy/Terms** | Links in settings modal | `/privacy`, `/terms`, `/content-policy` templates | — | — | `LegalActivity` (local HTML assets) |
| **Enterprise Dashboards** | — | Executive, R&D, QC, Warehouse, Production, Procurement | — | — | — |
| **GMP Modules** | — | QMS, LIMS, MES, DMS, RIM, ERP, GST | — | — | — |

## Shared Backend Modules

```
shared/
├── wakeword/          → Used by: backend/app/main.py, shims_personal/app.py, shims_android JNI
├── rate_limit.py      → Used by: ALL backends
├── config.py          → Used by: ALL backends
├── database.py        → Used by: shims_enterprise/app.py
├── ai.py              → Used by: backend/app/main.py, shims_personal/voice_pipeline.py
├── voice_state.py     → Used by: backend/app/main.py, shims_personal/voice_pipeline.py
├── search_policy.py   → Used by: backend/app/main.py
├── provider_registry.py → Used by: backend/app/main.py
└── telemetry.py       → Used by: backend/app/main.py, shims_enterprise/app.py
```

## Data Flow Examples

### Wake Word Detection (Android Standalone)
```
[Phone Mic] → AudioRecord → [JNI C++ wakeword_detector] → DTW matching
    → Detected? → WakeWordService.onWakeWordDetected() → Intent → MainActivity
    → Not detected? → Continue listening (battery-safe loop)
```

### AI Chat with Cloud Fallback (Web)
```
[User types] → shims_omni.js → POST /brain/turn
    → ProviderRegistry checks local model availability
    → Local available? → Ollama → Stream response
    → Local unavailable? → Cloud provider (OpenAI/Anthropic) → Stream response
    → Response → TTS (if voiceOn) → Browser speechSynthesis
```

### Abuse Report (Cross-Platform)
```
Web:  reportAbuse() → POST /api/v15/support/abuse-report → storage/support/abuse_*.json
Android: SupportActivity → ApiClient.reportAbuse() → POST /api/v15/support/abuse-report
Enterprise: support.html → POST /api/support/abuse-report → storage/support/abuse_*.json
```

### Subscription (Android)
```
User taps Subscribe → BillingManager.launchPurchaseFlow() → Google Play Billing
    → Purchase successful → acknowledgePurchase() → queryPurchases()
    → Premium unlocked → unlimited chats, cloud sync, advanced voice
```

## Google Play Compliance Checklist

| Requirement | Location | Status |
|-------------|----------|--------|
| Privacy Policy | `shims_android/app/src/main/assets/privacy_policy.html` | ✅ |
| Terms of Use | `shims_android/app/src/main/assets/terms_of_use.html` | ✅ |
| Content Policy | `shims_android/app/src/main/assets/content_policy.html` | ✅ |
| Data Safety Form | `docs/GOOGLE_PLAY_COMPLIANCE.md` | ✅ |
| Account Deletion | Settings > Delete Local Data + support email | ✅ |
| Abuse Reporting | In-app Report Abuse + support@jklifecare.com | ✅ |
| Crash Reporting | Sentry (opt-in) | ✅ |
| In-app billing | Google Play Billing Library v6.1 | ✅ |
