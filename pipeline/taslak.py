"""Cümle başına çekim taslağını (cumle_taslak.md) okur.

Biçim:
  ## [S00] Kanca
  - `C00679@5-25 · C00705 · C00821` — Cümle metni.

C00705 tüm çekimi, C00679@5-25 çekimin 5. ile 25. saniyeleri arasını gösterir.
Sonuna ! eklenen çekim (C03473!) durağan kareye çevrilmez, hep klip olarak oynar.
"""
import re
from pathlib import Path

BASLIK = re.compile(r"^## \[(S\d+)\]\s*(.*)$")
CUMLE = re.compile(r"^- `([^`]+)`\s*[—-]\s*(.+)$")
CEKIM = re.compile(r"^(C\d+)(?:@([\d.]+)-([\d.]+))?(!)?$")


def oku(yol):
    segmentler = []
    # utf-8-sig: Not Defteri'nin eklediği BOM ilk başlığı bozmasın.
    for satir in Path(yol).read_text(encoding="utf-8-sig").splitlines():
        satir = satir.strip()
        if m := BASLIK.match(satir):
            segmentler.append({"id": m[1], "baslik": m[2], "cumleler": []})
        elif (m := CUMLE.match(satir)) and segmentler:
            cekimler = []
            for parca in m[1].split("·"):
                c = CEKIM.match(parca.strip())
                if not c:
                    raise ValueError(f"çekim okunamadı: {parca!r} ({satir[:60]})")
                cekimler.append({"id": c[1],
                                 "ofset": (float(c[2]), float(c[3])) if c[2] else None,
                                 "klip": bool(c[4])})
            segmentler[-1]["cumleler"].append({"metin": m[2].strip(), "cekimler": cekimler})
    return [s for s in segmentler if s["cumleler"]]


def metin(segment):
    return " ".join(c["metin"] for c in segment["cumleler"])
