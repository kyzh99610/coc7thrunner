from __future__ import annotations

from typing import Any

from coc_runner.domain.models import (
    BeatCondition,
    BeatConsequence,
    ClueFailForwardTrigger,
    CurrentSceneInCondition,
    KeeperPromptPriority,
    LanguagePreference,
    MarkSceneObjectiveCompleteConsequence,
    QueueKPPromptConsequence,
    ScenarioBeat,
    ScenarioClue,
    ScenarioNPC,
    ScenarioScaffold,
    ScenarioScene,
    SceneObjective,
    UpdateNPCAttitudeConsequence,
    ApplyStatusConsequence,
)


def build_whispering_guesthouse_scenario() -> ScenarioScaffold:
    return ScenarioScaffold(
        scenario_id="scenario.whispering_guesthouse",
        title="雾港旅店的低语",
        hook="一名旧友来信称旅店老板封死了地下储物间，却每晚都能听见门后传来低语。",
        starting_location="雾港旅店",
        start_scene_id="scene.guesthouse_lobby",
        tags=["调查", "旅店", "低语", "小型剧本"],
        scenes=[
            ScenarioScene(
                scene_id="scene.guesthouse_lobby",
                title="雾港旅店大堂",
                summary="老式煤气灯照着空荡大堂，秦老板正反复擦拭柜台。",
                phase="investigation",
                revealed=True,
                linked_clue_ids=["clue.old_floorplan"],
                scene_objectives=[
                    SceneObjective(
                        objective_id="objective.lobby.observe_keeper",
                        text="确认老板是否在刻意回避储物间问题",
                        beat_id="beat.lobby_pressure",
                    )
                ],
                keeper_notes=["如果调查员直接提到地窖，老板先否认其存在。"],
            ),
            ScenarioScene(
                scene_id="scene.guesthouse_office",
                title="旅店账房",
                summary="账房里堆着旧账册、欠条与发黄的维修文件。",
                phase="investigation",
                linked_clue_ids=["clue.old_floorplan", "clue.office_ledger"],
                scene_objectives=[
                    SceneObjective(
                        objective_id="objective.office.find_records",
                        text="找到能指向地窖的记录",
                        beat_id="beat.office_records",
                    )
                ],
                keeper_notes=["账房门平时上锁，但老板转身时可趁机溜入。"],
            ),
            ScenarioScene(
                scene_id="scene.guesthouse_cellar",
                title="封死的地窖门",
                summary="走廊尽头的木门被铁链封住，门槛边有异常磨损与潮湿腥味。",
                phase="investigation",
                linked_clue_ids=["clue.cellar_sigil"],
                scene_objectives=[
                    SceneObjective(
                        objective_id="objective.cellar.assess_whispers",
                        text="确认地窖入口异常并决定是否需要理智审阅",
                        beat_id="beat.cellar_entry",
                    )
                ],
                keeper_notes=["若调查员继续贴门聆听，低语会明显加重心理压力。"],
            ),
        ],
        clues=[
            ScenarioClue(
                clue_id="clue.old_floorplan",
                title="旅店旧图纸",
                text="旧图纸显示账房后方原本有通往地下储物间的狭窄通道。",
                visibility_scope="kp_only",
                language_preference=LanguagePreference.ZH_CN,
            ),
            ScenarioClue(
                clue_id="clue.office_ledger",
                title="储物间账本残页",
                text="残页上记着夜间有人额外搬运煤炭，却从未登记地窖钥匙。",
                visibility_scope="kp_only",
                language_preference=LanguagePreference.ZH_CN,
            ),
            ScenarioClue(
                clue_id="clue.cellar_sigil",
                title="地窖门槛符号",
                text="门槛内侧被反复描摹过一个歪斜的旧印记。",
                visibility_scope="kp_only",
                core_clue_flag=True,
                alternate_paths=["查看旧图纸上的封堵标记", "逼问老板为什么封死地窖入口"],
                fail_forward_text="即使侦查失败，也会因门槛磨损与异味意识到这里存在关键异常。",
                fail_forward_triggers=[
                    ClueFailForwardTrigger(
                        action_types=["investigate_search"],
                        required_topic="term:spot_hidden",
                        fallback_status="partially_understood",
                        reveal_to="party",
                        assign_to_actor=False,
                        discovered_via="cellar_fail_forward",
                    )
                ],
                language_preference=LanguagePreference.ZH_CN,
            ),
        ],
        beats=[
            ScenarioBeat(
                beat_id="beat.lobby_pressure",
                title="稳住老板并找到账房线索",
                start_unlocked=True,
                complete_conditions=BeatCondition.model_validate(
                    {"clue_discovered": {"clue_id": "clue.old_floorplan"}}
                ),
                consequences=[
                    BeatConsequence(
                        queue_kp_prompts=[
                            QueueKPPromptConsequence(
                                prompt_text="KP：秦老板看到调查员翻出旧图纸时，应表现出短暂失态。",
                                category="npc_reaction",
                                scene_id="scene.guesthouse_lobby",
                                reason="调查员接近了被隐瞒的地窖线索",
                            )
                        ],
                        mark_scene_objectives_complete=[
                            MarkSceneObjectiveCompleteConsequence(
                                objective_id="objective.lobby.observe_keeper",
                                scene_id="scene.guesthouse_lobby",
                            )
                        ],
                    )
                ],
            ),
            ScenarioBeat(
                beat_id="beat.office_records",
                title="核对账房记录",
                unlock_conditions=BeatCondition.model_validate(
                    {
                        "all_of": [
                            {
                                "beat_status_is": {
                                    "beat_id": "beat.lobby_pressure",
                                    "status": "completed",
                                }
                            },
                            {"current_scene_in": {"scene_ids": ["scene.guesthouse_office"]}},
                        ]
                    }
                ),
                complete_conditions=BeatCondition.model_validate(
                    {"clue_discovered": {"clue_id": "clue.office_ledger"}}
                ),
                consequences=[
                    BeatConsequence(
                        npc_attitude_updates=[
                            UpdateNPCAttitudeConsequence(
                                npc_id="npc.innkeeper",
                                attitude="defensive",
                                note="老板开始刻意遮掩与地窖有关的话题。",
                            )
                        ],
                        queue_kp_prompts=[
                            QueueKPPromptConsequence(
                                prompt_text="KP：若调查员提到地窖，秦老板应先否认，再含糊改口。",
                                category="npc_reaction",
                                scene_id="scene.guesthouse_office",
                                reason="账房记录暴露了老板的隐瞒",
                            )
                        ],
                        mark_scene_objectives_complete=[
                            MarkSceneObjectiveCompleteConsequence(
                                objective_id="objective.office.find_records",
                                scene_id="scene.guesthouse_office",
                            )
                        ],
                    )
                ],
            ),
            ScenarioBeat(
                beat_id="beat.cellar_entry",
                title="检查封死的地窖门",
                optional_clues=["clue.cellar_sigil"],
                unlock_conditions=BeatCondition.model_validate(
                    {
                        "all_of": [
                            {
                                "beat_status_is": {
                                    "beat_id": "beat.office_records",
                                    "status": "completed",
                                }
                            },
                            {"current_scene_in": {"scene_ids": ["scene.guesthouse_cellar"]}},
                        ]
                    }
                ),
                complete_conditions=BeatCondition.model_validate(
                    {"clue_discovered": {"clue_id": "clue.cellar_sigil"}}
                ),
                consequences=[
                    BeatConsequence(
                        apply_statuses=[
                            ApplyStatusConsequence(
                                actor_id="investigator-1",
                                add_temporary_conditions=["心神不宁"],
                            )
                        ],
                        queue_kp_prompts=[
                            QueueKPPromptConsequence(
                                prompt_text="KP：地窖低语可能触发理智检定，请人工确认是否需要立即审阅。",
                                category="sanity_review",
                                scene_id="scene.guesthouse_cellar",
                                reason="核心线索伴随明显精神压力",
                            )
                        ],
                        mark_scene_objectives_complete=[
                            MarkSceneObjectiveCompleteConsequence(
                                objective_id="objective.cellar.assess_whispers",
                                scene_id="scene.guesthouse_cellar",
                            )
                        ],
                    )
                ],
            ),
            ScenarioBeat(
                beat_id="beat.sanity_review",
                title="裁定地窖低语的理智影响",
                scene_objective="决定是否要求理智检定与人工审阅",
                unlock_conditions=BeatCondition.model_validate(
                    {
                        "all_of": [
                            {
                                "beat_status_is": {
                                    "beat_id": "beat.cellar_entry",
                                    "status": "completed",
                                }
                            },
                            {"scene_is": {"scene_id": "scene.guesthouse_cellar"}},
                            {"any_actor_has_status": {"status": "心神不宁"}},
                        ]
                    }
                ),
            ),
        ],
        npcs=[
            ScenarioNPC(
                npc_id="npc.innkeeper",
                name="秦老板",
                role="旅店老板",
                initial_attitude="guarded",
                keeper_notes=["他知道地窖里曾发生过事，但不愿主动提起。"],
            )
        ],
        language_preference=LanguagePreference.ZH_CN,
    )


