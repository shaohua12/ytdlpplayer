import argparse
import tempfile
import tkinter
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dlpplayer
from dlpplayer import (
    OUTPUT_DEFAULT,
    VIDEO_FORMAT_DEFAULT,
    build_download_command,
    build_mpv_url_command,
    build_video_info_command,
    create_parser,
    extract_http_url,
    extract_local_paths,
    format_video_info,
    is_http_url,
    is_supported_url,
    video_info_from_ffprobe,
    video_info_from_ytdlp,
)


class BilibiliUrlTests(unittest.TestCase):
    def test_accepts_bilibili_video_and_short_links(self):
        self.assertTrue(is_supported_url("https://www.bilibili.com/video/BV123"))
        self.assertTrue(is_supported_url("https://b23.tv/abc"))
        self.assertTrue(is_supported_url("https://www.bilibili.tv/video/123"))

    def test_accepts_youtube_video_and_short_links(self):
        self.assertTrue(is_supported_url("https://www.youtube.com/watch?v=BaW_jenozKc"))
        self.assertTrue(is_supported_url("https://youtu.be/BaW_jenozKc"))
        self.assertTrue(is_supported_url("https://music.youtube.com/watch?v=BaW_jenozKc"))

    def test_rejects_other_hosts_and_malformed_urls(self):
        self.assertFalse(is_supported_url("https://bilibili.com.attacker.example/video/1"))
        self.assertFalse(is_supported_url("https://youtube.com.attacker.example/watch?v=x"))
        self.assertFalse(is_supported_url("file:///tmp/video.mp4"))
        self.assertFalse(is_supported_url("not a url"))

    def test_extracts_any_http_url_from_drag_payload(self):
        url = "https://www.bilibili.com/video/BV123"
        self.assertEqual(extract_http_url(f"Link to video: {url} (open)"), url)

    def test_extracts_non_bilibili_http_url_from_drag_payload(self):
        url = "https://example.com/video"
        self.assertEqual(extract_http_url(url), url)

    def test_ignores_non_http_urls_in_drag_payload(self):
        self.assertIsNone(extract_http_url("ftp://example.com/video"))
        self.assertFalse(is_http_url("not a URL"))

    def test_extracts_local_video_and_playlist_drop_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "local video.mp4"
            playlist = Path(temp_dir) / "watch later.m3u"
            video.touch()
            playlist.write_text(str(video), encoding="utf-8")
            interpreter = tkinter.Tcl()
            payload = interpreter.eval(f"list {{{video}}} {{{playlist}}}")

            self.assertEqual(
                extract_local_paths(payload, interpreter.splitlist),
                (video, playlist),
            )

    def test_download_folder_defaults_to_workspace(self):
        args = create_parser().parse_args(
            ["download", "https://www.bilibili.com/video/BV123"]
        )
        self.assertEqual(args.output_dir, OUTPUT_DEFAULT)
        self.assertEqual(OUTPUT_DEFAULT, Path(__file__).resolve().parent)

    def test_running_without_arguments_opens_gui(self):
        with patch.object(dlpplayer, "launch_gui", return_value=0) as launch:
            self.assertEqual(dlpplayer.main([]), 0)
        launch.assert_called_once_with()


