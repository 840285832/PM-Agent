"""产物与状态管理:需求目录初始化、state.json 读写、代码块/JSON 容错解析。"""
import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 输出根目录:默认写桌面外部目录(项目内不堆积产物);可用环境变量 PMA_OUTPUT_DIR 覆盖。
# 每个需求一个子目录 OUTPUT_DIR/REQ-YYYYMMDD-NNN/,超过保留数量的最旧目录自动移入 _archive/。
OUTPUT_DIR = Path(os.environ.get("PMA_OUTPUT_DIR", r"C:\Users\hanyi\Desktop\产品经理—项目分析\test1"))
MAX_ACTIVE_REQUIREMENTS = 3
CONFIG_DIR = Path(__file__).resolve().parent / "my_project" / "config"

GATE_TEMPLATE = """# 需求评审结论（人工填写）

> 使用方式：将下方【结论】行替换为：通过 / 有条件通过 / 打回，并在【评审意见】填写理由。
> 或使用 CLI：python main.py gate --set review --decision passed --note "理由"

【结论】(待填写)

【评审意见】(待填写)
"""

SUPPLEMENT_REL_PATH = "input/supplement.md"
ENTRY_MARKER = "### 【补充】"

SUPPLEMENT_TEMPLATE = """# 需求补充信息（信息补充）

> 需求创建后产生的衍生问题，由 PM 在此追加补充信息；多轮补充按时间记录，不会覆盖。
> 追加方式：
>   CLI:  python main.py supplement --text "补充内容" [--requirement REQ-xxx]
>   Web:  操作面板 →「需求补充」页签 → 填写后点「追加保存」
> 注意：Web 面板在主页面「输入预览」中选择目标步骤追加，保存后自动 force 重跑该步骤，
>       在原需求+全部补充的基础上重新分析；多次追加叠加，不覆盖历史。
>       CLI 保存后不自动重跑，由 PM 手动决定何时重跑（--force 或面板中步骤右侧 ↻）。

## 补充记录

(暂无补充记录)
"""

PROCESS_LOG_TEMPLATES: dict[str, str] = {
    "dev_status.md": """# 研发过程记录（人工填写）

> 研发启动后逐任务填写，任务ID 见 artifacts/09_schedule_plan.md 排期表。
> 未填写的任务将被视为「未采集」。

| 任务ID | 计划起止 | 实际起止 | 实际人天 | 状态(未开始/进行中/已完成/延期) | 延期原因 |
|--------|----------|----------|----------|-------------------------------|----------|
| (待填写) | | | | | |

## 里程碑备注
(待填写)
""",
    "acceptance_result.md": """# 验收记录（人工填写）

> 验收清单见 artifacts/09_schedule_plan.md，逐任务填写结论。

| 任务ID | 验收结论(通过/不通过) | 问题描述 |
|--------|----------------------|----------|
| (待填写) | | |
""",
    "post_launch_data.md": """# 上线后数据（人工填写）

> 填写需求卡片/决策简报中预期指标的实际值，用于复盘对比。

| 指标 | 预期值 | 实际值 | 备注 |
|------|--------|--------|------|
| (待填写) | | | |
""",
    "feedback.md": """# 用户反馈（人工粘贴）

> 上线后收集的用户反馈原文，逐条粘贴。复盘时由 Agent 做情感分析。

(待填写)
""",
}


# ==================== 需求目录 ====================

# 并发保护:Web 面板多线程 + CLI 可能同时创建需求,编号生成与归档必须原子化
_INIT_LOCK = threading.Lock()


def generate_requirement_id() -> str:
    """按日期生成需求编号 REQ-YYYYMMDD-NNN。归档目录(_archive)也参与扫描,编号永不重用。"""
    date_part = datetime.now().strftime("%Y%m%d")
    prefix = f"REQ-{date_part}-"
    seq = 1
    existing: set[str] = set()
    for base in (OUTPUT_DIR, OUTPUT_DIR / "_archive"):
        if base.exists():
            existing |= {d.name for d in base.iterdir() if d.is_dir() and d.name.startswith(prefix)}
    while f"{prefix}{seq:03d}" in existing:
        seq += 1
    return f"{prefix}{seq:03d}"


