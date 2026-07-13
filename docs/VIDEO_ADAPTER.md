# Video Adapter Notes

The local package stores video requests as JSON files so prompts are not lost when no cloud video provider is configured.

Production video generation should be implemented as a queued job:

1. Accept prompt and policy metadata.
2. Create a generation request with the provider.
3. Poll for job completion.
4. Download result to `storage/generated`.
5. Return a local download URL.

The app is structured so this can be added inside `shared/media_service.py` without changing the Omni frontend contract.
