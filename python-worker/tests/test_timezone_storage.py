from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sunny_core.db import now_sql, sql_datetime


class TimezoneStorageTests(unittest.TestCase):
    def test_current_time_is_stored_with_shanghai_offset(self):
        with patch.dict(os.environ, {"SUNNY_TIMEZONE": "Asia/Shanghai"}):
            value = datetime.fromisoformat(now_sql())

        self.assertEqual(value.utcoffset().total_seconds(), 8 * 60 * 60)

    def test_epoch_keeps_its_instant_when_converted_to_shanghai(self):
        epoch = 1893456000
        with patch.dict(os.environ, {"SUNNY_TIMEZONE": "Asia/Shanghai"}):
            value = datetime.fromisoformat(sql_datetime(epoch))

        self.assertEqual(value.utcoffset().total_seconds(), 8 * 60 * 60)
        self.assertEqual(int(value.astimezone(timezone.utc).timestamp()), epoch)


if __name__ == "__main__":
    unittest.main()
