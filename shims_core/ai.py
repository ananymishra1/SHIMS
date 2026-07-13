from __future__ import annotations

import json
from typing import Dict, List, Any
import httpx
from .settings import settings

SYSTEM = """You are SHIMS Omni, a local-first AI assistant for J K Lifecare Centers.
You are a separate independent entity by default. You may control Enterprise only after explicit pairing.
Speak naturally, remember the user's SHIMS project context, and help with coding, documents, research, pharma workflows, dashboards, and operations.
For pharma/manufacturing decisions, be practical, audit-friendly, and clear that final regulated decisions require human approval.
"""


async def ollama_models() -> list[dict[str, Any]]:
    """Return installed Ollama models from the local Ollama daemon."""
    url = settings.ollama_base_url.rstrip('/') + '/api/tags'
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        return data.get('models', [])


async def ollama_chat(messages: List[Dict[str, str]], model: str | None = None, temperature: float = 0.7) -> str:
    url = settings.ollama_base_url.rstrip('/') + '/api/chat'
    payload = {
        'model': model or settings.ollama_model,
        'messages': messages,
        'stream': False,
        'options': {'temperature': temperature},
        'keep_alive': '30m',
    }
    async with httpx.AsyncClient(timeout=180) as client:
        res = await client.post(url, json=payload)
        res.raise_for_status()
        data = res.json()
        return data.get('message', {}).get('content', '')


async def anthropic_chat(messages: List[Dict[str, str]], model: str | None = None) -> str:
    """Chat via Anthropic Claude API."""
    from shared.config import settings
    if not settings.anthropic_api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not configured')
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    system_msg = ""
    chat_msgs = []
    for m in messages:
        if m['role'] == 'system':
            system_msg = m['content']
        else:
            chat_msgs.append({"role": m['role'], "content": m['content']})
    response = await client.messages.create(
        model=model or settings.anthropic_model,
        max_tokens=8192,
        system=system_msg or None,
        messages=chat_msgs
    )
    return response.content[0].text if response.content else ''


async def kimi_chat(messages: List[Dict[str, str]], model: str | None = None) -> str:
    from shared.config import settings
    if not settings.kimi_api_key:
        raise RuntimeError('KIMI_API_KEY not configured')
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=settings.kimi_base_url, api_key=settings.kimi_api_key)
    response = await client.chat.completions.create(model=model or settings.kimi_model, messages=messages)
    return response.choices[0].message.content or ''


async def deepseek_chat(messages: List[Dict[str, str]], model: str | None = None) -> str:
    from shared.config import settings
    if not settings.deepseek_api_key:
        raise RuntimeError('DEEPSEEK_API_KEY not configured')
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key)
    response = await client.chat.completions.create(model=model or settings.deepseek_model, messages=messages)
    return response.choices[0].message.content or ''


async def qwen_chat(messages: List[Dict[str, str]], model: str | None = None) -> str:
    from shared.config import settings
    if not settings.qwen_api_key:
        raise RuntimeError('QWEN_API_KEY not configured')
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=settings.qwen_base_url, api_key=settings.qwen_api_key)
    response = await client.chat.completions.create(model=model or settings.qwen_model, messages=messages)
    return response.choices[0].message.content or ''


async def huggingface_chat(messages: List[Dict[str, str]], model: str | None = None) -> str:
    from shared.config import settings
    url = settings.huggingface_base_url.rstrip('/') + '/v1/chat/completions'
    payload = {
        'model': model or settings.huggingface_model,
        'messages': messages,
        'stream': False,
    }
    headers = {'Content-Type': 'application/json'}
    if settings.huggingface_api_key:
        headers['Authorization'] = f'Bearer {settings.huggingface_api_key}'
    async with httpx.AsyncClient(timeout=180) as client:
        res = await client.post(url, json=payload, headers=headers)
        res.raise_for_status()
        data = res.json()
        return data.get('choices', [{}])[0].get('message', {}).get('content', '')


async def openai_chat(messages: List[Dict[str, str]], model: str | None = None) -> str:
    from shared.config import settings
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY not configured')
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(model=model or settings.openai_model, messages=messages)
    return response.choices[0].message.content or ''


async def gemini_chat(messages: List[Dict[str, str]], model: str | None = None) -> str:
    from shared.config import settings
    if not settings.google_api_key:
        raise RuntimeError('GEMINI_API_KEY not configured')
    from google import genai
    client = genai.Client(api_key=settings.google_api_key)
    prompt = '\n'.join([f"{m['role']}: {m['content']}" for m in messages])
    response = client.models.generate_content(model=model or settings.gemini_model, contents=prompt)
    return getattr(response, 'text', '') or ''


