import sys
import json
import subprocess

WORKSPACE_DIR = "/workspace"


def main():
    try:
        # Read the execution payload from stdin to avoid shell argument injection.
        input_data = sys.stdin.read()
        if not input_data.strip():
            print(json.dumps({"error": "Empty stdin payload context."}))
            sys.exit(1)

        payload = json.loads(input_data)
        code = payload.get("code", "")
        run_type = payload.get("type", "python")
        allowed_pip = payload.get("install_packages", [])
        
        # 1. Process user-approved dynamic pip installations
        if allowed_pip:
            for package in allowed_pip:
                # Install into the unprivileged user's site-packages so dynamic
                # dependencies work without requiring write access to the base image.
                install_result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--user",
                        "--quiet",
                        "--no-cache-dir",
                        package,
                    ],
                    check=False, capture_output=True
                )
                if install_result.returncode != 0:
                    stderr_text = install_result.stderr.decode("utf-8", errors="replace").strip()
                    stdout_text = install_result.stdout.decode("utf-8", errors="replace").strip()
                    print(
                        json.dumps(
                            {
                                "error": f"Failed to install requested package: {package}",
                                "package": package,
                                "pip_exit_code": install_result.returncode,
                                "pip_stdout": stdout_text,
                                "pip_stderr": stderr_text,
                            }
                        )
                    )
                    sys.exit(1)

        # 2. Execute the user payload securely in the isolated container space
        if run_type == "shell":
            result = subprocess.run(
                ["/bin/bash", "-c", code],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=WORKSPACE_DIR,
            )
        else:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=WORKSPACE_DIR,
            )

        # 3. Format the execution log to stdout
        output = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
        print(json.dumps(output))

    except subprocess.TimeoutExpired:
        print(json.dumps({"error": "Execution timed out (Max 30 seconds limit exceeded)."}))
    except Exception as e:
        print(json.dumps({"error": f"Internal Sandbox Crash: {str(e)}"}))

if __name__ == "__main__":
    main()
