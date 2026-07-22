"""daily.py 纯逻辑单测:WMO 映射 + 天气格式化 + 邮件拼装。

只测无需网络/密钥的纯函数。HTTP 编排通过 monkeypatch 注入 fake。
"""
from __future__ import annotations

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
    assert not q.startswith(" "), "应去除首尾空白"
