"""One online Discord connection, a shared queue, and authenticated desktop control."""
import asyncio
from collections import OrderedDict, deque
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from aiohttp import web
from discord_player import DiscordMusicPlayer

log = logging.getLogger('lospi.shared')


class ControlError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class SharedController:
    def __init__(self, player, token, loop, allowed_guilds=(), owns_player=True):
        self.player, self.token, self.loop = player, token, loop
        self.owns_player = owns_player
        self.allowed_guilds = {str(value) for value in allowed_guilds}
        self.lock = asyncio.Lock()
        self.pending = deque()
        self.current = None
        self.current_generation = None
        self.paused = False
        self.revision = 0
        self.last_error = ''
        self.requests = OrderedDict()
        self.stopping = False
        self.tasks = set()
        self.player.on_audio_finished = self._audio_finished
        self.media_dir = tempfile.TemporaryDirectory(prefix='lospi-audio-')
        self.uploads = {}
        self.uploading = set()
        self.idle_since = time.monotonic()
        self.idle_disconnected = False

    async def check_idle(self, now=None):
        """Leave voice after five minutes with no current track or queue."""
        async with self.lock:
            now = time.monotonic() if now is None else now
            if self.current or self.pending:
                self.idle_since = None
                self.idle_disconnected = False
                return
            if self.idle_since is None:
                self.idle_since = now
            if not self.idle_disconnected and now >= self.idle_since + 300:
                await self._stop_audio(leave=True)
                self.idle_disconnected = True
                self.revision += 1
                log.info('Salida automática de voz tras 5 minutos sin canciones.')

    async def idle_watchdog(self):
        while not self.stopping:
            await asyncio.sleep(1)
            try:
                await self.check_idle()
            except Exception:
                log.warning('No se pudo salir de voz por inactividad; se reintentará.')

    def discard_media(self, track):
        if track and track.get('upload_id'):
            item = self.uploads.pop(track['upload_id'], None)
            if item:
                item['path'].unlink(missing_ok=True)

    async def upload(self, request):
        upload_id = request.match_info['upload_id']
        try:
            if str(uuid.UUID(upload_id)) != upload_id:
                raise ValueError()
        except ValueError:
            raise ControlError('Identificador de audio inválido.')
        for ident, item in list(self.uploads.items()):
            if not item['claimed'] and time.monotonic() - item['created'] > 1800:
                self.discard_media({'upload_id': ident})
        if upload_id in self.uploads:
            return web.json_response({'upload_id': upload_id})
        maximum = 64 * 1024 * 1024
        if upload_id in self.uploading or len(self.uploading) >= 2:
            raise ControlError('Hay otras transferencias en curso. Reintentá en unos segundos.', 409)
        if sum(v['size'] for v in self.uploads.values()) + (len(self.uploading) + 1) * maximum > 256 * 1024 * 1024:
            raise ControlError('El espacio temporal está lleno. Esperá a que avance la cola.', 409)
        if not request.content_length or request.content_length > maximum:
            raise ControlError('El audio debe ocupar entre 1 byte y 64 MB.', 413)
        self.uploading.add(upload_id)
        path = Path(self.media_dir.name) / upload_id
        size = 0
        try:
            async with asyncio.timeout(180):
                with path.open('wb') as output:
                    async for chunk in request.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > maximum:
                            raise ControlError('El audio supera 64 MB.', 413)
                        output.write(chunk)
            if size != request.content_length:
                raise ControlError('La transferencia quedó incompleta.')
            self.uploads[upload_id] = {'path': path, 'size': size, 'created': time.monotonic(), 'claimed': False}
            return web.json_response({'upload_id': upload_id})
        finally:
            self.uploading.discard(upload_id)
            if upload_id not in self.uploads:
                path.unlink(missing_ok=True)

    async def _call(self, coroutine):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coroutine, self.player.loop))

    def _audio_finished(self, generation, failed, packets):
        def schedule():
            if self.stopping:
                return
            task = asyncio.create_task(self._finish(generation, failed or packets == 0))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        self.loop.call_soon_threadsafe(schedule)

    def snapshot(self):
        return {'revision': self.revision, 'current': dict(self.current) if self.current else None,
                'queue': [dict(item) for item in self.pending], 'paused': self.paused,
                'last_error': self.last_error}

    async def rooms(self):
        # Calls that can log in or reconnect share the same lock as playback.
        async with self.lock:
            return await self._rooms_locked()

    async def _rooms_locked(self):
        rooms = await self._call(self.player._list_rooms(self.token))
        if self.allowed_guilds:
            async def allowed():
                return {str(c.id) for g in self.player.client.guilds
                        if str(g.id) in self.allowed_guilds for c in g.voice_channels}
            ids = await self._call(allowed())
            rooms = [room for room in rooms if room['id'] in ids]
        return rooms

    async def _stop_audio(self, leave=False):
        async def stop():
            self.player.playback_generation += 1
            if self.player.client:
                for voice in list(self.player.client.voice_clients):
                    voice.stop()
                    if leave:
                        await voice.disconnect(force=True)
        await self._call(stop())
        self.discard_media(self.current)
        self.current = None
        self.current_generation = None
        self.paused = False

    async def _start_next(self):
        while self.pending:
            track = self.pending.popleft()
            self.current = track
            self.idle_since = None
            self.idle_disconnected = False
            self.paused = False
            self.revision += 1
            try:
                if track.get('test'):
                    await self._call(self.player._test_sound(self.token, int(track['channel_id'])))
                elif track.get('upload_id'):
                    await self._call(self.player._play_file(self.token, int(track['channel_id']), self.uploads[track['upload_id']]['path']))
                else:
                    await self._call(self.player._play(self.token, int(track['channel_id']), track['url']))
                self.current_generation = self.player.playback_generation
                return
            except Exception:
                log.warning('No se pudo iniciar una pista; se continúa con la cola.')
                self.last_error = 'Una pista no pudo reproducirse. Probá otra canción o el tono de prueba.'
                self.discard_media(track)
                self.current = None
                self.current_generation = None
        if self.idle_since is None:
            self.idle_since = time.monotonic()
        self.revision += 1

    async def _finish(self, generation, failed):
        async with self.lock:
            if generation != self.current_generation:
                return
            if failed:
                self.last_error = 'Falló el envío de audio de una pista.'
            self.discard_media(self.current)
            self.current = None
            self.current_generation = None
            self.paused = False
            await self._start_next()

    async def command(self, action, payload, request_id):
        if not isinstance(payload, dict):
            raise ControlError('El cuerpo debe ser un objeto JSON.')
        try:
            uuid.UUID(request_id)
        except (ValueError, TypeError, AttributeError):
            raise ControlError('Falta un identificador válido para el pedido.')
        digest = hashlib.sha256(json.dumps([action, payload], sort_keys=True).encode()).hexdigest()
        async with self.lock:
            now = time.monotonic()
            while self.requests and (len(self.requests) > 2048 or next(iter(self.requests.values()))[0] < now - 3600):
                self.requests.popitem(last=False)
            previous = self.requests.get(request_id)
            if previous:
                if previous[1] != digest:
                    raise ControlError('Ese identificador ya pertenece a otro pedido.', 409)
                return previous[2]
            result = await self._command_locked(action, payload)
            self.requests[request_id] = (time.monotonic(), digest, result)
            return result

    async def _command_locked(self, action, payload):
        if action in ('play', 'test'):
            if len(self.pending) >= 100:
                raise ControlError('La cola está llena. Esperá a que avance.', 409)
            channel = str(payload.get('channel_id', ''))
            room_ids = {room['id'] for room in await self._rooms_locked()}
            if channel not in room_ids:
                raise ControlError('El bot no puede reproducir en esa sala.', 403)
            if self.current and self.current['channel_id'] != channel:
                raise ControlError('El bot ya está reproduciendo en otra sala. Detengan la sesión antes de cambiar de sala.', 409)
            if action == 'test' and (self.current or self.pending):
                raise ControlError('La prueba de sonido requiere que la cola esté vacía.', 409)
            url = payload.get('url', '')
            upload_id = payload.get('upload_id')
            if upload_id is not None and (not isinstance(upload_id, str) or upload_id not in self.uploads or self.uploads[upload_id]['claimed']):
                raise ControlError('Ese audio no está disponible. Volvé a agregar la canción.', 409)
            if action == 'play' and not upload_id and (not isinstance(url, str) or not re.fullmatch(r'https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}', url)):
                raise ControlError('Se requiere un enlace de video de YouTube válido.')
            track = {'id': str(uuid.uuid4()), 'url': url, 'channel_id': channel,
                     'title': str(payload.get('title') or 'Canción de YouTube')[:200],
                     'requested_by': str(payload.get('name') or 'Amigo')[:48]}
            if action == 'test':
                track.update(test=True, title='Tono de prueba')
            elif upload_id:
                self.uploads[upload_id]['claimed'] = True
                track['upload_id'] = upload_id
            was_playing = self.current is not None
            self.last_error = ''
            self.pending.append(track)
            self.revision += 1
            if not self.current:
                await self._start_next()
                if not self.current:
                    raise ControlError(self.last_error, 502)
            message = 'Canción agregada a la cola' if was_playing else 'Reproduciendo en Discord'
        elif action == 'skip':
            await self._stop_audio()
            await self._start_next()
            message = 'Se avanzó a la siguiente canción'
        elif action == 'stop':
            for track in self.pending:
                self.discard_media(track)
            self.pending.clear()
            await self._stop_audio(leave=True)
            self.idle_disconnected = True
            self.revision += 1
            message = 'Sesión detenida y cola vaciada'
        elif action in ('pause', 'resume'):
            if not self.current:
                raise ControlError('No hay una canción en reproducción.', 409)
            async def change_pause():
                for voice in self.player.client.voice_clients:
                    voice.pause() if action == 'pause' else voice.resume()
            await self._call(change_pause())
            self.paused = action == 'pause'
            self.revision += 1
            message = 'Pausado en Discord' if self.paused else 'Reproducción reanudada'
        else:
            raise ControlError('Comando desconocido.', 404)
        return {'message': message, 'state': self.snapshot()}

    async def close(self):
        self.stopping = True
        self.player.on_audio_finished = None
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        async with self.lock:
            await self._call(self.player._disconnect())
        if self.owns_player:
            self.player.close()
            await asyncio.to_thread(self.player.thread.join, 5)
        self.media_dir.cleanup()


