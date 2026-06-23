import asyncio, json, websockets

async def main():
    async with websockets.connect('ws://127.0.0.1:8010/ws/enterprise') as ws:
        await ws.send(json.dumps({"action": "ping"}))
        msg = json.loads(await ws.recv())
        print('received:', msg)

asyncio.run(main())
