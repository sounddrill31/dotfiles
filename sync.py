import os
import sys
import shutil
import requests
import tarfile
import yaml
import stat
import subprocess
import json
from git import Repo, GitCommandError
import argparse

def parse_args():
    parser = argparse.ArgumentParser(
        description="⚙️  dotfiles SYNC — fetch and install your config files from repo/archive/git.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--prefix", default="~", help="Prefix target install path (normally ~)."
    )
    parser.add_argument(
        "--reinstall", action="store_true", help="Force reinstall of all items, ignoring saved state."
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="Default timeout in seconds for commands."
    )
    return parser.parse_args()


# ---- Helper functions ----


def home_path(target_path, prefix="~"):
    base = os.path.expanduser(prefix)

    # Apply to target_path
    if target_path.startswith("~"):
        target_path = target_path.replace("~", base, 1)
    elif not os.path.isabs(target_path):
        target_path = os.path.join(base, target_path)

    return target_path


def print_status(idx, total, path, status, msg):
    percent = int(100 * (idx + 1) / total)
    color = {"ok": "\033[92m●\033[0m", "fail": "\033[91m✗\033[0m", "skip": "\033[94m◦\033[0m"}.get(status, "")
    print(f"{percent:3}% [{color}] {path} - {msg}")


def check_if_sourced(script_path):
    """
    Checks if the given script is sourced in common shell config files.
    This is robust against different path formats (relative, absolute, ~),
    symlinks, and comments.
    """
    # Get the absolute, canonical path of the script we are looking for.
    canonical_target_path = os.path.realpath(os.path.expanduser(script_path))

    home = os.path.expanduser("~")
    shell_profiles = [".bashrc", ".zshrc", ".profile", ".bash_profile"]

    for profile_name in shell_profiles:
        profile_path = os.path.join(home, profile_name)
        if not os.path.exists(profile_path):
            continue

        try:
            with open(profile_path, "r") as f:
                for line in f:
                    line = line.strip()
                    # Ignore comments and empty lines
                    if not line or line.startswith("#"):
                        continue

                    parts = line.split()
                    # Check for 'source <path>' or '. <path>'
                    if len(parts) >= 2 and (parts[0] == "source" or parts[0] == "."):
                        path_from_file = parts[1]
                        
                        try:
                            # Normalize the path found in the config file
                            canonical_path_from_file = os.path.realpath(os.path.expanduser(path_from_file))
                            
                            # Compare the canonical paths
                            if canonical_target_path == canonical_path_from_file:
                                return True
                        except FileNotFoundError:
                            # The path in the rc file might be invalid or a variable; ignore it.
                            continue
        except Exception:
            # Ignore files we can't read or have encoding issues.
            continue

    return False


def print_autostart_instructions(script_path):
    """Prints instructions for the user to source the script."""
    user_friendly_path = script_path.replace(os.path.expanduser("~"), "~")
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    RESET = "\033[0m"
    
    print(f"\n{YELLOW}🔔 ACTION REQUIRED for {user_friendly_path}{RESET}")
    print("   To enable autostart, run the following command:")
    print(f"   {CYAN}echo 'source {user_friendly_path}' >> ~/.bashrc{RESET}")
    print("   (Note: You may need to change '.bashrc' to '.zshrc' or your shell's equivalent)")
    print("   Then, restart your shell or run the command manually to apply.")


def fetch_raw_file(repo_url, branch, rel_path, dest):
    raw_base = repo_url.rstrip("/").replace("github.com/", "raw.githubusercontent.com/")
    raw_url = f"{raw_base}/{branch}/home/{rel_path.lstrip('/')}"
    try:
        r = requests.get(raw_url, timeout=20)
        r.raise_for_status()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(r.content)
        return True, None
    except Exception as e:
        return False, str(e)

