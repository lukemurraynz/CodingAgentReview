"""Self-review: run harness lenses over its own codebase with the symbol index."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from graph.symbols import build_symbol_index
from lenses import LensContext, LensFile, ProductionValidationLens, StructuralLens
from lenses.diffparse import parse_unified_diff

ROOT = Path(__file__).parent
index = build_symbol_index(ROOT)
print(f"symbol index: {len(index.definitions)} defs, {len(index.invocations)} invoked names, "
      f"{len(index.registrations)} registrations")

files = []
for py in sorted((ROOT / "src").rglob("*.py")):
    rel = py.relative_to(ROOT).as_posix()
    code = py.read_text(encoding="utf8", errors="replace")
    body = "".join(f"+{line}\n" for line in code.splitlines())
    diff = (
        f"diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n"
        f"@@ -0,0 +1,{max(len(code.splitlines()), 1)} @@\n{body}"
    )
    parsed = parse_unified_diff(diff)
    for lf in parsed:
        files.append(LensFile(path=lf.path, content=lf.content,
                              added_lines=frozenset(range(1, len(lf.content.splitlines()) + 2))))

ctx = LensContext(change_id="self-review", repo_id="harness/self", files=files, symbol_index=index)


async def _run():
    out = []
    for lens in (ProductionValidationLens(), StructuralLens()):
        found = await lens.run(ctx)
        for f in found:
            ev = f.evidence[0]
            print(f"[{f.severity.value}] {lens.name}: {f.title} ({ev.path}:{ev.line_start})")
            out.append(f)
    return out

asyncio.run(_run())
