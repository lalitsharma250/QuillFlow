"""
docker/entrypoint.py

Cross-platform startup script for QuillFlow.
Works on Windows line endings or Linux.
"""

import os
import subprocess
import sys


def main():
    mode = os.environ.get("STARTUP_MODE", "combined").lower()
    port = os.environ.get("PORT", "8000")

    print(f"Starting QuillFlow in mode: {mode}")

    if mode == "api":
        # API only
        cmd = [
            "uvicorn", "app.main:create_app",
            "--factory",
            "--host", "0.0.0.0",
            "--port", port,
            "--workers", "1",
        ]
        os.execvp("uvicorn", cmd)

    elif mode == "worker":
        # Worker only
        cmd = ["arq", "app.workers.settings.WorkerSettings"]
        os.execvp("arq", cmd)

    elif mode == "combined":
        # Start worker in background
        worker_proc = subprocess.Popen(
            ["arq", "app.workers.settings.WorkerSettings"]
        )
        print(f"Worker started (pid={worker_proc.pid})")

        # Start API in foreground
        try:
            subprocess.run(
                [
                    "uvicorn", "app.main:create_app",
                    "--factory",
                    "--host", "0.0.0.0",
                    "--port", port,
                    "--workers", "1",
                ],
                check=True,
            )
        except KeyboardInterrupt:
            pass
        finally:
            print("API exited, stopping worker...")
            worker_proc.terminate()
            worker_proc.wait(timeout=10)

    else:
        print(f"ERROR: Unknown STARTUP_MODE: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()