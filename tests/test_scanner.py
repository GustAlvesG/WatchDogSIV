import unittest
from datetime import datetime, timedelta

from Database import DatabaseUnavailable
from Ftp import Entry, FtpUnavailable
from Scanner import Scanner

FILE_NAME = "2026-08-26T09-54-58&Prata&A"
PENDING = f"QXA4C30/{FILE_NAME}.vehicleBody.jpg"
FINAL = f"QXA4C30/{FILE_NAME}.jpg"


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


# FTP falso em memória: {pasta: {arquivo: tamanho}}. Com `mlsd=False` simula um servidor
# que só devolve os nomes na listagem.
class FakeFtp():

    def __init__(self, mlsd=True):
        self.mlsd = mlsd
        self.dirs = {}
        self.root_files = {}
        self.mtimes = {}
        self.unavailable = False
        self.rename_ok = True
        self.listed = []

    def add(self, rel, size=100):
        plate, name = rel.split("/")
        self.dirs.setdefault(plate, {})[name] = size
        self._touch(plate)

    def _touch(self, plate):
        self.mtimes[plate] = self.mtimes.get(plate, 0) + 1

    def exists(self, rel):
        plate, name = rel.split("/")
        return name in self.dirs.get(plate, {})

    def _check(self):
        if self.unavailable:
            raise FtpUnavailable("ftp fora do ar")

    def _entry(self, name, is_dir, size, mtime):
        if not self.mlsd:
            return Entry(name, None, None, None)
        return Entry(name, is_dir, size, mtime)

    def list_dir(self, rel=""):
        self._check()
        self.listed.append(rel)
        if rel == "":
            entries = [self._entry(plate, True, None, self.mtimes[plate]) for plate in self.dirs]
            return entries + [self._entry(name, False, size, None) for name, size in self.root_files.items()]
        if rel not in self.dirs:
            return None
        return [self._entry(name, False, size, None) for name, size in self.dirs[rel].items()]

    def size(self, rel):
        self._check()
        plate, name = rel.split("/")
        return self.dirs[plate][name]

    def rename(self, rel_from, rel_to):
        self._check()
        if not self.rename_ok:
            return False
        plate, name = rel_from.split("/")
        self.dirs[plate][rel_to.split("/")[1]] = self.dirs[plate].pop(name)
        self._touch(plate)
        return True

    def delete(self, rel):
        self._check()
        if "/" in rel:
            plate, name = rel.split("/")
            del self.dirs[plate][name]
            self._touch(plate)
        else:
            del self.root_files[rel]
        return True

    def rmdir(self, rel):
        self._check()
        if self.dirs[rel]:
            return False
        del self.dirs[rel]
        return True


