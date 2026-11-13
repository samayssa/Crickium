"""Crickium commentary - phrase banks.

Pure data. `engines/commentary_studio.py` composes a line from these banks for
every ball (opener + delivery + shot/direction + result + optional situation
or cameo), so the number of distinct lines is the PRODUCT of the banks, far
beyond the number of phrases written here.

Slots: {B}/{b} batter full/surname, {W}/{w} bowler full/surname, {F}/{f} fielder,
{K}/{k} keeper, {pos} fielding position, {he}/{his}/{him} pronouns of the batter,
{runs}/{balls} batter score & balls, {SIT} live equation sentence.
"""
from __future__ import annotations

# ------------------------------------------------------------------ ZONES ---
# zone -> (ground direction text, normal fielder position, deep fielder position)
ZONES: dict[str, tuple[str, str, str]] = {
    "tm": ("third man", "third man", "third man"),
    "pt": ("point", "point", "deep point"),
    "cv": ("cover", "cover", "deep cover"),
    "mo": ("mid-off", "mid-off", "long-off"),
    "st": ("straight down the ground", "the bowler", "long-on"),
    "mn": ("mid-on", "mid-on", "long-on"),
    "mw": ("mid-wicket", "mid-wicket", "deep mid-wicket"),
    "sq": ("square leg", "square leg", "deep square leg"),
    "fl": ("fine leg", "short fine leg", "deep fine leg"),
}
ZONE_W_FOUR = {"tm": 6, "pt": 9, "cv": 15, "mo": 8, "st": 10, "mn": 8, "mw": 14, "sq": 10, "fl": 8}
ZONE_W_SIX = {"cv": 8, "mo": 9, "st": 22, "mn": 12, "mw": 24, "sq": 10, "fl": 3, "pt": 2}
ZONE_W_RUN = {"tm": 4, "pt": 9, "cv": 12, "mo": 10, "st": 4, "mn": 10, "mw": 13, "sq": 10, "fl": 7}
ZONE_W_CATCH = {"tm": 5, "pt": 8, "cv": 11, "mo": 9, "st": 14, "mn": 10, "mw": 14, "sq": 9, "fl": 6}
# delivery tilt: tactic -> {zone: multiplier}
ZONE_TILT = {
    "swinging": {"cv": 1.3, "pt": 1.2, "tm": 1.5, "mo": 1.1},
    "back_of_length": {"mw": 1.4, "sq": 1.3, "fl": 1.3, "st": 0.8},
    "pace_up": {"st": 1.25, "mn": 1.1, "mo": 1.1},
    "variation": {"st": 1.2, "mn": 1.2},
    "defensive": {"mo": 1.1, "mn": 1.1},
    "spin": {"mw": 1.35, "sq": 1.25, "mn": 1.15, "tm": 0.5, "fl": 0.7},
}

