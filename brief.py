#!/usr/bin/env python3
"""AI HOT 每日简报:aihot.virxact.com → GLM-5.2 归纳 → Gmail 推送。

依赖:Python 3.10+ 标准库,无第三方包。
环境变量:
  GLM_API_KEY         (必填) GLM Coding Plan token
  GMAIL_APP_PASSWORD  (必填) Gmail 应用专用密码(16 位)
  SMTP_USER           (可选) 发件/收件邮箱,默认 xi.ke0709@gmail.com
  SMTP_TO             (可选) 收件邮箱,默认同 SMTP_USER
  HOURS_WINDOW        (可选) 滚动时间窗(小时),默认 24
  MAX_TOKENS          (可选) LLM 输出上限,默认 50000
  GLM_MODEL           (可选) LLM 模型,默认 glm-5.2
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

GLM_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-5.2")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "50000"))

AIHOT_BASE = "https://aihot.virxact.com/api/public/items"
# /api/public/* 走 nginx UA 黑名单挡商业爬虫,必须用浏览器 UA
AIHOT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TAKE = 50  # items 端点单页上限内,过去 24h 精选通常 < 50

DEFAULT_MAIL = "xi.ke0709@gmail.com"
SMTP_USER = os.environ.get("SMTP_USER", DEFAULT_MAIL)
SMTP_TO = os.environ.get("SMTP_TO", SMTP_USER)

SYSTEM_PROMPT = (
    "你是资深 AI 资讯编辑。把用户提供的 AI 动态 JSON 条目整理成一份简洁、有信息量的"
    "中文 markdown 简报。要求:1) 按「模型发布 / 产品发布 / 行业动态 / 论文研究 / "
    "技巧与观点」分版块,没有条目的版块省略;2) 每条格式:**标题 — 来源** + 一句话"
    "摘要 + 原文链接;3) 全局连续编号;4) 开头一句话概述今日重点;5) 只输出 markdown,"
    "不要啰嗦开场白或元说明;6) 若某条内容涉及政治/暴力/敏感争议,客观中立转述或"
    "直接跳过该条,绝不做发散评论,避免触发内容审查。"
)

# 降级路径用:不分版块、不要概述、不要编号,多段可直接拼接
SYSTEM_PROMPT_FRAGMENT = (
    "把用户提供的 AI 动态 JSON 条目整理成中文 markdown 条目列表。每条格式:"
    "**标题 — 来源** + 一句话摘要 + 原文链接,条目间用空行分隔。"
    "只输出条目,不要概述、不要版块标题、不要编号。"
    "遇政治/暴力/敏感争议客观中立转述或跳过,避免触发内容审查。"
)


class GlmContentFilterError(Exception):
    """GLM 1301 内容审查拦截。降级路径据此二分重试。"""


def is_content_filtered(payload: dict) -> bool:
    """GLM 错误响应是否为 1301 内容审查。配额/鉴权等其他错误返回 False。"""
    err = payload.get("error") or {}
    return str(err.get("code", "")) == "1301"


def fetch_aihot(hours: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    qs = urllib.parse.urlencode({"mode": "selected", "since": since, "take": TAKE})
    req = urllib.request.Request(
        f"{AIHOT_BASE}?{qs}", headers={"User-Agent": AIHOT_UA}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    items = data.get("items", [])
    print(f"[aihot] since={since} fetched {len(items)} items")
    return items


def _call_glm(items: list[dict], system_prompt: str = SYSTEM_PROMPT) -> str:
    """单次 GLM 调用,返回内容文本。1301 抛 GlmContentFilterError,其他错误原样抛。"""
    body = json.dumps({
        "model": GLM_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
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
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            res = json.load(resp)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # 网关返回非 JSON 错误页(HTML/空 body 等):原样抛出 HTTPError,保留真实状态码
            raise
        if is_content_filtered(payload):
            raise GlmContentFilterError(f"HTTP {e.code}: {payload}")
        raise
    choice = res["choices"][0]
    usage = res.get("usage", {})
    details = usage.get("completion_tokens_details", {})
    print(
        f"[glm] prompt={usage.get('prompt_tokens')} "
        f"completion={usage.get('completion_tokens')} "
        f"reasoning={details.get('reasoning_tokens')} "
        f"finish={choice.get('finish_reason')}"
    )
    if choice.get("finish_reason") == "length":
        print("[glm] WARN: 输出被截断(finish_reason=length),如内容不全请调大 MAX_TOKENS")
    return (choice["message"].get("content") or "").strip()


def halve(items: list) -> tuple[list, list]:
    """对半切;奇数时左半多一个,单元素切成 ([x], []),空列表切成 ([], [])。"""
    if not items:
        return [], []
    mid = (len(items) + 1) // 2  # 左半多一个
    return items[:mid], items[mid:]


def merge_briefs(fragments: list[str]) -> str:
    """合并降级产出的条目片段:丢弃空片段,用空行连接。"""
    return "\n\n".join(f for f in fragments if f.strip())


def _summarize_resilient(items: list[dict], fragment: bool = False) -> str:
    """带 1301 降级的归纳:全量失败时二分重试,丢弃触发审查的部分。

    全量成功 → 直接返回完整简报(用 SYSTEM_PROMPT)。
    全量 1301 → 切两半各自降级(用 SYSTEM_PROMPT_FRAGMENT,产出可拼接的条目)。
    非内容审查错误 → 原样抛出(不静默吞配额/网络错误)。
    """
    if not items:
        return ""
    prompt = SYSTEM_PROMPT_FRAGMENT if fragment else SYSTEM_PROMPT
    try:
        return _call_glm(items, system_prompt=prompt)
    except GlmContentFilterError:
        if len(items) == 1:
            # 单条仍触发审查,丢弃该条
            print(f"[glm] 丢弃触发内容审查的条目: {items[0].get('title', items[0])}")
            return ""
        print(f"[glm] {len(items)} 条触发内容审查,二分降级")
        left, right = halve(items)
        fragments = [
            _summarize_resilient(left, fragment=True) if left else "",
            _summarize_resilient(right, fragment=True) if right else "",
        ]
        return merge_briefs(fragments)


def summarize(items: list[dict]) -> str:
    """向后兼容的归纳入口;实际逻辑在 _summarize_resilient。"""
    return _summarize_resilient(items)


def send_mail(brief: str, n_items: int) -> None:
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    subject = f"AI HOT 日报 · {today}（{n_items} 条）"
    msg = MIMEText(brief, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = SMTP_TO
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls()
        s.login(SMTP_USER, os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)
    print(f"[mail] sent -> {SMTP_TO} | {subject}")


def main() -> int:
    hours = int(os.environ.get("HOURS_WINDOW", "24"))
    try:
        items = fetch_aihot(hours)
    except Exception as e:  # noqa: BLE001 - 顶层流程,任意失败都要可见退出
        print(f"[aihot] FAILED: {e!r}", file=sys.stderr)
        return 1
    if not items:
        print("[aihot] 过去窗口内无精选条目,跳过")
        return 0
    try:
        brief = summarize(items)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        print(f"[glm] HTTP {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[glm] FAILED: {e!r}", file=sys.stderr)
        return 1
    if not brief:
        # GLM 产出空内容几乎总是模型/配额/过滤问题,不是"今天没新闻";
        # 视为失败(return 1)让 run 变红,触发下方 Alert on failure 步骤发告警邮件
        print("[glm] 简报为空(GLM 未产出内容,视为失败)", file=sys.stderr)
        return 1
    try:
        send_mail(brief, len(items))
    except Exception as e:  # noqa: BLE001
        print(f"[mail] FAILED: {e!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
