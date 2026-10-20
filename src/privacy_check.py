"""Stop a commit that would put personal details into this public repository.

The details to look for live in the git-ignored data/private_terms.txt, one per
line (lines starting with # are ignored), so the list itself never reaches
GitHub. Each term is matched case-insensitively as a whole word or number.

Installed as the pre-commit and commit-msg hooks by Dashboard.command:

    python3 src/privacy_check.py --staged         # added lines and file names
    python3 src/privacy_check.py --message FILE   # the commit message
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
TERMS_FILE = ROOT / "data" / "private_terms.txt"


def load_terms(path: Path = TERMS_FILE) -> List[str]:
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def find_terms(lines: Iterable[str], terms: List[str]) -> List[str]:
    """Return the terms that appear in any line, matched as whole words."""
    patterns = {term: re.compile(rf"(?<![\w]){re.escape(term)}(?![\w])", re.I) for term in terms}
    found = []
    for line in lines:
        for term, pattern in patterns.items():
            if term not in found and pattern.search(line):
                found.append(term)
    return found


def staged_lines() -> List[str]:
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    diff = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--no-color"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
    return names + added


def mask(term: str) -> str:
    """Show enough of a term to recognise it without printing it whole."""
    return term[:2] + "…" if len(term) > 3 else "…"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="Check the staged changes")
    group.add_argument("--message", help="Check a commit message file")
    args = parser.parse_args()

    terms = load_terms()
    if not terms:
        return 0
    lines = staged_lines() if args.staged else Path(args.message).read_text(encoding="utf-8").splitlines()
    found = find_terms(lines, terms)
    if not found:
        return 0
    where = "staged changes" if args.staged else "commit message"
    print(
        f"Commit stopped: the {where} contain {len(found)} personal detail(s) from "
        f"data/private_terms.txt ({', '.join(mask(term) for term in found)}). "
        "Remove them and commit again.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
