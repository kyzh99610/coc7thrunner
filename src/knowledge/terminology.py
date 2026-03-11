from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NormalizedTermMatch:
    dev_id: str
    canonical_zh: str
    matched_alias: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _TermDefinition:
    dev_id: str
    canonical_zh: str
    aliases: tuple[str, ...]
    requires_context: bool = False


_TERM_DEFINITIONS: tuple[_TermDefinition, ...] = (
    _TermDefinition(
        dev_id="spot_hidden",
        canonical_zh="侦查",
        aliases=("侦察", "观察", "索敌", "查看"),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="brawl",
        canonical_zh="斗殴",
        aliases=("近战", "搏斗"),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="listen",
        canonical_zh="聆听",
        aliases=("听觉",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="library_use",
        canonical_zh="图书馆使用",
        aliases=("图书馆", "查阅"),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="sanity_point",
        canonical_zh="理智值",
        aliases=("SAN值", "San值", "SAN"),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="sanity_check",
        canonical_zh="理智检定",
        aliases=("SAN检定", "San check"),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="bout_of_madness",
        canonical_zh="疯狂发作",
        aliases=("临时疯狂",),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="bout_of_madness",
        canonical_zh="疯狂发作",
        aliases=("发疯",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="hard_success",
        canonical_zh="困难成功",
        aliases=(),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="extreme_success",
        canonical_zh="极难成功",
        aliases=(),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="pushed_roll",
        canonical_zh="推动检定",
        aliases=("推骰", "孤注一掷"),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="pushed_roll",
        canonical_zh="推动检定",
        aliases=("推动",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="psychology",
        canonical_zh="心理学",
        aliases=("心理",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="persuade",
        canonical_zh="说服",
        aliases=("劝说",),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="fast_talk",
        canonical_zh="话术",
        aliases=("忽悠", "花言巧语"),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="intimidate",
        canonical_zh="恐吓",
        aliases=("威胁", "威吓"),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="charm",
        canonical_zh="魅惑",
        aliases=("魅力",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="dodge",
        canonical_zh="闪避",
        aliases=(),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="first_aid",
        canonical_zh="急救",
        aliases=("紧急处理",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="medicine",
        canonical_zh="医学",
        aliases=("治疗",),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="occult",
        canonical_zh="神秘学",
        aliases=("密教知识",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="archaeology",
        canonical_zh="考古学",
        aliases=("考古",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="credit_rating",
        canonical_zh="信用评级",
        aliases=("信誉",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="cthulhu_mythos",
        canonical_zh="克苏鲁神话",
        aliases=("CM",),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="luck",
        canonical_zh="幸运",
        aliases=("幸运值", "运气"),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="combat",
        canonical_zh="战斗",
        aliases=("战斗回合",),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="chase",
        canonical_zh="追逐",
        aliases=("追逐战",),
        requires_context=False,
    ),
    _TermDefinition(
        dev_id="firearms",
        canonical_zh="射击",
        aliases=("枪械", "开枪"),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="throw",
        canonical_zh="投掷",
        aliases=(),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="navigate",
        canonical_zh="导航",
        aliases=("寻路",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="stealth",
        canonical_zh="潜行",
        aliases=("隐匿", "躲藏"),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="climb",
        canonical_zh="攀爬",
        aliases=("攀登",),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="swim",
        canonical_zh="游泳",
        aliases=(),
        requires_context=True,
    ),
    _TermDefinition(
        dev_id="house_rule",
        canonical_zh="房规",
        aliases=("house rule", "House Rule"),
        requires_context=False,
    ),
)

_CONTEXT_MARKERS: tuple[str, ...] = (
    "检定",
    "判定",
    "技能",
    "成功",
    "失败",
    "困难成功",
    "极难成功",
    "推动检定",
    "理智检定",
    "常规成功",
    "投掷",
    "投一次",
    "掷骰",
    "进行检定",
    "进行一次",
    "进行过",
    "做一次",
    "使用",
    "尝试",
    "资料",
    "报纸",
    "档案",
    "馆藏",
    "文献",
    "记录",
    "脚印",
    "线索",
    "痕迹",
    "暗门",
    "门后",
    "门边",
    "%",
    "值",
    "点",
)


def normalize_chinese_text(raw_text: str) -> str:
    """Normalize aliases to canonical Chinese display terms only."""

    matches = extract_term_matches(raw_text)
    if not matches:
        return raw_text

    normalized = raw_text
    for match in sorted(matches, key=lambda item: item.start, reverse=True):
        normalized = (
            normalized[: match.start]
            + match.canonical_zh
            + normalized[match.end :]
        )
    return normalized


def extract_term_matches(raw_text: str) -> list[NormalizedTermMatch]:
    """Extract developer-facing term matches without mutating user-facing text."""

    candidates: list[NormalizedTermMatch] = []
    for term in _TERM_DEFINITIONS:
        for surface_form in _iter_surface_forms(term):
            for start, end, matched_text in _find_surface_matches(raw_text, surface_form):
                if (
                    term.requires_context
                    and matched_text != term.canonical_zh
                    and not _has_term_context(raw_text, start, end)
                ):
                    continue
                candidates.append(
                    NormalizedTermMatch(
                        dev_id=term.dev_id,
                        canonical_zh=term.canonical_zh,
                        matched_alias=matched_text,
                        start=start,
                        end=end,
                    )
                )

    if not candidates:
        return []

    return _deduplicate_overlaps(candidates)


def _iter_surface_forms(term: _TermDefinition) -> tuple[str, ...]:
    surface_forms = {term.canonical_zh, *term.aliases}
    return tuple(sorted(surface_forms, key=len, reverse=True))


def _find_surface_matches(raw_text: str, surface_form: str) -> list[tuple[int, int, str]]:
    if _is_ascii_term(surface_form):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(surface_form)}(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
    elif _contains_ascii_letters(surface_form):
        pattern = re.compile(re.escape(surface_form), re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(surface_form))
    return [(match.start(), match.end(), raw_text[match.start() : match.end()]) for match in pattern.finditer(raw_text)]


def _is_ascii_term(surface_form: str) -> bool:
    return all(ord(character) < 128 for character in surface_form)


def _contains_ascii_letters(surface_form: str) -> bool:
    return any("A" <= character <= "Z" or "a" <= character <= "z" for character in surface_form)


def _has_term_context(raw_text: str, start: int, end: int) -> bool:
    window_start = max(0, start - 12)
    window_end = min(len(raw_text), end + 12)
    context_window = raw_text[window_start:window_end]
    return any(marker in context_window for marker in _CONTEXT_MARKERS)


def _deduplicate_overlaps(candidates: list[NormalizedTermMatch]) -> list[NormalizedTermMatch]:
    ordered = sorted(
        candidates,
        key=lambda item: (item.start, -(item.end - item.start), item.matched_alias),
    )
    accepted: list[NormalizedTermMatch] = []
    occupied_ranges: list[tuple[int, int]] = []

    for candidate in ordered:
        if any(_ranges_overlap(candidate.start, candidate.end, start, end) for start, end in occupied_ranges):
            continue
        accepted.append(candidate)
        occupied_ranges.append((candidate.start, candidate.end))

    # TODO: Replace simple proximity-based disambiguation with syntax-aware term classification later.
    return accepted


def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)
