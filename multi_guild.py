"""One Discord Gateway, isolated playback and HTTP credentials per guild."""
import asyncio
import hmac
import json
from guild_registry import fingerprint
from types import SimpleNamespace


def parse_guild_keys(raw, legacy_key='', legacy_guilds=''):
    """Fail closed; a key must identify exactly one guild."""
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        values = json.loads(raw, object_pairs_hook=unique) if raw.strip() else {}
        if not isinstance(values, dict):
            raise ValueError()
        for guild, key in values.items():
            if not isinstance(guild, str) or not guild.isdecimal() or int(guild) <= 0:
                raise ValueError()
            if not isinstance(key, str) or len(key) < 32 or key != key.strip():
                raise ValueError()
        if legacy_key:
            guilds = [g.strip() for g in legacy_guilds.split(',') if g.strip()]
            if len(guilds) != 1 or not guilds[0].isdecimal() or int(guilds[0]) <= 0 or len(legacy_key) < 32:
                raise ValueError()
            # Explicit configuration can rotate a guild's legacy key.
            values.setdefault(guilds[0], legacy_key)
        if not values or len(set(map(int, values))) != len(values) or len(set(values.values())) != len(values):
            raise ValueError()
        return {int(g): key for g, key in values.items()}
    except (ValueError, TypeError):
        raise ValueError('GUILD_ACCESS_KEYS debe mapear cada ID de servidor a una clave única de al menos 32 caracteres. CONTROL_KEY requiere un solo ALLOWED_GUILD_IDS.') from None


class GuildPlayer:
    """Narrow view of the shared Discord transport, with its own generation."""
    def __init__(self, owner, guild_id):
        self.owner, self.guild_id = owner, guild_id
        self.loop = owner.player.loop
        self.on_audio_finished = None

    @property
    def playback_generation(self):
        return self.owner.player.voice_generations.get(self.guild_id, 0)

    @playback_generation.setter
    def playback_generation(self, value):
        self.owner.player.voice_generations[self.guild_id] = value

    @property
    def client(self):
        client = self.owner.player.client
        return SimpleNamespace(
            guilds=[g for g in client.guilds if g.id == self.guild_id] if client else [],
            voice_clients=[v for v in client.voice_clients if v.guild.id == self.guild_id] if client else [])

    async def _ready(self, token):
        # Created and used only on the shared transport's event loop.
        if self.owner.login_lock is None:
            self.owner.login_lock = asyncio.Lock()
        async with self.owner.login_lock:
            await self.owner.player._connect(token)

    async def _list_rooms(self, token):
        await self._ready(token)
        rooms = await self.owner.player._list_rooms(token)
        allowed = {str(c.id) for g in self.client.guilds for c in g.voice_channels}
        return [r for r in rooms if r['id'] in allowed]

    async def _validate_channel(self, token, channel):
        await self._ready(token)
        target = self.owner.player.client.get_channel(channel)
        if target is None:
            target = await self.owner.player.client.fetch_channel(channel)
        if not getattr(target, 'guild', None) or target.guild.id != self.guild_id:
            raise RuntimeError('Ese canal no pertenece al servidor de esta clave.')

    async def _play(self, token, channel, url):
        await self._validate_channel(token, channel)
        await self.owner.player._play(token, channel, url)

    async def _play_file(self, token, channel, path):
        await self._validate_channel(token, channel)
        await self.owner.player._play_file(token, channel, path)

    async def _test_sound(self, token, channel):
        await self._validate_channel(token, channel)
        await self.owner.player._test_sound(token, channel)

    async def _disconnect(self):
        self.playback_generation += 1
        for voice in self.client.voice_clients:
            voice.stop()
            await voice.disconnect(force=True)


