"""One Discord Gateway, isolated playback and HTTP credentials per guild."""
import asyncio
import hmac
import json
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
    def __init__(self, player, token, loop, guild_keys, session_factory=None):
        if session_factory is None:
            from shared_server import SharedController
            session_factory = SharedController
        self.player = player
        self.login_lock = None
        self.sessions = {}
        self.keys = dict(guild_keys)
        self.stopping = False
        self.tasks = set()
        player.multi_guild = True
        player.on_guild_audio_finished = self._audio_finished
        for guild_id in guild_keys:
            proxy = GuildPlayer(self, guild_id)
            self.sessions[guild_id] = session_factory(proxy, token, loop, [guild_id], owns_player=False)

    def resolve_key(self, authorization):
        selected = None
        for guild_id, key in self.keys.items():
            if hmac.compare_digest(authorization.encode(), ('Bearer ' + key).encode()):
                selected = self.sessions[guild_id]
        return selected

    def _audio_finished(self, guild_id, generation, failed, packets):
        session = self.sessions.get(guild_id)
        if session and session.player.on_audio_finished:
            session.player.on_audio_finished(generation, failed, packets)

    async def idle_watchdog(self):
        # Separate tasks: a slow Discord operation for one guild cannot prevent
        # another guild's inactivity timer from running.
        await asyncio.gather(*(session.idle_watchdog() for session in self.sessions.values()))

    async def close(self):
        self.stopping = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.gather(*(session.close() for session in self.sessions.values()))
        self.player.on_guild_audio_finished = None
        self.player.close()
        await asyncio.to_thread(self.player.thread.join, 5)