def create_app(controller, control_key):
    multi = hasattr(controller, 'resolve_key')
    if not multi and (not isinstance(control_key, str) or len(control_key) < 32):
        raise ValueError('CONTROL_KEY debe tener al menos 32 caracteres.')

    @web.middleware
    async def guard(request, handler):
        if request.path != '/healthz':
            provided = request.headers.get('Authorization', '')
            admin = request.path.startswith('/v1/admin/')
            scoped = controller.resolve_key(provided) if multi else controller
            authorized = controller.is_admin(provided) if admin and multi else (not admin and scoped is not None and (multi or hmac.compare_digest(provided.encode(), ('Bearer ' + control_key).encode())))
            if not authorized:
                return web.json_response({'error': 'Clave de acceso incorrecta.'}, status=401)
            request['session_controller'] = scoped
        try:
            return await handler(request)
        except ControlError as exc:
            return web.json_response({'error': str(exc)}, status=exc.status)
        except web.HTTPException:
            raise
        except (ValueError, json.JSONDecodeError):
            return web.json_response({'error': 'JSON inválido.'}, status=400)
        except Exception:
            log.warning('Falló una operación del controlador compartido.')
            return web.json_response({'error': 'No se pudo completar la operación. Revisá la conexión y la configuración del servidor.'}, status=502)

    app = web.Application(middlewares=[guard], client_max_size=16384)
    async def health(request):
        return web.json_response({'status': 'ok'})
    async def state(request):
        return web.json_response(request['session_controller'].snapshot())
    async def rooms(request):
        return web.json_response({'rooms': await request['session_controller'].rooms()})
    async def command(request):
        payload = await request.json()
        result = await request['session_controller'].command(request.match_info['action'], payload, request.headers.get('X-Request-ID', ''))
        return web.json_response(result)
    async def upload(request):
        return await request['session_controller'].upload(request)
    async def admin_guilds(request):
        return web.json_response(await controller.admin_guilds())
    async def admin_register(request):
        status, result = await controller.register_guild(await request.json())
        return web.json_response(result, status=status)
    async def cleanup(application):
        await controller.close()
    async def start_idle_watchdog(application):
        task = asyncio.create_task(controller.idle_watchdog())
        controller.tasks.add(task)
        task.add_done_callback(controller.tasks.discard)
    app.router.add_get('/healthz', health)
    app.router.add_get('/v1/state', state)
    app.router.add_get('/v1/rooms', rooms)
    app.router.add_post('/v1/commands/{action}', command)
    app.router.add_put('/v1/audio/{upload_id}', upload)
    app.router.add_get('/v1/admin/guilds', admin_guilds)
    app.router.add_post('/v1/admin/guilds', admin_register)
    app.on_cleanup.append(cleanup)
    app.on_startup.append(start_idle_watchdog)
    return app


