# 国债卖方策略决策驾驶舱

白底、原生 HTML，无构建步骤和 CDN 依赖。前端按页面打开时配置的数据地址读取 JSON。后端在 GitHub Actions 上完成抓取、备份、提取和提交。现有 NXNY 私有仓库可继续保持原状态。

## 1. 打开页面与接入公开数据

初始 dashboard_data.json 含 6 家模拟券商、12 份示例研报、60 条历史期限记录。演示周为 2026-11-16 至 2026-11-20。报告、引文、基准、点位均为模拟。

在 GitHub 网页新建一个公开仓库，默认分支用 main。如果从 ZIP 手动建立另一个仓库，请将项目文件放在仓库根目录，包括隐藏目录 .github 和 .gitignore。若网页上传忽略点号目录，可用 GitHub 的 Add file → Create new file，文件名填 .github/workflows/update_dashboard.yml，再粘贴包内同名文件全文。生产输入 PDF 留在运行器和 Drive，工作流只 git add dashboard_data.json。公开 JSON 包含券商署名、标题、历史观点及引文。

打开仓库里的 dashboard_data.json，点击 Raw。公开仓库示例地址如下，替换 OWNER 和 REPO。

```text
https://raw.githubusercontent.com/OWNER/REPO/main/dashboard_data.json
```

打开 index.html，顶部 DATA_URL 默认是 ./dashboard_data.json。独立托管页面时，将其改为上述 Raw 地址并发布一次。以后 Actions 更新 JSON，用户刷新原网址即可获取数据。也可直接在本版网页地址后加 ?data=https://raw.githubusercontent.com/OWNER/REPO/main/dashboard_data.json 并收藏，这种方式无需修改已发布页面。查询参数只接受 GitHub Raw 的 HTTPS 地址，顶部常量还支持其他允许跨域读取的 HTTPS 来源。请勿在地址中附加令牌。网络、CORS、超时、HTTP 错误或格式异常时，会切换内置样本并显示原因和“重试”。页面顶部显示数据文件给出的更新时间。GitHub Raw 和网络中间缓存可能有传播延迟。

## 2. 准备云端凭据

到新仓库 Settings → Secrets and variables → Actions。Secrets 标签存敏感值，Variables 标签存普通配置。已有仓库的仓库级 Secrets 不会随文件复制。

| 类型 | 名称 | 填写内容 |
| --- | --- | --- |
| Secret | OPENAI_API_KEY | OpenAI Platform 项目 API 密钥。项目需有可用 API 额度。可在项目中管理用量及预算提醒。 |
| Secret | TINYFISH_API_KEY | 可访问既有 NXNY 云浏览器配置的 TinyFish key。 |
| Secret | TINYFISH_PROFILE_ID | 已在 NXNY 登录且有相应 PDF 下载资格的 TinyFish 配置 ID。 |
| Secret | GDRIVE_OAUTH_JSON | Google 用户 OAuth 授权 JSON，结构见下。 |
| Variable | GDRIVE_FOLDER_ID | 该 Google 用户可写的专用备份目录 ID。取 Drive 文件夹网址 /folders/ 后的 ID。 |
| Variable | OPENAI_MODEL | 可选，默认 gpt-4.1-mini，也可填写 gpt-4.1。 |
| Variable | ENABLE_REPORT_PIPELINE | 默认为停用。确认可公开发布提取后的 JSON 后，才填 true。 |
| Variable | MAX_NEW_PDFS | 可选，每次新下载尝试数 1–20，默认 1。首次验收可填 1。恢复备份不占 NXNY 新下载次数。 |
| Variable | SCHEDULER | 手动验收时留空。正式定时填 github 或 cloudflare，二选一。 |
| Variable | DEPLOY_GITHUB_PAGES | 仅使用 GitHub Pages 时填 true。 |

**Google Drive。** 在 Google Cloud Console 启用 Google Drive API。可以复用具有目标目录读写权且由你控制的现有用户 OAuth 授权，取同一套 client_id、client_secret、refresh_token。写入 Secret 的格式如下。access_token 会由运行器自动刷新。

```json
{"client_id":"YOUR_CLIENT_ID","client_secret":"YOUR_CLIENT_SECRET","refresh_token":"YOUR_REFRESH_TOKEN"}
```

