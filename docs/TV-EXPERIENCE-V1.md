# Diziflix TV deneyimi — v1 tasarım kararı

Durum: aşamalı uygulama; mevcut `tizen-client` 8091 üzerinden test ediliyor.

Son güncelleme: Ana Sayfa sırası **Hero → İzlemeye Devam Et → Diziler → Filmler → Listem**.
Hero, Yabancı Dizi ana sayfasındaki `.poster-media` kartlarının kaynak sırasını
koruyan `featured_yabancidizi` listesini kullanır. Geniş kapaklar scraper'dan
alınır; önceki/sonraki düğmeleriyle gezilir. Video olmayan yapımların ayrıntıları
ve Listem eylemi açıktır. İzleme geçmişi boşsa Devam Et satırı gizlenir.

Güncel karar: Filmler ve Diziler ayrı menü/sayfa değildir. Ana Sayfada önce
Diziler, ardından Filmler yatay satırı bulunur; her biri `added_at` azalan,
eşit tarihlerde kalıcı ID sırasıyla listelenir. Fragman veya yalnız katalog
bilgisi olan yapımlar da bu satırlarda bulunur. Yirmilik gruplar aynı satırdaki
Daha eskiler/Daha yeniler düğmeleriyle gezilir. Arama ve Listem ayrı kalır.

Aşağıdaki ilk tasarımdaki ayrı katalog sayfaları, yalnız izlenebilir içerikli ana
sayfa ve yeni bölüm satırı bu güncel kararla değiştirilmiştir. Bağımsız bölüm
metadata kaydı ve scraper alan eşlemeleri sonraki entegrasyon adımıdır.

## 1. Sabit kararlar

- Kullanıcı film veya dizi seçer. Site adı gezinme, kategori veya katalog kimliği değildir.
- Ana gezinme: **Ana Sayfa · Arama · Listem**.
- Profil ve Ayarlar sağ üstte yardımcı işlemlerdir; ana içerik kategorisi değildir.
- Film/dizi bilgisi, bölüm bilgisi ve oynatma sağlayıcısı ayrı varlıklardır.
- Türler merkezi kimliklerle tutulur. Ülke, dil, görüntü formatı ve site listesi tür değildir.
- Film detayı ile dizi detayı farklı davranır. Dizinin bir bölümü yoksa bölüm numarası tahmin edilmez.
- Fragman ayrı eylemdir. Tam film/bölüm açılamazsa fragmana kendiliğinden geçilmez.
- Bilgisi bulunan her yapım katalogda kalabilir; Ana Sayfa öncelikle video kaynağı olan içerikleri sunar.

## 2. Ana Sayfa

1920×1080 referans ekran; her kenarda mevcut 60 px güvenli alan. Mevcut koyu
Diziflix görünümü ve sarı odak rengi korunur. Üst gezinme 100–120 px; sabit bir
reklam alanı veya otomatik dönen afiş yoktur.

| Sıra | Bölüm | İçerik ve sıralama | Boşken |
|---|---|---|---|
| 1 | Odak alanı | Son odaklanan kartın afişi, başlığı, kısa açıklaması ve eylemleri | Video bulunmuyorsa katalog keşfi çağrısı |
| 2 | İzlemeye Devam Et | Profilin son izleme zamanına göre; dizi için tam bölüm hedefi | Gizle |
| 3 | Yeni Eklenen Filmler | En az bir etkin tam film kaynağı; ilk kullanılabilir olma tarihi azalan | Gizle |
| 4 | Yeni Eklenen Bölümler | Bölüm kartları; ilk kullanılabilir olma tarihi azalan | Gizle |
| 5 | Listem | Kullanıcının son eklediği yapımlar, en fazla 12; tüm listeye geçiş | Gizle |

Ana Sayfa onlarca tür satırı içermez. Türler Filmler/Diziler ekranlarının
filtreleridir. Popüler, trend ve kişisel öneriler v1'de üretilmez: bunlar için
ayrı, güvenilir veri/hesaplama tanımlanmamıştır.

Devam Et ve Listem kişisel bilgidir: video kaynağı sonradan bozulsa da kart
kaybolmaz. Kart "Kaynak kontrol ediliyor" veya "İzleme kaynağı yok" durumunu
belirtir ve detay açar. Yeni içerik satırları yalnızca `availability.state=ready`
içeriği öne çıkarır. `unknown` durumundaki kayıtlı sağlayıcı denenebilir fakat
çalıştığı doğrulanmış gibi etiketlenmez.

Yeni bölüm satırı bir diziyi değil bölümü temsil eder: dizi adı + S01 B03 + bölüm
adı. Aynı bölümün başka siteden gelmesi yeni kart oluşturmaz. Dizi başına en yeni
bölüm ilk 12 kartta gösterilir; "Tüm yeni bölümler" görünümünde bölüm bazında tam
liste bulunur. Sezon 0 özel bölümleri bu satırda ayrı etiket taşır.

## 3. Filmler ve Diziler

