import base64, io, zipfile, sys
from pathlib import Path
here = Path(__file__).resolve().parent
files = ["ailab.py", "launcher.pyw", "convert.py", "a11y.py", "mailer.py", "HOW-TO.txt"]
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for name in files:
        z.write(here / name, name)
b64 = base64.b64encode(buf.getvalue()).decode("ascii")
wrapped = "\n".join(b64[i:i+100] for i in range(0, len(b64), 100))
tpl = (here / "installer_template.ps1").read_text(encoding="utf-8")
assert tpl.count("__PAYLOAD__") == 1
out = tpl.replace("__PAYLOAD__", wrapped)
assert all(ord(c) < 128 for c in out)
dest = Path(sys.argv[1]); dest.mkdir(parents=True, exist_ok=True)
(dest / "Install-AILab.ps1").write_bytes(out.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))
print("zip bytes", len(buf.getvalue()), "script bytes", len(out))
