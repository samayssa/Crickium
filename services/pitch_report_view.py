from services.match_score_view import Blocks, esc

PITCH_INFO = {
    "green": {
        "emoji": "🌿", "title": "Green",
        "tagline": "Grass cover, seam movement and early wickets.",
        "story": "A fresh surface with live grass that grips the seamers. The new ball seams and bounces, so openers have to earn their runs. Pacers rule the first six overs, spinners are a sideshow, and totals stay modest. Captains who win the toss often bowl first.",
        "favours": "Pace bowlers", "tip": "Bowl first, attack with the new ball",
    },
    "dry": {
        "emoji": "🏜️", "title": "Dry",
        "tagline": "Cracking surface that slows down and starts to turn.",
        "story": "Baked and brittle, a dry pitch loses pace as the game goes on. Strokeplay is hard early because the ball sticks, and spinners come into play from the middle overs. Wickets in hand matter more than powerplay fireworks.",
        "favours": "Spinners and patient batters", "tip": "Post a safe total, then squeeze with spin",
    },
    "dusty": {
        "emoji": "🌪️", "title": "Dusty",
        "tagline": "Loose top layer, sharp turn and variable bounce.",
        "story": "A dusty track breaks up quickly and rewards wrist-spin and off-spin alike. Bounce turns unpredictable, byes are common and big hits are rare. Whoever bats last faces the toughest conditions, so runs on the board are worth gold.",
        "favours": "Spinners", "tip": "Bat first and defend, spin does the rest",
    },
    "flat": {
        "emoji": "🛣️", "title": "Flat",
        "tagline": "Batting paradise with true bounce and little help for bowlers.",
        "story": "Nothing for the bowlers here. Even bounce and pace let batters hit through the line from ball one, so 200 is gettable and chases are routinely won. Bowlers survive on variations, yorkers and well-placed fields.",
        "favours": "Batters", "tip": "Chase if you can, never feel safe with fewer than 190",
    },
    "hard": {
        "emoji": "🪨", "title": "Hard",
        "tagline": "Hard, true surface with pace and carry.",
        "story": "A firm pitch gives consistent bounce and plenty of carry, so timing is rewarded and boundaries flow. Quicks get some lift, but batters who commit to the shot are rarely punished. Expect healthy scores and lively cricket.",
        "favours": "Stroke-making batters, quick bowlers with bounce", "tip": "Take on the bowling early, back your shots",
    },
    "even": {
        "emoji": "⚖️", "title": "Even",
        "tagline": "Balanced surface with something for everyone.",
        "story": "No obvious bias: pace, bounce and turn are all moderate and consistent. The contest is decided by skill, planning and execution rather than conditions. A neutral pitch where the better XI usually wins.",
        "favours": "Balanced sides", "tip": "Pick on merit and trust your game plan",
    },
    "bouncy": {
        "emoji": "🏀", "title": "Bouncy",
        "tagline": "Steep bounce and carry, tough on the front foot.",
        "story": "Extra bounce makes driving risky and pulls and cuts the main scoring shots. Fast bowlers hunt wickets with short and hostile lines, and edges carry to the keeper and slips. Dot balls build pressure, so aggressive overs often end in wickets.",
        "favours": "Fast bowlers", "tip": "Play the short ball well or lose wickets in clusters",
    },
    "slow": {
        "emoji": "🐢", "title": "Slow",
        "tagline": "Low, slow surface where timing is hard to find.",
        "story": "The ball skids on slowly and stops on the bat, so big shots are tough. Cutters and spinners choke the scoring, and low totals are normal. Rotating the strike and running hard beats waiting for boundaries.",
        "favours": "Spinners and slower-ball bowlers", "tip": "Bat deep, build innings through singles and twos",
    },
}


def _avg(total: int, count: int, digits: int = 1) -> str:
    return f"{total / count:.{digits}f}" if count else "—"


def _pct(part: int, whole: int) -> str:
    return f"{part * 100 / whole:.0f}%" if whole else "—"


def _innings_lines(row: dict) -> list[str]:
    n, d = int(row.get("n") or 0), int(row.get("detail_n") or 0)
    if not n:
        return ["No matches recorded yet."]
    lines = [
        f"📈 Average score: <b>{_avg(row['runs'], n)}</b>",
        f"🎯 Average wickets: <b>{_avg(row['wickets'], n)}</b>",
    ]
    if d:
        lines += [
            f"⚡ Pace wickets: <b>{_avg(row['pace_wkts'], d)}</b> · 🌀 Spin wickets: <b>{_avg(row['spin_wkts'], d)}</b>",
            f"🔰 Powerplay: <b>{_avg(row['pp_runs'], d)}</b> runs · <b>{_avg(row['pp_wkts'], d)}</b> wkts",
            f"⚔️ Middle overs: <b>{_avg(row['mid_runs'], d)}</b> runs · <b>{_avg(row['mid_wkts'], d)}</b> wkts",
            f"🔥 Death overs: <b>{_avg(row['death_runs'], d)}</b> runs · <b>{_avg(row['death_wkts'], d)}</b> wkts",
        ]
    return lines


def build_pitch_view(pitch: str, innings: dict, results: dict) -> Blocks:
    info = PITCH_INFO[pitch]
    view = Blocks()
    view.h1(f"{info['emoji']} {info['title'].upper()} PITCH REPORT")
    view.p(f"<i>{esc(info['tagline'])}</i>")
    view.hr()
    view.h2("📖 Pitch Behaviour")
    view.p(esc(info["story"]))
    view.p(f"✅ <b>Favours:</b> {esc(info['favours'])}")
    view.p(f"💡 <b>Strategy:</b> {esc(info['tip'])}")
    view.hr()
    first, second = innings.get(1, {}), innings.get(2, {})
    view.h2("📊 Averages")
    view.p(
        f"🥇 First innings <b>{_avg(first.get('runs', 0), first.get('n', 0))}</b>/{_avg(first.get('wickets', 0), first.get('n', 0))} · "
        f"🥈 Second innings <b>{_avg(second.get('runs', 0), second.get('n', 0))}</b>/{_avg(second.get('wickets', 0), second.get('n', 0))}"
    )
    view.details("🥇 First Innings Breakdown", _innings_lines(first))
    view.details("🥈 Second Innings Breakdown", _innings_lines(second))
    view.hr()
    decided = int(results.get("defend_wins") or 0) + int(results.get("chase_wins") or 0)
    defend, chase = int(results.get("defend_wins") or 0), int(results.get("chase_wins") or 0)
    view.h2("🏆 Win Record")
    if decided:
        view.p(f"🛡️ <b>Defending teams</b> · Won {defend} ({_pct(defend, decided)}) · Lost {chase} ({_pct(chase, decided)})")
        view.p(f"🎯 <b>Chasing teams</b> · Won {chase} ({_pct(chase, decided)}) · Lost {defend} ({_pct(defend, decided)})")
    else:
        view.p("No completed results recorded yet.")
    matches = max(int(first.get("n") or 0), int(second.get("n") or 0))
    view.footer(f"Based on {matches} completed match{'es' if matches != 1 else ''} on this pitch")
    return view


def build_pitch_menu() -> Blocks:
    view = Blocks()
    view.h1("🏟️ PITCH REPORT")
    view.p("Pick a pitch to see its behaviour and match records.")
    for key, info in PITCH_INFO.items():
        view.details(f"{info['emoji']} {info['title']}", [f"{esc(info['tagline'])}", f"Use: <b>/pitchreport {key}</b>"])
    view.footer("Example: /pitchreport green")
    return view
