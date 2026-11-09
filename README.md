# DLPPlayer — yt-dlp downloader/player wrapper for Windows

A small Python app that uses yt-dlp and ffmpeg to download videos from sites
supported by yt-dlp and open video links in mpv.
Its drag-and-drop window uses the `tkinterdnd2` package.

## External tools

`mpv`, `yt-dlp`, FFmpeg, and Node.js are separate executables. Installing the
Python requirements do not install them. Install all four for the full feature
set:

- **mpv** is required to play local files and URLs.
- **yt-dlp** is required for URL playback, URL and playlist details, and downloads.
  mpv also uses yt-dlp's hook to resolve supported video URLs.
- **FFmpeg** is required for downloads, including merging separate audio/video
  streams. Its companion `ffprobe` (usually included in FFmpeg builds) reads
  local-file media details.
- **Node.js** is used as yt-dlp's JavaScript runtime for YouTube when the
  installed yt-dlp supports `--js-runtimes`. DLPPlayer detects support and
  omits the option on older yt-dlp builds; update yt-dlp when YouTube extraction
  needs JavaScript challenge support.

Local-file playback needs only mpv. URL playback needs mpv and yt-dlp. YouTube
playback and downloads use Node.js when yt-dlp supports `--js-runtimes`.
Downloads need yt-dlp and FFmpeg. DLPPlayer checks its configured tool locations
and then `PATH`; see the tool-location settings below to configure paths.

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
Use **Save playlist** beside the episode selector to export the current list as an
M3U/M3U8 file; drop that file into the app later to load it locally.
A DLPPlayer-managed MPV process stays open after normal playback finishes. GUI and
CLI playback reuse that per-user process for subsequent URLs, playlists, and local
media. Before reusing MPV for YouTube, DLPPlayer refreshes the yt-dlp path, format,
browser-cookie, and JavaScript-runtime options over IPC. **Stop** stops normal
playback but leaves MPV ready. An MPV started
independently without DLPPlayer's IPC endpoint cannot be attached. `RD...` radio
mixes use ten-item batches; each batch plays in order, and the next batch loads
when MPV finishes. Use **Stop** to end the mix; closing MPV also ends the current
batch before continuing.
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
Drag-select any part of the title or metadata and press Ctrl+C to copy it.
The status bar starts with a playback icon: a black play triangle while playing,
two pause bars while paused, and a solid black square when stopped or idle.
While a video plays, a progress bar with the playback percentage appears to the
right, followed by the video's total length.
The status text stays on one line and is cut with `...` when it does not fit the
window width.
The panel shows up to 100 available URL comments in descending like-count
order. Bilibili and YouTube retrieval are capped at 100; other sites are not
forced to fetch comments. YouTube details appear before the separate comments
fetch completes.
The comments box is omitted when none are available.
The window starts 20 pixels from the desktop's top and right edges, shrinks to a
compact idle height, and grows to fit the panel during playback.
URL details include resolution, duration, format, codecs, frame rate, size,
uploader, date, counts, categories, tags, and a short description. Local files
show available format, codec, frame rate, and size details. For multiple local
files, it describes the first file; URL metadata comes from yt-dlp and local
metadata from ffprobe.
When video details cannot be read, the information panel shows the error. YouTube
members-only errors explain the browser account requirement.

## Command-line usage

```powershell
python .\dlpplayer.py download <URL> [options]
python .\dlpplayer.py play <FILE> [--mpv PATH]
```

`download` accepts one HTTP(S) video URL supported by yt-dlp. It downloads into
the directory containing `dlpplayer.py` by default, names the file using its title
and video ID, and merges the output into MP4. `--play` hands the completed download
to the shared MPV session. `-o`/`--output-dir` selects another directory. The
command handles one video at a time; use the GUI for playlists.

`play` also sends local files to that session. If no session exists, DLPPlayer
starts MPV in idle mode; GUI and CLI playback reuse it across app invocations.
Commands return after handing media to MPV. `--mpv` is used only when DLPPlayer
needs to start MPV.

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
- `--play`: send the downloaded file to the shared MPV session after download
  succeeds.
- `--cookie-browser NAME`: browser to read cookies from for this download;
  defaults to the `cookie_browser` setting (Chrome).
- `--cookie-profile NAME_OR_PATH`: use this profile for the first cookie
  attempt; the browser's default profile otherwise.
- `--no-cookies`: do not read browser cookies.
- `--yt-dlp PATH`, `--ffmpeg PATH`, `--mpv PATH`: override tool locations;
  `--ffmpeg` may be an executable or its containing directory.
- `--node PATH`: Node.js executable passed to yt-dlp when it supports
  `--js-runtimes`. GUI YouTube actions use `DLPPLAYER_NODE` or `node_path`.

Direct yt-dlp requests try the configured browser first, then retry in Chrome,
Edge, Firefox, and Brave order. MPV cannot retry cookie sources itself, so
DLPPlayer gives it the first usable source instead: the configured browser,
then the same order. A Chromium browser is skipped when its cookie database is
locked by a running browser or uses app-bound encryption (`v20` cookies), which
yt-dlp cannot decrypt. If no source is usable, MPV starts without cookies.

DLPPlayer reads `dlpplayer.json` beside `dlpplayer.py` at startup. The file
sets `max_allowed_resolution` to `1080` by default; change it to another positive
height (for example `720`) to cap yt-dlp selections for playback and downloads.
Set it to `null` to remove the height cap. The download `-f` option still
provides a per-command override.

`cookie_browser` selects the browser yt-dlp and MPV read cookies from; it
defaults to `chrome`. Set it to another yt-dlp browser name such as `edge`,
`firefox`, or `brave`. The CLI `--cookie-browser` option overrides it for one
download, and `--cookie-profile` selects a non-default profile.

Tool locations are configurable with `yt_dlp_path`, `mpv_path`, `ffmpeg_path`,
and `node_path`. Values may be absolute paths or paths relative to
`dlpplayer.json`; set one to `null` to find that tool on `PATH`. Command-line
overrides take precedence over environment variables, which take precedence over
the JSON settings. The default paths are `D:\bin\yt-dlp\yt-dlp.exe`,
`D:\bin\mpv\mpv.exe`, `D:\bin\ffmpeg\bin`, and `D:\bin\nodejs\node.exe`.

`python_library_path` optionally points to a directory containing Python
packages such as `tkinterdnd2`. It may also be relative to `dlpplayer.json`; leave
it `null` to use the current Python environment.

`radio_mix_batch_size` controls how many YouTube radio-mix (`RD...`) episodes are
loaded at a time; it defaults to `68`. Raise it to show more of the mix in the
episode list, or lower it to start playback sooner. The episode dropdown grows
with the list and shows up to 30 rows at once.

For direct yt-dlp calls, cookies from Chrome, Edge, Firefox, or Brave are passed
directly to yt-dlp; this project does not export, copy, or store cookie databases.
Keep browser profiles private and do not share them. On Windows, yt-dlp cannot
copy a Chromium cookie database while its browser is running and reports
"Could not copy Chrome cookie database"; close Chrome (or the selected browser)
before downloading when cookies are required.

Only download videos you own or are authorized to save.

## Tests

```powershell
python -m unittest -v
```
