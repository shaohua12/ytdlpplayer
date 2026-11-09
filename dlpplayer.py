"""Download videos from yt-dlp-supported sites and optionally play them with mpv."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


YTDLP_DEFAULT = Path(r"D:\bin\yt-dlp\yt-dlp.exe")
MPV_DEFAULT = Path(r"D:\bin\mpv\mpv.exe")
FFMPEG_DEFAULT = Path(r"D:\bin\ffmpeg\bin")
NODE_DEFAULT = Path(r"D:\bin\nodejs\node.exe")
DEFAULT_MAX_ALLOWED_RESOLUTION = 1080
SETTINGS_PATH = Path(__file__).resolve().with_name("dlpplayer.json")
DEFAULT_RADIO_MIX_BATCH_SIZE = 68
YTDLP_SUBTITLE_OPTIONS = (
    ("sub-langs", "en.*"),
    ("write-subs", ""),
    ("write-auto-subs", ""),
)


YOUTUBE_COMMENT_FETCH_LIMIT = 100
YOUTUBE_COMMENT_FETCH_TIMEOUT = 120
DEFAULT_COOKIE_BROWSER = "chrome"
# Ordered by desktop browser market share (StatCounter, October 2026).
YTDLP_COOKIE_BROWSER_ORDER = ("chrome", "edge", "firefox", "brave")
CHROMIUM_USER_DATA_SUBDIRS = {
    "brave": ("BraveSoftware", "Brave-Browser", "User Data"),
    "chrome": ("Google", "Chrome", "User Data"),
    "chromium": ("Chromium", "User Data"),
    "edge": ("Microsoft", "Edge", "User Data"),
}
VIDEO_COMMENT_DISPLAY_LIMIT = 100
BILIBILI_COMMENT_PAGE_SIZE = 20
BILIBILI_COMMENT_TIMEOUT = 5
BILIBILI_VIDEO_METADATA_TIMEOUT = 10
HLS_METADATA_TIMEOUT = 10
IDLE_WINDOW_MIN_HEIGHT = 150
EPISODE_DROPDOWN_MAX_ROWS = 30
OUTPUT_DEFAULT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class AppSettings:
    max_allowed_resolution: int | None = DEFAULT_MAX_ALLOWED_RESOLUTION
    yt_dlp_path: Path | None = YTDLP_DEFAULT
    mpv_path: Path | None = MPV_DEFAULT
    ffmpeg_path: Path | None = FFMPEG_DEFAULT
    node_path: Path | None = NODE_DEFAULT
    python_library_path: Path | None = None
    radio_mix_batch_size: int = DEFAULT_RADIO_MIX_BATCH_SIZE
    cookie_browser: str = DEFAULT_COOKIE_BROWSER

    @property
    def video_format(self) -> str:
        if self.max_allowed_resolution is None:
            return "bv*+ba/b"
        return (
            f"bv*[height<={self.max_allowed_resolution}]+ba/"
            f"b[height<={self.max_allowed_resolution}]"
        )


def _settings_path_value(
    data: dict[str, object],
    key: str,
    default: Path | None,
    settings_path: Path,
) -> Path | None:
    if key not in data:
        return default
    value = data[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        print(
            f"Warning: {key} in {settings_path} must be a path string or null. "
            "Using its default.",
            file=sys.stderr,
        )
        return default
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = settings_path.resolve().parent / candidate
    return candidate


def load_settings(settings_path: Path = SETTINGS_PATH) -> AppSettings:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppSettings()
    except (OSError, json.JSONDecodeError) as error:
        print(
            f"Warning: could not read {settings_path}: {error}. Using default settings.",
            file=sys.stderr,
        )
        return AppSettings()

    if not isinstance(data, dict):
        print(
            f"Warning: {settings_path} must contain a JSON object. Using default settings.",
            file=sys.stderr,
        )
        return AppSettings()

    max_resolution = data.get(
        "max_allowed_resolution", DEFAULT_MAX_ALLOWED_RESOLUTION
    )
    if max_resolution is not None and (
        type(max_resolution) is not int or max_resolution <= 0
    ):
        print(
            f"Warning: max_allowed_resolution in {settings_path} must be a positive integer or null. "
            "Using the default setting.",
            file=sys.stderr,
        )
        max_resolution = DEFAULT_MAX_ALLOWED_RESOLUTION

    radio_mix_batch_size = data.get(
        "radio_mix_batch_size", DEFAULT_RADIO_MIX_BATCH_SIZE
    )
    if type(radio_mix_batch_size) is not int or radio_mix_batch_size <= 0:
        print(
            f"Warning: radio_mix_batch_size in {settings_path} must be a positive integer. "
            "Using the default setting.",
            file=sys.stderr,
        )
        radio_mix_batch_size = DEFAULT_RADIO_MIX_BATCH_SIZE

    cookie_browser = data.get("cookie_browser", DEFAULT_COOKIE_BROWSER)
    if not isinstance(cookie_browser, str) or not cookie_browser.strip():
        print(
            f"Warning: cookie_browser in {settings_path} must be a non-empty string. "
            "Using the default setting.",
            file=sys.stderr,
        )
        cookie_browser = DEFAULT_COOKIE_BROWSER
    else:
        cookie_browser = cookie_browser.strip().casefold()

    return AppSettings(
        max_allowed_resolution=max_resolution,
        radio_mix_batch_size=radio_mix_batch_size,
        cookie_browser=cookie_browser,
        yt_dlp_path=_settings_path_value(data, "yt_dlp_path", YTDLP_DEFAULT, settings_path),
        mpv_path=_settings_path_value(data, "mpv_path", MPV_DEFAULT, settings_path),
        ffmpeg_path=_settings_path_value(data, "ffmpeg_path", FFMPEG_DEFAULT, settings_path),
        node_path=_settings_path_value(data, "node_path", NODE_DEFAULT, settings_path),
        python_library_path=_settings_path_value(
            data, "python_library_path", None, settings_path
        ),
    )


def _add_python_library_path(path: Path | None) -> None:
    if path is None:
        return
    if not path.is_dir():
        print(
            f"Warning: Python library directory was not found: {path}",
            file=sys.stderr,
        )
        return
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


SETTINGS = load_settings()
YTDLP_DEFAULT = SETTINGS.yt_dlp_path
MPV_DEFAULT = SETTINGS.mpv_path
FFMPEG_DEFAULT = SETTINGS.ffmpeg_path
NODE_DEFAULT = SETTINGS.node_path
RADIO_MIX_BATCH_SIZE = SETTINGS.radio_mix_batch_size
COOKIE_BROWSER = SETTINGS.cookie_browser
VIDEO_FORMAT_DEFAULT = SETTINGS.video_format
_add_python_library_path(SETTINGS.python_library_path)


URL_PATTERN = re.compile(r"https?://[^\s{}<>\"']+", re.IGNORECASE)


def is_bilibili_url(url: str) -> bool:
    if not is_http_url(url):
        return False
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return (
        host == "bilibili.com"
        or host.endswith(".bilibili.com")
        or host in {"b23.tv", "bilibili.tv"}
        or host.endswith(".bilibili.tv")
    )


def is_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def is_youtube_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def is_playlist_url_hint(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    query_keys = {key.lower() for key in parse_qs(parsed.query)}
    if query_keys & {"list", "playlist", "playlist_id", "playlistid"}:
        return True
    path = parsed.path.lower()
    return any(marker in path for marker in ("/playlist", "/medialist/", "/series/"))


def _should_inspect_playlist_first(url: str) -> bool:
    return is_bilibili_url(url) or is_playlist_url_hint(url)


def _youtube_playlist_url(url: str) -> str | None:
    if not is_youtube_url(url):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    playlist_ids = parse_qs(parsed.query).get("list")
    if not playlist_ids or not playlist_ids[0]:
        return None
    return urlunsplit(
        (
            "https",
            "www.youtube.com",
            "/playlist",
            urlencode({"list": playlist_ids[0]}),
            "",
        )
    )

def _youtube_video_url(url: str) -> str | None:
    if not is_youtube_url(url):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    video_ids = parse_qs(parsed.query).get("v")
    if not video_ids or not video_ids[0]:
        return None
    return urlunsplit(
        (
            "https",
            "www.youtube.com",
            "/watch",
            urlencode({"v": video_ids[0]}),
            "",
        )
    )


def _video_info_error_message(url: str | None, error: object) -> str:
    message = str(error).strip()
    if (
        url is not None
        and is_youtube_url(url)
        and "members-only content" in message.casefold()
    ):
        return (
            "YouTube reports this video is members-only. Check that the browser "
            "profile used by DLPPlayer is signed in to an account that has joined "
            "the channel."
        )
    if not message:
        return "Video details unavailable."
    return f"Video details unavailable: {message}"



def extract_http_url(text: str) -> str | None:
    for match in URL_PATTERN.finditer(text):
        candidate = match.group(0).rstrip(".,;:!?)]}")
        if is_http_url(candidate):
            return candidate
    return None


def extract_local_paths(
    payload: str, splitlist: Callable[[str], tuple[str, ...]]
) -> tuple[Path, ...]:
    if not payload:
        return ()
    direct_path = Path(payload).expanduser()
    if direct_path.is_file():
        return (direct_path,)
    return tuple(Path(item).expanduser() for item in splitlist(payload) if item)


_YTDLP_JS_RUNTIME_SUPPORT: dict[str, bool] = {}


def _probe_ytdlp_js_runtime_support(yt_dlp: str) -> None:
    if yt_dlp in _YTDLP_JS_RUNTIME_SUPPORT:
        return
    try:
        result = subprocess.run(
            [yt_dlp, "--help"],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return
    help_text = f"{result.stdout or ''}\n{result.stderr or ''}"
    _YTDLP_JS_RUNTIME_SUPPORT[yt_dlp] = "--js-runtimes" in help_text


def _yt_dlp_supports_js_runtimes(yt_dlp: str) -> bool:
    return _YTDLP_JS_RUNTIME_SUPPORT.get(yt_dlp, True)


def _resolved_tool_path(path: str | Path, name: str) -> str:
    resolved = str(path)
    if name == "yt-dlp":
        _probe_ytdlp_js_runtime_support(resolved)
    return resolved


def resolve_tool(
    option: str | None,
    environment_name: str,
    default: Path | None,
    name: str,
    allow_directory: bool = False,
) -> str:
    configured = option or os.environ.get(environment_name)
    if configured:
        candidate = Path(configured)
        if candidate.is_file() or (allow_directory and candidate.is_dir()):
            return _resolved_tool_path(candidate, name)
        located = shutil.which(configured)
        if located:
            return _resolved_tool_path(located, name)
        raise FileNotFoundError(f"{name} was not found at {configured!r}.")

    if default is not None and (
        default.is_file() or (allow_directory and default.is_dir())
    ):
        return _resolved_tool_path(default, name)
    located = shutil.which(name)
    if located:
        return _resolved_tool_path(located, name)
    raise FileNotFoundError(
        f"{name} was not found. Install it or pass its path with --{name}."
    )


def _cookie_source(browser: str, profile: str | None = None) -> str:
    return f"{browser}:{profile}" if profile else browser


def build_download_command(
    yt_dlp: str,
    ffmpeg_location: str,
    output_dir: Path,
    url: str,
    cookie_profile: str | None = None,
    use_cookies: bool = True,
    result_file: str | None = None,
    video_format: str = VIDEO_FORMAT_DEFAULT,
    node_runtime: str | None = None,
    cookie_browser: str = COOKIE_BROWSER,
) -> list[str]:
    command = [
        yt_dlp,
        "--no-playlist",
        "--windows-filenames",
        "--ffmpeg-location",
        ffmpeg_location,
        "--paths",
        str(output_dir),
        "-o",
        "%(title)s [%(id)s].%(ext)s",
        "-f",
        video_format,
        "--merge-output-format",
        "mp4",
    ]
    if is_youtube_url(url) and _yt_dlp_supports_js_runtimes(yt_dlp):
        runtime = f"node:{node_runtime}" if node_runtime else "node"
        command.extend(["--js-runtimes", runtime])
    if use_cookies:
        command.extend(
            [
                "--cookies-from-browser",
                _cookie_source(cookie_browser, cookie_profile),
            ]
        )
    if result_file:
        command.extend(["--print-to-file", "after_move:filepath", result_file])
    command.append(url)
    return command


def _build_mpv_playlist_command(
    mpv: str,
    yt_dlp: str,
    urls: tuple[str, ...],
    node_runtime: str | None = None,
    cookie_profile: str | None = None,
    use_cookies: bool = True,
    playlist_file: str | None = None,
    ipc_server: str | None = None,
    keep_open: bool = True,
    cookie_browser: str = COOKIE_BROWSER,
) -> list[str]:
    if not urls:
        raise ValueError("At least one playlist item is required.")
    command = [
        mpv,
        "--pause=no",
        "--slang=en,en-US,en-GB",
        f"--script-opts=ytdl_hook-ytdl_path={yt_dlp}",
        f"--ytdl-format={VIDEO_FORMAT_DEFAULT}",
    ]
    if keep_open:
        command.extend(["--idle=yes", "--force-window=yes"])
    if ipc_server:
        command.append(f"--input-ipc-server={ipc_server}")
    raw_options = [
        f"{name}={value}" for name, value in YTDLP_SUBTITLE_OPTIONS
    ]
    if use_cookies:
        raw_options.append(
            "cookies-from-browser="
            + _cookie_source(cookie_browser, cookie_profile)
        )
    if any(is_youtube_url(url) for url in urls) and _yt_dlp_supports_js_runtimes(yt_dlp):
        runtime = f"node:{node_runtime}" if node_runtime else "node"
        raw_options.append(f"js-runtimes={runtime}")
    if raw_options:
        command.append(f"--ytdl-raw-options={','.join(raw_options)}")
    if playlist_file is None:
        command.extend(urls)
    else:
        command.append(f"--playlist={playlist_file}")
    return command


def build_mpv_url_command(
    mpv: str,
    yt_dlp: str,
    url: str,
    node_runtime: str | None = None,
    cookie_profile: str | None = None,
    use_cookies: bool = True,
    ipc_server: str | None = None,
    keep_open: bool = True,
    cookie_browser: str = COOKIE_BROWSER,
) -> list[str]:
    return _build_mpv_playlist_command(
        mpv,
        yt_dlp,
        (url,),
        node_runtime,
        cookie_profile,
        use_cookies,
        ipc_server=ipc_server,
        keep_open=keep_open,
        cookie_browser=cookie_browser,
    )

def _mpv_ipc_server_path() -> str:
    home = str(Path.home())
    if os.name == "nt":
        home = home.casefold()
    user_key = uuid.uuid5(uuid.NAMESPACE_URL, home).hex[:16]
    name = f"dlpplayer-{user_key}"
    if os.name == "nt":
        return rf"\\.\pipe\{name}"
    return str(Path(tempfile.gettempdir()) / f"{name}.sock")


def _send_mpv_ipc_command(server: str, command: list[object]) -> bool:
    message = json.dumps(
        {"command": command}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    try:
        if os.name == "nt":
            with open(server, "r+b", buffering=0) as connection:
                return connection.write(message) == len(message)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1.0)
            connection.connect(server)
            connection.sendall(message)
        return True
    except OSError:
        return False


def _send_mpv_ipc_command_retry(
    server: str,
    command: list[object],
    attempts: int = 6,
    retry_delay: float = 0.1,
) -> bool:
    for attempt in range(attempts):
        if _send_mpv_ipc_command(server, command):
            return True
        if attempt + 1 < attempts:
            time.sleep(retry_delay)
    return False


def _load_mpv_content(server: str, command: list[object]) -> bool:
    if not _send_mpv_ipc_command_retry(server, command):
        return False
    return _send_mpv_ipc_command_retry(server, ["set_property", "pause", False])


def _mpv_ipc_request(server: str, command: list[object]) -> dict[str, object] | None:
    request_id = uuid.uuid4().int & ((1 << 63) - 1)
    message = json.dumps(
        {"command": command, "request_id": request_id},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    try:
        if os.name == "nt":
            with open(server, "r+b", buffering=0) as connection:
                connection.write(message)
                while line := connection.readline():
                    response = json.loads(line)
                    if response.get("request_id") == request_id:
                        return response
            return None
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1.0)
            connection.connect(server)
            connection.sendall(message)
            with connection.makefile("rb") as stream:
                while line := stream.readline():
                    response = json.loads(line)
                    if response.get("request_id") == request_id:
                        return response
        return None
    except (OSError, ValueError):
        return None


def _open_mpv_media(media_file: Path, mpv_option: str | None = None) -> int:
    ipc_server = _mpv_ipc_server_path()
    media_path = str(media_file.resolve())
    command = ["loadfile", media_path, "replace"]
    if _load_mpv_content(ipc_server, command):
        return 0

    mpv = resolve_tool(mpv_option, "DLPPLAYER_MPV", MPV_DEFAULT, "mpv")
    launch_command = [
        mpv,
        "--pause=no",
        "--idle=yes",
        "--force-window=yes",
        f"--input-ipc-server={ipc_server}",
        media_path,
    ]
    try:
        subprocess.Popen(launch_command)
    except OSError:
        if _load_mpv_content(ipc_server, command):
            return 0
        raise
    return 0


def _mpv_property_value(server: str, property_name: str) -> object | None:
    response = _mpv_ipc_request(server, ["get_property", property_name])
    if response is None or response.get("error") != "success":
        return None
    return response.get("data")


def _chromium_user_data_root(browser: str) -> Path | None:
    if os.name != "nt":
        return None
    parts = CHROMIUM_USER_DATA_SUBDIRS.get(browser)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not parts or not local_app_data:
        return None
    return Path(local_app_data).joinpath(*parts)


def _chromium_cookie_database(root: Path) -> Path | None:
    for profile in (root / "Default", *_sorted_profiles(root)):
        database = profile / "Network" / "Cookies"
        if database.is_file():
            return database
    return None


def _sorted_profiles(root: Path) -> list[Path]:
    try:
        return sorted(
            path for path in root.glob("Profile *") if path.is_dir()
        )
    except OSError:
        return []


def _chromium_uses_app_bound_encryption(root: Path) -> bool:
    # yt-dlp cannot decrypt cookies using Chromium's app-bound encryption,
    # which stores v20 cookies (yt-dlp issue 10927).
    try:
        data = json.loads((root / "Local State").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    os_crypt = data.get("os_crypt") if isinstance(data, dict) else None
    return isinstance(os_crypt, dict) and "app_bound_encrypted_key" in os_crypt


def _cookie_browser_usable(browser: str) -> bool:
    name = browser.split("+", 1)[0].split(":", 1)[0].casefold()
    if os.name != "nt":
        return True
    if name == "firefox":
        root = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "Profiles"
        try:
            return any(
                (profile / "cookies.sqlite").is_file()
                for profile in root.glob("*")
            )
        except OSError:
            return False

    root = _chromium_user_data_root(name)
    if root is None:
        return True
    database = _chromium_cookie_database(root)
    if database is None or _chromium_uses_app_bound_encryption(root):
        return False
    try:
        # Chromium locks the cookie database exclusively while it runs.
        with database.open("rb"):
            pass
    except OSError:
        return False
    return True


def _usable_cookie_browser(preferred: str = COOKIE_BROWSER) -> str | None:
    candidates = [preferred]
    candidates.extend(
        browser
        for browser in YTDLP_COOKIE_BROWSER_ORDER
        if browser.casefold() != preferred.split(":", 1)[0].casefold()
    )
    for browser in candidates:
        if _cookie_browser_usable(browser):
            return browser
    return None


def _configure_mpv_for_youtube(
    server: str,
    yt_dlp: str | None,
    node_runtime: str | None,
    cookie_browser: str | None = COOKIE_BROWSER,
) -> bool:
    if not yt_dlp or not node_runtime:
        return False

    script_options = _mpv_property_value(server, "options/script-opts")
    script_options = dict(script_options) if isinstance(script_options, dict) else {}
    script_options["ytdl_hook-ytdl_path"] = yt_dlp

    raw_options = _mpv_property_value(server, "options/ytdl-raw-options")
    raw_options = dict(raw_options) if isinstance(raw_options, dict) else {}
    raw_options.pop("en,write-subs", None)
    if cookie_browser:
        raw_options["cookies-from-browser"] = cookie_browser
    else:
        raw_options.pop("cookies-from-browser", None)
    raw_options.pop("js-runtimes", None)
    if _yt_dlp_supports_js_runtimes(yt_dlp):
        raw_options["js-runtimes"] = f"node:{node_runtime}"
    raw_options.update(YTDLP_SUBTITLE_OPTIONS)

    for property_name, value in (
        ("options/script-opts", script_options),
        ("options/slang", ["en", "en-US", "en-GB"]),
        ("options/ytdl-format", VIDEO_FORMAT_DEFAULT),
        ("options/ytdl-raw-options", raw_options),
    ):
        response = _mpv_ipc_request(
            server, ["set_property", property_name, value]
        )
        if response is None or response.get("error") != "success":
            return False
    return True


def _mpv_seconds(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _format_mpv_time(value: object) -> str | None:
    seconds = _mpv_seconds(value)
    if seconds is None:
        return None
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds_part:02d}"
    return f"{minutes}:{seconds_part:02d}"


def _format_mpv_progress(
    time_position: object, duration: object, paused: bool = False
) -> str:
    state = "Paused" if paused else "Playing"
    position_text = _format_mpv_time(time_position)
    if position_text is None:
        return f"{state} | loading media..."

    duration_text = _format_mpv_time(duration)
    duration_seconds = _mpv_seconds(duration)
    position_seconds = _mpv_seconds(time_position)
    if duration_text is None or duration_seconds is None or duration_seconds == 0:
        return f"{state} | {position_text}"

    progress = round(min(position_seconds / duration_seconds, 1) * 100)
    return f"{state} | {position_text} / {duration_text} ({progress}%)"


def _meaningful_mpv_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    title = " ".join(value.split())
    if not title or title.casefold() in {"mpv", "unknown"} or is_http_url(title):
        return None
    return title[:100].rstrip()


def _mpv_status_text(server: str, fallback_title: str | None = None) -> str | None:
    idle_active = _mpv_property_value(server, "idle-active")
    if idle_active is True:
        return "MPV is ready."
    if idle_active is None:
        # MPV can be starting or stopping while its IPC endpoint is available.
        time_position = _mpv_property_value(server, "time-pos")
        duration = _mpv_property_value(server, "duration")
        if time_position is None and duration is None:
            return None
    else:
        time_position = _mpv_property_value(server, "time-pos")
        duration = _mpv_property_value(server, "duration")
    paused = _mpv_property_value(server, "pause") is True
    title = _meaningful_mpv_title(
        _mpv_property_value(server, "media-title")
    ) or _meaningful_mpv_title(fallback_title)
    progress = _format_mpv_progress(time_position, duration, paused)
    return f"{title} | {progress}" if title else progress


def _playback_progress_display(status: str) -> tuple[str, int] | None:
    percent_start = status.rfind(" (")
    if percent_start < 0 or not status.endswith("%)"):
        return None
    percent_text = status[percent_start + 2 : -2]
    if not percent_text.isdecimal():
        return None
    percentage = int(percent_text)
    if not 0 <= percentage <= 100:
        return None

    playback_status = status[:percent_start]
    separator = playback_status.rfind(" | ")
    if separator < 0 or " / " not in playback_status[separator + 3 :]:
        return None
    return playback_status[:separator].strip(), percentage


def _playback_duration(status: str) -> str | None:
    if _playback_progress_display(status) is None:
        return None
    time_range = status.rsplit(" (", 1)[0].rsplit(" | ", 1)[-1]
    _, separator, duration = time_range.partition(" / ")
    return duration if separator and duration else None


def build_video_info_command(
    yt_dlp: str,
    url: str,
    node_runtime: str | None = None,
    include_comments: bool = False,
    cookie_browser: str = COOKIE_BROWSER,
) -> list[str]:
    command = [
        yt_dlp,
        "--no-warnings",
        "--dump-single-json",
        "--no-write-comments",
        "--skip-download",
        "--playlist-items",
        "1",
        "--format",
        VIDEO_FORMAT_DEFAULT,
        "--cookies-from-browser",
        cookie_browser,
    ]
    if is_youtube_url(url) and _yt_dlp_supports_js_runtimes(yt_dlp):
        runtime = f"node:{node_runtime}" if node_runtime else "node"
        command.extend(["--js-runtimes", runtime])
        if include_comments:
            command.append("--write-comments")
            command.extend(
                [
                    "--extractor-args",
                    f"youtube:comment_sort=top;max_comments={YOUTUBE_COMMENT_FETCH_LIMIT}",
                ]
            )
    command.append(url)
    return command


def _build_playlist_command(
    yt_dlp: str,
    url: str,
    node_runtime: str | None = None,
    playlist_items: tuple[int, int] | None = None,
    cookie_browser: str = COOKIE_BROWSER,
) -> list[str]:
    command = [
        yt_dlp,
        "--no-warnings",
        "--dump-single-json",
        "--skip-download",
        "--flat-playlist",
        "--no-write-comments",
        "--cookies-from-browser",
        cookie_browser,
    ]
    if playlist_items is not None:
        start, end = playlist_items
        command.extend(
            ["--yes-playlist", "--playlist-items", f"{start}-{end}"]
        )
    if is_youtube_url(url) and _yt_dlp_supports_js_runtimes(yt_dlp):
        runtime = f"node:{node_runtime}" if node_runtime else "node"
        command.extend(["--js-runtimes", runtime])
    command.append(url)
    return command


def build_playlist_command(
    yt_dlp: str,
    url: str,
    node_runtime: str | None = None,
    cookie_browser: str = COOKIE_BROWSER,
) -> list[str]:
    return _build_playlist_command(
        yt_dlp, url, node_runtime, cookie_browser=cookie_browser
    )


def build_episode_metadata_command(
    yt_dlp: str,
    url: str,
    node_runtime: str | None = None,
    cookie_browser: str = COOKIE_BROWSER,
) -> list[str]:
    command = [
        yt_dlp,
        "--no-warnings",
        "--dump-single-json",
        "--skip-download",
        "--no-write-comments",
        "--no-playlist",
    ]
    if is_http_url(url):
        command.extend(["--cookies-from-browser", cookie_browser])
    if is_youtube_url(url) and _yt_dlp_supports_js_runtimes(yt_dlp):
        runtime = f"node:{node_runtime}" if node_runtime else "node"
        command.extend(["--js-runtimes", runtime])
    command.append(url)
    return command


def _run_ytdlp_with_browser_fallback(
    command: list[str], **run_options: object
) -> subprocess.CompletedProcess:
    try:
        cookie_index = command.index("--cookies-from-browser")
    except ValueError:
        return subprocess.run(command, **run_options)

    requested_browser = command[cookie_index + 1]
    browser_name = requested_browser.split("+", 1)[0].split(":", 1)[0].casefold()
    browser_sources = [requested_browser]
    browser_sources.extend(
        browser
        for browser in YTDLP_COOKIE_BROWSER_ORDER
        if browser != browser_name
    )
    first_result = None
    result = None
    for browser in browser_sources:
        attempt = command.copy()
        attempt[cookie_index + 1] = browser
        result = subprocess.run(attempt, **run_options)
        if first_result is None:
            first_result = result
        if result.returncode == 0:
            return result
    assert first_result is not None
    return first_result


@dataclass(frozen=True)
class VideoComment:
    text: str
    like_count: int
    author: str | None


@dataclass(frozen=True)
class VideoInfo:
    title: str
    details: tuple[str, ...]
    comments: tuple[VideoComment, ...] = ()


@dataclass(frozen=True)
class PlaylistEpisode:
    title: str
    target: str
    duration_seconds: int | None = None


def _write_mpv_playlist(
    path: Path, episodes: tuple[PlaylistEpisode, ...]
) -> None:
    lines = ["#EXTM3U"]
    for episode in episodes:
        duration = episode.duration_seconds
        title = " ".join(episode.title.split())
        formatted_duration = _format_duration(duration)
        if formatted_duration:
            title = f"{title} ({formatted_duration})"
        lines.extend(
            (
                f"#EXTINF:{duration if duration is not None else -1},{title}",
                episode.target,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _suggest_playlist_filename(
    episodes: tuple[PlaylistEpisode, ...], source_path: Path | None = None
) -> str:
    source_stem = source_path.stem if source_path is not None else ""
    if source_stem.casefold() in {"playlist", "new playlist", "episodes", "list"}:
        source_stem = ""
    if source_stem:
        name = source_stem
    elif episodes:
        first_title = " ".join(episodes[0].title.split()) or "Saved playlist"
        count_suffix = f" - {len(episodes)} episodes" if len(episodes) > 1 else ""
        name = f"{first_title}{count_suffix}"
    else:
        name = "Saved playlist"

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")[:100].rstrip(" .")
    if not name:
        name = "Saved playlist"
    if name.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(
        r"(?:COM|LPT)[1-9]", name, re.IGNORECASE
    ):
        name = f"_{name}"
    return f"{name}.m3u8"


def _write_local_m3u_playlist(
    path: Path, episodes: tuple[PlaylistEpisode, ...]
) -> None:
    if not episodes:
        raise ValueError("Cannot save an empty playlist.")
    lines = ["#EXTM3U"]
    for episode in episodes:
        if "\n" in episode.target or "\r" in episode.target:
            raise ValueError("Playlist targets cannot contain line breaks.")
        title = " ".join(episode.title.split()) or "Untitled"
        duration = episode.duration_seconds
        if duration is None or duration < 0:
            duration = -1
        lines.extend((f"#EXTINF:{duration},{title}", episode.target))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_video_info(
    title: str | None,
    width: object,
    height: object,
    duration: object,
    additional_details: tuple[str, ...] = (),
    comments: tuple[VideoComment, ...] = (),
) -> VideoInfo:
    display_title = title.strip() if isinstance(title, str) else ""
    display_title = display_title or "Unknown"
    summary = []

    try:
        video_width = int(width)
        video_height = int(height)
    except (TypeError, ValueError, OverflowError):
        pass
    else:
        if video_width > 0 and video_height > 0:
            summary.append(f"{video_width} × {video_height}")

    duration_text = _format_duration(duration)
    if duration_text is not None:
        summary.append(duration_text)

    details = ([" · ".join(summary)] if summary else []) + list(additional_details)
    return VideoInfo(display_title, tuple(details), comments)


def _duration_seconds(value: object) -> int | None:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if seconds >= 0 else None


def _format_duration(value: object) -> str | None:
    seconds = _duration_seconds(value)
    if seconds is None:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"{hours}:{minutes:02}:{seconds:02}"
        if hours
        else f"{minutes}:{seconds:02}"
    )


def _hls_duration_seconds(url: str) -> int | None:
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return None
    if not path.endswith((".m3u", ".m3u8")):
        return None
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=HLS_METADATA_TIMEOUT) as response:
            manifest = response.read().decode("utf-8-sig", errors="replace")
    except (OSError, ValueError):
        return None
    if "#EXT-X-ENDLIST" not in manifest.upper():
        return None

    durations = []
    for line in manifest.splitlines():
        line = line.strip()
        if not line.upper().startswith("#EXTINF:"):
            continue
        raw_duration = line[len("#EXTINF:") :].partition(",")[0].strip()
        try:
            duration = float(raw_duration)
        except ValueError:
            return None
        if not math.isfinite(duration) or duration < 0:
            return None
        durations.append(duration)
    total_duration = sum(durations)
    if not durations or not math.isfinite(total_duration):
        return None
    return int(round(total_duration))


def _playlist_episode_label(index: int, episode: PlaylistEpisode) -> str:
    label = f"{index}. {episode.title}"
    duration = _format_duration(episode.duration_seconds)
    return f"{label}  {duration}" if duration else label


def _episode_dropdown_height(episode_count: int) -> int:
    if episode_count <= 0:
        return 1
    return min(episode_count, EPISODE_DROPDOWN_MAX_ROWS)


def _bilibili_ugc_season_episodes(
    video_data: dict[str, object],
) -> tuple[PlaylistEpisode, ...] | None:
    season = video_data.get("ugc_season")
    sections = season.get("sections") if isinstance(season, dict) else None
    if not isinstance(sections, list):
        return None

    episodes = []
    seen_targets = set()
    for section in sections:
        section_episodes = section.get("episodes") if isinstance(section, dict) else None
        if not isinstance(section_episodes, list):
            continue
        for item in section_episodes:
            if not isinstance(item, dict):
                continue
            bvid = item.get("bvid")
            aid = item.get("aid")
            if isinstance(bvid, str) and re.fullmatch(
                r"BV[A-Za-z0-9]+", bvid.strip(), re.IGNORECASE
            ):
                video_id = bvid.strip()
            elif isinstance(aid, (int, str)) and str(aid).isdecimal():
                video_id = f"av{aid}"
            else:
                continue
            target = f"https://www.bilibili.com/video/{video_id}"
            if target.casefold() in seen_targets:
                continue
            seen_targets.add(target.casefold())

            arc = item.get("arc") if isinstance(item.get("arc"), dict) else {}
            page = item.get("page") if isinstance(item.get("page"), dict) else {}
            title = item.get("title") or arc.get("title") or page.get("part")
            if not isinstance(title, str) or not title.strip():
                title = f"Episode {len(episodes) + 1}"
            duration = arc.get("duration") or page.get("duration")
            episodes.append(
                PlaylistEpisode(
                    title.strip(), target, _duration_seconds(duration)
                )
            )
    return tuple(episodes) if episodes else None


def _playlist_start_index(
    url: str, episodes: tuple[PlaylistEpisode, ...]
) -> int:
    try:
        parsed_url = urlsplit(url)
    except ValueError:
        return 0
    video_match = re.fullmatch(
        r"/video/(BV[A-Za-z0-9]+|av\d+)/?", parsed_url.path, re.IGNORECASE
    )
    if video_match is None:
        return 0
    video_id = video_match.group(1).casefold()
    requested_page = parse_qs(parsed_url.query).get("p", [None])[0]
    first_match = None
    for index, episode in enumerate(episodes):
        try:
            episode_url = urlsplit(episode.target)
        except ValueError:
            continue
        episode_match = re.fullmatch(
            r"/video/(BV[A-Za-z0-9]+|av\d+)/?", episode_url.path, re.IGNORECASE
        )
        if episode_match is None or episode_match.group(1).casefold() != video_id:
            continue
        if first_match is None:
            first_match = index
        if requested_page is None:
            return index
        episode_page = parse_qs(episode_url.query).get("p", [None])[0]
        if episode_page == requested_page:
            return index
    return first_match if first_match is not None else 0


def _fetch_bilibili_video_episodes(
    url: str,
) -> tuple[PlaylistEpisode, ...] | None:
    try:
        parsed_url = urlsplit(url)
    except ValueError:
        return None
    host = (parsed_url.hostname or "").lower().rstrip(".")
    if host != "bilibili.com" and not host.endswith(".bilibili.com"):
        return None
    video_match = re.fullmatch(
        r"/video/(BV[A-Za-z0-9]+|av\d+)/?", parsed_url.path, re.IGNORECASE
    )
    if video_match is None:
        return None

    video_id = video_match.group(1)
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bilibili.com/",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urlopen(request, timeout=BILIBILI_VIDEO_METADATA_TIMEOUT) as response:
            page = response.read()
            content_encoding = response.headers.get("Content-Encoding", "").lower()
        if content_encoding == "gzip":
            page = gzip.decompress(page)
        html = page.decode("utf-8-sig", errors="replace")
        state_marker = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
        if state_marker is None:
            return None
        state, _ = json.JSONDecoder().raw_decode(html[state_marker.end() :])
    except (OSError, EOFError, ValueError, zlib.error):
        return None

    video_data = state.get("videoData") if isinstance(state, dict) else None
    if isinstance(video_data, dict):
        season_episodes = _bilibili_ugc_season_episodes(video_data)
        if season_episodes:
            return season_episodes
    pages = video_data.get("pages") if isinstance(video_data, dict) else None
    if not isinstance(pages, list) or not pages:
        return None

    episodes = []
    for index, page_data in enumerate(pages, start=1):
        if not isinstance(page_data, dict):
            continue
        raw_page_number = page_data.get("page")
        page_number = (
            raw_page_number
            if isinstance(raw_page_number, int) and raw_page_number > 0
            else index
        )
        title = page_data.get("part")
        if not isinstance(title, str) or not title.strip():
            title = f"Episode {page_number}"
        episodes.append(
            PlaylistEpisode(
                title.strip(),
                f"https://www.bilibili.com/video/{video_id}?{urlencode({'p': page_number})}",
                _duration_seconds(page_data.get("duration")),
            )
        )
    return tuple(episodes) if episodes else None




def _metadata_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


def _bilibili_api_data(
    endpoint: str, query: dict[str, str | int], referer: str
) -> dict[str, object] | None:
    request = Request(
        f"{endpoint}?{urlencode(query)}",
        headers={"Referer": referer, "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urlopen(request, timeout=BILIBILI_COMMENT_TIMEOUT) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("code") not in (None, 0, "0"):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None



def _bilibili_archive_aid(metadata: dict[str, object]) -> str | None:
    archive_ids = metadata.get("_old_archive_ids")
    if not isinstance(archive_ids, list):
        return None
    for archive_id in archive_ids:
        if not isinstance(archive_id, str):
            continue
        match = re.fullmatch(
            r"bilibili\s+(\d+)(?:_part\d+)?", archive_id, re.IGNORECASE
        )
        if match:
            return match.group(1)
    return None


def _fetch_bilibili_comments(
    url: str, metadata: dict[str, object]
) -> list[dict[str, object]]:
    video_id = _metadata_text(metadata.get("id"))
    if video_id is None:
        return []
    try:
        parsed_url = urlsplit(url)
    except ValueError:
        return []

    host = (parsed_url.hostname or "").lower().rstrip(".")
    referer = _metadata_text(metadata.get("webpage_url")) or url
    is_bilibili_tv = host == "bilibili.tv" or host.endswith(".bilibili.tv")
    if is_bilibili_tv:
        endpoint = "https://api.bilibili.tv/reply/web/root"
        page_start = 0
        video_type = 3 if "/play/" in parsed_url.path.lower() else 1
    else:
        video_id = re.sub(r"_p\d+$", "", video_id)
        video_query = (
            {"aid": video_id[2:]}
            if video_id.lower().startswith("av") and video_id[2:].isdigit()
            else {"bvid": video_id}
        )
        aid = _bilibili_archive_aid(metadata)
        if aid is None:
            video_data = _bilibili_api_data(
                "https://api.bilibili.com/x/web-interface/view", video_query, referer
            )
            aid = video_data.get("aid") if video_data else None
        if aid is None:
            return []
        endpoint = "https://api.bilibili.com/x/v2/reply"
        page_start = 1

    comments = []
    page_count = (
        VIDEO_COMMENT_DISPLAY_LIMIT + BILIBILI_COMMENT_PAGE_SIZE - 1
    ) // BILIBILI_COMMENT_PAGE_SIZE
    for page in range(page_start, page_start + page_count):
        if is_bilibili_tv:
            query = {
                "platform": "web",
                "pn": page,
                "ps": BILIBILI_COMMENT_PAGE_SIZE,
                "oid": video_id,
                "type": video_type,
                "sort_type": 1,
            }
        else:
            query = {
                "pn": page,
                "ps": BILIBILI_COMMENT_PAGE_SIZE,
                "oid": aid,
                "type": 1,
                "jsonp": "jsonp",
                "sort": 2,
            }
        page_data = _bilibili_api_data(endpoint, query, referer)
        replies = page_data.get("replies") if page_data else None
        if not isinstance(replies, list) or not replies:
            break

        pending = list(reversed(replies))
        while pending and len(comments) < VIDEO_COMMENT_DISPLAY_LIMIT:
            reply = pending.pop()
            if not isinstance(reply, dict):
                continue
            content = reply.get("content")
            text = content.get("message") if isinstance(content, dict) else None
            if isinstance(text, str) and text.strip():
                member = reply.get("member")
                comments.append(
                    {
                        "text": text,
                        "like_count": reply.get(
                            "like_count" if is_bilibili_tv else "like"
                        ),
                        "author": (
                            member.get("name" if is_bilibili_tv else "uname")
                            if isinstance(member, dict)
                            else None
                        ),
                    }
                )
            children = reply.get("replies")
            if isinstance(children, list):
                remaining = VIDEO_COMMENT_DISPLAY_LIMIT - len(comments)
                pending.extend(reversed(children[:remaining]))

    return comments


def _ytdlp_comments(metadata: dict[str, object]) -> tuple[VideoComment, ...]:
    raw_comments = metadata.get("comments")
    if not isinstance(raw_comments, list):
        return ()

    comments = []
    for item in raw_comments:
        if not isinstance(item, dict):
            continue
        text = _metadata_text(item.get("text"))
        if text is None:
            continue
        try:
            like_count = int(item.get("like_count") or 0)
        except (TypeError, ValueError, OverflowError):
            like_count = 0
        comments.append(
            VideoComment(text, max(0, like_count), _metadata_text(item.get("author")))
        )

    comments.sort(key=lambda comment: comment.like_count, reverse=True)
    return tuple(comments[:VIDEO_COMMENT_DISPLAY_LIMIT])


def _format_file_size(value: object) -> str | None:
    try:
        size = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 <= size < 1e18:
        return None
    if size < 1000:
        return f"{int(size):,} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1000
        if size < 1000 or unit == "TB":
            return f"{size:.1f} {unit}"
    return None


def _format_frame_rate(value: object) -> str | None:
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            frames_per_second = float(numerator) / float(denominator)
        else:
            frames_per_second = float(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if not 0 < frames_per_second < 10000:
        return None
    return f"{frames_per_second:g} fps"


def _format_metric_rate(value: object, divisor: float = 1) -> str | None:
    try:
        rate = float(value) / divisor
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if not 0 < rate < 1e9:
        return None
    return f"{rate:,.0f} kb/s"


def _format_count(value: object, label: str) -> str | None:
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if count < 0:
        return None
    return f"{count:,} {label}"


def _format_upload_date(value: object) -> str | None:
    date = _metadata_text(value)
    if date and len(date) == 8 and date.isdigit():
        return f"{date[:4]}-{date[4:6]}-{date[6:]}"
    return date


def _ytdlp_detail_lines(
    metadata: dict[str, object],
    video_format: dict[str, object] | None,
    audio_format: dict[str, object] | None,
    selected_formats: list[dict[str, object]],
) -> tuple[str, ...]:
    details = []
    format_id = _metadata_text(metadata.get("format_id"))
    if format_id is None:
        format_ids = [
            _metadata_text(selected.get("format_id"))
            for selected in (video_format, audio_format)
            if selected is not None
        ]
        format_id = "+".join(item for item in format_ids if item) or None

    format_line = [f"Format {format_id}"] if format_id else []
    format_note = _metadata_text(metadata.get("format_note"))
    if format_note and format_note != format_id:
        format_line.append(format_note)
    elif not format_note:
        format_text = _metadata_text(metadata.get("format"))
        if format_text and format_text != format_id and len(format_text) <= 80:
            format_line.append(format_text)
    extension = _metadata_text(
        metadata.get("ext") or (video_format or {}).get("ext")
    )
    if extension:
        format_line.append(extension.upper())
    frame_rate = _format_frame_rate(
        metadata.get("fps") or (video_format or {}).get("fps")
    )
    if frame_rate:
        format_line.append(frame_rate)
    if format_line:
        details.append(" · ".join(format_line))

    video_codec = _metadata_text(metadata.get("vcodec"))
    if video_codec in (None, "none"):
        video_codec = _metadata_text((video_format or {}).get("vcodec"))
    audio_codec = _metadata_text(metadata.get("acodec"))
    if audio_codec in (None, "none"):
        audio_codec = _metadata_text((audio_format or {}).get("acodec"))
    codec_line = [codec for codec in (video_codec, audio_codec) if codec]
    file_size = metadata.get("filesize")
    if file_size is None:
        file_size = metadata.get("filesize_approx")
    if file_size is None:
        sizes = [
            selected.get("filesize") or selected.get("filesize_approx")
            for selected in selected_formats
        ]
        known_sizes = [size for size in sizes if size is not None]
        if known_sizes:
            try:
                file_size = sum(float(size) for size in known_sizes)
            except (TypeError, ValueError, OverflowError):
                file_size = None
    size_text = _format_file_size(file_size)
    if size_text:
        codec_line.append(size_text)
    bitrate = _format_metric_rate(metadata.get("tbr"))
    if bitrate:
        codec_line.append(bitrate)
    if codec_line:
        details.append(" · ".join(codec_line))

    uploader = _metadata_text(metadata.get("uploader")) or _metadata_text(
        metadata.get("channel")
    )
    creator_line = [uploader] if uploader else []
    upload_date = _format_upload_date(
        metadata.get("upload_date") or metadata.get("release_date")
    )
    if upload_date:
        creator_line.append(upload_date)
    creator_line.extend(
        metric
        for metric in (
            _format_count(metadata.get("view_count"), "views"),
            _format_count(metadata.get("like_count"), "likes"),
            _format_count(metadata.get("comment_count"), "comments"),
        )
        if metric
    )
    if creator_line:
        details.append(" · ".join(creator_line))

    categories = metadata.get("categories")
    if isinstance(categories, list):
        category_names = [name for item in categories if (name := _metadata_text(item))]
        if category_names:
            details.append("Categories: " + " · ".join(category_names))
    tags = metadata.get("tags")
    if isinstance(tags, list):
        tag_names = [name for item in tags if (name := _metadata_text(item))]
        if tag_names:
            extra_count = max(0, len(tag_names) - 8)
            visible_tags = " · ".join(tag_names[:8])
            if extra_count:
                visible_tags += f" · +{extra_count} more"
            details.append("Tags: " + visible_tags)
    description = _metadata_text(metadata.get("description"))
    if description:
        if len(description) > 320:
            description = description[:317].rsplit(" ", 1)[0] + "…"
        details.append(description)
    if metadata.get("live_status") == "is_live":
        details.insert(0, "LIVE")
    elif metadata.get("live_status") == "is_upcoming":
        details.insert(0, "Upcoming live stream")
    return tuple(details)


def video_info_from_ytdlp(metadata: dict[str, object]) -> VideoInfo:
    raw_formats = metadata.get("requested_formats") or metadata.get("formats")
    selected_formats = (
        [item for item in raw_formats if isinstance(item, dict)]
        if isinstance(raw_formats, list)
        else []
    )
    video_formats = [
        item for item in selected_formats if item.get("vcodec") != "none"
    ]
    video_format = max(
        video_formats,
        key=lambda item: int(item.get("height") or 0),
        default=None,
    )
    audio_format = next(
        (
            item
            for item in selected_formats
            if item.get("vcodec") == "none" and item.get("acodec") != "none"
        ),
        None,
    )
    if video_format is None and metadata.get("vcodec") == "none":
        raise ValueError("No video stream found.")
    if video_format is None and metadata.get("vcodec") not in (None, "none"):
        video_format = metadata
    width = metadata.get("width") or (video_format or {}).get("width")
    height = metadata.get("height") or (video_format or {}).get("height")
    return format_video_info(
        _metadata_text(metadata.get("title")),
        width,
        height,
        metadata.get("duration"),
        _ytdlp_detail_lines(
            metadata, video_format, audio_format, selected_formats
        ),
        _ytdlp_comments(metadata),
    )


def video_info_from_ffprobe(
    metadata: dict[str, object], fallback_title: str
) -> VideoInfo:
    streams = metadata.get("streams")
    video_stream = {}
    audio_stream = {}
    if isinstance(streams, list):
        video_stream = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "video"
            ),
            {},
        )
        audio_stream = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "audio"
            ),
            {},
        )
    if not video_stream:
        raise ValueError("No video stream found.")

    media_format = metadata.get("format")
    if not isinstance(media_format, dict):
        media_format = {}
    tags = media_format.get("tags")
    title = tags.get("title") if isinstance(tags, dict) else None
    display_title = (
        title if isinstance(title, str) and title.strip() else fallback_title
    )
    details = []

    format_details = []
    container = _metadata_text(media_format.get("format_name"))
    if container:
        format_details.append(container.split(",", 1)[0].upper())
    frame_rate = _format_frame_rate(
        video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    )
    if frame_rate:
        format_details.append(frame_rate)
    if format_details:
        details.append(" · ".join(format_details))
    codecs = [
        codec
        for codec in (
            _metadata_text(video_stream.get("codec_name")),
            _metadata_text(audio_stream.get("codec_name")),
        )
        if codec
    ]
    file_size = _format_file_size(media_format.get("size"))
    if file_size:
        codecs.append(file_size)
    bitrate = _format_metric_rate(media_format.get("bit_rate"), divisor=1000)
    if bitrate:
        codecs.append(bitrate)
    if codecs:
        details.append(" · ".join(codecs))

    return format_video_info(
        display_title,
        video_stream.get("width"),
        video_stream.get("height"),
        media_format.get("duration"),
        tuple(details),
    )


def fetch_url_video_info(
    yt_dlp: str, url: str, node_runtime: str | None = None
) -> VideoInfo:
    result = _run_ytdlp_with_browser_fallback(
        build_video_info_command(yt_dlp, url, node_runtime),
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "yt-dlp could not read video information.")
    metadata = json.loads(result.stdout)
    entries = metadata.get("entries") if isinstance(metadata, dict) else None
    if isinstance(entries, list):
        video_entry = next(
            (entry for entry in entries if isinstance(entry, dict)), None
        )
        if video_entry is None:
            raise ValueError("No video found in the playlist.")
        metadata = video_entry
    if not isinstance(metadata, dict):
        raise ValueError("yt-dlp returned invalid video information.")
    if is_bilibili_url(url):
        metadata["comments"] = _fetch_bilibili_comments(url, metadata)
    return video_info_from_ytdlp(metadata)


def fetch_youtube_video_comments(
    yt_dlp: str, url: str, node_runtime: str | None = None
) -> tuple[VideoComment, ...]:
    result = _run_ytdlp_with_browser_fallback(
        build_video_info_command(
            yt_dlp, url, node_runtime, include_comments=True
        ),
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=YOUTUBE_COMMENT_FETCH_TIMEOUT,
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip() or "yt-dlp could not read video comments."
        )
    metadata = json.loads(result.stdout)
    entries = metadata.get("entries") if isinstance(metadata, dict) else None
    if isinstance(entries, list):
        metadata = next(
            (entry for entry in entries if isinstance(entry, dict)), {}
        )
    if not isinstance(metadata, dict):
        return ()
    return _ytdlp_comments(metadata)


def _load_video_info(
    request_id: int,
    loader: Callable[[], VideoInfo],
    url: str | None,
    comments_loader: Callable[[], tuple[VideoComment, ...]] | None,
    results: queue.Queue[tuple[int, VideoInfo | None, str | None]],
) -> None:
    try:
        info = loader()
    except Exception as error:
        results.put((request_id, None, _video_info_error_message(url, error)))
        return
    results.put((request_id, info, None))
    if comments_loader is None:
        return
    try:
        comments = comments_loader()
    except Exception:
        return
    if comments:
        results.put((request_id, VideoInfo(info.title, info.details, comments), None))


def _fetch_playlist_episodes(
    yt_dlp: str,
    url: str,
    node_runtime: str | None = None,
    playlist_items: tuple[int, int] | None = None,
) -> tuple[PlaylistEpisode, ...] | None:
    result = _run_ytdlp_with_browser_fallback(
        _build_playlist_command(yt_dlp, url, node_runtime, playlist_items),
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "yt-dlp could not read the playlist.")
    metadata = json.loads(result.stdout)
    if not isinstance(metadata, dict):
        raise ValueError("yt-dlp returned invalid playlist information.")
    entries = metadata.get("entries")
    if not isinstance(entries, list):
        return None

    episodes = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        availability = entry.get("availability")
        if isinstance(availability, str) and availability.lower() in {
            "needs_auth",
            "unavailable",
        }:
            continue
        episode_url = None
        for key in ("webpage_url", "original_url", "url"):
            candidate = entry.get(key)
            if isinstance(candidate, str) and is_http_url(candidate):
                episode_url = candidate
                break
        identifier = entry.get("id")
        if episode_url is None and isinstance(identifier, str):
            if is_youtube_url(url):
                episode_url = (
                    f"https://www.youtube.com/watch?{urlencode({'v': identifier})}"
                )
            elif is_bilibili_url(url):
                video_id = identifier
                if video_id.isdecimal():
                    video_id = f"av{video_id}"
                if video_id.lower().startswith("bv") and video_id[2:].isalnum():
                    episode_url = f"https://www.bilibili.com/video/{video_id}"
                elif video_id.lower().startswith("av") and video_id[2:].isdecimal():
                    episode_url = f"https://www.bilibili.com/video/{video_id}"
        if episode_url is None:
            continue
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            title = str(identifier) if identifier else f"Episode {index}"
        episodes.append(
            PlaylistEpisode(
                title.strip(), episode_url, _duration_seconds(entry.get("duration"))
            )
        )
    return tuple(episodes)


def fetch_playlist_episodes(
    yt_dlp: str, url: str, node_runtime: str | None = None
) -> tuple[PlaylistEpisode, ...] | None:
    bilibili_episodes = _fetch_bilibili_video_episodes(url)
    if bilibili_episodes is not None:
        return bilibili_episodes
    return _fetch_playlist_episodes(yt_dlp, url, node_runtime)


def _playlist_episode_at_position(
    episodes: tuple[PlaylistEpisode, ...], position: object
) -> PlaylistEpisode | None:
    if type(position) is not int or not 0 <= position < len(episodes):
        return None
    return episodes[position]


def read_local_playlist_episodes(
    playlist_file: Path,
) -> tuple[PlaylistEpisode, ...] | None:
    if playlist_file.suffix.lower() not in {".m3u", ".m3u8"}:
        return None
    lines = playlist_file.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    first_line = next((line.strip() for line in lines if line.strip()), "")
    if first_line.upper() != "#EXTM3U":
        return None

    episodes = []
    pending_title: str | None = None
    pending_duration: int | None = None
    for line in lines:
        line = line.strip()
        if line.upper().startswith("#EXTINF:"):
            raw_duration, separator, title = line[len("#EXTINF:") :].partition(",")
            pending_title = title.strip() if separator else None
            pending_title = pending_title or None
            pending_duration = (
                _duration_seconds(raw_duration) if separator else None
            )
            continue
        if not line or line.startswith("#") or pending_title is None:
            continue
        if is_http_url(line):
            target = line
        elif "://" in line:
            pending_title = None
            pending_duration = None
            continue
        else:
            target_path = Path(line).expanduser()
            if not target_path.is_absolute():
                target_path = playlist_file.resolve().parent / target_path
            target = str(target_path.resolve())
        episodes.append(PlaylistEpisode(pending_title, target, pending_duration))
        pending_title = None
        pending_duration = None
    return tuple(episodes) if episodes else None



def fetch_episode_metadata(
    yt_dlp: str | None, episode: PlaylistEpisode, node_runtime: str | None = None
) -> PlaylistEpisode:
    if not is_http_url(episode.target):
        return episode
    hls_duration = _hls_duration_seconds(episode.target)
    if hls_duration is not None:
        return PlaylistEpisode(episode.title, episode.target, hls_duration)
    if yt_dlp is None:
        return episode
    result = _run_ytdlp_with_browser_fallback(
        build_episode_metadata_command(yt_dlp, episode.target, node_runtime),
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode:
        return episode
    metadata = json.loads(result.stdout)
    if not isinstance(metadata, dict):
        return episode
    entries = metadata.get("entries")
    if isinstance(entries, list):
        metadata = next((entry for entry in entries if isinstance(entry, dict)), {})
    if not isinstance(metadata, dict):
        return episode

    title = episode.title
    metadata_title = metadata.get("title")
    if isinstance(metadata_title, str) and metadata_title.strip():
        path = Path(urlsplit(episode.target).path)
        generic_titles = {
            path.name.casefold(),
            path.stem.casefold(),
            path.parent.name.casefold(),
            "remote clip",
        }
        if metadata_title.strip().casefold() not in generic_titles:
            title = metadata_title.strip()
    duration = _duration_seconds(metadata.get("duration"))
    if duration is None:
        duration = episode.duration_seconds
    return PlaylistEpisode(title, episode.target, duration)


def resolve_ffprobe_tool() -> str:
    try:
        ffmpeg = resolve_tool(
            None, "DLPPLAYER_FFMPEG", FFMPEG_DEFAULT, "ffmpeg", allow_directory=True
        )
    except FileNotFoundError:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            return ffprobe
        raise

    ffmpeg_path = Path(ffmpeg)
    executable_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    if ffmpeg_path.is_dir():
        sibling = ffmpeg_path / executable_name
    else:
        sibling = ffmpeg_path.with_name(executable_name)
    if sibling.is_file():
        return str(sibling)
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe
    raise FileNotFoundError("ffprobe was not found next to ffmpeg or on PATH.")


def fetch_local_video_info(ffprobe: str, media_file: Path) -> VideoInfo:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name,size,bit_rate:"
            "format_tags=title:"
            "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,bit_rate",
            "-of",
            "json",
            str(media_file),
        ],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe could not read video information.")
    metadata = json.loads(result.stdout)
    if not isinstance(metadata, dict):
        raise ValueError("ffprobe returned invalid video information.")
    return video_info_from_ffprobe(metadata, media_file.stem)


def download(args: argparse.Namespace) -> int:
    if not is_http_url(args.url):
        print("Error: URL must use HTTP or HTTPS.", file=sys.stderr)
        return 2

    try:
        yt_dlp = resolve_tool(args.yt_dlp, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp")
        node_runtime = None
        if is_youtube_url(args.url):
            node_runtime = resolve_tool(
                args.node, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
            )
        ffmpeg = resolve_tool(
            args.ffmpeg, "DLPPLAYER_FFMPEG", FFMPEG_DEFAULT, "ffmpeg", allow_directory=True
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="dlpplayer-") as temporary_directory:
            result_file = str(Path(temporary_directory) / "downloaded-path.txt")
            command = build_download_command(
                yt_dlp,
                ffmpeg,
                args.output_dir,
                args.url,
                args.cookie_profile,
                not args.no_cookies,
                result_file,
                args.video_format,
                node_runtime,
                args.cookie_browser or COOKIE_BROWSER,
            )
            result = _run_ytdlp_with_browser_fallback(command, check=False)
            if result.returncode:
                return result.returncode

            if args.play:
                result_path = Path(result_file)
                if not result_path.is_file():
                    print("Error: yt-dlp completed but did not report the downloaded file.", file=sys.stderr)
                    return 1
                downloaded_file = Path(result_path.read_text(encoding="utf-8").strip())
                if not downloaded_file.is_file():
                    print(f"Error: downloaded file was not found: {downloaded_file}", file=sys.stderr)
                    return 1
                return _open_mpv_media(downloaded_file, args.mpv)

        return 0
    except (FileNotFoundError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def play(args: argparse.Namespace) -> int:
    media_file = Path(args.file).expanduser()
    if not media_file.is_file():
        print(f"Error: media file was not found: {media_file}", file=sys.stderr)
        return 2
    try:
        return _open_mpv_media(media_file, args.mpv)
    except (FileNotFoundError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def launch_gui(_args: argparse.Namespace | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk

        from tkinterdnd2 import DND_ALL, TkinterDnD
    except ImportError as error:
        print(
            "The drag-and-drop GUI needs its dependencies. Install them with "
            "`python -m pip install -r requirements.txt`.",
            file=sys.stderr,
        )
        print(f"Error: {error}", file=sys.stderr)
        return 1

    root = TkinterDnD.Tk()

    playlist_tempdir = tempfile.TemporaryDirectory(prefix="dlpplayer-mpv-")
    playlist_manifest_path = Path(playlist_tempdir.name) / "playlist.m3u"
    root.title("DLPPlayer — Drop a video, playlist, or link")
    window_width = 600
    screen_width = root.winfo_screenwidth()
    window_margin = 20
    window_x = max(0, screen_width - window_width - window_margin)
    root.geometry(
        f"{window_width}x{IDLE_WINDOW_MIN_HEIGHT}+{window_x}+{window_margin}"
    )
    root.minsize(460, IDLE_WINDOW_MIN_HEIGHT)

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    drop_area = tk.Label(
        frame,
        text="Drop local video, playlist, or URL here",
        relief="groove",
        borderwidth=2,
        font=("Segoe UI", 11),
        height=2,
    )
    drop_area.pack(fill="x", pady=(0, 10))

    url_row = ttk.Frame(frame)
    url_row.pack(fill="x")
    url_entry = ttk.Entry(url_row)
    url_entry.pack(side="left", fill="x", expand=True)
    stop_button = ttk.Button(url_row, text="■ Stop", state="disabled")
    stop_button.pack(side="right", padx=(8, 0))
    play_button = ttk.Button(url_row, text="▶ Play in mpv")
    play_button.pack(side="right")

    episode_row = ttk.Frame(frame)
    ttk.Label(episode_row, text="Episode:").pack(side="left", padx=(0, 8))
    episode_selection = tk.StringVar(value="")
    episode_combo = ttk.Combobox(
        episode_row,
        textvariable=episode_selection,
        state="readonly",
        height=12,
    )
    episode_combo.pack(side="left", fill="x", expand=True)
    save_playlist_button = ttk.Button(
        episode_row, text="Save playlist", state="disabled"
    )
    save_playlist_button.pack(side="right", padx=(8, 0))

    info_frame = ttk.LabelFrame(frame, text="Video information", padding=8)
    video_issue = tk.StringVar(value="")
    info_label_style = ttk.Style(root)
    info_background = (
        info_label_style.lookup("TLabel", "background")
        or root.cget("background")
    )
    info_foreground = info_label_style.lookup("TLabel", "foreground") or "black"
    info_font = info_label_style.lookup("TLabel", "font") or "TkDefaultFont"
    video_title_text = tk.Text(
        info_frame,
        height=1,
        wrap="word",
        font=("Segoe UI", 16, "bold"),
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        padx=0,
        pady=0,
        background=info_background,
        foreground=info_foreground,
        state="disabled",
        takefocus=True,
    )
    video_title_text.pack(fill="x", pady=(0, 6))
    video_details_text = tk.Text(
        info_frame,
        height=1,
        wrap="word",
        font=info_font,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        padx=0,
        pady=0,
        background=info_background,
        foreground=info_foreground,
        state="disabled",
        takefocus=True,
    )
    video_details_text.pack(fill="x", pady=(6, 0))
    video_issue_label = ttk.Label(
        info_frame,
        textvariable=video_issue,
        wraplength=520,
        justify="left",
    )
    comments_frame = ttk.LabelFrame(info_frame, text="Most liked comments", padding=4)
    comments_text = scrolledtext.ScrolledText(
        comments_frame,
        height=20,
        wrap="word",
        font=("Segoe UI", 9),
        state="disabled",
    )
    comments_text.pack(fill="both", expand=True)
    metadata_results: queue.Queue[
        tuple[int, VideoInfo | None, str | None]
    ] = queue.Queue()
    metadata_request = 0
    playlist_results: queue.Queue[
        tuple[int, str, tuple[PlaylistEpisode, ...] | None, str | None]
    ] = queue.Queue()
    playlist_request = 0
    radio_mix_results: queue.Queue[
        tuple[int, int, tuple[PlaylistEpisode, ...] | None, str | None]
    ] = queue.Queue()
    radio_mix_request = 0
    radio_mix_url: str | None = None
    radio_mix_next_start = 1
    radio_mix_loading = False
    radio_mix_active = False
    radio_mix_yt_dlp: str | None = None
    radio_mix_node_runtime: str | None = None
    episode_metadata_results: queue.Queue[
        tuple[int, int, PlaylistEpisode]
    ] = queue.Queue()
    pending_playlist_url: str | None = None
    playlist_playing_url: str | None = None
    loaded_playlist_url: str | None = None
    loaded_playlist_path: Path | None = None
    playlist_episodes: tuple[PlaylistEpisode, ...] = ()

    def display_comments(comments: tuple[VideoComment, ...]) -> None:
        comments_text.configure(state="normal")
        comments_text.delete("1.0", tk.END)
        for comment in comments:
            author = f" · {comment.author}" if comment.author else ""
            comments_text.insert(
                tk.END,
                f"{comment.like_count:,} likes{author}\n{comment.text}\n\n",
            )
        comments_text.configure(state="disabled")
        if comments:
            comments_frame.pack(fill="x", pady=(8, 0))
        else:
            comments_frame.pack_forget()

    def set_selectable_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal", height=1)
        widget.delete("1.0", tk.END)
        if value:
            widget.insert("1.0", value)
        widget.configure(state="disabled")

    def fit_selectable_text(widget: tk.Text) -> None:
        if not widget.winfo_ismapped() or widget.winfo_width() <= 1:
            return
        display_lines = widget.count("1.0", "end-1c", "displaylines")
        height = max(1, display_lines[0] if display_lines else 1)
        if int(widget.cget("height")) != height:
            widget.configure(height=height)
            root.after_idle(fit_window_height)

    video_title_text.bind(
        "<Configure>",
        lambda _event: fit_selectable_text(video_title_text),
    )
    video_details_text.bind(
        "<Configure>",
        lambda _event: fit_selectable_text(video_details_text),
    )
    video_title_text.bind(
        "<Map>",
        lambda _event: fit_selectable_text(video_title_text),
    )
    video_details_text.bind(
        "<Map>",
        lambda _event: fit_selectable_text(video_details_text),
    )


    def fit_window_height() -> None:
        root.update_idletasks()
        height = max(root.winfo_reqheight(), IDLE_WINDOW_MIN_HEIGHT)
        root.geometry(f"{root.winfo_width()}x{height}")

    def clear_playlist_selection() -> None:
        nonlocal playlist_request, pending_playlist_url
        nonlocal loaded_playlist_url, loaded_playlist_path, playlist_episodes
        nonlocal playlist_playing_url, radio_mix_request, radio_mix_url
        nonlocal radio_mix_loading, radio_mix_active
        playlist_request += 1
        radio_mix_request += 1
        radio_mix_url = None
        radio_mix_loading = False
        radio_mix_active = False
        pending_playlist_url = None
        playlist_playing_url = None
        loaded_playlist_url = None
        loaded_playlist_path = None
        playlist_episodes = ()
        episode_combo.configure(values=())
        episode_combo.set("")
        episode_row.pack_forget()
        save_playlist_button.configure(state="disabled")
        play_button.configure(state="normal")
        stop_button.configure(
            state="normal" if current_player is not None else "disabled"
        )
        fit_window_height()

    def show_playlist_episodes(episodes: tuple[PlaylistEpisode, ...]) -> None:
        nonlocal playlist_episodes
        playlist_episodes = episodes
        if not episodes:
            save_playlist_button.configure(state="disabled")
            status.set("No playable episodes were found in this playlist.")
            return
        save_playlist_button.configure(state="normal")
        episode_combo.configure(
            height=_episode_dropdown_height(len(episodes)),
            values=tuple(
                _playlist_episode_label(index, episode)
                for index, episode in enumerate(episodes, start=1)
            ),
        )
        episode_combo.current(0)
        episode_row.pack(after=url_row, fill="x", pady=(8, 0))
        fit_window_height()
        status.set(f"Found {len(episodes)} episodes. Playing from episode 1.")

    def save_current_playlist() -> None:
        if not playlist_episodes:
            status.set("No playlist is loaded to save.")
            return
        initial_name = _suggest_playlist_filename(
            playlist_episodes, loaded_playlist_path
        )
        options = {
            "parent": root,
            "title": "Save playlist",
            "initialfile": initial_name,
            "defaultextension": ".m3u8",
            "filetypes": (("M3U playlists", "*.m3u8 *.m3u"), ("All files", "*.*")),
        }
        if loaded_playlist_path is not None:
            options["initialdir"] = str(loaded_playlist_path.parent)
        destination = filedialog.asksaveasfilename(**options)
        if not destination:
            return
        playlist_path = Path(destination)
        try:
            _write_local_m3u_playlist(playlist_path, playlist_episodes)
        except (OSError, ValueError) as error:
            status.set(f"Could not save playlist: {error}")
            messagebox.showerror("Could not save playlist", str(error), parent=root)
            return
        status.set(f"Saved {len(playlist_episodes)} episodes to {playlist_path}.")

    save_playlist_button.configure(command=save_current_playlist)

    def request_episode_metadata(
        episodes: tuple[PlaylistEpisode, ...], request_id: int
    ) -> None:
        targets = tuple(
            (index, episode)
            for index, episode in enumerate(episodes)
            if is_http_url(episode.target)
        )
        if not targets:
            return
        try:
            yt_dlp = resolve_tool(None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp")
        except FileNotFoundError:
            yt_dlp = None
        node_runtime = None
        if yt_dlp is not None and any(
            is_youtube_url(episode.target) for _, episode in targets
        ):
            try:
                node_runtime = resolve_tool(
                    None, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
                )
            except FileNotFoundError:
                pass

        pending = queue.Queue()
        for item in targets:
            pending.put(item)

        def enrich_episodes() -> None:
            while True:
                try:
                    index, episode = pending.get_nowait()
                except queue.Empty:
                    return
                try:
                    updated_episode = fetch_episode_metadata(
                        yt_dlp, episode, node_runtime
                    )
                except Exception:
                    continue
                if updated_episode != episode:
                    episode_metadata_results.put(
                        (request_id, index, updated_episode)
                    )

        for _ in range(min(4, len(targets))):
            threading.Thread(target=enrich_episodes, daemon=True).start()

    def clear_video_info() -> None:
        nonlocal metadata_request
        metadata_request += 1
        info_frame.pack_forget()
        set_selectable_text(video_title_text, "")
        set_selectable_text(video_details_text, "")
        video_issue.set("")
        display_comments(())
        fit_window_height()

    def request_video_info(
        loader: Callable[[], VideoInfo],
        url: str | None = None,
        comments_loader: Callable[[], tuple[VideoComment, ...]] | None = None,
    ) -> None:
        nonlocal metadata_request
        metadata_request += 1
        request_id = metadata_request
        threading.Thread(
            target=_load_video_info,
            args=(request_id, loader, url, comments_loader, metadata_results),
            daemon=True,
        ).start()

    def request_playlist_episode_info(
        episode: PlaylistEpisode,
        yt_dlp: str | None = None,
        node_runtime: str | None = None,
    ) -> None:
        if is_http_url(episode.target):
            try:
                if yt_dlp is None:
                    yt_dlp = resolve_tool(
                        None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp"
                    )
                if is_youtube_url(episode.target) and node_runtime is None:
                    try:
                        node_runtime = resolve_tool(
                            None, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
                        )
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                return
            comments_loader = (
                (
                    lambda: fetch_youtube_video_comments(
                        yt_dlp, episode.target, node_runtime
                    )
                )
                if is_youtube_url(episode.target)
                else None
            )
            request_video_info(
                lambda: fetch_url_video_info(yt_dlp, episode.target, node_runtime),
                episode.target,
                comments_loader,
            )
            return

        first_file = Path(episode.target).resolve()
        request_video_info(
            lambda: fetch_local_video_info(resolve_ffprobe_tool(), first_file)
        )

    def poll_video_info() -> None:
        while True:
            try:
                request_id, info, error_message = metadata_results.get_nowait()
            except queue.Empty:
                break
            if request_id != metadata_request:
                continue
            if info is None:
                if error_message is None:
                    continue
                set_selectable_text(video_title_text, "")
                set_selectable_text(video_details_text, "")
                video_title_text.pack_forget()
                video_details_text.pack_forget()
                video_issue.set(error_message)
                if not video_issue_label.winfo_manager():
                    video_issue_label.pack(fill="x", pady=(6, 0))
                display_comments(())
            else:
                set_selectable_text(video_title_text, info.title)
                set_selectable_text(video_details_text, "\n".join(info.details))
                video_issue.set("")
                video_issue_label.pack_forget()
                if not video_title_text.winfo_manager():
                    video_title_text.pack(fill="x", pady=(0, 6))
                if not video_details_text.winfo_manager():
                    video_details_text.pack(fill="x", pady=(6, 0))
                display_comments(info.comments)
            info_frame.pack(before=status_row, fill="x", pady=(10, 0))
            if info is not None:
                root.update_idletasks()
                fit_selectable_text(video_title_text)
                fit_selectable_text(video_details_text)
            fit_window_height()
        root.after(100, poll_video_info)

    root.after(100, poll_video_info)
    status = tk.StringVar(value="Ready.")
    current_player: subprocess.Popen | None = None
    current_player_ipc_server: str | None = _mpv_ipc_server_path()
    active_playing_episodes: tuple[PlaylistEpisode, ...] = ()
    active_playlist_position: int | None = None
    active_playlist_offset = 0

    def stop_current_player() -> None:
        nonlocal current_player, current_player_ipc_server
        player = current_player
        current_player = None
        current_player_ipc_server = None
        if player is not None and player.poll() is None:
            try:
                player.terminate()
            except OSError:
                pass
        stop_button.configure(state="disabled")

    def stop_playback() -> None:
        nonlocal radio_mix_request, radio_mix_url, radio_mix_loading, radio_mix_active
        had_playback = current_player is not None or radio_mix_active
        radio_mix_request += 1
        radio_mix_url = None
        radio_mix_loading = False
        radio_mix_active = False
        stopped = False
        if (
            current_player_ipc_server is not None
            and (current_player is None or current_player.poll() is None)
        ):
            stopped = _send_mpv_ipc_command_retry(
                current_player_ipc_server, ["stop"]
            )
        had_playback = had_playback or stopped
        if not stopped and current_player is not None and current_player.poll() is None:
            play_button.configure(state="normal")
            stop_button.configure(state="normal")
            set_playback_lamp(True, True)
            status.set("Could not reach MPV; it was left open. Try Stop again.")
            return
        if not stopped:
            stop_current_player()
        play_button.configure(state="normal")
        stop_button.configure(state="disabled")
        if had_playback:
            set_playback_lamp(current_player is not None or stopped, False)
            clear_video_info()
            status.set(
                "Playback stopped; MPV remains open."
                if stopped
                else "Playback stopped."
            )
            if stopped and current_player is None:
                root.after(2000, check_player, None)

    def set_playback_lamp(available: bool, playing: bool = False) -> None:
        if not available:
            lamp_canvas.pack_forget()
            return
        lamp_canvas.itemconfigure(
            lamp_playing, state="normal" if playing else "hidden"
        )
        lamp_canvas.itemconfigure(
            lamp_stopped, state="hidden" if playing else "normal"
        )
        lamp_canvas.pack(side="left", before=status_label, padx=(0, 6))

    def track_active_playlist(
        episodes: tuple[PlaylistEpisode, ...],
        position: int = 0,
        offset: int = 0,
        request_info: bool = True,
    ) -> None:
        nonlocal active_playing_episodes, active_playlist_position
        nonlocal active_playlist_offset
        active_playing_episodes = episodes
        active_playlist_offset = offset
        active_playlist_position = (
            position if _playlist_episode_at_position(episodes, position) else None
        )
        if request_info and active_playlist_position is not None:
            request_playlist_episode_info(episodes[active_playlist_position])

    def update_current_episode_info() -> None:
        nonlocal active_playlist_position
        if current_player_ipc_server is None or not active_playing_episodes:
            return
        position = _mpv_property_value(current_player_ipc_server, "playlist-pos")
        episode = _playlist_episode_at_position(active_playing_episodes, position)
        if episode is None or position == active_playlist_position:
            return
        active_playlist_position = position
        display_position = active_playlist_offset + position
        if 0 <= display_position < len(playlist_episodes):
            episode_combo.current(display_position)
        request_playlist_episode_info(episode)

    def check_player(player: subprocess.Popen | None) -> None:
        nonlocal current_player, current_player_ipc_server, radio_mix_active
        if player is not current_player:
            return
        if player is None:
            update_current_episode_info()
            ipc_server = current_player_ipc_server
            playback_status = (
                _mpv_status_text(
                    ipc_server, video_title_text.get("1.0", "end-1c").strip()
                )
                if ipc_server is not None
                else None
            )
            if playback_status is not None:
                idle = playback_status == "MPV is ready."
                set_playback_lamp(True, not idle)
                stop_button.configure(state="disabled" if idle else "normal")
                play_button.configure(state="normal")
                status.set(
                    "Checking URL for playlist episodes..."
                    if idle and pending_playlist_url is not None
                    else playback_status
                )
                root.after(2000, check_player, None)
                return
            current_player_ipc_server = None
            set_playback_lamp(False)
            stop_button.configure(state="disabled")
            play_button.configure(state="normal")
            return
        if player.poll() is None:
            update_current_episode_info()
            playback_status = (
                _mpv_status_text(
                    current_player_ipc_server,
                    video_title_text.get("1.0", "end-1c").strip(),
                )
                if current_player_ipc_server is not None
                else None
            )
            idle = playback_status == "MPV is ready."
            set_playback_lamp(True, not idle)
            stop_button.configure(
                state="normal" if radio_mix_active else ("disabled" if idle else "normal")
            )
            play_button.configure(state="normal")
            if idle and pending_playlist_url is not None:
                status.set("Checking URL for playlist episodes...")
            elif radio_mix_active and idle:
                status.set("Mix batch finished; loading the next batch...")
                request_radio_mix_batch(radio_mix_next_start)
            elif radio_mix_active:
                status.set(
                    f"YouTube radio mix | {playback_status}"
                    if playback_status is not None
                    else "YouTube radio mix is playing. Use Stop to end it."
                )
            else:
                status.set(
                    playback_status or "MPV is running; waiting for playback status..."
                )
            root.after(2000, check_player, player)
            return

        current_player = None
        current_player_ipc_server = None
        set_playback_lamp(True, False)
        clear_video_info()
        if radio_mix_active and player.returncode == 0:
            status.set("Mix batch finished; loading the next batch...")
            request_radio_mix_batch(radio_mix_next_start)
            return
        if radio_mix_active:
            radio_mix_active = False
        stop_button.configure(state="disabled")
        play_button.configure(state="normal")
        if player.returncode == 0:
            status.set("mpv finished playback.")
        else:
            status.set(
                f"mpv exited with code {player.returncode}; check its window for details."
            )

    def start_playback(url: str, display_url: str) -> bool:
        nonlocal current_player, current_player_ipc_server, playlist_playing_url
        playlist_playing_url = None
        set_playback_lamp(False)
        clear_video_info()
        if not is_http_url(url):
            status.set("Please use a valid HTTP(S) URL.")
            return False
        is_youtube = is_youtube_url(url)
        yt_dlp = None
        node_runtime = None
        if is_youtube:
            try:
                yt_dlp = resolve_tool(
                    None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp"
                )
                node_runtime = resolve_tool(
                    None, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
                )
            except (FileNotFoundError, OSError) as error:
                status.set(f"Could not play URL: {error}")
                messagebox.showerror("Could not play URL", str(error), parent=root)
                return False
        if (
            (current_player is None or current_player.poll() is None)
            and current_player_ipc_server is not None
        ):
            youtube_configured = True
            if is_youtube:
                mpv_available = (
                    _mpv_property_value(
                        current_player_ipc_server, "idle-active"
                    )
                    is not None
                )
                youtube_configured = mpv_available and _configure_mpv_for_youtube(
                    current_player_ipc_server,
                    yt_dlp,
                    node_runtime,
                    _usable_cookie_browser(),
                )
                if mpv_available and not youtube_configured:
                    status.set(
                        "Could not configure the existing MPV for YouTube playback."
                    )
                    return False
            if youtube_configured and _load_mpv_content(
                current_player_ipc_server, ["loadfile", url, "replace"]
            ):
                stop_button.configure(state="normal")
                set_playback_lamp(True, True)
                if _youtube_playlist_url(url) is None:
                    track_active_playlist(
                        (PlaylistEpisode("Video", url),), request_info=False
                    )
                    request_playlist_episode_info(PlaylistEpisode("Video", url))
                else:
                    track_active_playlist((), request_info=False)
                url_entry.delete(0, tk.END)
                url_entry.insert(0, display_url)
                status.set("MPV switched to the requested video.")
                if current_player is None:
                    root.after(2000, check_player, None)
                return True
        if current_player is not None and current_player.poll() is None:
            set_playback_lamp(True, True)
            status.set("Could not reach the current MPV player; leaving it open.")
            return False
        stop_current_player()
        status.set("URL validated. Starting mpv...")
        root.update_idletasks()
        try:
            mpv = resolve_tool(None, "DLPPLAYER_MPV", MPV_DEFAULT, "mpv")
            if yt_dlp is None:
                yt_dlp = resolve_tool(
                    None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp"
                )
            if is_youtube and node_runtime is None:
                node_runtime = resolve_tool(
                    None, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
                )
            ipc_server = _mpv_ipc_server_path()
            cookie_browser = _usable_cookie_browser()
            command = build_mpv_url_command(
                mpv,
                yt_dlp,
                url,
                node_runtime,
                ipc_server=ipc_server,
                use_cookies=cookie_browser is not None,
                cookie_browser=cookie_browser or COOKIE_BROWSER,
            )
            player = subprocess.Popen(command)
        except (FileNotFoundError, OSError) as error:
            status.set(f"Could not start mpv: {error}")
            messagebox.showerror("Could not play URL", str(error), parent=root)
            return False
        current_player = player
        current_player_ipc_server = ipc_server
        stop_button.configure(state="normal")
        set_playback_lamp(True, player.poll() is None)
        if _youtube_playlist_url(url) is None:
            track_active_playlist(
                (PlaylistEpisode("Video", url),), request_info=False
            )
            comments_loader = (
                (
                    lambda: fetch_youtube_video_comments(
                        yt_dlp, url, node_runtime
                    )
                )
                if is_youtube_url(url)
                else None
            )
            request_video_info(
                lambda: fetch_url_video_info(yt_dlp, url, node_runtime),
                url,
                comments_loader,
            )
        else:
            track_active_playlist((), request_info=False)
        url_entry.delete(0, tk.END)
        url_entry.insert(0, display_url)
        status.set("mpv started successfully; yt-dlp is resolving the stream.")
        root.after(2000, check_player, player)
        return True

    def start_playlist_playback(display_url: str, start_index: int = 0) -> None:
        nonlocal current_player, current_player_ipc_server, playlist_playing_url
        if not 0 <= start_index < len(playlist_episodes):
            status.set("Choose an episode from the list.")
            return
        episodes = playlist_episodes[start_index:]
        targets = tuple(episode.target for episode in episodes)
        first_episode = episodes[0]
        missing_path = next(
            (
                Path(target)
                for target in targets
                if not is_http_url(target) and not Path(target).is_file()
            ),
            None,
        )
        if missing_path is not None:
            status.set(f"Local media file was not found: {missing_path}")
            return

        has_youtube_targets = any(is_youtube_url(target) for target in targets)
        yt_dlp = None
        node_runtime = None
        if has_youtube_targets:
            try:
                yt_dlp = resolve_tool(
                    None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp"
                )
                node_runtime = resolve_tool(
                    None, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
                )
            except (FileNotFoundError, OSError) as error:
                status.set(f"Could not play playlist: {error}")
                messagebox.showerror(
                    "Could not play playlist", str(error), parent=root
                )
                return

        if (
            (current_player is None or current_player.poll() is None)
            and current_player_ipc_server is not None
        ):
            youtube_configured = True
            if has_youtube_targets:
                mpv_available = (
                    _mpv_property_value(
                        current_player_ipc_server, "idle-active"
                    )
                    is not None
                )
                youtube_configured = mpv_available and _configure_mpv_for_youtube(
                    current_player_ipc_server,
                    yt_dlp,
                    node_runtime,
                    _usable_cookie_browser(),
                )
                if mpv_available and not youtube_configured:
                    status.set(
                        "Could not configure the existing MPV for YouTube playback."
                    )
                    return
            try:
                _write_mpv_playlist(playlist_manifest_path, episodes)
                jumped = youtube_configured and _load_mpv_content(
                    current_player_ipc_server,
                    ["loadlist", str(playlist_manifest_path), "replace"],
                )
            except OSError:
                jumped = False
            if jumped:
                playlist_playing_url = (
                    display_url if is_http_url(display_url) else None
                )
                episode_combo.current(start_index)
                set_playback_lamp(True, True)
                url_entry.delete(0, tk.END)
                url_entry.insert(0, display_url)
                clear_video_info()
                track_active_playlist(episodes, offset=start_index)
                if current_player is None:
                    root.after(2000, check_player, None)
                status.set(
                    f"MPV jumped to {first_episode.title}; "
                    f"{len(targets)} episodes queued."
                )
                return

        if current_player is not None and current_player.poll() is None:
            set_playback_lamp(True, True)
            status.set("Could not switch episodes; the current MPV player was left open.")
            return
        stop_current_player()
        playlist_playing_url = None
        set_playback_lamp(False)
        clear_video_info()
        status.set("Starting playlist in mpv...")
        root.update_idletasks()
        try:
            mpv = resolve_tool(None, "DLPPLAYER_MPV", MPV_DEFAULT, "mpv")
            ipc_server = _mpv_ipc_server_path()
            _write_mpv_playlist(playlist_manifest_path, episodes)
            if any(is_http_url(target) for target in targets):
                if yt_dlp is None:
                    yt_dlp = resolve_tool(
                        None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp"
                    )
                cookie_browser = _usable_cookie_browser()
                command = _build_mpv_playlist_command(
                    mpv,
                    yt_dlp,
                    targets,
                    node_runtime,
                    playlist_file=str(playlist_manifest_path),
                    ipc_server=ipc_server,
                    keep_open=True,
                    use_cookies=cookie_browser is not None,
                    cookie_browser=cookie_browser or COOKIE_BROWSER,
                )
            else:
                command = [
                    mpv,
                    "--pause=no",
                    f"--input-ipc-server={ipc_server}",
                    "--idle=yes",
                    "--force-window=yes",
                    f"--playlist={playlist_manifest_path}",
                ]
            player = subprocess.Popen(command)
        except (FileNotFoundError, OSError) as error:
            status.set(f"Could not start playlist: {error}")
            messagebox.showerror("Could not play playlist", str(error), parent=root)
            return

        current_player = player
        current_player_ipc_server = ipc_server
        stop_button.configure(state="normal")
        playlist_playing_url = (
            display_url if is_http_url(display_url) else None
        )
        episode_combo.current(start_index)
        set_playback_lamp(True, player.poll() is None)
        url_entry.delete(0, tk.END)
        url_entry.insert(0, display_url)
        track_active_playlist(episodes, offset=start_index, request_info=False)
        request_playlist_episode_info(first_episode, yt_dlp, node_runtime)
        status.set(
            f"mpv started; playing {first_episode.title}; "
            f"{len(targets)} episodes queued in order."
        )
        root.after(2000, check_player, player)

    def play_selected_episode(display_url: str) -> None:
        nonlocal radio_mix_request, radio_mix_url, radio_mix_loading, radio_mix_active
        episode_index = episode_combo.current()
        if not 0 <= episode_index < len(playlist_episodes):
            status.set("Choose an episode from the list.")
            return
        if radio_mix_active:
            radio_mix_request += 1
            radio_mix_url = None
            radio_mix_loading = False
            radio_mix_active = False
        start_playlist_playback(display_url, episode_index)

    def on_episode_selected(_event: object) -> None:
        display_url = loaded_playlist_url
        if display_url is None and loaded_playlist_path is not None:
            display_url = str(loaded_playlist_path)
        if display_url is not None:
            play_selected_episode(display_url)

    def request_radio_mix_batch(start: int) -> None:
        nonlocal radio_mix_loading
        if not radio_mix_active or radio_mix_url is None or radio_mix_loading:
            return
        radio_mix_loading = True
        request_id = radio_mix_request
        url = radio_mix_url
        yt_dlp = radio_mix_yt_dlp
        node_runtime = radio_mix_node_runtime

        def inspect_batch() -> None:
            try:
                if yt_dlp is None:
                    raise FileNotFoundError("yt-dlp was not found.")
                episodes = _fetch_playlist_episodes(
                    yt_dlp,
                    url,
                    node_runtime,
                    (start, start + RADIO_MIX_BATCH_SIZE - 1),
                )
                error_message = None
            except Exception as error:
                episodes = None
                error_message = str(error) or "yt-dlp could not read the mix."
            radio_mix_results.put((request_id, start, episodes, error_message))

        threading.Thread(target=inspect_batch, daemon=True).start()

    def start_radio_mix(url: str, yt_dlp: str, node_runtime: str | None) -> None:
        nonlocal radio_mix_request, radio_mix_url, radio_mix_next_start
        nonlocal radio_mix_loading, radio_mix_active, radio_mix_yt_dlp
        nonlocal radio_mix_node_runtime, pending_playlist_url
        radio_mix_request += 1
        radio_mix_url = url
        radio_mix_next_start = 1
        radio_mix_loading = False
        radio_mix_active = True
        radio_mix_yt_dlp = yt_dlp
        radio_mix_node_runtime = node_runtime
        pending_playlist_url = None
        play_button.configure(state="disabled")
        stop_button.configure(state="normal")
        status.set("Loading the first YouTube radio mix batch...")
        request_radio_mix_batch(1)


    def play_url(url: str) -> None:
        nonlocal pending_playlist_url, playlist_playing_url
        if not is_http_url(url):
            clear_playlist_selection()
            status.set("Please use a valid HTTP(S) URL.")
            return
        youtube_playlist = _youtube_playlist_url(url)
        playlist_id = (
            parse_qs(urlsplit(youtube_playlist).query).get("list", [""])[0]
            if youtube_playlist is not None
            else ""
        )
        is_radio_mix = playlist_id.startswith("RD")
        if is_radio_mix and radio_mix_active and radio_mix_url == url:
            return
        if not is_radio_mix and loaded_playlist_url == url and playlist_episodes:
            play_selected_episode(url)
            return
        if pending_playlist_url == url:
            return

        clear_playlist_selection()
        request_id = playlist_request
        pending_playlist_url = url
        set_playback_lamp(False)
        clear_video_info()
        url_entry.delete(0, tk.END)
        url_entry.insert(0, url)
        status.set("Checking URL for playlist episodes...")
        root.update_idletasks()
        if is_radio_mix:
            if _youtube_video_url(url) is None:
                pending_playlist_url = None
                status.set("This YouTube mix URL has no selected video to play.")
                return
            try:
                yt_dlp = resolve_tool(
                    None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp"
                )
                node_runtime = resolve_tool(
                    None, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
                )
            except FileNotFoundError as error:
                pending_playlist_url = None
                status.set(f"Could not inspect URL: {error}")
                messagebox.showerror("Could not inspect URL", str(error), parent=root)
                return
            start_radio_mix(url, yt_dlp, node_runtime)
            return
        if youtube_playlist is not None:
            if not start_playback(youtube_playlist, url):
                pending_playlist_url = None
                play_button.configure(state="normal")
                return
            playlist_playing_url = url
            status.set("mpv is playing the YouTube playlist while loading its episodes.")
        elif is_youtube_url(url):
            pending_playlist_url = None
            start_playback(url, url)
            return
        elif not _should_inspect_playlist_first(url):
            pending_playlist_url = None
            start_playback(url, url)
            return
        try:
            yt_dlp = resolve_tool(None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp")
            node_runtime = None
            if is_youtube_url(url):
                node_runtime = resolve_tool(
                    None, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
                )
        except FileNotFoundError as error:
            pending_playlist_url = None
            if playlist_playing_url == url:
                status.set("mpv started; episode details are unavailable.")
            else:
                status.set(f"Could not inspect URL: {error}")
                messagebox.showerror("Could not inspect URL", str(error), parent=root)
            return
        if playlist_playing_url != url:
            play_button.configure(state="disabled")

        def inspect_playlist() -> None:
            try:
                episodes = fetch_playlist_episodes(
                    yt_dlp, youtube_playlist or url, node_runtime
                )
                error_message = None
            except Exception as error:
                episodes = None
                error_message = str(error) or "yt-dlp could not inspect the URL."
            playlist_results.put((request_id, url, episodes, error_message))

        threading.Thread(target=inspect_playlist, daemon=True).start()



    def poll_playlist_results() -> None:
        nonlocal pending_playlist_url, loaded_playlist_url, playlist_playing_url
        nonlocal loaded_playlist_path, playlist_episodes
        while True:
            try:
                request_id, url, episodes, error_message = playlist_results.get_nowait()
            except queue.Empty:
                break
            if request_id != playlist_request:
                continue
            pending_playlist_url = None
            play_button.configure(state="normal")
            if extract_http_url(url_entry.get()) != url:
                clear_playlist_selection()
                status.set("URL changed. Press Play in mpv to inspect it.")
                continue
            if error_message is not None:
                if playlist_playing_url == url:
                    status.set("Episode list unavailable; mpv playback was started.")
                else:
                    playback_url = _youtube_playlist_url(url) or url
                    if start_playback(playback_url, url):
                        playlist_playing_url = url
                continue
            if episodes is None or not episodes:
                if playlist_playing_url != url:
                    playback_url = _youtube_playlist_url(url) or url
                    if start_playback(playback_url, url):
                        playlist_playing_url = url
                elif not episodes:
                    status.set("No episode list was returned; mpv playback continues.")
                continue
            loaded_playlist_url = url
            loaded_playlist_path = None
            show_playlist_episodes(episodes)
            if playlist_playing_url != url:
                start_playlist_playback(url, _playlist_start_index(url, episodes))
            else:
                current_position = (
                    _mpv_property_value(current_player_ipc_server, "playlist-pos")
                    if current_player_ipc_server is not None
                    else None
                )
                if _playlist_episode_at_position(episodes, current_position) is None:
                    current_position = 0
                track_active_playlist(episodes, position=current_position)


        while True:
            try:
                request_id, index, episode = episode_metadata_results.get_nowait()
            except queue.Empty:
                break
            if request_id != playlist_request or loaded_playlist_path is None:
                continue
            if index >= len(playlist_episodes):
                continue
            if playlist_episodes[index].target != episode.target:
                continue
            selected_index = episode_combo.current()
            updated_episodes = list(playlist_episodes)
            updated_episodes[index] = episode
            playlist_episodes = tuple(updated_episodes)
            episode_combo.configure(
                values=tuple(
                    _playlist_episode_label(item_index, item)
                    for item_index, item in enumerate(playlist_episodes, start=1)
                )
            )
            if selected_index >= 0:
                episode_combo.current(selected_index)
        root.after(100, poll_playlist_results)

    def poll_radio_mix_results() -> None:
        nonlocal radio_mix_loading, radio_mix_active, radio_mix_next_start
        nonlocal loaded_playlist_url, loaded_playlist_path
        while True:
            try:
                request_id, start, episodes, error_message = radio_mix_results.get_nowait()
            except queue.Empty:
                break
            if request_id != radio_mix_request or not radio_mix_active:
                continue
            radio_mix_loading = False
            end = start + RADIO_MIX_BATCH_SIZE - 1
            if error_message is not None:
                radio_mix_active = False
                stop_button.configure(state="disabled")
                play_button.configure(state="normal")
                status.set(
                    f"Could not load YouTube mix items {start}-{end}: "
                    f"{error_message}"
                )
                continue
            if not episodes:
                radio_mix_active = False
                stop_button.configure(state="disabled")
                play_button.configure(state="normal")
                status.set("No more items are available in this YouTube radio mix.")
                continue
            radio_mix_next_start = start + RADIO_MIX_BATCH_SIZE
            loaded_playlist_url = radio_mix_url
            loaded_playlist_path = None
            show_playlist_episodes(episodes)
            if radio_mix_url is not None:
                start_playlist_playback(radio_mix_url)
            if current_player is None:
                radio_mix_active = False
                stop_button.configure(state="disabled")
                play_button.configure(state="normal")
        root.after(100, poll_radio_mix_results)

    def play_local_files(paths: tuple[Path, ...]) -> None:
        nonlocal current_player, current_player_ipc_server, loaded_playlist_path
        clear_playlist_selection()
        set_playback_lamp(False)
        clear_video_info()
        missing_path = next((path for path in paths if not path.is_file()), None)
        if missing_path is not None:
            status.set(f"Local media file was not found: {missing_path}")
            return
        if not paths:
            status.set("Drop a local media file, playlist, or HTTP(S) URL.")
            return
        if len(paths) == 1:
            playlist_file = paths[0]
            try:
                episodes = read_local_playlist_episodes(playlist_file)
            except OSError as error:
                status.set(f"Could not read local playlist: {error}")
                return
            if episodes is not None:
                loaded_playlist_path = playlist_file.resolve()
                url_entry.delete(0, tk.END)
                url_entry.insert(0, str(loaded_playlist_path))
                show_playlist_episodes(episodes)
                request_episode_metadata(episodes, playlist_request)
                start_playlist_playback(str(loaded_playlist_path))
                return
        status.set("Starting local media in mpv...")
        root.update_idletasks()

        local_episodes = tuple(
            PlaylistEpisode(path.stem, str(path.resolve())) for path in paths
        )
        if (
            (current_player is None or current_player.poll() is None)
            and current_player_ipc_server is not None
        ):
            try:
                _write_mpv_playlist(playlist_manifest_path, local_episodes)
                reused = _load_mpv_content(
                    current_player_ipc_server,
                    ["loadlist", str(playlist_manifest_path), "replace"],
                )
            except OSError:
                reused = False
            if reused:
                stop_button.configure(state="normal")
                set_playback_lamp(True, True)
                track_active_playlist(local_episodes, request_info=False)
                first_file = paths[0].resolve()
                request_video_info(
                    lambda: fetch_local_video_info(
                        resolve_ffprobe_tool(), first_file
                    )
                )
                status.set("MPV switched to local media.")
                if current_player is None:
                    root.after(2000, check_player, None)
                return
        if current_player is not None and current_player.poll() is None:
            set_playback_lamp(True, True)
            status.set("Could not reach the current MPV player; leaving it open.")
            return
        stop_current_player()
        try:
            mpv = resolve_tool(None, "DLPPLAYER_MPV", MPV_DEFAULT, "mpv")
            ipc_server = _mpv_ipc_server_path()
            _write_mpv_playlist(playlist_manifest_path, local_episodes)
            command = [
                mpv,
                "--pause=no",
                "--idle=yes",
                "--force-window=yes",
                f"--input-ipc-server={ipc_server}",
                f"--playlist={playlist_manifest_path}",
            ]
            player = subprocess.Popen(command)
        except (FileNotFoundError, OSError) as error:
            status.set(f"Could not start mpv: {error}")
            messagebox.showerror("Could not play local media", str(error), parent=root)
            return
        current_player = player
        current_player_ipc_server = ipc_server
        stop_button.configure(state="normal")
        set_playback_lamp(True, player.poll() is None)
        track_active_playlist(local_episodes, request_info=False)
        first_file = paths[0].resolve()
        request_video_info(
            lambda: fetch_local_video_info(resolve_ffprobe_tool(), first_file)
        )
        status.set("mpv started successfully; local media playback is starting.")
        root.after(2000, check_player, player)

    def on_drop(event: object) -> str:
        payload = str(getattr(event, "data", ""))
        dropped_url = extract_http_url(payload)
        if dropped_url is not None:
            play_url(dropped_url)
        else:
            try:
                paths = extract_local_paths(payload, root.tk.splitlist)
            except tk.TclError:
                paths = ()
            play_local_files(paths)
        return "break"

    for widget in (root, drop_area):
        widget.drop_target_register(DND_ALL)
        widget.dnd_bind("<<Drop>>", on_drop)

    def play_entry() -> None:
        text = url_entry.get().strip()
        url = extract_http_url(text)
        if url is not None:
            play_url(url)
            return
        if loaded_playlist_path is not None and text == str(loaded_playlist_path):
            play_selected_episode(text)
            return
        local_path = Path(text).expanduser()
        if local_path.is_file():
            play_local_files((local_path,))
            return
        clear_playlist_selection()
        status.set("Enter a valid HTTP(S) URL or local media path.")

    episode_combo.bind("<<ComboboxSelected>>", on_episode_selected)
    play_button.configure(command=play_entry)
    stop_button.configure(command=stop_playback)
    status_row = ttk.Frame(frame)
    status_row.pack(fill="x", pady=(10, 0))
    status_label = ttk.Label(status_row, wraplength=380)
    status_label.pack(side="left", fill="x", expand=True)
    progress_frame = ttk.Frame(status_row)
    progress_bar = ttk.Progressbar(
        progress_frame,
        orient="horizontal",
        mode="determinate",
        maximum=100,
        length=140,
    )
    progress_bar.pack(fill="x", expand=True)
    progress_percentage = ttk.Label(progress_frame, text="", anchor="center")
    progress_percentage.place(relx=0.5, rely=0.5, anchor="center")
    duration_label = ttk.Label(status_row, anchor="e")

    def update_status_display(*_args: str) -> None:
        current_status = status.get()
        progress = _playback_progress_display(current_status)
        if progress is None:
            status_label.configure(text=current_status)
            progress_frame.pack_forget()
            duration_label.pack_forget()
            return
        status_text, percentage = progress
        status_label.configure(text=status_text)
        progress_bar.configure(value=percentage)
        progress_percentage.configure(text=f"{percentage}%")
        duration = _playback_duration(current_status)
        if duration is None:
            duration_label.pack_forget()
        else:
            duration_label.configure(text=duration)
            if not duration_label.winfo_manager():
                duration_label.pack(side="right", padx=(8, 0))
        if not progress_frame.winfo_manager():
            progress_frame.pack(side="right", padx=(8, 0))

    status.trace_add("write", update_status_display)
    update_status_display()

    lamp_background = (
        ttk.Style(root).lookup("TFrame", "background") or root.cget("background")
    )
    lamp_canvas = tk.Canvas(
        status_row,
        width=18,
        height=18,
        borderwidth=0,
        highlightthickness=0,
        background=lamp_background,
    )
    lamp_playing = lamp_canvas.create_polygon(
        4, 3, 15, 9, 4, 15, fill="#000000", outline="#000000", state="hidden"
    )
    lamp_stopped = lamp_canvas.create_rectangle(
        4, 4, 14, 14, fill="#000000", outline="#000000", state="hidden"
    )
    root.after(100, poll_playlist_results)
    root.after(100, poll_radio_mix_results)
    root.mainloop()
    playlist_tempdir.cleanup()
    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DLPPlayer: download videos and play them with mpv.")
    commands = parser.add_subparsers(dest="command", required=True)

    gui_parser = commands.add_parser("gui", help="Open the drag-and-drop player window.")
    gui_parser.set_defaults(handler=launch_gui)

    download_parser = commands.add_parser(
        "download", help="Download one video URL supported by yt-dlp."
    )
    download_parser.add_argument("url", help="HTTP(S) video URL supported by yt-dlp")
    download_parser.add_argument(
        "-o", "--output-dir", type=Path, default=OUTPUT_DEFAULT, help="download folder"
    )
    download_parser.add_argument(
        "--cookie-browser",
        help="browser to read cookies from (default: the cookie_browser setting, "
        "Brave)",
    )
    download_parser.add_argument(
        "--cookie-profile",
        help="profile name or path for the cookie browser (default: its default profile)",
    )
    download_parser.add_argument(
        "--no-cookies", action="store_true", help="do not read browser cookies"
    )
    download_parser.add_argument("--play", action="store_true", help="play the downloaded video in mpv")
    download_parser.add_argument("--yt-dlp", help="path to yt-dlp executable")
    download_parser.add_argument("--ffmpeg", help="path to ffmpeg executable or its directory")
    download_parser.add_argument("--mpv", help="path to mpv executable")
    download_parser.add_argument("--node", help="path to Node.js executable for YouTube")
    download_parser.add_argument(
        "-f",
        "--format",
        dest="video_format",
        default=VIDEO_FORMAT_DEFAULT,
        help="yt-dlp format selector (default: configured maximum resolution plus audio)",
    )
    download_parser.set_defaults(handler=download)

    play_parser = commands.add_parser("play", help="Play a downloaded media file with mpv.")
    play_parser.add_argument("file", help="path to a local media file")
    play_parser.add_argument("--mpv", help="path to mpv executable")
    play_parser.set_defaults(handler=play)

    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        return launch_gui()
    args = create_parser().parse_args(arguments)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
