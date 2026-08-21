#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Átirat Letöltő Webes Kiszolgáló (FastAPI + SSE valós idejű kommunikáció + Teljes Sütikezelés & Gyűjtemények Hub)
"""

import asyncio
import io
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Helyi modul importálása
from transcript_downloader import (
    YouTubeExtractor,
    TranscriptFetcher,
    TranscriptFormatter,
    TranscriptTranslator,
    CookieManager,
    sanitize_filename,
)

app = FastAPI(title="YouTube Átirat Letöltő Web UI")

BASE_DIR = Path(__file__).parent.resolve()
OUTPUT_DIR = BASE_DIR / "transcripts_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Aktív feladatok és eseménycsatornák
active_tasks: Dict[str, Dict[str, Any]] = {}
task_event_queues: Dict[str, asyncio.Queue] = {}

cookie_mgr = CookieManager(str(OUTPUT_DIR / ".saved_cookies.txt"))


class DownloadRequest(BaseModel):
    url: str
    format: str = "both"  # 'md', 'txt', 'both'
    languages: str = "hu,en"
    target_language: str = "original"  # 'original' (nincs fordítás), 'hu', 'en', 'de', stb.
    delay_min: float = 3.0
    delay_max: float = 5.0
    include_timestamps: bool = True
    limit: Optional[int] = None
    cookies_text: Optional[str] = None
    proxy: Optional[str] = None


class SaveCookieRequest(BaseModel):
    cookies_text: str


class BrowserCookieRequest(BaseModel):
    browser: str


class ReRunRequest(BaseModel):
    folder_name: str


def run_downloader_task(task_id: str, req: DownloadRequest, loop: asyncio.AbstractEventLoop, temp_cookies_file: Optional[str] = None):
    """Háttérszálban futó letöltési motor, amely eseményeket küld az SSE queue-ba."""
    task_info = active_tasks[task_id]
    queue = task_event_queues.get(task_id)

    def emit_event(event_type: str, data: Dict[str, Any]):
        message = {"type": event_type, "data": data, "timestamp": time.time()}
        if queue:
            loop.call_soon_threadsafe(queue.put_nowait, message)

    try:
        emit_event("log", {"level": "info", "message": f"URL elemzése: {req.url}..."})
        extractor = YouTubeExtractor(cookies_file=temp_cookies_file, proxy=req.proxy)
        collection_title, videos = extractor.extract_info(req.url, limit=req.limit)

        if not videos:
            task_info["status"] = "error"
            task_info["error"] = "Nem található letölthető videó a megadott linken."
            emit_event("error", {"message": task_info["error"]})
            return

        if req.limit and req.limit > 0:
            videos = videos[:req.limit]

        total_videos = len(videos)
        sanitized_col_title = sanitize_filename(collection_title)
        target_folder = OUTPUT_DIR / sanitized_col_title
        target_folder.mkdir(parents=True, exist_ok=True)

        task_info["collection_title"] = collection_title
        task_info["target_folder"] = str(target_folder)
        task_info["total_videos"] = total_videos

        emit_event("info_extracted", {
            "collection_title": collection_title,
            "total_videos": total_videos,
            "target_folder": str(target_folder),
        })

        langs = [l.strip() for l in req.languages.split(",") if l.strip()]
        fetcher = TranscriptFetcher(
            preferred_languages=langs,
            cookies_file=temp_cookies_file,
            proxy=req.proxy,
        )

        summary: Dict[str, Any] = {
            "collection_title": collection_title,
            "url": req.url,
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_videos": total_videos,
            "target_language": req.target_language,
            "successful_count": 0,
            "blocked_count": 0,
            "no_transcript_count": 0,
            "error_count": 0,
            "zip_downloaded": False,
            "zip_downloaded_at": None,
            "zip_download_count": 0,
            "original_params": {
                "url": req.url,
                "format": req.format,
                "languages": req.languages,
                "target_language": req.target_language,
                "delay_min": req.delay_min,
                "delay_max": req.delay_max,
                "include_timestamps": req.include_timestamps,
                "limit": req.limit,
                "proxy": req.proxy,
            },
            "successful_videos": [],
            "blocked_videos": [],
            "no_transcript_videos": [],
            "error_videos": [],
        }

        task_info["summary"] = summary

        for index, video in enumerate(videos, start=1):
            if task_info.get("cancelled", False):
                emit_event("log", {"level": "warn", "message": "A feladat felhasználói kérésre megszakítva."})
                break

            vid_id = video["id"]
            vid_title = video.get("title", f"video_{vid_id}")
            safe_title = sanitize_filename(vid_title)
            filename_base = f"{index:03d} - {safe_title} [{vid_id}]"

            emit_event("video_started", {
                "index": index,
                "total": total_videos,
                "id": vid_id,
                "title": vid_title,
                "url": video["url"],
            })

            # Átirat lekérése
            transcript_res = fetcher.get_transcript(vid_id)

            if transcript_res["status"] == "success":
                # Ha kértek fordítást
                if req.target_language and req.target_language.lower() not in ("original", "none", ""):
                    t_name = TranscriptTranslator.get_language_name(req.target_language)
                    emit_event("log", {
                        "level": "info",
                        "message": f"[{index}/{total_videos}] Fordítás {t_name} nyelvre..."
                    })
                    transcript_res = TranscriptTranslator.translate_transcript(
                        transcript_res, req.target_language
                    )

                saved_files = []
                if req.format in ("txt", "both"):
                    txt_path = target_folder / f"{filename_base}.txt"
                    txt_content = TranscriptFormatter.format_as_txt(
                        video, transcript_res, include_timestamps=req.include_timestamps
                    )
                    txt_path.write_text(txt_content, encoding="utf-8")
                    saved_files.append(str(txt_path))

                if req.format in ("md", "both"):
                    md_path = target_folder / f"{filename_base}.md"
                    md_content = TranscriptFormatter.format_as_markdown(
                        video, transcript_res, include_timestamps=req.include_timestamps
                    )
                    md_path.write_text(md_content, encoding="utf-8")
                    saved_files.append(str(md_path))

                summary["successful_count"] += 1
                video_record = {
                    "index": index,
                    "id": vid_id,
                    "title": vid_title,
                    "url": video["url"],
                    "language": transcript_res.get("language"),
                    "language_code": transcript_res.get("language_code"),
                    "is_generated": transcript_res.get("is_generated"),
                    "is_translated": transcript_res.get("is_translated", False),
                    "original_language": transcript_res.get("original_language"),
                    "target_language": transcript_res.get("target_language"),
                    "status": "success",
                    "files": saved_files,
                }
                summary["successful_videos"].append(video_record)
                task_info["current_progress"] = int((index / total_videos) * 100)

                emit_event("video_completed", {
                    "index": index,
                    "total": total_videos,
                    "status": "success",
                    "video": video_record,
                    "progress": task_info["current_progress"],
                })

            elif transcript_res["status"] == "blocked":
                reason = transcript_res.get("error_reason", "YouTube botvédelem (HTTP 429)")
                summary["blocked_count"] += 1
                video_record = {
                    "index": index,
                    "id": vid_id,
                    "title": vid_title,
                    "url": video["url"],
                    "reason": reason,
                    "status": "blocked",
                }
                summary["blocked_videos"].append(video_record)
                task_info["current_progress"] = int((index / total_videos) * 100)

                emit_event("video_completed", {
                    "index": index,
                    "total": total_videos,
                    "status": "blocked",
                    "video": video_record,
                    "progress": task_info["current_progress"],
                })

            elif transcript_res["status"] == "no_transcript":
                reason = transcript_res.get("error_reason", "A videóhoz nem található átirat a YouTube-on")
                summary["no_transcript_count"] += 1
                video_record = {
                    "index": index,
                    "id": vid_id,
                    "title": vid_title,
                    "url": video["url"],
                    "reason": reason,
                    "status": "no_transcript",
                }
                summary["no_transcript_videos"].append(video_record)
                task_info["current_progress"] = int((index / total_videos) * 100)

                emit_event("video_completed", {
                    "index": index,
                    "total": total_videos,
                    "status": "no_transcript",
                    "video": video_record,
                    "progress": task_info["current_progress"],
                })

            else:
                reason = transcript_res.get("error_reason", "Ismeretlen hiba")
                summary["error_count"] += 1
                video_record = {
                    "index": index,
                    "id": vid_id,
                    "title": vid_title,
                    "url": video["url"],
                    "reason": reason,
                    "status": "error",
                }
                summary["error_videos"].append(video_record)
                task_info["current_progress"] = int((index / total_videos) * 100)

                emit_event("video_completed", {
                    "index": index,
                    "total": total_videos,
                    "status": "error",
                    "video": video_record,
                    "progress": task_info["current_progress"],
                })

            # Késleltetés a YouTube védelmi rendszerének elkerülésére (ha nem az utolsó videó)
            if index < total_videos and not task_info.get("cancelled", False):
                if index % 20 == 0:
                    pause_time = random.uniform(15.0, 22.0)
                    emit_event("log", {
                        "level": "info",
                        "message": f"☕ Szakaszos pihenő fázis ({index} videó után): {round(pause_time, 1)} mp szünet a YouTube botvédelem megelőzésére..."
                    })
                    emit_event("waiting", {
                        "delay_seconds": round(pause_time, 1),
                        "next_index": index + 1,
                        "is_batch_pause": True,
                    })
                    time.sleep(pause_time)
                else:
                    delay_sec = random.uniform(req.delay_min, req.delay_max)
                    emit_event("waiting", {
                        "delay_seconds": round(delay_sec, 2),
                        "next_index": index + 1,
                        "is_batch_pause": False,
                    })
                    time.sleep(delay_sec)

        # Jelentések mentése
        json_path = target_folder / "summary.json"
        json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        md_lines = [
            f"# Átirat Letöltési Összefoglaló: {summary['collection_title']}\n",
            f"- **Forrás URL:** [{summary['url']}]({summary['url']})",
            f"- **Feldolgozás ideje:** {summary['processed_at']}",
            f"- **Összes videó:** {summary['total_videos']} db",
            f"- **Sikeresen letöltve:** {summary['successful_count']} db ✅",
        ]
        if summary.get("blocked_count", 0) > 0:
            md_lines.append(f"- **YouTube által blokkolva (HTTP 429):** {summary['blocked_count']} db 🚫")
        md_lines.append(f"- **Valóban nincs átirat:** {summary['no_transcript_count']} db ⚠️")
        md_lines.append(f"- **Hibák:** {summary['error_count']} db ❌\n")

        if summary.get("blocked_videos"):
            md_lines.append("## 🚫 YouTube Botvédelem által BLOKKOLT videók (HTTP 429)\n")
            md_lines.append("> [!WARNING]")
            md_lines.append("> Ezekhez a videókhoz **létezik felirat**, de a YouTube szervere ideiglenesen korlátozta a letöltést az IP címedről.")
            md_lines.append("> **Megoldás:** Tölts be egy `cookies.txt` fájlt a Sütikezelőben vagy használj böngésző sütiket!\n")
            md_lines.append("| # | Cím | URL | Ok / Megoldás |")
            md_lines.append("|---|---|---|---|")
            for item in summary["blocked_videos"]:
                clean_t = item["title"].replace("|", "-")
                md_lines.append(f"| {item['index']} | {clean_t} | [{item['id']}]({item['url']}) | {item['reason']} |")
            md_lines.append("\n")

        if summary["no_transcript_videos"]:
            md_lines.append("## ⚠️ Videók, amelyeknél VALÓBAN NEM volt elérhető átirat\n")
            md_lines.append("| # | Cím | URL | Ok |")
            md_lines.append("|---|---|---|---|")
            for item in summary["no_transcript_videos"]:
                clean_t = item["title"].replace("|", "-")
                md_lines.append(f"| {item['index']} | {clean_t} | [{item['id']}]({item['url']}) | {item['reason']} |")
            md_lines.append("\n")

        if summary["error_videos"]:
            md_lines.append("## ❌ Hibára futott videók\n")
            md_lines.append("| # | Cím | URL | Hibaüzenet |")
            md_lines.append("|---|---|---|---|")
            for item in summary["error_videos"]:
                clean_t = item["title"].replace("|", "-")
                md_lines.append(f"| {item['index']} | {clean_t} | [{item['id']}]({item['url']}) | {item['reason']} |")
            md_lines.append("\n")

        if summary["successful_videos"]:
            md_lines.append("## ✅ Sikeresen letöltött átiratok\n")
            md_lines.append("| # | Cím | URL | Típus / Nyelv |")
            md_lines.append("|---|---|---|---|")
            for item in summary["successful_videos"]:
                clean_t = item["title"].replace("|", "-")
                type_name = "Auto" if item.get("is_generated") else "Manuális"
                md_lines.append(f"| {item['index']} | {clean_t} | [{item['id']}]({item['url']}) | {type_name} ({item.get('language')}) |")
            md_lines.append("\n")

        md_path = target_folder / "summary.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        task_info["status"] = "completed"
        emit_event("finished", {
            "summary": summary,
            "target_folder": str(target_folder),
            "collection_title": collection_title,
        })

    except Exception as e:
        task_info["status"] = "error"
        task_info["error"] = str(e)
        emit_event("error", {"message": f"Kivétel történt: {str(e)}"})
    finally:
        if temp_cookies_file and os.path.exists(temp_cookies_file):
            try:
                os.remove(temp_cookies_file)
            except Exception:
                pass


@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    """Kiszolgálja a webes kezelőfelületet."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/start")
async def start_download(req: DownloadRequest):
    """Új letöltési folyamat indítása a háttérben."""
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="A YouTube URL megadása kötelező!")

    temp_cookies_file = None
    if req.cookies_text and req.cookies_text.strip():
        tf = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8")
        tf.write(req.cookies_text.strip())
        tf.close()
        temp_cookies_file = tf.name

    task_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    event_queue = asyncio.Queue()

    task_event_queues[task_id] = event_queue
    active_tasks[task_id] = {
        "id": task_id,
        "url": req.url,
        "status": "running",
        "current_progress": 0,
        "total_videos": 0,
        "collection_title": "",
        "created_at": time.time(),
        "summary": None,
        "error": None,
        "cancelled": False,
    }

    thread = threading.Thread(
        target=run_downloader_task,
        args=(task_id, req, loop, temp_cookies_file),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "started", "url": req.url}


