# Villa Bridge Teması: Nasıl Uygulanmalı, Nelere Dikkat Edilmeli ve Tasarım Prensipleri

## 1. Belgenin amacı

Bu belge, `Experiment/` klasöründeki görsel prototipin Villa Bridge’in gerçek arayüzüne aktarılması için uygulama rehberidir. Başka bir ajan bu çalışmayı devraldığında yalnız görünümü kopyalamamalı; aşağıdaki kullanıcı ayrımını, bilgi hiyerarşisini, etkileşim kurallarını ve güvenlik sınırlarını da korumalıdır.

`Experiment/` bir görsel referanstır. Üretim sırasında bu klasör doğrudan uygulamaya bağlanmamalı, çalışma zamanı bağımlılığı yapılmamalı ve buradaki sahte veriler gerçek sisteme taşınmamalıdır.

Referans dosyaları:

- `Experiment/index.html`: ekran kabuğu ve ana yerleşim
- `Experiment/styles.css`: renkler, yüzeyler, ölçüler ve responsive davranış
- `Experiment/app.js`: örnek ekran akışları ve sahte etkileşimler

## 2. Temel hedef

Arayüz aynı evde iki farklı kullanıcı türünü desteklemelidir:

1. **Günlük ev kullanıcısı:** Teknolojiyle yakın olmak zorunda değildir. Odayı, ışığı veya rutini en fazla bir-iki dokunuşta bulabilmelidir. Teknik terim, bağlantı protokolü ve ayrıntılı ayar görmemelidir.
2. **Yönetici kullanıcı:** Cihaz ekleme, bağlantı durumu, otomasyon ve sistem ayarlarına erişebilmelidir. Bu alan günlük ev ekranından görsel ve yapısal olarak açıkça ayrılmalıdır.

Ana tasarım ilkesi şudur:

> Günlük kullanım sakin, tanıdık ve tek dokunuşlu; yönetim alanı güçlü fakat kontrollü olmalıdır.

## 3. Değiştirilmemesi gereken tasarım kararları

Aşağıdaki kararlar prototip değerlendirmesi sırasında kesinleşmiştir:

- Tablet önceliklidir; ana hedef yatay, yaklaşık `1024 × 640` duvar tabletidir.
- Saat ve hava için ayrı, yüksek bir kart kullanılmaz. İkisi başlığın ortasındaki tek, şeffaf ve ince bordürlü alanda bulunur.
- Saat ve hava bölümleri ayrı ayrı tıklanabilir kalır.
- Saat tarafında saat, tarih ve konum; hava tarafında büyük ikon, derece ve kısa açıklama gösterilir.
- Hava ikonu küçük bir işaret gibi değil, uzaktan anlaşılacak kadar büyük ve belirgin olmalıdır.
- “Demo zamanı”, oynat/duraklat düğmesi ve zaman sürgüsü üretim arayüzünde bulunmaz.
- Canlı gökyüzü kendiliğinden çalışır. Üretimde hızlandırılmış demo zamanı değil, cihazın gerçek yerel saati kullanılmalıdır.
- Hızlı sahne kartlarında yalnız işlev ikonu ve başlık bulunur. Açıklama metni ana ekranda gösterilmez.
- Profil tablette sol gezinmenin en altında, telefonda alt menüde bulunur. Sağ üstte ikinci bir profil düğmesi kullanılmaz.
- Cihazlar ürün fotoğraflarıyla değil; ampul, priz, perde ve sensör gibi işlev ikonlarıyla gösterilir.
- Günlük kullanıcı rutinleri çalıştırabilir fakat düzenleyemez.
- Teknik bağlantılar ve gelişmiş işlemler yalnız yönetici modunda görünür.

## 4. Bilgi mimarisi

### 4.1 Ev modu

Ev modunun ana gezinmesi:

- Ev
- Odalar
- Rutinler
- Profil

Ana ekranın dikey sırası:

