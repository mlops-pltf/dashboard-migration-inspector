# /// script
# requires-python = ">=3.12"
# dependencies = ["runpod==1.12.0"]
# ///
"""A private, OpenAI-compatible LLM server on RunPod for testing the Inspector agents.

One GPU pod running vLLM (tool calling on, API key required). Standalone: nothing here is imported by the agents.

    export RUNPOD_API_KEY=...                  # RunPod console → Settings → API Keys
    uv run infra/runpod/llm_pod.py up          # create the pod, wait until the model answers, print .env lines
    uv run infra/runpod/llm_pod.py status      # state, uptime, cost per hour
    uv run infra/runpod/llm_pod.py env         # print the .env lines again
    uv run infra/runpod/llm_pod.py stop        # stop billing for the GPU (the volume with the weights stays)
    uv run infra/runpod/llm_pod.py start       # resume a stopped pod
    uv run infra/runpod/llm_pod.py down        # terminate: pod and volume gone, billing ends

A running pod bills per hour until `stop` or `down`. State (pod id, generated API key) is kept in
infra/runpod/.pod.json, which is gitignored.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import runpod

STATE = Path(__file__).with_name(".pod.json")
IMAGE = "vllm/vllm-openai:v0.11.0"
PORT = 8000
DEFAULT_MODEL = "Qwen/Qwen3-32B-AWQ"
# Tried in order until one has capacity. 48 GB is enough for a 32B AWQ model with a 32k context.
DEFAULT_GPUS = ["NVIDIA L40S", "NVIDIA RTX A6000", "NVIDIA A40", "NVIDIA RTX 6000 Ada Generation"]


def _load() -> dict:
    if not STATE.exists():
        sys.exit("No pod recorded. Run `up` first.")
    return json.loads(STATE.read_text())


def _save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))
    STATE.chmod(0o600)


def _base_url(pod_id: str) -> str:
    return f"https://{pod_id}-{PORT}.proxy.runpod.net/v1"


def _vllm_args(model: str, max_len: int) -> str:
    # RunPod passes this string as the container command; no quotes allowed (the SDK inlines it in GraphQL).
    return " ".join([
        f"--model {model}", f"--port {PORT}", f"--max-model-len {max_len}",
        "--gpu-memory-utilization 0.92", "--enable-auto-tool-choice", "--tool-call-parser hermes",
        "--reasoning-parser qwen3", "--download-dir /workspace/hf",
    ])


def _models(state: dict) -> list[str] | None:
    req = urllib.request.Request(f"{_base_url(state['pod_id'])}/models",
                                 headers={"Authorization": f"Bearer {state['api_key']}",
                                          # RunPod's Cloudflare proxy rejects the default Python-urllib agent (1010)
                                          "User-Agent": "inspector-llm-pod/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return [m["id"] for m in json.load(resp)["data"]]
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        return None


def _wait_ready(state: dict, timeout_s: int) -> None:
    print(f"Waiting for the model (first start downloads the weights, often 5-15 min; timeout {timeout_s}s) ...")
    start = time.time()
    while time.time() - start < timeout_s:
        if _models(state):
            print(f"Ready after {int(time.time() - start)}s.")
            return
        time.sleep(20)
        print(f"  ... {int(time.time() - start)}s")
    sys.exit("Not ready in time. The pod is still running (and billing): check its logs in the RunPod console, "
             "then `status`, or `down` to terminate.")


def _print_env(state: dict) -> None:
    model = f"hosted_vllm/{state['model']}"
    print("\nAdd to .env (one model serves both tiers):\n")
    print(f"INSPECTOR_LLM_MODEL_STRONG={model}")
    print(f"INSPECTOR_LLM_MODEL_FAST={model}")
    print(f"INSPECTOR_LLM_API_BASE={_base_url(state['pod_id'])}")
    print(f"INSPECTOR_LLM_API_KEY={state['api_key']}")


def up(args: argparse.Namespace) -> None:
    if STATE.exists():
        sys.exit(f"A pod is already recorded ({_load()['pod_id']}). Use `status`, `start` or `down` first.")
    api_key = secrets.token_hex(24)
    gpus = args.gpu or DEFAULT_GPUS
    pod = None
    for gpu in gpus:
        try:
            print(f"Requesting {gpu} ({args.cloud}) ...")
            pod = runpod.create_pod(
                name="inspector-llm", image_name=IMAGE, gpu_type_id=gpu, cloud_type=args.cloud,
                gpu_count=1, container_disk_in_gb=30, volume_in_gb=args.volume_gb, volume_mount_path="/workspace",
                ports=f"{PORT}/http", start_ssh=False, docker_args=_vllm_args(args.model, args.max_len),
                env={"VLLM_API_KEY": api_key, "HF_HOME": "/workspace/hf"},
            )
            break
        except Exception as exc:  # no capacity for this GPU type, try the next one
            print(f"  not available: {exc}")
    if not pod:
        sys.exit("No GPU from the list had capacity. Try later, `--cloud ALL`, or other `--gpu` types.")
    state = {"pod_id": pod["id"], "api_key": api_key, "model": args.model, "gpu": gpu}
    _save(state)
    print(f"Pod {pod['id']} created on {gpu}. Billing has started.")
    _wait_ready(state, args.timeout)
    _print_env(state)
    print("\nWhen done: `stop` (keeps the weights) or `down` (removes everything).")


def status(_: argparse.Namespace) -> None:
    state = _load()
    pod = runpod.get_pod(state["pod_id"])
    if not pod:
        sys.exit(f"Pod {state['pod_id']} not found (terminated?). Delete {STATE} to start over.")
    uptime = pod.get("uptimeSeconds") or 0
    print(f"pod {pod['id']}  {pod.get('desiredStatus')}  {state['gpu']}  ${pod.get('costPerHr')}/h  "
          f"uptime {uptime // 3600}h{uptime % 3600 // 60:02d}m")
    print(f"model {state['model']}: {'answering' if _models(state) else 'not answering'} at {_base_url(state['pod_id'])}")


def env(_: argparse.Namespace) -> None:
    _print_env(_load())


def stop(_: argparse.Namespace) -> None:
    state = _load()
    runpod.stop_pod(state["pod_id"])
    print(f"Pod {state['pod_id']} stopped. GPU billing ended; the volume is billed at storage rates until `down`.")


def start(args: argparse.Namespace) -> None:
    state = _load()
    runpod.resume_pod(state["pod_id"], gpu_count=1)
    print(f"Pod {state['pod_id']} resuming. Billing has started.")
    _wait_ready(state, args.timeout)
    _print_env(state)


def down(_: argparse.Namespace) -> None:
    state = _load()
    runpod.terminate_pod(state["pod_id"])
    STATE.unlink()
    print(f"Pod {state['pod_id']} terminated. Remove the INSPECTOR_LLM_* lines from .env.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_up = sub.add_parser("up", help="create the pod and wait until the model answers")
    p_up.add_argument("--model", default=DEFAULT_MODEL, help=f"Hugging Face model id (default {DEFAULT_MODEL})")
    p_up.add_argument("--gpu", action="append", help="GPU type id, repeatable, tried in order")
    p_up.add_argument("--cloud", default="ALL", choices=["ALL", "SECURE", "COMMUNITY"])
    p_up.add_argument("--max-len", type=int, default=32768, help="context length")
    p_up.add_argument("--volume-gb", type=int, default=60, help="volume for the weights")
    p_up.add_argument("--timeout", type=int, default=1800)
    p_start = sub.add_parser("start", help="resume a stopped pod")
    p_start.add_argument("--timeout", type=int, default=900)
    for name in ("status", "env", "stop", "down"):
        sub.add_parser(name)
    args = parser.parse_args()

    runpod.api_key = os.environ.get("RUNPOD_API_KEY") or sys.exit("Set RUNPOD_API_KEY.")
    {"up": up, "status": status, "env": env, "stop": stop, "start": start, "down": down}[args.cmd](args)


if __name__ == "__main__":
    main()
