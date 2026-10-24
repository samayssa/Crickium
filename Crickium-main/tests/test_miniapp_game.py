"""Headless end-to-end test of the Play Game API (no database, no Telegram)."""
import asyncio, json, random, sys, types

if "asyncpg" not in sys.modules:
    try:
        import asyncpg  # noqa
    except ImportError:
        sys.modules["asyncpg"] = types.ModuleType("asyncpg")

from database import miniapp_game_repo as repo
from miniapp_backend import game_api, api as core_api
from services.miniapp_game_squads import make_player

ROLES = ["Batsman", "Batsman", "Wicketkeeper", "Batsman", "AllRounder", "AllRounder", "Bowler", "Bowler", "Bowler", "Bowler", "Batsman", "Bowler", "AllRounder"]
RAW = []
for i, role in enumerate(ROLES):
    RAW.append({"player_id": 100 + i, "name": f"Player {i}", "country": "IN", "role": role,
                "bat_level": 90 - i if role != "Bowler" else 60, "bowl_level": 85 - i if role in ("Bowler", "AllRounder") else 40,
                "batting_hand": "RH", "bowling_hand": "RAF" if i % 2 else "RAO"})
SQUAD = [make_player(r, i, entity_id=r["player_id"], kind="global") for i, r in enumerate(RAW)]
STORE = {}
results = []


async def load_ctx(uid):
    return {"first_name": "Samay", "franchise": "Test FC", "squad": [dict(p) for p in SQUAD], "lineup": [p["pid"] for p in SQUAD[:11]]}
async def ai_pool(): return []
async def save_session(g): STORE[g["user_id"] if "user_id" in g else 1] = json.loads(json.dumps(g))
async def load_active(uid): return None
async def record_result(g): results.append(g["result"]); return True
async def career_stats(uid): return {"games": len(results)}
async def recent_results(uid, n=6): return []
async def noop(*a, **k): return None
repo.load_user_context, repo.ai_pool, repo.save_session, repo.load_active = load_ctx, ai_pool, save_session, load_active
repo.record_result, repo.career_stats, repo.recent_results = record_result, career_stats, recent_results
core_api._ensure_user = noop


LINKS = {}
async def create_link(uid, name, chat, msg, overs):
    LINKS["tok123"] = {"token": "tok123", "user_id": uid, "user_name": name, "chat_id": chat, "message_id": msg, "overs": overs,
                       "game_id": None, "status": "open", "result_text": None, "created_at": None}
    return "tok123"
async def get_link(t): return LINKS.get(t)
async def attach_link(t, gid): LINKS[t].update(game_id=gid, status="playing")
async def finish_link(t, text): LINKS[t].update(status="done", result_text=text)
async def load_state_by_id(gid): return None
repo.create_link, repo.get_link, repo.attach_link, repo.finish_link, repo.load_state_by_id = create_link, get_link, attach_link, finish_link, load_state_by_id
DELIVERED = []


class V:
    id = 7
    first_name = "Samay"


class Other:
    id = 99
    first_name = "Watcher"


async def call(method, path, payload=None):
    st, body = await game_api.handle(V(), method, path, {}, payload or {})
    json.dumps(body)
    return st, body


async def play(cfg, link=None):
    st, b = await call("POST", "/api/game/start", {**cfg, "replace": True, **({"link": link} if link else {})})
    assert st == 200, b
    g = b["game"]
    assert g["stage"] == "xi"
    # swap a bench player in and move one
    xi, bench = g["xi"]["players"], g["xi"]["bench"]
    if bench:
        st, b = await call("POST", "/api/game/xi/swap", {"rev": g["rev"], "out_pid": xi[10]["pid"], "in_pid": bench[0]["pid"]})
        assert st == 200, b
        g = b["game"]
        assert g["xi"]["changes"] == 1
    st, b = await call("POST", "/api/game/xi/confirm", {"rev": g["rev"]}); g = b["game"]
    assert g["stage"] == "toss"
    st, b = await call("POST", "/api/game/toss", {"rev": g["rev"], "call": "heads"}); g = b["game"]
    if g["stage"] == "decision":
        st, b = await call("POST", "/api/game/toss/decide", {"rev": g["rev"], "decision": random.choice(["bat", "bowl"])}); g = b["game"]
    steps = 0
    rnd = random.Random(1)
    while g["stage"] != "done":
        steps += 1
        assert steps < 800, "game does not finish"
        need = g["need"]
        k = need["kind"]
        if g["stage"] == "break" or k == "continue":
            st, b = await call("POST", "/api/game/continue", {"rev": g["rev"]})
        elif k == "mindset":
            st, b = await call("POST", "/api/game/play", {"rev": g["rev"], "choice": rnd.choice([o["key"] for o in need["options"]])})
        elif k == "bowler":
            st, b = await call("POST", "/api/game/bowler", {"rev": g["rev"], "slot": rnd.choice(need["options"])["slot"]})
        elif k == "tactic":
            st, b = await call("POST", "/api/game/play", {"rev": g["rev"], "choice": rnd.choice([o["key"] for o in need["options"]])})
        else:
            raise AssertionError(need)
        assert st == 200, b
        g = b["game"]
    assert g["result"]["text"]
    return g