Üstte başlık ve sonuç sayısı; altında **Tür · Yıl · İzlenebilirlik · Sıralama**.
Filtreler tam ekran liste yerine küçük seçim paneli açar. Varsayılan tür/yıl Tümü,
izlenebilirlik Tümü; izlenebilir içerikler sıralamada önce gelir. Kullanıcı özellikle
"Yalnızca izlenebilir" seçerse `ready` kayıtlar döner.

- Tür: tek merkezi tür seçimi; başlangıçta çoklu seçim karmaşıklığı yok.
- Yıl: yapımın çıkış/ilk yayın yılı; null yıl "Bilinmiyor", 0 veya tarama yılı değildir.
- İzlenebilirlik: Tümü / İzlenebilir / İzleme kaynağı olmayanlar. Kontrol gerekenler
  Tümü'nde açık etiketle bulunur, diğer iki kesin sınıfa zorlanmaz.
- Sıralama: Yeni eklenen / Yıl / Ad. Puan yalnızca kaynağı biliniyorsa gösterilir.
- Kart: afiş, başlık, yıl; gerekiyorsa durum. Site adı kartın üstünde bulunmaz.
- Referans yerleşim: bir sırada 5 poster, görünür alanın altında sonraki sıranın başlangıcı.
- Ekran başına filtre ve odak konumu dönüşlerde korunur; profil değişiminde sıfırlanır.
- Arama Türkçe harfleri normalize eder; başlık ve özgün başlıkta arar. Film/dizi
  seçimi ve izlenebilirlik filtresi sonuçlarda uygulanır. Boş sorgu popülerlik uydurmaz.

## 4. Film detayı

Sol: başlık, yıl, tür, süre (dakika), kaynak adı belirtilmiş puan, 3 satır açıklama.
Sağ/arka: dekoratif afiş; eksik afişte yerleşim değişmez.

Eylem önceliği:

1. **Devam Et** veya **Filmi Oynat** — yalnızca tam film sağlayıcısı varsa.
2. **Listeme Ekle / Listemden Çıkar**.
3. **Fragman** — yalnızca ayrı fragman sağlayıcısı varsa.

`check_required` için ilk eylem **Kaynağı Dene**; `unavailable` için oynatma
butonu yerine neden yazısı vardır. Fragman düğmesinin metni hiçbir durumda
"Filmi Oynat" olmaz. Fragman ilerlemesi film izleme ilerlemesine yazılmaz.

Alt bölüm: tam açıklama, oyuncular, yönetmen; bilinmeyen alanlar gizlenir. Kaynak
sayısı yardımcı bilgi olabilir. Teknik URL, resolver adı ve harici kimlikler TV'de
bulunmaz; dashboard içindir.

## 5. Dizi, sezon ve bölüm

Dizi başlığı altında ana eylem **S01 B03'e Devam Et** gibi hedefi açıkça söyler.
İlerleme yoksa ilk izlenebilir bölüm önerilir; daha erken bir bölümün kaynağı yoksa
bunun "Diziyi Başlat" olduğu iddia edilmez. Örneğin "1. bölümün kaynağı yok;
2. bölüm mevcut" gösterilir.

Sezon seçimi yatay satır, altında dikey bölüm listesi:

| Bölüm kartı | Kaynak |
|---|---|
| Numara, başlık, bölüm görseli | Bölüm metadata kaydı |
| Açıklama, yayın tarihi, dakika | Bölüm metadata kaydı; bilinmiyorsa gizle |
| İlerleme / izlendi | Aktif profil + bölüm kimliği |
| Oynat / Kaynağı Dene / Kaynak yok | Yalnızca bu bölümün sağlayıcıları |

Sezon 0 "Özel Bölümler" olarak görünür; normal sezonların ardından listelenir.
Sezon veya bölüm numarası çıkarılamadıysa veri incelemeye gider. Bir dizinin ana
sayfa kartı veya "son bölüm" etiketi tek başına bölüm listesini üretmez. Kaynağı
olmayan ama metadata'sı bilinen bölümler görünür kalır; sezon eksikliği saklanmaz.

Bölüm tamamlanınca aynı dizinin bir sonraki **ardışık** bölümü hazırsa 5 saniyelik,
iptal edilebilir geçiş sunulur. Aradaki bölüm eksikse sessizce sonraki mevcut bölüme
atlanmaz. Otomatik sonraki bölüm hedefi detay verisinin sırasından tahmin edilmez;
server açık hedef döndürür.

## 6. Oynatıcı ve kaynak paneli

Video açılınca varsayılan sağlayıcıyı server seçer. Kullanıcının son dil tercihi,
sağlayıcı sağlığı ve cihazın desteklediği biçim dikkate alınır. 4K otomatik olarak
her cihaz için en iyi seçenek sayılmaz.

Yukarı/OK ile kontroller; **Kaynak ve kalite** paneli sağlayıcı > dil > kalite
sırasıyla gösterir. Aynı sağlayıcının 720p ve 1080p sürümleri iki farklı film
kaynağı gibi listelenmez. Kaynak değişiminde film/bölüm kimliği ve izleme konumu
korunur. Site adı yalnızca bu panelde görünür.