@app.post("/api/re-run")
async def rerun_saved_collection(req: ReRunRequest):
    """Mentett gyűjtemény újrafuttatása az eredeti URL-lel és paraméterekkel."""
    clean_name = sanitize_filename(req.folder_name)
    target_folder = OUTPUT_DIR / clean_name
    summary_file = target_folder / "summary.json"

    if not summary_file.exists():
        raise HTTPException(status_code=404, detail="A megadott gyűjtemény nem található.")

    try:
        data = json.loads(summary_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Nem sikerült beolvasni a gyűjtemény adatait: {e}")

    orig = data.get("original_params") or {}
    url = orig.get("url") or data.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Nem található forrás URL a gyűjteményben.")

    download_req = DownloadRequest(
        url=url,
        format=orig.get("format", "both"),
        languages=orig.get("languages", "hu,en"),
        target_language=orig.get("target_language") or data.get("target_language") or "original",
        delay_min=float(orig.get("delay_min", 2.0)),
        delay_max=float(orig.get("delay_max", 3.0)),
        include_timestamps=bool(orig.get("include_timestamps", True)),
        limit=orig.get("limit", None),
        proxy=orig.get("proxy", None),
    )

    return await start_download(download_req)


# ==================== SÜTIKEZELŐ VÉGPONTOK (Cookie Manager) ====================

@app.get("/api/cookies/status")
async def get_cookies_status():
    """Lekéri a perzisztens sütik állapotát és az elérhető böngészőket."""
    return cookie_mgr.get_status()


@app.post("/api/cookies/diagnose")
async def diagnose_cookies(req: SaveCookieRequest):
    """Süti tartalom valós idejű hitelességi és hiányossági elemzése."""
    diag = CookieManager.inspect_cookie_content(req.cookies_text)
    return diag


@app.post("/api/cookies/save")
async def save_cookies(req: SaveCookieRequest):
    """Manuálisan beillesztett sütik mentése perzisztens fájlba."""
    if not req.cookies_text or not req.cookies_text.strip():
        raise HTTPException(status_code=400, detail="A süti szöveg nem lehet üres.")
    cookie_mgr.save_cookie_text(req.cookies_text)
    return {"success": True, "status": cookie_mgr.get_status()}


@app.post("/api/cookies/from-browser")
async def extract_browser_cookies(req: BrowserCookieRequest):
    """Sütik kinyerése közvetlenül egy telepített böngészőből."""
    try:
        success, msg = await asyncio.wait_for(
            asyncio.to_thread(cookie_mgr.extract_from_browser, req.browser.lower()),
            timeout=8.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"A(z) {req.browser.capitalize()} sütik kinyerése időtúllépés miatt megszakadt (a böngésző vagy a rendszer kulcstartója zárolva lehet). Kérlek használd a cookies.txt feltöltést/beillesztést!"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg, "status": cookie_mgr.get_status()}


@app.post("/api/cookies/clear")
async def clear_cookies():
    """Mentett sütik törlése."""
    cookie_mgr.clear()
    return {"success": True, "status": cookie_mgr.get_status()}


# ==================== PROGRESS ÉS LETÖLTÉS ====================

@app.get("/api/progress/{task_id}")
async def get_progress_stream(task_id: str):
    """SSE (Server-Sent Events) folyam valós idejű állapotfrissítésekhez."""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="A feladat nem található.")

    queue = task_event_queues.get(task_id)

    async def event_generator():
        task = active_tasks[task_id]
        yield f"data: {json.dumps({'type': 'init', 'data': task})}\n\n"

        if queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] in ("finished", "error"):
                        break
                except asyncio.TimeoutError:
                    yield f": ping\n\n"
                except Exception:
                    break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/status/{task_id}")
