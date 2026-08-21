#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tesztek a transcript_downloader.py funkcióihoz.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from transcript_downloader import (
    sanitize_filename,
    format_timestamp,
    TranscriptFormatter,
    DownloaderEngine,
)


class TestTranscriptDownloader(unittest.TestCase):

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("Normal Title"), "Normal Title")
        self.assertEqual(sanitize_filename("Title / With: Invalid * Characters?"), "Title With Invalid Characters")
        self.assertEqual(sanitize_filename("   Spaces   "), "Spaces")
        self.assertEqual(sanitize_filename(""), "untitled")
        self.assertEqual(sanitize_filename("A" * 200, max_length=50), "A" * 50)

    def test_format_timestamp(self):
        self.assertEqual(format_timestamp(0), "00:00")
        self.assertEqual(format_timestamp(45), "00:45")
        self.assertEqual(format_timestamp(65), "01:05")
        self.assertEqual(format_timestamp(3665), "01:01:05")
        self.assertEqual(format_timestamp(7325), "02:02:05")

    def test_transcript_formatter_txt(self):
        video_info = {
            "title": "Teszt Videó",
            "url": "https://www.youtube.com/watch?v=12345",
            "channel": "Teszt Csatorna",
        }
        transcript_data = {
            "is_generated": False,
            "language": "Hungarian",
            "language_code": "hu",
            "segments": [
                {"start": 0.0, "duration": 2.5, "text": "Első mondat."},
                {"start": 3.0, "duration": 2.0, "text": "Második mondat."},
            ],
            "raw_text": "Első mondat. Második mondat.",
        }

        # Időbélyeg nélkül
        txt_plain = TranscriptFormatter.format_as_txt(video_info, transcript_data, include_timestamps=False)
        self.assertIn("CÍM: Teszt Videó", txt_plain)
        self.assertIn("Manuális felirat", txt_plain)
        self.assertIn("Első mondat. Második mondat.", txt_plain)

        # Időbélyeggel
        txt_ts = TranscriptFormatter.format_as_txt(video_info, transcript_data, include_timestamps=True)
        self.assertIn("[00:00] Első mondat.", txt_ts)
        self.assertIn("[00:03] Második mondat.", txt_ts)

    def test_transcript_formatter_markdown(self):
        video_info = {
            "title": "Teszt Markdown Videó",
            "url": "https://www.youtube.com/watch?v=test_id",
            "channel": "Készítő Csatorna",
            "id": "test_id",
            "duration": 125,
        }
        transcript_data = {
            "is_generated": True,
            "language": "English",
            "language_code": "en",
            "segments": [
                {"start": 10.0, "duration": 5.0, "text": "Hello world"},
                {"start": 16.0, "duration": 4.0, "text": "Welcome to the video"},
            ],
            "raw_text": "Hello world Welcome to the video",
        }

        md_out = TranscriptFormatter.format_as_markdown(video_info, transcript_data, include_timestamps=True)
        self.assertIn("# Teszt Markdown Videó", md_out)
        self.assertIn("🤖 Automatikusan generált", md_out)
        self.assertIn("`test_id`", md_out)
        self.assertIn("- **`[00:10]`** Hello world", md_out)
        self.assertIn("- **`[00:16]`** Welcome to the video", md_out)

    def test_summary_report_generation(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            engine = DownloaderEngine(output_dir=str(temp_dir))
            summary_data = {
                "collection_title": "Teszt Kollekció",
                "url": "https://www.youtube.com/playlist?list=PLtest",
                "processed_at": "2026-08-21T08:50:00",
                "total_videos": 3,
                "successful_count": 1,
                "no_transcript_count": 1,
                "error_count": 1,
                "successful_videos": [
                    {
                        "index": 1,
                        "id": "vid1",
                        "title": "Sikeres videó",
                        "url": "https://www.youtube.com/watch?v=vid1",
                        "language": "Hungarian",
                        "is_generated": False,
                    }
                ],
                "no_transcript_videos": [
                    {
                        "index": 2,
                        "id": "vid2",
                        "title": "Zenei videó szöveg nélkül",
                        "url": "https://www.youtube.com/watch?v=vid2",
                        "reason": "Az átiratok / feliratok le vannak tiltva ezen a videón (TranscriptsDisabled).",
                    }
                ],
                "error_videos": [
                    {
                        "index": 3,
                        "id": "vid3",
                        "title": "Privát videó",
                        "url": "https://www.youtube.com/watch?v=vid3",
                        "reason": "A videó nem érhető el vagy privát (VideoUnavailable).",
                    }
                ],
            }

            engine._save_summary_reports(temp_dir, summary_data)

            # Ellenőrizzük a JSON fájlt
            json_file = temp_dir / "summary.json"
            self.assertTrue(json_file.exists())
            with open(json_file, "r", encoding="utf-8") as f:
                loaded_json = json.load(f)
            self.assertEqual(loaded_json["total_videos"], 3)
            self.assertEqual(loaded_json["no_transcript_count"], 1)

            # Ellenőrizzük a Markdown összefoglalót
            md_file = temp_dir / "summary.md"
            self.assertTrue(md_file.exists())
            md_content = md_file.read_text(encoding="utf-8")
            self.assertIn("# Átirat Letöltési Összefoglaló: Teszt Kollekció", md_content)
            self.assertIn("VALÓBAN NEM volt elérhető átirat", md_content)
            self.assertIn("Zenei videó szöveg nélkül", md_content)
            self.assertIn("TranscriptsDisabled", md_content)
            self.assertIn("Hibára futott videók", md_content)
            self.assertIn("Privát videó", md_content)

        finally:
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()
