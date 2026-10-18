"""Compare fresh eight-thread/eight-process batches, including the whole process tree.

Run: python -m tests.benchmark_render_comparison --repeats 3
The external sampler stays responsive even if drawing holds the worker's GIL.
RSS sums shared pages more than once; USS counts private resident pages only.
No network, production data or bot instance is used.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil


def measure(mode, all_help):
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "tests.benchmark_rendering",
        "--workers",
        "8",
        "--mode",
        mode,
    ]
    if all_help:
        command.append("--all-help")
    peak_rss = peak_uss = 0
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, env=environment, stdout=output, stderr=errors)
        parent = psutil.Process(process.pid)
        deadline = time.monotonic() + 180
        while process.poll() is None:
            total_rss = total_uss = 0
            try:
                children = [parent, *parent.children(recursive=True)]
            except psutil.Error:
                children = []
            for child in children:
                try:
                    memory = child.memory_full_info()
                    total_rss += memory.rss
                    total_uss += memory.uss
                except psutil.Error:
                    pass
            peak_rss = max(peak_rss, total_rss)
            peak_uss = max(peak_uss, total_uss)
            if time.monotonic() > deadline:
                for child in reversed(children):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
                process.wait()
                raise TimeoutError("Benchmark exceeded 180 seconds")
            time.sleep(0.02)
        output.seek(0)
        errors.seek(0)
        if process.returncode:
            return {
                "mode": mode,
                "all_help": all_help,
                "exit_code": process.returncode,
                "output": output.read().decode("utf-8", errors="replace")[-2000:],
                "error": errors.read().decode("utf-8", errors="replace")[-2000:],
            }
        result = json.loads(output.read())
        result["tree_peak_rss_mib"] = round(peak_rss / 1024**2, 1)
        result["tree_peak_uss_mib"] = round(peak_uss / 1024**2, 1)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("thread", "thread-locked", "process"),
        default=["thread", "thread-locked", "process"],
    )
    args = parser.parse_args()
    for repeat in range(args.repeats):
        # Alternate the order to reduce warm OS-cache/order effects.
        modes = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
        for all_help in (False, True):
            for mode in modes:
                print(json.dumps({"repeat": repeat + 1, **measure(mode, all_help)}), flush=True)


if __name__ == "__main__":
    main()
