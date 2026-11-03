"""Download Youtube/Bilibili videos with yt-dlp and optionally play them with mpv."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


YTDLP_DEFAULT = Path(r"D:\bin\yt-dlp\yt-dlp.exe")
MPV_DEFAULT = Path(r"D:\bin\mpv\mpv.exe")
FFMPEG_DEFAULT = Path(r"D:\bin\ffmpeg\bin")
NODE_DEFAULT = Path(r"D:\bin\nodejs\node.exe")
VIDEO_FORMAT_DEFAULT = "bv*[height<=1080]+ba/b[height<=1080]"
YOUTUBE_COMMENT_FETCH_LIMIT = 100
VIDEO_COMMENT_DISPLAY_LIMIT = 100
BILIBILI_COMMENT_PAGE_SIZE = 20
BILIBILI_COMMENT_TIMEOUT = 5
OUTPUT_DEFAULT = Path(__file__).resolve().parent


URL_PATTERN = re.compile(r"https?://[^\s{}<>\"']+", re.IGNORECASE)


def is_supported_url(url: str) -> bool:
    if not is_http_url(url):
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False

    bilibili_host = (
        host == "bilibili.com"
        or host.endswith(".bilibili.com")
        or host in {"b23.tv", "bilibili.tv"}
        or host.endswith(".bilibili.tv")
    )
    youtube_host = (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )
    return bilibili_host or youtube_host


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


def resolve_tool(
    option: str | None,
    environment_name: str,
    default: Path,
    name: str,
    allow_directory: bool = False,
) -> str:
    configured = option or os.environ.get(environment_name)
    if configured:
        candidate = Path(configured)
        if candidate.is_file() or (allow_directory and candidate.is_dir()):
            return str(candidate)
        located = shutil.which(configured)
        if located:
            return located
        raise FileNotFoundError(f"{name} was not found at {configured!r}.")

    if default.is_file() or (allow_directory and default.is_dir()):
        return str(default)
    located = shutil.which(name)
    if located:
        return located
    raise FileNotFoundError(
        f"{name} was not found. Install it or pass its path with --{name}."
    )


def build_download_command(
    yt_dlp: str,
    ffmpeg_location: str,
    output_dir: Path,
    url: str,
    firefox_profile: str | None = None,
    use_cookies: bool = True,
    result_file: str | None = None,
    video_format: str = VIDEO_FORMAT_DEFAULT,
    node_runtime: str | None = None,
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
    if is_youtube_url(url):
        runtime = f"node:{node_runtime}" if node_runtime else "node"
        command.extend(["--js-runtimes", runtime])
    if use_cookies:
        browser = "firefox"
        if firefox_profile:
            browser += f":{firefox_profile}"
        command.extend(["--cookies-from-browser", browser])
    if result_file:
        command.extend(["--print-to-file", "after_move:filepath", result_file])
    command.append(url)
    return command


def build_mpv_url_command(
    mpv: str,
    yt_dlp: str,
    url: str,
    node_runtime: str | None = None,
    firefox_profile: str | None = None,
    use_cookies: bool = True,
) -> list[str]:
    command = [
        mpv,
        f"--script-opts=ytdl_hook-ytdl_path={yt_dlp}",
        f"--ytdl-format={VIDEO_FORMAT_DEFAULT}",
    ]
    raw_options = []
    if use_cookies:
        browser = "firefox"
        if firefox_profile:
            browser += f":{firefox_profile}"
        raw_options.append(f"cookies-from-browser={browser}")
    if is_youtube_url(url):
        runtime = f"node:{node_runtime}" if node_runtime else "node"
        raw_options.append(f"js-runtimes={runtime}")
    if raw_options:
        command.append(f"--ytdl-raw-options={','.join(raw_options)}")
    command.append(url)
    return command


def build_video_info_command(
    yt_dlp: str,
    url: str,
    node_runtime: str | None = None,
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
        "firefox",
    ]
    if is_youtube_url(url):
        runtime = f"node:{node_runtime}" if node_runtime else "node"
        command.extend(["--write-comments", "--js-runtimes", runtime])
        command.extend(
            [
                "--extractor-args",
                f"youtube:comment_sort=top;max_comments={YOUTUBE_COMMENT_FETCH_LIMIT}",
            ]
        )
    command.append(url)
    return command


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

    try:
        seconds = int(float(duration))
        if seconds < 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        pass
    else:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        summary.append(
            f"{hours}:{minutes:02}:{seconds:02}"
            if hours
            else f"{minutes}:{seconds:02}"
        )

    details = ([" · ".join(summary)] if summary else []) + list(additional_details)
    return VideoInfo(display_title, tuple(details), comments)


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
    result = subprocess.run(
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
    if is_supported_url(url) and not is_youtube_url(url):
        metadata["comments"] = _fetch_bilibili_comments(url, metadata)
    return video_info_from_ytdlp(metadata)


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
    if not is_supported_url(args.url):
        print("Error: URL must be from Bilibili or YouTube.", file=sys.stderr)
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
        mpv = None
        if args.play:
            mpv = resolve_tool(args.mpv, "DLPPLAYER_MPV", MPV_DEFAULT, "mpv")
        args.output_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="dlpplayer-") as temporary_directory:
            result_file = str(Path(temporary_directory) / "downloaded-path.txt")
            command = build_download_command(
                yt_dlp,
                ffmpeg,
                args.output_dir,
                args.url,
                args.firefox_profile,
                not args.no_cookies,
                result_file,
                args.video_format,
                node_runtime,
            )
            result = subprocess.run(command, check=False)
            if result.returncode:
                return result.returncode

            if args.play and mpv:
                result_path = Path(result_file)
                if not result_path.is_file():
                    print("Error: yt-dlp completed but did not report the downloaded file.", file=sys.stderr)
                    return 1
                downloaded_file = Path(result_path.read_text(encoding="utf-8").strip())
                if not downloaded_file.is_file():
                    print(f"Error: downloaded file was not found: {downloaded_file}", file=sys.stderr)
                    return 1
                return subprocess.run([mpv, str(downloaded_file)], check=False).returncode

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
        mpv = resolve_tool(args.mpv, "DLPPLAYER_MPV", MPV_DEFAULT, "mpv")
        return subprocess.run([mpv, str(media_file.resolve())], check=False).returncode
    except (FileNotFoundError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def launch_gui(_args: argparse.Namespace | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext, ttk

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
    root.title("DLPPlayer — Drop a video, playlist, or link")
    root.geometry("600x460")
    root.minsize(460, 320)

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
    play_button = ttk.Button(url_row, text="Play in mpv")
    play_button.pack(side="right", padx=(8, 0))

    info_frame = ttk.LabelFrame(frame, text="Video information", padding=8)
    video_title = tk.StringVar(value="")
    video_details = tk.StringVar(value="")
    ttk.Label(
        info_frame,
        textvariable=video_title,
        font=("Segoe UI", 16, "bold"),
        wraplength=520,
    ).pack(anchor="w", pady=(0, 6))
    ttk.Label(
        info_frame,
        textvariable=video_details,
        wraplength=520,
        justify="left",
    ).pack(fill="x", pady=(6, 0))
    comments_frame = ttk.LabelFrame(info_frame, text="Most liked comments", padding=4)
    comments_text = scrolledtext.ScrolledText(
        comments_frame,
        height=20,
        wrap="word",
        font=("Segoe UI", 9),
        state="disabled",
    )
    comments_text.pack(fill="both", expand=True)
    metadata_results: queue.Queue[tuple[int, VideoInfo | None]] = queue.Queue()
    metadata_request = 0

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


    def fit_window_height() -> None:
        root.update_idletasks()
        height = max(root.winfo_reqheight(), 460)
        root.geometry(f"{root.winfo_width()}x{height}")

    def clear_video_info() -> None:
        nonlocal metadata_request
        metadata_request += 1
        info_frame.pack_forget()
        video_title.set("")
        video_details.set("")
        display_comments(())
        fit_window_height()

    def request_video_info(loader: Callable[[], VideoInfo]) -> None:
        nonlocal metadata_request
        metadata_request += 1
        request_id = metadata_request

        def load_video_info() -> None:
            try:
                info = loader()
            except Exception:
                info = None
            metadata_results.put((request_id, info))

        threading.Thread(target=load_video_info, daemon=True).start()

    def poll_video_info() -> None:
        while True:
            try:
                request_id, info = metadata_results.get_nowait()
            except queue.Empty:
                break
            if request_id != metadata_request or info is None:
                continue
            video_title.set(info.title)
            video_details.set("\n".join(info.details))
            display_comments(info.comments)
            info_frame.pack(before=status_label, fill="x", pady=(10, 0))
            fit_window_height()
        root.after(100, poll_video_info)

    root.after(100, poll_video_info)
    status = tk.StringVar(value="Ready.")
    current_player: subprocess.Popen | None = None

    def check_player(player: subprocess.Popen) -> None:
        if player is not current_player:
            return
        if player.poll() is None:
            status.set("mpv is running; check its window for stream status.")
            root.after(2000, check_player, player)
        else:
            clear_video_info()
            if player.returncode == 0:
                status.set("mpv finished playback.")
            else:
                status.set(
                    f"mpv exited with code {player.returncode}; check its window for details."
                )

    def play_url(url: str) -> None:
        nonlocal current_player
        current_player = None
        clear_video_info()
        status.set("Validating URL...")
        root.update_idletasks()
        if not is_http_url(url):
            status.set("Please use a valid HTTP(S) URL.")
            return
        status.set("URL validated. Starting mpv...")
        root.update_idletasks()
        try:
            mpv = resolve_tool(None, "DLPPLAYER_MPV", MPV_DEFAULT, "mpv")
            yt_dlp = resolve_tool(None, "DLPPLAYER_YTDLP", YTDLP_DEFAULT, "yt-dlp")
            node_runtime = None
            if is_youtube_url(url):
                node_runtime = resolve_tool(
                    None, "DLPPLAYER_NODE", NODE_DEFAULT, "node"
                )
            command = build_mpv_url_command(
                mpv,
                yt_dlp,
                url,
                node_runtime,
            )
            player = subprocess.Popen(command)
        except (FileNotFoundError, OSError) as error:
            status.set(f"Could not start mpv: {error}")
            messagebox.showerror("Could not play URL", str(error), parent=root)
            return
        current_player = player
        request_video_info(lambda: fetch_url_video_info(yt_dlp, url, node_runtime))
        url_entry.delete(0, tk.END)
        url_entry.insert(0, url)
        status.set("mpv started successfully; yt-dlp is resolving the stream.")
        root.after(2000, check_player, player)

    def play_local_files(paths: tuple[Path, ...]) -> None:
        nonlocal current_player
        current_player = None
        clear_video_info()
        missing_path = next((path for path in paths if not path.is_file()), None)
        if missing_path is not None:
            status.set(f"Local media file was not found: {missing_path}")
            return
        if not paths:
            status.set("Drop a local media file, playlist, or HTTP(S) URL.")
            return
        status.set("Starting local media in mpv...")
        root.update_idletasks()
        try:
            mpv = resolve_tool(None, "DLPPLAYER_MPV", MPV_DEFAULT, "mpv")
            command = [mpv, *(str(path.resolve()) for path in paths)]
            player = subprocess.Popen(command)
        except (FileNotFoundError, OSError) as error:
            status.set(f"Could not start mpv: {error}")
            messagebox.showerror("Could not play local media", str(error), parent=root)
            return
        current_player = player
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
        url = extract_http_url(url_entry.get())
        if url is None:
            status.set("Enter a valid HTTP(S) URL.")
            return
        play_url(url)

    play_button.configure(command=play_entry)
    status_label = ttk.Label(frame, textvariable=status, wraplength=460)
    status_label.pack(fill="x", pady=(10, 0))
    root.mainloop()
    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DLPPlayer: download videos and play them with mpv.")
    commands = parser.add_subparsers(dest="command", required=True)

    gui_parser = commands.add_parser("gui", help="Open the drag-and-drop player window.")
    gui_parser.set_defaults(handler=launch_gui)

    download_parser = commands.add_parser("download", help="Download one Bilibili or YouTube video.")
    download_parser.add_argument("url", help="Bilibili or YouTube video URL")
    download_parser.add_argument(
        "-o", "--output-dir", type=Path, default=OUTPUT_DEFAULT, help="download folder"
    )
    download_parser.add_argument(
        "--firefox-profile", help="Firefox profile name or path (default: Firefox's default profile)"
    )
    download_parser.add_argument(
        "--no-cookies", action="store_true", help="do not read cookies from Firefox"
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
        help="yt-dlp format selector (default: best video up to 1080p plus audio)",
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
