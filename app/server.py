import bootstrap
import argparse
import contextlib
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from engine import Engine, atomic_json
from device import json_value
from tools import TOOLS, validate_arguments

ROOT = bootstrap.ROOT
DATA = ROOT / 'data'
VERSION = '1.0.0'
PROTOCOLS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05']


def rpc(engine, message):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return {'jsonrpc': '2.0', 'id': message.get('id') if isinstance(message, dict) else None, 'error': {'code': -32600, 'message': 'Invalid request'}}
    if 'id' not in message:
        return None
    identity = message['id']
    method = message['method']
    params = message.get('params', {})
    response = {'jsonrpc': '2.0', 'id': identity}
    if not isinstance(params, dict):
        response['error'] = {'code': -32602, 'message': 'Parameters must be an object'}
        return response
    if method == 'initialize':
        requested = params.get('protocolVersion')
        response['result'] = {'protocolVersion': requested if requested in PROTOCOLS else PROTOCOLS[0],
                              'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'iphone-organizer', 'version': VERSION}}
    elif method == 'ping':
        response['result'] = {}
    elif method == 'tools/list':
        response['result'] = {'tools': TOOLS}
    elif method == 'tools/call':
        try:
            if not isinstance(params, dict):
                raise ValueError('Invalid tool parameters.')
            name, args = params['name'], params.get('arguments', {})
            validate_arguments(name, args)
            result = json_value(engine.call(name, args))
            response['result'] = {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}], 'structuredContent': result, 'isError': False}
        except Exception as e:
            response['result'] = {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
    else:
        response['error'] = {'code': -32601, 'message': 'Method not found'}
    return response


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # URLs and authorization values never enter logs.

    def reply(self, code, value=None, content_type='application/json'):
        data = b'' if value is None else value if isinstance(value, bytes) else json.dumps(json_value(value), ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        if data:
            self.wfile.write(data)

    def permitted(self, authenticated=True):
        host = self.headers.get('Host')
        if host != f'127.0.0.1:{self.server.server_port}':
            self.reply(403, {'error': 'Invalid host.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            self.reply(403, {'error': 'Invalid origin.'})
            return False
        if authenticated and not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + self.server.token):
            self.reply(401, {'error': 'Open the organizer through its launcher to authenticate.'})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.permitted(authenticated=path not in ('/', '/app.js', '/style.css')):
            return
        if path == '/health':
            self.reply(200, {'ready': True, 'version': VERSION})
        elif path == '/config':
            self.reply(200, {'mcpServers': {'iphone-organizer': {'command': str(ROOT / 'runtime/python.exe'),
                'args': [str(ROOT / 'app/server.py'), '--stdio']}}})
        elif path == '/mcp':
            self.reply(405, {'error': 'This server returns JSON to POST requests; it does not offer an SSE stream.'})
        elif path in ('/', '/app.js', '/style.css'):
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
            kind = {'/': 'text/html;charset=utf-8', '/app.js': 'text/javascript;charset=utf-8', '/style.css': 'text/css;charset=utf-8'}[path]
            self.reply(200, (ROOT / 'ui' / name).read_bytes(), kind)
        else:
            self.reply(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.permitted():
            return
        path = urlsplit(self.path).path
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 2_000_000:
                self.reply(413, {'error': 'Invalid or oversized request.'})
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.reply(415, {'error': 'Use application/json.'})
                return
            message = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            self.reply(400, {'error': 'Invalid JSON.'})
            return
        if path == '/mcp':
            version = self.headers.get('MCP-Protocol-Version')
            if version and version not in PROTOCOLS:
                self.reply(400, {'error': 'Unsupported protocol version.'})
                return
            try:
                result = rpc(self.server.engine, message)
                self.reply(202 if result is None else 200, result)
            except Exception:
                self.reply(400, {'error': 'Invalid request.'})
        elif path == '/api':
            try:
                if not isinstance(message, dict):
                    raise ValueError('Invalid request.')
                name, args = message['name'], message.get('arguments', {})
                validate_arguments(name, args)
                self.reply(200, self.server.engine.call(name, args))
            except Exception as e:
                self.reply(400, {'error': str(e)})
        elif path == '/shutdown':
            self.reply(200, {'stopping': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.reply(404, {'error': 'Not found.'})


def request(runtime, endpoint, body=None):
    headers = {'Authorization': 'Bearer ' + runtime['token'], 'Content-Type': 'application/json',
               'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': PROTOCOLS[0]}
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(runtime['url'] + endpoint, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=900) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


def existing():
    try:
        info = json.loads((DATA / 'runtime.json').read_text())
        if not info['url'].startswith('http://127.0.0.1:') or urlsplit(info['url']).path:
            return None
        headers = {'Authorization': 'Bearer ' + info['token']}
        with urllib.request.urlopen(urllib.request.Request(info['url'] + '/health', headers=headers), timeout=1) as r:
            if json.load(r).get('ready'):
                return info
    except Exception:
        pass
    return None


def ensure_server():
    info = existing()
    if info:
        return info
    DATA.mkdir(exist_ok=True)
    log = (DATA / 'server.log').open('ab')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
    log.close()
    for _ in range(100):
        time.sleep(.1)
        info = existing()
        if info:
            return info
    raise RuntimeError('The organizer could not start. Check data/server.log.')


def serve():
    DATA.mkdir(exist_ok=True)
    lock_file = (DATA / 'server.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        lock_file.seek(0)
        if lock_file.read(1) == b'':
            lock_file.write(b'0')
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.token = secrets.token_urlsafe(32)
    server.origin = f'http://127.0.0.1:{server.server_port}'
    server.engine = Engine(DATA)
    info = {'url': server.origin, 'token': server.token, 'pid': os.getpid(), 'version': VERSION}
    atomic_json(DATA / 'runtime.json', info)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        with contextlib.suppress(OSError):
            (DATA / 'runtime.json').unlink()
        lock_file.close()


def stdio():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    info = ensure_server()
    for line in sys.stdin:
        try:
            message = json.loads(line)
            result = request(info, '/mcp', message)
        except Exception as e:
            result = {'jsonrpc': '2.0', 'id': message.get('id') if 'message' in locals() and isinstance(message, dict) else None,
                      'error': {'code': -32603, 'message': str(e)}}
        if result is not None:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--stdio', action='store_true')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    try:
        if args.serve:
            serve()
        elif args.stdio:
            stdio()
        elif args.stop:
            info = existing()
            if info:
                request(info, '/shutdown', {})
        else:
            info = ensure_server()
            webbrowser.open(info['url'] + '/#' + info['token'])
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