如需在浏览器中首次授权，可在 Google Auth Platform 配置受众和 OAuth 客户端，选择 Web application，在 Authorized redirect URIs 登记 https://developers.google.com/oauthplayground 。打开 Google OAuth 2.0 Playground 设置，启用 Use your own OAuth credentials，使用自己的客户端并选择 Offline。授权能读写目标目录的 Drive scope，交换授权码后，把 refresh_token 与该客户端的一对凭据写入上面的 Secret。drive.file 仅能使用该客户端已获准访问的文件或目录；若使用既有的完整 Drive 授权，其 scope 为 https://www.googleapis.com/auth/drive 。外部测试状态签发的刷新令牌可能很快失效，长期运行前检查授权应用的发布状态。

**NXNY。** 采集器从债券列表首页顺序翻页，按北京时间保留标题日期为当天至往前第 7 天、符合覆盖券商和周报规则的候选。优先恢复尚未入库的 Drive 文件，并通过站内研报编号跳过归档和已发布记录。找到足够的未下载候选、超过上传日期窗口或到达列表末页时停止扫描。超过 30 页还未完成、目录异常或登录过期会产生失败信号。之后使用已登录云浏览器配置在详情页下载，按 PDF SHA-256 防止重复入库。指定目录宜为本项目专用；旧系统没有本项目摘要标记的历史 PDF 默认不会自动识别。并行运行其它采集器时，各自的下载预算独立。

**GitHub 写权限。** Settings → Actions → General 允许工作流使用 contents: write，并确认默认分支规则允许数据提交。内置 GITHUB_TOKEN 由 Actions 自动签发。分支保护禁止机器人推送时，需要用组织允许的发布分支或相应规则。无需把个人 GitHub token 放入前端。

## 3. 手动验收与定时

1. 确认本次提取结果可以公开发布，再填好上述四个 Secrets 和新建空备份目录的 GDRIVE_FOLDER_ID，设置 ENABLE_REPORT_PIPELINE=true、MAX_NEW_PDFS=1。到 Actions → Update bond cockpit → Run workflow，选择默认分支，trigger_source 选 manual。
2. 在 Actions 看 Fetch、Back up、Extract、Commit 四步。在 Drive 核对 PDF 的 ID 前缀和原始标题。成功提取后，dashboard_data.json 应出现真实报告哈希，demo_mode 变为 false。刷新网页，选择期限 → 券商 → 查看原文证据，核对页码和引文。抽屉仅引用文字和页码，原始 PDF 在 Drive 备份目录。
3. 新报告全部重复或列表没有新报告时，文件保持原样。单篇模型错误继续处理本批其它 PDF；部分成功先提交，最终将该批标红。全失败保留旧文件，已备份的 PDF 可在下次恢复。不要把“已派单”当作“已产出数据”。
4. 本仓库的 GitHub Actions 排程为北京时间每日 08:17、12:17、17:17，对应 UTC 00:17、04:17、09:17。如选 GitHub 调度，SCHEDULER 填 github，实际开始时间由 GitHub 安排。如选 Cloudflare，SCHEDULER 填 cloudflare，再按下一节配置 Worker。手动触发可用于独立验收。

## 4. Cloudflare Workers 后台

在 GitHub Settings → Developer settings → Fine-grained personal access tokens 创建仅限新仓库的 token，Repository permissions → Actions 选 Read and write。Worker 只调用 workflow_dispatch。仓库组织若需批准，先完成批准。

在 Cloudflare Dashboard → Workers & Pages 创建一个新的 ES module Worker，如 rates-cockpit-dispatch。将 cloudflare/worker.mjs 全文粘贴至代码编辑器并部署。在 Worker Settings → Variables and Secrets 配置下表。

| 类型 | 名称 | 值 |
| --- | --- | --- |
| Secret | GITHUB_TOKEN | 上述目标仓库的细粒度 token |
| Variable | GITHUB_OWNER | 仓库所有者 |
| Variable | GITHUB_REPO | 新公开仓库名称 |
| Variable | GITHUB_REF | 默认分支，如 main |
| Variable | SCHEDULER_ENABLED | 验收后填 true；暂停时填 false |

在 Triggers / Cron Triggers 中添加 `17 0 * * *`。若用 Wrangler，则修改包内 cloudflare/wrangler.toml 中的变量，在该目录运行 `npx wrangler secret put GITHUB_TOKEN`，再运行 `npx wrangler deploy`。Worker 仅提供定时处理，普通 HTTP 访问返回 404。Worker 日志的 dispatch_accepted 和请求 ID 表示 GitHub 已接受调用，完成状态到 Actions 查看。Worker 和 GitHub 都触发时，工作流根据 SCHEDULER 只执行选中的定时来源。

## 5. 数据口径与故障恢复

