import traceback

from app import app

COMMANDS = [
    ("start", "Start Crickium and open menu"),
    ("app", "Launch the Crickium mini app"),
    ("menu", "Open the main bot menu"),
    ("debut", "Claim your debut player card"),
    ("daily", "Claim your daily login rewards"),
    ("cquest", "View daily quests and rewards"),
    ("claim", "Claim one player card hourly"),
    ("play", "Play with your own XI"),
    ("playint", "Play international T20 matches"),
    ("playipl", "Play IPL cricket matches"),
    ("playwpl", "Play Women's Premier League matches"),
    ("playwbbl", "Play Women's Big Bash League matches"),
    ("playbbl", "Play Big Bash League matches"),
    ("playilt", "Play International League T20 matches"),
    ("playso", "Play a one-over Super Over"),
    ("playai", "Play a quick match against the AI"),
    ("h2h", "Compare head-to-head cricket records"),
    ("matchscore", "Show the live score of your running match"),
    ("pitchreport", "View pitch behaviour and match records"),
    ("pause", "Pause your running game session"),
    ("resume", "Resume your stuck game session"),
    ("pshop", "Browse the player card shop"),
    ("packs", "Browse collectible card packs"),
    ("mypacks", "View your unopened card packs"),
    ("openpack", "Open an unopened card pack"),
    ("packcards", "Review pulled cards awaiting a decision"),
    ("player", "View player card details"),
    ("pxl", "View your current Playing XI"),
    ("squad", "View your complete player squad"),
    ("team", "View any supported team roster"),
    ("mybank", "Check your coins and rubies"),
    ("buildxl", "Build your strongest playing XI"),
    ("profile", "View your Crickium profile"),
    ("buy", "Buy player cards with coins"),
    ("buypack", "Purchase card packs with sigils"),
    ("sell", "Sell player cards for coins"),
    ("evolve", "Evolve a card into a higher edition"),
    ("trade", "Trade player cards with users"),
    ("give", "Send coins to another user"),
    ("refer", "Get your personal referral link"),
    ("myreferrals", "Track referrals and earned rewards"),
    ("bstatus", "Check the bot's connection status"),
    ("plstats", "View detailed player statistics"),
    ("teamlogo", "Set your team logo"),
    ("myteam", "View your auction team"),
    ("cobidder", "Manage team co-bidders"),
    ("coowner", "Manage team co-owners"),
    ("teamname", "Rename your tournament team"),
    ("endtour", "End your tournament"),
    ("setpurse", "Set team auction budgets"),
    ("setsteps", "Set bidding increments"),
    ("aucrules", "View auction rules"),
    ("setroles", "Set player role limits"),
    ("setsquad", "Set squad size limits"),
    ("setoverseas", "Set overseas player limits"),
    ("prize", "Set tournament prizes"),
    ("prizesplit", "Set prize distribution"),
    ("teamown", "Manage team owners"),
    ("auction", "View live auction"),
    ("bid", "Place an auction bid"),
    ("aucpause", "Pause the auction"),
    ("aucresume", "Resume the auction"),
    ("tplay", "Join your scheduled match"),
    ("tour", "View tournament standings"),
    ("tourstats", "View tournament statistics"),
    ("tsched", "View tournament schedule"),
    ("achievements", "View your achievements"),
    ("forfeitall", "Forfeit all active games"),
    ("endgame", "End your active match session"),
    ("exitgame", "Exit your current game session"),
    ("changexl", "Move squad players into XI"),
    ("teamcap", "Set your team's captain"),
    ("id", "Find user and emoji IDs"),
    ("setteam", "Set your custom team name"),
    ("setlogo", "Set your team logo emoji"),
    ("bug", "Report a problem to developers"),
    ("feedback", "Share suggestions with the developers"),
    ("cshop", "Browse available coin shop items"),
    ("cbuy", "Buy items using your coins"),
    ("ushop", "Browse available player upgrades"),
    ("ubuy", "Buy player upgrade items"),
    ("ulevelup", "Level up your equipped upgrades"),
    ("inventory", "View your upgrade item inventory"),
    ("equip", "Equip upgrades onto your players"),
    ("equiplist", "View upgrades equipped on players"),
    ("unequip", "Remove a player's equipped upgrade"),
]

SCOPES = (
    {"type": "default"},
    {"type": "all_private_chats"},
    {"type": "all_group_chats"},
    {"type": "all_chat_administrators"},
)


def command_payload():
    return [{"command": name, "description": text} for name, text in COMMANDS]


async def sync_bot_commands():
    payload = command_payload()
    done = 0
    for scope in SCOPES:
        try:
            await app._call_bot_api("setMyCommands", json_payload={"commands": payload, "scope": scope})
            done += 1
        except Exception as exc:
            print(f"[bot_commands] {scope['type']} failed: {exc!r}")
    print(f"[bot_commands] command list synced for {done}/{len(SCOPES)} scopes")
    return done
