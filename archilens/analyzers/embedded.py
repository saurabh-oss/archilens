"""
Code embedded in another file format.

Vue and Svelte single-file components keep their JavaScript or TypeScript in
``<script>`` blocks beside a template and styles. ``script_source`` returns just
that code, with every other line blanked rather than removed, so line numbers in
the result still point at the right line of the original file.
"""

from __future__ import annotations

import re

#: Languages whose files hold their code in <script> blocks.
SCRIPT_HOSTS = frozenset({"vue", "svelte"})

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.DOTALL | re.IGNORECASE)
_TYPESCRIPT = re.compile(r"""\blang\s*=\s*["']?(?:ts|tsx|typescript)\b""", re.IGNORECASE)


def script_source(content: str, language: str) -> tuple[str, str]:
    """
    The code to parse in a file, and the language to parse it as.

    For a Vue or Svelte file: its ``<script>`` blocks (a component may have two, a
    module-level one and a ``setup`` or instance one), as TypeScript if any block
    says ``lang="ts"`` and JavaScript otherwise. For any other language, the
    content and language unchanged.
    """
    if language not in SCRIPT_HOSTS:
        return content, language

    parts: list[str] = []
    position = 0
    typescript = False
    for match in _SCRIPT.finditer(content):
        typescript = typescript or bool(_TYPESCRIPT.search(match.group(1)))
        parts.append("\n" * content.count("\n", position, match.start(2)))  # keep the line numbers
        parts.append(match.group(2))
        position = match.end(2)
    parts.append("\n" * content.count("\n", position))
    return "".join(parts), "typescript" if typescript else "javascript"
