import os
import sys
import shutil
import requests
import tarfile
import yaml
import stat
from git import Repo, GitCommandError

# ---- Helper functions ----

def home_path(target_path):
    target_path = target_path.replace("~", os.path.expanduser("~"))
    if target_path[0] != "/":
        return os.path.join(os.path.expanduser("~"), target_path)
    return target_path

def print_status(idx, total, path, status, msg):
    percent = int(100 * (idx + 1) / total)
    color = {"ok": "\033[92m●\033[0m", "fail": "\033[91m✗\033[0m", "skip": "\033[94m◦\033[0m"}.get(status, "")
    print(f"{percent:3}% [{color}] {path} - {msg}")

def fetch_raw_file(repo_url, branch, rel_path, dest):
    raw_base = repo_url.rstrip("/").replace("github.com/", "raw.githubusercontent.com/")
    raw_url = f"{raw_base}/{branch}/{rel_path.lstrip('/')}"
    try:
        r = requests.get(raw_url, timeout=20)
        r.raise_for_status()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(r.content)
        return True, None
    except Exception as e:
        return False, str(e)

def fetch_from_git(url, target, branch="main"):
    try:
        if os.path.exists(target):
            shutil.rmtree(target)
        Repo.clone_from(url, target, depth=1, branch=branch)
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

    for idx, entry in enumerate(files):
        path = entry["path"]
        source = entry.get("source", "repo")
        abs_path = home_path(path)
        # Remove if exists (unless it's git but same path)
        if source == "git":
            if os.path.exists(abs_path):
                shutil.rmtree(abs_path)
        elif os.path.isdir(abs_path) or os.path.isfile(abs_path):
            if os.path.isdir(abs_path):
                shutil.rmtree(abs_path)
            else:
                os.remove(abs_path)

        if source == "repo":
            ok, err = fetch_raw_file(repo_url, branch, path.replace(os.path.expanduser("~") + "/", ""), abs_path)
            msg = "fetched from repo" if ok else f"FAILED: {err}"
        elif source == "git":
            branch_override = entry.get("branch") or "main"
            url = entry["url"]
            ok, err = fetch_from_git(url, abs_path, branch_override)
            msg = f"cloned {url}@{branch_override}" if ok else f"FAILED: {err}"
        elif source == "external":
            ok, err = fetch_from_external(entry, abs_path)
            msg = f"downloaded {entry['url']}" if ok else f"FAILED: {err}"
        else:
            ok, err = False, f"Unknown source: {source}"
            msg = f"FAILED: {err}"

        if ok and entry.get("exec", False):
            try:
                # Add execute permissions for user, group, and others (like chmod +x)
                current_st = os.stat(abs_path)
                os.chmod(abs_path, current_st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                msg += " (executable)"
            except Exception as e:
                # If chmod fails, mark the entire operation as a failure
                ok = False
                msg = f"FAILED: chmod failed - {e}"

        print_status(idx, total, path, "ok" if ok else "fail", msg)
        if ok:
            ok_count += 1

    print(f"\n\033[96mDone:\033[0m {ok_count}/{total} successful, {total-ok_count} failed.\n")

if __name__ == "__main__":
    main()
