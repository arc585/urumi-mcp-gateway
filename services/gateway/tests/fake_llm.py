"""A fake OpenAI-compatible Chat Completions server for testing the chat tool loop offline.

Turn 1 (last message from the user): request two tool calls on two different upstreams.
Turn 2 (last message is a tool result): answer with a report of what the gateway sent, so tests
can check that results were labelled untrusted and schemas were sanitised.
"""

import json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def _completion(message: dict) -> JSONResponse:
    finish = "tool_calls" if message.get("tool_calls") else "stop"
    return JSONResponse({
        "id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": "fake-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


async def completions(request: Request) -> JSONResponse:
    body = await request.json()
    if body.get("model") == "exhausted-model":  # simulates a model whose free-tier quota is used up
        return JSONResponse({"error": {"code": 429, "message": "Quota exceeded for metric: free_tier_requests",
                                       "status": "RESOURCE_EXHAUSTED"}}, status_code=429)
    messages = body["messages"]
    tools = body.get("tools") or []
    last = messages[-1]
    if last["role"] == "user":
        return _completion({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                _tool_call("call_1", "store-info__lookup_store_info", {"topic": "returns"}),
                _tool_call("call_2", "shipping-info__estimate_shipping", {"destination_city": "Bengaluru", "order_total": 1200}),
            ],
        })
    tool_messages = [m for m in messages if m["role"] == "tool"]
    schemas = json.dumps([tool["function"]["parameters"] for tool in tools])
    descriptions = [tool["function"]["description"] for tool in tools]
    report = {
        "tool_results": len(tool_messages),
        "all_labelled_untrusted": all("UNTRUSTED DATA" in m["content"] for m in tool_messages),
        "tools_offered": sorted(tool["function"]["name"] for tool in tools),
        "schemas_have_titles": '"title"' in schemas,
        "descriptions_single_line": all("\n" not in d for d in descriptions),
        "system_prompt_present": messages[0]["role"] == "system" and "UNTRUSTED DATA" in messages[0]["content"],
    }
    return _completion({"role": "assistant", "content": "REPORT " + json.dumps(report)})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


app = Starlette(routes=[
    Route("/v1/chat/completions", completions, methods=["POST"]),
    Route("/health", health),
])
