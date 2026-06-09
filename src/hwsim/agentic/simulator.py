from __future__ import annotations

import math
import random
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from hwsim.agentic.agents import AdvisorProvider, AgentProvider, DeterministicAdvisorProvider, MockAgentProvider
from hwsim.agentic.city_data import load_city_graph, load_general_seeds
from hwsim.agentic.models import (
    AnimationEvent,
    AgentObservation,
    AgentOrder,
    AgentPlan,
    AgenticAlliance,
    AgenticGameState,
    AgenticUnit,
    AdvisorRecommendation,
    BattleEvent,
    CityView,
    DiplomacyOrder,
    FactionView,
    GeneralView,
    GameView,
    PendingAttack,
    Policy,
    RealMapProvinceView,
    RealMapStateLabel,
    RealMapView,
    ResourceType,
    Resources,
    RoundLog,
)
from hwsim.core.models import Faction, MapConfig, Region, ScenarioConfig
from hwsim.map.map_loader import load_bundle
from hwsim.map.real_map import prepare_real_map
from hwsim.utils.file_utils import read_json, resolve_path


FACTION_IDS = ("cao", "liu_bei", "sun_quan")
PLAYER_FACTION = "liu_bei"
MAX_ROUNDS = 100
DIPLOMACY_ACTIONS_PER_ROUND = 1
DOMINANCE_SHARE = 0.75
DEFAULT_SCENARIO = Path("configs/scenarios/sanguo_shu_unification_demo.json")
REAL_MAP_CONFIG = Path("configs/maps/sanguo_real_map.json")
REAL_MAP_FILE = Path("data/maps/sanguo_real_map_prepared.json")
REAL_STATE_FILE = Path("configs/maps/sanguo_state_regions.json")

ACTION_COSTS = {
    "rest": 1,
    "attack": 2,
    "defend": 1,
    "scout": 1,
    "farm": 1,
    "transfer": 1,
}

POLICY_MODIFIERS: dict[Policy, dict[str, float]] = {
    "balanced": {"food": 1.0, "weapons": 1.0, "gold": 1.0, "manpower": 1.0, "attack": 1.0, "defense": 1.0, "transfer": 1.0},
    "farming": {"food": 1.35, "weapons": 0.86, "gold": 1.0, "manpower": 1.08, "attack": 0.86, "defense": 0.95, "transfer": 1.15},
    "war": {"food": 0.74, "weapons": 0.92, "gold": 0.84, "manpower": 0.9, "attack": 1.28, "defense": 1.16, "transfer": 0.9},
    "logistics": {"food": 0.96, "weapons": 1.0, "gold": 0.95, "manpower": 1.0, "attack": 1.02, "defense": 1.0, "transfer": 1.45},
    "defense": {"food": 0.96, "weapons": 0.94, "gold": 0.95, "manpower": 1.0, "attack": 0.84, "defense": 1.36, "transfer": 0.96},
    "diplomacy": {"food": 1.0, "weapons": 0.9, "gold": 1.2, "manpower": 1.0, "attack": 0.84, "defense": 0.96, "transfer": 1.12},
}

TERRAIN_DEFENSE = {
    "plain": 8.0,
    "river": 13.0,
    "mountain": 18.0,
    "pass": 15.0,
}

INITIAL_OWNER_MAP = {
    "liu_bei": {"yizhou", "hanzhong", "jingzhou", "nanzhong"},
    "sun_quan": {"yangzhou", "jiaozhou"},
}

INITIAL_RESOURCES = {
    "cao": Resources(food=145, weapons=120, gold=105, manpower=135, intel=10),
    "liu_bei": Resources(food=98, weapons=78, gold=78, manpower=80, intel=14),
    "sun_quan": Resources(food=112, weapons=90, gold=94, manpower=90, intel=12),
}

INITIAL_UNITS = {
    "cao": [
        ("army", "sili", 1.12),
        ("army", "yanzhou", 1.08),
        ("army", "yuzhou", 1.0),
        ("army", "qingzhou", 0.96),
        ("worker", "jizhou", 1.0),
        ("scout", "sili", 1.0),
        ("caravan", "yanzhou", 1.0),
    ],
    "liu_bei": [
        ("army", "hanzhong", 1.1),
        ("army", "jingzhou", 1.0),
        ("worker", "yizhou", 1.0),
        ("scout", "jingzhou", 1.0),
        ("caravan", "yizhou", 1.0),
    ],
    "sun_quan": [
        ("army", "yangzhou", 1.08),
        ("army", "jiaozhou", 0.92),
        ("worker", "yangzhou", 1.0),
        ("scout", "yangzhou", 1.0),
        ("caravan", "jiaozhou", 1.0),
    ],
}


