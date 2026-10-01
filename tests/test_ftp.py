import logging
import os
import socket
import tempfile
import threading
import unittest

from Ftp import Ftp, FtpUnavailable

try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
except ImportError:
    FTPServer = None
else:
    logging.getLogger("pyftpdlib").setLevel(logging.ERROR)

FILE_NAME = "2026-08-26T09-54-58&Prata&A"


# Testa a classe Ftp contra um servidor FTP real, local (requer: pip install pyftpdlib)
@unittest.skipIf(FTPServer is None, "pyftpdlib não instalado")
class FtpTest(unittest.TestCase):
    # Comandos que o servidor de teste não aceita (para simular servidores sem MLSD)
    disabled_commands = ()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = self._tmp.name
        os.makedirs(os.path.join(self.home, "lpr", "QXA4C30"))
        os.makedirs(os.path.join(self.home, "lpr", "VAZIA"))
        with open(os.path.join(self.home, "lpr", "QXA4C30", FILE_NAME + ".vehicleBody.jpg"), "wb") as file:
            file.write(b"x" * 123)

        authorizer = DummyAuthorizer()
        authorizer.add_user("lpr", "senha", self.home, perm="elradfmwMT")
        handler = type("Handler", (FTPHandler,), {})
        handler.authorizer = authorizer
        handler.proto_cmds = {
            cmd: props for cmd, props in FTPHandler.proto_cmds.items() if cmd not in self.disabled_commands
        }
        self.server = FTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.close_all)

        self.ftp = self._client()
        self.addCleanup(self.ftp.quit)

    def _client(self, password="senha"):
        ftp = Ftp()
        ftp.host, ftp.port = self.server.address[:2]
        ftp.user, ftp.password = "lpr", password
        ftp.path = "lpr"
        ftp.timeout = 5
        return ftp

    def test_list_root(self):
        entries = {entry.name: entry for entry in self.ftp.list_dir("")}
        self.assertEqual(set(entries), {"QXA4C30", "VAZIA"})
        if not self.disabled_commands:
            self.assertTrue(entries["QXA4C30"].is_dir)
            self.assertIsNotNone(entries["QXA4C30"].mtime)

    def test_list_folder(self):
        entries = self.ftp.list_dir("QXA4C30")
        self.assertEqual([entry.name for entry in entries], [FILE_NAME + ".vehicleBody.jpg"])
        if not self.disabled_commands:
            self.assertFalse(entries[0].is_dir)
            self.assertEqual(entries[0].size, 123)
        self.assertEqual(self.ftp.list_dir("VAZIA"), [])
        self.assertIsNone(self.ftp.list_dir("NAOEXISTE"))
        self.assertIsNone(self.ftp.list_dir(f"QXA4C30/{FILE_NAME}.vehicleBody.jpg"))

    def test_size(self):
        self.ftp.list_dir("QXA4C30")
        self.assertEqual(self.ftp.size(f"QXA4C30/{FILE_NAME}.vehicleBody.jpg"), 123)
        self.assertIsNone(self.ftp.size("QXA4C30/naoexiste.jpg"))

    def test_rename_delete_and_rmdir(self):
        pending = f"QXA4C30/{FILE_NAME}.vehicleBody.jpg"
        final = f"QXA4C30/{FILE_NAME}.jpg"
        self.assertTrue(self.ftp.rename(pending, final))
        self.assertTrue(os.path.exists(os.path.join(self.home, "lpr", "QXA4C30", FILE_NAME + ".jpg")))
        self.assertFalse(self.ftp.rename(pending, final))

        # Pasta com arquivo não é removida
        self.ftp.list_dir("QXA4C30")
        self.assertFalse(self.ftp.rmdir("QXA4C30"))
        self.assertTrue(self.ftp.delete(final))
        self.assertFalse(self.ftp.delete(final))
        self.assertTrue(self.ftp.rmdir("QXA4C30"))
        self.assertFalse(os.path.exists(os.path.join(self.home, "lpr", "QXA4C30")))

    def test_reconnects_after_connection_is_lost(self):
        self.ftp.list_dir("")
        self.ftp._ftp.sock.shutdown(socket.SHUT_RDWR)
        with self.assertRaises(FtpUnavailable):
            self.ftp.list_dir("")
        self.assertEqual(len(self.ftp.list_dir("")), 2)

    def test_wrong_password_is_unavailable(self):
        ftp = self._client(password="errada")
        with self.assertRaises(FtpUnavailable):
            ftp.list_dir("")


class FtpWithoutMlsdTest(FtpTest):
    disabled_commands = ("MLSD", "MLST")


if __name__ == "__main__":
    unittest.main()
