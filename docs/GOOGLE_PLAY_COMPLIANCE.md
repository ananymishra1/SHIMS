# SHIMS AI — Google Play Compliance Guide

## Data Safety Form (Play Console)

### Data Collection
| Data Type | Collected? | Shared? | Encrypted? | Purpose | Required? |
|-----------|-----------|---------|------------|---------|-----------|
| Email address | ✓ (account) | ✗ | ✓ | Account management, subscription | No (anonymous use allowed) |
| Voice audio | ✓ (opt-in) | ✗ | ✓ | Wake word enrollment, voice commands | No |
| Crash logs | ✓ (opt-in) | ✓ (Sentry) | ✓ | Stability improvements | No |
| Usage analytics | ✓ (opt-in) | ✗ | ✓ | Feature improvement | No |
| Device info | ✓ (minimal) | ✗ | ✓ | Diagnostics | No |
| Chat messages | ✓ | ✓ (cloud AI only) | ✓ | AI conversation | Yes (core feature) |

### Data Usage & Sharing
- **Third parties:** Google Play Billing (payments), Sentry (crash analytics, opt-in), Cloud AI providers (only when user selects cloud model).
- **No sale of data:** We do not sell user data.
- **Encryption:** All network traffic uses TLS 1.2+. Local data is stored in app-private storage.

### Account Deletion
Users can delete local data via Settings > Delete Local Data. Cloud data deletion requires email to support@jklifecare.com. We respond within 30 days.

## Required Policies

1. **Privacy Policy** — `assets/privacy_policy.html` (in-app, offline)
2. **Terms of Use** — `assets/terms_of_use.html` (in-app, offline)
3. **Content Policy** — `assets/content_policy.html` (in-app, offline)
4. **Abuse reporting** — In-app via Settings > Support > Report Abuse

## Content Rating
- **Category:** Tools / Productivity
- **Content descriptors:** Users can interact with an AI that generates text. No user-generated content is shared publicly.

## Target Audience
- Primary: Adults 18+
- Not designed for children under 13.

## Permissions Justification
| Permission | Justification |
|------------|---------------|
| `INTERNET` | Required for cloud AI, backend bridge, and subscription verification |
| `RECORD_AUDIO` | Required for voice input and wake word detection |
| `FOREGROUND_SERVICE` | Required for battery-safe wake word listening |
| `POST_NOTIFICATIONS` | Required to show wake word detection status |
| `RECEIVE_BOOT_COMPLETED` | Required to restart wake word service after reboot |
| `BILLING` | Required for Google Play subscription purchases |