def requirement_dir(requirement_id: str) -> Path:
    return OUTPUT_DIR / requirement_id


def latest_requirement_dir() -> Optional[Path]:
    """返回最近修改的需求目录,不存在则 None。"""
    if not OUTPUT_DIR.exists():
        return None
    dirs = [d for d in OUTPUT_DIR.iterdir() if d.is_dir() and d.name.startswith("REQ-")]
    if not dirs:
        return None
    return max(dirs, key=lambda d: d.stat().st_mtime)


def list_requirement_ids() -> list[str]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted(d.name for d in OUTPUT_DIR.iterdir() if d.is_dir() and d.name.startswith("REQ-"))


def init_new_requirement(raw_input: str, req_id: Optional[str] = None) -> tuple[str, Path]:
    """生成编号并创建需求目录(加锁,防止并发创建导致编号冲突/归档冲突)。"""
    with _INIT_LOCK:
        rid = req_id or generate_requirement_id()
        return rid, init_requirement_dir(rid, raw_input)


def init_requirement_dir(requirement_id: str, raw_input: str) -> Path:
    """创建需求目录:子目录、原始输入拷贝、process_log 模板、gate 占位、state.json。
    创建后自动执行归档检查(超出保留数量时最旧的移入 _archive/)。"""
    req_dir = requirement_dir(requirement_id)
    for sub in ["input", "artifacts", "gates", "process_log", "process_log/_templates", "reports"]:
        (req_dir / sub).mkdir(parents=True, exist_ok=True)

    write_text(req_dir / "input" / "raw_requirement.txt", raw_input)
    write_text(req_dir / "input" / "supplement.md", SUPPLEMENT_TEMPLATE)

    for name, template in PROCESS_LOG_TEMPLATES.items():
        write_text(req_dir / "process_log" / "_templates" / name, template)
        target = req_dir / "process_log" / name
        if not target.exists():
            write_text(target, template)

    gate = req_dir / "gates" / "review_decision.md"
    if not gate.exists():
        write_text(gate, GATE_TEMPLATE)

    save_state(req_dir, new_state(requirement_id))

    archived = prune_old_requirements()
    if archived:
        print(f"已归档最旧需求(超过保留数量 {MAX_ACTIVE_REQUIREMENTS}): {', '.join(archived)}")
    return req_dir


def prune_old_requirements(keep: int = MAX_ACTIVE_REQUIREMENTS) -> list[str]:
    """将超出保留数量的最旧需求目录移入 _archive/(可恢复,不删除)。
    目录名 REQ-YYYYMMDD-NNN 按名称排序即时间顺序。返回被归档的 id 列表。
    归档目标已存在时(历史脏数据)加时间戳后缀,不覆盖、不崩溃。"""
    ids = list_requirement_ids()
    if len(ids) <= keep:
        return []
    archive_root = OUTPUT_DIR / "_archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived = []
    for req_id in ids[: len(ids) - keep]:
        src = requirement_dir(req_id)
        dst = archive_root / req_id
        if dst.exists():
            dst = archive_root / f"{req_id}_dup_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            print(f"警告: 归档目标已存在 {archive_root / req_id},已改用 {dst.name}")
        shutil.move(str(src), str(dst))
        archived.append(req_id)
    return archived


# ==================== state.json ====================

def new_state(requirement_id: str) -> dict:
    return {
        "requirement_id": requirement_id,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "steps": {},
        "warnings": [],
    }


def load_state(req_dir: Path) -> dict:
    path = req_dir / "state.json"
    if not path.exists():
        return new_state(req_dir.name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"警告: {path} 解析失败,按空状态处理")
        return new_state(req_dir.name)


