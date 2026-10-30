"""Cümle başına çekim taslağı + ElevenLabs sesinden kurgu videosu üretir.

Görüntü sesten türetilir, hiçbir süre tahmin edilmez:
- Segment sesleri (S00.wav ...) tek bir ana ses dosyasında, örnek sayısı kesin olarak birleştirilir.
- Her cümlenin penceresi seslendir.py'nin çıkardığı zaman damgalarından gelir; görüntü geçişleri
  cümleler arasındaki duraklamalara düşer.
- Kesim noktaları ana ses zaman çizelgesinde mutlak kare numarası olarak hesaplanır, yuvarlama
  hatası birikmez. Sonda görüntü ile ses arasındaki fark 1 kareden büyükse araç hata verir.

Her cümle penceresi yalnızca o cümleye bağlanan çekimlerle dolar: 2-4 klip (4-6 sn, en fazla
7 sn), ardından yavaşça zoom in ya da zoom out yapan durağan kare, sonra yine klipler...
Bir cümledeki her çekim en az 2.2 sn ekranda kalır; sığmayan fazla çekimler atılır ve uyarı yazılır.
Filmin sesi kullanılmaz (Content ID en kolay sesi yakalar).

Kullanım:
  python pipeline/katalog.py ...          (çekim kataloğu)
  python pipeline/seslendir.py ...        (ses + zaman damgaları)
  python pipeline/kurgu.py --film filmler/chronicle.mp4 \
      --taslak ozetler/chronicle/cumle_taslak.md --ses ozetler/chronicle/ses \
      --sadece S00,S01,S02 --cikti ozetler/chronicle/ornek_1dk.mp4
"""
import argparse
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from collections import Counter
from fractions import Fraction
from pathlib import Path

GENISLIK, YUKSEKLIK = 1920, 1080
EN_KISA_CEKIM = 1.5  # bundan kısa çekimler klip için kullanılmaz
SAHNE_ESIGI = 0.3  # ffmpeg sahne değişim eşiği
ZOOM = 0.12  # durağan karede toplam zoom miktarı (%12)
EN_UZUN_PARCA = 7.0  # bir klip ya da durağan kare bundan uzun olmaz (sn)
EN_KISA_PAY = 2.2  # bir cümledeki her çekim ekranda en az bu kadar kalır (sn)
KODLAMA = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
           "-video_track_timescale", "24000", "-an"]


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


def en_net_kare(film, cekim, gecici, ad):
    """Aralıktaki 5 aday kareden en netini seçer (netlik ~ JPEG dosya boyutu)."""
    a, b = cekim
    en_iyi, en_boy = None, -1
    for i in range(5):
        t = a + (b - a) * (0.1 + 0.2 * i)
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


def kare_no(t, fps):
    return round(Fraction(t).limit_denominator(1_000_000) * fps)


def ana_ses_yap(segmentler, ses, ara, hedef):
    """Segment WAV'larını araya sessizlik koyarak tek dosyada birleştirir; segment başlangıçlarını döndürür."""
    baslangiclar, toplam, oran = [], 0, None
    with wave.open(str(hedef), "wb") as cikis:
        for s in segmentler:
            with wave.open(str(ses / f"{s['id']}.wav"), "rb") as w:
                if oran is None:
                    oran = w.getframerate()
                    cikis.setparams(w.getparams())
                elif w.getframerate() != oran:
                    sys.exit(f"{s['id']}.wav örnekleme hızı farklı.")
                baslangiclar.append(toplam / oran)
                veri = w.readframes(w.getnframes())
                sessizlik = b"\x00" * (round(ara * oran) * w.getsampwidth() * w.getnchannels())
                cikis.writeframes(veri + sessizlik)
                toplam += len(veri + sessizlik) // (w.getsampwidth() * w.getnchannels())
    return baslangiclar, toplam / oran


