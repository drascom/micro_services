# Eşleşen cihaz saniyeler sonra düşüyor — `permit_join` erken kapanıyordu

## Belirti

Yeni bir ampul eşleştirmede ağa katılıyor, interview tamamlanıyor, panel "eşleştirme tamam"
diyor — ve **saniyeler sonra** cihaz `device_leave` ile ağdan düşüyor. Aynı cihaz üst üste
denendiğinde bazen tutuyor. Cihaz: `0xa4c138462c230400`, Zigbee spec **revision 21**.

## Kanıt

Canlı günlükten korelasyon 4/4:

| Deneme | `permit_join` | Sonuç |
|---|---|---|
| 23:11 | interview'dan 7 sn sonra kapatıldı | 1 sn sonra `device_leave` |
| 23:12 | 6 sn sonra kapatıldı | 5 sn sonra `device_leave` |
| 01:10 | 6 sn sonra kapatıldı | 4 sn sonra `device_leave` |
| 01:25 | **hiç kapatılmadı** | **düşme yok, cihaz duruyor** |

Ağın kapatılmadığı tek denemede cihaz kaldı. Değişken tek: kapatma anı.

## Kök sebep

`public/index.html`, `trackPairingProgress()` (eski hâlinde `:3548-3560`): `interviewCompleted`
`true` olur olmaz `session.phase="ready"` oluyor, panel 1,2 sn sonra kendiliğinden
`POST /api/pairing/stop` çağırıp `permit_join`'i kapatıyordu. Sunucu tarafında suç yok —
`/api/pairing/stop` yalnız panelin istediğini yapıyor.

**Hipotez (r21):** revizyon 21 cihazlar katılımdan sonra güven merkezi bağlantı anahtarı (TCLK)
değişimini sürdürür. Interview bittiğinde bu **henüz tamamlanmamış** olabiliyor; ağı o anda
kapatmak süreci yarıda kesiyor ve cihaz ağdan ayrılıyor. Eski (r20 ve öncesi) cihazlarda bu adım
interview içinde bittiği için sorun görünmüyordu.

## Çözüm

Arayüz akışı **değişmedi**: diyalog yine kapanıyor, bildirim çıkıyor, isim/oda/rol adımları
başlıyor. Değişen tek şey ağın kapanma anı.

- `pairingNetworkHoldMs = 60000` — interview bittikten sonra ağ en az bir dakika açık kalır.
  `schedulePairingNetworkClose()` bu beklemeyi kurar.
- `closePairingNetworkIfIdle()` beklemeyi ve kurulum akışını birlikte gözetir:
  süre dolsa bile `setupFlowDeviceId()` bir cihaz döndürüyorsa (isim/oda/rol adımları sürüyorsa)
  kapatma ertelenir. `refresh()` her turda bu işlevi çağırdığı için kurulum bittiğinde ya da
  iptal edildiğinde kapatma kendiliğinden yapılır — hangisi önce olursa.
- Elle **"Aramayı durdur"** bilinçli eylem: `startPairing(false)` bekleyeni iptal edip anında
  kapatır. Aynı iptal `startPairing(true)`'da da var, böylece eski oturumun bekleyen kapatması
  yeni açılan ağı kapatamaz.
- **Sekme kapanır/panel yenilenirse** bekleyen kapatma askıda kalmaz: zamanlayıcı sayfayla
  birlikte kaybolur, ağı sunucunun kendi süresi (`seconds: 180`) söndürür. Bilinçli karar —
  panelde ekstra kalıcılık yok, kapanma garantisi sunucuda.

Kullanıcı zaten 180 sn istiyor; 60 sn'lik beklemenin üst sınırı bu, erken kapatmanın kazancı
yok denecek kadar az.

Testler: `src/dashboard-pairing-hold.test.ts` (sahte saatle; hemen kapatmama, elle anında
kapatma, kurulum sürerken erteleme, ikinci oturumun etkilenmemesi).

## Bir daha karşılaşılırsa

Günlükte **`permit_join ... time: 0` ile `device_leave` arasındaki mesafeye** bak. İkisi
saniyeler içinde ardışıksa sebep yine ağın erken kapanmasıdır — cihazın kendisi ya da menzil
değil. O durumda `pairingNetworkHoldMs` değerini yükselt (ve gerekiyorsa `/api/pairing/start`
gövdesindeki `seconds: 180`'i de), sonra aynı korelasyonu tekrar ölç.
