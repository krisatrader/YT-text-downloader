#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tesztek a web_app.py FastAPI végpontjaihoz (Sütikezelés, Letöltésjelölés, Újrafuttatás, Előzmények).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient
from web_app import app, OUTPUT_DIR, cookie_mgr


class TestWebApp(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.test_folder = OUTPUT_DIR / "Teszt_Gyujtemeny"
        self.test_folder.mkdir(parents=True, exist_ok=True)
        
        # Mintafájlok és summary.json létrehozása
        sample_file = self.test_folder / "001 - Teszt Video [12345].md"
        sample_file.write_text("# Teszt Video Átirat\nEz egy próba szöveg.", encoding="utf-8")

        summary_data = {
            "collection_title": "Teszt Gyűjtemény",
            "url": "https://www.youtube.com/watch?v=12345",
            "processed_at": "2026-08-21 09:30:00",
            "total_videos": 1,
            "successful_count": 1,
            "blocked_count": 0,
            "no_transcript_count": 0,
            "error_count": 0,
            "zip_downloaded": False,
            "zip_downloaded_at": None,
            "zip_download_count": 0,
            "original_params": {
                "url": "https://www.youtube.com/watch?v=12345",
                "format": "both",
                "languages": "hu,en",
                "delay_min": 2.0,
                "delay_max": 3.0,
                "include_timestamps": True,
                "limit": None,
            },
            "successful_videos": [
                {
                    "index": 1,
                    "id": "12345",
                    "title": "Teszt Video",
                    "url": "https://www.youtube.com/watch?v=12345",
                    "files": [str(sample_file)],
                }
            ],
            "blocked_videos": [],
            "no_transcript_videos": [],
            "error_videos": [],
        }
        (self.test_folder / "summary.json").write_text(json.dumps(summary_data, indent=2), encoding="utf-8")

    def tearDown(self):
        if self.test_folder.exists():
            shutil.rmtree(self.test_folder)

    def test_index_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("YouTube Átirat Letöltő", response.text)
        self.assertIn("Beépített YouTube Sütik", response.text)
        self.assertIn("Mentett Gyűjtemények", response.text)

    def test_cookies_endpoints(self):
        # 1. Lekérdezés
        res = self.client.get("/api/cookies/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("has_cookies", data)
        self.assertIn("available_browsers", data)

        # 2. Mentés
        sample_cookie = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1800000000\tSID\tsample_value"
        res_save = self.client.post("/api/cookies/save", json={"cookies_text": sample_cookie})
        self.assertEqual(res_save.status_code, 200)
        self.assertTrue(res_save.json()["status"]["has_cookies"])

        # 3. Törlés
        res_clear = self.client.post("/api/cookies/clear")
        self.assertEqual(res_clear.status_code, 200)
        self.assertFalse(res_clear.json()["status"]["has_cookies"])

    def test_history_and_collection_details(self):
        # 1. Előzmények lekérése
        res = self.client.get("/api/history")
        self.assertEqual(res.status_code, 200)
        history = res.json()["history"]
        self.assertGreaterEqual(len(history), 1)

        # 2. Gyűjtemény részleteinek lekérése
        res_det = self.client.get("/api/collection-details?folder_name=Teszt_Gyujtemeny")
        self.assertEqual(res_det.status_code, 200)
        det_data = res_det.json()
        self.assertEqual(det_data["collection_title"], "Teszt Gyűjtemény")
        self.assertEqual(len(det_data["successful_videos"]), 1)

    def test_zip_download_and_status_marking(self):
        # Letöltés előtt
        res_det1 = self.client.get("/api/collection-details?folder_name=Teszt_Gyujtemeny")
        self.assertFalse(res_det1.json().get("zip_downloaded", False))

        # ZIP letöltése
        res_zip = self.client.get("/api/download-collection-zip?folder_name=Teszt_Gyujtemeny")
        self.assertEqual(res_zip.status_code, 200)
        self.assertEqual(res_zip.headers["content-type"], "application/zip")

        # Letöltés után a summary.json-ben be kell lennie jelölve a letöltésnek
        res_det2 = self.client.get("/api/collection-details?folder_name=Teszt_Gyujtemeny")
        self.assertTrue(res_det2.json()["zip_downloaded"])
        self.assertIsNotNone(res_det2.json()["zip_downloaded_at"])
        self.assertEqual(res_det2.json()["zip_download_count"], 1)

    def test_rerun_saved_collection(self):
        # Újrafuttatás tesztelése
        res_rerun = self.client.post("/api/re-run", json={"folder_name": "Teszt_Gyujtemeny"})
        self.assertEqual(res_rerun.status_code, 200)
        data = res_rerun.json()
        self.assertIn("task_id", data)
        self.assertEqual(data["status"], "started")


if __name__ == "__main__":
    unittest.main()