def cekim_araligi(c, katalog):
    """Taslaktaki çekim referansını filmde mutlak (bas, son) aralığına çevirir."""
    if c["id"] not in katalog:
        sys.exit(f"{c['id']} katalogda yok; önce katalog.py ile o bölümü kataloglayın.")
    k = katalog[c["id"]]
    if k["aciklama"].startswith("YASAK"):
        sys.exit(f"{c['id']} yasaklı çekim: {k['aciklama']}")
    if not c["ofset"]:
        return k["bas"], k["son"]
    a, b = k["bas"] + c["ofset"][0], min(k["bas"] + c["ofset"][1], k["son"])
    if b - a < 0.5:
        sys.exit(f"{c['id']}@{c['ofset']} çekimin dışında (çekim {k['sure']} sn).")
    return max(a, k["bas"]), b


def tekrar_kontrol(segmentler, katalog):
    """Taslağın tamamında aynı film saniyelerinin iki ayrı cümlede kullanılıp kullanılmadığını bulur."""
    kullanim = []
    for s in segmentler:
        for ci, cumle in enumerate(s["cumleler"], 1):
            for c in cumle["cekimler"]:
                a, b = cekim_araligi(c, katalog)
                kullanim.append((a, b, f"{s['id']} cümle {ci} ({c['id']})"))
    kullanim.sort()
    uyarilar = []
    for i, (a, b, ad) in enumerate(kullanim):
        for a2, b2, ad2 in kullanim[i + 1:]:
            if a2 >= b:
                break
            ortak = min(b, b2) - a2
            if ortak > 0.5 and ad.split(" (")[0] != ad2.split(" (")[0]:
                uyarilar.append(f"  TEKRAR: {ad} ile {ad2} filmin {ortak:.1f} sn'sini paylaşıyor")
    return uyarilar


def pencere_parcala(kare_sayisi, araliklar, fps, ayar, durum, etiket):
    """Bir cümle penceresini (kare sayısı) çekimlere ve klip/kare parçalarına böler."""
    parcalar = []
    sigan = max(1, int(kare_sayisi / fps // EN_KISA_PAY))
    if sigan < len(araliklar):
        print(f"  UYARI {etiket}: {kare_sayisi / fps:.1f} sn'lik cümleye {len(araliklar)} çekim "
              f"sığmıyor, ilk {sigan} tanesi kullanıldı. Taslakta çekim sayısını azaltın.")
        araliklar = araliklar[:sigan]
    for i, (a, b, zorla) in enumerate(araliklar):
        pay_bas = round(kare_sayisi * i / len(araliklar))
        pay_son = round(kare_sayisi * (i + 1) / len(araliklar))
        pay = pay_son - pay_bas
        if pay <= 0:
            continue
        sure = pay / fps
        orta = (ayar.min_sure + ayar.max_sure) / 2
        adet = max(1, round(sure / orta))
        while sure / adet > EN_UZUN_PARCA:
            adet += 1
        sinirlar = [round(pay * j / adet) for j in range(adet + 1)]
        uzunluklar = [y - x for x, y in zip(sinirlar, sinirlar[1:])]

        turler = []
        for _ in uzunluklar:
            if durum["sonraki_kare"] == 0:
                turler.append("kare")
                durum["sonraki_kare"] = durum["rnd"].randint(ayar.min_klip, ayar.max_klip)
            else:
                turler.append("klip")
                durum["sonraki_kare"] -= 1
        if zorla:  # taslakta ! ile işaretli: hep klip
            turler = ["klip"] * len(uzunluklar)
        # Çekim kliplere yetmiyorsa sondan başlayarak durağan kareye çevir.
        while sum(n for n, t in zip(uzunluklar, turler) if t == "klip") / fps > (b - a) \
                and "klip" in turler:
            turler[len(turler) - 1 - turler[::-1].index("klip")] = "kare"

        klip_toplam = sum(n for n, t in zip(uzunluklar, turler) if t == "klip") / fps
        bosluk = ((b - a) - klip_toplam) / max(1, len(uzunluklar))
        konum = a + bosluk / 2
        for n, t in zip(uzunluklar, turler):
            dilim = n / fps
            if t == "klip":
                parcalar.append({"tur": "klip", "kare": n, "kaynak": konum, "cekim": (a, b)})
                konum += dilim + bosluk
            else:
                ust = min(b, konum + max(dilim, 1.0))
                parcalar.append({"tur": "kare", "kare": n, "cekim": (konum, ust) if ust - konum >= 0.5
                                 else (a, b)})
                konum += bosluk
    return parcalar


def klip_uret(film, p, hedef, kirpma, fps_metin):
    calistir(["ffmpeg", "-y", "-v", "error", "-ss", f"{p['kaynak']:.3f}", "-i", str(film),
              "-vf", f"{goruntu_filtresi(kirpma)},fps={fps_metin}", "-frames:v", str(p["kare"]),
              *KODLAMA, str(hedef)])


def kare_uret(film, p, hedef, kirpma, fps_metin, yakinlas, gecici):
    t = en_net_kare(film, p["cekim"], gecici, hedef.stem)
    p["kaynak"] = t
    png = hedef.with_suffix(".png")
    calistir(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(film),
              "-frames:v", "1", "-vf", goruntu_filtresi(kirpma), str(png)])
    hiz = ZOOM / p["kare"]
    zoom = f"1+{hiz}*on" if yakinlas else f"{1 + ZOOM}-{hiz}*on"
    calistir([
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png), "-filter_complex",
        # 2 kat büyütüp zoom yapmak, zoompan'ın tam sayı yuvarlamasından gelen titremeyi azaltır.
        f"[0:v]scale={GENISLIK * 2}:{YUKSEKLIK * 2},"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={p['kare']}:s={GENISLIK}x{YUKSEKLIK}:fps={fps_metin},setsar=1[v]",
        "-map", "[v]", "-frames:v", str(p["kare"]), *KODLAMA, str(hedef)])
    png.unlink()


