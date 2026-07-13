"""Tests for AMD media tool routing in shared/agent_tools.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_generate_image_prefers_comfyui_when_available() -> None:
    """When ComfyUI is reachable, media.generate_image should use it."""
    from shared import agent_tools

    comfy_result = {"ok": True, "provider": "comfyui", "path": "/tmp/comfy.png"}
    pollinations_result = {"ok": True, "provider": "pollinations", "url": "http://pollinations.ai/x.png"}

    with patch("shared.amd_acceleration.comfy_ui_status", return_value={"ok": True}) as mock_status, \
         patch("shared.amd_acceleration.generate_comfy_image", return_value=comfy_result) as mock_comfy, \
         patch("shared.media_tools.generate_image", return_value=pollinations_result) as mock_pollinations:
        result = agent_tools.run_tool("media.generate_image", {"prompt": "a cat", "backend": "auto"})
        assert result.get("provider") == "comfyui"
        mock_status.assert_called_once()
        mock_comfy.assert_called_once()
        mock_pollinations.assert_not_called()


@pytest.mark.asyncio
async def test_generate_image_falls_back_to_pollinations() -> None:
    """When ComfyUI is not reachable, media.generate_image falls back."""
    from shared import agent_tools

    pollinations_result = {"ok": True, "provider": "pollinations", "url": "http://pollinations.ai/x.png"}

    with patch("shared.amd_acceleration.comfy_ui_status", return_value={"ok": False}), \
         patch("shared.amd_acceleration.generate_comfy_image", return_value={"ok": False}), \
         patch("shared.media_tools.generate_image", return_value=pollinations_result) as mock_pollinations:
        result = agent_tools.run_tool("media.generate_image", {"prompt": "a cat", "backend": "auto"})
        assert result.get("provider") == "pollinations"
        mock_pollinations.assert_called_once()


def test_generate_video_prefers_amuse_when_available() -> None:
    """When AMUSE video model is ready, media.generate_video should use it."""
    from shared import agent_tools

    amuse_result = {"ok": True, "provider": "amuse", "path": "/tmp/amuse.mp4"}
    placeholder_result = {"ok": True, "provider": "placeholder", "note": "use endpoint"}

    with patch("shared.amd_acceleration.find_amuse_video_model", return_value={"name": "wan2.1-t2v-14b"}) as mock_find, \
         patch("shared.amd_acceleration.generate_amuse_video", return_value=amuse_result) as mock_amuse, \
         patch("shared.media_tools.generate_video_placeholder", return_value=placeholder_result) as mock_placeholder:
        result = agent_tools.run_tool("media.generate_video", {"prompt": "a robot dancing", "backend": "auto"})
        assert result.get("provider") == "amuse"
        mock_find.assert_called_once()
        mock_amuse.assert_called_once()
        mock_placeholder.assert_not_called()


def test_generate_video_falls_back_to_placeholder() -> None:
    """When AMUSE model is missing, media.generate_video falls back."""
    from shared import agent_tools

    placeholder_result = {"ok": True, "provider": "placeholder", "note": "use endpoint"}

    with patch("shared.amd_acceleration.find_amuse_video_model", return_value=None), \
         patch("shared.amd_acceleration.generate_amuse_video", return_value={"ok": False}), \
         patch("shared.media_tools.generate_video_placeholder", return_value=placeholder_result) as mock_placeholder:
        result = agent_tools.run_tool("media.generate_video", {"prompt": "a robot dancing", "backend": "auto"})
        assert result.get("provider") == "placeholder"
        mock_placeholder.assert_called_once()


def test_agent_spawn_tool_registered() -> None:
    """agent.spawn should be in the tool registry."""
    from shared import agent_tools
    assert "agent.spawn" in agent_tools.TOOLS
    spec = agent_tools.TOOLS["agent.spawn"].spec()
    assert "prompt" in str(spec)
