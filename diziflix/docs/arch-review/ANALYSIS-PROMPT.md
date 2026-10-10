# Ajan prompt'u: diziflix için yeni mimari analizi

> Kullanım: aşağıdaki "PROMPT" bölümünü olduğu gibi ajana ver. Ajan repo'da (`micro_services/diziflix`) çalışmalı; yan dosya
> `docs/arch-review/SYSTEM-OVERVIEW.md` zorunlu girdi. Ajan SALT-OKUR çalışır (kod değiştirmez), çıktısı bir markdown dosyasıdır.

---

## PROMPT

Sen kıdemli bir yazılım mimarısın. Görevin, `micro_services/diziflix` projesinin **mevcut sistemini eleştirel biçimde analiz etmek ve
daha sade, daha dayanıklı bir mimari önermek**. Kod YAZMA ve DEĞİŞTİRME; yalnız oku, çalıştırma (testleri, uygulamayı, deploy'u, sunucuya
bağlanmayı çalıştırma). Çıktın tek bir markdown raporu.

### 1. Önce şunları oku (sırayla)
1. `diziflix/docs/arch-review/SYSTEM-OVERVIEW.md` (bu görevin ana girdisi: amaç, bileşenler, yaşam döngüleri, zayıflıklar, sık hatalar, olay
   günlüğü, kısıtlar, sorular).