async def get_task_status(task_id: str):
    """JSON formátumú állapotlekérdezés."""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="A feladat nem található.")
    return active_tasks[task_id]


@app.post("/api/cancel/{task_id}")
async def cancel_task(task_id: str):
    """Futó letöltési feladat megszakítása."""
    if task_id in active_tasks:
        active_tasks[task_id]["cancelled"] = True
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="A feladat nem található.")


def mark_zip_as_downloaded(folder: Path):
    """Megjelöli a summary.json-ben, hogy a ZIP fájl le lett töltve."""
    summary_file = folder / "summary.json"
    if summary_file.exists():
        try:
            data = json.loads(summary_file.read_text(encoding="utf-8"))
            data["zip_downloaded"] = True
            data["zip_downloaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            data["zip_download_count"] = data.get("zip_download_count", 0) + 1
            summary_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


@app.get("/api/download-zip/{task_id}")
async def download_zip(task_id: str):
    """Letöltött fájlok tömörítése és letöltése ZIP archívumként (megjelölve letöltöttként)."""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="A feladat nem található.")

    task = active_tasks[task_id]
    target_folder_str = task.get("target_folder")
    if not target_folder_str or not os.path.exists(target_folder_str):
        raise HTTPException(status_code=404, detail="A kimeneti könyvtár még nem érhető el.")

    target_folder = Path(target_folder_str)
    mark_zip_as_downloaded(target_folder)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in target_folder.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(target_folder)
                zip_file.write(file_path, arcname=str(arcname))

    zip_buffer.seek(0)
    zip_filename = f"{sanitize_filename(task.get('collection_title', 'youtube_transcripts'))}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@app.get("/api/download-collection-zip")