def onbellek_anahtari(film, p, kirpma, fps_metin, yakinlas):
    """Parçayı belirleyen her şeyden bir anahtar; aynı anahtar = aynı video parçası."""
    if p["tur"] == "klip":
        icerik = ("klip", round(p["kaynak"], 3), p["kare"])
    else:
        icerik = ("kare", round(p["cekim"][0], 3), round(p["cekim"][1], 3), p["kare"], yakinlas)
    metin = json.dumps([Path(film).name, kirpma, fps_metin, ZOOM, KODLAMA, *icerik])
    return hashlib.sha1(metin.encode("utf-8")).hexdigest()[:20]


def kare_say(dosya):
    return int(calistir(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0",
                         str(dosya)]).strip())


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import taslak

    ap = argparse.ArgumentParser()
    ap.add_argument("--film", required=True)
    ap.add_argument("--taslak", required=True, help="cümle başına çekim taslağı (.md)")
    ap.add_argument("--ses", required=True, help="seslendir.py çıktısı: S00.wav + S00.json ...")
    ap.add_argument("--katalog", help="varsayılan: taslağın yanındaki katalog/katalog.json")
    ap.add_argument("--cikti", required=True)
    ap.add_argument("--ara", type=float, default=0.45, help="segmentler arası sessizlik (sn)")
    ap.add_argument("--min-sure", type=float, default=4.0, help="parça süresi alt sınırı (sn)")
    ap.add_argument("--max-sure", type=float, default=6.0,
                    help=f"parça süresi üst sınırı (sn, en fazla {EN_UZUN_PARCA:g})")
    ap.add_argument("--min-klip", type=int, default=2, help="iki kare arasındaki en az klip")
    ap.add_argument("--max-klip", type=int, default=4, help="iki kare arasındaki en çok klip")
    ap.add_argument("--kirpma", default="otomatik", help="otomatik | yok | w:h:x:y")
    ap.add_argument("--sadece", help="yalnızca bu segmentler, örn. S00,S01")
    ap.add_argument("--tohum", type=int, default=1, help="farklı klip/kare dizilimi için değiştirin")
    ap.add_argument("--kontrol", action="store_true",
                    help="yalnızca taslağı ve tekrarları denetle, video üretme")
    ayar = ap.parse_args()
    ayar.max_sure = min(ayar.max_sure, EN_UZUN_PARCA)
    ayar.min_sure = min(ayar.min_sure, ayar.max_sure)

    ffmpeg_kontrol()
    film = Path(ayar.film)
    if not film.exists():
        sys.exit(f"Film bulunamadı: {film}")
    segmentler = taslak.oku(ayar.taslak)
    katalog_yolu = Path(ayar.katalog) if ayar.katalog else Path(ayar.taslak).with_name("katalog") / "katalog.json"
    katalog = {c["id"]: c for c in json.loads(katalog_yolu.read_text(encoding="utf-8"))}
    for uyari in tekrar_kontrol(segmentler, katalog):
        print(uyari)
    if ayar.kontrol:
        return
    if ayar.sadece:
        secili = set(ayar.sadece.split(","))
        segmentler = [s for s in segmentler if s["id"] in secili]
    ses = Path(ayar.ses)
    zamanlar = {}
    for s in segmentler:
        bilgi_yolu = ses / f"{s['id']}.json"
        if not bilgi_yolu.exists() or not (ses / f"{s['id']}.wav").exists():
            sys.exit(f"{s['id']} sesi yok; önce seslendir.py çalıştırın (süre tahmin edilmez).")
        bilgi = json.loads(bilgi_yolu.read_text(encoding="utf-8"))
        if bilgi["metin"] != taslak.metin(s):
            sys.exit(f"{s['id']}: taslak metni değişmiş, ses eski. seslendir.py'yi yeniden çalıştırın.")
        zamanlar[s["id"]] = bilgi["cumleler"]

    cikti = Path(ayar.cikti)
    cikti.parent.mkdir(parents=True, exist_ok=True)
    bilgi = film_bilgisi(film)
    fps = Fraction(bilgi["fps"])
    if ayar.kirpma == "otomatik":
        kirpma = siyah_bant_bul(film, bilgi["sure"])
    else:
        kirpma = None if ayar.kirpma == "yok" else ayar.kirpma
    print(f"Film: {bilgi['genislik']}x{bilgi['yukseklik']}, {bilgi['fps']} fps, "
          f"{bilgi['sure'] / 60:.1f} dk, kırpma: {kirpma or 'yok'}")

    gecici = Path(tempfile.mkdtemp(prefix="kurgu_", dir=cikti.parent))
    # Parça önbelleği: değişmeyen klip/kareler sonraki kurgularda yeniden kodlanmaz.
    onbellek = Path(ayar.taslak).with_name("kurgu_onbellek")
    onbellek.mkdir(exist_ok=True)
    yeniden_kullanilan = 0
    try:
        ana_ses = gecici / "ana_ses.wav"
        seg_baslari, ses_suresi = ana_ses_yap(segmentler, ses, ayar.ara, ana_ses)
        toplam_kare = kare_no(ses_suresi, fps)

        durum = {"rnd": random.Random(ayar.tohum), "sonraki_kare": 0}
        durum["sonraki_kare"] = durum["rnd"].randint(ayar.min_klip, ayar.max_klip)
        parca_dosyalari, plan, yakinlas = [], [], True
        for si, s in enumerate(segmentler):
            seg_son = seg_baslari[si + 1] if si + 1 < len(segmentler) else ses_suresi
            for ci, (cumle, z) in enumerate(zip(s["cumleler"], zamanlar[s["id"]])):
                bas = seg_baslari[si] + z["bas"]
                son = seg_son if ci == len(s["cumleler"]) - 1 else seg_baslari[si] + z["son"]
                ilk, sonra = kare_no(bas, fps), kare_no(son, fps)
                araliklar = [(*cekim_araligi(c, katalog), c["klip"]) for c in cumle["cekimler"]]
                parcalar = pencere_parcala(sonra - ilk, araliklar, fps, ayar, durum,
                                           f"{s['id']} cümle {ci + 1}")
                kare_imleci = ilk
                for p in parcalar:
                    anahtar = onbellek_anahtari(film, p, kirpma, bilgi["fps"], yakinlas)
                    hedef = onbellek / f"{anahtar}.mp4"
                    kayit = onbellek / f"{anahtar}.json"
                    if hedef.exists() and kayit.exists():
                        p["kaynak"] = json.loads(kayit.read_text())["kaynak"]
                        yeniden_kullanilan += 1
                    elif p["tur"] == "klip":
                        klip_uret(film, p, hedef, kirpma, bilgi["fps"])
                    else:
                        kare_uret(film, p, hedef, kirpma, bilgi["fps"], yakinlas, gecici)
                    kayit.write_text(json.dumps({"kaynak": p["kaynak"]}))
                    if p["tur"] == "kare":
                        p["zoom"] = "in" if yakinlas else "out"
                        yakinlas = not yakinlas
                    parca_dosyalari.append(hedef)
                    plan.append({"segment": s["id"], "cumle": ci + 1, "tur": p["tur"],
                                 "zoom": p.get("zoom"), "kaynak": round(p["kaynak"], 2),
                                 "video_bas": round(float(kare_imleci / fps), 3),
                                 "kare_sayisi": p["kare"]})
                    kare_imleci += p["kare"]
            print(f"{s['id']}: {seg_son - seg_baslari[si]:.2f} sn, {len(s['cumleler'])} cümle")

        liste = gecici / "liste.txt"
        liste.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parca_dosyalari),
                         encoding="utf-8")
        gorsel = gecici / "gorsel.mp4"
        calistir(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(liste),
                  "-c", "copy", str(gorsel)])
        gercek_kare = kare_say(gorsel)
        if gercek_kare != toplam_kare:
            sys.exit(f"SENKRON HATASI: görüntü {gercek_kare} kare, ses {toplam_kare} kare tutuyor.")

        # Ses normalize edilir, sonra görüntünün tam süresine örnek düzeyinde kesilir/tamamlanır.
        ornek = round(Fraction(toplam_kare) / fps * 48000)
        normal = gecici / "normal.wav"
        calistir(["ffmpeg", "-y", "-v", "error", "-i", str(ana_ses), "-af", "loudnorm=I=-14:TP=-1.5",
                  "-ar", "48000", "-c:a", "pcm_s16le", str(normal)])
        esit = gecici / "esit.wav"
        with wave.open(str(normal), "rb") as w, wave.open(str(esit), "wb") as e:
            e.setparams(w.getparams())
            kare_boyu = w.getsampwidth() * w.getnchannels()
            veri = w.readframes(w.getnframes())[:ornek * kare_boyu]
            e.writeframes(veri + b"\x00" * (ornek * kare_boyu - len(veri)))
        calistir(["ffmpeg", "-y", "-v", "error", "-i", str(gorsel), "-i", str(esit),
                  "-map", "0:v", "-map", "1:a",
                  "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(cikti)])
        # Kontrol: kodlanmış ses çözülüp örnekleri sayılır (AAC'nin kap süresine eklediği dolgu sayılmaz).
        cozulen = gecici / "kontrol.wav"
        calistir(["ffmpeg", "-y", "-v", "error", "-i", str(cikti), "-map", "0:a", "-c:a", "pcm_s16le",
                  str(cozulen)])
        with wave.open(str(cozulen), "rb") as w:
            ses_ornek = w.getnframes()
        sure = {"video": float(gercek_kare / fps), "audio": ses_ornek / 48000}
        fark = abs(sure["video"] - sure["audio"])
        if fark > 1 / fps:
            sys.exit(f"SENKRON HATASI: görüntü {sure['video']:.3f} sn, ses {sure['audio']:.3f} sn.")

        cikti.with_suffix(".plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"-> {cikti} ({sure['video']:.2f} sn, {gercek_kare} kare, ses farkı "
              f"{fark * 1000:.0f} ms), plan: {cikti.with_suffix('.plan.json')}, "
              f"önbellekten {yeniden_kullanilan}/{len(parca_dosyalari)} parça")
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


if __name__ == "__main__":
    main()
