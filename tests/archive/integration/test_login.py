import requests
s = requests.Session()
for pw in ['SHIMS2025!', 'TestPass123!']:
    r = s.post('http://127.0.0.1:8021/login', data={'username': 'admin', 'password': pw}, allow_redirects=False)
    print(pw, r.status_code, r.headers.get('location'), s.cookies.get_dict())
    s.cookies.clear()
