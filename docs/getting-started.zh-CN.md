# 从 QFinZero 到 LiveOption：安装、数据与运行

[English](getting-started.md) · [运维说明](open-source-release.md)

整套系统由两个独立项目组成：QFinZero 提供行情、新闻事件和模拟券商；
LiveOption 通过 REST API 调用这些服务，运行期权 Agent 并展示结果。
使用 LiveOption 无需配置 MCP。

建议用 QFinZero 自带的合成数据完成首次测试，再导入真实行情。
合成数据不需要商业数据账号；运行模型仍需自己的模型 API key。

## 1. 安装和启动 QFinZero

准备 Git、Docker 24+ 和 Docker Compose：

```bash
git clone https://github.com/CityU-MLO/qfinzero.git
cd qfinzero
export QFZ_ADMIN_PASSWORD='替换为你自己的长密码'
docker compose up -d --build
curl --fail http://127.0.0.1:19777/health
```

首次构建需要编译 UPQ 和 Web，首次启动会生成合成数据，保存到 `qfz-data`
Docker volume。等 `upq`、`pmb`、`esp` 显示 `up` 后，打开
<http://127.0.0.1:19777>，使用 `admin` 和刚设置的密码登录。

在 **Account** 创建 API key，或在 QFinZero 仓库执行：

```bash
docker compose exec -T qfinzero qfz-auth create-key admin \
  --name liveoption --scopes market-data,events,trading
```

保存返回 JSON 中的 **`token`**。LiveOption 需要 `market-data`、`trading` 权限，
使用新闻或财报时还需要 `events`。key 必须允许模拟交易写入；viewer 用户和
只读 key 不能下单。也可以由管理员为 researcher 用户签发 key。

