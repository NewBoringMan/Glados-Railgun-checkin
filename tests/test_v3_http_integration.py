import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import v3_runtime as v

class Handler(BaseHTTPRequestHandler):
    post_count=0
    get_count=0
    def log_message(self, *args): pass
    def _write(self,status,payload,headers=None):
        body=json.dumps(payload).encode(); self.send_response(status)
        for k,val in (headers or {}).items(): self.send_header(k,val)
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        Handler.get_count += 1
        if self.path.endswith('/api/user/status'): self._write(200,{'data':{'leftDays':123,'vip':11}})
        elif self.path.endswith('/api/user/points'): self._write(200,{'points':500,'plans':{'plan500':{'points':500,'days':100}},'history':[]})
        else: self._write(404,{})
    def do_POST(self):
        Handler.post_count += 1
        if self.path.endswith('/api/user/checkin'): self._write(500,{'message':'transient'})
        else: self._write(404,{})

class HTTPIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_address[1]}'
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close()
    def setUp(self): Handler.post_count=0; Handler.get_count=0
    def test_real_http_gets_parse_and_post_is_not_retried(self):
        c=v.Client('glados.cloud','secret'); c.base=self.base
        self.assertEqual(v._int(c.status()['leftDays']),123)
        self.assertEqual(v._int(c.points()['points']),500)
        with self.assertRaises(v.NetworkError): c.checkin()
        self.assertEqual(Handler.post_count,1)
        c.close()

if __name__=='__main__': unittest.main()
