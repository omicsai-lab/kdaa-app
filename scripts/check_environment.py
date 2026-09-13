"""Read-only prerequisites check. Does not install anything or change system settings."""
import platform
import shutil
import subprocess

def main():
    print(f"OS: {platform.system()} {platform.release()} | CPU: {platform.machine()}")
    for command in [["git", "--version"], ["docker", "--version"], ["docker", "compose", "version"], ["docker", "info", "--format", "{{.ServerVersion}}"]]:
        print("\n$ " + " ".join(command))
        if not shutil.which(command[0]):
            print("MISSING — install or start this prerequisite with the user's permission.")
            continue
        result = subprocess.run(command, text=True, capture_output=True)
        print((result.stdout or result.stderr).strip())
        print(f"exit={result.returncode}")
    print("\nHost Python/Node are optional for the Docker startup path. No AWS login is needed.")

if __name__ == "__main__":
    main()
