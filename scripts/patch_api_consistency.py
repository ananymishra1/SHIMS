import re
from pathlib import Path

FILE = Path('shims_enterprise/app.py')
lines = FILE.read_text(encoding='utf-8').splitlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    m = re.match(r"^(\s+)return RedirectResponse\('([^']+)', status_code=(\d+)\)\s*$", line)
    if m:
        indent, url, code = m.groups()
        # Search backward for the most recent `var = db.execute(` before this return
        j = i - 1
        last_var = None
        while j >= 0:
            prev = lines[j]
            # Stop if we hit another return or a new function decorator/definition
            if prev.strip().startswith('return ') or prev.strip().startswith('@') or prev.strip().startswith('async def ') or prev.strip().startswith('def '):
                break
            vm = re.search(r"\b(\w+)\s*=\s*db\.execute\(", prev)
            if vm:
                last_var = vm.group(1)
                break
            j -= 1
        if last_var:
            new_lines.append(f"{indent}return _api_response(request, {{'ok': True, 'id': {last_var}}}, '{url}', {code})")
        else:
            new_lines.append(line)
    else:
        new_lines.append(line)
    i += 1

FILE.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
print('Patched', FILE)
