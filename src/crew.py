# crew.py
"""产品经理 Agent 系统:Agent 与 Task 工厂(角色与任务模板见 my_project/config/*.yaml)。"""
import os
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml
from crewai import Agent, Task, Crew, Process
from dotenv import load_dotenv

from my_project.tools.custom_tools import SentimentAnalysisTools

load_dotenv()

CONFIG_DIR = Path(__file__).resolve().parent / "my_project" / "config"


class ProductManagerCrew:
    """产品经理 Agent 系统主控类"""

    # 步骤名 -> task 工厂方法名(pipeline.STEP_REGISTRY 按此分发)
    TASK_FACTORIES = {
        "orchestrator": "create_orchestrator_task",
        "collect": "create_collection_task",
        "analyze": "create_analysis_task",
        "evaluate": "create_evaluation_task",
        "design": "create_design_task",
        "feasibility": "create_feasibility_task",
        "prototype": "create_prototype_task",
        "narrate": "create_narration_task",
        "schedule": "create_schedule_task",
        "retrospect": "create_retrospective_task",
    }

    def __init__(self, llm_config: Optional[Dict] = None):
        self.llm_config = llm_config or self._get_default_llm_config()
        self.agents_config = self._load_yaml(CONFIG_DIR / "agents.yaml")
        self.tasks_config = self._load_yaml(CONFIG_DIR / "tasks.yaml")

    def _get_default_llm_config(self) -> Dict:
        return {
            "model": os.getenv("OPENAI_MODEL", "deepseek-chat"),
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.3")),
            "api_key": os.getenv("OPENAI_API_KEY"),
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
        }

    def _load_yaml(self, file_path: Path) -> Dict:
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except FileNotFoundError:
            print(f"警告: 配置文件 {file_path} 未找到")
            return {}
        except yaml.YAMLError as e:
            print(f"错误: 解析 {file_path} 失败 - {e}")
            return {}

    # ==================== Agent 工厂 ====================

    def _make_agent(self, key: str, delegation: bool = False, tools: Optional[list] = None,
                    llm_config: Optional[Dict] = None) -> Agent:
        config = self.agents_config.get(key, {})
        return Agent(
            role=config.get("role", key),
            goal=config.get("goal", ""),
            backstory=config.get("backstory", ""),
            verbose=True,
            allow_delegation=delegation,
            llm=llm_config or self.llm_config,
            tools=tools or [],
        )

    def create_orchestrator_agent(self) -> Agent:
        return self._make_agent("orchestrator_agent", delegation=True)

    def create_requirement_collector_agent(self) -> Agent:
        return self._make_agent("requirement_collector_agent")

    def create_requirement_analyst_agent(self) -> Agent:
        return self._make_agent("requirement_analyst_agent")

    def create_requirement_evaluator_agent(self) -> Agent:
        return self._make_agent("requirement_evaluator_agent")

    def create_solution_designer_agent(self) -> Agent:
        return self._make_agent("solution_designer_agent")

    def create_prototype_designer_agent(self) -> Agent:
        # 原型任务输出长(规格+HTML),单独放大 max_tokens 避免截断
        llm_config = dict(self.llm_config)
        llm_config["max_tokens"] = 8192
        return self._make_agent("prototype_designer_agent", llm_config=llm_config)

    def create_technical_bridge_agent(self) -> Agent:
        return self._make_agent("technical_bridge_agent")

    def create_schedule_planner_agent(self) -> Agent:
        return self._make_agent("schedule_planner_agent")

    def create_value_narrator_agent(self) -> Agent:
        return self._make_agent("value_narrator_agent")

    def create_tracking_retrospective_agent(self) -> Agent:
        return self._make_agent("tracking_retrospective_agent", tools=[SentimentAnalysisTools()])

    # ==================== Task 工厂 ====================

    def _fill(self, template: str, inputs: Dict[str, str]) -> str:
        for key, value in inputs.items():
            template = template.replace("{" + key + "}", value if value and value.strip() else "(未提供)")
        return template

    def _make_task(self, task_key: str, agent: Agent, inputs: Dict[str, str]) -> Task:
        config = self.tasks_config.get(task_key, {})
        return Task(
            description=self._fill(config.get("description", ""), inputs),
            expected_output=self._fill(config.get("expected_output", ""), inputs),
            agent=agent,
        )

    def create_orchestrator_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("orchestrator_task", self.create_orchestrator_agent(), inputs)

    def create_collection_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("requirement_collection_task", self.create_requirement_collector_agent(), inputs)

    def create_analysis_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("requirement_analysis_task", self.create_requirement_analyst_agent(), inputs)

    def create_evaluation_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("requirement_evaluation_task", self.create_requirement_evaluator_agent(), inputs)

    def create_design_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("solution_design_task", self.create_solution_designer_agent(), inputs)

    def create_feasibility_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("technical_feasibility_task", self.create_technical_bridge_agent(), inputs)

    def create_prototype_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("prototype_design_task", self.create_prototype_designer_agent(), inputs)

    def create_narration_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("value_narration_task", self.create_value_narrator_agent(), inputs)

    def create_schedule_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("schedule_planning_task", self.create_schedule_planner_agent(), inputs)

    def create_retrospective_task(self, inputs: Dict[str, str]) -> Task:
        return self._make_task("retrospective_task", self.create_tracking_retrospective_agent(), inputs)

    # ==================== 统一入口 ====================

    def create_task(self, step_name: str, inputs: Dict[str, str]) -> Task:
        """按 pipeline 的步骤名分发到对应 task 工厂。"""
        if step_name not in self.TASK_FACTORIES:
            raise ValueError(f"未知步骤: {step_name},可选: {list(self.TASK_FACTORIES)}")
        return getattr(self, self.TASK_FACTORIES[step_name])(inputs)

    def kickoff_task(self, task: Task) -> str:
        """单 Agent Crew 执行一个 task,返回文本结果。"""
        crew = Crew(agents=[task.agent], tasks=[task], process=Process.sequential, verbose=True)
        return str(crew.kickoff())


if __name__ == "__main__":
    print("本模块为 Agent/Task 工厂库,请通过 main.py 使用:")
    print("  python main.py --help")
