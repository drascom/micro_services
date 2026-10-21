  /* RENK MATEMATİĞİ TEK YERDE. `color-mix()` ve `light-dark()` eski Android WebView'de YOK, o
     yüzden her karışım burada JS'te yapılır ve CSS'e HAZIR renk gider. Yardımcılar bu dosyada
     (yükleme sırasında en erken tema dosyası) durur; solar karışım 84'te bunları kullanır. */
  const clamp01=value=>Math.max(0,Math.min(1,value));
  const parseThemeColor=value=>{
    const text=String(value||"").trim();
    if(text.startsWith("#")){
      const body=text.slice(1);const expanded=body.length===6?`${body}ff`:body;
      if(expanded.length===8)return[0,2,4,6].map(index=>parseInt(expanded.slice(index,index+2),16)/255);
    }
    const match=text.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/i);
    return match?[Number(match[1])/255,Number(match[2])/255,Number(match[3])/255,match[4]===undefined?1:Number(match[4])]:[0,0,0,1];
  };
  const themeRgba=(rgba,alpha)=>`rgba(${Math.round(clamp01(rgba[0])*255)},${Math.round(clamp01(rgba[1])*255)},${Math.round(clamp01(rgba[2])*255)},${clamp01(alpha===undefined?rgba[3]:alpha).toFixed(3)})`;
  /* ÜÇLÜ (`r,g,b`) — alfası CSS'ten gelecek renkler için. Gökyüzü aşamalarının opaklığı
     `--sky-a-*` değişkenlerinde, dağın gündüz katmanınınki `--sky-lum`'da duruyor; bir arka plan
     KATMANINA ayrıca opaklık verilemediği için renk `rgba(var(--üçlü),var(--alfa))` biçiminde
     yazılmak zorunda. Bu yüzden paket renkleri hem tam renk hem üçlü olarak yayımlanır. */
  const themeRgbTriple=color=>{
    const rgba=parseThemeColor(color);
    return`${Math.round(clamp01(rgba[0])*255)},${Math.round(clamp01(rgba[1])*255)},${Math.round(clamp01(rgba[2])*255)}`;
  };
  const mixedThemeColor=(entries,token)=>{
    const rgba=[0,0,0,0];
    entries.forEach(([palette,weight])=>parseThemeColor(palette.colors[token]).forEach((channel,index)=>{rgba[index]+=channel*weight}));
    return themeRgba(rgba,rgba[3]);
  };
  const themeTokenCss={
    page:"--theme-page",ink:"--theme-ink",inkSoft:"--theme-ink-soft",glassCard:"--theme-glass-card",
    glassTile:"--theme-glass-tile",glassNav:"--theme-glass-nav",glassEdge:"--theme-glass-edge",
    glassSheen:"--theme-glass-sheen",accent:"--theme-accent",accentSoft:"--theme-accent-soft",
    stateOn:"--theme-state-on",stateActive:"--theme-state-active",stateAlert:"--theme-state-alert",
    stateOffline:"--theme-state-offline",skyTop:"--theme-sky-top",skyBottom:"--theme-sky-bottom",
    sun:"--theme-sun",moon:"--theme-moon",stars:"--theme-stars",mountainFar:"--theme-mountain-far",
    mountainNear:"--theme-mountain-near"
  };
  const themeRuntime={packages:[],appearance:null,ready:false,selectedPackageId:null};
  const selectedThemePackageId=()=>{
    try{return localStorage.getItem("villa-theme-package")||themeRuntime.appearance?.defaultPackageId||"villa-liquid-glass"}
    catch{return themeRuntime.appearance?.defaultPackageId||"villa-liquid-glass"}
  };
  const activeThemePackage=()=>{
    const selected=selectedThemePackageId();
    return themeRuntime.packages.find(item=>item.id===selected)
      ||themeRuntime.packages.find(item=>item.id===themeRuntime.appearance?.defaultPackageId)
      ||themeRuntime.packages[0]
      ||null;
  };
  const themeOverride=(packageId,tone)=>{
    const item=themeRuntime.appearance?.overrides?.[packageId]||{};
    return tone==="light"||tone==="dark"?item[tone]||{}:{};
  };
  const mergeThemePalette=(base,override={})=>({
    colors:{...base.colors,...override},
    materials:{...base.materials}
  });
  let themeCacheWrittenAt=0;
  /* GÖLGE DAR VE YUMUŞAK — YARIÇAP KOMŞU BOŞLUĞUNDAN KÜÇÜK. Paket profilleri ilk turda geniş
     yazılmıştı (`0 18px 40px` / `0 10px 28px`); 40px bulanıklık kenardan ~20px dışarı taşar,
     oysa ölçülen en dar gerçek boşluk 8px (dar yatayda `#home .strip-row{gap:8px}`),
     tablette 10px. İki komşunun gölgesi aradaki boşlukta üst üste binip tek koyu bant kuruyordu —
     panelin kendi kümelerinde daha önce "tünel" diye düzeltilen hatanın aynısı, paket yolundan
     geri gelmişti. Yeni ifade iki katmanlı: `0 1px 2px` TEMAS gölgesi düğmeyi zemine bağlar,
     `0 2px 6px` KALDIRMA gölgesi yüzme hissini verir. 6px yanlara 3px taşar; 8px'lik en dar
     boşlukta bile iki komşunun taşması ancak alfası sıfırlanmış uçlarıyla buluşur. */
  const themeShadowValue=profile=>profile==="floating"
    ?"0 1px 2px rgba(0,0,0,.42),0 2px 6px rgba(0,0,0,.26)"
    :profile==="soft"?"0 1px 2px rgba(16,26,38,.17),0 2px 6px rgba(16,26,38,.12)":"none";
  function applyResolvedThemePalette(palette,meta={}){
    if(!palette?.colors)return;
    const root=document.documentElement;
    Object.entries(themeTokenCss).forEach(([token,css])=>{
      if(typeof palette.colors[token]==="string")root.style.setProperty(css,palette.colors[token]);
    });
    /* Kartın çizgi ve iç gölgesi paketin ayrı bir tokenı DEĞİL, mürekkebin seyreltilmişidir —
       böylece mürekkep gün içinde akarken ayraçlar da onunla akar ve paket iki token daha
       taşımak zorunda kalmaz. */
    const ink=parseThemeColor(palette.colors.ink);
    root.style.setProperty("--theme-ink-line",themeRgba(ink,.16));
    root.style.setProperty("--theme-ink-inset",themeRgba(ink,.07));
    /* KAPALI DÖŞEMENİN HALKASI. `stateOffline` paketin tek "sönük" rengi ve bugüne kadar hiçbir
       kural onu okumuyordu — paket bağlandıktan sonra kapalı döşeme, tabanın parlak cam kenarını
       ve panelin ESKİ (beyazımsı) `--glass-off-sub` mürekkebini birlikte kullanıyordu; gündüz
       açık pakette bu 1,02:1 demekti, yani ikon ve alt satır yok olurdu. Halka artık bundan
       türer. Yalnız HALKA: ölçüldüğünde `stateOffline` gliflik doygunluğa sahip değil (gündüz
       3,38:1, şafak 2,95:1), o yüzden ikonun ve alt satırın mürekkebi `inkSoft` kalır. */
    const offline=parseThemeColor(palette.colors.stateOffline||palette.colors.inkSoft);
    root.style.setProperty("--theme-state-offline-line",themeRgba(offline,.55));
    const material=palette.materials||{};
    root.style.setProperty("--theme-glass-blur",`${Number(material.blur)||0}px`);
    root.style.setProperty("--theme-glass-saturation",String(Number(material.saturation)||1));
    root.style.setProperty("--theme-glass-brightness",String(Number(material.brightness)||1));
    root.style.setProperty("--theme-glass-shadow",themeShadowValue(material.shadow));
    root.dataset.themePackage=activeThemePackage()?.id||"";
    if(meta.tone)root.dataset.themeTone=meta.tone;
    if(meta.resolvedTheme==="light"||meta.resolvedTheme==="dark"){
      root.dataset.theme=meta.resolvedTheme;
      root.style.colorScheme=meta.resolvedTheme;
      const themeMeta=document.querySelector('meta[name="theme-color"]');
      if(themeMeta)themeMeta.content=meta.resolvedTheme==="dark"?"#101514":"#edf0f2";
    }
    /* İLK KARE ÖNBELLEĞİ, KISILMIŞ. Bu kayıt yalnız bir sonraki AÇILIŞIN ilk karesi için var
       (`<head>` içindeki betik onu okur), yani birkaç saniye eski olması hiçbir şeyi bozmaz.
       Kısma zorunlu: palet artık gökyüzü motoruyla aynı karede boyanıyor ve akış/kaydırıcı
       önizlemesinde o kare saniyede ~7 kez dönüyor — her karede `JSON.stringify` + disk yazımı
       tablette boşuna yük olurdu. */
    const now=Date.now();
    if(now-themeCacheWrittenAt>2000){
      themeCacheWrittenAt=now;
      try{
        localStorage.setItem("villa-appearance-cache-v2",JSON.stringify({
          schemaVersion:2,packageId:root.dataset.themePackage,tone:meta.tone||"light",
          resolvedTheme:meta.resolvedTheme||root.dataset.theme||"light",
          sky:state.themeMode==="sun"?"live":"fixed",colors:palette.colors,materials:palette.materials
        }));
      }catch{}
    }
  }
  /* DÖRT ÇAPA BİRDEN CSS'E. Paket faz BAŞINA ayrı bir küme veriyor; bizim motorumuz ise dört
     fazı aynı anda ağırlıklandırıp karıştırıyor (`--sky-a-dawn/-day/-dusk` + gece payı,
     `--sky-lum`). Bu yüzden yalnız aktif fazın değil, DÖRDÜNÜN de renkleri köke yazılır: gökyüzü
     gradyanları ile dağın gece tabanı karışımı CSS'te kendi alfa zinciriyle yapar, mürekkep ve
     cam ise JS'te aynı ağırlıklarla karışır (84). Adlandırma tek desendir:
     `--theme-<çapa>-<token>` tam renk, `--theme-<çapa>-<token>-rgb` aynı rengin üçlüsü.
     BU DEĞERLER SABİTTİR — pakete (ve kullanıcının geçersiz kılmalarına) bağlıdır, saate değil.
     O yüzden dakikalık döngüde değil, yalnız paket/kip değişiminde yazılır. */
  const themeAnchorNames=["night","dawn","day","dusk"];
  const themeAnchorTripleTokens=["skyTop","skyBottom","mountainFar","mountainNear","stars","sun","moon"];
  function applyThemeAnchors(theme){
    const anchors=theme?.palettes?.solar?.anchors;
    if(!anchors)return;
    const root=document.documentElement;
    themeAnchorNames.forEach(name=>{
      const palette=mergeThemePalette(anchors[name],themeRuntime.appearance?.overrides?.[theme.id]?.solar?.[name]||{});
      Object.entries(themeTokenCss).forEach(([token,css])=>{
        const value=palette.colors[token];
        if(typeof value!=="string")return;
        const suffix=css.replace("--theme-","");
        root.style.setProperty(`--theme-${name}-${suffix}`,value);
        if(themeAnchorTripleTokens.includes(token))root.style.setProperty(`--theme-${name}-${suffix}-rgb`,themeRgbTriple(value));
      });
      /* Gökyüzü gradyanının ORTA DURAĞI: paket iki uç verir, gradyanın katman ve durak sayısı
         ise değişmemeli (listeler `body::before` içinde elle hizalı). Orta durak iki ucun tam
         ortasıdır, yani iki duraklı bir gradyanla birebir aynı sonucu verir. */
      root.style.setProperty(`--theme-${name}-sky-mid-rgb`,themeRgbTriple(mixedThemeColor(
        [[{colors:{sky:palette.colors.skyTop}},.5],[{colors:{sky:palette.colors.skyBottom}},.5]],"sky")));
    });
  }
  /* ————— SABİT YÜZEYLERİN MÜREKKEBİ — TEK KAYNAK —————
     Panelde birkaç yüzey gökyüzüne UYMAZ. Hızlı kumanda ve alarm saati koyu; canlı kipteki
     menü ise açık kalır. Köprü ise `--glass-ink*`/`--ink`/`--muted`
     ailesini paketin O ANKİ faz mürekkebine bağlıyor — gündüz çapasında bu KOYU lacivert
     (`day.ink` #10263a). Koyu yüzey + koyu mürekkep = ölçülen 1,17:1 (şafak) ve 1,41:1 (gündüz),
     yani yazı yok olur. Aynı hata kapalı döşemede (`1ee9117`) TERS yönde çıkmıştı: orada açık
     yüzeyin üstüne panelin beyazımsı mürekkebi düşüyordu.
     TEK KURAL, İKİ BASAMAK: (1) burada, koyu yüzeye uygun mürekkep kümesi `--on-dark-*` adıyla
     BİR KEZ yayımlanır; (2) `panel.css` içinde koyu kalan yüzeyleri sayan TEK kural o kümeye
     bağlanır. Yeni kural yazmak = o listeye bir seçici eklemek; renk kararı tekrarlanmaz.
     KAYNAK PALET paketin kendisidir, uydurma renk yok: canlı kipte solar `night` çapası, sabit
     kiplerde `palettes.dark`. İkisi de "koyu zemin" için tasarlanmış kümeler.
     `accentInk` paket şemasında YOK; dolu aksan düğmesinin (seçili tema/dil çipi) yazısı o
     paletin KENDİ zemininden (`page`) türer — koyu zemin, açık nane aksanın üstünde 12,76:1.
     Şema değişmedi, yeni token paket dosyasına eklenmedi. */
  const darkSurfaceTokenCss={ink:"--on-dark-ink",inkSoft:"--on-dark-ink-soft",page:"--on-dark-accent-ink",
    accent:"--on-dark-accent",accentSoft:"--on-dark-accent-soft",glassEdge:"--on-dark-edge",
    glassTile:"--on-dark-tile",stateAlert:"--on-dark-danger"};
  function applyDarkSurfaceInk(theme,mode){
    const night=theme?.palettes?.solar?.anchors?.night;
    const source=mode==="sun"&&night
      ?mergeThemePalette(night,themeRuntime.appearance?.overrides?.[theme.id]?.solar?.night||{})
      :mergeThemePalette(theme.palettes.dark,themeOverride(theme.id,"dark"));
    if(!source?.colors)return;
    const root=document.documentElement;
    Object.entries(darkSurfaceTokenCss).forEach(([token,css])=>{
      if(typeof source.colors[token]==="string")root.style.setProperty(css,source.colors[token]);
    });
    // Ayraç ve iç yüzey mürekkebin seyreltilmişidir — köprünün `--theme-ink-line/-inset`
    // türetmesinin birebir aynısı, yalnız koyu paletten.
    const ink=parseThemeColor(source.colors.ink);
    root.style.setProperty("--on-dark-line",themeRgba(ink,.16));
    root.style.setProperty("--on-dark-inset",themeRgba(ink,.07));
    const danger=parseThemeColor(source.colors.stateAlert||source.colors.ink);
    root.style.setProperty("--on-dark-danger-soft",themeRgba(danger,.26));
  }
  /* Canlı gökyüzündeki menü günün her saatinde AÇIK bir levhadır. Bu nedenle mürekkebi o anki
     solar fazdan değil, paketin açık paletinden gelir. Aksi hâlde akşam/gece fazının beyaz
     `ink` değeri açık levhanın üstüne düşer. Kullanıcı renk geçersiz kılmaları burada da
     korunur; sabit renk uydurulmaz. */
  const lightSurfaceTokenCss={ink:"--on-light-ink",inkSoft:"--on-light-ink-soft",page:"--on-light-accent-ink",
    accent:"--on-light-accent",accentSoft:"--on-light-accent-soft",glassEdge:"--on-light-edge",
    glassTile:"--on-light-tile",stateAlert:"--on-light-danger"};
  function applyLightSurfaceInk(theme){
    const source=mergeThemePalette(theme.palettes.light,themeOverride(theme.id,"light"));
    if(!source?.colors)return;
    const root=document.documentElement;
    Object.entries(lightSurfaceTokenCss).forEach(([token,css])=>{
      if(typeof source.colors[token]==="string")root.style.setProperty(css,source.colors[token]);
    });
    const ink=parseThemeColor(source.colors.ink);
    root.style.setProperty("--on-light-line",themeRgba(ink,.16));
    root.style.setProperty("--on-light-inset",themeRgba(ink,.07));
    const danger=parseThemeColor(source.colors.stateAlert||source.colors.ink);
    root.style.setProperty("--on-light-danger-soft",themeRgba(danger,.12));
  }
  function applyThemePackage(mode=state.themeMode){
    const theme=activeThemePackage();
    if(!theme)return;
    applyThemeAnchors(theme);
    /* Çapalar gibi SABİTTİR (pakete ve kipe bağlı, saate değil), o yüzden dakikalık döngüde
       değil yalnız paket/kip değişiminde yazılır. */
    applyDarkSurfaceInk(theme,mode);
    applyLightSurfaceInk(theme);
    if(mode==="sun"){
      if(typeof applySolarThemePaint==="function")applySolarThemePaint();
      return;
    }
    const tone=mode==="dark"||(mode==="system"&&themeMedia?.matches)?"dark":"light";
    applyResolvedThemePalette(
      mergeThemePalette(theme.palettes[tone],themeOverride(theme.id,tone)),
      {tone,resolvedTheme:tone}
    );
  }
  async function initializeThemeRuntime(){
    try{
      const[packagesResponse,appearanceResponse]=await Promise.all([api("/api/theme-packages"),api("/api/appearance")]);
      themeRuntime.packages=Array.isArray(packagesResponse.packages)?packagesResponse.packages:[];
      themeRuntime.appearance=appearanceResponse.appearance||null;
      themeRuntime.ready=true;
      // Gökyüzünü çizen ve zamanlayan tek yer `applySunGround` (90-shell). Burada yalnız paletin
      // sahibi değişir: paket gelir gelmez yürürlükteki ağırlıklarla bir kez boyanır.
      applyThemePackage();
      if(typeof renderAppearanceSettings==="function")renderAppearanceSettings();
    }catch(error){
      console.warn("Tema paketleri yüklenemedi; güvenli CSS varsayılanı kullanılıyor.",error);
    }
  }
  async function selectThemePackage(packageId){
    if(!themeRuntime.packages.some(item=>item.id===packageId))return;
    try{localStorage.setItem("villa-theme-package",packageId)}catch{}
    themeRuntime.selectedPackageId=packageId;
    applyThemePackage();
  }
  async function saveThemeOverrides(overrides){
    if(!themeRuntime.appearance)return;
    const appearance={...themeRuntime.appearance,overrides:{...themeRuntime.appearance.overrides,...overrides}};
    const response=await api("/api/appearance",{method:"PUT",body:JSON.stringify(appearance)});
    themeRuntime.appearance=response.appearance;
    applyThemePackage();
  }
