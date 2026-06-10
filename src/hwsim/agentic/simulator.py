from __future__ import annotations

import math
import random
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from hwsim.agentic.agents import (
    AdvisorProvider,
    AgentProvider,
    DeterministicAdvisorProvider,
    LocalCodexAdvisorError,
    LocalCodexAdvisorProvider,
    MockAgentProvider,
)
from hwsim.agentic.city_data import RoadConfig, load_city_graph, load_general_seeds
from hwsim.agentic.models import (
    ActiveBattle,
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
    CityStackView,
    DiplomacyOrder,
    FactionView,
    GeneralDiscoveryEvent,
    GeneralView,
    GameView,
    IncidentEvent,
    PendingAttack,
    Policy,
    RealMapProvinceView,
    RealMapRegionPolygon,
    RealMapStateLabel,
    RealMapPolyline,
    RealMapView,
    RoadView,
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
ALLIANCE_DURATION_ROUNDS = 10
DEFAULT_SCENARIO = Path("configs/scenarios/sanguo_shu_unification_demo.json")
REAL_MAP_CONFIG = Path("configs/maps/sanguo_real_map.json")
REAL_MAP_FILE = Path("data/maps/sanguo_real_map_prepared.json")
REAL_STATE_FILE = Path("configs/maps/sanguo_state_regions.json")

ACTION_COSTS = {
    "rest": 1,
    "move": 1,
    "attack": 2,
    "defend": 1,
    "scout": 1,
    "farm": 1,
    "transfer": 1,
}

MOVE_FOOD_COST = 2
ATTACK_FOOD_COST = 5
ARMY_GOLD_UPKEEP_DIVISOR = 8500
GOLD_RECRUIT_COST_PER_1000 = 4
INITIAL_GENERAL_COUNTS = {"cao": 12, "liu_bei": 8, "sun_quan": 7}
MIN_BATTLE_ROUNDS = 2
MAX_BATTLE_ROUNDS = 10

GENERAL_FAME_PRIORITY = {
    "liu_bei": ["zhuge_liang", "liu_bei", "guan_yu", "zhang_fei", "zhao_yun", "ma_chao", "huang_zhong", "wei_yan", "jiang_wei"],
    "cao": ["cao_cao", "sima_yi", "zhang_liao", "xu_huang", "xiahou_dun", "cao_ren", "dian_wei", "jia_xu", "zhang_he"],
    "sun_quan": ["zhou_yu", "lu_xun", "lu_meng", "sun_quan", "gan_ning", "taishi_ci", "huang_gai", "cheng_pu"],
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
        local_codex_advisor_provider: AdvisorProvider | None = None,
    ) -> None:
        self.scenario = scenario
        self.map_config = map_config
        self.agent_provider = agent_provider or MockAgentProvider()
        self.fallback_provider = fallback_provider or MockAgentProvider()
        self.advisor_provider = advisor_provider or DeterministicAdvisorProvider()
        self.local_codex_advisor_provider = local_codex_advisor_provider or LocalCodexAdvisorProvider()
        self.real_map_view = self._load_real_map_view()
        self.city_graph = load_city_graph()
        self.city_config = self.city_graph.city_map()
        self.road_config = self.city_graph.roads
        self.road_lookup = self.city_graph.road_map()
        self.general_seeds = load_general_seeds(city_ids=set(self.city_config))

    @classmethod
    def from_default_scenario(
        cls,
        agent_provider: AgentProvider | None = None,
        fallback_provider: AgentProvider | None = None,
        advisor_provider: AdvisorProvider | None = None,
        local_codex_advisor_provider: AdvisorProvider | None = None,
    ) -> "AgenticGameEngine":
        scenario, map_config, _events, _style = load_bundle(DEFAULT_SCENARIO)
        return cls(
            scenario,
            map_config,
            agent_provider=agent_provider,
            fallback_provider=fallback_provider,
            advisor_provider=advisor_provider,
            local_codex_advisor_provider=local_codex_advisor_provider,
        )

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
        self._clear_advisor_cache(state)
        return state

    def resolve_round(self, state: AgenticGameState) -> AgenticGameState:
        if state.finished:
            return state
        self._update_victory(state)
        if state.finished:
            return state
        round_number = state.round + 1
        plans = self._collect_plans(state, round_number)
        state.last_plans = plans
        state.round = round_number

        self._start_round(state)
        self._apply_random_incidents(state)
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

        self._open_or_reinforce_attacks(state, attacks, defense_bonus)
        self._advance_active_battles(state, defense_bonus)
        self._resolve_stranded_units(state)
        self._sync_generals(state)
        state.region_owners = self._derive_region_owners_from_cities(state.city_owners, state.regions)
        self._apply_round_income(state)
        self._recover_units(state)
        self._discover_generals(state)
        self._resolve_stranded_units(state)
        self._expire_alliances(state)
        self._update_victory(state)
        state.current_player_orders = []
        state.current_player_diplomacy = []
        self._clear_advisor_cache(state)
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
            roads=self._road_views(),
            city_stacks=self._city_stacks(state),
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
            active_battles=state.active_battles,
            incident_events=state.incident_events[-12:],
            general_discovery_events=state.general_discovery_events[-12:],
            animations=state.animations,
            alliances=state.alliances,
            logs=state.logs[-80:],
            current_player_command=state.current_player_command,
            current_player_policy=state.current_player_policy,
            current_player_orders=state.current_player_orders,
            advisor_recommendation=self._advisor_recommendation_for_view(state),
            advisor_source=state.advisor_source,
            advisor_error=state.advisor_error,
        )

    def _advisor_recommendation_for_view(self, state: AgenticGameState) -> AdvisorRecommendation:
        if state.cached_advisor_recommendation and state.advisor_cache_round == state.round:
            return state.cached_advisor_recommendation
        return self.recommend_player_plan(state)

    def recommend_player_plan(self, state: AgenticGameState) -> AdvisorRecommendation:
        observation = self._observation_for(state, state.player_faction, min(state.round + 1, state.max_rounds))
        try:
            recommendation = self.advisor_provider.recommend(observation)
        except Exception as exc:
            fallback = DeterministicAdvisorProvider().recommend(observation)
            fallback.summary = f"规则军师已接管：原建议出错（{exc}）。{fallback.summary}"
            return fallback
        return AdvisorRecommendation.model_validate(recommendation)

    def request_local_codex_advisor(self, state: AgenticGameState) -> AdvisorRecommendation:
        observation = self._observation_for(state, state.player_faction, min(state.round + 1, state.max_rounds))
        try:
            recommendation = AdvisorRecommendation.model_validate(self.local_codex_advisor_provider.recommend(observation))
            self._ensure_legal_player_recommendation(state, recommendation)
            state.cached_advisor_recommendation = recommendation
            state.advisor_source = "local_codex"
            state.advisor_error = ""
        except Exception as exc:
            fallback = DeterministicAdvisorProvider().recommend(observation)
            fallback.summary = f"本地Codex军师暂不可用，规则军师已接管。{fallback.summary}"
            state.cached_advisor_recommendation = fallback
            state.advisor_source = "local_codex_fallback"
            state.advisor_error = str(exc)
        state.advisor_cache_round = state.round
        return state.cached_advisor_recommendation

    def _clear_advisor_cache(self, state: AgenticGameState) -> None:
        state.cached_advisor_recommendation = None
        state.advisor_source = "deterministic"
        state.advisor_error = ""
        state.advisor_cache_round = None

    def _ensure_legal_player_recommendation(self, state: AgenticGameState, recommendation: AdvisorRecommendation) -> None:
        if recommendation.policy not in POLICY_MODIFIERS:
            raise LocalCodexAdvisorError(f"非法政策：{recommendation.policy}")
        player = state.player_faction
        units = {unit.id: unit for unit in state.units if unit.faction_id == player}
        if not recommendation.orders:
            raise LocalCodexAdvisorError("本地 Codex 没有返回任何军令。")
        seen_units: set[str] = set()
        repaired_orders: list[AgentOrder] = []
        repaired_count = 0
        for order in recommendation.orders:
            if not order.unit_id or order.unit_id not in units:
                raise LocalCodexAdvisorError(f"军令引用了不存在的蜀汉单位：{order.unit_id or '空'}")
            if order.unit_id in seen_units:
                raise LocalCodexAdvisorError(f"同一单位被重复下令：{order.unit_id}")
            seen_units.add(order.unit_id)
            unit = units[order.unit_id]
            try:
                self._ensure_legal_player_order(state, unit, order)
                repaired_orders.append(order)
            except LocalCodexAdvisorError:
                safe_order = self._safe_player_order(unit)
                self._ensure_legal_player_order(state, unit, safe_order)
                repaired_orders.append(safe_order)
                repaired_count += 1
        if repaired_count:
            recommendation.orders = repaired_orders
            recommendation.summary = f"{recommendation.summary}（{repaired_count}条不合法军令已自动改为固守。）"
        for order in recommendation.diplomacy:
            if order.target not in FACTION_IDS or order.target == player:
                raise LocalCodexAdvisorError(f"非法外交目标：{order.target}")
            if order.duration_rounds <= 0:
                raise LocalCodexAdvisorError("外交时长必须大于 0。")

    def _safe_player_order(self, unit: AgenticUnit) -> AgentOrder:
        return AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="defend", region_id=unit.region_id, source_city_id=unit.city_id)

    def _ensure_legal_player_order(self, state: AgenticGameState, unit: AgenticUnit, order: AgentOrder) -> None:
        faction_id = state.player_faction
        source_city = self._source_city_for_order(unit, order)
        if source_city and unit.city_id and source_city != unit.city_id:
            raise LocalCodexAdvisorError(f"{unit.id} 不在 {source_city}，不能从该城出发。")
        if order.action == "rest":
            return
        if order.action == "defend":
            if not source_city or state.city_owners.get(source_city) != faction_id:
                raise LocalCodexAdvisorError(f"{unit.id} 不能在非己方城池固守。")
            return
        if order.action == "farm":
            if unit.unit_type != "worker":
                raise LocalCodexAdvisorError(f"{unit.id} 不是屯田单位。")
            if not source_city or state.city_owners.get(source_city) != faction_id:
                raise LocalCodexAdvisorError(f"{unit.id} 不能在非己方城池屯田。")
            return
        if order.action == "move":
            target = order.target_city_id or (order.target_city_ids[0] if order.target_city_ids else None)
            self._ensure_adjacent_city_target(state, unit, source_city, target, allow_enemy=False)
            return
        if order.action == "scout":
            if unit.unit_type != "scout":
                raise LocalCodexAdvisorError(f"{unit.id} 不是斥候。")
            target = order.target_city_id or (order.target_city_ids[0] if order.target_city_ids else None)
            self._ensure_adjacent_city_target(state, unit, source_city, target, allow_enemy=True)
            return
        if order.action == "transfer":
            if unit.unit_type != "caravan":
                raise LocalCodexAdvisorError(f"{unit.id} 不是辎重队。")
            if order.resource not in ("food", "weapons", "gold"):
                raise LocalCodexAdvisorError(f"转运资源非法：{order.resource}")
            target = order.target_city_id or (order.target_city_ids[0] if order.target_city_ids else None) or source_city
            self._ensure_adjacent_city_target(state, unit, source_city, target, allow_enemy=False, allow_same=True)
            return
        if order.action == "reinforce":
            if unit.unit_type != "army":
                raise LocalCodexAdvisorError(f"{unit.id} 不是战斗部队。")
            if self._battle_for_order(state, order, unit) is None:
                raise LocalCodexAdvisorError(f"{unit.id} 没有可增援的相邻战役。")
            return
        if order.action == "retreat":
            if unit.unit_type != "army" or self._active_battle_for_unit(state, unit.id) is None:
                raise LocalCodexAdvisorError(f"{unit.id} 不在可撤退战役中。")
            return
        if order.action == "attack":
            if unit.unit_type != "army":
                raise LocalCodexAdvisorError(f"{unit.id} 不是战斗部队。")
            raw_targets = [order.target_city_id] if order.target_city_id else []
            raw_targets.extend(order.target_city_ids)
            if not source_city or not raw_targets:
                raise LocalCodexAdvisorError(f"{unit.id} 进攻必须指定相邻目标城。")
            targets = self._target_cities_for_order(state, unit, order)
            if len(targets) != len(raw_targets):
                raise LocalCodexAdvisorError(f"{unit.id} 的进攻路线不是连续相邻道路。")
            for target in targets:
                owner = state.city_owners[target]
                if owner == faction_id or self._are_allied(state, faction_id, owner):
                    raise LocalCodexAdvisorError(f"{unit.id} 不能进攻友城或盟友城池。")
            return
        raise LocalCodexAdvisorError(f"未知军令：{order.action}")

    def _ensure_adjacent_city_target(
        self,
        state: AgenticGameState,
        unit: AgenticUnit,
        source_city: str | None,
        target_city: str | None,
        *,
        allow_enemy: bool,
        allow_same: bool = False,
    ) -> None:
        if not source_city or source_city not in state.cities or not target_city or target_city not in state.cities:
            raise LocalCodexAdvisorError(f"{unit.id} 缺少合法出发城或目标城。")
        if target_city == source_city:
            if allow_same:
                return
            raise LocalCodexAdvisorError(f"{unit.id} 目标城不能与出发城相同。")
        if self._road_between(source_city, target_city) is None:
            raise LocalCodexAdvisorError(f"{state.cities[source_city].name_cn}到{state.cities[target_city].name_cn}没有相邻道路。")
        owner = state.city_owners[target_city]
        if not allow_enemy and owner != unit.faction_id and not self._are_allied(state, unit.faction_id, owner):
            raise LocalCodexAdvisorError(f"{state.cities[target_city].name_cn}不是己方或盟友城池。")

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

    def _road_views(self) -> list[RoadView]:
        return [
            RoadView(
                from_city_id=road.from_city_id,
                to_city_id=road.to_city_id,
                route_type=road.route_type,
                distance_km=road.distance_km,
                food_cost=road.food_cost,
                gold_cost=road.gold_cost,
                soldier_loss_bps=road.soldier_loss_bps,
                readiness_cost=road.readiness_cost,
                source_note=road.source_note,
            )
            for road in self.road_config
        ]

    def _city_road_views(self) -> dict[str, list[RoadView]]:
        by_city: dict[str, list[RoadView]] = defaultdict(list)
        for road in self._road_views():
            by_city[road.from_city_id].append(road)
            by_city[road.to_city_id].append(road)
        return dict(by_city)

    def _city_stacks(self, state: AgenticGameState) -> list[CityStackView]:
        grouped: dict[tuple[str, str], list[GeneralView]] = defaultdict(list)
        for general in state.generals.values():
            if general.city_id and general.soldiers > 0:
                grouped[(general.city_id, general.faction_id)].append(general)
        stacks: list[CityStackView] = []
        for (city_id, faction_id), generals in grouped.items():
            leader = self._stack_leader(generals, faction_id)
            total_soldiers = sum(max(0, general.soldiers) for general in generals)
            power_score = sum((general.command + general.attack + general.defense) / 3 for general in generals) + math.sqrt(max(1, total_soldiers))
            stacks.append(
                CityStackView(
                    city_id=city_id,
                    faction_id=faction_id,
                    leader_general_id=leader.id,
                    general_count=len(generals),
                    total_soldiers=total_soldiers,
                    power_score=round(power_score, 2),
                )
            )
        return sorted(stacks, key=lambda item: (item.city_id, item.faction_id))

    def _stack_leader(self, generals: list[GeneralView], faction_id: str) -> GeneralView:
        fame = {general_id: index for index, general_id in enumerate(GENERAL_FAME_PRIORITY.get(faction_id, []))}
        return min(
            generals,
            key=lambda general: (
                fame.get(general.id, 999),
                -(general.command + general.attack + general.defense),
                -general.soldiers,
                general.id,
            ),
        )

    def _initial_generals(self) -> dict[str, GeneralView]:
        selected = self._select_initial_general_seeds()
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
            for seed in selected
        }

    def _select_initial_general_seeds(self) -> list[Any]:
        selected = []
        priority = {
            "cao": ["cao_cao", "xiahou_dun", "zhang_liao", "sima_yi", "xu_huang", "dian_wei", "cao_ren", "jia_xu"],
            "liu_bei": ["guan_yu", "zhang_fei", "zhao_yun", "zhuge_liang", "ma_chao"],
            "sun_quan": ["sun_quan", "zhou_yu", "lu_xun", "gan_ning", "lu_meng"],
        }
        by_id = {seed.id: seed for seed in self.general_seeds}
        for faction_id in FACTION_IDS:
            faction_seeds = [seed for seed in self.general_seeds if seed.faction_id == faction_id]
            chosen_ids: list[str] = []
            for seed_id in priority[faction_id]:
                if seed_id in by_id:
                    chosen_ids.append(seed_id)
            for seed in sorted(faction_seeds, key=lambda item: (item.starting_city_id, item.id)):
                if len(chosen_ids) >= INITIAL_GENERAL_COUNTS[faction_id]:
                    break
                if seed.id not in chosen_ids:
                    chosen_ids.append(seed.id)
            selected.extend(by_id[seed_id] for seed_id in chosen_ids[: INITIAL_GENERAL_COUNTS[faction_id]])
        return selected

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
            explicit_region = str(province.get("region_id", ""))
            region_id = explicit_region if explicit_region in self._region_ids() else self._display_region_for_province(name, province.get("owner"), state_records)
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
            region_polygons=[
                RealMapRegionPolygon.model_validate(item)
                for item in prepared.get("region_polygons", [])
            ],
            rivers=[
                RealMapPolyline.model_validate(item)
                for item in prepared.get("rivers", [])
            ],
            terrain_lines=[
                RealMapPolyline.model_validate(item)
                for item in prepared.get("terrain_lines", [])
            ],
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
            targets = []
            if any(token in text for token in ("wei", "魏", "曹")):
                targets.append("cao")
            if any(token in text for token in ("wu", "吴", "孙")) or not targets:
                targets.append("sun_quan")
            for target in targets:
                if not self._are_allied(state, state.player_faction, target):
                    diplomacy.append(DiplomacyOrder(type="propose_alliance", target=target, duration_rounds=ALLIANCE_DURATION_ROUNDS))

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
            roads=self._road_views(),
            city_roads=self._city_road_views(),
            alliances=[item for item in alliances if item],
            recent_log=[log.detail for log in state.logs[-8:]],
            player_command=state.current_player_command if faction_id == state.player_faction else "",
            active_battles=state.active_battles,
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
                accepted, chance = self._alliance_vote(state, faction_id, order.target)
                if not accepted:
                    self._log(
                        state,
                        "结盟受阻",
                        f"{state.factions[order.target].display_name(222)}拒绝与{state.factions[faction_id].display_name(222)}结盟；朝议支持率约{chance:.0%}。",
                        faction_id=order.target,
                        tone="diplomacy",
                    )
                    continue
                state.alliances = [alliance for alliance in state.alliances if alliance.factions != pair]
                expires_round = state.round + ALLIANCE_DURATION_ROUNDS
                state.alliances.append(AgenticAlliance(factions=pair, expires_round=expires_round, source=f"{faction_id}_proposal"))
                self._log(
                    state,
                    "盟约缔结",
                    f"{state.factions[faction_id].display_name(222)}与{state.factions[order.target].display_name(222)}定下十回合盟约，至第{expires_round}回合止。",
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
                        "盟约破裂",
                        f"{state.factions[faction_id].display_name(222)}撕毁与{state.factions[order.target].display_name(222)}的盟约，威望折损并耗费金。",
                        faction_id=faction_id,
                        tone="diplomacy",
                    )

    def _alliance_vote(self, state: AgenticGameState, proposer_id: str, target_id: str) -> tuple[bool, float]:
        if self._are_allied(state, proposer_id, target_id):
            return True, 1.0
        target_score = self._score_faction(state, target_id)
        proposer_score = self._score_faction(state, proposer_id)
        strongest_enemy = max(
            (self._score_faction(state, faction_id) for faction_id in FACTION_IDS if faction_id not in {proposer_id, target_id}),
            default=0,
        )
        shared_threat = max(0, strongest_enemy - max(target_score, proposer_score)) / max(1, strongest_enemy + target_score + proposer_score)
        border_tension = self._border_tension(state, proposer_id, target_id)
        recent_hostility = sum(
            1
            for event in state.battle_events[-12:]
            if {event.attacker_faction, event.defender_faction} == {proposer_id, target_id}
        )
        chance = 0.38 + shared_threat * 0.65 - border_tension * 0.08 - recent_hostility * 0.08
        chance += self._policy_mod(state, proposer_id, "gold") * 0.04
        chance = max(0.12, min(0.86, chance))
        if {proposer_id, target_id} == {"liu_bei", "sun_quan"} and state.round <= 8:
            chance = min(0.94, chance + 0.3)
        rng = random.Random(f"{state.round}:alliance:{proposer_id}:{target_id}")
        return rng.random() < chance, chance

    def _border_tension(self, state: AgenticGameState, faction_a: str, faction_b: str) -> int:
        tension = 0
        for city_id, owner in state.city_owners.items():
            if owner != faction_a:
                continue
            for neighbor in state.cities[city_id].neighbors:
                if state.city_owners.get(neighbor) == faction_b:
                    tension += 1
        return tension

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

    def _road_between(self, source_city_id: str, target_city_id: str) -> RoadConfig | None:
        return self.road_lookup.get(tuple(sorted((source_city_id, target_city_id))))

    def _can_pay_route(self, state: AgenticGameState, faction_id: str, source_city_id: str, road: RoadConfig) -> bool:
        food_available = self._city_supply(state, source_city_id, "food") + state.resources[faction_id].food
        gold_available = self._city_supply(state, source_city_id, "gold") + state.resources[faction_id].gold
        return food_available >= road.food_cost and gold_available >= road.gold_cost

    def _apply_travel_cost(
        self,
        state: AgenticGameState,
        faction_id: str,
        unit: AgenticUnit,
        source_city_id: str,
        target_city_id: str,
        action: str,
        *,
        forced: bool = False,
    ) -> bool:
        road = self._road_between(source_city_id, target_city_id)
        if road is None:
            return self._reject(
                state,
                faction_id,
                "行军被拒",
                f"{self._unit_label(state, unit)}无法从{state.cities[source_city_id].name_cn}直达{state.cities[target_city_id].name_cn}。",
            )
        multiplier = 1
        if not forced and not self._can_pay_route(state, faction_id, source_city_id, road):
            return self._reject(
                state,
                faction_id,
                "行军被拒",
                f"{self._unit_label(state, unit)}缺少{road.food_cost}粮/{road.gold_cost}金，无法走{self._route_type_cn(road.route_type)}。",
            )
        food_spent = self._consume_city_supply_or_pool(state, faction_id, source_city_id, "food", road.food_cost)
        gold_spent = self._consume_city_supply_or_pool(state, faction_id, source_city_id, "gold", road.gold_cost)
        if forced and (food_spent < road.food_cost or gold_spent < road.gold_cost):
            multiplier = 2
        readiness_loss = max(1, road.readiness_cost * multiplier)
        soldier_loss = 0
        if unit.unit_type == "army" and unit.soldiers > 0:
            soldier_loss = max(0, int(unit.soldiers * road.soldier_loss_bps * multiplier / 10000))
            unit.soldiers = max(0, unit.soldiers - soldier_loss)
        unit.readiness = max(0, unit.readiness - readiness_loss)
        self._log(
            state,
            "行军消耗",
            (
                f"{self._unit_label(state, unit)}走{self._route_type_cn(road.route_type)}"
                f"{state.cities[source_city_id].name_cn}→{state.cities[target_city_id].name_cn}，"
                f"耗粮{food_spent}/{road.food_cost}、耗金{gold_spent}/{road.gold_cost}、损兵{soldier_loss}、疲劳{readiness_loss}。"
            ),
            faction_id=faction_id,
            region_id=state.cities[target_city_id].region_id,
            tone="supply" if action in {"move", "transfer", "scout"} else "war",
        )
        self._sync_generals(state)
        self._remove_dead_armies(state)
        return unit.soldiers > 0 or unit.unit_type != "army"

    def _route_type_cn(self, route_type: str) -> str:
        return {
            "plain_road": "官道",
            "mountain_pass": "山道",
            "shu_road": "蜀道",
            "river": "水陆道",
            "frontier": "边郡远道",
        }.get(route_type, route_type)

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
            self._log(state, "Rest", f"{self._unit_label(state, unit)}休整整补，恢复军心与战备。", faction_id=faction_id, region_id=unit.region_id)
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
            self._log(state, "Defense set", f"{self._unit_label(state, unit)}固守{city_name}，护卫邻近友城，并安定屯田。", faction_id=faction_id, region_id=unit.region_id)
            return True

        if order.action == "move":
            source_city = self._source_city_for_order(unit, order)
            target = order.target_city_id or (order.target_city_ids[0] if order.target_city_ids else None)
            if not source_city or not target or target not in state.cities:
                return self._reject(state, faction_id, "移动被拒", f"{self._unit_label(state, unit)}没有相邻目标。")
            if self._road_between(source_city, target) is None:
                return self._reject(state, faction_id, "移动被拒", f"{state.cities[source_city].name_cn}到{state.cities[target].name_cn}没有直达道路。")
            target_owner = state.city_owners[target]
            if target_owner != faction_id and not self._are_allied(state, faction_id, target_owner):
                return self._reject(state, faction_id, "移动被拒", f"{state.cities[target].name_cn}非己方或盟友城池，请改用进攻。")
            if not self._apply_travel_cost(state, faction_id, unit, source_city, target, "move"):
                return False
            unit.city_id = target
            unit.region_id = state.cities[target].region_id
            unit.status = "moving"
            state.animations.append(AnimationEvent(type="move", faction_id=faction_id, general_id=unit.general_id, from_city_id=source_city, to_city_id=target, tone="supply"))
            self._log(state, "部队移动", f"{self._unit_label(state, unit)}进驻{state.cities[target].name_cn}。", faction_id=faction_id, region_id=unit.region_id, tone="supply")
            return True

        if order.action == "scout":
            if unit.unit_type != "scout":
                return self._reject(state, faction_id, "Scout rejected", f"{unit.id} is not a scout.")
            source_city = self._source_city_for_order(unit, order)
            target = order.target_city_id or (order.target_city_ids[0] if order.target_city_ids else None) or self._first_enemy_city_neighbor(state, source_city or "", faction_id)
            if not source_city or not target or target not in state.cities or self._road_between(source_city, target) is None:
                return self._reject(state, faction_id, "Scout rejected", f"{unit.id} has no adjacent target.")
            if not self._apply_travel_cost(state, faction_id, unit, source_city, target, "scout"):
                return False
            state.resources[faction_id].add("intel", 14)
            self._add_city_supply(state, target, "intel", 10)
            unit.city_id = source_city
            unit.status = f"scouting {target}"
            owner = state.city_owners[target]
            self._log(
                state,
                "Scout report",
                f"{unit.id}侦察{state.cities[target].name_cn}：归属{state.factions[owner].display_name(222)}，道路{len(state.cities[target].neighbors)}，下次攻城胜势提高。",
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
            self._log(state, "Farms expanded", f"{self._unit_label(state, unit)}整修{state.cities[source_city].name_cn}田亩，粮道更稳。", faction_id=faction_id, region_id=unit.region_id, tone="economy")
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
            if not source_city or not target or target not in state.cities or (target != source_city and self._road_between(source_city, target) is None):
                return self._reject(state, faction_id, "Transfer rejected", f"{unit.id} cannot reach the target this round.")
            target_owner = state.city_owners[target]
            if target_owner != faction_id and not self._are_allied(state, faction_id, target_owner):
                return self._reject(state, faction_id, "Transfer rejected", f"{target} is not owned by an ally.")
            if target != source_city and not self._apply_travel_cost(state, faction_id, unit, source_city, target, "transfer"):
                return False
            amount = max(1, min(40, order.amount or 12))
            if not state.resources[faction_id].spend(resource, amount):
                return self._reject(state, faction_id, "Transfer rejected", f"{faction_id} lacks {resource}.")
            delivered = max(1, int(amount * self._policy_mod(state, faction_id, "transfer")))
            self._add_city_supply(state, target, resource, delivered)
            self._add_region_supply(state, state.cities[target].region_id, resource, delivered)
            unit.city_id = target
            unit.region_id = state.cities[target].region_id
            unit.status = f"delivered {resource}"
            state.animations.append(AnimationEvent(type="move", faction_id=faction_id, from_city_id=source_city, to_city_id=target, value=delivered, tone="supply"))
            self._log(
                state,
                "Supply transfer",
                f"{self._unit_label(state, unit)}向{state.cities[target].name_cn}转运{delivered}{self._resource_cn(resource)}。",
                faction_id=faction_id,
                region_id=state.cities[target].region_id,
                tone="supply",
            )
            return True

        if order.action == "reinforce":
            if unit.unit_type != "army":
                return self._reject(state, faction_id, "增援被拒", f"{unit.id}不是战斗部队。")
            battle = self._battle_for_order(state, order, unit)
            if battle is None:
                return self._reject(state, faction_id, "增援被拒", f"{self._unit_label(state, unit)}没有可增援的邻近战役。")
            source_city = self._source_city_for_order(unit, order)
            if not source_city or (battle.target_city_id != source_city and self._road_between(source_city, battle.target_city_id) is None):
                return self._reject(state, faction_id, "增援被拒", f"{self._unit_label(state, unit)}离战场太远。")
            if battle.target_city_id != source_city and not self._apply_travel_cost(state, faction_id, unit, source_city, battle.target_city_id, "reinforce"):
                return False
            supply = self._reinforcement_supply_package(state, faction_id, source_city, unit)
            for resource, amount in supply.items():
                if amount:
                    self._add_city_supply(state, battle.target_city_id, resource, amount)  # type: ignore[arg-type]
                    self._add_region_supply(state, state.cities[battle.target_city_id].region_id, resource, amount)  # type: ignore[arg-type]
            if faction_id == battle.attacker_faction and unit.id not in battle.attacker_unit_ids:
                battle.attacker_unit_ids.append(unit.id)
            elif faction_id == battle.defender_faction and unit.id not in battle.defender_unit_ids:
                battle.defender_unit_ids.append(unit.id)
            else:
                return self._reject(state, faction_id, "增援被拒", "该势力不是交战方。")
            unit.city_id = battle.target_city_id
            unit.region_id = state.cities[battle.target_city_id].region_id
            unit.status = "reinforcing"
            state.animations.append(AnimationEvent(type="move", faction_id=faction_id, general_id=unit.general_id, from_city_id=source_city, to_city_id=battle.target_city_id, tone="war"))
            supply_text = "、".join(f"{amount}{self._resource_cn(resource)}" for resource, amount in supply.items() if amount)
            self._log(state, "战场增援", f"{self._unit_label(state, unit)}赶赴{state.cities[battle.target_city_id].name_cn}战场，带入{supply_text or '少量'}补给。", faction_id=faction_id, region_id=unit.region_id, tone="war")
            return True

        if order.action == "retreat":
            if unit.unit_type != "army":
                return self._reject(state, faction_id, "撤退被拒", f"{unit.id}不是战斗部队。")
            battle = self._active_battle_for_unit(state, unit.id)
            if battle is None:
                return self._reject(state, faction_id, "撤退被拒", f"{self._unit_label(state, unit)}不在战役中。")
            target = self._retreat_city_for_defender(state, faction_id, battle.target_city_id) or battle.source_city_id
            if state.city_owners.get(target) != faction_id:
                target = self._first_owned_city(state, faction_id)
            if not target:
                unit.soldiers = 0
                self._log(state, "撤退失败", f"{self._unit_label(state, unit)}无路可退，全军溃散。", faction_id=faction_id, tone="warning")
                return True
            if unit.id in battle.attacker_unit_ids:
                battle.attacker_unit_ids.remove(unit.id)
            if unit.id in battle.defender_unit_ids:
                battle.defender_unit_ids.remove(unit.id)
            if target != battle.target_city_id:
                self._apply_travel_cost(state, faction_id, unit, battle.target_city_id, target, "retreat", forced=True)
            unit.city_id = target
            unit.region_id = state.cities[target].region_id
            unit.status = "retreating"
            self._roll_retreat_general_outcome(state, unit, battle.target_city_id, target, rng=random.Random(f"{state.round}:manual-retreat:{unit.id}:{battle.target_city_id}"))
            state.animations.append(AnimationEvent(type="retreat", faction_id=faction_id, general_id=unit.general_id, from_city_id=battle.target_city_id, to_city_id=target, tone="defense"))
            self._log(state, "主动撤退", f"{self._unit_label(state, unit)}脱离{state.cities[battle.target_city_id].name_cn}战场，退往{state.cities[target].name_cn}。", faction_id=faction_id, region_id=unit.region_id, tone="defense")
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
            return True

        return False

    def _attack_score(self, state: AgenticGameState, faction_id: str, unit: AgenticUnit, target_city_id: str) -> float:
        faction = state.factions[faction_id]
        general = self._general_for_unit(state, unit)
        source_city = unit.city_id or self._first_city_for_region(unit.region_id)
        consumed_weapons = self._consume_city_supply_or_pool(state, faction_id, source_city or "", "weapons", 8)
        local_food = self._city_supply(state, source_city or "", "food")
        battle_pay = self._consume_city_supply(state, source_city or "", "gold", 3)
        target = state.cities[target_city_id]
        terrain_penalty = 0.0
        if target.terrain in ("mountain", "pass"):
            terrain_penalty = 6.0
        elif target.terrain == "river":
            terrain_penalty = max(0.0, 5.0 - faction.naval * 2.5)
        target_intel = self._city_supply(state, target_city_id, "intel")
        intel_bonus = min(18.0, state.resources[faction_id].intel / 7 + target_intel * 0.55)
        supply_bonus = consumed_weapons * 1.9 + min(18.0, local_food * 0.22) + battle_pay * 2.4
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
        self._open_or_reinforce_attacks(state, attacks, defense_bonus)

    def _open_or_reinforce_attacks(
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
                if not current_city or self._road_between(current_city, target_city_id) is None:
                    self._reject(state, attack.faction_id, "Attack halted", f"{unit.id} cannot continue from {current_city} to {target_city_id}.")
                    break
                target_owner = state.city_owners[target_city_id]
                if target_owner == attack.faction_id or self._are_allied(state, attack.faction_id, target_owner):
                    break
                if not self._apply_travel_cost(state, attack.faction_id, unit, current_city, target_city_id, "attack"):
                    break
                battle = self._active_battle_at_city(state, target_city_id, attack.faction_id, target_owner)
                if battle:
                    if unit.id not in battle.attacker_unit_ids:
                        battle.attacker_unit_ids.append(unit.id)
                    unit.city_id = target_city_id
                    unit.region_id = state.cities[target_city_id].region_id
                    unit.status = "reinforcing"
                    self._log(state, "加入战役", f"{self._unit_label(state, unit)}加入{state.cities[target_city_id].name_cn}战场。", faction_id=attack.faction_id, region_id=unit.region_id, tone="war")
                    break
                battle = self._create_active_battle(state, unit, current_city, target_city_id, target_owner, defense_bonus)
                unit.city_id = target_city_id
                unit.region_id = state.cities[target_city_id].region_id
                unit.status = "in battle"
                current_city = target_city_id
                if battle.duration_rounds > 1:
                    break
                won = self._resolve_active_battle(state, battle, defense_bonus)
                if not won:
                    unit.city_id = attack.source_city_id or current_city
                    unit.region_id = state.cities[current_city].region_id
                    break
                if unit.readiness < 18 or unit.soldiers < 1000:
                    break

    def _create_active_battle(
        self,
        state: AgenticGameState,
        attacker_unit: AgenticUnit,
        source_city_id: str,
        target_city_id: str,
        defender_id: str,
        defense_bonus: dict[str, float],
    ) -> ActiveBattle:
        rng = random.Random(f"{state.round}:battle-duration:{attacker_unit.id}:{source_city_id}:{target_city_id}")
        duration = rng.randint(MIN_BATTLE_ROUNDS, MAX_BATTLE_ROUNDS)
        defender_units = [
            unit.id
            for unit in state.units
            if unit.faction_id == defender_id and unit.unit_type == "army" and unit.city_id == target_city_id and unit.soldiers > 0
        ]
        attack_score = self._attack_score(state, attacker_unit.faction_id, attacker_unit, target_city_id)
        defense_score = self._defense_score(state, defender_id, target_city_id, defense_bonus)
        odds = max(0.12, min(0.88, attack_score / max(1.0, attack_score + defense_score)))
        battle = ActiveBattle(
            id=f"battle_{state.round}_{attacker_unit.id}_{target_city_id}",
            target_city_id=target_city_id,
            source_city_id=source_city_id,
            attacker_faction=attacker_unit.faction_id,
            defender_faction=defender_id,
            attacker_unit_ids=[attacker_unit.id],
            defender_unit_ids=defender_units,
            started_round=state.round,
            duration_rounds=duration,
            odds=round(odds, 4),
            summary=f"{state.cities[target_city_id].name_cn}战役爆发，预计持续{duration}回合。",
        )
        state.active_battles.append(battle)
        state.animations.append(AnimationEvent(type="clash", faction_id=attacker_unit.faction_id, general_id=attacker_unit.general_id, from_city_id=source_city_id, to_city_id=target_city_id, tone="war"))
        self._log(state, "战役爆发", battle.summary, faction_id=attacker_unit.faction_id, region_id=state.cities[target_city_id].region_id, tone="war")
        return battle

    def _advance_active_battles(self, state: AgenticGameState, defense_bonus: dict[str, float]) -> None:
        remaining: list[ActiveBattle] = []
        for battle in state.active_battles:
            battle.attacker_unit_ids = [unit_id for unit_id in battle.attacker_unit_ids if self._unit_by_id(state, unit_id)]
            battle.defender_unit_ids = [unit_id for unit_id in battle.defender_unit_ids if self._unit_by_id(state, unit_id)]
            if not battle.attacker_unit_ids:
                self._end_abandoned_battle(state, battle, "进攻方已无可战之兵，战役解除。")
                continue
            if state.city_owners.get(battle.target_city_id) != battle.defender_faction:
                self._end_abandoned_battle(state, battle, "城池归属已变，旧战役解除。")
                continue
            battle.elapsed_rounds += 1
            self._apply_battle_attrition(state, battle)
            if battle.elapsed_rounds >= battle.duration_rounds:
                self._resolve_active_battle(state, battle, defense_bonus)
            else:
                battle.odds = self._active_battle_odds(state, battle, defense_bonus)
                battle.summary = (
                    f"{state.cities[battle.target_city_id].name_cn}鏖战第{battle.elapsed_rounds}/{battle.duration_rounds}回合，"
                    f"进攻胜势约{battle.odds:.0%}，双方仍可增援或撤退。"
                )
                state.animations.append(AnimationEvent(type="clash", faction_id=battle.attacker_faction, city_id=battle.target_city_id, tone="war"))
                self._log(state, "战役胶着", battle.summary, faction_id=battle.attacker_faction, region_id=state.cities[battle.target_city_id].region_id, tone="war")
                remaining.append(battle)
        state.active_battles = remaining

    def _resolve_active_battle(self, state: AgenticGameState, battle: ActiveBattle, defense_bonus: dict[str, float]) -> bool:
        attacker = self._strongest_battle_unit(state, battle.attacker_unit_ids)
        if attacker is None:
            self._end_abandoned_battle(state, battle, "进攻方全军散尽。")
            return False
        defender_id = state.city_owners.get(battle.target_city_id, battle.defender_faction)
        for unit_id in battle.defender_unit_ids:
            unit = self._unit_by_id(state, unit_id)
            if unit and unit.faction_id == defender_id:
                unit.status = "defending battle"
        support_attack = sum(
            self._unit_battle_strength(state, unit_id, attack=True)
            for unit_id in battle.attacker_unit_ids
            if unit_id != attacker.id
        )
        support_defense = sum(self._unit_battle_strength(state, unit_id, attack=False) for unit_id in battle.defender_unit_ids)
        original_power = attacker.power
        attacker.power = min(2.2, attacker.power + support_attack / 260)
        defense_bonus[battle.target_city_id] += support_defense / 6
        won = self._resolve_city_battle(state, attacker, battle.source_city_id, battle.target_city_id, defender_id, defense_bonus)
        attacker.power = original_power
        battle.status = "resolved"
        return won

    def _apply_battle_attrition(self, state: AgenticGameState, battle: ActiveBattle) -> None:
        rng = random.Random(f"{state.round}:battle-attrition:{battle.id}:{battle.elapsed_rounds}")
        for unit_id in [*battle.attacker_unit_ids, *battle.defender_unit_ids]:
            unit = self._unit_by_id(state, unit_id)
            if not unit or unit.soldiers <= 0:
                continue
            loss_rate = rng.uniform(0.015, 0.045)
            unit.soldiers = max(0, int(unit.soldiers * (1 - loss_rate)))
            unit.readiness = max(0, unit.readiness - rng.randint(3, 8))
            unit.status = "in battle"
        self._remove_dead_armies(state)

    def _active_battle_odds(self, state: AgenticGameState, battle: ActiveBattle, defense_bonus: dict[str, float]) -> float:
        attack = sum(self._unit_battle_strength(state, unit_id, attack=True) for unit_id in battle.attacker_unit_ids)
        defense = sum(self._unit_battle_strength(state, unit_id, attack=False) for unit_id in battle.defender_unit_ids)
        defense += self._defense_score(state, battle.defender_faction, battle.target_city_id, defense_bonus) * 0.35
        return max(0.08, min(0.92, attack / max(1.0, attack + defense)))

    def _unit_battle_strength(self, state: AgenticGameState, unit_id: str, attack: bool) -> float:
        unit = self._unit_by_id(state, unit_id)
        if not unit:
            return 0.0
        general = self._general_for_unit(state, unit)
        stat = general.attack if attack and general else general.defense if general else 70
        return math.sqrt(max(1, unit.soldiers)) * 0.38 + unit.readiness * 0.22 + stat * 0.22 + unit.power * 18

    def _strongest_battle_unit(self, state: AgenticGameState, unit_ids: list[str]) -> AgenticUnit | None:
        units = [unit for unit_id in unit_ids if (unit := self._unit_by_id(state, unit_id)) is not None and unit.soldiers > 0]
        return max(units, key=lambda item: (item.soldiers, item.readiness, item.power), default=None)

    def _end_abandoned_battle(self, state: AgenticGameState, battle: ActiveBattle, detail: str) -> None:
        battle.status = "abandoned"
        self._log(state, "战役解除", f"{state.cities[battle.target_city_id].name_cn}：{detail}", faction_id=battle.attacker_faction, region_id=state.cities[battle.target_city_id].region_id, tone="warning")

    def _active_battle_at_city(self, state: AgenticGameState, city_id: str, attacker_id: str | None = None, defender_id: str | None = None) -> ActiveBattle | None:
        for battle in state.active_battles:
            if battle.target_city_id != city_id or battle.status != "active":
                continue
            if attacker_id and battle.attacker_faction != attacker_id:
                continue
            if defender_id and battle.defender_faction != defender_id:
                continue
            return battle

    def _active_battle_for_unit(self, state: AgenticGameState, unit_id: str) -> ActiveBattle | None:
        for battle in state.active_battles:
            if unit_id in battle.attacker_unit_ids or unit_id in battle.defender_unit_ids:
                return battle
        return None

    def _battle_for_order(self, state: AgenticGameState, order: AgentOrder, unit: AgenticUnit) -> ActiveBattle | None:
        if order.battle_id:
            for battle in state.active_battles:
                if battle.id == order.battle_id:
                    return battle
        source = self._source_city_for_order(unit, order)
        target = order.target_city_id or (order.target_city_ids[0] if order.target_city_ids else None)
        for battle in state.active_battles:
            if target and battle.target_city_id != target:
                continue
            if source and battle.target_city_id != source and self._road_between(source, battle.target_city_id) is None:
                continue
            if unit.faction_id in {battle.attacker_faction, battle.defender_faction}:
                return battle
        return None

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
        supply_score = min(
            18.0,
            self._city_supply(state, city_id, "food") * 0.12
            + self._city_supply(state, city_id, "weapons") * 0.18
            + self._city_supply(state, city_id, "gold") * 0.16,
        )
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
        if self._city_supply(state, source_city_id, "gold") > 0:
            factors.append("attacker paid battle awards")
        if state.resources[attacker_id].intel > 0:
            factors.append("scout intel improved odds")
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
                self._broadcast_general_surrender(state, defender_general, defender_id, attacker_id, target_city_id, "败军降服")
            elif aftermath == "annihilated" and defender_general:
                self._kill_general(state, defender_unit, defender_general, target_city_id, "兵败阵亡")
            elif aftermath == "retreat" and defender_unit:
                self._roll_retreat_general_outcome(state, defender_unit, target_city_id, defender_unit.city_id or target_city_id, rng)
            self._remove_dead_armies(state)
            reward_text = self._conquest_reward_text(conquest_rewards)
            summary = f"{attacker_name}攻下{city.name_cn}；{defender_name}{self._aftermath_cn(aftermath)}。缴获：{reward_text}"
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
            self._roll_retreat_general_outcome(state, attacker_unit, target_city_id, source_city_id, rng)
            self._remove_dead_armies(state)
            summary = f"{defender_name}守住{city.name_cn}；{attacker_name}退回{state.cities[source_city_id].name_cn}。"
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

    def _reinforcement_supply_package(
        self,
        state: AgenticGameState,
        faction_id: str,
        source_city_id: str,
        unit: AgenticUnit,
    ) -> dict[str, int]:
        scale = max(1, unit.soldiers // 5000)
        desired = {"food": 2 + scale, "weapons": 1 + scale // 2, "gold": 1}
        delivered: dict[str, int] = {}
        for resource, amount in desired.items():
            delivered[resource] = self._consume_city_supply_or_pool(state, faction_id, source_city_id, resource, amount)  # type: ignore[arg-type]
        return delivered

    def _roll_retreat_general_outcome(
        self,
        state: AgenticGameState,
        unit: AgenticUnit,
        from_city_id: str,
        to_city_id: str,
        rng: random.Random,
    ) -> None:
        general = self._general_for_unit(state, unit)
        if not general or unit.soldiers <= 0:
            return
        lost_ratio = 1.0 - min(1.0, unit.soldiers / max(1, unit.max_soldiers))
        death_chance = max(0.03, min(0.24, 0.04 + lost_ratio * 0.18 + (100 - general.mobility) / 900))
        surrender_chance = max(0.02, min(0.22, general.surrender_risk + lost_ratio * 0.12 + (100 - general.loyalty) / 700))
        roll = rng.random()
        if roll < death_chance:
            self._kill_general(state, unit, general, from_city_id, "退路遇伏")
        elif roll < death_chance + surrender_chance:
            captor = state.city_owners.get(from_city_id)
            if captor and captor != unit.faction_id:
                old_faction = unit.faction_id
                unit.faction_id = captor
                unit.city_id = from_city_id
                unit.region_id = state.cities[from_city_id].region_id
                unit.soldiers = max(300, int(unit.soldiers * rng.uniform(0.25, 0.5)))
                unit.status = "surrendered"
                general.faction_id = captor
                general.city_id = from_city_id
                general.status = "surrendered"
                self._broadcast_general_surrender(state, general, old_faction, captor, from_city_id, "退军降服")

    def _kill_general(self, state: AgenticGameState, unit: AgenticUnit | None, general: GeneralView, city_id: str, reason: str) -> None:
        if unit:
            unit.soldiers = 0
            unit.status = "dead"
        general.soldiers = 0
        general.city_id = None
        general.status = "dead"
        self._log(
            state,
            "将领阵亡",
            f"{general.name_cn}于{state.cities[city_id].name_cn}{reason}，所部溃散。",
            faction_id=general.faction_id,
            region_id=state.cities[city_id].region_id,
            tone="warning",
        )
        state.animations.append(AnimationEvent(type="incident", faction_id=general.faction_id, general_id=general.id, city_id=city_id, tone="warning"))

    def _broadcast_general_surrender(
        self,
        state: AgenticGameState,
        general: GeneralView,
        old_faction: str,
        new_faction: str,
        city_id: str,
        reason: str,
    ) -> None:
        self._log(
            state,
            "将领归降",
            f"{general.name_cn}在{state.cities[city_id].name_cn}{reason}，由{state.factions[old_faction].display_name(222)}归于{state.factions[new_faction].display_name(222)}。",
            faction_id=new_faction,
            region_id=state.cities[city_id].region_id,
            tone="diplomacy",
        )
        state.animations.append(AnimationEvent(type="surrender", faction_id=old_faction, general_id=general.id, city_id=city_id, tone="diplomacy"))

    def _resource_cn(self, resource: str) -> str:
        return {"food": "粮", "weapons": "兵械", "gold": "金"}.get(resource, resource)

    def _aftermath_cn(self, aftermath: str) -> str:
        return {
            "hold": "仍在固守",
            "retreat": "败退",
            "surrender_soldiers": "部众降服",
            "surrender_general": "主将归降",
            "annihilated": "全军覆没",
        }.get(aftermath, aftermath)

    def _action_cn(self, action: str) -> str:
        return {
            "move": "行军",
            "attack": "进攻",
            "defend": "固守",
            "scout": "侦察",
            "farm": "屯田",
            "transfer": "转运",
            "reinforce": "增援",
            "retreat": "撤退",
            "rest": "休整",
        }.get(action, action)

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
            f"{state.factions[attacker_id].display_name(222)}夺取{city.name_cn}，缴获{state.factions[defender_id].display_name(222)}城中府库：{self._conquest_reward_text(rewards)}",
            faction_id=attacker_id,
            region_id=city.region_id,
            tone="victory",
        )
        return rewards

    def _conquest_reward_text(self, rewards: dict[str, int]) -> str:
        labels = {"food": "粮", "weapons": "兵械", "gold": "金", "manpower": "丁壮"}
        parts = [f"+{amount} {labels[resource]}" for resource, amount in rewards.items() if amount > 0]
        return "、".join(parts) if parts else "府库焚毁无余"

    def _battle_rng(self, state: AgenticGameState, unit_id: str, source_city_id: str, target_city_id: str) -> random.Random:
        seed = f"{state.round}:{unit_id}:{source_city_id}:{target_city_id}"
        return random.Random(seed)

    def _resolve_stranded_units(self, state: AgenticGameState) -> None:
        protected = {
            unit_id
            for battle in state.active_battles
            for unit_id in [*battle.attacker_unit_ids, *battle.defender_unit_ids]
        }
        for unit in list(state.units):
            if not unit.city_id or unit.city_id not in state.cities or unit.id in protected:
                continue
            city_owner = state.city_owners.get(unit.city_id)
            if city_owner == unit.faction_id or (city_owner and self._are_allied(state, unit.faction_id, city_owner)):
                continue
            if unit.unit_type != "army":
                home = self._first_owned_city(state, unit.faction_id)
                if home:
                    unit.city_id = home
                    unit.region_id = state.cities[home].region_id
                continue
            rng = random.Random(f"{state.round}:stranded:{unit.id}:{unit.city_id}:{city_owner}")
            general = self._general_for_unit(state, unit)
            retreat = self._retreat_city_for_defender(state, unit.faction_id, unit.city_id) or self._first_owned_city(state, unit.faction_id)
            risk = general.surrender_risk if general else 0.1
            roll = rng.random()
            if retreat and roll > risk + 0.22:
                old_city = unit.city_id
                unit.city_id = retreat
                unit.region_id = state.cities[retreat].region_id
                unit.status = "forced retreat"
                unit.readiness = max(0, unit.readiness - 22)
                unit.soldiers = max(0, int(unit.soldiers * rng.uniform(0.72, 0.9)))
                state.animations.append(AnimationEvent(type="retreat", faction_id=unit.faction_id, general_id=unit.general_id, from_city_id=old_city, to_city_id=retreat, tone="defense"))
                self._log(state, "孤军撤退", f"{self._unit_label(state, unit)}滞留敌境，被迫退往{state.cities[retreat].name_cn}。", faction_id=unit.faction_id, region_id=unit.region_id, tone="warning")
            elif roll < risk + 0.1 and city_owner:
                old_faction = unit.faction_id
                unit.faction_id = city_owner
                unit.status = "surrendered"
                unit.soldiers = max(300, int(unit.soldiers * rng.uniform(0.32, 0.62)))
                if general:
                    general.faction_id = city_owner
                    general.status = "surrendered"
                self._log(state, "孤军降服", f"{self._unit_label(state, unit)}陷于敌境，率残部降于{state.factions[city_owner].display_name(222)}。", faction_id=city_owner, region_id=unit.region_id, tone="diplomacy")
                state.animations.append(AnimationEvent(type="surrender", faction_id=old_faction, city_id=unit.city_id, tone="diplomacy"))
            else:
                unit.soldiers = 0
                if general:
                    general.status = "lost"
                self._log(state, "孤军覆灭", f"{self._unit_label(state, unit)}无城可依，在敌境溃灭。", faction_id=unit.faction_id, region_id=unit.region_id, tone="warning")
        self._remove_dead_armies(state)
        self._sync_generals(state)

    def _apply_random_incidents(self, state: AgenticGameState) -> None:
        rng = random.Random(f"{state.round}:incident")
        if state.round <= 0 or rng.random() > 0.42:
            return
        city = rng.choice(list(state.cities.values()))
        owner = state.city_owners[city.id]
        incident_type = rng.choice(["weather", "drought", "flood", "bandits", "harvest", "volunteers", "armory", "trade"])
        event = IncidentEvent(id=f"incident_{state.round}_{city.id}_{incident_type}", round=state.round, type=incident_type, city_id=city.id, region_id=city.region_id, faction_id=owner)
        if incident_type == "weather":
            event.food_delta = -rng.randint(4, 12)
            event.gold_delta = -rng.randint(2, 8)
            event.summary = f"{city.name_cn}风雨失时，粮与金收入受损。"
        elif incident_type == "drought":
            event.food_delta = -rng.randint(10, 22)
            event.population_delta = -rng.randint(1, 4)
            event.development_delta = -0.04
            event.summary = f"{city.name_cn}旱情蔓延，田亩与人口受损。"
        elif incident_type == "flood":
            event.food_delta = -rng.randint(6, 16)
            event.development_delta = -0.06
            event.summary = f"{city.name_cn}水患冲毁道路，屯田下降。"
        elif incident_type == "bandits":
            event.gold_delta = -rng.randint(5, 14)
            event.soldier_delta = -rng.randint(120, 520)
            event.summary = f"{city.name_cn}盗寇扰境，金库和驻军折损。"
        elif incident_type == "harvest":
            event.food_delta = rng.randint(10, 24)
            event.development_delta = 0.035
            event.summary = f"{city.name_cn}秋收丰足，粮草入仓。"
        elif incident_type == "volunteers":
            event.manpower_delta = rng.randint(600, 1800)
            event.summary = f"{city.name_cn}乡勇来投，丁壮增加。"
        elif incident_type == "armory":
            event.weapons_delta = rng.randint(8, 22)
            event.summary = f"{city.name_cn}旧府库清点出兵械。"
        else:
            event.gold_delta = rng.randint(8, 22)
            event.summary = f"{city.name_cn}商旅复通，金收入增加。"
        state.resources[owner].add("food", event.food_delta)
        state.resources[owner].add("gold", event.gold_delta)
        state.resources[owner].add("weapons", event.weapons_delta)
        state.resources[owner].add("manpower", event.manpower_delta)
        if event.population_delta:
            city.population = max(6, city.population + event.population_delta)
        if event.development_delta:
            state.city_development[city.id] = max(0.45, min(1.85, state.city_development.get(city.id, 1.0) + event.development_delta))
        if event.soldier_delta:
            armies = [unit for unit in state.units if unit.faction_id == owner and unit.city_id == city.id and unit.unit_type == "army"]
            if armies:
                target = max(armies, key=lambda item: item.soldiers)
                target.soldiers = max(0, target.soldiers + event.soldier_delta)
        state.incident_events.append(event)
        state.animations.append(AnimationEvent(type="incident", faction_id=owner, city_id=city.id, value=event.food_delta + event.gold_delta + event.weapons_delta + event.manpower_delta, tone="warning" if event.food_delta < 0 or event.gold_delta < 0 else "economy"))
        self._log(state, "天时民变", event.summary, faction_id=owner, region_id=city.region_id, tone="warning" if event.food_delta < 0 or event.gold_delta < 0 else "economy")

    def _discover_generals(self, state: AgenticGameState) -> None:
        active_ids = set(state.generals)
        by_faction = {
            faction_id: [seed for seed in self.general_seeds if seed.faction_id == faction_id and seed.id not in active_ids]
            for faction_id in FACTION_IDS
        }
        for faction_id, available in by_faction.items():
            if not available or not self._faction_alive(state, faction_id):
                continue
            owned_cities = [city for city in state.cities.values() if state.city_owners.get(city.id) == faction_id]
            if not owned_cities:
                continue
            active_count = sum(1 for unit in state.units if unit.faction_id == faction_id and unit.unit_type == "army" and unit.soldiers > 0)
            population = sum(city.population for city in owned_cities)
            if state.round < 4 and active_count >= 6:
                continue
            pressure_bonus = max(0, 8 - active_count) * 0.012
            population_bonus = population / 9000
            probability = max(0.008, min(0.16, population_bonus + pressure_bonus))
            rng = random.Random(f"{state.round}:discover:{faction_id}")
            if rng.random() > probability:
                continue
            seed = rng.choice(sorted(available, key=lambda item: item.id))
            city = max(owned_cities, key=lambda item: (item.population + item.economy, item.id))
            soldiers = max(1800, min(seed.soldiers, int(city.population * rng.randint(95, 150))))
            general = GeneralView(
                id=seed.id,
                name_cn=seed.name_cn,
                name_en=seed.name_en,
                faction_id=faction_id,
                portrait_path=seed.portrait_path,
                city_id=city.id,
                soldiers=soldiers,
                max_soldiers=seed.max_soldiers,
                command=seed.command,
                attack=seed.attack,
                defense=seed.defense,
                mobility=seed.mobility,
                loyalty=seed.loyalty,
                food_need=seed.food_need,
                surrender_risk=seed.surrender_risk,
                status="discovered",
            )
            unit_id = self._next_army_id(state, faction_id)
            general.unit_id = unit_id
            state.generals[general.id] = general
            state.units.append(
                AgenticUnit(
                    id=unit_id,
                    faction_id=faction_id,
                    unit_type="army",
                    region_id=city.region_id,
                    city_id=city.id,
                    general_id=general.id,
                    soldiers=soldiers,
                    max_soldiers=seed.max_soldiers,
                    readiness=82,
                    power=(seed.command + seed.attack + seed.defense) / 255,
                    status="discovered",
                )
            )
            event = GeneralDiscoveryEvent(
                id=f"discover_{state.round}_{general.id}",
                round=state.round,
                faction_id=faction_id,
                general_id=general.id,
                city_id=city.id,
                soldiers=soldiers,
                probability=round(probability, 4),
                summary=f"{state.factions[faction_id].display_name(222)}在{city.name_cn}发现新将{general.name_cn}，募兵{soldiers}。",
            )
            state.general_discovery_events.append(event)
            state.animations.append(AnimationEvent(type="discover", faction_id=faction_id, general_id=general.id, city_id=city.id, value=soldiers, tone="economy"))
            self._log(state, "新将出仕", event.summary, faction_id=faction_id, region_id=city.region_id, tone="economy")

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
                f"{state.factions[faction_id].display_name(222)}收入+{income.food}粮、+{income.weapons}兵械、+{income.gold}金。",
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
                gold_need = max(1, unit.soldiers // ARMY_GOLD_UPKEEP_DIVISOR)
                city_id = unit.city_id or ""
                if state.resources[owner].spend("food", food_need):
                    recovery += 2
                else:
                    unit.readiness = max(0, unit.readiness - 8)
                    unit.soldiers = max(0, int(unit.soldiers * 0.985))
                gold_paid = self._consume_city_supply_or_pool(state, owner, city_id, "gold", gold_need) if city_id else 0
                if gold_paid >= gold_need:
                    recovery += 1
                else:
                    unit.readiness = max(0, unit.readiness - 6)
                missing = max(0, (unit.max_soldiers or unit.soldiers) - unit.soldiers)
                recruit_by_manpower = int(state.resources[owner].manpower * 0.03)
                recruit_by_gold = (state.resources[owner].gold // GOLD_RECRUIT_COST_PER_1000) * 1000
                reinforce = min(missing, recruit_by_manpower, recruit_by_gold)
                if reinforce > 0:
                    gold_cost = max(1, math.ceil(reinforce / 1000) * GOLD_RECRUIT_COST_PER_1000)
                    if state.resources[owner].spend("gold", gold_cost) and state.resources[owner].spend("manpower", reinforce):
                        unit.soldiers += reinforce
                        self._log(
                            state,
                            "Recruitment",
                            f"{state.factions[owner].display_name(222)}支出{gold_cost}金与{reinforce}丁壮，为{self._unit_label(state, unit)}募兵补员。",
                            faction_id=owner,
                            region_id=unit.region_id,
                            tone="economy",
                        )
            if unit.city_id and self._city_supply(state, unit.city_id, "food") > 0:
                self._consume_city_supply(state, unit.city_id, "food", 1)
                recovery += 3
            unit.readiness = min(100, unit.readiness + recovery)
        self._sync_generals(state)

    def _expire_alliances(self, state: AgenticGameState) -> None:
        before = len(state.alliances)
        state.alliances = [alliance for alliance in state.alliances if alliance.expires_round > state.round]
        if len(state.alliances) != before:
            self._log(state, "Treaty expired", "盟约到期，边境再度可以交战。", tone="diplomacy")

    def _update_victory(self, state: AgenticGameState) -> None:
        alive = [faction_id for faction_id in FACTION_IDS if self._faction_alive(state, faction_id)]
        if len(alive) == 1:
            winner = alive[0]
            state.finished = True
            state.winner = winner
            self._log(
                state,
                "Conquest victory",
                f"{state.factions[winner].display_name(222)} eliminates every rival faction and unifies all remaining cities.",
                faction_id=winner,
                tone="victory",
            )

    def _faction_alive(self, state: AgenticGameState, faction_id: str) -> bool:
        owns_city = any(owner == faction_id for owner in state.city_owners.values())
        has_army = any(unit.faction_id == faction_id and unit.unit_type == "army" and unit.soldiers > 0 for unit in state.units)
        return owns_city or has_army

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
        if not raw_targets and order.target_region_id:
            region_target = self._best_city_in_region_for_attack(state, unit, order.target_region_id)
            if region_target:
                raw_targets.append(region_target)
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
            if self._road_between(current, target) is None:
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
            if city.region_id == region_id and (city.id == source_city_id or self._road_between(source_city_id, city.id)):
                return city.id
        return None

    def _best_city_in_region_for_attack(self, state: AgenticGameState, unit: AgenticUnit, region_id: str) -> str | None:
        source_city = unit.city_id
        if not source_city:
            return None
        candidates = []
        for city in state.cities.values():
            if city.region_id != region_id:
                continue
            owner = state.city_owners[city.id]
            if owner == unit.faction_id:
                continue
            if self._are_allied(state, unit.faction_id, owner):
                return city.id
            if self._road_between(source_city, city.id):
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

    def _first_owned_city(self, state: AgenticGameState, faction_id: str) -> str | None:
        return next((city_id for city_id, owner in sorted(state.city_owners.items()) if owner == faction_id), None)

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

    def _next_army_id(self, state: AgenticGameState, faction_id: str) -> str:
        max_index = 0
        prefix = f"{faction_id}_army_"
        for unit in state.units:
            if unit.id.startswith(prefix):
                try:
                    max_index = max(max_index, int(unit.id.removeprefix(prefix)))
                except ValueError:
                    continue
        return f"{prefix}{max_index + 1}"

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

    def _spend_food_for_action(
        self,
        state: AgenticGameState,
        faction_id: str,
        city_id: str,
        amount: int,
        action: str,
    ) -> bool:
        if self._city_supply(state, city_id, "food") + state.resources[faction_id].food < amount:
            return False
        spent = self._consume_city_supply_or_pool(state, faction_id, city_id, "food", amount)
        if spent >= amount:
            self._log(
                state,
                "Food spent",
                f"{state.factions[faction_id].display_name(222)}自{state.cities[city_id].name_cn}支出{amount}粮用于{self._action_cn(action)}。",
                faction_id=faction_id,
                region_id=state.cities[city_id].region_id,
                tone="supply",
            )
            return True
        return False

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
