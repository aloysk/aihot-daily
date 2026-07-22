"""brief.py 纯逻辑单测:1301 内容审查识别 + 二分降级。

只测无需网络/密钥的纯函数。HTTP 编排(_call_glm/_summarize_resilient)
通过 monkeypatch 注入 fake,验证降级路径而非真实网络。
"""
from __future__ import annotations

import brief


# ---------- is_content_filtered ----------

def test_is_content_filtered_recognizes_1301():
    """真实失败日志里的 1301 响应体必须被识别为内容审查。"""
    payload = {
        "contentFilter": [{"level": 1, "role": "assistant"}],
        "error": {
            "code": "1301",
            "message": "系统检测到输入或生成内容可能包含不安全或敏感内容",
        },
    }
    assert brief.is_content_filtered(payload) is True


def test_is_content_filtered_rejects_other_errors():
    """配额(429/1302)、鉴权(401)等不应被误判为内容审查 —— 降级对它们无意义。"""
    assert brief.is_content_filtered({"error": {"code": "1302", "message": "余额不足"}}) is False
    assert brief.is_content_filtered({"error": {"code": "429", "message": "rate limit"}}) is False
    assert brief.is_content_filtered({}) is False


# ---------- halve ----------

def test_halve_even():
    assert brief.halve([1, 2, 3, 4]) == ([1, 2], [3, 4])


def test_halve_odd_keeps_left_larger():
    """奇数个时左半多一个,保证单元素列表也能切(左=[x], 右=[])。"""
    assert brief.halve([1, 2, 3]) == ([1, 2], [3])
    assert brief.halve([1]) == ([1], [])
    assert brief.halve([]) == ([], [])


# ---------- merge_briefs ----------

def test_merge_briefs_drops_empty_and_preserves_order():
    """降级路径:成功片段按顺序拼接,空片段(失败的一半)被丢弃。"""
    fragments = ["第一条\n第二条", "", "第三条", ""]
    merged = brief.merge_briefs(fragments)
    assert "第一条" in merged and "第二条" in merged and "第三条" in merged
    # 空片段不应产生多余分隔
    assert merged.count("---") == 0


# ---------- _summarize_resilient 降级编排 ----------

class _FilterErr(Exception):
    """测试桩:模拟 GLM 1301。"""


def _make_items(n: int) -> list[dict]:
    return [{"id": i, "title": f"新闻 {i}", "url": f"https://x/{i}"} for i in range(n)]


def test_resilient_no_filter_returns_whole_brief(monkeypatch):
    """全量请求未触发审查时,不应进入降级路径,直接返回。"""
    calls = []

    def fake_call(items, system_prompt=brief.SYSTEM_PROMPT):
        calls.append(len(items))
        return f"全量简报({len(items)} 条)"

    monkeypatch.setattr(brief, "_call_glm", fake_call)
    items = _make_items(25)
    result = brief._summarize_resilient(items)
    assert result.startswith("全量简报")
    assert len(calls) == 1, "未触发审查不应降级"


def test_resilient_filters_out_bad_half(monkeypatch):
    """全量触发 1301 时,二分降级:敏感的一半被丢弃,合法的一半仍产出。

    设定:全量(25 条)必失败;含 id>=20 的任何批次都失败(模拟那几条敏感)。
    期望:降级最终能产出非空简报,且成功批次里不含 id>=20 的条目。
    """
    BAD_IDS = {20, 21, 22, 23, 24}  # 这 5 条触发审查

    def fake_call(items, system_prompt=brief.SYSTEM_PROMPT):
        if any(i["id"] in BAD_IDS for i in items):
            raise _FilterErr("simulated 1301")
        return "合法批次简报:" + ",".join(str(i["id"]) for i in items)

    monkeypatch.setattr(brief, "_call_glm", fake_call)
    monkeypatch.setattr(brief, "GlmContentFilterError", _FilterErr)  # 让 except 命中
    items = _make_items(25)
    result = brief._summarize_resilient(items)
    assert result != "", "应通过降级产出非空简报"
    # 敏感 id 不应出现在最终结果里
    for bad in BAD_IDS:
        assert str(bad) not in result, f"敏感条目 {bad} 未被丢弃"


def test_resilient_all_filtered_returns_empty(monkeypatch):
    """全部条目都触发审查时,降级无法挽救,应返回空串(不静默假装成功)。"""
    def fake_call(items, system_prompt=brief.SYSTEM_PROMPT):
        raise _FilterErr("everything is sensitive")

    monkeypatch.setattr(brief, "_call_glm", fake_call)
    monkeypatch.setattr(brief, "GlmContentFilterError", _FilterErr)
    result = brief._summarize_resilient(_make_items(5))
    assert result == "", "全失败时返回空串,由上层判定为失败"


def test_resilient_non_filter_error_propagates(monkeypatch):
    """非 1301 错误(配额/网络)不应被吞,必须向上抛出,避免静默失败。"""
    def fake_call(items, system_prompt=brief.SYSTEM_PROMPT):
        raise RuntimeError("网络错误")

    monkeypatch.setattr(brief, "_call_glm", fake_call)
    # GlmContentFilterError 此时是真实类;RuntimeError 不是它的子类,应原样抛出
    raised = False
    try:
        brief._summarize_resilient(_make_items(4))
    except RuntimeError:
        raised = True
    assert raised, "非内容审查错误必须向上抛出"


def test_resilient_degradation_uses_fragment_prompt(monkeypatch):
    """回归:降级子调用必须用 SYSTEM_PROMPT_FRAGMENT,否则合并的片段会带重复编号/版块。

    全量调用 → 用 SYSTEM_PROMPT;触发 1301 二分后子调用 → 用 SYSTEM_PROMPT_FRAGMENT。
    """
    seen_prompts = []

    def fake_call(items, system_prompt=brief.SYSTEM_PROMPT):
        seen_prompts.append(system_prompt)
        if system_prompt is brief.SYSTEM_PROMPT:
            raise brief.GlmContentFilterError("simulated 1301")
        return "片段:" + ",".join(str(i["id"]) for i in items)

    monkeypatch.setattr(brief, "_call_glm", fake_call)
    brief._summarize_resilient(_make_items(8))

    # 首次全量调用用完整 prompt
    assert seen_prompts[0] is brief.SYSTEM_PROMPT
    # 所有降级子调用必须用 FRAGMENT(否则合并会重复编号/版块)
    degraded = seen_prompts[1:]
    assert degraded, "应触发降级"
    assert all(p is brief.SYSTEM_PROMPT_FRAGMENT for p in degraded), \
        "降级子调用必须用 SYSTEM_PROMPT_FRAGMENT"
