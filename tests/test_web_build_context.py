"""Check external CSS dependencies against the real Docker stage. FubukiHanasaki."""

import posixpath
from pathlib import Path
import re
import shlex


def test_console_relative_css_imports_are_in_frontend_image_stage():
    root = Path(__file__).resolve().parents[1]
    directory = "/"
    files = {}
    stages = 0
    for line in (root / "deploy/Dockerfile").read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        instruction, *args = parts
        if instruction == "FROM":
            stages += 1
            if stages > 1:
                break
        elif instruction == "WORKDIR":
            directory = posixpath.normpath(posixpath.join(directory, args[0]))
        elif instruction == "COPY":
            destination = posixpath.normpath(posixpath.join(directory, args[-1]))
            for pattern in args[:-1]:
                for source in root.glob(pattern):
                    if source.is_dir():
                        for entry in source.rglob("*"):
                            if entry.is_file() and not {
                                "node_modules",
                                "__pycache__",
                            }.intersection(entry.relative_to(source).parts):
                                target = posixpath.join(
                                    destination, entry.relative_to(source).as_posix()
                                )
                                files[target] = entry
                    else:
                        target = (
                            posixpath.join(destination, source.name)
                            if args[-1].endswith(("/", "."))
                            else destination
                        )
                        files[target] = source
    dependencies = []
    for target, source in files.items():
        if source.suffix != ".css":
            continue
        for reference in re.findall(
            r"""@import\s+(?:url\(\s*)?["']([^"']+)["']""", source.read_text()
        ):
            if reference.startswith("."):
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(target), reference)
                )
                dependencies.append((target, resolved))
    assert dependencies, "No local CSS dependency was checked"
    for target, dependency in dependencies:
        assert dependency in files, (
            f"Docker frontend stage misses {dependency}, imported by {target}"
        )
