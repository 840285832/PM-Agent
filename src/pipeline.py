"""流水线编排:步骤注册表 + 状态机。每步产物落盘,支持断点续跑与人工 gate 拦截。"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from artifacts import (
    extract_code_block,
    extract_json,
    load_state,
    mark_step_done,
    read_input,
    read_text_file,
    step_done,
    write_text,
)


class GateBlockedError(Exception):
    """前置 gate 未通过时抛出,中断流水线。"""


@dataclass
class OutputSpec:
    rel_path: str          # 相对需求目录的产物路径
    kind: str = "full"     # full | json_block | html_block | mermaid_block


@dataclass
class StepSpec:
    name: str              # 步骤名(对应 crew.TASK_FACTORIES 的 key)
    label: str             # 中文名,用于展示
    inputs: dict[str, str] # 占位符名 -> 输入文件相对路径(cfg: 前缀读全局配置)
    outputs: list[OutputSpec]
    gate: Optional[str] = None  # 前置人工 gate 名称


STEP_REGISTRY: list[StepSpec] = [
    StepSpec("orchestrator", "执行计划",
             {"raw_input": "input/raw_requirement.txt",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/00_execution_plan.md")]),
    StepSpec("collect", "需求收集",
             {"raw_input": "input/raw_requirement.txt",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/01_collected_items.md")]),
    StepSpec("analyze", "需求分析",
             {"input_data": "artifacts/01_collected_items.md",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/02_requirement_card.md"),
              OutputSpec("artifacts/02_requirement_card.json", "json_block")]),
    StepSpec("evaluate", "优先级评估",
             {"requirement_card": "artifacts/02_requirement_card.md",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/03_priority_report.md")]),
    StepSpec("design", "方案设计",
             {"requirement_card": "artifacts/02_requirement_card.md",
              "priority_report": "artifacts/03_priority_report.md",
              "execution_plan": "artifacts/00_execution_plan.md",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/04_prd_draft.md")]),
    StepSpec("feasibility", "技术可行性",
             {"prd_draft": "artifacts/04_prd_draft.md",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/05_feasibility_report.md")]),
    StepSpec("prototype", "原型设计",
             {"prd_draft": "artifacts/04_prd_draft.md",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/06_prototype_spec.md"),
              OutputSpec("artifacts/07_prototype.html", "html_block")]),
    StepSpec("narrate", "价值叙事",
             {"prd_draft": "artifacts/04_prd_draft.md",
              "priority_report": "artifacts/03_priority_report.md",
              "feasibility_report": "artifacts/05_feasibility_report.md",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/08_decision_brief.md")]),
    StepSpec("schedule", "排期规划",
             {"prd_draft": "artifacts/04_prd_draft.md",
              "feasibility_report": "artifacts/05_feasibility_report.md",
              "team_config": "cfg:team_config.yaml",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/09_schedule_plan.md"),
              OutputSpec("artifacts/09_schedule_plan.json", "json_block"),
              OutputSpec("artifacts/09_schedule_gantt.md", "mermaid_block")],
             gate="review"),
    StepSpec("retrospect", "复盘",
             {"schedule_plan": "artifacts/09_schedule_plan.md",
              "dev_status": "process_log/dev_status.md",
              "acceptance_result": "process_log/acceptance_result.md",
              "post_launch_data": "process_log/post_launch_data.md",
              "feedback_data": "process_log/feedback.md",
              "supplement": "input/supplement.md"},
             [OutputSpec("artifacts/10_retrospective_report.md")]),
]


def find_step(name: str) -> Optional[StepSpec]:
    for spec in STEP_REGISTRY:
        if spec.name == name:
            return spec
    return None


# ==================== gate ====================

def check_gate(req_dir: Path, gate_name: str) -> bool:
    """读取 gates/<name>_decision.md 的【结论】行,判断 gate 是否通过。"""
    text = read_text_file(req_dir / "gates" / f"{gate_name}_decision.md")
    match = re.search(r"【结论】\s*(.+)", text)
    if not match:
        return False
    decision = match.group(1).strip()
    if not decision or "待填写" in decision or "打回" in decision:
        return False
    return "通过" in decision


# ==================== 单步执行 ====================

def _write_output(req_dir: Path, out: OutputSpec, result: str, warnings: list[str]) -> None:
    """把 Agent 输出按 OutputSpec.kind 落盘为产物文件。"""
    target = req_dir / out.rel_path
    if out.kind == "full":
        write_text(target, result)
    elif out.kind == "json_block":
        data = extract_json(result)
        if data is not None:
            write_text(target, json.dumps(data, ensure_ascii=False, indent=2))
        else:
            write_text(target, "⚠️ JSON 解析失败,以下为 Agent 原始输出,请人工修正为合法 JSON:\n\n" + result)
            warnings.append(f"{out.rel_path} JSON 解析失败,已存原始输出,请人工修正")
    elif out.kind == "html_block":
        block = extract_code_block(result, "html")
        if block:
            write_text(target, block)
        else:
            alt = target.with_suffix(".md")
            write_text(alt, result)
            warnings.append(f"未提取到 HTML 代码块,原始输出已降级保存为 {alt.name},请人工处理(不阻塞后续流程)")
    elif out.kind == "mermaid_block":
        block = extract_code_block(result, "mermaid", tolerant=False)
        if block and "gantt" in block.lower():
            write_text(target, "```mermaid\n" + block + "\n```")
        else:
            warnings.append(f"{out.rel_path} 未提取到 gantt 甘特图,已跳过(不阻塞后续流程)")


def run_step(crew, req_dir: Path, spec: StepSpec, force: bool = False) -> None:
    """执行单步:读输入文件 → 拼 task → 单 Agent Crew kickoff → 产物落盘 → 更新 state。"""
    state = load_state(req_dir)
    if step_done(state, spec.name) and not force:
        print(f"⏭️  [{spec.name}] {spec.label}: 已完成,跳过(重跑加 --force)")
        return
    if spec.gate and not check_gate(req_dir, spec.gate):
        raise GateBlockedError(
            f"步骤 [{spec.name}] {spec.label} 需要人工 gate [{spec.gate}] 通过后才能执行。\n"
            f"  请填写 {req_dir / 'gates' / f'{spec.gate}_decision.md'} 或运行:\n"
            f'  python main.py gate --set {spec.gate} --decision passed --note "评审通过"'
        )

    inputs: dict[str, str] = {}
    for placeholder, rel_path in spec.inputs.items():
        content = read_input(req_dir, rel_path)
        inputs[placeholder] = content
        if not content.strip():
            print(f"⚠️  输入 {rel_path} 为空或不存在,占位符 {{{placeholder}}} 将以(未提供)填充")

    task = crew.create_task(spec.name, inputs)
    print(f"🚀 步骤 [{spec.name}] {spec.label} 执行中...")
    result = str(crew.kickoff_task(task))

    warnings: list[str] = []
    for out in spec.outputs:
        _write_output(req_dir, out, result, warnings)
    mark_step_done(req_dir, spec.name, [o.rel_path for o in spec.outputs], warnings)

    print(f"✅ 步骤 [{spec.name}] {spec.label} 完成")
    for w in warnings:
        print(f"⚠️  {w}")


# ==================== 流水线 ====================

def resume_pipeline(crew, req_dir: Path, from_step: Optional[str] = None) -> bool:
    """从 state.json 中第一个未完成的步骤开始执行到结束,gate 未通过则停止。"""
    start = 0
    if from_step:
        names = [s.name for s in STEP_REGISTRY]
        if from_step not in names:
            print(f"❌ 未知步骤: {from_step},可选: {', '.join(names)}")
            return False
        start = names.index(from_step)

    for spec in STEP_REGISTRY[start:]:
        try:
            run_step(crew, req_dir, spec)
        except GateBlockedError as e:
            print(f"🛑 {e}")
            print("人工完成 gate 后再次运行 resume 继续。")
            return False
    return True
