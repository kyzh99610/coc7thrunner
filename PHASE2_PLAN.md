# Phase 2 Plan: Auto-Grounding & Keeper Workflow Polish

日期：2026-03-10
基线：PLAYTEST_RESULT_01 PASS（雾港旅店的低语 最小试玩版）
目标：从"KP 手动裁定一切"提升到"系统能自动命中常见规则，KP 只在必要时介入"

---

## 现状摘要

Phase 1 核心闭环已跑通：人物卡导入 → session start → player-action / manual-action → beat progression → clue visibility → scene reveal → character state mutation → keeper prompt lifecycle。

四个最明显缺口（均来自 PLAYTEST_RESULT_01）：

| # | 缺口 | 影响 |
|---|------|------|
| 1 | player-action 的 rules grounding 基本未命中 | 每次都要 KP 手动裁定，系统退化为纯笔记工具 |
| 2 | scenario 完成后 current_beat = null，无显式完结态 | KP 不知道是否"正式结束"，前端无法触发结局流 |
| 3 | sanity-review prompt 存在但后续自动化不够 | SAN 损失后的疯狂判定仍需 KP 全手动 |
| 4 | keeper workflow 偏 engine/debug 风格 | prompt 文案不适合给非技术 KP 看 |

附加背景：即将从笔记本迁移到 7950X3D + 64GB + 3090 Ti 桌面机，需要准备本地模型 / embeddings / 图片生成接入点。

---

## 任务 1：Rules Grounding 命中率提升（最高优先级）

### 目标
让 player-action 和 manual-action 的 `rules_query_text` 在典型中文调查场景下至少能命中一条相关 RuleChunk，从而产出有意义的 `RuleGroundingSummary`。

### 为什么现在做
这是 playtest 暴露的最大功能性缺口。当前 `_ground_rules_for_action` 几乎总是返回空结果，因为：
1. `_resolve_rules_query_text` 仅在请求显式携带 `rules_query_text` 或 structured_action 包含特定字段时才有值，大多数 free_text_action 不会命中。
2. terminology.py 的 `_TERM_DEFINITIONS` 只有 11 个术语，覆盖面太窄。
3. 测试用的 RuleChunk fixtures 是手写的少量条目，没有从实际规则书内容生成的 chunk。
4. `_matches_query` 的 token 分词对中文短句（如"我查看门边的脚印"）命中率很低——中文动词短语很难直接匹配 topic_key。

### 涉及文件
- `src/knowledge/terminology.py` — 扩展术语表
- `src/knowledge/retrieval.py` — 改进 `_matches_query` 和 `_split_query_tokens`
- `src/coc_runner/application/session_service.py` — 改进 `_resolve_rules_query_text` 回退逻辑
- `tests/fixtures/knowledge/` — 新增更完整的 CoC 7e 规则 chunks
- `tests/test_rules_query_acceptance.py` — 新增验收用例
- `tests/test_gameplay_grounding_integration.py` — 扩展集成测试

### 具体改进点

#### 1a. `_resolve_rules_query_text` 自动回退
当 `rules_query_text` 为空时，从 `action_text` 自动提取查询：
- 先跑 `extract_term_matches(action_text)`，如果命中任何术语 → 以术语的 canonical_zh 组合为 query_text
- 否则用 `action_text` 本身（去除人称代词和常见无信息量词）作为 fallback query
- 这是唯一需要改的核心逻辑

#### 1b. 术语表扩展
当前 11 个 term 不够。至少补齐以下高频调查/检定术语：
- 心理学、说服、话术、恐吓、魅惑、闪避、急救、医学、神秘学、考古学
- 追逐、战斗、先攻、闪避、射击、投掷
- 幸运、信用评级、克苏鲁神话
- 推动检定已有，但需补充"孤注一掷"别名

#### 1c. `_matches_query` 改进
增加一个 bigram 匹配层：对中文 query 按 2-gram 滑窗切分，与 chunk 的 normalized_haystack 做交集计分。这比纯 token split 对中文短语更友好。不替换现有逻辑，作为额外得分加成。

