# shorts-bot — Türkçe film özeti kanalı otomasyonu

Kullanıcı Türkçe film özeti videoları yapıyor. Seslendirme, kullanıcının ElevenLabs'ta klonlanmış
kendi sesiyle yapılacak. Kullanıcının bilgisayarı Windows. Kullanıcıyla Türkçe konuş.

## İş akışı

1. Film + altyazı (`filmler/` altında, git'e girmez). Türkçe altyazılar genelde Windows-1254
   kodlamalıdır; okumadan önce UTF-8'e çevir.
2. Altyazı zaman damgalarından sahne dizini çıkar ve 10-15 dakikalık seslendirme metnini yaz:
   `ozetler/<film>/seslendirme.md`. Her paragraf `## [S05] 00:07:07-00:09:24 | ... · Başlık`
   biçiminde, gösterilecek film aralıklarına bağlı.
3. Kullanıcı metni onaylar.
4. `python pipeline/senaryo.py ozetler/<film>/seslendirme.md` → `segmentler.json`.
5. Her segment ElevenLabs ile ayrı seslendirilir → `ozetler/<film>/ses/S00.mp3, S01.mp3 ...`
   (henüz otomatik değil).
6. `python pipeline/kurgu.py --film ... --segmentler ... --ses ... --cikti ...`
   Kalıp: 3 klip (3-4 sn), sonra yavaş zoom in/out yapan durağan kare, tekrar. Önce
   `--sadece S00,S01` ile kısa deneme yap.

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
- Sıradaki: Chronicle'dan `--sadece S00,S01,S02` ile deneme kurgusu; ardından PySceneDetect ile
  klipleri çekim sınırlarına oturtma ve ElevenLabs seslendirmesini otomatikleştirme.
- Kullanıcıya hatırlatıldı: kurgu kalıbı telifli bir filmi Creative Commons yapmaz; risk
  kullanıcının kararı.
