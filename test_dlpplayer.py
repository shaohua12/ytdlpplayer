import queue
import tempfile
import threading
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
    load_settings,
    video_info_from_ffprobe,
    video_info_from_ytdlp,
)


class SettingsTests(unittest.TestCase):
    def test_reads_maximum_resolution_from_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "dlpplayer.json"
            settings_path.write_text(
                '{"max_allowed_resolution": 720}', encoding="utf-8"
            )

            settings = load_settings(settings_path)

        self.assertEqual(settings.max_allowed_resolution, 720)
        self.assertEqual(
            settings.video_format,
            "bv*[height<=720]+ba/b[height<=720]",
        )

    def test_null_resolution_allows_any_available_height(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "dlpplayer.json"
            settings_path.write_text(
                '{"max_allowed_resolution": null}', encoding="utf-8"
            )

            settings = load_settings(settings_path)

        self.assertIsNone(settings.max_allowed_resolution)
        self.assertEqual(settings.video_format, "bv*+ba/b")

    def test_invalid_resolution_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "dlpplayer.json"
            settings_path.write_text(
                '{"max_allowed_resolution": true}', encoding="utf-8"
            )

            with patch("sys.stderr"):
                settings = load_settings(settings_path)

        self.assertEqual(settings.max_allowed_resolution, 1080)
        self.assertEqual(settings.video_format, dlpplayer.AppSettings().video_format)

    def test_reads_relative_tool_and_python_library_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "vendor").mkdir()
            settings_path = root / "dlpplayer.json"
            settings_path.write_text(
                '{"yt_dlp_path":"tools/yt-dlp.exe", "mpv_path":null, '
                '"ffmpeg_path":"tools/ffmpeg", "node_path":"node/node.exe", '
                '"python_library_path":"vendor"}',
                encoding="utf-8",
            )

            settings = load_settings(settings_path)
            self.assertEqual(settings.yt_dlp_path, root / "tools/yt-dlp.exe")
            self.assertIsNone(settings.mpv_path)
            self.assertEqual(settings.ffmpeg_path, root / "tools/ffmpeg")
            self.assertEqual(settings.node_path, root / "node/node.exe")
            self.assertEqual(settings.python_library_path, root / "vendor")

            with patch.object(dlpplayer.sys, "path", ["existing"]):
                dlpplayer._add_python_library_path(settings.python_library_path)
                self.assertEqual(
                    dlpplayer.sys.path,
                    [str((root / "vendor").resolve()), "existing"],
                )

    def test_null_tool_path_uses_path_lookup(self):
        with patch.object(dlpplayer.shutil, "which", return_value="mpv.exe") as which:
            resolved = dlpplayer.resolve_tool(None, "DLPPLAYER_MPV", None, "mpv")

        self.assertEqual(resolved, "mpv.exe")
        which.assert_called_once_with("mpv")

    def test_radio_mix_batch_size_defaults_and_validates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "dlpplayer.json"
            self.assertEqual(
                load_settings(settings_path).radio_mix_batch_size, 68
            )

            settings_path.write_text(
                '{"radio_mix_batch_size": 25}', encoding="utf-8"
            )
            self.assertEqual(
                load_settings(settings_path).radio_mix_batch_size, 25
            )

            settings_path.write_text(
                '{"radio_mix_batch_size": 0}', encoding="utf-8"
            )
            with patch("sys.stderr"):
                self.assertEqual(
                    load_settings(settings_path).radio_mix_batch_size, 68
                )

    def test_cookie_browser_defaults_to_chrome_and_validates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "dlpplayer.json"
            self.assertEqual(load_settings(settings_path).cookie_browser, "chrome")

            settings_path.write_text(
                '{"cookie_browser": "Firefox"}', encoding="utf-8"
            )
            self.assertEqual(
                load_settings(settings_path).cookie_browser, "firefox"
            )

            settings_path.write_text(
                '{"cookie_browser": "  "}', encoding="utf-8"
            )
            with patch("sys.stderr"):
                self.assertEqual(
                    load_settings(settings_path).cookie_browser, "chrome"
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

    def test_direct_video_urls_skip_playlist_inspection(self):
        self.assertFalse(
            dlpplayer._should_inspect_playlist_first(
                "https://media.example/watch?id=123"
            )
        )
        self.assertTrue(
            dlpplayer._should_inspect_playlist_first(
                "https://media.example/playlist?id=123"
            )
        )
        self.assertTrue(
            dlpplayer._should_inspect_playlist_first(
                "https://www.bilibili.com/video/BV123"
            )
        )

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


class YtDlpRuntimeCompatibilityTests(unittest.TestCase):
    def _resolve_ytdlp(self, temp_dir: Path, help_text: str) -> str:
        executable = temp_dir / "yt-dlp.exe"
        executable.touch()
        help_result = SimpleNamespace(
            returncode=0,
            stdout=help_text,
            stderr="",
        )
        with patch.object(
            dlpplayer.subprocess, "run", return_value=help_result
        ):
            return dlpplayer.resolve_tool(
                str(executable), "DLPPLAYER_YTDLP", None, "yt-dlp"
            )

    def test_older_ytdlp_omits_unsupported_runtime_option(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            yt_dlp = self._resolve_ytdlp(
                Path(temp_dir), "Usage: yt-dlp [OPTIONS] URL"
            )
            url = "https://www.youtube.com/watch?v=video"
            commands = (
                dlpplayer.build_download_command(
                    yt_dlp,
                    "ffmpeg",
                    Path("downloads"),
                    url,
                    use_cookies=False,
                    node_runtime="node.exe",
                ),
                build_video_info_command(yt_dlp, url, "node.exe"),
                dlpplayer.build_playlist_command(yt_dlp, url, "node.exe"),
                dlpplayer.build_episode_metadata_command(yt_dlp, url, "node.exe"),
            )
            for command in commands:
                self.assertNotIn("--js-runtimes", command)

            mpv_command = build_mpv_url_command(
                "mpv.exe", yt_dlp, url, node_runtime="node.exe", use_cookies=False
            )
            self.assertFalse(any("js-runtimes=" in item for item in mpv_command))

            with patch.object(
                dlpplayer,
                "_mpv_property_value",
                side_effect=[{}, {"js-runtimes": "node:node.exe"}],
            ):
                with patch.object(
                    dlpplayer,
                    "_mpv_ipc_request",
                    return_value={"error": "success"},
                ) as request:
                    configured = dlpplayer._configure_mpv_for_youtube(
                        r"\\.\pipe\test", yt_dlp, "node.exe"
                    )

            self.assertTrue(configured)
            raw_options = next(
                call.args[1][2]
                for call in request.call_args_list
                if call.args[1][1] == "options/ytdl-raw-options"
            )
            self.assertNotIn("js-runtimes", raw_options)

    def test_newer_ytdlp_keeps_supported_runtime_option(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            yt_dlp = self._resolve_ytdlp(
                Path(temp_dir), "--js-runtimes RUNTIME[:PATH]"
            )
            command = build_video_info_command(
                yt_dlp,
                "https://www.youtube.com/watch?v=video",
                "node.exe",
            )

        self.assertIn("--js-runtimes", command)


class DownloadCommandTests(unittest.TestCase):
    def test_uses_chrome_cookies_and_local_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            command = build_download_command(
                "yt-dlp.exe",
                r"D:\bin\ffmpeg\bin",
                Path(temp_dir),
                "https://www.bilibili.com/video/BV123",
                result_file="result.txt",
            )

        self.assertIn("--cookies-from-browser", command)
        self.assertIn("chrome", command)
        self.assertIn("--ffmpeg-location", command)
        self.assertIn("--print-to-file", command)
        self.assertIn("https://www.bilibili.com/video/BV123", command)
        self.assertIn(temp_dir, command)
        self.assertEqual(command[command.index("-f") + 1], VIDEO_FORMAT_DEFAULT)

    def test_can_skip_cookies_and_choose_cookie_profile(self):
        command = build_download_command(
            "yt-dlp.exe",
            "ffmpeg",
            Path("downloads"),
            "https://www.bilibili.com/video/BV123",
            cookie_profile="Profile 1",
            use_cookies=False,
        )
        self.assertNotIn("--cookies-from-browser", command)

        command = build_download_command(
            "yt-dlp.exe",
            "ffmpeg",
            Path("downloads"),
            "https://www.bilibili.com/video/BV123",
            cookie_profile="Profile 1",
        )
        cookie_index = command.index("--cookies-from-browser")
        self.assertEqual(command[cookie_index + 1], "chrome:Profile 1")

    def test_cookie_browser_override(self):
        command = build_download_command(
            "yt-dlp.exe",
            "ffmpeg",
            Path("downloads"),
            "https://www.bilibili.com/video/BV123",
            cookie_browser="firefox",
        )
        cookie_index = command.index("--cookies-from-browser")
        self.assertEqual(command[cookie_index + 1], "firefox")

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


class YtDlpCookieFallbackTests(unittest.TestCase):
    def test_retries_chrome_edge_firefox_then_brave(self):
        command = [
            "yt-dlp.exe",
            "--cookies-from-browser",
            "firefox:default-release",
            "https://www.youtube.com/watch?v=video",
        ]
        failure = SimpleNamespace(returncode=1)
        success = SimpleNamespace(returncode=0)
        with patch.object(
            dlpplayer.subprocess,
            "run",
            side_effect=[failure, failure, failure, success],
        ) as run:
            result = dlpplayer._run_ytdlp_with_browser_fallback(command)

        attempted_sources = [
            call.args[0][call.args[0].index("--cookies-from-browser") + 1]
            for call in run.call_args_list
        ]
        self.assertIs(result, success)
        self.assertEqual(
            attempted_sources,
            ["firefox:default-release", "chrome", "edge", "brave"],
        )

    def test_returns_configured_browser_error_when_all_attempts_fail(self):
        command = [
            "yt-dlp.exe",
            "--cookies-from-browser",
            "brave",
            "https://www.youtube.com/watch?v=video",
        ]
        brave_failure = SimpleNamespace(
            returncode=1,
            stderr="Could not copy Chrome cookie database.",
        )
        other_failure = SimpleNamespace(returncode=1, stderr="browser unavailable")
        with patch.object(
            dlpplayer.subprocess,
            "run",
            side_effect=[brave_failure, other_failure, other_failure, other_failure],
        ):
            result = dlpplayer._run_ytdlp_with_browser_fallback(command)

        self.assertIs(result, brave_failure)

    def test_does_not_fallback_when_cookies_are_disabled(self):
        command = ["yt-dlp.exe", "--skip-download", "https://media.example/video"]
        failure = SimpleNamespace(returncode=1)
        with patch.object(dlpplayer.subprocess, "run", return_value=failure) as run:
            result = dlpplayer._run_ytdlp_with_browser_fallback(
                command, check=False
            )

        self.assertIs(result, failure)
        run.assert_called_once_with(command, check=False)


class CookieBrowserDetectionTests(unittest.TestCase):
    def _brave_root(self, temp_dir: Path) -> Path:
        root = (
            temp_dir / "BraveSoftware" / "Brave-Browser" / "User Data"
        )
        (root / "Default" / "Network").mkdir(parents=True)
        (root / "Default" / "Network" / "Cookies").write_bytes(b"")
        return root

    def _check(self, temp_dir: Path, browser: str, local_state: str) -> bool:
        root = self._brave_root(temp_dir)
        (root / "Local State").write_text(local_state, encoding="utf-8")
        with patch.object(dlpplayer.os, "name", "nt"):
            with patch.object(
                dlpplayer.os,
                "environ",
                {"LOCALAPPDATA": str(temp_dir), "APPDATA": str(temp_dir)},
            ):
                return dlpplayer._cookie_browser_usable(browser)

    def test_app_bound_encryption_makes_chromium_cookies_unusable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertFalse(
                self._check(
                    Path(temp_dir),
                    "brave",
                    '{"os_crypt":{"app_bound_encrypted_key":"APPB"}}',
                )
            )

    def test_chromium_cookies_without_app_bound_encryption_are_usable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertTrue(
                self._check(
                    Path(temp_dir),
                    "brave",
                    '{"os_crypt":{"encrypted_key":"RFBB"}}',
                )
            )

    def test_locked_chromium_cookie_database_is_unusable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._brave_root(Path(temp_dir))
            (root / "Local State").write_text(
                '{"os_crypt":{"encrypted_key":"RFBB"}}', encoding="utf-8"
            )
            original_open = Path.open

            def locked_open(path, *args, **kwargs):
                if path.name == "Cookies":
                    raise PermissionError(13, "locked")
                return original_open(path, *args, **kwargs)

            with patch.object(dlpplayer.os, "name", "nt"):
                with patch.object(
                    dlpplayer.os,
                    "environ",
                    {
                        "LOCALAPPDATA": str(temp_dir),
                        "APPDATA": str(temp_dir),
                    },
                ):
                    with patch.object(Path, "open", locked_open):
                        self.assertFalse(
                            dlpplayer._cookie_browser_usable("brave")
                        )

    def test_missing_firefox_profiles_are_unusable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(dlpplayer.os, "name", "nt"):
                with patch.object(
                    dlpplayer.os, "environ", {"APPDATA": str(temp_dir)}
                ):
                    self.assertFalse(
                        dlpplayer._cookie_browser_usable("firefox")
                    )

    def test_usable_browser_skips_unavailable_preferred_browser(self):
        with patch.object(
            dlpplayer,
            "_cookie_browser_usable",
            side_effect=lambda browser: browser == "chrome",
        ):
            self.assertEqual(
                dlpplayer._usable_cookie_browser("brave"), "chrome"
            )

    def test_mpv_config_removes_cookies_when_no_browser_is_usable(self):
        with patch.object(
            dlpplayer,
            "_mpv_property_value",
            side_effect=[
                {"ytdl_hook-ytdl_path": "yt-dlp.exe"},
                {
                    "cookies-from-browser": "brave",
                    "en,write-subs": "",
                },
            ],
        ):
            with patch.object(
                dlpplayer,
                "_mpv_ipc_request",
                return_value={"error": "success"},
            ) as request:
                configured = dlpplayer._configure_mpv_for_youtube(
                    r"\\.\pipe\test", "yt-dlp.exe", "node.exe", None
                )

        self.assertTrue(configured)
        raw_options = next(
            call.args[1][2]
            for call in request.call_args_list
            if call.args[1][1] == "options/ytdl-raw-options"
        )
        self.assertNotIn("cookies-from-browser", raw_options)
        self.assertNotIn("en,write-subs", raw_options)
        self.assertEqual(raw_options["js-runtimes"], "node:node.exe")
        self.assertEqual(raw_options["sub-langs"], "en.*")
        self.assertIn("write-subs", raw_options)
        self.assertIn("write-auto-subs", raw_options)
        self.assertIn(
            ["set_property", "options/slang", ["en", "en-US", "en-GB"]],
            [call.args[1] for call in request.call_args_list],
        )


class MpvIpcServerTests(unittest.TestCase):
    def test_ipc_endpoint_is_stable_and_per_user(self):
        first_profile = Path("D:/profiles/first")
        second_profile = Path("D:/profiles/second")
        with patch.object(dlpplayer.Path, "home", return_value=first_profile):
            first_endpoint = dlpplayer._mpv_ipc_server_path()
            repeated_endpoint = dlpplayer._mpv_ipc_server_path()
        with patch.object(dlpplayer.Path, "home", return_value=second_profile):
            second_endpoint = dlpplayer._mpv_ipc_server_path()

        self.assertEqual(first_endpoint, repeated_endpoint)
        self.assertNotEqual(first_endpoint, second_endpoint)

    def test_replacing_mpv_media_unpauses_after_loading(self):
        with patch.object(
            dlpplayer, "_send_mpv_ipc_command_retry", return_value=True
        ) as send:
            loaded = dlpplayer._load_mpv_content(
                "ipc", ["loadfile", "https://media.example/video", "replace"]
            )

        self.assertTrue(loaded)
        self.assertEqual(
            [item.args[1] for item in send.call_args_list],
            [
                ["loadfile", "https://media.example/video", "replace"],
                ["set_property", "pause", False],
            ],
        )

    def test_ipc_command_retries_transient_connection_failures(self):
        with (
            patch.object(
                dlpplayer,
                "_send_mpv_ipc_command",
                side_effect=[False, False, True],
            ) as send,
            patch.object(dlpplayer.time, "sleep") as sleep,
        ):
            sent = dlpplayer._send_mpv_ipc_command_retry(
                "ipc", ["stop"], attempts=3
            )

        self.assertTrue(sent)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_ipc_command_stops_retrying_after_attempt_limit(self):
        with (
            patch.object(dlpplayer, "_send_mpv_ipc_command", return_value=False) as send,
            patch.object(dlpplayer.time, "sleep") as sleep,
        ):
            sent = dlpplayer._send_mpv_ipc_command_retry(
                "ipc", ["stop"], attempts=2
            )

        self.assertFalse(sent)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(sleep.call_count, 1)


class MpvProgressTests(unittest.TestCase):
    def test_extracts_status_label_and_percentage(self):
        self.assertEqual(
            dlpplayer._playback_progress_display(
                "Example episode | Playing | 4:47 / 16:35 (30%)"
            ),
            ("Example episode | Playing", 30),
        )
        self.assertEqual(
            dlpplayer._playback_progress_display(
                "Paused | 4:47 / 16:35 (30%)"
            ),
            ("Paused", 30),
        )

    def test_ignores_status_without_a_valid_percentage(self):
        self.assertIsNone(
            dlpplayer._playback_progress_display("Playing | loading media...")
        )
        self.assertIsNone(
            dlpplayer._playback_progress_display("Playing | 4:47 / 16:35 (101%)")
        )

    def test_formats_playback_time_and_percentage(self):
        self.assertEqual(dlpplayer._format_mpv_time(3661.9), "1:01:01")
        self.assertEqual(
            dlpplayer._format_mpv_progress(65, 300),
            "Playing | 1:05 / 5:00 (22%)",
        )

    def test_formats_paused_playback_without_known_duration(self):
        self.assertEqual(
            dlpplayer._format_mpv_progress(65, None, paused=True),
            "Paused | 1:05",
        )

    def test_extracts_playback_duration(self):
        self.assertEqual(
            dlpplayer._playback_duration("Example | Playing | 1:05 / 5:00 (22%)"),
            "5:00",
        )
        self.assertEqual(
            dlpplayer._playback_duration("Paused | 1:05 / 5:00 (22%)"),
            "5:00",
        )
        self.assertIsNone(dlpplayer._playback_duration("MPV is ready."))
        self.assertIsNone(
            dlpplayer._playback_duration("Playing | loading media...")
        )

    def test_reads_status_from_mpv_properties(self):
        with patch.object(
            dlpplayer,
            "_mpv_property_value",
            side_effect=[False, 65, 300, False, "Example episode"],
        ):
            status = dlpplayer._mpv_status_text("ipc")

        self.assertEqual(
            status, "Example episode | Playing | 1:05 / 5:00 (22%)"
        )

    def test_uses_fallback_title_when_mpv_title_is_a_url(self):
        with patch.object(
            dlpplayer,
            "_mpv_property_value",
            side_effect=[False, 65, 300, False, "https://example.com/video"],
        ):
            status = dlpplayer._mpv_status_text("ipc", "Useful metadata title")

        self.assertEqual(
            status, "Useful metadata title | Playing | 1:05 / 5:00 (22%)"
        )

    def test_reports_idle_player_as_ready(self):
        with patch.object(dlpplayer, "_mpv_property_value", return_value=True) as prop:
            status = dlpplayer._mpv_status_text("ipc")

        self.assertEqual(status, "MPV is ready.")
        prop.assert_called_once_with("ipc", "idle-active")


class MpvPauseToggleTests(unittest.TestCase):
    def test_pauses_a_playing_video(self):
        with (
            patch.object(
                dlpplayer, "_mpv_property_value", side_effect=[False, False]
            ) as prop,
            patch.object(
                dlpplayer, "_send_mpv_ipc_command", return_value=True
            ) as send,
        ):
            state = dlpplayer._toggle_mpv_pause("ipc")

        self.assertEqual(state, "paused")
        self.assertEqual(
            [call.args for call in prop.call_args_list],
            [("ipc", "idle-active"), ("ipc", "pause")],
        )
        send.assert_called_once_with("ipc", ["cycle", "pause"])

    def test_resumes_a_paused_video(self):
        with (
            patch.object(
                dlpplayer, "_mpv_property_value", side_effect=[False, True]
            ),
            patch.object(dlpplayer, "_send_mpv_ipc_command", return_value=True),
        ):
            self.assertEqual(dlpplayer._toggle_mpv_pause("ipc"), "playing")

    def test_ignores_an_idle_player(self):
        with (
            patch.object(dlpplayer, "_mpv_property_value", return_value=True),
            patch.object(dlpplayer, "_send_mpv_ipc_command") as send,
        ):
            self.assertIsNone(dlpplayer._toggle_mpv_pause("ipc"))

        send.assert_not_called()

    def test_reports_nothing_when_the_command_fails(self):
        with (
            patch.object(dlpplayer, "_mpv_property_value", return_value=False),
            patch.object(dlpplayer, "_send_mpv_ipc_command", return_value=False),
        ):
            self.assertIsNone(dlpplayer._toggle_mpv_pause("ipc"))


class MpvLampTests(unittest.TestCase):
    def test_reads_playing_status(self):
        self.assertEqual(
            dlpplayer._mpv_lamp_state("Example | Playing | 1:05 / 5:00 (22%)"),
            "playing",
        )

    def test_reads_paused_status(self):
        for status in (
            "Example | Paused | 1:05 / 5:00 (22%)",
            "Paused | loading media...",
            "Paused | 1:05",
            "A | B | Paused | 1:05 / 5:00 (22%)",
        ):
            with self.subTest(status=status):
                self.assertEqual(dlpplayer._mpv_lamp_state(status), "paused")

    def test_reads_idle_player_as_stopped(self):
        self.assertEqual(dlpplayer._mpv_lamp_state("MPV is ready."), "stopped")

    def test_defaults_to_playing_without_status(self):
        self.assertEqual(dlpplayer._mpv_lamp_state(None), "playing")

    def test_shows_only_the_requested_icon(self):
        class RecordingCanvas:
            def __init__(self):
                self.states = {}

            def itemconfigure(self, item, **options):
                self.states[item] = options["state"]

        icons = {"playing": (1,), "paused": (2, 3), "stopped": (4,)}

        canvas = RecordingCanvas()
        dlpplayer._set_lamp_state(canvas, icons, "paused")
        self.assertEqual(
            canvas.states, {1: "hidden", 2: "normal", 3: "normal", 4: "hidden"}
        )

        canvas = RecordingCanvas()
        dlpplayer._set_lamp_state(canvas, icons, None)
        self.assertEqual(
            canvas.states, {1: "hidden", 2: "hidden", 3: "hidden", 4: "hidden"}
        )


class StatusTextFitTests(unittest.TestCase):
    def test_keeps_text_that_fits(self):
        measure = lambda text: len(text) * 10

        self.assertEqual(
            dlpplayer._fit_text_to_width("Ready.", measure, 100), "Ready."
        )

    def test_cuts_text_with_three_dots(self):
        measure = lambda text: len(text) * 10

        fitted = dlpplayer._fit_text_to_width("abcdefghij", measure, 50)

        self.assertEqual(fitted, "ab...")
        self.assertLessEqual(measure(fitted), 50)

    def test_drops_trailing_space_before_the_dots(self):
        measure = lambda text: len(text) * 10

        self.assertEqual(
            dlpplayer._fit_text_to_width("abc defg", measure, 70), "abc..."
        )

    def test_returns_nothing_when_the_dots_cannot_fit(self):
        measure = lambda text: len(text) * 10

        self.assertEqual(dlpplayer._fit_text_to_width("abcdef", measure, 20), "")
        self.assertEqual(dlpplayer._fit_text_to_width("abcdef", measure, 0), "")


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
        self.assertIn("--slang=en,en-US,en-GB", command)
        self.assertIn(f"--ytdl-format={VIDEO_FORMAT_DEFAULT}", command)
        raw_options = next(
            item.removeprefix("--ytdl-raw-options=")
            for item in command
            if item.startswith("--ytdl-raw-options=")
        )
        self.assertIn("sub-langs=en.*", raw_options)
        self.assertIn("write-subs=", raw_options)
        self.assertIn("write-auto-subs=", raw_options)
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

    def test_mpv_remains_idle_and_visible_when_a_file_finishes(self):
        command = build_mpv_url_command(
            "mpv.exe",
            "yt-dlp.exe",
            "https://media.example/watch?id=123",
            use_cookies=False,
        )

        self.assertIn("--pause=no", command)
        self.assertIn("--idle=yes", command)
        self.assertIn("--force-window=yes", command)

    def test_radio_batch_can_keep_exit_on_completion(self):
        command = dlpplayer._build_mpv_playlist_command(
            "mpv.exe",
            "yt-dlp.exe",
            ("https://media.example/1",),
            use_cookies=False,
            keep_open=False,
        )

        self.assertNotIn("--idle=yes", command)
        self.assertNotIn("--force-window=yes", command)

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
            "--ytdl-raw-options=sub-langs=en.*,write-subs=,"
            "write-auto-subs=,cookies-from-browser=chrome,"
            "js-runtimes=node:node.exe",
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

    def test_saves_and_reloads_local_m3u_playlist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_file = root / "local video.mp4"
            local_file.touch()
            output = root / "saved playlist.m3u8"
            episodes = (
                dlpplayer.PlaylistEpisode(
                    "First\nepisode", "https://media.example/watch/1", 92
                ),
                dlpplayer.PlaylistEpisode("Local episode", str(local_file.resolve())),
            )

            dlpplayer._write_local_m3u_playlist(output, episodes)
            lines = output.read_text(encoding="utf-8").splitlines()
            loaded = dlpplayer.read_local_playlist_episodes(output)

        self.assertEqual(
            lines,
            [
                "#EXTM3U",
                "#EXTINF:92,First episode",
                "https://media.example/watch/1",
                "#EXTINF:-1,Local episode",
                str(local_file.resolve()),
            ],
        )
        self.assertEqual(
            loaded,
            (
                dlpplayer.PlaylistEpisode(
                    "First episode", "https://media.example/watch/1", 92
                ),
                dlpplayer.PlaylistEpisode("Local episode", str(local_file.resolve())),
            ),
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


class VideoInfoErrorMessageTests(unittest.TestCase):
    def test_explains_youtube_members_only_access(self):
        message = dlpplayer._video_info_error_message(
            "https://www.youtube.com/watch?v=-7Le2prbpH8",
            "ERROR: [youtube] -7Le2prbpH8: Join this channel to get access to "
            "members-only content like this video, and other exclusive perks.",
        )

        self.assertEqual(
            message,
            "YouTube reports this video is members-only. Check that the browser "
            "profile used by DLPPlayer is signed in to an account that has joined "
            "the channel.",
        )

    def test_preserves_other_video_metadata_errors(self):
        self.assertEqual(
            dlpplayer._video_info_error_message(
                "https://media.example/video", "network unavailable"
            ),
            "Video details unavailable: network unavailable",
        )


class VideoInfoCommandTests(unittest.TestCase):
    def test_separates_video_info_from_bounded_top_comments(self):
        url = "https://youtu.be/BaW_jenozKc"
        command = build_video_info_command(
            "yt-dlp.exe", url, node_runtime="node.exe"
        )
        comments_command = build_video_info_command(
            "yt-dlp.exe", url, node_runtime="node.exe", include_comments=True
        )

        self.assertIn("--no-write-comments", command)
        self.assertNotIn("--write-comments", command)
        self.assertIn("--write-comments", comments_command)
        self.assertEqual(
            comments_command[comments_command.index("--extractor-args") + 1],
            "youtube:comment_sort=top;max_comments=100",
        )
        bilibili_command = build_video_info_command(
            "yt-dlp.exe", "https://www.bilibili.com/video/BV123"
        )
        self.assertIn("--no-write-comments", bilibili_command)
        self.assertNotIn("--write-comments", bilibili_command)

    def test_queues_video_information_before_comments_finish(self):
        info = dlpplayer.VideoInfo("Video", ("1920 × 1080",))
        comments = (dlpplayer.VideoComment("Top comment", 12, "Author"),)
        comments_started = threading.Event()
        finish_comments = threading.Event()
        results = queue.Queue()

        def load_comments():
            comments_started.set()
            if not finish_comments.wait(2):
                raise TimeoutError("comments still loading")
            return comments

        worker = threading.Thread(
            target=dlpplayer._load_video_info,
            args=(7, lambda: info, "https://youtu.be/video", load_comments, results),
        )
        worker.start()
        try:
            self.assertEqual(results.get(timeout=1), (7, info, None))
            self.assertTrue(comments_started.wait(1))
        finally:
            finish_comments.set()
            worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(
            results.get(timeout=1),
            (7, dlpplayer.VideoInfo(info.title, info.details, comments), None),
        )

    def test_fetches_youtube_comments_in_separate_command(self):
        result = SimpleNamespace(
            returncode=0,
            stdout='{"title":"Video","comments":[{"text":"Top",'
            '"like_count":8}]}',
            stderr="",
        )
        with patch.object(dlpplayer.subprocess, "run", return_value=result) as run:
            comments = dlpplayer.fetch_youtube_video_comments(
                "yt-dlp.exe", "https://youtu.be/video", "node.exe"
            )

        command = run.call_args.args[0]
        self.assertIn("--write-comments", command)
        self.assertEqual(comments, (dlpplayer.VideoComment("Top", 8, None),))

    def test_video_info_retries_with_edge_cookies(self):
        failure = SimpleNamespace(returncode=1, stderr="Chrome cookies unavailable")
        success = SimpleNamespace(
            returncode=0,
            stdout='{"title":"Video","vcodec":"h264"}',
            stderr="",
        )
        with patch.object(
            dlpplayer.subprocess, "run", side_effect=[failure, success]
        ) as run:
            info = dlpplayer.fetch_url_video_info(
                "yt-dlp.exe", "https://www.youtube.com/watch?v=video"
            )

        self.assertEqual(info.title, "Video")
        self.assertEqual(
            [
                call.args[0][call.args[0].index("--cookies-from-browser") + 1]
                for call in run.call_args_list
            ],
            ["chrome", "edge"],
        )

    def test_comment_timeout_does_not_invalidate_video_information(self):
        info = dlpplayer.VideoInfo("Video", ("1920 × 1080",))
        results = queue.Queue()

        def timeout_comments():
            raise TimeoutError("comments exceeded their deadline")

        dlpplayer._load_video_info(
            8, lambda: info, "https://youtu.be/video", timeout_comments, results
        )

        self.assertEqual(results.get_nowait(), (8, info, None))
        self.assertTrue(results.empty())

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
    def test_episode_dropdown_shows_up_to_thirty_rows(self):
        self.assertEqual(dlpplayer._episode_dropdown_height(1), 1)
        self.assertEqual(dlpplayer._episode_dropdown_height(12), 12)
        self.assertEqual(dlpplayer._episode_dropdown_height(68), 30)
        self.assertEqual(dlpplayer._episode_dropdown_height(0), 1)

    def test_maps_mpv_playlist_position_to_episode(self):
        episodes = (
            dlpplayer.PlaylistEpisode("First", "https://media.example/1"),
            dlpplayer.PlaylistEpisode("Second", "https://media.example/2"),
        )

        self.assertEqual(
            dlpplayer._playlist_episode_at_position(episodes, 1), episodes[1]
        )
        self.assertIsNone(dlpplayer._playlist_episode_at_position(episodes, -1))
        self.assertIsNone(dlpplayer._playlist_episode_at_position(episodes, 2))
        self.assertIsNone(dlpplayer._playlist_episode_at_position(episodes, True))

    def test_suggests_meaningful_playlist_filenames(self):
        episodes = (
            dlpplayer.PlaylistEpisode("My: Show / Episode 1", "https://example.com/1"),
            dlpplayer.PlaylistEpisode("Episode 2", "https://example.com/2"),
        )

        self.assertEqual(
            dlpplayer._suggest_playlist_filename(episodes),
            "My- Show - Episode 1 - 2 episodes.m3u8",
        )
        self.assertEqual(
            dlpplayer._suggest_playlist_filename(episodes, Path("favorites.m3u")),
            "favorites.m3u8",
        )
        self.assertEqual(
            dlpplayer._suggest_playlist_filename(episodes, Path("playlist.m3u")),
            "My- Show - Episode 1 - 2 episodes.m3u8",
        )

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

    def test_lists_single_page_bilibili_video_as_one_episode(self):
        url = (
            "https://www.bilibili.com/video/"
            "BV1VZWc6dECG/?spm_id_from=333.337.search-card.all.click"
        )
        html = (
            '<script>window.__INITIAL_STATE__={"videoData":{"pages":['
            '{"page":1,"part":"Season 1 compilation","duration":3297}]}};</script>'
        )

        class FakeResponse(BytesIO):
            headers = {"Content-Encoding": "identity"}

        with patch(
            "dlpplayer.urlopen", return_value=FakeResponse(html.encode("utf-8"))
        ) as open_page:
            with patch.object(dlpplayer.subprocess, "run") as run:
                episodes = dlpplayer.fetch_playlist_episodes("yt-dlp.exe", url)

        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "Season 1 compilation",
                    "https://www.bilibili.com/video/BV1VZWc6dECG?p=1",
                    3297,
                ),
            ),
        )
        open_page.assert_called_once()
        run.assert_not_called()

    def test_reads_bilibili_ugc_season_and_selects_current_episode(self):
        url = "https://www.bilibili.com/video/BVcurrent/?spm_id_from=search"
        html = (
            '<script>window.__INITIAL_STATE__={"videoData":'
            '{"pages":[{"page":1,"part":"Current video","duration":90}],'
            '"ugc_season":{"sections":[{"episodes":['
            '{"bvid":"BVfirst","title":"Episode one",'
            '"arc":{"duration":60}},'
            '{"bvid":"BVcurrent","title":"Episode two",'
            '"arc":{"duration":90}},'
            '{"bvid":"BVlast","title":"Episode three",'
            '"arc":{"duration":120}}]}]}}};</script>'
        )

        class FakeResponse(BytesIO):
            headers = {"Content-Encoding": "identity"}

        with patch(
            "dlpplayer.urlopen", return_value=FakeResponse(html.encode("utf-8"))
        ):
            episodes = dlpplayer.fetch_playlist_episodes("yt-dlp.exe", url)

        self.assertEqual(
            episodes,
            (
                dlpplayer.PlaylistEpisode(
                    "Episode one", "https://www.bilibili.com/video/BVfirst", 60
                ),
                dlpplayer.PlaylistEpisode(
                    "Episode two", "https://www.bilibili.com/video/BVcurrent", 90
                ),
                dlpplayer.PlaylistEpisode(
                    "Episode three", "https://www.bilibili.com/video/BVlast", 120
                ),
            ),
        )
        self.assertEqual(dlpplayer._playlist_start_index(url, episodes), 1)

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