def fetch_from_git(url, target, branch="main", submodules=False):
    try:
        if os.path.exists(target) and os.path.isdir(os.path.join(target, ".git")):
            repo = Repo(target)
            repo.remotes.origin.fetch()
            repo.git.reset("--hard", f"origin/{branch}")
            if submodules:
                repo.submodule_update(init=True, recursive=True)
        else:
            if os.path.exists(target):
                shutil.rmtree(target)
            
            clone_args = {
                "depth": 1,
                "branch": branch
            }
            if submodules:
                clone_args["recurse_submodules"] = True

            Repo.clone_from(url, target, **clone_args)
        return True, None
    except GitCommandError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)

def fetch_from_external(entry, dest):
    url = entry["url"]
    typ = entry.get("type", "direct")
    try:
        r = requests.get(url, stream=(typ != "direct"), timeout=30)
        r.raise_for_status()
        # Handle tar archives
        if typ.startswith("tar"):
            tarfile_path = dest + ".tarfile"
            with open(tarfile_path, "wb") as tempf:
                for chunk in r.iter_content(chunk_size=8192):
                    tempf.write(chunk)
            with tarfile.open(tarfile_path, mode="r:xz" if "xz" in typ else "r:*") as tar:
                dir_name = entry.get("dir_name")
                extract_path = dest.rstrip("/")  # What we want as the target
                if os.path.exists(extract_path):
                    shutil.rmtree(extract_path)
                os.makedirs(os.path.dirname(extract_path), exist_ok=True)
                tar.extractall(os.path.dirname(extract_path))
                # If archive has a single top-level folder that matches dir_name, rename/move to correct dest
                if dir_name:
                    src_dir = os.path.join(os.path.dirname(extract_path), dir_name)
                    if src_dir != extract_path:
                        if os.path.exists(extract_path):
                            shutil.rmtree(extract_path)
                        shutil.move(src_dir, extract_path)
            os.remove(tarfile_path)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(r.content)
        return True, None
    except Exception as e:
        return False, str(e)

# ---- Main logic ----


