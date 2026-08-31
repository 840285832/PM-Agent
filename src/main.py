# main.py
"""CLI 入口: init / run / step / resume / status / gate / report"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows 管道/重定向下强制 UTF-8 输出,避免中文乱码
if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from artifacts import (
    SUPPLEMENT_TEMPLATE,
    append_supplement,
    count_supplement_entries,
    init_new_requirement,
    latest_requirement_dir,
    list_requirement_ids,
    load_state,
    read_supplement,
    read_text_file,
    requirement_dir,
    step_done,
    supplement_stale,
    write_text,
)
from crew import ProductManagerCrew
from pipeline import STEP_REGISTRY, check_gate, find_step, resume_pipeline, run_step

DEFAULT_INPUT = Path(__file__).resolve().parent / "input.txt"

GATE_DECISIONS = {"passed": "通过", "conditional": "有条件通过", "rejected": "打回"}

REPORT_SECTIONS = [
    ("artifacts/00_execution_plan.md", "执行计划"),
    ("artifacts/01_collected_items.md", "需求池清单"),
    ("artifacts/02_requirement_card.md", "需求卡片"),
    ("artifacts/03_priority_report.md", "优先级评估报告"),
    ("artifacts/04_prd_draft.md", "PRD初稿"),
    ("artifacts/05_feasibility_report.md", "技术可行性报告"),
    ("artifacts/06_prototype_spec.md", "原型规格文档"),
    ("artifacts/08_decision_brief.md", "决策简报"),
    ("artifacts/09_schedule_plan.md", "排期表"),
    ("artifacts/10_retrospective_report.md", "复盘报告"),
    ("gates/review_decision.md", "评审结论"),
    ("process_log/dev_status.md", "研发过程记录"),
    ("process_log/acceptance_result.md", "验收记录"),
    ("process_log/post_launch_data.md", "上线数据"),
    ("process_log/feedback.md", "用户反馈"),
]


def resolve_req_dir(requirement_id: str | None) -> Path:
    """按指定 id 或最近需求目录解析,失败退出。"""
    if requirement_id:
        req_dir = requirement_dir(requirement_id)
        if not req_dir.exists():
            print(f"❌ 需求目录不存在: {req_dir}")
            print(f"  现有需求: {', '.join(list_requirement_ids()) or '(无)'}")
            sys.exit(1)
        return req_dir
    req_dir = latest_requirement_dir()
    if req_dir is None:
        print("❌ 还没有需求目录,请先运行: python main.py init --input input.txt")
        sys.exit(1)
    return req_dir


def read_raw_input(input_arg: str | None) -> str:
    path = Path(input_arg) if input_arg else DEFAULT_INPUT
    if not path.exists():
        print(f"❌ 输入文件不存在: {path}")
        sys.exit(1)
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        print(f"❌ 输入文件为空: {path}")
        sys.exit(1)
    return content


# ==================== 子命令 ====================

def cmd_init(args, crew):
    raw = read_raw_input(args.input)
    try:
        req_id, req_dir = init_new_requirement(raw, args.id)
    except Exception as e:
        print(f"❌ 初始化需求目录失败: {e}")
        sys.exit(1)
    print(f"✅ 已初始化需求目录: {req_dir}")
    print(f"   下一步: python main.py run --requirement {req_id}")


def cmd_run(args, crew):
    if args.input:
        raw = read_raw_input(args.input)
        try:
            req_id, req_dir = init_new_requirement(raw, args.id)
        except Exception as e:
            print(f"❌ 初始化需求目录失败: {e}")
            sys.exit(1)
        print(f"✅ 已初始化需求目录: {req_dir}")
    else:
        req_dir = resolve_req_dir(args.requirement)
    ok = resume_pipeline(crew, req_dir, from_step=args.from_step)
    if ok:
        cmd_report(args, crew, req_dir=req_dir)
        print(f"\n📦 全部产物位于: {req_dir}")
    else:
        print(f"\n⏸️ 流水线暂停,产物位于: {req_dir}")


def cmd_step(args, crew):
    req_dir = resolve_req_dir(args.requirement)
    spec = find_step(args.step)
    if spec is None:
        print(f"❌ 未知步骤: {args.step},可选: {', '.join(s.name for s in STEP_REGISTRY)}")
        sys.exit(1)
    try:
        run_step(crew, req_dir, spec, force=args.force)
    except Exception as e:
        print(f"🛑 {e}")
        sys.exit(1)


def cmd_resume(args, crew):
    req_dir = resolve_req_dir(args.requirement)
    ok = resume_pipeline(crew, req_dir, from_step=args.from_step)
    if ok:
        cmd_report(args, crew, req_dir=req_dir)
    else:
        print(f"\n⏸️ 流水线暂停,产物位于: {req_dir}")


def cmd_status(args, crew):
    req_dir = resolve_req_dir(args.requirement)
    state = load_state(req_dir)
    print(f"📋 需求: {state.get('requirement_id', req_dir.name)}")
    print(f"   创建时间: {state.get('created_at', '?')}")
    print("-" * 60)
    for spec in STEP_REGISTRY:
        done = step_done(state, spec.name)
        info = state.get("steps", {}).get(spec.name, {})
        mark = "✅" if done else "⏳"
        artifact = info.get("artifact", "")
        line = f"{mark} [{spec.name:<12}] {spec.label:<6} {artifact}"
        if spec.gate:
            gate_ok = check_gate(req_dir, spec.gate)
            line += f"   (gate:{spec.gate} {'✅' if gate_ok else '⏳'})"
        print(line)
        for w in info.get("warnings", []):
            print(f"      ⚠️  {w}")
    sup_content = read_supplement(req_dir)
    print(f"📥 需求补充: {count_supplement_entries(sup_content)} 条记录  ({req_dir / 'input' / 'supplement.md'})")
    if supplement_stale(req_dir, state):
        print("⚠️  补充信息晚于 analyze 完成时间,建议强制重跑: step collect --force → step analyze --force → 下游按需 --force")
    print("-" * 60)


def cmd_gate(args, crew):
    if not args.set:
        req_dir = resolve_req_dir(args.requirement)
        print(read_text_file(req_dir / "gates" / "review_decision.md") or "(gate 文件不存在)")
        return
    if args.decision not in GATE_DECISIONS:
        print(f"❌ 未知结论: {args.decision},可选: {', '.join(GATE_DECISIONS)}")
        sys.exit(1)
    req_dir = resolve_req_dir(args.requirement)
    gate_file = req_dir / "gates" / f"{args.set}_decision.md"
    content = (
        "# 需求评审结论（人工/CLI 填写）\n\n"
        f"【结论】{GATE_DECISIONS[args.decision]}\n\n"
        f"【评审意见】{args.note or '(无)'}\n\n"
        f"【更新时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    write_text(gate_file, content)
    print(f"✅ 已更新 gate [{args.set}]: {GATE_DECISIONS[args.decision]} → {gate_file}")


def supplement_hint(req_dir: Path) -> str:
    """追加补充后的提示:补充是所有步骤的输入;未执行的步骤自动携带,已执行的需 force 重跑。"""
    state = load_state(req_dir)
    pending = next((s.name for s in STEP_REGISTRY if not step_done(state, s.name)), None)
    if pending:
        return (f"   提示:步骤 [{pending}] 尚未执行,后续运行会自动携带补充信息,无需 force。\n"
                f"   补充信息是所有步骤的输入,force 重跑任意步骤都会在原需求+全部补充的基础上重新分析。")
    return ("   提示:所有步骤均已执行,resume 会跳过已完成步骤,需 force 重跑受影响的步骤才生效,例如:\n"
            "       python main.py step collect --force   # 更新需求池清单\n"
            "       python main.py step analyze --force   # 更新需求卡片\n"
            "       其他步骤按需 --force;或 Web 面板「输入预览」中选择目标步骤追加(保存后自动重跑该步骤)。")


def cmd_supplement(args, crew):
    req_dir = resolve_req_dir(args.requirement)
    if args.view:
        content = read_supplement(req_dir)
        print(content if content.strip() else SUPPLEMENT_TEMPLATE)
        return
    if args.text and args.file:
        print("❌ --text 与 --file 请二选一")
        sys.exit(1)
    text = None
    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"❌ 读取文件失败: {e}")
            sys.exit(1)
    elif args.text:
        text = args.text.strip()
    elif not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        print('❌ 补充内容为空。用法: python main.py supplement --text "..." 或 --file path [--requirement REQ-xxx]')
        sys.exit(1)
    try:
        entries = append_supplement(req_dir, text)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    print(f"✅ 已追加第 {entries} 条需求补充: {req_dir / 'input' / 'supplement.md'}")
    print(supplement_hint(req_dir))


def cmd_report(args, crew, req_dir: Path | None = None):
    req_dir = req_dir or resolve_req_dir(args.requirement)
    report_file = req_dir / "reports" / "final_report.md"
    parts = [f"# 产品经理 Agent 全流程报告\n",
             f"- 需求编号: {req_dir.name}",
             f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
             "---\n"]
    for rel_path, title in REPORT_SECTIONS:
        content = read_text_file(req_dir / rel_path)
        if not content.strip():
            continue
        parts.append(f"## {title}\n")
        parts.append(content)
        parts.append("\n---\n")
    if req_dir.joinpath("artifacts/07_prototype.html").exists():
        parts.append("## 可交互原型\n")
        parts.append("浏览器打开 artifacts/07_prototype.html 体验原型。\n")
    write_text(report_file, "\n".join(parts))
    print(f"✅ 汇总报告已导出: {report_file}")


def cmd_web(args, crew):
    """启动可视化操作面板(懒加载 webapp,避免拖慢其他命令)。"""
    from webapp import serve
    serve(port=args.port, open_browser=not args.no_browser)


# ==================== 入口 ====================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="🤖 产品经理全流程 Agent 系统(基于 CrewAI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python main.py init --input input.txt            # 初始化需求目录\n"
            "  python main.py run --input input.txt              # 初始化并跑全流程\n"
            "  python main.py run --from design                  # 从方案设计步骤起跑\n"
            "  python main.py step prototype --force             # 单步重跑\n"
            "  python main.py status                             # 查看进度\n"
            "  python main.py gate --set review --decision passed --note \"评审通过\"\n"
            "  python main.py resume                             # 断点续跑\n"
            "  python main.py report                             # 生成汇总报告\n"
            "  python main.py web                                # 可视化操作面板(浏览器)\n"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="初始化需求目录")
    p_init.add_argument("--input", help="原始需求文件(默认 src/input.txt)")
    p_init.add_argument("--id", help="指定需求编号(默认自动生成 REQ-YYYYMMDD-NNN)")

    p_run = sub.add_parser("run", help="运行流水线(可 --input 初始化后直接跑)")
    p_run.add_argument("--input", help="原始需求文件,提供则先 init")
    p_run.add_argument("--id", help="init 时指定需求编号")
    p_run.add_argument("--requirement", help="指定已有需求编号")
    p_run.add_argument("--from", dest="from_step", help="从指定步骤起跑")

    p_step = sub.add_parser("step", help="只运行某一个步骤")
    p_step.add_argument("step", help="步骤名: " + ", ".join(s.name for s in STEP_REGISTRY))
    p_step.add_argument("--requirement", help="指定需求编号(默认最近一个)")
    p_step.add_argument("--force", action="store_true", help="已有产物也强制重跑")

    p_resume = sub.add_parser("resume", help="断点续跑(跳过已完成步骤)")
    p_resume.add_argument("--requirement", help="指定需求编号(默认最近一个)")
    p_resume.add_argument("--from", dest="from_step", help="从指定步骤起跑")

    p_status = sub.add_parser("status", help="查看流水线进度")
    p_status.add_argument("--requirement", help="指定需求编号(默认最近一个)")

    p_gate = sub.add_parser("gate", help="查看/填写人工评审 gate")
    p_gate.add_argument("--requirement", help="指定需求编号(默认最近一个)")
    p_gate.add_argument("--set", help="gate 名称(当前仅 review)")
    p_gate.add_argument("--decision", help="结论: " + ", ".join(GATE_DECISIONS))
    p_gate.add_argument("--note", help="评审意见")

    p_supplement = sub.add_parser("supplement", help="查看/追加需求补充信息(信息补充)")
    p_supplement.add_argument("--requirement", help="指定需求编号(默认最近一个)")
    p_supplement.add_argument("--text", help="补充内容(直接传参)")
    p_supplement.add_argument("--file", help="从文件读取补充内容(UTF-8 编码)")
    p_supplement.add_argument("--view", action="store_true", help="只查看当前补充内容")

    p_report = sub.add_parser("report", help="从产物汇总生成 final_report.md")
    p_report.add_argument("--requirement", help="指定需求编号(默认最近一个)")

    p_web = sub.add_parser("web", help="启动可视化操作面板(浏览器中逐步/全量运行)")
    p_web.add_argument("--port", type=int, default=8321, help="监听端口(默认 8321)")
    p_web.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    crew = ProductManagerCrew()
    dispatch = {
        "init": cmd_init,
        "run": cmd_run,
        "step": cmd_step,
        "resume": cmd_resume,
        "status": cmd_status,
        "gate": cmd_gate,
        "supplement": cmd_supplement,
        "report": cmd_report,
        "web": cmd_web,
    }
    dispatch[args.command](args, crew)


if __name__ == "__main__":
    main()
