import http.client
import os
import tempfile
import threading
import unittest

from test_server import seed

from batchall import server


def get(port, host_header, connect_to="127.0.0.1"):
    conn = http.client.HTTPConnection(connect_to, port, timeout=5)
    conn.request("GET", "/api/meta", headers={"Host": host_header})
    status = conn.getresponse().status
    conn.close()
    return status


class HostCheckTests(unittest.TestCase):
    def start(self, host):
        httpd = server.make_server(port=0, host=host, db_path=self.db_path, art_dir=self.tmp.name)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "test.db")
        seed(self.db_path)

    def test_loopback_server_still_rejects_other_addresses(self):
        httpd = self.start("127.0.0.1")
        port = httpd.server_address[1]
        self.assertEqual(get(port, f"127.0.0.1:{port}"), 200)
        self.assertEqual(get(port, f"localhost:{port}"), 200)
        self.assertEqual(get(port, f"172.16.29.185:{port}"), 403)

    def test_server_started_on_an_address_accepts_that_address(self):
        # 127.0.0.2 stands in for a LAN address: it is not in the loopback list but this machine owns it.
        httpd = self.start("127.0.0.2")
        port = httpd.server_address[1]
        self.assertEqual(get(port, f"127.0.0.2:{port}", connect_to="127.0.0.2"), 200)
        self.assertEqual(get(port, "127.0.0.2", connect_to="127.0.0.2"), 200)

    def test_only_that_address_and_localhost_are_accepted(self):
        httpd = self.start("127.0.0.2")
        port = httpd.server_address[1]
        self.assertEqual(get(port, f"localhost:{port}", connect_to="127.0.0.2"), 200)
        self.assertEqual(get(port, f"10.1.2.3:{port}", connect_to="127.0.0.2"), 403)
        self.assertEqual(get(port, f"evil.example.com:{port}", connect_to="127.0.0.2"), 403)

    def test_wildcard_bind_names_no_address_so_only_localhost_is_accepted(self):
        httpd = self.start("0.0.0.0")
        port = httpd.server_address[1]
        self.assertEqual(get(port, f"localhost:{port}"), 200)
        self.assertEqual(get(port, f"192.168.1.5:{port}"), 403)


if __name__ == "__main__":
    unittest.main()
