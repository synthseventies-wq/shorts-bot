"""Taslaktaki her segmenti ElevenLabs ile seslendirir: S00.wav + S00.json (cümle zamanları).

Ses sıkıştırmasız PCM olarak alınır (MP3'ün baş/son boşlukları birikip kayma yaratmasın diye)
ve ElevenLabs'ın harf başına zaman damgalarından her cümlenin başlangıç ve bitişi çıkarılır.
Metni değişmeyen segmentler yeniden üretilmez (karakter hakkı boşa gitmesin).

Kullanım:
  python pipeline/seslendir.py --taslak ozetler/chronicle/cumle_taslak.md \
      --cikti ozetler/chronicle/ses --ses-id <voice_id> --sadece S00,S01,S02

Sitede elle üretilmiş sesleri içe aktarmak (S00.mp3, S01.mp3 ... bir klasörde):
  python pipeline/seslendir.py --taslak ... --cikti ozetler/chronicle/ses_emre \
      --ice-aktar ozetler/chronicle/ses_emre --sadece S00,S01,S02
Dosyalar WAV'a çevrilir ve cümle zamanları ElevenLabs'ın hizalama servisiyle (forced alignment)
bu WAV üzerinden çıkarılır; karakter hakkından düşmez.

API anahtarı ELEVENLABS_API_KEY ortam değişkeninden ya da depo kökündeki ELEVENLABS_API.txt'den okunur.
"""
import argparse
import array
import base64
import json
import math
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

import taslak

ORNEKLEME = 24000  # pcm_24000: tüm aboneliklerde açık
API = "https://api.elevenlabs.io/v1/text-to-speech/{}/with-timestamps?output_format=pcm_{}"


def anahtar():
    if os.environ.get("ELEVENLABS_API_KEY"):
        return os.environ["ELEVENLABS_API_KEY"].strip()
    dosya = Path(__file__).resolve().parent.parent / "ELEVENLABS_API.txt"
    if dosya.exists():
        return dosya.read_text(encoding="utf-8").strip()
    sys.exit("ElevenLabs anahtarı yok: ELEVENLABS_API_KEY ya da ELEVENLABS_API.txt")


