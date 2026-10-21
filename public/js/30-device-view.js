  const isAlert=device=>Array.isArray(device.alerts)&&device.alerts.length>0;
  const criticalAlert=device=>Array.isArray(device.alerts)?device.alerts.find(alert=>alert?.severity==="critical")||null:null;
  const criticalAlertKeys={smoke:"smokeAlarmDevice",carbon_monoxide:"carbonMonoxideAlarmDevice"};
  const batteryPercent=device=>typeof device.state?.battery==="number"?Math.round(Math.max(0,Math.min(100,device.state.battery))):null;
  const lowBatteryThreshold=()=>state.settings?.alerts?.lowBatteryThreshold??15;
  const hasLowBattery=device=>{
    if(Array.isArray(device.alerts)&&device.alerts.some(alert=>alert?.code==="low_battery"))return true;
    const battery=batteryPercent(device);
    return battery!==null&&battery<=lowBatteryThreshold();
  };
  const deviceNeedsAttention=device=>device.availability==="offline"
    ||device.supported===false
    ||device.interviewCompleted===false
    ||isAlert(device);
  // Otomatik onarım izi: cihaz kendini ilan edince köprünün yazdığı `self_heal` olayı.
  const selfHealLabelKeys={attempt:"selfHealAttempt",ok:"selfHealOk",failed:"selfHealFailed"};
  /* Her olay üç şey söyler: işaret (`icon`), sözü (`label`) ve rengi (`tone`). Dördüncü alan
     `quiet`, olayın "ev hareketleri" listesinde satır hak edip etmediğini belirler.
     Ayrım VERİDENDİR — olayın özelliği ve değeri; cihaz adına/modeline bakan hiçbir kural yok:
       quiet=true  → bir DEĞİŞİM değil, "her şey yolunda" bildirimi: cihaz yeniden erişilebildi,
                     otomatik onarım denendi/tuttu, pil yeniden normale döndü. Bunlar listeyi
                     "Hazır · 3 dk önce" satırlarıyla dolduruyordu; artık tek satırlık sessiz
                     özette sayılırlar.
       quiet=false → gerçek değişim: düğmeye basıldı, kapı açıldı/kapandı, hareket başladı/bitti,
                     duman/CO, pil zayıfladı, cihaz erişilemez oldu, onarım başarısız, ışık yandı.
     `tone` durum renklerinin bilinen anlamını sürdürür: alert kırmızı, active kehribar/yeşil,
     muted sönük. */
  const eventPresentation=event=>{
    const enabled=event.value===true||String(event.value).toUpperCase()==="ON";
    if(event.property==="action")return{icon:"◉",label:String(event.value).replaceAll("_"," "),tone:"active",quiet:false};
    if(event.property==="contact")return{icon:"⌑",label:event.value?t("doorClosed"):t("doorOpen"),tone:event.value?"muted":"alert",quiet:false};
    if(event.property==="occupancy"||event.property==="presence")return{icon:"⌁",label:event.value?t("motionDetected"):t("motionClear"),tone:event.value?"active":"muted",quiet:false};
    if(event.property==="smoke")return{icon:"△",label:event.value?t("smokeDetected"):t("noSmoke"),tone:event.value?"alert":"muted",quiet:false};
    if(event.property==="carbon_monoxide")return{icon:"△",label:event.value?t("coDetected"):t("coClear"),tone:event.value?"alert":"muted",quiet:false};
    if(event.property==="battery_low"||event.property==="battery_threshold")return{icon:"▱",label:event.value?t("batteryLow"):t("batteryOkay"),tone:event.value?"alert":"muted",quiet:!event.value};
    if(event.property==="availability")return{icon:"●",label:event.value==="online"?t("ready"):t("unreachable"),tone:event.value==="online"?"muted":"alert",quiet:event.value==="online"};
    if(event.property==="self_heal")return{icon:"⟳",label:t(selfHealLabelKeys[event.value]||"selfHealAttempt"),tone:event.value==="failed"?"alert":"muted",quiet:event.value!=="failed"};
    if(event.property==="state")return{icon:"◉",label:enabled?t("on"):t("off"),tone:enabled?"active":"muted",quiet:false};
    return{icon:"•",label:String(event.value).replaceAll("_"," "),tone:"muted",quiet:false};
  };
  // Alt yazı önce sunucunun sınıfına bakar (tahmin ya da kullanıcının seçtiği rol); sınıf
  // belirsizse eski kontrol tabanlı yedeğe düşer, böylece hiçbir cihaz etiketsiz kalmaz.
  const deviceCategoryLabels={light:"lightDevice",switch:"switchDevice",cover:"coverDevice",climate:"climateDevice",lock:"lockDevice",fan:"fanDevice"};
  // Sınıfı yalnız ana kontroller belirler: `find_switch` gibi ayar niteliğindeki (adminOnly)
  // aç/kapa alanları sayılmaz, yoksa bir varlık sensörü "Controller" diye etiketlenir.
  const hasMainControl=(device,...kinds)=>device.controls.some(control=>kinds.includes(control.kind)&&control.adminOnly!==true);
  const deviceKind=device=>{
    const labelled=deviceCategoryLabels[device.category];
    if(labelled&&!hasMainControl(device,"siren"))return t(labelled);
    return hasMainControl(device,"cover","position")?t("coverDevice"):device.controls.some(control=>control.kind==="climate")?t("climateDevice"):hasMainControl(device,"lock")?t("lockDevice"):hasMainControl(device,"fan")?t("fanDevice"):hasMainControl(device,"siren")?t("sirenDevice"):hasMainControl(device,"switch")?t("controller"):device.features.includes("contact")?t("doorSensor"):device.features.some(x=>x==="presence"||x==="occupancy")?t("motionSensor"):device.features.includes("smoke")?t("smokeSensor"):device.features.includes("carbon_monoxide")?t("coSensor"):t("sensor");
  };
  // Bir kanal gösteriliyorsa sınıf o kanalın rolünden gelir: çok kanallı anahtarda bir kanal
  // lamba, diğeri priz olabilir. Kanal yoksa cihazın (kanallardan türetilen) sınıfı kullanılır.
  const deviceIconKind=(device,control)=>{
    const features=device.features||[];
    const identity=`${device.name||""} ${device.model||""}`.toLowerCase();
    const category=(control?.kind==="switch"&&control.category)||device.category;
    if(features.includes("smoke")||features.includes("carbon_monoxide"))return"smoke";
    if(category==="cover"||device.controls.some(control=>control.kind==="cover"||control.kind==="position"))return"cover";
    if(category==="lock"||device.controls.some(control=>control.kind==="lock"))return"lock";
    if(category==="fan"||device.controls.some(control=>control.kind==="fan"))return"fan";
    if(device.controls.some(control=>control.kind==="siren"))return"siren";
    if(features.includes("contact"))return"door";
    if(features.some(feature=>feature==="presence"||feature==="occupancy"))return"motion";
    if(category==="light")return"light";
    if(/\b(remote|kumanda)\b/.test(identity))return"remote";
    if(/\b(plug|socket|outlet|priz)\b/.test(identity))return"plug";
    if(category==="switch")return"switch";
    if(features.some(feature=>["brightness","color","color_temp"].includes(feature))||/\b(light|lamp|bulb|led|ışık|lamba)\b/.test(identity))return"light";
    if(device.controls.some(control=>control.kind==="switch"))return"switch";
    if(features.some(feature=>["temperature","humidity","illuminance"].includes(feature)))return"climate";
    return"sensor";
  };
  const deviceIconSvg=kind=>{
    const icons={
      light:'<path d="M9 18h6m-5 3h4m-6.1-7.1A6 6 0 1 1 16.1 14c-.8.7-1.1 1.4-1.1 2H9c0-.7-.3-1.4-1.1-2.1Z"/>',
      switch:'<path d="M12 3v8m-4.9-5.1a7 7 0 1 0 9.8 0"/>',
      plug:'<path d="M8 3v5m8-5v5m-9 0h10v2a5 5 0 0 1-5 5v6m-3 0h6"/>',
      remote:'<rect x="7" y="2.5" width="10" height="19" rx="3"/><circle cx="12" cy="7" r="1.5"/><path d="M10 12h4m-4 4h.1m3.8 0h.1"/>',
      door:'<path d="M5 21V4l12-1v18M5 21h14M14 12h.1"/>',
      motion:'<circle cx="12" cy="6" r="2.2"/><path d="m9 21 1-6-2-3 2-2 4 1 2 4m-1 6-3-6m6-7c1.5 1 2.5 2.4 3 4M6 8c-1.5 1-2.5 2.4-3 4"/>',
      smoke:'<path d="M12 3 3 20h18L12 3Z"/><path d="M12 9v5m0 3v.1"/>',
      climate:'<path d="M10 14.8V5a2 2 0 1 1 4 0v9.8a4 4 0 1 1-4 0Z"/><path d="M12 8v8"/>',
      cover:'<path d="M5 4h14v16H5zM5 8h14M5 12h14M5 16h14"/><path d="m9 2 3 3 3-3"/>',
      lock:'<rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
      fan:'<circle cx="12" cy="12" r="2"/><path d="M12 10c-1-5 1-7 3-7 3 1 3 5-1 8M14 12c5-1 7 1 7 3-1 3-5 3-8-1M12 14c1 5-1 7-3 7-3-1-3-5 1-8M10 12c-5 1-7-1-7-3 1-3 5-3 8 1"/>',
      siren:'<path d="M8 18v-7a4 4 0 0 1 8 0v7M5 21h14M4 10H2m20 0h-2M6 4 4.5 2.5M18 4l1.5-1.5M12 3V1"/>',
      sensor:'<circle cx="12" cy="12" r="3"/><path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"/>',
      overview:'<path d="M4 11 12 4l8 7"/><path d="M6 10v9h12v-9"/><path d="M10 19v-5h4v5"/>',
      /* Rutin ve tepkisel kural: aynı ikon ailesinden iki İŞLEV ikonu (not §5.4 — ikon markayı
         değil yapılan işi anlatır). Zaman/güneş tetikleyen kural saattir, olay dinleyen kural
         şimşek. Ayrım `genSceneCatalog`ın `kind` alanından gelir, ada bakan hiçbir kural yok. */
      routine:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
      reactive:'<path d="M13 3 5 14h6l-1 7 8-11h-6l1-7Z"/>',
      manual:'<circle cx="12" cy="12" r="8.5"/><path d="m10 8 6 4-6 4V8Z"/>',
      group:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'
    };
    return`<svg class="device-type-svg" viewBox="0 0 24 24" aria-hidden="true">${icons[kind]||icons.sensor}</svg>`;
  };
  const deviceTypeIcon=(device,control)=>deviceIconSvg(deviceIconKind(device,control));
  /* CİHAZIN GÖRSEL ANAHTARI — model kodudur, IEEE değil: `/api/device-image/:model` upstream
     kataloğuna bakar, katalog cihazı değil MODELİ tanır. Ama model adının geçerli olması görselin
     VAR olduğu anlamına gelmez (ör. `TS0601_u8ouaqsz` katalogda yok). Sunucu bunu önbellekten
     bilir ve `image.available===false` diye söyler; o durumda `<img>` HİÇ kurulmaz — istek
     atılmaz, konsol 404 ile dolmaz, yerine tür ikonu geçer.
     `available` henüz bilinmiyorsa (null) davranış eskisi gibidir: istek atılır, `onerror`
     yakalar. Böylece daha önce görünen hiçbir görsel kaybolmaz. */
  const deviceImageModel=device=>{
    if(!device.image)return device.model;
    return device.image.available===false?null:device.image.model;
  };
  const deviceVisualFor=(device,imageModel)=>{
    const fallback=`<span class="device-fallback"${imageModel?" hidden":""}>${deviceTypeIcon(device)}</span>`;
    if(!imageModel)return`<div class="device-visual">${fallback.replace(" hidden","")}</div>`;
    const source=`/api/device-image/${encodeURIComponent(imageModel)}`;
    return`<div class="device-visual"><img class="device-photo" src="${esc(source)}" alt="" data-device-image data-model="${esc(imageModel)}">${fallback}</div>`;
  };
  // Sinyal rozeti ve yeniden adlandırma kalemi başlıktaki adın yanında durur; gövdede ayrı satır yok.
  const deviceDetailMetaHtml=device=>`${linkQualityBadge(device)}<button class="device-name-edit" type="button" data-admin-only data-rename="${esc(device.id)}" aria-label="${esc(t("changeName"))}" title="${esc(t("changeName"))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16-1 5 5-1L19 9l-4-4L4 16Z"/><path d="m13.5 6.5 4 4"/></svg></button>`;
  const deviceDetailPhoto=device=>{
    const imageModel=deviceImageModel(device);
    if(!imageModel)return"";
    return`<div class="device-detail-photo" data-device-photo hidden><img class="device-photo" src="/api/device-image/${encodeURIComponent(imageModel)}" alt="" data-device-image data-model="${esc(imageModel)}"><button class="image-edit-overlay" type="button" data-admin-only data-change-image="${esc(device.id)}" aria-label="${esc(t("changeImage"))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h4l2-2h4l2 2h4v15H4V5Z"/><circle cx="12" cy="12" r="4"/></svg><span>${t("changeImage")}</span></button></div>`;
  };
  /* Ham LQI (0-255). Sunucu son değeri cihaz başına saklar (`device.linkquality`); doğrudan kipte
     sinyal cihaz durumunun parçası olmadığı için tek güvenilir kaynak odur. Eski gölge payload'ı
     için `state.linkquality` yedekte kalır. Değer yoksa `null` döner — uydurma değer yok. */
  const rawLinkQuality=device=>{
    const value=Number(device.linkquality??device.state?.linkquality);
    return Number.isFinite(value)?Math.round(Math.max(0,Math.min(255,value))):null;
  };
  const linkQualityPercent=device=>{
    const value=rawLinkQuality(device);
    return value===null?null:Math.round(value/255*100);
  };
  const linkQualityBadge=device=>{
    const percent=linkQualityPercent(device);
    const tone=percent===null?"":percent>=70?"strong":percent>=25?"good":"weak";
    const label=`${t("signal")} ${percent===null?"—":`${percent}%`}`;
    return`<span class="device-link-level${tone?` ${tone}`:""}" title="${esc(label)}" aria-label="${esc(label)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 16a10 10 0 0 1 14 0M8 19a6 6 0 0 1 8 0M11 22a2 2 0 0 1 2 0"/></svg><span>${percent===null?"—":`${percent}%`}</span></span>`;
  };
  /* Gizlenenler listesi: kayıtta olmayan her şey görünür. Yıldız/favori değil — "bu kartta
     göster/gizle". Karar artık KART BAŞINA verilir: aynı lamba salon kartında durup "Işıklar"
     kartında gizlenebilir. Kapsam = kartın kimliği; kapsamsız (eski) kayıt her kartta geçerlidir. */
  const isTileHidden=(scope,deviceId,controlId)=>
    state.hiddenTiles.has(tileVisibilityKey(scope,deviceId,controlId))
    ||state.hiddenTiles.has(legacyTileVisibilityKey(deviceId,controlId));
  /* Kart bilmeyen yüzeyler (cihaz detayı, ışık kolonu) için: cihaz HERHANGİ bir kartta gizli mi?
     Oradaki göz tek bir kartın değil, cihazın tamamının kararını verir. */
  const isDeviceHiddenAnywhere=(deviceId,controlId)=>{
    const wanted=controlId||groupDeviceControlId;
    if(state.hiddenTiles.has(legacyTileVisibilityKey(deviceId,wanted)))return true;
    for(const key of state.hiddenTiles){
      const parsed=parseVisibilityKey(key);
      if(parsed&&parsed.scope&&parsed.deviceId===deviceId&&parsed.controlId===wanted)return true;
    }
    return false;
  };
  /* Sunucudaki şekil: `{hiddenDevices:[{deviceId,controlId,scope?}],hiddenGroups:[groupId]}`.
     Panelde kayıt tek dizeli anahtarla taşınır; çeviri burada. Kapsamsız anahtar `scope`
     ALANINI HİÇ ÜRETMEZ — eski kayıt sunucuya olduğu gibi geri yazılır. */
  function visibilityPayload(){
    return{
      hiddenDevices:[...state.hiddenTiles].map(parseVisibilityKey).filter(Boolean).map(entry=>
        entry.scope
          ?{deviceId:entry.deviceId,controlId:entry.controlId,scope:entry.scope}
          :{deviceId:entry.deviceId,controlId:entry.controlId}),
      hiddenGroups:[...state.hiddenGroups]
    };
  }
  /* Yerel yansı yalnız çevrimdışı açılış içindir: karar sunucudadır, bu kayıt onu ezmez. */
  function cacheVisibility(){
    try{localStorage.setItem(visibilityCacheKey,JSON.stringify({
      hiddenDevices:[...state.hiddenTiles],
      hiddenGroups:[...state.hiddenGroups]
    }))}catch{}
  }
  function applyVisibility(visibility){
    const devices=Array.isArray(visibility?.hiddenDevices)?visibility.hiddenDevices:[];
    const groups=Array.isArray(visibility?.hiddenGroups)?visibility.hiddenGroups:[];
    state.hiddenTiles=new Set(devices
      .filter(entry=>entry&&typeof entry.deviceId==="string"&&typeof entry.controlId==="string")
      .map(entry=>typeof entry.scope==="string"&&entry.scope
        ?tileVisibilityKey(entry.scope,entry.deviceId,entry.controlId)
        :legacyTileVisibilityKey(entry.deviceId,entry.controlId)));
    state.hiddenGroups=new Set(groups.filter(id=>typeof id==="string"&&id));
    cacheVisibility();
  }
  /* Yazma hatası sessiz kalmaz: kullanıcı görünür uyarı alır, sunucu tarafında da
     `RecentErrorLog`'a düşer. Panel son bilinen değerle çalışmaya devam eder. */
  async function saveHomeVisibility(){
    cacheVisibility();
    try{
      await api("/api/home-visibility",{method:"PUT",body:JSON.stringify({visibility:visibilityPayload()})});
      return true;
    }catch(error){showToast(t("visibilitySaveFailed",{error:error.message}),true);return false}
  }
  /* KART BAŞINA gizleme. Kapsam kartın kimliğidir; kapsamsız eski kayda denk gelinirse kayıt
     burada GÖÇ EDER: silinmeden önce cihazın göründüğü HER kartın kapsamına yazılır, ancak
     ondan sonra bu kartınki kaldırılır. Böylece "her yerde gizliydi" tercihi öteki kartlarda
     aynen sürer — kimse tercihini kaybetmez. Göç kullanıcının o karta baktığı anda olur, yani
     oda listesinin yüklü olduğu an: erken çalışıp boş listeye göç etmesi mümkün değil. */
  let visibilityMutation=0;
  async function toggleTileVisibility(scope,deviceId,controlId){
    if(!deviceId||!scope)return;
    const previous=new Set(state.hiddenTiles);
    const mutation=++visibilityMutation;
    const key=tileVisibilityKey(scope,deviceId,controlId);
    const legacy=legacyTileVisibilityKey(deviceId,controlId);
    if(state.hiddenTiles.has(legacy)){
      state.hiddenTiles.delete(legacy);
      for(const other of tileVisibilityScopes(deviceId,controlId)){
        state.hiddenTiles.add(tileVisibilityKey(other,deviceId,controlId));
      }
      state.hiddenTiles.add(key);
    }
    if(state.hiddenTiles.has(key))state.hiddenTiles.delete(key);
    else state.hiddenTiles.add(key);
    render();
    if(await saveHomeVisibility()||mutation!==visibilityMutation)return;
    state.hiddenTiles=previous;
    cacheVisibility();
    render();
  }
  /* Cihaz detayındaki göz TEK kartın değil, cihazın kararını verir: gizle = her kartta gizle
     (kapsamsız kayıt), göster = kapsamlı/kapsamsız bütün gizleme kayıtlarını kaldır. */
  async function toggleDeviceVisibility(deviceId,controlId){
    if(!deviceId)return;
    const previous=new Set(state.hiddenTiles);
    const mutation=++visibilityMutation;
    const wanted=controlId||groupDeviceControlId;
    const hidden=isDeviceHiddenAnywhere(deviceId,wanted);
    for(const key of[...state.hiddenTiles]){
      const parsed=parseVisibilityKey(key);
      if(parsed&&parsed.deviceId===deviceId&&parsed.controlId===wanted)state.hiddenTiles.delete(key);
    }
    if(!hidden)state.hiddenTiles.add(legacyTileVisibilityKey(deviceId,wanted));
    render();
    if(await saveHomeVisibility()||mutation!==visibilityMutation)return;
    state.hiddenTiles=previous;
    cacheVisibility();
    render();
  }
  const visibilityIcon=hidden=>hidden
    ?'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 3 18 18"/><path d="M10.6 5.2A9.3 9.3 0 0 1 12 5.1c5 0 9 4.4 9 6.9 0 1-.7 2.3-1.9 3.5"/><path d="M6.4 6.8C4.3 8.2 3 10.1 3 12c0 2.5 4 6.9 9 6.9 1.7 0 3.2-.5 4.5-1.2"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>'
    :'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12c0-2.5 4-6.9 9-6.9s9 4.4 9 6.9-4 6.9-9 6.9-9-4.4-9-6.9Z"/><circle cx="12" cy="12" r="3"/></svg>';
  const visibilityLabel=hidden=>t(hidden?"showOnRoomCard":"hideFromRoomCard");
  /* `options.tip`: düğme ışık kumandasının hızlı sırasında çizildiğinde tarayıcının `title`
     balonu yerine panelin kendi `data-tip` balonu kullanılır (ikisi birden = çift balon).
     Öteki yüzeylerde (kontrol satırları, oda kartları) `title` bugünkü gibi kalır. Metin iki
     durumda da AYNI kaynaktan, `visibilityLabel(hidden)`dan gelir; `aria-label` hiç değişmez. */
  const tipOrTitle=(text,options)=>`${options?.tip===true?"data-tip":"title"}="${esc(text)}"`;
  const visibilityButton=(device,control,options)=>{
    const hidden=isDeviceHiddenAnywhere(device.id,control.id);
    const label=`${visibilityLabel(hidden)}: ${control.name||device.name}`;
    return`<button class="visibility-toggle${hidden?" is-hidden":""}" type="button" role="switch" aria-checked="${hidden?"false":"true"}" data-visibility-device="${esc(device.id)}" data-visibility-control="${esc(control.id)}" aria-label="${esc(label)}" ${tipOrTitle(visibilityLabel(hidden),options)}>${visibilityIcon(hidden)}</button>`;
  };
  /* FAVORİLER — göz ikonunun kardeşi. Aynı iskelet: karar SUNUCUDA (`/api/favorites`, ev genelinde
     tek doğru), yerel kayıt yalnız çevrimdışı açılışın yansısı, anahtar cihaz kimliği + kontrol
     kimliği. Fark tek: göz "gizlenenleri", yıldız "seçilenleri" tutar. */
  const isFavorite=(deviceId,controlId)=>state.favorites.has(favoriteKey(deviceId,controlId));
  /* Sunucudaki şekil: `[{deviceId,controlId}]`. Panelde tek dizeli anahtar taşınır; çeviri burada. */
  function favoritesPayload(){
    return[...state.favorites].map(key=>{
      const index=key.lastIndexOf("::");
      return index<0?null:{deviceId:key.slice(0,index),controlId:key.slice(index+2)};
    }).filter(entry=>entry&&entry.deviceId&&entry.controlId);
  }
  function cacheFavorites(){
    try{localStorage.setItem(favoritesCacheKey,JSON.stringify([...state.favorites]))}catch{}
  }
  function applyFavorites(favorites){
    const list=Array.isArray(favorites)?favorites:[];
    state.favorites=new Set(list
      .filter(entry=>entry&&typeof entry.deviceId==="string"&&typeof entry.controlId==="string")
      .map(entry=>favoriteKey(entry.deviceId,entry.controlId)));
    cacheFavorites();
  }
  async function saveHomeFavorites(){
    cacheFavorites();
    try{
      await api("/api/favorites",{method:"PUT",body:JSON.stringify({favorites:favoritesPayload()})});
      return true;
    }catch(error){showToast(t("favoritesSaveFailed",{error:error.message}),true);return false}
  }
  /* Sunucu 64 kaydı aşan listeyi reddediyor: kullanıcı sessiz bir hatayla değil, açık bir uyarıyla
     karşılaşsın ve yıldız işaretlenmemiş kalsın. */
  let favoritesMutation=0;
  async function toggleFavorite(deviceId,controlId){
    if(!deviceId||!controlId||controlId===groupDeviceControlId)return;
    const previous=new Set(state.favorites);
    const mutation=++favoritesMutation;
    const key=favoriteKey(deviceId,controlId);
    if(state.favorites.has(key))state.favorites.delete(key);
    else{
      if(state.favorites.size>=maxHomeFavorites){showToast(t("favoritesFull",{count:maxHomeFavorites}),true);return}
      state.favorites.add(key);
    }
    render();
    if(await saveHomeFavorites()||mutation!==favoritesMutation)return;
    state.favorites=previous;
    cacheFavorites();
    render();
  }
  /* Favori kartının içeriği: kayıttaki SIRAYLA çözülür. Silinen cihaz ya da kalkmış kanal kaydı
     burada sessizce düşer (sunucu da cihaz silinince kendi kaydını temizliyor); çevrimdışı cihaz
     düşmez — döşemesi normal "offline" hâliyle durur, çünkü kullanıcı onu bilerek seçti. */
  function favoriteEntries(){
    return[...state.favorites].map(key=>{
      const index=key.lastIndexOf("::");
      if(index<0)return null;
      const device=state.devices.find(candidate=>candidate.id===key.slice(0,index));
      const control=device?.controls.find(candidate=>candidate.id===key.slice(index+2)&&isDashboardControl(candidate));
      return device&&control?{device,control}:null;
    }).filter(Boolean);
  }
  const favoriteIcon=()=>'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3.6 2.6 5.3 5.8.85-4.2 4.1 1 5.75L12 16.85 6.8 19.6l1-5.75-4.2-4.1 5.8-.85z"/></svg>';
  const favoriteLabel=on=>t(on?"removeFromFavorites":"addToFavorites");
  const favoriteButton=(device,control,options)=>{
    const on=isFavorite(device.id,control.id);
    const label=`${favoriteLabel(on)}: ${control.name||device.name}`;
    return`<button class="favorite-toggle" type="button" aria-pressed="${on?"true":"false"}" data-favorite-device="${esc(device.id)}" data-favorite-control="${esc(control.id)}" aria-label="${esc(label)}" ${tipOrTitle(favoriteLabel(on),options)}>${favoriteIcon()}</button>`;
  };
  const commandValue=value=>esc(JSON.stringify(value));
  const optionValue=(values,pattern,fallback)=>values?.find(value=>pattern.test(String(value).toUpperCase()))??fallback;
  const dashboardControlKinds=new Set(["switch","fan","siren","cover","position","lock","climate"]);
  const isDashboardControl=control=>dashboardControlKinds.has(control.kind)&&control.adminOnly!==true;
  const dashboardControlForDevice=device=>device.controls.find(control=>isDashboardControl(control)&&control.id==="main")
    ||device.controls.find(control=>isDashboardControl(control)&&["switch","fan","siren","cover","lock"].includes(control.kind))
    ||device.controls.find(isDashboardControl);
  const binaryControlActive=control=>
    control.value===true
    ||String(control.value).toUpperCase()===String(control.valueOn??"ON").toUpperCase();
  const dashboardControlValueForState=(control,active)=>{
    if(["switch","fan","siren"].includes(control.kind))return Boolean(active);
    if(control.kind==="cover"){
      const values=control.values||[];
      return active?optionValue(values,/OPEN|UP/,"OPEN"):optionValue(values,/CLOSE|DOWN/,"CLOSE");
    }
    if(control.kind==="lock"){
      const values=control.values||[];
      return active?optionValue(values,/^LOCK(?:ED)?$/,"LOCK"):optionValue(values,/UNLOCK/,"UNLOCK");
    }
    return undefined;
  };
  const dashboardControlAction=control=>{
    if(!control)return null;
    if(["switch","fan","siren"].includes(control.kind)){
      const active=binaryControlActive(control);
      return{active,value:dashboardControlValueForState(control,!active)};
    }
    if(control.kind==="cover"){
      const active=/OPEN|OPENING|UP/.test(String(control.value??"").toUpperCase());
      return{active,value:dashboardControlValueForState(control,!active)};
    }
    if(control.kind==="lock"){
      const active=/^LOCK(?:ED)?$/.test(String(control.value??"").toUpperCase());
      return{active,value:dashboardControlValueForState(control,!active)};
    }
    return null;
  };
  const confirmCommandKey=(control,action)=>{
    if(!control||!action)return"";
    if(control.kind==="lock")return action.active===true?"confirmUnlockDevice":"";
    if(control.kind==="siren")return action.active===true?"":"confirmSirenDevice";
    return"";
  };
  const confirmCommandAttribute=(control,action)=>{
    const key=confirmCommandKey(control,action);
    return key?` data-confirm-command="${key}"`:"";
  };
  const levelValueHtml=(value,unit)=>`${esc(value)}${unit?`<span class="control-unit">${esc(unit)}</span>`:""}`;
  const renameGlyph=`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16-1 5 5-1L19 9l-4-4L4 16Z"/><path d="m13.5 6.5 4 4"/></svg>`;
  // Ayrı adı olan tek şey aç/kapa kanalıdır: `state`/`state_lN` kanalları `<ieee>:<kanal>` takma adını
  // okur, kimlikleri de o kanaldır ("main", "l1"). Perde, kilit, fan, siren ve cihazın yayımladığı
  // ikili ayarların kimliği `cover:...`, `switch:child_lock` gibidir; adları cihazın kendi etiketinden
  // gelir, takma ad okunmaz — bu satırlarda kalem gösterilmez.
  const isNamedChannel=control=>control?.kind==="switch"&&!String(control.id||"").includes(":");
  const deviceNamedChannels=device=>(device?.controls||[]).filter(isNamedChannel);
  // Tek kanallı cihazda "kanal adı" diye ayrı bir kavram yok: cihaz adı kanalın da adıdır, tek kalem
  // (başlıktaki) yeter. Ayrı kanal adları yalnız birden çok aç/kapa kanalı olan cihazda anlamlı.
  const deviceHasChannelNames=device=>deviceNamedChannels(device).length>1;
  const channelDisplayName=(device,control)=>
    isNamedChannel(control)&&!deviceHasChannelNames(device)?device.name:control.name;
  const renameControlButton=(device,control)=>{
    if(!isNamedChannel(control)||!deviceHasChannelNames(device))return"";
    const label=control.name?`${t("nameChannel")}: ${control.name}`:t("nameChannel");
    return`<button class="control-rename" type="button" data-admin-only data-rename-channel="${esc(device.id)}" data-channel="${esc(control.id)}" aria-label="${esc(label)}" title="${esc(t("nameChannel"))}">${renameGlyph}</button>`;
  };
  const controlHtml=(device,control)=>{
    const adminClass=control.adminOnly?" admin-control":"";
    const adminAttr=control.adminOnly?" data-admin-only":"";
    if(["switch","fan","siren"].includes(control.kind)){
      const active=binaryControlActive(control);
      const known=typeof control.value==="boolean";
      const valueLabel=control.value===null?t("unknownState"):active?t("on"):t("off");
      const pending=commandPending(device.id,control.property);
      const name=channelDisplayName(device,control);
      return`<div class="control-row${adminClass}"${adminAttr}><div><div class="control-name">${esc(name)}${renameControlButton(device,control)}</div><div class="control-value">${pending?t("sendingCommand"):valueLabel}</div></div><div class="control-actions">${isDashboardControl(control)?favoriteButton(device,control)+visibilityButton(device,control):""}<button class="switch ${active?"on":""}${pending?" pending":""}" data-command-value="${commandValue(!active)}" data-device="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${esc(name)}" aria-pressed="${active}"${pending?' aria-busy="true"':""}${pending?" disabled":""}><span></span></button></div></div>`;
    }
    if(control.kind==="cover"){
      const values=control.values||[];
      const openValue=optionValue(values,/OPEN|UP/,"OPEN");
      const stopValue=optionValue(values,/STOP/,"STOP");
      const closeValue=optionValue(values,/CLOSE|DOWN/,"CLOSE");
      const current=String(control.value??"").toUpperCase();
      return`<div class="control-row${adminClass}"${adminAttr}><div><div class="control-name">${esc(control.name||t("cover"))}</div><div class="control-value">${esc(control.value??t("unknownState"))}</div></div><div class="control-icon-actions">${isDashboardControl(control)?favoriteButton(device,control)+visibilityButton(device,control):""}<button class="control-command ${current===String(openValue).toUpperCase()?"active":""}" data-command-value="${commandValue(openValue)}" data-device="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${t("open")}" title="${t("open")}">↑</button><button class="control-command stop" data-command-value="${commandValue(stopValue)}" data-device="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${t("stop")}" title="${t("stop")}">■</button><button class="control-command close-action ${current===String(closeValue).toUpperCase()?"active":""}" data-command-value="${commandValue(closeValue)}" data-device="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${t("close")}" title="${t("close")}">↓</button></div></div>`;
    }
    if(control.kind==="lock"){
      const values=control.values||[];
      const lockValue=optionValue(values,/^LOCK(?:ED)?$/,"LOCK");
      const unlockValue=optionValue(values,/UNLOCK/,"UNLOCK");
      const locked=/^LOCK(?:ED)?$/.test(String(control.value??"").toUpperCase());
      return`<div class="control-row${adminClass}"${adminAttr}><div><div class="control-name">${esc(control.name||t("lockDevice"))}</div><div class="control-value">${locked?t("lock"):t("unlock")}</div></div><div class="control-icon-actions">${isDashboardControl(control)?favoriteButton(device,control)+visibilityButton(device,control):""}<button class="control-command ${locked?"active":""}" data-command-value="${commandValue(lockValue)}" data-device="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${t("lock")}" title="${t("lock")}">▣</button><button class="control-command ${!locked?"active":""}" data-command-value="${commandValue(unlockValue)}" data-device="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${t("unlock")}" title="${t("unlock")}">□</button></div></div>`;
    }
    if(control.kind==="select"){
      return`<div class="control-row${adminClass}"${adminAttr}><div><div class="control-name">${esc(control.name)}</div>${control.adminOnly?`<div class="control-value">${t("deviceSetting")}</div>`:""}</div><select class="control-select" data-select="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${esc(control.name)}">${(control.values||[]).map(value=>`<option value="${esc(value)}"${String(value)===String(control.value)?" selected":""}>${esc(String(value).replaceAll("_"," "))}</option>`).join("")}</select></div>`;
    }
    if(control.kind==="color")return`<div class="control-row${adminClass}"${adminAttr}><div><div class="control-name">${t("color")}</div><div class="control-value">${esc(control.value)}</div></div><input type="color" value="${esc(control.value||"#ffffff")}" data-color="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${t("color")}"></div>`;
    const known=typeof control.value==="number";
    const label=control.kind==="position"?t("position"):control.kind==="climate"?t("targetTemperature"):control.name;
    return`<div class="control-row${adminClass}"${adminAttr}><div><div class="control-name">${esc(label)}</div><div class="control-value">${known?levelValueHtml(control.value,control.unit):t("unknownState")}</div></div><div class="control-actions">${isDashboardControl(control)?favoriteButton(device,control)+visibilityButton(device,control):""}<input type="range" min="${control.min??0}" max="${control.max??100}" step="${control.step??1}" value="${known?control.value:control.min??0}" data-level="${esc(device.id)}" data-property="${esc(control.property)}" data-unit="${esc(control.unit||"")}" aria-label="${esc(label)}"></div></div>`;
  };
  // Rol satırı cihazın yayımladığı ayarların en altında, "switch type" gibi görünür. Cihaza yazılmaz,
  // Villa Bridge'in kendi ayarıdır — alt yazısı bunu söyler. Satır her cihazda çıkar; seçim yalnız
  // lamba↔anahtar karışıklığının mümkün olduğu yerde, yani aç/kapa kontrolü olan cihazda sunulur.
  // Ölçüt cihazın yeteneği; ad ya da model listesi yok. Sensör, perde, kilit gibi cihazlarda satır
  // tespit edilen sınıfı düz metin gösterir.
  const deviceRoleChannels=device=>(device?.controls||[]).filter(control=>control.kind==="switch");
  const deviceRoleRowsHtml=device=>{
    const channels=deviceRoleChannels(device);
    if(!channels.length){
      return`<div class="control-row admin-control"><div><div class="control-name">${esc(t("deviceRoleLabel"))}</div><div class="control-value">${t("appSetting")}</div></div><div class="control-value device-role-fixed">${esc(t("deviceRoleFixed",{kind:deviceKind(device)}))}</div></div>`;
    }
    return channels.map(control=>{
      const guess=deviceCategoryLabels[control.detectedCategory||device.detectedCategory];
      const current=control.role||"auto";
      // Tek kanallı cihazda satır bugünküyle aynı; çoklu kanalda başlık kanalın adını taşır
      // (kullanıcının verdiği kanal adı varsa o gelir — kanal kimliği şeması ile aynı). Ayrı kanal
      // adı olmayan satırda cihaz adı kullanılır, kanal adı uydurulmaz.
      const label=channels.length>1?t("deviceRoleChannelLabel",{channel:channelDisplayName(device,control)}):t("deviceRoleLabel");
      const options=deviceRoleOptions.map(role=>{
        const optionLabel=role!=="auto"
          ?t(deviceRoleLabels[role])
          :`${t("deviceRoleAuto")} (${guess?t("deviceRoleAutoGuess",{kind:t(guess)}):t("deviceRoleAutoUnknown")})`;
        return`<option value="${role}"${role===current?" selected":""}>${esc(optionLabel)}</option>`;
      }).join("");
      return`<div class="control-row admin-control"><div><div class="control-name">${esc(label)}</div><div class="control-value">${t("appSetting")}</div></div><select class="control-select" data-device-role-select="${esc(device.id)}" data-device-role-channel="${esc(control.id)}" aria-label="${esc(label)}">${options}</select></div>`;
    }).join("");
  };
  const primaryStatusForDevice=(device,preparing=false)=>{
    if(preparing)return{label:t("preparing"),tone:"muted"};
    if(device.availability==="offline")return{label:device.lastSeen?t("unreachableSince",{time:ago(device.lastSeen)}):t("unreachable"),tone:"danger"};
    if(device.supported===false)return{label:t("unsupportedDevice"),tone:"danger"};
    if(device.interviewCompleted===false)return{label:t("setupIncomplete"),tone:"danger"};
    const value=device.state||{};
    if(value.smoke!==undefined)return{label:value.smoke?t("smokeDetected"):t("noSmoke"),tone:value.smoke?"danger":"muted"};
    if(value.carbon_monoxide!==undefined)return{label:value.carbon_monoxide?t("coDetected"):t("coClear"),tone:value.carbon_monoxide?"danger":"muted"};
    if(value.contact!==undefined)return{label:value.contact?t("doorClosed"):t("doorOpen"),tone:value.contact?"muted":"active"};
    const occupied=value.occupancy!==undefined?value.occupancy:value.presence;
    if(occupied!==undefined)return{label:occupied?t("motionDetected"):t("motionClear"),tone:occupied?"active":"muted"};
    if(hasLowBattery(device))return{label:t("batteryLow"),tone:"danger"};
    const mainControl=dashboardControlForDevice(device);
    if(typeof mainControl?.value==="boolean")return{label:mainControl.value?t("on"):t("off"),tone:mainControl.value?"active":"muted"};
    if(mainControl?.value!==null&&mainControl?.value!==undefined){
      return{label:String(mainControl.value).replaceAll("_"," "),tone:"muted"};
    }
    if(typeof value.action==="string")return{label:String(value.action).replaceAll("_"," "),tone:"active"};
    return{label:device.availability==="online"?t("ready"):t("standby"),tone:device.availability==="online"?"active":"muted"};
  };
  const deviceStatusIcon=(device,primaryStatus)=>`<span class="device-status-icon ${primaryStatus.tone}${device.availability==="offline"?" offline":""}" aria-hidden="true">${deviceTypeIcon(device)}</span>`;
  const cardStatusBadge=device=>{
    const percent=linkQualityPercent(device);
    if(percent!==null&&percent<25){
      const label=`${t("signal")} ${percent}%`;
      return`<span class="device-link-level compact weak" title="${esc(label)}" aria-label="${esc(label)}">${percent}%</span>`;
    }
    const battery=batteryPercent(device);
    if(battery!==null&&battery<=lowBatteryThreshold()){
      const label=`${t("battery")} ${battery}%`;
      return`<span class="quick-battery low card-battery" title="${esc(label)}" aria-label="${esc(label)}"><span class="battery-glyph" aria-hidden="true"><i style="width:${battery}%"></i></span>${battery}%</span>`;
    }
    return"";
  };
  const deviceCardToggle=(device,preparing)=>{
    const control=dashboardControlForDevice(device);
    const action=dashboardControlAction(control);
    if(!action)return"";
    const pending=commandPending(device.id,control.property);
    const disabled=preparing||pending||device.availability==="offline";
    const shown=pending?!action.active:action.active;
    return`<button class="device-card-toggle${shown?" on":""}${pending?" pending":""}" type="button" data-command-value="${commandValue(action.value)}" data-device="${esc(device.id)}" data-property="${esc(control.property)}" aria-label="${esc(device.name)}" aria-pressed="${action.active}"${pending?` aria-busy="true"`:""}${disabled?" disabled":""}><span class="toggle-track" aria-hidden="true"><span class="toggle-knob"></span>${pending?'<span class="command-spinner toggle-spinner"></span>':""}</span></button>`;
  };
  const deviceCardHtml=device=>{
    const preparing=device.preparing===true;
    const failed=commandFailed(device.id);
    const primaryStatus=failed?{label:t("commandFailed"),tone:"danger"}:primaryStatusForDevice(device,preparing);
    const offline=device.availability==="offline";
    const cardToggle=deviceCardToggle(device,preparing);
    const detailHint=`<span class="device-card-hint${cardToggle?" divided":""}" aria-hidden="true">›</span>`;
    return`<article class="device-card${failed?" command-failed":""}${offline?" device-card-offline":""}${preparing?" preparing":""}"${preparing?' inert aria-busy="true"':""} tabindex="0" data-device-card="${esc(device.id)}" data-name="${esc(device.name.toLowerCase())}" aria-label="${esc(device.name)} · ${esc(primaryStatus.label)}">
      <div class="device-card-header"><div class="device-card-lead">${deviceStatusIcon(device,primaryStatus)}${cardStatusBadge(device)}</div><div class="device-card-copy"><div class="device-name-row"><div class="device-name">${esc(device.name)}</div></div><div class="device-meta"><span class="device-meta-text"><span class="device-primary-status ${primaryStatus.tone}">${esc(primaryStatus.label)}</span></span></div></div><div class="device-header-status">${detailHint}${cardToggle}</div></div>
    </article>`;
  };
  /* Liste görünümü — Zigbee2MQTT'nin Devices tablosunun sütun düzeni, aynı sırayla:
     # · Görsel · Ad · UID · Üretici · Model · LQI · Durum · Güç · Eylemler.
     "En son görülme" Z2M'de ayrı sütun değildir; 1024×640'ta on birinci sütun sığmadığı için
     Durum hücresinin altına ikinci satır olarak konur (göreli metin, tam zaman `title`'da).
     Hiçbir hücrede uydurma değer yoktur: veri yoksa "—" yazar. */
  const deviceTableDash=`<span class="device-table-text" aria-hidden="true">—</span>`;
  const deviceTablePower=device=>{
    const battery=batteryPercent(device);
    const source=String(device.powerSource||"");
    if(battery!==null)return`${t("powerBattery")} ${battery}%`;
    if(/batter/i.test(source))return t("powerBattery");
    if(/mains|dc\s*source/i.test(source))return t("powerMains");
    /* `powerSource` yoksa pil özelliğinin varlığından türetilir; o da yoksa bilgi yoktur. */
    if(device.features?.includes("battery"))return t("powerBattery");
    return null;
  };
  const deviceTableAvailability=device=>{
    if(device.availability==="online")return{tone:"online",label:t("online")};
    if(device.availability==="offline")return{tone:"offline",label:t("unreachable")};
    return{tone:"",label:t("availabilityUnknown")};
  };
  /* Sıralama anahtarı olarak da kullanılır: karşılaştırılabilir bir skaler döndürür. */
  const deviceSortValue=(device,key)=>{
    if(key==="lqi")return rawLinkQuality(device)??-1;
    if(key==="status")return device.availability==="online"?2:device.availability==="offline"?0:1;
    if(key==="lastSeen")return device.lastSeen?new Date(device.lastSeen).getTime()||0:0;
    return null;
  };
  const sortedDeviceRows=devices=>{
    const{key,direction}=state.deviceSort;
    const factor=direction==="desc"?-1:1;
    const byName=(left,right)=>String(left.name).localeCompare(String(right.name),state.language);
    if(key==="name")return[...devices].sort((left,right)=>byName(left,right)*factor);
    return[...devices].sort((left,right)=>{
      const difference=deviceSortValue(left,key)-deviceSortValue(right,key);
      return difference!==0?difference*factor:byName(left,right);
    });
  };
  const deviceTableHeader=(key,label)=>{
    const active=state.deviceSort.key===key;
    const sort=active?(state.deviceSort.direction==="desc"?"descending":"ascending"):"none";
    /* `aria-sort` yalnızca başlık hücresine aittir; düğmedeki `data-sort` sadece okun CSS kancasıdır. */
    return`<th scope="col" aria-sort="${sort}"><button class="device-table-sort" type="button" data-device-sort="${esc(key)}" data-sort="${sort}" title="${esc(t("deviceTableSort",{label}))}"><span>${esc(label)}</span><span class="device-table-arrow" aria-hidden="true">${state.deviceSort.direction==="desc"?"▼":"▲"}</span></button></th>`;
  };
  const deviceTableActions=device=>[
    `<button class="device-table-action" type="button" data-admin-only data-rename="${esc(device.id)}" title="${esc(t("changeName"))}" aria-label="${esc(`${t("changeName")}: ${device.name}`)}">${renameGlyph}</button>`,
    `<button class="device-table-action" type="button" data-admin-only data-reconfigure="${esc(device.id)}" title="${esc(t("repairDevice"))}" aria-label="${esc(`${t("repairDevice")}: ${device.name}`)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5"/><path d="M19 12a7 7 0 1 0-2 5"/></svg></button>`,
    `<button class="device-table-action danger" type="button" data-admin-only data-remove="${esc(device.id)}" title="${esc(t("removeDevice"))}" aria-label="${esc(`${t("removeDevice")}: ${device.name}`)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14"/><path d="M9 7V5h6v2"/><path d="M7 7l1 13h8l1-13"/></svg></button>`
  ].join("");
  const deviceTableRowHtml=(device,index)=>{
    const availability=deviceTableAvailability(device);
    const lqi=rawLinkQuality(device);
    const percent=linkQualityPercent(device);
    const lqiTone=percent===null?"":percent>=70?"strong":percent>=25?"good":"weak";
    const power=deviceTablePower(device);
    const shortAddress=Number.isInteger(device.networkAddress)
      ?`<small>0x${device.networkAddress.toString(16).padStart(4,"0")}</small>`
      :"";
    const seen=device.lastSeen
      ?`<span class="device-table-seen" title="${esc(new Date(device.lastSeen).toLocaleString(state.language))}">${esc(ago(device.lastSeen))}</span>`
      :`<span class="device-table-seen">${esc(t("deviceTableLastSeen"))}: —</span>`;
    return`<tr tabindex="0" data-device-card="${esc(device.id)}" data-name="${esc(device.name.toLowerCase())}"${device.availability==="offline"?' class="device-row-offline"':""} aria-label="${esc(`${device.name} · ${availability.label}`)}">
      <td class="device-table-index">${index+1}</td>
      <td class="device-table-visual">${deviceVisualFor(device,deviceImageModel(device))}</td>
      <td class="device-table-name">${esc(device.name)}</td>
      <td class="device-table-uid">${esc(device.id)}${shortAddress}</td>
      <td class="device-table-text">${device.vendor?esc(device.vendor):deviceTableDash}</td>
      <td class="device-table-text">${device.model?esc(device.model):deviceTableDash}</td>
      <td class="device-table-lqi${lqiTone?` ${lqiTone}`:""}"${lqi===null?"":` title="${esc(`${t("signal")} ${percent}%`)}"`}>${lqi===null?"—":lqi}</td>
      <td><span class="device-table-state${availability.tone?` ${availability.tone}`:""}"><span class="device-table-dot" aria-hidden="true"></span>${esc(availability.label)}</span>${seen}</td>
      <td class="device-table-text">${power?esc(power):deviceTableDash}</td>
      <td class="device-table-actions"><div class="device-table-action-row">${deviceTableActions(device)}</div></td>
    </tr>`;
  };
  const deviceTableHtml=devices=>{
    const rows=sortedDeviceRows(devices).map(deviceTableRowHtml).join("");
    return`<div class="device-table-panel"><div class="device-table-wrap"><table class="device-table"><thead><tr>
      <th scope="col" class="device-table-index">#</th>
      <th scope="col">${esc(t("deviceTablePicture"))}</th>
      ${deviceTableHeader("name",t("deviceTableName"))}
      <th scope="col">${esc(t("uid"))}</th>
      <th scope="col">${esc(t("deviceTableVendor"))}</th>
      <th scope="col">${esc(t("model"))}</th>
      ${deviceTableHeader("lqi","LQI")}
      ${deviceTableHeader("status",t("deviceTableStatus"))}
      <th scope="col">${esc(t("deviceTablePower"))}</th>
      <th scope="col" class="device-table-actions">${esc(t("deviceTableActions"))}</th>
    </tr></thead><tbody>${rows}</tbody></table></div><p class="device-table-note">${esc(t("deviceTableCount",{count:devices.length}))}</p></div>`;
  };
  function setDeviceSort(key){
    const same=state.deviceSort.key===key;
    /* Ad artan başlar (alfabe), ölçüm sütunları azalan başlar: en güçlü/en yeni üstte. */
    const direction=same?(state.deviceSort.direction==="asc"?"desc":"asc"):key==="name"?"asc":"desc";
    state.deviceSort={key,direction};
    try{localStorage.setItem("villa-device-sort",`${key}:${direction}`)}catch{}
    filterDevices();
    bindCards();
  }
  const deviceRoomsHtml=device=>{
    if(!state.groups.length)return"";
    const chips=state.groups.map(group=>{
      const member=deviceInRoom(device,group.id);
      const label=t(member?"removeFromRoom":"addToRoom",{name:group.name});
      return`<button class="room-membership${member?" active":""}" type="button" data-toggle-room="${esc(group.id)}" data-room-device="${esc(device.id)}" aria-pressed="${member}" title="${esc(label)}" aria-label="${esc(label)}"><span class="room-membership-mark" aria-hidden="true">${member?"✓":"+"}</span>${esc(group.name)}</button>`;
    }).join("");
    return`<div class="device-rooms"><div class="device-rooms-head">${t("deviceRooms")}</div><div class="device-rooms-chips">${chips}</div></div>`;
  };
  /* Kontrolü olan cihazlarda göster/gizle kontrol satırında duruyor. Kontrolü olmayan cihazın
     (sensör) döşemesi de gizlenebildiği için Cihazlar sayfasında karşılığı burada verilir —
     yoksa gizlenen sensör yalnız düzenleme kipinden geri getirilebilirdi. */
  const deviceVisibilityHtml=device=>{
    if(device.controls.some(isDashboardControl))return"";
    const hidden=isDeviceHiddenAnywhere(device.id,null);
    const label=visibilityLabel(hidden);
    return`<div class="device-rooms"><div class="device-rooms-head">${t("roomCardVisibility")}</div><div class="device-rooms-chips"><button class="room-membership${hidden?"":" active"}" type="button" role="switch" aria-checked="${hidden?"false":"true"}" data-visibility-device="${esc(device.id)}" data-visibility-control="${esc(groupDeviceControlId)}" aria-label="${esc(label)}" title="${esc(label)}">${visibilityIcon(hidden)}${esc(label)}</button></div></div>`;
  };
  // §5.1.1 — düğmeler sunucudan alt varlık olarak gelir; arayüz ham `action` dizesini kendi çözmez.
  const deviceButtonNamedKeys={on:"deviceButtonOn",off:"deviceButtonOff",toggle:"deviceButtonToggle",up:"deviceButtonUp",down:"deviceButtonDown",left:"deviceButtonLeft",right:"deviceButtonRight",both:"deviceButtonBoth",open:"deviceButtonOpen",close:"deviceButtonClose",stop:"deviceButtonStop",brightness_up:"deviceButtonBrightnessUp",brightness_down:"deviceButtonBrightnessDown",arrow_left:"deviceButtonArrowLeft",arrow_right:"deviceButtonArrowRight",play_pause:"deviceButtonPlayPause",next:"deviceButtonNext",previous:"deviceButtonPrevious"};
  const deviceButtonKey=button=>button?.kind==="numbered"?"deviceButtonNumbered":button?.kind==="single"?"deviceButtonSingle":button?.kind==="ungrouped"?"deviceButtonOther":deviceButtonNamedKeys[button?.key]||"";
  const deviceButtonDefaultName=(button,language)=>{
    const template=translations[language]?.[deviceButtonKey(button)];
    return template?template.replace("{number}",String(button?.number??"")):"";
  };
  // Kullanıcının verdiği ad daima kazanır; ad yoksa sunucunun ürettiği varsayılan yerine arayüz dili.
  const deviceButtonName=button=>{
    const generated=deviceButtonDefaultName(button,"tr");
    const localized=deviceButtonDefaultName(button,state.language);
    if(generated&&localized&&button?.name===generated)return localized;
    return button?.name||localized||String(button?.id??"");
  };
  // Tanınmayan basış (`ungrouped` kovası) ham değeriyle gösterilir; boşluk ya da çökme olmaz.
  const deviceButtonPressLabel=press=>{const key=automationPressKeys[press];return key?t(key):String(press??"")};
  // Kullanıcıya yalnızca kısa basış ve basılı tutma sunulur; art arda basış seçeneği gösterilmez.
  // Eleme yalnız sunum katmanındadır: sunucu tüm basışları üretmeye, kayıtlı kurallar çalışmaya devam eder.
  const hiddenPressKinds=new Set(["double","triple","quadruple","quintuple","many"]);
  // Elemeden sonra hiçbir şey kalmazsa liste boş bırakılmaz; cihaz ne bildiriyorsa o gösterilir.
  // `keep`: hâlihazırda kayıtlı olan seçim, elense bile listede kalır (düzenleme seçimi kaybolmasın).
  const visiblePresses=(entries,keep)=>{
    const list=entries||[];
    const kept=list.filter(entry=>!hiddenPressKinds.has(entry.press)||(keep!=null&&keep===entry.action));
    return kept.length?kept:list;
  };
  const deviceButtonPressList=button=>visiblePresses(button?.actions).map(entry=>deviceButtonPressLabel(entry.press)).join(" · ");
  const deviceButtonRenameButton=(device,button)=>`<button class="control-rename" type="button" data-admin-only data-rename-channel="${esc(device.id)}" data-channel="${esc(button.id)}" aria-label="${esc(`${t("nameButton")}: ${deviceButtonName(button)}`)}" title="${esc(t("nameButton"))}">${renameGlyph}</button>`;
  // Vurgu penceresi 8 sn'lik yenilemeye eşit: her basış en az bir kez vurgulanır, yeni zamanlayıcı kurulmaz.
  const deviceButtonPressWindowMs=8000;
  const deviceButtonPressed=(device,button)=>{
    const last=device.lastAction;
    if(!last||last.buttonId!==button.id)return false;
    return Date.now()-new Date(last.at).getTime()<deviceButtonPressWindowMs;
  };
  // Cihaz tanımında `action` görünmesi düğme sinyali gönderdiğini KANITLAMAZ: duvar dimmer'ları da
  // bildirir ama hiç yaymaz. Tek kanıt cihazın kendi yaydığı basış — sunucudaki `lastAction` ve olay akışı.
  // Türetici (src/device-buttons.ts) değişmez; eleme yalnız burada, sunum katmanındadır.
  const deviceSeenPress=device=>Boolean(device?.lastAction)
    ||(state.events||[]).some(item=>item.sourceName===device?.sourceName&&item.property==="action");
  const deviceButtonLastLine=device=>{
    const last=device.lastAction;
    const button=last?(device.buttons||[]).find(item=>item.id===last.buttonId):null;
    if(!last||!button)return`<div class="device-buttons-hint">${esc(t("deviceButtonLearnHint"))}</div>`;
    const entry=button.actions.find(item=>item.action===last.action);
    return`<div class="device-buttons-last">${esc(t("deviceButtonLastPress",{button:deviceButtonName(button),press:deviceButtonPressLabel(entry?.press??last.action),time:ago(last.at)}))}</div>`;
  };
  const deviceButtonsHtml=device=>{
    const buttons=device.buttons||[];
    if(!buttons.length)return"";
    // Prizden beslenen cihaz (kumanda değil, duvar anahtarı/dimmer) hiç basış yaymadıysa bölüm çıkmaz:
    // orada bastıracak düğme yok, adlandırma kutusu yanıltıyor. Pilli kumandada bölüm kalır —
    // yeni eşleştirilmiş kumanda henüz basılmamış olabilir, ipucu satırı ona yol gösterir.
    if(device.type==="Router"&&!deviceSeenPress(device))return"";
    const rows=buttons.map(button=>`<div class="device-button-row${deviceButtonPressed(device,button)?" pressed":""}" data-device-button="${esc(button.id)}"><div class="device-button-name">${esc(deviceButtonName(button))}${deviceButtonRenameButton(device,button)}</div><div class="device-button-presses">${esc(deviceButtonPressList(button))}</div></div>`).join("");
    return`<div class="device-buttons"><div class="device-buttons-head">${t("deviceButtons")}</div>${deviceButtonLastLine(device)}${rows}</div>`;
  };
  /* Cihazın yayımladığı ama yazılamayan TÜM ölçümler. Liste sunucudan hazır gelir
     (`device.readings`, bkz. `src/device-store.ts` → `deviceReadings`); panelde cihaza ya da
     özellik adına özel hiçbir kural yoktur — biçim yalnız expose tipinden okunur. */
  /* Zigbee2MQTT'nin STANDART algılama sözlüğü — her üreticide aynı property adları. Liste cihaz
     modeline/markasına değil, protokolün ortak sözcüklerine dayanır: bu yüzden yeni bir sensör
     eklendiğinde tek satır bile değişmez. Bu ölçümlerde "Açık/Kapalı" bir güç dilidir ve yanlış
     okunur; doğru dil "Var/Yok"tur. */
  const readingDetectionProperties=new Set(["occupancy","presence","vibration","smoke","water_leak","gas","carbon_monoxide","tamper","moving"]);
  const readingBinaryLabels=property=>readingDetectionProperties.has(property)
    ?{on:"readingPresent",off:"readingAbsent"}
    :{on:"readingOn",off:"readingOff"};
  const readingValueText=reading=>{
    const value=reading?.value;
    if(value===null||value===undefined)return t("unknownState");
    const property=String(reading?.property||"");
    const labels=readingBinaryLabels(property);
    /* İkili ölçüm cihazdan ya boolean ya da kendi sözcüğüyle gelir (`ON`, `open`, `alarm`…).
       Eşleme cihazın kendi bildirdiği uçlarla yapılır (`value_on`/`value_off`); tanım yoksa
       boolean da aynı iki etikete düşer. Karşılaştırma tip-gevşek: Z2M uçları sayı da olabilir. */
    if(reading.type==="binary"){
      const same=other=>other!==undefined&&other!==null&&String(value)===String(other);
      // Cihazın kendi bildirdiği uçlar önce gelir: ters kutuplu ikili ölçümde (`value_on:false`)
      // ham boolean'a bakmak yanlış etiketi verirdi.
      if(same(reading.valueOn))return t(labels.on);
      if(same(reading.valueOff))return t(labels.off);
    }
    /* Uçlar bildirilmemişse ham boolean çevrilir. Tek istisna `contact`: Z2M sözleşmesinde
       `contact:true` = kapı KAPALI (uç tanımı da `value_on:false` ile bunu söyler). Düz eşleme
       "Açık" yazıp aktif olarak yanıltıyordu, bu yüzden yalnız bu özellikte kutup ters çevrilir —
       yeni etiket gerekmez, mevcut Açık/Kapalı çifti ters eşlenir. */
    if(typeof value==="boolean")return t((property==="contact"?!value:value)?labels.on:labels.off);
    if(typeof value==="number"){
      // Ondalık gürültüsü kırpılır (21.599999998 → 21.6); tam sayı olduğu gibi kalır.
      const rounded=Number.isInteger(value)?value:Math.round(value*100)/100;
      return reading.unit?`${rounded} ${reading.unit}`:String(rounded);
    }
    return String(value).replaceAll("_"," ");
  };
  const deviceReadingsHtml=device=>{
    const list=Array.isArray(device?.readings)?device.readings:[];
    if(!list.length)return"";
    const rows=list.map(reading=>{
      const label=reading.parentName?`${reading.parentName} · ${reading.name}`:reading.name;
      // Tanı amaçlı alanlar (pil voltajı, sayaçlar…) ev sakinine kalabalık yapmasın.
      const admin=reading.category==="diagnostic"?" data-admin-only":"";
      return`<div class="control-row"${admin}><span class="control-name">${esc(label)}</span><span class="control-value">${esc(readingValueText(reading))}</span></div>`;
    }).join("");
    return`<div class="device-buttons device-readings"><div class="device-buttons-head">${esc(t("readings"))}</div><div class="controls">${rows}</div></div>`;
  };
  // Otomatik onarım sessiz kalmasın: cihazın kendi kartında son denemenin izi görünür.
  const deviceSelfHealHtml=device=>{
    const event=(state.events||[]).find(item=>item.sourceName===device.sourceName&&item.property==="self_heal");
    if(!event)return"";
    return`<div class="device-buttons-last">${esc(t("selfHealLast",{status:t(selfHealLabelKeys[event.value]||"selfHealAttempt"),time:ago(event.at)}))}</div>`;
  };
  /* Büyük dikey ışık kumandası — yetenek bazlı çizilir. Hangi parçanın görüneceğini cihazın
     SUNDUĞU kumandalar belirler; model ya da ad listesi yoktur. Parçalar `DeviceControlView`
     kimliğinden okunur: `main` aç/kapa kanalı, `main:brightness`, `main:temperature` ve
     `main:color`. Yoksa o parça hiç çizilmez. Efekt burada YOK: ileri bir cihaz ayarı olduğu
     için yeri ayar satırları listesi (bkz. `lightPanelCoveredControls` — efekt covered değildir,
     satırı listede durur). */
  const lightPanelParts=device=>{
    const controls=device?.controls||[];
    const byId=(id,kind)=>controls.find(control=>control.id===id&&control.kind===kind)||null;
    return{
      power:controls.find(control=>control.kind==="switch"&&control.id==="main")||controls.find(isNamedChannel)||null,
      level:byId("main:brightness","level"),
      temperature:byId("main:temperature","temperature"),
      color:byId("main:color","color")
    };
  };
  /* Kumanda ışık profiline aittir: sınıf tanım verisinden (`light` expose tipi) çıkar, kullanıcının
     seçtiği rol onu ezebilir. Priz/anahtarda çizilmez — dolan bir kolon orada olmayan bir karartmayı
     vaat eder, o cihazlar zaten satır anahtarıyla açılıp kapanıyor. Lambayı süren röleyi kullanıcı
     rolüyle "Işık" yapınca kumanda kendiliğinden gelir; ölçüt yine yetenek, ad değil. */
  const lightPanelSupported=(device,parts)=>device?.category==="light"&&Boolean(parts.power||parts.level);
  /* Kumandanın güç düğmesi aç/kapa satırıyla aynı kanalı sürüyor; satırı elemek için o satırın TAŞIDIĞI
     her şeyin yukarı taşınmış olması gerekir. Göz (`visibilityButton`) kumandaya taşındı, kalem değil:
     kalem yalnız ÇOK KANALLI cihazda çıkar ve o satır kanalın adını da taşır — kumandada kanal adı diye
     bir yer yok, ayrıca kumanda yalnız ilk kanalı sürdüğünden öteki kanalların satırları listede kalır;
     birini eleyip ötekini bırakmak listeyi asimetrik yapardı. Bu yüzden çok kanallı cihazda satır DURUR,
     gözü de kendi satırında kalır (panelde çıkmaz) — her kanalın gözü tek yerde.
     Yönetici-only kanal da elenmez: orada satırda zaten göz yoktu, satırın `data-admin-only` gizlemesi
     korunur. Kısacası: satır elenir ⟺ gözü kumanda taşır. */
  const lightPanelCoversPower=(device,parts)=>Boolean(parts.power)&&isDashboardControl(parts.power)&&!deviceHasChannelNames(device);
  const lightPanelMode=(device,parts)=>{
    const stored=state.lightPanelMode;
    if(stored==="temperature"&&parts.temperature)return"temperature";
    if(stored==="color"&&parts.color)return"color";
    if(stored==="level"&&parts.level)return"level";
    return parts.level?"level":parts.temperature?"temperature":"switch";
  };
  /* Kolonun ne ayarladığı: parlaklık, renk sıcaklığı ya da (ikisi de yoksa) yalnız aç/kapat.
     Her kip aynı sözleşmeyi taşır: oran↔değer çevrimi ve okunacak metin. */
  const lightColumnSpec=(device,parts,mode)=>{
    if(mode==="temperature"&&parts.temperature){
      const control=parts.temperature;
      const min=Number(control.min??153);
      const max=Number(control.max??500);
      const span=max-min||1;
      // Mired ters çalışır: kolonu yukarı çekince görünen Kelvin ARTSIN diye eşleme çevrilir.
      return{kind:"temperature",control,known:typeof control.value==="number",
        value:typeof control.value==="number"?control.value:max,
        valueAt:fraction=>Math.round(max-fraction*span),
        fractionOf:value=>(max-Math.min(max,Math.max(min,Number(value))))/span,
        readoutAt:value=>`${temperatureKelvin(value)} K`};
    }
    if(parts.level){
      const control=parts.level;
      const min=Number(control.min??1);
      const max=Number(control.max??254);
      const span=max-min||1;
      return{kind:"level",control,known:typeof control.value==="number",
        value:typeof control.value==="number"?control.value:min,
        valueAt:fraction=>Math.round(min+fraction*span),
        fractionOf:value=>(Math.min(max,Math.max(min,Number(value)))-min)/span,
        readoutAt:value=>`${Math.round((Math.min(max,Math.max(min,Number(value)))-min)/span*100)}%`};
    }
    return{kind:"switch",control:parts.power,known:typeof parts.power?.value==="boolean",
      value:Boolean(parts.power&&binaryControlActive(parts.power)),
      valueAt:fraction=>fraction>=.5,
      fractionOf:value=>value?1:0,
      readoutAt:value=>t(value?"on":"off")};
  };
  // Kelvin → RGB yaklaşımı (siyah cisim eğrisi): kolon renk sıcaklığını gerçekten gösterir.
  const kelvinHex=kelvin=>{
    const temp=Math.max(1000,Math.min(12000,Number(kelvin)||2700))/100;
    const byte=value=>Math.max(0,Math.min(255,Math.round(value))).toString(16).padStart(2,"0");
    const red=temp<=66?255:329.7*Math.pow(temp-60,-.1332);
    const green=temp<=66?99.47*Math.log(temp)-161.12:288.12*Math.pow(temp-60,-.0755);
    const blue=temp>=66?255:temp<=19?0:138.52*Math.log(temp-10)-305.04;
    return`#${byte(red)}${byte(green)}${byte(blue)}`;
  };
  const lightColumnTint=parts=>{
    if(typeof parts.color?.value==="string"&&/^#[0-9a-f]{6}$/i.test(parts.color.value))return parts.color.value.toLowerCase();
    if(typeof parts.temperature?.value==="number")return kelvinHex(temperatureKelvin(parts.temperature.value));
    return"";
  };
  const lightColorPresets=["#ffcf8e","#ffffff","#ff5147","#ff9b2e","#ffe14d","#57d17f","#3f9dff","#a06bff"];
  const lightGlyphs={
    power:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v8"/><path d="M7.5 6.3a7.5 7.5 0 1 0 9 0"/></svg>',
    level:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.6M12 18.9v2.6M2.5 12h2.6M18.9 12h2.6M5.2 5.2l1.9 1.9M16.9 16.9l1.9 1.9M18.8 5.2l-1.9 1.9M7.1 16.9l-1.9 1.9"/></svg>',
    temperature:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7.4"/><path d="M12 4.6a7.4 7.4 0 0 1 0 14.8Z" fill="currentColor" stroke="none"/></svg>',
    color:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.4c4.3 3.6 6.6 6.5 6.6 9.2a6.6 6.6 0 0 1-13.2 0c0-2.7 2.3-5.6 6.6-9.2Z"/></svg>',
    column:'<svg class="light-column-glyph" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v8"/><path d="M7.5 6.3a7.5 7.5 0 1 0 9 0"/></svg>'
  };
  /* `options.compact`: aynı kumanda hızlı kumanda penceresinde de çizilir. Orada göz (oda kartında
     göster/gizle) yeri değil — pencereyi açan döşemeyi pencerenin içinden kaldırmak anlamsız;
     göz cihaz sayfasındaki yerinde durmaya devam eder. Başka hiçbir parça değişmez.
     "Efekt" açılır listesi kumandanın HİÇBİR sürümünde çizilmez: hızlı kumanda parlaklık, renk
     sıcaklığı/renk ve aç/kapa demektir. Efekt ileri bir cihaz ayarıdır ve zaten ayar satırları
     listesinde ("effect · cihaz ayarı") duruyordu — panelde ikinci kez çizmek aynı ayarı iki
     arayüzle sunmaktı. Satır listede kalır; efekt covered kümesine EKLENMEZ. */
  const lightPanelHtml=(device,options={})=>{
    const parts=lightPanelParts(device);
    if(!lightPanelSupported(device,parts))return"";
    const mode=lightPanelMode(device,parts);
    const spec=lightColumnSpec(device,parts,mode);
    const slider=spec.kind!=="switch";
    const lit=parts.power?binaryControlActive(parts.power):spec.fractionOf(spec.value)>0;
    const unknown=Boolean(parts.power&&parts.power.value===null);
    const percent=Math.round((lit&&spec.known?spec.fractionOf(spec.value):0)*100);
    const readout=unknown?t("unknownState"):lit?spec.readoutAt(spec.value):t("off");
    const label=`${device.name} · ${slider?(spec.kind==="temperature"?t("colorTemperature"):t("brightness")):t("lightPower")}`;
    const tint=lightColumnTint(parts);
    const offline=device.availability==="offline";
    const modeLabels={level:t("brightness"),temperature:t("colorTemperature"),color:t("color")};
    /* İpucu balonu `title` DEĞİL `data-tip`: tarayıcının yavaş, biçimsiz balonu yerine panelin
       kendi dili (bkz. panel.css `.light-actions [data-tip]`). İkisi aynı düğmede DURMAZ, yoksa
       üst üste iki balon çıkar. `aria-label` aynen kalır — ekran okuyucu ondan okur, balon yalnız
       fare/klavye için görsel bir ek. Metinler mevcut anahtarlardan, yeni anahtar açılmadı. */
    const modeButton=(key,control,active)=>control
      ?`<button class="light-mode${active?" active":""}" type="button" data-light-mode-button="${key}" aria-pressed="${active}" aria-label="${esc(modeLabels[key])}" data-tip="${esc(modeLabels[key])}">${lightGlyphs[key]}</button>`
      :"";
    // Güç düğmesi genel komut yolunu kullanır (`data-command-value`), ayrı bir bağlama gerekmez.
    const powerButton=parts.power
      ?`<button class="light-mode${lit?" on":""}" type="button" data-device="${esc(device.id)}" data-property="${esc(parts.power.property)}" data-command-value="${commandValue(!lit)}" aria-pressed="${lit}" aria-label="${esc(`${device.name} · ${lit?t("off"):t("on")}`)}" data-tip="${esc(t("lightPower"))}">${lightGlyphs.power}</button>`
      :"";
    const modeButtons=powerButton+modeButton("level",parts.level,mode==="level")+modeButton("temperature",parts.temperature,mode==="temperature")+modeButton("color",parts.color,mode==="color");
    const modesHtml=modeButtons?`<div class="light-modes" role="group" aria-label="${esc(t("quickControl"))}">${modeButtons}</div>`:"";
    /* Yıldız ve göz kip düğmelerinin yanında, kumandanın içinde durur. Yeni bileşen yok: satırdakiyle
       BİREBİR aynı `favoriteButton`/`visibilityButton` çağrıları — aynı sınıf, aynı işaretleme, aynı
       `data-favorite-*`/`data-visibility-*` bağlaması. Yalnız yerleri değişti. */
    const eyeHtml=options.compact!==true&&lightPanelCoversPower(device,parts)
      ?favoriteButton(device,parts.power,{tip:true})+visibilityButton(device,parts.power,{tip:true})
      :"";
    const actionsHtml=modesHtml||eyeHtml?`<div class="light-actions">${modesHtml}${eyeHtml}</div>`:"";
    /* Hazır renklerin altında panelin KENDİ renk seçicisi durur (`.color-picker` + `data-color`):
       serbest renk için ikinci bir arayüz yazılmadı, mevcut olan yeniden kullanıldı. */
    const presetsHtml=parts.color
      ?`<div class="light-color"${mode==="color"?"":" hidden"}><div class="light-presets" role="group" aria-label="${esc(t("lightPresetColors"))}">${lightColorPresets.map(hex=>`<button class="light-preset" type="button" data-light-preset="${hex}" data-device="${esc(device.id)}" data-property="${esc(parts.color.property)}" aria-label="${esc(`${t("color")} ${hex}`)}" title="${hex}" style="background:${hex}"></button>`).join("")}</div><input class="color-picker" type="color" value="${esc(typeof parts.color.value==="string"?parts.color.value:"#ffffff")}" data-color="${esc(device.id)}" data-property="${esc(parts.color.property)}" aria-label="${esc(t("color"))}"></div>`
      :"";
    return`<div class="light-panel" data-light-panel="${esc(device.id)}">
        <div class="light-readout"><div class="light-readout-value" data-light-readout>${esc(readout)}</div><div class="light-readout-since">${esc(ago(device.stateUpdatedAt))}</div></div>
        <div class="light-column${lit?"":" off"}" data-light-column="${esc(device.id)}" data-light-kind="${spec.kind}" tabindex="0" role="${slider?"slider":"switch"}" ${slider?`aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}" aria-valuetext="${esc(readout)}"`:`aria-checked="${lit}"`} aria-label="${esc(label)}"${offline?' aria-disabled="true"':""}${tint?` style="--light-tint:${esc(tint)}"`:""}><div class="light-column-fill" style="height:${percent}%"></div>${lightGlyphs.column}</div>
        ${actionsHtml}${presetsHtml}
      </div>`;
  };
  /* Büyük kumanda parlaklığı, renk sıcaklığını, rengi ve gücü zaten sürüyor; aynı kanalın satır
     kopyası listeden düşer, tek iş iki arayüzle yapılmaz. Eleme DAR tutulur: yalnız kumandanın
     çizildiği cihazda ve yalnız kumandanın gerçekten bağladığı kumanda NESNELERİ (`main:brightness`,
     `main:temperature`, `main:color` ve devraldığı güç kanalı) düşer — kumanda çizilmeyen cihazda, ve
     çok kanallı bir cihazın `l2:brightness` gibi kumandanın dokunmadığı kanallarında satır aynen kalır.
     Sessiz işlev kaybı yok. `level`/`temperature`/`color` satırları ne göz ne kalem taşıyordu: göz
     (`visibilityButton`) yalnız `dashboardControlKinds` üyelerinde çıkar ve bu üç kind o kümede DEĞİL,
     kalem (`renameControlButton`) yalnız adlı aç/kapa kanalında çıkar. Renk satırı ayrıca bedelsiz:
     kumanda renk kipinde aynı `.color-picker` + `data-color` arayüzünü gösteriyor. Aç/kapa satırı ise
     yalnız `lightPanelCoversPower` doğruyken düşer — yani gözü kumandaya taşınmış, kalemi olmayan
     kanalda; gerekçesi orada yazılı. */
  const lightPanelCoveredControls=device=>{
    const parts=lightPanelParts(device);
    if(!lightPanelSupported(device,parts))return new Set();
    return new Set([parts.level,parts.temperature,parts.color,lightPanelCoversPower(device,parts)?parts.power:null].filter(Boolean));
  };
  /* Adı hiç verilmemiş cihaz: listede ham kimliğiyle duruyor demektir (kurulum yarım kalmış).
     Aynı ölçü hem isim adımının kutusunu boş açmak hem de kurtarma düğmesini göstermek için. */
  function deviceNeedsName(device){
    const name=String(device?.name??"").trim();
    if(!name)return true;
    return name===device.id||/^0x[0-9a-f]+$/i.test(name);
  }
  const deviceDetailBodyHtml=device=>{
    const updateState=device.state.update&&typeof device.state.update==="object"?device.state.update:{};
    const updateRunning=updateState.state==="updating";
    const updateScheduled=updateState.state==="scheduled";
    const updateLabel=updateRunning?t("updatingFirmware",{progress:Math.round(Number(updateState.progress)||0)}):t(updateScheduled?"cancelScheduledUpdate":"scheduleUpdate");
    const otaButton=device.otaSupported?`<button class="secondary" data-admin-only data-ota-check="${esc(device.id)}"${updateRunning?" disabled":""}>↻ ${t("checkForUpdate")}</button><button class="secondary" data-admin-only data-ota="${esc(device.id)}" data-ota-enabled="${updateScheduled?"false":"true"}"${updateRunning?" disabled":""}>⇧ ${esc(updateLabel)}</button>`:"";
    const photoHtml=`${deviceDetailPhoto(device)}`;
    /* Rozet şeridi detaydan kaldırıldı: "Değerler" kartı aynı bilgiyi satır satır ve birimiyle
       veriyordu, şerit tek başına duran bir tekrar oluyordu. Üreteci (`facts()`) tek çağıranı
       burasıydı; çağrı kalkınca işlev de silindi. Rozetlerin kullandığı çeviri anahtarları
       DURUYOR — hepsi olay akışında ve `primaryStatusForDevice`ta ayrıca kullanılıyor.
       Roller kendi kartında ve SAĞ kolonun en üstünde: "bu cihaz ne?" sorusu kumandadan önce gelir.
       Kart kabuğu ve başlık dili ölçüm kartıyla aynı (`.device-buttons` + `-head`), yeni görsel
       dil icat edilmedi. Başlık mevcut `deviceRoleTitle` anahtarından gelir. */
    /* Kartın kendisi `data-admin-only`: içindeki satırların HEPSİ zaten yöneticiye özel
       (`body.home-mode [data-admin-only]{display:none}`), sarmalayıcı işaretlenmezse ev
       modunda başlığı olan boş bir kart kalırdı. */
    const rolesHtml=`<div class="device-buttons device-detail-roles-card" data-admin-only><div class="device-buttons-head">${esc(t("deviceRoleTitle"))}</div><div class="controls device-detail-roles">${deviceRoleRowsHtml(device)}</div></div>`;
    // Sol kolonda artık YALNIZ resim kartı var; yüksekliği sağ kolondan gelir (bkz. panel.css).
    const mediaHtml=`<div class="device-detail-media">${photoHtml}</div>`;
    // Düğme de bir kontroldür: kumandayı zaten onunla kullanıyoruz. Bu yüzden düğmeler kontrol
    // sütununda, kontrollerin altında durur; aşağıda ikinci bir kopya gösterilmez.
    const covered=lightPanelCoveredControls(device);
    const controlsBodyHtml=device.controls.filter(control=>!covered.has(control)).map(control=>controlHtml(device,control)).join("")+deviceButtonsHtml(device);
    /* Boş-durum metni yalnızca ne kontrol ne de düğme kaldığında (sensör gibi) çıkar. Kumanda
       çizilmişken çıkmaz: her şeyi kumanda devraldıysa "kumanda yok" demek yanlış olurdu. */
    /* "Cihazı kaldır" bakım düğmelerinin yanında durur — onar/yeniden yapılandır ile aynı satırda,
       hemen onun ardında: ikisi de aynı işin (bozuk cihazı düzeltme) iki ucudur. Kırmızı zemin ve
       çerçeve onu bakış açısından ayırır. Yetki `data-admin-only` ile aynı, onay akışı değişmedi.
       Pencerenin alt satırında yalnız kapatma kalır; o satır kaydırma alanının DIŞINDA, belgede
       `#deviceDetailBody`nin kardeşi olarak durur (bkz. `public/index.html`). */
    const panelHtml=lightPanelHtml(device);
    /* Kurulumu yarım kalmış cihaz için tek düğmelik kurtarma: yeni ekran yok, aynı isim/oda/rol
       adımları açılır. Adı verilmiş cihazda bu satır hiç çizilmez. */
    const setupHtml=deviceNeedsName(device)
      ?`<div class="device-setup-recovery"><p>${esc(t("finishSetupLead"))}</p><button class="primary" type="button" data-finish-setup="${esc(device.id)}">${esc(t("finishSetup"))}</button></div>`
      :"";
    /* Kumanda ayrı bir tam genişlik bloğu DEĞİL: ayar satırlarının durduğu sütunun ilk öğesi.
       Üstte tek başına dururken pencerenin başını boş bir şeride çeviriyordu; sütuna girince
       aynı akışın içinde, satırların hemen üstünde okunuyor ve toplam yükseklik artmıyor
       (öncesi: panel + max(medya,satırlar); sonrası: max(medya, panel+satırlar) — asla daha büyük değil).
       Kurulum kurtarma şeridi taşınmadı, pencerenin en üstünde kalır. */
    /* Sağ sütun bir yığın: rol kartı → `-lead` (kumanda paneli ya da kontrol listesi) → `-rest`
       (paneli olan cihazda kontrol listesi + ölçümler). Satırın boyunu bu yığın belirler. */
    const controlsListHtml=`<div class="controls">${controlsBodyHtml||(panelHtml?"":`<div class="device-exposed-empty">${t("noExposedControls")}</div>`)}</div>`;
    const controlsLeadHtml=panelHtml||controlsListHtml;
    /* Ölçümler kumanda sütununun altına iner: aynı sütunda, kontrollerin/düğmelerin hemen
       ardında. `.device-detail-controls` `display:contents` olduğu için doğrudan çocuk
       eklenmez — ızgara gözü tanımlı olan "rest" sarmalayıcının içine girer. */
    const readingsHtml=deviceReadingsHtml(device);
    const controlsRestBody=`${panelHtml?controlsListHtml:""}${readingsHtml}`;
    const controlsRestHtml=controlsRestBody?`<div class="device-detail-controls-rest">${controlsRestBody}</div>`:"";
    return`${setupHtml}<div class="device-detail-layout">
        <div class="device-detail-controls">${rolesHtml}<div class="device-detail-controls-lead">${controlsLeadHtml}</div>${controlsRestHtml}</div>
        ${mediaHtml}
      </div>
      ${deviceRoomsHtml(device)}
      ${deviceVisibilityHtml(device)}
      <details class="technical-details" data-admin-only${state.detailTechnicalOpen?" open":""}><summary>${t("technicalDetails")}</summary><div class="technical-body"><div class="technical">${t("uid")}: ${esc(device.id)}<br>${t("source")}: ${esc(device.sourceName)}<br>${t("model")}: ${esc(device.model||"—")}<br>${t("rawSignal")}: ${rawLinkQuality(device)===null?"—":esc(rawLinkQuality(device))} LQI</div></div></details>
      ${deviceSelfHealHtml(device)}
      <div class="card-actions"><button class="secondary" data-note="${esc(device.id)}">📝 ${t("note")}</button><button class="secondary" data-admin-only data-options="${esc(device.id)}">⚙ ${t("options")}</button><button class="secondary" data-admin-only data-reconfigure="${esc(device.id)}">${t("repairDevice")}</button><button class="remove" type="button" data-admin-only data-remove="${esc(device.id)}">${t("removeDevice")}</button>${otaButton}</div>`;
  };