# --------------------------------------------------------------- DELIVERIES ---
DELIVERY_PACE = {
    "defensive": ["Good length outside off from {w}", "{w} tucks it in on a good length", "Full and straight from {w}",
                  "{w} hits a good length on off stump", "Tight line from {w}, on a good length", "{w} keeps it in the channel"],
    "swinging": ["Swinging in from {w}", "{w} gets some late swing", "Shaping away from {w}", "{w} swings it back in late",
                 "Nice outswinger from {w}", "{w} finds some movement in the air"],
    "pace_up": ["Real pace from {w}", "{w} hurries it on", "Express speed from {w}", "{w} sends down a rapid one",
                "Quick from {w}, hitting the deck", "{w} cranks up the pace"],
    "back_of_length": ["Back of a length from {w}", "{w} digs it in short of a length", "A short one from {w}", "{w} drags it back of a length",
                       "Banged in by {w}", "Short and rising from {w}"],
    "variation": ["A slower ball from {w}", "{w} changes the pace with a cutter", "Wide yorker attempt from {w}", "{w} mixes it up with a knuckle ball",
                  "Slower bouncer from {w}", "{w} tries the yorker"],
}
DELIVERY_SPIN = {
    "off_break": ["{w} tosses it up on off stump", "Flighted off-break from {w}", "{w} turns one in, off the pitch"],
    "doosra": ["{w} bowls the doosra", "The doosra from {w}, turning away", "{w} sends down a doosra"],
    "arm_ball": ["The arm ball from {w}, skidding on", "{w} slides the arm ball through", "{w} gets the arm ball to skid straight"],
    "carrom_ball": ["The carrom ball from {w}", "{w} flicks out a carrom ball", "A carrom ball, nicely disguised by {w}"],
    "top_spin": ["Top-spinner from {w}, dipping on the batter", "{w} gets the top-spin to bounce", "Overspin from {w}, with extra bounce"],
    "leg_breaker": ["{w} lands it and spins it big", "Leg-break from {w}, ripping across", "{w} turns it sharply away"],
    "top_spinner": ["Top-spinner from {w}", "{w} gets over the ball, it dips and bounces", "{w} sends down a top-spinner"],
    "slider": ["The slider from {w}, skidding low", "{w} pushes the slider through", "{w} fires it in flat and quick"],
    "flipper": ["The flipper from {w}, staying low", "{w} bowls the flipper", "{w} squeezes out a flipper"],
    "googly_ball": ["{w} bowls the googly", "The wrong'un from {w}", "{w} disguises the googly well"],
}
DELIVERY_GENERIC = {"p": ["{w} runs in and delivers", "{w} bowls it on a good length", "{w} hits the pitch hard"],
                    "s": ["{w} tosses it up", "{w} flights it on off stump", "{w} bowls a turning delivery"]}

# -------------------------------------------------------------------- SHOTS ---
SHOT_FOUR = {
    "tm": ["steers it past the slips down to third man", "guides it fine, all the way to the third man fence", "opens the face and glides it to third man",
           "edges it, and it races through the slip cordon"],
    "pt": ["cuts it hard past point", "slashes it over the point fielder and into the gap", "punches it square on the off side, past point",
           "carves it away behind point"],
    "cv": ["drives it through cover", "leans into a cover drive, pure timing", "hits it on the up through the covers", "caresses it past cover",
           "lofts it over the cover fielder"],
    "mo": ["drives it past mid-off", "punches it through the gap at mid-off", "whips a lofted drive over mid-off", "creams it wide of mid-off"],
    "st": ["drives it straight down the ground", "straight-drives it past the bowler", "gets under it and flicks it down the ground",
           "times it beautifully back past the bowler"],
    "mn": ["drives it through mid-on", "clips it off the pads past mid-on", "pushes it firmly wide of mid-on", "heaves it past mid-on"],
    "mw": ["pulls it through mid-wicket", "slog-sweeps it into the mid-wicket gap", "whips it off the stumps to mid-wicket", "smashes a pull past mid-wicket",
           "hits it across the line through mid-wicket"],
    "sq": ["sweeps it fine past square leg", "pulls it hard behind square", "paddles it round the corner at square leg", "cuts loose with a slog sweep square on the leg side"],
    "fl": ["glances it fine down to the fine leg fence", "flicks it off the hip past fine leg", "tickles it fine, and it races away",
           "ramps it over the keeper and down to fine leg"],
}
SHOT_SIX = {
    "pt": ["slices it high over point", "upper-cuts it over the point boundary"],
    "cv": ["launches it over the covers", "lofts it high over extra cover", "hammers it over cover for a maximum"],
    "mo": ["clears long-off with a huge hit", "lofts it over long-off", "swings through the line and sends it over long-off"],
    "st": ["hits it straight back over the bowler's head", "launches it down the ground", "clears the straight boundary, a massive hit",
           "gets under it and it sails over long-on"],
    "mn": ["heaves it over long-on", "swings it high over mid-on", "muscles it over the long-on rope"],
    "mw": ["slog-sweeps it over deep mid-wicket", "pulls it into the stands at mid-wicket", "smacks it over the mid-wicket boundary",
           "swings it flat and hard over deep mid-wicket"],
    "sq": ["pulls it over square leg", "sweeps it high over deep square leg", "hits it into the crowd beyond square leg"],
    "fl": ["ramps it over the fine leg boundary", "scoops it over fine leg"],
}
SHOT_SOFT = {   # for 1/2/3 runs
    "tm": ["taps it fine to third man", "dabs it down to third man", "guides it to the third man area"],
    "pt": ["dabs it to point", "cuts it square to point", "steers it behind point", "slides it into the gap at point"],
    "cv": ["places it into the gap at cover", "punches it to cover", "drives it firmly to cover", "pushes it into the cover gap"],
    "mo": ["pushes it to mid-off", "drives it down to mid-off", "nudges it wide of mid-off"],
    "st": ["drives it back down the ground", "pushes it straight past the bowler", "works it down the ground"],
    "mn": ["clips it to mid-on", "works it to the on side, near mid-on", "pushes it wide of mid-on"],
    "mw": ["works it into the gap at mid-wicket", "flicks it to mid-wicket", "pulls it into the mid-wicket gap", "nudges it into the on side"],
    "sq": ["tucks it away to square leg", "glances it to square leg", "sweeps it to square leg", "whips it off the hip to square leg"],
    "fl": ["tickles it fine on the leg side", "glances it down to fine leg", "flicks it fine"],
}
RUN_1 = ["and they take a single", "and picks up one", "and they scamper through for one", "for a quick single", "and they cross for one",
         "and they steal a single", "and it is one run", "and they sneak a single"]
