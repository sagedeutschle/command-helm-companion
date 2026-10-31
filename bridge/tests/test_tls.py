import http.client
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from helm_remote.server import make_server
from helm_remote.store import Store

class TLSTests(unittest.TestCase):
    def test_idle_tcp_client_does_not_block_other_tls_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem'),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            store=Store(root/'state.sqlite')
            server=make_server(store,'test-auth',('127.0.0.1',0))
            ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(root/'cert.pem',root/'key.pem')
            server.socket=ctx.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            idle=socket.create_connection(server.server_address)
            client=http.client.HTTPSConnection('localhost',server.server_address[1],timeout=2,context=ssl.create_default_context(cafile=str(root/'cert.pem')))
            try:
                start=time.monotonic()
                client.request('GET','/v1/snapshot',headers={'Authorization':'Bearer test-auth'})
                response=client.getresponse();response.read()
                self.assertEqual(response.status,200)
                self.assertLess(time.monotonic()-start,2)
            finally:
                client.close();idle.close();server.shutdown();server.server_close();thread.join();store.close()