def fallback_answer(message: str, context: Dict[str, Any] | None = None) -> str:
    low = message.lower()
    if 'llama' in low or 'model' in low:
        return 'Model selection is now enabled. Open the Model panel, press Refresh Models, and choose any installed Ollama model such as llama3.2:latest.'
    if 'enterprise' in low and any(x in low for x in ['overview', 'status', 'factory', 'dashboard']):
        return 'Enterprise control stays disabled until you explicitly pair Omni with Enterprise from the Pairing panel. After pairing, I can read dashboard status and trigger approved Enterprise actions.'
    if any(x in low for x in ['coa', 'qc', 'certificate']):
        return 'QC workflow: create/edit a COA template, enter batch results, validate required parameters, then export the certificate for human review and approval.'
    if any(x in low for x in ['r&d', 'experiment', 'reaction', 'formulation', 'chemistry']):
        return 'R&D workflow: define objective, hypothesis, variables, control batch, acceptance criteria, safety notes, observations, and next-trial plan. I can generate structured experiments and suggestions.'
    return 'I am SHIMS Omni running in local fallback mode. Start Ollama or configure an API key for deeper AI responses. I can still generate documents, media placeholders, code, and Enterprise workflow guidance.'


async def ai_chat(message: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    context = context or {}
    selected_provider = str(context.get('provider') or settings.llm_provider or 'ollama').lower()
    selected_model = str(context.get('model') or '').strip() or None
    temperature = float(context.get('temperature') or 0.7)
    messages = [{'role': 'system', 'content': SYSTEM}]
    if context:
        safe_context = {k: v for k, v in context.items() if k not in {'provider', 'model', 'temperature'}}
        if safe_context:
            messages.append({'role': 'system', 'content': 'Context: ' + json.dumps(safe_context, default=str)[:5000]})
    messages.append({'role': 'user', 'content': message})

    all_candidates = ['ollama', 'kimi', 'anthropic', 'openai', 'deepseek', 'gemini', 'qwen', 'huggingface']
    if selected_provider in all_candidates:
        providers.append(selected_provider)
    for candidate in all_candidates:
        if candidate not in providers:
            providers.append(candidate)

    errors = []
    for provider in providers:
        try:
            if provider == 'ollama':
                return {'provider': 'ollama', 'model': selected_model or settings.ollama_model, 'content': await ollama_chat(messages, selected_model, temperature)}
            if provider == 'kimi':
                return {'provider': 'kimi', 'model': selected_model or settings.kimi_model, 'content': await kimi_chat(messages, selected_model)}
            if provider == 'anthropic':
                return {'provider': 'anthropic', 'model': selected_model or settings.anthropic_model, 'content': await anthropic_chat(messages, selected_model)}
            if provider == 'openai':
                return {'provider': 'openai', 'model': selected_model or settings.openai_model, 'content': await openai_chat(messages, selected_model)}
            if provider == 'deepseek':
                return {'provider': 'deepseek', 'model': selected_model or settings.deepseek_model, 'content': await deepseek_chat(messages, selected_model)}
            if provider == 'gemini':
                return {'provider': 'gemini', 'model': selected_model or settings.gemini_model, 'content': await gemini_chat(messages, selected_model)}
            if provider == 'qwen':
                return {'provider': 'qwen', 'model': selected_model or settings.qwen_model, 'content': await qwen_chat(messages, selected_model)}
            if provider == 'huggingface':
                return {'provider': 'huggingface', 'model': selected_model or settings.huggingface_model, 'content': await huggingface_chat(messages, selected_model)}
        except Exception as exc:
            errors.append(f'{provider}: {exc}')
    return {'provider': 'fallback', 'model': selected_model, 'content': fallback_answer(message, context), 'errors': errors[-3:]}


async def generate_code_from_task(task: str) -> str:
    prompt = f"Write a safe, self-contained Python solution for this task. Return only Python code, no markdown. Task: {task}"
    result = await ai_chat(prompt)
    text = result.get('content', '')
    if '```' in text:
        text = text.split('```')[-2]
        if text.strip().startswith('python'):
            text = text.strip()[6:]
    if not text.strip():
        text = f'def solve():\n    return "Task: {task}"\n\nif __name__ == "__main__":\n    print(solve())\n'
    return text.strip() + '\n'
