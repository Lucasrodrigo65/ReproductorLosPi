from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from discord_settings import SettingsError
from shared_app import SharedDesktopPlayer
from shared_settings import load_connection, save_connection, validate_server_url


class SharedSettingsTests(unittest.TestCase):
    def test_shared_key_round_trip_without_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'connection.json'
            key = 'example-control-key-' + 'x' * 32
            save_connection('https://music.example.com', key, 'Amigo', '123', path)
            self.assertNotIn(key, path.read_text())
            result = load_connection(path)
            self.assertEqual(result['key'], key)
            self.assertEqual(result['channel_id'], '123')

    def test_requires_https_except_for_loopback_tests(self):
        self.assertEqual(validate_server_url('http://127.0.0.1:8080'), 'http://127.0.0.1:8080')
        for url in ['http://music.example.com', 'https://user:pass@example.com', 'https://example.com?key=secret']:
            with self.assertRaises(SettingsError):
                validate_server_url(url)

    def test_desktop_does_not_load_or_connect_with_private_bot_credentials(self):
        with patch('shared_app.load_connection', return_value={}), \
                patch('overlay.load_settings', side_effect=AssertionError('must not read private settings')), \
                patch.object(SharedDesktopPlayer, 'setup_hotkeys'), \
                patch('overlay.VLC_OK', False), \
                patch('overlay.messagebox.askyesno', return_value=False):
            app = SharedDesktopPlayer()
            app.root.withdraw()
            try:
                self.assertEqual(app.discord_token.get(), '')
                self.assertEqual(app.discord_channel.get(), '')
                self.assertIsNone(app.discord_player)
                self.assertFalse(app.audio_initialized)
                self.assertEqual(app.discord_config_btn.cget('text'), 'Servidor compartido')
            finally:
                app.cleanup()
                app.root.destroy()


if __name__ == '__main__':
    unittest.main()
