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
