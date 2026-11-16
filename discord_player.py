"""Discord voice playback on a dedicated asyncio thread; no Tk calls here."""
import asyncio
import queue
import shlex
import threading


class CheckedAudio:
    """Wrap an AudioSource while retaining packet counts for EOF diagnostics."""
    def __init__(self, source):
        self.source = source
        self.packets = 0

    def read(self):
        packet = self.source.read()
        if packet:
            self.packets += 1
        return packet

    def is_opus(self):
        return self.source.is_opus()

    def cleanup(self):
        self.source.cleanup()


class DiscordMusicPlayer:
    def __init__(self):
        self.events = queue.Queue()
        self.client = None
        self.gateway = None
        self.token = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.closed = False
        self.playback_generation = 0
        self.on_audio_finished = None

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    def _submit(self, operation, message, token='', success_kind='success'):
        if self.closed:
            operation.close()
            return
        future = asyncio.run_coroutine_threadsafe(operation, self.loop)

        def done(result):
            try:
                value = result.result()
                self.events.put((success_kind, value if success_kind == 'channels' else message))
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                if token:
                    error = error.replace(token, '[token oculto]')
                self.events.put(('error', error))

        future.add_done_callback(done)

    def play(self, token, channel_id, url):
        self._submit(self._play(token, channel_id, url), 'Reproduciendo en Discord', token)

    def test_sound(self, token, channel_id):
        self._submit(self._test_sound(token, channel_id), 'Enviando tono de prueba a Discord (3 segundos)', token)

    def list_rooms(self, token):
        self._submit(self._list_rooms(token), None, token, success_kind='channels')

    async def _list_rooms(self, token):
        await self._connect(token)
        rooms = []
        for guild in sorted(self.client.guilds, key=lambda g: (g.name.casefold(), g.id)):
            if guild.me is None:
                continue
            for channel in guild.voice_channels:
                permissions = channel.permissions_for(guild.me)
                if permissions.view_channel and permissions.connect and permissions.speak:
                    rooms.append({'id': str(channel.id), 'server': guild.name,
                                  'category': channel.category.name if channel.category else '',
                                  'name': channel.name})
        return rooms

    async def _connect(self, token):
        import discord

        if self.client and self.token == token and self.client.is_ready():
            return
        await self._disconnect()
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        self.client = discord.Client(intents=intents)
        self.token = token
        try:
            await asyncio.wait_for(self.client.login(token), timeout=30)
            self.gateway = asyncio.create_task(self.client.connect(reconnect=True))
            ready = asyncio.create_task(self.client.wait_until_ready())
            try:
                completed, _ = await asyncio.wait(
                    (ready, self.gateway), timeout=45,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self.gateway in completed:
                    await self.gateway
                    raise RuntimeError('Discord cerró la conexión. Volvé a intentar.')
                if ready not in completed:
                    raise RuntimeError('Discord no respondió a tiempo. Revisá tu conexión.')
                await ready
            finally:
                ready.cancel()
                await asyncio.gather(ready, return_exceptions=True)
        except BaseException:
            await self._disconnect()
            raise

    @staticmethod
    def _extract(url):
        import yt_dlp

        with yt_dlp.YoutubeDL({
            'format': 'bestaudio/best', 'noplaylist': True,
            'quiet': True, 'no_warnings': True, 'socket_timeout': 15,
        }) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info or not info.get('url'):
            raise RuntimeError('No se pudo obtener el audio de YouTube.')
        return info

    async def _join_voice(self, token, channel_id):
        import discord
        await self._connect(token)
        channel = self.client.get_channel(channel_id)
        if channel is None:
            channel = await self.client.fetch_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            raise RuntimeError('El ID debe ser de un canal de voz normal del servidor.')
        permissions = channel.permissions_for(channel.guild.me)
        if not (permissions.view_channel and permissions.connect and permissions.speak):
            raise RuntimeError('El bot necesita Ver canal, Conectar y Hablar en ese canal.')
        voice = channel.guild.voice_client
        if voice and voice.is_connected():
            if voice.channel.id != channel.id:
                await voice.move_to(channel)
        else:
            voice = await channel.connect(timeout=30, reconnect=True, self_deaf=True)
        # Keep this desktop player in a single voice channel at a time.
        for other in list(self.client.voice_clients):
            if other is not voice:
                await other.disconnect(force=True)
        state = channel.guild.me.voice
        if state and (state.mute or state.suppress):
            raise RuntimeError('Discord tiene al bot silenciado o con la voz suprimida en el servidor.')
        # Voice transport may connect before the end-to-end encryption session is ready.
        connection = voice._connection
        for _ in range(100):
            if not connection.dave_protocol_version or connection.can_encrypt:
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError('El cifrado de voz de Discord no quedó listo. Desconectá el bot y reintentá.')
        return voice

    async def _play(self, token, channel_id, url):
        import discord
        import imageio_ffmpeg

        ffmpeg = await asyncio.to_thread(imageio_ffmpeg.get_ffmpeg_exe)
        voice = await self._join_voice(token, channel_id)
        # Resolve afresh: signed YouTube audio URLs expire.
        info = await asyncio.to_thread(self._extract, url)
        headers = ''.join(
            f'{key}: {value}\r\n'
            for key, value in info.get('http_headers', {}).items()
            if key.lower() in ('user-agent', 'referer')
            and '\r' not in str(value) and '\n' not in str(value)
        )
        before = '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        if headers:
            before += ' -headers ' + shlex.quote(headers)
        source = discord.FFmpegOpusAudio(
            info['url'], executable=ffmpeg, before_options=before,
            options='-vn -loglevel error', bitrate=128,
        )
        self._start_audio(voice, source)

    async def _test_sound(self, token, channel_id):
        import discord
        import imageio_ffmpeg

        ffmpeg = await asyncio.to_thread(imageio_ffmpeg.get_ffmpeg_exe)
        voice = await self._join_voice(token, channel_id)
        source = discord.FFmpegOpusAudio(
            'sine=frequency=440:sample_rate=48000:duration=3',
            executable=ffmpeg, before_options='-nostdin -f lavfi',
            options='-vn -af volume=0.5 -loglevel error', bitrate=128,
        )
        self._start_audio(voice, source)

    def _start_audio(self, voice, source):
        import discord

        # Discord requires AudioSource subclasses, so adapt the monitored source.
        class MonitoredAudio(discord.AudioSource, CheckedAudio):
            read = CheckedAudio.read
            is_opus = CheckedAudio.is_opus
            cleanup = CheckedAudio.cleanup

        checked = MonitoredAudio(source)
        self.playback_generation += 1
        generation = self.playback_generation
        def finished(error):
            if generation != self.playback_generation:
                return
            callback = getattr(self, 'on_audio_finished', None)
            if callback:
                callback(generation, error is not None, checked.packets)
            if error:
                self.events.put(('playback_error', 'Falló la transmisión de voz a Discord. Desconectá el bot y volvé a intentar.'))
            elif checked.packets == 0:
                self.events.put(('playback_error', 'FFmpeg no produjo audio. La fuente pudo rechazar el acceso; probá otra canción o el tono de prueba.'))
            else:
                self.events.put(('playback_finished', f'Envío terminado: {checked.packets} paquetes de audio.'))
        try:
            voice.stop()
            voice.play(checked, after=finished)
        except BaseException:
            source.cleanup()
            raise

    def disconnect(self):
        self._submit(self._disconnect(), 'Bot desconectado de Discord', self.token or '')

    async def _disconnect(self):
        self.playback_generation = getattr(self, 'playback_generation', 0) + 1
        if self.client:
            try:
                for voice in list(self.client.voice_clients):
                    await voice.disconnect(force=True)
            finally:
                await self.client.close()
                if self.gateway:
                    self.gateway.cancel()
                    await asyncio.gather(self.gateway, return_exceptions=True)
                self.client = None
                self.gateway = None
                self.token = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        future = asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop)
        future.add_done_callback(lambda _: self.loop.call_soon_threadsafe(self.loop.stop))
