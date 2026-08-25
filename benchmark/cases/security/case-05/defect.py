from pathlib import Path


def read_doc(base: Path, filename: str) -> str:
    return (base / filename).read_text()
