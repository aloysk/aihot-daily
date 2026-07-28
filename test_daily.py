"""daily.py 纯逻辑单测:WMO 映射 + 天气格式化 + 邮件拼装 + 金句去重。

只测无需网络/密钥的纯函数。HTTP 编排通过 monkeypatch 注入 fake。
"""
from __future__ import annotations

import base64
import json
import urllib.error

import daily


# ---------- WMO weather_code 中文映射 ----------

def test_wmo_clear_sky():
    """0 = 晴。"""
    assert "晴" in daily.wmo_desc(0)


def test_wmo_cloudy_variants():
    """1/2/3 = 少云/多云。"""
    for code in (1, 2, 3):
        desc = daily.wmo_desc(code)
        assert desc, f"code {code} 应有非空描述"


def test_wmo_thunderstorm():
    """95 = 雷暴。"""
    assert "雷" in daily.wmo_desc(95)


def test_wmo_rain():
    """61/63/65 = 小/中/大雨。"""
    assert "雨" in daily.wmo_desc(61)
    assert "雨" in daily.wmo_desc(63)
    assert "雨" in daily.wmo_desc(65)


def test_wmo_unknown_code_returns_placeholder():
    """未知 code 不应崩溃,返回占位符。"""
    desc = daily.wmo_desc(999)
    assert desc, "未知 code 应返回非空占位符,而非空串或抛错"


# ---------- 天气数据格式化 ----------

FAKE_OPEN_METEO = {
    "current": {
        "temperature_2m": 27.9,
        "relative_humidity_2m": 81,
        "weather_code": 3,
        "uv_index": 1.55,
    },
    "daily": {
        "temperature_2m_max": [31.0],
        "temperature_2m_min": [24.0],
        "precipitation_probability_max": [15],
    },
}


def test_format_weather_basic_fields():
    """格式化后的天气摘要包含:天气描述、温度范围、当前温度、湿度。"""
    text = daily.format_weather(FAKE_OPEN_METEO)
    assert "多云" in text, "weather_code 3 应映射为多云"
    assert "24" in text and "31" in text, "应有最低/最高温"
    assert "27.9" in text, "应有当前温度"
    assert "81" in text, "应有湿度"


def test_format_weather_handles_missing_daily():
    """缺 daily 字段(只给 current)不应崩溃。"""
    partial = {"current": FAKE_OPEN_METEO["current"], "daily": {}}
    text = daily.format_weather(partial)
    assert "多云" in text, "current 部分应仍能格式化"


# ---------- 邮件正文拼装 ----------

def test_compose_body_structure():
    """邮件正文应包含天气区和金句区两个版块。"""
    body = daily.compose_body("新加坡今日:多云,24~31°C", "成长就是不断打破昨天的自己。\n—— 佚名")
    assert "新加坡今日:多云,24~31°C" in body
    assert "成长就是不断打破昨天的自己" in body
    assert "金句" in body


# ---------- gen_quote(通过 monkeypatch _call_glm) ----------

def test_gen_quote_strips_output(monkeypatch):
    """gen_quote 应返回 GLM 输出的纯文本(去空白)。"""
    monkeypatch.setattr(daily, "_call_glm", lambda prompt: "  机会总是留给有准备的人。\n—— 巴斯德  ")
    q = daily.gen_quote()
    assert q == "机会总是留给有准备的人。\n—— 巴斯德"


def test_gen_quote_prompt_asks_for_english_first(monkeypatch):
    """prompt 应要求英文为主、英文须配中文释义;避免长期都是中文励志经典。

    验证 prompt 的关键约束词存在,不验证 GLM 的实际产出(那是模型行为)。
    """
    seen = {}
    def fake(prompt):
        seen["prompt"] = prompt
        return "Stay hungry, stay foolish.\n(求知若饥，虚心若愚)\n—— Steve Jobs"
    monkeypatch.setattr(daily, "_call_glm", fake)
    daily.gen_quote()
    p = seen["prompt"]
    assert "英文" in p, "prompt 应明确要求支持英文"
    assert "中文" in p, "prompt 应要求英文配中文释义"
    assert "不要" in p and ("重复" in p or "同一句" in p), "prompt 应要求避免重复"


# ---------- normalize(归一化,用于硬匹配去重) ----------

