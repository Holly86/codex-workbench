#!/usr/bin/env python3
"""Deterministic direct-child Codex 0.146.0 fixture used by contract tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def send(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def thread(cwd: str, thread_id: str = "0198f000-0000-7000-8000-000000000001") -> dict[str, Any]:
    return {
        "id": thread_id,
        "sessionId": "0198f000-0000-7000-8000-000000000000",
        "forkedFromId": None,
        "parentThreadId": None,
        "preview": "Deterministic fake thread",
        "ephemeral": False,
        "isPinned": False,
        "modelProvider": "openai",
        "createdAt": 1_700_000_000,
        "updatedAt": 1_700_000_001,
        "recencyAt": 1_700_000_001,
        "status": {"type": "idle"},
        "path": None,
        "cwd": cwd,
        "cliVersion": "0.146.0",
        "source": "appServer",
        "threadSource": None,
        "agentNickname": None,
        "agentRole": None,
        "gitInfo": None,
        "name": "Fake thread",
        "turns": [],
    }


def turn(status: str = "inProgress", items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": "0198f000-0000-7000-8000-000000000002",
        "items": items or [],
        "itemsView": {"type": "full"},
        "status": status,
        "error": None,
        "startedAt": 1_700_000_002,
        "completedAt": 1_700_000_003 if status != "inProgress" else None,
        "durationMs": 1000 if status != "inProgress" else None,
    }


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print("codex-cli 0.146.0")
        return 0
    if sys.argv[1:] != ["app-server", "--stdio"]:
        return 2
    scenario = os.environ.get("FAKE_CODEX_SCENARIO", "normal")
    active_cwd = str(Path.cwd())
    active_thread = thread(active_cwd)
    pending_out_of_order: list[dict[str, Any]] = []
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}
        if method == "initialize":
            send(
                {
                    "id": request_id,
                    "result": {
                        "userAgent": "codex-cli/0.146.0 fake",
                        "codexHome": "/private/fake-codex-home",
                        "platformFamily": "unix",
                        "platformOs": "linux",
                    },
                }
            )
        elif method == "initialized":
            if scenario == "malformed":
                sys.stdout.write("{not-json}\n")
                sys.stdout.flush()
            elif scenario == "eof":
                return 0
            elif scenario == "crash":
                return 17
            elif scenario == "stderr_flood":
                sys.stderr.write("authorization=secret sk-fixture-secret " * 5000)
                sys.stderr.flush()
        elif method == "test/outOfOrder":
            pending_out_of_order.append(request)
            if len(pending_out_of_order) == 2:
                for pending in reversed(pending_out_of_order):
                    send({"id": pending["id"], "result": pending["params"]})
                pending_out_of_order.clear()
        elif method == "test/never":
            continue
        elif method == "thread/list":
            active_cwd = params.get("cwd", active_cwd)
            active_thread = thread(active_cwd)
            send(
                {
                    "id": request_id,
                    "result": {
                        "data": [active_thread],
                        "nextCursor": None,
                        "backwardsCursor": None,
                    },
                }
            )
        elif method == "thread/read":
            send({"id": request_id, "result": {"thread": active_thread}})
        elif method in {"thread/start", "thread/resume"}:
            active_cwd = params.get("cwd", active_cwd)
            active_thread = thread(active_cwd, params.get("threadId", active_thread["id"]))
            send(
                {
                    "id": request_id,
                    "result": {
                        "thread": active_thread,
                        "model": "gpt-5.2-codex",
                        "modelProvider": "openai",
                        "serviceTier": None,
                        "cwd": active_cwd,
                        "instructionSources": [],
                        "approvalPolicy": params.get("approvalPolicy", "on-request"),
                        "approvalsReviewer": "user",
                        "sandbox": {
                            "type": "workspaceWrite",
                            "writableRoots": [active_cwd],
                            "networkAccess": False,
                            "excludeTmpdirEnvVar": True,
                            "excludeSlashTmp": True,
                        },
                        "reasoningEffort": None,
                    },
                }
            )
        elif method == "turn/start":
            current_turn = turn()
            send({"id": request_id, "result": {"turn": current_turn}})
            send(
                {
                    "method": "turn/started",
                    "params": {"threadId": active_thread["id"], "turn": current_turn},
                }
            )
            send(
                {
                    "method": "turn/plan/updated",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": current_turn["id"],
                        "explanation": "Fixture plan",
                        "plan": [{"step": "Inspect", "status": "inProgress"}],
                    },
                }
            )
            send(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": current_turn["id"],
                        "itemId": "item-agent",
                        "delta": "Hello from the fake server.",
                    },
                }
            )
            send(
                {
                    "method": "item/reasoning/summaryTextDelta",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": current_turn["id"],
                        "itemId": "item-reasoning",
                        "summaryIndex": 0,
                        "delta": "Fixture reasoning summary.",
                    },
                }
            )
            command_item: dict[str, Any] = {
                "id": "item-command",
                "type": "commandExecution",
                "command": "git status --short",
                "cwd": active_cwd,
                "processId": None,
                "status": "inProgress",
                "commandActions": [{"type": "unknown", "command": "git status --short"}],
                "aggregatedOutput": "",
                "exitCode": None,
                "durationMs": None,
            }
            send(
                {
                    "method": "item/started",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": current_turn["id"],
                        "item": command_item,
                    },
                }
            )
            send(
                {
                    "method": "item/commandExecution/outputDelta",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": current_turn["id"],
                        "itemId": "item-command",
                        "delta": f"scanning {active_cwd}/README.md\n M README.md\n",
                    },
                }
            )
            send(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": current_turn["id"],
                        "tokenUsage": {
                            "total": {
                                "totalTokens": 20,
                                "inputTokens": 10,
                                "cachedInputTokens": 2,
                                "cacheWriteInputTokens": 0,
                                "outputTokens": 10,
                                "reasoningOutputTokens": 1,
                            },
                            "last": {
                                "totalTokens": 12,
                                "inputTokens": 6,
                                "cachedInputTokens": 2,
                                "cacheWriteInputTokens": 0,
                                "outputTokens": 6,
                                "reasoningOutputTokens": 1,
                            },
                            "modelContextWindow": 1000,
                        },
                    },
                }
            )
            send(
                {
                    "method": "item/commandExecution/requestApproval",
                    "id": 900,
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": current_turn["id"],
                        "itemId": "item-command",
                        "startedAtMs": 1_700_000_002_000,
                        "environmentId": None,
                        "reason": "Fixture approval",
                        "command": "git status --short",
                        "cwd": active_cwd,
                        "commandActions": [{"type": "unknown", "command": "git status --short"}],
                    },
                }
            )
        elif request_id == 900:
            command_item = {
                "id": "item-command",
                "type": "commandExecution",
                "command": "git status --short",
                "cwd": active_cwd,
                "processId": "fixture-process",
                "status": "completed",
                "commandActions": [{"type": "unknown", "command": "git status --short"}],
                "aggregatedOutput": " M README.md\n",
                "exitCode": 0,
                "durationMs": 12,
            }
            file_item: dict[str, Any] = {
                "id": "item-file",
                "type": "fileChange",
                "changes": [
                    {
                        "path": str(Path(active_cwd) / "README.md"),
                        "kind": "update",
                        "diff": "+fixture",
                    }
                ],
                "status": "completed",
            }
            send(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": "0198f000-0000-7000-8000-000000000002",
                        "item": command_item,
                    },
                }
            )
            send(
                {
                    "method": "item/started",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": "0198f000-0000-7000-8000-000000000002",
                        "item": {**file_item, "status": "inProgress"},
                    },
                }
            )
            send(
                {
                    "method": "item/fileChange/patchUpdated",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": "0198f000-0000-7000-8000-000000000002",
                        "itemId": "item-file",
                        "changes": file_item["changes"],
                    },
                }
            )
            send(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": active_thread["id"],
                        "turnId": "0198f000-0000-7000-8000-000000000002",
                        "item": file_item,
                    },
                }
            )
            completed = turn("completed", [command_item, file_item])
            send(
                {
                    "method": "serverRequest/resolved",
                    "params": {"threadId": active_thread["id"], "requestId": 900},
                }
            )
            send(
                {
                    "method": "turn/completed",
                    "params": {"threadId": active_thread["id"], "turn": completed},
                }
            )
        elif method == "turn/interrupt":
            send({"id": request_id, "result": {}})
            send(
                {
                    "method": "turn/completed",
                    "params": {"threadId": active_thread["id"], "turn": turn("interrupted")},
                }
            )
        elif method == "account/rateLimits/read":
            send(
                {
                    "id": request_id,
                    "result": {
                        "rateLimits": {
                            "limitId": "codex",
                            "limitName": "Codex",
                            "primary": {
                                "usedPercent": 25.0,
                                "windowDurationMins": 300,
                                "resetsAt": 1_900_000_000,
                            },
                            "secondary": None,
                            "credits": {"hasCredits": True, "unlimited": False, "balance": "10"},
                            "individualLimit": None,
                            "spendControlReached": False,
                            "planType": "plus",
                            "rateLimitReachedType": None,
                        },
                        "rateLimitsByLimitId": None,
                        "rateLimitResetCredits": None,
                    },
                }
            )
        else:
            send({"id": request_id, "error": {"code": -32601, "message": "unknown fixture method"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
