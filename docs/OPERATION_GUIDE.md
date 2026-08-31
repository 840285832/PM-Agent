# 操作指南:改哪里、影响什么
> 使用命令见 [README](../README.md),流程设计见 [PM_WORKFLOW_BLUEPRINT.md](PM_WORKFLOW_BLUEPRINT.md)。

## 一、文件影响总览

| 文件 | 作用 | 改错的影响等级 |
|------|------|----------------|
| `src/.env` | LLM 配置(模型/密钥/温度) | 🔴 高:改错所有步骤全部失败 |
| `src/input.txt` | 原始需求输入(init 时读入) | 🟡 中:只影响新初始化的需求 |
| `src/my_project/config/tasks.yaml` | 10 个步骤的指令与输出模板 | 🔴 高:直接影响对应步骤行为 |
| `src/my_project/config/agents.yaml` | 10 个 Agent 的角色/职责 | 🟡 中:影响 Agent 行为风格 |
| `src/my_project/config/team_config.yaml` | 团队资源假设(排期输入) | 🟡 中:只影响排期/复盘 |
| `src/pipeline.py` | 步骤编排:输入映射/产物/gate | 🔴 高:改错会断链或静默降级 |
| `src/crew.py` | Agent/Task 工厂与步骤分发 | 🔴 高:与 pipeline/tasks 必须一致 |
| `src/artifacts.py` | 目录初始化/状态/容错解析 | 🟡 中:影响产物解析与模板 |
| `src/my_project/tools/custom_tools.py` | 情感分析工具(复盘用) | 🟢 低:只影响复盘反馈分析 |
| `src/main.py` | CLI 与汇总报告 | 🟢 低:只影响命令与报告排版 |
| `REQ-xxx/input/supplement.md` | 需求补充信息(orchestrator/collect/analyze 的输入) | 🟡 中:只影响这三个步骤;已执行过需 --force 重跑才生效 |
| `test1/REQ-xxx/` | 运行时产物(见下,默认在桌面 `产品经理—项目分析\test1\`) | 🔴 高:删了不可恢复 |

## 二、各文件可改项详解

### 1. `src/.env` — 换模型 / 换服务商

| 改什么 | 影响 | 改完要做什么 |
|--------|------|--------------|
| `OPENAI_API_KEY` | 密钥错误 → 所有 LLM 调用报错 | 改完直接生效,重跑任意步骤验证 |
| `OPENAI_MODEL` | 换模型影响输出质量/速度/价格(如 deepseek-chat → deepseek-reasoner 更慢但更深思) | 同上 |
| `OPENAI_BASE_URL` | 换服务商(DeepSeek/通义/Kimi 等 OpenAI 兼容接口) | 同上 |
| `LLM_TEMPERATURE` | 0→更稳定刻板,1→更发散;排期/JSON 类步骤建议 ≤0.3 | 同上 |

### 2. `src/input.txt` — 换输入需求

- **只在 `init` 时被读取**,拷贝为 `test1/REQ-xxx/input/raw_requirement.txt`(输出目录默认在桌面,见 [artifacts.py](../src/artifacts.py) 顶部 `OUTPUT_DIR`)。
- 改了 input.txt 之后:**已初始化的需求不受影响**。要么重新 `init`(生成新编号,建议),要么直接改 `raw_requirement.txt` 后 `step collect --force` 并重跑下游。
- 只补几条衍生信息、不想重建需求时,推荐用**需求补充入口**:`main.py supplement --text "..."`(或 Web 面板「需求补充」页签),追加写入 `input/supplement.md`,再对 orchestrator/collect/analyze(及需要的下游)逐步 `--force`。见第 7.5 节。
- 不要只改 input.txt 就指望旧需求目录跟着变。

### 3. `tasks.yaml` — 调整某步骤的指令或输出格式

- `description`:该步骤 Agent 收到的指令。改这里 = 直接改变该步骤行为与输出内容。
- `expected_output`:输出格式模板,Agent 会照着写。
- **占位符 `{xxx}` 的规则**:名字必须与 [pipeline.py](../src/pipeline.py) 中该步骤 `inputs` 的键一致,否则会被替换成"(未提供)"而静默失效。
- **代码块格式红线**:` ```json ` / ` ```html ` / ` ```mermaid ` 围栏别改坏,产物提取依赖它们;提取失败会降级(JSON 失败会提示人工修正,HTML 失败降级为 .md,甘特图失败直接跳过)。
- 改完:`step <该步骤> --force` 重跑,并按依赖链 force 重跑下游(依赖顺序见 pipeline.py STEP_REGISTRY)。

### 4. `agents.yaml` — 调整 Agent 人设/职责

- `role`:只影响展示;`goal`/`backstory`:影响 Agent 关注点、语气、输出风格。
- **新增 Agent 条目 ≠ 生效**:必须同时改 4 处才能接入流程 —— ①这里加条目;② `tasks.yaml` 加对应 task;③ [crew.py](../src/crew.py) 加工厂方法并登记到 `TASK_FACTORIES`;④ [pipeline.py](../src/pipeline.py) 在 `STEP_REGISTRY` 加步骤。漏一处 = 静默不生效(不会报错)。
- 改完:重跑对应步骤(该步骤引用此 Agent 的)。

### 5. `team_config.yaml` — 调整排期参数

| 改什么 | 影响 |
|--------|------|
| `developers` / `qa` / `designers` | 排期表任务并行度、总工期、甘特图 |
| `start_date` | 所有任务起止日期整体平移 |
| `sprint_days` / `working_days_per_week` | 工期计算与里程碑日期 |
| `parallel_tasks_per_dev` | 同一开发可并行的任务数 |

改完:`step schedule --force` 重跑排期;复盘报告引用了排期数据,建议再 `step retrospect --force`。

### 6. `pipeline.py` — 编排调整(慎改)

- `STEP_REGISTRY` 中某步骤的 `inputs`(占位符 → 文件路径):改路径会让该步骤读到空输入;`cfg:` 前缀表示读 `src/my_project/config/` 下全局配置。
- `outputs` 的 `kind`(`full`/`json_block`/`html_block`/`mermaid_block`):决定产物怎么从 Agent 输出中提取落盘。
- `gate` 字段:给步骤加 `gate="review"` = 该步骤前强制人工评审;去掉 = 放行。
- **调整步骤顺序**:下游依赖上游产物文件,顺序乱了会导致读不到输入。新增/删除步骤需同步 crew.py 的 `TASK_FACTORIES`。
- 改完:受影响步骤 `--force` 重跑。

### 7. `test1/REQ-xxx/` — 运行时产物(最常操作)

每个需求一个子目录,位于输出根目录(默认桌面 `产品经理—项目分析\test1\`,可用环境变量 `PMA_OUTPUT_DIR` 改位置)。**最多保留 3 个活动需求,新建第 4 个时最旧的自动移入 `test1\_archive\`**(可恢复;想恢复把文件夹移回 test1 即可)。

| 改什么 | 影响 | 说明 |
|--------|------|------|
| `input/supplement.md` | **所有步骤**的输入 | 追加需求补充信息(CLI `supplement --text`/`--file` 或面板主页面「输入预览」);**面板追加后自动重跑所选步骤**,在原需求+全部补充基础上重新分析;CLI 追加后需对受影响步骤 `--force` 才生效;多轮追加叠加不覆盖历史 |
| `artifacts/*.md` / `*.json` | 该文件被下游步骤读取 | **人工修正中间产物后,已完成的下游不会自动重跑**,需对下游逐步 `--force`(或删下游 state 条目) |
| `gates/review_decision.md` | 控制排期 gate 放行/拦截 | 改【结论】行;或用 `main.py gate --set`。结论取值:通过/有条件通过/打回 |
| `process_log/*.md` | 复盘步骤的输入(预期vs实际对比) | 填写真实数据后 `step retrospect --force` 重新复盘;空数据会标注「未采集」 |
| `state.json` | 步骤完成状态 | **别手改**,易导致重复跑或跳过;想重跑某步用 `--force`,想从头再来重新 `init` |
| 整个 `test1\` | 全部需求记录 | 🔴 删除即丢失,无 git 时不可恢复 |

## 三、常见操作速查(按目标反查)

| 我想做什么 | 改哪里 | 改完做什么 |
|-----------|--------|-----------|
| 换模型/服务商/温度 | `src/.env` | 直接重跑任意步骤 |
| 换一个新需求 | 写 `src/input.txt` | `main.py init --input input.txt` 生成新需求目录 |
| 需求创建后产生衍生问题,需补充信息 | 不动任何配置,用补充入口 | `main.py supplement --text "..."`(或面板「需求补充」页签)→ 步骤未执行则直接续跑;已执行则 `step collect --force` → `step analyze --force` → 下游按需 --force |
| 提升某步输出质量 | `tasks.yaml` 对应 task 的 description | `step <步骤> --force`,再 force 下游 |
| 改输出格式(如排期表加列) | `tasks.yaml` 对应 expected_output | 同上 |
| 改 Agent 人设 | `agents.yaml` 对应 agent | 重跑引用它的步骤 |
| 调整排期(人数/日期/周期) | `team_config.yaml` | `step schedule --force` →(可选)`step retrospect --force` |
| 修正一个中间产物(如手改需求卡片/排期 JSON) | 直接编辑 `artifacts/` 对应文件 | 对依赖它的下游步骤逐一 `--force` |
| 评审打回后重新评审 | `main.py gate --set review --decision ...` | `resume` 继续 |
| 临时跳过评审 | `gate --set review --decision passed` | `resume` |
| 填完过程数据重新复盘 | `process_log/` 四个文件 | `step retrospect --force` |
| 新增一个分析步骤(如竞品分析) | 4 处联动:agents.yaml、tasks.yaml、crew.py、pipeline.py | 全部改完后 `step <新步骤> --force` |
| 汇总最新报告 | 不改文件 | `main.py report` |

## 四、改完后的验证方式

1. `main.py status --requirement <id>` — 看步骤状态、警告、gate
2. `main.py report --requirement <id>` — 重新汇总 `reports/final_report.md`
3. 检查对应产物文件内容是否符合预期(重点看 `artifacts/` 和步骤输出里的 ⚠️ 降级提示)
4. 涉及 YAML 的改动先做语法检查:
   ```
   .venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('src/my_project/config/tasks.yaml', encoding='utf-8'))"
   ```
   (tasks.yaml / agents.yaml / team_config.yaml 同理)

## 五、危险操作清单

| 操作 | 后果 | 正确做法 |
|------|------|----------|
| 改坏 tasks.yaml 的 ``` 代码块围栏 | 产物提取降级,JSON/HTML/甘特图不落盘 | 改完先跑第 4 节语法检查,跑一次步骤看有无 ⚠️ |
| 改坏 gate 模板的【结论】行格式 | gate 永远拦截或永远放行 | 保持 `【结论】通过` 一行格式 |
| 只改 agents.yaml 新增 Agent,不改其余 3 处 | 静默不生效,排查困难 | 按第 2.4 节四处联动 |
| 手改 state.json | resume 可能跳步或重跑 | 用 `--force` / 重新 `init` |
| 补充需求信息后直接 `resume` | 已完成步骤被跳过,补充不生效 | 按 `status` 的 ⚠️ 提示用 `--force` 重跑 collect/analyze 及下游(或面板 ↻) |
| 改 tasks.yaml 占位符但 pipeline.py 不同步 | 该输入静默变"(未提供)",步骤照样跑 | 占位符与 STEP_REGISTRY inputs 键名一一对应 |
| 删除 test1(输出目录) | 需求记录全部丢失 | 需要保留时先备份;系统自动归档到 `_archive\`,不主动删除 |
| 升级 crewai 大版本 | 现有 Agent/Task 用法可能失效 | 先锁定版本;本项目按 crewai 1.x 验证 |
