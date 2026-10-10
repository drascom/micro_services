# diziflix: mimari teşhis ve öneri

## TL;DR

1. Sorun "çok fazla özellik" değil, **ortak bir sorun kaydının olmaması**: aynı oynatma sorununu finder, playheal (5 sinyal), drift heal, onboarding edit ve admin ayrı durumlarla, çoğu bellekte, ele alıyor.
2. En pahalı üç kusur kodda doğrulandı: (a) edit/rollback kaydı bekleyen finder işlerini uyandırmıyor, (b) finder'ın heal kapısı "sayfada aday yok" durumunu atlıyor (oysa heal bu katmanı zaten tanıyor), (c) restart sonrası `searching` işleri sahipsiz kalıyor.
3. Öneri: **Alternatif A (evrimsel)**: tek `problems` tablosu + olay günlüğü + izleyici (söz) kaydı; finder/heal/edit bunun "eylemleri" olur.
4. Teşhis LLM'siz ve kanıta dayalı tek bir fonksiyona iner; çıktısı katmandır (fetch / resolvers / provider / host / yetenek / halka kapalı). Heal tetik kapıları bunun sonucudur.
5. Motor boşluğu ayrı tablo (`capability_gaps`): boşluk kapanınca bekleyen sorunlar kendiliğinden yeniden denenir.
6. Host bilgisi tek sahibe iner: ölçülen kural DB'de (`stream_host_rules`), kalıcı tarif provider kütüphanesinde; terfi kapılı.
7. Ajan oturumları kısa ömürlü olur: her koşu taze oturum + sunucunun ürettiği durum özeti.
8. İlk üç adım tablo eklemeden/az ekleyerek bir hafta sonunda yapılır ve iki gerçek olayı (Evlilik Güzeldir, restart) kapatır.
9. API yalnız alan ekler; `finder` alanı aynen kalır, anlamı "arkada sahibi ve son tarihi olan bir sorun var" olur.
10. Radikal alternatif (kalıcı iş motoru) bugün gereksiz; yalnız iki parçası (kiralamalı iş kaydı, kısa oturum) sonradan alınır.

---

## 1. Yönetici özeti

**Teşhis.** Sistem beş ayrı "onarım girişi" taşıyor; her biri kendi kapısı, bekleme süresi ve bütçesiyle çalışıyor ve birbirinin sonucunu yalnız bir yerde (`state.py:273`) öğreniyor. Durumun çoğu süreç belleğinde (`sourcefinder.py:56-59`, `playheal.py:86-91`, `state.py:193-194`), bu yüzden restart hem işi hem kullanıcıya verilen sözü siler. Teşhis ile eylem iç içe: "heal çalışsın mı" kararı üç farklı yerde üç farklı ölçütle veriliyor.

**Yön.** Mevcut kodu koruyup tek bir sorun kaydı (durum makinesi) ile sarmak. Finder, playheal, heal ajanı, onboarding edit ve admin düğmeleri bu kaydın eylemleri olur; istemciye söz yalnız bu kayıttan verilir.

**İlk üç adım.**
1. Döngüleri kapat (tablo yok): edit/rollback sonrası finder yeniden denemesi, "aday yok" için heal kapısı, restart sonrası sahipsiz iş kurtarma, deploy öncesi çalışan iş kapısı.
2. `problems` + `problem_events` + `problem_watchers` tablolarını gölge modda yaz; admin'e tek "Sorunlar" görünümü.
3. Tek teşhis fonksiyonu + `capability_gaps`; finder ve playheal kapıları buna bağlanır.

---

## 2. Mevcut durumun doğrulanmış teşhisi

Kök neden sınıfı: **M** = mimari (yama ile kapanmaz), **A** = ayar/eşik, **Ö** = eksik özellik, **P** = ajan/prompt.

### 2.1 Zayıflıklar (Z1-Z10)

| # | İddia | Hüküm | Kanıt | Etki | Sınıf |
|---|---|---|---|---|---|
| Z1 | Ortak durum yok, çok giriş | **Doğru** | `onboard.save` yeni sürümü yazıp tarama başlatır, finder/issue/pencereye dokunmaz (`onboard.py:1255`, `1292`); finder yeniden denemesi yalnız uygulanan heal kaydından gelir (`state.py:273`); rollback de tetiklemez (`ops.py:447-461`) | Kullanıcı düzeltir, bekleyen bölüm 1 sa `not_found` kalır (`sourcefinder.py:142-146`) | M |
| Z2 | Dar heal kapısı | **Doğru, eksik anlatılmış** | Aday yoksa `_signal` boş döner (`sourcefinder.py:477-487`), adım atlanır (`652-654`). Oysa pencere bu durumu `discover` aşaması diye etiketler (`playheal.py:141`) ve heal bunu `resolvers` katmanına eşler (`heal_agent.py:157-158`). Kapı, var olan taksonomiyle çelişiyor | Tek bölümlük "oynatıcı bulunamadı" hiçbir yoldan onarılmaz: pencere en az 3 farklı kaynak ister (`playheal.py:228-231`) | M (karar üç yerde) + A |
| Z3 | Motor boşluğu kayboluyor | **Doğru** | Onay taslak JSON'una ve `_ops.json` akışına yazılır (`onboard.py:862-869`, `308-314`); akış 200 kayıtla sınırlı (`state.py:191`, `257`). Notlardaki `ENGINE_GAP:` / `needs code:` metnini ayrıştıran sunucu kodu yok (yalnız yönerge metinleri: `onboard.py:257-258`, `heal_agent.py:352`, `provider_match.py:186-195`) | Boşluk listesi yok; yetenek eklenince kimse bekleyeni uyandırmaz | M |
| Z4 | Heuristik doğrulanmıyor | **Büyük ölçüde giderilmiş** | `badhosts.py` yalnız izleyici host'ları içerir, medya görünümlü URL asla reddedilmez (`badhosts.py:3-5`, `16-20`, `40-48`); sıralama süre ölçümüyle (`videos.py:447-448`) | Kalan risk: finder'ın `_transient` / `_signal` metin eşleştirmesi (`sourcefinder.py:464-466`) | A |
| Z5 | Bilgi dağınık | **Doğru** (bir ayrıntı yanlış) | Kaynak başı `proxy_required/proxy_headers` (`db.py:149`), host kuralı (`db.py:185-188`), tarif alanları (`playheal.py:78-80` ipucu metni), env `STREAM_PROXY_HOSTS/FORCE` (`config.py:229-230`); öncelik sırası `streams.py:28-30`. `badhosts.py` artık host davranışı taşımıyor | "Çözüm nerede yaşar" her seferinde yeniden karar veriliyor | M |
| Z6 | Sürekli yama | **Doğru** | `config.py`'de 122 ayrı env adı, uygulama genelinde 143 (sayım); finder için 9 ayar (`config.py:323-337`), playheal için 9 (`config.py:280-306`); 7 resolver tipi (`resolvers/__init__.py:37-38`) | Her yeni durum yeni eşik getiriyor | M'nin belirtisi |
| Z7 | Bayat oturum + token | **Kısmen doğru** | Süreç uzun yaşamıyor: koşu en çok 900 sn (`config.py:272`). Bayat olan, aynı `--session-id` ile sürdürülen oturum dosyası (`pi_agent.py:168-169`, `onboard.py:844-846`). Katalog yeniden okuma yaması var (`onboard.py:278-300`). Token rakamı doğrulanmadı | Eski oturum yeni yeteneği bilmez | M (oturum modeli) |
| Z8 | Operasyonel kırılganlık | **Doğru** | `do_restart` çalışan iş kontrolü yapmadan restart eder (`deploy.sh:1158-1167`); açılışta yalnız onboarding taslakları kurtarılır (`main.py:58`, `onboard.py:930-942`). 12 test düşüşü doğrulanmadı (test koşulmadı) | Deploy = iş kaybı | Ö + M |
| Z9 | Trend dengesizliği | **Kısmen doğrulandı** | Site listeleri round-robin, eksik kalan `trend_score` ile doldurulur (`homelayout.py:278-292`); `added_at` pencereleri puan verir (`homelayout.py:57`) | Trend listesi olmayan site doldurmada tazelik puanıyla öne geçer | A (mimari değil) |
| Z10 | Gözlemlenebilirlik dağınık | **Doğru** | Olay akışı 6 kaynağı zaman sırasına dizer, durum taşımaz (`ops.py:290-345`) | "Bu sorun şu an kimde" sorusunun cevabı yok | M |