def main():
    args = parse_args()
    prefix = args.prefix
    reinstall_forced = args.reinstall
    default_timeout = args.timeout
    resolved_prefix = os.path.expanduser(prefix)
    state_file = os.path.join(resolved_prefix, ".dotfiles_sync_state.json")

    if prefix != "~":
        print(f"\033[36mUsing prefix as {resolved_prefix}\033[0m")
    if not os.path.exists(resolved_prefix):
        try:
            os.makedirs(resolved_prefix)
        except Exception as e:
            print(f"\033[91mERROR: Failed to create prefix path {resolved_prefix}: {e}\033[0m")
            sys.exit(1)

    try:
        with open(state_file, "r") as f:
            installed_state = set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        installed_state = set()

    try:
        with open("dotfiles_sync.yaml") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"\033[91mFailed to read 'dotfiles_sync.yaml': {e}\033[0m")
        sys.exit(1)

    repo_url = config["repo"]
    branch = config.get("branch", "main")
    files = config.get("files", [])
    total = len(files)
    ok_count = 0
    skipped_count = 0
    autostart_instructions = []

    for idx, entry in enumerate(files):
        path = entry["path"]
        
        # Check if we should skip this entry
        if not reinstall_forced and entry.get("idempotent", False) and path in installed_state:
            print_status(idx, total, path, "skip", "already installed (idempotent)")
            skipped_count += 1
            ok_count += 1
            continue

        source = entry.get("source", "repo")
        abs_path = home_path(path,prefix)
        # Remove if exists, but not for git sources
        if source not in ["git", "git-with-submodules"]:
            if os.path.lexists(abs_path): # Use lexists for symlinks
                if os.path.isdir(abs_path) and not os.path.islink(abs_path):
                    shutil.rmtree(abs_path)
                else:
                    os.remove(abs_path)

        if source == "repo":
            repo_relative_path = path.replace("~/", "") # Assume paths are relative to home
            ok, err = fetch_raw_file(repo_url, branch, repo_relative_path, abs_path)
            msg = "fetched from repo" if ok else f"FAILED: {err}"
        elif source == "git":
            ok, err = fetch_from_git(entry["url"], abs_path, entry.get("branch", "main"))
            msg = f"cloned {entry['url']}" if ok else f"FAILED: {err}"
        elif source == "git-with-submodules":
            ok, err = fetch_from_git(entry["url"], abs_path, entry.get("branch", "main"), submodules=True)
            msg = f"cloned {entry['url']} with submodules" if ok else f"FAILED: {err}"
        elif source == "external":
            ok, err = fetch_from_external(entry, abs_path)
            msg = f"downloaded {entry['url']}" if ok else f"FAILED: {err}"
        else:
            ok, err, msg = False, f"Unknown source: {source}", f"FAILED: Unknown source"
        
        # Make executable if requested
        if ok and entry.get("exec", False):
            try:
                st = os.stat(abs_path)
                os.chmod(abs_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                msg += " (executable)"
            except Exception as e:
                ok, msg = False, f"FAILED: chmod failed - {e}"

        # Run post-install command(s) if specified
        if ok and "run-after" in entry:
            commands = entry["run-after"]
            if isinstance(commands, str):
                commands = [commands]

            # Determine the correct working directory for the command
            cwd = abs_path
            if os.path.isfile(abs_path):
                cwd = os.path.dirname(abs_path)

            # Determine the timeout for the command
            timeout_val = entry.get("timeout", default_timeout)
            if timeout_val == "none":
                timeout = None
            else:
                timeout = int(timeout_val)

            num_commands = len(commands)
            for i, command in enumerate(commands):
                try:
                    print_status(idx, total, path, "ok", f"running task {i+1}/{num_commands}: \033[33m{command}\033[0m...")
                    result = subprocess.run(
                        command,
                        shell=True,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=cwd
                    )
                    
                    # Show last few lines of output
                    output_lines = (result.stdout.strip() + "\n" + result.stderr.strip()).strip().split('\n')
                    last_lines = "\n".join(output_lines[-3:])
                    
                    msg += f" (ran: '{command}')"

                except subprocess.CalledProcessError as e:
                    ok = False
                    error_output = (e.stderr.strip() + "\n" + e.stdout.strip()).strip()
                    msg = f"FAILED on task {i+1}/{num_commands}: '{command}' (exit {e.returncode}). Output: {error_output}"
                    break # Stop on first failure
                except FileNotFoundError:
                    ok = False
                    msg = f"FAILED on task {i+1}/{num_commands}: command not found: '{command.split()[0]}'"
                    break # Stop on first failure
                except subprocess.TimeoutExpired:
                    ok = False
                    msg = f"FAILED on task {i+1}/{num_commands}: '{command}' timed out after {timeout} seconds"
                    break # Stop on first failure
                except Exception as e:
                    ok = False
                    msg = f"FAILED on task {i+1}/{num_commands}: '{command}' encountered an error: {e}"
                    break # Stop on first failure
            
            if ok:
                msg = f"all {num_commands} tasks completed"


        print_status(idx, total, path, "ok" if ok else "fail", msg)
        if ok:
            ok_count += 1
            installed_state.add(path)
            # --- Autostart check ---
            if entry.get("autostart", False):
                if not check_if_sourced(abs_path):
                    autostart_instructions.append(abs_path)
        else:
            installed_state.discard(path)

    # --- Save state ---
    try:
        with open(state_file, "w") as f:
            json.dump(list(installed_state), f, indent=2)
    except Exception as e:
        print(f"\n\033[91mWarning: Failed to save state file {state_file}: {e}\033[0m")

    print(f"\n\033[96mDone:\033[0m {ok_count}/{total} successful ({skipped_count} skipped), {total-ok_count} failed.")

    # --- Print all autostart instructions at the end ---
    if autostart_instructions:
        for script_path in autostart_instructions:
            print_autostart_instructions(script_path)
        print()

if __name__ == "__main__":
    main()