#### 1d. 测试用 fixture chunks
基于 house_rules.md 和 CoC 7e 核心规则，手写 20-30 条高质量 RuleChunk，覆盖：侦查、图书馆使用、理智检定、推动检定、幸运消耗、核心线索保底、战斗动作、追逐简化规则。

### 风险
- bigram 切分可能产生误匹配（"图书" 匹配到不相关 chunk）→ 通过 relevance score 权重控制
- 自动回退可能让本来不需要规则查询的叙事行动也触发查询 → 加 `skip_auto_grounding: bool` 字段或按 action_type 过滤

### 验收标准
- 以下 5 个典型 action_text 至少命中 1 条 chunk：
  1. "我查看门边的脚印" → 侦查相关
  2. "我去图书馆查阅旧报纸" → 图书馆使用相关
  3. "我尝试说服旅店老板开门" → 说服/话术相关
  4. "我推动侦查检定" → 推动检定相关
  5. "目睹深渊生物后进行理智检定" → 理智检定相关
- 全量测试套件通过
- 不影响现有 deterministic_handoff 和 beat condition 评估

---

## 任务 2：Scenario Completed / Epilogue State

### 目标
为 scenario 提供显式的完结状态机，使 KP 和前端能明确知道"模组已结束"。

### 为什么现在做
playtest 中最后一个 beat 完成后 `current_beat = null`，KP 无法区分"所有节奏点已完成 = 模组结束"和"系统出错 = 当前 beat 丢失"。前端也无法触发结局界面。

### 涉及文件
- `src/coc_runner/domain/models.py` — 新增 `ScenarioPhase` enum、修改 `ScenarioProgressState`
- `src/coc_runner/application/session_service.py` — beat completion 后检测 scenario 完成、触发 epilogue
- `src/coc_runner/domain/scenario_examples.py` — 给现有 scenario 补充 epilogue beat/config
- `tests/test_scenario_progression.py` — 验收测试

### 设计

```python
class ScenarioPhase(StrEnum):
    IN_PROGRESS = "in_progress"
    EPILOGUE = "epilogue"
    COMPLETED = "completed"
```

在 `ScenarioProgressState` 新增：
```python
scenario_phase: ScenarioPhase = ScenarioPhase.IN_PROGRESS
completed_at: datetime | None = None
epilogue_text: str | None = None
```

在 `ScenarioScaffold` 新增：
```python
completion_beat_ids: list[str] = Field(default_factory=list)
epilogue_text: str | None = None
epilogue_kp_prompt: str | None = None
```

逻辑：当 `completion_beat_ids` 中所有 beat 均为 COMPLETED → 自动转入 `EPILOGUE`；KP 确认 epilogue prompt 后 → 转入 `COMPLETED`；`SessionStatus` 随之设为 `COMPLETED`。

如果 `completion_beat_ids` 为空，回退到"所有 beat 均 COMPLETED"作为判定。

### 风险
- 某些场景不是线性的，可能存在"分支结局"→ `completion_beat_ids` 按分支定义，any_of/all_of 未来可扩展
- 自动设 SessionStatus.COMPLETED 可能误触 → epilogue 必须经 KP 确认

### 验收标准
- 雾港旅店 scenario 完成所有 beat 后，`scenario_phase == "epilogue"` 且 KP 收到 epilogue prompt
- KP 确认 epilogue prompt 后，`scenario_phase == "completed"` 且 `session.status == "completed"`
- 回滚能正确恢复 scenario_phase
- `current_beat = null` 且 `scenario_phase = "in_progress"` 时不误判为已完成

---

## 任务 3：Sanity-Review Deterministic Handoff 收尾

### 目标
当 SAN 损失触发疯狂阈值时，系统自动创建高优先级 KP prompt 并附带规则引用，形成从"SAN 变化 → 阈值检测 → 疯狂判定 prompt → KP 裁定 → 状态生效"的完整自动化链路。

