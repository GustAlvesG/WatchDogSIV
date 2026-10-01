import os
import tempfile
import time
import unittest

from Utils import Utils

FILE_NAME = "2026-08-26T09-54-58&Prata&A"


class FilterSqlCreatedFileTest(unittest.TestCase):

    def setUp(self):
        self.folder = os.path.join(tempfile.gettempdir(), "lpr")

    def test_pending_file(self):
        path = os.path.join(self.folder, "QXA4C30", FILE_NAME + ".vehicleBody.jpg")
        sql, params = Utils.filter_sql_created_file(path, self.folder)
        self.assertEqual(
            sql, "INSERT INTO parkings (plate, color, entry_date, file, gate) VALUES (%s, %s, %s, %s, %s);"
        )
        # A coluna `file` guarda o nome final, sem o sufixo .vehicleBody
        self.assertEqual(
            params, ("QXA4C30", "Prata", "2026-08-26T09-54-58", f"QXA4C30/{FILE_NAME}.jpg", "A")
        )

    def test_file_in_root_is_rejected(self):
        path = os.path.join(self.folder, FILE_NAME + ".vehicleBody.jpg")
        self.assertIsNone(Utils.filter_sql_created_file(path, self.folder))

    def test_missing_gate_is_rejected(self):
        path = os.path.join(self.folder, "QXA4C30", "2026-08-26T09-54-58&Prata.vehicleBody.jpg")
        self.assertIsNone(Utils.filter_sql_created_file(path, self.folder))

    def test_file_outside_folder_is_rejected(self):
        path = os.path.join(tempfile.gettempdir(), "outra", "QXA4C30", FILE_NAME + ".vehicleBody.jpg")
        self.assertIsNone(Utils.filter_sql_created_file(path, self.folder))


class ClearOldFilesTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name

    def _create(self, *parts, age_days=0.0):
        path = os.path.join(self.folder, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as file:
            file.write("x")
        self._age(path, age_days)
        return path

    def _age(self, path, age_days):
        ts = time.time() - age_days * 24 * 60 * 60
        os.utime(path, (ts, ts))

    def test_removes_only_old_images(self):
        old_image = self._create("AAA1A11", "antiga.jpg", age_days=20)
        new_image = self._create("AAA1A11", "nova.jpg", age_days=1)
        old_other = self._create("AAA1A11", "antigo.txt", age_days=20)

        Utils.clear_old_files(self.folder, 15)

        self.assertFalse(os.path.exists(old_image))
        self.assertTrue(os.path.exists(new_image))
        self.assertTrue(os.path.exists(old_other))

    def test_removes_old_empty_folder_but_keeps_recent_one(self):
        old_image = self._create("BBB2B22", "antiga.jpg", age_days=20)
        old_dir = os.path.dirname(old_image)
        recent_dir = os.path.join(self.folder, "CCC3C33")
        os.makedirs(recent_dir)

        # 1ª limpeza: remove a imagem; a pasta acabou de ser modificada, então fica
        Utils.clear_old_files(self.folder, 15)
        self.assertFalse(os.path.exists(old_image))
        self.assertTrue(os.path.isdir(old_dir))

        # 2ª limpeza, com a pasta vazia já antiga: agora é removida
        self._age(old_dir, 1)
        Utils.clear_old_files(self.folder, 15)
        self.assertFalse(os.path.exists(old_dir))
        self.assertTrue(os.path.isdir(recent_dir))
        self.assertTrue(os.path.isdir(self.folder))


if __name__ == "__main__":
    unittest.main()