RUN_2 = ["and they come back for two", "and they run hard for two", "and gets two", "for a brace", "and they sprint back for the second",
         "and they push for two", "for a couple"]
RUN_3 = ["and they run three, superb running", "and it's three, after a long chase", "and they race through for three"]

DOT_BEATEN = ["beaten outside off", "beaten all ends up", "plays and misses", "swishes and misses", "beaten by the movement", "has a wild swing and misses"]
DOT_DEFENDED = ["defends it solidly back to the bowler", "blocks it on the front foot", "dead-bats it into the ground", "plays it carefully off the back foot",
                "pushes it to the off side, no run", "gets behind it and defends"]
DOT_LEFT = ["leaves it alone, outside off", "shoulders arms to one that moves away", "lets it go through to the keeper", "watches it go by outside the off stump"]
DOT_FIELDED = ["drives it, but {f} at {pos} stops it", "pushes it to {pos}, where {f} fields cleanly", "hits it hard, but {f} dives at {pos} and cuts it off",
               "clips it away, and {f} at {pos} is on it in a flash", "works it, but {f} fields at {pos}"]
DOT_PAD = ["is hit on the pad, and the umpire is unmoved", "gets struck on the pads, a big appeal but not given", "is rapped on the thigh pad, no run",
           "takes it on the body, and there is no run"]
DOT_EDGE = ["gets a thick edge that falls short of the slip", "inside edges it onto the pads", "gets a bottom-edge that rolls to the keeper", "squirts it off the edge, just short"]

# ------------------------------------------------------------------ OPENERS ---
OPEN_DOT = {"calm": ["Dot ball.", "No run.", "Nothing there.", "Good ball.", "Tight.", "", "", "", "Dot.", "Defended."],
            "tense": ["Pressure.", "Dot ball.", "Another dot.", "No run.", "Tight bowling.", "Nothing doing.", "", ""],
            "thriller": ["DOT!", "No run!", "Nothing!", "A precious dot!", "Squeezed!", "Tight!", "Dot ball!", "Not a run!"]}
