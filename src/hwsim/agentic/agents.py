from __future__ import annotations

import json
import os
from typing import Protocol

from hwsim.agentic.models import AdvisorRecommendation, AgentObservation, AgentOrder, AgentPlan, DiplomacyOrder


class AgentProvider(Protocol):
    def decide(self, observation: AgentObservation) -> AgentPlan:
        ...


class AdvisorProvider(Protocol):
    def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
        ...


class MockAgentProvider:
    """Deterministic strategy provider for tests and offline demos."""

    def decide(self, observation: AgentObservation) -> AgentPlan:
        if observation.faction_id == "cao":
            return self._cao_plan(observation)
        if observation.faction_id == "sun_quan":
            return self._wu_plan(observation)
        return self._balanced_plan(observation)

    def _cao_plan(self, observation: AgentObservation) -> AgentPlan:
        orders = self._conquest_orders(observation, prefer_resource="weapons")
        return AgentPlan(
            policy="war",
            orders=orders,
            reasoning_summary="Wei seeks city control quickly, attacking every open border and feeding weapons to the front.",
        )

    def _wu_plan(self, observation: AgentObservation) -> AgentPlan:
        diplomacy: list[DiplomacyOrder] = []
        if "liu_bei" not in observation.alliances and observation.round <= 8:
            diplomacy.append(DiplomacyOrder(type="propose_alliance", target="liu_bei", duration_rounds=5))
        elif "liu_bei" in observation.alliances and observation.round > 26:
            diplomacy.append(DiplomacyOrder(type="break_alliance", target="liu_bei", duration_rounds=1))
        orders = self._conquest_orders(observation, prefer_resource="food")
        return AgentPlan(
            policy="farming" if observation.round <= 4 else "war",
            orders=orders,
            diplomacy=diplomacy,
            reasoning_summary="Wu uses early diplomacy as cover, then expands from river cities toward any vulnerable neighbor.",
        )

    def _balanced_plan(self, observation: AgentObservation) -> AgentPlan:
        orders = self._conquest_orders(observation, prefer_resource="food")
        return AgentPlan(policy="war" if any(order.action == "attack" for order in orders) else "balanced", orders=orders, reasoning_summary="Seek city gains while keeping unsupported units useful.")

    def _conquest_orders(self, observation: AgentObservation, prefer_resource: str) -> list[AgentOrder]:
        orders: list[AgentOrder] = []
        ordered_units = sorted(observation.units, key=lambda item: (item.unit_type != "army", -item.readiness, item.id))
        for unit in ordered_units:
            if unit.unit_type == "army":
                orders.append(self._first_city_attack_order(observation, unit) or self._defend_order(unit))
            elif unit.unit_type == "worker":
                orders.append(AgentOrder(unit_id=unit.id, action="farm", region_id=unit.region_id, source_city_id=unit.city_id))
            elif unit.unit_type == "scout":
                orders.append(self._scout_order(observation, unit) or self._defend_order(unit))
            elif unit.unit_type == "caravan":
                orders.append(self._transfer_order(observation, unit, prefer_resource))
        return orders

    def _border_attack_orders(self, observation: AgentObservation, limit: int) -> list[AgentOrder]:
        orders: list[AgentOrder] = []
        armies = [unit for unit in observation.units if unit.unit_type == "army"]
        for unit in sorted(armies, key=lambda item: (-item.readiness, item.id)):
            if unit.city_id and observation.city_neighbors:
                for neighbor in observation.city_neighbors.get(unit.city_id, []):
                    owner = observation.visible_cities.get(neighbor)
                    if owner and owner != observation.faction_id and owner not in observation.alliances:
                        orders.append(
                            AgentOrder(
                                unit_id=unit.id,
                                general_id=unit.general_id,
                                action="attack",
                                source_city_id=unit.city_id,
                                target_city_ids=[neighbor],
                            )
                        )
                        break
                if len(orders) >= limit:
                    break
                if orders and orders[-1].unit_id == unit.id:
                    continue
            for neighbor in observation.neighbors.get(unit.region_id, []):
                owner = observation.visible_regions.get(neighbor)
                if owner and owner != observation.faction_id and owner not in observation.alliances:
                    orders.append(AgentOrder(unit_id=unit.id, action="attack", target_region_id=neighbor))
                    break
            if len(orders) >= limit:
                break
        return orders

    def _first_city_attack_order(self, observation: AgentObservation, unit) -> AgentOrder | None:
        if unit.city_id and observation.city_neighbors:
            best_target: str | None = None
            best_score = -1
            for neighbor in observation.city_neighbors.get(unit.city_id, []):
                owner = observation.visible_cities.get(neighbor)
                if owner and owner != observation.faction_id and owner not in observation.alliances:
                    score = len(observation.city_neighbors.get(neighbor, [])) * 10
                    if score > best_score:
                        best_target = neighbor
                        best_score = score
            if best_target:
                return AgentOrder(
                    unit_id=unit.id,
                    general_id=unit.general_id,
                    action="attack",
                    source_city_id=unit.city_id,
                    target_city_ids=[best_target],
                )
        for neighbor in observation.neighbors.get(unit.region_id, []):
            owner = observation.visible_regions.get(neighbor)
            if owner and owner != observation.faction_id and owner not in observation.alliances:
                return AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="attack", target_region_id=neighbor)
        return None

    def _defend_order(self, unit) -> AgentOrder:
        return AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="defend", region_id=unit.region_id, source_city_id=unit.city_id)

    def _scout_order(self, observation: AgentObservation, unit) -> AgentOrder | None:
        target = self._first_enemy_city_neighbor(observation, unit.city_id or "") or self._first_enemy_neighbor(observation, unit.region_id)
        if not target:
            return None
        if target in observation.visible_cities:
            return AgentOrder(unit_id=unit.id, action="scout", source_city_id=unit.city_id, target_city_id=target)
        return AgentOrder(unit_id=unit.id, action="scout", target_region_id=target)

    def _transfer_order(self, observation: AgentObservation, unit, resource: str) -> AgentOrder:
        target = self._first_border_city(observation) or self._first_border_region(observation) or unit.region_id
        kwargs = {"target_city_id": target} if target in observation.visible_cities else {"target_region_id": target}
        return AgentOrder(
            unit_id=unit.id,
            action="transfer",
            source_city_id=unit.city_id,
            resource=resource,  # type: ignore[arg-type]
            amount=18,
            **kwargs,
        )

    def _support_orders(self, observation: AgentObservation, prefer_resource: str) -> list[AgentOrder]:
        orders: list[AgentOrder] = []
        for unit in sorted(observation.units, key=lambda item: item.id):
            if unit.unit_type == "worker":
                orders.append(AgentOrder(unit_id=unit.id, action="farm", region_id=unit.region_id, source_city_id=unit.city_id))
            elif unit.unit_type == "scout":
                target = self._first_enemy_city_neighbor(observation, unit.city_id or "") or self._first_enemy_neighbor(observation, unit.region_id)
                if target:
                    if target in observation.visible_cities:
                        orders.append(AgentOrder(unit_id=unit.id, action="scout", source_city_id=unit.city_id, target_city_id=target))
                    else:
                        orders.append(AgentOrder(unit_id=unit.id, action="scout", target_region_id=target))
            elif unit.unit_type == "caravan":
                target = self._first_border_city(observation) or self._first_border_region(observation) or unit.region_id
                kwargs = {"target_city_id": target} if target in observation.visible_cities else {"target_region_id": target}
                orders.append(
                    AgentOrder(
                        unit_id=unit.id,
                        action="transfer",
                        source_city_id=unit.city_id,
                        resource=prefer_resource,  # type: ignore[arg-type]
                        amount=14,
                        **kwargs,
                    )
                )
        return orders

    def _first_enemy_city_neighbor(self, observation: AgentObservation, city_id: str) -> str | None:
        for neighbor in observation.city_neighbors.get(city_id, []):
            owner = observation.visible_cities.get(neighbor)
            if owner and owner != observation.faction_id and owner not in observation.alliances:
                return neighbor
        return None

    def _first_enemy_neighbor(self, observation: AgentObservation, region_id: str) -> str | None:
        for neighbor in observation.neighbors.get(region_id, []):
            owner = observation.visible_regions.get(neighbor)
            if owner and owner != observation.faction_id and owner not in observation.alliances:
                return neighbor
        return None

    def _first_border_region(self, observation: AgentObservation) -> str | None:
        for region_id in sorted(observation.owned_regions):
            if self._first_enemy_neighbor(observation, region_id):
                return region_id
        return observation.owned_regions[0] if observation.owned_regions else None

    def _first_border_city(self, observation: AgentObservation) -> str | None:
        owned = [unit.city_id for unit in observation.units if unit.city_id]
        for city_id in sorted(set(owned)):
            if self._first_enemy_city_neighbor(observation, city_id):
                return city_id
        return owned[0] if owned else None


