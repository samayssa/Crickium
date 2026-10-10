import asyncio
import hashlib
import hmac
import json
import re
import time
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from miniapp_backend import api
from miniapp_backend.auth import TelegramViewer, _verify_init_data
from miniapp_backend.config import BOT_TOKEN
from miniapp_backend.server import app


def signed_init_data(*, auth_date: int | None = None, user_id: int = 4242) -> str:
    params = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeConnection:
    def __init__(self, balances, squads):
        self.balances = dict(balances)
        self.squads = {user_id: list(squad) for user_id, squad in squads.items()}
        self.writes = []

    def transaction(self):
        return FakeTransaction()

    async def fetchrow(self, query, *args):
        normalized = " ".join(query.lower().split())
        user_id = int(args[0])
        if "select balance from users" in normalized:
            return {"balance": self.balances.get(user_id)} if user_id in self.balances else None
        if "select squad from team_squads" in normalized:
            return {"squad": json.dumps(self.squads[user_id])} if user_id in self.squads else None
        raise AssertionError(f"Unexpected SQL in test: {normalized[:80]}")

    async def execute(self, query, *args):
        normalized = " ".join(query.lower().split())
        self.writes.append((normalized, args))
        if normalized.startswith("update users set balance=balance-"):
            price, user_id = int(args[0]), int(args[1])
            self.balances[user_id] -= price
        elif normalized.startswith("insert into team_squads"):
            user_id, squad_json = int(args[0]), args[1]
            self.squads[user_id] = json.loads(squad_json)
        else:
            raise AssertionError(f"Unexpected write in test: {normalized[:80]}")
        return "UPDATE 1"


