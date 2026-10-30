# shorts-bot — Türkçe film özeti kanalı otomasyonu

Kullanıcı Türkçe film özeti videoları yapıyor. Seslendirme, kullanıcının ElevenLabs'ta klonlanmış
kendi sesiyle yapılacak. Kullanıcının bilgisayarı Windows. Kullanıcıyla Türkçe konuş.

## İş akışı

1. Film + altyazı (`filmler/` altında, git'e girmez). Türkçe altyazılar genelde Windows-1254
   kodlamalıdır; okumadan önce UTF-8'e çevir.
2. Altyazı zaman damgalarından sahne dizini çıkar ve 10-15 dakikalık seslendirme metnini yaz:
   `ozetler/<film>/seslendirme.md`.
3. Çekim kataloğu: `py pipeline/katalog.py --film ... --araliklar ... --cikti ozetler/<film>/katalog`.
   Çıkan kare tablolarına (ve uzun çekimlerin içine) bak, her çekimin ne gösterdiğini
   `katalog.json`'daki `aciklama`ya yaz. Logo/jenerik gibi kullanılmayacakları `YASAK:` ile başlat.
4. Cümle başına çekim taslağı: `ozetler/<film>/cumle_taslak.md`, her cümle
   `` - `C00960@38-50 · C00941` — Cümle. `` biçiminde. Kullanıcı metni ve çekimleri onaylar.
5. `py pipeline/seslendir.py --taslak ... --cikti ozetler/<film>/ses --ses-id ...` → S00.wav +
   S00.json (cümle zamanları). PCM alınır, değişmeyen segment yeniden üretilmez. Anahtar
   `ELEVENLABS_API.txt` (git dışı). Karakter hakkı sınırlı: önce kısa deneme.
   Kullanıcının sesi: "Ben Emre" (`Ryy6b6VE8AUAag7r1Dok`, profesyonel klon), her zaman
   `--model eleven_v4` ile (S00-S02 sitede v4 ile üretildi; ton tutarlı kalsın). Sesler
   `ses_emre/`'de. Kullanıcı sitede üretirse `--ice-aktar <klasör>` (WAV'a çevirir, cümle
   zamanlarını forced alignment ile o WAV'dan çıkarır, karakter harcamaz).
   v4 bazen dosyanın son 10-40 ms'sine sonraki nefesin başını koyuyor; `son_temizle` bunu keser.
6. `py pipeline/kurgu.py --film ... --taslak ... --ses ... --cikti ...`. Süreyi hiç tahmin etmez;
   kesimler ana ses üzerinde kare numarasıyla hesaplanır, fark 1 kareyi aşarsa hata verir.
   Kalıp: 2-4 klip (4-6 sn, kullanıcı daha hızlısını beğenmedi), sonra yavaş zoom in/out yapan
   durağan kare. Bir cümledeki her çekim en az 2.2 sn kalır; kısa cümleye en fazla 2 çekim bağla.
   Filmin sesi kullanılmaz.
   Önce `--sadece S00,S01,S02` ile deneme yap. `--kontrol` yalnızca taslağı denetler: tüm
   taslakta aynı film saniyeleri iki ayrı cümlede kullanılmışsa `TEKRAR` uyarısı verir.
   Taslakta çekimin sonuna `!` (ör. `C03473!`) konursa durağan kareye çevrilmez, hep klip
   oynar; kilit anlarda (yıldırım, kaza) kullan. Parçalar `ozetler/<film>/kurgu_onbellek/`'te
   saklanır; değişmeyen parçalar yeniden kodlanmaz (tam kurgu ~10 dk yerine ~30 sn).
   Katalog kısa kesmeli sahneleri (parti gibi) atlayabilir; gerekirse aralığı `katalog.json`'a
   elle ekle (açıklama `ELLE:` ile başlar).

Windows'ta `python` Store kısayoluna düşüyor; `py` kullan.

## Üslup (kullanıcının kendi videolarından)

- Giriş: "Herkese selam, kanalıma hoş geldiniz. Bugün sizlere [yıl] yılında gösterime giren
  [film] adlı [ülke] [tür] filmini anlatacağım. Video spoiler içermektedir. Bu tür içerikler
  hoşunuza gidiyorsa videoyu beğenebilir ve abone olabilirsiniz."
- Anlatım geniş zamanla, karakter adıyla, kronolojik; "Ancak", "Bu sırada", "Sonraki gün".
- Kapanış: "Videonun sonuna geldik. Videoyu izlediğiniz için çok teşekkür ederim. Yorumlarda
  incelemesinin ya da özetinin gelmesini istediğiniz filmleri belirtebilirsiniz. Aynı zamanda
  videoda gördüğünüz eksikleri eleştirirseniz çok sevinirim. Beni desteklemek için videoyu
  beğenebilir ve kanala abone olabilirsiniz."
- Eklediğimiz: başa 15 sn'lik kanca, sona kısa bir yorum paragrafı (dönüştürücü içerik).
- Hedef: ~1600-1900 kelime (12-14 dk).

## Durum

- Chronicle (2012, Director's Cut) metni hazır: `ozetler/chronicle/`. Diyalogsuz sahnelerin
  (Wayne'in dişleri, soygun, Seattle çatışması, mızrak) aralıkları tahmini; film karelerine
  bakarak doğrula.
- S00-S02 için katalog, cümle taslağı ve deneme kurgusu var: `ornek_1dk.mp4` (OZAN KARACA test
  sesi, `ses/`) ve `ornek_1dk_emre.mp4` (kullanıcının sesi, `ses_emre/`, içe aktarıldı).
- Çalışma 5 turda (~2 dk'lık bölümler): S03-S11, S12-S20, S21-S30, S31-S38, S39-S45. Tur
  akışı: katalog → taslak → kullanıcıya kopyala-yapıştır metin → kullanıcı sitede v4 ile
  (hız 100, stabilite 50, benzerlik 75) üretip `ses_emre/`'ye koyar → içe aktar → kurgu.
  Video süresi ~11:45 hedefleniyor; kullanıcı metni uzatmak istemedi.
- Chronicle tamamlandı: `ozetler/chronicle/chronicle_tam.mp4` (S00-S45, 11:57, 176 cümle,
  ses farkı 0 ms). Tüm segmentler kullanıcının sesiyle (`ses_emre/`). Kalan: son rötuş.
- Son rötuş listesi: karanlık sahnelerin (S12 araba, S20 gece, S29 fırtına) ışığını açmak;
  durağan karelerin bağlamını gözden geçirmek.
- Her turda doğrula: her cümlenin ortasından kare + plandaki her durağan karenin kaynak
  görüntüsü (kullanıcı durağan karelerin ara sıra bağlamdan koptuğunu söyledi; son rötuşta
  tekrar bakılacak). Karanlık/bulanık kareleri ve kısa (<2 sn) çekimlerin art arda durağan
  kareye dönüşmesini yakala.
  `senaryo.py`/`segmentler.json` eski akıştan kaldı, yeni kurgu kullanmıyor.
- Kullanıcıya hatırlatıldı: kurgu kalıbı telifli bir filmi Creative Commons yapmaz; risk
  kullanıcının kararı.
