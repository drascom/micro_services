# T25 raporu: aşama örnek düzenleyici (Sil/Düzenle/Ekle, "Yalnız benim örneklerim")

**Madde 1 bulgusu (neden kullanılamadı):** T18 "çıkar" yalnız son ölçümün örnek listesinde, `sample` eylemi izinliyken (bekliyor/geçti/yardım) ve aşama koşmuyorken görünüyordu: koşan/onay/motor boşluğu kartında yok (kullanıcının yabancidizi A5'i `running`). Asıl neden şuydu: çıkarma yalnız `ctx.allowed` çağıran yerlerde (A2, A3, devir) uygulanıyordu. A4 (`meta.title`), A5 (`meta.episodes/title/player`'dan yeniden türetilen bölümler), A6 (`meta.player`) ve A7 (`meta`) bunu süzmüyordu. Bu yüzden çıkarılan ya da eski otomatik örnekler her koşuda geri geliyordu. Kullanıcı örneği yalnız başa ekleniyor, eskiler de ölçülüyordu ("eskilere de bakıyor"). Verilen örnekler ayrı listelenmiyordu, çıkarma da hemen yeniden koşturuyordu.

**Yapılan**
- `StageCtx.own_only` + `allowed(url, own=)` + `pool(urls)`: A4/A5/A6/A7 örnek kaynakları (meta, devir, keşif) artık tek süzgeçten geçiyor; dışlananlar asla dönmüyor. "Yalnız benim" açıkken yalnız `user_samples` ölçülüyor (A4'te verilen bölümden türetilen dizi sayfası kalıyor; onarım koşusu anahtarı yok sayıyor).
- `stages/samples.py`: `listing` (adres, kaynak `senin`/`önceki aşamadan`/`otomatik`, son ölçüm ✓/✗ + not, `used`), `add`, `edit` (önce yeni adres doğrulanıyor, sonra eski dışlanıyor), `delete`, `restore`, `toggle`, `run`. Yalnız `run` koşturuyor. `min_needed` eşiği beklentiden okuyor (A2/A7=1, A3=3, A4-A6=2). Az örnekte "En az N örnek gerekiyor; şu an M" çıkıyor: `run` 400 döndürüyor, zincir ya da rerun ile başlayan koşu ise `needs_human` oluyor.
- `actions.sample/exclude(run=False)`. Uçlar: `GET/POST /api/control/sites/{id}/stages/{st}/samples` (`op`: add|edit|delete|restore|toggle|run; başka host 400).
- Admin (A2-A7, A1 eskisi gibi): "Örnekler" tablosu (`cardSync.samplesHtml`, saf işlev). Satır içi Düzenle (Kaydet/Vazgeç; düzenlerken yoklama bölümü çizmez), Sil (onaylı), Ekle kutusu, "Silinenler (N)" katı + geri al, "Yalnız benim örneklerimi kullan" düğmesi, örnek verilince "Sadece bunları kullan?" önerisi, "Örneklerle yeniden çalıştır". Çıktılar kaçışlı (`qesc`), bölüm farkı anahtarına `sample_list` eklendi. `status.overview` yanıtında `sample_list` ve `own_samples_only` var.

**Sapma:** Anahtarı `site_stages`'te tutmak için yeni migrasyon gerekti: `app/migrations/021_own_samples.py` (`own_samples_only INTEGER DEFAULT 0`, yalnız ekleme). İzin verilen dizinlerin dışında.

**Testler:** `tests/test_t25_sample_editor.py` (5 test, ~0.3 sn) + `tests/js/samples_test.js`:
- silinen devir örneği A2'den yeniden türetilse de dönmüyor;
- Düzenle'de başka host 400 dönüyor ve hiçbir şey değişmiyor; eski adres dışlanıyor, yeni adres `senin` oluyor;
- anahtar açıkken 1 örnekte mesaj çıkıyor (run 400, koşu `needs_human`), 3 örnekte yalnız bunlar ölçülüyor;
- `reset_after` sonrası örnekler, dışlamalar ve anahtar kalıyor;
- düğüm testinde tablo, kaçış ve bölüm planı doğrulanıyor.

`test_a4_ownership` sahte ctx'i için A4'e `_series_pool` geri dönüşü eklendi.

**check.sh:** YESIL (6 adım), 375 test, ~14.6 sn.
**Elle kontrol (kullanıcı):** A3/A5 kartında Örnekler tablosu, Düzenle/Sil/Ekle, Silinenler katı, anahtar ve az örnek mesajı, "Örneklerle yeniden çalıştır".
