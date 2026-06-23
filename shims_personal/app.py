"""Legacy SHIMS Personal wake-word API shim.

The personal app wake-word detector shares the same local detector/trainer used
by Omni. This keeps older Android/personal clients alive without duplicating the
full Omni backend.
"""

from fastapi import FastAPI, Request

from shared.wakeword import WakeWordTrainer, get_detector


app = FastAPI(title="SHIMS Personal Compatibility")


@app.get("/api/v15/wakeword/status")
async def wakeword_status() -> dict[str, object]:
    return {"ok": True, "status": get_detector().status()}


@app.post("/api/v15/wakeword/detect")
async def wakeword_detect(request: Request) -> dict[str, object]:
    audio_bytes = await request.body()
    transcript = request.query_params.get("transcript")
    result = get_detector().detect(audio_bytes, transcript=transcript)
    if result:
        return {"ok": True, "detected": True, **result}
    return {"ok": True, "detected": False}


@app.get("/api/v15/wakeword/list")
async def wakeword_list() -> dict[str, object]:
    return {"ok": True, "wake_words": WakeWordTrainer().list_wake_words()}


__all__ = ["app"]
