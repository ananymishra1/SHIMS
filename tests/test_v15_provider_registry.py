try:
    from shared.provider_registry import resolve_provider
except Exception:
    from shared.provider_registry_patch import resolve_provider


def test_claude_never_routes_to_ollama():
    p, _ = resolve_provider("ollama", "claude-sonnet-4-6")
    assert p == "anthropic"


def test_llama_routes_to_ollama():
    p, _ = resolve_provider("auto", "llama3.2:latest")
    assert p == "ollama"


def test_force_local_clears_cloud():
    p, m = resolve_provider("anthropic", "claude-sonnet-4-6", force_local=True)
    assert p == "ollama"
    assert m == "llama3.2:latest"
