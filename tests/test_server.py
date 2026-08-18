import tempfile
import unittest
from pathlib import Path

from server import Database, seed_payload_qirui


class DatabaseContractTests(unittest.TestCase):
    def test_projects_are_seeded_and_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "demo.sqlite3")
            projects = database.projects()
            self.assertEqual({item["id"] for item in projects}, {"qirui", "demo-sales"})
            self.assertEqual(database.overview("qirui")["call_count"], 1)
            self.assertEqual(database.overview("demo-sales")["call_count"], 1)

    def test_postcall_contract_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "demo.sqlite3")
            payload = seed_payload_qirui()
            payload["unique_id"] = "test-call"
            first = database.ingest("qirui", payload)
            second = database.ingest("qirui", payload)
            self.assertFalse(first["deduplicated"])
            self.assertTrue(second["deduplicated"])
            detail = database.call_detail("qirui", "test-call")
            self.assertEqual(len(detail["quality_checks"]), 4)
            self.assertEqual(len(detail["redlines"]), 2)
            self.assertEqual(detail["badcases"][0]["category"], "workflow_execution")
            self.assertIsNone(database.call_detail("demo-sales", "test-call"))


if __name__ == "__main__":
    unittest.main()