def whispering_guesthouse_payload() -> dict[str, Any]:
    return build_whispering_guesthouse_scenario().model_dump(mode="json")


def build_midnight_archive_scenario() -> ScenarioScaffold:
    return ScenarioScaffold(
        scenario_id="scenario.midnight_archive",
        title="雨夜档案馆",
        hook="档案馆守夜人声称地下楼梯间在深夜会传来灼热气味与金属摩擦声。",
        starting_location="旧城区档案馆",
        start_scene_id="scene.archive_reading_room",
        tags=["调查", "档案馆", "雨夜", "小型剧本"],
        scenes=[
            ScenarioScene(
                scene_id="scene.archive_reading_room",
                title="阅览室",
                summary="昏黄台灯下摆着未归档的借阅目录，窗外雨声盖住了街道动静。",
                phase="investigation",
                revealed=True,
                linked_clue_ids=["clue.burned_memo"],
                scene_objectives=[
                    SceneObjective(
                        objective_id="objective.archive.review_catalog",
                        text="确认是否有人在档案馆关闭后进入地下区域",
                        beat_id="beat.archive_review_catalog",
                    )
                ],
                keeper_notes=["守夜人不愿独自靠近地下楼梯间，但会提供旧借阅记录。"],
            ),
            ScenarioScene(
                scene_id="scene.archive_basement_stairs",
                title="地下楼梯间",
                summary="楼梯扶手带着异常温热，台阶边缘有像被拖拽过的细碎擦痕。",
                phase="investigation",
                revealed=False,
                linked_clue_ids=["clue.burn_mark"],
                scene_objectives=[
                    SceneObjective(
                        objective_id="objective.archive.inspect_stairs",
                        text="判断楼梯间异常是否值得立即下探",
                        beat_id="beat.archive_inspect_stairs",
                    )
                ],
                keeper_notes=["若调查员在此停留过久，可引导出压抑与灼热感。"],
            ),
        ],
        clues=[
            ScenarioClue(
                clue_id="clue.burned_memo",
                title="烧焦便笺",
                text="便笺提到地下保管柜被临时加锁，且夜间有人借走了不该外借的卷宗。",
                visibility_scope="kp_only",
                language_preference=LanguagePreference.ZH_CN,
            ),
            ScenarioClue(
                clue_id="clue.burn_mark",
                title="楼梯灼痕",
                text="扶手与台阶交界处有新近灼痕，像是有人拖着滚烫金属物经过。",
                visibility_scope="kp_only",
                core_clue_flag=True,
                alternate_paths=["核对夜间借阅目录与守夜人口供", "观察楼梯扶手的余温与焦味来源"],
                fail_forward_text="即使没找到完整证据，也会意识到地下楼梯间发生了异常且不可忽视。",
                fail_forward_triggers=[
                    ClueFailForwardTrigger(
                        action_types=["investigate_search"],
                        required_topic="term:spot_hidden",
                        fallback_status="partially_understood",
                        reveal_to="actor",
                        assign_to_actor=True,
                        discovered_via="archive_fail_forward",
                    )
                ],
                language_preference=LanguagePreference.ZH_CN,
            ),
        ],
        beats=[
            ScenarioBeat(
                beat_id="beat.archive_review_catalog",
                title="核对夜间借阅目录",
                start_unlocked=True,
                complete_conditions=BeatCondition.model_validate(
                    {"clue_discovered": {"clue_id": "clue.burned_memo"}}
                ),
                consequences=[
                    BeatConsequence(
                        reveal_scenes=[{"scene_id": "scene.archive_basement_stairs"}],
                        queue_kp_prompts=[
                            QueueKPPromptConsequence(
                                prompt_text="KP：守夜人提到地下楼梯间时，应明显压低声音并看向走廊尽头。",
                                category="npc_reaction",
                                scene_id="scene.archive_reading_room",
                                reason="阅览室线索让守夜人意识到调查员已接近真相。",
                            )
                        ],
                        mark_scene_objectives_complete=[
                            MarkSceneObjectiveCompleteConsequence(
                                objective_id="objective.archive.review_catalog",
                                scene_id="scene.archive_reading_room",
                            )
                        ],
                    )
                ],
                next_beats=["beat.archive_inspect_stairs"],
            ),
            ScenarioBeat(
                beat_id="beat.archive_inspect_stairs",
                title="检查地下楼梯间",
                optional_clues=["clue.burn_mark"],
                unlock_conditions=BeatCondition.model_validate(
                    {
                        "all_of": [
                            {
                                "beat_status_is": {
                                    "beat_id": "beat.archive_review_catalog",
                                    "status": "completed",
                                }
                            },
                            {"current_scene_in": {"scene_ids": ["scene.archive_basement_stairs"]}},
                        ]
                    }
                ),
                complete_conditions=BeatCondition.model_validate(
                    {"clue_discovered": {"clue_id": "clue.burn_mark"}}
                ),
                consequences=[
                    BeatConsequence(
                        apply_statuses=[
                            ApplyStatusConsequence(
                                actor_id="investigator-1",
                                add_temporary_conditions=["余悸"],
                            )
                        ],
                        queue_kp_prompts=[
                            QueueKPPromptConsequence(
                                prompt_text="KP：若调查员继续下探，先确认是否需要追加理智或危险审阅。",
                                category="hazard_review",
                                scene_id="scene.archive_basement_stairs",
                                reason="楼梯间异常带来明确风险与心理压力。",
                            )
                        ],
                        mark_scene_objectives_complete=[
                            MarkSceneObjectiveCompleteConsequence(
                                objective_id="objective.archive.inspect_stairs",
                                scene_id="scene.archive_basement_stairs",
                            )
                        ],
                    )
                ],
            ),
            ScenarioBeat(
                beat_id="beat.archive_decide_descent",
                title="决定是否继续下探",
                scene_objective="决定是立即下楼、呼叫支援，还是先稳住情绪。",
                unlock_conditions=BeatCondition.model_validate(
                    {
                        "all_of": [
                            {
                                "beat_status_is": {
                                    "beat_id": "beat.archive_inspect_stairs",
                                    "status": "completed",
                                }
                            },
                            {"scene_is": {"scene_id": "scene.archive_basement_stairs"}},
                            {"any_actor_has_status": {"status": "余悸"}},
                        ]
                    }
                ),
            ),
        ],
        npcs=[
            ScenarioNPC(
                npc_id="npc.archivist",
                name="守夜人陆先生",
                role="档案馆守夜人",
                initial_attitude="nervous",
                keeper_notes=["他知道楼梯间最近不对劲，但不愿意独自确认。"],
            )
        ],
        language_preference=LanguagePreference.ZH_CN,
    )


