
from __future__ import annotations

from datetime import datetime, timezone

from .auth import TelegramViewer
from .db import execute, fetchrow, fetchval
from .schemas import HomeResponse, Profile, Wallet, League, Stats, RewardState, PlayerSummary, PlayerDetail, PlayerSearchResponse
from .telegram import get_profile_photo_url, get_file_url
from database.players_repo import get_player, search_players
from database.card_images_repo import get_player_card_image, get_card_template_image
from utils.style import batting_style_text, bowling_style_text
from utils.price_chart import get_price
from utils.rarity import get_rarity


def _display_name(viewer: TelegramViewer) -> str:
    parts = [viewer.first_name or ""]
    if viewer.last_name:
        parts.append(viewer.last_name)
    name = " ".join(part.strip() for part in parts if part.strip()).strip()
    return name or "Crickium Player"


def _league_from_spent(total_spent: int) -> tuple[str, int, str]:
    tiers = [
        (0, "Bronze League"),
        (5000, "Silver League"),
        (20000, "Gold League"),
        (50000, "Platinum League"),
        (120000, "Diamond League"),
    ]
    label = tiers[0][1]
    next_threshold = 5000
    lower = 0

    for i, (threshold, tier_label) in enumerate(tiers):
        if total_spent >= threshold:
            label = tier_label
            lower = threshold
            if i + 1 < len(tiers):
                next_threshold = tiers[i + 1][0]
            else:
                next_threshold = total_spent + 1

    span = max(1, next_threshold - lower)
    progress = int(((total_spent - lower) / span) * 100)
    progress = max(0, min(100, progress))
    progress_text = f"{total_spent:,} / {next_threshold:,}"
    return label, progress, progress_text


def _overall_rating(bat_level: int, bowl_level: int) -> int:
    return max(int(bat_level or 0), int(bowl_level or 0))


def _rarity_from_overall(overall: int) -> str:
    if overall >= 95:
        return "Legendary"
    if overall >= 85:
        return "Epic"
    if overall >= 75:
        return "Rare"
    return "Common"


def _role_icon(role: str) -> str:
    role_key = str(role or "").strip().lower()
    if role_key == "batsman":
        return "🏏"
    if role_key == "bowler":
        return "🎯"
    if role_key == "allrounder":
        return "⚡"
    if role_key in {"wk", "wicketkeeper", "wicket-keeper", "wicket keeper"}:
        return "🧤"
    return "👤"


def _resolve_card_type(player: dict) -> str:
    role = str(player.get("role") or "").strip().lower()
    if role == "bowler":
        return "ball"
    if role in ("batsman", "wk", "wicketkeeper", "wicket-keeper", "wicket keeper"):
        return "bat"
    if role == "allrounder":
        bat_level = int(player.get("bat_level") or 0)
        bowl_level = int(player.get("bowl_level") or 0)
        return "ball" if bowl_level > bat_level else "bat"
    return "bat"


def _squad_player_ids(squad: object) -> set[int]:
    ids: set[int] = set()
    if not squad:
        return ids
    if isinstance(squad, list):
        for item in squad:
            if isinstance(item, dict):
                pid = item.get("player_id") or item.get("id")
            else:
                pid = item
            try:
                ids.add(int(pid))
            except (TypeError, ValueError):
                continue
    return ids


