"""Web 可视化操作面板:步骤查看 / 单步执行 / 全流程执行 / 评审 gate / 过程数据录入。

零外部依赖(Python 标准库)。启动: python main.py web [--port 8321]
"""
import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifacts import (
    OUTPUT_DIR,
    append_supplement,
    count_supplement_entries,
    init_new_requirement,
    latest_requirement_dir,
    list_requirement_ids,
    load_state,
    read_supplement,
    read_text_file,
    requirement_dir,
    save_state,
    step_done,
    supplement_stale,
    write_text,
)
from pipeline import STEP_REGISTRY, GateBlockedError, check_gate, find_step, run_step

WEBUI_DIR = Path(__file__).resolve().parent / "webui"

# 允许通过 API 读取的需求目录内相对路径前缀(防路径穿越)
ALLOWED_PREFIXES = ("artifacts/", "gates/", "process_log/", "input/", "reports/", "state.json")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


class RunState:
    """全局运行状态(同时只允许一个步骤在跑)。"""

    lock = threading.Lock()
    req_id: str | None = None
    running = False
    current_step: str | None = None
    stop_requested = False
    last_error: str | None = None
    last_finished: str | None = None


_crew = None
_crew_lock = threading.Lock()


def _get_crew():
    """懒加载 crew(导入 crewai 较慢,首次执行步骤时才创建)。"""
    global _crew
    if _crew is None:
        with _crew_lock:
            if _crew is None:
                from crew import ProductManagerCrew
                _crew = ProductManagerCrew()
    return _crew


def _record_error(req_dir: Path, step_name: str, error: Exception) -> None:
    state = load_state(req_dir)
    state.setdefault("steps", {}).setdefault(step_name, {})
    state["steps"][step_name]["last_error"] = f"{type(error).__name__}: {error}"
    save_state(req_dir, state)


def _run_spec(req_dir: Path, spec, force: bool = False) -> None:
    """执行单个步骤,异常记录到 state 并抛出。"""
    try:
        run_step(_get_crew(), req_dir, spec, force=force)
    except GateBlockedError:
        raise
    except Exception as e:
        _record_error(req_dir, spec.name, e)
        raise


def _job(req_id: str, mode: str, step_name: str | None, force: bool) -> None:
    req_dir = requirement_dir(req_id)
    try:
        if mode == "full":
            for spec in STEP_REGISTRY:
                if RunState.stop_requested:
                    break
                RunState.current_step = spec.name
                try:
                    _run_spec(req_dir, spec)
                except GateBlockedError as e:
                    RunState.last_error = str(e).splitlines()[0] if str(e) else "评审 gate 未通过"
                    break
                except Exception:
                    RunState.last_error = traceback.format_exc(limit=1).strip().splitlines()[-1]
                    break
                RunState.last_finished = spec.name
        elif mode == "next":
            spec = _next_pending(req_dir)
            if spec is None:
                RunState.last_error = "所有步骤已完成"
                return
            RunState.current_step = spec.name
            try:
                _run_spec(req_dir, spec)
            except GateBlockedError as e:
                RunState.last_error = str(e).splitlines()[0] if str(e) else "评审 gate 未通过"
                return
            except Exception:
                RunState.last_error = traceback.format_exc(limit=1).strip().splitlines()[-1]
                return
            RunState.last_finished = spec.name
        elif mode == "step":
            spec = find_step(step_name or "")
            if spec is None:
                RunState.last_error = f"未知步骤: {step_name}"
                return
            RunState.current_step = spec.name
            try:
                _run_spec(req_dir, spec, force=force)
            except GateBlockedError as e:
                RunState.last_error = str(e).splitlines()[0] if str(e) else "评审 gate 未通过"
                return
            except Exception:
                RunState.last_error = traceback.format_exc(limit=1).strip().splitlines()[-1]
                return
            RunState.last_finished = spec.name
    finally:
        RunState.current_step = None
        RunState.running = False


