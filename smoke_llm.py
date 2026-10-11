
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
api_key = os.getenv("LLM_API_KEY", "")
model = os.getenv("LLM_MODEL", "")

if not base_url.startswith("https://"):
    raise ValueError("请检查 LLM_BASE_URL")

if not api_key or not model or "REPLACE_ME" in (api_key, model):
    raise ValueError("请先配置真实的 API Key 和模型 ID")

print("开始测试当前模型 API...")
print("模型:", model)

start = time.perf_counter()

try:
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "请只回复：首单实验室API连接成功"
                }
            ],
            "stream": False
        },
        timeout=60.0
    )

    print("HTTP 状态码:", response.status_code)
    response.raise_for_status()

    data = response.json()
    answer = data["choices"][0]["message"]["content"]

    print("模型回复:", answer)
    print("Token 用量:", data.get("usage", "未提供"))
    print("耗时:", round(time.perf_counter() - start, 2), "秒")
    print("API 测试通过")

except httpx.HTTPStatusError as e:
    print("接口请求失败，状态码:", e.response.status_code)
except httpx.RequestError as e:
    print("网络连接异常:", type(e).__name__)
except (KeyError, IndexError, ValueError, TypeError):
    print("响应格式不符合预期，请检查模型接口兼容性")