class MiniAppBackendTests(unittest.TestCase):
    def test_telegram_init_data_signature_and_expiry(self):
        self.assertEqual(_verify_init_data(signed_init_data())["auth_date"], str(int(time.time())))
        with self.assertRaises(ValueError):
            _verify_init_data(signed_init_data(auth_date=int(time.time()) - 7 * 3600))
        with self.assertRaises(ValueError):
            _verify_init_data(signed_init_data().replace("query_id=test-query", "query_id=test-query&query_id=again"))
        with self.assertRaises(ValueError):
            _verify_init_data(signed_init_data().replace("hash=", "hash=00"))

    def test_asgi_public_shell_health_and_protected_api(self):
        async def request(path, method="GET", headers=None):
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            await app(
                {"type": "http", "method": method, "path": path, "headers": headers or [], "query_string": b""},
                receive, send,
            )
            status = next(msg["status"] for msg in sent if msg["type"] == "http.response.start")
            body = b"".join(msg.get("body", b"") for msg in sent if msg["type"] == "http.response.body")
            return status, body

        async def run():
            status, body = await request("/")
            self.assertEqual(status, 200)
            self.assertIn(b"app_v2.js", body)
            status, body = await request("/api/health")
            self.assertEqual(status, 200)
            self.assertIn(b'"ok":true', body)
            status, _ = await request("/api/home")
            self.assertEqual(status, 401)
            status, _ = await request("/static/../config.py")
            self.assertEqual(status, 404)

        asyncio.run(run())

    def test_match_challenge_query_uses_real_schema_names(self):
        async def fake_fetch(query, *args):
            if "FROM match_challenges" in query:
                return [{
                    "match_id": 81,
                    "challenger_id": 4242,
                    "challenger_name": "Test",
                    "opponent_id": 55,
                    "opponent_name": "Opponent",
                    "status": "pending",
                    "pitch": None,
                    "match_type": "T20",
                    "created_at": None,
                }]
            return []

        async def run():
            with patch.object(api, "fetch", new=AsyncMock(side_effect=fake_fetch)):
                items = await api._active_matches(4242)
            challenge = next(item for item in items if item["engine"] == "MATCH")
            self.assertEqual(challenge["match_id"], 81)
            self.assertEqual(challenge["match_type"], "T20")

        asyncio.run(run())

    def test_market_role_and_price_filters_use_server_price_chart(self):
        players = {
            "global": [
                {"player_id": 10, "name": "High", "role": "Batsman", "bat_level": 85, "bowl_level": 30, "player_kind": "global"},
                {"player_id": 11, "name": "Budget", "role": "Batsman", "bat_level": 75, "bowl_level": 20, "player_kind": "global"},
            ],
            "special": [
                {"player_id": -22, "special_edition_id": 22, "name": "All Rounder", "role": "All Rounder", "bat_level": 82, "bowl_level": 83, "player_kind": "special"},
            ],
            "showcase": [],
        }

        async def fake_query(kind, *_):
            return players[kind]

        async def run():
            with patch.object(api, "_query_players", new=AsyncMock(side_effect=fake_query)), patch.object(
                api, "_owned_squad", new=AsyncMock(return_value=[])
            ):
                filtered = await api.build_market_response(
                    TelegramViewer(id=4242), {"min_price": "1000000", "limit": "10"}
                )
                allrounders = await api.build_market_response(
                    TelegramViewer(id=4242), {"role": "allrounder", "limit": "10"}
                )
            self.assertEqual([item["name"] for item in filtered["items"]], ["High"])
            self.assertEqual([item["name"] for item in allrounders["items"]], ["All Rounder"])

        asyncio.run(run())

    def test_player_performance_stats_are_scoped_to_the_viewer(self):
        queries = []

        async def fake_fetchrow(query, *args):
            normalized = " ".join(query.lower().split())
            queries.append((normalized, args))
            if "from players where player_id=$1" in normalized:
                return {
                    "player_id": 10, "name": "Catalog Player", "country": "India",
                    "role": "Batsman", "bat_level": 85, "bowl_level": 30,
                    "player_kind": "global",
                }
            return {
                "bat_matches": 1, "bat_innings": 1, "runs": 40, "fifties": 0,
                "centuries": 0, "bat_balls": 20, "dismissals": 1,
                "bowl_matches": 0, "bowl_innings": 0, "wickets": 0,
                "bowl_balls": 0, "bowl_runs": 0,
                "highest_recorded_match_runs": 40, "best_recorded_match_wickets": 0,
            }

        async def fake_fetch(query, *args):
            queries.append((" ".join(query.lower().split()), args))
            return []

        async def run():
            with patch.object(api, "fetchrow", new=AsyncMock(side_effect=fake_fetchrow)), patch.object(
                api, "fetch", new=AsyncMock(side_effect=fake_fetch)
            ), patch.object(api, "_owned_squad", new=AsyncMock(return_value=[])):
                details = await api.build_player_detail_response(
                    TelegramViewer(id=4242), kind="global", entity_id=10
                )

            stats_queries = [(sql, args) for sql, args in queries if "player_user_match_stats" in sql]
            self.assertEqual(len(stats_queries), 2)
            for sql, args in stats_queries:
                self.assertIn("where user_id=$1 and player_id=$2", sql)
                self.assertEqual(args, (4242, 10))
            self.assertEqual(details["batting_stats"]["runs"], 40)
            self.assertIn("this Telegram user's values", details["stats_note"])

        asyncio.run(run())

    def test_purchase_is_scoped_to_authenticated_users_own_squad_and_balance(self):
        player = {
            "player_id": 10,
            "name": "Actual Catalog Player",
            "country": "India",
            "role": "Batsman",
            "bat_level": 85,
            "bowl_level": 30,
            "player_kind": "global",
        }
        owned_by_user_2 = dict(player)
        conn = FakeConnection(
            balances={101: 2_000_000, 202: 2_000_000},
            squads={101: [], 202: [owned_by_user_2]},
        )

        @asynccontextmanager
        async def fake_acquire():
            yield conn

        async def run():
            with patch.object(api, "_select_player", new=AsyncMock(return_value=player)), patch.object(
                api, "acquire", new=fake_acquire
            ), patch("database.player_user_stats_repo.reset_player_user_stats", new=AsyncMock()), patch(
                "services.quest_engine.record_quest_event", new=AsyncMock()
            ):
                buyer_result = await api.purchase_market_player(
                    TelegramViewer(id=101), "global", 10
                )
                existing_owner_result = await api.purchase_market_player(
                    TelegramViewer(id=202), "global", 10
                )

            self.assertEqual(buyer_result["status"], "success")
            self.assertEqual(existing_owner_result["status"], "already_owned")
            self.assertEqual(len(conn.squads[101]), 1)
            self.assertEqual(len(conn.squads[202]), 1)
            self.assertEqual(conn.balances[101], 2_000_000 - buyer_result["price"])
            self.assertEqual(conn.balances[202], 2_000_000)
            self.assertTrue(all(args[-1] == 101 for sql, args in conn.writes if sql.startswith("update users")))
            self.assertFalse(any("update users" in sql and args[-1] == 202 for sql, args in conn.writes))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
