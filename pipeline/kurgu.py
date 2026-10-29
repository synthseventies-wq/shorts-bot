"""segmentler.json + film dosyasından kurgu videosu üretir.

Her segmentte görüntü şöyle akar: 2-4 klip (her biri 2.5-4.5 sn, en fazla 5 sn), ardından
yavaşça zoom in ya da zoom out yapan durağan bir kare, sonra yine klipler...
Klipler ve kareler yalnızca o segmentin sahne aralıklarından seçilir.

Kendiliğinden çözülen sorunlar:
- Klipler çekim geçişlerine ve siyah karelere denk gelmez (ffmpeg sahne/siyah tespiti).
- Durağan kare, aynı çekimdeki birkaç aday arasından en net olanı seçilir.
- Siyah bantlar (letterbox) kırpılır, görüntü 1920x1080'i tamamen doldurur.
- Çıktı filmin kendi kare hızıyla üretilir (24 -> 30 dönüşümündeki takılma olmaz).
- Sahne aralığı anlatıma yetmezse aynı klip tekrarlanmaz; araya daha çok durağan kare girer
  ve uyarı yazılır.

Kullanım (sessiz önizleme):
  python pipeline/kurgu.py --film filmler/chronicle.mp4 \
      --segmentler ozetler/chronicle/segmentler.json \
      --sadece S00,S01,S02 --cikti ozetler/chronicle/ornek_1dk.mp4

--ses klasöründe S00.mp3, S01.mp3 ... varsa süreler oradan alınır ve anlatım eklenir.
Yoksa süre kelime sayısından tahmin edilir.
"""
import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

GENISLIK, YUKSEKLIK = 1920, 1080
KELIME_SANIYE = 2.3  # ses yokken süre tahmini (Türkçe anlatım hızı)
EN_KISA_CEKIM = 1.5  # bundan kısa çekimler klip için kullanılmaz
SAHNE_ESIGI = 0.3  # ffmpeg sahne değişim eşiği
ZOOM = 0.12  # durağan karede toplam zoom miktarı (%12)


def calistir(komut, yakala=False):
    sonuc = subprocess.run(komut, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if sonuc.returncode != 0:
        sys.exit(f"ffmpeg hatası:\n{' '.join(map(str, komut))}\n{sonuc.stderr[-2000:]}")
    return sonuc.stderr if yakala else sonuc.stdout


def ffmpeg_kontrol():
    for arac in ("ffmpeg", "ffprobe"):
        if not shutil.which(arac):
            sys.exit(f"{arac} bulunamadı. Kurup PATH'e ekleyin "
                     "(Windows: winget install Gyan.FFmpeg, sonra terminali yeniden açın).")


def film_bilgisi(film):
    cikti = calistir(["ffprobe", "-v", "error", "-select_streams", "v:0",
                      "-show_entries", "stream=r_frame_rate,width,height:format=duration",
                      "-of", "json", str(film)])
    bilgi = json.loads(cikti)
    akis = bilgi["streams"][0]
    return {"fps": akis["r_frame_rate"], "genislik": akis["width"],
            "yukseklik": akis["height"], "sure": float(bilgi["format"]["duration"])}


def sure(dosya):
    return float(calistir(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "csv=p=0", str(dosya)]).strip())


def siyah_bant_bul(film, toplam_sure):
    """Filmin farklı yerlerinden örnek alıp siyah bantsız alanı bulur."""
    bulunan = Counter()
    for i in range(1, 9):
        t = toplam_sure * i / 9
        log = calistir(["ffmpeg", "-hide_banner", "-ss", f"{t:.1f}", "-i", str(film), "-t", "2",
                        "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"],
                       yakala=True)
        sonuncu = re.findall(r"crop=(\d+:\d+:\d+:\d+)", log)
        if sonuncu:
            bulunan[sonuncu[-1]] += 1
    if not bulunan:
        return None
    # Karanlık sahneler yanlış kırpma önerebilir; en çok görülen ve en geniş alanı seç.
    en_cok = max(bulunan.values())
    adaylar = [c for c, n in bulunan.items() if n == en_cok]
    return max(adaylar, key=lambda c: int(c.split(":")[0]) * int(c.split(":")[1]))


