"""Send a documented physics command to the local Studio simulator runtime."""
import argparse
import asyncio
import json
import time
import uuid
import websockets
import sys
import msgpack
sys.path.insert(0, '/usr/local/booster_robot/booster_robocup_sim')
from transport.websocket.message_protocol import unpack_message, MessageType


async def run(command, params):
    request_id = str(uuid.uuid4())
    params['_request_id'] = request_id
    async with websockets.connect('ws://127.0.0.1:8788', max_size=64*1024*1024) as connection:
        await connection.send(json.dumps({'type': 'command', 'command': command, 'params': params}))
        if command in ('pause', 'resume', 'step', 'set_speed'):
            # These control commands have no correlated response in this runtime.
            await asyncio.sleep(0.2)
            print(f'Queued {command}; verify simulation state separately.')
            return
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(connection.recv(), max(0.1, deadline-time.monotonic()))
            except websockets.exceptions.ConnectionClosedOK:
                if command == 'switch_scene':
                    print('Scene switch accepted by transport; the physics server restarted. Verify the new scene state.')
                    return
                raise
            if isinstance(raw, str):
                message = json.loads(raw)
            else:
                kind, payload = unpack_message(raw)
                if kind != MessageType.COMMAND_RESPONSE:
                    continue
                message = msgpack.unpackb(payload, raw=False)
            if message.get('request_id') == request_id:
                print(json.dumps(message, indent=2))
                if message.get('error'):
                    raise RuntimeError(message['error'])
                return
        raise TimeoutError('Studio command response timed out')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command')
    parser.add_argument('params', help='JSON command parameters')
    args = parser.parse_args()
    asyncio.run(run(args.command, json.loads(args.params)))
