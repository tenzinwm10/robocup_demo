"""Bounded, asynchronous NDJSON logs; logging never delays a robot callback."""
import json
from pathlib import Path
import queue
import threading
import time

class EventLog:
    def __init__(self, path, max_bytes=20*1024*1024):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes; self.dropped = 0
        self.queue = queue.Queue(maxsize=2048)
        self.thread = threading.Thread(target=self.write, daemon=True); self.thread.start()

    def emit(self, event):
        try: self.queue.put_nowait(dict(event, wall_time=time.time()))
        except queue.Full: self.dropped += 1

    def write(self):
        output = self.path.open('a', buffering=1)
        try:
            while True:
                event = self.queue.get()
                if event is None: break
                if output.tell() >= self.max_bytes:
                    output.close()
                    oldest = self.path.with_suffix(self.path.suffix+'.2')
                    previous = self.path.with_suffix(self.path.suffix+'.1')
                    if oldest.exists(): oldest.unlink()
                    if previous.exists(): previous.replace(oldest)
                    self.path.replace(previous); output = self.path.open('a', buffering=1)
                output.write(json.dumps(event, allow_nan=False)+'\n')
        finally: output.close()

    def close(self):
        try: self.queue.put(None, timeout=2)
        except queue.Full: return
        self.thread.join(timeout=2)