class ScannerTest(unittest.TestCase):
    mlsd = True

    def setUp(self):
        self.ftp = FakeFtp(mlsd=self.mlsd)
        self.db = FakeDatabase()
        self.scanner = Scanner(self.ftp, self.db)

    def test_inserts_and_renames_pending_file(self):
        self.ftp.add(PENDING)

        # 1ª varredura só registra o tamanho; na 2ª, com o tamanho estável, processa
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(self.db.inserts, [])
        self.assertEqual(self.scanner.scan(), 1)

        self.assertFalse(self.ftp.exists(PENDING))
        self.assertTrue(self.ftp.exists(FINAL))
        self.assertEqual(self.db.inserts, [("QXA4C30", "Prata", "2026-08-26T09-54-58", FINAL, "A")])

        # O arquivo já finalizado não é inserido de novo
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(len(self.db.inserts), 1)

    def test_waits_while_file_is_still_growing(self):
        self.ftp.add(PENDING, size=10)
        self.assertEqual(self.scanner.scan(), 0)
        self.ftp.dirs["QXA4C30"][PENDING.split("/")[1]] = 50
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(self.db.inserts, [])

        self.assertEqual(self.scanner.scan(), 1)
        self.assertEqual(len(self.db.inserts), 1)

    def test_retries_when_database_is_unavailable(self):
        self.ftp.add(PENDING)
        self.db.unavailable = True
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(self.scanner.scan(), 0)
        self.assertTrue(self.ftp.exists(PENDING))

        self.db.unavailable = False
        self.assertEqual(self.scanner.scan(), 1)
        self.assertTrue(self.ftp.exists(FINAL))
        self.assertEqual(len(self.db.inserts), 1)

    def test_retries_when_ftp_is_unavailable(self):
        self.ftp.add(PENDING)
        self.ftp.unavailable = True
        self.assertEqual(self.scanner.scan(), 0)

        self.ftp.unavailable = False
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(self.scanner.scan(), 1)
        self.assertEqual(len(self.db.inserts), 1)

    def test_does_not_insert_twice_when_rename_fails(self):
        self.ftp.add(PENDING)
        self.ftp.rename_ok = False
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(self.scanner.scan(), 0)
        self.assertEqual(len(self.db.inserts), 1)

        self.ftp.rename_ok = True
        self.assertEqual(self.scanner.scan(), 1)
        self.assertTrue(self.ftp.exists(FINAL))
        self.assertEqual(len(self.db.inserts), 1)

    def test_rejected_file_is_tried_only_once(self):
        self.ftp.add("QXA4C30/sem-cor-nem-portao.vehicleBody.jpg")
        self.ftp.add("ABC1D23/" + FILE_NAME + ".vehicleBody.jpg")
        self.db.result = False
        calls = []
        execute = self.db.execute
        self.db.execute = lambda *args, **kwargs: calls.append(args) or execute(*args, **kwargs)

        for _ in range(4):
            self.assertEqual(self.scanner.scan(), 0)

        self.assertEqual(len(calls), 1)
        self.assertTrue(self.ftp.exists("QXA4C30/sem-cor-nem-portao.vehicleBody.jpg"))

    def test_cleanup_removes_only_old_images(self):
        old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%dT%H-%M-%S")
        new = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H-%M-%S")
        self.ftp.add(f"AAA1A11/{old}&Prata&A.jpg")
        self.ftp.add(f"AAA1A11/{new}&Prata&A.jpg")
        self.ftp.add(f"AAA1A11/{old}&Prata&A.txt")

        self.assertTrue(self.scanner.cleanup(15))

        self.assertFalse(self.ftp.exists(f"AAA1A11/{old}&Prata&A.jpg"))
        self.assertTrue(self.ftp.exists(f"AAA1A11/{new}&Prata&A.jpg"))
        self.assertTrue(self.ftp.exists(f"AAA1A11/{old}&Prata&A.txt"))

    def test_cleanup_removes_folder_only_when_empty_twice(self):
        old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%dT%H-%M-%S")
        self.ftp.add(f"BBB2B22/{old}&Prata&A.jpg")
        self.ftp.dirs["CCC3C33"] = {}
        self.ftp.mtimes["CCC3C33"] = 1

        # 1ª limpeza: apaga a imagem, mas as pastas vazias ficam
        self.assertTrue(self.scanner.cleanup(15))
        self.assertEqual(self.ftp.dirs, {"BBB2B22": {}, "CCC3C33": {}})

        # Uma das pastas recebe uma foto antes da 2ª limpeza: só a outra é removida
        self.ftp.add("CCC3C33/" + FILE_NAME.replace("2026-08-26", datetime.now().strftime("%Y-%m-%d")) + ".jpg")
        self.assertTrue(self.scanner.cleanup(15))
        self.assertEqual(list(self.ftp.dirs), ["CCC3C33"])

    def test_cleanup_reports_ftp_unavailable(self):
        self.ftp.unavailable = True
        self.assertFalse(self.scanner.cleanup(15))


# Mesmos testes contra um servidor que só devolve nomes na listagem (sem MLSD)
class ScannerWithoutMlsdTest(ScannerTest):
    mlsd = False


class ScannerDirSkipTest(unittest.TestCase):

    def test_only_changed_folders_are_listed_between_full_scans(self):
        ftp = FakeFtp()
        scanner = Scanner(ftp, FakeDatabase())
        ftp.add("AAA1A11/" + FILE_NAME + ".jpg")
        ftp.add("BBB2B22/" + FILE_NAME + ".jpg")
        scanner.scan()

        ftp.listed.clear()
        scanner.scan()
        self.assertEqual(ftp.listed, [""])

        ftp.add("BBB2B22/" + FILE_NAME + ".vehicleBody.jpg")
        ftp.listed.clear()
        scanner.scan()
        self.assertEqual(ftp.listed, ["", "BBB2B22"])
        self.assertEqual(scanner.scan(), 1)


if __name__ == "__main__":
    unittest.main()