class MultiGuildController:
    def __init__(self, player, token, loop, guild_keys, session_factory=None, registry=None, admin_key=''):
        if session_factory is None:
            from shared_server import SharedController
            session_factory = SharedController
        self.player = player
        self.token, self.loop, self.session_factory = token, loop, session_factory
        self.registry, self.admin_key = registry, admin_key
        self.admin_lock = asyncio.Lock()
        self.watchdogs_started = False
        self.login_lock = None
        self.sessions = {}
        self.keys = dict(guild_keys)
        self.dynamic_keys = registry.load() if registry else {}
        if set(self.dynamic_keys) & set(self.keys) or set(self.dynamic_keys.values()) & {fingerprint(k) for k in self.keys.values()}:
            raise ValueError('Hay servidores o claves duplicados entre las variables y el registro persistente.')
        if admin_key and (len(admin_key) < 32 or fingerprint(admin_key) in set(self.dynamic_keys.values()) | {fingerprint(k) for k in self.keys.values()}):
            raise ValueError('La clave de administración debe ser única y tener al menos 32 caracteres.')
        self.stopping = False
        self.tasks = set()
        player.multi_guild = True
        player.on_guild_audio_finished = self._audio_finished
        for guild_id in set(guild_keys) | set(self.dynamic_keys):
            self._add_session(guild_id)

    def _add_session(self, guild_id):
        proxy = GuildPlayer(self, guild_id)
        session = self.session_factory(proxy, self.token, self.loop, [guild_id], owns_player=False)
        self.sessions[guild_id] = session
        if self.watchdogs_started:
            self._watch(session)

    def _watch(self, session):
        task = asyncio.create_task(session.idle_watchdog())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def is_admin(self, authorization):
        return bool(self.registry and self.admin_key and hmac.compare_digest(authorization.encode(), ('Bearer ' + self.admin_key).encode()))

    async def admin_guilds(self):
        proxy = next(iter(self.sessions.values())).player
        async def available():
            await proxy._ready(self.token)
            client = self.player.client
            return {'invite_url': f'https://discord.com/oauth2/authorize?client_id={client.user.id}&scope=bot&permissions=3146752',
                    'guilds': [{'id': str(g.id), 'name': g.name, 'enabled': g.id in self.sessions} for g in client.guilds]}
        return await next(iter(self.sessions.values()))._call(available())

    async def register_guild(self, payload):
        if not isinstance(payload, dict):
            return 400, {'error': 'Se requiere un objeto JSON.'}
        guild, key = payload.get('guild_id'), payload.get('key')
        if not isinstance(guild, str) or not guild.isdecimal() or not 0 < int(guild) < 2**64:
            return 400, {'error': 'Elegí un servidor válido.'}
        if not isinstance(key, str) or len(key) < 32 or len(key) > 256 or key != key.strip():
            return 400, {'error': 'La clave debe tener entre 32 y 256 caracteres.'}
        guild, digest = int(guild), fingerprint(key)
        async with self.admin_lock:
            if self.dynamic_keys.get(guild) == digest:
                return 200, {'message': 'Servidor habilitado.', 'guild_id': str(guild)}
            if guild in self.sessions:
                return 409, {'error': 'Este servidor ya tiene una clave. Se conservó su acceso actual.'}
            if digest in set(self.dynamic_keys.values()) | {fingerprint(k) for k in self.keys.values()} | {fingerprint(self.admin_key)}:
                return 409, {'error': 'Generá una clave diferente.'}
            available = await self.admin_guilds()
            if str(guild) not in {g['id'] for g in available['guilds']}:
                return 403, {'error': 'Primero invitá al bot a ese servidor y actualizá la lista.'}
            # Commit before enabling access. A failed write never grants temporary access.
            self.registry.add(guild, digest)
            self.dynamic_keys[guild] = digest
            self._add_session(guild)
            return 200, {'message': 'Servidor habilitado. Compartí su clave por privado.', 'guild_id': str(guild)}

    def resolve_key(self, authorization):
        selected = None
        for guild_id, key in self.keys.items():
            if hmac.compare_digest(authorization.encode(), ('Bearer ' + key).encode()):
                selected = self.sessions[guild_id]
        if authorization.startswith('Bearer '):
            digest = fingerprint(authorization[7:])
            for guild_id, stored in self.dynamic_keys.items():
                if hmac.compare_digest(digest, stored):
                    selected = self.sessions[guild_id]
        return selected

    def _audio_finished(self, guild_id, generation, failed, packets):
        session = self.sessions.get(guild_id)
        if session and session.player.on_audio_finished:
            session.player.on_audio_finished(generation, failed, packets)

    async def idle_watchdog(self):
        # Separate tasks: a slow Discord operation for one guild cannot prevent
        # another guild's inactivity timer from running.
        self.watchdogs_started = True
        for session in self.sessions.values():
            self._watch(session)
        while not self.stopping:
            await asyncio.sleep(1)

    async def close(self):
        self.stopping = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.gather(*(session.close() for session in self.sessions.values()))
        self.player.on_guild_audio_finished = None
        self.player.close()
        await asyncio.to_thread(self.player.thread.join, 5)
        if self.registry:
            self.registry.close()
