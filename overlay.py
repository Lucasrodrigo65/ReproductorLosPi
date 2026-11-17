import tkinter as tk
from tkinter import messagebox, ttk
import threading
import webbrowser
import subprocess
import sys
import os
import re
import queue
from window_hotkey import WindowsHotkey, is_foreground, promote
from discord_player import DiscordMusicPlayer
from discord_settings import DEFAULT_CHANNEL, SettingsError, load_settings, save_settings
from urllib.parse import urlparse, parse_qs

try:
    import yt_dlp
    YT_OK = True
except ImportError:
    YT_OK = False

try:
    import vlc
    VLC_OK = True
except (ImportError, OSError):
    VLC_OK = False

try:
    import pynput
    from pynput import keyboard
    HOTKEY_OK = True
except ImportError:
    HOTKEY_OK = False

class StreamingMusicPlayerEnhanced:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Reproductor de Discord de Los π")
        self.root.geometry("700x760")
        self.root.attributes('-alpha', 1.0)
        self.root.configure(bg='#1a1a1a')
        self.server_photo = None
        image_path = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'assets', 'server.png')
        if os.path.isfile(image_path):
            self.server_icon = tk.PhotoImage(file=image_path)
            self.root.iconphoto(True, self.server_icon)
            factor = max(1, (max(self.server_icon.width(), self.server_icon.height()) + 63) // 64)
            header_image = os.path.join(os.path.dirname(image_path), 'server-header.png')
            self.server_photo = tk.PhotoImage(file=header_image) if os.path.isfile(header_image) else self.server_icon.subsample(factor, factor)

        # Variables
        self.playing = False
        self.paused = False
        self.current_url = None
        self.results = []
        self.current_index = -1
        self.player = None
        self.is_visible = True
        self.hotkey_listener = None
        self.hotkey_events = queue.Queue()
        self.hotkey_closed = False
        self.url_cache = {}  # Cache para URLs de streaming
        self.current_total_duration = 0
        self.is_dragging_progress = False
        self.discord_player = None
        self.discord_busy = False
        self.settings_error = None
        try:
            saved_settings = self.read_discord_configuration()
        except SettingsError as exc:
            saved_settings = {}
            self.settings_error = str(exc)
        self.discord_token = tk.StringVar(value=saved_settings.get('token', ''))
        self.discord_channel = tk.StringVar(value=saved_settings.get('channel_id', DEFAULT_CHANNEL))
        self.discord_room_name = tk.StringVar(value='Cargá las salas para elegir dónde reproducir')
        self.discord_rooms = {}
        self.discord_rooms_token = self.discord_token.get().strip()

        # Initialize VLC player
        if VLC_OK:
            try:
                vlc_args = [
                    '--no-xlib',
                    '--file-caching=1000',
                    '--network-caching=1000',
                    '--live-caching=1000',
                    '--sout-mux-caching=1000'
                ]
                self.vlc_instance = vlc.Instance(' '.join(vlc_args))
                self.player = self.vlc_instance.media_player_new()
                self.audio_initialized = True
            except Exception as e:
                self.audio_initialized = False
                print(f"VLC initialization error: {e}")
        else:
            self.audio_initialized = False

        # Setup hotkeys
        self.setup_hotkeys()

        # Create interface
        self.create_ui()
        if self.settings_error:
            self.discord_status.config(text=self.settings_error, fg='orange')
        elif saved_settings:
            self.discord_status.config(text="Discord: configuración guardada cargada", fg='#aaa')
        self.root.after(100, self._poll_discord)
        if self.discord_token.get().strip():
            self.root.after(500, self.load_discord_rooms)

        # Setup window events
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Check dependencies
        if not YT_OK:
            self.show_install_message("yt-dlp")
        elif not VLC_OK:
            self.show_vlc_install_message()

    def setup_hotkeys(self):
        """Setup global hotkeys"""
        self.root.after(50, self._poll_hotkey)
        if os.name == 'nt':
            try:
                self.hotkey_listener = WindowsHotkey()
                return
            except OSError as exc:
                print(f'Atajo de Windows no disponible: {exc}; usando alternativa.')
        if not HOTKEY_OK:
            print("pynput no disponible - hotkeys deshabilitados")
            return

        try:
            # Hotkey combination: Ctrl+Alt+M
            hotkey_combo = {keyboard.Key.ctrl, keyboard.Key.alt, keyboard.KeyCode.from_char('m')}

            def on_hotkey():
                # pynput runs outside Tk: only enqueue, never touch widgets here.
                self.hotkey_events.put(True)

            # Start hotkey listener
            self.hotkey_listener = keyboard.GlobalHotKeys({
                '<ctrl>+<alt>+m': on_hotkey
            })
            self.hotkey_listener.start()
            print("Hotkey configurado: Ctrl+Alt+M para mostrar/ocultar")

        except Exception as e:
            print(f"Error configurando hotkeys: {e}")

    def _poll_hotkey(self):
        if self.hotkey_closed:
            return
        pressed = isinstance(self.hotkey_listener, WindowsHotkey) and self.hotkey_listener.pending()
        while not self.hotkey_events.empty():
            self.hotkey_events.get_nowait()
            pressed = True
        if pressed:
            self.toggle_window()
        self.root.after(50, self._poll_hotkey)

    def _keep_overlay_above(self):
        if not self.hotkey_closed and self.is_visible and self.root.state() != 'withdrawn':
            promote(self.root, activate=False)

    def toggle_window(self):
        """Toggle window visibility"""
        try:
            if self.is_visible and self.root.state() not in ('iconic', 'withdrawn') and is_foreground(self.root):
                self.root.attributes('-topmost', False)
                self.root.withdraw()  # Hide window
                self.is_visible = False
            else:
                self.root.deiconify()  # Show window
                self.is_visible = True
                self.root.update_idletasks()
                promote(self.root)
                self.root.after(100, self._keep_overlay_above)
                self.root.after(350, self._keep_overlay_above)
        except Exception as e:
            print(f"Error toggling window: {e}")

    def create_ui(self):
        # Main frame
        main = tk.Frame(self.root, bg='#1a1a1a', padx=15, pady=15)
        main.pack(fill=tk.BOTH, expand=True)

        # Title with status indicator
        title_frame = tk.Frame(main, bg='#1a1a1a')
        title_frame.pack(fill=tk.X, pady=(0,10))

        if self.server_photo is not None:
            tk.Label(title_frame, image=self.server_photo, bg='#1a1a1a').pack(side=tk.LEFT, padx=(0, 12))
        else:
            tk.Label(title_frame, text="π", font=('Arial', 32, 'bold'),
                     fg='white', bg='#5865F2', width=2).pack(side=tk.LEFT, padx=(0, 12))
        brand = tk.Frame(title_frame, bg='#1a1a1a')
        brand.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(brand, text="Reproductor de Discord de Los π",
                 font=('Arial', 17, 'bold'), fg='white', bg='#1a1a1a').pack(anchor=tk.W)
        tk.Label(brand, text="La música de los pibes · Ñeke Music",
                 font=('Arial', 10), fg='#a8adff', bg='#1a1a1a').pack(anchor=tk.W, pady=(3, 0))
        tk.Label(brand, text="Audio en tu PC listo" if self.audio_initialized else "Discord disponible · audio local requiere VLC",
                 font=('Arial', 9), fg='#aaa', bg='#1a1a1a').pack(anchor=tk.W, pady=(3, 0))

        # Hotkey info
        hotkey_text = "⌨️ Ctrl+Alt+M para mostrar/ocultar" if HOTKEY_OK else "⌨️ Hotkeys no disponibles"
        hotkey_color = '#4CAF50' if HOTKEY_OK else '#888'
        tk.Label(main, text=hotkey_text, font=('Arial', 8),
                fg=hotkey_color, bg='#1a1a1a').pack(pady=(0,10))

        # URL input section
        url_frame = tk.Frame(main, bg='#1a1a1a')
        url_frame.pack(fill=tk.X, pady=(0,15))

        tk.Label(url_frame, text="🔗 URL Directa de YouTube:", font=('Arial', 10),
                fg='white', bg='#1a1a1a').pack(anchor=tk.W)

        url_entry_frame = tk.Frame(url_frame, bg='#1a1a1a')
        url_entry_frame.pack(fill=tk.X, pady=(5,0))

        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(url_entry_frame, textvariable=self.url_var,
                                 font=('Arial', 10), bg='#333', fg='white',
                                 insertbackground='white', relief=tk.FLAT, bd=6)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,10))
        self.url_entry.bind('<Return>', self.play_from_url)

        self.url_btn = tk.Button(url_entry_frame, text="▶️ Reproducir URL", bg='#FF5722', fg='white',
                                command=self.play_from_url, font=('Arial', 9, 'bold'),
                                relief=tk.FLAT, cursor='hand2', padx=10)
        self.url_btn.pack(side=tk.RIGHT)

        # Separator
        separator = tk.Frame(main, height=2, bg='#333')
        separator.pack(fill=tk.X, pady=10)

        # Search section
        search_frame = tk.Frame(main, bg='#1a1a1a')
        search_frame.pack(fill=tk.X, pady=(0,15))

        tk.Label(search_frame, text="🔍 Buscar en YouTube:", font=('Arial', 10),
                fg='white', bg='#1a1a1a').pack(anchor=tk.W)

        entry_frame = tk.Frame(search_frame, bg='#1a1a1a')
        entry_frame.pack(fill=tk.X, pady=(5,0))

        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(entry_frame, textvariable=self.search_var,
                                   font=('Arial', 12), bg='#333', fg='white',
                                   insertbackground='white', relief=tk.FLAT, bd=8)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,10))
        self.search_entry.bind('<Return>', self.search)

        self.search_btn = tk.Button(entry_frame, text="🔍 Buscar", bg='#4CAF50', fg='white',
                                   command=self.search, font=('Arial', 10, 'bold'),
                                   relief=tk.FLAT, cursor='hand2', padx=15)
        self.search_btn.pack(side=tk.RIGHT)

        discord_frame = tk.Frame(main, bg='#1a1a1a')
        discord_frame.pack(fill=tk.X, pady=(0, 10))
        self.discord_play_btn = tk.Button(
            discord_frame, text="Reproducir en Discord", bg='#5865F2', fg='white',
            command=self.play_in_discord, relief=tk.FLAT, cursor='hand2', padx=10)
        self.discord_play_btn.pack(side=tk.LEFT)
        self.discord_config_btn = tk.Button(discord_frame, text="Configurar bot", command=self.configure_discord,
                                            relief=tk.FLAT)
        self.discord_config_btn.pack(side=tk.LEFT, padx=6)
        self.discord_disconnect_btn = tk.Button(
            discord_frame, text="Desconectar bot", command=self.disconnect_discord,
            relief=tk.FLAT)
        self.discord_disconnect_btn.pack(side=tk.RIGHT)
        self.discord_test_btn = tk.Button(
            discord_frame, text="Probar sonido", command=self.test_discord_sound, relief=tk.FLAT)
        self.discord_test_btn.pack(side=tk.LEFT, padx=4)
        room_frame = tk.Frame(main, bg='#1a1a1a')
        room_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(room_frame, text="Sala de voz:", bg='#1a1a1a', fg='white').pack(side=tk.LEFT, padx=(0, 8))
        self.discord_room_combo = ttk.Combobox(
            room_frame, textvariable=self.discord_room_name, state='readonly', width=46)
        self.discord_room_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.discord_room_combo.bind('<<ComboboxSelected>>', self.select_discord_room)
        self.discord_rooms_btn = tk.Button(room_frame, text="Actualizar salas", command=self.load_discord_rooms)
        self.discord_rooms_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.discord_status = tk.Label(main, text="Discord: sin conectar", bg='#1a1a1a',
                                       fg='#aaa', wraplength=570)
        self.discord_status.pack(fill=tk.X, pady=(0, 5))

        # Status label
        self.status = tk.Label(main, text="✅ Listo - Pega una URL o busca música",
                              font=('Arial', 10), fg='#4CAF50', bg='#1a1a1a')
        self.status.pack(pady=(0,10))

        # Results list with scrollbar
        list_frame = tk.Frame(main, bg='#1a1a1a')
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0,15))

        # Create treeview for better display
        columns = ('Title', 'Duration')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='tree headings', height=6)

        # Configure columns
        self.tree.heading('#0', text='#')
        self.tree.heading('Title', text='Título y Canal')
        self.tree.heading('Duration', text='Duración')

        self.tree.column('#0', width=30, minwidth=30)
        self.tree.column('Title', width=350, minwidth=200)
        self.tree.column('Duration', width=80, minwidth=60)

        # Scrollbar for treeview
        tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind('<Double-1>', self.play_selected)

        # Progress and seek bar section
        progress_frame = tk.Frame(main, bg='#1a1a1a')
        progress_frame.pack(fill=tk.X, pady=(0, 10))

        self.time_current_label = tk.Label(progress_frame, text="00:00",
                                           font=('Arial', 9, 'bold'), fg='#4CAF50', bg='#1a1a1a',
                                           width=5, anchor='e')
        self.time_current_label.pack(side=tk.LEFT, padx=(0, 6))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_scale = tk.Scale(progress_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                       variable=self.progress_var, showvalue=False,
                                       bg='#1a1a1a', fg='white', highlightthickness=0,
                                       troughcolor='#333', activebackground='#4CAF50',
                                       sliderrelief=tk.FLAT, bd=0, sliderlength=16,
                                       cursor='hand2')
        self.progress_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress_scale.bind('<ButtonPress-1>', self.on_progress_press)
        self.progress_scale.bind('<B1-Motion>', self.on_progress_motion)
        self.progress_scale.bind('<ButtonRelease-1>', self.on_progress_release)

        self.time_total_label = tk.Label(progress_frame, text="00:00",
                                         font=('Arial', 9), fg='#888', bg='#1a1a1a',
                                         width=5, anchor='w')
        self.time_total_label.pack(side=tk.RIGHT, padx=(6, 0))

        # Control buttons
        controls = tk.Frame(main, bg='#1a1a1a')
        controls.pack(fill=tk.X, pady=(0,10))

        # Playback controls
        playback_frame = tk.Frame(controls, bg='#1a1a1a')
        playback_frame.pack(side=tk.LEFT)

        self.rewind_btn = tk.Button(playback_frame, text="⏪", bg='#37474F', fg='white',
                                    font=('Arial', 14), command=lambda: self.rewind(10),
                                    relief=tk.FLAT, cursor='hand2', width=3, height=1)
        self.rewind_btn.pack(side=tk.LEFT, padx=(0,5))

        self.play_btn = tk.Button(playback_frame, text="▶️", bg='#4CAF50', fg='white',
                                 font=('Arial', 16), command=self.toggle_play,
                                 relief=tk.FLAT, cursor='hand2', width=3, height=1)
        self.play_btn.pack(side=tk.LEFT, padx=(0,5))

        tk.Button(playback_frame, text="⏹️", bg='#f44336', fg='white',
                 font=('Arial', 16), command=self.stop, relief=tk.FLAT,
                 cursor='hand2', width=3, height=1).pack(side=tk.LEFT, padx=(0,5))

        self.forward_btn = tk.Button(playback_frame, text="⏩", bg='#37474F', fg='white',
                                     font=('Arial', 14), command=lambda: self.forward(10),
                                     relief=tk.FLAT, cursor='hand2', width=3, height=1)
        self.forward_btn.pack(side=tk.LEFT, padx=(0,5))

        tk.Button(playback_frame, text="⏭️", bg='#2196F3', fg='white',
                 font=('Arial', 16), command=self.next_song, relief=tk.FLAT,
                 cursor='hand2', width=3, height=1).pack(side=tk.LEFT, padx=(0,5))

        # Additional controls
        tk.Button(playback_frame, text="🌐", bg='#FF9800', fg='white',
                 font=('Arial', 16), command=self.open_in_browser, relief=tk.FLAT,
                 cursor='hand2', width=3, height=1).pack(side=tk.LEFT, padx=(0,5))

        tk.Button(playback_frame, text="📋", bg='#9C27B0', fg='white',
                 font=('Arial', 16), command=self.paste_from_clipboard, relief=tk.FLAT,
                 cursor='hand2', width=3, height=1).pack(side=tk.LEFT)

        # Volume control
        volume_frame = tk.Frame(controls, bg='#1a1a1a')
        volume_frame.pack(side=tk.RIGHT)

        tk.Label(volume_frame, text="🔊", fg='white', bg='#1a1a1a',
                font=('Arial', 12)).pack(side=tk.LEFT, padx=(0,5))

        self.volume_var = tk.DoubleVar(value=70)
        self.volume_scale = tk.Scale(volume_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                   variable=self.volume_var, command=self.change_volume,
                                   bg='#1a1a1a', fg='white', highlightthickness=0,
                                   troughcolor='#333', activebackground='#4CAF50',
                                   length=120, width=20)
        self.volume_scale.pack(side=tk.LEFT)

        # Current song display
        self.current_label = tk.Label(main, text="🎵 Ninguna canción reproduciéndose",
                                     font=('Arial', 11, 'italic'), fg='#ccc', bg='#1a1a1a',
                                     wraplength=450, justify=tk.CENTER)
        self.current_label.pack(pady=(5,0))

        # Tips
        tips_text = "Los π · Elegí una canción y compartila en Discord"
        tk.Label(main, text=tips_text, font=('Arial', 8),
                fg='#888', bg='#1a1a1a').pack(pady=(5,0))

    def configure_discord(self):
        window = tk.Toplevel(self.root)
        window.title("Configurar bot de Discord")
        window.geometry("510x230")
        window.transient(self.root)
        token_var = tk.StringVar(value=self.discord_token.get())
        tk.Label(window, text="Token del BOT (guardado cifrado para tu usuario de Windows):").pack(
            anchor='w', padx=15, pady=(15, 4))
        tk.Entry(window, textvariable=token_var, show='*', width=65).pack(padx=15)
        tk.Label(window, text="Invitá el bot a tu servidor con permisos Ver canal, Conectar y Hablar.\n"
                 "Al guardar, se cargarán las salas disponibles en el selector principal.\n"
                 "Ingresá el token aquí; no lo compartas por chat.",
                 wraplength=480, justify=tk.LEFT).pack(padx=15, pady=12)
        tk.Button(window, text="Portal de Discord", command=lambda: webbrowser.open(
            'https://discord.com/developers/applications')).pack(side=tk.LEFT, padx=15)
        tk.Button(window, text="Guardar", command=lambda: self.save_discord_configuration(window, token_var.get())).pack(side=tk.RIGHT, padx=15)

    def read_discord_configuration(self):
        return load_settings()

    def persist_discord_configuration(self, token, channel):
        save_settings(token, channel)

    def new_discord_player(self):
        return DiscordMusicPlayer()

    def handle_discord_event(self, kind, message):
        return False

    def save_discord_configuration(self, window, token=None):
        if self.discord_busy:
            messagebox.showinfo("Discord", "Esperá a que termine la operación actual.", parent=window)
            return
        token = self.discord_token.get().strip() if token is None else token.strip()
        channel = self.discord_channel.get() if token == self.discord_token.get().strip() else ''
        try:
            self.persist_discord_configuration(token, channel)
        except SettingsError as exc:
            messagebox.showerror("Configuración de Discord", str(exc), parent=window)
            return
        self.discord_token.set(token)
        self.discord_channel.set(channel)
        self.discord_status.config(text="Discord: token y canal guardados", fg='#4CAF50')
        window.destroy()
        self.load_discord_rooms()

    def load_discord_rooms(self):
        if self.discord_busy:
            return
        token = self.discord_token.get().strip()
        if not token:
            self.configure_discord()
            return
        if token != self.discord_rooms_token:
            self.discord_rooms.clear()
            self.discord_channel.set('')
            self.discord_room_combo.config(values=())
            self.discord_room_name.set('Elegí una sala')
        self.discord_rooms_token = token
        if self.discord_player is None:
            self.discord_player = self.new_discord_player()
        self._set_discord_busy(True)
        self.discord_status.config(text="Discord: consultando salas disponibles...", fg='orange')
        self.discord_player.list_rooms(token)

    def _populate_discord_rooms(self, rooms):
        saved_channel = self.discord_channel.get()
        self.discord_rooms = {}
        for room in rooms:
            base = ' / '.join(part for part in (room['server'], room['category'], room['name']) if part)
            label, suffix = base, 2
            while label in self.discord_rooms:
                label = f'{base} ({suffix})'
                suffix += 1
            self.discord_rooms[label] = room['id']
        self.discord_room_combo.config(values=list(self.discord_rooms))
        selected = next((label for label, channel in self.discord_rooms.items() if channel == saved_channel), None)
        self.discord_room_name.set(selected or 'Elegí una sala')
        if selected is None:
            self.discord_channel.set('')
        self._set_discord_busy(False)
        self.discord_status.config(
            text=f'Discord: {len(rooms)} salas disponibles' if rooms else 'No hay salas accesibles. Revisá la invitación y los permisos del bot.',
            fg='#4CAF50' if rooms else 'orange')

    def select_discord_room(self, event=None):
        channel = self.discord_rooms.get(self.discord_room_name.get())
        if not channel:
            return
        self.discord_channel.set(channel)
        try:
            self.persist_discord_configuration(self.discord_token.get(), channel)
        except SettingsError as exc:
            messagebox.showerror("Configuración de Discord", str(exc))
            return
        self.discord_status.config(text="Sala elegida y guardada. Enviá una canción para reproducir ahí.", fg='#4CAF50')

    def _set_discord_busy(self, busy):
        self.discord_busy = busy
        state = 'disabled' if busy else 'normal'
        self.discord_play_btn.config(state=state)
        self.discord_disconnect_btn.config(state=state)
        self.discord_test_btn.config(state=state)
        self.discord_config_btn.config(state=state)
        self.discord_rooms_btn.config(state=state)
        self.discord_room_combo.config(state='disabled' if busy else 'readonly')

    def test_discord_sound(self):
        if self.discord_busy:
            return
        token = self.discord_token.get().strip()
        channel = self.discord_channel.get().strip()
        if not token:
            self.configure_discord()
            return
        if not channel:
            messagebox.showinfo("Discord", "Cargá las salas y elegí dónde reproducir.")
            return
        if not channel.isdecimal() or int(channel) <= 0:
            messagebox.showerror("Discord", "El ID del canal debe ser un número positivo.")
            return
        if self.discord_player is None:
            self.discord_player = self.new_discord_player()
        self._set_discord_busy(True)
        self.discord_status.config(text="Discord: preparando tono de prueba...", fg='orange')
        self.discord_player.test_sound(token, int(channel))

    def play_in_discord(self):
        if self.discord_busy:
            return
        token = self.discord_token.get().strip()
        channel = self.discord_channel.get().strip()
        if not token:
            self.configure_discord()
            return
        if not channel:
            messagebox.showinfo("Discord", "Cargá las salas y elegí dónde reproducir.")
            return
        if not channel.isdecimal() or int(channel) <= 0:
            messagebox.showerror("Discord", "El ID del canal debe ser un número positivo.")
            return
        # Explicit selection takes precedence, followed by pasted URL and current song.
        selection = self.tree.selection()
        url = None
        if selection:
            index = int(self.tree.item(selection[0])['text']) - 1
            if 0 <= index < len(self.results):
                url = self.results[index]['url']
        if not url:
            url = self.url_var.get().strip() or self.current_url
        video_id = self.extract_video_id(url) if url else None
        if not video_id:
            messagebox.showinfo("Discord", "Seleccioná una canción o pegá una URL de YouTube.")
            return
        if self.discord_player is None:
            self.discord_player = self.new_discord_player()
        self._set_discord_busy(True)
        self.discord_status.config(text="Discord: conectando y obteniendo audio...", fg='orange')
        self.discord_player.play(token, int(channel), f'https://www.youtube.com/watch?v={video_id}')

    def disconnect_discord(self):
        if self.discord_player is not None and not self.discord_busy:
            self._set_discord_busy(True)
            self.discord_status.config(text="Discord: desconectando...", fg='orange')
            self.discord_player.disconnect()

    def _poll_discord(self):
        if self.discord_player is not None:
            while True:
                try:
                    kind, message = self.discord_player.events.get_nowait()
                except queue.Empty:
                    break
                if self.handle_discord_event(kind, message):
                    continue
                if kind == 'channels':
                    self._populate_discord_rooms(message)
                    continue
                if kind in ('success', 'error'):
                    self._set_discord_busy(False)
                self.discord_status.config(text=message, fg='red' if 'error' in kind else '#4CAF50')
                if 'error' in kind:
                    messagebox.showerror("Discord", message)
        self.root.after(100, self._poll_discord)

    def is_youtube_url(self, url):
        """Accept supported YouTube video links, including Music and Shorts."""
        return self.extract_video_id(url) is not None

    def extract_video_id(self, url):
        """Parse the host and video ID instead of depending on query order."""
        url = url.strip()
        if '://' not in url:
            url = 'https://' + url
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return None
            host = (parsed.hostname or '').lower()
            parts = parsed.path.strip('/').split('/')
            video_id = None
            if host in ('youtu.be', 'www.youtu.be'):
                video_id = parts[0]
            elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
                if parsed.path.rstrip('/') == '/watch':
                    video_id = parse_qs(parsed.query).get('v', [None])[0]
                elif len(parts) == 2 and parts[0] in ('embed', 'shorts', 'live'):
                    video_id = parts[1]
            if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                return video_id
        except ValueError:
            pass
        return None

    def play_from_url(self, event=None):
        """Play directly from YouTube URL"""
        if not self.audio_initialized:
            messagebox.showerror("Error", "VLC no está disponible!")
            return

        if not YT_OK:
            self.show_install_message("yt-dlp")
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("URL Vacía", "Por favor ingresa una URL de YouTube")
            return

        if not self.is_youtube_url(url):
            messagebox.showerror("URL Inválida", "Por favor ingresa una URL válida de YouTube")
            return

        self.status.config(text="🔗 Procesando URL directa...", fg='orange')
        self.url_btn.config(state='disabled', text="Procesando...")

        url = f"https://www.youtube.com/watch?v={self.extract_video_id(url)}"
        # Process URL in separate thread
        threading.Thread(target=self._process_url_thread, args=(url,), daemon=True).start()

    def _process_url_thread(self, url):
        try:
            opts = {
                'quiet': True,
                'no_warnings': True,
                'format': 'bestaudio/best',
                'noplaylist': True,
            }

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if info:
                    result = {
                        'title': info.get('title', 'Título Desconocido'),
                        'uploader': info.get('uploader', 'Desconocido'),
                        'duration': info.get('duration', 0),
                        'url': url,
                        'id': info.get('id', ''),
                        'stream_url': None
                    }

                    # Add to results for next/previous functionality
                    self.results = [result]
                    self.current_index = 0

                    # Stream the audio
                    self.root.after(0, lambda: self.stream_audio(result))
                else:
                    self.root.after(0, lambda: self._url_error("No se pudo obtener información del video"))

        except Exception as e:
            error_msg = str(e)
            self.root.after(0, lambda: self._url_error(error_msg))


    def _url_error(self, error):
        self.status.config(text="❌ Error procesando URL", fg='red')
        self.url_btn.config(state='normal', text="▶️ Reproducir URL")
        messagebox.showerror("Error de URL", f"Error procesando URL: {error}")

    def paste_from_clipboard(self):
        """Paste URL from clipboard"""
        try:
            clipboard_content = self.root.clipboard_get()
            if self.is_youtube_url(clipboard_content):
                self.url_var.set(clipboard_content)
                self.status.config(text="📋 URL pegada desde portapapeles", fg='green')
            else:
                messagebox.showwarning("Portapapeles", "El portapapeles no contiene una URL válida de YouTube")
        except tk.TclError:
            messagebox.showwarning("Portapapeles", "El portapapeles está vacío")

    def show_install_message(self, package):
        """Show message if package is missing"""
        self.status.config(text=f"❌ Instala: pip install {package}", fg='red')
        messagebox.showerror("Dependencia Faltante",
                           f"{package} es requerido.\n\nInstala con: pip install {package}")

    def show_vlc_install_message(self):
        """Show VLC installation message"""
        self.status.config(text="❌ VLC Python requerido para streaming", fg='red')
        msg = """Para reproducir música en streaming necesitas instalar:

1. VLC Media Player: https://www.videolan.org/vlc/
2. Python VLC bindings: pip install python-vlc

¿Quieres abrir la página de descarga de VLC?"""

        if messagebox.askyesno("VLC Requerido", msg):
            webbrowser.open("https://www.videolan.org/vlc/")

    def search(self, event=None):
        if not YT_OK:
            messagebox.showerror("Error", "yt-dlp no está instalado!")
            return

        query = self.search_var.get().strip()
        if not query:
            return

        self.status.config(text="🔍 Buscando en YouTube...", fg='orange')
        self.search_btn.config(state='disabled', text="Buscando...")

        # Clear previous results
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Search in separate thread
        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()

    def _search_thread(self, query):
        try:
            opts = {
                'quiet': True,
                'no_warnings': True,
                'default_search': 'ytsearch5:',
                'extract_flat': 'in_playlist',
                'skip_download': True,
                'format': 'bestaudio',
                'noplaylist': True,
                'ignoreerrors': True,
                'extract_info': True
            }

            with yt_dlp.YoutubeDL(opts) as ydl:
                search_query = f"ytsearch5:{query}"
                info = ydl.extract_info(search_query, download=False)

                self.results = []
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if entry and entry.get('title'):
                            video_id = entry.get('id', '')
                            webpage_url = entry.get('webpage_url', '')

                            # If webpage_url is empty, construct it from video ID
                            if not webpage_url and video_id:
                                webpage_url = f"https://www.youtube.com/watch?v={video_id}"

                            self.results.append({
                                'title': entry.get('title', 'Título Desconocido'),
                                'uploader': entry.get('uploader', 'Desconocido'),
                                'duration': entry.get('duration', 0),
                                'url': webpage_url,
                                'id': video_id,
                                'stream_url': None
                            })

                    print(f"Encontrados {len(self.results)} resultados")
                else:
                    print("No se encontraron resultados")

                self.root.after(0, self._update_results)

        except Exception as e:
            print(f"Error en búsqueda: {e}")
            error_msg = str(e)
            self.root.after(0, lambda: self._search_error(error_msg))


    def _update_results(self):
        # Clear tree
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Add results
        for i, result in enumerate(self.results):
            duration = self._format_duration(result.get('duration', 0))
            title_info = f"{result['title']} - {result['uploader']}"

            self.tree.insert('', 'end', text=str(i+1),
                           values=(title_info, duration))

        self.status.config(text=f"✅ Encontradas {len(self.results)} canciones", fg='green')
        self.search_btn.config(state='normal', text="🔍 Buscar")

    def _format_duration(self, seconds):
        """Format duration in MM:SS format, handling None and invalid values"""
        try:
            # Handle None or invalid values
            if not seconds or seconds == 0:
                return "Desconocido"

            # Convert to int to handle float values
            seconds = int(seconds)
            minutes = seconds // 60
            seconds = seconds % 60

            return f"{minutes}:{seconds:02d}"
        except (ValueError, TypeError):
            # Fallback for any conversion errors
            return "Desconocido"

    def _format_time(self, seconds):
        """Format seconds into MM:SS or HH:MM:SS"""
        try:
            seconds = max(0, int(seconds))
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            if hours > 0:
                return f"{hours}:{minutes:02d}:{secs:02d}"
            else:
                return f"{minutes:02d}:{secs:02d}"
        except (ValueError, TypeError):
            return "00:00"

    def _calculate_scale_time(self, event):
        """Calculate target seconds from mouse event on progress_scale"""
        width = self.progress_scale.winfo_width()
        if width > 16 and self.current_total_duration > 0:
            margin = 8
            usable = width - (2 * margin)
            ratio = max(0.0, min(1.0, (event.x - margin) / usable))
            return ratio * self.current_total_duration
        elif self.current_total_duration > 0:
            return self.progress_var.get()
        return 0

    def on_progress_press(self, event):
        """Handle mouse press on progress bar"""
        if not self.audio_initialized or not self.player:
            return
        self.is_dragging_progress = True
        target_val = self._calculate_scale_time(event)
        self.progress_var.set(target_val)
        self.time_current_label.config(text=self._format_time(target_val))

    def on_progress_motion(self, event):
        """Handle mouse drag on progress bar"""
        if not self.audio_initialized or not self.player:
            return
        target_val = self._calculate_scale_time(event)
        self.progress_var.set(target_val)
        self.time_current_label.config(text=self._format_time(target_val))

    def on_progress_release(self, event):
        """Handle mouse release on progress bar"""
        if not self.audio_initialized or not self.player:
            return
        target_val = self._calculate_scale_time(event)
        self.seek_to(target_val)
        self.is_dragging_progress = False

    def seek_to(self, seconds):
        """Seek to a specific time in seconds"""
        if not self.audio_initialized or not self.player:
            return
        try:
            max_dur = float(self.current_total_duration) if self.current_total_duration > 0 else float(seconds)
            target_sec = max(0.0, min(float(seconds), max_dur))
            target_ms = int(target_sec * 1000)
            self.player.set_time(target_ms)
            self.progress_var.set(target_sec)
            self.time_current_label.config(text=self._format_time(target_sec))
        except Exception as e:
            print(f"Error seeking: {e}")

    def forward(self, seconds=10):
        """Forward current playback by seconds"""
        if not self.audio_initialized or not self.player:
            return
        current_ms = self.player.get_time()
        current_sec = (current_ms // 1000) if current_ms > 0 else self.progress_var.get()
        self.seek_to(current_sec + seconds)

    def rewind(self, seconds=10):
        """Rewind current playback by seconds"""
        if not self.audio_initialized or not self.player:
            return
        current_ms = self.player.get_time()
        current_sec = (current_ms // 1000) if current_ms > 0 else self.progress_var.get()
        self.seek_to(max(0, current_sec - seconds))


    def _search_error(self, error):
        self.status.config(text="❌ Error en la búsqueda", fg='red')
        self.search_btn.config(state='normal', text="🔍 Buscar")
        messagebox.showerror("Error de Búsqueda", f"Falló la búsqueda: {error}")

    def play_selected(self, event=None):
        if not self.audio_initialized:
            messagebox.showerror("Error", "VLC no está disponible para streaming!")
            return

        selection = self.tree.selection()
        if not selection or not self.results:
            return

        # Get selected index
        item = self.tree.item(selection[0])
        index = int(item['text']) - 1

        if 0 <= index < len(self.results):
            self.current_index = index
            self.stream_audio(self.results[index])

    def stream_audio(self, result):
        if not self.audio_initialized:
            messagebox.showerror("Error", "Sistema de audio no disponible!")
            return

        self.status.config(text="🌐 Obteniendo URL de streaming...", fg='orange')
        self.play_btn.config(state='disabled')

        # Get stream URL in separate thread
        threading.Thread(target=self._get_stream_thread, args=(result,), daemon=True).start()

    def _get_stream_thread(self, result):
        try:
            if not result.get('url'):
                self.root.after(0, lambda: self._stream_error("URL de video vacía"))
                return

            video_id = result['id']
            if video_id in self.url_cache:
                stream_url = self.url_cache[video_id]
                self.root.after(0, lambda: self._play_stream(stream_url, result))
                return

            # Get direct stream URL
            opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'noplaylist': True
            }

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(result['url'], download=False)

                # Use the format selected by yt-dlp, not the first format listed.
                stream_url = info.get('url') if info else None

                if stream_url:
                    self.url_cache[video_id] = stream_url
                    self.root.after(0, lambda: self._play_stream(stream_url, result))
                else:
                    self.root.after(0, lambda: self._stream_error("No se encontró URL de streaming"))

        except Exception as e:
            error_msg = str(e)
            self.root.after(0, lambda: self._stream_error(error_msg))


    def _play_stream(self, stream_url, result):
        try:
            # Stop current playback
            if self.player:
                self.player.stop()

            # Create media and play
            media = self.vlc_instance.media_new(stream_url)
            self.player.set_media(media)
            self.player.play()

            self.playing = True
            self.paused = False
            self.current_url = result['url']

            # Setup duration & progress bar
            raw_duration = result.get('duration', 0) or 0
            self.current_total_duration = int(raw_duration) if raw_duration else 0
            self.progress_scale.config(to=max(1, self.current_total_duration) if self.current_total_duration > 0 else 100)
            self.progress_var.set(0)
            self.time_current_label.config(text="00:00")
            self.time_total_label.config(text=self._format_time(self.current_total_duration) if self.current_total_duration > 0 else "--:--")

            self.play_btn.config(text="⏸️", state='normal')
            self.url_btn.config(state='normal', text="▶️ Reproducir URL")
            self.status.config(text="🎵 Reproduciendo en streaming", fg='green')

            # Show current song info
            self.current_label.config(text=f"🎵 {result['title']} [STREAMING]")

            # Start monitoring
            self._monitor_playback()

        except Exception as e:
            self._stream_error(f"Error de reproducción: {e}")

    def _stream_error(self, error):
        self.status.config(text="❌ Error de streaming", fg='red')
        self.play_btn.config(state='normal')
        self.url_btn.config(state='normal', text="▶️ Reproducir URL")
        self.progress_var.set(0)
        self.time_current_label.config(text="00:00")
        messagebox.showerror("Error de Streaming", f"No se puede reproducir:\n{error}")

    def _monitor_playback(self):
        """Monitor playback status and update progress bar"""
        if self.playing and self.player:
            try:
                state = self.player.get_state()
                if state == vlc.State.Ended:
                    # Song finished, play next
                    self.next_song()
                    return
                elif state == vlc.State.Error:
                    self._stream_error("Error durante la reproducción")
                    return

                # Check dynamic duration from VLC if not set or updated
                length_ms = self.player.get_length()
                if length_ms > 0:
                    vlc_duration = length_ms // 1000
                    if vlc_duration != self.current_total_duration and vlc_duration > 0:
                        self.current_total_duration = vlc_duration
                        self.progress_scale.config(to=self.current_total_duration)
                        self.time_total_label.config(text=self._format_time(self.current_total_duration))

                # Update progress slider and current time
                current_ms = self.player.get_time()
                if current_ms >= 0 and not self.is_dragging_progress:
                    current_sec = current_ms // 1000
                    self.progress_var.set(current_sec)
                    self.time_current_label.config(text=self._format_time(current_sec))
            except Exception as e:
                print(f"Error en monitor: {e}")

            if self.playing and not self.paused:
                # Continue monitoring every 500ms
                self.root.after(500, self._monitor_playback)

    def toggle_play(self):
        if not self.player or not self.audio_initialized:
            return

        try:
            if self.playing and not self.paused:
                self.player.pause()
                self.paused = True
                self.play_btn.config(text="▶️")
                self.status.config(text="⏸️ Pausado", fg='yellow')
            elif self.paused:
                self.player.pause()  # VLC pause toggles
                self.paused = False
                self.play_btn.config(text="⏸️")
                self.status.config(text="🎵 Reproduciendo", fg='green')
                self._monitor_playback()
        except Exception as e:
            self._stream_error(f"Error de control: {e}")

    def stop(self):
        if not self.audio_initialized or not self.player:
            return

        try:
            self.player.stop()
            self.playing = False
            self.paused = False
            self.play_btn.config(text="▶️")
            self.status.config(text="⏹️ Detenido", fg='gray')
            self.current_label.config(text="🎵 Ninguna canción reproduciéndose")
            self.current_total_duration = 0
            self.progress_var.set(0)
            self.progress_scale.config(to=100)
            self.time_current_label.config(text="00:00")
            self.time_total_label.config(text="00:00")
        except Exception as e:
            print(f"Error al detener: {e}")

    def next_song(self):
        """Play next song in the list"""
        if not self.results:
            return

        if self.current_index >= 0:
            next_index = (self.current_index + 1) % len(self.results)
        else:
            next_index = 0

        self.current_index = next_index
        self.stream_audio(self.results[next_index])

        # Highlight current song in tree
        for item in self.tree.get_children():
            if int(self.tree.item(item)['text']) - 1 == next_index:
                self.tree.selection_set(item)
                self.tree.focus(item)
                break

    def open_in_browser(self):
        """Open current song in browser"""
        if self.current_url:
            webbrowser.open(self.current_url)
        else:
            messagebox.showinfo("Info", "No hay canción seleccionada")

    def change_volume(self, value):
        """Change volume"""
        if self.audio_initialized and self.player:
            volume = int(float(value))
            self.player.audio_set_volume(volume)

    def on_closing(self):
        """Handle window closing"""
        if messagebox.askquestion("Cerrar", "¿Quieres cerrar el reproductor?\n\n(Puedes usar Ctrl+Alt+M para ocultarlo)") == 'yes':
            self.cleanup()
            self.root.destroy()
        else:
            # Just hide the window instead of closing
            self.toggle_window()

    def cleanup(self):
        """Cleanup resources"""
        self.hotkey_closed = True
        if self.discord_player is not None:
            self.discord_player.close()
            self.discord_player.thread.join(timeout=5)
        if self.player:
            self.player.stop()
        if self.hotkey_listener:
            self.hotkey_listener.stop()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.cleanup()

if __name__ == "__main__":
    app = StreamingMusicPlayerEnhanced()
    app.run()
