"""Commands a lent session may run without asking: the kinds the user allowed before in that project, and only
when nothing in the command reaches past the project (credentials, the network, paths outside it, other programs
started from inside it, publishing or installing). What reaches past it is told by rules read off the command's
own text, never by a model, so a session cannot talk its way past them."""

from __future__ import annotations

import re
import shlex
from typing import Any, Callable, Sequence

from .ports import StateStore
from .language import Language

AUTO = "permissions:auto_commands"  # whether learned commands are allowed without asking (on unless turned off)

# what in a command always comes to the user, learned or not: why, and how it shows in the text
REACHES = (
    ("碰到密碼或金鑰", re.compile(r"\bsecurity\b|keychain|\.ssh\b|\.aws\b|\.gnupg|\.netrc|id_rsa|id_ed25519|"
                              r"\btoken\b|secret|passw|\.env\b|credential|environ|getenv|printenv|^\s*env\b|\bset\s*$", re.I | re.M)),
    ("連網路", re.compile(r"\bcurl\b|\bwget\b|https?://|\bssh\b|\bscp\b|\brsync\b|\bnc\b|\bsocket\b|urllib|"
                       r"\brequests\b|httpx|aiohttp|http\.client|\bftp", re.I)),
    ("用到專案以外的路徑", re.compile(r"(?:^|[\s'\"=(,:<>])(?:/(?!dev/null\b)|~|\$HOME|\$\{HOME\}|\.\./)|Path\.home|expanduser|"
                                r"\.parents?\b|getcwd|abspath|realpath|dirname|os\.sep|(?:^|[;&|]\s*)cd\s*(?:$|[;&|])", re.M)),
    ("寫法看不清楚在做什麼", re.compile(r"\bchr\s*\(|\\x[0-9a-fA-F]{2}|base64|codecs|fromhex|getattr|__\w+__|ctypes|marshal|"
                                 r"pickle|\bcompile\s*\(|globals\s*\(|\[::-1\]")),
    ("從裡面再啟動別的程式", re.compile(r"subprocess|os\.system|os\.popen|os\.exec|\bpty\b|__import__|importlib|"
                               r"\beval\s*\(|\bexec\s*\(|osascript|\b(?:ba|z)?sh\s+-c\b|\bsudo\b|\bxargs\b|`|\$\(")),
    ("會發布、安裝或刪除", re.compile(r"git\s+(?:push|remote|config)|\brm\s+-\w*[rf]|\bpip3?\s+install|"
                              r"\b(?:npm|pnpm|yarn)\s+(?:i|install|add)\b|\bbrew\b|\buv\s+(?:add|pip)|\bchmod\b|\bchown\b")),
)

SUBCOMMANDS = ("git", "uv", "npm", "pnpm", "yarn", "cargo", "go", "docker")  # programs whose first word is what they do
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


INTERPRETERS = {"python", "python3", "node", "deno", "bun", "ruby", "perl", "php", "bash", "sh", "zsh", "fish", "blender",
                "osascript", "lua", "Rscript", "julia"}
RUNS_SCRIPTS = re.compile(r"^(?:make|just|npx|bunx|tox|nox)$")  # programs that run code kept in the project
INLINE = {"-c", "-e", "--eval", "--python-expr"}  # flags whose next word is the code itself, there to be read


def reaches(command: str) -> str | None:
    """Why the command reaches past the project, or None when nothing in it does (read off all of its text, the
    scripts it is fed included). Code kept in a file is not there to read, so a command that runs one always asks."""
    why = next((why for why, pattern in REACHES if pattern.search(command)), None)
    return why or next((RUNS_FILE for segment in segments(command) if runs_file(segment)), None)


RUNS_FILE = "跑的是檔案裡的程式(看不到內容)"


