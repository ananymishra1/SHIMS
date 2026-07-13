"""AMD AI acceleration bridge for SHIMS.

Leverages the AMD AI Bundle (Amuse/DirectML) and AMD Radeon 8060S GPU
on Ryzen AI MAX+ 395 systems for accelerated local inference.

This module provides:
- DirectML-based ONNX Runtime inference sessions
- AMD GPU detection and capability reporting
- Fallback to CPU when DirectML is unavailable
- Integration with SHIMS provider registry and agent loop

Usage:
    from shared.amd_acceleration import AMDAccelerator
    accel = AMDAccelerator()
    if accel.available:
        result = accel.run_inference(model_path, inputs)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import settings, get_logger

logger = get_logger("amd_acceleration")

# AMD AI Bundle paths
_AMD_BUNDLE_PATHS = [
    Path(r"C:\Program Files\AMD\AI_Bundle\Amuse"),
    Path(r"C:\Program Files (x86)\AMD\AI_Bundle\Amuse"),
    Path(os.environ.get("AMD_AI_BUNDLE_PATH", "")) if os.environ.get("AMD_AI_BUNDLE_PATH") else None,
]

# DirectML provider GUID for ONNX Runtime
_DIRECTML_PROVIDER = "DmlExecutionProvider"


class AMDAccelerator:
    """AMD GPU acceleration wrapper for SHIMS inference tasks.

    Detects AMD AI Bundle installation, DirectML availability, and
    provides a unified interface for GPU-accelerated model inference.
    """

    def __init__(self) -> None:
        self._bundle_path: Optional[Path] = None
        self._directml_dll: Optional[Path] = None
        self._onnxruntime_dll: Optional[Path] = None
        self._available = False
        self._gpu_name = ""
        self._gpu_vram_mb = 0
        self._detect()

    # ── detection ──────────────────────────────────────────────────────

    def _detect(self) -> None:
        """Probe for AMD AI Bundle and DirectML."""
        for path in _AMD_BUNDLE_PATHS:
            if path and path.exists() and (path / "DirectML.dll").exists():
                self._bundle_path = path
                self._directml_dll = path / "DirectML.dll"
                self._onnxruntime_dll = path / "onnxruntime.dll"
                break

        if not self._bundle_path:
            logger.info("AMD AI Bundle not found — acceleration disabled")
            return

        # Detect GPU via WMI
        self._gpu_name, self._gpu_vram_mb = self._detect_gpu()

        # Check if we can use DirectML via ONNX Runtime Python
        self._available = self._probe_directml()

        if self._available:
            logger.info(
                f"AMD acceleration ready: {self._gpu_name} ({self._gpu_vram_mb}MB VRAM) via DirectML"
            )
        else:
            logger.info("AMD bundle found but DirectML provider unavailable in Python ONNX Runtime")

    def _detect_gpu(self) -> tuple[str, int]:
        """Return (GPU name, VRAM MB) via Windows WMI."""
        try:
            import wmi  # type: ignore
            c = wmi.WMI()
            for gpu in c.Win32_VideoController():
                if gpu.AdapterRAM:
                    vram_mb = int(gpu.AdapterRAM) // (1024 * 1024)
                    return gpu.Name or "AMD GPU", vram_mb
        except Exception:
            pass

        # Fallback: try PowerShell
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "(Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json -Compress)"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if isinstance(data, list) and data:
                    gpu = data[0]
                    name = gpu.get("Name", "AMD GPU")
                    vram = gpu.get("AdapterRAM", 0)
                    vram_mb = int(vram) // (1024 * 1024) if vram else 0
                    return name, vram_mb
                elif isinstance(data, dict):
                    name = data.get("Name", "AMD GPU")
                    vram = data.get("AdapterRAM", 0)
                    vram_mb = int(vram) // (1024 * 1024) if vram else 0
                    return name, vram_mb
        except Exception:
            pass

        return "AMD GPU", 0

    def _probe_directml(self) -> bool:
        """Check if ONNX Runtime can load the DirectML provider."""
        try:
            import onnxruntime as ort
            providers = ort.get_available_providers()
            return _DIRECTML_PROVIDER in providers
        except Exception as exc:
            logger.debug(f"DirectML probe failed: {exc}")
            return False

    # ── properties ───────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    @property
    def gpu_name(self) -> str:
        return self._gpu_name

    @property
    def gpu_vram_mb(self) -> int:
        return self._gpu_vram_mb

    @property
    def bundle_path(self) -> Optional[Path]:
        return self._bundle_path

    # ── inference helpers ────────────────────────────────────────────────

    def create_onnx_session(self, model_path: str | Path) -> Any:
        """Create an ONNX Runtime InferenceSession with DirectML if available.

        Falls back to CPUExecutionProvider if DirectML is not usable.
        """
        import onnxruntime as ort

        if self._available:
            providers = [_DIRECTML_PROVIDER, "CPUExecutionProvider"]
            logger.debug(f"Loading ONNX model with DirectML: {model_path}")
        else:
            providers = ["CPUExecutionProvider"]
            logger.debug(f"Loading ONNX model with CPU: {model_path}")

        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        return ort.InferenceSession(str(model_path), session_options, providers=providers)

    def run_inference(self, model_path: str | Path, inputs: dict[str, Any]) -> dict[str, Any]:
        """Run a single inference pass and return outputs as numpy arrays."""
        import numpy as np

        session = self.create_onnx_session(model_path)
        input_names = [inp.name for inp in session.get_inputs()]
        output_names = [out.name for out in session.get_outputs()]

        # Feed only the inputs the model expects
        feed = {name: inputs[name] for name in input_names if name in inputs}
        results = session.run(output_names, feed)
        return {name: arr for name, arr in zip(output_names, results)}

    # ── health / status ──────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        """Return a JSON-serializable health dict for the SHIMS health endpoint."""
        return {
            "available": self._available,
            "gpu_name": self._gpu_name,
            "gpu_vram_mb": self._gpu_vram_mb,
            "bundle_path": str(self._bundle_path) if self._bundle_path else None,
            "directml_dll": str(self._directml_dll) if self._directml_dll else None,
        }


# ── singleton ──────────────────────────────────────────────────────────
_amd_accel: Optional[AMDAccelerator] = None


def get_amd_accelerator() -> AMDAccelerator:
    global _amd_accel
    if _amd_accel is None:
        _amd_accel = AMDAccelerator()
    return _amd_accel


# ── SHIMS integration hooks ────────────────────────────────────────────

async def amd_gpu_status() -> dict[str, Any]:
    """FastAPI-friendly async wrapper for health checks."""
    return get_amd_accelerator().health()


def suggest_amd_optimized_model(original_model: str) -> str:
    """Given a model name, suggest an AMD-optimized variant if available.

    For now this is a simple mapping; in the future it could query
    the AMD model zoo or Hugging Face for DirectML-optimized ONNX exports.
    """
    # Map common models to AMD-optimized ONNX variants
    _AMD_OPTIMIZED: dict[str, str] = {
        "llama3.2": "amd-optimized/Llama-3.2-ONNX-DirectML",
        "qwen2.5": "amd-optimized/Qwen2.5-ONNX-DirectML",
        "phi4": "amd-optimized/Phi-4-ONNX-DirectML",
    }
    lower = original_model.lower()
    for key, val in _AMD_OPTIMIZED.items():
        if key in lower:
            return val
    return original_model


# ── env var helpers ──────────────────────────────────────────────────────

def amd_env_report() -> dict[str, str]:
    """Return a dict of relevant AMD environment variables."""
    keys = [
        "AMD_AI_BUNDLE_PATH",
        "AMD_DIRECTML_PATH",
        "ONNXRUNTIME_PROVIDER",
    ]
    return {k: os.environ.get(k, "<not set>") for k in keys}


# ── AMUSE video model detection ─────────────────────────────────────────

_AMUSE_MODEL_BASE = Path(r"C:\Users\direc\AppData\Local\Amuse\Models")

_AMUSE_VIDEO_MODELS = {
    "wan2.2-t2v-a14b": {
        "path": _AMUSE_MODEL_BASE / "Diffusion" / "Wan2.2-T2V-A14B-Diffusers",
        "pipeline": "WanPipeline",
        "family": "wan",
    },
    "wan2.1-t2v-14b": {
        "path": _AMUSE_MODEL_BASE / "Diffusion" / "Wan2.1-T2V-14B-Diffusers",
        "pipeline": "WanPipeline",
        "family": "wan",
    },
    "cogvideox-2b": {
        "path": _AMUSE_MODEL_BASE / "Diffusion" / "CogVideoX_2B",
        "pipeline": "CogVideoXPipeline",
        "family": "cogvideox",
    },
    "cogvideox-5b": {
        "path": _AMUSE_MODEL_BASE / "Diffusion" / "CogVideoX_5B",
        "pipeline": "CogVideoXPipeline",
        "family": "cogvideox",
    },
}


def _is_complete_model(path: Path) -> bool:
    """Check whether an AMUSE model folder has finished downloading.

    AMUSE downloads weights as *.safetensors.download / *.bin.download
    files while they are in progress. A model is usable only when at
    least one real weight file exists and no .download siblings remain.
    """
    if not path.exists():
        return False
    has_weight = False
    has_incomplete = False
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        name = f.name.lower()
        if name.endswith(".download"):
            has_incomplete = True
        elif name.endswith((".safetensors", ".bin", ".pth", ".pt", ".ckpt")):
            has_weight = True
    return has_weight and not has_incomplete


def _model_download_state(path: Path) -> dict[str, Any]:
    """Describe the on-disk state of a partially-downloaded AMUSE model."""
    state = {"has_weights": False, "has_partial": False, "stale_minutes": None, "active": False}
    now = time.time()
    newest_partial = 0.0
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        name = f.name.lower()
        if name.endswith(".download"):
            state["has_partial"] = True
            mtime = f.stat().st_mtime
            newest_partial = max(newest_partial, mtime)
        elif name.endswith((".safetensors", ".bin", ".pth", ".pt", ".ckpt")):
            state["has_weights"] = True
    if state["has_partial"] and newest_partial:
        stale_minutes = (now - newest_partial) / 60.0
        state["stale_minutes"] = round(stale_minutes, 1)
        # Consider active only if a .download file was touched in the last 5 minutes
        state["active"] = stale_minutes < 5
    return state


def find_amuse_video_model() -> dict[str, Any] | None:
    """Return the first fully-downloaded AMUSE video diffusion model path."""
    for name, info in _AMUSE_VIDEO_MODELS.items():
        model_path = info["path"]
        if _is_complete_model(model_path):
            return {
                "name": name,
                "path": str(model_path),
                "pipeline": info["pipeline"],
                "family": info["family"],
            }
    return None


def amuse_video_status() -> dict[str, Any]:
    """Report whether an AMUSE video model is available for SHIMS to use."""
    amuse_running = False
    try:
        import subprocess
        result = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=5)
        amuse_running = "Amuse.exe" in result.stdout
    except Exception:
        pass

    # Look for any partially downloaded models so the user knows why nothing is ready
    incomplete: list[dict[str, Any]] = []
    for name, info in _AMUSE_VIDEO_MODELS.items():
        model_path = info["path"]
        if not model_path.exists():
            continue
        if _is_complete_model(model_path):
            continue
        dl_state = _model_download_state(model_path)
        incomplete.append({
            "name": name,
            "path": str(model_path),
            "status": "downloading" if dl_state["active"] else "stale-partial",
            "stale_minutes": dl_state["stale_minutes"],
        })

    model = find_amuse_video_model()
    accel = get_amd_accelerator()

    note_parts = []
    if model:
        note_parts.append("AMUSE video model detected and ready.")
    else:
        note_parts.append("No fully-downloaded AMUSE video model found.")
    if incomplete:
        stale_count = sum(1 for item in incomplete if item["status"] == "stale-partial")
        active_count = sum(1 for item in incomplete if item["status"] == "downloading")
        if active_count:
            note_parts.append(f"{active_count} model(s) actively downloading in AMUSE.")
        if stale_count:
            note_parts.append(f"{stale_count} model(s) have stale partial files. Open AMUSE, select the video model, and resume/queue the download.")
    if amuse_running and not model and not any(item["status"] == "downloading" for item in incomplete):
        note_parts.append("AMUSE is open but not downloading a video model. Pick a Text-to-Video model in AMUSE to finish the download.")
    if not accel.available:
        note_parts.append("DirectML not available in Python env — generation would run on CPU and be very slow.")

    return {
        "amuse_running": amuse_running,
        "model": model,
        "incomplete_downloads": incomplete,
        "amd_gpu": accel.health(),
        "note": " ".join(note_parts),
    }


# ── ComfyUI integration ─────────────────────────────────────────────────

_COMFYUI_DEFAULT_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")


def comfy_ui_status() -> dict[str, Any]:
    """Probe the local ComfyUI instance."""
    import urllib.request
    url = _COMFYUI_DEFAULT_URL.rstrip("/") + "/system_stats"
    try:
        with urllib.request.urlopen(url, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "ok": True,
            "url": _COMFYUI_DEFAULT_URL,
            "system_stats": data,
            "note": "ComfyUI is reachable.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": _COMFYUI_DEFAULT_URL,
            "error": str(exc),
            "note": "ComfyUI not reachable at the default URL. Start ComfyUI or set COMFYUI_URL.",
        }


def _comfy_list_checkpoints() -> list[str]:
    """Return the list of available checkpoint model files in ComfyUI."""
    import urllib.request
    url = _COMFYUI_DEFAULT_URL.rstrip("/") + "/api/models/checkpoints"
    try:
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _comfy_queue_prompt(workflow: dict[str, Any]) -> dict[str, Any]:
    """Queue a prompt on ComfyUI and return the prompt_id."""
    import urllib.request
    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    url = _COMFYUI_DEFAULT_URL.rstrip("/") + "/prompt"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _comfy_get_history(prompt_id: str, timeout: float = 120.0) -> dict[str, Any]:
    """Poll ComfyUI history for a prompt_id."""
    import urllib.request
    url_base = _COMFYUI_DEFAULT_URL.rstrip("/") + "/history/" + prompt_id
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url_base, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data:
                return data
        except Exception:
            pass
        time.sleep(2.0)
    return {}


def _comfy_download_image(filename: str, subfolder: str, folder_type: str, output_path: Path) -> Path:
    """Download an output image from ComfyUI."""
    import urllib.request
    import urllib.parse
    params = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": folder_type})
    url = _COMFYUI_DEFAULT_URL.rstrip("/") + "/view?" + params
    output_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, str(output_path))
    return output_path


def build_comfy_text_to_image_workflow(prompt: str, width: int = 512, height: int = 512, seed: int = 0, ckpt_name: str | None = None) -> dict[str, Any]:
    """Return a minimal ComfyUI workflow dict for txt2img.

    This is a basic KSampler workflow. It uses the supplied checkpoint name,
    or picks the first available ComfyUI checkpoint. If no checkpoint is
    available, a ValueError is raised so callers can surface a clear message.
    """
    if seed <= 0:
        seed = int(time.time()) % 2**32

    if ckpt_name:
        chosen_ckpt = ckpt_name
    else:
        available = _comfy_list_checkpoints()
        if not available:
            raise ValueError(
                "No ComfyUI checkpoint models found. "
                "Download a Stable Diffusion checkpoint (e.g. SD 1.5 or SDXL) "
                "into ComfyUI's models/checkpoints folder and retry."
            )
        chosen_ckpt = available[0]

    return {
        "3": {
            "inputs": {"text": prompt, "clip": ["4", 1]},
            "class_type": "CLIPTextEncode",
        },
        "4": {
            "inputs": {"ckpt_name": chosen_ckpt},
            "class_type": "CheckpointLoaderSimple",
        },
        "5": {
            "inputs": {"width": width, "height": height, "batch_size": 1},
            "class_type": "EmptyLatentImage",
        },
        "6": {
            "inputs": {
                "text": "",
                "clip": ["4", 1],
            },
            "class_type": "CLIPTextEncode",
        },
        "7": {
            "inputs": {
                "seed": seed,
                "steps": 20,
                "cfg": 8.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["3", 0],
                "negative": ["6", 0],
                "latent_image": ["5", 0],
            },
            "class_type": "KSampler",
        },
        "8": {
            "inputs": {"samples": ["7", 0], "vae": ["4", 2]},
            "class_type": "VAEDecode",
        },
        "9": {
            "inputs": {"filename_prefix": "shims_comfy", "images": ["8", 0]},
            "class_type": "SaveImage",
        },
    }


async def generate_comfy_image(
    prompt: str,
    output_path: Path,
    width: int = 512,
    height: int = 512,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate an image via a local ComfyUI instance."""
    status = comfy_ui_status()
    if not status.get("ok"):
        return {"ok": False, "error": status.get("note") or "ComfyUI not reachable", "provider": "comfyui"}

    try:
        wf = workflow or build_comfy_text_to_image_workflow(prompt, width=width, height=height)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "provider": "comfyui"}

    try:
        queued = _comfy_queue_prompt(wf)
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            return {"ok": False, "error": "ComfyUI did not return a prompt_id", "provider": "comfyui"}
        history = _comfy_get_history(prompt_id, timeout=180.0)
        entry = history.get(prompt_id, {})
        outputs = entry.get("outputs", {})
        if not outputs:
            return {"ok": False, "error": "ComfyUI produced no outputs", "provider": "comfyui", "prompt_id": prompt_id}
        # Download the first image output
        for node_id, node_outputs in outputs.items():
            images = node_outputs.get("images", [])
            for img in images:
                filename = img.get("filename")
                subfolder = img.get("subfolder", "")
                if filename:
                    downloaded = _comfy_download_image(filename, subfolder, "output", output_path)
                    return {
                        "ok": True,
                        "provider": "comfyui",
                        "path": str(downloaded),
                        "prompt_id": prompt_id,
                    }
        return {"ok": False, "error": "No image files in ComfyUI outputs", "provider": "comfyui", "prompt_id": prompt_id}
    except Exception as exc:
        return {"ok": False, "error": f"ComfyUI generation failed: {exc}", "provider": "comfyui"}