async def download_collection_zip(folder_name: str):
    """Meglévő letöltött kollekció mappa letöltése ZIP-ként név alapján (megjelölve letöltöttként)."""
    clean_name = sanitize_filename(folder_name)
    target_folder = OUTPUT_DIR / clean_name
    if not target_folder.exists() or not target_folder.is_dir():
        raise HTTPException(status_code=404, detail="A megadott gyűjtemény nem található.")

    mark_zip_as_downloaded(target_folder)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in target_folder.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(target_folder)
                zip_file.write(file_path, arcname=str(arcname))

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{clean_name}.zip"'},
    )


@app.get("/api/file-content")
async def get_file_content(path: str):
    """Egy adott átirat fájl tartalmának biztonságos beolvasása előnézethez."""
    file_path = Path(path).resolve()
    if not str(file_path).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Hozzáférés megtagadva.")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="A fájl nem található.")

    content = file_path.read_text(encoding="utf-8")
    return {"content": content, "filename": file_path.name}


@app.get("/api/collection-details")
async def get_collection_details(folder_name: str):
    """Egy mentett gyűjtemény összes részletének és videólistájának lekérése."""
    clean_name = sanitize_filename(folder_name)
    target_folder = OUTPUT_DIR / clean_name
    summary_file = target_folder / "summary.json"

    if not summary_file.exists():
        raise HTTPException(status_code=404, detail="A gyűjtemény nem található.")

    try:
        data = json.loads(summary_file.read_text(encoding="utf-8"))
        data["folder_name"] = clean_name
        
        # Ha a summary.json-ben nincsenek feltöltve a fájlok, de a lemezen vannak fájlok, egészítsük ki
        existing_files = {f.name: str(f.resolve()) for f in target_folder.glob("*") if f.is_file()}
        for vid in data.get("successful_videos", []):
            if not vid.get("files"):
                matched = [path for fname, path in existing_files.items() if vid["id"] in fname]
                if matched:
                    vid["files"] = matched

        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/collection")