def test_normalize_strips_punctuation_and_case():
    """归一化:去标点、去空格、转小写。中英文都要处理。"""
    assert daily.normalize("Stay hungry, stay foolish!") == "stayhungrystayfoolish"
    assert daily.normalize("既然选择了远方，便只顾风雨兼程。") == "既然选择了远方便只顾风雨兼程"
    # 换行/多空格也要压平
    assert daily.normalize("A B\nC  D") == "abcd"


def test_normalize_empty_string():
    assert daily.normalize("") == ""


# ---------- is_duplicate(硬匹配) ----------

def test_is_duplicate_matches_normalized_substring():
    """历史里有相似句(归一化后子串包含)就算重复。"""
    history = ["既然选择了远方，便只顾风雨兼程。"]
    # 完全相同
    assert daily.is_duplicate("既然选择了远方，便只顾风雨兼程。", history) is True
    # 标点/空格不同但内容相同
    assert daily.is_duplicate("既然选择了远方,便只顾风雨兼程", history) is True
    # 不同内容
    assert daily.is_duplicate("另一句完全不同的话", history) is False


def test_is_duplicate_empty_history():
    assert daily.is_duplicate("任何金句", []) is False


# ---------- fetch_history(Contents API GET) ----------

class _FakeResp:
    """模拟 urllib.request.urlopen 的 context manager 响应。"""
    def __init__(self, data: bytes):
        self._data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self._data


def test_fetch_history_parses_base64(monkeypatch):
    """fetch_history 应解析 Contents API 返回的 base64 content,返回(列表, sha)。"""
    history = ["金句一", "金句二"]
    payload = {
        "content": base64.b64encode(
            json.dumps(history, ensure_ascii=False).encode()
        ).decode(),
        "sha": "abc123sha",
    }
    monkeypatch.setattr(
        daily.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(json.dumps(payload).encode())
    )
    quotes, sha = daily.fetch_history()
    assert quotes == ["金句一", "金句二"]
    assert sha == "abc123sha"


def test_fetch_history_missing_file_returns_empty(monkeypatch):
    """文件不存在(404)时返回空列表,不抛异常。"""
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, None
        )
    monkeypatch.setattr(daily.urllib.request, "urlopen", fake_urlopen)
    quotes, sha = daily.fetch_history()
    assert quotes == []
    assert sha is None


# ---------- push_history(Contents API PUT) ----------

def test_push_history_success(monkeypatch):
    """push 成功时不抛异常。"""
    captured = {}
    def fake_urlopen(req, timeout):
        captured["data"] = json.loads(req.data.decode())
        return _FakeResp(b'{"content":{"sha":"newsha"}}')
    monkeypatch.setattr(daily.urllib.request, "urlopen", fake_urlopen)
    daily.push_history(["旧金句", "新金句"], "oldsha")  # 不抛即通过
    assert captured["data"]["sha"] == "oldsha", "PUT 应带原 sha 做乐观锁"
    assert "新金句" not in captured["data"]["message"], "commit message 不应含金句原文(防注入)"


def test_push_history_conflict_raises(monkeypatch):
    """乐观锁冲突(409)应抛异常,由上层决定告警。"""
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 409, "Conflict", {}, None
        )
    monkeypatch.setattr(daily.urllib.request, "urlopen", fake_urlopen)
    raised = False
    try:
        daily.push_history(["x"], "stale_sha")
    except Exception:
        raised = True
    assert raised, "409 冲突应抛出,不静默"


# ---------- gen_quote(硬匹配重试) ----------

def test_gen_quote_retries_on_duplicate(monkeypatch):
    """生成的金句与历史重复时,应重试,直到产出不重复的。"""
    history = ["既然选择了远方，便只顾风雨兼程。"]
    calls = []
    def fake_call(prompt):
        calls.append(1)
        # 前 2 次返回历史里的重复句,第 3 次返回新句
        if len(calls) <= 2:
            return "既然选择了远方，便只顾风雨兼程。"
        return "完全不同的一句新金句。"
    monkeypatch.setattr(daily, "_call_glm", fake_call)
    q = daily.gen_quote(history)
    assert "完全不同" in q
    assert len(calls) == 3, "前 2 次重复应触发重试,第 3 次成功"


def test_gen_quote_accepts_after_max_retries(monkeypatch):
    """超过最大重试次数仍重复时,接受最后结果(不无限循环)。"""
    history = ["固定重复句。"]
    def fake_call(prompt):
        return "固定重复句。"  # 永远重复
    monkeypatch.setattr(daily, "_call_glm", fake_call)
    q = daily.gen_quote(history)  # 不应无限循环/崩溃
    assert q, "超限后应接受最后结果,返回非空"
