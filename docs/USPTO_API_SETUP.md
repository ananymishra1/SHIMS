# USPTO API Setup Guide for SHIMS

## Overview
SHIMS Enterprise supports USPTO Patent Public Search (PPUBS) for real US patent lookups. This guide explains how to obtain and configure your free USPTO API key.

## Step 1: Register for a USPTO Developer Account
1. Visit https://developer.uspto.gov/
2. Click **Sign Up** and create a free account
3. Verify your email address

## Step 2: Request API Access
1. Log in to the USPTO Developer Portal
2. Navigate to **APIs** → **Patent Public Search** (PPUBS)
3. Click **Subscribe** or **Request Access**
4. Fill out the short usage questionnaire (select "Research / Educational" if applicable)
5. Submit the request

## Step 3: Get Your API Key
1. Once approved (usually instant for research use), go to **My Apps** or **API Keys**
2. Create a new application named "SHIMS Enterprise"
3. Copy the generated **API Key / Client ID**
4. Note: Some versions also provide a **Client Secret** — keep both safe

## Step 4: Configure SHIMS
Add the key to your `.env` file:

```bash
USPTO_API_KEY=your_uspto_key_here
```

Restart SHIMS Enterprise for the change to take effect.

## Step 5: Verify
Open the R&D Process page in Enterprise and run a patent search. If the USPTO key is configured, US patents will be queried directly from the USPTO database and returned with real patent numbers, titles, assignees, and filing dates.

## Troubleshooting
- **"USPTO_API_KEY is not set"**: The key is missing from `.env`. Add it and restart.
- **401 Unauthorized**: Your key may be expired or the subscription not yet active. Check the USPTO developer portal.
- **No USPTO results**: If the USPTO API is down, SHIMS automatically falls back to SerpAPI Google Patents → web search.

## Rate Limits
- USPTO PPUBS: ~100 requests/minute for free tier
- If you exceed limits, SHIMS will silently fall back to other providers

## Chinese Patents (CNIPA)
SHIMS also supports Chinese patent search via CNIPA / Google Patents cross-index:
- No separate API key is required
- SerpAPI (already configured) handles CNIPA indexing automatically
- Results are mixed with US and global patents in the R&D Brain
