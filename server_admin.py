"""Owner-only desktop administration, independent of playback credentials."""
import json
import queue
import secrets
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import webbrowser

from shared_settings import load_connection, save_connection, settings_path, validate_server_url


def admin_settings_path():
    return settings_path().with_name('administration.json')


def admin_request(url, key, payload=None):
    endpoint = validate_server_url(url)
    request = Request(endpoint + '/v1/admin/guilds',
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=75) as response:
            return json.loads(response.read(1024 * 1024))
    except HTTPError as exc:
        try:
            error = json.loads(exc.read(16384)).get('error', 'El servidor rechazó el pedido.')
        except (ValueError, AttributeError):
            error = 'El servidor rechazó el pedido.'
        if exc.code == 401:
            error = 'Se requiere tu clave de administración; la clave de los pibes no permite dar altas.'
        raise RuntimeError(error) from None
    except (URLError, OSError, TimeoutError):
        raise RuntimeError('No se pudo contactar al servidor. Podés reintentar sin generar otra clave.') from None


class ServerAdminWindow:
    def __init__(self, root, default_url):
        self.window = tk.Toplevel(root)
        self.window.title('Administrar servidores de Discord')
        self.window.geometry('660x450')
        self.window.transient(root)
        try:
            saved = load_connection(admin_settings_path())
        except Exception:
            saved = {}
        self.url = tk.StringVar(value=saved.get('url', default_url))
        self.key = tk.StringVar(value=saved.get('key', ''))
        self.output = tk.StringVar()
        self.status = tk.StringVar(value='Conectate como administrador para ver los servidores del bot.')
        self.results = queue.Queue()
        self.guilds, self.invite_url = [], ''
        self.pending = None
        for label, value in [('Dirección del servidor:', self.url), ('Tu clave de administración (no compartir):', self.key)]:
            tk.Label(self.window, text=label).pack(anchor='w', padx=15, pady=(10, 2))
            tk.Entry(self.window, textvariable=value, show='*' if value is self.key else '', width=85).pack(padx=15)
        row = tk.Frame(self.window)
        row.pack(pady=10)
        self.refresh_btn = tk.Button(row, text='Conectar / actualizar servidores', command=self.refresh)
        self.refresh_btn.pack(side=tk.LEFT, padx=5)
        self.invite_btn = tk.Button(row, text='Invitar al bot', command=self.invite, state='disabled')
        self.invite_btn.pack(side=tk.LEFT)
        tk.Label(self.window, text='Invitá al bot, actualizá la lista y elegí el nuevo servidor:').pack()
        self.selection = ttk.Combobox(self.window, state='readonly', width=78)
        self.selection.pack(padx=15, pady=8)
        self.create_btn = tk.Button(self.window, text='Habilitar servidor y generar su clave', command=self.create, state='disabled')
        self.create_btn.pack()
        tk.Label(self.window, textvariable=self.status, wraplength=610).pack(padx=15, pady=10)
        tk.Entry(self.window, textvariable=self.output, state='readonly', width=80).pack(padx=15)
        self.copy_btn = tk.Button(self.window, text='Copiar conexión para los pibes', command=self.copy, state='disabled')
        self.copy_btn.pack(pady=8)
        tk.Label(self.window, text='Cada grupo recibe solo su clave. Los servidores habilitados conservan su acceso.', wraplength=610).pack()
        self.window.after(100, self.poll)

    def run(self, operation, payload=None):
        try:
            endpoint = validate_server_url(self.url.get())
            key = self.key.get().strip()
            save_connection(endpoint, key, 'Administrador', path=admin_settings_path())
        except Exception as exc:
            messagebox.showerror('Administración', str(exc), parent=self.window)
            return
        self.endpoint = endpoint
        self.refresh_btn.config(state='disabled')
        self.create_btn.config(state='disabled')
        self.status.set('Conectando…')
        def worker():
            try:
                self.results.put((operation, admin_request(endpoint, key, payload)))
            except Exception as exc:
                self.results.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        self.run('list')

    def invite(self):
        if self.invite_url:
            webbrowser.open(self.invite_url)

    def create(self):
        index = self.selection.current()
        if index < 0:
            return
        guild = self.guilds[index]
        if guild['enabled'] and not (self.pending and self.pending['guild_id'] == guild['id']):
            self.status.set('Este servidor ya está habilitado. Se conserva su clave actual.')
            return
        if not self.pending or self.pending['guild_id'] != guild['id']:
            self.pending = {'guild_id': guild['id'], 'key': secrets.token_urlsafe(32)}
        self.run('create', dict(self.pending))

    def copy(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(f'Servidor: {self.share_endpoint}\nClave: {self.output.get()}')
        self.status.set('Conexión copiada. Compartila por privado con ese grupo.')

    def poll(self):
        if not self.window.winfo_exists():
            return
        try:
            kind, result = self.results.get_nowait()
        except queue.Empty:
            self.window.after(100, self.poll)
            return
        self.refresh_btn.config(state='normal')
        self.create_btn.config(state='normal' if self.guilds else 'disabled')
        if kind == 'list':
            self.guilds = result['guilds']
            self.invite_url = result['invite_url']
            self.selection['values'] = [g['name'] + (' · habilitado' if g['enabled'] else ' · nuevo') for g in self.guilds]
            if self.guilds:
                self.selection.current(0)
                self.create_btn.config(state='normal')
            self.invite_btn.config(state='normal')
            self.status.set('Elegí un servidor nuevo para habilitarlo, o invitá al bot a otro Discord.')
        elif kind == 'create':
            self.share_endpoint = self.endpoint
            self.output.set(self.pending['key'])
            self.copy_btn.config(state='normal')
            for guild in self.guilds:
                if guild['id'] == self.pending['guild_id']:
                    guild['enabled'] = True
            self.selection['values'] = [g['name'] + (' · habilitado' if g['enabled'] else ' · nuevo') for g in self.guilds]
            self.status.set('Servidor habilitado. Copiá su conexión antes de cerrar esta ventana.')
            self.pending = None
        else:
            self.status.set(result)
        self.window.after(100, self.poll)
