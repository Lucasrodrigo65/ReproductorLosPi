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
    def __init__(self, player, token, loop, allowed_guilds=()):
        self.player, self.token, self.loop = player, token, loop
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
        self.current = None
        self.current_generation = None
        self.paused = False

    async def _start_next(self):
        while self.pending:
            track = self.pending.popleft()
            self.current = track
            self.paused = False
            self.revision += 1
            try:
                if track.get('test'):
                    await self._call(self.player._test_sound(self.token, int(track['channel_id'])))
                else:
                    await self._call(self.player._play(self.token, int(track['channel_id']), track['url']))
                self.current_generation = self.player.playback_generation
                return
            except Exception:
                log.warning('No se pudo iniciar una pista; se continúa con la cola.')
                self.last_error = 'Una pista no pudo reproducirse. Probá otra canción o el tono de prueba.'
                self.current = None
                self.current_generation = None
        self.revision += 1

    async def _finish(self, generation, failed):
        async with self.lock:
            if generation != self.current_generation:
                return
            if failed:
                self.last_error = 'Falló el envío de audio de una pista.'
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
            if action == 'play' and (not isinstance(url, str) or not re.fullmatch(r'https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}', url)):
                raise ControlError('Se requiere un enlace de video de YouTube válido.')
            track = {'id': str(uuid.uuid4()), 'url': url, 'channel_id': channel,
                     'title': str(payload.get('title') or 'Canción de YouTube')[:200],
                     'requested_by': str(payload.get('name') or 'Amigo')[:48]}
            if action == 'test':
                track.update(test=True, title='Tono de prueba')
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
            self.pending.clear()
            await self._stop_audio(leave=True)
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
        self.player.close()
        await asyncio.to_thread(self.player.thread.join, 5)


def create_app(controller, control_key):
    if not isinstance(control_key, str) or len(control_key) < 32:
        raise ValueError('CONTROL_KEY debe tener al menos 32 caracteres.')

    @web.middleware
    async def guard(request, handler):
        if request.path != '/healthz':
            provided = request.headers.get('Authorization', '')
            if not hmac.compare_digest(provided.encode(), ('Bearer ' + control_key).encode()):
                return web.json_response({'error': 'Clave de acceso incorrecta.'}, status=401)
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
        return web.json_response(controller.snapshot())
    async def rooms(request):
        return web.json_response({'rooms': await controller.rooms()})
    async def command(request):
        payload = await request.json()
        result = await controller.command(request.match_info['action'], payload, request.headers.get('X-Request-ID', ''))
        return web.json_response(result)
    async def cleanup(application):
        await controller.close()
    app.router.add_get('/healthz', health)
    app.router.add_get('/v1/state', state)
    app.router.add_get('/v1/rooms', rooms)
    app.router.add_post('/v1/commands/{action}', command)
    app.on_cleanup.append(cleanup)
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
    token = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
    key = os.environ.get('CONTROL_KEY', '').strip()
    if not token or len(key) < 32:
        raise SystemExit('Configurá DISCORD_BOT_TOKEN y CONTROL_KEY (mínimo 32 caracteres) en el servidor.')
    lock = single_instance()
    async def application():
        player = DiscordMusicPlayer()
        guilds = os.environ.get('ALLOWED_GUILD_IDS', '').split(',')
        controller = SharedController(player, token, asyncio.get_running_loop(), [v.strip() for v in guilds if v.strip()])
        return create_app(controller, key)
    try:
        logging.basicConfig(level=logging.INFO)
        web.run_app(application(), host='0.0.0.0', port=int(os.environ.get('PORT', '8080')), access_log=None)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
