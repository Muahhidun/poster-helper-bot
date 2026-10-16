"""Export the permanent MCP's exact file allowlist, without bot/data/secrets."""
from pathlib import Path
import shutil
import sys

_UNCHANGED_FILES = (
    "requirements-furniture-mcp.txt",
    "furniture_mcp/__init__.py", "furniture_mcp/archive.py", "furniture_mcp/auth.py",
    "furniture_mcp/config.py", "furniture_mcp/server.py", "furniture_mcp/http_archive.py",
    "furniture_reader/__init__.py", "furniture_reader/config.py", "furniture_reader/store.py",
    "furniture_reader/http_contract.py", "deploy/production/railway.toml",
)
FILES = {path: path for path in _UNCHANGED_FILES}
FILES.update({"Dockerfile": "deploy/production/Dockerfile",
              "Dockerfile.dockerignore": "deploy/production/Dockerfile.dockerignore"})


def export_context(destination: Path):
    source = Path(__file__).resolve().parents[2]
    destination = Path(destination)
    if (not destination.is_absolute() or destination.exists()
            or any(path.is_symlink() for path in (destination, *destination.parents))):
        raise ValueError("Build context must be a new absolute directory")
    sources = []
    for relative, source_relative in FILES.items():
        path = source / source_relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError("Build source rejected")
        sources.append((relative, path))
    destination.mkdir(mode=0o700)
    for relative, path in sources:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o644)
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m deploy.production.build_context /absolute/new/context")
    export_context(Path(sys.argv[1]))
