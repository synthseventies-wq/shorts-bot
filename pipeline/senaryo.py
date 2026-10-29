"""Seslendirme metnini (seslendirme.md) kurgu için JSON'a çevirir.

Kullanım: python3 pipeline/senaryo.py ozetler/chronicle/seslendirme.md
Çıktı aynı klasöre segmentler.json olarak yazılır.
"""
import json
import re
import sys
from pathlib import Path

BASLIK = re.compile(r"^## \[(S\d+)\] (.+?) · (.+)$", re.M)


def saniye(zaman):
    s, d, sn = (int(x) for x in zaman.split(":"))
    return s * 3600 + d * 60 + sn


def oku(yol):
    metin = Path(yol).read_text(encoding="utf-8")
    basliklar = list(BASLIK.finditer(metin))
    segmentler = []
    for i, m in enumerate(basliklar):
        son = basliklar[i + 1].start() if i + 1 < len(basliklar) else len(metin)
        aralıklar = []
        for parca in m.group(2).split("|"):
            bas, bit = parca.strip().split("-")
            if saniye(bit) <= saniye(bas):
                raise ValueError(f"{m.group(1)}: geçersiz aralık {parca.strip()}")
            aralıklar.append({"bas": saniye(bas), "son": saniye(bit)})
        segmentler.append({
            "id": m.group(1),
            "baslik": m.group(3).strip(),
            "sahneler": aralıklar,
            "metin": " ".join(metin[m.end():son].split()),
        })
    return segmentler


if __name__ == "__main__":
    kaynak = Path(sys.argv[1])
    segmentler = oku(kaynak)
    hedef = kaynak.with_name("segmentler.json")
    hedef.write_text(json.dumps(segmentler, ensure_ascii=False, indent=2), encoding="utf-8")
    kelime = sum(len(s["metin"].split()) for s in segmentler)
    print(f"{len(segmentler)} segment, {kelime} kelime -> {hedef}")