2. `diziflix/CLAUDE.md` ve `diziflix/docs/arch/*.md` (mevcut mimari notları, kullanıcı kararları).
3. Zayıflıkları doğrulamak için kodun ilgili yerleri (SYSTEM-OVERVIEW §9'daki liste): özellikle `server/app/library/{videos,sourcefinder,streamdiag,
   streamprobe,hostrules,playissues}.py`, `server/app/scraper/{playheal,heal_agent,heal,onboard,onboard_pipeline}.py`,
   `server/app/routers/{onboard_sandbox,streams,ops*}.py`, `server/app/db.py`, `API.md`.
Belgedeki her iddiayı varsayılan olarak "doğrulanması gereken hipotez" say; kritik olanları kodda ara, `dosya:satır` ile belgele. Yanlış ya da
eksik bulduklarını raporda açıkça yaz.

### 2. Bağlam (özet; ayrıntı belgede)
diziflix: ev ağında tek kullanıcılı dizi/film kataloğu + oynatma sistemi. Dış sitelerden selector tabanlı (LLM'siz) tarama; siteler bir LLM ajanıyla
("Site ekle") yaml olarak onboard edilir; bozulunca LLM "heal" ile onarılır; oynatma, sitelerin oynatıcı sayfalarından akış çıkaran resolver/provider
kütüphanesiyle yapılır; hata olursa kullanıcı bilgilendirilir ve arkada çözülmeye çalışılır. Kullanıcının asıl hedefi: **sistemin siteleri kendisinin
eklemesi ve kendini düzeltmesi, elle müdahale olmaması, modüler (site yaml = tasarım, provider kütüphanesi = video host), yalnız halka açık içerik,
düşük LLM/token maliyeti, kullanıcının yorulmaması**. Kullanıcı, mevcut sistemin "çok karışık tasarlanmış olabileceğini" ve yaşam döngüsünde "tasarım ve
sıralama hatası" hissettiğini söylüyor.

### 3. İstenen çıktı
`diziflix/docs/arch-review/ARCHITECTURE-PROPOSAL.md` dosyasını yaz. Bölümler:

1. **Yönetici özeti (en çok 15 satır):** teşhis + önerilen yön + ilk üç adım.
2. **Mevcut durumun doğrulanmış teşhisi:** SYSTEM-OVERVIEW'daki zayıflıklar (Z1-Z10) ve hatalar (E1-E13) için: doğru mu, eksik mi, kök neden ne?
   Bulduğun EK zayıflıkları ekle. Her madde: iddia, kanıt (`dosya:satır`), etki, kök neden sınıfı (mimari / ayar-eşik / eksik özellik / ajan-prompt).
   Gerçekten mimari olan sorunları (yama ile kapanmayanları) küçük ölçekli olanlardan AYIR.
3. **Hedef mimari ilkeleri:** 5-8 madde, her biri "neden" ve "bugünkü hangi sorunu çözer" ile.
4. **En az 2, en çok 3 alternatif mimari:** her biri için: ana fikir, bileşen/veri modeli şeması (metin/ASCII diyagram), yaşam döngüsü (özellikle
   "oynatma sorunu" ve "site ekleme/düzenleme"), mevcut kodla ilişkisi (neyi korur, neyi birleştirir, neyi siler), maliyet/riskler, ne zaman seçilmemeli.
   Mutlaka bir "evrimsel" alternatif (mevcut sistemi tek bir sorun-kaydı/iş soyutlamasıyla sarmak) ve bir "daha radikal" alternatif düşün.
5. **Karşılaştırma tablosu ve TEK öneri:** ölçütler: sadelik, kullanıcıya görünürlük, LLM/token maliyeti, geçiş riski, test edilebilirlik, mevcut
   API/istemciyle uyum, yeni site eklemenin kolaylığı. Önerini gerekçelendir; neden diğerlerini seçmediğini yaz.
6. **Önerilen mimarinin ayrıntısı:**
   - Veri modeli (tablolar/alanlar; mevcut tablolarla eşleme: hangileri korunur/birleşir/kalkar).
   - **Tek problem yaşam döngüsü** (durum makinesi: durumlar, geçiş olayları, sahipler, zaman aşımları, kullanıcıya/istemciye giden bildirimler,
     idempotentlik ve yeniden başlatma sonrası devam).
   - **Teşhis ve katman kararı** (sayfa/akış kanıtından "yaml mı, provider mı, motor yeteneği mi, korumalı site mi" kararı; heal tetik kapısının
     yerine geçen mantık). Doğrulama ilkesi (heuristik yerine ölçüm/kanıt).
   - **Bilgi sahipliği** (host/sağlayıcı davranışı nerede yaşar; öğrenilen çözümün terfisi).
   - **Motor boşluğu yönetimi** ve uzun ömürlü ajan oturumlarının ele alınışı.
   - **Operasyon** (deploy/restart sırasında çalışan işler, config referansı, kuyruk/iş modeli).
   - Gözlemlenebilirlik (admin'de tek bakışta durum).
7. **Sadeleşme listesi:** kaldırılabilecek/birleştirilebilecek ayar, mod, tablo, kod yolu, yaml anahtarı (kanıtlı).
8. **Geçiş planı:** küçük, bağımsız, geri alınabilir adımlar; her adımın hedefi, dokunduğu dosyalar, doğrulama yolu (smoke test + elle kontrol),
   riski, geri alma yolu. İlk 3 adımın "bir hafta sonu işi" boyutunda olması hedeflenir. Mevcut API (`API.md`) yalnız alan ekleyerek korunmalı.
9. **Riskler ve açık sorular:** doğrulayamadıkların, kullanıcıya sorulması gereken kararlar (en çok 8, her biri için önerin).
10. **Ek: kanıt tablosu:** raporda kullandığın tüm `dosya:satır` referansları.

### 4. Kısıtlar (ihlal etme)
- Kod, test, config, yaml, `.env`, sunucu, deploy, git (commit/push) üzerinde DEĞİŞİKLİK YAPMA. Yalnız `docs/arch-review/ARCHITECTURE-PROPOSAL.md`'yi yaz.
- Sunucuya (SSH/HTTP) bağlanma; gerçek veriye ihtiyacın varsa raporda "şu salt-okuma sorgusu gerekir" diye yaz.
- Gizli bilgileri (API anahtarı, `.env` içeriği) okuma/yazma/raporlama.
- Öneri, aşağıdaki kullanıcı kararlarıyla çelişmemeli: siteler sistemle eklenir (elle düzeltme yok); modüler site-yaml/provider ayrımı; yalnız
  halka açık içerik; LAN/tek kullanıcı/SQLite/FastAPI; mevcut web istemcisi ve `API.md` sözleşmesi çalışmaya devam; tarama LLM'siz; token ekonomisi;
  küçük smoke test stratejisi (2700 testi koşturmayı gerektiren bir plan önerme).
- "Her şeyi LLM'e yaptır", "yeni ağır bağımlılık/altyapı (Kafka, Postgres vb.)", "sıfırdan yeniden yaz" tipi öneriler ancak güçlü gerekçeyle ve ayrı
  bir alternatif olarak sunulabilir; önerdiğin yol bu değilse nedenini yaz.
- Emin olmadığın yerde tahmin yürütme: "bilinmiyor / doğrulanmadı" yaz ve nasıl doğrulanacağını belirt.

### 5. Kalite çıtası
- Somut ol: tablo adı, dosya adı, durum adı, örnek olay akışı (adım adım) ver; soyut ilkelerle yetinme.
- Raporda en az iki gerçek olayı (SYSTEM-OVERVIEW §6: Evlilik Güzeldir/YouTube, Anne Yarısı/twimg) yeni mimaride **baştan sona** nasıl işleneceğini
  adım adım anlat ("bugün neden takıldı -> yeni mimaride hangi durum/olay ne yapardı").
- Her öneri için "bugünkü hangi gerçek sorunu çözer?" cevabı olsun; cevabı olmayan öneriyi çıkar.
- Uzunluk: 400-800 satır; gereksiz tekrar yok; tablolar ve kısa şemalar tercih edilir. Dil: Türkçe (teknik adlar İngilizce kalabilir).
- Bitirince en üstte 10 satırlık "TL;DR" bırak.

### 6. Başlamadan
Bu prompt ve SYSTEM-OVERVIEW arasında çelişki bulursan SYSTEM-OVERVIEW'ı değil kodu esas al ve çelişkiyi raporla. Tıkanırsan en fazla 3 soru sor;
yoksa makul varsayımla devam et ve varsayımı raporun "Riskler ve açık sorular" bölümüne yaz.

---