OPEN_SINGLE = {"calm": ["Single.", "One run.", "A single.", "", "", "", "", "Just the one."],
               "tense": ["Single.", "One run.", "A single.", "", "Quick single."],
               "thriller": ["Single!", "One run!", "A single!", "They get one!", "Just a single!", "They scamper through!"]}
OPEN_DOUBLE = {"calm": ["Two runs.", "A couple.", "Two!", "", "", "Good running."],
               "tense": ["Two runs.", "Two!", "A couple.", "Good running.", ""],
               "thriller": ["Two runs!", "They run hard!", "Two!", "What running!", "Two precious runs!"]}
OPEN_TRIPLE = {"calm": ["Three runs!", "Superb running!", "Three!"], "tense": ["Three runs!", "Three!", "Excellent running!"],
               "thriller": ["Three runs!", "They sprint three!", "What running, three!"]}
OPEN_FOUR = {"calm": ["FOUR!", "Four!", "Boundary!", "FOUR runs!", "Lovely shot, four!", "Brilliant, four!"],
             "tense": ["FOUR!", "Four!", "A big boundary!", "That's a boundary!", "FOUR! Vital runs!"],
             "thriller": ["FOUR!", "BOUNDARY!", "FOUR! Huge!", "That is a massive boundary!", "FOUR! The pressure is released!"]}
OPEN_SIX = {"calm": ["SIX!", "That's a SIX!", "Maximum!", "SIX runs!", "Up and over, SIX!"],
            "tense": ["SIX!", "Maximum!", "SIX! Massive!", "That's huge, SIX!"],
            "thriller": ["SIX!", "SIX! What a hit!", "MAXIMUM!", "SIX! Enormous!", "SIX! Can you believe it?"]}
OPEN_FIVE = {"calm": ["Five runs!", "Five!"], "tense": ["Five runs!", "Five!"], "thriller": ["Five runs!", "Five!"]}
OPEN_FH_FOUR = ["FREE HIT, and it is four!", "Four off the free hit!", "FREE HIT! Boundary!", "The free hit goes for four!"]
OPEN_FH_SIX = ["FREE HIT, and it is SIX!", "SIX off the free hit!", "FREE HIT! Maximum!", "The free hit is smashed for six!"]
OPEN_FH_DOT = ["FREE HIT, and no run.", "Dot ball on the free hit.", "No run off the free hit."]
OPEN_FH_RUN = ["FREE HIT.", "Free hit.", "Off the free hit,"]

# --------------------------------------------------------------- RESULT TAILS ---
TAIL_FOUR = ["It races away to the rope.", "No chance for the fielder.", "Pierces the gap and runs away.", "Pure timing.", "All the way to the fence.",
             "The ball just beats the fielder.", "It sped to the boundary.", "Not even close to stopping it."]
TAIL_SIX = ["It lands well into the stands.", "That has cleared the rope by a distance.", "The crowd is on its feet.", "What a strike.", "Gone, right out of the middle of the bat.",
            "That is out of the ground."]
TAIL_SINGLE = ["Strike rotated.", "The strike changes.", "Keeps the scoreboard ticking.", "Sensible cricket.", "Good, busy batting.", "{n} takes strike next.",
               "They cross, and {n} is on strike.", "The partnership ticks along."]
TAIL_DOUBLE = ["Excellent awareness.", "Smart running.", "They turn sharply.", "Good, busy batting.", "Two more on the board.", "They give the fielder no chance."]
TAIL_DOT = ["Pressure builds.", "The bowler is on top here.", "Nothing off that one.", "Good, tidy bowling.", "The batter will want a release."]
TAIL_THRILLER = {"single": ["Every run counts now!", "They scamper through, hearts in mouths!", "One run closer, but will it be enough?", "The tension grows!"],
                 "double": ["Two precious runs!", "What running!", "Every run is gold now!", "They get back, and it is two!"],
                 "dot": ["The crowd holds its breath!", "Every dot hurts now!", "The pressure is immense!", "The tension is almost too much!", "No breathing space at all!"],
                 "four": ["That could be the turning point!", "The crowd erupts!", "The equation changes in a flash!"],
                 "six": ["Unbelievable scenes!", "The dugout is up on its feet!", "That could change everything!"]}