### 为什么现在做
playtest 中 SAN 50 → 47 已能手动操作，但缺少阈值自动检测和结构化 prompt。真人多人场景中 KP 很容易漏掉需要判定的理智事件。

### 涉及文件
- `src/coc_runner/application/session_service.py` — `_apply_character_stat_effects` 后检测 SAN 阈值
- `src/coc_runner/domain/models.py` — 新增 `SanityCheckTrigger` 模型
- `src/knowledge/terminology.py` — 确保理智相关术语完整
- `tests/test_session_progression.py` — sanity trigger 验收

### 设计
在 `_apply_character_stat_effects` 中，当 san_delta < 0 时：

1. **单次损失 >= 5** → 触发临时疯狂检查 prompt（priority: HIGH）
2. **累计一小时内损失 >= 当前 SAN / 5** → 触发不定性疯狂检查 prompt（priority: HIGH）
3. **SAN 降到 0** → 触发永久疯狂 prompt（priority: HIGH，requires_explicit_approval: true）

每种触发自动生成：
```python
class SanityCheckTrigger(BaseModel):
    trigger_type: str  # "temporary_madness" | "indefinite_madness" | "permanent_madness"
    actor_id: str
    san_before: int
    san_after: int
    san_delta: int
    threshold_rule: str  # 引用规则文本
    recommended_action: str  # 中文建议
```

触发后：
- 向 `queued_kp_prompts` 追加结构化 prompt
- 向角色 `temporary_conditions` 追加 "需要进行理智审阅"
- 附带 `rules_grounding` 如果能命中理智检定相关 chunk

### 风险
- 阈值计算需要时间窗口内的 SAN 变化历史 → Phase 2 先用单次损失阈值，累计阈值标记为 TODO
- 不能自动决定疯狂表现（house_rules 明确禁止）→ 只生成 prompt，不自动应用状态

### 验收标准
- SAN delta >= -5 时自动生成 HIGH priority KP prompt
- SAN 降到 0 时自动生成永久疯狂 prompt
- prompt 包含 `trigger_type`、`san_before`、`san_after`、`threshold_rule`
- 不自动改变角色性格或疯狂状态（仅 prompt）
- 全套 12+ 现有测试不受影响

---

## 任务 4：Keeper Prompt 模板化与生命周期收尾

### 目标
将 keeper prompt 从 engine/debug 风格升级为面向非技术 KP 的结构化中文提示，并补齐 prompt 到期/超时/批量关闭机制。

### 为什么现在做
playtest 中 prompt 文案是开发用语（如"beat_reach_corridor 已完成，请确认下一步推进"），非技术 KP 看不懂。同时没有批量关闭或过期机制，长 session 会积累大量已过时 prompt。

### 涉及文件
- `src/coc_runner/domain/models.py` — `QueuedKPPrompt` 新增 `expires_after_beat`, `auto_dismiss_on_scene_change`
- `src/coc_runner/application/session_service.py` — prompt 模板化、场景变化时自动 dismiss 过期 prompt
- `src/coc_runner/domain/scenario_examples.py` — 更新现有 scenario 的 prompt 文案为面向 KP 的中文
- `tests/test_session_progression.py` — auto-dismiss 验收

### 设计

#### 4a. Prompt 模板层
新增 `_format_kp_prompt(category, context_values, language)` 方法，将 prompt_text 从硬编码切换为模板化：

```
category: "beat_completed" → "节奏「{beat_title}」已完成。建议推进：{next_beat_titles}。"
category: "sanity_check"   → "调查员 {actor_name} 理智值下降至 {san_after}（损失 {san_delta}），建议进行{trigger_type_zh}判定。"
category: "scene_objective" → "当前场景目标「{objective_text}」已达成，请决定是否切换场景。"
```

#### 4b. 自动 Dismiss 机制
在 `QueuedKPPrompt` 新增：
```python
expires_after_beat: str | None = None      # beat 完成后自动 dismiss
auto_dismiss_on_scene_change: bool = False  # 场景切换后自动 dismiss
```