def midnight_archive_payload() -> dict[str, Any]:
    return build_midnight_archive_scenario().model_dump(mode="json")


def build_blackout_clinic_scenario() -> ScenarioScaffold:
    return ScenarioScaffold(
        scenario_id="scenario.blackout_clinic",
        title="停电诊所的冷光",
        hook="深夜停电后，一家私人诊所的备用电灯忽明忽暗，护士坚称病历室里传出过金属碰撞声。",
        starting_location="河岸私立诊所",
        start_scene_id="scene.clinic_reception",
        tags=["调查", "诊所", "停电", "小型剧本"],
        scenes=[
            ScenarioScene(
                scene_id="scene.clinic_reception",
                title="诊所前台",
                summary="前台台灯靠备用电池勉强亮着，潮湿空气里混着消毒水和焦味。",
                phase="investigation",
                revealed=True,
                linked_clue_ids=["clue.intake_log"],
                scene_objectives=[
                    SceneObjective(
                        objective_id="objective.clinic.review_intake",
                        text="确认停电前最后接触病历室的人是谁",
                        beat_id="beat.clinic_review_intake",
                    )
                ],
                keeper_notes=["护士陈述时语速很快，但一提病历室就明显停顿。"],
            ),
            ScenarioScene(
                scene_id="scene.clinic_records",
                title="病历室",
                summary="金属档案柜半开着，冷白应急灯照出地上拖拽留下的浅痕。",
                phase="investigation",
                revealed=False,
                linked_clue_ids=["clue.cabinet_key"],
                scene_objectives=[
                    SceneObjective(
                        objective_id="objective.clinic.inspect_records",
                        text="决定是先核对病历柜，还是先稳住惊慌的护士",
                        beat_id="beat.clinic_inspect_records",
                    )
                ],
                keeper_notes=["病历室异响可引出即时危险判断或额外压力。"],
            ),
        ],
        clues=[
            ScenarioClue(
                clue_id="clue.intake_log",
                title="夜班登记簿",
                text="登记簿显示停电前最后进入病历室的是一名临时护工。",
                visibility_scope="kp_only",
                language_preference=LanguagePreference.ZH_CN,
            ),
            ScenarioClue(
                clue_id="clue.cabinet_key",
                title="病历柜钥匙",
                text="钥匙挂在档案柜背后的钉子上，只会被真正进入病历室的人看到。",
                visibility_scope="kp_only",
                language_preference=LanguagePreference.ZH_CN,
            ),
        ],
        beats=[
            ScenarioBeat(
                beat_id="beat.clinic_review_intake",
                title="核对夜班登记",
                start_unlocked=True,
                complete_conditions=BeatCondition.model_validate(
                    {"clue_discovered": {"clue_id": "clue.intake_log"}}
                ),
                consequences=[
                    BeatConsequence(
                        reveal_scenes=[{"scene_id": "scene.clinic_records"}],
                        reveal_clues=[
                            {
                                "clue_id": "clue.cabinet_key",
                                "share_with_party": False,
                                "visible_to_actor_ids": ["investigator-1"],
                                "owner_actor_ids": ["investigator-1"],
                                "discovered_by_actor_ids": ["investigator-1"],
                                "discovered_via": "beat:beat.clinic_review_intake",
                            }
                        ],
                        queue_kp_prompts=[
                            QueueKPPromptConsequence(
                                prompt_text="KP：护士提到病历室时应立即打断自己，并催调查员先别开门。",
                                category="npc_pressure",
                                scene_id="scene.clinic_reception",
                                priority=KeeperPromptPriority.HIGH,
                                reason="护士知道病历室里发生了异常，但不敢直说。",
                            )
                        ],
                        mark_scene_objectives_complete=[
                            MarkSceneObjectiveCompleteConsequence(
                                objective_id="objective.clinic.review_intake",
                                scene_id="scene.clinic_reception",
                            )
                        ],
                    )
                ],
                next_beats=["beat.clinic_inspect_records"],
            ),
            ScenarioBeat(
                beat_id="beat.clinic_inspect_records",
                title="检查病历室",
                unlock_conditions=BeatCondition.model_validate(
                    {
                        "all_of": [
                            {
                                "beat_status_is": {
                                    "beat_id": "beat.clinic_review_intake",
                                    "status": "completed",
                                }
                            },
                            {"scene_is": {"scene_id": "scene.clinic_records"}},
                            {
                                "actor_owns_clue": {
                                    "actor_id": "investigator-1",
                                    "clue_id": "clue.cabinet_key",
                                }
                            },
                        ]
                    }
                ),
                scene_objective="决定先检查钥匙对应的柜门，还是先回头安抚护士。",
            ),
        ],
        npcs=[
            ScenarioNPC(
                npc_id="npc.clinic_nurse",
                name="许护士",
                role="夜班护士",
                initial_attitude="anxious",
                keeper_notes=["她知道停电前有人进入病历室，但担心牵连自己。"],
            )
        ],
        language_preference=LanguagePreference.ZH_CN,
    )


def blackout_clinic_payload() -> dict[str, Any]:
    return build_blackout_clinic_scenario().model_dump(mode="json")
