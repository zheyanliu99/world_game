from __future__ import annotations

import json
import os
from typing import Protocol

from hwsim.agentic.models import AgentObservation, AgentOrder, AgentPlan, DiplomacyOrder


class AgentProvider(Protocol):
    def decide(self, observation: AgentObservation) -> AgentPlan:
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
        orders = self._border_attack_orders(observation, limit=2)
        orders.extend(self._support_orders(observation, prefer_resource="weapons"))
        return AgentPlan(
            policy="war",
            orders=orders[:5],
            reasoning_summary="Wei presses the strongest border and keeps weapons moving to the front.",
        )

    def _wu_plan(self, observation: AgentObservation) -> AgentPlan:
        diplomacy: list[DiplomacyOrder] = []
        if "liu_bei" not in observation.alliances and observation.round <= 6:
            diplomacy.append(DiplomacyOrder(type="propose_alliance", target="liu_bei", duration_rounds=5))
        attack_orders = self._border_attack_orders(observation, limit=1)
        support_orders = self._support_orders(observation, prefer_resource="food")
        return AgentPlan(
            policy="farming" if observation.round <= 4 else "logistics",
            orders=(support_orders + attack_orders)[:5],
            diplomacy=diplomacy,
            reasoning_summary="Wu grows its southern base, keeps Shu friendly, and attacks only when a border opens.",
        )

    def _balanced_plan(self, observation: AgentObservation) -> AgentPlan:
        orders = self._support_orders(observation, prefer_resource="food")
        orders.extend(self._border_attack_orders(observation, limit=1))
        return AgentPlan(policy="balanced", orders=orders[:5], reasoning_summary="Hold borders and take safe gains.")

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
            "You control one Three Kingdoms faction in a 20-round strategy game. "
            "Return only legal JSON matching the schema. Use at most 5 action points: "
            "attack costs 2, diplomacy costs 2, all other unit actions cost 1. "
            "Prefer coherent strategy over maximal actions."
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


def default_agent_provider() -> AgentProvider:
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIAgentProvider()
    return MockAgentProvider()


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
