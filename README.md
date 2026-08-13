# ME × Protein 文献雷达

这是一个可直接上传 GitHub 的无人值守文献筛选与 QQ 邮件推送项目。它面向微生物代谢工程、酶工程和带湿实验验证的 AI for Protein 研究；每周一北京时间 10:00 运行，不要求电脑开机、保持 Codex 打开或持续登录 QQ 邮箱网页。

## 每期规则

- 正式精选 10–15 篇，其中至少 2 篇高质量综述；综述单列“科研视角”。
- 正式精选以独立期刊目录中的 Top 期刊为默认准入条件，每期至少 8 篇；Top 期刊同时获得排序加权。JAFC、Green Chemistry、Bioresource Technology、Chemical Engineering Journal 等应用期刊只有在题目或核验证据命中对应场景时才按 Top 处理。普通期刊最多 2 篇，且必须是原创研究、基础分至少 94，并由公开摘要或开放全文明确支持“首次完整新通路、新产物路线、领域领先性能、经湿实验验证的可迁移 AI 蛋白方法或通用酶工程平台”之一。配额不足时阻止发送并告警。
- 7–8 篇来自滚动六年窗口内且距出刊日超过 30 天的历史论文，其余优先选择近 30 天论文。
- 六年窗口定义为“本期日期向前滚动六个自然年，起止日期均包含”；例如 2026-08-17 对应 2020-08-17 至 2026-08-17。
- 完全关闭预印本检索与推送；arXiv、bioRxiv 不进入候选池，Crossref/PubMed 等来源识别出的预印本也会直接排除。
- 植物和动物方向排除；微生物群落纳入；无细胞系统只在直接服务酶工程或通路验证时纳入。
- AI for Protein 原创论文必须有湿实验验证；纯 AI 改酶相对降权，代谢工程及 ME × Protein 协同研究优先。
- 全文只从合法开放来源读取；拿不到开放全文时降级到公开摘要，仅有元数据的候选不会推荐。
- 合格候选充足时，同一本期刊优先不超过 2 篇、同一研究赛道优先不超过 4 篇；如果与综述、历史池或 Top 硬配额冲突，只弹性放宽多样性限制并在报告中记录。

## 工作流程

1. Crossref、PubMed、Europe PMC 和 OpenAlex 按来源并行检索正式发表论文；除主题检索外，PubMed/Europe PMC 使用分组期刊条件，Crossref 对重点化学与工程期刊逐刊定向召回。综述另走独立检索通道：PubMed 使用 `Review[Publication Type]`，Europe PMC 使用 `PUB_TYPE:review`，其余来源使用综述专用主题式，避免只靠正文中出现 review 一词。
2. 按 DOI 优先、题名与第一作者兜底去重，并做低成本主题预筛；预印本和明确的 nanozyme/临床动物等标题在模型调用前直接排除，模型候选池优先为 Top 期刊预留最多 40 个位置。
3. 优先核验 Europe PMC 开放全文；不可得时严格按公开摘要核验。公共数据库、开放全文和模型接口出现响应中断、连接重置或远端提前关闭时会自动退避重试；单篇开放全文失败仍降级为摘要，不阻断整期。
4. DeepSeek `deepseek-v4-flash` 先对候选池做轻量语义、湿实验、证据与创新性筛选；单篇筛选在重试后仍失败会被隔离并记录，只有连续 5 篇均不可用才判定模型服务异常。只有最终入选的 10–15 篇才再次调用模型生成中文题目、推荐理由和速读摘要。
5. 通过 `config/journals.json` 将期刊全名和简称归一化，区分直接 Top、条件 Top 与仅综述 Top；高水平综述目录覆盖 Nature Reviews、Trends、Chemical Society Reviews、Annual Review、Current Opinion 和 Biotechnology Advances 等相关刊物。再按研究方向、证据等级、论文类型、时间池和弹性多样性选稿。创新例外会在邮件中单独标注并展示证据依据。
6. QQ SMTP 发送成功后才写入历史；测试邮件有 `[TEST]` 前缀且不写历史。失败会发送告警邮件。

邮件页脚会显示模型前正式论文、Top 候选、期刊定向命中、语义筛选数量、最终摘要数量及多样性放宽次数，便于发现来源降级或候选结构异常。完整拒绝原因统计保存在每次运行的 `selection-日期.json`。程序运行成功只代表流程完成；正式发送还必须通过综述、历史论文、Top 期刊和创新例外质量门槛。

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

未提供 `OPENALEX_API_KEY` 时，程序会明确记录该来源降级，并继续使用 Crossref、PubMed 和 Europe PMC；不会因此中止整期。免费 key 可在 OpenAlex 账户设置页创建。

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

只检查公开数据库召回情况、不调用 DeepSeek、不发送邮件、不写历史：

```powershell
$env:PYTHONPATH = "src"
py -3.11 scripts/discovery_audit.py --issue-date 2026-08-17
```

不要把终端里的真实值保存到脚本或提交到 Git。
