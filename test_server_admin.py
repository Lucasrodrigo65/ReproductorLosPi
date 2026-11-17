import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from aiohttp.test_utils import TestClient, TestServer
from guild_registry import GuildRegistry, fingerprint
from multi_guild import MultiGuildController
from shared_server import create_app
from test_multi_guild import FakeTransport, KEY_A, KEY_B

ADMIN = 'owner-only-' + 'z' * 40


class AdministrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'access.sqlite3'
        self.manager, self.http = await self.start()

    async def start(self):
        transport = FakeTransport()
        transport.client.user = SimpleNamespace(id=123)
        for guild in transport.guilds:
            guild.name = 'Server ' + str(guild.id)
        manager = MultiGuildController(transport, 'fake', asyncio.get_running_loop(), {1: KEY_A},
                                       registry=GuildRegistry(self.path), admin_key=ADMIN)
        http = TestClient(TestServer(create_app(manager, None)))
        await http.start_server()
        return manager, http

    async def asyncTearDown(self):
        await self.http.close()
        self.directory.cleanup()

    def headers(self, key=ADMIN):
        return {'Authorization': 'Bearer ' + key}

    async def add(self, guild='2', key=KEY_B):
        return await self.http.post('/v1/admin/guilds', headers=self.headers(), json={'guild_id': guild, 'key': key})

    async def test_owner_only_and_no_admin_playback_or_secret_listing(self):
        for key in (KEY_A, KEY_B, 'wrong'):
            self.assertEqual((await self.http.get('/v1/admin/guilds', headers=self.headers(key))).status, 401)
            reply = await self.http.post('/v1/admin/guilds', headers=self.headers(key), json={'guild_id': '2', 'key': KEY_B})
            self.assertEqual(reply.status, 401)
        self.assertEqual((await self.http.get('/v1/state', headers=self.headers())).status, 401)
        reply = await self.http.get('/v1/admin/guilds', headers=self.headers())
        text = await reply.text()
        self.assertEqual(reply.status, 200)
        self.assertNotIn(KEY_A, text)
        self.assertNotIn(ADMIN, text)

    async def test_add_is_immediate_durable_isolated_and_idempotent(self):
        self.assertEqual((await self.add()).status, 200)
        self.assertEqual((await self.add()).status, 200)
        self.assertEqual((await self.add(key='c' * 40)).status, 409)
        reply = await self.http.get('/v1/rooms', headers=self.headers(KEY_B))
        self.assertEqual([r['id'] for r in (await reply.json())['rooms']], ['20'])
        self.assertGreaterEqual(len(self.manager.tasks), 3)  # parent plus both idle timers
        self.assertEqual(self.manager.registry.load(), {2: fingerprint(KEY_B)})
        self.assertNotIn(KEY_B.encode(), self.path.read_bytes())
        await self.http.close()
        self.manager, self.http = await self.start()
        self.assertEqual((await self.http.get('/v1/state', headers=self.headers(KEY_B))).status, 200)
        self.assertEqual((await self.http.get('/v1/state', headers=self.headers(KEY_A))).status, 200)

    async def test_invalid_unknown_duplicate_and_storage_failure_do_not_grant_access(self):
        self.assertEqual((await self.add('999')).status, 403)
        self.assertEqual((await self.add(key='short')).status, 400)
        self.assertEqual((await self.add(key=KEY_A)).status, 409)
        self.assertEqual((await self.add(key=ADMIN)).status, 409)
        self.assertEqual((await self.add('1')).status, 409)
        self.manager.registry.db.execute('PRAGMA query_only=ON')
        self.assertEqual((await self.add()).status, 502)
        self.assertNotIn(2, self.manager.sessions)
        self.assertEqual((await self.http.get('/v1/state', headers=self.headers(KEY_B))).status, 401)


if __name__ == '__main__':
    unittest.main()