class DownloadCommandTests(unittest.TestCase):
    def test_uses_firefox_cookies_and_local_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            command = build_download_command(
                "yt-dlp.exe",
                r"D:\bin\ffmpeg\bin",
                Path(temp_dir),
                "https://www.bilibili.com/video/BV123",
                result_file="result.txt",
            )

        self.assertIn("--cookies-from-browser", command)
        self.assertIn("firefox", command)
        self.assertIn("--ffmpeg-location", command)
        self.assertIn("--print-to-file", command)
        self.assertIn("https://www.bilibili.com/video/BV123", command)
        self.assertIn(temp_dir, command)
        self.assertEqual(command[command.index("-f") + 1], VIDEO_FORMAT_DEFAULT)

    def test_can_skip_cookies_and_choose_firefox_profile(self):
        command = build_download_command(
            "yt-dlp.exe",
            "ffmpeg",
            Path("downloads"),
            "https://www.bilibili.com/video/BV123",
            firefox_profile="default-release",
            use_cookies=False,
        )
        self.assertNotIn("--cookies-from-browser", command)

        command = build_download_command(
            "yt-dlp.exe",
            "ffmpeg",
            Path("downloads"),
            "https://www.bilibili.com/video/BV123",
            firefox_profile="default-release",
        )
        cookie_index = command.index("--cookies-from-browser")
        self.assertEqual(command[cookie_index + 1], "firefox:default-release")

    def test_youtube_command_uses_node_and_custom_format(self):
        command = build_download_command(
            "yt-dlp.exe",
            "ffmpeg",
            Path("downloads"),
            "https://youtu.be/BaW_jenozKc",
            use_cookies=False,
            video_format="bv*[height<=480]+ba/b",
            node_runtime=r"D:\bin\nodejs\node.exe",
        )
        self.assertEqual(
            command[command.index("--js-runtimes") + 1],
            r"node:D:\bin\nodejs\node.exe",
        )
        self.assertEqual(command[command.index("-f") + 1], "bv*[height<=480]+ba/b")

    def test_download_then_play_uses_the_saved_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            yt_dlp = root / "yt-dlp.exe"
            ffmpeg = root / "ffmpeg"
            mpv = root / "mpv.exe"
            output_dir = root / "videos"
            downloaded_file = output_dir / "sample [BV123].mp4"
            for tool in (yt_dlp, ffmpeg, mpv):
                tool.touch()

            def run_process(command, check=False):
                if command[0] == str(yt_dlp):
                    result_file = Path(command[command.index("--print-to-file") + 2])
                    output_dir.mkdir(parents=True, exist_ok=True)
                    downloaded_file.write_bytes(b"test video")
                    result_file.write_text(str(downloaded_file), encoding="utf-8")
                else:
                    self.assertEqual(command, [str(mpv), str(downloaded_file)])
                return SimpleNamespace(returncode=0)

            args = argparse.Namespace(
                url="https://www.bilibili.com/video/BV123",
                yt_dlp=str(yt_dlp),
                ffmpeg=str(ffmpeg),
                mpv=str(mpv),
                output_dir=output_dir,
                firefox_profile=None,
                no_cookies=False,
                play=True,
                video_format="bv*+ba/b",
            )
            with patch.object(dlpplayer.subprocess, "run", side_effect=run_process) as run:
                self.assertEqual(dlpplayer.download(args), 0)

            self.assertEqual(run.call_count, 2)
            self.assertTrue(downloaded_file.is_file())


class MpvUrlCommandTests(unittest.TestCase):
    def test_builds_command_for_generic_http_url(self):
        url = "https://media.example/watch?id=123"
        command = build_mpv_url_command(
            "mpv.exe",
            "yt-dlp.exe",
            url,
            node_runtime="node.exe",
        )

        self.assertEqual(command[-1], url)
        self.assertIn(f"--ytdl-format={VIDEO_FORMAT_DEFAULT}", command)
        self.assertFalse(any("js-runtimes" in item for item in command))


class VideoInfoCommandTests(unittest.TestCase):
    def test_requests_bounded_top_comments(self):
        command = build_video_info_command(
            "yt-dlp.exe",
            "https://youtu.be/BaW_jenozKc",
            node_runtime="node.exe",
        )

        self.assertIn("--write-comments", command)
        self.assertEqual(
            command[command.index("--extractor-args") + 1],
            "youtube:comment_sort=top;max_comments=100",
        )
        bilibili_command = build_video_info_command(
            "yt-dlp.exe", "https://www.bilibili.com/video/BV123"
        )
        self.assertIn("--no-write-comments", bilibili_command)
        self.assertNotIn("--write-comments", bilibili_command)
        self.assertGreater(
            command.index("--write-comments"), command.index("--no-write-comments")
        )


class BilibiliCommentFetchTests(unittest.TestCase):
    def test_fetches_at_most_one_hundred_comments(self):
        requested_pages = []
        requested_oids = []

        def fake_api(endpoint, query, referer):
            requested_pages.append(query["pn"])
            requested_oids.append(query["oid"])
            first_comment = (query["pn"] - 1) * 20
            return {
                "replies": [
                    {
                        "content": {"message": f"Comment {index}"},
                        "member": {"uname": f"User {index}"},
                        "like": index + 1,
                    }
                    for index in range(first_comment, first_comment + 20)
                ]
            }

        with patch.object(dlpplayer, "_bilibili_api_data", side_effect=fake_api):
            comments = dlpplayer._fetch_bilibili_comments(
                "https://www.bilibili.com/video/BV123",
                {
                    "id": "BV123",
                    "_old_archive_ids": ["bilibili 123_part1"],
                },
            )
        info = video_info_from_ytdlp(
            {"title": "Bilibili clip", "vcodec": "h264", "comments": comments}
        )

        self.assertEqual(requested_pages, [1, 2, 3, 4, 5])
        self.assertEqual(requested_oids, ["123"] * 5)
        self.assertEqual(len(info.comments), 100)
        self.assertEqual(
            [comment.like_count for comment in info.comments],
            list(range(100, 0, -1)),
        )



