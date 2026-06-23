import requests, json
r = requests.post('http://127.0.0.1:8010/ollama/pull', json={'model':'chemdfm','stream':True}, stream=True, timeout=20)
for line in r.iter_lines():
    if line:
        print(line.decode())