async def ensure_user(viewer: TelegramViewer) -> dict:
    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET
            username = COALESCE(EXCLUDED.username, users.username),
            first_name = COALESCE(EXCLUDED.first_name, users.first_name),
            last_seen_at = NOW();
        """,
        viewer.id,
        viewer.username,
        viewer.first_name,
    )
    row = await fetchrow("SELECT * FROM users WHERE user_id = $1;", viewer.id)
    return dict(row) if row else {
        "user_id": viewer.id,
        "username": viewer.username,
        "first_name": viewer.first_name,
        "balance": 0,
        "rubies": 0,
        "total_spent": 0,
    }


async def get_daily_reward_state(user_id: int) -> RewardState:
    row = await fetchrow("SELECT * FROM daily_rewards WHERE user_id = $1;", user_id)
    if not row:
        return RewardState()

    next_claim = row["next_claim_at"]
    now = datetime.now(timezone.utc)

    if next_claim is not None:
        next_claim_aware = next_claim.replace(tzinfo=timezone.utc)
        if next_claim_aware > now:
            seconds_until = int((next_claim_aware - now).total_seconds())
            return RewardState(
                streak=int(row["streak"] or 0),
                total_claimed=int(row["total_claimed"] or 0),
                available=False,
                seconds_until_available=max(0, seconds_until),
            )

    return RewardState(
        streak=int(row["streak"] or 0),
        total_claimed=int(row["total_claimed"] or 0),
        available=True,
        seconds_until_available=0,
    )


async def build_home_response(viewer: TelegramViewer) -> HomeResponse:
    user = await ensure_user(viewer)
    photo_url = None
    try:
        photo_url = await get_profile_photo_url(viewer.id)
    except Exception as exc:
        print(f"[miniapp_backend] profile photo lookup failed: {exc!r}")

    total_members = int(await fetchval("SELECT COUNT(*) FROM users;") or 0)
    total_players = int(await fetchval("SELECT COUNT(*) FROM players;") or 0)
    total_matches = int(await fetchval("SELECT COUNT(*) FROM matches;") or 0)
    active_matches = int(await fetchval("SELECT COUNT(*) FROM play_matches WHERE status NOT IN ('ended', 'finished', 'complete');") or 0)
    active_users = int(await fetchval("SELECT COUNT(*) FROM users WHERE last_seen_at >= NOW() - INTERVAL '7 days';") or 0)

    stats_row = await fetchrow(
        """
        SELECT
            COALESCE(SUM(matches), 0) AS matches_played,
            COALESCE(SUM(wins), 0) AS matches_won
        FROM player_stats;
        """
    )
    matches_played = int(stats_row["matches_played"] if stats_row else 0 or 0)
    matches_won = int(stats_row["matches_won"] if stats_row else 0 or 0)
    win_percentage = round((matches_won / matches_played) * 100, 2) if matches_played else 0.0

    total_spent = int(user.get("total_spent") or 0)
    league_label, progress_percent, progress_text = _league_from_spent(total_spent)
    reward_state = await get_daily_reward_state(viewer.id)

    return HomeResponse(
        profile=Profile(
            id=viewer.id,
            display_name=_display_name(viewer),
            first_name=viewer.first_name,
            username=viewer.username,
            photo_url=photo_url,
        ),
        wallet=Wallet(
            coins=int(user.get("balance") or 0),
            rubies=int(user.get("rubies") or 0),
            total_spent=total_spent,
        ),
        league=League(
            label=league_label,
            progress_percent=progress_percent,
            progress_text=progress_text,
        ),
        stats=Stats(
            total_members=total_members,
            total_players=total_players,
            total_matches=total_matches,
            active_matches=active_matches,
            active_users=active_users,
            matches_played=matches_played,
            matches_won=matches_won,
            win_percentage=win_percentage,
        ),
        daily_reward=reward_state,
        banner_title="Daily Rewards",
        banner_subtitle="Collect bonuses, boost your balance, and keep the streak warm.",
        primary_cta="View All Rewards",
    )


async def _card_image_url(player: dict) -> str | None:
    player_id = int(player.get("player_id") or 0)
    if not player_id:
        return None

    custom = await get_player_card_image(player_id)
    if custom and custom.get("file_id"):
        return await get_file_url(custom["file_id"])

    card_type = _resolve_card_type(player)
    template = await get_card_template_image(card_type)
    if template and template.get("file_id"):
        return await get_file_url(template["file_id"])

    return None


async def build_player_summary(player: dict, owned: bool = False) -> PlayerSummary:
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)
    overall = _overall_rating(bat_level, bowl_level)
    role = str(player.get("role") or "Unknown")
    return PlayerSummary(
        player_id=int(player.get("player_id") or 0),
        name=str(player.get("name") or "Unknown"),
        country=player.get("country"),
        role=role,
        role_icon=_role_icon(role),
        bat_level=bat_level,
        bowl_level=bowl_level,
        overall=overall,
        rarity=get_rarity(overall),
        batting_style=batting_style_text(player.get("batting_hand")),
        bowling_style=bowling_style_text(player.get("bowling_hand")),
        buy_price=int(get_price(overall)[0]),
        owned=owned,
        card_image_url=await _card_image_url(player),
    )


async def build_player_search_response(viewer: TelegramViewer, query: str, limit: int = 10) -> PlayerSearchResponse:
    rows = await search_players(query, limit=limit)
    squad_row = await fetchrow("SELECT squad FROM team_squads WHERE user_id = $1;", viewer.id)
    owned_ids = _squad_player_ids(squad_row["squad"] if squad_row else None)
    results = [await build_player_summary(row, owned=int(row.get("player_id") or 0) in owned_ids) for row in rows]
    return PlayerSearchResponse(query=query, count=len(results), results=results)


async def build_player_detail_response(viewer: TelegramViewer, player_name: str) -> PlayerDetail:
    player = await get_player(player_name)
    if not player:
        raise ValueError("Player not found")

    squad_row = await fetchrow("SELECT squad FROM team_squads WHERE user_id = $1;", viewer.id)
    owned_ids = _squad_player_ids(squad_row["squad"] if squad_row else None)
    owned = int(player.get("player_id") or 0) in owned_ids
    summary = await build_player_summary(player, owned=owned)
    card_type = _resolve_card_type(player)

    return PlayerDetail(
        **summary.dict(),
        description=f"{summary.role} • {summary.batting_style} • {summary.bowling_style}",
        ball_level=int(player.get("bowl_level") or 0),
        card_type=card_type,
    )