def _next_pending(req_dir: Path):
    state = load_state(req_dir)
    for spec in STEP_REGISTRY:
        if not step_done(state, spec.name):
            return spec
    return None


def start_job(req_id: str, mode: str, step_name: str | None = None, force: bool = False) -> str | None:
    """启动后台任务,返回错误信息(None 表示已启动)。"""
    with RunState.lock:
        if RunState.running:
            return "已有任务在运行中,请等待完成或先停止"
        if not requirement_dir(req_id).exists():
            return f"需求目录不存在: {req_id}"
        RunState.req_id = req_id
        RunState.running = True
        RunState.stop_requested = False
        RunState.last_error = None
        RunState.last_finished = None
        RunState.current_step = None
    threading.Thread(target=_job, args=(req_id, mode, step_name, force), daemon=True).start()
    return None


def build_status(req_id: str) -> dict:
    req_dir = requirement_dir(req_id)
    state = load_state(req_dir)
    steps = []
    for spec in STEP_REGISTRY:
        info = state.get("steps", {}).get(spec.name, {})
        steps.append({
            "name": spec.name,
            "label": spec.label,
            "done": bool(info.get("done")),
            "warnings": info.get("warnings", []),
            "last_error": info.get("last_error"),
            "artifact": info.get("artifact", ""),
            "gate": spec.gate,
            "gate_ok": check_gate(req_dir, spec.gate) if spec.gate else None,
        })
    next_pending = next((s["name"] for s in steps if not s["done"]), None)
    is_this = RunState.req_id == req_id
    return {
        "requirement_id": req_id,
        "steps": steps,
        "next_pending": next_pending,
        "gate": {"review": check_gate(req_dir, "review")},
        "running": RunState.running and is_this,
        "current_step": RunState.current_step if is_this else None,
        "stop_requested": RunState.stop_requested and is_this,
        "last_error": RunState.last_error if is_this else None,
        "last_finished": RunState.last_finished if is_this else None,
        "supplement": {
            "entries": count_supplement_entries(read_supplement(req_dir)),
            "stale": supplement_stale(req_dir, state),
        },
    }


def list_requirements() -> list[dict]:
    result = []
    for req_id in list_requirement_ids():
        req_dir = requirement_dir(req_id)
        state = load_state(req_dir)
        done = sum(1 for s in state.get("steps", {}).values() if s.get("done"))
        result.append({
            "id": req_id,
            "created_at": state.get("created_at", ""),
            "steps_done": done,
            "steps_total": len(STEP_REGISTRY),
        })
    return result


def safe_rel_path(req_dir: Path, rel: str) -> Path | None:
    """校验并返回需求目录内的安全路径,非法返回 None。"""
    rel = rel.replace("\\", "/").lstrip("/")
    if rel.startswith("..") or any(part == ".." for part in rel.split("/")):
        return None
    if not rel.startswith(ALLOWED_PREFIXES):
        return None
    return req_dir / rel


def list_files(req_dir: Path) -> list[dict]:
    files = []
    for p in sorted(req_dir.rglob("*")):
        if p.is_file() and p.name not in ("state.json",):
            rel = p.relative_to(req_dir).as_posix()
            if rel.startswith(ALLOWED_PREFIXES):
                files.append({"path": rel, "size": p.stat().st_size})
    return files