def seslendir(metin, ayar, onceki, sonraki):
    govde = {"text": metin, "model_id": ayar.model,
             "voice_settings": {"stability": ayar.stabilite, "similarity_boost": 0.75,
                                "speed": ayar.hiz},
             "previous_text": onceki or None, "next_text": sonraki or None}
    istek = urllib.request.Request(
        API.format(ayar.ses_id, ORNEKLEME), data=json.dumps(govde).encode("utf-8"),
        headers={"xi-api-key": anahtar(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(istek, timeout=300) as yanit:
            return json.loads(yanit.read())
    except urllib.error.HTTPError as h:
        sys.exit(f"ElevenLabs hatası {h.code}: {h.read().decode('utf-8', 'replace')[:500]}")


def hizala(wav, metin):
    """Var olan sesi metinle hizalar; seslendirme yanıtıyla aynı biçimde harf zamanları döndürür."""
    sinir = uuid.uuid4().hex
    govde = (f"--{sinir}\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\n{metin}\r\n"
             f"--{sinir}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{wav.name}\"\r\n"
             "Content-Type: audio/wav\r\n\r\n").encode("utf-8") + wav.read_bytes() + \
        f"\r\n--{sinir}--\r\n".encode("utf-8")
    istek = urllib.request.Request(
        "https://api.elevenlabs.io/v1/forced-alignment", data=govde,
        headers={"xi-api-key": anahtar(), "Content-Type": f"multipart/form-data; boundary={sinir}"})
    try:
        with urllib.request.urlopen(istek, timeout=300) as yanit:
            sonuc = json.loads(yanit.read())
    except urllib.error.HTTPError as h:
        sys.exit(f"ElevenLabs hizalama hatası {h.code}: {h.read().decode('utf-8', 'replace')[:500]}")
    harfler = sonuc["characters"]
    return {"characters": [h["text"] for h in harfler],
            "character_start_times_seconds": [h["start"] for h in harfler],
            "character_end_times_seconds": [h["end"] for h in harfler]}


def ice_aktar(segment, kaynak_klasor, wav):
    """S00.mp3/.wav/... dosyasını kurgunun beklediği PCM WAV'a çevirir; örnek sayısını döndürür."""
    adaylar = sorted(kaynak_klasor.glob(f"{segment['id']}.*"))
    kaynak = next((p for p in adaylar if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")
                   and p.resolve() != wav.resolve()), None)
    if not kaynak:
        sys.exit(f"{segment['id']}: {kaynak_klasor} içinde ses dosyası yok.")
    gecici = wav.with_suffix(".tmp.wav")
    sonuc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(kaynak), "-ac", "1",
                            "-ar", str(ORNEKLEME), "-c:a", "pcm_s16le", str(gecici)],
                           capture_output=True, text=True)
    if sonuc.returncode != 0:
        sys.exit(f"{kaynak} çevrilemedi:\n{sonuc.stderr[-1000:]}")
    gecici.replace(wav)
    with wave.open(str(wav), "rb") as w:
        return w.getnframes(), kaynak


def son_temizle(wav):
    """Dosya sonundaki sessizlikten sonra gelen kısa sesi (sonraki cümlenin nefes başlangıcı) keser
    ve son 10 ms'yi kısar. Kalan örnek sayısını döndürür."""
    with wave.open(str(wav), "rb") as w:
        params = w.getparams()
        ornekler = array.array("h", w.readframes(w.getnframes()))
    dilim = params.framerate // 100  # 10 ms

    def tepe_db(i):
        parca = ornekler[i * dilim:(i + 1) * dilim]
        return 20 * math.log10((max((abs(x) for x in parca), default=0) or 1) / 32768)

    adet = len(ornekler) // dilim
    yuksek = 0
    while yuksek < 7 and adet - yuksek - 1 >= 0 and tepe_db(adet - yuksek - 1) > -50:
        yuksek += 1
    sessiz = sum(1 for i in range(adet - yuksek - 5, adet - yuksek) if i >= 0 and tepe_db(i) < -50)
    if 0 < yuksek <= 6 and sessiz == 5:
        del ornekler[(adet - yuksek) * dilim:]
    for i in range(min(dilim, len(ornekler))):
        ornekler[-1 - i] = int(ornekler[-1 - i] * i / dilim)
    with wave.open(str(wav), "wb") as w:
        w.setparams(params)
        w.writeframes(ornekler.tobytes())
    return len(ornekler)


def cumle_zamanlari(segment, hizalama, sure):
    """Harf zamanlarından cümle aralıklarını çıkarır; aralıklar boşluksuz ardışıktır."""
    harfler = hizalama["characters"]
    baslar, sonlar = hizalama["character_start_times_seconds"], hizalama["character_end_times_seconds"]
    if "".join(harfler) != taslak.metin(segment):
        sys.exit(f"{segment['id']}: ElevenLabs hizalaması metinle eşleşmiyor.")
    konum, ham = 0, []
    for c in segment["cumleler"]:
        ham.append((baslar[konum], sonlar[konum + len(c["metin"]) - 1]))
        konum += len(c["metin"]) + 1
    # Görüntü geçişi cümleler arasındaki duraklamanın ortasına düşer.
    sinirlar = [0.0] + [(ham[i][1] + ham[i + 1][0]) / 2 for i in range(len(ham) - 1)] + [sure]
    return [{"bas": round(a, 4), "son": round(b, 4), "konusma": [round(min(x, sure), 4) for x in h]}
            for a, b, h in zip(sinirlar, sinirlar[1:], ham)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taslak", required=True)
    ap.add_argument("--cikti", required=True, help="ses klasörü")
    ap.add_argument("--ses-id", help="seslendirilecek ses (içe aktarmada gerekmez)")
    ap.add_argument("--ice-aktar", help="elle üretilmiş S00.mp3 ... dosyalarının klasörü")
    ap.add_argument("--model", default="eleven_multilingual_v2")
    ap.add_argument("--stabilite", type=float, default=0.5)
    ap.add_argument("--hiz", type=float, default=1.0)
    ap.add_argument("--sadece", help="yalnızca bu segmentler, örn. S00,S01")
    ap.add_argument("--yeniden", action="store_true", help="var olanları da yeniden üret")
    ayar = ap.parse_args()
    if not ayar.ses_id and not ayar.ice_aktar:
        ap.error("--ses-id ya da --ice-aktar gerekli")

    hepsi = taslak.oku(ayar.taslak)
    secili = set(ayar.sadece.split(",")) if ayar.sadece else None
    klasor = Path(ayar.cikti)
    klasor.mkdir(parents=True, exist_ok=True)

    harcanan = 0
    for i, s in enumerate(hepsi):
        if secili and s["id"] not in secili:
            continue
        metin = taslak.metin(s)
        wav, bilgi_yolu = klasor / f"{s['id']}.wav", klasor / f"{s['id']}.json"
        if ayar.ice_aktar:
            _, kaynak = ice_aktar(s, Path(ayar.ice_aktar), wav)
            ornek = son_temizle(wav)
            sure = ornek / ORNEKLEME
            bilgi = {"metin": metin, "ses_id": f"ice_aktarma:{kaynak.name}", "model": None,
                     "ornekleme": ORNEKLEME, "ornek_sayisi": ornek, "sure": sure,
                     "cumleler": cumle_zamanlari(s, hizala(wav, metin), sure)}
            bilgi_yolu.write_text(json.dumps(bilgi, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{s['id']}: {sure:.2f} sn, {len(s['cumleler'])} cümle (içe aktarıldı)")
            continue
        if not ayar.yeniden and wav.exists() and bilgi_yolu.exists():
            eski = json.loads(bilgi_yolu.read_text(encoding="utf-8"))
            if eski["metin"] == metin and eski["ses_id"] == ayar.ses_id:
                print(f"{s['id']}: değişmemiş, atlandı")
                continue

        onceki = taslak.metin(hepsi[i - 1]) if i > 0 else ""
        sonraki = taslak.metin(hepsi[i + 1]) if i + 1 < len(hepsi) else ""
        yanit = seslendir(metin, ayar, onceki[-300:], sonraki[:300])
        pcm = base64.b64decode(yanit["audio_base64"])
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(ORNEKLEME)
            w.writeframes(pcm)
        ornek = son_temizle(wav)
        sure = ornek / ORNEKLEME
        bilgi = {"metin": metin, "ses_id": ayar.ses_id, "model": ayar.model,
                 "ornekleme": ORNEKLEME, "ornek_sayisi": ornek, "sure": sure,
                 "cumleler": cumle_zamanlari(s, yanit["alignment"], sure)}
        bilgi_yolu.write_text(json.dumps(bilgi, ensure_ascii=False, indent=1), encoding="utf-8")
        harcanan += len(metin)
        print(f"{s['id']}: {sure:.2f} sn, {len(s['cumleler'])} cümle, {len(metin)} karakter")
    print(f"Harcanan karakter: {harcanan}")


if __name__ == "__main__":
    main()
