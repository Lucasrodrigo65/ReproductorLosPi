import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from discord_settings import DEFAULT_CHANNEL, SettingsError, load_settings, save_settings

TEST_CHANNEL = '123456789012345678'


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config' / 'discord.json'

    def test_saved_credentials_load_in_new_process(self):
        token = 'dummy-token-for-settings-test'
        save_settings(token, TEST_CHANNEL, self.path)
        self.assertNotIn(token, self.path.read_text())
        code = (
            'import sys; from discord_settings import load_settings; '
            's=load_settings(sys.argv[1]); '
            "assert s['token']=='dummy-token-for-settings-test'; "
            f"assert s['channel_id']=='{TEST_CHANNEL}'"
        )
        subprocess.run([sys.executable, '-c', code, str(self.path)], check=True,
                       capture_output=True, timeout=15)

    def test_public_channel_starts_empty(self):
        self.assertEqual(DEFAULT_CHANNEL, '')

    def test_missing_settings(self):
        self.assertEqual(load_settings(self.path), {})

    def test_damaged_encrypted_token(self):
        save_settings('dummy-token', TEST_CHANNEL, self.path)
        payload = json.loads(self.path.read_text())
        payload['token_dpapi'] = 'aW52YWxpZA=='
        self.path.write_text(json.dumps(payload))
        with self.assertRaises(SettingsError):
            load_settings(self.path)

    def test_invalid_configuration_does_not_replace_saved_data(self):
        save_settings('original-dummy', TEST_CHANNEL, self.path)
        with self.assertRaises(SettingsError):
            save_settings('new-dummy', 'invalid-channel', self.path)
        self.assertEqual(load_settings(self.path)['token'], 'original-dummy')

    def test_failed_write_preserves_saved_configuration(self):
        save_settings('original-dummy', TEST_CHANNEL, self.path)
        with patch('discord_settings.os.replace', side_effect=OSError('test')):
            with self.assertRaises(SettingsError):
                save_settings('replacement-dummy', TEST_CHANNEL, self.path)
        self.assertEqual(load_settings(self.path)['token'], 'original-dummy')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