async def generate_amuse_video(prompt: str, output_path: Path, width: int = 720, height: int = 480, frames: int = 16) -> dict[str, Any]:
    """Generate a video using a locally installed AMUSE diffusers model.

    This requires the diffusers package and a PyTorch build that can run on
    the AMD GPU (DirectML/ROCm) or CPU. If the environment cannot load the
    pipeline, a clear error is returned so callers can fall back to other
    backends.
    """
    model = find_amuse_video_model()
    if not model:
        # Check if a download is in progress so we can report it
        incomplete = []
        for name, info in _AMUSE_VIDEO_MODELS.items():
            if info["path"].exists() and not _is_complete_model(info["path"]):
                state = _model_download_state(info["path"])
                status = "downloading" if state["active"] else "stale partial files"
                incomplete.append(f"{name} ({status})")
        if incomplete:
            return {"ok": False, "error": f"AMUSE model(s) incomplete: {', '.join(incomplete)}. Open AMUSE, queue the video model download, and retry once finished."}
        return {"ok": False, "error": "No AMUSE video model is installed."}

    try:
        import torch
        from diffusers import WanPipeline, CogVideoXPipeline  # type: ignore
    except Exception as exc:
        return {
            "ok": False,
            "error": f"AMUSE model found but diffusers/torch not available: {exc}",
            "model": model,
        }

    # CPU-only fallback: use tiny dimensions and few frames so it doesn't hang forever
    accel = get_amd_accelerator()
    if not torch.cuda.is_available() and not accel.available:
        width = min(width, 320)
        height = min(height, 240)
        frames = min(frames, 8)

    try:
        family = model.get("family", "")
        if family == "wan":
            pipe = WanPipeline.from_pretrained(
                model["path"],
                torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            )
        elif family == "cogvideox":
            pipe = CogVideoXPipeline.from_pretrained(
                model["path"],
                torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            )
        else:
            return {"ok": False, "error": f"Unsupported AMUSE pipeline family: {family}"}

        # Use CUDA if available; otherwise CPU (slow but functional)
        if torch.cuda.is_available():
            pipe.to("cuda")
        else:
            pipe.to("cpu")

        result = pipe(
            prompt,
            width=width,
            height=height,
            num_frames=frames,
            num_inference_steps=25,
        ).frames[0]

        # Export frames to MP4 using ffmpeg or imageio
        try:
            import imageio
            writer = imageio.get_writer(str(output_path), fps=8, codec="libx264")
            for frame in result:
                writer.append_data(frame)
            writer.close()
        except Exception:
            # Fallback: save frames as GIF if MP4 fails
            output_path = output_path.with_suffix(".gif")
            result[0].save(
                str(output_path),
                save_all=True,
                append_images=result[1:],
                duration=125,
                loop=0,
            )

        return {
            "ok": True,
            "provider": "amuse",
            "model": model["name"],
            "path": str(output_path),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"AMUSE video generation failed: {exc}",
            "model": model,
        }