def runs_file(segment: str) -> bool:
    """Whether a command runs code from a file: an interpreter given one (not its standard input or code written
    out after -c), a program by its path, or a tool that runs the project's own scripts."""
    try:
        words = shlex.split(segment.replace("<<", " "))
    except ValueError:
        words = segment.split()
    if not words:
        return False
    program, rest = words[0], words[1:]
    if "/" in program or RUNS_SCRIPTS.match(program) or program in ("npm", "pnpm", "yarn", "uv") and rest[:1] in (["run"], ["exec"], ["x"]):
        return True
    if program.rsplit("/", 1)[-1] not in INTERPRETERS:
        return False
    skip = False
    for word in rest:
        if skip:
            return False  # the code is right here, after -c
        if word in INLINE:
            skip = True
        elif word in ("-", "--"):
            return False  # its standard input: the script it is fed, read with the command
        elif not word.startswith("-"):
            return True
    return False


def segments(command: str) -> list[str]:
    """The commands it runs, one by one: split where the shell would run the next (&&, ||, ;, |, a new line) outside
    quotes, with the text fed to a command by a here-document taken out (it is that command's input, not commands)."""
    out, cur, quote, i = [], [], "", 0
    feeds: list[str] = []  # here-documents opened on this line, read once the line ends
    while i < len(command):
        c = command[i]
        if quote:
            if c == quote:
                quote = ""
            elif c == "\\" and quote == '"':
                cur.append(c)
                i += 1
                c = command[i] if i < len(command) else ""
            cur.append(c)
            i += 1
            continue
        if c in "'\"":
            quote = c
            cur.append(c)
            i += 1
            continue
        if m := HEREDOC.match(command, i):
            feeds.append(m.group(2))
            cur.append("<<")
            i = m.end()
            continue
        two = command[i:i + 2]
        if two in ("&&", "||") or c in ";|\n":
            out.append("".join(cur))
            cur = []
            i += 2 if two in ("&&", "||") else 1
            if c == "\n" and two not in ("&&", "||"):
                for word in feeds:  # skip each here-document's lines, up to its closing word
                    end = re.compile(rf"^[ \t]*{re.escape(word)}[ \t]*$", re.M).search(command, i)
                    i = end.end() + 1 if end else len(command)
                feeds = []
            continue
        cur.append(c)
        i += 1
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def shape(command: str) -> str:
    """The kind of command it is, the same for each run of it: each program it runs (with the flags it gives it,
    and what it does for programs like git), their arguments, the scripts they are fed and the messages they write
    left out. `python3 -I - <<'EOF' … EOF` → "python3 -I - <<"; `git add a b && git commit -m "…"` → "git add && git commit"."""
    parts = []
    for segment in segments(command):
        fed = segment.endswith("<<") or " << " in segment
        try:
            words = shlex.split(segment.replace("<<", " "), comments=False)
        except ValueError:
            words = segment.replace("<<", " ").split()
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1]
        rest = words[1:]
        if program in SUBCOMMANDS:
            what = next((w for w in rest if not w.startswith("-")), "")
            part = f"{program} {what}".strip()
        else:
            part = " ".join([program, *(re.sub(r"^-\d+$", "-N", w) for w in rest if w.startswith("-"))])  # tail -15, tail -25: one kind
        parts.append(part + (" <<" if fed else ""))
    return " && ".join(parts)


class CommandTrust:
    def __init__(self, store: StateStore, decided: Callable[[], Sequence[dict[str, Any]]], *, words: Language):
        """`decided()`: the permission requests asked so far, oldest first (decided or not), as the store keeps them."""
        self.words = words
        self._store = store
        self._decided = decided

    def on(self) -> bool:
        return self._store.get_state(AUTO) is not False

    def turn(self, on: bool) -> bool:
        self._store.set_state(AUTO, bool(on))
        return self.on()

    def allows(self, project: str | None, command: str) -> bool:
        """Whether a command can run without asking: learning is on, nothing in it reaches past the project, and the
        last time the user decided on a command of its kind in this project, they allowed it."""
        if not self.on() or not project or not command.strip() or reaches(command) is not None:
            return False
        kind = shape(command)
        last = None
        for row in self._decided():
            if (row.get("project") == project and row.get("kind") == "run_command" and row.get("by") == "user"
                    and row.get("decision") in ("allow", "deny") and shape(str(row.get("input_summary") or "")) == kind):
                last = row["decision"]
        return last == "allow"
