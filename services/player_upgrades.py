"""Pure player-upgrade catalogue and probability modifier service."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from utils.upgrade_prices import tier_strength

BATTER_APPROACHES = {"defensive", "rotate", "neutral", "aggressive", "ultra_aggressive"}
BOWLER_TACTICS = {"defensive", "swinging", "pace_up", "back_of_length", "variation",
                  "off_break", "doosra", "arm_ball", "carrom_ball", "top_spin",
                  "leg_breaker", "top_spinner", "slider", "flipper", "googly_ball"}

SPIN_TACTICS = {"off_break", "doosra", "arm_ball", "carrom_ball", "top_spin",
                "leg_breaker", "top_spinner", "slider", "flipper", "googly_ball"}

OUTCOMES = (0, 1, 2, 3, 4, 6, "W", "WD", "NB", "LB", "BY")

@dataclass(frozen=True, slots=True)
class UpgradeDef:
    key: str
    name: str
    category: str
    roles: frozenset[str]
    family: str | None
    tactics: frozenset[str]
    phases: frozenset[str]
    approaches: frozenset[str]
    description: str
    detail: str
    effect_kind: str
    outcomes: tuple[Any, ...]
    fund_from: tuple[Any, ...]
    counter_approaches: frozenset[str] = frozenset()


UPGRADES: tuple[UpgradeDef, ...] = (
    UpgradeDef("powerplay_striker", "Powerplay Striker", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset(), frozenset({"powerplay"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative boundary conversion in the Powerplay.", "Rewards attacking Powerplay intent by improving four and six conversion. It is active only in overs 1-6 when the batter is using an attacking mindset.", "boundary", (4,6), (0,1,2), frozenset({"rotate","defensive"})),
    UpgradeDef("new_ball_survivor", "New Ball Survivor", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset({"swinging","pace_up"}), frozenset({"powerplay"}), frozenset({"defensive","rotate","neutral"}), "+5% to +15% relative early-ball stability.", "Works in the Powerplay for a fresh batter facing Swinging or Pace Up. It trims dot pressure and slightly softens early wicket exposure.", "survival", (0,1), (4,6), frozenset({"back_of_length","variation"})),
    UpgradeDef("powerplay_controller", "Powerplay Controller", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset({"defensive","swinging","back_of_length","variation"}), frozenset({"powerplay"}), frozenset({"defensive","rotate","neutral"}), "+5% to +15% relative controlled scoring conversion.", "Improves singles and controlled twos during overs 1-6 when the batter is managing risk rather than forcing boundaries.", "rotation", (1,2), (0,4,6), frozenset({"pace_up"})),
    UpgradeDef("seam_breaker", "Seam Breaker", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset({"swinging","pace_up","back_of_length"}), frozenset({"powerplay"}), frozenset({"neutral","aggressive"}), "+5% to +15% relative four conversion against seam pressure.", "Best in the Powerplay on green or bouncy conditions. It specifically improves four conversion without creating a six or wicket shield.", "four", (4,), (0,1,2,6), frozenset({"variation"})),
    UpgradeDef("rotation_specialist", "Rotation Specialist", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset(), frozenset({"middle"}), frozenset({"rotate"}), "+5% to +15% relative single and double conversion.", "Designed for overs 7-15. It turns controlled intent into more singles and twos while trimming a small amount of dot pressure.", "rotation", (1,2), (0,4,6), frozenset({"variation","defensive"})),
    UpgradeDef("spin_controller", "Spin Controller", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), "spin", frozenset(SPIN_TACTICS), frozenset({"middle"}), frozenset({"rotate","neutral"}), "+5% to +15% relative controlled scoring against spin.", "Works mainly in middle overs against spin. It improves strike rotation while leaving wicket risk governed by the existing tactics engine.", "rotation", (1,2), (0,4,6), frozenset({"googly_ball","doosra"})),
    UpgradeDef("gap_finder", "Gap Finder", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset({"defensive","back_of_length","variation"}), frozenset({"middle"}), frozenset({"rotate","neutral"}), "+5% to +15% relative one/two conversion into gaps.", "Most useful in the middle overs against containing lines. It improves low-risk scoring rather than raising six probability.", "rotation", (1,2), (0,4,6), frozenset({"variation","back_of_length"})),
    UpgradeDef("set_batter_converter", "Set Batter Converter", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset(), frozenset({"middle","death"}), frozenset({"neutral","aggressive"}), "+5% to +15% relative boundary conversion for set batters.", "Activates after 16 legal balls faced. It gives a set batter slightly better four conversion without removing the existing wicket and phase controls.", "four", (4,), (0,1,2,6), frozenset({"variation","pace_up"})),
    UpgradeDef("death_finisher", "Death Finisher", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset(), frozenset({"death"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative four/six conversion at the death.", "Active in overs 16-20 for attacking intent. It increases boundary conversion while leaving wicket exposure with the existing simulation.", "boundary", (4,6), (0,1,2), frozenset({"variation","pace_up"})),
    UpgradeDef("yorker_breaker", "Yorker Breaker", "batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset({"pace_up","variation"}), frozenset({"death"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative four conversion against death-over pace pressure.", "Built for overs 16-20 when pace-heavy execution is being used. It improves four conversion but does not create a six or survival guarantee.", "four", (4,), (0,1,2,6), frozenset({"back_of_length","defensive"})),
    UpgradeDef("chase_finisher", "Chase Finisher", "pressure_batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset(), frozenset({"death"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative boundary conversion in a demanding chase.", "Works in the second innings during the final 30 balls when the required rate is 8-13 and the batter is attacking. It does not reduce wicket probability.", "boundary", (4,6), (0,1,2), frozenset({"defensive","neutral"})),
    UpgradeDef("pressure_performer", "Pressure Performer", "pressure_batting", frozenset({"batsman","wicketkeeper","allrounder"}), None, frozenset(), frozenset({"middle","death"}), frozenset({"neutral","aggressive","ultra_aggressive"}), "+5% to +15% relative controlled scoring under chase pressure.", "Activates in the second innings when the required rate is at least 10 and confidence is 60 or higher. It improves controlled scoring without becoming a wicket shield.", "pressure", (1,2,4), (0,1), frozenset({"defensive"})),
    UpgradeDef("powerplay_hunter", "Powerplay Hunter", "pace_bowling", frozenset({"bowler","allrounder"}), "pace", frozenset({"swinging","pace_up"}), frozenset({"powerplay"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative dot/wicket pressure in the Powerplay.", "An attack specialist for overs 1-6. It becomes strongest when Swinging or Pace Up meets aggressive intent.", "hunter", (0,"W"), (4,6,1,2), frozenset({"rotate","neutral"})),
    UpgradeDef("swing_controller", "Swing Controller", "pace_bowling", frozenset({"bowler","allrounder"}), "pace", frozenset({"swinging"}), frozenset({"powerplay"}), frozenset({"neutral","aggressive"}), "+5% to +15% relative dot/wicket pressure from Swinging.", "Specialises in new-ball Swinging on green or bouncy conditions, increasing pressure without becoming an all-phase wicket boost.", "hunter", (0,"W"), (4,6,1), frozenset({"defensive","rotate"})),
    UpgradeDef("pace_up_enforcer", "Pace-Up Enforcer", "pace_bowling", frozenset({"bowler","allrounder"}), "pace", frozenset({"pace_up"}), frozenset({"powerplay","middle","death"}), frozenset({"ultra_aggressive","aggressive"}), "+5% to +15% relative dot/wicket pressure from Pace Up.", "Uses Pace Up more effectively against aggressive intent, with the strongest contextual value in Powerplay and Death phases.", "hunter", (0,"W"), (4,6,1,2), frozenset({"rotate","defensive"})),
    UpgradeDef("short_ball_operator", "Short Ball Operator", "pace_bowling", frozenset({"bowler","allrounder"}), "pace", frozenset({"back_of_length"}), frozenset({"middle"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative pressure against aggressive batting.", "Designed for overs 7-15 with Back of Length. It increases wicket pressure and suppresses easy single conversion while the base engine controls the final outcome.", "hunter", ("W",0), (1,2,4,6), frozenset({"defensive","rotate"})),
    UpgradeDef("death_executioner", "Death Executioner", "pace_bowling", frozenset({"bowler","allrounder"}), "pace", frozenset({"variation","pace_up"}), frozenset({"death"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative death-over dot/wicket pressure.", "Built for overs 16-20 with Variation or Pace Up. It strengthens late pressure without granting a generic wicket bonus outside its phase.", "hunter", (0,"W"), (4,6,1,2), frozenset({"rotate","neutral"})),
    UpgradeDef("spin_lockdown", "Spin Lockdown", "spin_bowling", frozenset({"bowler","allrounder"}), "spin", frozenset({"off_break","arm_ball","top_spin","slider"}), frozenset({"middle"}), frozenset({"rotate","neutral"}), "+5% to +15% relative dot pressure and reduced easy rotation.", "A middle-over containment upgrade for controlled batters. It works with the specified spin deliveries and increases pressure while preserving the normal scoring alternatives.", "dot", (0,), (1,2,4,6), frozenset({"aggressive"})),
    UpgradeDef("googly_trap", "Googly Trap", "spin_bowling", frozenset({"bowler","allrounder"}), "spin", frozenset({"googly_ball"}), frozenset({"middle"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative dot/wicket pressure from the Googly.", "A leg-spin specialist for overs 7-15. It becomes strongest when the Googly meets aggressive batting intent.", "hunter", (0,"W"), (4,6,1,2), frozenset({"neutral","defensive"})),
    UpgradeDef("turn_extractor", "Turn Extractor", "spin_bowling", frozenset({"bowler","allrounder"}), "spin", frozenset({"leg_breaker","top_spinner","flipper"}), frozenset({"middle","death"}), frozenset({"aggressive","ultra_aggressive"}), "+5% to +15% relative spin pressure on turning surfaces.", "Most effective on dusty, dry, or slow pitches with the listed leg-spin deliveries. It raises pressure against aggressive intent without guaranteeing wickets.", "hunter", (0,"W"), (4,6,1,2), frozenset({"rotate","defensive"})),
    UpgradeDef("off_spin_controller", "Off-Spin Controller", "spin_bowling", frozenset({"bowler","allrounder"}), "spin", frozenset({"off_break","doosra","arm_ball","carrom_ball"}), frozenset({"middle"}), frozenset({"rotate","neutral"}), "+5% to +15% relative suppression of easy singles and twos.", "An off-spin controller for the middle overs. It makes strike rotation harder while leaving boundary and wicket outcomes governed by the existing tactical engine.", "dot", (0,), (1,2,4,6), frozenset({"aggressive"})),
)

UPGRADE_BY_KEY = {u.key: u for u in UPGRADES}


def phase_for_over(over_number: int) -> str:
    if int(over_number) <= 6:
        return "powerplay"
    if int(over_number) <= 15:
        return "middle"
    return "death"


def normalise_role(role: str | None) -> str:
    return str(role or "").strip().lower().replace(" ", "").replace("_", "")


def role_key(role: str | None) -> str:
    raw = normalise_role(role)
    if raw in {"batsman", "batter"}:
        return "batsman"
    if raw in {"wicketkeeper", "wk", "keeper"}:
        return "wicketkeeper"
    if raw in {"allrounder", "allround"}:
        return "allrounder"
    return "bowler"


def bowler_family(bowler_role: str | None, bowling_hand: str | None = None) -> str:
    hand = str(bowling_hand or "").strip().upper().replace(" ", "")
    role = str(bowler_role or "").strip().upper().replace(" ", "")
    if hand in {"RAO", "LAO", "RAL", "LAL"}:
        return "spin"
    text = f"{hand} {role}".replace(" ", "")
    if any(k in text for k in ("SPIN", "SPINNER", "OFFBREAK", "OFFSPIN", "LEGBREAK", "LEGSPIN", "ORTHODOX", "CHINAMAN", "RAO", "LAO", "RAL", "LAL")):
        return "spin"
    return "pace"


def chase_context(*, target: int | None, total_runs: int, balls_remaining: int) -> tuple[bool, float]:
    if target is None or balls_remaining <= 0:
        return False, 0.0
    runs_required = max(0, int(target) - int(total_runs))
    required_rate = runs_required * 6 / max(1, int(balls_remaining))
    if required_rate < 8:
        return False, required_rate
    return True, required_rate


def activation_strength(upgrade: UpgradeDef, *, tier: int, phase: str, tactic: str, mindset: str,
                        bowler_family_name: str, batsman_balls_faced: int, target: int | None,
                        total_runs: int, balls_remaining: int, confidence: float) -> float:
    if upgrade.phases and phase not in upgrade.phases:
        return 0.0
    if upgrade.tactics and tactic not in upgrade.tactics:
        if upgrade.category in {"pace_bowling", "spin_bowling"}:
            return 0.0
        if tactic in BOWLER_TACTICS and upgrade.key not in {"powerplay_striker", "rotation_specialist", "powerplay_controller", "gap_finder", "set_batter_converter", "death_finisher", "chase_finisher", "pressure_performer"}:
            return 0.0
    if upgrade.family and bowler_family_name != upgrade.family:
        return 0.0
    if upgrade.approaches and mindset not in upgrade.approaches:
        return 0.35 if mindset in BATTER_APPROACHES else 0.0

    if upgrade.key in {"powerplay_striker", "death_finisher", "yorker_breaker", "chase_finisher"} and mindset not in {"aggressive","ultra_aggressive"}:
        return 0.0
    if upgrade.key == "new_ball_survivor" and int(batsman_balls_faced) > 5:
        return 0.0
    if upgrade.key == "set_batter_converter" and int(batsman_balls_faced) < 16:
        return 0.0
    if upgrade.key == "chase_finisher":
        chasing, rate = chase_context(target=target, total_runs=total_runs, balls_remaining=balls_remaining)
        if not chasing or not (8 <= rate <= 13) or int(balls_remaining) > 30:
            return 0.0
    if upgrade.key == "pressure_performer":
        chasing, rate = chase_context(target=target, total_runs=total_runs, balls_remaining=balls_remaining)
        if not chasing or rate < 10 or float(confidence) < 60:
            return 0.0
    if upgrade.key == "turn_extractor":
        # Pitch gating is handled by caller through a compatible family/phase check.
        pass
    strength = tier_strength(tier)
    if upgrade.tactics and tactic in upgrade.tactics and upgrade.phases and phase in upgrade.phases:
        return strength
    if not upgrade.tactics:
        return strength
    return strength * 0.35


def _scale_targets(weights: dict[Any, float], targets: tuple[Any, ...], relative: float) -> None:
    for key in targets:
        if key in weights:
            weights[key] = max(0.0, float(weights[key])) * (1.0 + relative)


def _transfer(weights: dict[Any, float], targets: tuple[Any, ...], sources: tuple[Any, ...], fraction: float) -> None:
    fraction = max(0.0, min(1.0, fraction))
    source_total = sum(max(0.0, float(weights.get(k, 0.0))) for k in sources)
    if source_total <= 0:
        return
    moved = source_total * fraction
    for key in sources:
        current = max(0.0, float(weights.get(key, 0.0)))
        if current:
            weights[key] = current - moved * (current / source_total)
    target_total = sum(max(0.0, float(weights.get(k, 0.0))) for k in targets)
    if target_total <= 0:
        return
    for key in targets:
        current = max(0.0, float(weights.get(key, 0.0)))
        weights[key] = current + moved * (current / target_total)


def _normalise(weights: dict[Any, float]) -> dict[Any, float]:
    cleaned = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(cleaned.values())
    if total <= 0:
        return dict(cleaned)
    return {k: v / total for k, v in cleaned.items()}


def apply_upgrade_layer(weights: Mapping[Any, float], *, batting_upgrade: Mapping[str, Any] | None = None,
                        bowling_upgrade: Mapping[str, Any] | None = None, phase: str,
                        tactic: str, mindset: str, pitch: str, bowler_family_name: str,
                        batsman_balls_faced: int, target: int | None, total_runs: int,
                        balls_remaining: int, confidence: float, batter_role: str | None,
                        bowler_role: str | None) -> dict[Any, float]:
    out = {k: max(0.0, float(v)) for k, v in weights.items()}

    def apply_one(owned: Mapping[str, Any] | None, is_batting: bool) -> None:
        if not owned:
            return
        key = str(owned.get("upgrade_key") or "")
        tier = int(owned.get("tier") or 1)
        upgrade = UPGRADE_BY_KEY.get(key)
        if upgrade is None:
            return
        role = role_key(batter_role if is_batting else bowler_role)
        if role not in upgrade.roles:
            return
        strength = activation_strength(
            upgrade, tier=tier, phase=phase, tactic=tactic, mindset=mindset,
            bowler_family_name=bowler_family_name, batsman_balls_faced=batsman_balls_faced,
            target=target, total_runs=total_runs, balls_remaining=balls_remaining, confidence=confidence,
        )
        if strength <= 0:
            return
        # Spin surface specialists need the actual pitch family.
        if key == "turn_extractor" and pitch not in {"dusty", "dry", "slow"}:
            return

        if upgrade.effect_kind == "boundary":
            _scale_targets(out, upgrade.outcomes, min(0.15, strength))
            _transfer(out, upgrade.outcomes, upgrade.fund_from, min(0.30, strength))
        elif upgrade.effect_kind == "four":
            _scale_targets(out, upgrade.outcomes, min(0.15, strength))
            _transfer(out, upgrade.outcomes, upgrade.fund_from, min(0.25, strength))
        elif upgrade.effect_kind == "rotation":
            _scale_targets(out, upgrade.outcomes, min(0.15, strength))
            _transfer(out, upgrade.outcomes, upgrade.fund_from, min(0.18, strength))
        elif upgrade.effect_kind == "survival":
            _scale_targets(out, (0,1), min(0.10, strength))
            if "W" in out:
                out["W"] *= max(0.90, 1.0 - min(0.10, strength))
            _transfer(out, (1,2), (4,6), min(0.10, strength * 0.5))
        elif upgrade.effect_kind == "dot":
            _scale_targets(out, (0,), min(0.12, strength))
            _transfer(out, (0,), (1,2,4,6), min(0.18, strength))
        elif upgrade.effect_kind == "hunter":
            _scale_targets(out, (0,"W"), min(0.12, strength))
            _transfer(out, (0,"W"), (4,6,1,2), min(0.18, strength))
        elif upgrade.effect_kind == "pressure":
            _scale_targets(out, (1,2,4), min(0.10, strength))
            _transfer(out, (1,2,4), (0,1), min(0.12, strength))

    apply_one(batting_upgrade, True)
    apply_one(bowling_upgrade, False)

    # Final upgrade-only relative safety caps.
    baseline = max(1e-12, sum(max(0.0, float(v)) for v in weights.values()))
    for key, value in list(out.items()):
        if not (value >= 0.0):
            out[key] = 0.0
        out[key] = min(out[key], baseline * 3.0)
    return _normalise(out)


def eligible_for_player(upgrade: UpgradeDef, *, role: str | None, bowling_style: str | None, bowling_hand: str | None) -> bool:
    if role_key(role) not in upgrade.roles:
        return False
    if upgrade.family and bowler_family(bowling_style or role, bowling_hand) != upgrade.family:
        return False
    return True
