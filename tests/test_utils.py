import unittest

from Utils import Utils

FILE_NAME = "2026-08-26T09-54-58&Prata&A"


class FilterSqlCreatedFileTest(unittest.TestCase):

    def test_pending_file(self):
        sql, params = Utils.filter_sql_created_file(f"QXA4C30/{FILE_NAME}.vehicleBody.jpg")
        self.assertEqual(
            sql, "INSERT INTO parkings (plate, color, entry_date, file, gate) VALUES (%s, %s, %s, %s, %s);"
        )
        # A coluna `file` guarda o nome final, sem o sufixo .vehicleBody
        self.assertEqual(
            params, ("QXA4C30", "Prata", "2026-08-26T09-54-58", f"QXA4C30/{FILE_NAME}.jpg", "A")
        )

    def test_file_in_root_is_rejected(self):
        self.assertIsNone(Utils.filter_sql_created_file(f"{FILE_NAME}.vehicleBody.jpg"))

    def test_file_in_subfolder_is_rejected(self):
        self.assertIsNone(Utils.filter_sql_created_file(f"LPR/QXA4C30/{FILE_NAME}.vehicleBody.jpg"))

    def test_missing_gate_is_rejected(self):
        self.assertIsNone(Utils.filter_sql_created_file("QXA4C30/2026-08-26T09-54-58&Prata.vehicleBody.jpg"))


if __name__ == "__main__":
    unittest.main()
