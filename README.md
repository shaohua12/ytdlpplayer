# DLPPlayer — yt-dlp video downloader for Windows

A small Python app that uses yt-dlp and ffmpeg to download videos from sites
supported by yt-dlp and open video links in mpv.
Its drag-and-drop window uses the `tkinterdnd2` package.

Install the GUI dependency once:

```powershell
python -m pip install -r .\requirements.txt
```

## GUI

Launch the drag-and-drop window with either command:

```powershell
python .\dlpplayer.py
python .\dlpplayer.py gui
```

With no arguments, the program opens the GUI. Drop a video link or the URL from
the browser address bar onto the window, or drop local video files and playlist
files. Local video files open directly in mpv; multiple files play in order.
M3U/M3U8 and supported URL playlists start at episode 1 and continue in order.
The episode dropdown is shown when items can be enumerated. Selecting an episode
switches the current MPV player to it and continues through the remaining list.
YouTube playlists start in mpv while episode details load in the background
when available. `RD...` radio mixes are fetched ten items at a time; each batch
plays in order, then the app loads the next until **Stop** is pressed. These
dynamic mixes have no fixed last item. Closing mpv ends the current batch and
continues with the next; use **Stop** in DLPPlayer to end the mix.
When yt-dlp provides titles, mpv's playlist displays them instead of raw watch
URLs, including items that have not started playing yet.

Episode rows show source titles and runtimes when available. Local HLS runtimes
are read from completed stream manifests. Multi-part Bilibili videos use each
page's per-part title and duration in the episode list and MPV playlist.

The GUI accepts HTTP(S) URLs supported by yt-dlp. You can also paste a link and
press **Play in mpv**.

Single-video URLs play with the same button.

The window title prompts "Drop a video, playlist, or link."
The information panel shows the video title prominently and hides when mpv exits.
The status bar shows a black play triangle while the mpv process runs and a solid
black square after it stops.
The panel shows up to 100 available URL comments in descending like-count
order. Bilibili and YouTube retrieval are capped at 100; other sites are not
forced to fetch comments. The comments box is omitted when none are available.
The window starts 20 pixels from the desktop's top and right edges, shrinks to a
compact idle height, and grows to fit the panel during playback.
URL details include resolution, duration, format, codecs, frame rate, size,
uploader, date, counts, categories, tags, and a short description. Local files
show available format, codec, frame rate, and size details. For multiple local
files, it describes the first file; URL metadata comes from yt-dlp and local
metadata from ffprobe.

## Command-line usage

```powershell
python .\dlpplayer.py download <URL> [options]
python .\dlpplayer.py play <FILE> [--mpv PATH]
```

`download` accepts one HTTP(S) video URL supported by yt-dlp. It downloads into
the directory containing `dlpplayer.py` by default, names the file using its title
and video ID, and merges the output into MP4. Use `--play` to open the completed
download in mpv. `-o`/`--output-dir` selects another download directory. The
download command handles one video at a time; use the GUI for playlists.

```powershell
python .\dlpplayer.py download "https://www.bilibili.com/video/BV..." --play
python .\dlpplayer.py download "https://www.youtube.com/watch?v=..." -o "D:\Videos" -f "bv*[height<=480]+ba/b"
python .\dlpplayer.py play "D:\Videos\video.mp4"
```

Download options:

- `-o`, `--output-dir DIR`: download directory; defaults to the directory
  containing `dlpplayer.py`.
- `-f`, `--format SELECTOR`: yt-dlp format selector; defaults to the best
  video up to 1080p plus audio.
- `--play`: play the downloaded file with mpv after the download succeeds.
- `--firefox-profile NAME_OR_PATH`: use the selected Firefox profile. By
  default yt-dlp reads cookies from Firefox's default profile.
- `--no-cookies`: do not read Firefox cookies.
- `--yt-dlp PATH`, `--ffmpeg PATH`, `--mpv PATH`: override tool locations;
  `--ffmpeg` may be an executable or its containing directory.
- `--node PATH`: Node.js executable for YouTube JavaScript challenge support.

The default tool locations are `D:\bin\yt-dlp\yt-dlp.exe`,
`D:\bin\ffmpeg\bin`, `D:\bin\mpv\mpv.exe`, and
`D:\bin\nodejs\node.exe` (YouTube). Set `DLPPLAYER_YTDLP`,
`DLPPLAYER_FFMPEG`, `DLPPLAYER_MPV`, or `DLPPLAYER_NODE` to override the
corresponding default; command-line paths take precedence.

Firefox cookies are passed directly to yt-dlp; this project does not export,
copy, or store the cookie database. Keep the Firefox profile private and do not
share it.

Only download videos you own or are authorized to save.

## Tests

```powershell
python -m unittest -v
```
