#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find work in Lichii forks that upstream nx2 does not have.

    fork_scan.py scan [--days 3] [--max-forks 400] --out <dir>
    fork_scan.py rank <dir> [--top 40]

`scan` lists the forks of zortos293/Lichii pushed in the last --days, compares each recently
pushed branch with upstream nx2 through the GitHub compare API (read-only, through `gh api`) and
writes one JSON line per branch that is ahead: commits (sha, author, date, subject) and files.
`rank` scores those branches by how much of their unique work touches the emulation hot paths and
names Ocarina of Time or performance, and prints the best ones. Patch equivalence with the local
tree is a separate step: fetch the branch and use `git cherry` / `git range-diff`.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

UPSTREAM = "zortos293/Lichii"
BASE = "nx2"
HOT = re.compile(r"src/(video_core|core/hle/service/(nvdrv|nvnflinger|vi)|core/(arm|memory|"
                 r"core_timing|cpu_manager)|common/(host_memory|page_table|thread)|"
                 r"shader_recompiler|dynarmic)")
WORDS = re.compile(r"ocarina|\boot\b|perf|fps|faster|cheaper|stutter|fence|semaphore|split|"
                   r"drain|wait|draw|report|query|descriptor|pipeline|uniform|texture cache|"
                   r"buffer cache|jit|dynarmic|fastmem|scheduler|pacing|upload|download", re.I)


def gh(path, *fields):
    done = subprocess.run(["gh", "api", "-X", "GET", path, *fields], text=True,
                          capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"GitHub request failed for {path}: {done.stderr.strip()}")
    return json.loads(done.stdout)


def pages(path, **fields):
    """GitHub list endpoints can have more than 100 entries."""
    page = 1
    while True:
        params = [part for key, value in fields.items() for part in ("-f", f"{key}={value}")]
        batch = gh(path, *params, "-f", "per_page=100", "-f", f"page={page}")
        yield from batch
        if len(batch) < 100:
            break
        page += 1


def forks(since):
    found = [fork for fork in pages(f"repos/{UPSTREAM}/forks", sort="newest")
             if fork["pushed_at"] >= since]
    return sorted(found, key=lambda fork: fork["pushed_at"], reverse=True)


def scan(args):
    os.makedirs(args.out, exist_ok=True)
    since = (datetime.datetime.now(datetime.timezone.utc) -
             datetime.timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    listed = forks(since)[:args.max_forks]
    print(f"{len(listed)} forks pushed since {since}", file=sys.stderr)
    base_sha = gh(f"repos/{UPSTREAM}/commits/{BASE}")["sha"]
    print(f"Comparing with {base_sha}; failure leaves an incomplete scan", file=sys.stderr)
    seen_heads = set()
    with open(os.path.join(args.out, "branches.jsonl"), "w", encoding="utf-8") as out:
        for index, fork in enumerate(listed):
            name = fork["full_name"]
            branches = pages(f"repos/{name}/branches")
            for branch in branches:
                head = branch["commit"]["sha"]
                if head in seen_heads:
                    continue
                compare_path = f"repos/{UPSTREAM}/compare/{base_sha}...{head}"
                compared = gh(compare_path, "-f", "per_page=100", "-f", "page=1")
                seen_heads.add(head)
                if compared.get("ahead_by", 0) == 0:
                    continue
                all_commits = list(compared.get("commits", []))
                page = 2
                while len(all_commits) < compared["total_commits"]:
                    more = gh(compare_path, "-f", "per_page=100", "-f", f"page={page}")
                    if not more.get("commits"):
                        raise RuntimeError(f"Incomplete commit comparison for {name}:{branch['name']}")
                    all_commits.extend(more["commits"])
                    page += 1
                commits = [{"sha": c["sha"],
                            "author": (c["commit"]["author"] or {}).get("name", ""),
                            "date": (c["commit"]["author"] or {}).get("date", ""),
                            "subject": c["commit"]["message"].split("\n", 1)[0][:160]}
                           for c in all_commits]
                record = {"fork": name, "branch": branch["name"], "head": head, "upstream_sha": base_sha,
                          "ahead": compared["ahead_by"], "behind": compared["behind_by"],
                          "merge_base": compared["merge_base_commit"]["sha"],
                          "pushed": fork["pushed_at"], "commits": commits,
                          "files": [f["filename"] for f in compared.get("files", [])],
                          "files_may_be_truncated": len(compared.get("files", [])) >= 300}
                out.write(json.dumps(record) + "\n")
                out.flush()
            if index % 20 == 19:
                print(f"{index + 1}/{len(listed)} forks", file=sys.stderr)
    print(f"Completed {len(listed)} selected forks against {base_sha}", file=sys.stderr)


def score(record):
    newest = max((c["date"] for c in record["commits"]), default="")
    subjects = [c["subject"] for c in record["commits"] if not c["subject"].startswith("Merge")]
    hot = sum(1 for f in record["files"] if HOT.search(f))
    words = sum(1 for s in subjects if WORDS.search(s))
    ocarina = sum(1 for s in subjects if re.search(r"ocarina|\boot\b", s, re.I))
    return (2 * ocarina + words + min(hot, 20) / 4, newest)


def rank(args):
    records = []
    with open(os.path.join(args.dir, "branches.jsonl"), encoding="utf-8") as handle:
        for text in handle:
            record = json.loads(text)
            if 0 < record["ahead"] <= args.max_ahead:
                records.append(record)
    records.sort(key=score, reverse=True)
    for record in records[:args.top]:
        points, newest = score(record)
        print(f"{points:5.1f} {record['fork']}:{record['branch']} ahead {record['ahead']} "
              f"behind {record['behind']} base {record['merge_base']} newest {newest[:16]}")
        for commit in record["commits"][-args.subjects:]:
            if not commit["subject"].startswith("Merge"):
                print(f"        {commit['sha']} {commit['author'][:16]:<16} {commit['subject'][:110]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("scan")
    one.add_argument("--days", type=float, default=3)
    one.add_argument("--max-forks", type=int, default=400)
    one.add_argument("--out", required=True)
    one.set_defaults(func=scan)
    two = commands.add_parser("rank")
    two.add_argument("dir")
    two.add_argument("--top", type=int, default=40)
    two.add_argument("--max-ahead", type=int, default=150)
    two.add_argument("--subjects", type=int, default=6)
    two.set_defaults(func=rank)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
