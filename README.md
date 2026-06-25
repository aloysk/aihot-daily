# aihot-daily

每天北京时间 05:30 自动拉取过去 24h AI 圈精选动态,GLM-5.2 归纳成中文简报,发到 Gmail。

## 数据流

```
GitHub Actions cron(每天 UTC 21:30 = 北京 05:30)
  → curl aihot.virxact.com/api/public/items?mode=selected&since=now-24h  (匿名,浏览器 UA)
  → GLM-5.2 (Coding Plan, OpenAI 协议, /api/coding/paas/v4) 归纳
  → Gmail SMTP 推送
```

## 必填 GitHub Secrets

| Secret | 说明 |
|---|---|
| `GLM_API_KEY` | GLM Coding Plan 的 API token |
| `GMAIL_APP_PASSWORD` | Gmail 应用专用密码(myaccount.google.com/apppasswords,需先开两步验证) |

收发邮箱默认 `xi.ke0709@gmail.com`,改邮箱改 `brief.py` 顶部 `DEFAULT_MAIL`。

## 本地调试

```bash
export GLM_API_KEY=...
export GMAIL_APP_PASSWORD=...
python brief.py
```

## 手动触发

仓库 Actions 页 → `AI HOT Daily` → `Run workflow`。