在 beat completion 和 scene transition 的代码路径中，扫描并 dismiss 过期 prompt。

#### 4c. 批量操作
新增 `POST /sessions/{id}/keeper-prompts/batch-dismiss` 端点，允许 KP 一次性关闭所有 PENDING 或 ACKNOWLEDGED 状态的低优先级 prompt。

### 风险
- 模板化可能不覆盖所有 prompt 来源 → 保留 `prompt_text` 作为 fallback，模板只在 `category` 匹配时覆盖
- 自动 dismiss 可能误关重要 prompt → 只对显式标记了 `expires_after_beat` 或 `auto_dismiss_on_scene_change` 的 prompt 生效

### 验收标准
- 雾港旅店 scenario 的所有 prompt 文案为中文面向 KP 风格
- beat 完成后，标记了 `expires_after_beat` 的 prompt 自动变为 DISMISSED
- 批量 dismiss 端点正常工作
- 现有 prompt lifecycle 测试不受影响

---

## 任务 5：桌面机部署与本地模型接入准备

### 目标
为 7950X3D + 64GB + 3090 Ti 桌面机环境准备部署脚本和模型抽象层，使后续接入本地 LLM / embeddings / 图片生成有清晰的接口。

### 为什么现在做
硬件迁移即将发生。如果不预先定义模型抽象接口，后续每个功能（AI KP narration、AI investigator、embedding retrieval、image generation）都会各自 hardcode 不同的调用方式。

### 涉及文件
- 新增 `src/coc_runner/inference/__init__.py`
- 新增 `src/coc_runner/inference/contracts.py` — 抽象接口
- 新增 `src/coc_runner/inference/local_stub.py` — 本地 stub 实现
- `src/coc_runner/config.py` — 新增模型配置
- `pyproject.toml` — 可选依赖组
- 新增 `scripts/setup_desktop.sh` — 桌面机环境配置

### 设计

```python
# contracts.py
class TextGenerationBackend(Protocol):
    def generate(self, prompt: str, *, max_tokens: int = 2048, language: str = "zh-CN") -> str: ...

class EmbeddingBackend(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class ImageGenerationBackend(Protocol):
    def generate_image(self, prompt: str, *, width: int = 512, height: int = 512) -> bytes: ...
```

Phase 2 只实现 stub（返回固定文本/零向量/占位图片），不接入真实模型。真实模型接入是 Phase 3 任务。

Config 新增：
```python
text_model_backend: str = "stub"  # "stub" | "vllm" | "llamacpp" | "openai_compat"
embedding_backend: str = "stub"   # "stub" | "sentence_transformers" | "openai_compat"
image_backend: str = "disabled"   # "disabled" | "comfyui" | "diffusers"
```

### 风险
- 过早抽象可能与实际模型 API 不匹配 → 保持 Protocol 极简，只定义 generate/embed/generate_image 三个方法
- 不要在 Phase 2 引入任何真实模型依赖

### 验收标准
- `TextGenerationBackend`、`EmbeddingBackend`、`ImageGenerationBackend` Protocol 定义完成
- stub 实现通过基本调用测试
- config 新增三个 backend 配置项，默认均为 stub/disabled
- 现有测试不受影响（不依赖新模块）
- `scripts/setup_desktop.sh` 能在 Windows (Git Bash) 下正常执行基本环境检查

---

## 优先级排序与依赖关系

```
任务 1 (Rules Grounding) ──────── 无依赖，最高优先级
    │
    ├── 任务 3 (Sanity Handoff) ── 依赖任务 1 的术语表扩展
    │
任务 2 (Scenario Completed) ──── 无依赖，可与任务 1 并行
    │
    ├── 任务 4 (Keeper Prompt) ── 依赖任务 2 的 epilogue prompt
    │
任务 5 (Desktop Prep) ─────────── 无依赖，可并行，但优先级最低
```

建议执行顺序：1 → 2 → 3 → 4 → 5

---

## Patch-Ready Instructions for Codex（任务 1 专用）