class Handler(BaseHTTPRequestHandler):
    server_version = "PMAgentsWeb/1.0"

    def log_message(self, format, *args):  # 精简日志
        sys.stdout.write(f"[web] {self.address_string()} {format % args}\n")

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, msg, code=400):
        self._send_json({"error": msg}, code)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None  # 交给 do_POST 返回 400,避免错误请求静默当作空 body

    def _route(self, method, path, body):
        parts = [p for p in path.split("/") if p]
        # GET / → 面板页面
        if method == "GET" and path in ("/", "/index.html"):
            return self._serve_index()
        # 静态资源
        if method == "GET" and parts and parts[0] == "webui":
            return self._serve_static(parts[1] if len(parts) > 1 else "index.html")
        # API
        if parts and parts[0] == "api":
            return self._route_api(method, parts[1:], body)
        self._send_error("未找到资源", 404)

    def _serve_index(self):
        return self._serve_static("index.html")

    def _serve_static(self, name: str):
        if "/" in name or "\\" in name or ".." in name:
            return self._send_error("非法路径", 400)
        file_path = WEBUI_DIR / name
        if not file_path.exists() or not file_path.is_file():
            return self._send_error("未找到资源", 404)
        content = file_path.read_bytes()
        content_type = CONTENT_TYPES.get(file_path.suffix.lower(), "text/plain; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _route_api(self, method, parts, body):
        # /api/requirements 或 /api/requirements/init(前端使用后者)
        if parts and parts[0] == "requirements":
            if method == "GET" and len(parts) == 1:
                return self._send_json(list_requirements())
            if method == "POST" and (len(parts) == 1 or (len(parts) == 2 and parts[1] == "init")):
                return self._api_init_requirement(body)
        # /api/req/<id>/...
        if len(parts) >= 3 and parts[0] == "req":
            req_id = parts[1]
            req_dir = requirement_dir(req_id)
            if not req_dir.exists():
                return self._send_error(f"需求目录不存在: {req_id}", 404)
            action = parts[2]
            if method == "GET" and action == "status":
                return self._send_json(build_status(req_id))
            if method == "POST" and action == "run":
                return self._api_run(req_id, body)
            if method == "POST" and action == "stop":
                RunState.stop_requested = True
                return self._send_json({"ok": True, "msg": "将在当前步骤结束后停止"})
            if action == "gate" and method in ("GET", "POST"):
                return self._api_gate(method, req_id, req_dir, body)
            if action == "files" and method == "GET":
                return self._send_json(list_files(req_dir))
            if action in ("artifact", "raw") and method == "GET":
                # 路径可放在 URL 路径段(/api/req/<id>/artifact/a/b.md)或 ?path= 查询参数
                rel = "/".join(parts[3:]) if len(parts) >= 4 else ""
                if not rel and body.get("path"):
                    rel = body["path"][0] if isinstance(body["path"], list) else str(body["path"])
                if rel:
                    return self._api_file(method, req_dir, rel, raw=(action == "raw"))
            if action == "processlog" and len(parts) >= 4:
                return self._api_processlog(method, req_dir, parts[3], body)
            if action == "supplement" and method in ("GET", "POST"):
                return self._api_supplement(method, req_dir, body)
            if action == "input" and method == "GET":
                return self._api_input(req_dir)
        self._send_error("未找到接口", 404)

    def _api_init_requirement(self, body):
        text = (body.get("input") or "").strip()
        if not text:
            default_input = Path(__file__).resolve().parent / "input.txt"
            if default_input.exists():
                text = default_input.read_text(encoding="utf-8").strip()
        if not text:
            return self._send_error("请输入需求内容,或先填写 src/input.txt", 400)
        try:
            req_id, req_dir = init_new_requirement(text)
        except Exception as e:  # 任何初始化异常都返回可读错误,避免连接无响应(fail to fetch)
            traceback.print_exc()
            return self._send_error(f"初始化需求目录失败: {e}", 500)
        return self._send_json({"ok": True, "id": req_id, "dir": str(req_dir)})

    def _api_run(self, req_id, body):
        mode = body.get("mode", "next")
        if mode not in ("full", "next", "step"):
            return self._send_error(f"未知模式: {mode}")
        step_name = body.get("step") if mode == "step" else None
        force = bool(body.get("force", False))
        error = start_job(req_id, mode, step_name, force)
        if error:
            return self._send_error(error, 409)
        return self._send_json({"ok": True, "mode": mode})

    def _api_gate(self, method, req_id, req_dir, body):
        gate_file = req_dir / "gates" / "review_decision.md"
        if method == "GET":
            return self._send_json({"decision_text": read_text_file(gate_file),
                                    "passed": check_gate(req_dir, "review")})
        from datetime import datetime
        decision_map = {"passed": "通过", "conditional": "有条件通过", "rejected": "打回"}
        decision = decision_map.get(body.get("decision"))
        if not decision:
            return self._send_error("decision 需为 passed/conditional/rejected", 400)
        write_text(gate_file, (
            "# 需求评审结论（Web 面板填写）\n\n"
            f"【结论】{decision}\n\n"
            f"【评审意见】{body.get('note') or '(无)'}\n\n"
            f"【更新时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        ))
        return self._send_json({"ok": True, "passed": check_gate(req_dir, "review")})

    def _api_file(self, method, req_dir, rel, raw=False):
        if method != "GET":
            return self._send_error("仅支持 GET", 405)
        safe = safe_rel_path(req_dir, rel)
        if safe is None or not safe.exists():
            return self._send_error(f"文件不存在或路径非法: {rel}", 404)
        if raw:
            content = safe.read_bytes()
            content_type = CONTENT_TYPES.get(safe.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        return self._send_json({"path": rel, "content": read_text_file(safe)})

    def _api_processlog(self, method, req_dir, name, body):
        allowed = {"dev_status.md", "acceptance_result.md", "post_launch_data.md", "feedback.md"}
        if name not in allowed:
            return self._send_error(f"未知过程数据文件: {name}", 404)
        file_path = req_dir / "process_log" / name
        if method == "GET":
            return self._send_json({"name": name, "content": read_text_file(file_path)})
        if method == "POST":
            write_text(file_path, body.get("content") or "")
            return self._send_json({"ok": True})
        return self._send_error("仅支持 GET/POST", 405)

    def _api_supplement(self, method, req_dir, body):
        """需求补充信息读写:GET 查看,POST 追加(不覆盖历史)并按目标步骤自动重跑。"""
        if method == "GET":
            content = read_supplement(req_dir)
            return self._send_json({"name": "supplement", "content": content,
                                    "entries": count_supplement_entries(content)})
        if method == "POST":
            text = (body.get("content") or "").strip()
            if not text:
                return self._send_error("补充内容不能为空", 400)
            with RunState.lock:  # 串行化读-改-写,避免并发追加丢内容
                entries = append_supplement(req_dir, text)
            # 保存后自动重跑目标步骤:body.step 指定,未指定时取下一个待执行步骤。
            # 该步骤 force 重跑,在原需求+全部补充的基础上重新分析。已有任务在跑则不抢占。
            result = {"ok": True, "entries": entries, "auto_run": False}
            step_arg = (body.get("step") or "").strip()
            target = None
            if step_arg:
                if find_step(step_arg) is None:
                    return self._send_error(f"未知步骤: {step_arg}", 400)
                target = step_arg
            else:
                state = load_state(req_dir)
                target = next((s.name for s in STEP_REGISTRY if not step_done(state, s.name)), None)
            if target:
                error = start_job(req_dir.name, "step", target, force=True)
                if error:
                    result["auto_run_error"] = error
                else:
                    result.update(auto_run=True, auto_run_step=target)
            return self._send_json(result)
        return self._send_error("仅支持 GET/POST", 405)

    def _api_input(self, req_dir):
        """合并后的当前需求输入预览:原始需求 + 全部补充记录(执行前展示)。"""
        raw = read_text_file(req_dir / "input" / "raw_requirement.txt")
        supplement = read_supplement(req_dir)
        return self._send_json({"raw": raw, "supplement": supplement,
                                "entries": count_supplement_entries(supplement)})

    def do_GET(self):
        parsed = urlparse(self.path)
        self._route("GET", parsed.path, dict(parse_qs(parsed.query)))

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._read_body()
        if body is None:
            return self._send_error("请求体必须是 UTF-8 编码的 JSON", 400)
        self._route("POST", parsed.path, body)


def serve(port: int = 8321, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print("🤖 PMAgents 可视化操作面板")
    print(f"   地址: {url}")
    print("   Ctrl+C 退出")
    print("=" * 60)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出面板服务")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
