"""segmentler.json + film dosyasından kurgu videosu üretir.

Her segmentte görüntü şu kalıpla akar:
  3-4 sn klip, 3-4 sn klip, 3-4 sn klip, yavaş zoom'lu durağan kare, klip, klip, ...
Klipler ve kareler o segmentin sahne aralıklarından seçilir.

Kullanım:
  python3 pipeline/kurgu.py --film filmler/chronicle.mkv \
      --segmentler ozetler/chronicle/segmentler.json \
      --ses ozetler/chronicle/ses \
      --cikti ozetler/chronicle/kurgu.mp4

--ses klasöründe S00.mp3, S01.mp3 ... varsa süreler oradan alınır ve anlatım videoya
eklenir. Yoksa süre kelime sayısından tahmin edilir (sessiz önizleme).
"""
import argparse
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

GENISLIK, YUKSEKLIK, FPS = 1920, 1080, 30
KELIME_SANIYE = 2.3  # ses yokken süre tahmini
FILM_SES = 0.12  # kliplerde filmin orijinal sesi


def calistir(komut):
    subprocess.run(komut, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def sure(dosya):
    cikti = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(dosya)],
        check=True, capture_output=True, text=True,
    ).stdout
    return float(cikti.strip())


def plan(segment, toplam, ayar, rnd):
    """Segment süresini klip/kare parçalarına böler; her parçaya kaynak zamanı atar."""
    parcalar, gecen, sira = [], 0.0, 0
    while toplam - gecen > 0.3:
        kare = sira % (ayar.klip_sayisi + 1) == ayar.klip_sayisi
        uzunluk = min(rnd.uniform(ayar.min_sure, ayar.max_sure), toplam - gecen)
        parcalar.append({"tur": "kare" if kare else "klip", "sure": uzunluk})
        gecen += uzunluk
        sira += 1

    # Kaynak zamanlarını sahne aralıklarına sırayla ve eşit aralıkla yay.
    araliklar = segment["sahneler"]
    havuz = sum(a["son"] - a["bas"] for a in araliklar)
    adim = havuz / len(parcalar)
    for i, p in enumerate(parcalar):
        konum = i * adim
        for a in araliklar:
            uzun = a["son"] - a["bas"]
            if konum < uzun:
                bas = a["bas"] + konum
                # Klip aralığın dışına taşmasın.
                p["kaynak"] = max(a["bas"], min(bas, a["son"] - p["sure"]))
                break
            konum -= uzun
    return parcalar


def klip_uret(film, p, hedef):
    calistir([
        "ffmpeg", "-y", "-ss", f"{p['kaynak']:.3f}", "-t", f"{p['sure']:.3f}", "-i", str(film),
        "-f", "lavfi", "-t", f"{p['sure']:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex",
        f"[0:v]scale={GENISLIK}:{YUKSEKLIK}:force_original_aspect_ratio=decrease,"
        f"pad={GENISLIK}:{YUKSEKLIK}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}[v];"
        f"[0:a]volume={FILM_SES},aresample=48000[fa];[fa][1:a]amix=inputs=2:duration=longest[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{p['sure']:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(hedef),
    ])


def kare_uret(film, p, hedef, yakinlas):
    png = hedef.with_suffix(".png")
    calistir(["ffmpeg", "-y", "-ss", f"{p['kaynak'] + p['sure'] / 2:.3f}", "-i", str(film),
              "-frames:v", "1", str(png)])
    kare_sayisi = max(1, round(p["sure"] * FPS))
    hiz = 0.12 / kare_sayisi  # parça boyunca %12 zoom
    zoom = f"min(1+{hiz}*on,1.12)" if yakinlas else f"max(1.12-{hiz}*on,1)"
    calistir([
        "ffmpeg", "-y", "-loop", "1", "-i", str(png),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex",
        # Önce büyütmek zoom sırasındaki titremeyi azaltır.
        f"[0:v]scale={GENISLIK * 2}:{YUKSEKLIK * 2}:force_original_aspect_ratio=decrease,"
        f"pad={GENISLIK * 2}:{YUKSEKLIK * 2}:(ow-iw)/2:(oh-ih)/2,"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={kare_sayisi}:s={GENISLIK}x{YUKSEKLIK}:fps={FPS},setsar=1[v]",
        "-map", "[v]", "-map", "1:a", "-frames:v", str(kare_sayisi), "-t", f"{p['sure']:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(hedef),
    ])
    png.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--film", required=True)
    ap.add_argument("--segmentler", required=True)
    ap.add_argument("--ses", help="S00.mp3, S01.mp3 ... içeren klasör")
    ap.add_argument("--cikti", required=True)
    ap.add_argument("--klip-sayisi", type=int, default=3, help="iki kare arasındaki klip sayısı")
    ap.add_argument("--min-sure", type=float, default=3.0)
    ap.add_argument("--max-sure", type=float, default=4.0)
    ap.add_argument("--sadece", help="yalnızca bu segmentler, örn. S00,S01")
    ap.add_argument("--tohum", type=int, default=1)
    ayar = ap.parse_args()

    film = Path(ayar.film)
    segmentler = json.loads(Path(ayar.segmentler).read_text(encoding="utf-8"))
    if ayar.sadece:
        secili = set(ayar.sadece.split(","))
        segmentler = [s for s in segmentler if s["id"] in secili]
    ses_klasoru = Path(ayar.ses) if ayar.ses else None
    rnd = random.Random(ayar.tohum)

    gecici = Path(tempfile.mkdtemp(prefix="kurgu_"))
    try:
        parca_listesi, ses_listesi, yakinlas = [], [], True
        for s in segmentler:
            ses = ses_klasoru / f"{s['id']}.mp3" if ses_klasoru else None
            if ses and ses.exists():
                toplam = sure(ses)
                ses_listesi.append(ses)
            else:
                toplam = len(s["metin"].split()) / KELIME_SANIYE
            for i, p in enumerate(plan(s, toplam, ayar, rnd)):
                hedef = gecici / f"{s['id']}_{i:03d}.mp4"
                if p["tur"] == "klip":
                    klip_uret(film, p, hedef)
                else:
                    kare_uret(film, p, hedef, yakinlas)
                    yakinlas = not yakinlas
                parca_listesi.append(hedef)
            print(f"{s['id']}: {toplam:.1f} sn")

        liste = gecici / "liste.txt"
        liste.write_text("".join(f"file '{p.as_posix()}'\n" for p in parca_listesi), encoding="utf-8")
        gorsel = gecici / "gorsel.mp4"
        calistir(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(liste), "-c", "copy", str(gorsel)])

        if ses_listesi and len(ses_listesi) == len(segmentler):
            sliste = gecici / "ses.txt"
            sliste.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in ses_listesi), encoding="utf-8")
            calistir([
                "ffmpeg", "-y", "-i", str(gorsel), "-f", "concat", "-safe", "0", "-i", str(sliste),
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", ayar.cikti,
            ])
        else:
            shutil.copy(gorsel, ayar.cikti)
        print(f"-> {ayar.cikti} ({sure(ayar.cikti):.1f} sn)")
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


if __name__ == "__main__":
    main()
