import asyncio
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord_player import DiscordMusicPlayer


class DiscordPlaybackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Exercise the real coroutine logic without opening Discord connections.
        self.player = DiscordMusicPlayer.__new__(DiscordMusicPlayer)
        self.player.client = MagicMock()
        self.player.client.is_ready.return_value = True
        self.player.token = 'test-token'
        self.player.gateway = None
        self.player.playback_generation = 0
        self.player.events = queue.Queue()
        self.channel = MagicMock(spec=discord.VoiceChannel)
        self.channel.id = 123
        self.channel.guild.me.voice = SimpleNamespace(mute=False, suppress=False)
        self.channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True, connect=True, speak=True)
        self.voice = MagicMock()
        self.voice.is_connected.return_value = True
        self.voice.channel.id = 123
        self.channel.guild.voice_client = self.voice
        self.player.client.voice_clients = [self.voice]
        self.player.client.get_channel.return_value = self.channel
        self.info = {'url': 'https://example.com/audio',
                     'http_headers': {'User-Agent': 'test agent'}}

    async def test_existing_connection_replaces_audio(self):
        source = MagicMock()
        with patch.object(self.player, '_extract', return_value=self.info), \
                patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='ffmpeg'), \
                patch('discord.FFmpegOpusAudio', return_value=source) as audio:
            await self.player._play('test-token', 123, 'https://youtu.be/dQw4w9WgXcQ')
        self.voice.stop.assert_called_once()
        self.voice.play.assert_called_once()
        self.assertIs(self.voice.play.call_args.args[0].source, source)
        self.assertEqual(audio.call_args.args[0], self.info['url'])

    async def test_missing_permissions_does_not_extract_or_play(self):
        self.channel.permissions_for.return_value.speak = False
        with patch.object(self.player, '_extract') as extract, \
                patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='ffmpeg'):
            with self.assertRaisesRegex(RuntimeError, 'Hablar'):
                await self.player._play('test-token', 123, 'url')
        extract.assert_not_called()
        self.voice.play.assert_not_called()

    async def test_new_voice_connection(self):
        self.channel.guild.voice_client = None
        self.channel.connect = AsyncMock(return_value=self.voice)
        with patch.object(self.player, '_extract', return_value=self.info), \
                patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='ffmpeg'), \
                patch('discord.FFmpegOpusAudio'):
            await self.player._play('test-token', 123, 'url')
        self.channel.connect.assert_awaited_once_with(timeout=30, reconnect=True, self_deaf=True)

    async def test_failed_play_cleans_ffmpeg(self):
        self.voice.play.side_effect = RuntimeError('failed')
        with patch.object(self.player, '_extract', return_value=self.info), \
                patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='ffmpeg'), \
                patch('discord.FFmpegOpusAudio') as audio:
            with self.assertRaises(RuntimeError):
                await self.player._play('test-token', 123, 'url')
            audio.return_value.cleanup.assert_called_once()

    async def test_tone_does_not_use_youtube(self):
        with patch.object(self.player, '_extract') as extract, \
                patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='ffmpeg'), \
                patch('discord.FFmpegOpusAudio') as audio:
            await self.player._test_sound('test-token', 123)
        extract.assert_not_called()
        self.assertIn('sine=', audio.call_args.args[0])
        self.assertIn('-f lavfi', audio.call_args.kwargs['before_options'])

    async def test_empty_audio_reports_failure(self):
        source = MagicMock()
        source.read.return_value = b''
        self.player._start_audio(self.voice, source)
        self.voice.play.call_args.kwargs['after'](None)
        kind, message = self.player.events.get_nowait()
        self.assertEqual(kind, 'playback_error')
        self.assertIn('no produjo audio', message)

    async def test_old_playback_callback_is_ignored(self):
        self.player._start_audio(self.voice, MagicMock())
        old_finished = self.voice.play.call_args.kwargs['after']
        self.player._start_audio(self.voice, MagicMock())
        old_finished(None)
        self.assertTrue(self.player.events.empty())

    async def test_multiguild_audio_callbacks_have_independent_generations(self):
        self.player.multi_guild = True
        self.player.voice_generations = {}
        self.player.on_guild_audio_finished = MagicMock()
        self.voice.guild.id = 1
        other = MagicMock()
        other.guild.id = 2
        self.player._start_audio(self.voice, MagicMock())
        first_finished = self.voice.play.call_args.kwargs['after']
        self.player._start_audio(other, MagicMock())
        first_finished(None)
        self.player.on_guild_audio_finished.assert_called_once_with(1, 1, False, 0)
        self.player.on_guild_audio_finished.reset_mock()
        self.player._start_audio(self.voice, MagicMock())
        first_finished(None)
        self.player.on_guild_audio_finished.assert_not_called()

    async def test_multiguild_join_does_not_disconnect_other_guild(self):
        self.player.multi_guild = True
        other = MagicMock()
        other.disconnect = AsyncMock()
        self.player.client.voice_clients.append(other)
        await self.player._join_voice('test-token', 123)
        other.disconnect.assert_not_awaited()

    async def test_waits_for_voice_encryption(self):
        self.voice._connection = SimpleNamespace(dave_protocol_version=1, can_encrypt=False)
        async def encryption_ready(_):
            self.voice._connection.can_encrypt = True
        with patch('discord_player.asyncio.sleep', side_effect=encryption_ready) as wait:
            voice = await self.player._join_voice('test-token', 123)
        self.assertIs(voice, self.voice)
        wait.assert_awaited_once()

    async def test_server_muted_bot_reports_error(self):
        self.channel.guild.me.voice.mute = True
        with self.assertRaisesRegex(RuntimeError, 'silenciado'):
            await self.player._join_voice('test-token', 123)

    async def test_lists_only_accessible_voice_rooms(self):
        allowed = MagicMock()
        allowed.id = 123
        allowed.name = 'Sala disponible'
        allowed.category.name = 'Voz'
        allowed.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True, speak=True)
        blocked = MagicMock()
        blocked.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True, speak=False)
        server = SimpleNamespace(id=1, name='Servidor', me=object(), voice_channels=[allowed, blocked])
        self.player.client.guilds = [server]
        rooms = await self.player._list_rooms('test-token')
        self.assertEqual(rooms, [{'id': '123', 'server': 'Servidor', 'category': 'Voz', 'name': 'Sala disponible'}])

    async def test_no_servers_returns_empty_room_list(self):
        self.player.client.guilds = []
        self.assertEqual(await self.player._list_rooms('test-token'), [])

    async def test_changing_destination_moves_connected_bot(self):
        self.voice.channel.id = 456
        self.voice.move_to = AsyncMock()
        result = await self.player._join_voice('test-token', 123)
        self.voice.move_to.assert_awaited_once_with(self.channel)
        self.assertIs(result, self.voice)

    async def test_invalid_login_closes_client(self):
        self.player.client = None
        client = MagicMock()
        client.login = AsyncMock(side_effect=discord.LoginFailure('invalid token'))
        client.close = AsyncMock()
        client.voice_clients = []
        with patch('discord.Client', return_value=client):
            with self.assertRaises(discord.LoginFailure):
                await self.player._connect('wrong')
        client.close.assert_awaited_once()
        self.assertIsNone(self.player.client)
        self.assertIsNone(self.player.token)


class ThreadTests(unittest.TestCase):
    def test_errors_redact_token_and_thread_closes(self):
        player = DiscordMusicPlayer()

        async def fail():
            raise RuntimeError('invalid secret-value')

        try:
            player._submit(fail(), 'unused', 'secret-value')
            kind, message = player.events.get(timeout=5)
            self.assertEqual(kind, 'error')
            self.assertNotIn('secret-value', message)
        finally:
            player.close()
            player.thread.join(timeout=5)
        self.assertFalse(player.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
