# Faz 5 (sonra; kullanıcı onayıyla başlar): Heal'i aynı pi skill'ine taşımak

Sen bir worker'sın. Önce `docs/plans/site-onboarding/README.md` dosyasındaki ortak kuralları oku ve uygula.
Çıktın kısa olsun, özet geç.

**Önkoşul:** Faz 2c ve Faz 3 canlıda en az birkaç site eklemesiyle denenmiş olmalı. Kullanıcı "Faz 5'e başla"
demedikçe başlama.

## Amaç
Bugünkü heal (`server/app/scraper/heal.py`: tek atışlık prompt, `--no-tools`) yerine, onboarding ile **aynı skill**
ve aynı sandbox tool'larıyla çalışan, sayfayı kendisi sorgulayıp `test_config` ile doğrulayan ajan tabanlı bir heal.
Tek merkez: site bilgisi ve kalite kuralları tek skill'de toplanır.

## Kapsam
1. Skill'e "onarım modu" ekle (`SKILL.md`'de ayrı bölüm ya da `references/heal.md`):
   - Girdi: site_id, mevcut yaml, drift nedenleri, `last_good` metrikleri.
   - Kural: alan düşürme yok; attr/cast değişikliği gerekçesiz yok (bugünkü `_check_structure` kuralları);
     yalnızca bozuk selector'lar değişir.
2. Sandbox'a `load_site_config(site_id)` (salt-okuma) ve `test_config`'e `baseline` karşılaştırması (`_check_fill`
   eşdeğeri) ekle.
3. `heal.py`'de yeni bir sağlayıcı: `SCRAPER_HEAL_PROVIDER=pi_agent`. Mevcut `pi` ve `codex_cli` sağlayıcıları
   AYNEN kalır; varsayılan değişmez. Sonuç yine `_heal_impl`'in sandbox doğrulamasından ve `save_new_version`
   akışından geçer. Güvenlik katmanları atlanmaz: ajanın önerisi, eski yolun aynı denetimlerine tabidir.
4. Otomatik heal zaman aşımı (`SCRAPER_HEAL_AUTO_TIMEOUT`) ajan için ayrıca ayarlanabilir olsun (`SCRAPER_HEAL_AGENT_TIMEOUT`,
   varsayılan 300); cooldown kuralları aynen geçerli.
5. Admin heal olay detayında ajan günlüğü özeti (onboarding `events` biçimiyle).

## Testler
- `test_heal.py`'nin tüm senaryoları `pi_agent` sağlayıcısıyla da geçmeli (sahte süreç + kaydedilmiş olaylar).
- Eski sağlayıcılar için mevcut testler değişmeden yeşil.

## Kabul kriterleri
- Varsayılan davranış değişmemiş. `pi_agent` env ile açılınca çalışıyor. Rapor kısa.

## Genişletilmiş kapsam (kullanıcı kararı, 2026-10-01)
Heal'in amacı: sayfa yapısı değişince ya da video provider farklılaşınca hatayı OTOMATİK gidermek, yeni provider sorununu çözüp sisteme eklemek. Bugünkü heal yalnız liste sayfası drift'inde (`runner.py`) tetiklenir ve yalnız selector yazar; oynatma hatalarını görmez, resolver/provider'a dokunamaz. Faz 5 şunları da kapsar:
1. **Oynatma tetikli heal:** bir sitenin kaynaklarında (`video_sources`) aynı aşamada/aynı nedenle tekrarlayan çözümleme hatası eşiği (örn. son N çözümlemenin ≥%60'ı, en az 3 farklı kaynak, `state.record_resolver` izlerinden) bir heal işi başlatır. Cooldown/oran sınırı mevcut kurallarla aynı; otomatik uygulama `heal_autoapply` ayarına bağlı.
2. **Ajan tabanlı onarım:** onboarding ile AYNI skill ve sandbox araçları, "repair mode": girdi site_id, mevcut yaml, başarısız örnek URL'ler + çözümleme izi (aşama, host, hata), son iyi metrikler. Çıktı: (a) site yaml düzeltmesi (sayfa yapısı: selector, `resolvers:`, `providers:`) VE/YA (b) kütüphane için yeni/güncel provider tarifi (host'a göre; bkz. provider kütüphanesi).
3. **Modüler ayrım (ilke):** site tasarımı yaml'a, video host bilgisi provider kütüphanesine yazılır. Yeni host çıkınca tarif KÜTÜPHANEYE eklenir (site yaml'ına gömülmez), site yaml'ı yalnız ada başvurur. Mevcut provider farklı davranıyorsa (tür/yol/kalite değişimi) kütüphane tarifi güncellenir; tarif birden çok siteyi etkiler, bu yüzden uygulama öncesi o provider'ı kullanan HER sitenin örneklerinde doğrulanır (regresyon koruması).
4. **Doğrulama kapıları (atlanmaz):** öneri, onboarding'in `playable` kriterinin aynısıyla (≥3 farklı örnek, uçtan uca akış) ve eski çalışan örneklerle (regresyon) sandbox'ta doğrulanır; geçmeden sürüm artırılmaz. Geri alma (`rollback_config`) ve tarif sürümleme (`<ad>.vN.yaml`) aynı kalıpla.
5. **Sınırlar:** imza/çerez/TLS gerektiren host'lar (kod gerektirir) için ajan dürüstçe "needs code" raporlar; Olay defterinde heal olayı (`kind=heal`) ajan günlüğü özetiyle görünür.
Bağımlılık sırası: provider kütüphanesi tarifleri (veri-tabanlı provider) -> bu faz.