class VideoInformationTests(unittest.TestCase):
    def test_formats_unlabeled_title_resolution_and_duration(self):
        info = format_video_info("  Example title  ", 1920, 1080, 3665.9)
        self.assertEqual(info.title, "Example title")
        self.assertEqual(info.details, ("1920 × 1080 · 1:01:05",))

    def test_reads_ytdlp_format_and_available_video_details(self):
        metadata = {
            "title": "Remote clip",
            "duration": 62.9,
            "format_id": "248+251",
            "format": "248 - 720p+251 - audio",
            "ext": "webm",
            "fps": 30,
            "vcodec": "none",
            "acodec": "none",
            "filesize_approx": None,
            "uploader": "Uploader",
            "upload_date": "20250102",
            "view_count": 1234,
            "like_count": 67,
            "comment_count": 9,
            "categories": ["Gaming"],
            "tags": ["tag one", "tag two"],
            "description": "A video description.",
            "requested_formats": [
                {
                    "vcodec": "none",
                    "acodec": "opus",
                    "format_id": "251",
                    "ext": "webm",
                    "filesize_approx": 250000,
                },
                {
                    "vcodec": "vp9",
                    "acodec": "none",
                    "format_id": "248",
                    "ext": "webm",
                    "width": 1280,
                    "height": 720,
                    "fps": 30,
                    "filesize_approx": 1750000,
                },
            ],
        }
        info = video_info_from_ytdlp(metadata)

        self.assertEqual(info.title, "Remote clip")
        self.assertEqual(
            info.details,
            (
                "1280 × 720 · 1:02",
                "Format 248+251 · 248 - 720p+251 - audio · WEBM · 30 fps",
                "vp9 · opus · 2.0 MB",
                "Uploader · 2025-01-02 · 1,234 views · 67 likes · 9 comments",
                "Categories: Gaming",
                "Tags: tag one · tag two",
                "A video description.",
            ),
        )

    def test_sorts_and_limits_ytdlp_comments_by_like_count(self):
        comments = [
            {"text": f"Comment {likes}", "like_count": likes}
            for likes in range(125)
        ]
        comments.extend(
            [
                {"text": "No like count"},
                {"text": "Invalid likes", "like_count": "unknown"},
                {"text": "Negative likes", "like_count": -10},
                {"text": "   ", "like_count": 1000},
                "invalid comment",
            ]
        )
        info = video_info_from_ytdlp(
            {"title": "Clip", "vcodec": "h264", "comments": comments}
        )

        self.assertEqual(len(info.comments), 100)
        self.assertEqual(
            [(comment.text, comment.like_count) for comment in info.comments],
            [(f"Comment {likes}", likes) for likes in range(124, 24, -1)],
        )

    def test_sorts_unrated_ytdlp_comments_after_liked_comments(self):
        comments = [
            {"text": "Low", "like_count": 2},
            {"text": "Top", "like_count": 12},
            {"text": "Missing likes"},
            {"text": "Invalid likes", "like_count": "unknown"},
            {"text": "Negative likes", "like_count": -4},
            {"text": "   ", "like_count": 1000},
        ]
        info = video_info_from_ytdlp(
            {"title": "Clip", "vcodec": "h264", "comments": comments}
        )

        self.assertEqual(
            [(comment.text, comment.like_count) for comment in info.comments],
            [
                ("Top", 12),
                ("Low", 2),
                ("Missing likes", 0),
                ("Invalid likes", 0),
                ("Negative likes", 0),
            ],
        )

    def test_reads_local_video_stream_and_embedded_metadata(self):
        metadata = {
            "format": {
                "duration": "65.25",
                "format_name": "mov,mp4,m4a",
                "size": "1500000",
                "bit_rate": "1230000",
                "tags": {"title": "Local clip"},
            },
            "streams": [
                {"codec_type": "audio", "codec_name": "aac"},
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 640,
                    "height": 360,
                    "avg_frame_rate": "24000/1001",
                },
            ],
        }
        info = video_info_from_ffprobe(metadata, "fallback")

        self.assertEqual(info.title, "Local clip")
        self.assertEqual(
            info.details,
            (
                "640 × 360 · 1:05",
                "MOV · 23.976 fps",
                "h264 · aac · 1.5 MB · 1,230 kb/s",
            ),
        )

    def test_rejects_audio_only_metadata(self):
        with self.assertRaises(ValueError):
            video_info_from_ytdlp({"title": "Audio only", "vcodec": "none"})

        with self.assertRaises(ValueError):
            video_info_from_ffprobe(
                {"streams": [{"codec_type": "audio"}]}, "audio-only"
            )


if __name__ == "__main__":
    unittest.main()
