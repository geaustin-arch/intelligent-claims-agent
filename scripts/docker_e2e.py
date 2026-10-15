"""Isolated synthetic Docker scenario; never uses or removes the normal app volume."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = '''import json
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.fernet import Fernet
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
print(json.dumps({
 "private": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
 "public": key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
 "encryption": Fernet.generate_key().decode()
}))
'''

def main():
    # A unique project owns only synthetic volumes; always clean up that project.
    project = "synthetic-" + uuid.uuid4().hex[:12]
    env = os.environ.copy()
    for key in ("AUTH_ISSUER", "AUTH_AUDIENCE", "AUTH_PUBLIC_KEY", "DATA_ENCRYPTION_KEY", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="docker-synthetic-") as temp:
        config = Path(temp) / "credentials.env"
        config.touch(mode=0o600)
        command = ["docker", "compose", "--project-name", project, "--file", str(ROOT / "compose.yaml"), "--env-file", str(config)]
        def run(*args, source=None, capture=False):
            return subprocess.run(command + list(args), input=source, text=True,
                                  cwd=ROOT, env=env, check=True, timeout=600,
                                  capture_output=capture)
        try:
            run("build")
            # Capture generated secrets in memory; never print them or write a private key.
            result = run("run", "--rm", "--no-deps", "-T", "app", "python", "-", source=GENERATOR, capture=True)
            keys = json.loads(result.stdout)
            config.write_text("AUTH_ISSUER=https://synthetic.invalid\nAUTH_AUDIENCE=docker-synthetic\nAUTH_PUBLIC_KEY='" + keys["public"] + "'\nDATA_ENCRYPTION_KEY=" + keys["encryption"] + "\n")
            run("up", "--wait", "--wait-timeout", "120")
            for phase in ("create", "resume", "verify"):
                if phase != "create":
                    run("down")
                    run("up", "--wait", "--wait-timeout", "120")
                settings = {"private": keys["private"], "phase": phase}
                scenario = "CONFIG = " + repr(settings) + "\n" + (ROOT / "scripts/docker_scenario.py").read_text()
                run("exec", "-T", "app", "python", "-", source=scenario)
            print("Authenticated Docker scenario and restart checks passed")
        finally:
            subprocess.run(command + ["down", "--volumes", "--remove-orphans"], cwd=ROOT, env=env, timeout=120, check=True)

if __name__ == "__main__":
    main()
