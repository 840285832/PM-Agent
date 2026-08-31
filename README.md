# 产品经理全流程 Agent 系统(PMAgents)

基于 **CrewAI** 的多 Agent 系统,覆盖产品经理从「需求收集」到「上线复盘」的完整工作流。
定位:**个人提效优先**,起草、结构化、计算、汇总类工作交给 Agent;决策与真实信息采集由人完成。

## 流程总览

| # | 步骤 | Agent | 人工节点 |
|---|------|-------|----------|
| 0 | 执行计划 | orchestrator(主控调度) | — |
| 1 | 需求收集 | requirement_collector(需求收集师) | 提供原始材料 |
| 2 | 需求分析 | requirement_analyst(需求解析师) | — |
| 3 | 优先级评估 | requirement_evaluator(需求评估师) | 确认排序(多人时) |
| 4 | 方案设计 | solution_designer(方案设计师) | 修正定稿 |
| 5 | 技术可行性 | technical_bridge(技术桥接师) | — |
| 6 | 原型设计 | prototype_designer(原型设计师) | 走查、精修 |
| 7 | 价值叙事 | value_narrator(价值叙事师) | — |
| — | **需求评审** | Agent 只准备评审包 | **评审会拍板(gate)** |
| 8 | 排期规划 | schedule_planner(排期规划师) | 提供团队资源假设、确认排期 |
| 9 | 复盘 | tracking_retrospective(追踪复盘师) | 录入过程数据、提供上线数据 |

完整蓝图见 [docs/PM_WORKFLOW_BLUEPRINT.md](docs/PM_WORKFLOW_BLUEPRINT.md)。

## 快速开始

```bash
# 1. 配置 .env(src/.env):OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL(默认 DeepSeek)
# 2. 安装依赖
.venv/Scripts/pip install -r src/requirements.txt

# 3. 初始化并跑全流程(原始需求写入 src/input.txt)
cd src
../.venv/Scripts/python.exe main.py init --input input.txt
../.venv/Scripts/python.exe main.py run

# 4. 评审通过后(排期步骤的前置 gate)
../.venv/Scripts/python.exe main.py gate --set review --decision passed --note "评审通过"
../.venv/Scripts/python.exe main.py resume
```

## 可视化操作面板(Web UI)

不想记 CLI?用浏览器操作:

```bash
cd src
../.venv/Scripts/python.exe main.py web          # 启动面板,自动打开浏览器 http://127.0.0.1:8321
../.venv/Scripts/python.exe main.py web --no-browser --port 9000   # 不自动开浏览器、换端口
```

面板功能(单文件前端 `src/webui/index.html`,零外部依赖,离线可用):

- **步骤状态**:左侧 10 步列表实时显示 完成/运行中/待跑/出错,标注 gate 状态与降级警告
- **跑下一步 / 跑完全部 / 停止**:逐步模式每跑完一步可先查看产物再决定是否继续;随时可停止(当前步骤结束后停)
- **产物查看**:每个产物文件即时渲染(Markdown 表格/代码块/Mermaid 源码);HTML 原型一键在新窗口打开
- **评审 Gate**:排期步骤前的人工评审,面板内直接填 通过/有条件通过/打回
- **过程数据**:研发状态/验收记录/上线数据/用户反馈四个文件在线填写保存,填完重跑复盘
- **需求补充**:主页面「输入预览」展示原始需求+全部补充(执行前可见),选择当前所属步骤追加,保存后自动重跑该步骤重新分析;多次追加叠加;CLI 保存后由 PM 手动强制重跑
- **单步重跑**:步骤行右侧 ↻ 按钮强制重跑某一步(改完配置/中间产物后重跑下游用)
- **新建需求**:面板内粘贴原始需求,自动生成新的 REQ 编号并切换过去

## CLI 命令参考

