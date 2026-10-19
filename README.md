# 通用资料爬虫

安装依赖后，编辑 `config/site_config.json`，然后在本目录执行 `python main.py` 即可一键启动。程序无参数运行时固定读取这个配置文件。每个入口网址的目标是采集满 10,000 条符合范围且成功处理的数据条目；失败、跳过、范围外和目录页都不占目标名额。

## 代码目录

```text
001/
├── main.py                       一键启动入口
├── README.md
├── requirements.txt
├── config/
│   ├── site_config.json          main.py 无参数运行时读取的配置
│   ├── site_template.json        可复制的空白模板
│   ├── candidates.json           原始数据清单转换后的候选配置
│   └── giews_countrybrief.json   GIEWS 示例配置
├── src/source_crawler/
│   ├── cli.py                     命令行参数
│   ├── config.py                  默认路径、限速等配置
│   ├── models.py                  输入和输出数据结构
│   ├── pipeline.py                完整抓取流程
│   ├── clients/http.py            HTTP、重定向和 robots.txt
│   ├── parsers/input.py           JSON、TXT、CSV、Excel 清单
│   ├── parsers/page.py            页面元数据及链接
│   ├── validators/urls.py         网址、域名、文件类型规则
│   ├── validators/scope.py        关键词、地区、年份和详情页匹配
│   └── storage/
│       ├── filesystem.py          下载文件
│       ├── catalogue.py           SQLite、Excel、JSONL 目录
│       └── logger.py              逐条采集日志
├── tests/
└── data/                          运行时生成的结果
    ├── downloads/                已下载文件，按来源编号分目录
    ├── logs/crawl.jsonl           每次处理的采集日志
    ├── 采集结果.xlsx              Excel 工作簿：采集记录、候选进度
    ├── records.jsonl
    └── crawler_state.sqlite3      增量检查状态
```

## 安装并运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py --dry-run
python main.py
```

在 IDE 中直接运行项目根目录的 `main.py` 也会读取 `config/site_config.json`。`--dry-run` 只查看入口和范围配置状态，不发起网络请求：

```bash
python main.py --dry-run
```

日常使用只需修改 `config/site_config.json` 中的 `job.source_url`、`job.target_region`、`job.first_batch_scope`、`run.output_dir`、`run.max_candidates` 和 `candidates`，保存后直接运行 `python main.py`。当 `candidates` 是空数组时，程序自动把 `job.source_url` 作为唯一入口，并继承目标地区、材料名称和采集范围；当 `candidates` 有内容时，程序处理其中的网址，单条未填写的元数据会继承 `job` 和 `candidate_defaults`。

使用原始候选清单 `config/candidates.json` 时，先在对应候选的 `crawl` 中填写规则，再把 `scope_ready` 改成 `true`。只运行一个网站，用候选 `id` 选择；同一来源有多个网址时，例如 `D04` 和 `D04-2`，它们可以配置不同的规则：

```bash
python main.py --candidate-id D02 --dry-run
python main.py --candidate-id D02
python main.py --candidate-id D04-2 --output "/path/to/results"
```

`scope_ready=false` 的候选会被跳过；如果所有候选都尚未确认范围，实际抓取会报错并提示先配置。也可以把某个候选的 `enabled` 改为 `false`，让无参数启动时跳过它。使用另一套配置文件时运行 `python main.py --input config/another.json`。

GIEWS 已使用独立的站点配置文件。链接、目标区域、材料名称、首批范围、最新条数、内容字段、输出目录、下载格式、限速和超时都在同一个文件中；运行时无需再重复填写参数：

```bash
python main.py --input config/giews_countrybrief.json
```

配置分为四部分：

- `job`：来源主页、目标区域、材料名称、首批范围、内容字段和最新条数。
- `run`：输出目录、Excel 输出格式、是否允许 FAO、页面上限、限速、超时、文件大小和 robots.txt 设置。`output_format` 当前固定为 `xlsx`。
- `candidate_defaults`：所有条目共用的发布机构、许可、优先级、文件类型和下载规则。
- `candidates`：本批次每个文件或数据详情页的编号、国家、发布日期和实际下载地址。

命令行参数仍然可以临时覆盖 `run` 中的对应设置。例如 `--output` 会覆盖 `run.output_dir`。切换网站时，可以复制该配置文件并修改这四部分。

## 按网址配置采集规则

编辑 `config/site_config.json` 的 `candidates`，或者编辑原始清单配置 `config/candidates.json` 中相应 `id` 的对象即可。下面是字段示例；原清单的候选都已按这个结构写入，可以逐个调整：

```json
{
  "id": "D02",
  "enabled": true,
  "source_id": "D02",
  "priority": "P0",
  "name": "CAP基础法规与修订",
  "country_or_region": "欧盟",
  "url": "https://eur-lex.europa.eu/eli/reg/2021/2115/2026-08-18/eng",
  "first_batch_scope": "现行条款、实施与授权文件",
  "license_and_boundaries": "核对原始法令及合并文本的日期和性质",
  "crawl": {
    "scope_ready": false,
    "max_items": 10000,
    "max_pages": 50000,
    "max_depth": 3,
    "min_link_score": 2,
    "include_terms": [],
    "exclude_terms": [],
    "region_terms": [],
    "year_start": null,
    "year_end": null,
    "dataset_detail_url_patterns": [],
    "allowed_domains": [],
    "file_types": [],
    "download": true
  }
}
```

`url` 是该入口的网址，每个候选填一个网址，`id` 必须唯一。`country_or_region`、`first_batch_scope` 和 `license_and_boundaries` 会随采集记录写入 `采集结果.xlsx` 的“采集记录”工作表，方便追溯。**首批范围与许可边界是说明文字，不会被程序自动解释成筛选或授权**；需要逐站填写 `crawl` 的范围字段，核对后将 `scope_ready` 改为 `true`：

如果入口本身就是 PDF、Word 等直接下载地址，可在候选中填写 `publisher`、`publication_date` 和 `usage_license`。这些人工核实的元数据会直接写入下载记录，增量复查返回“未变化”时也会同步更新。

- `max_items`：每个候选要成功采集的数据条目目标，当前为 `10000`。一个符合范围且下载成功的文件、一个符合范围的数据集详情页、或配置为仅记录链接的文件，各算一条；目录页、失败请求、无法下载、已跳过和范围外资料不计入，程序会继续处理后续链接。
- `crawl.max_pages`：为了寻找条目，每个候选最多处理的网页和文件地址数，当前为 `50000`。这是探索上限，不等于采集条目数。通用模板把 `run.max_pages` 和 `run.max_pages_per_site` 留为 `null`，启动时会按本次所选候选的探索额度自动计算总上限，避免多个网址共享一个过小的固定上限。
- `max_depth`：普通链接跟进的层数；识别出的“下一页”分页链接不额外消耗层数。`0` 表示只处理入口页。
- `min_link_score`：普通网页链接的最低相关性分数；文件链接优先。
- `include_terms` / `exclude_terms`：用标题、链接文字、网址及页面正文中的关键词限定数据条目，忽略大小写。列表内为“任一匹配”；排除词命中即不计入。空数组表示不限制。
- `region_terms`：必须出现至少一个地区关键词。`country_or_region` 只是清单元数据，不自动限制页面；例如针对巴基斯坦可填写 `["Pakistan", "巴基斯坦"]`。
- `year_start` / `year_end`：可选的年份区间；设置后，资料标题、网址或页面正文中必须出现区间内的年份。缺少年份证据的文件会被排除。
- `dataset_detail_url_patterns`：用于识别数据集详情页的网址片段，例如 `["/dataset/", "/datasets/"]`。空数组时，HTML 页面只作导航和记录，不计入 10,000 条；文件仍可计入。
- `allowed_domains`：在入口域名及其子域名以外，还允许访问哪些域名。空数组表示只允许入口域名及其子域名；跳转目标也受此约束。只填域名，不填 `https://` 或路径。
- `file_types`：允许下载的文件后缀，例如 `["pdf", "doc", "docx", "txt", "md"]`；空数组表示允许程序支持的全部文件类型（也包括 Excel、CSV 等）。
- `download`：设为 `false` 时仅记录文件链接，不下载文件。