# --------------------------------------------------------------------- CAMEOS ---
CAMEO_FOUR = ["{f} gives chase from {pos}, but it is hopeless.", "{f} dives at {pos} but only gets a fingertip to it.", "{f} at {pos} has no chance.",
              "{f} is left chasing shadows at {pos}.", "No chance for {f} at {pos}, it races away."]
CAMEO_SIX = ["{f} at {pos} can only watch it sail over.", "{f} turns and watches it fly over {pos}.", "It lands well beyond {f} at {pos}.",
             "{f} can only look up at {pos} as it disappears."]
CAMEO_SINGLE = ["{f} fields at {pos}, and they take one.", "{f} fires it in from {pos}, but the batters are home.", "Good work from {f} at {pos}, restricting them to one.",
                "{f} at {pos} cuts it off, and they settle for one."]
CAMEO_DOUBLE = ["{f} chases it down at {pos}, but they get two.", "A good throw from {f} at {pos}, but they get back for two.",
                "{f} slides at {pos} to save the boundary, and they settle for two.", "{f} at {pos} saves the boundary, and it is two."]

# --------------------------------------------------------------------- EXTRAS ---
WIDE = ["WIDE! {W} strays down the leg side, and the umpire signals it.", "Wide called. {W} loses the line outside off.", "That is a wide from {W}, well outside the tramlines.",
        "{W} sprays it, and the umpire stretches the arms out. Wide.", "Too wide from {W}, and the keeper {k} has to move for it.", "Wide ball! {W} is struggling for the right line."]
WIDE_FH = ["Wide on the free hit! {W} cannot find the line.", "A wide, and the free hit stays alive. {W} is wayward.", "Wide again! {W} is struggling here."]
NO_BALL = ["NO BALL! {W} oversteps, and a free hit is coming!", "No ball! {W} has crossed the line. FREE HIT next!", "{W} drags the front foot over the line, no ball, and a free hit follows!",
           "The umpire calls a no-ball against {W}. FREE HIT coming up!"]
NO_BALL_FH = ["No ball again! {W} oversteps once more, and it is another free hit!", "Another no-ball from {W}, the free hit continues!"]
BYE = ["BYE! The ball beats {b} and the keeper {k}, and they steal a bye.", "Bye! It goes past {k} and they run one.", "Byes! {W} beats the batter and the keeper, a run is taken."]
LEG_BYE = ["Leg bye! The ball hits {b} on the pad and rolls away, and they take a leg bye.", "Leg bye! It deflects off the body and they sneak a run.",
           "A leg bye as the ball clips {b} on the thigh pad and runs away."]

# -------------------------------------------------------------------- WICKETS ---
OPEN_WICKET = {"calm": ["WICKET!", "OUT!", "GONE!", "That's out!", "A big wicket!", "And that's the end!", "Got him!", "Breakthrough!"],
               "tense": ["WICKET!", "OUT!", "BIG WICKET!", "GONE!", "That's a huge wicket!", "A crucial breakthrough!"],
               "thriller": ["OH, THAT'S OUT!", "WHAT A WICKET!", "A HUGE BREAKTHROUGH!", "GONE!!", "THE GAME TURNS!", "BIG, BIG WICKET!", "DISASTER!", "OUT! UNBELIEVABLE!"]}
BOWLED = ["{W} sends the stumps flying, {B} is bowled!", "{B} is clean bowled by {W}! The stumps are shattered.", "{B} is beaten all ends up, and {W} hits the top of off stump.",
          "{B} plays inside the line, and {W} knocks over the off stump.", "{B} chops it on to the stumps off {W}.", "Timber! {W} bowls {B} through the gate.",
          "{B} is bowled around the legs by {W}.", "{W} gets one to nip back, and {B} is bowled!"]
