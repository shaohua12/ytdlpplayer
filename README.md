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
Use **Save playlist** beside the episode selector to export the current list as an
M3U/M3U8 file; drop that file into the app later to load it locally.
A DLPPlayer-managed MPV process stays open after normal playback finishes. GUI and
CLI playback reuse that per-user process for subsequent URLs, playlists, and local
media. **Stop** stops normal playback but leaves MPV ready. An MPV started
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
The status bar shows a black play triangle while the mpv process runs and a solid
black square after it stops.
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
members-only errors explain the Firefox account requirement.

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
- `--firefox-profile NAME_OR_PATH`: use the selected Firefox profile. By
  default yt-dlp reads cookies from Firefox's default profile.
- `--no-cookies`: do not read Firefox cookies.
- `--yt-dlp PATH`, `--ffmpeg PATH`, `--mpv PATH`: override tool locations;
  `--ffmpeg` may be an executable or its containing directory.
- `--node PATH`: Node.js executable for YouTube JavaScript challenge support.

DLPPlayer reads `dlpplayer.json` beside `dlpplayer.py` at startup. The file
sets `max_allowed_resolution` to `1080` by default; change it to another positive
height (for example `720`) to cap yt-dlp selections for playback and downloads.
Set it to `null` to remove the height cap. The download `-f` option still
provides a per-command override.

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

Firefox cookies are passed directly to yt-dlp; this project does not export,
copy, or store the cookie database. Keep the Firefox profile private and do not
share it.

Only download videos you own or are authorized to save.

## Tests

```powershell
python -m unittest -v
```
