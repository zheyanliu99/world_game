import random

from hwsim.core.battle import collect_border_targets, run_battle_tick
from hwsim.core.models import Alliance, Faction, GameState, Region
from hwsim.core.story_director import StoryInfluence


def _faction(faction_id: str, troops: int, attack: float = 1.0, defense: float = 1.0) -> Faction:
    return Faction(
        id=faction_id,
        name_cn=faction_id,
        color="#668855",
        troops=troops,
        morale=75,
        economy=70,
        stability=70,
        legitimacy=50,
        attack=attack,
        defense=defense,
        naval=0.8,
        expansion=1.0,
        capital_region="a",
    )


def _region(region_id: str, neighbors: list[str]) -> Region:
    return Region(
        id=region_id,
        name_cn=region_id,
        center=(100, 100),
        polygon=[(0, 0), (10, 0), (10, 10), (0, 10)],
        terrain="plain",
        population=50,
        economy=50,
        initial_owner="attacker",
        neighbors=neighbors,
    )


def test_battle_tick_can_transfer_region_and_change_stats() -> None:
    state = GameState(
        current_year=200,
        factions={
            "attacker": _faction("attacker", 200_000, attack=2.2),
            "defender": _faction("defender", 2_000, defense=0.45),
        },
        regions={"a": _region("a", ["b"]), "b": _region("b", ["a"])},
        region_owners={"a": "attacker", "b": "defender"},
    )

    record = run_battle_tick(state, random.Random(3), StoryInfluence())

    assert record is not None
    assert state.region_owners["b"] == "attacker"
    assert state.factions["attacker"].troops < 200_000
    assert state.factions["defender"].morale < 75


def test_alliance_blocks_direct_attacks() -> None:
    state = GameState(
        current_year=208,
        factions={
            "liu_bei": _faction("liu_bei", 40_000),
            "sun_quan": _faction("sun_quan", 50_000),
        },
        regions={"xuzhou": _region("xuzhou", ["yangzhou"]), "yangzhou": _region("yangzhou", ["xuzhou"])},
        region_owners={"xuzhou": "liu_bei", "yangzhou": "sun_quan"},
    )
    state.alliances.append(
        Alliance(factions=("liu_bei", "sun_quan"), expires_year=216, source_event="chibi_208")
    )

    assert collect_border_targets(state, "liu_bei", StoryInfluence()) == []