```bash
python main.py init --input input.txt                 # 初始化需求目录(自动编号 REQ-YYYYMMDD-NNN)
python main.py run [--input input.txt] [--from step]  # 跑流水线;--input 则先 init
python main.py step <name> [--force]                  # 只跑某一步(collect/analyze/evaluate/design/
                                                      #   feasibility/prototype/narrate/schedule/retrospect)
python main.py resume [--from step]                   # 断点续跑(跳过已完成步骤)
python main.py status                                 # 查看进度
python main.py gate [--set review --decision passed|conditional|rejected --note "..." ]  # 人工评审 gate
python main.py supplement [--text "..." | --file path | --view]   # 查看/追加需求补充信息(信息补充)
python main.py report                                 # 汇总所有产物生成 final_report.md
python main.py web [--port 8321] [--no-browser]       # 可视化操作面板(浏览器)
```

## 目录结构与产物规范

每个需求一个目录,默认输出到**项目外的桌面目录**(项目内不堆积产物):

```
C:\Users\hanyi\Desktop\产品经理—项目分析\test1\REQ-YYYYMMDD-NNN\
  input/raw_requirement.txt            # 原始输入拷贝
  input/supplement.md                  # 需求补充信息(多轮追加,带时间戳;orchestrator/collect/analyze 的输入)
  artifacts/                           # 步骤产物(序号=执行顺序)
    00_execution_plan.md               # 执行计划
    01_collected_items.md              # 需求池清单
    02_requirement_card.md/.json       # 需求卡片(人读/机器读)
    03_priority_report.md              # 优先级评估
    04_prd_draft.md                    # PRD 初稿
    05_feasibility_report.md           # 技术可行性(分模块人天)
    06_prototype_spec.md               # 原型规格文档
    07_prototype.html                  # 单文件可交互原型(浏览器打开即用)
    08_decision_brief.md               # 一页纸决策简报(评审材料)
    09_schedule_plan.md/.json/gantt.md # 排期三件套(表格/JSON/甘特图)
    10_retrospective_report.md         # 复盘报告
  gates/review_decision.md             # 人工评审结论(排期前置 gate)
  process_log/                         # 人工录入:dev_status/acceptance_result/post_launch_data/feedback
  reports/final_report.md              # 全量汇总报告
  state.json                           # 步骤状态机(断点续跑依据)
```

- **保留策略**:最多保留 3 个活动需求记录,新建第 4 个时最旧的自动移入 `test1\_archive\`(可恢复,不删除;想恢复就把文件夹移回 test1 下)
- **换输出位置**:设置环境变量 `PMA_OUTPUT_DIR`(如 `set PMA_OUTPUT_DIR=D:\pm_out`),默认值在 [artifacts.py](src/artifacts.py) 顶部 `OUTPUT_DIR`

## 配置说明

- `src/.env`:LLM 配置(OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL / LLM_TEMPERATURE)
- `src/my_project/config/agents.yaml`:10 个 Agent 的角色与职责
- `src/my_project/config/tasks.yaml`:10 个 Task 的指令与输出模板
- `src/my_project/config/team_config.yaml`:团队资源假设(人数/开始日期/迭代周期),排期步骤输入,人工维护

**改哪个文件、造成什么影响、改完要重跑什么**,见 [docs/OPERATION_GUIDE.md](docs/OPERATION_GUIDE.md)。

## 原型工具路径

Agent 直接产出**单文件 HTML 低保真原型**(零依赖、可交互、可演示)。
落地到国产设计工具(墨刀/即时设计/MasterGo)的操作流程见 [docs/prototype_tool_sop.md](docs/prototype_tool_sop.md)。

## 已知限制与路线图

- 评审为人工 gate,系统不代替拍板;研发状态、验收、上线数据需人工录入 process_log/
- DeepSeek 的 JSON/HTML 输出不稳定时,系统容错降级并提示人工修正,不静默丢弃
- 路线图:需求池跨需求复用 → 国产工具程序化导入(视 API 验证)→ 团队协作扩展

## 安全提示

`src/.env` 含明文 API 密钥。若建 git 仓库,请立即将 `.env` 加入 `.gitignore`,不要提交密钥。
