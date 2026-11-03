# DLPPlayer — Bilibili and YouTube downloader for Windows

A small Python app that uses the installed `yt-dlp` extractor and `ffmpeg` to
download Bilibili or YouTube videos and open video links in mpv.
Its drag-and-drop window uses the `tkinterdnd2` package.

Install the GUI dependency once:

```powershell
python -m pip install -r .\requirements.txt
```

Then run either command to open the drag-and-drop window:

```powershell
python .\dlpplayer.py
python .\dlpplayer.py gui
```

Drag a video link or the URL from the browser address bar onto the window, or
drop local video files and playlist files from disk. Dropped local files open
directly in mpv; if you drop multiple files, mpv plays them in order. The
window accepts HTTP(S) URLs for any site supported by yt-dlp; yt-dlp handles
site extraction when mpv opens the link. You can also paste a link and press
**Play in mpv**. The existing command-line subcommands remain available.

The window title prompts "Drop a video, playlist, or link." The information
panel shows the video title prominently and hides when mpv exits.
The panel shows up to 100 available URL comments in descending like-count
order. Bilibili and YouTube retrieval are capped at 100; other sites are not
forced to fetch comments. The comments box is omitted when none are available.
The window starts at the desktop's top-right corner, shrinks to a compact idle height,
and grows to fit the panel during playback.
URL details include resolution, duration, format, codecs, frame rate, size,
uploader, date, counts, categories, tags, and a short description. Local files
show available format, codec, frame rate, and size details. For multiple local
files, it describes the first file; URL metadata comes from yt-dlp and local
metadata from ffprobe.

## Download and play

```powershell
python .\dlpplayer.py download "https://www.bilibili.com/video/BV..." --play
python .\dlpplayer.py download "https://www.youtube.com/watch?v=..." --play
```

By default the program asks yt-dlp to read Firefox's default-profile cookies,
saves the video beside `dlpplayer.py` in the project workspace, and uses these
installations:

- `D:\bin\yt-dlp\yt-dlp.exe`
- `D:\bin\ffmpeg\bin`
- `D:\bin\mpv\mpv.exe`
- `D:\bin\nodejs\node.exe` (YouTube JavaScript challenge support)

Downloads and mpv playback default to video formats no higher than
1080p. Use `-f` to select a lower resolution when downloading.
Firefox cookies are passed directly to yt-dlp using its
`--cookies-from-browser firefox` option. This project does not export, copy, or
store the cookie database. Firefox profile names or paths can be selected with
`--firefox-profile`; use `--no-cookies` for videos that do not need login.
For YouTube, yt-dlp is configured to use Node.js for its JavaScript challenge
support. The default Node.js path is `D:\bin\nodejs\node.exe`; override it with
`--node` or the `DLPPLAYER_NODE` environment variable. A format selector can be
passed with `-f`, for example: `-f "bv*[height<=480]+ba/b"`.

Examples:

```powershell
python .\dlpplayer.py download "https://www.bilibili.com/video/BV..." -o "D:\Videos\Bili"
python .\dlpplayer.py download "https://www.bilibili.com/video/BV..." --firefox-profile "default-release" --play
python .\dlpplayer.py play "D:\Videos\Bili\video.mp4"
```

If the tools are installed elsewhere, pass `--yt-dlp`, `--ffmpeg`, and `--mpv`
paths or set `DLPPLAYER_YTDLP`, `DLPPLAYER_FFMPEG`, and `DLPPLAYER_MPV`.

Only download videos you own or are authorized to save. Browser cookies grant
account access; keep the Firefox profile private and do not share it.

## Tests

```powershell
python -m unittest -v
```
