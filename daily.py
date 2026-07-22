#!/usr/bin/env python3
"""每日天气+名言推送:Open-Meteo 新加坡天气 → GLM 励志金句 → Gmail 推送。

依赖:Python 3.10+ 标准库,无第三方包。
环境变量:
  GLM_API_KEY         (必填) GLM Coding Plan token
  GMAIL_APP_PASSWORD  (必填) Gmail 应用专用密码(16 位)
  SMTP_USER           (可选) 发件/收件邮箱,默认 xi.ke0709@gmail.com
  SMTP_TO             (可选) 收件邮箱,默认同 SMTP_USER

目的:每日定时稳定消耗 GLM quota。天气+名言内容安全,不会触发 1301。
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

GLM_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-5.2")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "50000"))

# 新加坡坐标;Open-Meteo 免费、无 key、稳定
SG_LAT, SG_LON = 1.3521, 103.8198
OPEN_METEO_URL = (
    f"https://api.open-meteo.com/v1/forecast"
    f"?latitude={SG_LAT}&longitude={SG_LON}"
    f"&current=temperature_2m,relative_humidity_2m,weather_code,uv_index"
    f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
    f"&timezone=Asia/Singapore&forecast_days=1"
)

DEFAULT_MAIL = "xi.ke0709@gmail.com"
SMTP_USER = os.environ.get("SMTP_USER", DEFAULT_MAIL)
SMTP_TO = os.environ.get("SMTP_TO", SMTP_USER)

# WMO 标准天气代码 → 中文描述(http://www.kma.go.kr/eng/biz/info_02.html)
WMO_CODE_CN: dict[int, str] = {
    0: "晴", 1: "晴间少云", 2: "少云", 3: "多云",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    77: "米雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}


def wmo_desc(code: int) -> str:
    """WMO weather_code 转中文描述;未知 code 返回占位符,绝不抛错。"""
    return WMO_CODE_CN.get(code, f"未知天气(code {code})")


def fetch_weather() -> dict:
    """拉取 Open-Meteo 新加坡当日天气,返回原始 JSON dict。"""
    with urllib.request.urlopen(OPEN_METEO_URL, timeout=30) as resp:
        data = json.load(resp)
    cur = data.get("current", {})
    print(
        f"[weather] {wmo_desc(cur.get('weather_code', -1))} "
        f"{cur.get('temperature_2m')}°C 湿度{cur.get('relative_humidity_2m')}%"
    )
    return data


def format_weather(data: dict) -> str:
    """把 Open-Meteo 响应格式化为一行人类可读的天气摘要。"""
    cur = data.get("current", {})
    daily = data.get("daily", {})
    desc = wmo_desc(cur.get("weather_code", -1))
    temp_cur = cur.get("temperature_2m", "?")
    humidity = cur.get("relative_humidity_2m", "?")
    uv = cur.get("uv_index", "?")
    t_max = (daily.get("temperature_2m_max") or ["?"])[0]
    t_min = (daily.get("temperature_2m_min") or ["?"])[0]
    rain = (daily.get("precipitation_probability_max") or ["?"])[0]
    return (
        f"新加坡今日:{desc} ⛅\n"
        f"气温:{t_min} ~ {t_max}°C(现在 {temp_cur}°C)\n"
        f"湿度:{humidity}% | 紫外线:{uv} | 降水概率:{rain}%"
    )


def _call_glm(prompt: str) -> str:
    """单次 GLM 调用,返回内容文本;失败抛出。"""
    body = json.dumps({
        "model": GLM_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": "你是金句编辑,只输出一句中文励志金句和作者归属。"},
            {"role": "user", "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(
        f"{GLM_BASE_URL}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['GLM_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        res = json.load(resp)
    return (res["choices"][0]["message"].get("content") or "").strip()


def gen_quote() -> str:
    """生成一句不重复的励志金句。用日期注入降低重复率。"""
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    prompt = f"今天是{today},请给一句鼓舞人心的中文励志金句,附作者。只输出金句和作者两行,不要解释。"
    quote = _call_glm(prompt).strip()
    print(f"[quote] {quote[:40]}...")
    return quote


def compose_body(weather_text: str, quote: str) -> str:
    """拼装邮件正文。"""
    return f"{weather_text}\n\n— 今日金句 —\n{quote}"


def send_mail(body: str) -> None:
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    subject = f"新加坡天气 · {today}"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = SMTP_TO
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(SMTP_USER, os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)
    print(f"[mail] sent -> {SMTP_TO} | {subject}")


def main() -> int:
    try:
        weather = fetch_weather()
    except Exception as e:  # noqa: BLE001
        print(f"[weather] FAILED: {e!r}", file=sys.stderr)
        return 1
    try:
        quote = gen_quote()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        print(f"[glm] HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[glm] FAILED: {e!r}", file=sys.stderr)
        return 1
    if not quote:
        print("[glm] 金句为空,视为失败", file=sys.stderr)
        return 1
    body = compose_body(format_weather(weather), quote)
    try:
        send_mail(body)
    except Exception as e:  # noqa: BLE001
        print(f"[mail] FAILED: {e!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
