"""Filmin belirli aralıklarını çekimlere böler, her çekimden bir kare alır ve kare tablosu üretir.

Tablolar bakılarak her çekimin ne gösterdiği katalog.json'daki "aciklama" alanına yazılır;
seslendirme metnindeki her cümle bu çekim numaralarına bağlanır.

Kullanım:
  python pipeline/katalog.py --film filmler/chronicle.mp4 \
      --araliklar 00:00:00-00:02:30,00:11:03-00:13:12 --cikti ozetler/chronicle/katalog

Çekim numarası başlangıç saniyesinden gelir (C00096 = 96. saniyede başlayan çekim), bu yüzden
sonradan yeni aralık eklemek eski numaraları değiştirmez. Var olan açıklamalar korunur.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from kurgu import aralik_analiz, calistir, cekimler, ffmpeg_kontrol, film_bilgisi

TABLO = (4, 4)  # sütun x satır
FONT = "C\\:/Windows/Fonts/arial.ttf" if sys.platform == "win32" else "DejaVuSans"


def saniye(metin):
    parcalar = [float(x) for x in metin.split(":")]
    toplam = 0.0
    for p in parcalar:
        toplam = toplam * 60 + p
    return toplam


def zaman_etiketi(t):
    t = int(t)
    return f"{t // 3600:02d}h{t // 60 % 60:02d}m{t % 60:02d}s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--film", required=True)
    ap.add_argument("--araliklar", required=True, help="bas-son,bas-son (sn ya da hh:mm:ss)")
    ap.add_argument("--cikti", required=True, help="katalog klasörü")
    ayar = ap.parse_args()

    ffmpeg_kontrol()
    film = Path(ayar.film)
    bilgi = film_bilgisi(film)
    klasor = Path(ayar.cikti)
    kareler = klasor / "kareler"
    kareler.mkdir(parents=True, exist_ok=True)

    katalog_yolu = klasor / "katalog.json"
    eski = {c["id"]: c for c in json.loads(katalog_yolu.read_text(encoding="utf-8"))} \
        if katalog_yolu.exists() else {}
    onbellek_yolu = klasor / "analiz_onbellek.json"
    onbellek = json.loads(onbellek_yolu.read_text()) if onbellek_yolu.exists() else {}

    yeni = {}
    for aralik in ayar.araliklar.split(","):
        bas, son = (saniye(x) for x in aralik.split("-"))
        son = min(son, bilgi["sure"])
        for a, b in cekimler(bas, son, aralik_analiz(film, bas, son, onbellek)):
            cid = f"C{int(a):05d}"
            yeni[cid] = {"id": cid, "bas": round(a, 2), "son": round(b, 2),
                         "sure": round(b - a, 1), "aciklama": eski.get(cid, {}).get("aciklama", "")}
        onbellek_yolu.write_text(json.dumps(onbellek))
    birlesik = sorted({**eski, **yeni}.values(), key=lambda c: c["bas"])

    for c in yeni.values():
        jpg = kareler / f"{c['id']}.jpg"
        if jpg.exists():
            continue
        etiket = f"{c['id']}  {zaman_etiketi(c['bas'])}  {c['sure']}sn"
        calistir(["ffmpeg", "-y", "-v", "error", "-ss", f"{(c['bas'] + c['son']) / 2:.3f}",
                  "-i", str(film), "-frames:v", "1", "-vf",
                  f"scale=480:-2,drawtext=fontfile='{FONT}':text='{etiket}':x=8:y=8:"
                  "fontsize=20:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=4",
                  "-q:v", "3", str(jpg)])

    # Kare tabloları: yalnızca bu çalıştırmadaki çekimler, zaman sırasıyla.
    sirali = sorted(yeni.values(), key=lambda c: c["bas"])
    adet = TABLO[0] * TABLO[1]
    for eski_tablo in klasor.glob("tablo_*.jpg"):
        eski_tablo.unlink()
    for n in range(0, len(sirali), adet):
        gecici = klasor / "_tablo"
        gecici.mkdir(exist_ok=True)
        for i, c in enumerate(sirali[n:n + adet]):
            shutil.copy(kareler / f"{c['id']}.jpg", gecici / f"{i:02d}.jpg")
        calistir(["ffmpeg", "-y", "-v", "error", "-i", str(gecici / "%02d.jpg"),
                  "-vf", f"tile={TABLO[0]}x{TABLO[1]}:padding=4", "-frames:v", "1", "-q:v", "3",
                  str(klasor / f"tablo_{n // adet + 1:02d}.jpg")])
        shutil.rmtree(gecici)

    katalog_yolu.write_text(json.dumps(birlesik, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(yeni)} çekim, {-(-len(sirali) // adet)} tablo -> {klasor}")


if __name__ == "__main__":
    main()
