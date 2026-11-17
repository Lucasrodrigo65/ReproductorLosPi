import asyncio
import unittest
import uuid
import os
from pathlib import Path
import subprocess
import sys
import time
import socket
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

from aiohttp.test_utils import TestClient, TestServer
from shared_server import create_app
from multi_guild import MultiGuildController, parse_guild_keys

KEY_A, KEY_B = 'a' * 40, 'b' * 40
URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'


class FakeTransport:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.voice_generations = {}
        self.thread = MagicMock()
        self.close = MagicMock()
        self.guilds = [SimpleNamespace(id=i, voice_channels=[SimpleNamespace(id=i * 10)]) for i in (1, 2)]
        self.voices = []
        for guild in self.guilds:
            voice = MagicMock()
            voice.guild = guild
            voice.disconnect = AsyncMock()
            self.voices.append(voice)
        self.client = SimpleNamespace(guilds=self.guilds, voice_clients=self.voices,
                                      get_channel=lambda channel: SimpleNamespace(guild=self.guilds[channel // 10 - 1]))

    async def _connect(self, token):
        pass

    async def _list_rooms(self, token):
        return [{'id': str(i * 10), 'server': 'Server ' + str(i), 'name': 'Voice', 'category': ''} for i in (1, 2)]

    async def _play(self, token, channel, url):
        guild = channel // 10
        self.voice_generations[guild] = self.voice_generations.get(guild, 0) + 1

    async def _play_file(self, token, channel, path):
        await self._play(token, channel, str(path))

    async def _test_sound(self, token, channel):
        await self._play(token, channel, 'tone')


class KeyConfigTests(unittest.TestCase):
    def test_real_server_entrypoint_preserves_control_error_status(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        environment = {**os.environ, 'PORT': str(port), 'DISCORD_BOT_TOKEN': 'fake-not-used',
                       'CONTROL_KEY': KEY_A, 'ALLOWED_GUILD_IDS': '1', 'GUILD_ACCESS_KEYS': ''}
        process = subprocess.Popen([sys.executable, 'shared_server.py'], cwd=Path(__file__).parent,
                                   env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = 'http://127.0.0.1:' + str(port)
            deadline = time.monotonic() + 10
            while True:
                try:
                    with urlopen(base + '/healthz', timeout=1) as response:
                        self.assertEqual(response.status, 200)
                    break
                except (URLError, OSError):
                    if time.monotonic() > deadline or process.poll() is not None:
                        self.fail('El punto de entrada real no inició')
                    time.sleep(.05)
            request = Request(base + '/v1/commands/unknown', data=b'{}',
                              headers={'Authorization': 'Bearer ' + KEY_A, 'Content-Type': 'application/json',
                                       'X-Request-ID': str(uuid.uuid4())})
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_legacy_key_is_bound_to_one_guild(self):
        self.assertEqual(parse_guild_keys('', KEY_A, '1'), {1: KEY_A})
        self.assertEqual(parse_guild_keys('{"2":"' + KEY_B + '"}', KEY_A, '1'), {1: KEY_A, 2: KEY_B})

    def test_invalid_config_fails_closed(self):
        for value in ('{}', '[]', '{"1":"short"}', '{"1":"' + KEY_A + '","2":"' + KEY_A + '"}', '{"1":"' + KEY_A + '","1":"' + KEY_B + '"}'):
            with self.assertRaises(ValueError):
                parse_guild_keys(value)
        with self.assertRaises(ValueError):
            parse_guild_keys('', KEY_A, '1,2')


class MultiGuildTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = FakeTransport()
        self.manager = MultiGuildController(self.transport, 'fake-token', asyncio.get_running_loop(), {1: KEY_A, 2: KEY_B})
        self.http = TestClient(TestServer(create_app(self.manager, None)))
        await self.http.start_server()

    async def asyncTearDown(self):
        await self.http.close()

    def headers(self, key):
        return {'Authorization': 'Bearer ' + key, 'X-Request-ID': str(uuid.uuid4())}

    async def play(self, guild):
        return await self.http.post('/v1/commands/play', json={'channel_id': str(guild * 10), 'url': URL, 'title': 'Private ' + str(guild)}, headers=self.headers(KEY_A if guild == 1 else KEY_B))

    async def test_simultaneous_playback_and_private_state(self):
        replies = await asyncio.gather(self.play(1), self.play(2))
        self.assertEqual([r.status for r in replies], [200, 200])
        await self.play(1)
        await self.play(2)
        for guild, key in ((1, KEY_A), (2, KEY_B)):
            response = await self.http.get('/v1/state', headers=self.headers(key))
            state = await response.json()
            self.assertEqual(state['current']['title'], 'Private ' + str(guild))
            self.assertEqual(len(state['queue']), 1)
        self.assertEqual(self.transport.voice_generations, {1: 1, 2: 1})

    async def test_keys_cannot_see_or_control_other_guild(self):
        await self.play(2)
        response = await self.http.get('/v1/rooms', headers=self.headers(KEY_A))
        self.assertEqual([r['id'] for r in (await response.json())['rooms']], ['10'])
        response = await self.http.post('/v1/commands/play', json={'channel_id': '20', 'url': URL}, headers=self.headers(KEY_A))
        self.assertEqual(response.status, 403)
        await self.http.post('/v1/commands/stop', json={}, headers=self.headers(KEY_A))
        self.transport.voices[1].stop.assert_not_called()
        self.transport.voices[1].disconnect.assert_not_awaited()
        self.assertIsNotNone(self.manager.sessions[2].current)
        self.assertEqual((await self.http.get('/v1/state', headers=self.headers('bad'))).status, 401)

    async def test_upload_cannot_be_claimed_by_another_guild(self):
        ident = str(uuid.uuid4())
        response = await self.http.put('/v1/audio/' + ident, data=b'fake-audio', headers=self.headers(KEY_A))
        self.assertEqual(response.status, 200)
        response = await self.http.post('/v1/commands/play', json={'channel_id': '20', 'upload_id': ident}, headers=self.headers(KEY_B))
        self.assertEqual(response.status, 409)

    async def test_idle_and_skip_affect_only_target_guild(self):
        await self.play(1)
        await self.play(2)
        await self.play(2)
        a, b = self.manager.sessions[1], self.manager.sessions[2]
        await a._finish(a.current_generation, False)
        await a.check_idle(a.idle_since + 300)
        self.transport.voices[0].disconnect.assert_awaited_once()
        self.transport.voices[1].disconnect.assert_not_awaited()
        old_generation = b.current_generation
        await b.command('skip', {}, str(uuid.uuid4()))
        await b._finish(old_generation, False)
        self.assertIsNotNone(b.current)
        self.assertNotEqual(b.current_generation, old_generation)

    async def test_pause_does_not_pause_other_guild(self):
        await self.play(1)
        await self.play(2)
        await self.http.post('/v1/commands/pause', json={}, headers=self.headers(KEY_A))
        self.transport.voices[0].pause.assert_called_once()
        self.transport.voices[1].pause.assert_not_called()