1. Başlık, birleşik saat/hava alanı ve ev sağlık durumu
2. Hızlı sahneler
3. Oda kartları
4. Yalnız gerektiğinde uyarılar

Kullanıcı önce “hangi oda?” veya “hangi sahne?” sorusuna cevap vermelidir. Cihaz ayrıntıları ilk ekrana yığılmamalıdır.

### 4.2 Yönetici modu

Yönetici gezinmesi:

- Genel Bakış
- Cihazlar
- Otomasyonlar
- Bağlantılar
- Sistem

Yönetici modunda sıcak renkli bir bilgilendirme şeridi ve “Ev moduna dön” eylemi bulunmalıdır. Kullanıcı hangi modda olduğunu hiçbir zaman tahmin etmek zorunda kalmamalıdır.

Telefon ekranında alt menüye altı dar öğe sıkıştırılmamalıdır. Gerekirse ikincil yönetici bölümleri “Daha fazla” altında toplanmalı; her dokunma alanı okunabilir ve rahat kalmalıdır.

## 5. Görsel dil

### 5.1 Renk ve yüzeyler

Mevcut prototipin temel renk ailesi korunmalıdır:

| Amaç | Gündüz değeri | Gece yaklaşımı |
|---|---|---|
| Ana metin | `#17231f` | Açık, kırık beyaz |
| İkincil metin | `#5d6b65` | Soluk açık yeşil-gri |
| Ana vurgu | `#1e624c` | Daha açık yeşil |
| Güçlü vurgu | `#124937` | Açık mint tonu |
| Kart yüzeyi | Yaklaşık `%82` opak açık yüzey | Yaklaşık `%78` opak koyu yüzey |
| İnce çizgi | Koyu yeşilin yaklaşık `%13` opaklığı | Açık rengin yaklaşık `%13` opaklığı |
| Uyarı | Sıcak turuncu | Daha açık turuncu |
| Tehlike | Kontrollü kırmızı | Daha açık kırmızı |

Gökyüzü görünür kalmalı fakat içerikle yarışmamalıdır. Kartlar gökyüzünü hissettirecek kadar yarı saydam, yazıları rahat okutacak kadar opak olmalıdır. Yoğun cam efekti, aşırı parlama veya düşük kontrast kullanılmamalıdır.

### 5.2 Köşeler, gölgeler ve boşluk

- Büyük kart köşeleri: yaklaşık `22–28 px`
- Orta kontroller: yaklaşık `14–18 px`
- Kart aralıkları: çoğunlukla `12–13 px`
- Ana kart gölgesi: yumuşak ve geniş
- Küçük kart gölgesi: hafif, yalnız katman ayrımı sağlayacak kadar
- Kart iç boşlukları: karta göre `12–20 px`

Yuvarlaklık dekor değil, dokunulabilir alanları ve içerik gruplarını ayırmak için kullanılmalıdır. Her öğeye farklı yarıçap verilmemelidir.

### 5.3 Tipografi

- Gövde metninde sistem sans-serif yazı ailesi kullanılır.
- “Evim”, büyük saat ve önemli sayısal değerlerde ölçülü biçimde serif kullanılabilir.
- Başlıklar kısa ve doğrudan olmalıdır.
- Yardımcı metinler ikincil renkte gösterilir; kritik bilgi yalnız renkle anlatılmaz.
- Türkçe metinlerin uzayabileceği kabul edilerek tek satırlı alanlarda taşma kontrolü yapılır.

### 5.4 İkonlar

- İkon bir cihazın markasını değil, yaptığı işi anlatmalıdır.
- Aynı işlev her ekranda aynı ikonla temsil edilmelidir.
- Hava ikonları güneş, parçalı bulut, bulut ve ayı ilk bakışta ayırt ettirmelidir.
- Başlıktaki hava ikonu tablette yaklaşık `42 px` alan ve `30 px` görsel boyutunda; telefonda yaklaşık `38 px` alan ve `27 px` görsel boyutunda düşünülmelidir.
- Üretim uygulamasında mevcut, tutarlı bir ikon seti varsa öncelikle o kullanılmalıdır. Yeni bir ikon paketi yalnız bu ekran için eklenmemelidir.

