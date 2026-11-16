"""Per-user Discord settings; bot credentials are encrypted by Windows DPAPI."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

DEFAULT_CHANNEL = ''


class SettingsError(Exception):
    pass


def settings_path():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'ReproductorLosPiPublic' / 'discord.json'


def _crypt(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    protect = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    protect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    protect.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    # Current-user protection, with no Windows prompt or machine-wide key.
    if not protect(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise SettingsError('Windows no pudo proteger o recuperar el token guardado.')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def load_settings(path=None):
    path = Path(path) if path is not None else settings_path()
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('version') != 1:
            raise ValueError('version')
        channel = payload['channel_id']
        if not isinstance(channel, str) or (channel and (not channel.isdecimal() or int(channel) <= 0)):
            raise ValueError('channel')
        encrypted = base64.b64decode(payload['token_dpapi'], validate=True)
        token = _crypt(encrypted, decrypt=True).decode('utf-8')
        if not token.strip():
            raise ValueError('token')
        return {'token': token, 'channel_id': channel}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SettingsError) as exc:
        raise SettingsError('No se pudo recuperar la configuración. Volvé a ingresar el token y guardalo.') from exc


def save_settings(token, channel, path=None):
    token, channel = token.strip(), channel.strip()
    if not token or (channel and (not channel.isdecimal() or int(channel) <= 0)):
        raise SettingsError('Ingresá el token del bot y elegí una sala válida.')
    path = Path(path) if path is not None else settings_path()
    temporary = None
    try:
        payload = {'version': 1, 'channel_id': channel,
                   'token_dpapi': base64.b64encode(_crypt(token.encode('utf-8'))).decode('ascii')}
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='discord-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            json.dump(payload, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise SettingsError('No se pudo guardar la configuración en este equipo.') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