class AgenticGameEngine:
    def __init__(
        self,
        scenario: ScenarioConfig,
        map_config: MapConfig,
        agent_provider: AgentProvider | None = None,
        fallback_provider: AgentProvider | None = None,
        advisor_provider: AdvisorProvider | None = None,
    ) -> None:
        self.scenario = scenario
        self.map_config = map_config
        self.agent_provider = agent_provider or MockAgentProvider()
        self.fallback_provider = fallback_provider or MockAgentProvider()
        self.advisor_provider = advisor_provider or DeterministicAdvisorProvider()
        self.real_map_view = self._load_real_map_view()
        self.city_graph = load_city_graph()
        self.city_config = self.city_graph.city_map()
        self.general_seeds = load_general_seeds(city_ids=set(self.city_config))

    @classmethod
    def from_default_scenario(
        cls,
        agent_provider: AgentProvider | None = None,
        fallback_provider: AgentProvider | None = None,
        advisor_provider: AdvisorProvider | None = None,
    ) -> "AgenticGameEngine":
        scenario, map_config, _events, _style = load_bundle(DEFAULT_SCENARIO)
        return cls(scenario, map_config, agent_provider=agent_provider, fallback_provider=fallback_provider, advisor_provider=advisor_provider)

    def new_game(self, player_faction: str = PLAYER_FACTION, game_id: str | None = None) -> AgenticGameState:
        factions = self._three_kingdom_factions()
        regions = {region.id: region for region in self.map_config.regions}
        cities = self._city_views()
        city_owners = {city_id: city.initial_owner for city_id, city in self.city_config.items()}
        region_owners = self._derive_region_owners_from_cities(city_owners, regions)
        generals = self._initial_generals()
        units = self._initial_units(generals)
        state = AgenticGameState(
            game_id=game_id or uuid.uuid4().hex,
            round=0,
            max_rounds=MAX_ROUNDS,
            player_faction=player_faction,
            factions=factions,
            regions=regions,
            region_owners=region_owners,
            cities=cities,
            city_owners=city_owners,
            city_development={city_id: 1.0 for city_id in cities},
            city_supply={city_id: {} for city_id in cities},
            units=units,
            generals=generals,
            resources={faction_id: INITIAL_RESOURCES[faction_id].model_copy(deep=True) for faction_id in FACTION_IDS},
            policies={faction_id: "balanced" for faction_id in FACTION_IDS},
            region_development={region_id: 1.0 for region_id in regions},
            regional_supply={region_id: {} for region_id in regions},
            logs=[
                RoundLog(
                    round=0,
                    title="三国开局",
                    detail="你控制蜀汉。魏强、吴稳，一百回合内用政策、补给、联盟和战役改写局势。",
                    faction_id=player_faction,
                    tone="info",
                )
            ],
        )
        return state

    def save_player_command(
        self,
        state: AgenticGameState,
        strategy_text: str,
        policy: Policy,
        orders: list[AgentOrder] | None = None,
        diplomacy: list[DiplomacyOrder] | None = None,
    ) -> AgenticGameState:
        state.current_player_command = strategy_text.strip()
        state.current_player_policy = policy
        state.current_player_orders = orders or []
        state.current_player_diplomacy = diplomacy or []
        return state

    def resolve_round(self, state: AgenticGameState) -> AgenticGameState:
        if state.finished:
            return state
        self._update_victory(state)
        if state.finished:
            return state
        if state.round >= state.max_rounds:
            self._finish_by_score(state, "Round limit reached")
            return state

        round_number = state.round + 1
        plans = self._collect_plans(state, round_number)
        state.last_plans = plans
        state.round = round_number

        self._start_round(state)
        self._apply_policies(state, plans)
        defense_bonus: dict[str, float] = defaultdict(float)
        attacks: list[PendingAttack] = []
        diplomacy_used = {faction_id: 0 for faction_id in FACTION_IDS}
        used_units: set[str] = set()

        for faction_id in FACTION_IDS:
            self._apply_diplomacy(state, faction_id, plans[faction_id].diplomacy, diplomacy_used)

        for faction_id in FACTION_IDS:
            for order in plans[faction_id].orders:
                self._try_execute_order(state, faction_id, order, used_units, defense_bonus, attacks)
            self._apply_default_defense_orders(state, faction_id, used_units, defense_bonus, attacks)

        self._resolve_attacks(state, attacks, defense_bonus)
        self._sync_generals(state)
        state.region_owners = self._derive_region_owners_from_cities(state.city_owners, state.regions)
        self._apply_round_income(state)
        self._recover_units(state)
        self._expire_alliances(state)
        self._update_victory(state)
        state.current_player_orders = []
        state.current_player_diplomacy = []
        return state

    def to_view(self, state: AgenticGameState) -> GameView:
        self._sync_generals(state)
        region_counts = self._region_counts(state)
        unit_counts = {faction_id: sum(1 for unit in state.units if unit.faction_id == faction_id) for faction_id in FACTION_IDS}
        factions: dict[str, FactionView] = {}
        for faction_id in FACTION_IDS:
            faction = state.factions[faction_id]
            factions[faction_id] = FactionView(
                id=faction_id,
                name=faction.display_name(222),
                color=faction.color,
                policy=state.policies[faction_id],
                resources=state.resources[faction_id],
                region_count=region_counts[faction_id],
                unit_count=unit_counts[faction_id],
                score=self._score_faction(state, faction_id),
                ai_intent=state.last_plans.get(faction_id, AgentPlan()).reasoning_summary,
            )
        return GameView(
            game_id=state.game_id,
            round=state.round,
            max_rounds=state.max_rounds,
            player_faction=state.player_faction,
            finished=state.finished,
            winner=state.winner,
            real_map=self.real_map_view,
            regions=list(self.map_config.regions),
            region_owners=state.region_owners,
            cities=list(state.cities.values()),
            city_owners=state.city_owners,
            city_development=state.city_development,
            city_supply=state.city_supply,
            region_development=state.region_development,
            regional_supply=state.regional_supply,
            region_pressure=state.region_pressure,
            factions=factions,
            units=state.units,
            generals=list(state.generals.values()),
            battle_events=state.battle_events[-24:],
            animations=state.animations,
            alliances=state.alliances,
            logs=state.logs[-80:],
            current_player_command=state.current_player_command,
            current_player_policy=state.current_player_policy,
            current_player_orders=state.current_player_orders,
            advisor_recommendation=self.recommend_player_plan(state),
        )

    def recommend_player_plan(self, state: AgenticGameState) -> AdvisorRecommendation:
        observation = self._observation_for(state, state.player_faction, min(state.round + 1, state.max_rounds))
        try:
            recommendation = self.advisor_provider.recommend(observation)
        except Exception as exc:
            fallback = DeterministicAdvisorProvider().recommend(observation)
            fallback.summary = f"Local advisor fallback used after recommendation error: {exc}"
            return fallback
        return AdvisorRecommendation.model_validate(recommendation)

    def _three_kingdom_factions(self) -> dict[str, Faction]:
        factions: dict[str, Faction] = {}
        for faction_id in FACTION_IDS:
            faction = self.scenario.factions[faction_id].model_copy(deep=True)
            faction.id = faction_id
            factions[faction_id] = faction
        return factions

    def _initial_region_owners(self, regions: dict[str, Region]) -> dict[str, str]:
        owners: dict[str, str] = {}
        for region_id in regions:
            if region_id in INITIAL_OWNER_MAP["liu_bei"]:
                owners[region_id] = "liu_bei"
            elif region_id in INITIAL_OWNER_MAP["sun_quan"]:
                owners[region_id] = "sun_quan"
            else:
                owners[region_id] = "cao"
        return owners

    def _city_views(self) -> dict[str, CityView]:
        return {
            city_id: CityView(
                id=city.id,
                name_cn=city.name_cn,
                region_id=city.region_id,
                position=city.position,
                terrain=city.terrain,
                population=city.population,
                economy=city.economy,
                fort=city.fort,
                neighbors=city.neighbors,
            )
            for city_id, city in self.city_config.items()
        }

    def _initial_generals(self) -> dict[str, GeneralView]:
        return {
            seed.id: GeneralView(
                id=seed.id,
                name_cn=seed.name_cn,
                name_en=seed.name_en,
                faction_id=seed.faction_id,
                portrait_path=seed.portrait_path,
                city_id=seed.starting_city_id,
                soldiers=seed.soldiers,
                max_soldiers=seed.max_soldiers,
                command=seed.command,
                attack=seed.attack,
                defense=seed.defense,
                mobility=seed.mobility,
                loyalty=seed.loyalty,
                food_need=seed.food_need,
                surrender_risk=seed.surrender_risk,
            )
            for seed in self.general_seeds
        }

    def _initial_units(self, generals: dict[str, GeneralView]) -> list[AgenticUnit]:
        units: list[AgenticUnit] = []
        army_counts: dict[str, int] = defaultdict(int)
        for general in generals.values():
            city = self.city_config[general.city_id or ""]
            army_counts[general.faction_id] += 1
            unit_id = f"{general.faction_id}_army_{army_counts[general.faction_id]}"
            general.unit_id = unit_id
            units.append(
                AgenticUnit(
                    id=unit_id,
                    faction_id=general.faction_id,
                    unit_type="army",
                    region_id=city.region_id,
                    city_id=city.id,
                    general_id=general.id,
                    soldiers=general.soldiers,
                    max_soldiers=general.max_soldiers,
                    readiness=100,
                    power=(general.command + general.attack + general.defense) / 255,
                    status="idle",
                )
            )
        for faction_id, specs in INITIAL_UNITS.items():
            unit_type_counts: dict[str, int] = defaultdict(int)
            for unit_type, region_id, power in specs:
                if unit_type == "army":
                    continue
                unit_type_counts[unit_type] += 1
                city_id = self._default_city_for_region(faction_id, region_id)
                units.append(
                    AgenticUnit(
                        id=f"{faction_id}_{unit_type}_{unit_type_counts[unit_type]}",
                        faction_id=faction_id,
                        unit_type=unit_type,  # type: ignore[arg-type]
                        region_id=region_id,
                        city_id=city_id,
                        readiness=100,
                        power=power,
                    )
                )
        return units

    def _default_city_for_region(self, faction_id: str, region_id: str) -> str | None:
        for city in self.city_config.values():
            if city.region_id == region_id and city.initial_owner == faction_id:
                return city.id
        for city in self.city_config.values():
            if city.region_id == region_id:
                return city.id
        return None

    def _derive_region_owners_from_cities(self, city_owners: dict[str, str], regions: dict[str, Region]) -> dict[str, str]:
        owners: dict[str, str] = {}
        for region_id in regions:
            counts: dict[str, int] = defaultdict(int)
            for city_id, owner in city_owners.items():
                city = self.city_config.get(city_id)
                if city and city.region_id == region_id:
                    counts[owner] += max(1, city.population)
            if counts:
                owners[region_id] = max(FACTION_IDS, key=lambda faction_id: (counts.get(faction_id, 0), faction_id == "liu_bei"))
            elif region_id in INITIAL_OWNER_MAP["liu_bei"]:
                owners[region_id] = "liu_bei"
            elif region_id in INITIAL_OWNER_MAP["sun_quan"]:
                owners[region_id] = "sun_quan"
            else:
                owners[region_id] = "cao"
        return owners

    def _load_real_map_view(self) -> RealMapView | None:
        prepared_path = resolve_path(REAL_MAP_FILE)
        state_path = resolve_path(REAL_STATE_FILE)
        if not state_path.exists():
            return None
        if not prepared_path.exists():
            try:
                prepared_path = prepare_real_map(REAL_MAP_CONFIG)
            except Exception:
                return None
        prepared = read_json(prepared_path)
        state_records = read_json(state_path).get("states", [])
        provinces: list[RealMapProvinceView] = []
        state_points: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
        for province in prepared.get("provinces", []):
            name = str(province.get("name", ""))
            region_id = self._display_region_for_province(name, province.get("owner"), state_records)
            centroid_raw = province.get("centroid") or [0, 0]
            centroid = (float(centroid_raw[0]), float(centroid_raw[1]))
            cell_count = int(province.get("cell_count", 0))
            provinces.append(
                RealMapProvinceView(
                    id=int(province["id"]),
                    name=name,
                    region_id=region_id,
                    centroid=centroid,
                    cell_count=cell_count,
                )
            )
            state_points[region_id].append((centroid[0], centroid[1], max(1, cell_count)))

        state_names = {str(record.get("id")): str(record.get("name_cn", record.get("id"))) for record in state_records}
        state_labels = [
            RealMapStateLabel(id=region_id, name_cn=state_names.get(region_id, self._region_name(region_id)), centroid=self._weighted_centroid(points))
            for region_id, points in sorted(state_points.items())
            if region_id in self._region_ids()
        ]
        return RealMapView(
            canvas_size=tuple(prepared.get("canvas_size", (1280, 720))),
            grid_size=tuple(prepared.get("grid_size", (480, 270))),
            province_id_grid=prepared.get("province_id_grid", []),
            provinces=provinces,
            state_labels=state_labels,
            attribution=str(prepared.get("attribution", "")),
        )

    def _display_region_for_province(self, name: str, owner: str | None, state_records: list[dict]) -> str:
        lower_name = name.lower()
        for record in state_records:
            region_id = str(record.get("id", ""))
            contains = [str(item).lower() for item in record.get("contains", [])]
            if region_id in self._region_ids() and any(fragment in lower_name for fragment in contains):
                return region_id
        if any(fragment in lower_name for fragment in ("hong kong", "macau", "guangdong", "guangxi", "hainan")):
            return "jiaozhou"
        if owner == "liu_bei":
            return "yizhou"
        if owner == "sun_quan":
            return "yangzhou"
        return "sili"

    def _weighted_centroid(self, points: list[tuple[float, float, int]]) -> tuple[float, float]:
        total = sum(weight for _x, _y, weight in points)
        if total <= 0:
            return 0.0, 0.0
        return (
            sum(x * weight for x, _y, weight in points) / total,
            sum(y * weight for _x, y, weight in points) / total,
        )

    def _region_ids(self) -> set[str]:
        return {region.id for region in self.map_config.regions}

    def _region_name(self, region_id: str) -> str:
        for region in self.map_config.regions:
            if region.id == region_id:
                return region.name_cn
        return region_id

    def _collect_plans(self, state: AgenticGameState, round_number: int) -> dict[str, AgentPlan]:
        plans: dict[str, AgentPlan] = {}
        for faction_id in FACTION_IDS:
            if faction_id == state.player_faction:
                plans[faction_id] = self._player_plan(state)
                continue
            observation = self._observation_for(state, faction_id, round_number)
            try:
                plan = self.agent_provider.decide(observation)
            except Exception as exc:
                self._log(
                    state,
                    "AI fallback",
                    f"{state.factions[faction_id].display_name(222)} live agent failed; deterministic mock took over. {exc}",
                    faction_id=faction_id,
                    tone="warning",
                    round_number=round_number,
                )
                plan = self.fallback_provider.decide(observation)
            plans[faction_id] = AgentPlan.model_validate(plan)
        return plans

    def _player_plan(self, state: AgenticGameState) -> AgentPlan:
        diplomacy = list(state.current_player_diplomacy)
        text = state.current_player_command.lower()
        if "alliance" in text or "ally" in text or "联盟" in text or "结盟" in text:
            if not self._are_allied(state, state.player_faction, "sun_quan"):
                diplomacy.append(DiplomacyOrder(type="propose_alliance", target="sun_quan", duration_rounds=5))

        if state.current_player_orders:
            return AgentPlan(
                policy=state.current_player_policy,
                orders=state.current_player_orders,
                diplomacy=diplomacy,
                reasoning_summary=state.current_player_command or "Player submitted structured actions.",
            )

        orders = self._auto_player_orders_from_text(state, text)
        return AgentPlan(
            policy=state.current_player_policy,
            orders=orders,
            diplomacy=diplomacy,
            reasoning_summary=state.current_player_command or "Shu follows a cautious balanced plan.",
        )

    def _auto_player_orders_from_text(self, state: AgenticGameState, text: str) -> list[AgentOrder]:
        orders: list[AgentOrder] = []
        units = [unit for unit in state.units if unit.faction_id == state.player_faction]
        wants_attack = any(token in text for token in ("attack", "war", "攻", "打", "北伐"))
        wants_farm = any(token in text for token in ("farm", "food", "粮", "田", "农业"))
        wants_scout = any(token in text for token in ("scout", "intel", "探", "侦"))
        wants_transfer = any(token in text for token in ("transfer", "supply", "weapon", "补给", "武器"))

        if wants_attack:
            for unit in units:
                if unit.unit_type == "army":
                    target = self._best_attack_city_for_unit(state, unit)
                    if target:
                        orders.append(AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="attack", source_city_id=unit.city_id, target_city_ids=[target]))
        if wants_farm or not orders:
            for unit in units:
                if unit.unit_type == "worker":
                    orders.append(AgentOrder(unit_id=unit.id, action="farm", region_id=unit.region_id, source_city_id=unit.city_id))
        if wants_scout or not any(order.action == "scout" for order in orders):
            for unit in units:
                if unit.unit_type == "scout":
                    target = self._first_enemy_city_neighbor(state, unit.city_id or "", unit.faction_id)
                    if target:
                        orders.append(AgentOrder(unit_id=unit.id, action="scout", source_city_id=unit.city_id, target_city_id=target))
                    break
        if wants_transfer or state.current_player_policy in ("war", "logistics"):
            for unit in units:
                if unit.unit_type == "caravan":
                    target = self._first_border_city(state, unit.faction_id) or unit.city_id
                    orders.append(AgentOrder(unit_id=unit.id, action="transfer", source_city_id=unit.city_id, target_city_id=target, resource="weapons", amount=16))
        if not wants_attack:
            for unit in units:
                if unit.unit_type == "army":
                    orders.append(AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="defend", source_city_id=unit.city_id, region_id=unit.region_id))
        return orders

    def _observation_for(self, state: AgenticGameState, faction_id: str, round_number: int) -> AgentObservation:
        units = [unit for unit in state.units if unit.faction_id == faction_id]
        alliances = [alliance.other(faction_id) for alliance in state.alliances if alliance.other(faction_id)]
        return AgentObservation(
            faction_id=faction_id,
            round=round_number,
            max_rounds=state.max_rounds,
            policy=state.policies[faction_id],
            resources=state.resources[faction_id],
            owned_regions=self._owned_regions(state, faction_id),
            visible_regions=state.region_owners,
            visible_cities=state.city_owners,
            units=units,
            neighbors={region_id: region.neighbors for region_id, region in state.regions.items()},
            city_neighbors={city_id: city.neighbors for city_id, city in state.cities.items()},
            alliances=[item for item in alliances if item],
            recent_log=[log.detail for log in state.logs[-8:]],
            player_command=state.current_player_command if faction_id != state.player_faction else "",
        )

    def _start_round(self, state: AgenticGameState) -> None:
        state.animations = []
        state.battle_events = []
        for unit in state.units:
            unit.status = "idle"
        self._log(
            state,
            f"Round {state.round}",
            "各国同时下达军令，政策、外交、补给和战役将在本回合结算。",
            tone="info",
        )

    def _apply_policies(self, state: AgenticGameState, plans: dict[str, AgentPlan]) -> None:
        for faction_id, plan in plans.items():
            old_policy = state.policies[faction_id]
            state.policies[faction_id] = plan.policy
            if old_policy != plan.policy:
                self._log(
                    state,
                    "Policy shift",
                    f"{state.factions[faction_id].display_name(222)} shifts from {old_policy} to {plan.policy}.",
                    faction_id=faction_id,
                    tone="policy",
                )

    def _apply_diplomacy(
        self,
        state: AgenticGameState,
        faction_id: str,
        diplomacy: list[DiplomacyOrder],
        diplomacy_used: dict[str, int],
    ) -> None:
        for order in diplomacy:
            if diplomacy_used[faction_id] >= DIPLOMACY_ACTIONS_PER_ROUND:
                self._log(state, "Diplomacy skipped", f"{faction_id} already used diplomacy this round.", faction_id=faction_id, tone="warning")
                continue
            if order.target not in FACTION_IDS or order.target == faction_id:
                self._log(state, "Diplomacy rejected", f"{faction_id} used an invalid diplomacy target.", faction_id=faction_id, tone="warning")
                continue
            diplomacy_used[faction_id] += 1
            pair = tuple(sorted((faction_id, order.target)))
            if order.type == "propose_alliance":
                state.alliances = [alliance for alliance in state.alliances if alliance.factions != pair]
                expires_round = state.round + max(1, min(8, order.duration_rounds))
                state.alliances.append(AgenticAlliance(factions=pair, expires_round=expires_round, source=f"{faction_id}_proposal"))
                self._log(
                    state,
                    "Alliance formed",
                    f"{state.factions[faction_id].display_name(222)} and {state.factions[order.target].display_name(222)} agree not to fight until round {expires_round}.",
                    faction_id=faction_id,
                    tone="diplomacy",
                )
            elif order.type == "break_alliance":
                before = len(state.alliances)
                state.alliances = [alliance for alliance in state.alliances if alliance.factions != pair]
                if len(state.alliances) != before:
                    state.resources[faction_id].add("gold", -12)
                    self._log(
                        state,
                        "Alliance broken",
                        f"{state.factions[faction_id].display_name(222)} breaks its treaty with {state.factions[order.target].display_name(222)} and loses legitimacy gold.",
                        faction_id=faction_id,
                        tone="diplomacy",
                    )

    def _try_execute_order(
        self,
        state: AgenticGameState,
        faction_id: str,
        order: AgentOrder,
        used_units: set[str],
        defense_bonus: dict[str, float],
        attacks: list[PendingAttack],
    ) -> None:
        unit = self._unit_by_id(state, order.unit_id or "")
        if unit is None or unit.faction_id != faction_id:
            self._log(state, "Order rejected", f"{faction_id} referenced an unavailable unit.", faction_id=faction_id, tone="warning")
            return
        if unit.id in used_units:
            self._log(state, "Order rejected", f"{unit.id} already has an order this round.", faction_id=faction_id, tone="warning")
            return
        if self._execute_order(state, faction_id, unit, order, defense_bonus, attacks):
            used_units.add(unit.id)

    def _apply_default_defense_orders(
        self,
        state: AgenticGameState,
        faction_id: str,
        used_units: set[str],
        defense_bonus: dict[str, float],
        attacks: list[PendingAttack],
    ) -> None:
        for unit in state.units:
            if unit.faction_id != faction_id or unit.id in used_units:
                continue
            order = AgentOrder(
                unit_id=unit.id,
                general_id=unit.general_id,
                action="defend",
                source_city_id=unit.city_id,
                region_id=unit.region_id,
            )
            if self._execute_order(state, faction_id, unit, order, defense_bonus, attacks):
                used_units.add(unit.id)

    def _execute_order(
        self,
        state: AgenticGameState,
        faction_id: str,
        unit: AgenticUnit,
        order: AgentOrder,
        defense_bonus: dict[str, float],
        attacks: list[PendingAttack],
    ) -> bool:
        if order.action == "rest":
            unit.readiness = min(100, unit.readiness + 28)
            unit.status = "resting"
            self._log(state, "Rest", f"{unit.id} rests and recovers readiness.", faction_id=faction_id, region_id=unit.region_id)
            return True

        if order.action == "defend":
            source_city = self._source_city_for_order(unit, order)
            if not source_city or state.city_owners.get(source_city) != faction_id:
                return self._reject(state, faction_id, "Defend rejected", f"{unit.id} is outside friendly territory.")
            general = self._general_for_unit(state, unit)
            bonus = 10.0 + unit.readiness * 0.14 * unit.power + (general.defense * 0.14 if general else 0)
            defended_cities = [source_city]
            defended_cities.extend(
                neighbor for neighbor in state.cities[source_city].neighbors if state.city_owners.get(neighbor) == faction_id
            )
            for city_id in defended_cities:
                defense_bonus[city_id] += bonus
            state.city_development[source_city] = min(1.75, state.city_development.get(source_city, 1.0) + 0.035)
            state.region_development[state.cities[source_city].region_id] = min(
                1.6,
                state.region_development.get(state.cities[source_city].region_id, 1.0) + 0.015,
            )
            unit.readiness = max(0, unit.readiness - 7)
            unit.city_id = source_city
            unit.region_id = state.cities[source_city].region_id
            unit.status = "defending"
            state.animations.append(AnimationEvent(type="defend", faction_id=faction_id, general_id=unit.general_id, city_id=source_city, tone="defense"))
            city_name = state.cities[source_city].name_cn
            self._log(state, "Defense set", f"{self._unit_label(state, unit)} fortifies {city_name}, guards nearby friendly cities, and steadies local farms.", faction_id=faction_id, region_id=unit.region_id)
            return True

        if order.action == "scout":
            if unit.unit_type != "scout":
                return self._reject(state, faction_id, "Scout rejected", f"{unit.id} is not a scout.")
            source_city = self._source_city_for_order(unit, order)
            target = order.target_city_id or (order.target_city_ids[0] if order.target_city_ids else None) or self._first_enemy_city_neighbor(state, source_city or "", faction_id)
            if not source_city or not target or target not in state.cities or target not in state.cities[source_city].neighbors:
                return self._reject(state, faction_id, "Scout rejected", f"{unit.id} has no adjacent target.")
            state.resources[faction_id].add("intel", 9)
            unit.readiness = max(0, unit.readiness - 5)
            unit.city_id = source_city
            unit.status = f"scouting {target}"
            owner = state.city_owners[target]
            self._log(
                state,
                "Scout report",
                f"{unit.id} scouts {state.cities[target].name_cn}: owner {state.factions[owner].display_name(222)}, nearby roads {len(state.cities[target].neighbors)}.",
                faction_id=faction_id,
                region_id=state.cities[target].region_id,
                tone="intel",
            )
            return True

        if order.action == "farm":
            if unit.unit_type != "worker":
                return self._reject(state, faction_id, "Farm rejected", f"{unit.id} is not a worker.")
            source_city = self._source_city_for_order(unit, order)
            if not source_city or state.city_owners.get(source_city) != faction_id:
                return self._reject(state, faction_id, "Farm rejected", f"{unit.id} is outside friendly territory.")
            unit.city_id = source_city
            unit.region_id = state.cities[source_city].region_id
            state.city_development[source_city] = min(1.65, state.city_development.get(source_city, 1.0) + 0.12)
            state.region_development[unit.region_id] = min(1.55, state.region_development.get(unit.region_id, 1.0) + 0.05)
            state.resources[faction_id].add("food", 10)
            unit.readiness = max(0, unit.readiness - 6)
            unit.status = "farming"
            self._log(state, "Farms expanded", f"{unit.id} improves fields around {state.cities[source_city].name_cn}.", faction_id=faction_id, region_id=unit.region_id, tone="economy")
            return True

        if order.action == "transfer":
            if unit.unit_type != "caravan":
                return self._reject(state, faction_id, "Transfer rejected", f"{unit.id} is not a caravan.")
            source_city = self._source_city_for_order(unit, order)
            target = (
                order.target_city_id
                or (order.target_city_ids[0] if order.target_city_ids else None)
                or self._reachable_city_in_region(state, source_city or "", order.target_region_id or "")
                or source_city
            )
            resource = order.resource or "food"
            if resource not in ("food", "weapons", "gold"):
                return self._reject(state, faction_id, "Transfer rejected", f"{resource} cannot be moved by caravan.")
            if not source_city or not target or target not in state.cities or target not in [source_city, *state.cities[source_city].neighbors]:
                return self._reject(state, faction_id, "Transfer rejected", f"{unit.id} cannot reach the target this round.")
            target_owner = state.city_owners[target]
            if target_owner != faction_id and not self._are_allied(state, faction_id, target_owner):
                return self._reject(state, faction_id, "Transfer rejected", f"{target} is not owned by an ally.")
            amount = max(1, min(40, order.amount or 12))
            if not state.resources[faction_id].spend(resource, amount):
                return self._reject(state, faction_id, "Transfer rejected", f"{faction_id} lacks {resource}.")
            delivered = max(1, int(amount * self._policy_mod(state, faction_id, "transfer")))
            self._add_city_supply(state, target, resource, delivered)
            self._add_region_supply(state, state.cities[target].region_id, resource, delivered)
            unit.city_id = target
            unit.region_id = state.cities[target].region_id
            unit.readiness = max(0, unit.readiness - 4)
            unit.status = f"delivered {resource}"
            state.animations.append(AnimationEvent(type="move", faction_id=faction_id, from_city_id=source_city, to_city_id=target, value=delivered, tone="supply"))
            self._log(
                state,
                "Supply transfer",
                f"{unit.id} delivers {delivered} {resource} to {state.cities[target].name_cn}.",
                faction_id=faction_id,
                region_id=state.cities[target].region_id,
                tone="supply",
            )
            return True

        if order.action == "attack":
            if unit.unit_type != "army":
                return self._reject(state, faction_id, "Attack rejected", f"{unit.id} is not an army.")
            source_city = self._source_city_for_order(unit, order)
            target_cities = self._target_cities_for_order(state, unit, order)
            if not source_city or not target_cities:
                return self._reject(state, faction_id, "Attack rejected", f"{unit.id} has no adjacent city target.")
            for target in target_cities:
                target_owner = state.city_owners[target]
                if target_owner == faction_id:
                    return self._reject(state, faction_id, "Attack rejected", f"{state.cities[target].name_cn} is already friendly.")
                if self._are_allied(state, faction_id, target_owner):
                    return self._reject(state, faction_id, "Attack rejected", f"Alliance blocks fighting with {target_owner}.")
            unit.readiness = max(0, unit.readiness - 16)
            unit.status = f"attacking {target}"
            first_target = target_cities[0]
            attacks.append(
                PendingAttack(
                    faction_id=faction_id,
                    unit_id=unit.id,
                    source_region_id=unit.region_id,
                    target_region_id=state.cities[first_target].region_id,
                    attack_score=0.0,
                    source_city_id=source_city,
                    target_city_ids=target_cities,
                )
            )
            state.animations.append(AnimationEvent(type="move", faction_id=faction_id, general_id=unit.general_id, from_city_id=source_city, to_city_id=first_target, tone="war"))
            self._log(state, "Attack launched", f"{self._unit_label(state, unit)} attacks {state.cities[first_target].name_cn}.", faction_id=faction_id, region_id=state.cities[first_target].region_id, tone="war")
            return True

        return False

    def _attack_score(self, state: AgenticGameState, faction_id: str, unit: AgenticUnit, target_city_id: str) -> float:
        faction = state.factions[faction_id]
        general = self._general_for_unit(state, unit)
        source_city = unit.city_id or self._first_city_for_region(unit.region_id)
        consumed_weapons = self._consume_city_supply_or_pool(state, faction_id, source_city or "", "weapons", 8)
        local_food = self._city_supply(state, source_city or "", "food")
        target = state.cities[target_city_id]
        terrain_penalty = 0.0
        if target.terrain in ("mountain", "pass"):
            terrain_penalty = 6.0
        elif target.terrain == "river":
            terrain_penalty = max(0.0, 5.0 - faction.naval * 2.5)
        intel_bonus = min(6.0, state.resources[faction_id].intel / 12)
        supply_bonus = consumed_weapons * 1.2 + min(8.0, local_food * 0.1)
        soldier_score = math.sqrt(max(1, unit.soldiers or 1000)) * 0.28
        general_score = ((general.command + general.attack) / 2) * 0.22 if general else 0
        return (
            18.0 * unit.power
            + soldier_score
            + general_score
            + unit.readiness * 0.34
            + faction.attack * 11.0
            + supply_bonus
            + intel_bonus
            - terrain_penalty
        ) * self._policy_mod(state, faction_id, "attack")

    def _resolve_attacks(
        self,
        state: AgenticGameState,
        attacks: list[PendingAttack],
        defense_bonus: dict[str, float],
    ) -> None:
        for attack in sorted(attacks, key=lambda item: (item.target_region_id, item.faction_id, item.unit_id)):
            unit = self._unit_by_id(state, attack.unit_id)
            if unit is None:
                continue
            current_city = attack.source_city_id or unit.city_id
            for target_city_id in attack.target_city_ids:
                if not current_city or target_city_id not in state.cities[current_city].neighbors:
                    self._reject(state, attack.faction_id, "Attack halted", f"{unit.id} cannot continue from {current_city} to {target_city_id}.")
                    break
                target_owner = state.city_owners[target_city_id]
                if target_owner == attack.faction_id or self._are_allied(state, attack.faction_id, target_owner):
                    break
                won = self._resolve_city_battle(state, unit, current_city, target_city_id, target_owner, defense_bonus)
                if not won:
                    unit.city_id = current_city
                    unit.region_id = state.cities[current_city].region_id
                    break
                current_city = target_city_id
                if unit.readiness < 18 or unit.soldiers < 1000:
                    break

    def _defense_score(
        self,
        state: AgenticGameState,
        defender_id: str,
        city_id: str,
        defense_bonus: dict[str, float],
    ) -> float:
        faction = state.factions[defender_id]
        defending_units = [
            unit for unit in state.units if unit.faction_id == defender_id and unit.city_id == city_id and unit.unit_type == "army"
        ]
        unit_score = 0.0
        for unit in defending_units:
            general = self._general_for_unit(state, unit)
            general_score = ((general.command + general.defense) / 2) * 0.2 if general else 0
            unit_score += 10.0 * unit.power + unit.readiness * 0.22 + math.sqrt(max(1, unit.soldiers)) * 0.24 + general_score
        city = state.cities[city_id]
        terrain_score = TERRAIN_DEFENSE.get(city.terrain, 10.0) * city.fort
        supply_score = min(13.0, self._city_supply(state, city_id, "food") * 0.12 + self._city_supply(state, city_id, "weapons") * 0.18)
        return (
            20.0
            + unit_score
            + terrain_score
            + supply_score
            + defense_bonus.get(city_id, 0.0)
            + faction.defense * 10.0
        ) * self._policy_mod(state, defender_id, "defense")

    def _resolve_city_battle(
        self,
        state: AgenticGameState,
        attacker_unit: AgenticUnit,
        source_city_id: str,
        target_city_id: str,
        defender_id: str,
        defense_bonus: dict[str, float],
    ) -> bool:
        attacker_id = attacker_unit.faction_id
        attacker_general = self._general_for_unit(state, attacker_unit)
        defender_unit = self._best_defender_at_city(state, defender_id, target_city_id)
        defender_general = self._general_for_unit(state, defender_unit) if defender_unit else None
        attacker_before = max(0, attacker_unit.soldiers)
        defender_before = defender_unit.soldiers if defender_unit else int(state.cities[target_city_id].population * 145)
        attack_score = self._attack_score(state, attacker_id, attacker_unit, target_city_id)
        defender_score = self._defense_score(state, defender_id, target_city_id, defense_bonus)
        probability = max(0.12, min(0.88, attack_score / max(1.0, attack_score + defender_score)))
        rng = self._battle_rng(state, attacker_unit.id, source_city_id, target_city_id)
        attacker_won = rng.random() < probability
        city = state.cities[target_city_id]
        attacker_name = state.factions[attacker_id].display_name(222)
        defender_name = state.factions[defender_id].display_name(222)
        factors = [
            f"odds {probability:.0%}",
            f"attack {attack_score:.1f}",
            f"defense {defender_score:.1f}",
            f"terrain {city.terrain}",
            f"fort {city.fort:.2f}",
        ]
        if defense_bonus.get(target_city_id, 0) > 0:
            factors.append("defender prepared nearby defense")
        if self._city_supply(state, target_city_id, "food") or self._city_supply(state, target_city_id, "weapons"):
            factors.append("defender had local supply")

        if attacker_won:
            attacker_loss_rate = rng.uniform(0.10, 0.28)
            defender_loss_rate = rng.uniform(0.36, 0.78)
            attacker_unit.soldiers = max(0, int(attacker_unit.soldiers * (1 - attacker_loss_rate)))
            if defender_unit:
                defender_unit.soldiers = max(0, int(defender_unit.soldiers * (1 - defender_loss_rate)))
            defender_after = defender_unit.soldiers if defender_unit else 0
            aftermath = self._defender_aftermath(state, rng, defender_unit, defender_general, target_city_id, attacker_id)
            state.city_owners[target_city_id] = attacker_id
            conquest_rewards = self._award_city_conquest(state, attacker_id, defender_id, target_city_id, attacker_loss_rate + defender_loss_rate)
            attacker_unit.city_id = target_city_id
            attacker_unit.region_id = city.region_id
            attacker_unit.readiness = max(12, attacker_unit.readiness - 10)
            attacker_unit.status = "captured city"
            if aftermath == "surrender_soldiers" and defender_after:
                joined = max(300, int(defender_after * 0.45))
                attacker_unit.soldiers = min(attacker_unit.max_soldiers or attacker_unit.soldiers + joined, attacker_unit.soldiers + joined)
                if defender_unit:
                    defender_unit.soldiers = max(0, defender_unit.soldiers - joined)
            elif aftermath == "surrender_general" and defender_unit and defender_general:
                defender_unit.faction_id = attacker_id
                defender_unit.city_id = target_city_id
                defender_unit.region_id = city.region_id
                defender_unit.status = "surrendered"
                defender_general.faction_id = attacker_id
                defender_general.city_id = target_city_id
                defender_general.status = "surrendered"
            self._remove_dead_armies(state)
            reward_text = self._conquest_reward_text(conquest_rewards)
            summary = f"{attacker_name} takes {city.name_cn}; {defender_name} outcome: {aftermath.replace('_', ' ')}. {reward_text}"
            outcome = "attacker_win"
            tone = "victory"
            state.animations.append(AnimationEvent(type="clash", faction_id=attacker_id, general_id=attacker_unit.general_id, from_city_id=source_city_id, to_city_id=target_city_id, tone="victory"))
            if aftermath.startswith("surrender"):
                state.animations.append(AnimationEvent(type="surrender", faction_id=defender_id, city_id=target_city_id, tone="diplomacy"))
        else:
            attacker_loss_rate = rng.uniform(0.22, 0.55)
            defender_loss_rate = rng.uniform(0.06, 0.25)
            attacker_unit.soldiers = max(0, int(attacker_unit.soldiers * (1 - attacker_loss_rate)))
            if defender_unit:
                defender_unit.soldiers = max(0, int(defender_unit.soldiers * (1 - defender_loss_rate)))
            defender_after = defender_unit.soldiers if defender_unit else defender_before
            attacker_unit.city_id = source_city_id
            attacker_unit.region_id = state.cities[source_city_id].region_id
            attacker_unit.readiness = max(0, attacker_unit.readiness - 16)
            attacker_unit.status = "retreating"
            self._remove_dead_armies(state)
            summary = f"{defender_name} holds {city.name_cn}; {attacker_name} retreats to {state.cities[source_city_id].name_cn}."
            outcome = "defender_win"
            aftermath = "hold"
            tone = "defense"
            state.animations.append(AnimationEvent(type="retreat", faction_id=attacker_id, general_id=attacker_unit.general_id, from_city_id=target_city_id, to_city_id=source_city_id, tone="defense"))

        battle = BattleEvent(
            round=state.round,
            attacker_faction=attacker_id,
            defender_faction=defender_id,
            attacker_general_id=attacker_unit.general_id or attacker_unit.id,
            defender_general_id=defender_unit.general_id if defender_unit else None,
            source_city_id=source_city_id,
            target_city_id=target_city_id,
            outcome=outcome,  # type: ignore[arg-type]
            aftermath=aftermath,  # type: ignore[arg-type]
            attacker_before=attacker_before,
            attacker_after=max(0, attacker_unit.soldiers),
            defender_before=defender_before,
            defender_after=max(0, defender_after),
            win_probability=round(probability, 4),
            factors=factors,
            summary=summary,
        )
        state.battle_events.append(battle)
        self._log(state, "City battle", summary, faction_id=attacker_id if attacker_won else defender_id, region_id=city.region_id, tone=tone)
        return attacker_won

    def _defender_aftermath(
        self,
        state: AgenticGameState,
        rng: random.Random,
        defender_unit: AgenticUnit | None,
        defender_general: GeneralView | None,
        lost_city_id: str,
        attacker_id: str,
    ) -> str:
        if defender_unit is None or defender_unit.soldiers <= 0:
            return "annihilated"
        retreat_target = self._retreat_city_for_defender(state, defender_unit.faction_id, lost_city_id)
        risk = defender_general.surrender_risk if defender_general else 0.08
        low_soldiers = 1.0 - min(1.0, defender_unit.soldiers / max(1, defender_unit.max_soldiers))
        loyalty_penalty = (100 - (defender_general.loyalty if defender_general else 70)) / 150
        roll = rng.random()
        if roll < risk + loyalty_penalty * 0.4:
            return "surrender_general"
        if roll < risk + loyalty_penalty * 0.4 + 0.16 + low_soldiers * 0.18:
            return "surrender_soldiers"
        if retreat_target:
            defender_unit.city_id = retreat_target
            defender_unit.region_id = state.cities[retreat_target].region_id
            defender_unit.status = "retreating"
            return "retreat"
        defender_unit.soldiers = 0
        return "annihilated"

    def _award_city_conquest(
        self,
        state: AgenticGameState,
        attacker_id: str,
        defender_id: str,
        city_id: str,
        battle_damage: float,
    ) -> dict[str, int]:
        rewards: dict[str, int] = {"food": 0, "weapons": 0, "gold": 0, "manpower": 0}
        supply = state.city_supply.setdefault(city_id, {})
        for resource in ("food", "weapons", "gold"):
            available = int(supply.get(resource, 0))
            captured = int(available * 0.65)
            if captured:
                supply[resource] = available - captured
                state.resources[attacker_id].add(resource, captured)  # type: ignore[arg-type]
                rewards[resource] = captured
        city = state.cities[city_id]
        damage_penalty = max(0.45, min(0.95, 1.0 - battle_damage * 0.18))
        manpower = max(80, int(city.population * 35 * damage_penalty))
        state.resources[attacker_id].add("manpower", manpower)
        rewards["manpower"] = manpower
        state.city_development[city_id] = max(0.58, state.city_development.get(city_id, 1.0) * max(0.72, damage_penalty * 0.92))
        city.population = max(8, int(city.population * max(0.86, damage_penalty)))
        state.animations.append(AnimationEvent(type="capture", faction_id=attacker_id, city_id=city_id, value=manpower, tone="victory"))
        self._log(
            state,
            "City spoils",
            f"{state.factions[attacker_id].display_name(222)} seizes {city.name_cn}'s stores from {state.factions[defender_id].display_name(222)}: {self._conquest_reward_text(rewards)}",
            faction_id=attacker_id,
            region_id=city.region_id,
            tone="victory",
        )
        return rewards

    def _conquest_reward_text(self, rewards: dict[str, int]) -> str:
        labels = {"food": "food", "weapons": "weapons", "gold": "gold", "manpower": "manpower"}
        parts = [f"+{amount} {labels[resource]}" for resource, amount in rewards.items() if amount > 0]
        return "Spoils: " + (", ".join(parts) if parts else "no stores survived")

    def _battle_rng(self, state: AgenticGameState, unit_id: str, source_city_id: str, target_city_id: str) -> random.Random:
        seed = f"{state.round}:{unit_id}:{source_city_id}:{target_city_id}"
        return random.Random(seed)

    def _apply_round_income(self, state: AgenticGameState) -> None:
        totals = {faction_id: Resources() for faction_id in FACTION_IDS}
        for city_id, owner in state.city_owners.items():
            city = state.cities[city_id]
            development = state.city_development.get(city_id, 1.0)
            totals[owner].add("food", int((city.population * 0.08 + 3) * development * self._policy_mod(state, owner, "food")))
            totals[owner].add("gold", int((city.economy * 0.06 + 2) * development * self._policy_mod(state, owner, "gold")))
            totals[owner].add("weapons", int((city.economy * 0.04 + 2) * development * self._policy_mod(state, owner, "weapons")))
            totals[owner].add("manpower", int((city.population * 0.03 + 1) * self._policy_mod(state, owner, "manpower")))
        for faction_id, income in totals.items():
            resources = state.resources[faction_id]
            resources.add("food", income.food)
            resources.add("gold", income.gold)
            resources.add("weapons", income.weapons)
            resources.add("manpower", income.manpower)
            self._log(
                state,
                "Income",
                f"{state.factions[faction_id].display_name(222)} gains +{income.food} food, +{income.weapons} weapons, +{income.gold} gold.",
                faction_id=faction_id,
                tone="economy",
            )

    def _recover_units(self, state: AgenticGameState) -> None:
        for unit in state.units:
            owner = unit.faction_id
            recovery = 5
            if state.resources[owner].spend("food", 1):
                recovery += 4
            if unit.status == "resting":
                recovery += 10
            if unit.unit_type == "army" and unit.soldiers > 0:
                general = self._general_for_unit(state, unit)
                food_need = general.food_need if general else max(1, unit.soldiers // 4000)
                if state.resources[owner].spend("food", food_need):
                    recovery += 2
                    reinforce = min((unit.max_soldiers or unit.soldiers) - unit.soldiers, int(state.resources[owner].manpower * 0.03))
                    if reinforce > 0 and state.resources[owner].spend("manpower", reinforce):
                        unit.soldiers += reinforce
            if unit.city_id and self._city_supply(state, unit.city_id, "food") > 0:
                self._consume_city_supply(state, unit.city_id, "food", 1)
                recovery += 3
            unit.readiness = min(100, unit.readiness + recovery)
        self._sync_generals(state)

    def _expire_alliances(self, state: AgenticGameState) -> None:
        before = len(state.alliances)
        state.alliances = [alliance for alliance in state.alliances if alliance.expires_round > state.round]
        if len(state.alliances) != before:
            self._log(state, "Treaty expired", "An alliance expires and border fighting is legal again.", tone="diplomacy")

    def _update_victory(self, state: AgenticGameState) -> None:
        region_counts = self._region_counts(state)
        region_needed = math.ceil(len(state.regions) * DOMINANCE_SHARE)
        for faction_id, count in region_counts.items():
            if count >= region_needed:
                state.finished = True
                state.winner = faction_id
                self._log(
                    state,
                    "Dominance victory",
                    f"{state.factions[faction_id].display_name(222)} controls {count}/{len(state.regions)} regions and wins early.",
                    faction_id=faction_id,
                    tone="victory",
                )
                return
        counts = self._city_counts(state) if state.city_owners else self._region_counts(state)
        total = len(state.city_owners) if state.city_owners else len(state.regions)
        needed = math.ceil(total * DOMINANCE_SHARE)
        for faction_id, count in counts.items():
            if count >= needed:
                state.finished = True
                state.winner = faction_id
                self._log(
                    state,
                    "Dominance victory",
                    f"{state.factions[faction_id].display_name(222)} controls {count}/{total} cities and wins early.",
                    faction_id=faction_id,
                    tone="victory",
                )
                return
        if state.round >= state.max_rounds:
            self._finish_by_score(state, f"Round {state.max_rounds} reached")

    def _finish_by_score(self, state: AgenticGameState, reason: str) -> None:
        winner = max(FACTION_IDS, key=lambda faction_id: self._score_faction(state, faction_id))
        state.finished = True
        state.winner = winner
        self._log(
            state,
            "Score victory",
            f"{reason}. {state.factions[winner].display_name(222)} wins by total score.",
            faction_id=winner,
            tone="victory",
        )

    def _score_faction(self, state: AgenticGameState, faction_id: str) -> int:
        region_score = len(self._owned_regions(state, faction_id)) * 18
        city_score = sum(1 for owner in state.city_owners.values() if owner == faction_id) * 7
        ready_score = sum(unit.readiness for unit in state.units if unit.faction_id == faction_id) // 12
        soldier_score = sum(unit.soldiers for unit in state.units if unit.faction_id == faction_id and unit.unit_type == "army") // 3500
        resources = state.resources[faction_id]
        resource_score = (resources.food + resources.weapons + resources.gold + resources.manpower) // 35
        return region_score + city_score + ready_score + soldier_score + resource_score

    def _region_counts(self, state: AgenticGameState) -> dict[str, int]:
        return {faction_id: len(self._owned_regions(state, faction_id)) for faction_id in FACTION_IDS}

    def _city_counts(self, state: AgenticGameState) -> dict[str, int]:
        return {faction_id: sum(1 for owner in state.city_owners.values() if owner == faction_id) for faction_id in FACTION_IDS}

    def _owned_regions(self, state: AgenticGameState, faction_id: str) -> list[str]:
        return [region_id for region_id, owner in state.region_owners.items() if owner == faction_id]

    def _are_allied(self, state: AgenticGameState, faction_a: str, faction_b: str) -> bool:
        return any(alliance.includes_pair(faction_a, faction_b) for alliance in state.alliances)

    def _policy_mod(self, state: AgenticGameState, faction_id: str, stat: str) -> float:
        return POLICY_MODIFIERS[state.policies[faction_id]][stat]

    def _unit_by_id(self, state: AgenticGameState, unit_id: str) -> AgenticUnit | None:
        for unit in state.units:
            if unit.id == unit_id:
                return unit
        return None

    def _first_enemy_neighbor(self, state: AgenticGameState, region_id: str, faction_id: str) -> str | None:
        for neighbor in state.regions[region_id].neighbors:
            owner = state.region_owners[neighbor]
            if owner != faction_id and not self._are_allied(state, faction_id, owner):
                return neighbor
        return None

    def _first_border_region(self, state: AgenticGameState, faction_id: str) -> str | None:
        for region_id in sorted(self._owned_regions(state, faction_id)):
            if self._first_enemy_neighbor(state, region_id, faction_id):
                return region_id
        return None

    def _best_attack_target_for_unit(self, state: AgenticGameState, unit: AgenticUnit) -> str | None:
        candidates = []
        for neighbor in state.regions[unit.region_id].neighbors:
            owner = state.region_owners[neighbor]
            if owner != unit.faction_id and not self._are_allied(state, unit.faction_id, owner):
                region = state.regions[neighbor]
                candidates.append((region.population + region.economy, neighbor))
        if not candidates:
            return None
        return max(candidates)[1]

    def _source_city_for_order(self, unit: AgenticUnit, order: AgentOrder) -> str | None:
        return order.source_city_id or unit.city_id or self._first_city_for_region(order.region_id or unit.region_id)

    def _target_cities_for_order(self, state: AgenticGameState, unit: AgenticUnit, order: AgentOrder) -> list[str]:
        source_city = self._source_city_for_order(unit, order)
        if not source_city:
            return []
        raw_targets = list(order.target_city_ids)
        if order.target_city_id:
            raw_targets.insert(0, order.target_city_id)
        region_target_from_order = False
        if not raw_targets and order.target_region_id:
            region_target = self._best_city_in_region_for_attack(state, unit, order.target_region_id)
            if region_target:
                raw_targets.append(region_target)
                region_target_from_order = True
        if not raw_targets:
            best = self._best_attack_city_for_unit(state, unit)
            if best:
                raw_targets.append(best)
        targets: list[str] = []
        current = source_city
        mobility_limit = max(1, 1 + ((self._general_for_unit(state, unit).mobility if self._general_for_unit(state, unit) else 70) // 34))
        for target in raw_targets:
            if target not in state.cities:
                break
            region_compatible = bool(
                region_target_from_order
                and order.target_region_id
                and order.target_region_id in state.regions[unit.region_id].neighbors
                and target == raw_targets[0]
            )
            if target not in state.cities[current].neighbors and not region_compatible:
                break
            targets.append(target)
            current = target
            if len(targets) >= mobility_limit:
                break
        return targets

    def _reachable_city_in_region(self, state: AgenticGameState, source_city_id: str, region_id: str) -> str | None:
        if not source_city_id or not region_id:
            return None
        for city in state.cities.values():
            if city.region_id == region_id and city.id in [source_city_id, *state.cities[source_city_id].neighbors]:
                return city.id
        return self._first_city_for_region(region_id)

    def _best_city_in_region_for_attack(self, state: AgenticGameState, unit: AgenticUnit, region_id: str) -> str | None:
        source_city = unit.city_id
        if not source_city:
            return None
        candidates = []
        region_is_adjacent = region_id in state.regions[unit.region_id].neighbors or region_id == unit.region_id
        for city in state.cities.values():
            if city.region_id != region_id:
                continue
            owner = state.city_owners[city.id]
            if owner == unit.faction_id:
                continue
            if self._are_allied(state, unit.faction_id, owner):
                return city.id
            if city.id in state.cities[source_city].neighbors or region_is_adjacent:
                candidates.append((city.population + city.economy + city.fort * 15, city.id))
        if not candidates:
            return None
        return max(candidates)[1]

    def _best_attack_city_for_unit(self, state: AgenticGameState, unit: AgenticUnit) -> str | None:
        if not unit.city_id or unit.city_id not in state.cities:
            return None
        candidates = []
        for neighbor in state.cities[unit.city_id].neighbors:
            owner = state.city_owners[neighbor]
            if owner != unit.faction_id and not self._are_allied(state, unit.faction_id, owner):
                city = state.cities[neighbor]
                candidates.append((city.population + city.economy + city.fort * 15, neighbor))
        if not candidates:
            return None
        return max(candidates)[1]

    def _first_enemy_city_neighbor(self, state: AgenticGameState, city_id: str, faction_id: str) -> str | None:
        city = state.cities.get(city_id)
        if not city:
            return None
        for neighbor in city.neighbors:
            owner = state.city_owners[neighbor]
            if owner != faction_id and not self._are_allied(state, faction_id, owner):
                return neighbor
        return None

    def _first_border_city(self, state: AgenticGameState, faction_id: str) -> str | None:
        for city_id in sorted(city_id for city_id, owner in state.city_owners.items() if owner == faction_id):
            if self._first_enemy_city_neighbor(state, city_id, faction_id):
                return city_id
        return next((city_id for city_id, owner in state.city_owners.items() if owner == faction_id), None)

    def _first_city_for_region(self, region_id: str) -> str | None:
        for city in self.city_config.values():
            if city.region_id == region_id:
                return city.id
        return None

    def _general_for_unit(self, state: AgenticGameState, unit: AgenticUnit | None) -> GeneralView | None:
        if unit is None or not unit.general_id:
            return None
        return state.generals.get(unit.general_id)

    def _unit_label(self, state: AgenticGameState, unit: AgenticUnit) -> str:
        general = self._general_for_unit(state, unit)
        if general:
            return f"{general.name_cn}({unit.soldiers:,})"
        return unit.id

    def _best_defender_at_city(self, state: AgenticGameState, defender_id: str, city_id: str) -> AgenticUnit | None:
        defenders = [
            unit for unit in state.units
            if unit.faction_id == defender_id and unit.unit_type == "army" and unit.city_id == city_id and unit.soldiers > 0
        ]
        if not defenders:
            return None
        return max(defenders, key=lambda unit: (unit.soldiers, unit.readiness, unit.power))

    def _retreat_city_for_defender(self, state: AgenticGameState, defender_id: str, lost_city_id: str) -> str | None:
        for neighbor in state.cities[lost_city_id].neighbors:
            if state.city_owners.get(neighbor) == defender_id:
                return neighbor
        return None

    def _add_city_supply(self, state: AgenticGameState, city_id: str, resource: ResourceType, amount: int) -> None:
        state.city_supply.setdefault(city_id, {})
        state.city_supply[city_id][resource] = state.city_supply[city_id].get(resource, 0) + max(0, int(amount))

    def _city_supply(self, state: AgenticGameState, city_id: str, resource: ResourceType) -> int:
        return int(state.city_supply.get(city_id, {}).get(resource, 0))

    def _consume_city_supply(self, state: AgenticGameState, city_id: str, resource: ResourceType, amount: int) -> int:
        available = self._city_supply(state, city_id, resource)
        spent = min(available, max(0, int(amount)))
        if spent:
            state.city_supply[city_id][resource] = available - spent
        return spent

    def _consume_city_supply_or_pool(
        self,
        state: AgenticGameState,
        faction_id: str,
        city_id: str,
        resource: ResourceType,
        amount: int,
    ) -> int:
        spent = self._consume_city_supply(state, city_id, resource, amount)
        if spent >= amount:
            return spent
        remaining = amount - spent
        if state.resources[faction_id].spend(resource, remaining):
            return amount
        return spent

    def _remove_dead_armies(self, state: AgenticGameState) -> None:
        live_units: list[AgenticUnit] = []
        for unit in state.units:
            if unit.unit_type == "army" and unit.soldiers <= 0:
                general = self._general_for_unit(state, unit)
                if general:
                    general.soldiers = 0
                    general.city_id = None
                    general.status = "lost"
                continue
            live_units.append(unit)
        state.units = live_units

    def _sync_generals(self, state: AgenticGameState) -> None:
        for general in state.generals.values():
            general.unit_id = None
        for unit in state.units:
            general = self._general_for_unit(state, unit)
            if not general:
                continue
            general.unit_id = unit.id
            general.faction_id = unit.faction_id
            general.city_id = unit.city_id
            general.soldiers = max(0, unit.soldiers)
            general.max_soldiers = max(general.max_soldiers, unit.max_soldiers)
            general.status = unit.status

    def _add_region_supply(self, state: AgenticGameState, region_id: str, resource: ResourceType, amount: int) -> None:
        state.regional_supply.setdefault(region_id, {})
        state.regional_supply[region_id][resource] = state.regional_supply[region_id].get(resource, 0) + max(0, int(amount))

    def _region_supply(self, state: AgenticGameState, region_id: str, resource: ResourceType) -> int:
        return int(state.regional_supply.get(region_id, {}).get(resource, 0))

    def _consume_region_supply(self, state: AgenticGameState, region_id: str, resource: ResourceType, amount: int) -> int:
        available = self._region_supply(state, region_id, resource)
        spent = min(available, max(0, int(amount)))
        if spent:
            state.regional_supply[region_id][resource] = available - spent
        return spent

    def _consume_supply_or_pool(
        self,
        state: AgenticGameState,
        faction_id: str,
        region_id: str,
        resource: ResourceType,
        amount: int,
    ) -> int:
        spent = self._consume_region_supply(state, region_id, resource, amount)
        if spent >= amount:
            return spent
        remaining = amount - spent
        if state.resources[faction_id].spend(resource, remaining):
            return amount
        return spent

    def _clear_pressure_for_region(self, state: AgenticGameState, region_id: str) -> None:
        state.region_pressure = {
            key: value for key, value in state.region_pressure.items() if not key.endswith(f":{region_id}")
        }

    def _retreat_defenders(self, state: AgenticGameState, defender_id: str, lost_region_id: str) -> None:
        retreat_target = None
        for neighbor in state.regions[lost_region_id].neighbors:
            if state.region_owners[neighbor] == defender_id:
                retreat_target = neighbor
                break
        for unit in state.units:
            if unit.faction_id == defender_id and unit.region_id == lost_region_id:
                if retreat_target:
                    unit.region_id = retreat_target
                    unit.status = "retreating"
                unit.readiness = max(0, unit.readiness - 24)

    def _reject(self, state: AgenticGameState, faction_id: str, title: str, detail: str) -> bool:
        self._log(state, title, detail, faction_id=faction_id, tone="warning")
        return False

    def _log(
        self,
        state: AgenticGameState,
        title: str,
        detail: str,
        faction_id: str | None = None,
        region_id: str | None = None,
        tone: str = "neutral",
        round_number: int | None = None,
    ) -> None:
        from hwsim.agentic.models import RoundLog

        state.logs.append(
            RoundLog(
                round=state.round if round_number is None else round_number,
                title=title,
                detail=detail,
                faction_id=faction_id,
                region_id=region_id,
                tone=tone,
            )
        )


def game_state_to_dict(state: AgenticGameState) -> dict[str, Any]:
    return state.model_dump(mode="json")
