#!/usr/bin/env python3
import http.server
import socketserver
import os
import sys
import functools
import json
import urllib.parse

PROJECTS = {
    'demo': '/opt/nft-dashboard/DemoWeb',
    'docs': '/opt/nft-dashboard/AegisDocs',
}

class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urllib.parse.urlparse(path)
        clean_path = parsed.path
        if clean_path != '/' and not os.path.splitext(clean_path)[1]:
            candidate = os.path.join(self.directory, clean_path.lstrip('/') + '.html')
            if os.path.isfile(candidate):
                path = clean_path + '.html'
        return super().translate_path(path)

    def _json(self, payload):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.startswith(('/api/', '/dns/', '/dhcp/', '/vpn/', '/rules/', '/firewall/', '/network/', '/qos/', '/allowlist/', '/policy/', '/export/')):
            self._json({'ok': True, 'message': 'Demo mode: action simulated', 'imported': 42, 'total': 3842, 'cleared': 42})
            return
        super().do_GET()

    def do_GET(self):
        if self.path.startswith('/api/dns/queries'):
            self._json([
                {'id': 1, 'ts': 1716238400, 'domain': 'telemetry.example.com', 'client_ip': '192.168.10.24', 'qtype': 'A', 'action': 'block', 'reason': 'tracking', 'response': '0.0.0.0', 'upstream': '-', 'latency_ms': 1},
                {'id': 2, 'ts': 1716238460, 'domain': 'updates.example.net', 'client_ip': '192.168.10.11', 'qtype': 'AAAA', 'action': 'forwarded', 'reason': 'allowed', 'response': '203.0.113.10', 'upstream': '1.1.1.1', 'latency_ms': 12},
            ])
            return
        if self.path.startswith('/api/'):
            self._json({'ok': True, 'demo': True, 'message': 'Demo API response'})
            return
        super().do_GET()

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        if self.path.endswith('.html') or self.path.endswith('/'):
            self.send_header('Content-Type', 'text/html; charset=utf-8')
        super().end_headers()

    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    if any(arg in ('-h', '--help') for arg in sys.argv):
        print('Usage:')
        print('  python3 /opt/nft-dashboard/serve-demo.py docs 8080')
        print('  python3 /opt/nft-dashboard/serve-demo.py demo 8090')
        print('  python3 /opt/nft-dashboard/serve-demo.py 8080        # demo by default')
        sys.exit(0)

    project = 'demo'
    port = 8080
    args = sys.argv[1:]

    if args:
        if args[0] in PROJECTS:
            project = args[0]
            if len(args) > 1:
                port = int(args[1])
        else:
            port = int(args[0])

    directory = PROJECTS[project]
    os.chdir(directory)
    handler = functools.partial(Handler, directory=directory)
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Serving {project} from {directory} on http://0.0.0.0:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped")