class OpenAIAgentProvider:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_AGENT_MODEL", "gpt-5.4-mini")

    def decide(self, observation: AgentObservation) -> AgentPlan:
        from openai import OpenAI

        client = OpenAI()
        schema = AgentPlan.model_json_schema()
        prompt = (
            "You control one Three Kingdoms faction in a 100-round city conquest game. "
            "Your goal is to defeat the other factions by controlling the most valuable cities. "
            "Return only legal JSON matching the schema. Each unit may receive at most one order. "
            "Prefer attacks that capture cities, defend exposed fronts, and move supplies toward active generals."
        )
        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": observation.model_dump_json()},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "agent_plan",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        output_text = getattr(response, "output_text", None)
        if not output_text:
            output_text = _extract_output_text(response)
        return AgentPlan.model_validate_json(output_text)


class DeterministicAdvisorProvider:
    """Free local Shu advisor used by the web UI and tests."""

    def __init__(self) -> None:
        self._planner = MockAgentProvider()

    def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
        orders = self._planner._conquest_orders(observation, prefer_resource="weapons")
        attack_count = sum(1 for order in orders if order.action == "attack")
        defend_count = sum(1 for order in orders if order.action == "defend")
        exposed = sum(
            1
            for unit in observation.units
            if unit.unit_type == "army"
            and unit.city_id
            and self._planner._first_enemy_city_neighbor(observation, unit.city_id)
        )
        policy = "war" if attack_count else "defense" if exposed else "farming"
        diplomacy: list[DiplomacyOrder] = []
        if "sun_quan" not in observation.alliances and observation.round <= 8:
            diplomacy.append(DiplomacyOrder(type="propose_alliance", target="sun_quan", duration_rounds=5))
        summary = (
            f"Recommend {policy}: {attack_count} attack order(s), "
            f"{defend_count} defense order(s), and supply/scout moves so every unit acts this round."
        )
        return AdvisorRecommendation(policy=policy, orders=orders, diplomacy=diplomacy, summary=summary)