LBW = ["{B} is trapped in front by {W}, and the finger goes up! LBW.", "LBW! {B} is hit on the pad, plumb, and the umpire raises the finger. Wicket for {W}.",
       "{B} misses the flick, and it hits the pad in front of middle stump, out LBW to {W}.", "{W} raps {B} on the pad, and it is hitting the stumps, LBW!",
       "A big appeal from {W}, and {B} is given out LBW."]
LBW_SPIN = ["{B} is struck on the pad playing for the turn, LBW to {W}.", "{W} gets it to slide on with the arm, {B} is trapped LBW."]
CAUGHT = ["{B} {air}, and {F} at {pos} takes a good catch. {W} gets the wicket.", "{B} {air}, and it is in the hands of {F} at {pos}. Wicket for {W}!",
          "{B} {air} towards {pos}, and {F} pouches it. {W} has the breakthrough.", "{W} strikes! {B} {air}, and {F} at {pos} makes no mistake.",
          "{B} {air}, {F} settles under it at {pos}, and takes it safely. Out, and {W} is pleased!", "{F} at {pos} takes it comfortably as {B} {air}. {W} is delighted."]
AIR = ["hits it in the air", "skies it", "mistimes the pull", "finds the fielder with a lofted drive", "slices it up in the air", "miscues the big hit", "top-edges it high",
       "tries to go big and holes out", "mistimes the slog", "chips it straight up"]
CAUGHT_BEHIND = ["{B} gets a thin edge, and {K} takes a sharp catch behind the stumps. {W} gets the wicket.", "Edged and taken! {K} pouches it behind the stumps, {B} is gone. Wicket for {W}.",
                 "{W} gets one to leave {B} late, and {K} takes the catch behind the stumps.", "A feather behind! {K} takes it cleanly, and {B} walks back. {W} strikes.",
                 "{B} fishes outside off, and {K} holds a good catch. {W} has the breakthrough."]
CAUGHT_BOWLED = ["Caught and bowled! {W} dives low and takes a good return catch to dismiss {B}.", "{B} drives it back hard, and {W} holds on to a sharp caught-and-bowled!",
                 "Return catch! {W} stays low and takes it, {B} is out.", "{B} hits it straight back, and {W} snaps up the catch, caught and bowled!"]
STUMPED = ["{B} comes down the track, misses, and {K} whips off the bails. Stumped! Wicket for {W}.", "{B} is beaten in the flight, and {K} is quick behind the stumps. Stumped, and {W} gets the wicket!",
           "{W} draws {B} out of the crease, and {K} does the rest. A smart stumping.", "Stumped! {B} is stranded as {K} breaks the stumps off {W}'s delivery.",
           "{B} dances down, {W} beats the bat, and {K} stumps him in a flash."]
RUN_OUT_DIRECT = ["RUN OUT! {B} is sent back, but too late, and a direct hit from {F} at {pos} breaks the stumps.", "Direct hit! {F} at {pos} hits the stumps, and {B} is well short. Run out!",
                  "A mix-up, and {F} at {pos} hits the stumps with a direct throw. {B} has to go.", "{B} is stranded mid-pitch, and {F} hits the stumps from {pos}. Run out!"]
RUN_OUT_RELAY = ["RUN OUT! {F} at {pos} collects and fires it to {K}, who whips off the bails. {B} is short!", "Relay throw! {F} gets it to {K}, and the bails are off. {B} is run out.",
                 "A mix-up, {F} throws it in, and {K} breaks the stumps at the other end. {B} is run out.", "{F} gathers at {pos}, the throw is collected by {K}, and the stumps are broken. {B} has to go."]
RUN_OUT_RARE = "It's rare, but batsmen get out as run out. Okay, remember."
WK_TAILS = ["{B} departs for {runs} off {balls}.", "A brief stay for {B}, who goes for {runs}.", "{B} walks back for {runs} off {balls} balls.",
            "The innings of {B} ends on {runs} off {balls}."]