Hata: ilgili denemeyi kaydet, çözülmüş adresi geçersizleştir, uygun sonraki sağlayıcıyı
dene. Aynı sağlayıcıdan çok sayıda kalite denenmesi hata sayısını şişirmez. Tek
oynatma oturumunda denenen seçenek tekrar seçilmez. Hepsi başarısızsa Geri / Yeniden
Dene paneli açılır; sonsuz yeniden deneme yoktur.

İlk hata Şüpheli; üç ayrı başarısız deneme Kırık. Açık cihaz/codec/çevrimdışı/otomatik
oynatma engeli siteye kırık puanı yazmaz. Tam kaynak bulunamaması da var olmayan
bir sağlayıcıya hata yazmaz. Kullanıcı kontrollerden "Kaynak çalışmıyor" diyebilir;
kırmızı tuş bunun kısayoludur. Cross-origin iframe'de oynatma başlangıcı varsayılmaz.

## 7. Kumanda sözleşmesi

| Konum | Hareket |
|---|---|
| Üst gezinme | Sol/sağ sekme odağı; OK açar; yalnız odaklanmak ağ isteği başlatmaz |
| Üst gezinmeden aşağı | Son içerik odağı; ilk açılışta ana eylem / ilk kart |
| Yatay içerik satırı | Sol/sağ kart; aşağı/yukarı en yakın sütun; satır sütununu hatırla |
| Poster ızgarası | Sol/sağ komşu; aşağı/yukarı aynı sütun; eksik son satırda en yakın kart |
| Detaydan geri | Geldiği ekran, filtreler, kaydırma ve aynı kart |
| Sezon seçimi | Sol/sağ sezon; OK ile seç; aşağı ilk/son odaklanmış bölüm |
| Seçim paneli | Yukarı/aşağı seçenek; OK uygula; Geri değiştirmeden kapat ve odağı geri ver |
| Ana Sayfada geri | İlk basış üst gezinmeye döner; orada tekrar basış çıkış onayı |

Referans ölçüler: başlık 48–64 px, gövde 26–30 px, yardımcı yazı en az 22 px,
eylem yüksekliği en az 64 px. Odak sarı dolgu/çerçeve + görünür başlıkla belli olur;
renk tek işaret değildir. Focus animasyonu yerleşimi kaydırmaz. Gerçek TV'de
1920×1080, 60 px güvenli alan ve uzun Türkçe başlıklarla kontrol edilir.

## 8. Eksik veri ve yükleme

- Yeni ekran: iskelet; profil + ekran + filtre + sözleşme sürümüne göre önbellek.
- İstek eski ekrana aitse yanıt ekrana uygulanmaz; çıkılan oynatıcının hatası işlenmez.
- Boş sonuç: açıklama + filtreleri temizle; sonsuz iskelet yok.
- Ağ yok: önbellek varsa açık uyarı; yoksa Tekrar Dene.
- Hiç tam video kaynağı yoksa Ana Sayfa "Henüz izleme kaynağı yok" der; Filmler,
  Diziler ve Arama açıktır. Sadece fragman olması bu durumu değiştirmez.
- Ebeveyn/kids filtresi tüm ekranlarda sunucu tarafında uygulanır. Yaş uygunluğu
  bilinmeyen içeriği çocuk profiline uygun sayma; bu veri gelmeden çocuk katalog
  davranışı tamamlanmış kabul edilmez.

## 9. Uygulama sırası ve kabul

1. `DATA-CONTRACT-V1.md` ve JSON Schema ile normalizasyon girdisini doğrula.
2. Bölüm metadata tablosu ve merkezi tür eşlemesini ekle; sağlayıcıdan bağımsız kayıt oluştur.
3. Kullanılabilirlik, tarih ve bölüm hedeflerini sunucuda hesapla; yeni ekranların API'lerini ekle.
4. TV gezinmesi → katalog ızgaraları → iki detay ekranı → kaynak paneli.
5. Sinemalar ve Yabancı Dizi çıktılarını sözleşmeye bağla; her adapter için örnek veri doğrulaması.
6. 61'e kontrollü geçiş ve tek TV paketi güncellemesi; sonraki adapter'larda client değişmez.

Kabul senaryoları: aynı film/IMDb iki site → bir kart iki sağlayıcı; aynı TMDB
numaralı film ve dizi → ayrı kart; S01 B02 iki siteden → tek bölüm; fragman-only
→ Filmi Oynat yok; B02 kırık/B03 hazır → B02'den B03'e sessiz atlama yok; yeniden
tarama → Yeni Eklenenler tarihi sıfırlanmaz; filtre değişirken geciken yanıt →
eski içerik görünmez; kaynaksız katalog → boş ekran yerine açıklama; detaydan geri
→ aynı kart; çocuk profilinde uygunluğu bilinmeyen yapım → görünmez.