class OpenAIAdvisorProvider:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_ADVISOR_MODEL", os.environ.get("OPENAI_AGENT_MODEL", "gpt-5.4-mini"))

    def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
        from openai import OpenAI

        client = OpenAI()
        schema = AdvisorRecommendation.model_json_schema()
        prompt = (
            "You are Shu Han's military advisor in a 100-round Three Kingdoms city conquest game. "
            "Return only legal JSON matching the schema. Recommend one policy and at most one order per unit. "
            "Prefer winning city control, preserving generals, and exploiting weak adjacent cities."
        )
        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": observation.model_dump_json()},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "advisor_recommendation",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        output_text = getattr(response, "output_text", None) or _extract_output_text(response)
        return AdvisorRecommendation.model_validate_json(output_text)


def default_agent_provider() -> AgentProvider:
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIAgentProvider()
    return MockAgentProvider()


def default_advisor_provider() -> AdvisorProvider:
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_ADVISOR_ENABLED") == "1":
        return OpenAIAdvisorProvider()
    return DeterministicAdvisorProvider()


def _extract_output_text(response: object) -> str:
    try:
        data = response.model_dump()  # type: ignore[attr-defined]
    except AttributeError:
        data = response if isinstance(response, dict) else {}
    for item in data.get("output", []):
        for content in item.get("content", []):
            if "text" in content:
                return str(content["text"])
    return json.dumps({})