### 2.2 Hatalar (E1-E13)

| # | Hüküm | Kök neden / kanıt | Sınıf |
|---|---|---|---|
| E1 | Doğru | Öğrenme yalnız hata raporundan ya da finder'ın hızlı yoklamasından sonra (`streamdiag.py:397-419`, `436-449`); ilk oynatmada yoklama yok | Ö |
| E2 | Giderilmiş | bkz. Z4 | A |
| E3 | Doğru | bkz. Z2; ayrıca her yeni yapı yeni resolver tipi istiyor | M + Ö |
| E4 | Doğru | YouTube yalnız fragman host'u olarak geçiyor (`discover.py:66`); `providers/` altında yalnız `okru.py`, `vidmolly.py`, `recipes.py` var | Ö (yetenek) |
| E5 | Doğrulanmadı (ayrıntı) | `fetch_mode` http/browser var (`scraper/config.py:126-129`); `sitequality` okunmadı | Ö |
| E6 | Doğru | Site başına tek heal (`playheal.py:537-546`); finder 600 sn bekler (`sourcefinder.py:568-583`, `config.py:333`) | M (iş kuyruğu yok) |
| E7-E9 | Doğrulanmadı | Normalize/arama kodu bu incelemede okunmadı | - |
| E10 | Doğru | Drift heal ayrı yol, üç LLM çağırıcısı (`heal.py:191`, `239`, `278`), cooldown ortak (`heal.py:443-462`) | M (çift yol) |
| E11 | Doğru | bkz. Z7; "taslak değişmedi, hazır" dalı ajan hiçbir şey yapmasa da `ready` verir (`onboard.py:677-681`) | P + M |
| E12 | bkz. Z9 | | A |
| E13 | Doğru | bkz. Z8 | Ö |

### 2.3 Ek bulgular (belgede yok)

| # | Bulgu | Kanıt | Sınıf |
|---|---|---|---|
| X1 | **Restart sözü siler.** Çalışan finder işi `searching` satırı olarak kalır; durum ucu bunu `idle` döndürür, istemci yoklamayı sessizce bırakır | `sourcefinder.py:736-737`; `finder.js:4`; açılışta finder kurtarma yok (`main.py:49-64`) | M |
| X2 | **Finder bütçesi ve arama bekleme süresi bellekte**; günlük bütçe, 200 kayıtlık akıştan yeniden sayılır | `sourcefinder.py:58-59`, `620-634`; `state.py:191` | M |
| X3 | **Playheal'in beş sinyali var**, belge ikisini sayıyor: çözümleme penceresi, `stream_blocked`, sorun defteri, kapsama, dizi envanteri; ilk ikisi yalnız bellekte | `playheal.py:8-54`, `86-91`, `203-217` | Belge eksik |
| X4 | **Finder `not_found` sonucunu bildirmez**; kullanıcıya yalnız bulununca haber gider, vazgeçildiğini panel açıkken öğrenir | `sourcefinder.py:217-221` | Ö |
| X5 | **Edit ile heal aynı siteye iki ayrı ajan yolu**: edit, site tarama/heal altındayken reddedilir (`onboard.py:792-794`), heal kendi kapılarıyla (`heal_agent.py:128-138`) çalışır; ikisi de aynı yaml'ı sürümler ama yalnız heal sonrası yeniden deneme var | `onboard.py:1211-1212`, `state.py:273` | M |
| X6 | **Tek dosyada 4554 satır / 180 fonksiyon / 14 uç**: `routers/onboard_sandbox.py`. Analiz, kriter, oynatma takibi ve HTTP katmanı bir arada; `save` ve heal kapıları bunun özel fonksiyonlarını çağırıyor (`onboard.py:1220`, `1239`) | sayım | M (modül sınırı) |
| X7 | **Eski önbellek kolonları hâlâ okunuyor** (`source_items.resolved_*`) | `db.py:335-339`, `videos.py:381-384` | sadeleşme |
| X8 | **`tests_archive/` yerelde yok**; `server/tests/` altında 85 girdi var. CLAUDE.md "elenenler arşive taşınır" diyor | dizin listesi | Belge/kod çelişkisi |

### 2.4 Gerçekten mimari olanlar

Yama ile kapanmayanlar: **Z1, Z3, Z5, Z10, X1, X2, X5** ve Z2'nin "karar üç yerde" kısmı. Hepsi aynı eksikten doğuyor: kalıcı, tek sahipli bir sorun kaydı yok. Geri kalanı (Z4, Z9, E1, E4, E13, X4, X7) küçük ölçekli ve bağımsız düzeltilebilir.

---

## 3. Hedef mimari ilkeleri

| # | İlke | Neden | Bugün çözdüğü sorun |
|---|---|---|---|
| P1 | **Bir sorun, bir kayıt.** Her çözülmemiş durum `problems`'ta tek satır | Girişler çoğalsa da durum tek kalır | Z1, Z10, X5 |
| P2 | **Söz = kayıt.** İstemciye `searching` yalnız sahibi ve son tarihi olan açık sorun varsa döner; her sorun bir sonuçla kapanır ve izleyene bildirilir | Sözün arkası boş kalamaz | Z2, X1, X4 |
| P3 | **Önce teşhis, sonra eylem.** Teşhis LLM'siz, kanıt üretir, katman söyler; eylem katmana göre seçilir | Kapılar tek yerde | Z2, E3, E6 |
| P4 | **Ölçüm heuristiği yener.** Reddetme kararı yalnız ölçümle (yoklama, süre, HTTP kodu) verilir; ad/host tahmini yalnız sıralar | Yanlış varsayım regresyon üretmez | Z4, E2 |
| P5 | **Bilginin tek sahibi.** Site yaml = "oynatıcı nerede"; host davranışı = host kaydı + provider tarifi | "Çözüm nereye yazılır" sorusu biter | Z5, E1 |
| P6 | **Yetenek boşluğu birinci sınıf kayıt.** Boşluk kapanınca bekleyenler kendiliğinden döner | Kullanıcı takip etmez | Z3, E4 |
| P7 | **Durum diskte, ajan oturumu kısa.** Restart hiçbir işi ve sözü kaybetmez; ajan her koşuda güncel özetle başlar | Deploy güvenli, bayat bilgi yok | Z7, Z8, X1, X2 |
| P8 | **Yeni durum için yeni eşik yok.** Bekleme ve bütçe sorun kaydının alanıdır (deneme sayısı, sonraki deneme zamanı) | Ayar sayısı düşer | Z6 |

