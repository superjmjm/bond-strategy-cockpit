"""A Drive catalog entry can remain visible after its original becomes inaccessible."""
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from googleapiclient.errors import HttpError
from httplib2 import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backfill_range as br


class ArchivePreflight(TestCase):
    def test_stale_catalog_does_not_hide_source_candidate(self):
        name = "123-浙商证券-债市策略思考：测试-261026.pdf"
        drive = Mock()
        drive.files().get().execute.side_effect = HttpError(Response({"status": "404"}), b"{}")
        def scan(channel, start, end, brokers, ignored, rows, hits, skipped):
            rows["123"] = ("浙商证券", name, 12, channel)
            return 1
        env = dict(START_DATE="2026-10-26", END_DATE="2026-11-08", BROKERS="浙商证券",
                   MAX_DOWNLOADS="10", DRY_RUN="true")
        with TemporaryDirectory() as tmp, patch.dict(os.environ, env), \
                patch.object(br, "STATE", Path(tmp) / "inventory.json"), \
                patch.object(br.io, "drive_service", return_value=drive), \
                patch.object(br, "published", return_value=[]), \
                patch.object(br, "archive_rows", return_value=[(name, "123", "a" * 64, "inaccessible")]), \
                patch.object(br, "scan_channel", side_effect=scan):
            br.run()
            report = json.loads(br.STATE.read_bytes())
            self.assertEqual(report["archive"], {})
            self.assertEqual(report["candidates"]["浙商证券"], ["123"])
            self.assertEqual(report["inaccessible"]["浙商证券"], [name])
            drive.files().get_media.assert_not_called()