上述规则是每个网址独立的。程序可访问不计数的目录页和分页页，以发现范围内条目；符合范围的文件才会下载。全局排除域名、robots.txt、请求频率和文件大小上限仍会生效。配置中的许可说明不能代替逐文件核验。站点若不足 10,000 条、需要登录、依赖 JavaScript 或未提供可发现的分页，程序会在 `采集结果.xlsx` 的“候选进度”工作表写明实际采集数和停止原因，不保证凑足目标。

如需抓取其他地址，可以传入 `--url`，或通过 `--input` 指向 JSON、TXT、CSV、XLSX、XLSM 文件。传入 `--url` 时不会自动追加默认清单；同时传入 `--url` 和 `--input` 时会合并并去重。

```bash
python main.py --url "https://example.gov/data" --output "/path/to/results"
python main.py --input "/path/to/清单.xlsx" --sheet "数据库清单" --header-row 6 --url-column "来源入口"
```

输出目录优先读取 JSON 中的 `run.output_dir`；未配置时使用 `src/source_crawler/config.py` 的 `DEFAULT_OUTPUT`，也可以通过 `--output` 临时覆盖。启动参数可用 `python main.py --help` 查看。`run.user_agent` 也可以按网站单独配置。

## 采集范围与记录

程序从入口页跟进链接，优先下载符合范围的 PDF、Word、Excel、CSV 等文件。下载记录包含候选编号、优先级、首批范围、许可边界、名称、发布机构、国家或地区、发布日期、覆盖年份、原始网址、下载日期、文件类型、使用许可、来源类别、政策有效性、版本、实施时期、访问条件、失败原因、数据条目类型、范围状态、文件路径和 SHA-256。遇到需要登录、授权、站点限制或无法直接下载的页面，会留下网址和原因。

政策有效性、修订版本、实施时期及许可依赖页面元数据和关键词提取，仍需人工核验。研究博客、观点文章会单独分类；“政策相关页面”只是候选资料。此通用版从清单入口出发，不调用搜索引擎，也不自动判定机构权威性。

脚本默认遵守 robots.txt、限制请求频率，并防止访问内网地址。默认跳过 FAO；需要纳入时使用 `--include-fao`。遇到 JavaScript 页面、验证码或需要登录的站点，脚本会记录访问限制，不会尝试绕过。

测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```