---

## 4. Alternatif mimariler

### 4.1 Alternatif A: Sorun kaydı ile sarma (evrimsel)

**Ana fikir.** Var olan modüller kalır; hepsi tek bir `problems` kaydı üzerinden konuşur. Yeni kod: bir tablo ailesi, bir teşhis fonksiyonu, bir "tick".

```
 istemci ──/api/streams──▶ videos.streams ──akış yok──▶ problems.open(episode)
 istemci ──playback-report▶ videos.feedback ─hata────▶ problems.open(episode) + watcher
 tarama  ──drift/kapsama──▶ runner/ingest ───────────▶ problems.open(site)
 admin   ──edit/rollback/heal düğmesi───────────────▶ problems.signal(site, "config_changed")
                                   │
                         ┌─────────▼──────────┐
                         │ problems (durum)   │◀── problem_events (günlük)
                         │ problem_watchers   │──▶ notifications (var)
                         └─────────┬──────────┘
                    tick (60 sn, APScheduler, var)
        ┌───────────┬──────────┬───┴────────┬──────────────┐
     diagnose    retry/search  host yoklama  heal ajanı   capability_gaps
     (LLM'siz)   (finder kodu) (streamdiag)  (heal_agent) (bekle)
```

**Oynatma sorunu yaşam döngüsü.** `/api/streams` akış çıkaramaz -> `problems.open(scope=episode)` (aynı parmak izi varsa ona izleyici eklenir) -> teşhis -> eylem -> doğrulama (yeniden çözümle + yoklama) -> `resolved` ya da `exhausted`; iki durumda da izleyenlere bildirim.

**Site ekleme/düzenleme.** Onboarding akışı aynen kalır. Kayıt (yeni sürüm) `problems.signal(site, "config_changed")` üretir: o sitenin açık sorunları `verifying`'e alınır. Ajanın çözemediği yetenek `capability_gaps`'e yazılır, taslak ona bağlanır.

**Mevcut kodla ilişki.** Korur: resolver/provider, `heal_agent` kapıları ve katmanları, `streamdiag`, `hostrules`, `playissues` (kanıt defteri olarak), onboarding. Birleştirir: finder adımları + playheal tetikleri + `rerun_for_site` -> sorun eylemleri. Siler (sonunda): bellek pencereleri, `finder_jobs` durum mantığı, finder/playheal eşiklerinin çoğu.

**Maliyet/risk.** Orta: 3 tablo, ~600-900 satır yeni kod, geçişte çift yazım. Risk: gölge dönemde iki durumun ayrışması.

**Ne zaman seçilmemeli.** Site sayısı 1-2'de kalacaksa ve sorun yılda birkaç kez çıkıyorsa C yeter.

### 4.2 Alternatif B: Kalıcı iş motoru + yetenek kaydı (radikal)

**Ana fikir.** Tarama, envanter, onboarding, heal, finder, TMDB: hepsi SQLite'ta kiralamalı (`lease_until`) işler. Thread'ler yerine tek iş yürütücü; her iş adım adım ilerler ve restart'ta kaldığı adımdan devam eder. Resolver tipleri ve provider'lar bir "yetenek kaydı"nda sürümlenir; ajan her koşuda bu kayıttan üretilen özeti alır.

```
 jobs(id, kind, key UNIQUE, state, step, lease_until, attempts, run_after, payload)
 worker döngüsü: lease al -> adımı çalıştır -> step++ -> commit
 kinds: scan | series_crawl | onboard_turn | repair | find_source | tmdb
 capabilities(key, version, since)  ◀── resolver TYPES + provider registry
 problems = jobs üzerinde bir görünüm (kind in repair/find_source)
```

**Yaşam döngüleri.** Oynatma sorunu = `find_source` işi; adımları teşhis/yeniden dene/ara/onar. Onboarding = her ajan turu ayrı `onboard_turn` işi (taze oturum). Deploy = "yeni iş alma, süren adımı bitir" sinyali + restart.

**Mevcut kodla ilişki.** `autoscan`, `playheal`, `sourcefinder`, `onboard` yürütme iskeletleri yeniden yazılır; `state.activity_*` ve `_ops.json` kalkar. Çekirdek (çözümleme, kapılar, sandbox) korunur.

**Maliyet/risk.** Yüksek: beş modülün eşzamanlılık iskeleti değişir; smoke set ile güvence zor; tarama gibi sorunsuz çalışan yolları da riske atar.

