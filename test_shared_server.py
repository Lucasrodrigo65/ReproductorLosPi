import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

from aiohttp.test_utils import TestClient, TestServer
from shared_server import ControlError, SharedController, create_app
from shared_client import RemoteMusicPlayer

URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
KEY = 'shared-test-key-' + 'a' * 32


class FakePlayer:
    def __init__(self):
        self.playback_generation = 0
        self.calls = []
        self.voice = MagicMock()
        self.client = SimpleNamespace(voice_clients=[self.voice])
        self.thread = MagicMock()
        self.close = MagicMock()
        self.on_audio_finished = None

    async def _list_rooms(self, token):
        return [{'id': str(i), 'server': 'Test', 'category': 'Voz', 'name': f'Sala {i}'} for i in (123, 456)]

    async def _play(self, token, channel, url):
        await asyncio.sleep(0.005)
        self.playback_generation += 1
        self.calls.append((channel, url))

    async def _test_sound(self, token, channel):
        await self._play(token, channel, 'tone')

    async def _play_file(self, token, channel, path):
        assert path.read_bytes() == b'fake-audio'
        await self._play(token, channel, 'uploaded-file')

    async def _disconnect(self):
        pass


class SharedServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.player = FakePlayer()
        self.controller = SharedController(self.player, 'fake-bot-token', asyncio.get_running_loop())
        async def direct(operation):
            return await operation
        self.controller._call = direct

    async def asyncTearDown(self):
        self.controller.media_dir.cleanup()

    async def play(self, channel='123', request_id=None):
        return await self.controller.command('play', {'channel_id': channel, 'url': URL, 'name': 'Amigo'}, request_id or str(uuid.uuid4()))

    async def test_idle_leaves_voice_at_five_minutes_and_can_play_again(self):
        from unittest.mock import AsyncMock
        self.player.voice.disconnect = AsyncMock()
        await self.play()
        await self.controller._finish(self.controller.current_generation, False)
        ended = self.controller.idle_since
        await self.controller.check_idle(ended + 299)
        self.player.voice.disconnect.assert_not_called()
        await self.controller.check_idle(ended + 300)
        self.player.voice.disconnect.assert_awaited_once_with(force=True)
        await self.controller.check_idle(ended + 400)
        self.player.voice.disconnect.assert_awaited_once()
        await self.play()
        self.assertFalse(self.controller.idle_disconnected)
        self.assertIsNotNone(self.controller.current)

    async def test_new_song_resets_idle_and_pause_keeps_session(self):
        from unittest.mock import AsyncMock
        self.player.voice.disconnect = AsyncMock()
        self.controller.idle_since = 0
        await self.play()
        self.controller.paused = True
        await self.controller.check_idle(1000)
        self.player.voice.disconnect.assert_not_called()
        self.assertIsNone(self.controller.idle_since)
        await self.play()
        await self.controller._finish(self.controller.current_generation, False)
        await self.controller.check_idle(2000)
        self.player.voice.disconnect.assert_not_called()

    async def test_concurrent_friends_share_one_playback_and_queue(self):
        await asyncio.gather(*(self.play() for _ in range(20)))
        self.assertEqual(len(self.player.calls), 1)
        self.assertEqual(len(self.controller.pending), 19)
        self.assertIsNotNone(self.controller.current)

    async def test_retry_does_not_add_duplicate_track(self):
        request_id = str(uuid.uuid4())
        first = await self.play(request_id=request_id)
        self.assertEqual(await self.play(request_id=request_id), first)
        self.assertEqual(len(self.player.calls), 1)
        self.assertEqual(len(self.controller.pending), 0)

    async def test_busy_bot_cannot_be_stolen_by_another_room(self):
        await self.play()
        with self.assertRaises(ControlError) as error:
            await self.play(channel='456')
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.player.calls), 1)

    async def test_natural_finish_advances_queue(self):
        await self.play()
        await self.play()
        await self.controller._finish(self.controller.current_generation, False)
        self.assertEqual(len(self.player.calls), 2)
        self.assertEqual(len(self.controller.pending), 0)

    async def test_stale_finish_after_skip_does_not_skip_new_track(self):
        await self.play()
        generation = self.controller.current_generation
        await self.play()
        await self.play()
        await self.controller.command('skip', {}, str(uuid.uuid4()))
        await self.controller._finish(generation, False)
        self.assertEqual(len(self.player.calls), 2)
        self.assertEqual(len(self.controller.pending), 1)

    async def test_stop_clears_queue_and_rejects_stale_finish(self):
        await self.play()
        generation = self.controller.current_generation
        await self.play()
        # Fake disconnect must be awaitable.
        from unittest.mock import AsyncMock
        self.player.voice.disconnect = AsyncMock()
        await self.controller.command('stop', {}, str(uuid.uuid4()))
        await self.controller._finish(generation, False)
        self.assertIsNone(self.controller.current)
        self.assertFalse(self.controller.pending)
        self.assertEqual(len(self.player.calls), 1)

    async def test_remote_url_cannot_target_arbitrary_servers(self):
        with self.assertRaises(ControlError):
            await self.controller.command('play', {'channel_id': '123', 'url': 'http://127.0.0.1/private'}, str(uuid.uuid4()))
        self.assertEqual(self.player.calls, [])

    async def test_http_authentication_and_no_secret_in_status(self):
        client = TestClient(TestServer(create_app(self.controller, KEY)))
        await client.start_server()
        try:
            response = await client.get('/v1/state')
            self.assertEqual(response.status, 401)
            response = await client.get('/healthz')
            self.assertEqual(response.status, 200)
            response = await client.get('/v1/state', headers={'Authorization': 'Bearer ' + KEY})
            self.assertEqual(response.status, 200)
            text = await response.text()
            self.assertNotIn(KEY, text)
            self.assertNotIn('fake-bot-token', text)
        finally:
            await client.close()

    async def test_two_real_desktop_clients_use_same_http_queue(self):
        client = TestClient(TestServer(create_app(self.controller, KEY)))
        await client.start_server()
        endpoint = str(client.make_url('')).rstrip('/')
        friends = [RemoteMusicPlayer(endpoint, KEY, name) for name in ('Amigo A', 'Amigo B')]
        try:
            for friend in friends:
                friend._prepare_audio = lambda payload: payload
                friend.play('', 123, URL)
            async with asyncio.timeout(5):
                while len(self.player.calls) != 1 or len(self.controller.pending) != 1:
                    await asyncio.sleep(0.01)
            self.assertEqual(len(self.player.calls), 1)
            for friend in friends:
                friend.close()
            for friend in friends:
                await asyncio.to_thread(friend.thread.join, 2)
            self.assertIsNotNone(self.controller.current)
            self.assertEqual(len(self.controller.pending), 1)
        finally:
            for friend in friends:
                friend.close()
            await client.close()

    async def test_upload_auth_retry_play_and_cleanup(self):
        client = TestClient(TestServer(create_app(self.controller, KEY)))
        await client.start_server()
        ident = str(uuid.uuid4())
        headers = {'Authorization': 'Bearer ' + KEY}
        try:
            self.assertEqual((await client.put('/v1/audio/' + ident, data=b'fake-audio')).status, 401)
            response = await client.put('/v1/audio/' + ident, data=b'fake-audio', headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual((await client.put('/v1/audio/' + ident, data=b'fake-audio', headers=headers)).status, 200)
            path = self.controller.uploads[ident]['path']
            result = await self.controller.command('play', {'channel_id': '123', 'upload_id': ident}, str(uuid.uuid4()))
            self.assertNotIn(str(path), str(result))
            self.assertEqual(self.player.calls, [(123, 'uploaded-file')])
            with self.assertRaises(ControlError):
                await self.controller.command('play', {'channel_id': '123', 'upload_id': ident}, str(uuid.uuid4()))
            await self.controller._finish(self.controller.current_generation, False)
            self.assertFalse(path.exists())
            self.assertFalse(self.controller.uploads)
            self.assertEqual((await client.put('/v1/audio/not-a-uuid', data=b'x', headers=headers)).status, 400)
        finally:
            await client.close()

    async def test_desktop_download_upload_and_shared_queue(self):
        from pathlib import Path
        def fake_downloader(options):
            downloader = MagicMock()
            filename = options['outtmpl'].replace('%(ext)s', 'webm')
            def extract(url, download):
                Path(filename).write_bytes(b'fake-audio')
                return {'url': url}
            downloader.extract_info.side_effect = extract
            downloader.prepare_filename.return_value = filename
            downloader.__enter__.return_value = downloader
            return downloader
        client = TestClient(TestServer(create_app(self.controller, KEY)))
        await client.start_server()
        friends = []
        try:
            with patch('yt_dlp.YoutubeDL', side_effect=fake_downloader):
                friends = [RemoteMusicPlayer(str(client.make_url('')).rstrip('/'), KEY, name) for name in ('A', 'B')]
                for friend in friends:
                    friend.play('', 123, URL)
                async with asyncio.timeout(10):
                    while len(self.controller.uploads) != 2 or len(self.controller.pending) != 1:
                        await asyncio.sleep(.02)
                self.assertEqual(self.player.calls, [(123, 'uploaded-file')])
                await self.controller.command('skip', {}, str(uuid.uuid4()))
                self.assertEqual(len(self.controller.uploads), 1)
                self.assertEqual(len(self.player.calls), 2)
                from unittest.mock import AsyncMock
                self.player.voice.disconnect = AsyncMock()
                await self.controller.command('stop', {}, str(uuid.uuid4()))
                self.assertFalse(self.controller.uploads)
        finally:
            for friend in friends:
                friend.close()
                await asyncio.to_thread(friend.thread.join, 2)
            await client.close()


if __name__ == '__main__':
    unittest.main()
