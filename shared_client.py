"""A desktop remote control: it never connects to the Discord Gateway."""
import json
import queue
import threading
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared_settings import validate_server_url


class RemoteMusicPlayer:
    def __init__(self, url, key, name):
        self.url = validate_server_url(url)
        self.key, self.name = key, name
        self.events = queue.Queue()
        self.jobs = queue.Queue()
        self.closed = threading.Event()
        self.next_title = 'Canción de YouTube'
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _request(self, path, payload=None):
        headers = {'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json'}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers.update({'Content-Type': 'application/json', 'X-Request-ID': str(uuid.uuid4())})
        request = Request(self.url + path, data=body, headers=headers)
        for attempt in range(2):
            try:
                with urlopen(request, timeout=75) as response:
                    result = json.loads(response.read(1024 * 1024))
                    if not isinstance(result, dict):
                        raise RuntimeError('El servidor devolvió una respuesta inválida.')
                    return result
            except HTTPError as exc:
                try:
                    error = json.loads(exc.read(16384)).get('error', 'El servidor rechazó el pedido.')
                except (ValueError, AttributeError):
                    error = 'El servidor rechazó el pedido.'
                raise RuntimeError(str(error).replace(self.key, '[clave oculta]')) from None
            except (URLError, TimeoutError, OSError):
                if attempt:
                    raise RuntimeError('No se pudo contactar al servidor compartido. Revisá la conexión.') from None

    def _run(self):
        while not self.closed.is_set():
            try:
                kind, data = self.jobs.get(timeout=3)
            except queue.Empty:
                try:
                    self.events.put(('shared_state', self._request('/v1/state')))
                except Exception:
                    self.events.put(('shared_offline', 'Sin conexión con el servidor compartido.'))
                continue
            if kind == 'close':
                return
            try:
                if kind == 'rooms':
                    result = self._request('/v1/rooms')
                    self.events.put(('channels', result['rooms']))
                else:
                    result = self._request('/v1/commands/' + kind, data)
                    self.events.put(('shared_state', result['state']))
                    self.events.put(('success', result['message']))
            except Exception as exc:
                self.events.put(('error', str(exc).replace(self.key, '[clave oculta]')))

    def list_rooms(self, unused_key):
        self.jobs.put(('rooms', None))

    def play(self, unused_key, channel_id, url):
        self.jobs.put(('play', {'channel_id': str(channel_id), 'url': url,
                                'title': self.next_title, 'name': self.name}))

    def test_sound(self, unused_key, channel_id):
        self.jobs.put(('test', {'channel_id': str(channel_id), 'name': self.name}))

    def disconnect(self):
        self.jobs.put(('stop', {}))

    def skip(self):
        self.jobs.put(('skip', {}))

    def pause(self, paused):
        self.jobs.put(('resume' if paused else 'pause', {}))

    def close(self):
        # Closing one friend's app must not disconnect the bot for everyone.
        self.closed.set()
        self.jobs.put(('close', None))