async def main():
    for cfg in ({"difficulty": "easy", "overs": 2, "sim": "ball", "pitch": "flat"},
                {"difficulty": "medium", "overs": 5, "sim": "over", "pitch": "random"},
                {"difficulty": "hard", "overs": 10, "sim": "ball", "pitch": "green"},
                {"difficulty": "expert", "overs": 20, "sim": "over", "pitch": "dusty"}):
        g = await play(cfg)
        sc = g["scorecards"]
        print(cfg["difficulty"], cfg["overs"], cfg["sim"], "|", g["result"]["text"], "|", [(c["runs"], c["wkts"], c["overs"]) for c in sc], "| wp", g["wp"]["user"])
    # group link: owner plays, someone else watches, report is delivered once
    import services.miniapp_game_report as rp
    async def fake_deliver(game, link, token, number=0):
        DELIVERED.append((link["chat_id"], link["message_id"], len(rp.summary_card(game, 1)), len(rp.analysis_html(game, 1)))); return True
    rp.deliver = fake_deliver
    await asyncio.sleep(0.3); DELIVERED.clear()
    await create_link(7, "Samay", -100123, 555, 5)
    st, info = await game_api.handle(V(), "GET", "/api/game/link", {"token": "tok123"}, {})
    assert info["owner"] and info["overs"] == 5
    st, info2 = await game_api.handle(Other(), "GET", "/api/game/link", {"token": "tok123"}, {})
    assert not info2["owner"]
    st, w0 = await game_api.handle(Other(), "GET", "/api/game/watch", {"token": "tok123"}, {})
    assert w0["game"] is None
    st, b = await call("POST", "/api/game/start", {"difficulty": "easy", "overs": 2, "sim": "over", "pitch": "even", "replace": True, "link": "tok123"})
    assert LINKS["tok123"]["status"] == "playing"
    st, w1 = await game_api.handle(Other(), "GET", "/api/game/watch", {"token": "tok123"}, {})
    assert w1["game"] and w1["game"]["need"]["kind"] == "spectate" and "xi" not in w1["game"]
    g = b["game"]
    gg = await play({"difficulty": "easy", "overs": 2, "sim": "over", "pitch": "even"}, link="tok123") if False else None
    # finish the linked game
    st, b = await call("POST", "/api/game/xi/confirm", {"rev": g["rev"]}); g = b["game"]
    st, b = await call("POST", "/api/game/toss", {"rev": g["rev"], "call": "heads"}); g = b["game"]
    if g["stage"] == "decision":
        st, b = await call("POST", "/api/game/toss/decide", {"rev": g["rev"], "decision": "bat"}); g = b["game"]
    rnd = random.Random(3)
    while g["stage"] != "done":
        need = g["need"]; k = need["kind"]
        if k == "continue": st, b = await call("POST", "/api/game/continue", {"rev": g["rev"]})
        elif k == "bowler": st, b = await call("POST", "/api/game/bowler", {"rev": g["rev"], "slot": need["options"][0]["slot"]})
        else: st, b = await call("POST", "/api/game/play", {"rev": g["rev"], "choice": rnd.choice([o["key"] for o in need["options"]])})
        g = b["game"]
    await asyncio.sleep(0.5)
    assert LINKS["tok123"]["status"] == "done", LINKS
    assert DELIVERED and DELIVERED[0][:2] == (-100123, 555), DELIVERED
    print("group link OK", DELIVERED[0])
    DELIVERED.clear()
    await play({"difficulty": "easy", "overs": 2, "sim": "over", "pitch": "even"})
    await asyncio.sleep(0.5)
    assert DELIVERED and DELIVERED[0][:2] == (7, None), DELIVERED
    print("DM report OK")
    # stale rev & errors
    st, b = await call("POST", "/api/game/start", {"difficulty": "medium", "overs": 5, "sim": "ball", "pitch": "even", "replace": True})
    st, b2 = await call("POST", "/api/game/xi/confirm", {"rev": 9999})
    assert st == 409 and b2["status"] == "stale"
    st, b3 = await call("POST", "/api/game/start", {"difficulty": "nope", "overs": 5})
    assert st == 409
    print("ALL OK", len(results), "results")

asyncio.run(main())
