# Using Local Inference Engines with SHIMS Omni

SHIMS can route chat requests to any **OpenAI-compatible local server** instead of Ollama or a cloud provider. This is useful when you are running:

- [Text Generation Inference (TGI)](https://huggingface.co/docs/text-generation-inference)
- [vLLM](https://docs.vllm.ai/)
- [SGLang](https://sgl-project.github.io/)
- [Aphrodite Engine](https://aphrodite.pygmalion.chat/)
- [KoboldCPP](https://github.com/LostRuins/koboldcpp)
- [llama.cpp server](https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md)
- Any other server that exposes `/v1/chat/completions`

## Generic OpenAI-compatible endpoint (HUGGINGFACE_*)

The `HUGGINGFACE_BASE_URL` path works for any OpenAI-compatible server. Set these environment variables **before** starting the SHIMS backend:

```powershell
# Windows PowerShell
$env:HUGGINGFACE_BASE_URL = "http://127.0.0.1:8080"
$env:HUGGINGFACE_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
$env:HUGGINGFACE_API_KEY = ""   # only if your local server requires a bearer token
```

Or use a `.env` file in the project root:

```env
HUGGINGFACE_BASE_URL=http://127.0.0.1:8080
HUGGINGFACE_MODEL=meta-llama/Llama-3.1-8B-Instruct
HUGGINGFACE_API_KEY=
```

SHIMS will route the request to `HUGGINGFACE_BASE_URL/v1/chat/completions` with the standard OpenAI chat payload.

## Dedicated engine providers

SHIMS also has first-class providers for specific engines with their own env vars and health checks:

| Engine | Provider ID | Default Port | Env Vars |
|---|---|---|---|
| vLLM | `vllm` | 8000 | `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY` |
| SGLang | `sglang` | 30000 | `SGLANG_BASE_URL`, `SGLANG_MODEL`, `SGLANG_API_KEY` |
| Aphrodite | `aphrodite` | 2242 | `APHRODITE_BASE_URL`, `APHRODITE_MODEL`, `APHRODITE_API_KEY` |
| KoboldCPP | `koboldcpp` | 5001 | `KOBOLDCPP_BASE_URL`, `KOBOLDCPP_MODEL`, `KOBOLDCPP_API_KEY` |

Example `.env` for vLLM:

```env
VLLM_BASE_URL=http://127.0.0.1:8000
VLLM_MODEL=meta-llama/Llama-3.3-70B-Instruct
VLLM_API_KEY=
```

Then select the provider in SHIMS chat:

```text
Use vLLM model meta-llama/Llama-3.3-70B-Instruct
```

Or set it as the default:

```env
SHIMS_CHAT_PROVIDER=vllm
SHIMS_CHAT_MODEL=meta-llama/Llama-3.3-70B-Instruct
```

## Starting a local endpoint

### vLLM

```powershell
pip install vllm
python -m vllm.entrypoints.api_server --model meta-llama/Llama-3.3-70B-Instruct --host 0.0.0.0 --port 8000
```

### SGLang

```powershell
pip install sglang
python -m sglang.launch_server --model meta-llama/Llama-3.3-70B-Instruct --host 0.0.0.0 --port 30000
```

### Aphrodite Engine

```powershell
pip install aphrodite-engine
aphrodite serve --model Qwen/Qwen2.5-72B-Instruct --host 0.0.0.0 --port 2242
```

### KoboldCPP

```powershell
# Download koboldcpp.exe and a GGUF model, then:
.\koboldcpp.exe --model .\Llama-3.3-70B-Instruct-Q4_K_M.gguf --port 5001 --launch_openai
```

### llama.cpp server

```powershell
llama-server.exe -m .\Llama-3.1-8B-Instruct-Q4_K_M.gguf --host 127.0.0.1 --port 8080 -c 4096
```

## Provider priority

SHIMS tries providers in this order when a model name is ambiguous:

1. Anthropic (`claude-*`)
2. OpenAI (`gpt-*`)
3. Google (`gemini-*`)
4. **Hugging Face / vLLM / SGLang / Aphrodite / KoboldCPP** (any explicit local engine)
5. Ollama / LM Studio (local fallback)

## Hardware notes (Strix Halo / 128GB unified memory)

On AMD Ryzen AI Max+ 395 (Strix Halo) systems with 128GB unified memory:

- The BIOS typically partitions ~32GB for Windows and ~96GB for the GPU.
- **vLLM/SGLang** work best if you have ROCm installed; otherwise they fall back to CPU and are slow.
- **KoboldCPP** and **LM Studio** have native Vulkan support and work well without ROCm.
- A 70B model at Q4_K_M (~40GB) fits comfortably in 96GB GPU memory with room for KV cache.
- A 70B model at Q8_0 (~70GB) also fits but leaves less room for context.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` | Make sure the local server is running and the base URL matches its host/port. |
| `404 Not Found` | The server must expose `/v1/chat/completions`. Ollama's native endpoint is `/api/chat`; use a server with OpenAI compatibility. |
| Slow first response | Local models often need to load weights into GPU/CPU memory on first request. |
| No tool calling | Not all models support tool calls. Use an instruction-tuned model and verify your server sends `tool_calls` in the response. |

## Files involved

- `shared/agent_loop.py` — `_hf_chat_raw()`, `_openai_compatible_chat_raw()` build the request.
- `shared/config.py` — reads all engine env vars.
- `shared/agent_model_router.py` — chooses the provider from the model name / settings.
- `shared/llm_gateway.py` — health checks and provider routing.
