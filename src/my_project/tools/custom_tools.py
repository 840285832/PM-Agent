"""自定义工具:情感分析(通过 OpenAI 兼容 API 调用 LLM,支持 DeepSeek 等)。"""
import json
import os

from crewai.tools import BaseTool
from openai import OpenAI


class SentimentAnalysisTools(BaseTool):
    # 注意:tool name 会转换为 OpenAI function schema 的 name,
    # 只允许 ^[a-zA-Z0-9_-]+$,不能使用中文
    name: str = "sentiment_analysis"
    description: str = (
        "分析用户反馈文本的情感倾向（中文）:"
        "返回 JSON: {sentiment: 积极/中性/消极, score: 1-5(情感强度,5为最强), keywords: 关键词列表}"
    )

    def _run(self, text: str) -> str:
        """调用 OpenAI 兼容接口做情感分析,失败时返回带 error 字段的 JSON,不抛异常。"""
        try:
            client = OpenAI(
                api_key=os.getenv("OPENAI_API_KEY", ""),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
            )
            response = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
                temperature=0.1,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是情感分析器。分析用户反馈文本,只输出 JSON,不要输出任何其他内容。"
                            '格式: {"sentiment": "积极|中性|消极", "score": 1-5, "keywords": ["词1", "词2"]}'
                        ),
                    },
                    {"role": "user", "content": text},
                ],
            )
            return response.choices[0].message.content or "{}"
        except Exception as e:
            return json.dumps(
                {"sentiment": "未知", "score": 0, "keywords": [], "error": str(e)},
                ensure_ascii=False,
            )
