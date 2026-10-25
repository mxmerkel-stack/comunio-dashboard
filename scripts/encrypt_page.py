#!/usr/bin/env python3
"""Verschlüsselt dist/index.html mit einem Passwort (AES-256-GCM, Schlüssel per PBKDF2-SHA256).

Ergebnis ist eine Seite, die nur ein Passwortfeld zeigt; entschlüsselt wird im Browser (WebCrypto).
Ohne Passwort sind keine Daten lesbar – auch nicht im Quelltext.

Passwort aus Umgebungsvariable PAGE_PASSWORD.
"""
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parent.parent
DIST = Path(os.environ.get("DIST_DIR", ROOT / "dist"))
ITERATIONS = 600_000

LOGIN = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Comunio Zentrale</title>
<style>
:root{--panel:#e7f2dd;--fg:#16300f;--muted:#4a6b40;--line:#6d9b5c;--accent:#2e6b23;--bad:#a3261e}
body{margin:0;min-height:100vh;display:grid;place-items:center;color:var(--fg);
 background:#10300c linear-gradient(170deg,#2a5c20 0%,#1b4414 45%,#10300c 100%) fixed;
 font:13.5px/1.5 Verdana,Geneva,"DejaVu Sans",-apple-system,Segoe UI,sans-serif;padding:16px;box-sizing:border-box}
form{background:var(--panel);border:1px solid #11330d;padding:0 0 18px;width:100%;max-width:340px}
h1{font-size:17px;margin:0;padding:12px 18px;color:#fff;letter-spacing:.12em;text-transform:lowercase;
 background:linear-gradient(180deg,#36782a,#1d4a16);text-shadow:0 1px 0 #0d280a}
p{color:var(--muted);margin:14px 18px 14px;font-size:12px}
input[type=password]{width:calc(100% - 36px);margin:0 18px;box-sizing:border-box;font:inherit;padding:8px 10px;
 border:1px solid var(--line);background:#fff;color:var(--fg)}
label{display:flex;gap:8px;align-items:center;font-size:11.5px;color:var(--muted);margin:12px 18px}
button{width:calc(100% - 36px);margin:0 18px;font:inherit;font-weight:700;padding:9px;border:1px solid #11330d;
 background:linear-gradient(180deg,#3d7f2f,#2a6020);color:#fff;cursor:pointer}
#err{color:var(--bad);font-size:12px;min-height:18px;margin:10px 18px 0}
</style></head><body>
<form id="f" autocomplete="on">
 <h1>comunio zentrale</h1><p>Privat. Bitte Passwort eingeben.</p>
 <input type="text" name="username" value="comunio" autocomplete="username" hidden>
 <input id="pw" type="password" autocomplete="current-password" placeholder="Passwort" autofocus>
 <label><input id="rm" type="checkbox"> Auf diesem Gerät angemeldet bleiben</label>
 <button>Öffnen</button><div id="err"></div>
</form>
<script>
const P = __PAYLOAD__;
const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const KEY_STORE = "cz-key-" + P.salt.slice(0, 12);
async function derive(pw){
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({name:"PBKDF2", salt:b64(P.salt), iterations:P.iter, hash:"SHA-256"}, base, 256);
  return new Uint8Array(bits);
}
async function open(raw){
  const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt({name:"AES-GCM", iv:b64(P.iv)}, key, b64(P.data));
  const html = new TextDecoder().decode(plain);
  try { sessionStorage.setItem("cz-live-key", btoa(String.fromCharCode(...raw))); } catch (_) {}
  document.open(); document.write(html); document.close();
}
(async () => {
  try { const k = localStorage.getItem(KEY_STORE); if (k) { await open(b64(k)); return; } } catch (e) { try { localStorage.removeItem(KEY_STORE); } catch (_) {} }
})();
document.getElementById("f").addEventListener("submit", async e => {
  e.preventDefault(); const err = document.getElementById("err"); err.textContent = "Entschlüssele …";
  try {
    const raw = await derive(document.getElementById("pw").value);
    if (document.getElementById("rm").checked) { try { localStorage.setItem(KEY_STORE, btoa(String.fromCharCode(...raw))); } catch (_) {} }
    await open(raw);
  } catch (x) { err.textContent = "Falsches Passwort."; }
});
</script></body></html>
"""


def derive_key(pw):
    digest = hashes.Hash(hashes.SHA256())
    digest.update(b"comunio-zentrale:" + pw.encode())
    salt = digest.finalize()[:16]
    return salt, PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(pw.encode())


def encrypt_live(pw):
    """dist/live.json -> dist/live.enc.json (gleicher Schlüssel wie die Seite)."""
    src = DIST / "live.json"
    _, key = derive_key(pw)
    iv = os.urandom(12)
    data = AESGCM(key).encrypt(iv, src.read_bytes(), None)
    enc = lambda b: base64.b64encode(b).decode()
    (DIST / "live.enc.json").write_text(json.dumps({"iv": enc(iv), "data": enc(data)}))
    src.unlink()
    print("ok: live.enc.json verschlüsselt")


def main():
    pw = os.environ.get("PAGE_PASSWORD", "")
    if len(pw) < 12:
        sys.exit("PAGE_PASSWORD fehlt oder ist kürzer als 12 Zeichen.")
    if "--live" in sys.argv:
        return encrypt_live(pw)
    src = DIST / "index.html"
    plain = src.read_bytes()
    # Salz bleibt pro Passwort gleich, damit "angemeldet bleiben" tägliche Updates übersteht.
    digest = hashes.Hash(hashes.SHA256())
    digest.update(b"comunio-zentrale:" + pw.encode())
    salt, iv = digest.finalize()[:16], os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(pw.encode())
    data = AESGCM(key).encrypt(iv, plain, None)
    enc = lambda b: base64.b64encode(b).decode()
    payload = json.dumps({"salt": enc(salt), "iv": enc(iv), "iter": ITERATIONS, "data": enc(data)})
    src.write_text(LOGIN.replace("__PAYLOAD__", payload, 1))
    print(f"ok: {src} verschlüsselt ({len(plain)//1024} KB)")


if __name__ == "__main__":
    main()
