import os
import tempfile
import time
import unittest
from unittest import mock

from Database import DatabaseUnavailable
from Scanner import Scanner
from Utils import Utils

FILE_NAME = "2026-08-26T09-54-58&Prata&A"


# Banco falso: registra os inserts e permite simular falhas
class FakeDatabase():

    def __init__(self):
        self.inserts = []
        self.unavailable = False
        self.result = True

    def execute(self, sql, type, params=None):
        if self.unavailable:
            raise DatabaseUnavailable("banco fora do ar")
        if self.result:
            self.inserts.append(params)
        return self.result


class ScannerTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name
        self.db = FakeDatabase()
        self.scanner = Scanner(self.folder, db=self.db, settle_seconds=3)

    def _create(self, plate, name, age_seconds=60):
        path = os.path.join(self.folder, plate, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as file:
            file.write("x")
        ts = time.time() - age_seconds
        os.utime(path, (ts, ts))
        return path

    def test_inserts_and_renames_pending_file(self):
        pending = self._create("QXA4C30", FILE_NAME + ".vehicleBody.jpg")
        final = os.path.join(self.folder, "QXA4C30", FILE_NAME + ".jpg")

        self.assertEqual(self.scanner.scan(), 1)

        self.assertFalse(os.path.exists(pending))
        self.assertTrue(os.path.exists(final))
        self.assertEqual(
            self.db.inserts, [("QXA4C30", "Prata", "2026-08-26T09-54-58", f"QXA4C30/{FILE_NAME}.jpg", "A")]
        )

        # O arquivo já finalizado não é inserido de novo
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(len(self.db.inserts), 1)

    def test_ignores_file_still_being_uploaded(self):
        pending = self._create("QXA4C30", FILE_NAME + ".vehicleBody.jpg", age_seconds=0)

        self.assertEqual(self.scanner.scan(), 0)

        self.assertTrue(os.path.exists(pending))
        self.assertEqual(self.db.inserts, [])

    def test_retries_when_database_is_unavailable(self):
        pending = self._create("QXA4C30", FILE_NAME + ".vehicleBody.jpg")

        self.db.unavailable = True
        self.assertEqual(self.scanner.scan(), 0)
        self.assertTrue(os.path.exists(pending))

        self.db.unavailable = False
        self.assertEqual(self.scanner.scan(), 1)
        self.assertFalse(os.path.exists(pending))
        self.assertEqual(len(self.db.inserts), 1)

    def test_does_not_insert_twice_when_rename_fails(self):
        pending = self._create("QXA4C30", FILE_NAME + ".vehicleBody.jpg")

        with mock.patch.object(Utils, "rename_file", return_value=False):
            self.assertEqual(self.scanner.scan(), 0)
        self.assertTrue(os.path.exists(pending))
        self.assertEqual(len(self.db.inserts), 1)

        self.assertEqual(self.scanner.scan(), 1)
        self.assertFalse(os.path.exists(pending))
        self.assertEqual(len(self.db.inserts), 1)

    def test_rejected_file_is_tried_only_once(self):
        bad_name = self._create("QXA4C30", "sem-cor-nem-portao.vehicleBody.jpg")
        refused = self._create("ABC1D23", FILE_NAME + ".vehicleBody.jpg")
        self.db.result = False

        with mock.patch.object(self.db, "execute", wraps=self.db.execute) as execute:
            self.assertEqual(self.scanner.scan(), 0)
            self.assertEqual(self.scanner.scan(), 0)
            self.assertEqual(execute.call_count, 1)

        self.assertTrue(os.path.exists(bad_name))
        self.assertTrue(os.path.exists(refused))


if __name__ == "__main__":
    unittest.main()