**Ne zaman seçilmemeli.** Bugün. Çözdüğü ek sorun (uzun işlerin restart'ta devamı) yalnız onboarding ve tarama için değerli; onboarding zaten tur bazlı yeniden başlatılabiliyor.

### 4.3 Alternatif C: Yalnız yama (asgari)

**Ana fikir.** Tablo yok. Bilinen dört kopukluk kancayla kapatılır: edit/rollback -> finder yeniden deneme; "aday yok" -> heal kapısı; açılışta sahipsiz finder kurtarma; deploy kapısı.

**Mevcut kodla ilişki.** Her şey kalır; 4-5 küçük değişiklik.

**Maliyet/risk.** Çok düşük. Ama Z3, Z5, Z10 ve "karar üç yerde" aynen kalır; bir sonraki olay yeni bir kanca ister (bugünkü birikimin tekrarı).

**Ne zaman seçilmemeli.** Tek başına hedef olarak; A'nın birinci adımı olarak doğru.

---

## 5. Karşılaştırma ve öneri

| Ölçüt | A: sorun kaydı | B: iş motoru | C: yalnız yama |
|---|---|---|---|
| Sadelik (son durum) | İyi: kapı ve eşikler tek yerde | En iyi kağıt üstünde, en çok yeni iskelet | Kötü: karmaşa artar |
| Kullanıcıya görünürlük | İyi: tek "Sorunlar" listesi | İyi | Değişmez |
| LLM/token maliyeti | Düşer: teşhis LLM'siz, ajan yalnız katmanı belliyken | Aynı + kısa oturum kazancı | Artabilir (daha çok heal tetiklenir) |
| Geçiş riski | Orta, gölge modla ölçülür | Yüksek | Çok düşük |
| Test edilebilirlik | İyi: durum makinesi saf fonksiyon | Orta: yürütücü testleri ağır | Mevcut |
| API/istemci uyumu | Tam (alan eklenir) | Tam | Tam |
| Yeni site kolaylığı | İyileşir (boşluk kaydı, kısa oturum) | En iyi | Değişmez |

**Öneri: A.** C'nin dört kancası A'nın ilk adımıdır, yani ilk hafta sonu C ile aynı işi yapar ve hemen fayda verir. B'den iki fikir ertelenmiş adım olarak alınır: kısa ajan oturumu (adım 6) ve deploy'da süren işi bekleme (adım 1'de basit kapı, adım 7'de kiralama).

**Neden B değil.** Kanıtlanan olayların hiçbiri taramanın ya da envanterin iş modelinden kaynaklanmıyor; B sorunsuz çalışan yolları da yeniden yazdırır ve smoke test stratejisiyle güvence altına alınamaz. **Neden yalnız C değil.** Z3/Z5/Z10 açık kalır ve "her olay yeni kanca" düzeni sürer.

---

## 6. Önerilen mimarinin ayrıntısı

### 6.1 Veri modeli

```sql
problems(
  id INTEGER PRIMARY KEY,
  fingerprint TEXT UNIQUE,        -- scope + site + sınıf + (host | canonical:episode)
  scope TEXT,                     -- episode | site | host
  site TEXT, host TEXT, canonical_id TEXT, episode_id TEXT,
  class TEXT,                     -- teşhis sınıfı (6.3)
  layer TEXT,                     -- fetch|resolvers|provider|host|capability|content|list|series_page|normalize
  state TEXT,                     -- 6.2
  parent_id INTEGER,              -- bölüm sorunu -> site sorunu
  gap_id INTEGER,                 -- capability_gaps.id (waiting_capability)
  evidence TEXT,                  -- JSON, <= 6000 karakter (heal kanıt biçimi)
  attempts INTEGER, agent_runs INTEGER,
  next_at INTEGER, deadline_at INTEGER,
  opened_at INTEGER, updated_at INTEGER, closed_at INTEGER,
  resolution TEXT)                -- retry|search|host_rule|heal|edit|capability|not_public|gave_up
problem_events(id, problem_id, at, actor, kind, note, data)   -- actor: client|tick|agent|admin|deploy
problem_watchers(problem_id, profile_id, canonical_id, episode_id, notified_at,
                 PRIMARY KEY(problem_id, profile_id, episode_id))
capability_gaps(id, key TEXT UNIQUE, title, proposal, status,  -- open|shipped|declined
                first_at, sites TEXT, shipped_at, catalog_hash)
```

Mevcut tablolarla eşleme:

| Mevcut | Karar | Not |
|---|---|---|
| `video_sources` (sağlık kolonları dahil) | Korunur | Kaynak sağlığı ile sorun ayrı kavram: sağlık "bu satır", sorun "bu durum" |
| `playback_attempts` | Korunur | Rapor eşleme belirteci |
| `playback_issues` | Korunur, kanıt defteri | `triggered_at` dedup'u `problems`'a geçer (adım 4) |
| `finder_jobs` | Geçişte çift yazım, sonra kalkar | Durum ucu `problems`'tan okur; eski satırlar salt-okur kalır |
| `notifications` | Korunur | Yeni tür eklenir (6.2) |
| `stream_host_rules` | Korunur, "host kaydı" olur | 6.4 |
| `blocked_pages` | Korunur | `content` sınıfı sorun buraya yazıp kapanır |
| `_ops.json` heals/onboard | Akış için kalır; durum kaynağı olmaktan çıkar | Olaylar `problem_events`'e de yazılır |
| Bellek: `_inflight`, `_title_search`, `_heal_runs`, `_windows`, `_blocked` | Kalkar (adım 4) | Yerine `problems` alanları |

### 6.2 Tek problem yaşam döngüsü

```
            open ──▶ diagnosing ──┬─▶ acting ──▶ verifying ──┬─▶ resolved
                         ▲        │      ▲           │        └─▶ acting (deneme+1)
                         │        │      └───────────┘
       config_changed ───┘        ├─▶ waiting_agent      (ajan meşgul / bütçe)
       capability_shipped ────────┼─▶ waiting_capability (gap_id)
       admin cevabı ──────────────┼─▶ waiting_user       (ask_user)
                                  ├─▶ closed:not_public  (telif kapısı)
                                  └─▶ exhausted          (deneme/son tarih doldu)
```

| Durum | Sahibi | Girişte olan | Zaman aşımı | İstemciye |
|---|---|---|---|---|
| `open` | tick | kayıt + izleyici | 10 sn içinde teşhise | `finder.state=searching` |
| `diagnosing` | `diagnose()` | sayfa getirilir, karar yazılır | 60 sn -> `acting(retry)` | `searching` |
| `acting` | eylem (retry / search / host yoklama / heal) | bir eylem, bir olay | eyleme göre: 20 sn / 60 sn / 10 sn / ajan süresi | `searching` (+ adım notu) |
| `verifying` | tick | zorla çözümle + hızlı yoklama + süre ölçümü | 30 sn | `searching` |
| `waiting_agent` | tick | `next_at` = ajan boşalınca / bütçe yenilenince | sorunun `deadline_at`'i | `searching`; son tarih geçerse `not_found` |
| `waiting_capability` | geliştirici | boşluk kaydına bağlanır | yok (olayla uyanır) | `not_found` + bildirim "motor desteği bekleniyor" |
| `waiting_user` | admin | soru admin'de görünür | yok | `not_found` |
| `resolved` | - | izleyenlere `source_found` | - | `found` |
| `exhausted` | - | izleyenlere `source_unavailable` (yeni tür) | 24 sa sonra yeni açılabilir | `not_found` |

**Geçiş olayları.** `stream_missing` (videos.streams), `playback_failed` (feedback), `drift`, `no_sources`, `config_changed` (heal uygulandı / edit kaydedildi / rollback), `host_rule_learned`, `capability_shipped`, `admin_retry`, `deadline`.

**İdempotentlik.** `fingerprint` benzersiz: aynı bölüm için ikinci istek yeni sorun açmaz, izleyici ekler (bugünkü `profiles` listesi, `sourcefinder.py:139-140`). Eylemler "bitti" olayını yazmadan durum ilerlemez.

**Restart sonrası devam.** Açılışta `acting`/`diagnosing`/`verifying` durumundaki sorunlar `open`'a döner (`attempts` korunur); ajan koşusu yarıda kaldıysa `agent_runs` düşülmez, olay "deploy kesti" yazılır. Bu, `onboard.recover_stale`'in (`onboard.py:930-942`) genellenmiş hâlidir.

**Son tarih.** Bölüm sorunu için varsayılan 15 dk (ajan süresi 300 sn + doğrulama 420 sn'ye göre; `heal.py:502`, `heal_agent.py:62`). Son tarih geçince istemci `not_found` görür, sorun arkada `waiting_agent` olarak sürebilir; çözülürse bildirim yine gider. Söz böylece hem zamanla hem sonuçla bağlanır.

**Bölüm -> site yükseltme.** Aynı site + sınıf + katmanda 2 farklı bölüm sorunu açıksa bir site sorunu açılır, bölümler `parent_id` ile bağlanır; ajan yalnız site sorununda çalışır. Tek bölümde de katman `resolvers`/`provider` ise ajan çalışabilir (bugünkü finder davranışı), ama bütçeyi site sorunu tutar.

### 6.3 Teşhis ve katman kararı

Tek fonksiyon: `diagnose(source_row) -> {class, layer, evidence, action}`. LLM yok; girdi olarak bugün zaten üretilen izleri kullanır (`videos._record` olayı `videos.py:324-329`, `streamdiag` hükmü, `playheal._entry` aşaması).

| Ölçülen kanıt | Sınıf | Katman | Eylem |
|---|---|---|---|
| Sayfa getirilemedi / challenge | `page_unreachable` | fetch | 1 kez yeniden dene; tekrarında site sorunu, ajan `fetch_mode` katmanında |
| `blocked:` kuralı eşleşti | `not_public` | content | `blocked_pages`'e yaz, kapat |
| Sayfa geldi, aday yok, sayfada iframe/script/JSON oynatıcı izi var | `player_not_found` | resolvers | ajan (site yaml) |
| Sayfa geldi, aday yok, oynatıcı izi de yok | `no_player` | content | telif kapısı hükmü; ajan yok |
| Aday var, host'u hiçbir provider tanımıyor | `unknown_host` | provider | `match_providers` (LLM'siz) -> tutmazsa ajan (yeni tarif) |
| Aday bilinen ama desteklenmeyen gömme türü (ör. YouTube tam bölüm) | `capability_missing` | capability | `capability_gaps`, bekle |
| Aday var, provider biliniyor, akış yok | `provider_changed` | provider | ajan (tarif) |
| Akış çözüldü, yoklama 401/403/medya değil, Referer/vekil düzeltiyor | `host_needs_headers` | host | host kuralı yaz, doğrula (ajan yok) |
| Akış çözüldü, yoklama reddi düzelmiyor | `stream_refused` | provider | ajan; "kod gerekir" derse boşluk kaydı |
| Akış çözüldü, yoklama iyi, süre uyuyor, istemci oynatamıyor | `client_side` | - | kapat (görünür not); 2 farklı istemci motorunda tekrarlarsa `provider` |
| Akış çözüldü, süre kısa (klip/reklam) | `wrong_media` | resolvers | sıradaki adayı dene; yoksa ajan |

**Heal tetik kapısının yerine geçen mantık.** "Ajan çalışsın mı" = `layer in (resolvers, provider, fetch, list, series_page, normalize)` **ve** LLM hesabı sağlıklı **ve** site için ajan boşta **ve** sitenin günlük ajan bütçesi var. Eşik tabloları (pencere 12, en az 3, oran 0.6, sorun 2 kaynak...) kalkar; yerlerine "site sorunu = 2 farklı bölüm" kuralı ve sorun başına deneme sayısı gelir. Ajanın dokunabileceği anahtarlar için bugünkü `LAYERS` kapısı aynen kullanılır (`heal_agent.py:128-138`).

**Doğrulama ilkesi.** Bir sorun yalnız ölçümle kapanır: zorla çözümleme akış verdi + hızlı yoklama kabul edildi + (çalışma süresi biliniyorsa) süre uyuyor. Ajanın "düzelttim" demesi ya da config'in değişmesi kapatmaz; yalnız `verifying`'e alır. Reddetme kararı veren her kural, kararın dayandığı ölçümü `evidence`'a yazmak zorundadır.

### 6.4 Bilgi sahipliği

| Bilgi | Tek sahibi | Yazar | Not |
|---|---|---|---|
| Oynatıcı sayfada nerede (seçici, resolver tipi) | Site yaml | Onboarding/heal ajanı | Host adı, başlık, vekil bilgisi burada YASAK (kapı: `provider` katmanı dışı anahtar) |
| Host'tan akış nasıl çıkar | Provider tarifi / modülü | Ajan (tarif), geliştirici (kod) | Değişmez |
| Host akışı nasıl sunar (Referer, vekil, IP bağı) | `stream_host_rules` | Yalnız doğrulanmış yoklama (`hostrules.py:80-97`) | Kaynak başı `proxy_required/proxy_headers` host kuralının önbelleği sayılır; yeni öğrenme hep host'a yazılır |
| Geçici zorlamalar | env `STREAM_PROXY_HOSTS/FORCE` | Kullanıcı | Yalnız acil durum; belgeye öyle yazılır |

**Terfi.** Bir host kuralı 30 günlük ömründe (`config.py:296`) en az 2 kez yenilenip hiç askıya alınmadıysa ve host bir tarife aitse, sistem tarife `stream_headers` önerisi üretir (LLM'siz, tarifin yeni sürümü). Öneri bugünkü tarif kapılarından geçer (diğer sitelerde regresyon denetimi, `heal_agent.py:49-51`) ve `heal_autoapply` ayarına uyar. Config referansı sunucu olduğundan terfi sunucuda olur; yerele `--pull-configs` ile gelir.

**Proaktif öğrenme (E1).** Yeni bir akış host'u ilk kez çözüldüğünde (host kaydı yok) arka planda bir kez hızlı yoklama yapılır; düzeltme bulunursa kural ilk oynatmadan önce yazılır. Maliyet: host başına bir istek, bölüm başına değil.

### 6.5 Motor boşluğu ve ajan oturumları

**Boşluk kaydı.** Üç kaynak `capability_gaps`'e yazar: (1) onaylı `engine_gap` sorusu (bugün taslağa yazılan kayıt, `onboard.py:263-275`), (2) ajan çıktısındaki `ENGINE_GAP:` / `needs code:` satırları (sunucu `submit_draft`/`submit_repair` notlarını ayrıştırır; bugün ayrıştırılmıyor), (3) teşhisin `capability_missing` sınıfı. `key` örnekleri: `provider:youtube_episode`, `resolver:<tip>`, `needs_code:<host>`.

**Kapanış.** `catalog_hash` = resolver tipleri + provider adlarının özeti. Açılışta özet değiştiyse açık boşluklar için sistem kendi denetimini yapar: boşluğa bağlı sorunlar `open`'a döner (teşhis yeniden çalışır; sınıf değiştiyse ilerler), bağlı taslaklar "yeniden denenebilir" işaretlenir. Kullanıcıya tek bildirim: "X yeteneği eklendi, N bekleyen iş yeniden deneniyor".

**Notla geçen taslak.** Notlarında boşluk satırı olan taslak `passed` olsa da admin'de "eksikle kaydediliyor: <boşluk>" uyarısıyla gösterilir; kayıt engellenmez (kullanıcı kararı), ama boşluk kaydı açılır.

**Kısa oturum.** `message` ve otomatik turlar aynı `--session-id`'yi sürdürmek yerine taze oturum açar; ilk mesaj sunucunun ürettiği özet olur: güncel yaml, son rapordaki `failing[]`, atlanan alanlar, açık boşluklar, güncel katalog tipleri, son 5 kullanıcı mesajı. Böylece katalog yeniden okutma yaması (`onboard.py:278-300`) gereksizleşir ve bağlam küçülür. Token kazancı ölçülmeden iddia edilmez (bkz. 9).

### 6.6 Operasyon

- **Deploy kapısı.** `deploy.sh` restart'tan önce sunucudan çalışan işleri sorar (onboarding, heal, tarama; bugün `state.activity_list`, sonra `problems.state=acting` + ajan). İş varsa durur; `--force-restart` ile geçilir. Salt-okur bir uç yeterlidir.
- **Restart dayanıklılığı.** 6.2'deki açılış kurtarması. Tarama zaten tick ile yeniden başlıyor; onboarding koşusu kesilirse taslak `failed(server_restart)` olur ve kısa oturum sayesinde "devam et" tek tıkla çalışır.
- **Kuyruk modeli.** Ayrı kuyruk altyapısı yok: `problems.next_at` + mevcut 60 sn tick. Site başına tek ajan kuralı korunur; sıradaki sorun `waiting_agent`'ta bekler (bugünkü 600 sn'lik uyuyarak bekleme, `sourcefinder.py:568-583`, kalkar).
- **Config referansı.** Değişmez: sunucu referans, yerel ayna. Edit, heal, rollback ve terfi aynı `config_changed` olayını üretir.
- **Bütçe.** Tek ayar: site başına günlük ajan koşusu. Sayım `problem_events`'ten (kalıcı), bellekten değil.

### 6.7 Gözlemlenebilirlik

Admin'de tek "Sorunlar" görünümü (Olay defteri akış olarak kalır):

```
 Durum          Kapsam                      Sınıf / katman            Bekleyen  Sahibi      Yaş
 onarılıyor     ddizi (site, 3 bölüm)       player_not_found/resolv.  2 profil  ajan 2/3    4 dk
 yetenek bekl.  ddizi · Evlilik Güzeldir    capability: youtube       1 profil  geliştirici 2 g
 doğrulanıyor   streambox.xyz (host)        host_needs_headers        -         tick        10 sn
 çözüldü        trdiziizle · Anne Yarısı 1  host_rule                 1 bildir. -           1 sa
```

Satır açılınca `problem_events` zaman çizgisi ve kanıt. Üstte üç sayaç: açık, geliştirici bekleyen, kullanıcı bekleyen. "Yetenek boşlukları" küçük ayrı liste.

### 6.8 İki gerçek olayın yeni mimaride işlenişi

**Evlilik Güzeldir 2. bölüm (ddizi, YouTube iframe).**

Bugün neden takıldı: çözümleme "Sayfada video sağlayıcısı bulunamadı" verdi (`videos.py:414-416`); finder'da aday olmadığı için heal atlandı (`sourcefinder.py:652-654`); edit kaydı finder'ı uyandırmadı (`onboard.py:1255`, `1292`); YouTube ihtiyacı yalnız nota yazıldı.

Yeni akış:
1. `/api/streams` akış yok -> `problems.open(episode, ddizi)`, izleyici = profil, `deadline_at` = +15 dk. İstemci `searching`.
2. `diagnosing`: sayfada aday yok ama `iframe[src*="/player/"]` izi var -> sınıf `player_not_found`, katman `resolvers`.
3. `acting(heal)`: ajan `resolvers` katmanıyla çalışır, seçiciyi genişletir; kapılar örneği oynatmayı dener.
4. Aday artık bulunuyor ama gömme YouTube ve provider yok -> ajan `needs code`/`ENGINE_GAP` der, sunucu ayrıştırır: `capability_gaps(provider:youtube_episode)`. Seçici düzeltmesi yine de uygulanır (diğer bölümler için doğru).
5. `verifying`: yeniden teşhis -> `capability_missing` -> `waiting_capability`. İstemci `not_found`; izleyene bildirim "kaynak var, motor desteği bekleniyor". Admin'de sorun "geliştirici bekliyor" olarak görünür.
6. Yetenek eklenip deploy edilir -> açılışta `catalog_hash` değişir -> sorun `open` -> teşhis temiz -> `verifying` (yoklama) -> `resolved` -> `source_found` bildirimi.
7. Kullanıcı bu arada edit ile kendi düzeltirse: kayıt `config_changed` üretir, 5. adımdaki doğrulama hemen çalışır.

**Anne Yarısı 1. ve 2. bölüm (trdiziizle, streambox Referer / twimg).**

Bugün neden takıldı: 1. bölümde Referer öğrenmesi kaynak başınaydı; 2. bölümde host adına bakan kural gerçek akışı reddetti ve ajan yanlış kanıtla çalıştı.

Yeni akış:
1. 1. bölüm çözülür; akış host'u `streambox.xyz` ilk kez görülüyor -> proaktif yoklama: düz istek 403, sayfa Referer'ı ile 200 -> host kuralı yazılır. İlk oynatma vekilden gider, hata hiç oluşmaz.
2. Yoklama yetişmeden kullanıcı oynattıysa: `playback_failed` -> `problems.open` -> teşhis `host_needs_headers` (katman host) -> kural -> `verifying` -> `resolved`, bildirim. Ajan çalışmaz, token harcanmaz.
3. 2. bölüm: akış `video.twimg.com/*.m3u8`. Reddetme yalnız ölçümle olur: yoklama 200, süre 138.7 dk, beklenen süreyle uyumlu -> akış kabul edilir, sorun hiç açılmaz.
4. Süre uymasaydı: sınıf `wrong_media`, kanıtta ölçülen ve beklenen süre; ajan doğru girdiyle çalışırdı.
5. Başkalarının Hayatı / Ömür Usta: aynı site + sınıf -> tek site sorunu; diğer bölümler ona bağlanır, ajan bir kez çalışır, kapanışta bağlı tüm bölümler doğrulanır ve izleyenlere bildirilir (bugünkü "heal çalışıyor diye `not_found`" yok).

---

## 7. Sadeleşme listesi

| Kalkar / birleşir | Yerine | Kanıt | Adım |
|---|---|---|---|
| `SOURCEFINDER_COOLDOWN`, `_HEAL_WAIT`, `_HEAL_POLL`, `_RERUN_HOURS`, `_RERUN_MAX`, `_DAILY_BUDGET`, `_MAX_JOBS` (7 ayar) | `problems.next_at/attempts` + tek `AGENT_DAILY_BUDGET` | `config.py:323-337` | 4 |
| `PLAYHEAL_WINDOW`, `_MIN_SOURCES`, `_FAIL_RATIO`, `_STREAM_MIN_SOURCES`, `_ISSUE_MIN_SOURCES`, `_ISSUE_TTL`, `_ISSUE_COOLDOWN` (7 ayar) | "2 farklı bölüm = site sorunu" kuralı | `config.py:280-300` | 4 |
| Bellek pencereleri ve sayaçlar | `problems` | `playheal.py:86-91`, `sourcefinder.py:56-59` | 4 |
| `rerun_for_site` + `state.py:273` kancası | `config_changed` olayı | `sourcefinder.py:586-617` | 4 |
| Finder'ın `_signal` / `_transient` metin eşleştirmesi | `diagnose()` | `sourcefinder.py:464-487` | 3 |
| `finder_jobs` durum mantığı | `problems` (tablo salt-okur kalır) | `db.py:211-215` | 4-5 |
| Drift heal'in `codex_cli` ve `pi` çağırıcıları | Yalnız `pi_agent` (kullanıcı kararı, bkz. 9) | `heal.py:191`, `239`, `278` | ayrı |
| `source_items.resolved_*` eski önbellek yolu | Silinir (fragman önbelleği `video_sources`'ta) | `db.py:335-339`, `videos.py:381-384` | ayrı |
| `BAD_STREAM_HOSTS` takma adı | `TRACKER_HOSTS` | `badhosts.py:21` | ayrı |
| Oturum sürdürme + katalog yeniden okuma yönergesi | Taze oturum + özet | `onboard.py:278-300`, `pi_agent.py:168-169` | 6 |
| `_ops.json` içindeki `engine_gap` kaydı | `capability_gaps` | `onboard.py:862-869` | 3 |
| Olay defterindeki ayrı `finder` / `playback_issue` / `heal` süzgeçleri (durum için) | "Sorunlar" görünümü | `ops.py:290-345` | 2 |

Korunması önerilenler (sadeleştirme adayı gibi görünse de): 7 resolver tipi (her biri gerçek bir site yapısına karşılık geliyor; birleştirme için kanıt yok), iki fetch modu, `heal_agent` kapıları, `playback_issues` defteri.

---

## 8. Geçiş planı

Her adım tek başına deploy edilebilir ve geri alınabilir. İlk üçü hafta sonu boyutundadır.

### Adım 1: Döngüleri kapat (tablo yok)

- **Hedef:** Z1, Z2, X1, E13'ün görünen kısmı.
- **İş:** (a) `onboard.save` (edit) ve `ops.rollback` sonunda `sourcefinder.rerun_for_site(site)` çağır. (b) `sourcefinder._signal`: iz `stage == "discover"` (aday yok) ise sinyal "oynatıcı bulunamadı" dönsün; ayrıca çözümleme izi hiç yoksa kaynak satırından iz üret. (c) Açılışta `finder_jobs.state='searching'` satırlarını `not_found` + `error='server_restart'` yap ve aynı bölüm için cooldown'suz yeniden başlat. (d) `deploy.sh`: restart öncesi çalışan iş sorgusu, iş varsa dur.
- **Dosyalar:** `scraper/onboard.py`, `routers/ops.py`, `library/sourcefinder.py`, `main.py`, `deploy.sh`.
- **Doğrulama:** 3 hedefli test (edit sonrası yeniden deneme; aday yok -> heal adımı çalışır; açılış kurtarma). Elle: bozuk seçicili bir bölümü aç, edit ile düzelt, bildirimin geldiğini gör.
- **Risk:** (b) daha çok ajan koşusu tetikler; günlük bütçe (10) sınırlar. **Geri alma:** dört küçük değişiklik ayrı commit; (b) için env anahtarı.

### Adım 2: Sorun kaydı, gölge mod

- **Hedef:** Z10; A'nın veri temeli. Davranış değişmez.
- **İş:** üç tablo (`db.py` SCHEMA eki); `library/problems.py` (`open/signal/event/close`, saf durum geçiş fonksiyonu); finder başlangıç/bitiş, playheal tetik, heal sonucu, edit kaydı, engine_gap onayı buraya da yazar. `GET /api/ops/problems` + admin "Sorunlar" sekmesi (`_ASSETS`'e yeni js).
- **Dosyalar:** `db.py`, yeni `library/problems.py`, `sourcefinder.py`, `playheal.py`, `heal_agent.py`, `onboard.py`, `routers/ops.py`, `static/admin/`.
- **Doğrulama:** durum geçiş fonksiyonu için tablo güdümlü tek test; elle: bir hafta gölge kaydı ile `finder_jobs` sonuçlarını karşılaştır (salt-okur sorgu).
- **Risk:** çift yazımda hata; tüm yazımlar `try/except` içinde, asıl akışı bozmaz. **Geri alma:** yazım çağrılarını kapatan tek bayrak; tablolar kalabilir.

### Adım 3: Tek teşhis + yetenek boşluğu

- **Hedef:** Z2 (kalıcı), Z3, E4.
- **İş:** `library/diagnose.py` (6.3 tablosu; girdi: çözümleme olayı + yoklama hükmü); finder heal kapısı ve playheal `layers_of` bunu kullanır. `capability_gaps` tablosu; ajan notlarından `ENGINE_GAP:` / `needs code:` ayrıştırma; açılışta `catalog_hash` denetimi.
- **Dosyalar:** yeni `library/diagnose.py`, `sourcefinder.py`, `heal_agent.py`, `onboard.py`, `onboard_pipeline.py`, `db.py`, admin.
- **Doğrulama:** teşhis için örnek-olay tablosu testi (iki gerçek olay dahil, kayıtlı HTML ile). Elle: YouTube'lu bölüm "yetenek bekliyor" görünür.
- **Risk:** teşhis yanlış sınıflarsa ajan gereksiz/eksik çalışır; gölge kaydında "eski kapı ne derdi" alanı tutulur, iki karar bir hafta karşılaştırılır. **Geri alma:** kapıyı eski `_signal`'e döndüren bayrak.

### Adım 4: Sorun kaydı sürücü olur

- **Hedef:** Z1 (kalıcı), Z6, X2, E6.
- **İş:** `problems.tick()` 60 sn'lik zamanlayıcıya eklenir; finder adımları eylem fonksiyonlarına ayrılır; `/api/streams` ve `/api/source-finder` durumu `problems`'tan okur; izleyici/bildirim buradan; bellek durumları ve 14 ayar kalkar; `source_unavailable` bildirim türü.
- **Dosyalar:** `problems.py`, `sourcefinder.py`, `playheal.py`, `videos.py`, `routers/notifications.py`, `config.py`, `API.md`, `tizen-client/js/api.js` (yalnız yeni alan/tür).
- **Doğrulama:** `test_client_contract` + `api_samples_client.js`; elle: restart ortasında açık sorunun devam ettiğini gör.
- **Risk:** en büyük adım. **Geri alma:** `PROBLEMS_DRIVER=0` eski yolu çalıştırır (eski kod bir sürüm boyunca silinmez).

### Adım 5: Host bilgisi tek sahip + proaktif yoklama

- **Hedef:** Z5, E1. **Dosyalar:** `streamdiag.py`, `hostrules.py`, `videos.py`, `providers/recipes.py`. **Doğrulama:** yeni host'ta ilk oynatma hatasız (elle). **Geri alma:** bayrak.

### Adım 6: Kısa ajan oturumu

- **Hedef:** Z7, E11. **Dosyalar:** `onboard.py`, `pi_agent.py`, `server/pi/` (özet biçimi; boyut bütçesi içinde). **Doğrulama:** aynı taslakta eski/yeni mod token ve tur sayısı karşılaştırması. **Geri alma:** bayrak (oturum sürdürmeye dön).

### Adım 7 (isteğe bağlı): Kiralamalı uzun işler, zarif kapanış

- **Hedef:** Z8'in kalanı. SIGTERM'de yeni iş almama + süren ajan turunu bekleme. Adım 1(d) yeterli gelirse yapılmaz.

### Bağımsız küçük işler

- Trend adaleti (Z9): doldurmada site başına eşit pay; `homelayout.py:278-292`. Mimariyle ilgisiz, istenildiğinde.
- `not_found` bildirimi (X4), eski önbellek kolonları (X7): tek commit'lik işler.

**Önce ölçülecek risk:** adım 1(b) sonrası günlük ajan koşusu sayısı ve başarı oranı. Bu sayı, adım 3-4'teki "eşik yerine teşhis" kararının maliyetini önceden gösterir.

---

## 9. Riskler ve açık sorular

### 9.1 Doğrulanmayanlar

| Konu | Neden | Nasıl doğrulanır |
|---|---|---|
| Canlı olay ayrıntıları (finder adım notları, "retry yok") | Sunucuya bağlanılmadı | `SELECT state,trigger,steps,error FROM finder_jobs ORDER BY id DESC LIMIT 50` (salt-okur) |
| Worker başına 100-450K token | Ölçüm kaynağı kodda yok | pi oturum dosyalarından tur/boyut sayımı |
| 12 bilinen test düşüşü, ~2700 test | Test koşulmadı | Smoke set bir kez koşulur |
| E5, E7, E8, E9 kök nedenleri | `sitequality`, `normalize`, `search_generic`, `series_dir` okunmadı | Hedefli kod okuması |
| `_hardening` kriterleri, `onboard_sandbox` iç akışı | 4554 satırın yalnız çağrı noktaları okundu | Adım 3 öncesi okunmalı |
| Deploy config karar tablosu, tombstone etkileşimi | `deploy.sh` yalnız restart bölümü okundu | Adım 1(d) sırasında |
| İstemcinin bilinmeyen bildirim türüne tepkisi | İstemci bildirim kodu okunmadı | `tizen-client/js` bildirim işleyicisi; gerekirse tür yerine `source_found` + ek alan |
| Trend doldurmanın site ayırt etmediği | Yalnız `_trending` gövdesi görüldü | `homelayout.py:278-292` + canlı liste sorgusu |

### 9.2 Varsayımlar

- Bölüm sorunu için 15 dk son tarih kabul edilebilir.
- "Tek bölümde de ajan çalışabilir" (adım 1b) token ekonomisiyle çelişmez; günlük bütçe korur.
- `finder_jobs` geçmişi taşınmaz, salt-okur kalır.
- Belge "3 heal türü" derken finder'ı tür sayıyor; kodda finder ayrı bir heal değil, aynı `heal_site_playback`'i çağırıyor (`sourcefinder.py:693`).

### 9.3 Kullanıcıya sorulacak kararlar

| # | Soru | Öneri |
|---|---|---|
| 1 | Tek bölümlük "oynatıcı bulunamadı" için ajan çalışsın mı? | Evet; site başına günlük bütçe ile |
| 2 | İstemciye söz için son tarih kaç dakika? | 15 dk; sonrası "bulunamadı, arkada sürüyor" |
| 3 | Vazgeçilen sorun bildirilsin mi (`source_unavailable`)? | Evet, tek satır; sessiz kalmasın |
| 4 | Notunda motor boşluğu olan taslak kaydedilebilsin mi? | Evet, uyarıyla; boşluk kaydı otomatik açılır |
| 5 | Host kuralı tarife kendiliğinden terfi etsin mi? | `heal_autoapply` açıksa evet, kapılardan geçerek |
| 6 | Drift heal'de `codex_cli`/`pi` yolları kaldırılsın mı? | Evet, yalnız `pi_agent`; sunucudaki gerçek ayar önce sorgulanmalı |
| 7 | Deploy, çalışan iş varken dursun mu? | Evet; `--force-restart` ile aşılır |
| 8 | Elenen testler için `tests_archive/` oluşturulacak mı? | Evet; smoke set tanımı adım 2'den önce netleşsin |

---

## 10. Ek: kanıt tablosu

| Dosya:satır | Ne gösteriyor |
|---|---|
| `server/app/library/sourcefinder.py:49` | Finder adımları `retry, search, heal` |
| `sourcefinder.py:56-59` | Tek-uçuş, arama zamanı, bütçe sayacı bellekte |
| `sourcefinder.py:139-140` | İkinci profil çalışan işe eklenir |
| `sourcefinder.py:142-146` | Biten iş sonrası cooldown (`found` dahil) |
| `sourcefinder.py:217-221` | Yalnız bulununca bildirim |
| `sourcefinder.py:464-466` | Geçici hata metin eşleştirmesi |
| `sourcefinder.py:477-487` | `_signal`: aday yoksa boş |
| `sourcefinder.py:568-583` | Çalışan heal'i uyuyarak bekleme |
| `sourcefinder.py:586-617` | `rerun_for_site` |
| `sourcefinder.py:620-634` | Günlük bütçe: bellek ya da ops akışı |
| `sourcefinder.py:639-640`, `652-654` | Heal adımının atlandığı iki dal |
| `sourcefinder.py:693` | Finder aynı `heal_site_playback`'i çağırır |
| `sourcefinder.py:736-737` | Restart'ta kesilen iş `idle` görünür |
| `server/app/library/videos.py:324-329` | Çözümleme olayı (teşhis girdisi) |
| `videos.py:335-345` | Her çözümleme playheal penceresine |
| `videos.py:381-384` | Eski fragman önbelleği yolu |
| `videos.py:414-416` | "Sayfada video sağlayıcısı bulunamadı" |
| `videos.py:447-448` | Süreye göre akış sıralama |
| `videos.py:603-612`, `652-654` | Akış yoksa finder isteği ve `finder` alanı |
| `videos.py:724-737` | Oynatma hatasından finder |
| `videos.py:777-780` | 1 hata suspect, 3 hata broken |
| `server/app/scraper/playheal.py:8-54` | Beş tetik sinyali |
| `playheal.py:78-80` | Tarif alanları ipucu (`stream_headers` vb.) |
| `playheal.py:86-91` | Pencere ve rezervasyon bellekte |
| `playheal.py:141` | Aday yok = `discover` aşaması |
| `playheal.py:203-217`, `228-231` | `evaluate` ve eşikler |
| `playheal.py:537-546` | Site başına tek heal |
| `playheal.py:600-639` | `maybe_trigger` kapıları |
| `server/app/scraper/heal_agent.py:49-51`, `62` | Regresyon örnekleri, doğrulama süresi |
| `heal_agent.py:128-138` | Katman -> yaml anahtarı kapısı |
| `heal_agent.py:157-158` | `discover` -> `resolvers` katmanı |
| `heal_agent.py:352` | `needs code` yönergesi |
| `server/app/scraper/heal.py:191`, `239`, `278` | Üç LLM çağırıcısı |
| `heal.py:443-462` | Cooldown kuralı |
| `heal.py:502` | Ajan süresi 300 sn |
| `server/app/scraper/state.py:191`, `257` | Ops akışı 200 kayıt |
| `state.py:193-194` | Etkinlik listesi bellekte |
| `state.py:273` | Uygulanan heal -> finder yeniden deneme (tek bağ) |
| `server/app/scraper/onboard.py:257-258` | ENGINE_GAP yönergesi |
| `onboard.py:263-275`, `308-314` | Onay taslağa yazılır |
| `onboard.py:278-300` | Katalog yeniden okutma yaması |
| `onboard.py:677-681` | "Taslak değişmedi, hazır" |
| `onboard.py:792-794`, `1211-1212` | Edit, tarama/heal sırasında reddedilir |
| `onboard.py:844-846` | Mesaj aynı oturumu sürdürür |
| `onboard.py:862-869` | Engine gap ops kaydı |
| `onboard.py:930-942` | Açılış kurtarması (yalnız taslaklar) |
| `onboard.py:1220`, `1239` | `save`, sandbox özel fonksiyonlarını çağırır |
| `onboard.py:1255`, `1292` | Yeni sürüm + tarama; finder/issue kancası yok |
| `server/app/scraper/pi_agent.py:168-169` | Oturum kimliğiyle sürdürme |
| `server/app/scraper/provider_match.py:186-195` | `needs_code` kararı |
| `server/app/scraper/badhosts.py:3-5`, `16-21`, `40-48` | Yalnız izleyici host'ları; medya reddedilmez |
| `server/app/scraper/discover.py:66` | YouTube yalnız fragman host'u |
| `server/app/scraper/resolvers/__init__.py:37-38` | 7 resolver tipi |
| `server/app/scraper/config.py:126-129` | `fetch_mode` |
| `server/app/library/hostrules.py:80-97` | Kural yalnız doğrulanmış yoklamadan |
| `server/app/library/streamdiag.py:397-419`, `436-449` | Öğrenme hata/yoklama sonrası |
| `server/app/routers/streams.py:28-30` | Başlık önceliği |
| `server/app/routers/ops.py:290-345` | Olay akışı birleştirme |
| `ops.py:447-461` | Rollback, finder'ı tetiklemez |
| `server/app/main.py:49-64` | Açılışta yalnız onboarding kurtarma |
| `server/app/db.py:149`, `185-188`, `211-215`, `335-339` | Vekil kolonları, host kuralı, finder_jobs, eski kolonlar |
| `server/app/config.py:229-230`, `272`, `280-306`, `296`, `323-337` | Vekil env, onboarding süresi, playheal, kural ömrü, finder ayarları |
| `server/app/homelayout.py:57`, `278-292` | Tazelik puanı, trend doldurma |
| `deploy.sh:1158-1167` | Restart öncesi iş kontrolü yok |
| `tizen-client/js/finder.js:4`, `13` | Yoklama `idle`'da durur; söz metni |