## 6. Ana bileşen kuralları

### 6.1 Başlık ve saat/hava alanı

Tablet başlığı üç bölümlüdür:

1. Sol: küçük bağlam yazısı ve ekran başlığı
2. Orta: birleşik saat/hava alanı
3. Sağ: evin sağlık durumu

Birleşik alanın arkası şeffaftır; ince bordür ve hafif iç ışık yeterlidir. Saat ve hava arasındaki çizgi iki tıklanabilir bölgeyi anlatır. Ayrı bir saat/hava kartı veya ikinci satır oluşturulmamalıdır.

Saat tıklandığında dünya saatleri ve alarm özeti; hava tıklandığında günlük ve saatlik tahmin açılabilir. Bu ayrıntılar ana ekranın yüksekliğini tüketmemelidir.

### 6.2 Hızlı sahneler

- Tablette dört sütun hedeflenir.
- Kart yüksekliği yaklaşık `62 px` olmalıdır.
- Yalnız ikon ve kısa başlık gösterilir.
- Başlıklar eylem diliyle yazılır: “Evden çık”, “Gece”, “Misafir”, “Tüm ışıkları kapat”.
- Tıklama sonrasında kısa bir geri bildirim gösterilir.
- Uzun açıklamalar yalnız Rutinler sayfasında bulunur.

### 6.3 Oda kartları

Oda kartında şu sıra korunur:

- Oda ikonu ve adı
- Açık cihaz sayısı veya “Her şey kapalı” durumu
- Sıcaklık ve kısa sensör özeti
- Odaya ait tek ana aç/kapat eylemi

Oda kartı hem odaya girme hem ana kontrol sunabilir; bu iki dokunma alanı birbirinden açıkça ayrılmalıdır.

### 6.4 Cihaz kartları

Cihaz adı, işlev ikonu, açık/kapalı durumu ve en gerekli ikincil bilgi gösterilir. Ayrıntılı ayar ayrı panel veya pencereye taşınır. Sensörler anahtar gibi görünmemeli; salt okunur oldukları anlaşılmalıdır.

### 6.5 Profil

Profil paneli günlük ayarların ve yönetici moduna geçişin tek kapısıdır. Tablette sol rayın altında, telefonda alt menüde açılır. Aynı ekranda ikinci bir profil düğmesi oluşturulmamalıdır.

### 6.6 Onay ve geri bildirim

- Normal başarılar kısa bildirimle gösterilir.
- Riskli yönetici işlemleri açıklayıcı onay penceresi ister.
- Yükleniyor, başarı ve hata durumları birbirinden ayırt edilmelidir.
- Hata olduğunda yalnız “Başarısız” denmemeli; kullanıcıya güvenli bir sonraki adım söylenmelidir.

## 7. Responsive ve dokunma kuralları

Öncelikli doğrulama boyutları:

- Yatay tablet: `1024 × 640`
- Telefon: yaklaşık `390 × 844`

Tablet:

- Solda ince, sabit gezinme kullanılır.
- Ana içerik ilk ekranda hızlı sahneleri ve mümkün olduğunca çok oda kartını göstermelidir.
- Dikey boşluk dikkatli kullanılmalıdır; gereksiz başlık kartı, demo kontrolü veya tekrar eden araç çubuğu eklenmemelidir.

Telefon:

- Sol gezinme gizlenir, sabit alt gezinme açılır.
- Saat/hava alanı başlığın ikinci satırını kaplayabilir.
- Hızlı sahneler iki sütuna düşer.
- Oda ve cihaz kartları tek sütuna düşer.
- Profil alt menüden erişilebilir kalır.

Genel:

