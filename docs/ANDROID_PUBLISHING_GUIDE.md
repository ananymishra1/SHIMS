# SHIMS Android — Google Play Publishing Guide

## Prerequisites

1. **Google Play Developer Account** ($25 one-time fee)
   - Sign up at https://play.google.com/console
   - Complete identity verification

2. **Android SDK & NDK**
   - Android Studio Hedgehog (2023.1.1) or later
   - NDK r26b or later
   - CMake 3.22.1+

3. **Signing Keystore**
   ```bash
   keytool -genkey -v -keystore shims-release.keystore -alias shims -keyalg RSA -keysize 2048 -validity 10000
   ```

## Build Release APK

### Step 1: Configure signing
Create `shims_android/local.properties`:
```properties
sdk.dir=C:\\Users\\YOURNAME\\AppData\\Local\\Android\\Sdk
ndk.dir=C:\\Users\\YOURNAME\\AppData\\Local\\Android\\Sdk\\ndk\\26.1.10909125
```

Create `shims_android/keystore.properties`:
```properties
storeFile=../shims-release.keystore
storePassword=YOUR_STORE_PASSWORD
keyAlias=shims
keyPassword=YOUR_KEY_PASSWORD
```

### Step 2: Build
```bash
cd shims_android
./gradlew assembleRelease
```

The APK will be at:
```
shims_android/app/build/outputs/apk/release/app-release.apk
```

## Upload to Google Play

1. **Create App** in Play Console
   - App name: "SHIMS AI"
   - Default language: English (India)
   - App category: Productivity
   - Free / Paid: Free with subscriptions

2. **App Content**
   - Privacy Policy URL: `https://shims.jklifecare.com/privacy`
   - Terms of Service URL: `https://shims.jklifecare.com/terms`
   - App access: All functionality available without restrictions (free tier)

3. **Store Listing**
   - Short description: "Your personal AI assistant. Private, voice-activated, works offline."
   - Full description: Include features, privacy highlights, subscription info.
   - Screenshots: Capture onboarding, chat, settings, voice activation.
   - Feature graphic: 1024x500px banner.
   - App icon: 512x512px (already in mipmap).

4. **Data Safety Form**
   - Fill out using `docs/GOOGLE_PLAY_COMPLIANCE.md`
   - Declare all data types collected.

5. **Content Rating**
   - Answer the questionnaire honestly.
   - Expected rating: PEGI 3 / ESRB Everyone.

6. **Pricing & Distribution**
   - Countries: Select all or target markets.
   - In-app products: Configure `shims_premium_monthly` and `shims_premium_yearly` in Play Console > Monetization > Products.

7. **Release**
   - Upload APK to Internal Testing first.
   - Test on your device via Play Console internal sharing link.
   - Promote to Closed Testing → Open Testing → Production.

## Testing on Your Phone

### Method 1: Direct APK Install (Fastest)
```bash
adb install shims_android/app/build/outputs/apk/release/app-release.apk
```

### Method 2: Internal Testing Track
1. Add your Google account email to Internal Testers in Play Console.
2. Upload APK to Internal Testing.
3. Open the opt-in link on your phone.
4. Install from Play Store.

### Method 3: Android Studio
1. Connect phone via USB (enable Developer Options > USB Debugging).
2. Click "Run" in Android Studio with your phone selected.

## Post-Launch Checklist

- [ ] Monitor crash reports in Sentry dashboard
- [ ] Respond to user reviews within 48 hours
- [ ] Update privacy policy if data practices change
- [ ] Keep subscription products active in Play Console
- [ ] Update app description with new features
