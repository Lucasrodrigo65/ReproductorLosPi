import unittest
from unittest.mock import patch

import overlay


class RoomSelectorTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(overlay.StreamingMusicPlayerEnhanced, 'setup_hotkeys'),
            patch.object(overlay, 'load_settings', return_value={'token': 'dummy', 'channel_id': '123'}),
            patch.object(overlay.messagebox, 'askyesno', return_value=False),
            patch.object(overlay.messagebox, 'showerror'),
            patch.object(overlay, 'save_settings'),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.app = overlay.StreamingMusicPlayerEnhanced()
        self.app.root.withdraw()
        self.addCleanup(self.app.root.destroy)
        self.addCleanup(self.app.cleanup)
        self.rooms = [
            {'id': '123', 'server': 'Pibes', 'category': 'Voz', 'name': 'Sala'},
            {'id': '456', 'server': 'Pibes', 'category': 'Voz', 'name': 'Sala'},
        ]

    def test_restores_saved_room_and_distinguishes_duplicate_names(self):
        self.app._populate_discord_rooms(self.rooms)
        self.assertEqual(len(self.app.discord_rooms), 2)
        self.assertEqual(self.app.discord_rooms[self.app.discord_room_name.get()], '123')

    def test_selection_saves_actual_id(self):
        self.app._populate_discord_rooms(self.rooms)
        self.app.discord_room_name.set(next(label for label, value in self.app.discord_rooms.items() if value == '456'))
        self.app.select_discord_room()
        self.assertEqual(self.app.discord_channel.get(), '456')
        overlay.save_settings.assert_called_once_with('dummy', '456')

    def test_inaccessible_saved_room_is_cleared(self):
        self.app._populate_discord_rooms([])
        self.assertEqual(self.app.discord_channel.get(), '')
        self.assertEqual(self.app.discord_rooms, {})


if __name__ == '__main__':
    unittest.main()