WK_DUCK = ["That's a duck for {B}.", "{B} departs for a duck, off {balls} balls."]
WK_GOLDEN = ["A golden duck for {B}! First ball out!", "{B} goes first ball, a golden duck!"]
WK_GOOD = ["{B} made {runs} off {balls}, a useful contribution.", "A solid knock of {runs} from {balls} balls comes to an end."]
WK_BIG = ["A fine innings of {runs} from {balls} balls ends there.", "{B} walks back after a splendid {runs} off {balls}."]
WK_PARTNERSHIP = ["That ends a stand of {p} runs.", "A {p}-run partnership comes to an end.", "The partnership of {p} runs is broken."]
WK_BOWLER = {2: ["{W} has two wickets now.", "A second wicket for {W}."], 3: ["That's three for {W}!", "A third wicket for {W}! A fine spell!"],
             4: ["FOUR wickets for {W}! A fantastic spell!"], 5: ["A FIVE-WICKET HAUL for {W}! Sensational!"]}
HAT_TRICK_BALL = ["HAT-TRICK BALL! {W} has two in two!", "{W} is on a hat-trick! Two in two!"]
HAT_TRICK = ["HAT-TRICK! {W} has taken three in three!", "A HAT-TRICK for {W}! Incredible!"]
ALL_OUT = ["That's the end of the innings, all out for {score}!", "ALL OUT! The innings ends on {score}."]

# ---------------------------------------------------------------- SITUATION ---
SIT_CHASE = ["{needw} needed from {ballsw}.", "{need} off {balls}.", "Need {need} from {balls}.", "{needw} to win from {ballsw}.", "It is {need} off {balls} now.",
             "The equation: {need} off {balls}.", "{ballsw} left, {need} to get.", "The required rate is {rrr}.", "{need} off {balls}, and the required rate is {rrr}.",
             "{need} to win, {ballsw} to go, {wihw} in hand.", "{wihw} in hand, {need} needed from {balls}.", "Still {need} to get from {ballsw}.",
             "The asking rate is {rrr} now.", "{need} runs, {ballsw}, and {wihw} in hand."]
SIT_CHASE_WICKETS = ["Just {wihw} left, and {need} still needed.", "Only {wihw} in hand, and {need} needed from {ballsw}.", "{need} needed off {balls}, and only {wihw} in hand!",
                     "The tail is exposed, with {need} needed off {balls}.", "{need} to win, {wihw} left, {ballsw} to go.", "{need} off {balls}, and {wihw} left. Anything can happen.",
                     "The target is {target}, and with {wihw} left, it is anyone's game."]
SIT_LAST_OVER = ["{needw} needed from {ballsw}, in the final over.", "It all comes down to this, {need} needed from {ballsw}.", "{need} needed off {balls} in the final over.",
                 "Last over of the chase, {needw} to get."]
SIT_LAST_BALL = ["{needw} needed off the last ball.", "One ball left, and {needw} needed.", "Last ball, {needw} needed.", "Everything rides on this ball, {needw} needed."]
SIT_FIRST_THRILLER = ["{score}/{wkts}, and only {wihw} left.", "The innings is hanging by a thread at {score}/{wkts}.", "Only {wihw} in hand now, and the score is {score}/{wkts}.",
                      "{score} for {wkts}, and the tail is exposed.", "{wihw} left, and {ovl} to bat.", "Down to the last {wihw}, at {score}/{wkts}."]
SIT_FIRST_TENSE = ["{score}/{wkts} after {legal} balls.", "The score is {score}/{wkts}.", "{score}/{wkts}, with {ovl} to go.", "They are {score}/{wkts} now.",
                   "{score} for {wkts}, and {ovl} left to bat.", "That makes it {score} for {wkts}."]
