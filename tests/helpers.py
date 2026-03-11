from __future__ import annotations


def make_character(name: str, *, include_language: bool = True) -> dict:
    character = {
        "name": name,
        "occupation": "记者",
        "age": 28,
        "attributes": {
            "strength": 50,
            "constitution": 55,
            "size": 60,
            "dexterity": 65,
            "appearance": 45,
            "intelligence": 70,
            "power": 60,
            "education": 75,
        },
        "skills": {
            "图书馆使用": 70,
            "侦查": 60,
            "心理学": 50,
        },
    }
    if include_language:
        character["language_preference"] = "zh-CN"
    return character


def make_participant(
    actor_id: str,
    display_name: str,
    kind: str = "human",
    *,
    include_language: bool = True,
    imported_character_source_id: str | None = None,
    character_import_sync_policy: str | None = None,
) -> dict:
    participant = {
        "actor_id": actor_id,
        "display_name": display_name,
        "kind": kind,
        "character": make_character(display_name, include_language=include_language),
        "secrets": {
            "private_notes": [f"{display_name} 的私人笔记"],
            "personal_clues": [f"{display_name} 的私有线索"],
            "personal_goals": [f"{display_name} 的目标"],
            "hidden_flags": [f"{display_name} 的隐藏标记"],
            "knowledge_history": [f"{display_name} 的知识记录"],
        },
    }
    if imported_character_source_id is not None:
        participant["imported_character_source_id"] = imported_character_source_id
    if character_import_sync_policy is not None:
        participant["character_import_sync_policy"] = character_import_sync_policy
    return participant


def make_scenario(
    *,
    include_language: bool = True,
    clues: list[dict] | None = None,
    beats: list[dict] | None = None,
    scenes: list[dict] | None = None,
    npcs: list[dict] | None = None,
    start_scene_id: str | None = None,
) -> dict:
    scenario = {
        "title": "迷雾中的旅店",
        "hook": "调查员收到一封来自旧友的求助信。",
        "starting_location": "阿卡姆河畔旅店",
        "start_scene_id": start_scene_id,
        "tags": ["调查", "悬疑"],
        "scenes": scenes or [],
        "clues": clues or [],
        "beats": beats or [],
        "npcs": npcs or [],
    }
    if include_language:
        scenario["language_preference"] = "zh-CN"
    return scenario
