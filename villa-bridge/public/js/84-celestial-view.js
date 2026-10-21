  /* SOLAR PALET — PAKET RENGİ VERİR, MOTOR GEOMETRİYİ VE ZAMANLAMAYI SÜRER.
     BU DOSYA ARTIK NE ZAMANLAYICI TUTAR NE DE GÖKYÜZÜ ÇİZER. Eskiden burada ikinci bir motor
     vardı: `/api/celestial` dakikada bir yoklanıyor, gerçek güneş yüksekliğinden kendi faz
     ağırlıkları hesaplanıyor ve `--sky` KOMPLE eziliyordu (kendi altı katmanıyla). Oysa
     `panel.css` `--sky`'ı 10–12 katman olarak kuruyor ve `background-size/-repeat/-blend-mode`
     listeleri katman sayısına elle hizalı: Samanyolu düşüyor, gradyan yanlış slota oturuyordu.
     Ayrıca dakikada bir `refreshCelestialTheme` ile `applySunGround` iki ayrı motor olarak
     dönüyordu ve `--solar-*`/`--lunar-*` yazımlarının hiç tüketicisi yoktu.
     TEK MOTOR KARARI: gökyüzünün ağırlıkları `skyPhases`/`applySunGround` (90-shell) tarafından
     hesaplanır — saat kaydırıcısı, oynat/duraklat ve ay evresi önizlemesi ZATEN oradan geçiyor
     (`skyMinutes`), yani önizleme yolları paleti de kendiliğinden sürer. Astronomi servisi bir
     istekle bir dakikayı anlatabilir; kaydırıcı parmakla saniyede onlarca dakika geziyor, o
     yüzden veri kaynağı olarak da uygun değildi ve bağlantısı kaldırıldı (`/api/celestial`
     rotası duruyor, panelin paleti artık ona bakmıyor).
     BURADA KALAN TEK İŞ: verilen dört ağırlıkla paketin dört çapasını karıştırıp hazır rengi
     köke yazmak. `color-mix()` yok — karışım JS'te. */
  const solarThemeState={weights:{night:0,dawn:0,day:1,dusk:0}};
  const solarAnchorPalette=(theme,name)=>mergeThemePalette(
    theme.palettes.solar.anchors[name],
    themeRuntime.appearance?.overrides?.[theme.id]?.solar?.[name]||{}
  );
  /* Ağırlık > 0 olan çapalar [palet, ağırlık] çiftlerine döner. Toplam 1'dir (gece payı kalan),
     yani karışım normalize edilmiş olur. */
  const solarMixEntries=(theme,weights)=>Object.entries(weights)
    .filter(([,weight])=>weight>0)
    .map(([name,weight])=>[solarAnchorPalette(theme,name),weight]);
  /* OMUZ YOK — ÖLÇÜLDÜ VE VAZGEÇİLDİ. `applyPlateInk`teki teknik (ekseni ortadan gerip
     smootherstep'e sokmak) burada da denendi: 3:1'in altındaki alacakaranlık bandını günde
     79 dk'dan ~40 dk'ya indiriyor ama dakikalık renk adımını 6/255'ten 41–80/255'e çıkarıyor,
     yani "eşikte sıçrama olmasın" isteğinin TAM TERSİNİ yapıyor. Bandı asıl daraltan şey de o
     değil: paletin mürekkebi her iki alacakaranlıkta KUTUP DEĞİŞTİRİYOR (gece açık → şafak koyu,
     gündüz koyu → batım açık), yani sürekli bir karışım orta griden geçmek ZORUNDA. O an için
     sigorta harfin konturudur (`--sky-plate-shadow`, mürekkebin karşı tarafında, CSS bloğunda
     hem karta hem döşemeye bağlı), palet değil. Karışım bu yüzden HAM ağırlıklarla yapılır. */
  function resolvedSolarPalette(theme,weights){
    const entries=solarMixEntries(theme,weights);
    const colors={};
    Object.keys(themeTokenCss).forEach(token=>{colors[token]=mixedThemeColor(entries,token)});
    const materials={blur:0,saturation:0,brightness:0,shadow:"soft"};
    entries.forEach(([palette,weight])=>{
      materials.blur+=palette.materials.blur*weight;
      materials.saturation+=palette.materials.saturation*weight;
      materials.brightness+=palette.materials.brightness*weight;
      if(weight>.5)materials.shadow=palette.materials.shadow;
    });
    return{colors,materials};
  }
  /* GÜNEŞ DİSKİ TEK RENKTEN ÜÇ DURAK. Paket güneş için tek renk taşır (`sun`), disk ise çekirdek
     → orta → kenar diye üç duraklıdır (o yapı kalsın istendi). Çekirdek aynı rengin DOYMUŞ
     ucudur (mavi payı kısılır), kenar beyaza doğru açılmışıdır — ikisi de paket renginden türer,
     yani palet değişince disk de değişir. */
  const solarSunShade=rgba=>themeRgba([rgba[0],rgba[1]*.8,rgba[2]*.35,1],1);
  const solarSunRim=rgba=>themeRgba([1,rgba[1]+(1-rgba[1])*.45,rgba[2]+(1-rgba[2])*.25,1],.85);
  function applySolarThemePaint(){
    const theme=typeof activeThemePackage==="function"?activeThemePackage():null;
    if(!theme?.palettes?.solar?.anchors)return;
    const weights=solarThemeState.weights;
    const palette=resolvedSolarPalette(theme,weights);
    const dominant=Object.entries(weights).sort((left,right)=>right[1]-left[1])[0]?.[0]||"night";
    // `data-theme` (light/dark) BURADAN YAZILMAZ: eşiği `resolveThemeMode`/`syncSunTheme`
    // yönetiyor. İki yazıcı olsaydı eşik karesinde biri öbürünü ezerdi.
    applyResolvedThemePalette(palette,{tone:dominant});
    const root=document.documentElement;
    // Yıldız, güneş ve ay: renk paketten, opaklık/geometri motordan (`--star-a`, `--sun-disc`,
    // `--moon-disc`, kol açıları). Ay DOKUSU korunur — `moon` yalnız halenin tonudur.
    root.style.setProperty("--theme-stars-rgb",themeRgbTriple(palette.colors.stars));
    const sun=parseThemeColor(palette.colors.sun);
    root.style.setProperty("--theme-sun-rgb",themeRgbTriple(palette.colors.sun));
    root.style.setProperty("--theme-sun-core",solarSunShade(sun));
    root.style.setProperty("--theme-sun-rim",solarSunRim(sun));
    root.style.setProperty("--theme-moon-rgb",themeRgbTriple(palette.colors.moon));
    /* DAĞIN GÜNDÜZ KATMANI — hava perspektifi korunsun diye iki opak katman yerinde kalır:
       taban gece çapasından (CSS'te sabit), üstteki katman gündüz tarafının karışımıdır ve
       alfası `--sky-lum`. Karışım GECE PAYI DIŞARIDA bırakılarak normalize edilir; yoksa gece
       rengi hem tabanda hem üstte iki kez sayılırdı. Gündüz payı sıfıra inince katman zaten
       görünmez (alfa 0), o yüzden bölünme koruması gündüz çapasına düşer. */
    const daylight=weights.dawn+weights.day+weights.dusk;
    const mountainEntries=daylight>.0005
      ?[["dawn",weights.dawn],["day",weights.day],["dusk",weights.dusk]]
        .filter(([,weight])=>weight>0).map(([name,weight])=>[solarAnchorPalette(theme,name),weight/daylight])
      :[[solarAnchorPalette(theme,"day"),1]];
    root.style.setProperty("--theme-mountain-far-day-rgb",themeRgbTriple(mixedThemeColor(mountainEntries,"mountainFar")));
    root.style.setProperty("--theme-mountain-near-day-rgb",themeRgbTriple(mixedThemeColor(mountainEntries,"mountainNear")));
  }
  /* Tek giriş: motor ağırlıkları verir, palet aynı karede boyanır. Ağırlıklar `skyPhases`'in
     çıktısıdır (şafak · gündüz · batım) + gece payı; toplamı 1'dir. */
  function setSolarThemeWeights(weights){
    solarThemeState.weights={
      night:clamp01(weights?.night||0),
      dawn:clamp01(weights?.dawn||0),
      day:clamp01(weights?.day||0),
      dusk:clamp01(weights?.dusk||0),
    };
    applySolarThemePaint();
  }