SIT_SO = ["{needw} needed from {ballsw}.", "{need} off {balls}, and {wihw} in hand.", "{need} to win, {ballsw} left.", "The target is {target}, and {need} more are needed."]
SIT_SO_FIRST = ["{score}/{wkts} after {legal} balls, with {ballsw} to go.", "{score} for {wkts}, {ballsw} left in the Super Over.", "{wihw} left, {ballsw} to go, {score} on the board."]

# ---------------------------------------------------------- MILESTONES/STREAKS ---
MILE_FIFTY = ["FIFTY for {B}! {runs} off {balls} balls.", "{B} brings up a fine half-century off {balls} balls.", "A half-century for {B}, who raises the bat to the dressing room.",
              "That's fifty for {B}, and it has come off just {balls} balls.", "{B} goes to a wonderful fifty, and the crowd gives a standing ovation."]
MILE_HUNDRED = ["HUNDRED! {B} reaches a magnificent century, {runs} off {balls} balls!", "A CENTURY for {B}! What an innings!", "{B} raises the bat to the heavens, a hundred off {balls} balls!",
                "THREE FIGURES! {B} reaches a sensational century!"]
MILE_NINETIES = ["{B} is in the nineties now, {runs} off {balls}.", "Nervous nineties for {B}, on {runs}.", "{B} is closing in on a hundred, {runs} not out."]
DOT_STREAK = {3: ["That's three dots in a row.", "Three dot balls in a row now."], 4: ["Four dots in a row!", "Four dot balls now, and the pressure is building!"],
              5: ["Five dots in a row! Brilliant bowling!", "Five dot balls on the trot, superb stuff from {w}!"]}
BOUNDARY_AFTER_DOTS = ["Finally, a release after the dots.", "That ends the dot-ball streak.", "A boundary at last, after a string of dots."]
BACK_TO_BACK = ["Back-to-back boundaries!", "Two boundaries in a row!", "Another boundary, and {w} is under pressure."]
THREE_BOUNDARIES = ["That is three boundaries in the over!", "{w} is being hit all over the park, three boundaries in the over!"]
MAIDEN = ["That's a MAIDEN from {W}!", "A superb maiden over from {W}, six dots!", "{W} bowls a maiden, and the pressure is on!"]
OVER_EXPENSIVE = ["An expensive over for {W}, {orunw}.", "{W} goes for {orun} in that over.", "{orunw} from the over, and {W} will be disappointed."]
OVER_TIGHT = ["{W} concedes just {orunw} in that over.", "A tidy over from {W}, only {orunw}.", "Just {orunw} from the over, good bowling from {W}."]
OVER_WICKET = ["{W} finishes the over with a wicket and just {orunw}.", "A good over for {W}: one wicket, {orunw}."]
OVER_WICKETS_2 = ["Two wickets in the over, and {W} has turned this around!", "A double strike from {W}, two wickets in the over!"]
OVER_WICKETS_3 = ["THREE wickets in one over! {W} has wrecked the innings!"]
PHASE_PP = ["The powerplay restrictions are on, and the field is up.", "Only two fielders are outside the circle in the powerplay.", "The ball is still new, and the powerplay is at work."]
PHASE_MID = ["The middle overs are all about rotating the strike.", "The field is spread now, and the batters have to find the gaps.", "This is where the innings is built, one partnership at a time."]
PHASE_DEATH = ["The death overs are on, and every ball is a big one.", "The slog overs are here, and the yorkers are expected.", "It is crunch time, and every ball matters."]
PITCH_NOTES = {"green": ["There is some grass on this pitch, and the seamers enjoy it."], "dusty": ["The pitch is dusty, and the spinners are in the game."],
               "dry": ["There is some turn on this dry surface."], "hard": ["There is true bounce on this hard pitch."], "flat": ["This is a flat pitch, a belter for batting."],
               "bouncy": ["There is plenty of bounce on this pitch."], "slow": ["The slow surface makes timing difficult."], "even": ["An even-paced surface, with something for everyone."]}
