"""Tests V0: registry identity + telemetry truthfulness."""

from __future__ import annotations

from registry import get_node, init_registry, list_nodes, resolve_node_id
from telemetry import emit, last_voice, list_events


def setup_function() -> None:
    # Re-init safe: init_registry is idempotent once filled; for tests we
    # only assert public behavior after first init.
    init_registry()


def test_resolve_aliases_to_stable_node_id() -> None:
    assert resolve_node_id("bano") == "aqua"
    assert resolve_node_id("aqua") == "aqua"
    assert resolve_node_id("nova") == "nova"
    assert resolve_node_id("desktop-main") == "desktop-main"
    assert resolve_node_id("desktop") == "desktop-main"


def test_registry_lists_seed_nodes_without_ip_as_identity() -> None:
    nodes = {n["nodeId"]: n for n in list_nodes()}
    assert "desktop-main" in nodes
    assert "aqua" in nodes
    assert "nova" in nodes
    assert nodes["aqua"]["room"] == "baño"
    assert nodes["nova"]["room"] == "dormitorio"
    # Endpoint es transporte; puede ser None/str, nunca la identidad.
    assert nodes["aqua"]["nodeId"] == "aqua"
    assert "audio_input" in nodes["aqua"]["capabilities"]
    assert "display" in nodes["desktop-main"]["capabilities"]


def test_telemetry_updates_last_voice() -> None:
    emit(
        "STT_RESULT",
        source_node="nova",
        source_room="dormitorio",
        text="Viernes, qué hora es",
        result="OK",
    )
    emit(
        "INTENT_RESOLVED",
        source_node="nova",
        source_room="dormitorio",
        intent="TIME_GET_CURRENT",
        target_node="core",
        result="MATCHED",
    )
    emit(
        "ACTION_COMPLETED",
        source_node="nova",
        source_room="dormitorio",
        intent="TIME_GET_CURRENT",
        target_node="core",
        response_node="nova",
        result="SUCCESS",
    )
    voice = last_voice()
    assert voice is not None
    assert voice["sourceNode"] == "nova"
    assert voice["sourceRoom"] == "dormitorio"
    assert voice["text"] == "Viernes, qué hora es"
    assert voice["intent"] == "TIME_GET_CURRENT"
    assert voice["result"] == "SUCCESS"

    events = list_events(source="nova", limit=20)
    assert any(e["eventType"] == "STT_RESULT" for e in events)


def test_get_node_by_alias() -> None:
    node = get_node("bano")
    assert node is not None
    assert node["nodeId"] == "aqua"
