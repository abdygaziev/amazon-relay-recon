from datetime import date
import unittest

import pandas as pd

from reconcile import ValidationError, normalize_id, reconcile


class ReconcileTests(unittest.TestCase):
    def trips(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    " Load ID ": 1001,
                    "TripID": "T1",
                    "Status": "Completed",
                    "Driver": "A Driver",
                    "Route": "A>B",
                    "Stop 2 Actual Arrival Date": "05/20/2026",
                    "Stop 2 Actual Arrival Time": "10:00",
                },
                {
                    " Load ID ": 1002,
                    "TripID": "T2",
                    "Status": "Completed",
                    "Driver": "B Driver",
                    "Route": "B>C",
                    "Stop 2 Actual Arrival Date": "05/24/2026",
                    "Stop 2 Actual Arrival Time": "10:00",
                },
                {
                    " Load ID ": 1003,
                    "TripID": "T3",
                    "Status": "Cancelled",
                    "Driver": "C Driver",
                    "Route": "C>D",
                    "Stop 2 Actual Arrival Date": "",
                    "Stop 2 Actual Arrival Time": "",
                },
                {
                    " Load ID ": 1004,
                    "TripID": "T4",
                    "Status": "Completed",
                    "Driver": "D Driver",
                    "Route": "D>E",
                    "Stop 2 Actual Arrival Date": "05/21/2026",
                    "Stop 2 Actual Arrival Time": "09:00",
                },
            ]
        )

    def test_normalize_id_handles_numeric_export_values(self):
        self.assertEqual(normalize_id(1001.0), "1001")
        self.assertEqual(normalize_id(" 1001.0 "), "1001")
        self.assertEqual(normalize_id(None), "")

    def test_reconciles_paid_missing_cancelled_and_next_week(self):
        payments = pd.DataFrame(
            [
                {"Trip ID": "T1", "Load ID": "", "Gross Pay": "$500.00"},
            ]
        )

        result = reconcile(self.trips(), payments, date(2026, 5, 23))

        self.assertEqual(result.missing_df["Normalized Load ID"].tolist(), ["1004"])
        self.assertEqual(result.next_week_df["Normalized Load ID"].tolist(), ["1002"])
        self.assertEqual(result.cancelled_df["Normalized Load ID"].tolist(), ["1003"])
        self.assertEqual(result.paid_trip_count, 1)

    def test_completed_table_includes_payment_status_and_driver_display(self):
        payments = pd.DataFrame(
            [
                {"Trip ID": "T1", "Load ID": "", "Gross Pay": "$500.00"},
            ]
        )

        result = reconcile(self.trips(), payments, date(2026, 5, 23))

        self.assertEqual(result.completed_df["Normalized Load ID"].tolist(), ["1001", "1002", "1004"])
        self.assertEqual(result.completed_df["Driver Display"].tolist(), ["A Driver", "B Driver", "D Driver"])
        self.assertEqual(result.completed_df["Reconciliation Status"].tolist(), ["Paid", "Next Week", "Missing"])

    def test_nan_status_is_rejected(self):
        trips = self.trips()
        trips.loc[0, "Status"] = pd.NA
        payments = pd.DataFrame([{"Load ID": "OTHER"}])

        result = reconcile(trips, payments, date(2026, 5, 23))

        rejected = result.cancelled_df[result.cancelled_df["Normalized Load ID"] == "1001"]
        self.assertEqual(rejected["Status Display"].tolist(), ["Rejected"])
        self.assertNotIn("1001", result.missing_df["Normalized Load ID"].tolist())

    def test_payment_total_deduplicates_same_load_and_amount(self):
        trips = self.trips().iloc[:1]
        payments = pd.DataFrame(
            [
                {"LoadID": "1001", "GrossPay": "$500.00"},
                {"LoadID": "1001", "GrossPay": "$500.00"},
            ]
        )

        result = reconcile(trips, payments, date(2026, 5, 23))

        self.assertEqual(result.total_paid, 500.0)
        self.assertIn("Duplicate payment rows", result.warnings[0])

    def test_payment_total_uses_blank_id_invoice_summary_when_it_matches_details(self):
        trips = self.trips().iloc[:1]
        payments = pd.DataFrame(
            [
                {"Trip ID": "T1", "Load ID": "", "Gross Pay": "$400.00", "Item Type": "TOUR - COMPLETED"},
                {"Trip ID": "T1", "Load ID": "1001", "Gross Pay": "$100.00", "Item Type": "LOAD - COMPLETED"},
                {"Trip ID": "", "Load ID": "", "Gross Pay": "$500.00", "Item Type": ""},
            ]
        )

        result = reconcile(trips, payments, date(2026, 5, 23))

        self.assertEqual(result.total_paid, 500.0)

    def test_multi_stop_trip_rolls_to_next_week_when_last_stop_finishes_after_week_end(self):
        trips = pd.DataFrame(
            [
                {
                    "Load ID": "L1",
                    "Trip ID": "T1",
                    "Status": "Completed",
                    "Stop 2 Actual Arrival Date": "05/23/2026",
                    "Stop 2 Actual Arrival Time": "11:00",
                },
                {
                    "Load ID": "L2",
                    "Trip ID": "T1",
                    "Status": "Completed",
                    "Stop 2 Actual Arrival Date": "05/27/2026",
                    "Stop 2 Actual Arrival Time": "11:00",
                },
            ]
        )
        payments = pd.DataFrame([{"Load ID": "OTHER"}])

        result = reconcile(trips, payments, date(2026, 5, 23))

        self.assertEqual(len(result.missing_df), 0)
        self.assertEqual(result.next_week_df["Normalized Load ID"].tolist(), ["L1", "L2"])

    def test_missing_required_trip_columns_raise_validation_error(self):
        trips = pd.DataFrame([{"Load ID": "1001", "Status": "Completed"}])
        payments = pd.DataFrame([{"Load ID": "1001"}])

        with self.assertRaises(ValidationError) as context:
            reconcile(trips, payments, date(2026, 5, 23))

        self.assertIn("Completion date", str(context.exception))

    def test_requires_payment_load_or_trip_id(self):
        payments = pd.DataFrame([{"Gross Pay": "$10.00"}])

        with self.assertRaises(ValidationError) as context:
            reconcile(self.trips(), payments, date(2026, 5, 23))

        self.assertIn("Load ID or Trip ID", str(context.exception))


if __name__ == "__main__":
    unittest.main()