def aralik_analiz(film, bas, son, onbellek):
    """Aralıktaki çekim geçişlerini ve siyah bölümleri bulur (sonuç önbelleğe yazılır)."""
    anahtar = f"{bas:.2f}-{son:.2f}"
    if anahtar in onbellek:
        return onbellek[anahtar]
    log = calistir([
        "ffmpeg", "-hide_banner", "-ss", f"{bas:.3f}", "-t", f"{son - bas:.3f}", "-i", str(film),
        # blackdetect kareleri olduğu gibi geçirir; select'ten önce gelmeli.
        "-vf", f"scale=320:-2,blackdetect=d=0.2:pix_th=0.12,"
               f"select='gt(scene,{SAHNE_ESIGI})',showinfo",
        "-an", "-f", "null", "-"], yakala=True)
    kesimler = [bas + float(t) for t in re.findall(r"Parsed_showinfo.*?pts_time:([\d.]+)", log)]
    siyahlar = [(bas + float(a), bas + float(b))
                for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
    onbellek[anahtar] = {"kesimler": kesimler, "siyahlar": siyahlar}
    return onbellek[anahtar]


def cikar(araliklar, delikler):
    """Aralık listesinden delik aralıklarını çıkarır."""
    for da, db in delikler:
        yeni = []
        for a, b in araliklar:
            if db <= a or da >= b:
                yeni.append((a, b))
                continue
            if da > a:
                yeni.append((a, da))
            if db < b:
                yeni.append((db, b))
        araliklar = yeni
    return araliklar


def cekimler(bas, son, analiz):
    """Aralığı çekimlere böler, siyah bölümleri çıkarır."""
    sinirlar = [bas] + [k for k in analiz["kesimler"] if bas < k < son] + [son]
    parcalar = []
    for a, b in zip(sinirlar, sinirlar[1:]):
        # Geçişin hemen yanındaki kareleri bırak (çapraz geçiş ve sıkıştırma kalıntıları).
        parcalar += [(x + 0.1, y - 0.1) for x, y in cikar([(a, b)], analiz["siyahlar"])
                     if y - x - 0.2 >= EN_KISA_CEKIM]
    return parcalar


def plan_yap(toplam, ayar, rnd):
    """Süreyi klip/kare parçalarına böler."""
    parcalar, gecen = [], 0.0
    sonraki_kare = rnd.randint(ayar.min_klip, ayar.max_klip)
    while toplam - gecen > 0.05:
        kare = sonraki_kare == 0
        uzunluk = rnd.uniform(ayar.min_sure, ayar.max_sure)
        kalan = toplam - gecen - uzunluk
        if kalan < ayar.min_sure:  # küçük artık kalmasın: bu parçaya ekle ya da ikiye böl
            uzunluk = toplam - gecen if toplam - gecen <= 5.0 else (toplam - gecen) / 2
        parcalar.append({"tur": "kare" if kare else "klip", "sure": uzunluk})
        gecen += uzunluk
        sonraki_kare = rnd.randint(ayar.min_klip, ayar.max_klip) if kare else sonraki_kare - 1
    return parcalar


def kaynak_ata(parcalar, cekim_listesi, segment_id):
    """Her parçaya, çekim sınırını aşmayan ve tekrar etmeyen bir kaynak zamanı atar."""
    havuz = sum(b - a for a, b in cekim_listesi)
    gereken = sum(p["sure"] for p in parcalar if p["tur"] == "klip")
    if havuz < gereken:
        print(f"  UYARI {segment_id}: sahne aralığı {havuz:.0f} sn, klipler için {gereken:.0f} sn "
              "gerekiyor; bazı klipler durağan kareye çevrildi. Aralığı genişletmeyi düşünün.")

    def konum_zamani(konum):
        for a, b in cekim_listesi:
            if konum <= b - a:
                return a + konum
            konum -= b - a
        return cekim_listesi[-1][1]

    kullanilan = []  # (bas, son) — tekrar etmesin
    for i, p in enumerate(parcalar):
        hedef = konum_zamani(havuz * (i + 0.5) / len(parcalar))
        sirali = sorted(cekim_listesi, key=lambda c: 0 if c[0] <= hedef <= c[1] else
                        min(abs(c[0] - hedef), abs(c[1] - hedef)))
        if p["tur"] == "klip":
            for a, b in sirali:
                if b - a < p["sure"]:
                    continue
                bos = [(x, y) for x, y in cikar([(a, b)], kullanilan) if y - x >= p["sure"]]
                if not bos:
                    continue
                x, y = min(bos, key=lambda c: 0 if c[0] <= hedef <= c[1] else
                           min(abs(c[0] - hedef), abs(c[1] - hedef)))
                bas = max(x, min(hedef - p["sure"] / 2, y - p["sure"]))
                p["kaynak"] = bas
                kullanilan.append((bas, bas + p["sure"]))
                break
            else:
                p["tur"] = "kare"
        if p["tur"] == "kare":
            a, b = sirali[0]
            p["cekim"] = (a, b)
            p["kaynak"] = min(max(hedef, a), b)
    return parcalar


def en_net_kare(film, cekim, gecici, ad):
    """Çekimin ortasındaki 5 aday kareden en netini seçer (netlik ~ JPEG dosya boyutu)."""
    a, b = cekim
    en_iyi, en_boy = None, -1
    for i in range(5):
        t = a + (b - a) * (0.2 + 0.15 * i)
        jpg = gecici / f"{ad}_aday{i}.jpg"
        calistir(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(film),
                  "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(jpg)])
        boy = jpg.stat().st_size if jpg.exists() else 0
        if boy > en_boy:
            en_iyi, en_boy = t, boy
        jpg.unlink(missing_ok=True)
    return en_iyi


def goruntu_filtresi(kirpma):
    """Siyah bantları kırpar ve 16:9 kadrajı doldurur."""
    on = f"crop={kirpma}," if kirpma else ""
    return (f"{on}scale={GENISLIK}:{YUKSEKLIK}:force_original_aspect_ratio=increase,"
            f"crop={GENISLIK}:{YUKSEKLIK},setsar=1")


def klip_uret(film, p, hedef, kirpma, fps, film_ses):
    calistir([
        "ffmpeg", "-y", "-v", "error", "-ss", f"{p['kaynak']:.3f}", "-t", f"{p['sure']:.3f}",
        "-i", str(film), "-f", "lavfi", "-t", f"{p['sure']:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex",
        f"[0:v]{goruntu_filtresi(kirpma)},fps={fps}[v];"
        f"[0:a]volume={film_ses},aresample=48000[fa];[fa][1:a]amix=inputs=2:duration=longest[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{p['sure']:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(hedef),
    ])


def kare_uret(film, p, hedef, kirpma, fps, yakinlas, gecici):
    t = en_net_kare(film, p["cekim"], gecici, hedef.stem)
    p["kaynak"] = t
    png = hedef.with_suffix(".png")
    calistir(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(film),
              "-frames:v", "1", "-vf", goruntu_filtresi(kirpma), str(png)])
    pay, payda = (int(x) for x in fps.split("/")) if "/" in fps else (int(float(fps)), 1)
    kare_sayisi = max(1, round(p["sure"] * pay / payda))
    hiz = ZOOM / kare_sayisi
    zoom = f"1+{hiz}*on" if yakinlas else f"{1 + ZOOM}-{hiz}*on"
    calistir([
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex",
        # 2 kat büyütüp zoom yapmak, zoompan'ın tam sayı yuvarlamasından gelen titremeyi azaltır.
        f"[0:v]scale={GENISLIK * 2}:{YUKSEKLIK * 2},"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={kare_sayisi}:s={GENISLIK}x{YUKSEKLIK}:fps={fps},setsar=1[v]",
        "-map", "[v]", "-map", "1:a", "-frames:v", str(kare_sayisi), "-t", f"{p['sure']:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(hedef),
    ])
    png.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--film", required=True)
    ap.add_argument("--segmentler", required=True)
    ap.add_argument("--ses", help="S00.mp3, S01.mp3 ... içeren klasör")
    ap.add_argument("--cikti", required=True)
    ap.add_argument("--min-sure", type=float, default=2.5, help="parça süresi alt sınırı (sn)")
    ap.add_argument("--max-sure", type=float, default=4.5, help="parça süresi üst sınırı (sn, en fazla 5)")
    ap.add_argument("--min-klip", type=int, default=2, help="iki kare arasındaki en az klip")
    ap.add_argument("--max-klip", type=int, default=4, help="iki kare arasındaki en çok klip")
    ap.add_argument("--film-ses", type=float, default=0.12, help="kliplerde filmin sesi (0 = kapalı)")
    ap.add_argument("--kayma", type=float, default=0.0, help="altyazı-film kayması (sn)")
    ap.add_argument("--kirpma", default="otomatik", help="otomatik | yok | w:h:x:y")
    ap.add_argument("--sadece", help="yalnızca bu segmentler, örn. S00,S01")
    ap.add_argument("--tohum", type=int, default=1, help="farklı seçimler için değiştirin")
    ayar = ap.parse_args()
    ayar.max_sure = min(ayar.max_sure, 5.0)
    ayar.min_sure = min(ayar.min_sure, ayar.max_sure)

    ffmpeg_kontrol()
    film = Path(ayar.film)
    if not film.exists():
        sys.exit(f"Film bulunamadı: {film}")
    segmentler = json.loads(Path(ayar.segmentler).read_text(encoding="utf-8"))
    if ayar.sadece:
        secili = set(ayar.sadece.split(","))
        segmentler = [s for s in segmentler if s["id"] in secili]
    ses_klasoru = Path(ayar.ses) if ayar.ses else None
    rnd = random.Random(ayar.tohum)
    cikti = Path(ayar.cikti)
    cikti.parent.mkdir(parents=True, exist_ok=True)

    bilgi = film_bilgisi(film)
    if ayar.kirpma == "otomatik":
        kirpma = siyah_bant_bul(film, bilgi["sure"])
    else:
        kirpma = None if ayar.kirpma == "yok" else ayar.kirpma
    print(f"Film: {bilgi['genislik']}x{bilgi['yukseklik']}, {bilgi['fps']} fps, "
          f"{bilgi['sure'] / 60:.1f} dk, kırpma: {kirpma or 'yok'}")

    onbellek_yolu = Path(ayar.segmentler).with_name("analiz_onbellek.json")
    onbellek = json.loads(onbellek_yolu.read_text()) if onbellek_yolu.exists() else {}

    gecici = Path(tempfile.mkdtemp(prefix="kurgu_", dir=cikti.parent))
    try:
        parca_dosyalari, ses_listesi, kurgu_plani, yakinlas = [], [], [], True
        for s in segmentler:
            ses = ses_klasoru / f"{s['id']}.mp3" if ses_klasoru else None
            if ses and ses.exists():
                toplam = sure(ses)
                ses_listesi.append(ses)
            else:
                toplam = len(s["metin"].split()) / KELIME_SANIYE

            cekim_listesi = []
            for a in s["sahneler"]:
                bas = max(0.0, a["bas"] + ayar.kayma)
                son = min(bilgi["sure"], a["son"] + ayar.kayma)
                cekim_listesi += cekimler(bas, son, aralik_analiz(film, bas, son, onbellek))
            onbellek_yolu.write_text(json.dumps(onbellek))
            if not cekim_listesi:
                sys.exit(f"{s['id']}: aralıkta kullanılabilir görüntü yok (hepsi siyah ya da çok kısa).")

            parcalar = kaynak_ata(plan_yap(toplam, ayar, rnd), cekim_listesi, s["id"])
            for i, p in enumerate(parcalar):
                hedef = gecici / f"{s['id']}_{i:03d}.mp4"
                if p["tur"] == "klip":
                    klip_uret(film, p, hedef, kirpma, bilgi["fps"], ayar.film_ses)
                else:
                    kare_uret(film, p, hedef, kirpma, bilgi["fps"], yakinlas, gecici)
                    p["zoom"] = "in" if yakinlas else "out"
                    yakinlas = not yakinlas
                parca_dosyalari.append(hedef)
                kurgu_plani.append({"segment": s["id"], "tur": p["tur"], "zoom": p.get("zoom"),
                                    "kaynak": round(p["kaynak"], 2), "sure": round(p["sure"], 2)})
            klip = sum(p["tur"] == "klip" for p in parcalar)
            print(f"{s['id']}: {toplam:.1f} sn, {klip} klip + {len(parcalar) - klip} kare")

        liste = gecici / "liste.txt"
        liste.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parca_dosyalari),
                         encoding="utf-8")
        gorsel = gecici / "gorsel.mp4"
        calistir(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(liste),
                  "-c", "copy", str(gorsel)])

        if ses_listesi and len(ses_listesi) == len(segmentler):
            sliste = gecici / "ses.txt"
            sliste.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in ses_listesi),
                              encoding="utf-8")
            calistir([
                "ffmpeg", "-y", "-v", "error", "-i", str(gorsel),
                "-f", "concat", "-safe", "0", "-i", str(sliste),
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(cikti),
            ])
        else:
            if ses_listesi:
                print("UYARI: bazı segmentlerin sesi eksik, video anlatımsız üretildi.")
            shutil.copy(gorsel, cikti)

        cikti.with_suffix(".plan.json").write_text(
            json.dumps(kurgu_plani, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"-> {cikti} ({sure(cikti):.1f} sn), plan: {cikti.with_suffix('.plan.json')}")
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


if __name__ == "__main__":
    main()
