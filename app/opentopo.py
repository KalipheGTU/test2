"""OpenTopography (API globaldem) avec diagnostic réseau détaillé."""
import math, socket, ssl, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

HOST = "portal.opentopography.org"
URL = f"https://{HOST}/API/globaldem"
DEMS = {"COP30": "Copernicus GLO-30 (30 m)", "COP90": "Copernicus GLO-90 (90 m)", "SRTMGL1": "SRTM GL1 (30 m)",
        "SRTMGL3": "SRTM GL3 (90 m)", "NASADEM": "NASADEM (30 m)", "AW3D30": "ALOS World 3D (30 m)",
        "SRTM15Plus": "SRTM15+ (500 m, bathymétrie)", "EU_DTM": "EU DTM (30 m, Europe)"}
UA = "KalipheTiles/1.0 (desktop georeferencing app)"

class OTError(Exception):
    def __init__(self, kind, msg):
        super().__init__(msg); self.kind, self.msg = kind, msg

def classify(e):
    """Transforme une exception réseau en message compréhensible."""
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        try: body = e.read(400).decode("utf-8", "replace").strip()
        except Exception: body = ""
        if code in (401, 403): return OTError("key", f"OpenTopography a refusé la clé API (HTTP {code}). Vérifiez la clé, qu'elle est active et que votre compte a accès à ce jeu de données. {body}")
        if code == 400: return OTError("param", f"OpenTopography a refusé la requête (HTTP 400) : {body or 'paramètres invalides (emprise trop grande, type de MNT, hors couverture…)'}")
        if code == 429: return OTError("quota", "Limite de requêtes OpenTopography atteinte (HTTP 429) : réessayez plus tard.")
        if code >= 500: return OTError("remote", f"Le service OpenTopography est indisponible (HTTP {code}). Ce n'est pas votre ordinateur : réessayez plus tard.")
        return OTError("http", f"OpenTopography a répondu HTTP {code}. {body}")
    r = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(r, socket.gaierror):
        return OTError("dns", f"Résolution du nom « {HOST} » impossible (« getaddrinfo failed », erreur DNS). Votre ordinateur ne trouve pas l'adresse du service : "
                              "vérifiez votre connexion internet, désactivez un VPN ou un proxy, changez de serveur DNS (par ex. 1.1.1.1 ou 8.8.8.8) ou de réseau. "
                              "Ce n'est pas un problème de clé API ni du logiciel.")
    if isinstance(r, (socket.timeout, TimeoutError)) or "timed out" in str(r).lower():
        return OTError("timeout", "Délai dépassé : le serveur OpenTopography ne répond pas (réseau lent, pare-feu, ou service surchargé).")
    if isinstance(r, ssl.SSLError) or "CERTIFICATE" in str(r).upper():
        return OTError("ssl", f"Erreur de sécurité HTTPS ({r}). Vérifiez la date/heure de l'ordinateur ; un antivirus ou un pare-feu qui inspecte HTTPS peut bloquer la connexion.")
    if isinstance(r, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError)):
        return OTError("conn", f"Connexion refusée ou coupée ({type(r).__name__}) : pare-feu, proxy ou antivirus probable.")
    return OTError("net", f"Erreur réseau : {type(r).__name__}: {r}")

def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.headers.get("Content-Type", ""), r.read()

def area_km2(b):
    w, s, e, n = b; return abs(e - w) * 111.32 * math.cos(math.radians((s + n) / 2)) * abs(n - s) * 110.57

def fetch_dem(bbox, demtype, key, out_path, timeout=180):
    """Télécharge le MNT (GeoTIFF) couvrant exactement l'emprise (ouest, sud, est, nord)."""
    if not key: raise OTError("key", "Clé API OpenTopography manquante : saisissez-la dans l'onglet Terrain (compte gratuit sur opentopography.org).")
    if demtype not in DEMS: raise OTError("param", f"type de MNT inconnu : {demtype}")
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90): raise OTError("param", "emprise invalide")
    if area_km2(bbox) > 450_000: raise OTError("param", f"Emprise trop grande ({area_km2(bbox):,.0f} km²) pour OpenTopography (limite habituelle ≈ 450 000 km²).")
    q = urllib.parse.urlencode({"demtype": demtype, "south": f"{s:.6f}", "north": f"{n:.6f}", "west": f"{w:.6f}", "east": f"{e:.6f}",
                                "outputFormat": "GTiff", "API_Key": key})
    err = None
    for attempt in range(3):
        try:
            status, ctype, body = _get(f"{URL}?{q}", timeout); err = None; break
        except Exception as ex:
            err = classify(ex)
            if err.kind in ("key", "param", "dns", "ssl", "quota"): break      # inutile de réessayer
            time.sleep(1.5 * (attempt + 1))
    if err: raise err
    if body[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        txt = body[:300].decode("utf-8", "replace").strip()
        raise OTError("remote", f"OpenTopography n'a pas renvoyé de GeoTIFF : « {txt} »")
    out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".part"); tmp.write_bytes(body); tmp.replace(out_path)
    return len(body)

def diag(key):
    """Teste étape par étape : internet, DNS, connexion TCP/TLS, API (avec la clé)."""
    steps = []
    def step(name, fn, hint):
        t = time.time()
        try: detail = fn(); steps.append({"step": name, "ok": True, "detail": detail or "", "ms": int((time.time() - t) * 1000)})
        except Exception as ex:
            e = classify(ex) if not isinstance(ex, OTError) else ex
            steps.append({"step": name, "ok": False, "detail": e.msg, "hint": hint})
    step("Connexion internet générale (DNS de github.com)", lambda: socket.gethostbyname("github.com"),
         "Si cette étape échoue, votre ordinateur n'a pas d'accès internet / DNS : réparez d'abord le réseau (ce n'est pas lié à OpenTopography).")
    n0 = len(steps)
    step(f"Résolution DNS de {HOST}", lambda: socket.gethostbyname(HOST),
         "Le nom du service ne se résout pas : essayez un autre DNS (1.1.1.1 / 8.8.8.8), coupez le VPN/proxy.")
    if not steps[-1]["ok"]:                                   # inutile de tester la suite si le DNS échoue
        for name in ("Connexion TCP (port 443)", "Page d'accueil HTTPS", "API globaldem avec votre clé"):
            steps.append({"step": name, "ok": False, "detail": "non testé : la résolution DNS a échoué",
                          "hint": "Réparez d'abord le DNS / la connexion internet, puis relancez le test."})
        return steps
    def tcp():
        with socket.create_connection((HOST, 443), 10): return "port 443 joignable"
    step("Connexion TCP (port 443)", tcp, "Un pare-feu ou antivirus bloque probablement la connexion.")
    step("Page d'accueil HTTPS", lambda: f"HTTP {_get(f'https://{HOST}/', 20)[0]}", "Problème HTTPS / certificat / service distant.")
    if key:
        def api():
            q = urllib.parse.urlencode({"demtype": "COP30", "south": 43.0, "north": 43.01, "west": 3.0, "east": 3.01, "outputFormat": "GTiff", "API_Key": key})
            st, ct, body = _get(f"{URL}?{q}", 60)
            if body[:2] not in (b"II", b"MM"): raise OTError("remote", f"réponse inattendue : {body[:200].decode('utf-8', 'replace')}")
            return f"clé valide, MNT de test reçu ({len(body)} octets)"
        step("API globaldem avec votre clé", api, "Clé refusée ou service indisponible.")
    else:
        steps.append({"step": "API globaldem avec votre clé", "ok": False, "detail": "aucune clé enregistrée", "hint": "Saisissez la clé dans l'onglet Terrain."})
    return steps
