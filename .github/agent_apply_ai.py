import base64
import io
import pathlib
import tarfile

root = pathlib.Path(__file__).resolve().parents[1]
parts = sorted((root / ".github" / "agent_payload").glob("part*.txt"))
if not parts:
    raise RuntimeError("AI policy payload parts are missing")
data = base64.b64decode("".join(path.read_text().strip() for path in parts))
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
    for member in archive.getmembers():
        target = (root / member.name).resolve()
        if root not in target.parents:
            raise RuntimeError(f"unsafe payload path: {member.name}")
        source = archive.extractfile(member)
        if source is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read())
print("AI quantitative policy files applied")
