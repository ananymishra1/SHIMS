# Using a local Hugging Face endpoint with SHIMS Omni

SHIMS can route chat requests to any **OpenAI-compatible local server** instead of Ollama or a cloud provider. This is useful when you are running:

- [Text Generation Inference (TGI)](https://huggingface.co/docs/text-generation-inference)
- [vLLM](https://docs.vllm.ai/)
- [llama.cpp server](https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md)
- Any other server that exposes `/v1/chat/completions`

## Configuration

Set these environment variables **before** starting the SHIMS backend:

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

## Starting a local endpoint (llama.cpp example)

```powershell
# Download a GGUF model or use one in storage/models
$model = "E:\shims_final_omni_enterprise_2026\storage\models\Llama-3.1-8B-Instruct-Q4_K_M.gguf"

# Start the OpenAI-compatible server
llama-server.exe -m $model --host 127.0.0.1 --port 8080 -c 4096
```

## Selecting the Hugging Face model in SHIMS

Once the backend is running, you can ask SHIMS to use the Hugging Face provider by naming the model exactly as configured in `HUGGINGFACE_MODEL`:

```text
Use Hugging Face model meta-llama/Llama-3.1-8B-Instruct
```

SHIMS will route the request to `HUGGINGFACE_BASE_URL/v1/chat/completions` with the standard OpenAI chat payload.

## Provider priority

SHIMS tries providers in this order when a model name is ambiguous:

1. Anthropic (`claude-*`)
2. OpenAI (`gpt-*`)
3. Google (`gemini-*`)
4. **Hugging Face** (`HUGGINGFACE_MODEL` or any explicit model when HF is selected)
5. Ollama / local (fallback)

## DuoBot / Local Factory

DuoBot keeps the **Primary** agent on its configured cloud/local model and the **Local Factory** agent on the local Ollama peer by default. If you want the Local Factory to talk to the Hugging Face endpoint instead, set the same `HUGGINGFACE_*` variables on the Local Factory instance (port 8030) and configure its default model accordingly.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` | Make sure the local server is running and `HUGGINGFACE_BASE_URL` matches its host/port. |
| `404 Not Found` | The server must expose `/v1/chat/completions`. Ollama's native endpoint is `/api/chat`; use a server with OpenAI compatibility. |
| Slow first response | Local models often need to load weights into GPU/CPU memory on first request. |
| No tool calling | Not all HF-format models support tool calls. Use an instruction-tuned model and verify your server sends `tool_calls` in the response. |

## Files involved

- `shared/agent_loop.py` — `_hf_chat_raw()` builds the request.
- `shared/config.py` — reads `HUGGINGFACE_BASE_URL`, `HUGGINGFACE_MODEL`, `HUGGINGFACE_API_KEY`.
- `shared/agent_model_router.py` — chooses the provider from the model name / settings.
