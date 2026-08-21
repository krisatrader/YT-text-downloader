#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Video, Lejátszási lista és Csatorna Átirat Letöltő (Transcript Downloader)

Letölti az elérhető manuális és automatikusan generált feliratokat/átiratokat
betűről betűre .txt és .md formátumban, sebességkorlátozással (2-3 mp késleltetés),
beépített sütikezeléssel (cookies.txt / böngésző sütik) és részletes összefoglalóval a hiányzó átiratokról.
"""

import argparse
import datetime
import http.cookiejar
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Harmadik féltől származó csomagok importálása
try:
    import yt_dlp
    import yt_dlp.cookies
except ImportError:
    print("Hiba: A 'yt-dlp' csomag nincs telepítve. Futtasd: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import requests
    import certifi
    import youtube_transcript_api
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        CouldNotRetrieveTranscript,
    )
except ImportError:
    print("Hiba: A szükséges csomagok nincsenek telepítve. Futtasd: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None


def sanitize_filename(name: str, max_length: int = 150) -> str:
    """Fájlnév érvényesítése és tisztítása operációs rendszerekhez."""
    if not name:
        return "untitled"
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_length:
        clean = clean[:max_length].rstrip()
    return clean or "untitled"


def format_timestamp(seconds: float) -> str:
    """Másodpercek formázása [HH:MM:SS] vagy [MM:SS] alakra."""
    try:
        sec = int(round(seconds))
    except (ValueError, TypeError):
        return "00:00"
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    s = sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{s:02d}"
    return f"{minutes:02d}:{s:02d}"


class CookieManager:
    """Központi süti- és munkamenetkezelő (fájlból, böngészőből, perzisztens tárolóból)."""

    def __init__(self, cookies_file_path: Optional[str] = None):
        if cookies_file_path:
            self.storage_path = Path(cookies_file_path).resolve()
        else:
            self.storage_path = (Path(__file__).parent / "transcripts_output" / ".saved_cookies.txt").resolve()

    @staticmethod
    def inspect_cookie_content(text: str) -> Dict[str, Any]:
        """Elemzi a Netscape süti szövegét és ellenőrzi a kritikus YouTube munkamenet kulcsokat."""
        if not text or not text.strip():
            return {
                "has_cookies": False,
                "cookie_count": 0,
                "auth_level": "none",
                "auth_status_label": "Nincsenek sütik",
                "found_auth_tokens": [],
                "missing_auth_tokens": ["SID", "HSID", "SSID", "LOGIN_INFO"],
                "recommendation": "Illessz be vagy tölts fel egy érvényes Netscape formátumú cookies.txt fájlt!",
            }

        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
        names = set()
        for line in lines:
            parts = line.split("\t")
            if len(parts) >= 6:
                names.add(parts[5].strip())

        essential_tokens = ["SID", "HSID", "SSID", "LOGIN_INFO", "SAPISID", "APISID", "__Secure-1PSID", "__Secure-3PSID"]
        found = [tok for tok in essential_tokens if tok in names]
        missing = [tok for tok in ["SID", "LOGIN_INFO", "HSID", "SSID"] if tok not in names]

        # Auth szint meghatározása
        has_primary = ("SID" in names or "__Secure-1PSID" in names or "LOGIN_INFO" in names)
        if has_primary and len(found) >= 2:
            auth_level = "full"
            auth_status_label = "✅ Teljes bejelentkezett munkamenet (429 blokk feloldva)"
            rec = "A sütik tartalmazzák a szükséges hitelesítési tokeneket."
        elif len(lines) > 0:
            auth_level = "partial"
            auth_status_label = "⚠️ Részleges / Hiányos sütik (Csak másodlagos azonosítók)"
            rec = "A sütikből hiányoznak a bejelentkezési tokenek (pl. SID, LOGIN_INFO). A YouTube-on bejelentkezve exportáld a teljes Netscape cookies.txt-t!"
        else:
            auth_level = "none"
            auth_status_label = "Érvénytelen formátum"
            rec = "A megadott szöveg nem tartalmaz érvényes Netscape formátumú sütiket."

        return {
            "has_cookies": len(lines) > 0,
            "cookie_count": len(lines),
            "auth_level": auth_level,
            "auth_status_label": auth_status_label,
            "found_auth_tokens": found,
            "missing_auth_tokens": missing,
            "recommendation": rec,
        }

    def get_status(self) -> Dict[str, Any]:
        """Visszaadja a mentett sütik állapotát és diagnosztikáját."""
        browsers = sorted(list(yt_dlp.cookies.SUPPORTED_BROWSERS)) if hasattr(yt_dlp.cookies, "SUPPORTED_BROWSERS") else []
        if not self.storage_path.exists():
            return {
                "has_cookies": False,
                "path": str(self.storage_path),
                "cookie_count": 0,
                "auth_level": "none",
                "auth_status_label": "Nincsenek mentett sütik",
                "updated_at": None,
                "available_browsers": browsers,
                "found_auth_tokens": [],
                "missing_auth_tokens": ["SID", "LOGIN_INFO"],
            }
        try:
            mtime = datetime.datetime.fromtimestamp(self.storage_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            content = self.storage_path.read_text(encoding="utf-8")
            diag = self.inspect_cookie_content(content)
            diag.update({
                "path": str(self.storage_path),
                "updated_at": mtime,
                "available_browsers": browsers,
            })
            return diag
        except Exception as e:
            return {
                "has_cookies": False,
                "error": str(e),
                "available_browsers": browsers,
            }

    def save_cookie_text(self, text: str) -> bool:
        """Közvetlen szöveges süti tartalom mentése."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(text.strip(), encoding="utf-8")
        return True

    def extract_from_browser(self, browser_name: str) -> Tuple[bool, str]:
        """Sütik kinyerése közvetlenül a felhasználó telepített böngészőjéből."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            jar = yt_dlp.cookies.extract_cookies_from_browser(browser_name)
            jar.save(str(self.storage_path), ignore_discard=True, ignore_expires=True)
            lines = [l for l in self.storage_path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
            if not lines:
                return False, f"A(z) {browser_name.capitalize()} böngészőben nem található bejelentkezett YouTube süti."
            return True, f"Sikeresen kinyerve {len(lines)} db süti a(z) {browser_name.capitalize()} böngészőből!"
        except PermissionError:
            return False, (
                f"A macOS védelmi rendszere blokkolta a(z) {browser_name.capitalize()} sütifájl olvasását (PermissionError). "
                "Megoldás: Használd a 2. pontot (cookies.txt fájl feltöltése vagy beillesztése)!"
            )
        except Exception as e:
            err_msg = str(e)
            if "Operation not permitted" in err_msg or "Permission denied" in err_msg:
                return False, (
                    f"A macOS védelmi rendszere blokkolta a(z) {browser_name.capitalize()} sütifájl olvasását. "
                    "Megoldás: Használd a 2. pontot (cookies.txt fájl feltöltése vagy beillesztése)!"
                )
            return False, f"Nem sikerült a sütik kinyerése ({browser_name}): {err_msg[:140]}"

    def clear(self) -> bool:
        """Mentett sütik törlése."""
        if self.storage_path.exists():
            try:
                self.storage_path.unlink()
                return True
            except Exception:
                return False
        return True

    def get_valid_cookie_file(self) -> Optional[str]:
        """Visszaadja a sütifájl elérési útját, ha létezik és nem üres."""
        if self.storage_path.exists() and self.storage_path.stat().st_size > 0:
            return str(self.storage_path.resolve())
        return None


class YouTubeExtractor:
    """Videók és metaadatok kigyűjtése YouTube URL-ekből (egyedi videó, playlist, csatorna)."""

    def __init__(self, cookies_file: Optional[str] = None, proxy: Optional[str] = None):
        self.cookies_file = cookies_file or CookieManager().get_valid_cookie_file()
        self.proxy = proxy
        self.ydl_opts = {
            "extract_flat": "in_playlist",
            "quiet": True,
            "no_warnings": True,
            "ignore_no_formats_error": True,
            "skip_download": True,
            "nocheckcertificate": True,
            "extractor_args": {
                "youtubetab": {"skip": ["authcheck"]},
            },
        }
        if self.cookies_file and os.path.isfile(self.cookies_file):
            self.ydl_opts["cookiefile"] = self.cookies_file
        if self.proxy:
            self.ydl_opts["proxy"] = self.proxy

    @staticmethod
    def clean_url(url: str) -> str:
        """Tisztítja a YouTube URL-eket, eltávolítva a felesleges tracking paramétereket (?si=...)."""
        url = url.strip()
        if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
            return f"https://www.youtube.com/watch?v={url}"

        # Playlist URL tisztítása
        m_list = re.search(r"list=([a-zA-Z0-9_-]+)", url)
        if "playlist" in url and m_list:
            return f"https://www.youtube.com/playlist?list={m_list.group(1)}"

        m_short = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", url)
        if m_short:
            return f"https://www.youtube.com/watch?v={m_short.group(1)}"

        if "watch?v=" in url:
            m_watch = re.search(r"watch\?v=([a-zA-Z0-9_-]{11})", url)
            if m_watch:
                if m_list:
                    return f"https://www.youtube.com/watch?v={m_watch.group(1)}&list={m_list.group(1)}"
                return f"https://www.youtube.com/watch?v={m_watch.group(1)}"

        return url

    @staticmethod
    def extract_single_video_id(url: str) -> Optional[str]:
        """Kinyeri a 11 karakteres YouTube videó azonosítót tetszőleges URL-ből vagy szövegből."""
        url = url.strip()
        if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
            return url
        m = re.search(r"(?:v=|\/|be\/)([a-zA-Z0-9_-]{11})(?:[&?]|$)", url)
        return m.group(1) if m else None

    def extract_info(self, url: str, limit: Optional[int] = None) -> Tuple[str, List[Dict[str, Any]]]:
        """
        URL elemzése: visszaadja a gyűjtemény nevét (csatorna/lejátszási lista/videó)
        és a feldolgozandó videók metaadat listáját.
        """
        cleaned_url = self.clean_url(url)
        opts = dict(self.ydl_opts)
        if limit and limit > 0:
            opts["playlistend"] = limit

        info = None
        last_error = None

        # 1. Próbálkozás tisztított URL-lel és beállított opciókkal
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(cleaned_url, download=False)
        except Exception as e:
            last_error = e

        # 2. Ha sütikkel hibát dobott, megpróbáljuk sütik nélkül
        if not info and "cookiefile" in opts:
            opts_no_cookies = dict(opts)
            opts_no_cookies.pop("cookiefile", None)
            try:
                with yt_dlp.YoutubeDL(opts_no_cookies) as ydl:
                    info = ydl.extract_info(cleaned_url, download=False)
            except Exception as e:
                last_error = e

        # 3. Ha az eredeti URL-lel sem ment, megpróbáljuk az eredeti nyers URL-t is
        if not info and cleaned_url != url.strip():
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url.strip(), download=False)
            except Exception as e:
                last_error = e

        # 4. Ha yt-dlp nem adott eredményt, de egyetlen videó azonosító felismerhető
        if not info:
            single_id = self.extract_single_video_id(url)
            if single_id:
                return f"YouTube_Video_{single_id}", [{
                    "id": single_id,
                    "title": f"YouTube Videó ({single_id})",
                    "url": f"https://www.youtube.com/watch?v={single_id}",
                    "channel": "YouTube",
                    "duration": None,
                    "description": "",
                }]

            err_detail = f": {last_error}" if last_error else ""
            raise ValueError(f"Nem sikerült beolvasni az információkat a megadott URL-ről ({url}){err_detail}")

        videos: List[Dict[str, Any]] = []
        collection_title = ""

        # Ha lejátszási lista vagy csatorna (több videó bejegyzés)
        if "entries" in info:
            collection_title = (
                info.get("title")
                or info.get("uploader")
                or info.get("channel")
                or "YouTube_Collection"
            )
            for entry in info["entries"]:
                if not entry:
                    continue
                video_id = entry.get("id")
                if not video_id:
                    continue
                title = entry.get("title") or f"video_{video_id}"
                video_url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                if not video_url.startswith("http"):
                    video_url = f"https://www.youtube.com/watch?v={video_id}"

                videos.append({
                    "id": video_id,
                    "title": title,
                    "url": video_url,
                    "channel": entry.get("uploader") or entry.get("channel") or info.get("channel") or "Unknown",
                    "duration": entry.get("duration"),
                    "description": entry.get("description", ""),
                })
        else:
            # Egyetlen videó
            video_id = info.get("id") or self.extract_single_video_id(cleaned_url) or "unknown"
            title = info.get("title") or f"video_{video_id}"
            collection_title = title
            video_url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
            videos.append({
                "id": video_id,
                "title": title,
                "url": video_url,
                "channel": info.get("uploader") or info.get("channel") or "Unknown",
                "duration": info.get("duration"),
                "description": info.get("description", ""),
            })

        return collection_title, videos


class TranscriptFetcher:
    """Átiratok és feliratok kinyerése YouTube videókból sütikkel, proxyval és pontos hibadiagnosztikával."""

    def __init__(
        self,
        preferred_languages: Optional[List[str]] = None,
        cookies_file: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        self.preferred_languages = preferred_languages or ["hu", "en"]
        self.cookies_file = cookies_file or CookieManager().get_valid_cookie_file()
        self.proxy = proxy

        # Saját requests session konfigurálása
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,hu;q=0.8",
        })
        self.session.verify = certifi.where()

        if self.cookies_file and os.path.isfile(self.cookies_file):
            try:
                jar = http.cookiejar.MozillaCookieJar(self.cookies_file)
                jar.load(ignore_discard=True, ignore_expires=True)
                self.session.cookies = jar
            except Exception as e:
                print(f"Figyelmeztetés a sütifájl ({self.cookies_file}) betöltésekor: {e}", file=sys.stderr)

        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        self.api = YouTubeTranscriptApi(http_client=self.session)

    def get_transcript(self, video_id: str, retry_count: int = 0, max_retries: int = 2) -> Dict[str, Any]:
        """
        Lekéri a videó átiratát adaptív exponenciális visszalépéssel és pontos hibadiagnosztikával.
        Megkülönbözteti a valóban hiányzó átiratot a YouTube botvédelmi (HTTP 429 / IpBlocked) korlátozásától.
        """
        result = {
            "status": "error",
            "video_id": video_id,
            "is_generated": False,
            "is_translatable": False,
            "language": None,
            "language_code": None,
            "segments": [],
            "raw_text": "",
            "error_reason": None,
        }

        try:
            transcript_list = self.api.list(video_id)
            selected_transcript = None

            # 1. Keresünk manuálisan feltöltött feliratot a preferált nyelveken
            for lang in self.preferred_languages:
                try:
                    selected_transcript = transcript_list.find_manually_created_transcript([lang])
                    if selected_transcript:
                        break
                except Exception:
                    pass

            # 2. Ha nincs a preferált nyelveken, keresünk BÁRMILYEN manuális feliratot
            if not selected_transcript:
                try:
                    for t in transcript_list._manually_created_transcripts.values():
                        selected_transcript = t
                        break
                except Exception:
                    pass

            # 3. Ha nincs manuális, keresünk automatikusan generált feliratot a preferált nyelveken
            if not selected_transcript:
                for lang in self.preferred_languages:
                    try:
                        selected_transcript = transcript_list.find_generated_transcript([lang])
                        if selected_transcript:
                            break
                    except Exception:
                        pass

            # 4. Ha még mindig nincs, beérjük BÁRMILYEN elérhető (pl. más nyelvű automata) felirattal
            if not selected_transcript:
                try:
                    for t in transcript_list._generated_transcripts.values():
                        selected_transcript = t
                        break
                except Exception:
                    pass

            if not selected_transcript:
                result["status"] = "no_transcript"
                result["error_reason"] = "A videóhoz sem manuális, sem automatikus átirat nem létezik a YouTube-on."
                return result

            # Letöltjük a konkrét átirat adatokat
            fetched = selected_transcript.fetch()

            # Formátum ellenőrzése (FetchedTranscript vagy lista)
            raw_segments = []
            if hasattr(fetched, "to_raw_data"):
                raw_segments = fetched.to_raw_data()
            elif isinstance(fetched, list):
                raw_segments = fetched
            else:
                for item in fetched:
                    if hasattr(item, "text"):
                        raw_segments.append({
                            "text": getattr(item, "text", ""),
                            "start": getattr(item, "start", 0.0),
                            "duration": getattr(item, "duration", 0.0),
                        })
                    elif isinstance(item, dict):
                        raw_segments.append(item)

            # Tisztítás és formázás
            cleaned_segments = []
            all_text_parts = []
            for seg in raw_segments:
                txt = seg.get("text", "").replace("\n", " ").strip()
                if txt:
                    cleaned_segments.append({
                        "start": float(seg.get("start", 0.0)),
                        "duration": float(seg.get("duration", 0.0)),
                        "text": txt,
                    })
                    all_text_parts.append(txt)

            if not cleaned_segments:
                result["status"] = "no_transcript"
                result["error_reason"] = "Az átirat üres szöveget tartalmazott."
                return result

            result["status"] = "success"
            result["is_generated"] = selected_transcript.is_generated
            result["is_translatable"] = selected_transcript.is_translatable
            result["language"] = selected_transcript.language
            result["language_code"] = selected_transcript.language_code
            result["segments"] = cleaned_segments
            result["raw_text"] = " ".join(all_text_parts)
            return result

        except TranscriptsDisabled:
            result["status"] = "no_transcript"
            result["error_reason"] = "Az átiratok / feliratok le vannak tiltva a feltöltő által (TranscriptsDisabled)."
            return result
        except NoTranscriptFound:
            result["status"] = "no_transcript"
            result["error_reason"] = "Nem található felirat a videóhoz (NoTranscriptFound)."
            return result
        except VideoUnavailable:
            result["status"] = "error"
            result["error_reason"] = "A videó nem érhető el vagy privát (VideoUnavailable)."
            return result
        except (CouldNotRetrieveTranscript, Exception) as e:
            err_name = type(e).__name__
            err_msg = str(e)
            is_429 = (
                "blocking requests" in err_msg
                or "429" in err_msg
                or "IpBlocked" in err_name
                or "RequestBlocked" in err_name
                or "TooManyRequests" in err_name
            )

            if is_429:
                if retry_count < max_retries:
                    backoff = (2.5 ** (retry_count + 1)) + random.uniform(1.0, 2.5)
                    time.sleep(backoff)
                    return self.get_transcript(video_id, retry_count=retry_count + 1, max_retries=max_retries)

                result["status"] = "blocked"
                result["error_reason"] = (
                    "YouTube HTTP 429 Blokk: Az IP címedet a YouTube botvédelme ideiglenesen korlátozza. "
                    "Megoldások: (1) Sütikezelőben teljes cookies.txt (SID / LOGIN_INFO) mentése, "
                    "(2) Váltás Mobil Hotspotra vagy router újraindítás az IP megváltoztatásához, "
                    "(3) Proxy / Tor SOCKS5 használata, vagy (4) Futtatás Google Colabból."
                )
            elif "NoTranscriptFound" in err_name or "TranscriptsDisabled" in err_name:
                result["status"] = "no_transcript"
                result["error_reason"] = "A videóhoz nem található átirat a YouTube-on."
            else:
                result["status"] = "error"
                result["error_reason"] = f"Hiba ({err_name}): {err_msg[:120]}"
            return result


class TranscriptTranslator:
    """Átiratok és feliratok automatikus fordítása a kiválasztott célnyelvre."""

    LANGUAGE_NAMES = {
        "hu": "Magyar",
        "en": "Angol",
        "de": "Német",
        "es": "Spanyol",
        "fr": "Francia",
        "it": "Olasz",
        "pt": "Portugál",
        "ru": "Orosz",
        "zh": "Kínai",
        "ja": "Japán",
        "ko": "Koreai",
        "pl": "Lengyel",
        "ro": "Román",
        "nl": "Holland",
        "sv": "Svéd",
        "tr": "Török",
        "uk": "Ukrán",
        "cs": "Cseh",
        "sk": "Szlovák",
        "hr": "Horvát",
        "sr": "Szerb",
    }

    @classmethod
    def get_language_name(cls, code: str) -> str:
        if not code:
            return "Ismeretlen"
        clean = code.lower().split("-")[0].strip()
        return cls.LANGUAGE_NAMES.get(clean, code.upper())

    @classmethod
    def translate_segments(
        cls,
        segments: List[Dict[str, Any]],
        target_lang: str,
        source_lang: str = "auto",
        batch_size: int = 40,
    ) -> List[Dict[str, Any]]:
        """
        Időbélyeges szegmensek kötegelt fordítása a Google Translate motorjával.
        Megőrzi az eredeti kezdési időpontokat (start) és időtartamokat (duration).
        """
        if not segments or not target_lang or target_lang.lower() in ("original", "none", ""):
            return segments

        target_code = target_lang.lower().strip()
        translated_segments = []
        total = len(segments)

        for i in range(0, total, batch_size):
            chunk = segments[i : i + batch_size]
            orig_texts = [s.get("text", "").strip() for s in chunk]
            joined = "\n".join(orig_texts)

            if not joined.strip():
                translated_segments.extend(chunk)
                continue

            try:
                url = "https://translate.googleapis.com/translate_a/single"
                params = {
                    "client": "gtx",
                    "sl": source_lang,
                    "tl": target_code,
                    "dt": "t",
                    "q": joined,
                }
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                }
                r = requests.get(url, params=params, headers=headers, timeout=12.0)
                if r.status_code == 200:
                    data = r.json()
                    full_translated = "".join([item[0] for item in data[0] if item and item[0]])
                    trans_lines = full_translated.split("\n")

                    for idx, orig_seg in enumerate(chunk):
                        if idx < len(trans_lines) and trans_lines[idx].strip():
                            line_txt = trans_lines[idx].strip()
                        else:
                            line_txt = orig_seg.get("text", "")

                        translated_segments.append({
                            "start": orig_seg.get("start", 0.0),
                            "duration": orig_seg.get("duration", 0.0),
                            "text": line_txt,
                        })
                else:
                    translated_segments.extend(chunk)
            except Exception:
                translated_segments.extend(chunk)

        return translated_segments

    @classmethod
    def translate_transcript(cls, transcript_data: Dict[str, Any], target_lang: str) -> Dict[str, Any]:
        """Lefordítja a feliratot és szegmenseit a kívánt célnyelvre."""
        if not target_lang or target_lang.lower() in ("original", "none", ""):
            return transcript_data

        target_code = target_lang.lower().strip()
        current_code = (transcript_data.get("language_code") or "").lower().strip()

        # Ha eleve a célnyelven van
        if current_code == target_code:
            return transcript_data

        orig_lang_name = transcript_data.get("language") or cls.get_language_name(current_code) or "Eredeti"
        target_lang_name = cls.get_language_name(target_code)

        orig_segments = transcript_data.get("segments", [])
        if not orig_segments:
            return transcript_data

        translated_segs = cls.translate_segments(orig_segments, target_code, source_lang=current_code or "auto")
        all_text = " ".join([s["text"] for s in translated_segs])

        res = dict(transcript_data)
        res["is_translated"] = True
        res["original_language"] = orig_lang_name
        res["original_language_code"] = current_code
        res["target_language"] = target_lang_name
        res["target_language_code"] = target_code
        res["language"] = f"{target_lang_name} (Fordítva: {orig_lang_name} ➔ {target_lang_name})"
        res["language_code"] = target_code
        res["segments"] = translated_segs
        res["raw_text"] = all_text

        return res


class TranscriptFormatter:
    """Átiratok formázása szöveges (.txt) és Markdown (.md) kimenethez."""

    @staticmethod
    def format_as_txt(video_info: Dict[str, Any], transcript_data: Dict[str, Any], include_timestamps: bool = False) -> str:
        """Egyszerű szöveges (.txt) fájl formátum."""
        lines = []
        lines.append(f"CÍM: {video_info.get('title', 'N/A')}")
        lines.append(f"URL: {video_info.get('url', 'N/A')}")
        lines.append(f"CSATORNA: {video_info.get('channel', 'N/A')}")
        type_str = "Automatikusan generált" if transcript_data.get("is_generated") else "Manuális felirat"
        
        if transcript_data.get("is_translated"):
            lines.append(f"ÁTIRAT NYELVE: {transcript_data.get('language')} [Célnyelv: {transcript_data.get('target_language', 'N/A')}]")
            lines.append(f"EREDETI NYELV: {transcript_data.get('original_language', 'N/A')} [{transcript_data.get('original_language_code', '')}]")
        else:
            lines.append(f"ÁTIRAT TÍPUSA: {type_str} ({transcript_data.get('language', 'N/A')} [{transcript_data.get('language_code', '')}])")

        lines.append("=" * 60)
        lines.append("")

        if include_timestamps:
            for seg in transcript_data["segments"]:
                ts = format_timestamp(seg["start"])
                lines.append(f"[{ts}] {seg['text']}")
        else:
            lines.append(transcript_data.get("raw_text", ""))

        return "\n".join(lines)

    @staticmethod
    def format_as_markdown(video_info: Dict[str, Any], transcript_data: Dict[str, Any], include_timestamps: bool = True) -> str:
        """Részletes és jól strukturált Markdown (.md) formátum."""
        lines = []
        title = video_info.get("title", "YouTube Videó")
        lines.append(f"# {title}\n")
        lines.append("## Videó Információk")
        lines.append(f"- **URL:** [{video_info.get('url', '')}]({video_info.get('url', '')})")
        lines.append(f"- **Csatorna:** {video_info.get('channel', 'N/A')}")
        lines.append(f"- **Videó azonosító (ID):** `{video_info.get('id', '')}`")
        if video_info.get("duration"):
            lines.append(f"- **Hossz:** {format_timestamp(video_info['duration'])}")

        type_str = "🤖 Automatikusan generált" if transcript_data.get("is_generated") else "✍️ Manuális felirat"
        
        if transcript_data.get("is_translated"):
            lines.append(f"- **Átirat típusa:** {type_str}")
            lines.append(f"- **Fordítás:** 🌐 **{transcript_data.get('target_language')}** (Eredeti nyelv: `{transcript_data.get('original_language')} [{transcript_data.get('original_language_code')}]`)")
            lines.append(f"- **Letöltés és fordítás időpontja:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"\n> [!NOTE]\n> Ez az átirat automatikusan le lett fordítva **{transcript_data.get('target_language')}** nyelvre az eredeti videó feliratából.\n")
        else:
            lang_str = f"{transcript_data.get('language', 'Ismeretlen')} (`{transcript_data.get('language_code', '')}`)"
            lines.append(f"- **Átirat típusa:** {type_str}")
            lines.append(f"- **Nyelv:** {lang_str}")
            lines.append(f"- **Letöltés időpontja:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        lines.append("\n---\n")
        lines.append("## Átirat (Transcript)\n")
        if include_timestamps:
            for seg in transcript_data["segments"]:
                ts = format_timestamp(seg["start"])
                lines.append(f"- **`[{ts}]`** {seg['text']}")
        else:
            lines.append(transcript_data.get("raw_text", ""))

        lines.append("\n")
        return "\n".join(lines)


class DownloaderEngine:
    """A teljes letöltési folyamatot vezérlő motor sebességkorlátozással, fordítással és jelentéskészítéssel."""

    def __init__(
        self,
        output_dir: str = "transcripts_output",
        output_format: str = "both",  # 'txt', 'md', 'both'
        delay_range: Tuple[float, float] = (2.0, 3.0),
        preferred_languages: Optional[List[str]] = None,
        target_language: Optional[str] = None,
        include_timestamps: bool = True,
        limit: Optional[int] = None,
        cookies_file: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_format = output_format
        self.delay_min, self.delay_max = delay_range
        self.preferred_languages = preferred_languages or ["hu", "en"]
        self.target_language = target_language
        self.include_timestamps = include_timestamps
        self.limit = limit
        self.cookies_file = cookies_file or CookieManager().get_valid_cookie_file()
        self.proxy = proxy

        self.extractor = YouTubeExtractor(cookies_file=self.cookies_file, proxy=self.proxy)
        self.fetcher = TranscriptFetcher(
            preferred_languages=self.preferred_languages,
            cookies_file=self.cookies_file,
            proxy=self.proxy,
        )

    def run(self, url: str) -> Dict[str, Any]:
        """Feldolgozza a megadott URL-t."""
        print(f"\n🔍 Információk beolvasása az URL-ről: {url}...")
        try:
            collection_title, videos = self.extractor.extract_info(url, limit=self.limit)
        except Exception as e:
            print(f"❌ Hiba a videóinformációk kinyerése közben: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}

        if not videos:
            print("⚠️ Nem található letölthető videó a megadott hivatkozáson.")
            return {"success": False, "error": "No videos found"}

        if self.limit and self.limit > 0:
            videos = videos[:self.limit]

        total_videos = len(videos)
        sanitized_col_title = sanitize_filename(collection_title)

        # Mappa létrehozása
        target_folder = self.output_dir / sanitized_col_title
        target_folder.mkdir(parents=True, exist_ok=True)

        print(f"🎯 Gyűjtemény neve: {collection_title}")
        print(f"📊 Összesen feldolgozandó videó: {total_videos} db")
        print(f"📁 Mentési mappa: {target_folder.resolve()}")
        print(f"⏱️  Késleltetés kérések között: {self.delay_min:.1f} - {self.delay_max:.1f} mp")
        if self.target_language and self.target_language.lower() not in ("original", "none", ""):
            print(f"🌐 Célnyelv / Fordítás: {TranscriptTranslator.get_language_name(self.target_language)} [{self.target_language}]")
        print()

        results_summary = {
            "collection_title": collection_title,
            "url": url,
            "processed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_videos": total_videos,
            "target_language": self.target_language,
            "successful_count": 0,
            "no_transcript_count": 0,
            "blocked_count": 0,
            "error_count": 0,
            "zip_downloaded": False,
            "zip_downloaded_at": None,
            "zip_download_count": 0,
            "original_params": {
                "url": url,
                "format": self.output_format,
                "languages": ",".join(self.preferred_languages),
                "target_language": self.target_language,
                "delay_min": self.delay_min,
                "delay_max": self.delay_max,
                "include_timestamps": self.include_timestamps,
                "limit": self.limit,
            },
            "successful_videos": [],
            "no_transcript_videos": [],
            "blocked_videos": [],
            "error_videos": [],
        }

        # Szekvenciális feldolgozás
        for index, video in enumerate(videos, start=1):
            vid_id = video["id"]
            vid_title = video.get("title", f"video_{vid_id}")
            safe_title = sanitize_filename(vid_title)
            filename_base = f"{index:03d} - {safe_title} [{vid_id}]"

            status_prefix = f"[{index}/{total_videos}] {vid_title[:45]}..."
            print(f"⏳ {status_prefix}", end="\r", flush=True)

            # Átirat lekérése
            transcript_res = self.fetcher.get_transcript(vid_id)

            if transcript_res["status"] == "success":
                # Fájlok mentése
                saved_files = []
                if self.output_format in ("txt", "both"):
                    txt_path = target_folder / f"{filename_base}.txt"
                    txt_content = TranscriptFormatter.format_as_txt(
                        video, transcript_res, include_timestamps=self.include_timestamps
                    )
                    txt_path.write_text(txt_content, encoding="utf-8")
                    saved_files.append(str(txt_path))

                if self.output_format in ("md", "both"):
                    md_path = target_folder / f"{filename_base}.md"
                    md_content = TranscriptFormatter.format_as_markdown(
                        video, transcript_res, include_timestamps=self.include_timestamps
                    )
                    md_path.write_text(md_content, encoding="utf-8")
                    saved_files.append(str(md_path))

                type_label = "Auto" if transcript_res.get("is_generated") else "Kézi"
                lang_code = transcript_res.get("language_code", "?")
                print(f"✅ [{index}/{total_videos}] SIKER ({type_label}/{lang_code}): {vid_title[:50]}")
                results_summary["successful_count"] += 1
                results_summary["successful_videos"].append({
                    "index": index,
                    "id": vid_id,
                    "title": vid_title,
                    "url": video["url"],
                    "language": transcript_res.get("language"),
                    "is_generated": transcript_res.get("is_generated"),
                    "files": saved_files,
                })

            elif transcript_res["status"] == "blocked":
                reason = transcript_res.get("error_reason", "YouTube botvédelem (HTTP 429)")
                print(f"🚫 [{index}/{total_videos}] YOUTUBE BLOKKOLÁS (429): {vid_title[:40]}...")
                results_summary["blocked_count"] += 1
                results_summary["blocked_videos"].append({
                    "index": index,
                    "id": vid_id,
                    "title": vid_title,
                    "url": video["url"],
                    "reason": reason,
                })

            elif transcript_res["status"] == "no_transcript":
                reason = transcript_res.get("error_reason", "Nincs elérhető átirat")
                print(f"⚠️  [{index}/{total_videos}] NINCS ÁTIRAT: {vid_title[:45]} ({reason[:30]}...)")
                results_summary["no_transcript_count"] += 1
                results_summary["no_transcript_videos"].append({
                    "index": index,
                    "id": vid_id,
                    "title": vid_title,
                    "url": video["url"],
                    "reason": reason,
                })

            else:
                reason = transcript_res.get("error_reason", "Ismeretlen hiba")
                print(f"❌ [{index}/{total_videos}] HIBA: {vid_title[:45]} ({reason[:30]}...)")
                results_summary["error_count"] += 1
                results_summary["error_videos"].append({
                    "index": index,
                    "id": vid_id,
                    "title": vid_title,
                    "url": video["url"],
                    "reason": reason,
                })

            # Késleltetés a következő kérés előtt (kivéve az utolsó videó után)
            if index < total_videos:
                sleep_time = random.uniform(self.delay_min, self.delay_max)
                time.sleep(sleep_time)

        # Összefoglaló jelentés mentése
        self._save_summary_reports(target_folder, results_summary)
        self._print_console_summary(results_summary, target_folder)

        return results_summary

    def _save_summary_reports(self, target_folder: Path, summary: Dict[str, Any]) -> None:
        """Mentés summary.md és summary.json fájlokba."""
        json_path = target_folder / "summary.json"
        json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        md_lines = []
        md_lines.append(f"# Átirat Letöltési Összefoglaló: {summary['collection_title']}\n")
        md_lines.append(f"- **Forrás URL:** [{summary['url']}]({summary['url']})")
        md_lines.append(f"- **Feldolgozás ideje:** {summary['processed_at']}")
        md_lines.append(f"- **Összes videó:** {summary['total_videos']} db")
        md_lines.append(f"- **Sikeresen letöltve:** {summary['successful_count']} db ✅")
        if summary.get("blocked_count", 0) > 0:
            md_lines.append(f"- **YouTube által blokkolva (HTTP 429):** {summary['blocked_count']} db 🚫")
        md_lines.append(f"- **Nincs átirat:** {summary['no_transcript_count']} db ⚠️")
        md_lines.append(f"- **Hibák:** {summary['error_count']} db ❌\n")

        # YouTube által blokkolt videók
        if summary.get("blocked_videos"):
            md_lines.append("## 🚫 YouTube Botvédelem által BLOKKOLT videók (HTTP 429)\n")
            md_lines.append("> [!WARNING]")
            md_lines.append("> Ezekhez a videókhoz **létezik felirat**, de a YouTube szervere ideiglenesen korlátozta a letöltést az IP címedről.")
            md_lines.append("> **Megoldás:** Tölts be egy `cookies.txt` fájlt a beállításokban vagy használj proxy-t / hosszabb szünetet a kérések között.\n")
            md_lines.append("| # | Cím | URL | Ok / Megoldás |")
            md_lines.append("|---|---|---|---|")
            for item in summary["blocked_videos"]:
                clean_t = item["title"].replace("|", "-")
                md_lines.append(f"| {item['index']} | {clean_t} | [{item['id']}]({item['url']}) | {item['reason']} |")
            md_lines.append("\n")

        # Hiányzó átiratok listája
        if summary["no_transcript_videos"]:
            md_lines.append("## ⚠️ Videók, amelyeknél VALÓBAN NEM volt elérhető átirat\n")
            md_lines.append("| # | Cím | URL | Ok |")
            md_lines.append("|---|---|---|---|")
            for item in summary["no_transcript_videos"]:
                clean_t = item["title"].replace("|", "-")
                md_lines.append(f"| {item['index']} | {clean_t} | [{item['id']}]({item['url']}) | {item['reason']} |")
            md_lines.append("\n")

        # Hibák listája
        if summary["error_videos"]:
            md_lines.append("## ❌ Hibára futott videók\n")
            md_lines.append("| # | Cím | URL | Hibaüzenet |")
            md_lines.append("|---|---|---|---|")
            for item in summary["error_videos"]:
                clean_t = item["title"].replace("|", "-")
                md_lines.append(f"| {item['index']} | {clean_t} | [{item['id']}]({item['url']}) | {item['reason']} |")
            md_lines.append("\n")

        # Sikeres videók listája
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

    def _print_console_summary(self, summary: Dict[str, Any], target_folder: Path) -> None:
        """Konzolos vizuális összefoglaló megjelenítése."""
        if HAS_RICH and console:
            console.print()
            table = Table(title="📊 Feldolgozási Összegzés", show_header=True, header_style="bold magenta")
            table.add_column("Kategória", style="bold")
            table.add_column("Darabszám", justify="right")
            table.add_column("Arány", justify="right")

            total = summary["total_videos"]
            succ = summary["successful_count"]
            blocked = summary.get("blocked_count", 0)
            no_tr = summary["no_transcript_count"]
            err = summary["error_count"]

            table.add_row("✅ Sikeres", str(succ), f"{(succ/total*100):.1f}%" if total else "0%")
            if blocked > 0:
                table.add_row("🚫 YouTube Blokkolás (429)", str(blocked), f"{(blocked/total*100):.1f}%" if total else "0%", style="bold red")
            table.add_row("⚠️  Nincs átirat", str(no_tr), f"{(no_tr/total*100):.1f}%" if total else "0%")
            table.add_row("❌ Hibás", str(err), f"{(err/total*100):.1f}%" if total else "0%")
            table.add_row("ÖSSZESEN", str(total), "100%", style="bold")

            console.print(table)

            if blocked > 0:
                console.print(Panel.fit(
                    "[bold red]🚫 Figyelem: A YouTube szervere robotvédelmi okokból (HTTP 429) korlátozta a letöltést.[/bold red]\n"
                    "[yellow]A felirat létezik a videóknál, de a YouTube bejelentkezési sütiket (cookies.txt) vagy hosszabb késleltetést igényel.[/yellow]\n"
                    "[dim]Tipp: Használd a --cookies kapcsolót egy böngészőből exportált cookies.txt fájllal vagy a CookieManager-t![/dim]",
                    title="💡 Megoldási javaslat",
                    border_style="red"
                ))

            if summary["no_transcript_videos"]:
                console.print("\n[bold yellow]⚠️  Valóban átirat nélküli videók:[/bold yellow]")
                for item in summary["no_transcript_videos"]:
                    console.print(f"  • [bold]#{item['index']}[/bold] {item['title']} - [cyan]{item['url']}[/cyan]")
                    console.print(f"    [dim]Ok: {item['reason']}[/dim]")

            console.print(f"\n📂 [bold green]A fájlok és a részletes jelentés megtalálható:[/bold green] {target_folder.resolve()}")
            console.print(f"📄 Jelentés: [cyan]{(target_folder / 'summary.md').resolve()}[/cyan]\n")
        else:
            print("\n" + "=" * 60)
            print("📊 FELDOLGOZÁSI ÖSSZEGZÉS")
            print("=" * 60)
            print(f"Összes videó:     {summary['total_videos']}")
            print(f"Sikeres átirat:   {summary['successful_count']}")
            if summary.get("target_language"):
                print(f"Célnyelv:         {TranscriptTranslator.get_language_name(summary['target_language'])} [{summary['target_language']}]")
            print(f"YouTube Blokk 429:{summary.get('blocked_count', 0)}")
            print(f"Nincs átirat:     {summary['no_transcript_count']}")
            print(f"Hibás videó:      {summary['error_count']}")
            print(f"Mentés helye:     {target_folder.resolve()}")
            print("=" * 60 + "\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="YouTube Átirat Letöltő Pro — Teljes csatornák, lejátszási listák és videók átiratainak letöltése és fordítása."
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="YouTube hivatkozás (egyedi videó, lejátszási lista vagy @csatorna URL)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="transcripts_output",
        help="Kimeneti könyvtár elérési útja (alapértelmezett: transcripts_output)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["txt", "md", "both"],
        default="both",
        help="Kimeneti fájlformátum: txt, md, vagy both (alapértelmezett: both)",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=None,
        help="Feldolgozandó videók maximális száma (alapértelmezett: összes)",
    )
    parser.add_argument(
        "-l",
        "--languages",
        default="hu,en",
        help="Preferált nyelvkódok vesszővel elválasztva (pl. 'hu,en', alapértelmezett: hu,en)",
    )
    parser.add_argument(
        "-t",
        "--target-lang",
        "--translate-to",
        dest="target_lang",
        default=None,
        help="Átirat automatikus lefordítása a megadott célnyelvre (pl. hu, en, de, es, fr, it)",
    )
    parser.add_argument(
        "--delay",
        default="2.0-3.0",
        help="Kérések közötti késleltetési másodperctartomány (pl. '2.0-3.0', alapértelmezett: 2.0-3.0 mp)",
    )
    parser.add_argument(
        "--no-timestamps",
        action="store_true",
        help="Időbélyegek elhagyása a kimeneti fájlokból",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="Netscape formátumú cookies.txt fájl elérési útja",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Sütik automatikus kinyerése böngészőből (chrome, firefox, safari, edge, brave, opera, vivaldi)",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Opcionális HTTP/HTTPS/SOCKS5 proxy szerver (pl. http://user:pass@host:port)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Ha böngészőből kér sütiket, mentse el
    if args.cookies_from_browser:
        cm = CookieManager(args.cookies)
        success, msg = cm.extract_from_browser(args.cookies_from_browser)
        print(msg)
        if not success:
            sys.exit(1)

    if not args.url:
        print("Hiba: A YouTube URL megadása kötelező! (Használd a --help kapcsolót)", file=sys.stderr)
        sys.exit(1)

    # Késleltetés tartomány feldolgozása
    delay_min, delay_max = 2.0, 3.0
    if "-" in args.delay:
        try:
            parts = args.delay.split("-")
            delay_min = float(parts[0])
            delay_max = float(parts[1])
        except ValueError:
            print(f"⚠️ Érvénytelen késleltetés érték '{args.delay}', alapértelmezett 2.0-3.0 mp lesz.", file=sys.stderr)
    else:
        try:
            val = float(args.delay)
            delay_min, delay_max = val, val
        except ValueError:
            pass

    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]

    engine = DownloaderEngine(
        output_dir=args.output_dir,
        output_format=args.format,
        delay_range=(delay_min, delay_max),
        preferred_languages=languages,
        target_language=args.target_lang,
        include_timestamps=not args.no_timestamps,
        limit=args.limit,
        cookies_file=args.cookies,
        proxy=args.proxy,
    )

    try:
        engine.run(args.url)
    except KeyboardInterrupt:
        print("\n\n🛑 A folyamat a felhasználó által megszakítva.")
        sys.exit(0)


if __name__ == "__main__":
    main()
