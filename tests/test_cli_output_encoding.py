"""Text is UTF-8 on every platform, and the CLI never dies on output.

Windows reads and writes text in the ANSI code page (cp1252) unless told
otherwise; a pipe has no ✓/✗. A strict ASCII stream stands in for such a
pipe on every platform.
"""

import ast
import os
from pathlib import Path
import subprocess
import sys

import kindex


def _text_mode(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in (
            "os", "gzip", "tarfile", "zipfile", "webbrowser", "io", "codecs", "bz2", "lzma"):
        return False
    index = 1 if isinstance(func, ast.Name) else 0  # open(path, mode) / Path.open(mode)
    mode = next((k.value for k in call.keywords if k.arg == "mode"),
                call.args[index] if len(call.args) > index else ast.Constant("r"))
    return not (isinstance(mode, ast.Constant) and "b" in str(mode.value))


def test_kindex_names_the_encoding_of_every_text_file():
    package = Path(kindex.__file__).parent
    offenders = []
    for path in sorted(package.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_bytes())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in ("read_text", "write_text", "open"):
                continue
            if any(k.arg in ("encoding", None) for k in node.keywords):
                continue
            if name != "open" or _text_mode(node):
                offenders.append(f"{path.relative_to(package)}:{node.lineno} {name}")
    assert offenders == []


def test_an_unencodable_character_is_replaced_not_fatal():
    script = (
        "import sys\n"
        "from kindex import cli\n"
        "sys.argv = ['kin', '--help']\n"
        "try:\n"
        "    cli.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('\\u2717 done')\n"
    )
    env = dict(os.environ, PYTHONIOENCODING="ascii:strict")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == b"? done"
