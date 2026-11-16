"""Desktop settings for shared control; never read the local bot's credentials."""
import base64
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from discord_settings import SettingsError, _crypt


def validate_server_url(value):
    value = value.strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
        if (parsed.scheme != 'https' and not (local and parsed.scheme == 'http')) or not parsed.hostname:
            raise ValueError()
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError()
        parsed.port
    except ValueError:
        raise SettingsError('Ingresá la dirección HTTPS del servidor, sin rutas ni credenciales en la URL.')
    return value


def settings_path():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'LosPiCompartido' / 'connection.json'


def load_connection(path=None):
    path = Path(path) if path is not None else settings_path()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['version'] != 1:
            raise ValueError()
        data['url'] = validate_server_url(data['url'])
        key = _crypt(base64.b64decode(data['key_dpapi'], validate=True), decrypt=True).decode('utf-8')
        if len(key) < 32 or not isinstance(data['channel_id'], str):
            raise ValueError()
        return {'url': data['url'], 'key': key, 'name': str(data.get('name') or 'Amigo')[:48],
                'channel_id': data['channel_id']}
    except FileNotFoundError:
        return {}
    except (ValueError, TypeError, KeyError, AttributeError, OSError, SettingsError) as exc:
        raise SettingsError('No se pudo recuperar la conexión compartida. Configurala nuevamente.') from exc


def save_connection(url, key, name, channel='', path=None):
    url = validate_server_url(url)
    key = key.strip()
    if len(key) < 32:
        raise SettingsError('La clave de acceso debe tener al menos 32 caracteres.')
    if channel and (not channel.isdecimal() or int(channel) <= 0):
        raise SettingsError('Elegí una sala válida.')
    path = Path(path) if path is not None else settings_path()
    temporary = None
    try:
        data = {'version': 1, 'url': url, 'name': name.strip()[:48] or 'Amigo', 'channel_id': channel,
                'key_dpapi': base64.b64encode(_crypt(key.encode())).decode('ascii')}
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise SettingsError('No se pudo guardar la conexión compartida.') from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
