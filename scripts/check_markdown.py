#!/usr/bin/env python3
"""Check and fix Markdown strong-emphasis markers used in this repository."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


EXCLUDED_DIRS = {".git", ".venv", "node_modules"}
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
BROKEN_QUOTE_RE = re.compile(r'\*\*([^*\n]+?)["”]([:：])')


@dataclass(frozen=True)
class Issue:
    path: Path
    line: int
    message: str


def markdown_files(inputs: list[Path]) -> list[Path]:
    """Return unique Markdown files below the supplied files/directories."""
    found: set[Path] = set()
    for item in inputs:
        if item.is_file() and item.suffix.lower() == ".md":
            found.add(item.resolve())
            continue
        if not item.is_dir():
            continue
        for path in item.rglob("*.md"):
            if not any(part in EXCLUDED_DIRS for part in path.parts):
                found.add(path.resolve())
    return sorted(found)


def delimiter_positions(line: str) -> list[int]:
    """Find unescaped ** delimiters outside inline code spans."""
    positions: list[int] = []
    inline_ticks = 0
    index = 0

    while index < len(line):
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if char == "`":
            end = index
            while end < len(line) and line[end] == "`":
                end += 1
            run = end - index
            if inline_ticks == 0:
                inline_ticks = run
            elif run == inline_ticks:
                inline_ticks = 0
            index = end
            continue
        if inline_ticks == 0 and line.startswith("**", index):
            positions.append(index)
            index += 2
            continue
        index += 1

    return positions


def is_word_character(char: str) -> bool:
    """Return whether a character can prevent a strong marker from closing."""
    return char.isalnum()


def repair_line(line: str, fix: bool) -> tuple[str, list[str], int]:
    """Check one prose line and optionally return its repaired form."""
    messages: list[str] = []
    fixes = 0
    ending = ""
    body = line
    if body.endswith("\n"):
        ending = "\n"
        body = body[:-1]

    broken_quotes = list(BROKEN_QUOTE_RE.finditer(body))
    if broken_quotes:
        messages.append("加粗结束标记被误写为引号")
        if fix:
            body, count = BROKEN_QUOTE_RE.subn(r"**\1**\2", body)
            fixes += count

    positions = delimiter_positions(body)
    if len(positions) % 2:
        messages.append("存在未配对的 ** 标记")
        if fix:
            body += "**"
            fixes += 1
            positions = delimiter_positions(body)

    if len(positions) % 2:
        return body + ending, messages, fixes

    rebuilt: list[str] = []
    cursor = 0
    changed_boundary = False
    changed_inner_space = False

    for opening, closing in zip(positions[0::2], positions[1::2]):
        prefix = body[cursor:opening]
        content = body[opening + 2 : closing]
        stripped = content.strip()

        if content != stripped:
            changed_inner_space = True

        if prefix and is_word_character(prefix[-1]):
            prefix += " "
            changed_boundary = True

        rebuilt.append(prefix)
        rebuilt.append(f"**{stripped}**")

        after = closing + 2
        next_char = body[after : after + 1]
        if (next_char and is_word_character(next_char)) or body.startswith("**", after):
            rebuilt.append(" ")
            changed_boundary = True
        cursor = after

    rebuilt.append(body[cursor:])

    if changed_inner_space:
        messages.append("** 内侧含有导致渲染失败的空格")
    if changed_boundary:
        messages.append("** 与相邻正文紧贴")

    if fix and (changed_inner_space or changed_boundary):
        new_body = "".join(rebuilt)
        if new_body != body:
            fixes += 1
            body = new_body

    return body + ending, messages, fixes


def process_file(path: Path, fix: bool) -> tuple[list[Issue], int, bool]:
    """Check one file, optionally write repairs, and return its findings."""
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    output: list[str] = []
    issues: list[Issue] = []
    fixes = 0
    fence: tuple[str, int, int] | None = None

    for number, line in enumerate(lines, start=1):
        fence_match = FENCE_RE.match(line)
        if fence is not None:
            output.append(line)
            if fence_match:
                marker = fence_match.group(1)
                if marker[0] == fence[0] and len(marker) >= fence[1]:
                    fence = None
            continue

        if fence_match:
            marker = fence_match.group(1)
            fence = (marker[0], len(marker), number)
            output.append(line)
            continue

        repaired, messages, line_fixes = repair_line(line, fix)
        output.append(repaired)
        fixes += line_fixes
        issues.extend(Issue(path, number, message) for message in messages)

    if fence is not None:
        issues.append(Issue(path, fence[2], "代码围栏未闭合"))

    updated = "".join(output)
    changed = updated != original
    if fix and changed:
        path.write_text(updated, encoding="utf-8")

    return issues, fixes, changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查 Markdown 加粗标记，并可自动修复安全、明确的问题。"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="要检查的 .md 文件或目录；默认检查仓库根目录。",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="自动修复；省略时只检查，不修改文件。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository_root = Path(__file__).resolve().parent.parent
    inputs = args.paths or [repository_root]
    files = markdown_files(inputs)

    if not files:
        print("未找到 Markdown 文件。", file=sys.stderr)
        return 2

    all_issues: list[Issue] = []
    total_fixes = 0
    changed_files = 0

    for path in files:
        issues, fixes, changed = process_file(path, args.fix)
        all_issues.extend(issues)
        total_fixes += fixes
        changed_files += int(changed)

    if args.fix:
        # Recheck after writing so the exit code reflects the final state.
        remaining: list[Issue] = []
        for path in files:
            issues, _, _ = process_file(path, False)
            remaining.extend(issues)
        if remaining:
            for issue in remaining:
                print(f"{issue.path}:{issue.line}: {issue.message}")
            print(
                f"已修改 {changed_files} 个文件，执行 {total_fixes} 项修复；"
                f"仍有 {len(remaining)} 个问题。",
                file=sys.stderr,
            )
            return 1
        print(
            f"检查 {len(files)} 个 Markdown 文件；"
            f"已修改 {changed_files} 个文件，执行 {total_fixes} 项修复。"
        )
        return 0

    if all_issues:
        for issue in all_issues:
            print(f"{issue.path}:{issue.line}: {issue.message}")
        print(
            f"检查 {len(files)} 个 Markdown 文件，发现 {len(all_issues)} 个问题。"
            "运行时添加 --fix 可自动修复。",
            file=sys.stderr,
        )
        return 1

    print(f"检查 {len(files)} 个 Markdown 文件，未发现加粗格式问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
