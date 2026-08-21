# YouTube Átirat Letöltő (YouTube Transcript Downloader Pro)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/krisatrader/YT-text-downloader/blob/main/youtube_transcript_downloader_colab.ipynb)

Professzionális, automatizált megoldás YouTube videók, komplett lejátszási listák és teljes csatornák manuális vagy automatikusan generált átiratainak (feliratainak) betűről betűre történő letöltéséhez `.txt` és `.md` formátumban.

Az alkalmazás rendelkezik **parancssori (CLI)** és **modern böngészős kezelőfelülettel (Web UI)** is, beépített **YouTube sütikezelővel**, **anti-bot sebességkorlátozással**, **letöltöttségi állapotkövetéssel** és **mentett gyűjtemények újrafuttatási központjával**.

---

## ⚡ 1-Kattintásos Futtatás Ingyen (Google Colab)

Nem szeretnél semmit sem telepíteni a gépedre? Futtasd a Google felhőjéből:

👉 **[Kattints ide a Google Colab Notebook megnyitásához](https://colab.research.google.com/github/krisatrader/YT-text-downloader/blob/main/youtube_transcript_downloader_colab.ipynb)**

A Colab Notebookban:
1. Kattints a **1. Előkészítés** cella lejátszás gombjára.
2. Válassz:
   - **2/A**: Elindítja a Webes felületet egy publikus biztonságos linken.
   - **2/B**: Kitöltöd az űrlapot a videó/playlist linkkel, és azonnal automatikusan letölti a kész ZIP csomagot a gépedre!

---

## 🚀 Helyi Indítás (Saját gépen)

### Webes felület (Ajánlott)
```bash
cd /Users/sebestyenkristof/.gemini/antigravity/scratch/youtube_transcript_downloader
./run_web.sh
```
Ezután nyisd meg a böngésződben:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 🍪 Beépített Sütikezelés (HTTP 429 Blokkolás Megelőzése)

1. A webes felületen kattints a **"Sütikezelő megnyitása"** gombra.
2. Válassz egyet a telepített böngészőid közül (pl. **Chrome**, **Firefox**, **Safari**, **Brave**, **Edge**), vagy tölts fel egy `cookies.txt` fájlt.
3. A rendszer elmenti a munkamenetet, és minden jövőbeli letöltésnél automatikusan ezt használja.

Parancssorban:
```bash
# Sütik kinyerése Chrome-ból:
./venv/bin/python transcript_downloader.py --cookies-from-browser chrome

# Vagy meglévő cookies.txt fájl használata:
./venv/bin/python transcript_downloader.py "https://youtube.com/playlist?list=..." --cookies cookies.txt
```

---

## 💻 Parancssori (CLI) Használat

```bash
# Egyetlen videó letöltése:
./venv/bin/python transcript_downloader.py "https://www.youtube.com/watch?v=VIDEÓ_ID"

# Lejátszási lista letöltése:
./venv/bin/python transcript_downloader.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" -o "kimeneti_mappa"

# Teljes csatorna letöltése:
./venv/bin/python transcript_downloader.py "https://www.youtube.com/@CsatornaNev/videos" -o "csatorna_mentes"

# Egyedi késleltetés és nyelvpreferencia:
./venv/bin/python transcript_downloader.py "https://www.youtube.com/@CsatornaNev/videos" -l hu,en --delay 2.5-4.0
```

---

## ⚙️ CLI Kapcsolók Referenciája

| Kapcsoló | Típus | Alapértelmezett | Leírás |
|---|---|---|---|
| `url` | Pozicionális | *(Kötelező)* | A YouTube URL (videó, lejátszási lista vagy csatorna). |
| `-o, --output-dir` | Szöveg | `transcripts_output` | A kimeneti főkönyvtár elérési útja. |
| `-f, --format` | `md` / `txt` / `both` | `both` | Mentési fájlformátum (.md, .txt vagy mindkettő). |
| `-n, --limit` | Egész szám | `None` (összes) | A feldolgozandó videók maximális száma. |
| `-l, --languages` | Vesszővel elválasztott | `hu,en` | Preferált nyelvkódok sorrendje (pl. `hu,en`). |
| `--delay` | Tartomány | `2.0-3.0` | Késleltetési másodperctartomány a kérések között. |
| `--no-timestamps` | Zászló | `False` | Időbélyegek elhagyása a szövegből. |
| `--cookies` | Fájl útvonal | `None` | `cookies.txt` fájl elérési útja. |
| `--cookies-from-browser` | Szöveg | `None` | Böngésző neve (chrome, firefox, safari, brave, edge). |
| `--proxy` | URL | `None` | Opcionális HTTP/HTTPS/SOCKS5 proxy. |
