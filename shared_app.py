"""Distributable app for friends: a shared-service key replaces the bot token."""
import tkinter as tk
from tkinter import messagebox

from discord_settings import SettingsError
from overlay import StreamingMusicPlayerEnhanced
from shared_client import RemoteMusicPlayer
from shared_settings import load_connection, save_connection, validate_server_url


class SharedDesktopPlayer(StreamingMusicPlayerEnhanced):
    def __init__(self):
        self.connection = {}
        self.shared_paused = False
        super().__init__()
        self.root.title('Los π · Reproductor compartido')
        self.root.geometry('760x900')
        if not self.connection:
            self.discord_status.config(text='Configurá el servidor compartido para conectarte.', fg='#aaa')

    def read_discord_configuration(self):
        self.connection = load_connection()
        return {'token': self.connection.get('key', ''), 'channel_id': self.connection.get('channel_id', '')}

    def show_vlc_install_message(self):
        # Voice runs on the server; VLC is optional for local listening only.
        pass

    def persist_discord_configuration(self, token, channel):
        save_connection(self.connection['url'], token, self.connection.get('name', 'Amigo'), channel)

    def new_discord_player(self):
        return RemoteMusicPlayer(self.connection['url'], self.discord_token.get().strip(), self.connection.get('name', 'Amigo'))

    def create_ui(self):
        super().create_ui()
        self.discord_config_btn.config(text='Servidor compartido')
        self.discord_play_btn.config(text='Agregar a Discord')
        self.discord_disconnect_btn.config(text='Detener sesión')
        controls = tk.Frame(self.root, bg='#1a1a1a', padx=15)
        controls.pack(fill=tk.X)
        self.shared_skip_btn = tk.Button(controls, text='Siguiente en Discord', command=self.shared_skip)
        self.shared_skip_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.shared_pause_btn = tk.Button(controls, text='Pausar en Discord', command=self.shared_pause)
        self.shared_pause_btn.pack(side=tk.LEFT)
        tk.Button(controls, text='Administrar servidores', command=self.manage_servers).pack(side=tk.RIGHT)
        self.shared_queue = tk.Label(self.root, text='Conectate al servidor compartido para ver la cola.',
                                     bg='#1a1a1a', fg='#ccc', justify=tk.LEFT, anchor='w',
                                     wraplength=710, padx=15, pady=10)
        self.shared_queue.pack(fill=tk.X)

    def manage_servers(self):
        from server_admin import ServerAdminWindow
        ServerAdminWindow(self.root, self.connection.get('url', ''))

    def configure_discord(self):
        window = tk.Toplevel(self.root)
        window.title('Servidor compartido de Los π')
        window.geometry('560x305')
        window.transient(self.root)
        url = tk.StringVar(value=self.connection.get('url', ''))
        key = tk.StringVar(value=self.connection.get('key', ''))
        name = tk.StringVar(value=self.connection.get('name', 'Amigo'))
        for label, variable, hidden in [('Dirección HTTPS del servidor:', url, False),
                                         ('Clave de acceso compartida:', key, True),
                                         ('Tu nombre para la cola:', name, False)]:
            tk.Label(window, text=label).pack(anchor='w', padx=15, pady=(10, 3))
            tk.Entry(window, textvariable=variable, show='*' if hidden else '', width=70).pack(padx=15)
        tk.Label(window, text='Pedile la dirección y la clave al administrador.\nEl token del bot se configura únicamente en el servidor.').pack(pady=10)
        def save():
            if self.discord_busy:
                return
            try:
                endpoint = validate_server_url(url.get())
                changed = endpoint != self.connection.get('url') or key.get().strip() != self.connection.get('key')
                channel = '' if changed else self.discord_channel.get()
                save_connection(endpoint, key.get(), name.get(), channel)
            except SettingsError as exc:
                messagebox.showerror('Servidor compartido', str(exc), parent=window)
                return
            if self.discord_player:
                self.discord_player.close()
                self.discord_player = None
            self.connection = {'url': endpoint, 'key': key.get().strip(), 'name': name.get().strip() or 'Amigo'}
            self.discord_token.set(self.connection['key'])
            self.discord_channel.set(channel)
            self.discord_rooms_token = '' if changed else self.connection['key']
            window.destroy()
            self.load_discord_rooms()
        tk.Button(window, text='Guardar y conectar', command=save).pack()

    def play_in_discord(self):
        if self.discord_token.get().strip() and self.discord_player is None:
            self.discord_player = self.new_discord_player()
        if self.discord_player:
            title = 'Canción de YouTube'
            selection = self.tree.selection()
            if selection:
                index = int(self.tree.item(selection[0])['text']) - 1
                if 0 <= index < len(self.results):
                    title = self.results[index]['title']
            self.discord_player.next_title = title
        super().play_in_discord()

    def shared_skip(self):
        if self.discord_player and not self.discord_busy:
            self._set_discord_busy(True)
            self.discord_player.skip()

    def shared_pause(self):
        if self.discord_player and not self.discord_busy:
            self._set_discord_busy(True)
            self.discord_player.pause(self.shared_paused)

    def _set_discord_busy(self, busy):
        super()._set_discord_busy(busy)
        self.shared_skip_btn.config(state='disabled' if busy else 'normal')
        self.shared_pause_btn.config(state='disabled' if busy else 'normal')

    def handle_discord_event(self, kind, message):
        if kind == 'shared_offline':
            self.shared_queue.config(text=message, fg='orange')
            return True
        if kind != 'shared_state':
            return False
        current = message.get('current')
        pending = message.get('queue', [])
        self.shared_paused = message.get('paused', False)
        self.shared_pause_btn.config(text='Continuar en Discord' if self.shared_paused else 'Pausar en Discord')
        lines = [f"{'Pausado' if self.shared_paused else 'Sonando'}: {current['title']} · {current['requested_by']}" if current else 'Discord: sin canción en reproducción.']
        lines.append(f'Cola compartida: {len(pending)} canciones')
        lines.extend(f"{i + 1}. {item['title']} · {item['requested_by']}" for i, item in enumerate(pending[:3]))
        if message.get('last_error'):
            lines.append(message['last_error'])
        self.shared_queue.config(text='\n'.join(lines), fg='#ccc')
        return True


if __name__ == '__main__':
    SharedDesktopPlayer().run()
