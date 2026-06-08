from __future__ import annotations

import math
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from hwsim.agentic.agents import AgentProvider, MockAgentProvider
from hwsim.agentic.models import (
    AgentObservation,
    AgentOrder,
    AgentPlan,
    AgenticAlliance,
    AgenticGameState,
    AgenticUnit,
    DiplomacyOrder,
    FactionView,
    GameView,
    PendingAttack,
    Policy,
    ResourceType,
    Resources,
    RoundLog,
)
from hwsim.core.models import Faction, MapConfig, Region, ScenarioConfig
from hwsim.map.map_loader import load_bundle


FACTION_IDS = ("cao", "liu_bei", "sun_quan")
PLAYER_FACTION = "liu_bei"
MAX_ROUNDS = 20
ACTION_POINTS = 5
DOMINANCE_SHARE = 0.75
DEFAULT_SCENARIO = Path("configs/scenarios/sanguo_shu_unification_demo.json")

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
    ) -> None:
        self.scenario = scenario
        self.map_config = map_config
        self.agent_provider = agent_provider or MockAgentProvider()
        self.fallback_provider = fallback_provider or MockAgentProvider()

    @classmethod
    def from_default_scenario(
        cls,
        agent_provider: AgentProvider | None = None,
        fallback_provider: AgentProvider | None = None,
    ) -> "AgenticGameEngine":
        scenario, map_config, _events, _style = load_bundle(DEFAULT_SCENARIO)
        return cls(scenario, map_config, agent_provider=agent_provider, fallback_provider=fallback_provider)

    def new_game(self, player_faction: str = PLAYER_FACTION, game_id: str | None = None) -> AgenticGameState:
        factions = self._three_kingdom_factions()
        regions = {region.id: region for region in self.map_config.regions}
        region_owners = self._initial_region_owners(regions)
        units = self._initial_units()
        state = AgenticGameState(
            game_id=game_id or uuid.uuid4().hex,
            round=0,
            max_rounds=MAX_ROUNDS,
            player_faction=player_faction,
            factions=factions,
            regions=regions,
            region_owners=region_owners,
            units=units,
            resources={faction_id: INITIAL_RESOURCES[faction_id].model_copy(deep=True) for faction_id in FACTION_IDS},
            policies={faction_id: "balanced" for faction_id in FACTION_IDS},
            region_development={region_id: 1.0 for region_id in regions},
            regional_supply={region_id: {} for region_id in regions},
            logs=[
                RoundLog(
                    round=0,
                    title="三国开局",
                    detail="你控制蜀汉。魏强、吴稳，二十回合内用政策、补给、联盟和战役改写局势。",
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
        remaining_ap = {faction_id: ACTION_POINTS for faction_id in FACTION_IDS}
        used_units: set[str] = set()

        for faction_id in FACTION_IDS:
            self._apply_diplomacy(state, faction_id, plans[faction_id].diplomacy, remaining_ap)

        for faction_id in FACTION_IDS:
            for order in plans[faction_id].orders:
                self._try_execute_order(state, faction_id, order, remaining_ap, used_units, defense_bonus, attacks)

        self._resolve_attacks(state, attacks, defense_bonus)
        self._apply_round_income(state)
        self._recover_units(state)
        self._expire_alliances(state)
        self._update_victory(state)
        state.current_player_orders = []
        state.current_player_diplomacy = []
        return state

    def to_view(self, state: AgenticGameState) -> GameView:
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
            regions=list(self.map_config.regions),
            region_owners=state.region_owners,
            region_development=state.region_development,
            regional_supply=state.regional_supply,
            region_pressure=state.region_pressure,
            factions=factions,
            units=state.units,
            alliances=state.alliances,
            logs=state.logs[-80:],
            current_player_command=state.current_player_command,
            current_player_policy=state.current_player_policy,
            current_player_orders=state.current_player_orders,
        )

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

    def _initial_units(self) -> list[AgenticUnit]:
        units: list[AgenticUnit] = []
        for faction_id, specs in INITIAL_UNITS.items():
            unit_type_counts: dict[str, int] = defaultdict(int)
            for unit_type, region_id, power in specs:
                unit_type_counts[unit_type] += 1
                units.append(
                    AgenticUnit(
                        id=f"{faction_id}_{unit_type}_{unit_type_counts[unit_type]}",
                        faction_id=faction_id,
                        unit_type=unit_type,  # type: ignore[arg-type]
                        region_id=region_id,
                        readiness=100,
                        power=power,
                    )
                )
        return units

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
                    target = self._best_attack_target_for_unit(state, unit)
                    if target:
                        orders.append(AgentOrder(unit_id=unit.id, action="attack", target_region_id=target))
                        break
        if wants_farm or not orders:
            for unit in units:
                if unit.unit_type == "worker":
                    orders.append(AgentOrder(unit_id=unit.id, action="farm", region_id=unit.region_id))
                    break
        if wants_scout or not any(order.action == "scout" for order in orders):
            for unit in units:
                if unit.unit_type == "scout":
                    target = self._first_enemy_neighbor(state, unit.region_id, unit.faction_id)
                    if target:
                        orders.append(AgentOrder(unit_id=unit.id, action="scout", target_region_id=target))
                    break
        if wants_transfer or state.current_player_policy in ("war", "logistics"):
            for unit in units:
                if unit.unit_type == "caravan":
                    target = self._first_border_region(state, unit.faction_id) or unit.region_id
                    orders.append(AgentOrder(unit_id=unit.id, action="transfer", target_region_id=target, resource="weapons", amount=16))
                    break
        if not wants_attack:
            for unit in units:
                if unit.unit_type == "army" and len(orders) < 4:
                    orders.append(AgentOrder(unit_id=unit.id, action="defend", region_id=unit.region_id))
        return orders[:5]

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
            units=units,
            neighbors={region_id: region.neighbors for region_id, region in state.regions.items()},
            alliances=[item for item in alliances if item],
            recent_log=[log.detail for log in state.logs[-8:]],
            player_command=state.current_player_command if faction_id != state.player_faction else "",
        )

    def _start_round(self, state: AgenticGameState) -> None:
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
        remaining_ap: dict[str, int],
    ) -> None:
        for order in diplomacy:
            if remaining_ap[faction_id] < 2:
                self._log(state, "Diplomacy skipped", f"{faction_id} lacks action points for diplomacy.", faction_id=faction_id, tone="warning")
                continue
            if order.target not in FACTION_IDS or order.target == faction_id:
                self._log(state, "Diplomacy rejected", f"{faction_id} used an invalid diplomacy target.", faction_id=faction_id, tone="warning")
                continue
            remaining_ap[faction_id] -= 2
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
        remaining_ap: dict[str, int],
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
        cost = ACTION_COSTS[order.action]
        if remaining_ap[faction_id] < cost:
            self._log(state, "Order skipped", f"{faction_id} lacks AP for {order.action}.", faction_id=faction_id, tone="warning")
            return
        if self._execute_order(state, faction_id, unit, order, defense_bonus, attacks):
            remaining_ap[faction_id] -= cost
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
            if state.region_owners.get(unit.region_id) != faction_id:
                return self._reject(state, faction_id, "Defend rejected", f"{unit.id} is outside friendly territory.")
            bonus = 10.0 + unit.readiness * 0.14 * unit.power
            defense_bonus[unit.region_id] += bonus
            unit.readiness = max(0, unit.readiness - 7)
            unit.status = "defending"
            self._log(state, "Defense set", f"{unit.id} fortifies {state.regions[unit.region_id].name_cn}.", faction_id=faction_id, region_id=unit.region_id)
            return True

        if order.action == "scout":
            if unit.unit_type != "scout":
                return self._reject(state, faction_id, "Scout rejected", f"{unit.id} is not a scout.")
            target = order.target_region_id or self._first_enemy_neighbor(state, unit.region_id, faction_id)
            if not target or target not in state.regions or target not in state.regions[unit.region_id].neighbors:
                return self._reject(state, faction_id, "Scout rejected", f"{unit.id} has no adjacent target.")
            state.resources[faction_id].add("intel", 9)
            unit.readiness = max(0, unit.readiness - 5)
            unit.status = f"scouting {target}"
            owner = state.region_owners[target]
            self._log(
                state,
                "Scout report",
                f"{unit.id} scouts {state.regions[target].name_cn}: owner {state.factions[owner].display_name(222)}, pressure {state.region_pressure.get(f'{faction_id}:{target}', 0)}.",
                faction_id=faction_id,
                region_id=target,
                tone="intel",
            )
            return True

        if order.action == "farm":
            if unit.unit_type != "worker":
                return self._reject(state, faction_id, "Farm rejected", f"{unit.id} is not a worker.")
            if state.region_owners.get(unit.region_id) != faction_id:
                return self._reject(state, faction_id, "Farm rejected", f"{unit.id} is outside friendly territory.")
            state.region_development[unit.region_id] = min(1.55, state.region_development.get(unit.region_id, 1.0) + 0.1)
            state.resources[faction_id].add("food", 10)
            unit.readiness = max(0, unit.readiness - 6)
            unit.status = "farming"
            self._log(state, "Farms expanded", f"{unit.id} improves {state.regions[unit.region_id].name_cn}.", faction_id=faction_id, region_id=unit.region_id, tone="economy")
            return True

        if order.action == "transfer":
            if unit.unit_type != "caravan":
                return self._reject(state, faction_id, "Transfer rejected", f"{unit.id} is not a caravan.")
            target = order.target_region_id or unit.region_id
            resource = order.resource or "food"
            if resource not in ("food", "weapons", "gold"):
                return self._reject(state, faction_id, "Transfer rejected", f"{resource} cannot be moved by caravan.")
            if target not in state.regions or target not in [unit.region_id, *state.regions[unit.region_id].neighbors]:
                return self._reject(state, faction_id, "Transfer rejected", f"{unit.id} cannot reach the target this round.")
            target_owner = state.region_owners[target]
            if target_owner != faction_id and not self._are_allied(state, faction_id, target_owner):
                return self._reject(state, faction_id, "Transfer rejected", f"{target} is not owned by an ally.")
            amount = max(1, min(40, order.amount or 12))
            if not state.resources[faction_id].spend(resource, amount):
                return self._reject(state, faction_id, "Transfer rejected", f"{faction_id} lacks {resource}.")
            delivered = max(1, int(amount * self._policy_mod(state, faction_id, "transfer")))
            self._add_region_supply(state, target, resource, delivered)
            unit.region_id = target
            unit.readiness = max(0, unit.readiness - 4)
            unit.status = f"delivered {resource}"
            self._log(
                state,
                "Supply transfer",
                f"{unit.id} delivers {delivered} {resource} to {state.regions[target].name_cn}.",
                faction_id=faction_id,
                region_id=target,
                tone="supply",
            )
            return True

        if order.action == "attack":
            if unit.unit_type != "army":
                return self._reject(state, faction_id, "Attack rejected", f"{unit.id} is not an army.")
            target = order.target_region_id or self._best_attack_target_for_unit(state, unit)
            if not target or target not in state.regions[unit.region_id].neighbors:
                return self._reject(state, faction_id, "Attack rejected", f"{unit.id} has no adjacent target.")
            target_owner = state.region_owners[target]
            if target_owner == faction_id:
                return self._reject(state, faction_id, "Attack rejected", f"{target} is already friendly.")
            if self._are_allied(state, faction_id, target_owner):
                return self._reject(state, faction_id, "Attack rejected", f"Alliance blocks fighting with {target_owner}.")
            attack_score = self._attack_score(state, faction_id, unit, target)
            unit.readiness = max(0, unit.readiness - 16)
            unit.status = f"attacking {target}"
            attacks.append(
                PendingAttack(
                    faction_id=faction_id,
                    unit_id=unit.id,
                    source_region_id=unit.region_id,
                    target_region_id=target,
                    attack_score=attack_score,
                )
            )
            self._log(state, "Attack launched", f"{unit.id} attacks {state.regions[target].name_cn}.", faction_id=faction_id, region_id=target, tone="war")
            return True

        return False

    def _attack_score(self, state: AgenticGameState, faction_id: str, unit: AgenticUnit, target_region_id: str) -> float:
        faction = state.factions[faction_id]
        consumed_weapons = self._consume_supply_or_pool(state, faction_id, unit.region_id, "weapons", 8)
        local_food = self._region_supply(state, unit.region_id, "food")
        terrain_penalty = 0.0
        if state.regions[target_region_id].terrain in ("mountain", "pass"):
            terrain_penalty = 6.0
        elif state.regions[target_region_id].terrain == "river":
            terrain_penalty = max(0.0, 5.0 - faction.naval * 2.5)
        intel_bonus = min(6.0, state.resources[faction_id].intel / 12)
        supply_bonus = consumed_weapons * 1.2 + min(8.0, local_food * 0.1)
        return (
            18.0 * unit.power
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
            target_owner = state.region_owners[attack.target_region_id]
            if target_owner == attack.faction_id or self._are_allied(state, attack.faction_id, target_owner):
                continue
            defender_score = self._defense_score(state, target_owner, attack.target_region_id, defense_bonus)
            pressure_key = f"{attack.faction_id}:{attack.target_region_id}"
            pressure = state.region_pressure.get(pressure_key, 0)
            attacker_name = state.factions[attack.faction_id].display_name(222)
            defender_name = state.factions[target_owner].display_name(222)
            region_name = state.regions[attack.target_region_id].name_cn

            if attack.attack_score >= defender_score * 1.08 or (pressure >= 2 and attack.attack_score >= defender_score * 0.96):
                state.region_owners[attack.target_region_id] = attack.faction_id
                state.region_development[attack.target_region_id] = max(0.66, state.region_development.get(attack.target_region_id, 1.0) * 0.88)
                unit.region_id = attack.target_region_id
                unit.readiness = max(18, unit.readiness - 8)
                self._clear_pressure_for_region(state, attack.target_region_id)
                self._retreat_defenders(state, target_owner, attack.target_region_id)
                self._log(
                    state,
                    "Region captured",
                    f"{attacker_name} captures {region_name} from {defender_name} ({attack.attack_score:.1f} vs {defender_score:.1f}).",
                    faction_id=attack.faction_id,
                    region_id=attack.target_region_id,
                    tone="victory",
                )
            elif attack.attack_score >= defender_score * 0.92:
                state.region_pressure[pressure_key] = pressure + 1
                unit.readiness = max(0, unit.readiness - 8)
                self._log(
                    state,
                    "Front contested",
                    f"{attacker_name} pressures {region_name}, but {defender_name} holds for now ({attack.attack_score:.1f} vs {defender_score:.1f}).",
                    faction_id=attack.faction_id,
                    region_id=attack.target_region_id,
                    tone="war",
                )
            else:
                state.region_pressure[pressure_key] = max(0, pressure - 1)
                unit.readiness = max(0, unit.readiness - 14)
                self._log(
                    state,
                    "Attack repelled",
                    f"{defender_name} holds {region_name} against {attacker_name} ({attack.attack_score:.1f} vs {defender_score:.1f}).",
                    faction_id=target_owner,
                    region_id=attack.target_region_id,
                    tone="defense",
                )

    def _defense_score(
        self,
        state: AgenticGameState,
        defender_id: str,
        region_id: str,
        defense_bonus: dict[str, float],
    ) -> float:
        faction = state.factions[defender_id]
        defending_units = [
            unit for unit in state.units if unit.faction_id == defender_id and unit.region_id == region_id and unit.unit_type == "army"
        ]
        unit_score = sum(10.0 * unit.power + unit.readiness * 0.22 for unit in defending_units)
        terrain_score = TERRAIN_DEFENSE.get(state.regions[region_id].terrain, 10.0)
        supply_score = min(11.0, self._region_supply(state, region_id, "food") * 0.12 + self._region_supply(state, region_id, "weapons") * 0.18)
        return (
            20.0
            + unit_score
            + terrain_score
            + supply_score
            + defense_bonus.get(region_id, 0.0)
            + faction.defense * 10.0
        ) * self._policy_mod(state, defender_id, "defense")

    def _apply_round_income(self, state: AgenticGameState) -> None:
        totals = {faction_id: Resources() for faction_id in FACTION_IDS}
        for region_id, owner in state.region_owners.items():
            region = state.regions[region_id]
            development = state.region_development.get(region_id, 1.0)
            totals[owner].add("food", int((region.population * 0.16 + 5) * development * self._policy_mod(state, owner, "food")))
            totals[owner].add("gold", int((region.economy * 0.12 + 4) * development * self._policy_mod(state, owner, "gold")))
            totals[owner].add("weapons", int((region.economy * 0.07 + 3) * development * self._policy_mod(state, owner, "weapons")))
            totals[owner].add("manpower", int((region.population * 0.05 + 2) * self._policy_mod(state, owner, "manpower")))
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
            if self._region_supply(state, unit.region_id, "food") > 0:
                self._consume_region_supply(state, unit.region_id, "food", 1)
                recovery += 3
            unit.readiness = min(100, unit.readiness + recovery)

    def _expire_alliances(self, state: AgenticGameState) -> None:
        before = len(state.alliances)
        state.alliances = [alliance for alliance in state.alliances if alliance.expires_round > state.round]
        if len(state.alliances) != before:
            self._log(state, "Treaty expired", "An alliance expires and border fighting is legal again.", tone="diplomacy")

    def _update_victory(self, state: AgenticGameState) -> None:
        counts = self._region_counts(state)
        needed = math.ceil(len(state.regions) * DOMINANCE_SHARE)
        for faction_id, count in counts.items():
            if count >= needed:
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
        if state.round >= state.max_rounds:
            self._finish_by_score(state, "Round 20 reached")

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
        ready_score = sum(unit.readiness for unit in state.units if unit.faction_id == faction_id) // 12
        resources = state.resources[faction_id]
        resource_score = (resources.food + resources.weapons + resources.gold + resources.manpower) // 35
        return region_score + ready_score + resource_score

    def _region_counts(self, state: AgenticGameState) -> dict[str, int]:
        return {faction_id: len(self._owned_regions(state, faction_id)) for faction_id in FACTION_IDS}

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
