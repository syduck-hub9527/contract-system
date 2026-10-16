"""Validate the built wheel outside the checkout, including the volume UI resource."""

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
source_ui = (root / "contract_radar/assets/dashboard.html").read_bytes()
expected_ui = hashlib.sha256(source_ui).hexdigest()
wheels = list((root / "dist").glob("*.whl"))
if len(wheels) != 1:
    raise SystemExit("Expected exactly one wheel in dist/")
with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "installed"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheels[0])],
        check=True,
    )
    env = dict(os.environ, PYTHONPATH=str(target) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    subprocess.run(
        [sys.executable, "-m", "contract_radar", "--help"],
        cwd=tmp,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    script = target / "bin/contract-radar"
    if script.exists():  # POSIX: extensionless script with shebang, run via the interpreter
        cmd = [sys.executable, str(script)]
    else:  # Windows: pip --target emits only the .exe launcher
        script = target / "bin/contract-radar.exe"
        if not script.exists():
            raise SystemExit(f"console script missing under {target / 'bin'}")
        cmd = [str(script)]
    subprocess.run(
        [*cmd, "--help"],
        cwd=tmp,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    # The installed UI must be byte-identical to the single source in the checkout.
    code = f"""import hashlib, contract_radar
from importlib.resources import files
assert "installed" in contract_radar.__file__
b = files("contract_radar").joinpath("assets/dashboard.html").read_bytes()
assert hashlib.sha256(b).hexdigest() == "{expected_ui}"
"""
    subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env, check=True)
print("Installed wheel and volume UI: PASS")
