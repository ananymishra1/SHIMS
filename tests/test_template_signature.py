from pathlib import Path


def test_template_response_uses_current_fastapi_signature():
    text = Path('shims_omni/app.py').read_text(encoding='utf-8')
    assert "TemplateResponse(request, 'index.html', {" in text
    assert "TemplateResponse('index.html', {'request': request" not in text