def single_instance():
    """Prevent duplicate server processes on the same host/container."""
    handle = open(Path(tempfile.gettempdir()) / 'lospi-shared-bot.lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b'':
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Ya hay un servidor del bot ejecutándose en este equipo.')
    return handle


def main():
    from multi_guild import MultiGuildController, parse_guild_keys
    from guild_registry import GuildRegistry
    token = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
    key = os.environ.get('CONTROL_KEY', '').strip()
    guild_keys = parse_guild_keys(os.environ.get('GUILD_ACCESS_KEYS', ''), key, os.environ.get('ALLOWED_GUILD_IDS', ''))
    if not token:
        raise SystemExit('Configurá DISCORD_BOT_TOKEN en el servidor.')
    lock = single_instance()
    async def application():
        player = DiscordMusicPlayer()
        registry_path = os.environ.get('GUILD_REGISTRY_PATH', '')
        admin_key = os.environ.get('ADMIN_KEY', '').strip()
        if admin_key and not registry_path:
            raise ValueError('ADMIN_KEY requiere GUILD_REGISTRY_PATH persistente.')
        registry = GuildRegistry(registry_path) if registry_path else None
        controller = MultiGuildController(player, token, asyncio.get_running_loop(), guild_keys, session_factory=SharedController, registry=registry, admin_key=admin_key)
        return create_app(controller, key)
    try:
        logging.basicConfig(level=logging.INFO)
        web.run_app(application(), host='0.0.0.0', port=int(os.environ.get('PORT', '8080')), access_log=None)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
