#!/usr/bin/env python3
import os
import sys
import shutil
import yaml
import subprocess
import argparse
from pathlib import Path

# ---- CLI argument parsing ----
def parse_args():
    parser = argparse.ArgumentParser(
        description="Backup selected dotfiles from local system into a git-ready directory.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('--yaml-path', default='dotfiles_sync.yaml', help='YAML config [default: dotfiles_sync.yaml]')
    parser.add_argument('--default-repo', default=None, help='Git repo to use (overrides saved .repo)')
    parser.add_argument('--config-dir', default='~/.config/dotfiles-sync', help='Directory to store repo config [default: ~/.config/dotfiles-sync]')
    parser.add_argument('--prefix', default='~', help='Override home path (default: ~)')
    return parser.parse_args()

# ---- ANSI Colors ----
CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
RESET = "\033[0m"
BOLD = "\033[1m"


# ---- Save to .repo only if different ----
def save_repo_if_needed(repo_url, config_file):
    if os.path.exists(config_file):
        with open(config_file) as f:
            existing = f.read().strip()
        if existing == repo_url:
            return  # Skip saving (no change)
    with open(config_file, "w") as f:
        f.write(repo_url)
    print(f"{GREEN}✓ Saved repo URL to:{RESET} {config_file}")

# ---- Resolve repo URL (cli > file > prompt). Always return path to .repo too ----
def get_repo_url(config_dir, default_repo_override):
    config_dir = os.path.expanduser(config_dir)
    config_file = os.path.join(config_dir, ".repo")
    os.makedirs(config_dir, exist_ok=True)

    if default_repo_override:
        print(f"{CYAN}Using override repo URL from CLI:{RESET} {default_repo_override}")
        return default_repo_override, config_file

    if os.path.exists(config_file):
        with open(config_file) as f:
            repo = f.read().strip()
        print(f"{CYAN}Using saved repo URL:{RESET} {repo} (from {config_file})")
        return repo, config_file

    # Ask and save
    entered = input(f"{YELLOW}Enter git repo URL (default: https://github.com/sounddrill31/dotfiles): {RESET}").strip()
    repo = entered or "https://github.com/sounddrill31/dotfiles"
    save_repo_if_needed(repo, config_file)
    return repo, config_file

# ---- Prepare cloned or initialized repo ----
def prepare_repo(clone_path, repo_url, config_file):
    print(f"{CYAN}Preparing repo at:{RESET} {clone_path}")
    if os.path.exists(clone_path):
        print(f"{YELLOW}🧹 Clearing previous contents...{RESET}")
        shutil.rmtree(clone_path)
    try:
        subprocess.run(["git", "clone", repo_url, clone_path], check=True)
        print(f"{GREEN}✅ Repo cloned successfully!{RESET}")
    except subprocess.CalledProcessError:
        print(f"{YELLOW}⚠️ Clone failed. Falling back to git init.{RESET}")
        os.makedirs(clone_path, exist_ok=True)
        subprocess.run(["git", "init"], cwd=clone_path)
    finally:
        save_repo_if_needed(repo_url, config_file)

# ---- Check if entry should be backed up ----
def is_from_repo(entry):
    return entry.get("source", "repo") == "repo"

# ---- Copy files from system to clone_path ----
def backup_dotfiles(entries, clone_path, prefix):
    resolved_prefix = os.path.expanduser(prefix)

    for entry in entries:
        if not is_from_repo(entry):
            continue

        rel_path = entry["path"].replace("~", "")
        src = os.path.join(resolved_prefix, rel_path)
        dest = os.path.join(clone_path, "home", rel_path.lstrip("/"))

        if not os.path.exists(src):
            print(f"{YELLOW}✗ {src} does not exist — skipping{RESET}")
            continue

        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.isdir(src) and not os.path.islink(src):
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)
            print(f"{GREEN}✓{RESET} Copied {CYAN}{src}{RESET} → {dest}")
        except Exception as e:
            print(f"{YELLOW}✗ Failed to copy {src}: {e}{RESET}")

# ---- Final message ----
def finish_install(clone_path):
    print(f"\n{BOLD}Ready to push!{RESET}")
    print(f"{GREEN}✅ Backup complete in:{RESET} {clone_path}")
    print(f"Next: {CYAN}cd {clone_path} && git add . && git commit -m 'update dotfiles' && git push{RESET}")

# ---- Main function ----
def main():
    args = parse_args()
    yaml_path = args.yaml_path
    prefix = args.prefix
    resolved_prefix = os.path.expanduser(prefix)
    clone_path = os.path.expanduser("~/dotfiles-prep")

    print(f"{BOLD}{CYAN}✨ dotfiles BACKUP — Fancy Dotfile Exporter 🔧{RESET}\n")

    if prefix != "~":
        print(f"{CYAN}Using prefix as {resolved_prefix}{RESET}")
        if not os.path.exists(resolved_prefix):
            try:
                os.makedirs(resolved_prefix)
            except Exception as e:
                print(f"{YELLOW}Failed to create prefix path: {e}{RESET}")
                sys.exit(1)

    # Load YAML config
    if not os.path.exists(yaml_path):
        print(f"{YELLOW}⚠️ Could not find config file: {yaml_path}{RESET}")
        sys.exit(1)
    with open(yaml_path) as f:
        try:
            config = yaml.safe_load(f)
        except Exception as e:
            print(f"{YELLOW}YAML load failed: {e}{RESET}")
            sys.exit(1)

    entries = config.get("files", [])
    repo_url, config_file = get_repo_url(args.config_dir, args.default_repo)
    prepare_repo(clone_path, repo_url, config_file)
    backup_dotfiles(entries, clone_path, prefix)
    finish_install(clone_path)

if __name__ == "__main__":
    main()
