# ME × Protein 文献雷达

这是一个可直接上传 GitHub 的无人值守文献筛选与 QQ 邮件推送项目。它面向微生物代谢工程、酶工程和带湿实验验证的 AI for Protein 研究；每周一北京时间 10:00 运行，不要求电脑开机、保持 Codex 打开或持续登录 QQ 邮箱网页。

## 每期规则

- 正式精选 10–15 篇，其中至少 2 篇高质量综述；综述单列“科研视角”。
- 每期正式精选至少包含 2 篇配置清单内的 Top 期刊论文；Top 期刊同时获得排序加权。若配额不足，本期阻止发送并发出告警，不用预印本补位。
- 7–8 篇来自滚动六年窗口内且距出刊日超过 30 天的历史论文，其余优先选择近 30 天论文。
- 六年窗口定义为“本期日期向前滚动六个自然年，起止日期均包含”；例如 2026-08-17 对应 2020-08-17 至 2026-08-17。
- 预印本单列“前沿预警”，每期最多 2 篇且不占正式精选名额；正式精选与预印本都生成中文摘要并分别统计。
- 预印本同时按来源平台、Crossref `posted-content` 类型、典型 DOI 前缀和模型判定识别；任一信号确认后均不得进入正式精选。
- 植物和动物方向排除；微生物群落纳入；无细胞系统只在直接服务酶工程或通路验证时纳入。
- AI for Protein 原创论文必须有湿实验验证；纯 AI 改酶相对降权，代谢工程及 ME × Protein 协同研究优先。
- 全文只从合法开放来源读取；拿不到开放全文时降级到公开摘要，仅有元数据的候选不会推荐。

## 工作流程

1. Crossref、PubMed、Europe PMC、OpenAlex、arXiv 和 bioRxiv 独立检索。
2. 按 DOI 优先、题名与第一作者兜底去重，并做低成本主题预筛；正式论文与预印本在进入模型前分池，预印本最多占 4 个语义候选位。
3. 优先核验 Europe PMC 开放全文；不可得时严格按公开摘要核验。
4. DeepSeek `deepseek-v4-flash` 同时完成语义相关性判断、证据范围提取和中文速读摘要，结构化结果不合格会重试。
5. 按研究方向、证据等级、论文类型、时间池和期刊层级评分选稿，生成无 JavaScript 的静态 HTML 邮件。
6. QQ SMTP 发送成功后才写入历史；测试邮件有 `[TEST]` 前缀且不写历史。失败会发送告警邮件。

邮件页脚会显示模型前候选池中的正式论文、预印本和 Top 期刊数量，便于发现来源降级或候选结构异常。程序运行成功只代表流程完成；正式发送还必须通过正式/预印本隔离、综述、历史论文和 Top 期刊质量门槛。

GitHub 云端不会、也不能直接调用电脑里安装的 `nature-academic-search` Skill；因此本项目把公开数据库检索实现为独立模块。Codex 中的该 Skill 仍可用于临时复核、补检和人工深读，但不是每周自动推送的运行依赖。

## 上传 GitHub 前需要设置

在仓库 `Settings → Secrets and variables → Actions` 新建以下 Secrets：

| Secret | 必需 | 用途 |
|---|---:|---|
| `QQ_EMAIL` | 是 | QQ 发件地址，也用于 Crossref polite pool 联系信息 |
| `QQ_EMAIL_AUTH_CODE` | 是 | QQ 邮箱 SMTP 授权码，不是登录密码 |
| `RECIPIENT_EMAIL` | 是 | 收件地址，可以与发件地址相同 |
| `DEEPSEEK_API_KEY` | 是 | 语义筛选、证据提取和中文摘要 |
| `NCBI_API_KEY` | 否 | 提升 PubMed E-utilities 速率上限 |
| `OPENALEX_API_KEY` | 否 | 恢复 OpenAlex 来源；OpenAlex 自 2026-02-13 起要求免费 key |

任何真实密钥、授权码或令牌都不要写入源码、配置文件、工作流文件或 Git remote URL。`.env.example` 只有变量名。

QQ 邮箱网页不需要持续登录，但必须保持 SMTP 服务启用、授权码有效，且账户没有被安全策略临时限制。GitHub Actions 使用独立云端运行器；只有仓库被停用、Actions 被禁用、Secrets 失效或外部服务不可用时才会中断。

未提供 `OPENALEX_API_KEY` 时，程序会明确记录该来源降级，并继续使用 Crossref、PubMed、Europe PMC、arXiv 和 bioRxiv；不会因此中止整期。免费 key 可在 OpenAlex 账户设置页创建。

## 首次启用

1. 将本目录内容上传到一个私有 GitHub 仓库。
2. 添加上述 GitHub Secrets，并在仓库 Actions 设置中允许工作流具有读写权限。
3. 打开 `Actions → ME Protein Weekly Radar → Run workflow`，先选 `test`。
4. 确认收到带 `[TEST]` 前缀的邮件、排版和内容正常；测试不会写入 `data/history.json`。
5. 再手动选择 `production` 验证一次。此后计划任务会在每周一北京时间 10:00 自动运行。

GitHub 的计划任务可能因平台排队而延迟几分钟，并非本地时区错误。

## 费用保护

`config/radar.json` 中月度上限为 20 元。程序按 DeepSeek 返回的实际 token 数写入 `data/usage.json`，并使用缓存未命中的较保守价格估算；每次 API 尝试前先预留最坏费用，成功后以实际 token 结算，超时则保留预留额。若下一次尝试可能超过上限，任务就会终止、告警且不写推荐历史。修改模型或官方价格后，应同步修改配置中的模型名与单价。

## 本地验证

需要 Python 3.11 或更高版本：

```powershell
py -3.11 -m pip install -e .
py -3.11 -m unittest discover -s tests -v
```

完整本地试运行会真实检索并调用 DeepSeek，但不发送邮件、不写历史：

```powershell
$env:DEEPSEEK_API_KEY = "仅在当前终端临时设置"
$env:QQ_EMAIL = "联系邮箱"
py -3.11 -m me_protein_radar --mode test --dry-run
```

不要把终端里的真实值保存到脚本或提交到 Git。
