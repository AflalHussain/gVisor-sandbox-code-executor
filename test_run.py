import asyncio
from sandbox_engine import SandboxEngine

async def main():
    engine = SandboxEngine()

    # Case A: Execute Untrusted Python Script with dynamic user-approved PIP library installation
    untrusted_python_code = '''import requests\nprint("Pinging external API from gVisor container...")\nres = requests.get("https://github.com", timeout=5)\nprint(f"Network Status: {res.status_code}")'''
    print("--- Executing Python Task ---")
    print(untrusted_python_code)
    result_a = await engine.run_code_async(
        code=untrusted_python_code,
        run_type="python",
        allowed_pip=["httpx"] # User explicitly allowed this installation
    )
    print(json.dumps(result_a, indent=2))


    # Case B: Execute Untrusted Shell Script payload
    untrusted_shell_code = """
echo "Listing local execution directory:"
pwd
echo "Checking kernel architecture version:"
pwd
"""
    print("\n--- Executing Shell Task ---")
    result_b = await engine.run_code_async(
        code=untrusted_shell_code,
        run_type="shell"
    )
    print(json.dumps(result_b, indent=2))

if __name__ == "__main__":
    import json
    asyncio.run(main())

