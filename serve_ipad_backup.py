"""Run on Windows PC, visit http://PC_IP:8000 on iPad over same LAN."""
import argparse
import json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from threading import Lock
from common import PATTERNS

lock = Lock()
state = {'index': -1, 'ack_index': -999, 'browser': {}}
HTML = (Path(__file__).parent / 'ipad.html').read_bytes()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ('/', '/ipad.html'):
            self.reply(200, HTML, 'text/html; charset=utf-8'); return
        if u.path == '/api/state':
            with lock:
                i = state['index']
            self.reply(200, json.dumps({'index': i, 'pattern': PATTERNS[i] if i >= 0 else None}).encode(), 'application/json'); return
        if u.path == '/api/status':
            with lock:
                s = dict(state)
            self.reply(200, json.dumps(s).encode(), 'application/json'); return
        if u.path == '/api/set':
            try:
                i = int(parse_qs(u.query).get('index', ['-1'])[0]); assert -1 <= i < len(PATTERNS)
            except (ValueError, AssertionError):
                self.reply(400, b'bad index'); return
            with lock:
                state['index'] = i
                state['ack_index'] = -999
            self.reply(200, json.dumps({'index': i, 'name': PATTERNS[i]['name'] if i >= 0 else 'idle'}).encode(), 'application/json'); return
        self.reply(404, b'not found')

    def do_POST(self):
        if self.path != '/api/ack':
            self.reply(404, b'not found'); return
        try:
            n = int(self.headers.get('Content-Length', 0))
            d = json.loads(self.rfile.read(n))
            with lock:
                if d['index'] == state['index']:
                    state['ack_index'] = d['index']
                    state['browser'] = {k: d[k] for k in ('width','height','dpr','viewport_w','viewport_h') if k in d}
            self.reply(200, b'ok'); return
        except Exception as e:
            self.reply(400, str(e).encode()); return

    def reply(self, code, data, typ='text/plain; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', typ)
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/state'):
            super().log_message(fmt, *args)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8000)
    args = ap.parse_args()
    print('Server listening on 0.0.0.0:%d; find Windows IPv4 address using ipconfig' % args.port)
    print('Open on iPad: http://<Windows_LAN_IP>:%d' % args.port)
    ThreadingHTTPServer(('0.0.0.0', args.port), Handler).serve_forever()
