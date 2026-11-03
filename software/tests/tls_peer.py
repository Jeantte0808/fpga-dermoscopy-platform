"""Real TLS local peer. Certificate/key are public TEST ONLY fixtures, not API credentials."""
from contextlib import contextmanager
from http.server import HTTPServer,BaseHTTPRequestHandler
from pathlib import Path
import ssl,threading,json

CERT=Path(__file__).parent/'fixtures/tls/localhost-cert.pem'
KEY=Path(__file__).parent/'fixtures/tls/localhost-test-only-key.pem'

@contextmanager
def tls_peer():
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']))
            requests.append(dict(body=json.loads(body),authorization_present='Authorization' in self.headers))
            raw=b'{"ok":true}'
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=HTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(CERT,KEY)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'https://127.0.0.1:{server.server_port}/probe',requests
    finally:server.shutdown();server.server_close();thread.join(5)
