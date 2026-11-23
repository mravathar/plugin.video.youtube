"""Offline playback history regressions. Run: python3 -m unittest discover -s tests"""
import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest


def load_player_monitor():
    # Keep Kodi and the add-on package out of this offline test process.
    for name in ('offline', 'offline.kodion', 'offline.kodion.monitors',
                 'offline.kodion.utils'):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def stub(name, **values):
        module = types.ModuleType(name)
        module.__dict__.update(values)
        sys.modules[name] = module

    stub('offline.kodion.logging', getLogger=lambda name: None)
    stub('offline.kodion.compatibility', xbmc=types.SimpleNamespace(Player=object))
    constants = {name: name.lower() for name in (
        'BUSY_FLAG', 'CHANNEL_ID', 'PLAYBACK_STARTED', 'PLAYBACK_STOPPED',
        'PLAYER_DATA', 'PLAY_USING', 'REFRESH_CONTAINER', 'TRAKT_PAUSE_FLAG',
        'VIDEO_ID')}
    constants['PATHS'] = types.SimpleNamespace(PLAY='/play')
    stub('offline.kodion.constants', **constants)
    stub('offline.kodion.utils.redact', redact_params=lambda value: value)
    stub('offline.kodion.utils.convert_format', channel_filter_split=lambda x: (None, None, None))
    path = (Path(__file__).resolve().parents[1] / 'resources/lib/youtube_plugin/'
            'kodion/monitors/player_monitor.py')
    spec = importlib.util.spec_from_file_location('offline.kodion.monitors.player_monitor', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PlayerMonitorThread


PlayerMonitorThread = load_player_monitor()


class FakeClient:
    def __init__(self, logged_in):
        self.logged_in = logged_in
        self.reports = []

    def update_watch_history(self, video_id, url, **kwargs):
        self.reports.append((video_id, url, kwargs))


class HistoryPlaybackTest(unittest.TestCase):
    def run_playback(self, initial_login, login_after_reload, remote=True,
                     playback_url='start', watchtime_url='watchtime'):
        client = FakeClient(initial_login)
        state = {'reloads': 0, 'saved_login': initial_login}

        def reload_access_manager():
            state['reloads'] += 1
            state['saved_login'] = login_after_reload

        settings = types.SimpleNamespace(
            PLAY_REFRESH='play_refresh', get_bool=lambda *args: False,
            get_play_count_min_percent=lambda: 90)
        context = types.SimpleNamespace(
            reload_access_manager=reload_access_manager,
            get_access_manager=lambda: object(), get_settings=lambda: settings,
            get_playlist_player=lambda: object(), send_notification=lambda *args: None,
            abort_requested=lambda: False)

        def get_client(_context):
            client.logged_in = state['saved_login']
            return client

        provider = types.SimpleNamespace(get_client=get_client)
        player = types.SimpleNamespace(
            isPlaying=lambda: True, getPlayingFile=lambda: 'synthetic-file',
            getTime=lambda: 10, getTotalTime=lambda: 100, seeking=False,
            start_time=0, seek_time=0, end_time=0, current_time=0, total_time=0)
        worker = PlayerMonitorThread.__new__(PlayerMonitorThread)
        worker.video_id = 'synthetic-video'
        worker.channel_id = 'synthetic-channel'
        worker.video_status = {}
        worker.player_data = {
            'playing_file': 'synthetic-file', 'use_remote_history': remote,
            'playback_stats': {'playback_url': playback_url, 'watchtime_url': watchtime_url},
        }
        worker._context, worker._provider, worker._player = context, provider, player
        worker._stopped, worker._ended = threading.Event(), threading.Event()
        worker.current_time = worker.total_time = worker.progress = 0
        worker.log = types.SimpleNamespace(debug=lambda *args, **kwargs: None)
        # One loop iteration exercises the actual watchtime path without waiting.
        worker._monitor = types.SimpleNamespace(waitForAbort=lambda _: worker._stopped.set())
        worker.run()
        return state, client.reports

    def test_no_tracking_or_remote_disabled_never_reloads_or_reports(self):
        for remote, start, watchtime in ((True, None, None), (False, 'start', 'watchtime')):
            with self.subTest(remote=remote):
                state, reports = self.run_playback(False, True, remote, start, watchtime)
                self.assertEqual(state['reloads'], 0)
                self.assertEqual(reports, [])

    def test_authorized_client_does_not_reload(self):
        state, reports = self.run_playback(True, True)
        self.assertEqual(state['reloads'], 0)
        self.assertEqual([report[1] for report in reports], ['start', 'watchtime', 'watchtime'])
        self.assertEqual(reports[1][2]['status'][3], 'playing')

    def test_stale_client_reloads_then_reports_start_and_watchtime(self):
        state, reports = self.run_playback(False, True)
        self.assertEqual(state['reloads'], 1)
        self.assertEqual([report[1] for report in reports], ['start', 'watchtime', 'watchtime'])
        self.assertEqual(reports[1][2]['status'][3], 'playing')

    def test_signed_out_after_reload_never_reports(self):
        state, reports = self.run_playback(False, False)
        self.assertEqual(reports, [])
        self.assertEqual(state['reloads'], 1)

    def test_watchtime_alone_can_trigger_reload(self):
        state, reports = self.run_playback(False, True, playback_url=None)
        self.assertEqual(state['reloads'], 1)
        self.assertEqual([report[1] for report in reports], ['watchtime', 'watchtime'])


if __name__ == '__main__':
    unittest.main()