```
You are continuing implementation of the CoC Runner MVP backend.
Do not remove or weaken the existing review gate.
Do not change the Chinese-first default.
Do not make AI actions bypass human review.
Do not restructure the existing architecture.

Implement the following targeted changes to improve rules grounding hit rate.
Each change must include tests. Run the full test suite after each step.

## Step 1: Auto-fallback for rules_query_text

In session_service.py, find the _resolve_rules_query_text method.
Currently it returns None when both rules_query_text and the structured_action
topic field are absent. Change it so that when the return value would be None,
it falls back to:

1. Call extract_term_matches(action_text) from knowledge.terminology.
2. If any terms matched, return a query string composed of the matched
   canonical_zh values joined by spaces (e.g. "侦查 推动检定").
3. If no terms matched, return the action_text itself after stripping
   leading personal pronouns (我/我们/他/她) and common filler words
   (想/要/准备/打算/决定/尝试).
4. If the result is empty or fewer than 2 characters, return None.

Add action_text as a new parameter to _resolve_rules_query_text.
Update all call sites to pass the action_text through.

Add tests in test_gameplay_grounding_integration.py:
- "我查看门边的脚印" → query text contains "侦查"
- "我去图书馆查阅旧报纸" → query text contains "图书馆使用"
- "我尝试说服旅店老板" → query text is "说服旅店老板" (stripped)
- "好的" → returns None (too short)

## Step 2: Expand terminology definitions

In src/knowledge/terminology.py, add the following term definitions to
_TERM_DEFINITIONS. Follow the exact same pattern as existing entries:

- psychology / 心理学 / aliases: ("心理",) / requires_context: True
- persuade / 说服 / aliases: ("劝说",) / requires_context: True
- fast_talk / 话术 / aliases: ("忽悠", "花言巧语") / requires_context: True
- intimidate / 恐吓 / aliases: ("威胁", "威吓") / requires_context: True
- charm / 魅惑 / aliases: ("魅力",) / requires_context: True
- dodge / 闪避 / aliases: () / requires_context: True
- first_aid / 急救 / aliases: ("紧急处理",) / requires_context: True
- medicine / 医学 / aliases: ("治疗",) / requires_context: True
- occult / 神秘学 / aliases: ("密教知识",) / requires_context: True
- archaeology / 考古学 / aliases: ("考古",) / requires_context: True
- credit_rating / 信用评级 / aliases: ("信誉",) / requires_context: True
- cthulhu_mythos / 克苏鲁神话 / aliases: ("CM",) / requires_context: False
- luck / 幸运 / aliases: ("幸运值", "运气") / requires_context: True
- combat / 战斗 / aliases: ("战斗回合",) / requires_context: False
- chase / 追逐 / aliases: ("追逐战",) / requires_context: False
- firearms / 射击 / aliases: ("枪械", "开枪") / requires_context: True
- throw / 投掷 / aliases: () / requires_context: True
- navigate / 导航 / aliases: ("寻路",) / requires_context: True
- stealth / 潜行 / aliases: ("隐匿", "躲藏") / requires_context: True
- climb / 攀爬 / aliases: ("攀登",) / requires_context: True
- swim / 游泳 / aliases: () / requires_context: True

Add a test in a new test_terminology_expansion.py:
- extract_term_matches("我想用心理学分析这个NPC") → has match with dev_id="psychology"
- extract_term_matches("我尝试说服店主") → has match with dev_id="persuade"
- extract_term_matches("进行一次闪避") → has match with dev_id="dodge"
- extract_term_matches("我查看克苏鲁神话相关资料") → has match with dev_id="cthulhu_mythos"

## Step 3: Add bigram matching layer to retrieval

In src/knowledge/retrieval.py, add a _bigram_overlap_score static method:

@staticmethod
def _bigram_overlap_score(query_text: str, haystack: str) -> int:
    """Count 2-character Chinese bigram overlaps between query and haystack."""
    # Extract only Chinese character bigrams
    query_chars = re.findall(r'[\u4e00-\u9fff]', query_text)
    hay_chars = re.findall(r'[\u4e00-\u9fff]', haystack)
    if len(query_chars) < 2 or len(hay_chars) < 2:
        return 0
    query_bigrams = {query_chars[i] + query_chars[i+1] for i in range(len(query_chars) - 1)}
    hay_bigrams = {hay_chars[i] + hay_chars[i+1] for i in range(len(hay_chars) - 1)}
    return len(query_bigrams & hay_bigrams)

In _matches_query, after the existing token matching loop, add:
    if self._bigram_overlap_score(normalized_query or query_text, normalized_haystack) >= 2:
        return True

In _chunk_relevance_score, add bigram overlap as a bonus:
    bigram_score = KnowledgeRetriever._bigram_overlap_score(
        normalized_query, normalized_content
    )
    score += min(bigram_score, 4)  # cap at 4 bonus points

Add a test in test_rules_query_acceptance.py:
- A chunk with content "侦查技能用于发现隐藏的线索" should match query "我仔细观察地上的线索"
  even without an explicit term match, via bigram overlap on "线索".

## Step 4: Add fixture rule chunks for acceptance testing

Create tests/fixtures/knowledge/coc7e_core_rules_phase2.md with the following
markdown sections. Each H2 section becomes one RuleChunk via existing ingest:

## 侦查（Spot Hidden）
侦查技能用于注意到隐藏的、不明显的或被遮蔽的物品或线索。成功的侦查检定可以发现脚印、暗门、隐蔽的武器或偷偷接近的人。
topic_key: term:spot_hidden
priority: 100
tags: 技能, 调查, 感知

## 图书馆使用（Library Use）
图书馆使用技能允许调查员在档案、图书馆、报社文件或任何有组织的信息存储中找到特定信息。
topic_key: term:library_use
priority: 100
tags: 技能, 调查, 信息

## 理智检定（Sanity Check）
当调查员遭遇超自然恐怖、目睹可怕场景或经历极端恐惧时需要进行理智检定。失败则损失理智值。单次损失5点或以上应检查临时疯狂。
topic_key: term:sanity_check
priority: 100
tags: 理智, 检定, 恐惧

## 推动检定（Pushed Roll）
推动检定允许调查员在初次检定失败后再试一次，但必须说明如何用不同方式重试。推动失败的后果比初次失败更严重。
topic_key: term:pushed_roll
priority: 100
tags: 检定, 重试

## 心理学（Psychology）
心理学技能用于判断他人是否在说谎、察觉隐藏的情绪或动机。
topic_key: term:psychology
priority: 100
tags: 技能, 社交, 调查

## 说服（Persuade）
说服技能用于通过逻辑论证和合理请求改变他人的态度或行为。
topic_key: term:persuade
priority: 100
tags: 技能, 社交

## 话术（Fast Talk）
话术技能用于通过花言巧语、快速欺骗或混淆视听暂时迷惑目标。
topic_key: term:fast_talk
priority: 100
tags: 技能, 社交

## 幸运消耗
调查员可以选择消耗幸运值来提高一次检定的成功等级。每消耗1点幸运可以将检定结果降低1点。幸运消耗一旦使用不可恢复。
topic_key: luck_spend
priority: 100
tags: 幸运, 检定, 资源

## 核心线索保底（Fail Forward）
核心线索不应因为单次检定失败而永久锁死。失败可以导致信息不完整、获取速度更慢或额外代价，但核心推进路径必须保持可达。
topic_key: core_clue_fail_forward
priority: 200
tags: 核心线索, 调查, 保底
source_kind: house_rule

## 战斗回合（Combat Round）
战斗按回合制进行。每个回合中每个参与者可以进行一次动作。敏捷值决定行动顺序。
topic_key: term:combat
priority: 100
tags: 战斗, 回合

Create a test that ingests these chunks and verifies that the 5 acceptance
queries from Step 1 each return at least 1 matched chunk.

Run the full test suite. All existing tests must pass.
```

---

## 版本记录
- v0.1：基于 PLAYTEST_RESULT_01 初始规划
