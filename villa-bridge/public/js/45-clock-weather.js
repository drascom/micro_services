  const clockLocale=()=>state.language==="tr"?"tr-TR":"en-GB";
  const dateTimeFormatters=new Map();
  function dateTimeFormatter(options){
    const key=`${state.language}|${JSON.stringify(options)}`;
    let formatter=dateTimeFormatters.get(key);
    if(!formatter){
      formatter=new Intl.DateTimeFormat(clockLocale(),options);
      dateTimeFormatters.set(key,formatter);
    }
    return formatter;
  }
  const zoneTime=(now,timeZone)=>dateTimeFormatter({hour:"2-digit",minute:"2-digit",hour12:false,...(timeZone?{timeZone}:{})}).format(now);
  const zoneDay=(now,timeZone)=>dateTimeFormatter({weekday:"short",day:"numeric",month:"short",...(timeZone?{timeZone}:{})}).format(now);
  let renderedClockMinute=null;
  function renderWorldClock(){
    const time=$("#hubTime");
    if(!time)return;
    const now=new Date();
    renderedClockMinute=`${now.getHours()}:${now.getMinutes()}`;
    time.firstChild.nodeValue=zoneTime(now);
    $("#hubSeconds").textContent=String(now.getSeconds()).padStart(2,"0");
    $("#hubDate").textContent=dateTimeFormatter({weekday:"long",day:"numeric",month:"long"}).format(now);
    // Kullanıcının kendi kaydı IANA kimliğini yener: varsayılan "clockLocal" satırı listede her zaman
    // önce geldiği için `find` onu buluyordu ve hub'da ham kimlik ("Istanbul") yazıyordu.
    const namedZone=worldClockZones.find(zone=>zone.timeZone===localTimeZone&&zone.label!=="clockLocal");
    const localName=namedZone?locationName(namedZone):localTimeZone.split("/").pop().replace(/_/g," ");
    $("#hubZoneName").textContent=`${localName} · ${t("clockLocal")}`;
  }
  /* BAŞLIKTA YALNIZ SAAT, TARİH VE KONUM VAR (not §6.1). Dünya saatlerinin ve alarm özetinin
     hub'daki satırları kalktı: ikisi de `#clockDialog`un içinde tam hâliyle duruyor (şehir
     listesi + `#alarmStatus`) ve başlığın tek satırlık boyu ana ekranın dikey payını yemesin
     diye orada kalıyorlar. Bu yüzden `renderHubCities`/`renderHubAlarm` de kalktı — çağrıları
     boşa dönen ölü kod olurdu. */
  function tickWorldClock(){
    const now=new Date();
    const seconds=$("#hubSeconds");
    if(!seconds)return;
    if(renderedClockMinute!==`${now.getHours()}:${now.getMinutes()}`)renderWorldClock();
    else seconds.textContent=String(now.getSeconds()).padStart(2,"0");
    if($("#clockDialog").open)renderClockDialogRows();
    // Alarm denetimi saatin kendi tikine binir: ayrı bir zamanlayıcı kurulmaz, her turda
    // yeniden hesaplanır (bkz. `checkAlarmDue`).
    checkAlarmDue(now);
  }
  function scheduleWorldClockTick(){
    setTimeout(()=>{tickWorldClock();scheduleWorldClockTick()},1000-new Date().getMilliseconds()+20);
  }
  const locationKey=location=>String(location?.id||`${location?.latitude},${location?.longitude},${location?.timeZone||""}`);
  const locationName=location=>location?.label?t(location.label):location?.name||t("unknownLocation");
  const locationDetails=location=>{
    const values=[location?.admin1,location?.country].filter((value,index,array)=>value&&array.indexOf(value)===index);
    return values.join(", ")||location?.timeZone||"";
  };
  // Kabaca 11 metrelik yuvarlama: aynı yer iki kaynaktan gelse de tek satır "seçili" görünür.
  const locationCoordKey=location=>`${Number(location?.latitude).toFixed(4)},${Number(location?.longitude).toFixed(4)}`;
  const locationSelectionKey=(kind,location)=>kind==="home"?locationCoordKey(location):locationKey(location);
  function normalizeLocationResult(result){
    const latitude=Number(result?.latitude);
    const longitude=Number(result?.longitude);
    if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||typeof result?.name!=="string")return null;
    return{
      id:String(result.id||`${latitude},${longitude}`),
      name:result.name.slice(0,80),
      country:typeof result.country==="string"?result.country.slice(0,80):"",
      admin1:typeof result.admin1==="string"?result.admin1.slice(0,80):"",
      // Sunucu `timeZone` döndürür; ham Open-Meteo kaydında alan `timezone` idi. İkisi de okunur.
      timeZone:typeof result.timeZone==="string"?result.timeZone.slice(0,80):typeof result.timezone==="string"?result.timezone.slice(0,80):"",
      latitude,
      longitude
    };
  }
  const knownTimeZone=zone=>{try{new Intl.DateTimeFormat("en",{timeZone:zone}).format(new Date());return true}catch{return false}};
  function normalizeWorldClockZone(zone){
    const timeZone=typeof zone?.timeZone==="string"?zone.timeZone.slice(0,80):"";
    if(!timeZone||!knownTimeZone(timeZone))return null;
    const name=typeof zone.name==="string"?zone.name.slice(0,80):"";
    const cut=value=>typeof value==="string"?value.slice(0,80):"";
    return{id:String(zone.id||`${name}-${timeZone}`).slice(0,80),name,country:cut(zone.country),admin1:cut(zone.admin1),timeZone,label:cut(zone.label)};
  }
  function renderWorldClockZones(){
    renderClockDialogRows();
    renderLocationSearchResults("clock");
    renderWorldClock();
  }
  /* Şehir listesi evin ayarı: sunucuda durur, cihazda değil. Sunucuya ulaşılamazsa ekrandaki
     liste olduğu gibi kalır — saat bloğu boşalmaz. */
  async function loadWorldClockZones(){
    try{
      const data=await api("/api/world-clock");
      if(!Array.isArray(data?.zones))return;
      worldClockZonesConfigured=true;
      worldClockZones=data.zones.map(normalizeWorldClockZone).filter(Boolean);
      renderWorldClockZones();
    }catch(error){console.warn("world-clock",error)}
  }
  /* Yazma yönetici işi olduğu için ev sakininde 403 gelir; hata sessizce yutulmaz, kullanıcıya
     sunucunun kendi metni gösterilir ve liste eski hâline döner (ekranda yalan durmaz). */
  async function saveWorldClockZones(zones,silent){
    const previous=worldClockZones;
    worldClockZones=zones;
    renderWorldClockZones();
    try{
      const data=await api("/api/world-clock",{method:"PUT",body:JSON.stringify({zones})});
      worldClockZonesConfigured=true;
      if(Array.isArray(data?.zones))worldClockZones=data.zones.map(normalizeWorldClockZone).filter(Boolean);
    }catch(error){
      worldClockZones=previous;
      // Göç denemesi sessizdir: ev sakininin ekranında her açılışta "yönetici gerekli" uyarısı
      // belirmesin, kayıt dursun ve bir dahaki sefere yeniden denensin.
      if(!silent)showToast(error.message,true);
      throw error;
    }finally{
      renderWorldClockZones();
    }
  }
  /* Bir kerelik göç: cihazda kalmış liste, sunucuda liste hiç tanımlı DEĞİLKEN yukarı taşınır.
     Taşınamazsa (yönetici değil, ağ yok) yerel kayıt DURUR — bir sonraki açılışta yeniden
     denenir, kullanıcı listesini kaybetmez. */
  async function migrateWorldClockZones(){
    if(!savedWorldClockZones||worldClockZonesConfigured)return;
    try{
      await saveWorldClockZones(savedWorldClockZones,true);
      try{localStorage.removeItem("villa-world-clock-zones")}catch{}
    }catch{}
  }
  function renderClockDialogRows(){
    const container=$("#clockDialogRows");
    if(!container)return;
    const now=new Date();
    container.innerHTML=worldClockZones.length?worldClockZones.map(city=>`<div class="hub-row"><div><strong>${esc(locationName(city))}</strong><small>${esc(zoneDay(now,city.timeZone))} · ${esc(locationDetails(city)||city.timeZone)}</small></div><span class="hub-row-value">${esc(zoneTime(now,city.timeZone))}</span><button type="button" data-remove-clock-city="${esc(locationKey(city))}" aria-label="${esc(t("removeCity",{name:locationName(city)}))}"><svg class="location-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5"/></svg></button></div>`).join(""):`<div class="location-empty">${t("noClockCities")}</div>`;
    $$("[data-remove-clock-city]").forEach(button=>button.onclick=()=>{
      void saveWorldClockZones(worldClockZones.filter(city=>locationKey(city)!==button.dataset.removeClockCity)).catch(()=>{});
    });
  }
  function renderLocationSearchResults(kind){
    const search=locationSearchState[kind];
    const status=$(`#${kind}SearchStatus`);
    const results=$(`#${kind}SearchResults`);
    if(!status||!results)return;
    status.className=`location-search-status${search.error?" error":""}`;
    status.textContent=search.loading?t("locationSearching"):search.error||(!search.loading&&search.query.length>=2&&!search.results.length?t("locationNoResults"):"");
    // Hata: hava listesinde eylem ikonu bir onay işaretiydi, her satırda basıldığı için beş sonuç da
    // "seçili" görünüyordu. Onay işareti artık yalnız gerçekten seçili konumda; diğerleri harita iğnesi.
    // Evin konumu sunucuda yalnız koordinat olarak durur; kimliği olmadığı için karşılaştırma koordinattan.
    const chosen=kind==="weather"?weatherState.location:kind==="home"?state.homeLocation:null;
    const chosenKey=chosen?locationSelectionKey(kind,chosen):null;
    results.innerHTML=search.results.map((location,index)=>{
      const key=locationKey(location);
      const alreadyAdded=kind==="clock"&&worldClockZones.some(city=>locationKey(city)===key);
      const selected=chosenKey!==null&&locationSelectionKey(kind,location)===chosenKey;
      const glyph=kind==="clock"?'<path d="M12 5v14M5 12h14"/>':selected?'<path d="m5 12 4 4L19 6"/>':'<path d="M12 21s6.5-5.4 6.5-10.5a6.5 6.5 0 1 0-13 0C5.5 15.6 12 21 12 21Z"/><circle cx="12" cy="10.5" r="2.4"/>';
      return`<div class="location-result${selected?" is-selected":""}"${selected?' aria-current="true"':""}><div><strong>${esc(location.name)}</strong><small>${esc(locationDetails(location))}</small>${selected?`<em class="location-selected-tag">${esc(t("locationSelected"))}</em>`:""}</div><button type="button" data-location-result="${index}" aria-label="${esc(t(kind==="clock"?"addCity":selected?"locationSelected":"chooseLocation",{name:location.name}))}"${alreadyAdded?" disabled":""}><svg class="location-action-icon" viewBox="0 0 24 24" aria-hidden="true">${glyph}</svg></button></div>`;
    }).join("");
    $$(`#${kind}SearchResults [data-location-result]`).forEach(button=>button.onclick=()=>{
      const location=search.results[Number(button.dataset.locationResult)];
      if(!location)return;
      if(kind==="clock")addWorldClockCity(location);
      else if(kind==="home")chooseHomeLocation(location);
      else chooseWeatherLocation(location);
    });
  }
  async function searchLocations(kind,query){
    const search=locationSearchState[kind];
    const requestId=++search.requestId;
    search.loading=true;
    search.error=null;
    renderLocationSearchResults(kind);
    try{
      // Arama sunucudan geçer: panel hiçbir dış servise doğrudan çıkmaz.
      const params=new URLSearchParams({q:query,language:state.language});
      const data=await api(`/api/locations/search?${params}`);
      if(requestId!==search.requestId)return;
      search.results=(Array.isArray(data?.results)?data.results:[]).map(normalizeLocationResult).filter(Boolean);
    }catch(error){
      if(requestId!==search.requestId)return;
      search.results=[];
      // Sunucu metni Türkçe; ekranda panelin dili kalsın diye çeviri anahtarı yazılır.
      console.warn("location-search",error);
      search.error=t("locationSearchUnavailable");
    }finally{
      if(requestId===search.requestId){
        search.loading=false;
        renderLocationSearchResults(kind);
      }
    }
  }
  function scheduleLocationSearch(kind){
    const input=$(locationSearchInputs[kind]);
    const search=locationSearchState[kind];
    const query=input.value.trim();
    clearTimeout(search.timer);
    search.query=query;
    search.error=null;
    search.results=[];
    search.requestId++;
    if(query.length<2){
      search.loading=false;
      renderLocationSearchResults(kind);
      return;
    }
    search.loading=true;
    renderLocationSearchResults(kind);
    search.timer=setTimeout(()=>searchLocations(kind,query),320);
  }
  function resetLocationSearch(kind){
    const search=locationSearchState[kind];
    clearTimeout(search.timer);
    search.query="";
    search.results=[];
    search.loading=false;
    search.error=null;
    search.requestId++;
    const input=$(locationSearchInputs[kind]);
    if(input)input.value="";
    renderLocationSearchResults(kind);
  }
  function openClockManager(){
    resetLocationSearch("clock");
    renderClockDialogRows();
    renderAlarmSettings();
    if(!$("#clockDialog").open)$("#clockDialog").showModal();
  }
  function addWorldClockCity(location){
    if(!location.timeZone)return showToast(t("locationTimezoneUnavailable"),true);
    if(worldClockZones.some(city=>locationKey(city)===locationKey(location)))return;
    if(worldClockZones.length>=8)return showToast(t("clockCityLimit"),true);
    void saveWorldClockZones([...worldClockZones,location]).catch(()=>{});
  }
  function openWeatherLocationManager(){
    resetLocationSearch("weather");
    // Arama alanına odaklanmıyoruz: odağı `showModal` başlığa taşır, klavye kullanıcı
    // alana dokununca açılır.
    $("#weatherLocationDialog").showModal();
  }
  /* Şehir seçimi sunucuya yazılır ve oradan bütün ekranlara iner. Yazma yönetici işi olduğu için
     ev sakininde 403 gelir; hata sessizce yutulmaz, kullanıcıya sunucunun kendi metni gösterilir. */
  async function chooseWeatherLocation(location){
    try{
      const data=await api("/api/weather/location",{method:"PUT",body:JSON.stringify({location})});
      applyWeatherSnapshot(data);
      $("#weatherLocationDialog").close();
      renderWeather();
      renderLocationSearchResults("weather");
    }catch(error){showToast(error.message,true)}
  }
  /* Bir kerelik göç: cihazda kalmış seçim (ör. "London"), sunucuda hiç konum yokken yukarı
     taşınır. Taşınamazsa (yönetici değil, ağ yok) yerel kayıt DURUR — bir sonraki açılışta
     yeniden denenir, kullanıcı seçimini kaybetmez. Veri önbelleği her hâlükârda silinir. */
  async function migrateWeatherLocation(){
    try{localStorage.removeItem("villa-weather-cache")}catch{}
    if(!savedWeatherLocation||weatherState.location)return;
    try{
      applyWeatherSnapshot(await api("/api/weather/location",{method:"PUT",body:JSON.stringify({location:savedWeatherLocation})}));
      try{localStorage.removeItem("villa-weather-location")}catch{}
      renderWeather();
    }catch{}
  }
  function applyWeatherSnapshot(data){
    weatherState.location=data?.location||null;
    weatherState.data=data?.data||null;
    weatherState.updatedAt=Number(data?.updatedAt)||0;
    // Sunucudaki hata notu ("internet yok") son bilinen veriyle birlikte gelir; veri varsa blok
    // yine dolu görünür, notu `renderWeather` yazar. Sunucunun kendi metni Türkçedir ve panelin
    // dilinden bağımsızdır — ekrana çevrilmiş metin konur, ham hata yalnız konsola düşer.
    if(data?.error)console.warn("weather",data.error);
    weatherState.error=data?.error?t("weatherUnavailable"):null;
    weatherState.checkedAt=Date.now();
  }
  // Hava ikonları panelin geri kalanıyla aynı dili konuşur: 24×24 kutu, dolgusuz, `currentColor`
  // çizgi. Tek karakterlik metin glifleri (☀ ⛅ …) yazı tipine göre değişip bulanık duruyordu.
  // `scene` yalnız arka plandaki hava sahnesini seçer (CSS `[data-weather-scene]`).
  // DİKKAT: bu işlev testte yalıtılmış çalıştırılıyor — dışarıya bağlanma, parçalar içeride dursun.
  function weatherPresentation(code,isDay){
    const icon=body=>`<svg class="weather-icon" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
    const smallCloud='<path d="M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z"/>';
    const wideCloud='<path d="M4 14.9A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.24"/>';
    if(code===0)return isDay
      ?{icon:icon('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>'),label:"weatherClear",scene:"clear-day"}
      :{icon:icon('<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>'),label:"weatherClear",scene:"clear-night"};
    if(code===1)return isDay
      ?{icon:icon('<path d="M12 2v2M4.93 4.93l1.41 1.41M20 12h2M19.07 4.93l-1.41 1.41"/><path d="M15.95 12.65a4 4 0 0 0-5.93-4.13"/>'+smallCloud),label:"weatherCloudy",scene:"partly-day"}
      :{icon:icon('<g transform="translate(9.7 .2) scale(.5)" stroke-width="3.6"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></g>'+smallCloud),label:"weatherCloudy",scene:"partly-night"};
    if(code<=3)return{icon:icon('<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/>'),label:"weatherCloudy",scene:"cloudy"};
    if(code<=48)return{icon:icon(`${wideCloud}<path d="M16 17H7M17 21H9"/>`),label:"weatherFog",scene:"fog"};
    if(code<=67||code>=80&&code<=82)return{icon:icon(`${wideCloud}<path d="M16 14v6M8 14v6M12 16v6"/>`),label:"weatherRain",scene:"rain"};
    if(code<=77||code>=85&&code<=86)return{icon:icon(`${wideCloud}<path d="M8 15h.01M8 19h.01M12 17h.01M12 21h.01M16 15h.01M16 19h.01"/>`),label:"weatherSnow",scene:"snow"};
    if(code>=95)return{icon:icon('<path d="M6 16.33A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.97"/><path d="m13 12-3 5h4l-3 5"/>'),label:"weatherStorm",scene:"storm"};
    return{icon:icon('<circle cx="12" cy="12" r="8.5" stroke-dasharray="2.6 3.6"/>'),label:"weatherUnknown",scene:"unknown"};
  }
  /* Hava bölgesinin arkasındaki sahne hazır bir setten gelir: Meteocons — `public/assets/weather`,
     MIT (bkz. oradaki README/LICENSE). Kendi keyframe'imiz yok; animasyon setin kendi SMIL
     animasyonudur, bu yüzden görsel CSS arka planı değil gerçek bir `<img>` olarak konur
     (arka plan görseli olarak bazı motorlarda animasyon donuyor). `prefers-reduced-motion`
     açıkken setin durağan kopyası (`-static`) kullanılır. */
  const weatherSceneFiles={
    "clear-day":"clear-day",
    "clear-night":"clear-night",
    "partly-day":"partly-cloudy-day",
    "partly-night":"partly-cloudy-night",
    cloudy:"cloudy",
    fog:"fog",
    rain:"rain",
    snow:"snow",
    storm:"thunderstorms-rain",
    unknown:"not-available"
  };
  const weatherSceneAsset=scene=>{
    const file=weatherSceneFiles[scene];
    return file?`/assets/weather/${file}${reducedMotion()?"-static":""}.svg`:"";
  };
  function applyWeatherScene(scene){
    const zone=$("#hubWeatherZone");
    if(!zone)return;
    const source=weatherSceneAsset(scene);
    const existing=zone.querySelector(".hub-weather-scene");
    if(!source){
      delete zone.dataset.weatherScene;
      existing?.remove();
      return;
    }
    zone.dataset.weatherScene=scene;
    const image=existing||document.createElement("img");
    if(!existing){
      image.className="hub-weather-scene";
      image.alt="";
      image.setAttribute("aria-hidden","true");
      zone.prepend(image);
    }
    if(!image.getAttribute("src")||image.getAttribute("src")!==source)image.setAttribute("src",source);
  }
  const weatherLocationText=()=>weatherState.location?`${locationName(weatherState.location)}${locationDetails(weatherState.location)?`, ${locationDetails(weatherState.location)}`:""}`:t("weatherNoLocation");
  const weatherNumber=value=>Number.isFinite(Number(value))?Math.round(Number(value)):null;
  const weatherValue=(value,unit)=>weatherNumber(value)===null?"—":`${weatherNumber(value)}${unit||""}`;
  function weatherDailyEntries(limit){
    const daily=weatherState.data?.daily;
    if(!Array.isArray(daily?.time))return[];
    return daily.time.slice(0,limit).map((day,index)=>({
      day,
      index,
      code:Number(daily.weather_code?.[index]),
      max:daily.temperature_2m_max?.[index],
      min:daily.temperature_2m_min?.[index]
    }));
  }
  function weatherDayLabel(day,index){
    if(index===0)return t("hubToday");
    if(index===1)return t("hubTomorrow");
    const parsed=new Date(`${day}T12:00:00`);
    return Number.isNaN(parsed.getTime())?day:dateTimeFormatter({weekday:"long"}).format(parsed);
  }
  function weatherHourlyEntries(limit){
    const hourly=weatherState.data?.hourly;
    if(!Array.isArray(hourly?.time))return[];
    const from=String(weatherState.data?.current?.time||"").slice(0,13);
    const start=Math.max(0,hourly.time.findIndex(time=>String(time).slice(0,13)>=from));
    return hourly.time.slice(start,start+limit).map((time,offset)=>{
      const index=start+offset;
      return{
        time,
        code:Number(hourly.weather_code?.[index]),
        isDay:Number(hourly.is_day?.[index])!==0,
        temperature:hourly.temperature_2m?.[index]
      };
    });
  }
  function weatherAgeText(){
    if(!weatherState.updatedAt)return"";
    const at=new Date(weatherState.updatedAt);
    const sameDay=at.toDateString()===new Date().toDateString();
    return dateTimeFormatter(sameDay?{hour:"2-digit",minute:"2-digit",hour12:false}:{weekday:"short",hour:"2-digit",minute:"2-digit",hour12:false}).format(at);
  }
  // Hub'ın hava detayı: bugünün en yüksek/en düşük değeri (`daily`) ve nem (`current`).
  // Eksik alan satırı düşürür, hepsi eksikse hiç düğüm üretilmez — konum seçilmemişken zaten
  // `weatherState.data` boş olduğu için buraya hiç girilmez, davet satırı olduğu gibi kalır.
  function hubWeatherStats(current,units){
    const today=weatherDailyEntries(1)[0];
    const hasRange=weatherNumber(today?.max)!==null&&weatherNumber(today?.min)!==null;
    const hasHumidity=weatherNumber(current?.relative_humidity_2m)!==null;
    const range=hasRange?`<span class="hub-w-stat">${esc(t("hubWeatherRange",{high:weatherValue(today.max,"°"),low:weatherValue(today.min,"°")}))}</span>`:"";
    const humidity=hasHumidity?`<span class="hub-w-stat hub-w-stat-extra">${esc(t("hubWeatherHumidity",{humidity:weatherValue(current.relative_humidity_2m,units?.relative_humidity_2m||"%")}))}</span>`:"";
    return range||humidity?`<span class="hub-w-stats">${range}${humidity}</span>`:"";
  }
  function renderWeather(){
    const body=$("#hubWeatherBody");
    if(!body)return;
    // Hava sahnesi yalnız hava bölgesinde durur; saat tarafına ve ekran zeminine bulaşmaz.
    // Bölgenin en üstündeki asıl görseldir (CSS `order` ile konum etiketinin altına oturur).
    // Veri yokken/hata varken görsel kaldırılır, blok yalnız yazıya iner.
    /* Hub'da yalnız ŞEHİR yazar. Uzun hâli ("LONDON, ENGLAND, UNITED KINGDOM") saat sütunundaki
       "London · Yerel saat" satırıyla aynı şeyi ikinci kez söylüyordu; tam adres hava
       penceresinin başlık satırında (`renderWeatherDialog`) duruyor. */
    $("#hubWeatherLocation").textContent=weatherState.location?locationName(weatherState.location):t("weather");
    if(!weatherState.data){
      applyWeatherScene("");
      const message=weatherState.loading?t("weatherLoading"):weatherState.error||(weatherState.location?t("weatherUnavailable"):t("weatherNoLocationHint"));
      body.innerHTML=`<span class="hub-w-cond">${esc(message)}</span>`;
    }else{
      const current=weatherState.data.current||{};
      const units=weatherState.data.current_units||{};
      const presentation=weatherPresentation(Number(current.weather_code),Number(current.is_day)!==0);
      applyWeatherScene(presentation.scene);
      const degree=esc(units.temperature_2m||"°C");
      // Hub "şu an"ın yanına bugünün uçlarını ve nemi yazar; saatlik/günlük tahmin
      // `#weatherDialog`da duruyor. Üçü de zaten çekilen alanlar — ek istek yok.
      const note=weatherState.error?t("weatherOfflineNote",{time:weatherAgeText()}):weatherIsStale()?t("weatherStaleNote",{time:weatherAgeText()}):"";
      // Küçük çizgi ikon burada YOK: bölgenin ikonu artık üstteki büyük sahne görseli
      // (`.hub-weather-scene`). İki güneş çizmemek için hub'da tek ikon kaldı; ekran koruyucu ve
      // diyalogdaki listeler kendi küçük ikonlarını (`weatherPresentation`) kullanmaya devam eder.
      body.innerHTML=`<span class="hub-now"><b class="hub-w-temp">${esc(weatherValue(current.temperature_2m,"°"))}</b><span class="hub-w-cond">${esc(t(presentation.label))} · ${esc(t("weatherFeelsLike"))} ${esc(weatherValue(current.apparent_temperature,degree))}</span>${hubWeatherStats(current,units)}</span>${note?`<span class="hub-note">${esc(note)}</span>`:""}`;
    }
    if($("#weatherDialog").open)renderWeatherDialog();
  }
  function renderWeatherDialog(){
    const meta=$("#weatherDialogMeta");
    const body=$("#weatherDialogBody");
    if(!meta||!body)return;
    meta.textContent=weatherState.location?`${weatherLocationText()}${weatherState.updatedAt?` · ${t("weatherUpdated")} ${weatherAgeText()}`:""}`:t("weatherNoLocationHint");
    if(!weatherState.data){
      body.innerHTML=`<p class="location-empty">${esc(weatherState.loading?t("weatherLoading"):weatherState.error||(weatherState.location?t("weatherUnavailable"):t("weatherNoLocationHint")))}</p>`;
      return;
    }
    const current=weatherState.data.current||{};
    const units=weatherState.data.current_units||{};
    const dailyUnits=weatherState.data.daily_units||{};
    const presentation=weatherPresentation(Number(current.weather_code),Number(current.is_day)!==0);
    const degree=esc(units.temperature_2m||"°C");
    const daily=weatherState.data.daily||{};
    const sunrise=String(daily.sunrise?.[0]||"").slice(11,16);
    const sunset=String(daily.sunset?.[0]||"").slice(11,16);
    const chips=[
      `${t("weatherFeelsLike")} ${weatherValue(current.apparent_temperature,degree)}`,
      `${t("weatherHumidity")} ${weatherValue(current.relative_humidity_2m,units.relative_humidity_2m||"%")}`,
      `${t("weatherWind")} ${weatherValue(current.wind_speed_10m,"")} ${units.wind_speed_10m||"km/h"}`,
      sunrise?`${t("weatherSunrise")} ${sunrise}`:"",
      sunset?`${t("weatherSunset")} ${sunset}`:""
    ].filter(Boolean).map(text=>`<span>${esc(text)}</span>`).join("");
    const hours=weatherHourlyEntries(12).map(entry=>{
      const shape=weatherPresentation(entry.code,entry.isDay);
      return`<span>${esc(String(entry.time).slice(11,16))}<i aria-hidden="true">${shape.icon}</i><b>${esc(weatherValue(entry.temperature,"°"))}</b></span>`;
    }).join("");
    const days=weatherDailyEntries(4).map(entry=>{
      const shape=weatherPresentation(entry.code,true);
      return`<div class="hub-row"><div><strong>${esc(weatherDayLabel(entry.day,entry.index))}</strong><small>${esc(t(shape.label))}</small></div><span class="hub-row-value" aria-hidden="true">${shape.icon}</span><span class="hub-row-value">${esc(weatherValue(entry.max,dailyUnits.temperature_2m_max||"°"))} / ${esc(weatherValue(entry.min,dailyUnits.temperature_2m_min||"°"))}</span></div>`;
    }).join("");
    const note=weatherState.error?t("weatherOfflineNote",{time:weatherAgeText()}):weatherIsStale()?t("weatherStaleNote",{time:weatherAgeText()}):"";
    body.innerHTML=`${note?`<p class="hub-note">${esc(note)}</p>`:""}<div class="hub-now-big"><span class="hub-now-icon" aria-hidden="true">${presentation.icon}</span><div><div class="hub-now-temp">${esc(weatherValue(current.temperature_2m,degree))}</div><div class="hub-w-cond">${esc(t(presentation.label))}</div></div></div><div class="hub-chips">${chips}</div>${hours?`<section class="hub-section" style="margin-top:16px"><h3>${esc(t("weatherHourly"))}</h3><div class="hub-hours" tabindex="0" role="group" aria-label="${esc(t("weatherHourly"))}">${hours}</div></section>`:""}${days?`<section class="hub-section" style="margin-top:16px"><h3>${esc(t("weatherDaily"))}</h3><div class="hub-rows">${days}</div></section>`:""}`;
  }
  function openWeatherDialog(){
    if(!weatherState.location){
      openWeatherLocationManager();
      return;
    }
    renderWeatherDialog();
    if(!$("#weatherDialog").open)$("#weatherDialog").showModal();
    refreshWeatherIfNeeded();
  }
  /* Tek veri yolu: sunucu. Konumu da veriyi de o söyler, panel bir şey hesaplamaz. Sunucuya
     ulaşılamazsa elimizdeki son okuma ekranda kalır — blok boşalmaz. */
  function loadWeather(){
    // Uçuştaki istek paylaşılır: aynı anda birden çok yerden çağrılsa da (açılış, yeniden çizim,
    // sekmeye dönüş) tek istek gider ve HEPSİ aynı sonucu bekler. Erken `return` yerine ortak
    // söz döndürmek, açılıştaki göç adımının sunucunun cevabını gerçekten görmesini sağlar.
    if(weatherState.request)return weatherState.request;
    weatherState.loading=true;
    renderWeather();
    weatherState.request=(async()=>{
      try{
        applyWeatherSnapshot(await api("/api/weather"));
      }catch(error){
        weatherState.checkedAt=Date.now();
        weatherState.error=error?.message||t("weatherUnavailable");
        console.warn("weather",error);
      }finally{
        weatherState.request=null;
        weatherState.loading=false;
        renderWeather();
      }
    })();
    return weatherState.request;
  }
  // "Mevcut konumu kullan" kaldırıldı: panel düz HTTP ile servis edildiği için tarayıcının konum
  // servisi güvenli köken şartını karşılamıyor, düğme her zaman sessizce "izin verilmedi"ye düşüyordu.
  /* Sormak artık ucuz (kendi sunucumuz), ama her yeniden çizimde istek atılmasın diye bir
     dakikalık taban var. Sayfa yeniden görünür olduğunda `loadWeather` DOĞRUDAN çağrılır. */
  const weatherPollFloor=60000;
  function refreshWeatherIfNeeded(){
    if(weatherState.loading)return;
    if(!weatherState.checkedAt||Date.now()-weatherState.checkedAt>weatherPollFloor)loadWeather();
  }
  /* ——— ALARM ———
     TEK alarm, CİHAZDA durur (`localStorage`): hangi tablette çalacağı kullanıcının bilerek
     verdiği bir karar, sunucuya taşınmaz. Ayarın tamamı üç alandır — açık/kapalı, saat, tekrar
     (günlük ya da haftada bir gün). Alarm hiçbir cihaza komut göndermez; yalnız burada çalar. */
  const alarmStorageKey="villa-alarm";
  // Geç tetikleme penceresi: uykudan saatler sonra uyanan tablet geçmiş bir alarmı çalmasın.
  const alarmGraceMs=120000;
  // Kimse dokunmazsa uyarı kendiliğinden kapanır — duvarda sonsuza kadar öten tablet olmaz.
  const alarmAutoStopMs=120000;
  /* Sesin kademelenmesi tek yerden ayarlanır. Uyandırma eğrisi: ilk vuruşlar alçak, seyrek ve
     yumuşak; `alarmRampMs` boyunca hem seviye hem tını sertliği artar, vuruş arası kısalır.
     Eğri üstel (`alarmRampCurve`) — başlangıç bölgesinde uzun süre nazik kalır, sona doğru
     hızla sertleşir. Tepe seviye 1'in belirgin altında ve zincirin ucunda bir sıkıştırıcı var:
     tablet hoparlöründe kırık/bozuk çıkış olmasın. */
  const alarmRampMs=32000;
  const alarmRampCurve=1.6;
  const alarmChimeGapStartMs=2600;
  const alarmChimeGapPeakMs=1150;
  const alarmLevelStart=0.08;
  const alarmLevelPeak=0.42;
  // Sertlik karışımı: kare bileşen tınıya üst harmonik katar, oktav üstü bileşen "delici"liği verir.
  const alarmHarshMix=0.45;
  const alarmPierceMix=0.30;
  const alarmChimeNotes=[880,1174.66];
  const alarmNoteGapStart=0.24;
  const alarmNoteGapPeak=0.15;
  const alarmDecayStart=0.85;
  const alarmDecayPeak=0.34;
  const alarmDefaultTime="07:30";
  const normalizeAlarmTime=value=>/^([01]\d|2[0-3]):[0-5]\d$/.test(String(value))?String(value):alarmDefaultTime;
  /* Gün kimliği YEREL tarihten kurulur (ISO/UTC değil): yaz saati geçişinde ya da cihaz saati
     düzeltildiğinde "bugün" kayması olmasın. `lastFired` aynı alarmın aynı gün iki kez
     çalmasını engeller. */
  const alarmDayKey=date=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
  const alarmSetting=(()=>{try{
    const value=JSON.parse(localStorage.getItem(alarmStorageKey)||"null");
    const weekday=Number(value?.weekday);
    return{
      enabled:value?.enabled===true,
      time:normalizeAlarmTime(value?.time),
      repeat:value?.repeat==="weekly"?"weekly":"daily",
      weekday:Number.isInteger(weekday)&&weekday>=0&&weekday<=6?weekday:1,
      lastFired:typeof value?.lastFired==="string"?value.lastFired.slice(0,10):""
    };
  }catch{return{enabled:false,time:alarmDefaultTime,repeat:"daily",weekday:1,lastFired:""}}})();
  function persistAlarmSetting(){try{localStorage.setItem(alarmStorageKey,JSON.stringify(alarmSetting))}catch{}}
  function alarmAt(date){
    const[hour,minute]=alarmSetting.time.split(":").map(Number);
    const target=new Date(date);
    target.setHours(hour,minute,0,0);
    return target;
  }
  const alarmRingsOn=date=>alarmSetting.repeat!=="weekly"||date.getDay()===alarmSetting.weekday;
  /* Sıradaki çalma anı gün gün taranarak bulunur (en çok sekiz gün): haftalık kip de, günü
     geçmiş alarmın "yarın"ı da aynı döngüden çıkar. Hiçbir yerde gün ötesine sarkan tek bir
     `setTimeout` tutulmaz — değer her sorulduğunda yeniden hesaplanır. */
  function nextAlarmDate(from=new Date()){
    if(!alarmSetting.enabled)return null;
    for(let offset=0;offset<8;offset++){
      const day=new Date(from);
      day.setDate(day.getDate()+offset);
      const target=alarmAt(day);
      if(!alarmRingsOn(target))continue;
      if(target.getTime()<=from.getTime())continue;
      if(alarmSetting.lastFired===alarmDayKey(target))continue;
      return target;
    }
    return null;
  }
  function alarmWhenText(date){
    const today=new Date();
    const tomorrow=new Date(today);
    tomorrow.setDate(tomorrow.getDate()+1);
    const key=alarmDayKey(date);
    const day=key===alarmDayKey(today)?t("hubToday"):key===alarmDayKey(tomorrow)?t("hubTomorrow"):dateTimeFormatter({weekday:"long"}).format(date);
    return`${day} ${zoneTime(date)}`;
  }
  // Gün adları biçimlendiriciden gelir: yeni bir dil eklemek için burada yapılacak iş yok.
  function alarmWeekdayOptions(){
    const week=new Date();
    week.setDate(week.getDate()-week.getDay());
    return[0,1,2,3,4,5,6].map(index=>{
      const day=new Date(week);
      day.setDate(day.getDate()+index);
      return`<option value="${index}">${esc(dateTimeFormatter({weekday:"long"}).format(day))}</option>`;
    }).join("");
  }
  /* SAAT SEÇİCİ — panelin kendi pop-up'ı. `<input type="time">` kaldırıldı: Android WebView onun
     için yerel bir saat penceresi açıyor ve o pencerenin alt onay şeridi 800px yüksekliğinde
     kırpılıyordu; boyutu bizim CSS'imizle kontrol edilemiyordu. Yerine iPhone tarzı iki tekerlek
     kondu; saat penceresinde yalnız "07:30" yazan tek bir düğme durur, liste orayı şişirmez.
     Oturma (snap) tümüyle CSS'in işi (`panel.css` → `.time-wheel`); buradaki JS ortadaki satırı
     okur, komşuları soldurur ve klavye oklarını kaydırmaya çevirir. Kayıt biçimi aynı: "HH:MM". */
  const alarmMinuteStep=5;
  const alarmPad2=value=>String(value).padStart(2,"0");
  const alarmTimeParts=()=>({hour:Number(alarmSetting.time.slice(0,2)),minute:Number(alarmSetting.time.slice(3,5))});
  const alarmHourChoices=()=>Array.from({length:24},(unused,hour)=>hour);
  function alarmMinuteChoices(){
    const minutes=[];
    for(let minute=0;minute<60;minute+=alarmMinuteStep)minutes.push(minute);
    // 5'in katı olmayan eski bir kayıt sessizce yuvarlanmaz; kendi değeri listeye eklenir.
    const current=alarmTimeParts().minute;
    if(Number.isInteger(current)&&!minutes.includes(current))minutes.push(current);
    return minutes.sort((first,second)=>first-second);
  }
  const alarmWheels=()=>[[$("#alarmHourWheel"),alarmHourChoices()],[$("#alarmMinuteWheel"),alarmMinuteChoices()]];
  // `offsetHeight`, `getBoundingClientRect` DEĞİL: satırlar `transform:scale()` ile soluklaşıyor,
  // ölçülen kutu da onunla küçülürdü. Yerleşim yüksekliği ölçekten etkilenmez.
  const alarmWheelRow=wheel=>wheel.firstElementChild?.offsetHeight||0;
  function alarmWheelFill(wheel,values){
    const wanted=values.join(",");
    if(wheel.dataset.values===wanted)return;
    wheel.dataset.values=wanted;
    wheel.dataset.index="";
    wheel.innerHTML=values.map(value=>`<div class="time-wheel-option" role="option" aria-selected="false" data-wheel-value="${value}">${alarmPad2(value)}</div>`).join("");
  }
  /* Seçili satır ortadaki banttan okunur. Dolgu üstte ve altta tam iki satır olduğu için
     `scrollTop` her zaman "satır yüksekliği × sıra"dır; oran CSS ile ortak, ikisi birlikte değişir. */
  function alarmWheelIndex(wheel){
    const row=alarmWheelRow(wheel);
    const count=wheel.children.length;
    if(!row||!count)return 0;
    return Math.min(count-1,Math.max(0,Math.round(wheel.scrollTop/row)));
  }
  function alarmWheelMark(wheel,index){
    wheel.dataset.index=String(index);
    Array.from(wheel.children).forEach((option,position)=>{
      const distance=Math.abs(position-index);
      option.dataset.near=distance>2?"far":String(distance);
      option.setAttribute("aria-selected",distance===0?"true":"false");
    });
  }
  function alarmWheelPaint(wheel){
    const index=alarmWheelIndex(wheel);
    if(wheel.dataset.index!==String(index))alarmWheelMark(wheel,index);
  }
  // Vurgu kaydırmayı beklemez: `scroll` olayı gecikmeli gelir, işaret hemen konur.
  function alarmWheelScrollTo(wheel,index,smooth){
    const row=alarmWheelRow(wheel);
    if(!row)return;
    alarmWheelMark(wheel,index);
    wheel.scrollTo({top:index*row,behavior:smooth?"smooth":"auto"});
  }
  function alarmWheelKey(wheel,key){
    const count=wheel.children.length;
    if(!count)return false;
    const steps={ArrowDown:1,ArrowUp:-1,PageDown:5,PageUp:-5};
    const index=alarmWheelIndex(wheel);
    const next=key==="Home"?0:key==="End"?count-1:key in steps?index+steps[key]:null;
    if(next===null)return false;
    alarmWheelScrollTo(wheel,Math.min(count-1,Math.max(0,next)),true);
    return true;
  }
  /* Tekerlekler yalnız pencere açıkken doğru ölçülür: kapalı `dialog` sıfır yüksekliktedir,
     satır yüksekliği okunamaz. O yüzden doldurma ve ilk konumlama `showModal`dan SONRA. */
  function openAlarmTimePicker(){
    const dialog=$("#alarmTimeDialog");
    if(!dialog||dialog.open)return;
    const parts=alarmTimeParts();
    dialog.showModal();
    alarmWheels().forEach(([wheel,values])=>{
      alarmWheelFill(wheel,values);
      const wanted=wheel.id==="alarmHourWheel"?parts.hour:parts.minute;
      alarmWheelScrollTo(wheel,Math.max(0,values.indexOf(wanted)),false);
    });
  }
  // Vazgeçme ayrı bir yol istemez: değer yalnız burada yazılır, pencere kapanınca eskisi durur.
  function applyAlarmTimePicker(){
    const [[hourWheel,hours],[minuteWheel,minutes]]=alarmWheels();
    const parts=alarmTimeParts();
    const hour=hours[alarmWheelIndex(hourWheel)]??parts.hour;
    const minute=minutes[alarmWheelIndex(minuteWheel)]??parts.minute;
    alarmSetting.time=`${alarmPad2(hour)}:${alarmPad2(minute)}`;
    $("#alarmTimeDialog").close();
    saveAlarmSetting();
  }
  function renderAlarmSettings(){
    const enabled=$("#alarmEnabled");
    if(!enabled)return;
    const weekday=$("#alarmWeekday");
    enabled.checked=alarmSetting.enabled;
    $("#alarmTimeValue").textContent=alarmSetting.time;
    $("#alarmRepeat").value=alarmSetting.repeat;
    weekday.innerHTML=alarmWeekdayOptions();
    weekday.value=String(alarmSetting.weekday);
    // Alanlar alarm kapalıyken de açık kalır: kullanıcı önce saati kurup sonra açabilsin.
    $("#alarmDayField").hidden=alarmSetting.repeat!=="weekly";
    const next=nextAlarmDate();
    $("#alarmStatus").textContent=next?t("alarmNext",{when:alarmWhenText(next)}):t("alarmOff");
  }
  /* Kaydetme akışı pencerenin geri kalanıyla aynı: ayrı "Kaydet" düğmesi yok, değişiklik anında
     yazılır ve durum satırı sıradaki çalmayı söyler. Kurulum anında geçmişe düşen bir alarm
     hemen ötmesin diye, bugünün çalma anı geçtiyse gün "çalınmış" işaretlenir. */
  function saveAlarmSetting(){
    alarmSetting.enabled=$("#alarmEnabled").checked===true;
    // Saat, seçicinin kendi durumundan gelir (ayrı bir alan yok); yine de biçim doğrulanır.
    alarmSetting.time=normalizeAlarmTime(alarmSetting.time);
    alarmSetting.repeat=$("#alarmRepeat").value==="weekly"?"weekly":"daily";
    const weekday=Number($("#alarmWeekday").value);
    alarmSetting.weekday=Number.isInteger(weekday)&&weekday>=0&&weekday<=6?weekday:0;
    const now=new Date();
    const today=alarmAt(now);
    alarmSetting.lastFired=alarmRingsOn(today)&&now.getTime()>=today.getTime()?alarmDayKey(today):"";
    persistAlarmSetting();
    renderAlarmSettings();
  }
  /* Denetim her saniyelik tikte YENİDEN hesaplanır. Uyuyan tablet, düzeltilen sistem saati ya da
     yaz saati geçişi zinciri kırmaz: kurulu bir zamanlayıcı yok, yalnız "şu an alarm anına ne
     kadar yakın" sorusu var. Pencere iki dakikadır; daha geç uyanan cihaz sessiz kalır. */
  function checkAlarmDue(now=new Date()){
    if(!alarmSetting.enabled||alarmRingingSince!==null)return;
    const target=alarmAt(now);
    if(!alarmRingsOn(target))return;
    const delta=now.getTime()-target.getTime();
    if(delta<0||delta>alarmGraceMs)return;
    const key=alarmDayKey(target);
    if(alarmSetting.lastFired===key)return;
    alarmSetting.lastFired=key;
    persistAlarmSetting();
    startAlarmRing();
  }
  let alarmRingingSince=null;
  let alarmAutoStopTimer=null;
  let alarmRingClockTimer=null;
  function renderAlarmRing(){
    const time=$("#alarmRingTime");
    if(!time)return;
    const now=new Date();
    time.textContent=zoneTime(now);
    $("#alarmRingDate").textContent=dateTimeFormatter({weekday:"long",day:"numeric",month:"long"}).format(now);
  }
  function startAlarmRing(){
    if(alarmRingingSince!==null)return;
    alarmRingingSince=Date.now();
    renderAlarmRing();
    const dialog=$("#alarmDialog");
    if(dialog&&!dialog.open)dialog.showModal();
    // Bu pencerede odak başlığa değil "Durdur"a gider: metin alanı yok, klavye açılmaz ve
    // uyanan kullanıcı tek dokunuşla susturur.
    $("#stopAlarm")?.focus();
    startAlarmSound();
    alarmAutoStopTimer=setTimeout(stopAlarmRing,alarmAutoStopMs);
    alarmRingClockTimer=setInterval(renderAlarmRing,1000);
  }
  function stopAlarmRing(){
    if(alarmAutoStopTimer!==null){clearTimeout(alarmAutoStopTimer);alarmAutoStopTimer=null}
    if(alarmRingClockTimer!==null){clearInterval(alarmRingClockTimer);alarmRingClockTimer=null}
    stopAlarmSound();
    alarmRingingSince=null;
    const dialog=$("#alarmDialog");
    if(dialog?.open)dialog.close();
    renderAlarmSettings();
  }
  /* SES — harici dosya YOK, ton Web Audio ile üretilir: paket şişmez, çevrimdışı çalışır.
     Tarayıcı otomatik oynatma kısıtı: `AudioContext` ancak bir kullanıcı etkileşiminden sonra
     ses verir. Bu yüzden bağlam ilk dokunuşta kurulup canlı tutulur (`unlockAlarmAudio`,
     99-bind.js). Alarm anında bağlam hiç yoksa ya da askıda kalırsa HATA VERİLMEZ; uyarı
     sessizce yalnız görsele düşer. */
  let alarmAudioContext=null;
  let alarmGain=null;
  let alarmLimiter=null;
  let alarmChimeTimer=null;
  let alarmSoundStartedAt=0;
  function ensureAlarmAudio(){
    if(alarmAudioContext)return alarmAudioContext;
    const Factory=window.AudioContext||window.webkitAudioContext;
    if(!Factory)return null;
    try{alarmAudioContext=new Factory()}
    catch(error){console.warn("alarm-audio",error);return null}
    return alarmAudioContext;
  }
  function unlockAlarmAudio(){
    const context=ensureAlarmAudio();
    if(context&&context.state==="suspended")context.resume().catch(()=>{});
  }
  /* Tek vuruş: iki nota, her nota bir zarf ve zarfın altında üç ses. Sinüs temeli hep var;
     kare ve oktav üstü bileşenler `intensity` (0→1) ile açılır — çan yavaş yavaş cırtlak bir
     alarma döner. Şiddet arttıkça atak sertleşir, sönüm kısalır, notalar birbirine yaklaşır.
     Her vuruş kendi düğümlerini kurar ve kendi kendine biter; susturma ana kazancı koparır,
     kuyrukta bekleyen ses kalmaz. */
  function alarmChime(at,intensity){
    const context=alarmAudioContext;
    if(!context||!alarmGain)return;
    const blend=(from,to)=>from+(to-from)*intensity;
    const level=blend(alarmLevelStart,alarmLevelPeak);
    const decay=blend(alarmDecayStart,alarmDecayPeak);
    const noteGap=blend(alarmNoteGapStart,alarmNoteGapPeak);
    const attack=blend(0.05,0.012);
    alarmChimeNotes.forEach((frequency,index)=>{
      const start=at+index*noteGap;
      const envelope=context.createGain();
      envelope.gain.setValueAtTime(0.0001,start);
      envelope.gain.exponentialRampToValueAtTime(level,start+attack);
      envelope.gain.exponentialRampToValueAtTime(0.0001,start+decay);
      envelope.connect(alarmGain);
      [["sine",frequency,1],["square",frequency,alarmHarshMix*intensity],["square",frequency*2,alarmPierceMix*intensity*intensity]].forEach(([type,voiceFrequency,mix])=>{
        if(mix<=0.002)return;
        const oscillator=context.createOscillator();
        const voice=context.createGain();
        oscillator.type=type;
        oscillator.frequency.setValueAtTime(voiceFrequency,start);
        voice.gain.value=mix;
        oscillator.connect(voice).connect(envelope);
        oscillator.start(start);
        oscillator.stop(start+decay+0.05);
      });
    });
  }
  // Vuruşlar sabit bir `setInterval` yerine kendini yeniden kuran zamanlayıcıyla sürer: aralık
  // da şiddetle birlikte kısalır. Rampa dolduktan sonra değerler sabitlenir, alarm o seviyede
  // otomatik susana (`alarmAutoStopMs`) ya da kullanıcı durdurana kadar devam eder.
  function alarmIntensity(){
    const ramp=Math.min(1,Math.max(0,(Date.now()-alarmSoundStartedAt)/alarmRampMs));
    return Math.pow(ramp,alarmRampCurve);
  }
  function scheduleAlarmChime(){
    if(!alarmGain||!alarmAudioContext)return;
    const intensity=alarmIntensity();
    alarmChime(alarmAudioContext.currentTime+0.02,intensity);
    const gap=alarmChimeGapStartMs+(alarmChimeGapPeakMs-alarmChimeGapStartMs)*intensity;
    alarmChimeTimer=setTimeout(scheduleAlarmChime,gap);
  }
  function startAlarmSound(){
    try{
      const context=ensureAlarmAudio();
      if(!context)return false;
      if(context.state==="suspended")context.resume().catch(()=>{});
      alarmGain=context.createGain();
      alarmGain.gain.value=1;
      // Zincirin ucundaki sıkıştırıcı emniyet supabı: üst harmonikler üst üste binince çıkış
      // kırpılıp bozulmasın, tepe seviye hoparlörü zorlamasın.
      alarmLimiter=context.createDynamicsCompressor();
      alarmLimiter.threshold.value=-10;
      alarmLimiter.knee.value=6;
      alarmLimiter.ratio.value=12;
      alarmLimiter.attack.value=0.003;
      alarmLimiter.release.value=0.15;
      alarmGain.connect(alarmLimiter).connect(context.destination);
      alarmSoundStartedAt=Date.now();
      scheduleAlarmChime();
      return true;
    }catch(error){
      console.warn("alarm-audio",error);
      return false;
    }
  }
  function stopAlarmSound(){
    if(alarmChimeTimer!==null){clearTimeout(alarmChimeTimer);alarmChimeTimer=null}
    if(alarmLimiter){try{alarmLimiter.disconnect()}catch{}alarmLimiter=null}
    if(!alarmGain)return;
    try{alarmGain.disconnect()}catch{}
    alarmGain=null;
  }