async def delete_collection(folder_name: str):
    """Mentett gyűjtemény törlése."""
    clean_name = sanitize_filename(folder_name)
    target_folder = OUTPUT_DIR / clean_name
    if not target_folder.exists() or not target_folder.is_dir():
        raise HTTPException(status_code=404, detail="A megadott gyűjtemény nem található.")

    try:
        shutil.rmtree(target_folder)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
async def get_history():
    """Korábban letöltött kollekciók listázása a letöltöttségi státusszal és forrás URL-lel együtt."""
    history = []
    if OUTPUT_DIR.exists():
        for folder in sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if folder.is_dir() and not folder.name.startswith("."):
                summary_file = folder / "summary.json"
                if summary_file.exists():
                    try:
                        data = json.loads(summary_file.read_text(encoding="utf-8"))
                        history.append({
                            "folder_name": folder.name,
                            "collection_title": data.get("collection_title", folder.name),
                            "url": data.get("url", ""),
                            "processed_at": data.get("processed_at"),
                            "total_videos": data.get("total_videos", 0),
                            "successful_count": data.get("successful_count", 0),
                            "blocked_count": data.get("blocked_count", 0),
                            "no_transcript_count": data.get("no_transcript_count", 0),
                            "error_count": data.get("error_count", 0),
                            "target_language": data.get("target_language"),
                            "zip_downloaded": data.get("zip_downloaded", False),
                            "zip_downloaded_at": data.get("zip_downloaded_at"),
                            "zip_download_count": data.get("zip_download_count", 0),
                        })
                    except Exception:
                        history.append({
                            "folder_name": folder.name,
                            "collection_title": folder.name,
                            "url": "",
                            "processed_at": "Ismeretlen",
                            "total_videos": len(list(folder.glob("*.md"))),
                            "zip_downloaded": False,
                        })
    return {"history": history}


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("🚀 YouTube Átirat Letöltő Web Szerver Indulása...")
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000, reload=True)
