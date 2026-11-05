import argparse
import tempfile
import tkinter
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch

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
    video_info_from_ffprobe,
    video_info_from_ytdlp,
)

class HttpUrlTests(unittest.TestCase):

    def test_accepts_bilibili_video_and_short_links(self):
        self.assertTrue(is_http_url("https://www.bilibili.com/video/BV123"))
        self.assertTrue(is_http_url("https://b23.tv/abc"))
        self.assertTrue(is_http_url("https://www.bilibili.tv/video/123"))

    def test_accepts_youtube_video_and_short_links(self):
        self.assertTrue(is_http_url("https://www.youtube.com/watch?v=BaW_jenozKc"))
        self.assertTrue(is_http_url("https://youtu.be/BaW_jenozKc"))
        self.assertTrue(is_http_url("https://music.youtube.com/watch?v=BaW_jenozKc"))

    def test_accepts_other_sites_and_rejects_non_http_urls(self):
        self.assertTrue(is_http_url("https://media.example/watch?id=123"))
        self.assertTrue(is_http_url("https://vimeo.com/123456789"))
        self.assertFalse(is_http_url("file:///tmp/video.mp4"))
        self.assertFalse(is_http_url("not a url"))

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
                url="https://media.example/watch?id=123",
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
            self.assertEqual(
                run.call_args_list[0].args[0][-1],
                "https://media.example/watch?id=123",
            )
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

    def test_builds_mpv_command_with_ipc_server(self):
        ipc_server = r"\\.\pipe\dlpplayer-test"
        command = build_mpv_url_command(
            "mpv.exe",
            "yt-dlp.exe",
            "https://media.example/watch?id=123",
            use_cookies=False,
            ipc_server=ipc_server,
        )

        self.assertIn(f"--input-ipc-server={ipc_server}", command)

    def test_sends_mpvs_playlist_replacement_command_over_ipc(self):
        ipc_server = r"\\.\pipe\dlpplayer-test"
        pipe = mock_open()
        pipe.return_value.write.return_value = len(
            b'{"command":["loadlist","playlist.m3u","replace"]}\n'
        )
        with patch.object(dlpplayer.os, "name", "nt"):
            with patch("builtins.open", pipe):
                sent = dlpplayer._send_mpv_ipc_command(
                    ipc_server, ["loadlist", "playlist.m3u", "replace"]
                )

        self.assertTrue(sent)
        pipe.assert_called_once_with(ipc_server, "r+b", buffering=0)
        pipe().write.assert_called_once_with(
            b'{"command":["loadlist","playlist.m3u","replace"]}\n'
        )

    def test_builds_mpv_command_with_ordered_youtube_items(self):
        urls = (
            "https://www.youtube.com/watch?v=first",
            "https://www.youtube.com/watch?v=second",
        )
        command = dlpplayer._build_mpv_playlist_command(
            "mpv.exe", "yt-dlp.exe", urls, node_runtime="node.exe"
        )

        self.assertEqual(command[-2:], list(urls))
        self.assertIn(
            "--ytdl-raw-options=cookies-from-browser=firefox,js-runtimes=node:node.exe",
            command,
        )

    def test_builds_mpv_command_using_title_manifest(self):
        urls = (
            "https://www.youtube.com/watch?v=first",
            "https://www.youtube.com/watch?v=second",
        )
        command = dlpplayer._build_mpv_playlist_command(
            "mpv.exe",
            "yt-dlp.exe",
            urls,
            node_runtime="node.exe",
            playlist_file="playlist.m3u",
        )

        self.assertEqual(command[-1], "--playlist=playlist.m3u")
        self.assertNotIn(urls[0], command)
        self.assertIn("--ytdl-format=" + VIDEO_FORMAT_DEFAULT, command)

    def test_writes_playlist_titles_and_targets_in_order(self):
        episodes = (
            dlpplayer.PlaylistEpisode(
                "First\ntrack", "https://media.example/1", 75
            ),
            dlpplayer.PlaylistEpisode("Second track", "https://media.example/2"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = Path(temp_dir) / "playlist.m3u"
            dlpplayer._write_mpv_playlist(manifest, episodes)

            lines = manifest.read_text(encoding="utf-8").splitlines()

        self.assertEqual(
            lines,
            [
                "#EXTM3U",
                "#EXTINF:75,First track (1:15)",
                "https://media.example/1",
                "#EXTINF:-1,Second track",
                "https://media.example/2",
            ],
        )

    def test_canonicalizes_youtube_watch_playlist_url(self):
        url = (
            "https://www.youtube.com/watch?v=video1&list=PL123&start_radio=1"
        )

        self.assertEqual(
            dlpplayer._youtube_playlist_url(url),
            "https://www.youtube.com/playlist?list=PL123",
        )
        self.assertIsNone(
            dlpplayer._youtube_playlist_url("https://www.youtube.com/watch?v=video1")
        )

    def test_strips_mix_parameters_from_selected_youtube_video(self):
        url = (
            "https://www.youtube.com/watch?v=video1&list=RDvideo1&start_radio=1"
        )

        self.assertEqual(
            dlpplayer._youtube_video_url(url),
            "https://www.youtube.com/watch?v=video1",
        )
        self.assertIsNone(
            dlpplayer._youtube_video_url("https://www.youtube.com/playlist?list=RDvideo1")
        )


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

    def test_generic_video_info_skips_bilibili_comment_api(self):
        result = SimpleNamespace(
            returncode=0,
            stdout='{"title":"Site video","vcodec":"h264"}',
            stderr="",
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result):
            with patch.object(dlpplayer, "_fetch_bilibili_comments") as fetch_comments:
                info = dlpplayer.fetch_url_video_info(
                    "yt-dlp.exe", "https://media.example/watch?id=123"
                )

        self.assertEqual(info.title, "Site video")
        fetch_comments.assert_not_called()


class PlaylistEpisodeTests(unittest.TestCase):
    def test_builds_flat_playlist_command(self):
        url = "https://www.youtube.com/playlist?list=PL123"
        command = dlpplayer.build_playlist_command(
            "yt-dlp.exe", url, node_runtime="node.exe"
        )

        self.assertIn("--flat-playlist", command)
        self.assertIn("--dump-single-json", command)
        self.assertIn("--js-runtimes", command)
        self.assertNotIn("--playlist-items", command)
        self.assertEqual(command[-1], url)

    def test_fetches_a_bounded_youtube_radio_mix_batch(self):
        url = "https://www.youtube.com/watch?v=seed&list=RDseed&start_radio=1"
        result = SimpleNamespace(
            returncode=0,
            stdout='{"entries":[{"id":"track1","title":"Track one"},'
            '{"id":"track2","title":"Track two"}]}',
            stderr="",
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result) as run:
            episodes = dlpplayer._fetch_playlist_episodes(
                "yt-dlp.exe", url, "node.exe", (11, 20)
            )

        command = run.call_args.args[0]
        self.assertIn("--yes-playlist", command)
        self.assertEqual(
            command[command.index("--playlist-items") + 1], "11-20"
        )
        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "Track one", "https://www.youtube.com/watch?v=track1"
                ),
                dlpplayer.PlaylistEpisode(
                    "Track two", "https://www.youtube.com/watch?v=track2"
                ),
            ),
        )

    def test_builds_single_episode_metadata_command(self):
        url = "https://media.example/play/episode-id/index.m3u8"
        command = dlpplayer.build_episode_metadata_command("yt-dlp.exe", url)

        self.assertIn("--skip-download", command)
        self.assertIn("--no-playlist", command)
        self.assertIn("--cookies-from-browser", command)

    def test_fetches_episode_titles_and_playable_urls(self):
        playlist_url = "https://www.youtube.com/playlist?list=PL123"
        result = SimpleNamespace(
            returncode=0,
            stdout=(
                '{"entries":[{"id":"first","title":"First episode"},'
                '{"webpage_url":"https://youtu.be/second",'
                '"title":"Second episode","duration":62.9},null,{"title":"Unavailable"}]}'
            ),
            stderr="",
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result):
            episodes = dlpplayer.fetch_playlist_episodes("yt-dlp.exe", playlist_url)

        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "First episode", "https://www.youtube.com/watch?v=first"
                ),
                dlpplayer.PlaylistEpisode(
                    "Second episode", "https://youtu.be/second", 62
                ),
            ),
        )

    def test_fetches_generic_site_playlist_urls_without_fabricating_site_urls(self):
        result = SimpleNamespace(
            returncode=0,
            stdout=(
                '{"entries":[{"webpage_url":"https://media.example/watch/first",'
                '"id":"first","title":"First episode"},'
                '{"id":"unavailable","title":"Unavailable"}]}'
            ),
            stderr="",
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result):
            episodes = dlpplayer.fetch_playlist_episodes(
                "yt-dlp.exe", "https://media.example/playlist/1"
            )

        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "First episode", "https://media.example/watch/first"
                ),
            ),
        )

    def test_omits_unavailable_playlist_videos(self):
        playlist_url = "https://www.youtube.com/playlist?list=PL123"
        result = SimpleNamespace(
            returncode=0,
            stdout=(
                '{"entries":[{"id":"available","availability":"public",'
                '"title":"Available"},'
                '{"id":"blocked","availability":"unavailable",'
                '"title":"Unavailable"},'
                '{"id":"auth","availability":"needs_auth",'
                '"title":"Sign in required"}]}'
            ),
            stderr="",
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result):
            episodes = dlpplayer.fetch_playlist_episodes("yt-dlp.exe", playlist_url)

        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "Available", "https://www.youtube.com/watch?v=available"
                ),
            ),
        )

        result.stdout = (
            '{"entries":[{"id":"blocked","availability":"unavailable",'
            '"title":"Unavailable"}]}'
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result):
            self.assertEqual(
                dlpplayer.fetch_playlist_episodes("yt-dlp.exe", playlist_url), ()
            )

    def test_builds_playable_url_for_bilibili_episode_id(self):
        result = SimpleNamespace(
            returncode=0,
            stdout='{"entries":[{"id":"BV123","title":"Part one"}]}',
            stderr="",
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result):
            episodes = dlpplayer.fetch_playlist_episodes(
                "yt-dlp.exe", "https://www.bilibili.com/medialist/play/123"
            )

        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "Part one", "https://www.bilibili.com/video/BV123"
                ),
            ),
        )

    def test_reads_bilibili_part_titles_and_durations(self):
        url = "https://www.bilibili.com/video/BV14tsEzVEub/"
        html = (
            '<script>window.__INITIAL_STATE__={"videoData":{"pages":['
            '{"page":1,"part":"C语言前言简介","duration":55},'
            '{"page":2,"part":"0、C语言课程介绍","duration":776}]}};</script>'
        )

        class FakeResponse(BytesIO):
            headers = {"Content-Encoding": "identity"}

        with patch.object(
            dlpplayer, "urlopen", return_value=FakeResponse(html.encode("utf-8"))
        ) as open_page:
            with patch.object(dlpplayer.subprocess, "run") as run:
                episodes = dlpplayer.fetch_playlist_episodes("yt-dlp.exe", url)

        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "C语言前言简介",
                    "https://www.bilibili.com/video/BV14tsEzVEub?p=1",
                    55,
                ),
                dlpplayer.PlaylistEpisode(
                    "0、C语言课程介绍",
                    "https://www.bilibili.com/video/BV14tsEzVEub?p=2",
                    776,
                ),
            ),
        )
        self.assertEqual(
            dlpplayer._playlist_episode_label(1, episodes[0]),
            "1. C语言前言简介  0:55",
        )
        open_page.assert_called_once()
        run.assert_not_called()

    def test_returns_none_for_non_playlist_metadata(self):
        result = SimpleNamespace(
            returncode=0, stdout='{"id":"video1","title":"Single video"}', stderr=""
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result):
            self.assertIsNone(
                dlpplayer.fetch_playlist_episodes(
                    "yt-dlp.exe", "https://www.youtube.com/watch?v=video1"
                )
            )

    def test_hints_recognize_playlist_links(self):
        self.assertTrue(
            dlpplayer.is_playlist_url_hint(
                "https://www.youtube.com/watch?v=video1&list=PL123"
            )
        )
        self.assertFalse(
            dlpplayer.is_playlist_url_hint("https://www.youtube.com/watch?v=video1")
        )


    def test_reads_m3u8_episode_titles_and_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_episode = root / "local episode.mp4"
            local_episode.touch()
            remote_episode = "https://media.example/second/index.m3u8"
            playlist = root / "episodes.m3u8"
            playlist.write_text(
                "#EXTM3U\n"
                "#EXTINF:605,第01集\n"
                "https://media.example/first/index.m3u8\n"
                "#EXTINF:-1,第02集\n"
                f"{local_episode.name}\n"
                "#EXTINF:-1,第03集\n"
                f"{remote_episode}\n",
                encoding="utf-8",
            )

            episodes = dlpplayer.read_local_playlist_episodes(playlist)

        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "第01集", "https://media.example/first/index.m3u8", 605
                ),
                dlpplayer.PlaylistEpisode("第02集", str(local_episode.resolve())),
                dlpplayer.PlaylistEpisode("第03集", remote_episode),
            ),
        )

    def test_leaves_unnamed_hls_segments_as_direct_playback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            playlist = Path(temp_dir) / "stream.m3u8"
            playlist.write_text(
                "#EXTM3U\n#EXTINF:6,\nsegment-1.ts\n#EXTINF:6,\nsegment-2.ts\n",
                encoding="utf-8",
            )

            self.assertIsNone(dlpplayer.read_local_playlist_episodes(playlist))

    def test_reads_ended_hls_manifest_duration_without_ytdlp(self):
        episode = dlpplayer.PlaylistEpisode(
            "第01集", "https://media.example/play/episode-id/index.m3u8"
        )
        manifest = (
            b"#EXTM3U\n#EXTINF:8.25,\nsegment-1.ts\n"
            b"#EXTINF:7.75,\nsegment-2.ts\n#EXT-X-ENDLIST\n"
        )
        with patch.object(
            dlpplayer, "urlopen", return_value=BytesIO(manifest)
        ) as opener:
            with patch.object(dlpplayer.subprocess, "run") as run:
                enriched = dlpplayer.fetch_episode_metadata(None, episode)

        self.assertEqual(
            enriched,
            dlpplayer.PlaylistEpisode(episode.title, episode.target, 16),
        )
        opener.assert_called_once()
        run.assert_not_called()

    def test_ignores_live_hls_manifest_duration(self):
        manifest = BytesIO(b"#EXTM3U\n#EXTINF:10,\nsegment.ts\n")
        with patch.object(dlpplayer, "urlopen", return_value=manifest):
            duration = dlpplayer._hls_duration_seconds(
                "https://media.example/live/stream.m3u8"
            )

        self.assertIsNone(duration)

    def test_enriches_episode_title_and_duration(self):
        episode = dlpplayer.PlaylistEpisode(
            "第01集", "https://media.example/watch/episode-id"
        )
        result = SimpleNamespace(
            returncode=0,
            stdout='{"title":"A real episode title","duration":637}',
            stderr="",
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result):
            enriched = dlpplayer.fetch_episode_metadata("yt-dlp.exe", episode)

        self.assertEqual(
            enriched,
            dlpplayer.PlaylistEpisode(
                "A real episode title", episode.target, 637
            ),
        )
        self.assertEqual(
            dlpplayer._playlist_episode_label(1, enriched),
            "1. A real episode title  10:37",
        )

    def test_keeps_m3u_title_when_extractor_only_returns_generic_title(self):
        episode = dlpplayer.PlaylistEpisode(
            "第01集", "https://media.example/index/episode-id"
        )
        for generic_title in ("Remote clip", "index"):
            result = SimpleNamespace(
                returncode=0,
                stdout=f'{{"title":"{generic_title}","duration":637}}',
                stderr="",
            )
            with self.subTest(title=generic_title):
                with patch.object(
                    dlpplayer.subprocess, "run", return_value=result
                ):
                    enriched = dlpplayer.fetch_episode_metadata(
                        "yt-dlp.exe", episode
                    )

                self.assertEqual(enriched.title, "第01集")
                self.assertEqual(enriched.duration_seconds, 637)


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