本指南依据 QFinZero 公开版 v1.0.2，源码版本为
[`2d8dda5`](https://github.com/CityU-MLO/qfinzero/tree/2d8dda5c418ba710b88b949e50ad2b2cb565219a)。
该版本的 `/svc/*` 需要鉴权。只有明确关闭鉴权的旧私有实例才可以不设置
`QFZ_API_KEY`。公共 `/health` 可访问，不代表数据和交易接口已授权。

上游参考：[安装](https://github.com/CityU-MLO/qfinzero/blob/main/docs/cn/getting-started.md)、
[账户与 API key](https://github.com/CityU-MLO/qfinzero/blob/main/docs/en/platform-guide.md#6-accounts-roles-and-api-keys)。

## 2. 准备数据

### 使用自带合成数据

首次测试推荐：

| 项目 | 设置 |
| --- | --- |
| 有期权的标的 | `QZSP`，也可用 `ACME`、`BOLT` |
| 单日分钟回放 | `2025-03-24` |
| 分钟行情与报价范围 | `2025-03-18` 至 `2025-03-31` 的交易日 |
| 日线范围 | `2025-01-02` 至 `2025-03-31` 的交易日 |
| 初始资金 | 100,000 美元 |

合成数据没有 SPY、QQQ。LiveOption 的 SPY/QQQ 预设和旧示例需要另行导入真实行情。
合成数据请使用“自定义任务”。它用于验证软件流程，不能作为真实市场表现。

详细清单见 [QFinZero sample data](https://github.com/CityU-MLO/qfinzero/blob/main/docs/cn/sample-data.md)。

### 商业行情与自有行情

在 QFinZero 的 **Data setup** 页面：

1. 设置原始数据和转换后存储目录。Docker 用户需要把原始文件目录挂载进容器；
   页面中的文件选择器读取服务器/容器路径。
2. 选择 **massive** 或 **Databento**。已有文件使用 **Local files → Inspect →
   Import and convert**；需要下载时配置自己的供应商 key，在 **Download**
   确认标的、日期和报价。
3. 等 **Jobs** 中的转换任务完成，在 **Data availability** 检查标的、日期及数据类型。
4. 实际查询 UPQ 的 K 线和期权链，确认返回非空，再启动 LiveOption。

日内期权任务需要股票分钟行情、期权合约/行情，以及 PMB 成交模型使用的期权报价。
上下文用到的历史日线也要准备。覆盖任务需要持仓标的及持有期间的期权到期日数据；
财报任务还需要事件日历和事件前后进出场时间窗口的数据。

自有文件需要符合 QFinZero 存储格式，或通过适配器转换。当前单独扫描目录、
建立 `qfz.dataset.yaml` 索引，不会把数据写入 UPQ 实际查询的转换后存储。
把任意 CSV 放进 LiveOption 目录也不会自动导入。

如果已有完整的 QFinZero 数据目录，在 QFinZero 仓库创建 `compose.data.yaml`：

```yaml
services:
  qfinzero:
    environment:
      QFZ_SAMPLE: "0"
    volumes:
      - /你的绝对路径/qfz-data:/data
```

其中应有 `upq/`，启用事件服务时还应有 `esp/`。应用配置：

```bash
docker compose -f docker-compose.yml -f compose.data.yaml up -d
```

后续 Compose 操作继续使用这两个配置文件。合成数据和真实数据使用独立目录。
通过页面导入原始文件时，可另加一个原始目录挂载，并在 Data setup 设置对应路径。

上游参考：[数据配置](https://github.com/CityU-MLO/qfinzero/blob/main/docs/cn/data-setup.md)、
[数据适配器](https://github.com/CityU-MLO/qfinzero/blob/main/docs/en/data-adapters.md)。

### 新闻和财报

LiveOption 通过 QFinZero ESP 获取新闻和事件；原生美国新闻管线使用 Benzinga，
合成数据包含虚构新闻和事件。确认 ESP 能查询到对应标的/日期后，再打开任务的 News。

真实 Benzinga 归档可在 QFinZero **Settings → News storage** 按服务器路径导入。
使用 MongoDB 后端时，按上游说明启动 Mongo profile，并配置 ESP 连接和后端。
Docker 需要事先挂载归档目录。

自有新闻需要写入 ESP 支持的后端，字段包括 UTC 发布时间和标的标签，再通过
`/svc/esp/esp/news/search` 验证。SQLite 后端使用 `news.sqlite3` 及 `news_tickers`
索引，完整结构见
[`qfinzero/esp_schema.py`](https://github.com/CityU-MLO/qfinzero/blob/main/qfinzero/esp_schema.py)。
通用 JSON/CSV 新闻需要相应导入程序，LiveOption 没有通用新闻上传接口。
财报事件使用独立日历存储。

参考：[ESP 新闻存储](https://github.com/CityU-MLO/qfinzero/blob/main/docs/en/esp/README.md#news-storage)、
[配置项](https://github.com/CityU-MLO/qfinzero/blob/main/docs/en/configuration.md)。

## 3. 安装 LiveOption 并连接

另开终端，准备 Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node 22.12+
（或 20.19+）、pnpm 11.25.0：

```bash
git clone https://github.com/liveoption/LiveOption.git
cd LiveOption
uv sync --frozen --extra dev --extra web-dev
cp .env.example .env
```

编辑 `.env`：

```dotenv
QFZ_HUB_URL=http://127.0.0.1:19777
QFZ_API_KEY=qfz_替换为第一步创建的token
```

这里的 `127.0.0.1` 指 LiveOption 后端所在的机器。QFinZero 在远端时，使用后端
能够访问的 hub 地址或 SSH 隧道。`QFZ_HUB_URL` 不要包含 `/svc/upq`，各服务路径
会自动添加。后端在另一容器中时，也要使用该容器能访问的地址。

```bash
uv run liveoption setup
uv run liveoption doctor --symbol QZSP --date 2025-03-24 --with-news
```

预期 hub、UPQ、PMB、ESP 可访问，股票行情和期权链非空。某段时间没有新闻会提示
WARN；缺少行情/期权链会失败。只有日线时可加 `--frequency 1d`。
检查不调用模型、不创建账户或订单，只检查指定日期，不能保证整个回测区间都完整。

三种 key 的用途不同：

| 凭据 | 配置位置 | 用途 |
| --- | --- | --- |
| `QFZ_API_KEY` | LiveOption `.env` / 后端环境 | 访问 QFinZero 数据和模拟券商 |
| `LIVEOPTION_ACCESS_TOKEN` | 自动生成的 `.liveoption/runtime.env` 或 `.env` | 登录 LiveOption Web/CLI |
| 模型供应商 key | LiveOption **API 设置** | 请求 LLM |

QFinZero 和模型 key 不要填到登录页、任务 Context、Prompt 或任务 YAML。
修改 `.env` 后重启本地 Web/API；已经运行的 worker 保留启动时的环境。

## 4. 启动 Web 并运行短任务

```bash
uv run liveoption web
```

首次会安装锁定版本的前端依赖并构建。打开 <http://127.0.0.1:3001>，使用
`.liveoption/runtime.env` 中的 `LIVEOPTION_ACCESS_TOKEN` 登录；如果 `.env`
显式设置了该值，以覆盖值为准。内部 API 默认仅监听本地 `19720`。

1. **API 设置**：添加供应商组、base URL、key 和正确的协议，刷新 `/models`。
2. **新建测试 → 自定义任务 → 日内**。
3. 标的填 `QZSP`，开始/结束均为 `2025-03-24`，资金 100,000，选择已配置的模型。
4. 首次连通测试可用 60 分钟决策间隔、0–7 DTE、市价单；确认手续费、风险规则、
   新闻开关及 System/User Prompt。
5. 启动任务。模型 API 可能收费；Agent 也可以选择 HOLD，任务完成不代表一定成交。

在 **运行记录** 查看状态和 **运行日志**，再看 K 线、Prompt、模型输出、决策和
实际成交。结果页面可导出报告；没有测量的数据保持不可用。

## 5. 用 CLI 提交同一类任务

保持 Web 运行，在 LiveOption 仓库另开终端：

```bash
uv run liveoption task models
```

复制 `examples/tasks/qfinzero-sample.yaml` 为 `my-task.yaml`，把其中 `model_id`
改成上面返回的模型注册 **id**，然后：

```bash
uv run liveoption task submit --spec my-task.yaml
uv run liveoption task status --run-id RUN_ID
uv run liveoption task logs --run-id RUN_ID
uv run liveoption task analysis --run-id RUN_ID
uv run liveoption task returns --run-id RUN_ID
uv run liveoption task report --run-id RUN_ID --format html --output report.html
```

`submit` 会立即运行并调用模型。Web 和 CLI 使用同一 `liveoption.run.v1`
协议、运行数据库和分析逻辑。报告不会覆盖同名文件。

## 6. 不调用模型，检查模拟成交

下面的测试显式读取 `.env`，默认使用合成数据。它创建新模拟账户和 session，
发现一份期权并提交一笔订单，要求实际模拟成交：

```bash
LIVEOPTION_TEST_QFINZERO=1 uv run --env-file .env pytest \
  tests/trading/test_qfinzero_integration.py -q
```

用自己的数据时，设置 `LIVEOPTION_TEST_SYMBOL`、`LIVEOPTION_TEST_DATE`，
选择股票分钟行情和流动期权数据完整的交易日。空期权链或未成交会使测试失败。
该测试不连接实盘券商。

## 常见问题

| 现象 | 检查 |
| --- | --- |
| hub 无法连接 | Compose 状态/日志、19777 端口、后端所在机器的地址；本地服务不要经过外网代理。 |
| `/health` 成功但服务失败 | key、过期时间、用户角色、scopes 和只读限制。 |
| K 线/期权链为空 | 标的、交易日、日期范围、期权到期日、转换进度；合成数据没有 SPY。 |
| 一直没有成交 | 拒单/订单历史、期权报价、保证金、限价及后续回放 K 线，运行成交测试。 |
| 新闻为空 | ESP 后端、导入状态、UTC 发布时间及任务的 News 开关。 |
| 前端构建失败 | Node/pnpm 版本；更新源码后执行 `uv run liveoption web --build`。 |
| 任务 failed | 查看“运行日志”或 `liveoption task logs --run-id …` 中的具体错误。 |

QFinZero 日志：`docker compose logs --tail=100 qfinzero`。
Ctrl-C 停止本地 LiveOption Web/API，已提交的 worker 可能继续运行；从同一目录重启
即可查看状态。长期运行和公网部署见[运维说明](open-source-release.md)。