- records 保存历史。每家每期限使用最新发布日期的记录。若同日有多份且无日内顺序，则每份均对比前一个发布日期，以稳定报告 ID 排序确定当天展示记录。迟到的历史报告会触发后续差分重算。
- 方向按 偏空 → 谨慎 → 中性 → 偏多 排序。首次明确表态或上期未表态记 new，本期未表态记 unstated。两者均不触发“改口”。雷达只显示当周 upgrade、downgrade，真实周按北京时间，模拟周按样本日期。
- 多空分母只统计明确表态机构。“慎空”合计谨慎与偏空，抽屉中分别显示。高信噪比模式过滤“常年偏多、本期偏多、维持不变”，通过 broker_styles 为标准券商名配置标签；broker_aliases 可在首轮提取前统一署名。两个开关共同作用于最新记录，不回填旧报告。
- `benchmark_yields` 采用财政部专栏的中债国债日终到期收益率曲线（百分数），取同一交易日的 2 年、7 年、10 年和 30 年，每天北京时间 21:37 检查更新。`benchmark_as_of` 单独记录交易日；休市、未更新或读取失败时保留上次的完整数据。可用 `trigger_source=benchmark` 手动刷新。目标区间色带跨机构取上下沿包络，`null` 和 `[]` 均跳过。
- 优先保存报告原文；结构化输出经 Pydantic 验证。模型附的引文和 PDF 物理页码用于人工复核，未做逐字 OCR 验真。解析路径对上传成功的临时文件始终请求删除，删除请求失败会记录文件 ID 供追查。
- 第一次提取成功后，真实记录、雷达和分母与模拟样本隔离。GitHub 向主分支推送的只有更新后的 JSON。若远端 JSON 在提取期间已改动，则停止提交，重新从最新主分支运行。Drive 备份早于模型调用，推送失败后可复用 PDF，但未提交成功的模型结果可能需要再次提取。

## 6. 其它静态托管与可选本地预览

- Cloudflare Pages：连接新仓库的默认分支，框架选 None。构建命令用 `mkdir -p public && cp index.html dashboard_data.json public/`，输出目录为 public。
- GitHub Pages：仓库 Settings → Pages → Source 选择 GitHub Actions，设 DEPLOY_GITHUB_PAGES=true。一次默认分支上的前端提交亦可仅部署静态页。接入真实数据前完成上述 Pages 来源切换，演示阶段可使用 main 根目录的分支发布。工作流包含显式 Pages 发布步骤，可处理机器人提交本身不触发下一次 push 工作流的情况。
- 现有静态站或 chatgpt.site：发布 index.html 和初始 dashboard_data.json。如果仅发布 HTML，使用远程 DATA_URL 或内置演示。读取公开 Raw 后，数据更新无需重新部署页面。
- 如需本地自查，在解压后的项目根目录执行下列命令，然后打开 http://127.0.0.1:8000/ 。查看 10Y 期限，6 家中 3 家已表态；单开高信噪比剩 2 家已表态，再开“仅看本周改口”剩 1 家。停止时按 Ctrl+C。直接双击文件可能触发浏览器的本地文件限制，此时只会看到内置演示。

```bash
python -m http.server 8000 --bind 127.0.0.1
```

## 7. 文件清单

| 文件 | 用途 |
| --- | --- |
| index.html | 完整白底前端、DATA_URL、内置示例 |
| dashboard_data.json | 初始历史、基准、雷达和样本标记 |
| reports_inbox/.gitkeep | PDF 临时收件箱 |
| scripts/extract_bond_reports.py | PDF 原生输入、结构化提取、历史合并与原子写回 |
| scripts/report_io.py | NXNY 发现与下载、Drive 备份与恢复 |
| requirements.txt | 提取、云浏览器与 Drive 客户端依赖 |
| .github/workflows/update_dashboard.yml | 采集、备份、提取、推送、可选 Pages 发布 |
| cloudflare/worker.mjs、cloudflare/wrangler.toml | 定时派单与部署参数 |
| .gitignore | 排除 PDF、密钥文件和运行缓存 |

官方参考：[OpenAI PDF 输入](https://developers.openai.com/api/docs/guides/file-inputs)、[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)、[GitHub 工作流派单](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)、[Cloudflare Cron](https://developers.cloudflare.com/workers/configuration/cron-triggers/)、[Drive 上传](https://developers.google.com/workspace/drive/api/guides/manage-uploads)、[Google OAuth 离线授权](https://developers.google.com/identity/protocols/oauth2/web-server#offline)。
