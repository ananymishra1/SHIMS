import asyncio
from apps.shims_omni.main import app


def test_routes_exist():
    paths = {r.path for r in app.routes}
    assert '/chat/models' in paths
    assert '/health' in paths
    assert '/api/v11/chat/turn' in paths


def test_model_fallback_mentions_selection():
    from shared.ai import FallbackProvider
    result = asyncio.run(FallbackProvider().complete('how do i select llama model'))
    assert 'model' in result.text.lower() or 'ollama' in result.text.lower()