- Temel dokunma hedefleri en az `44 × 44 px` olmalıdır.
- Yalnız hover ile çalışan özellik olmamalıdır.
- Klavye odağı görünür olmalıdır.
- Güvenli alan boşlukları telefonlarda hesaba katılmalıdır.
- `320 px` altına destek verilmesi zorunlu değildir; `320 px` ve üzerinde yatay taşma olmamalıdır.

## 8. Canlı gökyüzü ve zaman davranışı

Prototipte gökyüzü değerlendirme amacıyla hızlandırılmıştır. Üretim uygulamasında:

- Zaman cihazın güvenilir yerel saatinden alınmalıdır.
- Gün doğumu/gün batımı hesabı için konum mevcutsa gerçek değerler kullanılabilir; yoksa sabit ve güvenli zaman aralıkları kullanılmalıdır.
- Arka plan geçişleri yumuşak olmalı, dikkat dağıtacak hızda hareket etmemelidir.
- Güneş, ay ve yıldızlar içerik okunurluğunu azaltmamalıdır.
- Kullanıcıya demo sürgüsü veya oynat/duraklat kontrolü gösterilmemelidir.
- `prefers-reduced-motion` etkinse hareket azaltılmalı veya yalnız renk geçişi bırakılmalıdır.

## 9. Gerçek sisteme entegrasyon yöntemi

Tema aktarılırken iş mantığı yeniden yazılmamalıdır. Önerilen sıra:

1. Mevcut üretim arayüzünün ekranlarını, durum modelini ve komut akışlarını belirle.
2. Gerçek cihaz ve oda verisini değiştirmeden, araya yalnız sunum için bir görünüm modeli koy.
3. Önce renk değişkenlerini, yüzeyleri, tipografiyi ve ana kabuğu uygula.
4. Sonra sırasıyla başlık, hızlı sahneler, oda kartları ve oda ayrıntısını taşı.
5. Ev modu tamamlandıktan sonra yönetici ekranlarını aynı bileşen diliyle dönüştür.
6. Her aşamada mevcut cihaz komutlarını ve bağlantı davranışlarını tekrar doğrula.

Gerçek veri eşlemesinde aşağıdaki kavramlar yeterlidir:

- Oda: kimlik, ad, ikon, sıcaklık, kısa sensör özeti, açık cihaz sayısı
- Cihaz: kimlik, oda, ad, işlev türü, durum, erişilebilir eylemler, bağlantı durumu
- Sahne/rutin: kimlik, ad, ikon, çalıştırma eylemi
- Sistem sağlığı: genel durum ve gerektiğinde kısa uyarı
- Yönetici bağlantısı: protokol adı, durum, cihaz sayısı ve güvenli yönetim eylemleri

## 10. Entegrasyon sınırları ve güvenlik

- `Experiment/app.js` içindeki sahte oda ve cihaz dizileri üretime kopyalanmamalıdır.
- Prototipteki sahte başarı mesajları gerçek komut sonucu gibi kullanılmamalıdır.
- Zigbee, MQTT, Matter, Home Assistant ve Villa Bridge bağlantıları için var olan üretim servisleri kullanılmalıdır; ikinci bir bağlantı katmanı oluşturulmamalıdır.
- Kullanıcının tek dokunuşu aynı komutun iki kez gönderilmesine yol açmamalıdır.
- Optimistic güncelleme yapılırsa gerçek durumla uzlaştırılmalı; hata halinde eski durum geri alınmalıdır.
- Çevrimdışı cihazlar açıkça gösterilmeli ve çalışıyor gibi davranmamalıdır.
- Riskli eylemler rol kontrolü ve onay olmadan çalışmamalıdır.
- Gizli anahtarlar, cihaz kimlikleri ve özel bağlantı adresleri arayüz metnine veya istemci kaydına yazılmamalıdır.

## 11. Erişilebilirlik ve anlaşılabilir dil

