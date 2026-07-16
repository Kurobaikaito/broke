import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy.dialects.mysql import dialect

from backend.app.models import AdjFactor
from backend.app.repositories import MysqlRepository


class DriverBulkUpsertTestCase(unittest.TestCase):
    def test_values_are_driver_bound_in_one_multi_row_statement(self):
        class FakeConnection:
            def __init__(self):
                self.calls = []

            def exec_driver_sql(self, statement, parameters):
                self.calls.append((statement, parameters))

        class FakeBind:
            def __init__(self):
                self.dialect = dialect()

        class FakeSession:
            def __init__(self):
                self.bind = FakeBind()
                self.driver_connection = FakeConnection()
                self.commits = 0

            def get_bind(self):
                return self.bind

            def connection(self):
                return self.driver_connection

            def commit(self):
                self.commits += 1

        session = FakeSession()
        repository = MysqlRepository(session)
        records = [
            {
                "code": "000001",
                "trade_date": date(2024, 1, 2),
                "adj_factor": Decimal("1.25"),
            },
            {
                "code": "600000",
                "trade_date": date(2024, 1, 2),
                "adj_factor": Decimal("2.50"),
            },
        ]

        count = repository._driver_multi_upsert(
            AdjFactor,
            records,
            ("adj_factor",),
            commit=False,
        )

        self.assertEqual(count, 2)
        self.assertEqual(session.commits, 0)
        self.assertEqual(len(session.driver_connection.calls), 1)
        statement, parameters = session.driver_connection.calls[0]
        self.assertIn("AS new ON DUPLICATE KEY UPDATE", statement)
        self.assertNotIn("000001", statement)
        self.assertEqual(parameters[0], "000001")
        self.assertEqual(parameters[3], "600000")

    def test_inconsistent_record_shapes_are_rejected(self):
        class FakeBind:
            def __init__(self):
                self.dialect = dialect()

        class FakeSession:
            def get_bind(self):
                return FakeBind()

        repository = MysqlRepository(FakeSession())

        with self.assertRaisesRegex(ValueError, "consistent"):
            repository._driver_multi_upsert(
                AdjFactor,
                [
                    {"code": "000001", "trade_date": date(2024, 1, 2), "adj_factor": 1},
                    {"code": "600000", "trade_date": date(2024, 1, 2)},
                ],
                ("adj_factor",),
                commit=False,
            )


if __name__ == "__main__":
    unittest.main()