def save_state(req_dir: Path, state: dict) -> None:
    write_text(req_dir / "state.json", json.dumps(state, ensure_ascii=False, indent=2))


def step_done(state: dict, step_name: str) -> bool:
    return bool(state.get("steps", {}).get(step_name, {}).get("done", False))


def mark_step_done(req_dir: Path, step_name: str, outputs: list[str], warnings: Optional[list[str]] = None) -> None:
    state = load_state(req_dir)
    state["steps"][step_name] = {
        "done": True,
        "artifact": outputs[0] if outputs else "",
        "outputs": outputs,
        "warnings": warnings or [],
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    state["warnings"] = (state.get("warnings") or []) + [f"[{step_name}] {w}" for w in (warnings or [])]
    save_state(req_dir, state)


# ==================== 文件读写 ====================

def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_input(req_dir: Path, rel_path: str) -> str:
    """按注册表的输入路径读内容。cfg: 前缀表示读 src/my_project/config/ 下的全局配置。"""
    if rel_path.startswith("cfg:"):
        return read_text_file(CONFIG_DIR / rel_path[4:])
    return read_text_file(req_dir / rel_path)


def read_artifact(req_dir: Path, rel_path: str) -> str:
    return read_text_file(req_dir / rel_path)


# ==================== 需求补充 ====================

def supplement_file(req_dir: Path) -> Path:
    return req_dir / SUPPLEMENT_REL_PATH


def read_supplement(req_dir: Path) -> str:
    return read_text_file(supplement_file(req_dir))


def count_supplement_entries(content: str) -> int:
    return content.count(ENTRY_MARKER)


def append_supplement(req_dir: Path, text: str) -> int:
    """追加一条带时间戳的补充记录;文件缺失/为空时先写入模板头(兼容旧需求目录)。
    首次追加时移除 (暂无补充记录) 占位行。返回总条数。"""
    text = text.strip()
    if not text:
        raise ValueError("补充内容不能为空")
    path = supplement_file(req_dir)
    content = read_text_file(path)
    if not content.strip():
        content = SUPPLEMENT_TEMPLATE
    content = content.replace("(暂无补充记录)", "").rstrip() + "\n\n"
    content += f"{ENTRY_MARKER} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{text}\n"
    write_text(path, content)
    return count_supplement_entries(content)


def supplement_stale(req_dir: Path, state: dict) -> bool:
    """补充文件最后修改时间晚于 analyze 步骤完成时间 → 补充未被分析吸收,建议强制重跑。"""
    path = supplement_file(req_dir)
    if not path.exists():
        return False
    fin = (state.get("steps") or {}).get("analyze", {}).get("finished_at")
    if not fin:
        return False
    try:
        fin_dt = datetime.strptime(fin, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return path.stat().st_mtime > fin_dt.timestamp()


# ==================== 容错解析 ====================

def extract_code_block(text: str, lang: str, tolerant: bool = True) -> Optional[str]:
    """提取第一个 ```lang 代码块(允许 ``` 后带额外参数,如 ```mermaid gantt)。
    tolerant=True 时:若缺少闭合 ```,取到文本末尾(LLM 输出常漏闭合围栏)。"""
    pattern = re.compile(rf"```{re.escape(lang)}[^\n]*\n(.*?)```", re.DOTALL)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    if tolerant:
        open_pattern = re.compile(rf"```{re.escape(lang)}[^\n]*\n(.*)$", re.DOTALL)
        open_match = open_pattern.search(text)
        if open_match and open_match.group(1).strip():
            return open_match.group(1).strip()
    return None


def extract_json(text: str) -> Optional[Any]:
    """容错解析 JSON:先尝试整段文本,再尝试 ```json 代码块;失败返回 None。"""
    candidates = [text.strip()]
    block = extract_code_block(text, "json")
    if block:
        candidates.append(block.strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # 尝试截取第一个 { 到最后一个 }
            start, end = candidate.find("{"), candidate.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(candidate[start:end + 1])
                except json.JSONDecodeError:
                    continue
    return None