- “Entity”, “endpoint”, “payload”, “coordinator” gibi teknik terimler ev modunda kullanılmamalıdır.
- Durumlar günlük Türkçeyle anlatılmalıdır: “Açık”, “Kapalı”, “Bağlantı yok”, “Tekrar dene”.
- İkonların erişilebilir adları olmalıdır.
- Renk tek başına durum taşımaz; metin veya sembolle desteklenir.
- Metin kontrastı dinamik gökyüzünün her evresinde kontrol edilmelidir.
- Diyalog açıldığında odak diyaloğa geçmeli, kapandığında çağıran düğmeye dönmelidir.
- Büyük yazı veya uzun Türkçe metinlerde kritik eylemler kesilmemelidir.

## 12. Kaçınılması gerekenler

- Ana ekrana teknik sistem durumlarının yığılması
- Aynı işlev için birden fazla düğme
- Saat/hava için ayrı büyük widget
- Hızlı sahne kartlarında açıklama paragrafı
- Sağ üstte tekrar eden profil düğmesi
- Üretimde demo zamanı kontrolü
- Çok şeffaf ve okunması zor kartlar
- Her kartta farklı ikon stili, renk veya köşe yarıçapı
- Küçük dokunma hedefleri
- Gerçek bağlantı sonucu gelmeden kalıcı başarı göstermek
- Prototipin sahte verisini gerçek veri kaynağı gibi kullanmak

## 13. Uygulama kabul kontrol listesi

Başka bir ajan işi tamamlamadan önce aşağıdakilerin tamamını doğrulamalıdır:

### Görsel

- [ ] `1024 × 640` görünümde başlık, hızlı sahneler ve ilk oda satırı rahatça görünür.
- [ ] `390 × 844` görünümde yatay taşma yoktur.
- [ ] Saat ve hava başlığa gömülüdür; ayrı widget yoktur.
- [ ] Hava ikonu uzaktan anlaşılır ve metni sıkıştırmaz.
- [ ] Demo zamanı alanı yoktur.
- [ ] Sağ üstte profil düğmesi yoktur.
- [ ] Hızlı sahnelerde yalnız ikon ve başlık vardır.
- [ ] Gündüz, gün batımı ve gece durumlarında metin kontrastı yeterlidir.

### Kullanım

- [ ] Ev kullanıcısı odaya ve rutine en fazla iki dokunuşta ulaşır.
- [ ] Profil tablette sol alttan, telefonda alt menüden açılır.
- [ ] Saat ve hava ayrıntıları ayrı ayrı açılır.
- [ ] Oda ana düğmesi ile odaya giriş alanı karışmaz.
- [ ] Başarı, yükleniyor, hata ve çevrimdışı durumları anlaşılırdır.
- [ ] Yönetici modundan ev moduna dönüş görünürdür.

### Teknik ve güvenlik

- [ ] Mevcut iş mantığı ve bağlantı servisleri korunmuştur.
- [ ] Sahte `Experiment` verileri üretime taşınmamıştır.
- [ ] Tek dokunuş tek komut üretir.
- [ ] Gerçek cihaz sonucu arayüzle uzlaştırılır.
- [ ] Riskli işlemler rol kontrolü ve onay ister.
- [ ] Gerçek servisleri çağırmayan görsel testlerle tablet ve telefon düzeni doğrulanmıştır.
- [ ] Değişikliklerden sonra projenin standart derleme ve kontrol adımları başarıyla tamamlanmıştır.

## 14. Tamamlanma tanımı

Tema aktarımı; yalnız ekran prototipe benzediğinde değil, teknolojiye uzak bir kullanıcının günlük işlemleri düşünmeden tamamlayabildiği, yönetici kullanıcının ise teknik kontrollere güvenli biçimde erişebildiği durumda tamamlanmış sayılır. Görsel sadelik, gerçek sistem doğruluğundan veya güvenli komut akışından taviz vermemelidir.
