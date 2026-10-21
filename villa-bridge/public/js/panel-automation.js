  const automationWeekDays=[1,2,3,4,5,6,7];
  const automationEveryDay=days=>automationWeekDays.every(day=>days.includes(day));
  const automationDayLabel=day=>t(`automationDay${day}`);
  const automationDayList=days=>[...days].sort((left,right)=>left-right).map(automationDayLabel).join(", ");
  // Otomasyon yeteneği cihaz/model listesinden değil, expose edilen kumandanın veri biçiminden
  // çıkar. Salt okunur durumlar cihaz ekranında kumanda gibi çizilebilse de hedef olamaz.
  const automationForbiddenControlKinds=new Set(["lock","siren"]);
  const automationNumericControlKinds=new Set(["level","temperature","position","climate","number"]);
  const automationEnumControlKinds=new Set(["select","cover"]);
  const automationControlValues=control=>Array.isArray(control?.values)
    ?control.values.filter(value=>["string","number","boolean"].includes(typeof value))
    :[];
  const isAutomationBinaryControl=control=>control?.kind==="switch"||control?.kind==="fan";
  const isAutomationNumericControl=control=>automationNumericControlKinds.has(control?.kind);
  const isAutomationEnumControl=control=>automationEnumControlKinds.has(control?.kind)
    ||(!isAutomationBinaryControl(control)&&automationControlValues(control).length>0);
  const isAutomationColorControl=control=>control?.kind==="color";
  const isAutomationValueControl=control=>(isAutomationNumericControl(control)
    ||isAutomationEnumControl(control)||isAutomationColorControl(control))
    &&control?.writable!==false&&control?.adminOnly!==true;
  // §8.1 — kilit ve siren bir otomasyon eylemi olamaz, listede hiç görünmez. Teknik/ayar
  // kumandaları da günlük otomasyon listesini kalabalıklaştırmaz.
  const isAutomationControl=control=>Boolean(control)
    &&control.writable!==false
    &&control.adminOnly!==true
    &&!automationForbiddenControlKinds.has(control.kind)
    &&(isAutomationBinaryControl(control)||isAutomationValueControl(control));
  const automationControls=device=>isProtectedDevice(device)?[]:(device?.controls||[]).filter(isAutomationControl);
  // §8.2 — döngü korumasının kanonik anahtarı: IEEE adresi + kanal (MQTT özelliği); dost isim değil.
  const automationChannelKey=(deviceId,property)=>`${String(deviceId||"").toLowerCase()}|${String(property||"")}`;
  const automationControlValue=(control,on)=>on?(control.valueOn??"ON"):(control.valueOff??"OFF");
  // Tek basışta hem açan hem kapatan seçenek yalnızca cihaz bunu bildiriyorsa sunulur.
  const automationCanToggle=control=>control?.valueToggle!==undefined&&control?.valueToggle!==null;
  /* ————— değer eylemleri. Parlaklık/renk özel durum değildir; sayı, konum, iklim hedefi,
     seçim ve perde gibi yazılabilir her skaler kumanda aynı akıştan geçer. */
  const automationValueChannel=control=>String(control?.id||"").split(":")[0];
  // Bir aç/kapa kanalının değer kumandaları: `main` kanalının `main:brightness` gibi kardeşleri.
  const automationValueControls=(device,channel)=>isProtectedDevice(device)?[]:(device?.controls||[])
    .filter(control=>isAutomationValueControl(control)&&automationValueChannel(control)===channel);
  const automationValueControl=(deviceId,controlId)=>{
    const device=state.devices.find(item=>item.id===deviceId)||null;
    if(!device||isProtectedDevice(device))return null;
    return(device.controls||[]).find(control=>control.id===controlId&&isAutomationValueControl(control))||null;
  };
  /* Kullanıcı YÜZDE seçer, kurala kumandanın KENDİ birimi yazılır. Aralık kumandanın `min`/`max`
     alanından okunur — cihazların ölçekleri farklıdır (biri 1–254, öbürü 0–100), sabit varsayım yok.
     Ham değer `step` verilmişse ona yuvarlanır, verilmemişse tam sayıya; sonra aralığa kırpılır.
     Sunucudaki ortak normalizasyon da aynı kırpmayı yapar, panel oraya geçersiz değer yollamaz. */
  const automationValueRange=control=>{
    const min=Number.isFinite(control?.min)?Number(control.min):0;
    const max=Number.isFinite(control?.max)?Number(control.max):100;
    return max>min?{min,max}:{min,max:min+1};
  };
  const automationValuePercent=(control,raw)=>{
    const{min,max}=automationValueRange(control);
    const value=Math.min(max,Math.max(min,Number(raw)||min));
    return Math.round((value-min)/(max-min)*100);
  };
  const automationValueRaw=(control,percent)=>{
    const{min,max}=automationValueRange(control);
    const clamped=Math.min(100,Math.max(0,Math.round(Number(percent)||0)));
    const exact=min+(max-min)*clamped/100;
    const step=Number(control?.step);
    const snapped=step>0?min+Math.round((exact-min)/step)*step:Math.round(exact);
    return Math.min(max,Math.max(min,Number(snapped.toFixed(6))));
  };
  // Sayaç bir dokunuşta onda bir aralık ilerler: uçtan uca on dokunuş, tablette rahat.
  const automationValuePercentStep=10;
  // Işık sıcaklığında yüzde tek başına bir şey söylemez: mired büyüdükçe ışık sıcaklaşır.
  const automationWarmthKey=percent=>percent<=33?"automationWarmthCool"
    :percent<=66?"automationWarmthNeutral":"automationWarmthWarm";
  const automationValueText=(control,value)=>{
    if(control?.kind==="color")return String(value||"").toLowerCase();
    if(control?.kind==="level")return t("automationValuePercent",{percent:automationValuePercent(control,value)});
    if(control?.kind==="temperature"){
      const percent=automationValuePercent(control,value);
      return t("automationValueWarmthText",{percent,warmth:t(automationWarmthKey(percent))});
    }
    if(isAutomationNumericControl(control)){
      const number=Number(value);
      const shown=Number.isFinite(number)?Math.round(number*1000)/1000:value;
      return`${shown}${control?.unit?` ${control.unit}`:""}`;
    }
    return automationScalarText(value);
  };
  /* ————— "tetikleyeni izle": sabit değer yerine tetikleyenin canlı değeri. Sayısal kanalda oran
     (yüzde) eşlenir, renkte değer olduğu gibi kopyalanır — renkte yüzde bir şey söylemez.
     Kip kumandanın türünden çıkar; kullanıcıya "oran mı kopya mı" diye sorulmaz. */
  const automationFollowEligibleControl=control=>isAutomationColorControl(control)||isAutomationNumericControl(control);
  const automationFollowMode=control=>control?.kind==="color"?"copy":"ratio";
  const automationFollowActionMode=follow=>follow?.mode==="copy"?"followColor":"followRatio";
  const automationFollowChoiceKeys={ratio:"automationFollowRatio",copy:"automationFollowColor"};
  /* İzleme yalnız tetikleyici canlı değer bildiriyorsa anlamlıdır: "değeri her değiştiğinde"
     tetikleyen bir kanal. Eşikte kural yalnız eşiği geçerken, `equals`te yalnız tek değere
     geçişte ateşler; ikisinde de izlenen değer tek bir sayıda donar ve izleme sabit değerden
     farksız olurdu — o yüzden seçenek hiç görünmez. Sunucu da aynı kuralı uyguluyor. */
  const automationFollowSource=wizard=>{
    if(!wizard||wizard.triggerKind!=="sensor"||wizard.triggerNumeric)return null;
    if(wizard.triggerEquals!==null&&wizard.triggerEquals!==undefined)return null;
    if(!wizard.triggerProperty)return null;
    const device=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
    const control=automationChangeControl(device,wizard.triggerProperty);
    return automationFollowEligibleControl(control)?control:null;
  };
  // Aynı tür kanala izin verilir: parlaklık parlaklığı, ışık sıcaklığı ışık sıcaklığını, renk rengi
  // izler. "Aynı oranda" ancak aynı cinsten iki şey arasında okunur.
  const automationFollowAvailable=(wizard,control)=>{
    const source=automationFollowSource(wizard);
    if(!source||!control||!automationFollowEligibleControl(control))return false;
    if(isAutomationColorControl(source)||isAutomationColorControl(control))return source.kind===control.kind;
    // Sayısal değerlerde aynı anlam/birim tercih edilir; aynı tür kumanda farklı ham aralıklara
    // sahipse motor oranı dönüştürür (örn. 0–254 parlaklıktan 0–100 parlaklığa).
    return source.kind===control.kind&&(source.unit||"")===(control.unit||"");
  };
  const automationValueDefaultColor="#ffcf8e";
  const automationValueSeed=control=>{
    if(control?.kind==="color"){
      return/^#[0-9a-fA-F]{6}$/.test(String(control.value||""))?String(control.value).toLowerCase():automationValueDefaultColor;
    }
    if(isAutomationEnumControl(control))return automationControlValues(control)
      .find(value=>String(value)===String(control.value))??automationControlValues(control)[0]??null;
    return Number.isFinite(control?.value)
      ?Number(control.value)
      :automationValueRaw(control,control?.kind==="level"?100:50);
  };
  // Sınıflandırma sunucudan gelir: `category` standart `definition.exposes[].type` tahminidir,
  // kullanıcı rol seçtiyse onunla ezilmiştir. İstemcide model listesi ya da satıcıya özgü
  // özellik adına bakan hiçbir kural yok — öyle bir kural yalnız tek bir evde çalışır.
  // Sekmeler cihaz sınıfından türer, eleme yapmaz. Bir cihaz birden çok sekmede görünebilir:
  // kanal seviyesi rol de sayılır, yani bir kanalı lamba olan çok kanallı anahtar hem "Lambalar"
  // hem "Anahtarlar" sekmesinde durur. Basış yayan cihaz "Kumandalar"a, sınıfı ve basışı olmayıp
  // yalnız sensör özelliği bildiren cihaz "Sensörler"e düşer; hiçbiri yoksa "Diğer".
  const automationTabOrder=["light","switch","cover","climate","lock","fan","button","sensor","other"];
  const automationTabLabels={
    light:"automationTabLight",switch:"automationTabSwitch",cover:"automationTabCover",
    climate:"automationTabClimate",lock:"automationTabLock",fan:"automationTabFan",
    button:"automationTabButton",sensor:"automationTabSensor",other:"automationTabOther"
  };
  const automationDeviceTabs=device=>{
    const tabs=new Set();
    const add=value=>{if(automationTabOrder.includes(value))tabs.add(value)};
    add(device?.category);
    for(const control of device?.controls||[])if(control.kind==="switch")add(control.category);
    const classified=tabs.size>0;
    if((device?.buttons||[]).length||(device?.actionTypes||[]).length)tabs.add("button");
    if(!classified&&!tabs.size&&automationCapabilities(device).length)tabs.add("sensor");
    if(!tabs.size)tabs.add("other");
    return[...tabs];
  };
  const automationTabMatches=(device,tab)=>!tab||tab==="all"||automationDeviceTabs(device).includes(tab);
  // Oda süzgeci kalktı; odaya erişim aramadan geliyor. Kanal ve düğme adları da aranabilir.
  const automationDeviceRooms=device=>(state.groups||[])
    .filter(group=>(group.items||[]).some(item=>item.deviceId===device?.id))
    .map(group=>group.name);
  const automationSearchMatches=(device,query)=>{
    const needle=String(query||"").trim().toLocaleLowerCase(state.language);
    if(!needle)return true;
    const parts=[
      device?.name,
      deviceKind(device),
      ...(device?.controls||[]).filter(control=>control.kind==="switch").map(control=>control.name),
      ...automationCapabilities(device).map(capability=>capability.name),
      ...(device?.buttons||[]).map(button=>deviceButtonName(button)),
      ...automationDeviceRooms(device)
    ];
    return parts.some(part=>String(part||"").toLocaleLowerCase(state.language).includes(needle));
  };
  const automationDevice=action=>state.devices.find(device=>device.id===action?.deviceId)||null;
  // Kanonik hedef property; controlId ve ad yalnızca sunum verisi (§5.1.1).
  const automationControl=action=>automationDevice(action)?.controls.find(control=>control.property===action?.property)||null;
  const automationActionMode=action=>{
    const control=automationControl(action);
    // "İzle" kipinde eylemin kendi değeri yoktur; kip kaydın kendisinden okunur.
    if(action?.follow)return automationFollowActionMode(action.follow);
    // Değer eylemlerinde "yön" yoktur: kip kumandanın türüdür, değeri cümle ayrıca söyler.
    if(control&&["level","temperature","color"].includes(control.kind))return control.kind;
    if(control&&isAutomationValueControl(control))return"value";
    if(control&&automationCanToggle(control)&&action?.value===control.valueToggle)return"toggle";
    if(control)return action?.value===automationControlValue(control,true)?"on":"off";
    if(String(action?.value).toUpperCase()==="TOGGLE")return"toggle";
    return action?.value===true||String(action?.value).toUpperCase()==="ON"?"on":"off";
  };
  const automationActionName=action=>{
    const device=automationDevice(action);
    if(!device)return t("automationMissingDevice");
    const control=automationControl(action);
    return control&&automationControls(device).length>1&&String(control.name)!==String(device.name)
      ?`${device.name} · ${control.name}`:device.name;
  };
  // Ham `1_single` gibi teknik dizeler kullanıcıya basılmaz; sayı + basış eki insan diline çevrilir.
  const automationPressKeys={
    single:"automationPressSingle",double:"automationPressDouble",triple:"automationPressTriple",
    quadruple:"automationPressQuadruple",hold:"automationPressHold",release:"automationPressRelease",
    press:"automationPressPress",on:"automationPressOn",off:"automationPressOff",
    toggle:"automationPressToggle",up:"automationPressUp",down:"automationPressDown",
    quintuple:"automationPressQuintuple",many:"automationPressMany"
  };
  // Sunucu düğmeleri alt varlık olarak veriyor: ad kullanıcının verdiği ad, basış çevrilmiş etiket.
  const automationButtonEntry=(device,action)=>{
    for(const button of device?.buttons||[]){
      const entry=button.actions.find(item=>item.action===action);
      if(entry)return{button,entry};
    }
    return null;
  };
  const automationButtonLabel=(device,action)=>{
    const found=automationButtonEntry(device,action);
    if(!found)return automationActionLabel(action);
    return t("automationButtonEvent",{button:deviceButtonName(found.button),press:deviceButtonPressLabel(found.entry.press)});
  };
  // Sunucu `buttons` vermediğinde (eski sürüm) devreye giren yedek: ham değeri istemcide çözer.
  const rawActionPress=value=>{
    const raw=String(value??"").trim();
    const numbered=/^(?:button_)?(\d{1,2})_([a-z_]+)$/.exec(raw);
    return numbered?numbered[2]:raw;
  };
  const automationActionLabel=value=>{
    const raw=String(value??"").trim();
    const numbered=/^(?:button_)?(\d{1,2})_([a-z_]+)$/.exec(raw);
    const press=automationPressKeys[numbered?numbered[2]:raw];
    // Tanımadığımız kalıpta son çare ham değer — boş liste ya da çökme olmasın.
    if(!press)return raw;
    return numbered?t("automationButtonPress",{number:numbered[1],press:t(press)}):t(press);
  };
  // Kullanıcı dilinde durum seçenekleri. `contact:true` kapalı demektir — açılma false'a bağlanır.
  const automationSensorEvents={
    occupancy:[{value:true,key:"automationEventMotion"},{value:false,key:"automationEventMotionEnds"}],
    presence:[{value:true,key:"automationEventMotion"},{value:false,key:"automationEventMotionEnds"}],
    contact:[{value:false,key:"automationEventOpened"},{value:true,key:"automationEventClosed"}],
    smoke:[{value:true,key:"automationEventSmoke"}],
    carbon_monoxide:[{value:true,key:"automationEventCo"}],
    water_leak:[{value:true,key:"automationEventWater"}],
    battery_low:[{value:true,key:"automationEventBatteryLow"}],
    alarm:[{value:true,key:"automationEventAlarm"}],
    lock_state:[{value:"locked",key:"automationEventLocked"},{value:"unlocked",key:"automationEventUnlocked"}]
  };
  // Motorla aynı dışlama ilkesi: cihazın sunduğu skaler değerler otomasyona girer, yalnız taşıma ve
  // firmware gürültüsü ile ayrı düğme olayı burada gösterilmez. `readings` tanımdan ve gözlenen
  // durumdan üretildiği için yeni cihaz/özellik eklemek bu dosyada bir liste değişikliği istemez.
  const automationNoisyProperties=new Set([
    "action","linkquality","last_seen","elapsed","update","update_available","update_state"
  ]);
  const automationScalar=value=>["string","number","boolean"].includes(typeof value)
    &&(typeof value!=="number"||Number.isFinite(value));
  const automationCapabilityName=(entry,property)=>{
    const own=String(entry?.name||"").trim();
    const base=own||String(property||"").replaceAll("_"," ");
    const parent=String(entry?.parentName||"").trim();
    return parent&&!base.toLocaleLowerCase(state.language).includes(parent.toLocaleLowerCase(state.language))
      ?`${parent} · ${base}`:base;
  };
  function automationCapabilities(device){
    if(!device)return[];
    const found=new Map();
    const hidden=new Set();
    const add=(entry,source)=>{
      const property=String(entry?.property||"").trim();
      if(!property||automationNoisyProperties.has(property)||found.has(property))return;
      const stateValue=device.state?.[property];
      const value=automationScalar(stateValue)?stateValue:automationScalar(entry?.value)?entry.value:null;
      // Tetikleyici listesi yalnız cihazda gerçekten gözlenmiş değerleri gösterir. Henüz hiç değer
      // bildirmemiş yazılabilir ayar hedefte kalır ama "değişince" listesinde gereksiz yer tutmaz.
      if(value===null)return;
      found.set(property,{
        property,name:automationCapabilityName(entry,property),value,
        kind:entry?.kind||entry?.type||typeof value,
        unit:entry?.unit||"",values:automationControlValues(entry),
        valueOn:entry?.valueOn,valueOff:entry?.valueOff,
        control:source==="control"?entry:null,
        writable:source==="control"&&isAutomationControl(entry)
      });
    };
    // Kumandalar önce: kullanıcı ana/alt kanalları ölçümlerden önce görür. Ayar/diagnostic alanları
    // günlük otomasyonda saklanır; cihaz ayar ekranında erişilebilir olmaya devam eder.
    for(const control of device.controls||[]){
      if(control.adminOnly===true){hidden.add(control.property);continue}
      add(control,"control");
    }
    for(const reading of device.readings||[]){
      if(reading.category==="config"||reading.category==="diagnostic"){
        hidden.add(reading.property);
        continue;
      }
      add(reading,"reading");
    }
    // Eski sunucu yanıtı `readings` taşımıyorsa gözlenen skaler durum yine kaybolmaz.
    for(const[property,value]of Object.entries(device.state||{})){
      if(hidden.has(property)||!automationScalar(value))continue;
      add({property,name:property.replaceAll("_"," "),value},"state");
    }
    return[...found.values()];
  }
  const automationCapability=(device,property)=>automationCapabilities(device)
    .find(item=>item.property===property)||null;
  const automationScalarText=value=>{
    if(value===true||String(value).toUpperCase()==="ON")return t("on");
    if(value===false||String(value).toUpperCase()==="OFF")return t("off");
    return String(value??"").replaceAll("_"," ");
  };
  const automationCapabilityBinaryValues=capability=>{
    if(capability?.valueOn!==undefined&&capability?.valueOff!==undefined)return[
      {value:capability.valueOn,label:t("on")},{value:capability.valueOff,label:t("off")}
    ];
    if(typeof capability?.value==="boolean")return[
      {value:true,label:t("on")},{value:false,label:t("off")}
    ];
    return[];
  };
  const automationStateControls=device=>automationControls(device).filter(control=>control.kind==="switch");
  // §8.1 tersi: kilit ve siren EYLEM olamaz ama tetikleyici olarak serbesttir — burada filtre yok.
  function automationTriggerEvents(device,kind,keep){
    if(!device)return[];
    const rows=[];
    if(kind==="button"){
      // Gruplu görünüm sunucudan: her düğme kendi basışlarıyla sırayla listelenir.
      for(const button of device.buttons||[]){
        for(const entry of visiblePresses(button.actions,keep)){
          rows.push({
            token:`action:${entry.action}`,
            action:entry.action,
            label:t("automationButtonEvent",{button:deviceButtonName(button),press:deviceButtonPressLabel(entry.press)})
          });
        }
      }
      // Yedek yol: sunucu düğmeleri türetmediyse ham `actionTypes` listesi kullanılır.
      if(!(device.buttons||[]).length){
        const raw=(device.actionTypes||[]).map(action=>String(action??"").trim())
          .filter(Boolean).map(action=>({action,press:rawActionPress(action)}));
        for(const entry of visiblePresses(raw,keep)){
          rows.push({token:`action:${entry.action}`,action:entry.action,label:automationActionLabel(entry.action)});
        }
      }
    }else if(kind==="sensor"){
      for(const capability of automationCapabilities(device)){
        const semantic=automationSensorEvents[capability.property]||[];
        const used=new Set();
        for(const option of semantic){
          // Bazı expose'lar ikili değeri boolean yerine ON/OFF olarak yayınlar (örn. siren alarmı).
          // Standart olay sözlüğündeki true/false, cihazın kendi uçlarına çevrilir.
          let value=option.value;
          if(typeof value==="boolean"&&capability.valueOn!==undefined&&capability.valueOff!==undefined
            &&typeof capability.valueOn!=="boolean"&&typeof capability.valueOff!=="boolean"){
            value=value?capability.valueOn:capability.valueOff;
          }
          const token=`${capability.property}=${String(value)}`;
          if(used.has(token))continue;
          used.add(token);
          rows.push({token,property:capability.property,equals:value,label:t(option.key)});
        }
        // Normal anahtarın açılma/kapanma yönleri ayrı, daha proaktif eşleme yolunda sunulur.
        // Değer-değişimi yolunda aynı iki satırı yeniden göstermek yerine yalnız "değişti" kalır.
        // Sensör/siren gibi anlamlı özelliklerde ise yönler burada gereklidir.
        const choices=capability.control?.kind==="switch"&&!semantic.length?[]
          :capability.values.length
          ?capability.values.map(value=>({value,label:automationScalarText(value)}))
          :automationCapabilityBinaryValues(capability);
        for(const option of choices){
          const token=`${capability.property}=${String(option.value)}`;
          if(used.has(token))continue;
          used.add(token);
          rows.push({
            token,property:capability.property,equals:option.value,
            label:t("automationValueBecomes",{reading:capability.name,value:option.label})
          });
        }
      }
    }else if(kind==="deviceState"){
      const controls=automationStateControls(device);
      for(const control of controls){
        for(const on of[true,false]){
          const value=automationControlValue(control,on);
          const label=t(on?"automationEventTurnedOn":"automationEventTurnedOff");
          rows.push({
            token:`${control.property}=${String(value)}`,
            property:control.property,
            equals:value,
            label:controls.length>1?t("automationChannelEvent",{channel:control.name,event:label}):label
          });
        }
      }
    }
    // Kanonik olay jetonu benzersizdir. Etikete göre elemek iki farklı alt öğenin aynı metinli
    // durumunu sessizce yok ediyordu; evrensel yetenek listesinde özellik kimliği korunur.
    const seen=new Set();
    return rows.filter(row=>{const key=row.token;return seen.has(key)?false:(seen.add(key),true)});
  }
  const automationTriggerDevice=trigger=>state.devices.find(device=>device.id===trigger?.deviceId)||null;
  const automationTriggerDeviceName=trigger=>automationTriggerDevice(trigger)?.name||t("automationMissingDevice");
  const automationEventLabel=trigger=>{
    const device=automationTriggerDevice(trigger);
    // Hedefsiz değer tetikleyicisinde `equals` yoktur; jetonu satır listesiyle aynı dilde yazılır.
    const token=`${trigger?.property}=${String(trigger?.equals??null)}`;
    const match=["sensor","deviceState"]
      .flatMap(kind=>automationTriggerRows(device,kind))
      .find(row=>row.token===token);
    return match?match.label:`${trigger?.property??""}`;
  };
  const automationSentenceValues=(trigger,action,phrase)=>({
    time:trigger.at,
    days:automationDayList(trigger.days),
    device:automationActionName(action),
    action:phrase
  });
  const automationEventValues=(trigger,action,phrase)=>({
    device:automationTriggerDeviceName(trigger),
    button:automationButtonLabel(automationTriggerDevice(trigger),trigger.action),
    event:automationEventLabel(trigger),
    target:automationActionName(action),
    action:phrase
  });
  // Her eylem biçimi için ayrı tam anahtar; TR/EN cümleleri parçadan kurulmuyor. Değer eylemlerinin
  // şablonu değeri de içinde taşır ("parlaklığı {value} olacak"): "parlaklık" + "%40" diye
  // birleştirilmez, çünkü TR/EN kelime sırası ve ek yapısı farklıdır.
  const automationSentenceKeys={
    on:"automationWillTurnOn",off:"automationWillTurnOff",toggle:"automationWillToggle",
    level:"automationWillSetBrightness",temperature:"automationWillSetWarmth",color:"automationWillSetColor",value:"automationWillSetValue",
    followRatio:"automationWillFollowRatio",followColor:"automationWillFollowColor"
  };
  const automationCardKeys={
    on:"automationTurnsOn",off:"automationTurnsOff",toggle:"automationToggles",
    level:"automationSetsBrightness",temperature:"automationSetsWarmth",color:"automationSetsColor",value:"automationSetsValue",
    followRatio:"automationFollowsRatio",followColor:"automationFollowsColor"
  };
  const automationActionPhrase=(keys,action)=>{
    const mode=automationActionMode(action);
    return t(keys[mode]||keys.on,{value:automationValueText(automationControl(action),action?.value)});
  };
  // §5.4 anahtar/priz yolu: tetikleyicide durum tutulmaz, yön eylemlerin `when` alanındadır.
  const automationMapModes=["on","off","toggle","none"];
  const automationStateValue=(deviceId,property,on)=>{
    const device=state.devices.find(item=>item.id===deviceId)||null;
    const control=(device?.controls||[]).find(item=>item.property===property)||null;
    return control?automationControlValue(control,on):(on?"ON":"OFF");
  };
  const automationWhenDirection=(trigger,when)=>{
    if(!trigger||!when)return null;
    if(when.equals===automationStateValue(trigger.deviceId,trigger.property,true))return"on";
    if(when.equals===automationStateValue(trigger.deviceId,trigger.property,false))return"off";
    const text=String(when.equals).toUpperCase();
    if(when.equals===true||text==="ON")return"on";
    if(when.equals===false||text==="OFF")return"off";
    return null;
  };
  // §9.1 — gün batımı + gün doğumu tek kuralda: iki güneş tetikleyicisi, eşleşme değeri olay adı.
  // Yuvalar tek olaylı eski kuralı da taşır: eksik olan yuva boş kalır, kayıt kaybolmaz.
  const automationSunSlots=automation=>{
    const all=(automation?.triggers||[]).filter(Boolean);
    const triggers=all.filter(trigger=>trigger.type==="sun");
    if(!triggers.length||triggers.length!==all.length)return null;
    return{
      sunset:triggers.find(trigger=>trigger.event==="sunset")||null,
      sunrise:triggers.find(trigger=>trigger.event==="sunrise")||null
    };
  };
  const automationSunPairTriggers=automation=>{
    const slots=automationSunSlots(automation);
    return slots?.sunset&&slots?.sunrise?slots:null;
  };
  // Güneş eşlemesinde yön adı olay adıdır: batış "açılınca" yerine geçer, doğuş "kapanınca".
  const automationSunWhenDirection=when=>when?.equals==="sunset"?"on":when?.equals==="sunrise"?"off":null;
  // Kayıtlı kuralı eşleme görünümüne çevirir. Çoklu hedefte her kanal kendi yön çiftini taşır;
  // bir kanalda aynı yön iki kez varsa bu bir eşleme değildir. Çözülemezse null döner.
  const automationMapView=automation=>{
    const trigger=automation?.triggers?.[0];
    const actions=(automation?.actions||[]).filter(Boolean);
    const pair=automationSunPairTriggers(automation);
    if(!pair){
      if(trigger?.type!=="deviceState")return null;
      if(trigger.equals!==undefined&&trigger.equals!==null)return null;
      // §2.1 — süreli tetikleyici eşleme değildir: anahtarın iki yönü değil, tek bir tutma anı.
      if(automationTriggerHeldSeconds(trigger)>0)return null;
    }
    if(!actions.length||!actions.every(action=>action.when))return null;
    const groups=[];
    for(const action of actions){
      const direction=pair?automationSunWhenDirection(action.when):automationWhenDirection(trigger,action.when);
      if(!direction)return null;
      const key=automationChannelKey(action.deviceId,action.property);
      let entry=groups.find(item=>item.key===key);
      if(!entry){entry={key,on:null,off:null};groups.push(entry)}
      if(entry[direction])return null;
      entry[direction]=action;
    }
    if(!groups.length)return null;
    return{
      kind:pair?"sun":"device",
      trigger,
      sun:pair,
      targets:groups.map(entry=>({
        target:entry.on||entry.off,
        onMode:entry.on?automationActionMode(entry.on):"none",
        offMode:entry.off?automationActionMode(entry.off):"none"
      }))
    };
  };
  // Tam şablon anahtarı — iki yönlü cümle parça birleştirilerek kurulmuyor (TR/EN kelime sırası farklı).
  const automationMapText=(keys,templates,device,target,onMode,offMode)=>{
    const values={device,target,onAction:t(keys[onMode]||keys.on),offAction:t(keys[offMode]||keys.off)};
    if(onMode!=="none"&&offMode!=="none")return t(templates.both,values);
    return onMode!=="none"?t(templates.on,values):t(templates.off,values);
  };
  const automationMapSentence=(device,target,onMode,offMode)=>automationMapText(
    automationSentenceKeys,
    {both:"automationSummaryMap",on:"automationSummaryMapOn",off:"automationSummaryMapOff"},
    device,target,onMode,offMode
  );
  const automationMapCardLine=(device,target,onMode,offMode)=>automationMapText(
    automationCardKeys,
    {both:"automationCardSummaryMap",on:"automationCardSummaryMapOn",off:"automationCardSummaryMapOff"},
    device,target,onMode,offMode
  );
  // §9.1 — güneş eşlemesinin cümleleri ayrı tam şablonlar: "açılınca" yerine "gün batımında".
  const automationSunMapSentence=(target,onMode,offMode)=>automationMapText(
    automationSentenceKeys,
    {both:"automationSummarySunMap",on:"automationSummarySunMapOn",off:"automationSummarySunMapOff"},
    "",target,onMode,offMode
  );
  const automationSunMapCardLine=(target,onMode,offMode)=>automationMapText(
    automationCardKeys,
    {both:"automationCardSummarySunMap",on:"automationCardSummarySunMapOn",off:"automationCardSummarySunMapOff"},
    "",target,onMode,offMode
  );
  // Tam şablon anahtarı — cümle parça birleştirilerek kurulmuyor (TR/EN kelime sırası farklı).
  // Güneş ve eşik tetikleyicileri kendi cümlelerini kurar; saat cümlesine zorlanmaz.
  const automationSunSentenceTime=trigger=>{
    const word=t(trigger.event==="sunrise"?"automationSunriseWord":"automationSunsetWord");
    if(!trigger.offsetMinutes)return word;
    return t(trigger.offsetMinutes<0?"automationSunBeforeShort":"automationSunAfterShort",{minutes:Math.abs(trigger.offsetMinutes),moment:word});
  };
  // §2.1 — süreli tetikleyici kayıtta da tanınır: kartlar ve özet cümleleri süreyi söyler.
  const automationTriggerHeldSeconds=trigger=>trigger?.type==="deviceState"
    &&Number.isFinite(trigger.forSeconds)&&trigger.forSeconds>0?trigger.forSeconds:0;
  const automationThresholdSentenceEvent=trigger=>{
    const device=automationTriggerDevice(trigger);
    const above=trigger.above!==undefined&&trigger.above!==null;
    // Süreli eşikte fiil değişir: "üstüne çıkınca" değil, "üstünde kalınca".
    const held=automationTriggerHeldSeconds(trigger)>0;
    return t(above
      ?(held?"automationThresholdAboveHeldShort":"automationThresholdAboveShort")
      :(held?"automationThresholdBelowHeldShort":"automationThresholdBelowShort"),{
      reading:automationPropertyLabel(device,trigger.property),
      value:`${above?trigger.above:trigger.below}${automationPropertyUnit(device,trigger.property)}`
    });
  };
  const automationSentence=(trigger,action)=>{
    const actionKey=automationActionPhrase(automationSentenceKeys,action);
    if(trigger.type==="deviceAction")return t("automationSummaryButton",automationEventValues(trigger,action,actionKey));
    if(trigger.type==="sun"){
      const values={...automationSentenceValues(trigger,action,actionKey),time:automationSunSentenceTime(trigger)};
      return t(automationEveryDay(trigger.days)?"automationSummaryTime":"automationSummaryTimeDays",values);
    }
    // §2.1 — süre taşıyan durum tetikleyicisinin kendi tam şablonu var; parça birleştirme yok.
    const held=automationTriggerHeldSeconds(trigger);
    const duration=held?automationDurationTextShort(held):"";
    if(automationTriggerHasThreshold(trigger)){
      return t(held?"automationSummaryStateFor":"automationSummaryState",{...automationEventValues(trigger,action,actionKey),event:automationThresholdSentenceEvent(trigger),duration});
    }
    if(trigger.type==="deviceState")return t(held?"automationSummaryStateFor":"automationSummaryState",{...automationEventValues(trigger,action,actionKey),duration});
    return t(
      automationEveryDay(trigger.days)?"automationSummaryTime":"automationSummaryTimeDays",
      automationSentenceValues(trigger,action,actionKey)
    );
  };
  const automationCardLine=(trigger,action)=>{
    const actionKey=automationActionPhrase(automationCardKeys,action);
    if(trigger.type==="deviceAction")return t("automationCardSummaryButton",automationEventValues(trigger,action,actionKey));
    if(trigger.type==="sun"){
      const values={...automationSentenceValues(trigger,action,actionKey),time:automationSunSentenceTime(trigger)};
      return t(automationEveryDay(trigger.days)?"automationCardSummary":"automationCardSummaryDays",values);
    }
    const held=automationTriggerHeldSeconds(trigger);
    const duration=held?automationDurationTextShort(held):"";
    if(automationTriggerHasThreshold(trigger)){
      return t(held?"automationCardSummaryStateFor":"automationCardSummaryState",{...automationEventValues(trigger,action,actionKey),event:automationThresholdSentenceEvent(trigger),duration});
    }
    if(trigger.type==="deviceState")return t(held?"automationCardSummaryStateFor":"automationCardSummaryState",{...automationEventValues(trigger,action,actionKey),duration});
    return t(
      automationEveryDay(trigger.days)?"automationCardSummary":"automationCardSummaryDays",
      automationSentenceValues(trigger,action,actionKey)
    );
  };
  // §9 "sonra kapat" — açan kural kendi kapanışını da taşır, kullanıcı iki kural kurmaz.
  const automationAutoOffModes=["none","idle","after"];
  const automationDurationText=seconds=>{
    const minutes=Math.max(1,Math.round(Number(seconds||0)/60));
    return minutes===1?t("automationDurationMinute"):t("automationDurationMinutes",{count:minutes});
  };
  // §2.1 — bir dakikanın altı saniye, üstü dakika. Süreli koşul ve süreli tetikleyici paylaşır.
  const automationDurationTextShort=seconds=>{
    const total=Math.max(1,Math.round(Number(seconds)||0));
    return total<60?t("automationDurationSeconds",{count:total}):automationDurationText(total);
  };
  // Tam şablon anahtarı — cümle parça birleştirilerek kurulmuyor (TR/EN kelime sırası farklı).
  const automationAutoOffLine=action=>{
    const auto=action?.autoOff;
    if(!auto)return"";
    const device=automationActionName(action);
    if(auto.mode==="after")return t("automationAutoOffAfterLine",{duration:automationDurationText(auto.seconds),device});
    return auto.seconds>=60
      ?t("automationAutoOffIdleWaitLine",{duration:automationDurationText(auto.seconds),device})
      :t("automationAutoOffIdleLine",{device});
  };
  /* Geçmiş SON 5 çalışmayla sınırlı. Sınır ISTEKTE uygulanır, panelde kırpılarak değil: sunucunun
     okuyucusu günlüğü sondan tarar ve sayı dolunca durur (`automation-runs.ts`), yani `limit=5`
     dosyadan okunan satırı da yanıtı da küçültür. Panelde kırpmak aynı işi yaptırıp fazlasını
     atmak olurdu. Tek kaynak burası; çizim de aynı sabiti savunma amaçlı kullanır. */
  const automationRunHistoryLimit=5;
  /* Alt satır "son çalışma SAATİ" der: bugünse saat, değilse gün + saat. `ago()` saatte durduğu
     için dünkü çalışma "31 saat önce" diye görünüyordu; kullanıcı saati istedi. */
  const automationRunClock=iso=>{
    const at=new Date(iso||"");
    if(Number.isNaN(at.getTime()))return"";
    const locale=state.language==="tr"?"tr-TR":"en-GB";
    const sameDay=at.toDateString()===new Date().toDateString();
    return at.toLocaleString(locale,sameDay
      ?{hour:"2-digit",minute:"2-digit"}
      :{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
  };
  const automationRunFullClock=iso=>{
    const at=new Date(iso||"");
    if(Number.isNaN(at.getTime()))return"";
    return at.toLocaleString(state.language==="tr"?"tr-TR":"en-GB");
  };
  // Günlükteki kimlik IEEE adresi; insan cihazın adını tanır, adresi değil.
  const automationRunDeviceName=id=>{
    const key=String(id||"");
    if(!key)return"";
    return state.devices.find(device=>device.id===key)?.name||key;
  };
  const automationRunTriggerKindText=trigger=>{
    const kind=trigger?.kind;
    if(!kind)return"";
    const key=`automationRunTrigger_${kind}`;
    const text=t(key);
    return text===key?"":text;
  };
  // Tetikleyen olay VE değeri: "cihaz olayı · Salon sensör · occupancy = true · 63 sn boyunca".
  const automationRunTriggerText=trigger=>{
    if(!trigger)return"";
    const value=trigger.value===undefined||trigger.value===null?"":String(trigger.value);
    const held=Number(trigger.heldSeconds);
    return[
      automationRunTriggerKindText(trigger),
      automationRunDeviceName(trigger.deviceId),
      trigger.property?(value?`${trigger.property} = ${value}`:String(trigger.property)):value,
      Number.isFinite(held)&&held>0?t("automationRunHeld",{duration:automationDurationTextShort(held)}):""
    ].filter(Boolean).join(" · ");
  };
  const automationRunFactHtml=(label,value)=>value
    ?`<div class="automation-run-fact"><dt>${esc(label)}</dt><dd>${value}</dd></div>`
    :"";
  const automationRunItemHtml=(ok,text,error)=>`<li class="automation-run-item ${ok?"is-ok":"is-failed"}"><span class="automation-run-glyph" aria-hidden="true">${ok?"✓":"✕"}</span><span>${esc(text)}${error?` <span class="automation-run-item-error">${esc(error)}</span>`:""}</span></li>`;
  const automationRunActionTypeText=type=>{
    const key=String(type||"")==="autoOff"?"automationRunAction_autoOff":null;
    return key?t(key):String(type||"");
  };
  /* Ayrıntı: "otomasyon neden çalışmadı" sorusu karta bakarak yanıtlanır. Kayıt zaten zengin
     (sonuç, sebep kodu, açıklama, tetikleyici, koşullar, eylem eylem sonuç) — burada okunur hale
     gelir. Boş alan hiç basılmaz; kayıt eskiyse ayrıntı kendiliğinden kısalır. */
  const automationRunDetailHtml=run=>{
    const reason=[run?.reason?automationReasonText(run.reason):"",String(run?.detail||"").trim()]
      .filter(Boolean).join(" — ");
    const conditions=(Array.isArray(run?.conditions)?run.conditions:[])
      .map(item=>automationRunItemHtml(item?.ok!==false,String(item?.type||""),item?.ok===false?String(item?.reason||""):""))
      .join("");
    const actions=(Array.isArray(run?.actions)?run.actions:[])
      .map(item=>automationRunItemHtml(
        item?.ok!==false,
        [automationRunActionTypeText(item?.type),automationRunDeviceName(item?.target)].filter(Boolean).join(" · "),
        item?.ok===false?String(item?.error||""):""
      ))
      .join("");
    const body=[
      automationRunFactHtml(t("automationRunDetailWhen"),esc(automationRunFullClock(run?.at))),
      automationRunFactHtml(t("automationRunDetailReason"),esc(reason)),
      automationRunFactHtml(t("automationRunDetailTrigger"),esc(automationRunTriggerText(run?.trigger))),
      automationRunFactHtml(t("automationRunDetailConditions"),conditions?`<ul class="automation-run-items">${conditions}</ul>`:""),
      automationRunFactHtml(
        t("automationRunDetailActions"),
        actions?`<ul class="automation-run-items">${actions}</ul>`:esc(t("automationRunNoAction"))
      )
    ].join("");
    return`<dl class="automation-run-detail">${body}</dl>`;
  };
  // "Neden çalışmadı?" — kuralın kendi çalışma geçmişi kartın içinde açılır, satır satır.
  const automationRunRowHtml=(run,key)=>{
    const outcome=String(run?.outcome||"");
    const glyph=automationOutcomeGlyphs[outcome]||"·";
    const reason=run?.reason?automationReasonText(run.reason):"";
    const meta=[reason,automationRunTriggerKindText(run?.trigger)].filter(Boolean).join(" · ");
    const open=state.automationRunDetail===key;
    return`<li class="automation-run-row is-${esc(outcome)}"><button class="automation-run-head" type="button" data-automation-run-detail="${esc(key)}" aria-expanded="${open}"><time datetime="${esc(run?.at||"")}">${esc(automationRunClock(run?.at))}</time><span class="automation-run-outcome"><span class="automation-run-glyph" aria-hidden="true">${glyph}</span>${esc(automationOutcomeText(outcome))}</span><span class="automation-run-reason">${esc(meta)}</span></button>${open?automationRunDetailHtml(run):""}</li>`;
  };
  const automationRunsHtml=automation=>{
    if(state.automationRunsOpen!==automation.id)return"";
    const runs=state.automationRuns[automation.id];
    if(runs===undefined)return`<div class="automation-runs"><p class="automation-runs-empty">${esc(t("automationRunsLoading"))}</p></div>`;
    // Günlük dosyası yoksa/okunamıyorsa sunucu 503 döner: bunu "hiç çalışmadı" diye göstermek
    // yalan olur, ayrı cümlesi var.
    if(!Array.isArray(runs))return`<div class="automation-runs"><p class="automation-runs-empty">${esc(t("automationRunsUnavailable"))}</p></div>`;
    // Kart "son çalışma" der ama günlük boşsa çelişki gibi görünür: günlüğün kuraldan yeni
    // olduğunu tek cümleyle söyler. Yalnız bu durumda — hiç çalışmamış kuralda eski cümle kalır.
    if(!runs.length)return`<div class="automation-runs"><p class="automation-runs-empty">${esc(t(automation.lastRunAt?"automationRunsOlderThanLog":"automationRunsEmpty"))}</p></div>`;
    const rows=runs.slice(0,automationRunHistoryLimit)
      .map((run,index)=>automationRunRowHtml(run,`${automation.id}|${index}`)).join("");
    return`<div class="automation-runs"><ul class="automation-run-list">${rows}</ul></div>`;
  };
  // Ajanın yazdığı kural ayırt edilir: kim yazdı ve ne zaman, işaretin ipucunda durur. Panelden
  // düzenlenen kural sihirbazda sıfırdan kurulduğu için damgayı kaybeder — kural artık insanındır.
  const automationAgentChip=automation=>{
    const agent=automation.agent;
    if(!agent)return"";
    const title=t("automationAgentChipTitle",{name:esc(agent.tokenName||""),time:ago(agent.at)});
    return`<span class="automation-card-chip agent" title="${esc(title)}"><span aria-hidden="true">🤖</span> ${esc(t("automationAgentChip"))}</span>`;
  };
  const automationInactiveHtml=automation=>{
    const inactive=automation.inactiveReason;
    if(!inactive)return"";
    const text=automationReasonText(inactive.code);
    return`<span class="automation-card-chip warn">${esc(text)}</span>`;
  };
  /* ALT SATIR — ince ayracın altında: son çalışma saati solda, geçmiş bağlantısı sağda.
     Hiç çalışmamış kuralda açılacak bir geçmiş yoktur: bağlantı hiç basılmaz, satır tek cümlede
     biter ve kart bir tık daha kısalır. */
  const automationCardFootHtml=automation=>{
    const failed=automation.lastRunOk===false;
    const when=automation.lastRunAt
      ?t(failed?"automationLastRunFailed":"automationLastRun",{time:automationRunClock(automation.lastRunAt)})
      :t("automationNeverRan");
    const open=state.automationRunsOpen===automation.id;
    const link=automation.lastRunAt
      ?`<button class="automation-runs-toggle" type="button" data-automation-runs="${esc(automation.id)}" aria-expanded="${open}"><span>${esc(t("automationRunsTitle"))}</span><span class="automation-runs-caret" aria-hidden="true">${open?"⌃":"⌄"}</span></button>`
      :"";
    return`<div class="automation-card-foot"><span class="automation-card-when${failed?" warn":""}">${esc(when)}</span>${automationInactiveHtml(automation)}${link}</div>`;
  };
  /* AYARLAR DİŞLİSİ — emoji glifi değil çizim. "⚙" karakteri yazı boyunda kalıyor, yazı tipine göre
     inceliyor ve dokunmatikte kayboluyordu; çizilen dişli kutunun tamamını doldurur, kalınlığı ve
     boyu buradan bellidir. */
  const AUTOMATION_GEAR_SVG='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/></svg>';
  const automationCardHtml=automation=>{
    const trigger=automation.triggers?.[0];
    const manual=automation.manual===true;
    const actions=(automation.actions||[]).filter(Boolean);
    const action=actions.find(item=>item.type==="device")||actions[0];
    if((!trigger&&!manual)||!action)return"";
    // Eşleme kuralı tek satırda iki yönü birden anlatır; kart özeti sihirbazdaki cümleyle tutarlıdır.
    const map=automationMapView(automation);
    const first=map?map.targets[0]:null;
    const line=manual
      ?t("automationManualCardLine",{action:automationActionName(action)})
      :map
      ?(map.kind==="sun"
        ?automationSunMapCardLine(automationActionName(first.target),first.onMode,first.offMode)
        :automationMapCardLine(automationTriggerDeviceName(map.trigger),automationActionName(first.target),first.onMode,first.offMode))
      :automationCardLine(trigger,action);
    // Çoklu hedefte kart ilk hedefi yazar, kalanı sayıyla anar; satır tek satır kalır.
    const rest=(map?map.targets.length:actions.length)-1;
    const more=rest>0?` ${t("automationCardMore",{count:rest})}`:"";
    // Kapanış sözü ayrı bir cümle olarak görünür; kart özeti sihirbazdaki cümleyle tutarlı kalır.
    const autoOff=map?"":automationAutoOffLine(actions.find(item=>item.autoOff)||action);
    // Kapanış sözü ayrı bir SATIR değil, aynı cümlenin devamı: iki satırlık kutuya sığar, kartı uzatmaz.
    const note=[line+more,autoOff].filter(Boolean).join(" · ");
    // BAŞLIK SATIRI: solda ayarlar dişlisi, ortada ad, sağda aç/kapa. Kural cümlesi kendi satırında,
    // adın altında alt başlık olarak akar — düğmelerle yan yana durmaz.
    return`<article class="automation-card${automation.enabled?"":" off"}" tabindex="0" data-automation-card="${esc(automation.id)}" aria-label="${esc(automation.name)}"><div class="automation-card-head"><button class="automation-card-menu" type="button" data-automation-menu="${esc(automation.id)}" aria-label="${esc(t("automationCardMenu"))}" title="${esc(t("automationCardMenu"))}">${AUTOMATION_GEAR_SVG}</button><strong class="automation-card-name"><span class="automation-card-title">${esc(automation.name)}</span>${automationAgentChip(automation)}</strong><button class="device-card-toggle${automation.enabled?" on":""}" type="button" data-automation-toggle="${esc(automation.id)}" aria-pressed="${automation.enabled}" aria-label="${esc(automation.name)}"><span class="toggle-track" aria-hidden="true"><span class="toggle-knob"></span></span></button><span class="automation-card-note" title="${esc(note)}">${esc(note)}</span></div>${automationCardFootHtml(automation)}${automationRunsHtml(automation)}</article>`;
  };
  /* Geri alma modelin değil kullanıcının işidir: bu yüzden bir MCP aracı değil, panelde bir düğme.
     Sunucu ajan yazmasından **önce** aldığı yedekleri sayar; sayı sıfırsa geri alınacak bir şey de
     yoktur ve şerit hiç görünmez. */
  function renderAutomationAgentBar(){
    const bar=$("#automationAgentBar");
    if(!bar)return;
    const count=Number(state.automationAgentBackups)||0;
    bar.hidden=count<1;
    const button=$("#revertAgentAutomations");
    if(button)button.disabled=count<1;
  }
  // Yedek tüketilir: düğmeye ikinci kez basmak ileri/geri salınmaz, bir adım daha geriye gider.
  async function revertAgentAutomations(){
    const button=$("#revertAgentAutomations");
    if(button)button.disabled=true;
    try{
      const data=await api("/api/automations/agent-revert",{method:"POST"});
      state.automations=Array.isArray(data.automations)?data.automations:state.automations;
      state.automationAgentBackups=Number(data.agentBackups)||0;
      renderAutomations();
      showToast(t("automationAgentReverted"));
    }catch(error){showToast(error.message,true)}
    renderAutomationAgentBar();
  }
  function renderAutomations(){
    // Aynı veri iki ekranda: yönetici listesi ve ev kullanıcısının Rutinler görünümü.
    renderRoutines();
    renderAutomationAgentBar();
    const container=$("#automationList");
    if(!container)return;
    const links=simpleLinks();
    const cards=[
      ...state.automations.map(automationCardHtml),
      ...links.map(link=>`<article class="automation-card link-card"><span class="automation-card-glyph" aria-hidden="true">⚡</span><div class="automation-card-copy"><strong>${t("simpleLinkSummary",{source:esc(link.sourceName),target:esc(link.targetName)})}</strong><span class="automation-card-note">${t("simpleLinkDirectNote")}</span></div><button class="danger-button" type="button" data-remove-link="${esc(link.key)}">${t("simpleLinkRemove")}</button></article>`)
    ].filter(Boolean);
    container.innerHTML=cards.length
      ?cards.join("")
      :`<div class="empty automation-empty"><div class="automation-empty-icon" aria-hidden="true">⚡</div><h2>${t("automationsEmptyTitle")}</h2><p>${t("automationsEmptyLead")}</p></div>`;
    $$("[data-remove-link]").forEach(button=>button.onclick=()=>removeSimpleLink(button.dataset.removeLink));
    // Anahtar ve menü kendi işini yapar, dokunuş karta sızmaz: yanlışlıkla düzenleme açılmaz.
    $$("[data-automation-toggle]").forEach(button=>button.onclick=event=>{event.stopPropagation();toggleAutomationEnabled(button.dataset.automationToggle)});
    $$("[data-automation-menu]").forEach(button=>button.onclick=event=>{event.stopPropagation();openAutomationActions(button.dataset.automationMenu)});
    $$("[data-automation-runs]").forEach(button=>button.onclick=event=>{event.stopPropagation();toggleAutomationRuns(button.dataset.automationRuns)});
    $$("[data-automation-run-detail]").forEach(button=>button.onclick=event=>{event.stopPropagation();toggleAutomationRunDetail(button.dataset.automationRunDetail)});
    // Kart gövdesine tek dokunuş doğrudan düzenlemeyi açar; seçenekler görünür "⚙" düğmesinde.
    $$("[data-automation-card]").forEach(card=>{
      card.onclick=event=>{
        if(event.target.closest?.("[data-automation-toggle],[data-automation-menu],[data-automation-runs],.automation-card-foot,.automation-runs"))return;
        openAutomationWizard(card.dataset.automationCard);
      };
    });
  }
  /* Geçmiş açılınca sunucudan çekilir; başarısız olursa boş kalmaz, sebebi yazar. Okunamayan
     günlük "hiç çalışmadı" DEĞİLDİR: dizi yerine işaret bırakılır, çizim ayrı cümle yazar. */
  async function toggleAutomationRuns(id){
    if(!id)return;
    if(state.automationRunsOpen===id){state.automationRunsOpen=null;state.automationRunDetail=null;renderAutomations();return}
    state.automationRunsOpen=id;
    state.automationRunDetail=null;
    delete state.automationRuns[id];
    renderAutomations();
    try{
      const data=await api(`/api/automations/${encodeURIComponent(id)}/runs?limit=${automationRunHistoryLimit}`);
      state.automationRuns[id]=Array.isArray(data.runs)?data.runs:[];
    }catch(error){
      state.automationRuns[id]={error:true};
      showToast(error.message,true);
    }
    if(state.automationRunsOpen===id)renderAutomations();
  }
  // Tek satır açık kalır: ikinci satıra dokunmak birincisini kapatır, liste kısa durur.
  function toggleAutomationRunDetail(key){
    if(!key)return;
    state.automationRunDetail=state.automationRunDetail===key?null:key;
    renderAutomations();
  }
  // İleri/Kaydet iki yerde durur (üstte ve altta) ama tek davranıştır; Geri yalnız altta kalır.
  // Tek birincil eylem: alttaki düğme. Üstteki kopya kaldırıldı, hangisinin asıl olduğu sorusu bitti.
  const automationNextButtons=()=>[$("#automationNext")].filter(Boolean);
  // PIN yeniden istenirken native dialog geçici kapanır. `close` dinleyicisi bu tek bayrakla
  // taslağı silmez; kullanıcı PIN'i girince aynı sihirbaz kaldığı yerden yeniden açılır.
  let automationWizardReauthorizing=false;
  // Son basış ipucu yoklama turuyla tazelenir; yalnız düğme seçim adımında, metin alanı yok.
  function refreshAutomationHint(){
    const wizard=state.automationWizard;
    if(!wizard||wizard.stage!=="trigEvent"||wizard.triggerKind!=="button"||!wizard.triggerDeviceId)return;
    if(!$("#automationDialog")?.open)return;
    renderAutomationWizard();
  }
  async function loadAutomations(){
    const data=await api("/api/automations");
    state.automations=Array.isArray(data.automations)?data.automations:[];
    // Güneş saatleri ve konum yanıtın kökünden gelir; arayüz kendi hesabını yapmaz.
    state.automationSun=data.sun||null;
    state.homeLocation=data.location||null;
    // Ajan yedeği sayısı: "geri al" yolunun görünür olup olmayacağını tek başına belirler.
    state.automationAgentBackups=Number(data.agentBackups)||0;
  }
  async function persistAutomations(automations,successKey,reauthorize={}){
    const returnView=document.body.dataset.activeView||"automations";
    let data;
    try{
      data=await api("/api/automations",{method:"PUT",body:JSON.stringify({automations})});
    }catch(error){
      if(error?.code!=="ELEVATION_REQUIRED"||reauthorize.retry===false)throw error;
      if(typeof reauthorize.before==="function")reauthorize.before();
      const granted=await requestAdminElevation();
      if(!granted){
        const canceled=new Error("Administrator mode was cancelled.");
        canceled.code="ELEVATION_CANCELED";
        throw canceled;
      }
      activateView(returnView);
      if(typeof reauthorize.after==="function")reauthorize.after();
      return persistAutomations(automations,successKey,{...reauthorize,retry:false});
    }
    state.automations=Array.isArray(data.automations)?data.automations:automations;
    renderAutomations();
    if(successKey)showToast(t(successKey));
  }
  /* İlk dört hızlı sahne gerçek otomasyon kaydıdır; örnek düğme ya da ayrı bir sahne deposu
     değildir. Yalnız yönetici oturumunda, yalnız eksik kimlikler eklenir. Eylemler cihazın
     sunduğu ve "ışık" diye sınıflandırdığı güvenli kanallardan türetilir; kilit/siren gibi
     korumalı cihazlar bu listeye hiçbir koşulda girmez. */
  const quickSceneExamples=[
    {id:quickSceneExampleIds[0],name:"quickSceneLeaveHome",mode:"off"},
    {id:quickSceneExampleIds[1],name:"quickSceneNight",mode:"night"},
    {id:quickSceneExampleIds[2],name:"quickSceneGuests",mode:"guest"},
    {id:quickSceneExampleIds[3],name:"quickSceneAllLightsOff",mode:"off"}
  ];
  const quickSceneLightControls=()=>state.devices.flatMap(device=>{
    if(isProtectedDevice(device))return[];
    return automationControls(device)
      .filter(control=>control.category==="light"||device.category==="light")
      .map(control=>({device,control}));
  });
  const quickSceneLevelControls=lights=>lights.flatMap(({device,control})=>
    automationValueControls(device,control.id)
      .filter(candidate=>candidate.kind==="level")
      .map(candidate=>({device,control:candidate})));
  const quickSceneAction=(device,control,value)=>({
    type:"device",deviceId:device.id,property:control.property,controlId:control.id,value
  });
  const quickSceneActions=(mode,lights,levels)=>{
    if(mode==="night"&&levels.length)return levels.slice(0,automationMaxActions)
      .map(({device,control})=>quickSceneAction(device,control,automationValueRaw(control,20)));
    if(mode==="guest"&&levels.length)return levels.slice(0,automationMaxActions)
      .map(({device,control})=>quickSceneAction(device,control,automationValueRaw(control,70)));
    const on=mode==="guest";
    return lights.slice(0,automationMaxActions)
      .map(({device,control})=>quickSceneAction(device,control,automationControlValue(control,on)));
  };
  async function ensureQuickSceneExamples(){
    if(state.auth.elevated!==true)return;
    const lights=quickSceneLightControls();
    if(!lights.length)return;
    const levels=quickSceneLevelControls(lights);
    try{
      const data=await api("/api/automations");
      const automations=Array.isArray(data.automations)?data.automations:[];
      const ids=new Set(automations.map(item=>item?.id));
      const additions=quickSceneExamples.filter(example=>!ids.has(example.id)).map(example=>({
        id:example.id,
        name:t(example.name),
        enabled:true,
        manual:true,
        triggers:[],
        conditions:[],
        actions:quickSceneActions(example.mode,lights,levels)
      })).filter(entry=>entry.actions.length>0).slice(0,Math.max(0,64-automations.length));
      if(!additions.length)return;
      await persistAutomations([...automations,...additions],null);
      renderHomeScenes();
      renderRoutines();
      showToast(t("quickSceneExamplesAdded",{count:additions.length}));
    }catch(error){showToast(error.message,true)}
  }
  // ————— evin konumu. Okuma ev modunda açık, yazma yönetici modu işi: ev modunda alanlar
  // salt-okunur. Gizleme bir yetki değil, sunucu aynı kuralı her istekte yeniden uygular.
  const isResidentSession=()=>state.auth.elevated!==true;
  async function loadHomeLocation(){
    const data=await api("/api/settings/location");
    state.homeLocation=data.location||null;
    state.homeLocationSource=data.source||null;
    state.automationSun=data.sun||null;
    state.homeLocationTimeZoneVerificationRequired=data.timeZoneVerificationRequired===true;
    renderHomeLocation();
  }
  // Kayıtlı yerin adı; ad yoksa (elle girilmiş koordinat) sayılar gösterilir, "enlem/boylam" denmez.
  const homeLocationName=()=>{
    if(!state.homeLocation)return t("homeLocationNotChosen");
    const label=String(state.homeLocation.label||"").trim();
    return label||`${state.homeLocation.latitude}, ${state.homeLocation.longitude}`;
  };
  function renderHomeLocation(){
    const readOnly=isResidentSession();
    const name=$("#homeLocationName");
    if(name)name.textContent=homeLocationName();
    const choose=$("#chooseHomeLocation");
    if(choose){choose.disabled=readOnly;choose.hidden=readOnly}
    const latitude=$("#homeLatitude");
    const longitude=$("#homeLongitude");
    const timeZone=$("#homeTimeZone");
    if(latitude&&longitude&&timeZone){
      if(document.activeElement!==latitude)latitude.value=state.homeLocation?String(state.homeLocation.latitude):"";
      if(document.activeElement!==longitude)longitude.value=state.homeLocation?String(state.homeLocation.longitude):"";
      if(document.activeElement!==timeZone)timeZone.value=state.homeLocation?.timeZone||"";
      latitude.readOnly=readOnly;
      longitude.readOnly=readOnly;
      timeZone.readOnly=readOnly;
    }
    const save=$("#saveHomeLocation");
    if(save){save.disabled=readOnly;save.hidden=readOnly}
    const note=$("#homeLocationSun");
    if(!note)return;
    // Kaydedilen yerin doğru olup olmadığı ancak güneş saatleriyle anlaşılır; sonuç hep yazılır.
    const sun=state.automationSun;
    if(!state.homeLocation)note.textContent=t("homeLocationMissing");
    else if(sun?.reason)note.textContent=automationReasonText(sun.reason);
    else if(sun?.sunrise&&sun?.sunset)note.textContent=t("homeLocationSunTimes",{sunrise:sun.sunrise,sunset:sun.sunset});
    else note.textContent=readOnly?t("homeLocationReadOnly"):"";
    if(readOnly&&state.homeLocation)note.textContent=`${note.textContent} ${t("homeLocationReadOnly")}`.trim();
    if(state.homeLocationTimeZoneVerificationRequired)note.textContent=`${note.textContent} ${t("homeLocationTimeZoneVerify")}`.trim();
  }
  // Tek yazma yolu: seçilen yer de elle girilen koordinat da buradan geçer. Ad varsa sunucuya yazılır.
  async function persistHomeLocation({latitude,longitude,timeZone,label}){
    if(isResidentSession())return false;
    if(!Number.isFinite(latitude)||latitude<-90||latitude>90){showToast(t("latitudeInvalid"),true);return false}
    if(!Number.isFinite(longitude)||longitude<-180||longitude>180){showToast(t("longitudeInvalid"),true);return false}
    const zone=String(timeZone||"").trim();
    try{new Intl.DateTimeFormat("en",{timeZone:zone}).format(new Date())}catch{showToast(t("timeZoneInvalid"),true);return false}
    const name=String(label??"").trim().slice(0,80);
    try{
      const data=await api("/api/settings/location",{method:"PUT",body:JSON.stringify(name?{latitude,longitude,timeZone:zone,label:name}:{latitude,longitude,timeZone:zone})});
      state.homeLocation=data.location||null;
      state.homeLocationSource=data.source||null;
      state.automationSun=data.sun||null;
      state.homeLocationTimeZoneVerificationRequired=false;
      renderHomeLocation();
      renderAutomations();
      renderLocationSearchResults("home");
      showToast(t("homeLocationSaved"));
      return true;
    }catch(error){showToast(error.message,true);return false}
  }
  async function saveHomeLocationForm(event){
    event?.preventDefault?.();
    await persistHomeLocation({latitude:Number($("#homeLatitude").value),longitude:Number($("#homeLongitude").value),timeZone:$("#homeTimeZone").value});
  }
  async function chooseHomeLocation(location){
    const saved=await persistHomeLocation({latitude:Number(location?.latitude),longitude:Number(location?.longitude),timeZone:location?.timeZone,label:location?.name});
    if(saved)$("#homeLocationDialog")?.close();
  }
  function openHomeLocationManager(){
    if(isResidentSession())return;
    resetLocationSearch("home");
    renderHomeLocationDialog();
    const dialog=$("#homeLocationDialog");
    if(dialog&&!dialog.open)dialog.showModal();
  }
  // Hava durumu konumu sunucudan gelir (`/api/weather`): seçilmişse tek dokunuşla evin konumu olur.
  function renderHomeLocationDialog(){
    const reuse=$("#useWeatherLocationForHome");
    if(!reuse)return;
    const weather=weatherState.location;
    reuse.hidden=!weather;
    reuse.disabled=!weather;
    reuse.textContent=weather?t("useWeatherLocationNamed",{name:locationName(weather)}):t("useWeatherLocation");
  }
  function useWeatherLocationForHome(){
    const weather=weatherState.location;
    if(!weather)return;
    chooseHomeLocation({latitude:weather.latitude,longitude:weather.longitude,timeZone:weather.timeZone,name:locationName(weather)});
  }
  // Güneş satırı pasifken kullanıcı çıkmaza düşmesin: doğrudan konum kartına götürür.
  function openHomeLocationSettings(){
    const dialog=$("#automationDialog");
    if(dialog?.open)dialog.close();
    activateView("settings");
    const button=$("#chooseHomeLocation");
    if(button){button.scrollIntoView({block:"center"});if(!button.hidden)button.focus()}
  }
  // Sunucu sebep kodları çeviri tablosundan geçer; bilinmeyen kod ham gösterilir, boşluk kalmaz.
  const automationReasonKeys={
    busy:"automationReasonBusy",noMatchingAction:"automationReasonNoMatchingAction",
    conditionFalse:"automationReasonConditionFalse",locationMissing:"automationReasonLocationMissing",
    sunUnavailable:"automationReasonSunUnavailable",nonNumericValue:"automationReasonNonNumericValue",
    noPreviousValue:"automationReasonNoPreviousValue",repeatSuppressed:"automationReasonRepeatSuppressed",
    stopped:"automationReasonStopped",unsupportedAction:"automationReasonUnsupportedAction",
    actionFailed:"automationReasonActionFailed",missing:"automationReasonMissing",
    autoOffCompleted:"automationReasonAutoOffCompleted",autoOffCanceled:"automationReasonAutoOffCanceled",
    autoOffFailed:"automationReasonAutoOffFailed"
  };
  // Bilinmeyen kod ham gösterilir; çeviri tablosunda olan kod çevrilir. Boşluk ya da çökme yok.
  const automationReasonText=code=>{
    const key=automationReasonKeys[String(code||"")];
    return key?t(key):String(code||"");
  };
  const automationOutcomeKeys={
    ok:"automationOutcomeOk",failed:"automationOutcomeFailed",busy:"automationOutcomeBusy",
    skipped:"automationOutcomeSkipped",blocked:"automationOutcomeBlocked"
  };
  // Renk tek başına yeterli değil: her sonuç kendi işaretini de taşır.
  const automationOutcomeGlyphs={ok:"✓",failed:"✕",busy:"↻",skipped:"→",blocked:"⊘"};
  const automationOutcomeText=outcome=>{
    const key=automationOutcomeKeys[String(outcome||"")];
    return key?t(key):String(outcome||"");
  };
  async function toggleAutomationEnabled(id){
    const next=state.automations.map(item=>item.id===id?{...item,enabled:item.enabled===false}:item);
    try{await persistAutomations(next)}
    catch(error){if(error?.code!=="ELEVATION_CANCELED")showToast(error.message,true)}
  }
  function openAutomationActions(id){
    const automation=state.automations.find(item=>item.id===id);
    if(!automation)return;
    state.automationContext=id;
    $("#automationActionName").textContent=automation.name;
    const dialog=$("#automationActionDialog");
    if(!dialog.open)dialog.showModal();
  }
  async function runAutomationNow(){
    const id=state.automationContext;
    $("#automationActionDialog").close();
    if(!id)return;
    try{
      await api(`/api/automations/${encodeURIComponent(id)}/run`,{method:"POST"});
      showToast(t("automationRanNow"));
      await loadAutomations();
      renderAutomations();
    }catch(error){showToast(error.message,true)}
  }
  /* ————— EV MODUNDAN TEK DOKUNUŞ. Yukarıdaki `runAutomationNow` yönetici penceresinin yoludur:
     kimliği `state.automationContext`ten okur ve pencereyi kapatır. Bu ince sarmalayıcı AYNI ucu
     kimlikle çağırır, pencere gerektirmez; sunucuda `POST /api/automations/:id/run` zaten
     `residentRoutes`ta olduğu için ev sakini rutini çalıştırabilir — ama düzenleyemez.

     ÇİFT GÖNDERİM YOK: istek cihaz komutlarıyla AYNI kayda (`state.pendingCommands`,
     `commandKey`) yazılır. İkinci dokunuş daha ağa çıkmadan döner, düğme de o sürede `disabled`
     çizilir. Kayıt `finally` içinde düşer: hata hâlinde de düğme kilitli kalmaz. */
  async function runSceneNow(id){
    if(!id)return;
    const key=commandKey(id,sceneCommandProperty);
    if(state.pendingCommands.has(key))return;
    state.pendingCommands.add(key);
    renderHomeScenes();
    renderRoutines();
    try{
      await api(`/api/automations/${encodeURIComponent(id)}/run`,{method:"POST"});
      const scene=state.automations.find(item=>item.id===id);
      showToast(t("sceneRan",{name:scene?.name||id}));
      // Son çalışma izi sunucudan gelir. Tazeleme başarısız olursa sonuç gizlenmez: kural çalıştı.
      await loadAutomations().catch(()=>{});
    }catch(error){showToast(error.message,true)}
    finally{
      state.pendingCommands.delete(key);
      renderHomeScenes();
      renderRoutines();
    }
  }
  /* ————— RUTİNLER GÖRÜNÜMÜ. Ev kullanıcısının listesi: ana ekranın şeridi ilk dördü gösterir,
     burada TAMAMI durur. Uzun açıklama (not §6.2) yalnız bu ekranda yazılır ve UYDURULMAZ —
     satırın altındaki cümle kuralın kendi sayılarından (tetikleyici cinsi, eylem sayısı, son
     çalışma) kurulur. Tek eylem var: çalıştır. Kapalı kural da listede kalır, çünkü elle
     çalıştırma motorun `enabled` bayrağına bakmaz; durumu satırda açıkça yazılır. */
  function routineCardHtml(scene){
    const busy=commandPending(scene.id,sceneCommandProperty);
    const notes=[
      t(scene.kind==="manual"?"routineKindManual":scene.kind==="routine"?"routineKindScheduled":"routineKindReactive"),
      t("routineActionCount",{count:scene.actionCount}),
      scene.lastRunAt?t("routineLastRun",{when:ago(scene.lastRunAt)}):t("routineNeverRun")
    ];
    const runLabel=t(busy?"sceneRunning":"routineRun");
    return`<article class="routine-card${scene.enabled?"":" is-off"}"><span class="routine-glyph" aria-hidden="true">${deviceIconSvg(sceneIconKind(scene))}</span><div class="routine-copy"><strong>${esc(scene.name)}</strong><span class="routine-note">${esc(notes.join(" · "))}</span>${scene.enabled?"":`<span class="routine-off-note">${esc(t("routineDisabledNote"))}</span>`}</div><button class="primary routine-run" type="button" data-run-scene="${esc(scene.id)}"${busy?' aria-busy="true" disabled':""}>${esc(runLabel)}</button></article>`;
  }
  function renderRoutines(){
    const container=$("#routineList");
    if(!container)return;
    const scenes=genSceneCatalog(state.automations);
    container.innerHTML=scenes.length
      ?scenes.map(routineCardHtml).join("")
      :`<div class="empty">${esc(t("routinesEmpty"))}</div>`;
    $$("#routineList [data-run-scene]").forEach(button=>button.onclick=()=>runSceneNow(button.dataset.runScene));
  }
  async function deleteAutomation(){
    const automation=state.automations.find(item=>item.id===state.automationContext);
    $("#automationActionDialog").close();
    if(!automation||!confirm(t("automationDeleteConfirm",{name:automation.name})))return;
    try{await persistAutomations(state.automations.filter(item=>item.id!==automation.id),"automationDeleted")}
    catch(error){if(error?.code!=="ELEVATION_CANCELED")showToast(error.message,true)}
  }
  /* ————— çoğaltma. Kopya sunucuya HEMEN yazılmaz: paneldeki "yeni otomasyon" da yalnız Kaydet'e
     basılınca kalıcı olur, çoğaltma da aynı akışa girer. Kullanıcı adı ve cihazları değiştirip
     kaydeder; vazgeçerse ortada yarım bir kural kalmaz. Açık taslak kopyaya dönüşür, özgün kayıt
     olduğu gibi durur. */
  function automationDraftToCopy(){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const base=automationWizardName(wizard);
    // Yeni kayıt yeni kimlik alır: kimlik kaydederken üretilir, eskisi taşınmaz.
    wizard.id=null;
    wizard.name=automationCopyName(base);
    // Aynı kural evde iki kez çalışmasın: kopya kapalı doğar, kullanıcı hazır olunca açar.
    wizard.enabled=false;
    wizard.touched=true;
    // Düzenleme kipi: kopya ad adımında açılır, oradan cihazlara da dokunulabilir.
    wizard.stage="name";
    renderAutomationWizard();
    showToast(t("automationDuplicateReady"));
  }
  // Listedeki "⋯" penceresinden: kimlik pencere kapanmadan okunur, kapanışta bağlam silinir.
  function duplicateAutomation(){
    const id=state.automationContext;
    $("#automationActionDialog").close();
    if(!id||!state.automations.some(item=>item.id===id))return;
    openAutomationWizard(id);
    automationDraftToCopy();
  }
  // Düzenleme ekranının kendi içinden aynı iş; yalnız kayıtlı bir kuralda anlamlıdır.
  function duplicateAutomationDraft(){
    const wizard=state.automationWizard;
    if(!wizard||!wizard.id)return;
    automationDraftToCopy();
  }
  const automationTriggerChoices=[
    {kind:"manual",glyph:"▶",label:"automationTriggerManual",ready:true},
    {kind:"time",glyph:"🕐",label:"automationTriggerTime",ready:true},
    {kind:"sun",glyph:"🌅",label:"automationTriggerSun",ready:true},
    {kind:"button",glyph:"🔘",label:"automationTriggerButton",ready:true},
    {kind:"sensor",glyph:"↕",label:"automationTriggerSensor",ready:true},
    {kind:"deviceState",glyph:"💡",label:"automationTriggerDeviceState",ready:true}
  ];
  const automationDeviceKinds=["button","sensor","deviceState"];
  const automationNewId=()=>`${Date.now().toString(36)}${Math.random().toString(36).slice(2,8)}`.toLowerCase();
  const automationMaxNameLength=64;
  // Kopyanın adı i18n'den gelir. Sunucu yalnız **kimliği** tekil tutar, adı değil — aynı ad iki kez
  // oluşabilirdi. Ad zaten kullanılıyorsa sıra sayısı eklenir; iki kayıt listede ayırt edilir.
  const automationCopyName=name=>{
    const base=String(name||"").trim();
    const taken=new Set(state.automations.map(item=>String(item.name||"").trim()));
    const fit=text=>String(text).slice(0,automationMaxNameLength).trim();
    let candidate=fit(t("automationCopyName",{name:base}));
    for(let index=2;taken.has(candidate)&&index<100;index+=1){
      candidate=fit(t("automationCopyNameNumbered",{name:base,count:index}));
    }
    return candidate;
  };
  // Kural saf JSON'dur; kopya hiçbir alanı özgün kayıtla paylaşmasın diye tam kopya alınır.
  const automationDeepCopy=value=>JSON.parse(JSON.stringify(value));
  const automationTimeText=wizard=>`${String(wizard.hour).padStart(2,"0")}:${String(wizard.minute).padStart(2,"0")}`;
  const maxAutomationSunOffset=240;
  // §9.1 — güneş yolu her zaman iki olayı taşır: kullanıcıya "batış mı doğuş mu" diye sorulmaz,
  // ikisi de sorulur ve istemediği yöne "Bir şey yapma" der. Kural anahtar akışındaki eşleme
  // formunu kullanır: iki `sun` tetikleyicisi + olay adına eşlenen eylemler.
  const automationSunBoth=wizard=>wizard?.triggerKind==="sun";
  // İki olay ayrı kaydırma ve ayrı gün seçimi taşır.
  const automationSunPart=(wizard,event)=>event==="sunrise"
    ?{offset:wizard.sunriseOffset,days:wizard.sunriseDays}
    :{offset:wizard.sunOffset,days:wizard.days};
  // Anahtar/priz yolu eşleme formuyla kurulur: durum tetikleyicide değil, eylemlerin `when` alanında.
  const automationMappingMode=wizard=>wizard?.triggerKind==="deviceState"||automationSunBoth(wizard);
  // Sayısal eşik yolu: `equals` yerine `above`/`below` yazılır; ikisi birlikte olamaz (sunucu kuralı).
  const automationThresholdActive=wizard=>wizard?.triggerKind==="sensor"&&Boolean(wizard?.triggerNumeric);
  // §2.1 — süreli tetikleyici: hedefi olan durum tetikleyicilerine biner. Kapalıyken alan hiç
  // yazılmaz, eski davranış aynen kalır.
  const automationTrigForApply=(wizard,trigger)=>{
    if(!automationTrigForEligible(wizard))return trigger;
    const seconds=Math.round(Number(wizard.triggerForSeconds));
    if(Number.isFinite(seconds)&&seconds>0)trigger.forSeconds=Math.min(seconds,maxAutomationCondForSeconds);
    return trigger;
  };
  const automationThresholdTrigger=wizard=>{
    const trigger={type:"deviceState",deviceId:wizard.triggerDeviceId,property:wizard.triggerProperty};
    if(wizard.thresholdDir==="below")trigger.below=wizard.thresholdValue;
    else trigger.above=wizard.thresholdValue;
    return automationTrigForApply(wizard,trigger);
  };
  const automationSunTriggerFor=(wizard,event)=>{
    const part=automationSunPart(wizard,event);
    return{type:"sun",event,offsetMinutes:part.offset,days:[...part.days].sort((left,right)=>left-right)};
  };
  const automationWizardTrigger=wizard=>wizard.triggerKind==="manual"
    ?null
    :wizard.triggerKind==="button"
    ?{type:"deviceAction",deviceId:wizard.triggerDeviceId,action:wizard.triggerAction}
    :wizard.triggerKind==="sun"
    ?automationSunTriggerFor(wizard,"sunset")
    :automationThresholdActive(wizard)
    ?automationThresholdTrigger(wizard)
    :automationMappingMode(wizard)
    ?{type:"deviceState",deviceId:wizard.triggerDeviceId,property:wizard.triggerProperty}
    :automationDeviceKinds.includes(wizard.triggerKind)
    // Hedefsiz değer tetikleyicisinde `equals` hiç yazılmaz: kural değer her değiştiğinde çalışır.
    ?automationTrigForApply(wizard,{type:"deviceState",deviceId:wizard.triggerDeviceId,property:wizard.triggerProperty,
      ...(wizard.triggerEquals===null||wizard.triggerEquals===undefined?{}:{equals:wizard.triggerEquals})})
    :{type:"time",at:automationTimeText(wizard),days:[...wizard.days].sort((left,right)=>left-right)};
  // Kaydedilecek tetikleyici listesi: güneşin iki olaylı yolunda iki satır, öbür yollarda tek.
  const automationWizardTriggers=wizard=>wizard.triggerKind==="manual"
    ?[]
    :automationSunBoth(wizard)
    ?[automationSunTriggerFor(wizard,"sunset"),automationSunTriggerFor(wizard,"sunrise")]
    :[automationWizardTrigger(wizard)];
  // Motor eylem başına sınırlıdır (`maxAutomationActions` = 8). Eşleme yolunda bir hedef iki eylem
  // üretir (açılınca + kapanınca), o yüzden orada hedef sınırı yarıya iner.
  const automationMaxActions=8;
  // Tetikleyiciden sonraki bekleme de bir eylem satırı yazar: seçiliyse hedef bütçesi bir azalır,
  // yoksa kaydederken sunucu sınırına takılırdı.
  const automationMaxTargets=wizard=>{
    const budget=automationMaxActions-(automationWaitSeconds(wizard)>0?1:0);
    return automationMappingMode(wizard)?Math.floor(budget/2):budget;
  };
  // Hedef satırı ad/cümle üreten yardımcılara eylem biçiminde verilir; kanonik bağ IEEE + özellik.
  // Hedef satırı üç yeni türü de taşır: cihaz kanalı, bekleme, grup ve sahne. Varsayılan "device".
  const automationTargetKind=target=>target?.kind||"device";
  const automationTargetRef=target=>target&&automationTargetKind(target)==="device"
    ?{type:"device",deviceId:target.deviceId,property:target.property,controlId:target.controlId,
      value:target.value??null,follow:target.follow||undefined}
    :null;
  const automationTargetMode=target=>automationTargetKind(target)==="device"
    ?automationActionMode(automationTargetRef(target))
    :"none";
  const automationTargetControl=target=>{
    if(automationTargetKind(target)!=="device")return null;
    const device=state.devices.find(item=>item.id===target?.deviceId)||null;
    return(device?.controls||[]).find(control=>control.property===target?.property)||null;
  };
  const automationFirstTarget=wizard=>wizard.targets.find(target=>automationTargetKind(target)==="device")||wizard.targets[0]||null;
  // Grup ve sahne adları grup listesinden gelir; kayıp grupta kimlik yazılır, satır boş kalmaz.
  const automationGroup=groupId=>(state.zigbeeGroups||[]).find(group=>group.id===groupId)||null;
  const automationGroupName=groupId=>automationGroup(groupId)?.name||String(groupId||"");
  const automationSceneName=(groupId,sceneId)=>{
    const scene=(automationGroup(groupId)?.scenes||[]).find(item=>item.id===sceneId);
    return scene?scene.name:t("automationSceneNumber",{scene:sceneId});
  };
  const automationSecondsText=seconds=>seconds>=60&&seconds%60===0
    ?automationDurationText(seconds)
    :t("automationSecondsUnit",{count:seconds});
  const automationTargetName=target=>{
    const kind=automationTargetKind(target);
    if(kind==="delay")return t("automationActionDelayName",{duration:automationSecondsText(target.seconds)});
    if(kind==="group")return automationGroupName(target.groupId);
    if(kind==="scene")return automationGroupName(target.groupId);
    return automationActionName(automationTargetRef(target));
  };
  const automationDefaultName=(trigger,target)=>(!trigger
    ?t("automationDefaultNameManual",{device:automationTargetName(target)})
    :trigger.type==="time"
    ?t("automationDefaultName",{device:automationTargetName(target),time:trigger.at})
    :trigger.type==="sun"
    ?t("automationDefaultName",{device:automationTargetName(target),time:t(trigger.event==="sunrise"?"automationSunriseWord":"automationSunsetWord")})
    :t("automationDefaultNameEvent",{device:automationTriggerDeviceName(trigger),target:automationTargetName(target)})).slice(0,64);
  const automationWizardName=wizard=>wizard.name.trim()
    ||(automationSunBoth(wizard)
      ?t("automationDefaultNameSunBoth",{device:automationTargetName(automationFirstTarget(wizard))}).slice(0,64)
      :automationDefaultName(automationWizardTrigger(wizard),automationFirstTarget(wizard)));
  const automationWizardMapSentence=wizard=>{
    const target=automationFirstTarget(wizard);
    const name=automationActionName(automationTargetRef(target));
    const onMode=target?.mapOn||"none";
    const offMode=target?.mapOff||"none";
    return automationSunBoth(wizard)
      ?automationSunMapSentence(name,onMode,offMode)
      :automationMapSentence(automationTriggerLabelName(wizard),name,onMode,offMode);
  };
  const automationWizardSentence=wizard=>wizard.triggerKind==="manual"
    ?t("automationManualSentence",{device:automationTargetName(automationFirstTarget(wizard))})
    :automationMappingMode(wizard)
    ?automationWizardMapSentence(wizard)
    :automationSentence(automationWizardTrigger(wizard),automationTargetRef(automationFirstTarget(wizard)));
  // Eşleme formundan kaydedilecek eylemler: her yön için uygun `when` taşıyan bir eylem.
  const automationMapActionsFor=(wizard,target)=>{
    const control=automationTargetControl(target);
    if(!control)return[];
    const build=(mode,on)=>({
      type:"device",
      deviceId:target.deviceId,
      property:control.property,
      controlId:control.id,
      value:mode==="toggle"?control.valueToggle:automationControlValue(control,mode==="on"),
      // §9.1 — güneş yolunda eşleşme değeri olay adıdır; anahtar yolunda kanalın açık/kapalı değeri.
      when:{equals:automationSunBoth(wizard)
        ?(on?"sunset":"sunrise")
        :automationStateValue(wizard.triggerDeviceId,wizard.triggerProperty,on)}
    });
    const actions=[];
    if(target.mapOn!=="none")actions.push(build(target.mapOn,true));
    if(target.mapOff!=="none")actions.push(build(target.mapOff,false));
    return actions;
  };
  // Kayıtlı kuralı forma geri okur: yeni `when`li kayıtlar doğrudan (kanal başına bir hedef satırı),
  // eski `equals`li tek eylemli kayıtlar tek yönlü eşleme olarak. Çözülemezse null döner.
  const automationMapSeed=automation=>{
    const map=automationMapView(automation);
    const asTarget=(action,onMode,offMode)=>({
      deviceId:action.deviceId,property:action.property,controlId:action.controlId,
      mapOn:onMode,mapOff:offMode
    });
    if(map)return map.targets.map(entry=>asTarget(entry.target,entry.onMode,entry.offMode));
    // §9.1 — tek `sun` tetikleyicisi taşıyan eski kural: eylemleri kendi olayının yönüne oturur,
    // öbür yön "bir şey yapma" olur. Kural düzenlemeye kayıpsız açılır.
    const slots=automationSunSlots(automation);
    if(slots&&!(slots.sunset&&slots.sunrise)){
      const actions=(automation?.actions||[]).filter(Boolean);
      if(!actions.length||actions.some(action=>action.when||(action.type&&action.type!=="device")))return null;
      const sunset=Boolean(slots.sunset);
      return actions.map(action=>asTarget(action,sunset?automationActionMode(action):"none",sunset?"none":automationActionMode(action)));
    }
    const trigger=automation?.triggers?.[0];
    const action=automation?.actions?.[0];
    if(trigger?.type!=="deviceState"||!action||automation.actions.length!==1||action.when)return null;
    // §2.1 — süreli tetikleyici tek yönlü eşleme sayılmaz; yoksa düzenlemede süre sessizce düşerdi.
    if(automationTriggerHeldSeconds(trigger)>0)return null;
    const direction=automationWhenDirection(trigger,{equals:trigger.equals});
    if(!direction)return null;
    const mode=automationActionMode(action);
    return[asTarget(action,direction==="on"?mode:"none",direction==="off"?mode:"none")];
  };
  const automationTriggerHasThreshold=trigger=>trigger?.type==="deviceState"
    &&(trigger.above!==undefined&&trigger.above!==null||trigger.below!==undefined&&trigger.below!==null);
  const automationTriggerKind=(trigger,automation)=>!trigger?null
    :trigger.type==="deviceAction"?"button"
    :trigger.type==="sun"?"sun"
    :trigger.type==="deviceState"
    // `equals` taşımayan her değişim tetikleyicisi anahtar eşlemesi değildir. Ayrım özellik adına
    // göre değil, kaydın yapısına göre yapılır: yeni eşlemede eylemler `when` taşır; eski tek yönlü
    // eşleme ise gerçekten yazılabilir bir anahtar kanalına bağlıdır.
    ?(automationMapView(automation)?.kind==="device"
      ||(trigger.equals!==undefined&&trigger.equals!==null
        &&automationStateControls(automationTriggerDevice(trigger)).some(control=>control.property===trigger.property)
        &&automationMapSeed(automation))?"deviceState":"sensor")
    :"time";
  // Akışın adımları. Dört bölüm: NE ZAMAN (kind…trigThreshold), KOŞUL (cond…), NE YAPSIN
  // (target…scene), SONRASI (autoOff, name). Ne zaman bölümü varsayılandır.
  const automationCondStages=["cond","condTime","condDevice","condState"];
  const automationThenStages=["wait","target","action","map","delay","group","groupAction","scene","sceneId"];
  const automationAfterStages=["autoOff","name"];
  const automationStageGroup=stage=>automationCondStages.includes(stage)?"cond"
    :automationThenStages.includes(stage)?"then"
    :automationAfterStages.includes(stage)?"after":"when";
  /* Dört sabit durak, içerideki teknik alt adımları kullanıcıya yüklemez. Örneğin cihaz ve
     kanal seçimi ayrı ekranlar olsa da ikisi de “Eylemler” altında kalır. Tamamlanmış durağa
     dokunmak mevcut özet satırlarıyla aynı şekilde o bölümü yeniden açar. */
  const automationProgressIndex=wizard=>wizard?.stage==="name"?3
    :wizard?.stage==="autoOff"?2
    :automationStageGroup(wizard?.stage)==="then"?1:0;
  function renderAutomationProgress(wizard,paths){
    const progress=$("#automationProgress");
    if(!progress)return;
    progress.hidden=paths;
    if(paths){progress.innerHTML="";return}
    const current=automationProgressIndex(wizard);
    const steps=[
      {label:"automationProgressTrigger",stage:automationTriggerEditStage(wizard),available:true},
      {label:"automationProgressActions",stage:"target",available:automationTriggerReady(wizard)},
      {label:"automationProgressAfter",stage:"autoOff",available:automationAutoOffAvailable(wizard)},
      {label:"automationProgressSave",stage:"name",available:automationWizardReady(wizard)}
    ];
    progress.innerHTML=steps.map((step,index)=>{
      const active=index===current;
      const skipped=index===2&&!step.available&&current>index;
      const done=index<current&&!skipped;
      const reachable=step.available&&index<=current;
      const classes=`automation-progress-step${active?" is-current":""}${done?" is-done":""}${skipped?" is-skipped":""}`;
      const mark=done?"✓":skipped?"–":String(index+1);
      return`<button class="${classes}" type="button" data-automation-stage="${step.stage}"${reachable?"":" disabled"}${active?' aria-current="step"':""}><span class="automation-progress-mark" aria-hidden="true">${mark}</span><span>${esc(t(step.label))}</span></button>`;
    }).join("");
  }
  // Kayıtlı eylemi sihirbaz satırına çevirir; hiçbir tür sessizce düşmez, hepsi düzenlenebilir.
  const automationActionToTarget=action=>{
    if(action?.type==="delay")return{kind:"delay",seconds:action.seconds};
    if(action?.type==="group")return{kind:"group",groupId:action.groupId,property:action.property,value:action.value};
    if(action?.type==="scene")return{kind:"scene",groupId:action.groupId,sceneId:action.sceneId};
    return{
      kind:"device",deviceId:action.deviceId,property:action.property,controlId:action.controlId,
      value:action.value,mapOn:"on",mapOff:"off",
      // Alan yalnız izleyen kayıtta taşınır; öbür satırların şekli bugünküyle birebir aynı kalır.
      ...(action.follow?{follow:{mode:action.follow.mode}}:{})
    };
  };
  function openAutomationWizard(id=null){
    const existing=id?state.automations.find(item=>item.id===id):null;
    const trigger=existing?.triggers?.[0]||null;
    const savedActions=(existing?.actions||[]).filter(Boolean);
    // Tetikleyiciden sonraki bekleme kuralın **ilk** eylemidir ve kendi ara adımına çekilir.
    // Listenin ortasındaki beklemeler eylemler arası duraklardır: yerlerinde kalır, satır olarak
    // görünmeye devam eder — düzenlemeye açmak veri kaybettirmez.
    const leadWait=savedActions[0]?.type==="delay"?savedActions[0]:null;
    const actions=leadWait?savedActions.slice(1):savedActions;
    // Eşleme tohumu da beklemesiz listeden okunur; yoksa baştaki bekleme formu bozardı.
    const seedSource=leadWait?{...existing,actions}:existing;
    const timed=trigger?.type==="time";
    const sunny=trigger?.type==="sun";
    // §9.1 — güneş kuralı eşleme formuna geri açılır: iki olaylı kayıt olduğu gibi, tek olaylı
    // kayıt da o olayın yönü dolu, öbür yön "bir şey yapma" olarak.
    const sunSlots=sunny?automationSunSlots(existing):null;
    const sunSeed=sunSlots?automationMapSeed(seedSource):null;
    // Eşleme tohumu yalnız anahtar/priz yolunda geçerlidir: o satırlar değer taşımaz, yön taşır.
    // Sensör kuralı da tek eylemli `deviceState` göründüğü için buraya düşüyordu ve hedefin
    // `value` alanı silinerek eylem tersine dönüyordu — ayrım tetikleyici türünden yapılır.
    const seed=sunSeed||(existing&&automationTriggerKind(trigger,seedSource)==="deviceState"?automationMapSeed(seedSource):null);
    const sunset=sunSlots?.sunset||null;
    const sunrise=sunSlots?.sunrise||null;
    const autoOff=actions.find(action=>action.autoOff)?.autoOff||null;
    const numeric=automationTriggerHasThreshold(trigger);
    state.automationWizard={
      // Yeni kural yol seçimiyle başlar; düzenlemede akış tamamlanmış gelir ve ada odaklanır.
      stage:existing?"name":"path",
      id:existing?.id||null,
      enabled:existing?existing.enabled!==false:true,
      triggerKind:existing?.manual===true?"manual":automationTriggerKind(trigger,seedSource),
      hour:timed?Number(trigger.at.slice(0,2)):19,
      minute:timed?Number(trigger.at.slice(3,5)):0,
      days:timed?[...(trigger.days||automationWeekDays)]
        :sunset?[...(sunset.days||automationWeekDays)]:[...automationWeekDays],
      // Güneş yolu: iki an, her biri kendi kaydırması ve günleriyle. `sunEvent` hangi anın
      // ayarlandığını söyler, kaydedilen kuralı değil — kayıt her zaman iki olayı taşır.
      sunEvent:"sunset",
      sunOffset:sunset?sunset.offsetMinutes:0,
      sunriseOffset:sunrise?sunrise.offsetMinutes:0,
      sunriseDays:[...((sunrise?.days)||automationWeekDays)],
      autoOffTouched:Boolean(existing),
      autoOffCustom:false,
      triggerDeviceId:trigger?.deviceId||null,
      triggerAction:trigger?.type==="deviceAction"?trigger.action:null,
      triggerProperty:trigger?.type==="deviceState"?trigger.property:null,
      triggerEquals:trigger?.type==="deviceState"&&!numeric?(trigger.equals??null):null,
      // Sayısal eşik: yalnız sayısal bir özellik seçildiyse sorulur, öbür özelliklerde hiç görünmez.
      triggerNumeric:numeric,
      thresholdDir:numeric&&trigger.below!==undefined&&trigger.below!==null&&(trigger.above===undefined||trigger.above===null)?"below":"above",
      thresholdValue:numeric?Number(trigger.above??trigger.below??0):0,
      // §2.1 — "… ve şu kadar süredir böyleyse". Alanı olmayan eski kurallarda kapalıdır ve
      // düzenlemeye açılınca aynı ekran çıkar: davranış birebir korunur.
      triggerForSeconds:trigger?.type==="deviceState"&&Number.isFinite(trigger.forSeconds)&&trigger.forSeconds>0?trigger.forSeconds:null,
      // Tetikleyici ile ilk eylem arasındaki bekleme (ara adım). 0 = hemen çalışsın.
      triggerWaitSeconds:leadWait&&Number.isFinite(leadWait.seconds)?Math.min(maxAutomationDelaySeconds,Math.max(0,Math.round(leadWait.seconds))):0,
      // Bekleme opsiyoneldir: sayaç kendiliğinden açılmaz, kullanıcı + ile açar. Kayıtlı kuralda
      // süre zaten varsa açık gelir (türetilir), bu alan yalnız elle açmayı taşır.
      waitOpen:false,
      // Koşullar (§5.3): boş liste "her zaman çalışsın" demektir.
      // Tam kopya: iç içe alanlar (saat aralığının uçları gibi) kayıtla paylaşılmaz, düzenleme
      // taslağı listedeki kuralı sessizce değiştirmez. Çoğaltmada da kopya kendi verisiyle açılır.
      conditions:(existing?.conditions||[]).map(condition=>automationDeepCopy(condition)),
      // §2.4 — kural başına tek anahtar: hepsi mi, herhangi biri mi. Alan yoksa "hepsi".
      conditionMode:existing?.conditionMode==="any"?"any":"all",
      draftCondition:null,
      draftConditionIndex:null,
      condQuery:"",
      condTab:"all",
      // Çoklu hedef: şema `actions[]` alır, motor hepsini sırayla çalıştırır — her eylem bir satır.
      targets:seed||actions.map(automationActionToTarget),
      // Seçilmiş ama henüz kesinleşmemiş hedef; kanal ve eşleme cevapları burada tutulur.
      draftTargetId:null,
      draftProperty:null,
      draftControlId:null,
      // Ayarlanmakta olan değer kumandası (parlaklık / ışık sıcaklığı / renk) ve ham değeri.
      // Boşsa eylem aç/kapattır — varsayılan yol değişmedi.
      draftValueTarget:null,
      draftValue:null,
      draftMapOn:"on",
      draftMapOff:"off",
      draftDelaySeconds:10,
      draftDelayCustom:false,
      draftGroupId:null,
      draftSceneId:null,
      draftTargetIndex:null,
      // §9 — "sonra kapat" açan her eyleme yazılır; alanı olmayan eski kurallarda kapalıdır.
      autoOffMode:autoOff?autoOff.mode:"none",
      autoOffMinutes:autoOff?.mode==="after"?Math.max(1,Math.round(autoOff.seconds/60)):5,
      autoOffIdleMinutes:autoOff?.mode==="idle"?Math.max(0,Math.round(autoOff.seconds/60)):0,
      // Seçici durumu adım başına ayrı: sekme + arama.
      triggerTab:"all",
      targetTab:"all",
      triggerQuery:"",
      targetQuery:"",
      name:existing?.name||"",
      // Az önce tamamlanan düğüm; bir kez yumuşak yerleşme animasyonu alır.
      fresh:null,
      // Kullanıcı sihirbaza dokundu mu: kapatma ikonu veri kaybı uyarısını buna göre sorar.
      touched:false,
      // Açık adımın seçenek listesi açık mı: seçim yapılınca tek satıra daralır (§2.1).
      pickerOpen:true
    };
    const dialog=$("#automationDialog");
    if(!dialog.open)dialog.showModal();
    automationAnimate=false;
    renderAutomationWizard();
  }
  // ————— akış parçaları: satır dili. Çerçeveli buton yok; ikon + metin satırı ve yuvarlak pill var.
  // Hareket azaltma tercihi dışarıdaki yardımcıdan gelir; yoksa animasyon yine de çalışır.
  const automationReducedMotion=()=>typeof reducedMotion==="function"&&reducedMotion()===true;
  const automationJoin=(...parts)=>parts.filter(Boolean).join(" · ");
  const automationStrong=text=>`<strong>${esc(text)}</strong>`;
  const automationPillKeys={
    on:"automationPillOn",off:"automationPillOff",toggle:"automationPillToggle",none:"automationPillNothing",
    level:"automationPillBrightness",temperature:"automationPillWarmth",color:"automationPillColor",value:"automationPillValue",
    followRatio:"automationPillFollowRatio",followColor:"automationPillFollowColor"
  };
  // Değer pill'i değeri de taşır ("Parlaklık %40"); şablonun tamamı tek anahtardır.
  const automationPillHtml=(mode,values)=>`<span class="automation-pill act-${mode}">${esc(t(automationPillKeys[mode]||automationPillKeys.on,values||{}))}</span>`;
  const automationTargetPillHtml=target=>{
    const mode=automationTargetMode(target);
    if(!["level","temperature","color","value"].includes(mode))return automationPillHtml(mode);
    return automationPillHtml(mode,{value:automationValueText(automationTargetControl(target),target?.value)});
  };
  // ————— eşleme çifti: "kaynak → sonuç". Satırda iki ayrı öbek durur, çünkü kural iki ayrı
  // eşlemedir; tek cümle gibi akmasın diye her çift kendi kutusundadır.
  // Çevirinin tamamı tek anahtardır (parça birleştirme YOK): kelime sırası tr/en'de farklı, o yüzden
  // ok bile şablonun içinde `{arrow}` olarak durur — yerini çeviri seçer, kod değil.
  // Ok yalnız süstür: `aria-hidden` ile okuyucudan gizlenir, çiftin tam metni `aria-label`da kalır.
  const automationMapPairKeys={on:"On",off:"Off",sunset:"Sunset",sunrise:"Sunrise",toggle:"Toggle",none:"Nothing"};
  const automationMapPairKey=(from,mode)=>`automationMapPair${automationMapPairKeys[from]||"On"}${automationMapPairKeys[mode]||"On"}`;
  const automationMapPairHtml=(from,mode)=>{
    // Şablon ayracı: `{arrow}` yerine görünmez bir işaret koyup ikiye böleriz. Çeviri oku
    // taşımıyorsa (ya da anahtar eksikse) metnin tamamı sonuç öbeği olur, uydurma ok çizilmez.
    const halves=t(automationMapPairKey(from,mode),{arrow:"\u001f"}).split("\u001f");
    const source=halves.length>1?halves[0].trim():"";
    const result=(halves.length>1?halves[1]:halves[0]).trim();
    const label=[source,result].filter(Boolean).join(" ");
    const lead=source?`<span class="automation-map-from">${esc(source)}</span><span class="automation-map-arrow" aria-hidden="true">→</span>`:"";
    return`<span class="automation-map-pair" role="img" aria-label="${esc(label)}">${lead}<span class="automation-pill act-${mode}">${esc(result)}</span></span>`;
  };
  // Bölüm boşken/varsayılandayken satır bir şeyi anlatmaz, teklif eder: düğme "Ekle" der.
  // Olmayan bir koşulu ya da beklemeyi "değiştirmek" tuhaftı. Doluysa dil yine "Değiştir".
  // `action` = {empty, aria}: aria etiketini çağıran verir, neyin eklendiği/değiştiği duyulsun.
  const automationSummaryAction=(empty,addKey,changeKey)=>({empty:Boolean(empty),aria:t(empty?addKey:changeKey)});
  const automationSummaryHtml=(line,hook,quiet,removeHook,action)=>{
    const empty=Boolean(action&&action.empty);
    const aria=action&&action.aria?` aria-label="${esc(action.aria)}"`:"";
    return`<div class="automation-summary${quiet?" is-quiet":""}${empty?" is-empty":""}"><button class="automation-summary-main" type="button" ${hook}${aria}><span class="automation-line">${line}</span><span class="automation-change">${esc(t(empty?"add":"automationChange"))}</span></button>${removeHook?`<button class="automation-summary-remove" type="button" ${removeHook} aria-label="${esc(t("automationRemove"))}" title="${esc(t("automationRemove"))}"><span aria-hidden="true">✕</span></button>`:""}</div>`;
  };
  // Adım içinde seçim yapılınca uzun liste yerine tek satır durur: tamamlanmış düğümlerdeki
  // aynı özet bileşeni, aynı "Değiştir" metniyle. Basınca liste geri açılır, seçim korunur.
  // Adım kapanmadığı için altındaki süre satırı ("… ve şu kadar süredir böyleyse") görünür kalır.
  const automationPickedRowHtml=label=>automationSummaryHtml(automationStrong(label),'data-automation-reopen="1"');
  const automationOptHtml=option=>`<button class="automation-opt${option.on?" is-on":""}" type="button" ${option.hook||""}${option.disabled?' disabled aria-disabled="true"':""} aria-pressed="${Boolean(option.on)}"><span class="automation-opt-glyph" aria-hidden="true">${option.glyph||"›"}</span><span class="automation-opt-body"><span class="automation-opt-title">${esc(option.title)}</span>${option.sub?`<span class="automation-opt-sub">${esc(option.sub)}</span>`:""}</span>${option.badge?`<span class="automation-badge">${esc(option.badge)}</span>`:`<span class="automation-tick" aria-hidden="true">✓</span>`}</button>`;
  const automationOptionsHtml=options=>`<div class="automation-options">${options.map(automationOptHtml).join("")}</div>`;
  const automationAddHtml=(label,hook)=>`<button class="automation-add" type="button" ${hook}><span class="automation-plus" aria-hidden="true">+</span><span>${esc(label)}</span></button>`;
  // Eylem pill'i: ince renkli kenarlık + kendi renginde metin. Kırmızı eylem rengi değildir —
  // panelde `--danger` hata/uyarı demektir, o yüzden Kapat sakin mürekkep tonunda durur.
  const automationChoiceHtml=(mode,labelKey,active,hook)=>`<button class="automation-choice act-${mode}${active?" active":""}" type="button" ${hook} aria-pressed="${Boolean(active)}">${esc(t(labelKey))}</button>`;
  const automationChoiceTextHtml=(mode,label,active,hook)=>`<button class="automation-choice act-${mode}${active?" active":""}" type="button" ${hook} aria-pressed="${Boolean(active)}">${esc(label)}</button>`;
  function automationTriggerChoicesHtml(wizard){
    return automationOptionsHtml(automationTriggerChoices.map(entry=>({
      glyph:entry.glyph,
      title:t(entry.label),
      on:wizard.triggerKind===entry.kind,
      disabled:!entry.ready,
      badge:entry.ready?null:t("comingSoon"),
      hook:`data-automation-trigger="${entry.kind}"`
    })));
  }
  // Gün çipleri tek bileşen: saat, güneş ve saat aralığı koşulu aynı satırı kullanır.
  const automationDaysHtml=(days,hook)=>{
    const everyDay=automationEveryDay(days);
    const chips=[
      `<button class="automation-day${everyDay?" active":""}" type="button" ${hook}="all" aria-pressed="${everyDay}">${t("automationEveryDayChip")}</button>`,
      ...automationWeekDays.map(day=>{
        const active=!everyDay&&days.includes(day);
        return`<button class="automation-day${active?" active":""}" type="button" ${hook}="${day}" aria-pressed="${active}">${esc(automationDayLabel(day))}</button>`;
      })
    ].join("");
    return`<div class="automation-days" role="group" aria-label="${esc(t("automationDaysLabel"))}">${chips}</div>`;
  };
  const automationTimeUnitHtml=(name,value,amount,upKey,downKey,hook)=>`<div class="automation-time-unit"><button class="automation-time-step" type="button" ${hook}="${name}:${amount}" aria-label="${esc(t(upKey))}">+</button><span class="automation-time-value">${String(value).padStart(2,"0")}</span><button class="automation-time-step" type="button" ${hook}="${name}:-${amount}" aria-label="${esc(t(downKey))}">−</button></div>`;
  const automationClockHtml=(hour,minute,hook,label)=>`<div class="automation-time" role="group" aria-label="${esc(t(label))}">${automationTimeUnitHtml("hour",hour,1,"automationHourUp","automationHourDown",hook)}<span class="automation-time-colon" aria-hidden="true">:</span>${automationTimeUnitHtml("minute",minute,5,"automationMinuteUp","automationMinuteDown",hook)}</div>`;
  function automationTimeHtml(wizard){
    return`${automationClockHtml(wizard.hour,wizard.minute,"data-automation-time","automationTimeLabel")}${automationDaysHtml(wizard.days,"data-automation-day")}`;
  }
  // ————— süre sayacı. Hazır çip satırları yerine tek dil: "−  s:dd  +". Değer hep dakikadır.
  // Güneş kaydırması işaretlidir (eksi = önce, artı = sonra, 0:00 tam o an); süreler işaretsizdir.
  // Adım sıfırın çevresinde ince, uzaklaştıkça kabalaşır: bir dokunuş bir dakika seçer, uzun
  // aralıklar da az dokunuşla yakalanır. Kadran dili saat seçiciyle bilerek aynı.
  const automationCounterStep=magnitude=>magnitude<5?1:magnitude<60?5:magnitude<240?15:30;
  const automationCounterNext=(minutes,direction)=>{
    const value=Math.round(Number(minutes)||0);
    const away=direction>0===value>=0;
    return value+direction*automationCounterStep(away?Math.abs(value):Math.abs(value)-1);
  };
  const automationCounterText=(minutes,signed)=>{
    const value=Math.round(Number(minutes)||0);
    const total=Math.abs(value);
    const clock=`${Math.floor(total/60)}:${String(total%60).padStart(2,"0")}`;
    return!signed||value===0?clock:`${value<0?"−":"+"}${clock}`;
  };
  const automationCounterHtml=(hook,minutes,signed,labelKey,downKey,upKey)=>{
    const step=(direction,key,glyph)=>`<button class="automation-time-step" type="button" ${hook}="${direction}" aria-label="${esc(t(key))}">${glyph}</button>`;
    return`<div class="automation-counter" role="group" aria-label="${esc(t(labelKey))}">${step(-1,downKey,"−")}<span class="automation-counter-value">${esc(automationCounterText(minutes,signed))}</span>${step(1,upKey,"+")}</div>`;
  };
  // ————— güneş tetikleyicisi. Konum yoksa satır pasif kalır ve sebebi sunucudan gelen kodla yazılır.
  const automationSunReason=()=>{
    const sun=state.automationSun;
    if(state.homeLocation&&!sun?.reason)return null;
    return sun?.reason||"locationMissing";
  };
  // Hangi anın ayarlandığı `sunEvent`te durur; kural her zaman iki anı da taşır.
  const automationSunEditing=wizard=>wizard?.sunEvent==="sunrise"?"sunrise":"sunset";
  function automationSunHtml(wizard){
    const reason=automationSunReason();
    const editing=automationSunEditing(wizard);
    const part=automationSunPart(wizard,editing);
    // Olay seçimi yok: iki an da kurulur, istenmeyen yöne eşleme formunda "Bir şey yapma" denir.
    const picker=`<p class="automation-part-name">${esc(t("automationSunPartTitle"))}</p><div class="automation-choices">${automationChoiceHtml("on","automationSunset",editing==="sunset",'data-automation-sun-edit="sunset"')}${automationChoiceHtml("off","automationSunrise",editing==="sunrise",'data-automation-sun-edit="sunrise"')}</div>`;
    // Bugünün saatleri seçim satırından kalktı; bilgi kaybolmasın diye ipucu satırında durur.
    const today=state.automationSun?.sunset&&state.automationSun?.sunrise
      ?`<p class="automation-hint">${esc(t("automationSunToday",{sunset:state.automationSun.sunset,sunrise:state.automationSun.sunrise}))}</p>`
      :"";
    const counter=automationCounterHtml("data-automation-sun-step",part.offset,true,"automationSunOffsetLabel","automationSunOffsetDown","automationSunOffsetUp");
    // Sessiz mekanizma yok: konum eksikse sebebi ve çıkış yolu aynı ekranda durur.
    const blocked=reason
      ?`<div class="automation-alt"><p>${esc(automationReasonText(reason))}</p>${automationAddHtml(t("automationOpenLocation"),'data-automation-open-location="1"')}</div>`
      :"";
    return`${blocked}${picker}${today}<p class="automation-part-name">${esc(t("automationSunOffsetTitle"))}</p>${counter}<p class="automation-hint">${esc(t("automationSunOffsetHint"))}</p>${automationDaysHtml(part.days,"data-automation-day")}`;
  }
  // ————— sayısal eşik. Yalnız sayısal bir özellik seçildiyse görünür.
  const automationNumericProperties=device=>{
    return automationCapabilities(device)
      .filter(item=>isAutomationNumericControl(item.control)||typeof item.value==="number")
      .filter(item=>!Object.prototype.hasOwnProperty.call(automationSensorEvents,item.property))
      .map(item=>item.property);
  };
  const automationPropertyLabel=(device,property)=>{
    const capability=automationCapability(device,property);
    if(capability)return capability.name;
    const key=`automationProperty_${property}`;
    const label=t(key);
    return label===key?property:label;
  };
  const automationPropertyUnit=(device,property)=>{
    return automationCapability(device,property)?.unit||"";
  };
  /* Adım cihaz/ölçüm tablosuna dayanmaz — öyle bir tablo tutulamaz, her cihaz kendi biriminde
     kendi büyüklüğünde konuşur. Cihaz kendi kumandasında bir `step` bildiriyorsa o kullanılır;
     bildirmiyorsa adım **o an okunan değerin büyüklüğünden** türetilir. Ölçü şu: bir uçtan öbür
     uca gitmek yüzlerce tık istememeli ama tek haneli bir okumada da hassasiyet kaybolmamalı,
     yani adım okumanın kabaca yüzde biri ile onda biri arasında kalsın. */
  const automationAutoStep=value=>{
    const size=Math.abs(Number(value)||0);
    if(size<10)return 1;
    if(size<100)return 5;
    if(size<1000)return 10;
    if(size<10000)return 50;
    return 100;
  };
  const automationThresholdStepFor=(device,property)=>{
    const control=(device?.controls||[]).find(item=>item.property===property);
    if(Number.isFinite(control?.step)&&control.step>0)return control.step;
    return automationAutoStep(device?.state?.[property]);
  };
  /* Eşik kadranı: iki adım düğmesi + **elle yazılabilen** alan. Adım otomatik olduğu için tam
     değeri yazmak yine mümkün olmalı; alan `change` ile okunur (odak kaybı ya da Enter), böylece
     yazarken ekran baştan çizilip imleç kaçmaz. Birim etiketi uydurulmaz: cihaz bir birim
     bildiriyorsa o, bildirmiyorsa okunan alanın kendi adı yazılır. */
  const automationThresholdDialHtml=({value,size,stepAttr,token,valueAttr,unitText,ariaLabel})=>{
    const step=(amount,labelKey,glyph)=>`<button class="automation-time-step" type="button" ${stepAttr}="${esc(token(amount))}" aria-label="${esc(t(labelKey))}">${glyph}</button>`;
    const field=`<input class="automation-time-value automation-time-field" type="number" inputmode="decimal" step="any" ${valueAttr} value="${esc(String(value))}" aria-label="${esc(ariaLabel)}">`;
    return`<div class="automation-time" role="group" aria-label="${esc(ariaLabel)}"><div class="automation-time-unit">${step(size,"automationThresholdUp","+")}${field}${step(-size,"automationThresholdDown","−")}</div><span class="automation-time-unit-label">${esc(unitText)}</span></div>`;
  };
  function automationThresholdHtml(wizard){
    const device=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
    const unit=automationPropertyUnit(device,wizard.triggerProperty);
    const reading=automationPropertyLabel(device,wizard.triggerProperty);
    const choice=(value,key)=>automationChoiceHtml(value==="above"?"on":"off",key,wizard.thresholdDir===value,`data-automation-threshold-dir="${value}"`);
    const dial=automationThresholdDialHtml({
      value:wizard.thresholdValue,
      size:automationThresholdStepFor(device,wizard.triggerProperty),
      stepAttr:"data-automation-threshold-step",token:amount=>String(amount),
      valueAttr:'data-automation-threshold-value="1"',
      // "Değer" yerine okunan alanın kendi adı: "30 Işık düzeyi" diye okunur.
      unitText:unit||reading,ariaLabel:reading
    });
    return`<div class="automation-parts"><div class="automation-part"><p class="automation-part-name">${esc(reading)}</p><div class="automation-choices">${choice("above","automationThresholdAbove")}${choice("below","automationThresholdBelow")}</div></div></div>${dial}<p class="automation-hint">${esc(t("automationThresholdHint"))}</p>${automationTrigForHtml(wizard)}`;
  }
  // ————— koşullar (§5.3). Yalnız VE; en fazla dört tane. Boşsa kural her zaman çalışır.
  const maxAutomationConditions=4;
  // §2.1 — "şu kadar süredir böyleyse" tavanı; sunucudaki `maxAutomationConditionForSeconds`.
  const maxAutomationCondForSeconds=86400;
  const automationConditionRows=device=>{
    const rows=[...automationTriggerEvents(device,"deviceState"),...automationTriggerEvents(device,"sensor")];
    const seen=new Set();
    return rows.filter(row=>{const key=`${row.property}=${String(row.equals)}`;return seen.has(key)?false:(seen.add(key),true)});
  };
  // Sayısal özellikler koşulda da satır olur; seçilince değer listesi yerine karşılaştırma gelir.
  const automationConditionNumericRows=device=>automationNumericProperties(device).map(property=>({
    token:`num:${property}`,property,numeric:true,
    label:t("automationCondThresholdRow",{reading:automationPropertyLabel(device,property)})
  }));
  const automationConditionAllRows=device=>[
    ...automationConditionRows(device),
    ...automationConditionNumericRows(device)
  ];
  /* Ölçüm yolu (`numericOnly`) listeyi sayısal okuması OLAN cihazlara daraltır: "ışık düzeyi
     30'un altında" kurmak isteyen kişiye açma/kapama anahtarlarını göstermenin anlamı yok. */
  const automationConditionDevices=(numericOnly=false)=>state.devices
    .filter(device=>(numericOnly?automationNumericProperties(device).length:automationConditionAllRows(device).length)>0)
    .sort(automationByName);
  // Evde ölçüm bildiren tek bir cihaz bile yoksa ölçüm seçeneği kapalı çizilir; sebebi rozette yazar.
  const automationHasNumericDevices=()=>automationConditionDevices(true).length>0;
  // §9.3 — cihaz satırının altında hangi okumaların koşula girebileceği önden görünsün.
  // Aynı özelliğin "açık/kapalı" gibi iki satırı tek okuma adına iner; üçten fazlası kısalır.
  const automationCondPropertyPreview=(device,numericOnly=false)=>{
    const names=[];
    for(const row of(numericOnly?automationConditionNumericRows(device):automationConditionAllRows(device))){
      const label=automationPropertyLabel(device,row.property);
      if(label&&!names.includes(label))names.push(label);
    }
    if(!names.length)return"";
    return t("automationCondPickReadings",{readings:names.slice(0,3).join(", ")+(names.length>3?"…":"")});
  };
  // §9.3 — tetikleyicideki cihaz listenin başında ayrı bir kümede durur: kullanıcı aynı cihazın
  // durumuna bakmak isterken onu baştan aramak zorunda kalıyordu.
  const automationCondPickGroups=(wizard,devices)=>{
    const triggerId=wizard?.triggerDeviceId||null;
    const same=devices.filter(device=>device.id===triggerId);
    if(!same.length)return[{devices,proven:true}];
    return[
      {devices:same,proven:true,head:"automationCondPickSameDevice"},
      {devices:devices.filter(device=>device.id!==triggerId),proven:true,head:"automationCondPickOtherDevices"}
    ];
  };
  const automationConditionEventLabel=condition=>{
    const device=state.devices.find(item=>item.id===condition.deviceId)||null;
    const value=condition.equals!==undefined?condition.equals:condition.not;
    const row=automationConditionRows(device).find(item=>item.property===condition.property&&item.equals===value);
    return row?row.label:`${condition.property}: ${String(value)}`;
  };
  const automationClockText=(hour,minute)=>`${String(hour).padStart(2,"0")}:${String(minute).padStart(2,"0")}`;
  // ————— §2.3 aralık ucu: sabit saat ya da güneşe göreli an. Sunucu nesne yazar ama eski
  // kayıtlarda uç hâlâ "HH:MM" dizesi olabilir; okuma yolu iki biçimi de kabul eder.
  const automationCondPointOf=value=>{
    if(typeof value==="string")return{kind:"clock",at:value};
    if(value&&value.kind==="sun")return{kind:"sun",event:value.event==="sunrise"?"sunrise":"sunset",offsetMinutes:Number(value.offsetMinutes)||0};
    return{kind:"clock",at:String(value?.at||"00:00")};
  };
  // Uç cümlesi: saat kendi değeridir, güneş ucu tetikleyicideki tam şablonu paylaşır.
  const automationCondPointText=point=>point.kind==="clock"?point.at:automationSunSentenceTime(point);
  // Gün içi dakika — yalnız "gece yarısını aşıyor" notu için. Güneş saatleri yoksa null döner.
  const automationCondPointMinutes=point=>{
    const stamp=point.kind==="clock"
      ?point.at
      :(point.event==="sunrise"?state.automationSun?.sunrise:state.automationSun?.sunset);
    if(!stamp)return null;
    const hour=Number(String(stamp).slice(0,2)),minute=Number(String(stamp).slice(3,5));
    if(!Number.isFinite(hour)||!Number.isFinite(minute))return null;
    const total=hour*60+minute+(point.kind==="sun"?point.offsetMinutes:0);
    return((total%1440)+1440)%1440;
  };
  // Hazır aralıklar: batış→doğuş "hava karanlıkken", doğuş→batış "gündüz". Kaydırma varsa özeldir.
  const automationCondRangePreset=(from,to)=>{
    if(from.kind!=="sun"||to.kind!=="sun"||from.offsetMinutes||to.offsetMinutes)return"custom";
    if(from.event==="sunset"&&to.event==="sunrise")return"dark";
    if(from.event==="sunrise"&&to.event==="sunset")return"daylight";
    return"custom";
  };
  const automationConditionLine=(condition,wizard)=>{
    if(condition.type==="timeRange"){
      const from=automationCondPointOf(condition.from),to=automationCondPointOf(condition.to);
      const everyDay=automationEveryDay(condition.days||automationWeekDays);
      const days=everyDay?"":esc(automationDayList(condition.days));
      // Ön ayarın kendi cümlesi var: "gün batımı ile gün doğumu arasında" demek yerine düz konuşur.
      const preset=automationCondRangePreset(from,to);
      if(preset==="dark")return everyDay?t("automationCondDarkLine"):t("automationCondDarkDaysLine",{days});
      if(preset==="daylight")return everyDay?t("automationCondDaylightLine"):t("automationCondDaylightDaysLine",{days});
      const fromText=automationStrong(automationCondPointText(from));
      const toText=automationStrong(automationCondPointText(to));
      const base=everyDay
        ?t("automationCondTimeLine",{from:fromText,to:toText})
        :t("automationCondTimeDaysLine",{from:fromText,to:toText,days});
      // Gece yarısını aşan aralık kullanıcıya açıkça yazılır: 22:00→06:00 geçerlidir.
      const fromMinutes=automationCondPointMinutes(from),toMinutes=automationCondPointMinutes(to);
      return fromMinutes!==null&&toMinutes!==null&&fromMinutes>toMinutes
        ?`${base} <span class="automation-line-meta">${esc(t("automationCondOvernight"))}</span>`
        :base;
    }
    /* Koşul tetikleyiciyle aynı cihazı okuyorsa ad iki kez yazılmaz: satır hemen tetikleyicinin
       altında durur, "aynı cihaz" oraya işaret eder ve cümle kısalır. Başka cihazda ad yazılır.
       İki biçim var, çünkü şablonun devamı iki türlü: durum cümlesinde ad yalın durur
       ("aynı cihaz varlık algılandı ise"), okuma cümlesinde okumanın sahibidir
       ("aynı cihazın ışık düzeyi"). İngilizcede de aynı ayrım ("the same device" / "…'s"). */
    const sameDevice=Boolean(wizard&&wizard.triggerDeviceId&&condition.deviceId===wizard.triggerDeviceId);
    const deviceName=owner=>automationStrong(sameDevice
      ?t(owner?"automationCondSameDeviceOwner":"automationCondSameDeviceName")
      :automationTriggerDeviceName({deviceId:condition.deviceId}));
    const device=deviceName(false);
    // §9.2 — süre koşulu cümlede "…dir/…dır" yönünü taşır ki "sonra kapat" ile karışmasın.
    // Parça birleştirme yok: süreli her biçimin kendi tam şablonu var.
    const held=Number.isFinite(condition.forSeconds)&&condition.forSeconds>0;
    const duration=held?automationStrong(automationDurationText(condition.forSeconds)):"";
    const key=base=>held?`${base}ForLine`:`${base}Line`;
    /* §2.6 — tazelik penceresi cümleye şablon olarak girmez: girse her biçimin (üstünde/altında/
       arasında × süreli/süresiz) iki katı şablon gerekirdi. Gece yarısını aşan aralıkta olduğu
       gibi, cümlenin arkasına sönük bir not olarak eklenir. */
    const fresh=Number.isFinite(condition.freshWithinSeconds)&&condition.freshWithinSeconds>0
      ?` <span class="automation-line-meta">${esc(t("automationCondFreshMeta",{window:automationDurationTextShort(condition.freshWithinSeconds)}))}</span>`
      :"";
    // Sayısal eşik: i18n kuralı gereği parça birleştirilmez, her biçimin tam şablonu vardır.
    if(condition.above!==undefined||condition.below!==undefined){
      const sensor=state.devices.find(item=>item.id===condition.deviceId)||null;
      const unit=automationPropertyUnit(sensor,condition.property);
      const reading=esc(automationPropertyLabel(sensor,condition.property));
      const owner=deviceName(true);
      const amount=value=>automationStrong(`${value}${unit}`);
      if(condition.above!==undefined&&condition.below!==undefined)
        return t(key("automationCondBetween"),{device:owner,reading,from:amount(condition.above),to:amount(condition.below),duration})+fresh;
      const above=condition.above!==undefined;
      return t(key(above?"automationCondAbove":"automationCondBelow"),{
        device:owner,reading,value:amount(above?condition.above:condition.below),duration
      })+fresh;
    }
    const event=esc(automationConditionEventLabel(condition));
    return t(key(condition.not!==undefined?"automationCondStateNot":"automationCondState"),{device,event,duration})+fresh;
  };
  // Taslak ucu her iki biçimi de taşır: kullanıcı saatten güneşe geçip dönünce girdiği saat durur.
  const automationCondEdgeDraft=(value,fallbackEvent)=>{
    const point=automationCondPointOf(value);
    if(point.kind==="sun")return{
      kind:"sun",event:point.event,offset:point.offsetMinutes,
      hour:point.event==="sunrise"?6:22,minute:0
    };
    return{
      kind:"clock",event:fallbackEvent,offset:0,
      hour:Number(point.at.slice(0,2))||0,minute:Number(point.at.slice(3,5))||0
    };
  };
  const automationCondEdgePayload=edge=>edge.kind==="sun"
    ?{kind:"sun",event:edge.event==="sunrise"?"sunrise":"sunset",
      offsetMinutes:Math.max(-maxAutomationSunOffset,Math.min(maxAutomationSunOffset,Math.round(Number(edge.offset)||0)))}
    :{kind:"clock",at:automationClockText(edge.hour,edge.minute)};
  const automationConditionDraft=(condition,index)=>{
    if(condition?.type==="timeRange")return{
      type:"timeRange",
      from:automationCondEdgeDraft(condition.from,"sunset"),
      to:automationCondEdgeDraft(condition.to,"sunrise"),
      days:[...(condition.days||automationWeekDays)],index:index??null
    };
    if(condition?.type==="deviceState"){
      const above=condition.above!==undefined&&condition.above!==null;
      const below=condition.below!==undefined&&condition.below!==null;
      // §2.1 süre ölçütü değer ölçütünün üstüne biner: sayısal da olsa boolean da olsa taşınır.
      const forSeconds=Number.isFinite(condition.forSeconds)&&condition.forSeconds>0?condition.forSeconds:null;
      // §2.6 — pencere yoksa taslakta da yoktur: düzenlenip kaydedilen eski kural aynen geri yazılır.
      const freshWithinSeconds=Number.isFinite(condition.freshWithinSeconds)&&condition.freshWithinSeconds>0
        ?condition.freshWithinSeconds:null;
      if(above||below)return{
        type:"deviceState",deviceId:condition.deviceId,property:condition.property,numeric:true,
        thresholdDir:above&&below?"between":below?"below":"above",
        above:Number(above?condition.above:condition.below),
        below:Number(below?condition.below:condition.above),
        value:null,negate:false,forSeconds,freshWithinSeconds,index:index??null
      };
      return{
        type:"deviceState",deviceId:condition.deviceId,property:condition.property,numeric:false,
        thresholdDir:"above",above:0,below:0,
        value:condition.equals!==undefined?condition.equals:condition.not,
        negate:condition.not!==undefined,forSeconds,freshWithinSeconds,index:index??null
      };
    }
    return null;
  };
  const automationConditionFromDraft=draft=>{
    if(!draft)return null;
    if(draft.type==="timeRange"){
      const from=automationCondEdgePayload(draft.from),to=automationCondEdgePayload(draft.to);
      // Aynı anı gösteren iki uç "hiç mi hep mi" belirsizdir; sunucu da reddeder.
      if(from.kind==="clock"&&to.kind==="clock"&&from.at===to.at)return null;
      if(from.kind==="sun"&&to.kind==="sun"&&from.event===to.event&&from.offsetMinutes===to.offsetMinutes)return null;
      const condition={type:"timeRange",from,to};
      if(!automationEveryDay(draft.days))condition.days=[...draft.days].sort((left,right)=>left-right);
      return condition;
    }
    if(!draft.deviceId||!draft.property)return null;
    // §2.1 — "şu kadar süredir böyleyse". Kapalıyken alan hiç yazılmaz: eski davranış aynen kalır.
    const applyFor=condition=>{
      const seconds=Math.round(Number(draft.forSeconds));
      if(Number.isFinite(seconds)&&seconds>0)condition.forSeconds=Math.min(seconds,maxAutomationCondForSeconds);
      // §2.6 — tazelik penceresi de aynı kuralda: kapalıyken alan hiç yazılmaz.
      const fresh=Math.round(Number(draft.freshWithinSeconds));
      if(Number.isFinite(fresh)&&fresh>0)condition.freshWithinSeconds=Math.min(fresh,maxAutomationCondFreshSeconds);
      return condition;
    };
    if(draft.numeric){
      const above=Number(draft.above),below=Number(draft.below);
      const wantsAbove=draft.thresholdDir!=="below",wantsBelow=draft.thresholdDir!=="above";
      if(wantsAbove&&!Number.isFinite(above))return null;
      if(wantsBelow&&!Number.isFinite(below))return null;
      // Ters aralık hiçbir zaman sağlanmaz; sunucu da reddeder.
      if(wantsAbove&&wantsBelow&&!(above<below))return null;
      const condition={type:"deviceState",deviceId:draft.deviceId,property:draft.property};
      if(wantsAbove)condition.above=above;
      if(wantsBelow)condition.below=below;
      return applyFor(condition);
    }
    if(draft.value===undefined||draft.value===null)return null;
    const condition={type:"deviceState",deviceId:draft.deviceId,property:draft.property};
    if(draft.negate)condition.not=draft.value;else condition.equals=draft.value;
    return applyFor(condition);
  };
  // Süre ölçütü (`forSeconds`) durum koşulunun içinde sessiz bir satırdı: kullanıcı orada olduğunu
  // bilmiyordu. Üçüncü satır aynı koşulu kurar, tek farkı süre satırının açık gelmesidir —
  // veri modeli aynı: `deviceState` + `forSeconds`.
  /* §9.4 — ölçüm koşulu KENDİ satırı. Aynı koşul "cihaz bir durumdaysa" yolundan da kurulabiliyor
     ama ancak cihaz seçildikten SONRA görünüyordu: "ışık düzeyi 30'un altındaysa" arayan kişi
     listede üç satır görüp yolu bulamıyordu. Bu satır veri modelini değiştirmez — aynı
     `deviceState` + `above`/`below` koşulunu kurar, yalnız akışı sayısal okumalara daraltır.
     Ölçüm bildiren cihaz yoksa satır gizlenmez, KAPALI çizilir: yokluğun sebebi görünsün. */
  const automationConditionChoicesHtml=()=>{
    const measures=automationHasNumericDevices();
    return automationOptionsHtml([
      {glyph:"🕐",title:t("automationCondTime"),sub:t("automationCondTimeSub"),hook:'data-automation-cond-kind="timeRange"'},
      {glyph:"💡",title:t("automationCondState"),sub:t("automationCondStateSub"),hook:'data-automation-cond-kind="deviceState"'},
      {glyph:"⏱",title:t("automationCondStateFor"),sub:t("automationCondStateForSub"),hook:'data-automation-cond-kind="deviceStateFor"'},
      {
        glyph:"📊",title:t("automationCondMeasure"),sub:t("automationCondMeasureSub"),
        disabled:!measures,badge:measures?null:t("automationCondMeasureNone"),
        hook:'data-automation-cond-kind="deviceMeasurement"'
      }
    ]);
  };
  // §2.4 — kural başına tek anahtar. Tek koşulda anlamsız olduğu için hiç çizilmez.
  function automationCondModeHtml(wizard){
    const active=wizard.conditionMode==="any"?"any":"all";
    const choice=(value,key)=>automationChoiceHtml(value==="any"?"toggle":"on",key,active===value,`data-automation-cond-mode="${value}"`);
    return`<div class="automation-parts"><div class="automation-part"><div class="automation-choices">${choice("all","automationCondModeAll")}${choice("any","automationCondModeAny")}</div></div></div>`;
  }
  // Aralığın bir ucu: "Saat / Gün doğumu / Gün batımı" seçimi, altında ya saat kadranı ya dakika
  // kaydırma kadranı. Konum yoksa güneş uçları kilitli kalır — sessiz bir hata yerine görünür kilit.
  function automationCondEdgeHtml(edge,which,locked){
    const hook=which==="to"?"data-automation-cond-time-to":"data-automation-cond-time";
    const label=which==="to"?"automationCondTo":"automationCondFrom";
    const pick=(kind,key)=>automationChoiceHtml(
      kind==="clock"?"on":"toggle",key,
      kind==="clock"?edge.kind==="clock":edge.kind==="sun"&&edge.event===kind,
      `data-automation-cond-point="${which}:${kind}"${kind!=="clock"&&locked?" disabled":""}`
    );
    const choices=`<div class="automation-choices">${pick("clock","automationCondPointClock")}${pick("sunrise","automationSunrise")}${pick("sunset","automationSunset")}</div>`;
    if(edge.kind!=="sun"){
      return`<div class="automation-part"><p class="automation-part-name">${esc(t(label))}</p>${choices}${automationClockHtml(edge.hour,edge.minute,hook,label)}</div>`;
    }
    const step=(amount,labelKey,glyph)=>`<button class="automation-time-step" type="button" data-automation-cond-sun-step="${which}:${amount}" aria-label="${esc(t(labelKey))}">${glyph}</button>`;
    const dial=`<div class="automation-time" role="group" aria-label="${esc(t("automationSunOffsetLabel"))}"><div class="automation-time-unit">${step(15,"automationSunOffsetUp","+")}<span class="automation-time-value">${esc(String(edge.offset))}</span>${step(-15,"automationSunOffsetDown","−")}</div><span class="automation-time-unit-label">${esc(t("automationSunOffsetUnit"))}</span></div>`;
    return`<div class="automation-part"><p class="automation-part-name">${esc(t(label))}</p>${choices}${dial}</div>`;
  }
  function automationConditionTimeHtml(wizard){
    const draft=wizard.draftCondition;
    const reason=automationSunReason();
    const locked=Boolean(reason);
    const preset=automationCondRangePreset(automationCondEdgePayload(draft.from),automationCondEdgePayload(draft.to));
    const chip=(name,key)=>`<button class="automation-day${preset===name?" active":""}" type="button" data-automation-cond-preset="${name}" aria-pressed="${preset===name}"${name!=="custom"&&locked?" disabled":""}>${esc(t(key))}</button>`;
    const chips=`<p class="automation-part-name">${esc(t("automationCondPresetTitle"))}</p><div class="automation-days">${chip("dark","automationCondPresetDark")}${chip("daylight","automationCondPresetDaylight")}${chip("custom","automationCondPresetCustom")}</div>`;
    // Sessiz mekanizma yok: konum eksikse sebebi ve çıkış yolu aynı ekranda durur.
    const blocked=locked
      ?`<div class="automation-alt"><p>${esc(t("automationCondSunLocked"))}</p>${automationAddHtml(t("automationOpenLocation"),'data-automation-open-location="1"')}</div>`
      :"";
    return`${blocked}${chips}<div class="automation-parts">${automationCondEdgeHtml(draft.from,"from",locked)}${automationCondEdgeHtml(draft.to,"to",locked)}</div>${automationDaysHtml(draft.days,"data-automation-cond-day")}<p class="automation-hint">${esc(t("automationCondOvernightHint"))}</p>`;
  }
  // Sayısal koşul: değer listesi yerine karşılaştırma satırı. Tetikleyicideki eşik arayüzünün
  // aynısı, üstüne yalnız koşula özgü "arasında" ucu eklenir.
  function automationCondThresholdHtml(draft,device){
    const unit=automationPropertyUnit(device,draft.property);
    const size=automationThresholdStepFor(device,draft.property);
    const reading=automationPropertyLabel(device,draft.property);
    const choice=(value,key)=>automationChoiceHtml(value==="below"?"off":"on",key,draft.thresholdDir===value,`data-automation-cond-threshold-dir="${value}"`);
    // Tek kadranda birim/alan adı yazılır; "arasında"da iki kadranı ayıran şey alt/üst sınır
    // etiketidir — orada ikisine de aynı adı yazmak hangisinin hangisi olduğunu gizlerdi.
    const dial=(edge,label)=>automationThresholdDialHtml({
      value:draft[edge],size,
      stepAttr:"data-automation-cond-threshold-step",token:amount=>`${edge}:${amount}`,
      valueAttr:`data-automation-cond-threshold-value="${edge}"`,
      unitText:unit||label,ariaLabel:label
    });
    const dials=draft.thresholdDir==="between"
      ?`${dial("above",t("automationCondThresholdFrom"))}${dial("below",t("automationCondThresholdTo"))}`
      :dial(draft.thresholdDir==="below"?"below":"above",reading);
    return`<div class="automation-parts"><div class="automation-part"><p class="automation-part-name">${esc(reading)}</p><div class="automation-choices">${choice("above","automationCondAbove")}${choice("below","automationCondBelow")}${choice("between","automationCondBetween")}</div></div></div>${dials}<p class="automation-hint">${esc(t("automationCondThresholdHint"))}</p>${automationCondFreshHtml(draft)}`;
  }
  /* §2.6 — tazelik penceresi. "Değer şu an eşiğin altında" demek, o değerin **taze** olduğu
     anlamına gelmiyor: sabit lüks yayan bir sensörde okuma aylık olabilir. Pencere kapalıyken
     (varsayılan) alan hiç yazılmaz ve mevcut kurallar aynen çalışır. Seçenekler sabit ve azdır —
     otomasyon anlıktır, "son 10 sn / 30 sn / 1 dk / 5 dk" bu işi görür. */
  const maxAutomationCondFreshSeconds=3600;
  const automationCondFreshChoices=[0,10,30,60,300];
  const automationCondFreshText=seconds=>seconds<=0
    ?t("automationCondFreshAny")
    :seconds<60
    ?t("automationCondFreshSeconds",{count:seconds})
    :t("automationCondFreshMinutes",{count:Math.round(seconds/60)});
  function automationCondFreshHtml(draft){
    const active=Number(draft?.freshWithinSeconds)>0?Number(draft.freshWithinSeconds):0;
    const chip=seconds=>`<button class="automation-day${active===seconds?" active":""}" type="button" data-automation-cond-fresh="${seconds}" aria-pressed="${active===seconds}">${esc(automationCondFreshText(seconds))}</button>`;
    return`<p class="automation-part-name">${esc(t("automationCondFreshTitle"))}</p><div class="automation-days">${automationCondFreshChoices.map(chip).join("")}</div><p class="automation-hint">${esc(t("automationCondFreshHint"))}</p>`;
  }
  // §9.2 — "şu kadar süredir böyleyse" satırı. Eylem adımındaki "sonra kapat"la aynı kelimeleri
  // kullandığı için karışıyordu: bu satır varsayılan olarak kapalı durur, kendi görsel dilini
  // (kesikli çerçeveli sessiz blok) taşır ve etiketi zaman yönünü "…dir/…dır" olarak söyler.
  // Süre sayacı dakika gösterir: sunucu tavanı 86400 saniye, yani 24:00.
  const maxAutomationCondForMinutes=Math.floor(maxAutomationCondForSeconds/60);
  const automationForMinutes=seconds=>Math.max(1,Math.min(maxAutomationCondForMinutes,Math.round((Number(seconds)||0)/60)));
  function automationCondForHtml(draft){
    const held=draft.forSeconds>0;
    // Kapalıyken tek bir sessiz düğme: ekran kalabalıklaşmasın, ama ölçüt görünür olsun.
    if(!held){
      return`<div class="automation-cond-for"><button class="automation-cond-for-open" type="button" data-automation-cond-for="1" aria-expanded="false">${esc(t("automationCondForOpen"))}</button></div>`;
    }
    const counter=automationCounterHtml("data-automation-cond-for-step",automationForMinutes(draft.forSeconds),false,"automationCondForTimeLabel","automationCondForDown","automationCondForUp");
    return`<div class="automation-cond-for is-on"><div class="automation-cond-for-head"><p class="automation-part-name">${esc(t("automationCondForLabel"))}</p><button class="automation-cond-for-clear" type="button" data-automation-cond-for="0">${esc(t("automationCondForClear"))}</button></div>${counter}<p class="automation-hint">${esc(t("automationCondForHint"))}</p></div>`;
  }
  // §2.1 — tetikleyicideki süre satırı. Kullanıcı süreyi **önce koşulda** aradı; tetikleyicide de
  // aynı yerde, aynı görünümde bulmalı: bu yüzden koşulun görsel dili (`automation-cond-for`
  // kesikli sessiz bloğu) bilerek aynen paylaşılır, yeni bir stil türetilmez.
  // Süre yalnız hedefi olan tetikleyicide anlamlıdır: anahtar eşleme yolunda ("her değişimde")
  // neyin kaç saniyedir sürdüğü tanımsızdır, sunucu da orada reddeder. Düğme de dışarıdadır:
  // düğme yolu `deviceAction` yazar, sunucu süreyi yalnız `deviceState` üstünde okur — orada
  // satır çizilseydi ayarlanan süre kaydederken sessizce düşerdi.
  const automationTrigForEligible=wizard=>Boolean(wizard)
    &&!automationMappingMode(wizard)
    &&wizard.triggerKind!=="button"
    // Hedefsiz değer tetikleyicisinde "şu kadar süredir böyleyse" tanımsızdır: neyin sürdüğünü
    // söyleyen bir hedef değer yok. Sunucu da bu birleşimi reddediyor.
    &&(automationThresholdActive(wizard)||(wizard.triggerEquals!==null&&wizard.triggerEquals!==undefined))
    &&automationDeviceKinds.includes(wizard.triggerKind);
  function automationTrigForHtml(wizard){
    if(!automationTrigForEligible(wizard))return"";
    const held=wizard.triggerForSeconds>0;
    if(!held){
      return`<div class="automation-cond-for"><button class="automation-cond-for-open" type="button" data-automation-trig-for="1" aria-expanded="false">${esc(t("automationTrigForOpen"))}</button></div>`;
    }
    const counter=automationCounterHtml("data-automation-trig-for-step",automationForMinutes(wizard.triggerForSeconds),false,"automationTrigForTimeLabel","automationTrigForDown","automationTrigForUp");
    return`<div class="automation-cond-for is-on"><div class="automation-cond-for-head"><p class="automation-part-name">${esc(t("automationTrigForLabel"))}</p><button class="automation-cond-for-clear" type="button" data-automation-trig-for="0">${esc(t("automationTrigForClear"))}</button></div>${counter}<p class="automation-hint">${esc(t("automationTrigForHint"))}</p></div>`;
  }
  function automationConditionStateHtml(wizard){
    const draft=wizard.draftCondition;
    const device=state.devices.find(item=>item.id===draft.deviceId)||null;
    // Ölçüm yolunda liste yalnız sayısal okumalardır; boşsa sebebi yazılır, boş kutu bırakılmaz.
    const rows=draft.numericOnly?automationConditionNumericRows(device):automationConditionAllRows(device);
    if(!rows.length)return`<p class="automation-empty-line">${esc(t(draft.numericOnly?"automationCondNoMeasure":"automationCondNoState"))}</p>`;
    if(draft.numeric)return`${automationCondThresholdHtml(draft,device)}${automationCondForHtml(draft)}`;
    const negate=(value,key)=>automationChoiceHtml(value?"off":"on",key,Boolean(draft.negate)===value,`data-automation-cond-negate="${value?1:0}"`);
    const picked=rows.find(row=>!row.numeric&&draft.property===row.property&&draft.value===row.equals)||null;
    // Seçildikten sonra liste seçilen satıra daralır; "Değiştir" listeyi geri açar.
    const list=picked&&!wizard.pickerOpen?automationPickedRowHtml(picked.label):automationOptionsHtml(rows.map(row=>({
      title:row.label,
      on:!row.numeric&&draft.property===row.property&&draft.value===row.equals,
      hook:`data-automation-cond-state="${esc(row.token)}"`
    })));
    return`<div class="automation-parts"><div class="automation-part"><div class="automation-choices">${negate(false,"automationCondIs")}${negate(true,"automationCondIsNot")}</div></div></div>${list}${automationCondForHtml(draft)}`;
  }
  // Cihaz olay akışından son basış — uydurma çağrı yok, mevcut /api/overview verisi okunuyor.
  const automationButtonHint=device=>{
    const event=(state.events||[]).find(item=>item.sourceName===device.sourceName&&item.property==="action");
    return event
      ?t("automationLastPress",{action:automationButtonLabel(device,event.value),time:ago(event.at)})
      :t("automationPressToLearn");
  };
  // Sayısal özellikler ayrı satırlar: seçilince eşik sorusu açılır. Sayısal olmayanda hiç görünmez.
  const automationSensorNumericRows=(device,kind)=>kind!=="sensor"?[]:automationNumericProperties(device).map(property=>({
    token:`num:${property}`,property,numeric:true,
    label:t("automationThresholdRow",{reading:automationPropertyLabel(device,property)})
  }));
  /* Seçili cihazın sunduğu her kullanılabilir skaler özellik için hedefsiz satır: "… değişince".
     Model/özellik listesi yoktur; yeni bir expose otomatik görünür. Eşik satırı yalnız sınır
     geçişinde ateşlediği için sayısal özelliklerde değişim ve eşik ayrı seçeneklerdir. */
  const automationChangeControls=device=>(device?.controls||[]).filter(isAutomationValueControl);
  const automationChangeControl=(device,property)=>automationChangeControls(device)
    .find(control=>control.property===property)||null;
  const automationChangeRows=(device,kind)=>kind!=="sensor"?[]:automationCapabilities(device).map(capability=>({
    // Kayıtlı kural `equals` taşımaz; jeton da onu `null` diye yazar ki geri okuma eşleşsin.
    token:`${capability.property}=null`,property:capability.property,equals:null,
    label:t("automationChangeRow",{reading:capability.name})
  }));
  const automationTriggerRows=(device,kind,keep)=>[
    ...automationTriggerEvents(device,kind,keep),
    ...automationChangeRows(device,kind),
    ...automationSensorNumericRows(device,kind)
  ];
  const automationEventPicked=(wizard,event)=>wizard.triggerKind==="button"
    ?wizard.triggerAction===event.action
    :event.numeric
    ?Boolean(wizard.triggerNumeric)&&wizard.triggerProperty===event.property
    :!wizard.triggerNumeric&&wizard.triggerProperty===event.property&&wizard.triggerEquals===event.equals;
  // Kanıtsız cihaz gizlenmez — yeni eşleştirilmiş, henüz basılmamış gerçek bir kumanda kaybolmasın.
  // Bunun yerine ikinci sıraya iner ve "henüz sinyal göndermedi" notuyla işaretlenir.
  const automationButtonUnproven=(wizard,device)=>Boolean(device)&&wizard?.triggerKind==="button"&&!deviceSeenPress(device);
  // Tetikleyici türüne göre kümeleme: eleme yok. Düğme yolunda kanıtsız cihaz gizlenmez, yalnız
  // ikinci kümeye iner ve "henüz sinyal göndermedi" notuyla işaretlenir.
  const automationPickGroups=(devices,kind)=>kind==="button"
    ?[{devices:devices.filter(deviceSeenPress),proven:true,head:"automationButtonProvenGroup"},
      {devices:devices.filter(device=>!deviceSeenPress(device)),proven:false,head:"automationButtonUnprovenGroup"}]
    :[{devices,proven:true}];
  const automationByName=(left,right)=>String(left.name).localeCompare(String(right.name),state.language);
  // Cihaz simgesi sunucu sınıflandırmasından türer; model ya da satıcı adına bakan kural yok.
  const automationTabGlyphs={light:"💡",switch:"🔌",cover:"🪟",climate:"🌡",lock:"🔒",fan:"🌀",button:"🔘",sensor:"🚪",other:"⚙"};
  const automationDeviceGlyph=device=>automationTabGlyphs[automationDeviceTabs(device)[0]]||automationTabGlyphs.other;
  // Her cihaz tetikleyici olabilir; yalnız o yolda hiç olayı olmayan cihaz listeye girmez.
  // Kayıtlı kural düzenlenirken seçili basış elenmiş olsa bile listede kalır (`wizard.triggerAction`).
  const automationTriggerDevices=wizard=>state.devices
    .filter(device=>automationTriggerRows(device,wizard.triggerKind,wizard.triggerAction).length>0)
    .sort(automationByName);
  // §8.2 — otomasyonu başlatan kanal hedef olamaz; döngü daha seçim anında engellenir.
  // Çok kanallı anahtarda cihazın komşu kanalları listede kalır: bir kanal tetikler, öbürü yanar.
  // Buton yolunda kanal (property) yoktur; orada cihazın tamamı elenir.
  // Hedef adımının tek elemesi cihazın gerçek yeteneğidir: açılıp kapatılabilir kontrolü olmayan
  // cihaz (ve §8.1 gereği kilit/siren) hiç listelenmez — ad ya da model tahmini yok.
  function automationTargetScope(wizard){
    const channelStarter=wizard.triggerKind!=="time"&&wizard.triggerKind!=="button"&&Boolean(wizard.triggerProperty);
    const starter=wizard.triggerKind==="time"||channelStarter?null:wizard.triggerDeviceId;
    const starterChannel=channelStarter?automationChannelKey(wizard.triggerDeviceId,wizard.triggerProperty):null;
    const targetControls=device=>(automationMappingMode(wizard)?automationStateControls(device):automationControls(device))
      .filter(control=>automationChannelKey(device.id,control.property)!==starterChannel);
    const devices=state.devices
      .filter(device=>targetControls(device).length>0&&device.id!==starter)
      .sort(automationByName);
    // Değer kumandaları da aynı elemeden geçsin diye tetikleyen kanal dışarı verilir (§8.2).
    return{targetControls,devices,starterChannel};
  }
  const automationPickScopeDevices=(wizard,scope)=>scope==="trigger"
    ?automationTriggerDevices(wizard)
    :scope==="cond"
    ?automationConditionDevices(Boolean(wizard?.draftCondition?.numericOnly))
    :automationTargetScope(wizard).devices;
  const automationPickQuery=(wizard,scope)=>scope==="trigger"?wizard.triggerQuery:scope==="cond"?wizard.condQuery:wizard.targetQuery;
  const automationPickTab=(wizard,scope)=>scope==="trigger"?wizard.triggerTab:scope==="cond"?wizard.condTab:wizard.targetTab;
  const automationPickSelectedId=(wizard,scope)=>scope==="trigger"
    ?wizard.triggerDeviceId
    :scope==="cond"
    ?wizard.draftCondition?.deviceId||null
    :wizard.draftTargetId;
  // Sekmeler evdeki cihazlardan türer: boş sekme çıkmaz, tek sekme kalırsa şerit hiç gösterilmez.
  // Çerçeve yok: düz metin, aktif olan yeşil ve kalın.
  function automationTabsHtml(wizard,scope,devices){
    const active=automationPickTab(wizard,scope)||"all";
    const found=new Set();
    for(const device of devices)for(const tab of automationDeviceTabs(device))found.add(tab);
    const tabs=automationTabOrder.filter(tab=>found.has(tab));
    if(tabs.length<2)return"";
    const chip=(tab,label)=>{
      const on=active===tab;
      return`<button class="automation-tab${on?" is-on":""}" type="button" data-automation-tab="${scope}|${tab}" aria-pressed="${on}">${esc(label)}</button>`;
    };
    return`<div class="automation-tabs" role="group" aria-label="${esc(t("automationTabsLabel"))}">${chip("all",t("automationTabAll"))}${tabs.map(tab=>chip(tab,t(automationTabLabels[tab]))).join("")}</div>`;
  }
  function automationPickListHtml(wizard,scope){
    const devices=automationPickScopeDevices(wizard,scope).filter(device=>
      automationTabMatches(device,automationPickTab(wizard,scope))&&automationSearchMatches(device,automationPickQuery(wizard,scope)));
    if(!devices.length)return`<p class="automation-empty-line">${esc(t("automationPickNoMatch"))}</p>`;
    const selected=automationPickSelectedId(wizard,scope);
    const groups=(scope==="trigger"
      ?automationPickGroups(devices,wizard.triggerKind)
      :scope==="cond"
      ?automationCondPickGroups(wizard,devices)
      :[{devices,proven:true}])
      .filter(group=>group.devices.length>0);
    // Başlık yalnızca iki küme de doluyken: tek kümede gereksiz gürültü olmasın.
    const labelled=groups.length>1;
    const list=groups.map(group=>{
      const head=labelled?`<p class="automation-group-head">${esc(t(group.head))}</p>`:"";
      const rows=group.devices.map(device=>({
        glyph:automationDeviceGlyph(device),
        title:device.name,
        // §9.3 — koşul listesinde satırın altında hangi özelliğin koşula gireceği önden yazar;
        // kullanıcı bugüne dek bunu ancak cihaza girerek görebiliyordu.
        sub:scope==="cond"
          ?automationCondPropertyPreview(device,Boolean(wizard.draftCondition?.numericOnly))
          :automationJoin(deviceKind(device),group.proven?"":t("automationButtonUnproven")),
        on:selected===device.id,
        hook:scope==="trigger"
          ?`data-automation-trigger-device="${esc(device.id)}"`
          :scope==="cond"
          ?`data-automation-cond-device="${esc(device.id)}"`
          :`data-automation-target-device="${esc(device.id)}"`
      }));
      return`${head}${automationOptionsHtml(rows)}`;
    }).join("");
    // §9.3 — listede olmayan cihazların neden orada olmadığı tek satırla söylenir.
    return scope==="cond"?`${list}<p class="automation-hint">${esc(t("automationCondPickWhy"))}</p>`:list;
  }
  // Ortak cihaz seçici: alt çizgili arama alanı + düz metin sekmeler + satır listesi.
  function automationPickerHtml(wizard,scope){
    const devices=automationPickScopeDevices(wizard,scope);
    const emptyKey=scope==="target"
      ?"automationNoTargets"
      :scope==="cond"
      ?(wizard.draftCondition?.numericOnly?"automationCondNoMeasureDevices":"automationCondNoDevices")
      :wizard.triggerKind==="button"?"automationNoButtons":"automationNoSensors";
    if(!devices.length)return`<p class="automation-empty-line">${t(emptyKey)}</p>`;
    const query=automationPickQuery(wizard,scope)||"";
    const search=`<div class="automation-search"><span class="automation-search-glyph" aria-hidden="true">⌕</span><input type="search" data-automation-search="${scope}" value="${esc(query)}" placeholder="${esc(t("automationSearchPlaceholder"))}" aria-label="${esc(t("automationSearchPlaceholder"))}" autocomplete="off" autocapitalize="none"></div>`;
    return`<div class="automation-filter">${search}${automationTabsHtml(wizard,scope,devices)}</div><div class="automation-pick-list" data-automation-pick-list="${scope}">${automationPickListHtml(wizard,scope)}</div>`;
  }
  // Alt öğe ekranı yalnız gerçek bir seçim varsa açılır: tek alt öğeli cihazda bu adım atlanır.
  // Kanıtsız düğmede uyarı ve durum alternatifi okunmadan geçilmesin diye ekran yine açılır.
  const automationTriggerChoiceCount=(wizard,device)=>automationMappingMode(wizard)
    ?automationStateControls(device).length
    :automationTriggerRows(device,wizard.triggerKind,wizard.triggerAction).length;
  const automationTriggerDetailDevice=wizard=>{
    const device=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
    if(!device)return null;
    return automationTriggerChoiceCount(wizard,device)>1||automationButtonUnproven(wizard,device)?device:null;
  };
  function automationTriggerDetailHtml(wizard,device){
    if(automationMappingMode(wizard)){
      // Durum sorulmaz; yalnız çok kanallı anahtarda hangi kanal olduğu sorulur.
      return automationOptionsHtml(automationStateControls(device).map(control=>({
        title:control.name,
        on:wizard.triggerProperty===control.property,
        hook:`data-automation-channel="${esc(control.property)}"`
      })));
    }
    const events=automationTriggerRows(device,wizard.triggerKind,wizard.triggerAction);
    // Alt varlık kuralı (§5.1.1): her düğme ve her özellik ayrı bir hedeftir.
    const list=automationOptionsHtml(events.map(event=>({
      title:event.label,
      on:automationEventPicked(wizard,event),
      hook:`data-automation-event="${esc(event.token)}"`
    })));
    const unproven=automationButtonUnproven(wizard,device);
    // Seçim engellenmez, ama kural neden hiç çalışmayabilir açıkça yazar.
    const warning=unproven?`<p class="automation-warning">${esc(t("automationButtonUnprovenWarning"))}</p>`:"";
    // Asıl değerli kısım: durum bildiren cihazda doğru yol "açılınca/kapanınca" — tek dokunuşla geçilir.
    const alternative=unproven&&automationTriggerEvents(device,"deviceState").length>0
      ?`<div class="automation-alt"><p>${esc(t("automationButtonStateAlternative"))}</p>${automationAddHtml(t("automationButtonStateAlternativeAction"),`data-automation-state-instead="${esc(device.id)}"`)}</div>`
      :"";
    const hint=wizard.triggerKind==="button"?`<p class="automation-hint">${esc(automationButtonHint(device))}</p>`:"";
    // Seçildikten sonra liste seçilen satıra daralır; "Değiştir" listeyi geri açar. Koşuldakiyle aynı.
    const picked=events.find(event=>automationEventPicked(wizard,event))||null;
    const body=picked&&!picked.numeric&&!wizard.pickerOpen?automationPickedRowHtml(picked.label):(events.length?list:"");
    // §2.1 — süre satırı listenin altında, koşuldakiyle aynı yerde durur; varsayılan kapalıdır.
    return`${warning}${alternative}${body}${hint}${automationTrigForHtml(wizard)}`;
  }
  // Hedef cihazın alt öğeleri: her kanal bir satır, satırın altında eylem pill'leri.
  function automationTargetPartsHtml(wizard,device){
    const scope=automationTargetScope(wizard);
    const controls=scope.targetControls(device);
    const single=controls.length===1;
    if(automationMappingMode(wizard)){
      // Anahtar yolunda burada yalnız kanal seçilir; ne yapılacağı eşleme formunda sorulur.
      return automationOptionsHtml(controls.map(control=>({
        title:control.name,
        on:wizard.draftProperty===control.property,
        hook:`data-automation-target="${esc(device.id)}|${esc(control.id)}"`
      })));
    }
    return`<div class="automation-parts">${controls.map(control=>{
      const name=single||String(control.name)===String(device.name)?"":`<p class="automation-part-name">${esc(control.name)}</p>`;
      if(isAutomationBinaryControl(control)){
        const choice=(key,labelKey)=>automationChoiceHtml(key,labelKey,false,`data-automation-action="${esc(device.id)}|${esc(control.id)}|${key}"`);
        const toggle=automationCanToggle(control)?choice("toggle","automationTurnToggle"):"";
        return`<div class="automation-part">${name}<div class="automation-choices">${choice("on","automationTurnOn")}${choice("off","automationTurnOff")}${toggle}</div></div>`;
      }
      const open=automationValueOpen(wizard,device.id,control.id);
      const key=automationValueChoiceKeys[control.kind]||"automationSetValue";
      const mode=["level","temperature","color"].includes(control.kind)?control.kind:"value";
      const valueChoice=automationChoiceHtml(mode,key,open,`data-automation-value="${esc(device.id)}|${esc(control.id)}"`);
      const follow=automationFollowAvailable(wizard,control)?automationChoiceHtml(
        automationFollowActionMode({mode:automationFollowMode(control)}),
        automationFollowChoiceKeys[automationFollowMode(control)],false,
        `data-automation-follow="${esc(device.id)}|${esc(control.id)}"`
      ):"";
      return`<div class="automation-part">${name}<div class="automation-choices">${valueChoice}${follow}</div>${open?automationValueEditorHtml(wizard,control):""}${follow?`<p class="automation-hint">${esc(t("automationFollowHint",{device:automationTriggerLabelName(wizard)}))}</p>`:""}</div>`;
    }).join("")}</div>`;
  }
  const automationValueChoiceKeys={level:"automationSetBrightness",temperature:"automationSetWarmth",color:"automationSetColor"};
  const automationValueOpen=(wizard,deviceId,controlId)=>wizard?.draftValueTarget?.deviceId===deviceId
    &&wizard?.draftValueTarget?.controlId===controlId;
  const automationValueDraftControl=wizard=>wizard?.draftValueTarget
    ?automationValueControl(wizard.draftValueTarget.deviceId,wizard.draftValueTarget.controlId)
    :null;
  // Renk seçici ışık kumandasının KENDİSİDİR: aynı hazır renkler, aynı `.color-picker`. Sihirbaza
  // ikinci bir seçici yazılmadı — iki yerde iki ayrı renk dili olmasın.
  const automationColorPresetsHtml=hex=>`<div class="light-presets" role="group" aria-label="${esc(t("lightPresetColors"))}">${lightColorPresets.map(preset=>{
    const on=preset.toLowerCase()===String(hex||"").toLowerCase();
    return`<button class="light-preset${on?" is-on":""}" type="button" data-automation-value-preset="${preset}" aria-pressed="${on}" aria-label="${esc(`${t("color")} ${preset}`)}" title="${preset}" style="background:${preset}"></button>`;
  }).join("")}</div>`;
  // Sayaç dili bekleme ve güneş kaydırmasıyla bilerek aynı: "−  değer  +". Değer yüzdedir;
  // kurala yazılan ham birim kullanıcıya hiç gösterilmez.
  function automationValueEditorHtml(wizard,control){
    if(control.kind==="color"){
      const hex=String(wizard.draftValue||automationValueDefaultColor).toLowerCase();
      return`<div class="automation-value">${automationColorPresetsHtml(hex)}<input class="color-picker" type="color" value="${esc(hex)}" data-automation-value-color="1" aria-label="${esc(t("automationSetColor"))}"><p class="automation-hint">${esc(t("automationValueColorHint"))}</p></div>`;
    }
    if(isAutomationEnumControl(control)){
      const choices=automationControlValues(control).map(value=>automationChoiceTextHtml(
        "value",automationScalarText(value),String(value)===String(wizard.draftValue),
        `data-automation-enum-value="${esc(encodeURIComponent(JSON.stringify(value)))}"`
      )).join("");
      return`<div class="automation-value"><div class="automation-choices">${choices}</div></div>`;
    }
    const warmth=control.kind==="temperature";
    const labelKey=warmth?"automationSetWarmth":control.kind==="level"?"automationSetBrightness":"automationSetValue";
    const step=(direction,key,glyph)=>`<button class="automation-time-step" type="button" data-automation-value-step="${direction}" aria-label="${esc(t(key))}">${glyph}</button>`;
    const hint=warmth?"automationValueWarmthHint":control.kind==="level"?"automationValueBrightnessHint":"automationValueGenericHint";
    return`<div class="automation-value"><div class="automation-counter" role="group" aria-label="${esc(t(labelKey))}">${step(-1,"automationValueDown","−")}<span class="automation-counter-value">${esc(automationValueText(control,wizard.draftValue))}</span>${step(1,"automationValueUp","+")}</div><p class="automation-hint">${esc(t(hint))}</p></div>`;
  }
  // Eşleme formu: her yön için ne yapılacağını kullanıcı seçer. Tablette geniş satır, geniş pill.
  function automationMapHtml(wizard){
    const control=automationTargetControl({deviceId:wizard.draftTargetId,property:wizard.draftProperty});
    const sun=automationSunBoth(wizard);
    const device=sun?"":automationTriggerLabelName(wizard);
    const row=(direction,labelKey,mode)=>{
      const choice=(value,key)=>automationChoiceHtml(value,key,mode===value,`data-automation-map="${direction}|${value}"`);
      // "Değiştir" yalnızca hedef tek komutla iki yönü de destekliyorsa görünür; güneş yolunda
      // iki olay da aynı şeyi yapacağı için anlamsızdır, o yüzden hiç sunulmaz (§9.1).
      const toggle=!sun&&automationCanToggle(control)?choice("toggle","automationTurnToggle"):"";
      return`<div class="automation-part"><p class="automation-part-name">${esc(t(labelKey,{device}))}</p><div class="automation-choices">${choice("on","automationTurnOn")}${choice("off","automationTurnOff")}${toggle}${choice("none","automationMapNothing")}</div></div>`;
    };
    // Güneş yolunda yön adları olaydır: "açılınca/kapanınca" değil, "gün batımında/gün doğumunda".
    return`<div class="automation-parts">${row("on",sun?"automationMapWhenSunset":"automationMapWhenOn",wizard.draftMapOn)}${row("off",sun?"automationMapWhenSunrise":"automationMapWhenOff",wizard.draftMapOff)}</div>`;
  }
  // ————— cihaz dışı eylemler: bekle, grup aç/kapat, sahne çağır. Hepsi aynı hedef listesinde durur.
  const automationDelayPresets=[5,10,30,60];
  const maxAutomationDelaySeconds=300;
  const automationExtraActionsHtml=wizard=>{
    const groups=(state.zigbeeGroups||[]).length;
    return automationOptionsHtml([
      {glyph:"⏳",title:t("automationActionDelay"),sub:t("automationActionDelaySub"),hook:'data-automation-action-kind="delay"'},
      {glyph:"◇",title:t("automationActionGroup"),sub:groups?t("automationActionGroupSub"):t("automationActionNoGroups"),
        disabled:!groups,hook:'data-automation-action-kind="group"'},
      {glyph:"🎬",title:t("automationActionScene"),sub:groups?t("automationActionSceneSub"):t("automationActionNoGroups"),
        disabled:!groups,hook:'data-automation-action-kind="scene"'}
    ]);
  };
  function automationDelayHtml(wizard){
    const custom=Boolean(wizard.draftDelayCustom)||!automationDelayPresets.includes(wizard.draftDelaySeconds);
    const chips=automationDelayPresets.map(value=>{
      const active=!custom&&wizard.draftDelaySeconds===value;
      return`<button class="automation-day${active?" active":""}" type="button" data-automation-delay="${value}" aria-pressed="${active}">${esc(t("automationSecondsUnit",{count:value}))}</button>`;
    });
    chips.push(`<button class="automation-day${custom?" active":""}" type="button" data-automation-delay-custom="1" aria-pressed="${custom}">${esc(t("automationAutoOffCustom"))}</button>`);
    const step=(amount,labelKey,glyph)=>`<button class="automation-time-step" type="button" data-automation-delay-step="${amount}" aria-label="${esc(t(labelKey))}">${glyph}</button>`;
    const counter=custom?`<div class="automation-time" role="group" aria-label="${esc(t("automationDelayLabel"))}"><div class="automation-time-unit">${step(5,"automationDelayUp","+")}<span class="automation-time-value">${wizard.draftDelaySeconds}</span>${step(-5,"automationDelayDown","−")}</div><span class="automation-time-unit-label">${t("automationDelayUnit")}</span></div>`:"";
    return`<div class="automation-days">${chips.join("")}</div>${counter}<p class="automation-hint">${esc(t("automationDelayHint"))}</p>`;
  }
  // ————— tetikleyiciden sonraki bekleme. Eylem listesindeki "⏳ bekle" eylemler *arasındaki*
  // duraktır ve etkisi sıraya bağlıdır; bu ara adım ise tetikleyiciyle ilk eylem arasını sorar ve
  // her zaman ilk eylem olarak yazılır. Ayrı bir "hemen / bekle" seçimi yok: sayacın 0:00'ı zaten
  // "hemen çalışsın" demektir, o yüzden adım tek dokunuşla atlanabilir.
  const automationWaitStepSeconds=5;
  const automationWaitSeconds=wizard=>Math.min(maxAutomationDelaySeconds,Math.max(0,Math.round(Number(wizard?.triggerWaitSeconds)||0)));
  // Sayaç bileşeni değeri "s:dd" olarak yazar; saniye verildiğinde aynı biçim "dakika:saniye" olur.
  // Bileşen ortak: güneş kaydırması ve süre satırlarıyla aynı kadran dili, yeni bir tane türetilmez.
  // Sayaç açık gelirse kullanıcı "süre girmek zorundayım" diye okuyordu: adım kapalı başlar,
  // satırda yalnız "Bekle" ve + durur. Süresi olan kayıtlı kural açık gelir.
  const automationWaitOpen=wizard=>Boolean(wizard?.waitOpen)||automationWaitSeconds(wizard)>0;
  function automationWaitHtml(wizard){
    if(!automationWaitOpen(wizard)){
      return`<div class="automation-cond-for"><button class="automation-cond-for-open automation-wait-open" type="button" data-automation-wait="1" aria-expanded="false"><span>${esc(t("automationWaitOpen"))}</span><span class="automation-plus" aria-hidden="true">+</span></button></div>`;
    }
    const counter=automationCounterHtml("data-automation-wait-step",automationWaitSeconds(wizard),false,"automationWaitLabel","automationWaitDown","automationWaitUp");
    return`<div class="automation-cond-for is-on"><div class="automation-cond-for-head"><p class="automation-part-name">${esc(t("automationWaitLabel"))}</p><button class="automation-cond-for-clear" type="button" data-automation-wait="0">${esc(t("automationWaitClear"))}</button></div>${counter}<p class="automation-hint">${esc(t("automationWaitHint"))}</p></div>`;
  }
  const automationWaitLineText=wizard=>{
    const seconds=automationWaitSeconds(wizard);
    return seconds>0?t("automationWaitLine",{duration:automationSecondsText(seconds)}):t("automationWaitNowLine");
  };
  const automationGroupListHtml=(wizard,hook)=>{
    const groups=[...(state.zigbeeGroups||[])].sort(automationByName);
    if(!groups.length)return`<p class="automation-empty-line">${esc(t("automationActionNoGroups"))}</p>`;
    return automationOptionsHtml(groups.map(group=>({
      glyph:"◇",title:group.name,sub:t("groupMembers",{count:group.members}),
      on:wizard.draftGroupId===group.id,hook:`${hook}="${esc(group.id)}"`
    })));
  };
  function automationGroupActionHtml(wizard){
    const choice=(mode,labelKey)=>automationChoiceHtml(mode,labelKey,false,`data-automation-group-value="${mode}"`);
    return`<div class="automation-parts"><div class="automation-part"><p class="automation-part-name">${esc(automationGroupName(wizard.draftGroupId))}</p><div class="automation-choices">${choice("on","automationTurnOn")}${choice("off","automationTurnOff")}</div></div></div>`;
  }
  function automationSceneListHtml(wizard){
    const scenes=automationGroup(wizard.draftGroupId)?.scenes||[];
    if(!scenes.length)return`<p class="automation-empty-line">${esc(t("automationActionNoScenes"))}</p>`;
    return automationOptionsHtml(scenes.map(scene=>({
      glyph:"🎬",title:scene.name,sub:t("automationSceneNumber",{scene:scene.id}),
      on:wizard.draftSceneId===scene.id,hook:`data-automation-scene="${scene.id}"`
    })));
  }
  // Geri alma yalnız "Aç" eyleminde anlamlıdır: kapatan ya da değiştiren eylemin geri alınacak yönü yok.
  const automationAutoOffAvailable=wizard=>Boolean(wizard)&&!automationMappingMode(wizard)
    &&wizard.targets.some(target=>Boolean(automationTargetControl(target))&&automationTargetMode(target)==="on");
  // "Hareket bitince" ölçütü tanım verisinden gelir: tetikleyici özelliğin aynı listede bir
  // "boş/hareketsiz" karşılığı varsa sunulur. Sensör modeline bakan hiçbir kural yok; bildirmeyen
  // bir özellikte seçenek hiç görünmez.
  const automationAutoOffIdleAvailable=wizard=>{
    if(!wizard||wizard.triggerKind!=="sensor")return false;
    if(!wizard.triggerProperty||wizard.triggerEquals===null||wizard.triggerEquals===undefined)return false;
    const device=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
    return automationTriggerEvents(device,"sensor")
      .some(row=>row.property===wizard.triggerProperty&&row.equals!==wizard.triggerEquals);
  };
  const automationAutoOffMinutes=wizard=>wizard.autoOffMode==="idle"?wizard.autoOffIdleMinutes:wizard.autoOffMinutes;
  // Kapanış sözü açan her hedefin üstüne yazılır; hedef başına kendi kapatma değeriyle.
  const automationAutoOffPayload=(wizard,target)=>{
    if(!automationAutoOffAvailable(wizard)||!target)return null;
    const control=automationTargetControl(target);
    if(!control||automationTargetMode(target)!=="on")return null;
    const mode=wizard.autoOffMode;
    if(mode!=="after"&&mode!=="idle")return null;
    if(mode==="idle"&&!automationAutoOffIdleAvailable(wizard))return null;
    const minutes=mode==="after"?Math.max(1,wizard.autoOffMinutes):Math.max(0,wizard.autoOffIdleMinutes);
    return{mode,seconds:minutes*60,value:automationControlValue(control,false)};
  };
  const automationAutoOffPresetValues=wizard=>wizard.autoOffMode==="idle"?[0,1,2,5,10]:[1,2,5,10,15,30];
  // Aynı değeri iki ayrı yoldan girmek karışıklık yaratıyordu. Tek yol: hazır süre çipleri.
  // Sayaç yalnız "Başka süre" seçilince açılır — listede olmayan bir süre için tek çıkış yolu.
  const automationAutoOffCustom=wizard=>Boolean(wizard.autoOffCustom)||!automationAutoOffPresetValues(wizard).includes(automationAutoOffMinutes(wizard));
  const automationAutoOffPresets=wizard=>{
    const current=automationAutoOffMinutes(wizard);
    const custom=automationAutoOffCustom(wizard);
    const chips=automationAutoOffPresetValues(wizard).map(value=>{
      const active=!custom&&current===value;
      const label=value===0?t("automationAutoOffNoWait"):automationDurationText(value*60);
      return`<button class="automation-day${active?" active":""}" type="button" data-automation-autooff-minutes="${value}" aria-pressed="${active}">${esc(label)}</button>`;
    });
    chips.push(`<button class="automation-day${custom?" active":""}" type="button" data-automation-autooff-custom="1" aria-pressed="${custom}">${esc(t("automationAutoOffCustom"))}</button>`);
    return chips.join("");
  };
  const automationAutoOffOptions=[
    {mode:"none",glyph:"—",title:"automationAutoOffNever",sub:"automationAutoOffNeverSub"},
    {mode:"idle",glyph:"🚶",title:"automationAutoOffIdle",sub:"automationAutoOffIdleSub"},
    {mode:"after",glyph:"⏱",title:"automationAutoOffAfter",sub:"automationAutoOffAfterSub"}
  ];
  // Süre girişi dokunmatikte rahat olsun: geniş hazır süre çipleri, gerekirse büyük +/− sayaç.
  function automationAutoOffHtml(wizard){
    const mode=automationAutoOffModes.includes(wizard.autoOffMode)?wizard.autoOffMode:"none";
    const options=automationAutoOffOptions
      .filter(option=>option.mode!=="idle"||automationAutoOffIdleAvailable(wizard))
      .map(option=>({
        glyph:option.glyph,title:t(option.title),sub:t(option.sub),
        on:mode===option.mode,hook:`data-automation-autooff="${option.mode}"`
      }));
    const step=(amount,labelKey,glyph)=>`<button class="automation-time-step" type="button" data-automation-autooff-step="${amount}" aria-label="${esc(t(labelKey))}">${glyph}</button>`;
    const counter=automationAutoOffCustom(wizard)?`<div class="automation-time" role="group" aria-label="${esc(t("automationAutoOffTimeLabel"))}"><div class="automation-time-unit">${step(1,"automationAutoOffMinutesUp","+")}<span class="automation-time-value">${automationAutoOffMinutes(wizard)}</span>${step(-1,"automationAutoOffMinutesDown","−")}</div><span class="automation-time-unit-label">${t("automationAutoOffMinutesUnit")}</span></div>`:"";
    const timer=mode==="none"?"":`<p class="automation-part-name">${t(mode==="idle"?"automationAutoOffIdleWaitLabel":"automationAutoOffAfterLabel")}</p><div class="automation-days">${automationAutoOffPresets(wizard)}</div>${counter}`;
    const hint=mode==="none"?"":`<p class="automation-hint">${t(mode==="idle"?"automationAutoOffIdleHint":"automationAutoOffAfterHint")}</p>`;
    return`${automationOptionsHtml(options)}${timer}${hint}`;
  }
  // Çoğaltma satırı yalnız kayıtlı bir kuralı düzenlerken görünür: yeni kuralda kopyalanacak bir şey
  // yok, kopyanın kendisinde (kimlik boş) ikinci kez basmak kafa karıştırırdı.
  const automationDuplicateHtml=wizard=>wizard.id
    ?`<button class="automation-add" type="button" data-automation-duplicate="1"><span class="automation-plus" aria-hidden="true">⧉</span><span>${esc(t("automationDuplicate"))}</span></button>`
    :"";
  const automationNameHtml=wizard=>`<div class="automation-name-field"><input id="automationName" type="text" maxlength="64" value="${esc(automationWizardName(wizard))}" placeholder="${esc(t("automationNamePlaceholder"))}" data-i18n-placeholder="automationNamePlaceholder" aria-label="${esc(t("automationNameLabel"))}"></div><p class="automation-hint">${esc(t("automationNameHint"))}</p>${automationDuplicateHtml(wizard)}`;
  // Yol seçimi sihirbazın ilk adımı: liste ile "yeni ekleme" ayrı yerler olduğu böyle anlaşılır.
  function automationPathHtml(){
    return automationOptionsHtml([
      {glyph:"⚡",title:t("simpleLinkPath"),sub:t("simpleLinkPathLead"),hook:'data-automation-path="link"'},
      {glyph:"🧩",title:t("rulePath"),sub:t("rulePathLead"),hook:'data-automation-path="rule"'}
    ]);
  }
  // ————— tek satır özet cümleleri. Cihaz adı yeşil ve kalın, gerisi sakin; taşarsa üç nokta.
  const automationTriggerChannelName=wizard=>{
    const device=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
    const controls=automationStateControls(device);
    const control=controls.find(item=>item.property===wizard.triggerProperty)||null;
    return control&&controls.length>1?control.name:"";
  };
  const automationTriggerLabelName=wizard=>automationJoin(
    automationTriggerDeviceName({deviceId:wizard.triggerDeviceId}),
    automationMappingMode(wizard)?automationTriggerChannelName(wizard):""
  );
  const automationTriggerLine=wizard=>{
    if(wizard.triggerKind==="manual")return t("automationLineManual");
    if(wizard.triggerKind==="time"){
      const time=automationStrong(automationTimeText(wizard));
      return automationEveryDay(wizard.days)
        ?t("automationLineTime",{time})
        :t("automationLineTimeDays",{time,days:esc(automationDayList(wizard.days))});
    }
    if(wizard.triggerKind==="sun"){
      // §9.1 — güneş satırı iki anı da anlatır; her an kendi günlerini taşır.
      const phrase=event=>{
        const part=automationSunPart(wizard,event);
        const moment=automationSunSentenceTime({event,offsetMinutes:part.offset});
        return automationEveryDay(part.days)
          ?moment
          :t("automationLineSunPartDays",{moment,days:automationDayList(part.days)});
      };
      return t("automationLineSunBoth",{first:automationStrong(phrase("sunset")),second:automationStrong(phrase("sunrise"))});
    }
    const device=automationStrong(automationTriggerLabelName(wizard));
    // §2.1 — süre taşıyan tetikleyicinin kendi tam şablonu var; parça birleştirme yok.
    const held=automationTrigForEligible(wizard)&&wizard.triggerForSeconds>0;
    const duration=held?automationStrong(automationDurationTextShort(wizard.triggerForSeconds)):"";
    const key=base=>held?`${base}For`:base;
    if(automationThresholdActive(wizard)){
      const sensor=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
      return t(key(wizard.thresholdDir==="below"?"automationLineBelow":"automationLineAbove"),{
        device,
        reading:esc(automationPropertyLabel(sensor,wizard.triggerProperty)),
        value:automationStrong(`${wizard.thresholdValue}${automationPropertyUnit(sensor,wizard.triggerProperty)}`),
        duration
      });
    }
    if(automationMappingMode(wizard))return t("automationLineStateBoth",{device});
    if(wizard.triggerKind==="button")return t("automationLineButton",{
      device,
      button:esc(automationButtonLabel(automationTriggerDevice({deviceId:wizard.triggerDeviceId}),wizard.triggerAction))
    });
    return t(key("automationLineEvent"),{
      device,
      event:esc(automationEventLabel({deviceId:wizard.triggerDeviceId,property:wizard.triggerProperty,equals:wizard.triggerEquals})),
      duration
    });
  };
  const automationGroupMode=target=>String(target?.value??"").toUpperCase()==="OFF"?"off":"on";
  const automationTargetLine=(wizard,target)=>{
    const kind=automationTargetKind(target);
    if(kind==="delay")return`<span class="automation-line-glyph" aria-hidden="true">⏳</span> ${automationStrong(t("automationActionDelayName",{duration:automationSecondsText(target.seconds)}))}`;
    if(kind==="group")return`<span class="automation-line-glyph" aria-hidden="true">◇</span> ${automationStrong(automationGroupName(target.groupId))} ${automationPillHtml(automationGroupMode(target))}`;
    if(kind==="scene")return`<span class="automation-line-glyph" aria-hidden="true">🎬</span> ${automationStrong(automationGroupName(target.groupId))} <span class="automation-line-meta">${esc(automationSceneName(target.groupId,target.sceneId))}</span>`;
    const name=automationStrong(automationActionName(automationTargetRef(target)));
    if(!automationMappingMode(wizard))return`${name} ${automationTargetPillHtml(target)}`;
    const sun=automationSunBoth(wizard);
    const pairs=[];
    if(target.mapOn!=="none")pairs.push(automationMapPairHtml(sun?"sunset":"on",target.mapOn));
    // "Bir şey yapma" seçilen yön hiç çizilmez: sessiz bir boşluk değil, o çift yok demektir.
    if(target.mapOff!=="none")pairs.push(automationMapPairHtml(sun?"sunrise":"off",target.mapOff));
    if(!pairs.length)return name;
    return`${name} <span class="automation-map-pairs">${pairs.join("")}</span>`;
  };
  const automationAutoOffLineText=wizard=>{
    const target=wizard.targets.find(item=>automationTargetMode(item)==="on")||null;
    const payload=automationAutoOffPayload(wizard,target);
    return payload?automationAutoOffLine({...automationTargetRef(target),autoOff:payload}):t("automationAutoOffNeverLine");
  };
  // ————— düğüm çizimi. Solda ince hat, üstünde bir nokta: bekleyen halka, biten yeşil ✓,
  // ekleme noktası kesik. Aktif düğüm `data-automation-active` taşır; geçişte yukarı kayıp söner.
  let automationAnimate=false;
  const automationNodeHtml=node=>{
    const mark=node.state?` is-${node.state}`:"";
    const active=node.state==="active"?" data-automation-active":"";
    const label=node.label?`<p class="automation-node-label">${esc(node.label)}</p>`:"";
    const inner=node.state==="active"&&automationAnimate?"automation-enter":(node.fresh?"automation-settle":"");
    return`<div class="automation-node${mark}"${active}><div class="automation-rail" aria-hidden="true"><span class="automation-dot">${node.state==="done"?"✓":""}</span></div><div class="automation-node-body">${label}<div class="${inner}">${node.body}</div></div></div>`;
  };
  const automationTriggerKindLabel=wizard=>{
    const choice=automationTriggerChoices.find(entry=>entry.kind===wizard.triggerKind);
    return choice?t(choice.label):"";
  };
  const automationTriggerKindGlyph=wizard=>{
    const choice=automationTriggerChoices.find(entry=>entry.kind===wizard.triggerKind);
    return choice?choice.glyph:"›";
  };
  // Tamamlanmış tetikleyiciye tıklayınca hangi soru yeniden açılır: alt öğe sorusu varsa o, yoksa cihaz.
  const automationTriggerEditStage=wizard=>wizard.triggerKind==="manual"
    ?"kind"
    :wizard.triggerKind==="time"
    ?"time"
    :wizard.triggerKind==="sun"
    ?"sun"
    :automationThresholdActive(wizard)
    ?"trigThreshold"
    :automationTriggerDetailDevice(wizard)?"trigEvent":"trigDevice";
  const automationDeviceRowHtml=(device,stage)=>automationSummaryHtml(
    `${automationStrong(device?device.name:t("automationMissingDevice"))} <span class="automation-line-meta">${esc(deviceKind(device))}</span>`,
    `data-automation-stage="${stage}"`,
    true
  );
  // Cihaz seçilince liste tek satıra iner: seçilen satırın kendisi kalır (aynı ikon, aynı ad) ve
  // sağında × durur. "Değiştir" metni kalktı — kullanıcı seçimi geri almanın yolunu göremiyordu.
  const automationPickedDeviceHtml=(device,scope)=>{
    const label=t("automationClearDevice");
    return`<div class="automation-picked-device"><span class="automation-opt is-on"><span class="automation-opt-glyph" aria-hidden="true">${device?automationDeviceGlyph(device):"›"}</span><span class="automation-opt-body"><span class="automation-opt-title">${esc(device?device.name:t("automationMissingDevice"))}</span><span class="automation-opt-sub">${esc(deviceKind(device))}</span></span></span><button class="automation-picked-clear" type="button" data-automation-clear-device="${scope}" aria-label="${esc(label)}" title="${esc(label)}"><span aria-hidden="true">×</span></button></div>`;
  };
  function automationWhenNodes(wizard,nodes){
    const kindRow=()=>automationSummaryHtml(
      `<span class="automation-line-glyph" aria-hidden="true">${automationTriggerKindGlyph(wizard)}</span> ${esc(automationTriggerKindLabel(wizard))}`,
      'data-automation-stage="kind"',
      true
    );
    if(wizard.stage==="kind"){
      nodes.push({state:"active",label:t("automationBlockTrigger"),body:automationTriggerChoicesHtml(wizard)});
      return;
    }
    if(wizard.stage==="time"){
      nodes.push({state:"done",body:kindRow()});
      nodes.push({state:"active",label:t("automationBlockTime"),body:automationTimeHtml(wizard)});
      return;
    }
    if(wizard.stage==="sun"){
      nodes.push({state:"done",body:kindRow()});
      nodes.push({state:"active",label:t("automationBlockSun"),body:automationSunHtml(wizard)});
      return;
    }
    if(wizard.stage==="trigThreshold"){
      const device=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
      nodes.push({state:"done",body:kindRow()});
      nodes.push({state:"done",body:automationPickedDeviceHtml(device,"trigger")});
      nodes.push({state:"active",label:t("automationBlockThreshold"),body:automationThresholdHtml(wizard)});
      return;
    }
    if(wizard.stage==="trigDevice"){
      nodes.push({state:"done",body:kindRow()});
      nodes.push({state:"active",label:t("automationBlockDevice"),body:automationPickerHtml(wizard,"trigger")});
      return;
    }
    if(wizard.stage==="trigEvent"){
      const device=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
      nodes.push({state:"done",body:kindRow()});
      nodes.push({state:"done",body:automationPickedDeviceHtml(device,"trigger")});
      nodes.push({state:"active",label:t("automationPickParts",{device:device?.name||""}),body:automationTriggerDetailHtml(wizard,device)});
      return;
    }
    if(automationTriggerReady(wizard)){
      // §5.4 — eşleme yolunda durum sorulmaz: hangi durumda ne olacağı hedef seçilince belirlenir.
      // Tek satırlık ipucu bunu önden söyler; hedef seçilir seçilmez susar, eşleme formu zaten anlatır.
      const mapHint=automationMappingMode(wizard)&&!wizard.targets.length&&!wizard.draftTargetId
        ?`<p class="automation-hint">${esc(t("automationMapLaterHint"))}</p>`
        :"";
      nodes.push({
        state:"done",fresh:wizard.fresh==="trigger",
        body:`${automationSummaryHtml(automationTriggerLine(wizard),`data-automation-stage="${automationTriggerEditStage(wizard)}"`)}${mapHint}`
      });
    }
  }
  // KOŞUL bölümü isteğe bağlıdır: hiç koşul yoksa tek satır "her zaman çalışsın" durur.
  function automationCondNodes(wizard,nodes){
    // İki ya da daha çok koşul varsa "hepsi mi, herhangi biri mi" sorusu listenin başında durur.
    if(wizard.conditions.length>1){
      nodes.push({state:"branch",label:t("automationCondModeTitle"),body:automationCondModeHtml(wizard)});
    }
    wizard.conditions.forEach((condition,index)=>{
      nodes.push({
        state:"done",fresh:wizard.fresh===`cond-${index}`,
        body:automationSummaryHtml(
          automationConditionLine(condition,wizard),
          `data-automation-edit-cond="${index}"`,
          false,
          `data-automation-remove-cond="${index}"`,
          automationSummaryAction(false,"automationCondAddAria","automationCondChangeAria")
        )
      });
    });
    if(wizard.stage==="cond"){
      nodes.push({state:"active",label:t("automationBlockCondition"),body:automationConditionChoicesHtml()});
      return;
    }
    if(wizard.stage==="condDevice"){
      nodes.push({state:"active",label:t("automationBlockCondDevice"),body:automationPickerHtml(wizard,"cond")});
      return;
    }
    if(wizard.stage==="condTime"){
      nodes.push({state:"active",label:t("automationCondTime"),body:automationConditionTimeHtml(wizard)});
      return;
    }
    if(wizard.stage==="condState"){
      const device=state.devices.find(item=>item.id===wizard.draftCondition?.deviceId)||null;
      nodes.push({state:"done",body:automationPickedDeviceHtml(device,"cond")});
      nodes.push({state:"active",label:t("automationBlockCondState"),body:automationConditionStateHtml(wizard)});
      return;
    }
    if(!wizard.conditions.length){
      // "Her zaman çalışsın" satırı bölümün bugünkü durumunu söyler; sönük olduğu için kullanıcı
      // onu bilgi sanıp geçiyordu. Altına aynı "+ … ekle" düğmesi konur: koşul eklemenin yeri
      // ikinci koşuldakiyle aynı biçimde, aranmadan görünür.
      nodes.push({state:"branch",body:automationSummaryHtml(
        esc(t("automationCondAlwaysLine")),'data-automation-stage="cond"',true,null,
        automationSummaryAction(true,"automationCondAddAria","automationCondChangeAria")
      )});
      nodes.push({state:"branch",body:automationAddHtml(t("automationAddFirstCondition"),'data-automation-add-cond="1"')});
      return;
    }
    if(wizard.conditions.length<maxAutomationConditions){
      nodes.push({state:"branch",body:automationAddHtml(t("automationAddCondition"),'data-automation-add-cond="1"')});
    }
  }
  // Bekleme kendi bölümünde durur ("GEREKİYORSA"): "ne yapsın" başlığının altında listelenince
  // kullanıcı onu zorunlu bir eylem sanıyordu. Okunuşu yine "tetiklendi → bekle → yap".
  function automationWaitNodes(wizard,nodes){
    if(wizard.stage==="wait"){
      nodes.push({state:"active",label:t("automationBlockWait"),body:automationWaitHtml(wizard)});
      return;
    }
    // Sıfırdayken sessiz durur ("hemen çalışsın") ve koşulsuz kuraldaki "her zaman çalışsın"
    // satırıyla aynı dili konuşur.
    nodes.push({
      state:"branch",fresh:wizard.fresh==="wait",
      body:automationSummaryHtml(
        esc(automationWaitLineText(wizard)),'data-automation-stage="wait"',automationWaitSeconds(wizard)===0,null,
        automationSummaryAction(automationWaitSeconds(wizard)===0,"automationWaitAddAria","automationWaitChangeAria")
      )
    });
  }
  function automationThenNodes(wizard,nodes){
    wizard.targets.forEach((target,index)=>{
      nodes.push({
        state:"done",fresh:wizard.fresh===`target-${index}`,
        body:automationSummaryHtml(
          // Eylemler sırayla çalışır: satırın başındaki numara sırayı görünür kılar.
          `<span class="automation-line-step" aria-hidden="true">${index+1}</span> ${automationTargetLine(wizard,target)}`,
          `data-automation-edit-target="${index}"`,
          false,
          // Tek hedef kalınca ✕ görünmez: kaldırılırsa kural geçersiz olurdu.
          wizard.targets.length>1?`data-automation-remove-target="${index}"`:null
        )
      });
    });
    if(wizard.stage==="wait")return;
    if(wizard.stage==="target"){
      nodes.push({
        state:"active",label:t("automationBlockTarget"),
        body:`${automationPickerHtml(wizard,"target")}<p class="automation-group-head">${esc(t("automationOtherActions"))}</p>${automationExtraActionsHtml(wizard)}`
      });
      return;
    }
    if(wizard.stage==="delay"){
      nodes.push({state:"active",label:t("automationActionDelay"),body:automationDelayHtml(wizard)});
      return;
    }
    if(wizard.stage==="group"){
      nodes.push({state:"active",label:t("automationActionGroup"),body:automationGroupListHtml(wizard,"data-automation-group")});
      return;
    }
    if(wizard.stage==="groupAction"){
      nodes.push({state:"active",label:t("automationActionGroup"),body:automationGroupActionHtml(wizard)});
      return;
    }
    if(wizard.stage==="scene"){
      nodes.push({state:"active",label:t("automationActionScene"),body:automationGroupListHtml(wizard,"data-automation-scene-group")});
      return;
    }
    if(wizard.stage==="sceneId"){
      nodes.push({state:"active",label:t("automationActionScene"),body:automationSceneListHtml(wizard)});
      return;
    }
    if(wizard.stage==="action"||wizard.stage==="map"){
      const device=state.devices.find(item=>item.id===wizard.draftTargetId)||null;
      nodes.push({state:"done",body:automationDeviceRowHtml(device,"target")});
      nodes.push({
        state:"active",
        label:wizard.stage==="map"?t("automationMapTitle"):t("automationPickParts",{device:device?.name||""}),
        body:wizard.stage==="map"?automationMapHtml(wizard):automationTargetPartsHtml(wizard,device)
      });
      return;
    }
    if(wizard.targets.length&&wizard.targets.length<automationMaxTargets(wizard)){
      nodes.push({state:"branch",body:automationAddHtml(t("automationAddTarget"),'data-automation-add-target="1"')});
    }
  }
  function automationAfterNodes(wizard,nodes){
    if(wizard.stage==="autoOff"){
      nodes.push({state:"active",label:t("automationAutoOffTitle"),body:automationAutoOffHtml(wizard)});
      return;
    }
    if(wizard.stage==="name"){
      nodes.push({state:"active",label:t("automationNameLabel"),body:automationNameHtml(wizard)});
    }
  }
  // Kaydetme adımında "Sonrası" kararını saklamayız. Kullanıcı üstteki ilerleme
  // şeridini bilmek zorunda kalmadan açılan hedefin açık kalacağını ya da ne zaman
  // kapanacağını görür ve bu satıra dokunarak kararı doğrudan değiştirebilir.
  const automationAfterReviewHtml=wizard=>{
    if(wizard.stage!=="name"||!automationAutoOffAvailable(wizard)||!wizard.autoOffTouched)return"";
    const quiet=wizard.autoOffMode==="none";
    return`<section class="automation-after-review${wizard.fresh==="autooff"?" automation-settle":""}"><div class="automation-after-review-head"><span class="automation-question-icon" aria-hidden="true">↻</span><strong>${esc(t("automationProgressAfter"))}</strong></div>${automationSummaryHtml(
      esc(automationAutoOffLineText(wizard)),'data-automation-stage="autoOff"',quiet,null,
      automationSummaryAction(quiet,"automationAutoOffAddAria","automationAutoOffChangeAria")
    )}</section>`;
  };
  // Bu çağrı fan/ışık gibi bir cihaz türüne bağlı değildir. Mevcut eylem tamamlandıktan
  // sonra aynı cihaz, başka cihaz, grup, sahne veya bekleme ekleme yolunu tek yerde açar.
  const automationNextActionHtml=wizard=>wizard.stage==="name"&&wizard.targets.length<automationMaxTargets(wizard)
    ?`<section class="automation-next-action"><p><span aria-hidden="true">→</span>${esc(t("automationNextActionTitle"))}</p>${automationAddHtml(t("automationAddTarget"),'data-automation-add-target="1"')}</section>`
    :"";
  const automationActiveStepHtml=(nodes,icon,fallbackLabel)=>{
    const activeIndex=nodes.findIndex(node=>node.state==="active");
    if(activeIndex<0)return"";
    const active=nodes[activeIndex];
    // Aktif sorunun hemen önceki seçimi bağlam olarak kalır (ör. seçilen cihaz). Daha eski
    // cevaplar kartın özetinde gösterilir; böylece yeni soru açıldığında ekran yeniden forma dönmez.
    const keepContext=["trigEvent","trigThreshold","condState","action","map"].includes(state.automationWizard?.stage);
    const previous=keepContext?[...nodes.slice(0,activeIndex)].reverse().find(node=>node.state==="done"):null;
    const context=previous?`<div class="automation-step-context">${previous.body}</div>`:"";
    const enter=automationAnimate?" automation-enter":"";
    return`<section class="automation-card-step${enter}" data-automation-active><div class="automation-question-heading"><span class="automation-question-icon" aria-hidden="true">${icon}</span><p>${esc(active.label||fallbackLabel)}</p></div>${context}<div class="automation-question-body">${active.body}</div></section>`;
  };
  const automationConditionSummaryHtml=(condition,index)=>automationSummaryHtml(
    automationConditionLine(condition,state.automationWizard),
    `data-automation-edit-cond="${index}"`,
    false,
    `data-automation-remove-cond="${index}"`,
    automationSummaryAction(false,"automationCondAddAria","automationCondChangeAria")
  );
  const automationConditionGroupHtml=(entries,label)=>entries.length
    ?`<div class="automation-causal-conditions"><p class="automation-causal-subhead"><span aria-hidden="true">◇</span>${esc(label)}</p>${entries.map(({condition,index})=>automationConditionSummaryHtml(condition,index)).join("")}</div>`
    :"";
  const automationTargetGroupKey=target=>{
    const kind=automationTargetKind(target);
    if(kind==="device")return`device:${target.deviceId}`;
    if(kind==="group")return`group:${target.groupId}`;
    if(kind==="scene")return`scene:${target.groupId}`;
    return"delay";
  };
  const automationTargetGroupMeta=target=>{
    const kind=automationTargetKind(target);
    if(kind==="device"){
      const device=state.devices.find(item=>item.id===target.deviceId)||null;
      return{name:device?.name||t("automationMissingDevice"),meta:deviceKind(device),glyph:device?automationDeviceGlyph(device):"›"};
    }
    if(kind==="group")return{name:automationGroupName(target.groupId),meta:t("automationActionGroup"),glyph:"◇"};
    if(kind==="scene")return{name:automationGroupName(target.groupId),meta:t("automationActionScene"),glyph:"🎬"};
    return{name:t("automationActionDelay"),meta:"",glyph:"⏳"};
  };
  function automationTargetGroupsHtml(wizard){
    const groups=[];
    const byKey=new Map();
    wizard.targets.forEach((target,index)=>{
      // Bir gecikme kendisinden sonraki eylemle aynı cihaz kartında görünür. Kayıt sırası ve
      // motorun çalışma biçimi değişmez; bu yalnız "önce bekle" ilişkisinin görsel karşılığıdır.
      const next=automationTargetKind(target)==="delay"?wizard.targets[index+1]:null;
      const anchor=next&&automationTargetKind(next)!=="delay"?next:target;
      const key=automationTargetGroupKey(anchor);
      let group=byKey.get(key);
      if(!group){group={anchor,items:[]};byKey.set(key,group);groups.push(group)}
      group.items.push({target,index});
    });
    return groups.map(group=>{
      const meta=automationTargetGroupMeta(group.anchor);
      const conditions=wizard.conditions.map((condition,index)=>({condition,index}))
        .filter(entry=>entry.condition.deviceId&&entry.condition.deviceId===group.anchor.deviceId&&entry.condition.deviceId!==wizard.triggerDeviceId);
      const rows=group.items.map(({target,index})=>{
        const delay=automationTargetKind(target)==="delay";
        const canWait=!delay&&(index===0||automationTargetKind(wizard.targets[index-1])!=="delay");
        const wait=canWait&&wizard.targets.length<automationMaxTargets(wizard)
          ?`<button class="automation-inline-add" type="button" data-automation-add-wait-before="${index}"><span aria-hidden="true">＋</span>${esc(t("automationAddWaitBefore"))}</button>`:"";
        return`<div class="automation-causal-action${delay?" is-delay":""}">${automationSummaryHtml(
          `<span class="automation-line-step" aria-hidden="true">${index+1}</span> ${automationTargetLine(wizard,target)}`,
          `data-automation-edit-target="${index}"`,
          false,
          wizard.targets.length>1?`data-automation-remove-target="${index}"`:null
        )}${wait}</div>`;
      }).join("");
      return`<section class="automation-device-group"><header class="automation-device-group-head"><span class="automation-device-group-icon" aria-hidden="true">${meta.glyph}</span><span><strong>${esc(meta.name)}</strong>${meta.meta?`<small>${esc(meta.meta)}</small>`:""}</span></header><div class="automation-device-group-body">${rows}${automationConditionGroupHtml(conditions,t("automationDeviceConditions"))}</div></section>`;
    }).join("");
  }
  const automationCausalCard=(kind,title,icon,body,current,reveal=false)=>`<section class="automation-causal-card is-${kind}${current?" is-current":" is-complete"}${reveal&&automationAnimate?" automation-card-reveal":""}"><header class="automation-causal-card-head"><span class="automation-causal-card-icon" aria-hidden="true">${icon}</span><div><span class="automation-causal-step">${kind==="trigger"?"1":"2"}</span><h3>${esc(title)}</h3></div>${!current?'<span class="automation-causal-check" aria-hidden="true">✓</span>':""}</header><div class="automation-causal-card-body">${body}</div></section>`;
  const automationInlineWaitHtml=wizard=>!automationWaitOpen(wizard)
    ?`<section class="automation-card-step automation-inline-wait"><button class="automation-inline-wait-open" type="button" data-automation-wait="1" aria-expanded="false"><span class="automation-inline-wait-label"><span class="automation-question-icon" aria-hidden="true">⏳</span><strong>${esc(t("automationBlockWait"))}</strong></span><span class="automation-inline-wait-add"><span>${esc(t("add"))}</span><span class="automation-plus" aria-hidden="true">+</span></span></button></section>`
    :`<section class="automation-card-step automation-inline-wait"><div class="automation-question-heading"><span class="automation-question-icon" aria-hidden="true">⏳</span><p>${esc(t("automationBlockWait"))}</p></div><div class="automation-question-body">${automationWaitHtml(wizard)}</div></section>`;
  function automationFlowHtml(wizard){
    const stageGroup=automationStageGroup(wizard.stage);
    const triggerCurrent=stageGroup==="when"||stageGroup==="cond";
    const whenNodes=[];
    automationWhenNodes(wizard,whenNodes);
    const condNodes=[];
    if(automationTriggerReady(wizard))automationCondNodes(wizard,condNodes);
    const allConditions=wizard.conditions.map((condition,index)=>({condition,index}));
    const targetDeviceIds=new Set(wizard.targets.filter(target=>automationTargetKind(target)==="device").map(target=>target.deviceId));
    const triggerConditions=allConditions.filter(entry=>!entry.condition.deviceId
      ||entry.condition.deviceId===wizard.triggerDeviceId
      ||!targetDeviceIds.has(entry.condition.deviceId));
    let triggerBody="";
    if(stageGroup==="when")triggerBody=automationActiveStepHtml(whenNodes,"◷",t("automationSectionWhen"));
    else{
      triggerBody=`<div class="automation-trigger-summary">${automationSummaryHtml(
        automationTriggerLine(wizard),
        `data-automation-stage="${automationTriggerEditStage(wizard)}"`
      )}</div>`;
      triggerBody+=automationConditionGroupHtml(triggerConditions,t("automationDeviceConditions"));
      if(stageGroup==="cond")triggerBody+=automationActiveStepHtml(condNodes,"◇",t("automationSectionCondition"));
      else if(wizard.conditions.length<maxAutomationConditions)triggerBody+=automationAddHtml(t("automationAddCondition"),'data-automation-add-cond="1"');
    }
    const triggerCard=automationCausalCard("trigger",t("automationTriggerCardTitle"),"◷",triggerBody,triggerCurrent);

    const showAction=wizard.targets.length>0||stageGroup==="then"||stageGroup==="after";
    if(!showAction)return`<div class="automation-causal-workspace is-single">${triggerCard}</div>`;
    const waitNodes=[];automationWaitNodes(wizard,waitNodes);
    const thenNodes=[];automationThenNodes(wizard,thenNodes);
    const afterNodes=[];automationAfterNodes(wizard,afterNodes);
    let actionBody=automationTargetGroupsHtml(wizard);
    if(wizard.stage==="wait")actionBody+=automationActiveStepHtml(waitNodes,"⏳",t("automationBlockWait"));
    // Yeni kuralda bekleme ve hedef seçimi aynı sağ kartta görünür. Bekleme isteğe bağlıdır;
    // hedefe dokunmak doğrudan cihaz eylemine geçirir.
    else if(wizard.stage==="target"&&!wizard.targets.length)actionBody+=automationInlineWaitHtml(wizard);
    else if(automationWaitSeconds(wizard)>0)actionBody+=`<div class="automation-global-wait">${automationSummaryHtml(
      esc(automationWaitLineText(wizard)),'data-automation-stage="wait"'
    )}</div>`;
    if(stageGroup==="then"&&wizard.stage!=="wait")actionBody+=automationActiveStepHtml(thenNodes,"⚡",t("automationSectionThen"));
    else if(stageGroup==="after"){
      actionBody+=automationAfterReviewHtml(wizard);
      actionBody+=automationNextActionHtml(wizard);
      actionBody+=automationActiveStepHtml(afterNodes,"↻",t("automationSectionAfter"));
    }
    const actionCurrent=stageGroup==="then"||stageGroup==="after";
    const actionCard=automationCausalCard("action",t("automationActionCardTitle"),"⚡",actionBody,actionCurrent,wizard.targets.length===0);
    return`<div class="automation-causal-workspace">${triggerCard}<div class="automation-causal-arrow" aria-hidden="true">→</div>${actionCard}</div>`;
  }
  // Sayaç düğmesi: tek dokunuş bir adım, basılı tutunca hızlanarak sürer. Tutuş boyunca ekran
  // baştan çizilmez — çizilse düğme silinir, parmak kalkınca sayaç durmazdı; yalnız rakam tazelenir.
  let automationHoldQuiet=false;
  function automationBindCounter(button,run,read){
    let timer=null;
    let ticks=0;
    let repeated=false;
    const stop=()=>{
      if(timer)clearTimeout(timer);
      timer=null;
      automationHoldQuiet=false;
      if(ticks>0){ticks=0;repeated=true;automationRedraw()}
    };
    const tick=()=>{
      ticks+=1;
      automationHoldQuiet=true;
      run();
      const box=button.parentElement;
      const label=box?box.querySelector(".automation-counter-value"):null;
      if(label)label.textContent=read();
      timer=setTimeout(tick,Math.max(70,300-ticks*20));
    };
    button.onclick=()=>{if(repeated){repeated=false;return}run()};
    button.onpointerdown=()=>{if(!timer)timer=setTimeout(tick,420)};
    button.onpointerup=stop;
    button.onpointerleave=stop;
    button.onpointercancel=stop;
  }
  // Gövde her yeniden çizimde ve arama tazelemesinde aynı yerden bağlanır.
  function automationBindBody(){
    const wizard=state.automationWizard;
    if(!wizard)return;
    $$("[data-automation-path]").forEach(button=>button.onclick=()=>chooseAutomationPath(button.dataset.automationPath));
    $$("[data-automation-stage]").forEach(button=>button.onclick=()=>goToAutomationStage(button.dataset.automationStage));
    $$("[data-automation-reopen]").forEach(button=>button.onclick=()=>reopenAutomationPicker());
    $$("[data-automation-trigger]").forEach(button=>button.onclick=()=>chooseAutomationTrigger(button.dataset.automationTrigger));
    $$("[data-automation-time]").forEach(button=>button.onclick=()=>{
      const [unit,amount]=button.dataset.automationTime.split(":");
      stepAutomationTime(unit,Number(amount));
    });
    $$("[data-automation-day]").forEach(button=>button.onclick=()=>toggleAutomationDay(button.dataset.automationDay));
    $$("[data-automation-tab]").forEach(button=>button.onclick=()=>chooseAutomationTab(button.dataset.automationTab));
    $$("[data-automation-search]").forEach(input=>{input.oninput=()=>automationSearchInput(input.dataset.automationSearch,input.value)});
    $$("[data-automation-trigger-device]").forEach(button=>button.onclick=()=>chooseAutomationTriggerDevice(button.dataset.automationTriggerDevice));
    $$("[data-automation-target-device]").forEach(button=>button.onclick=()=>chooseAutomationTargetDevice(button.dataset.automationTargetDevice));
    $$("[data-automation-event]").forEach(button=>button.onclick=()=>chooseAutomationEvent(button.dataset.automationEvent));
    $$("[data-automation-channel]").forEach(button=>button.onclick=()=>chooseAutomationChannel(button.dataset.automationChannel));
    $$("[data-automation-state-instead]").forEach(button=>button.onclick=()=>automationUseStateInstead(button.dataset.automationStateInstead));
    $$("[data-automation-action]").forEach(button=>button.onclick=()=>chooseAutomationAction(button.dataset.automationAction));
    $$("[data-automation-value]").forEach(button=>button.onclick=()=>chooseAutomationValue(button.dataset.automationValue));
    $$("[data-automation-follow]").forEach(button=>button.onclick=()=>chooseAutomationFollow(button.dataset.automationFollow));
    // Değer sayacı da basılı tutuşu destekler; okuma her adımda tazedir, ekran çizilmez.
    const valueText=()=>{
      const control=automationValueDraftControl(state.automationWizard);
      return control?automationValueText(control,state.automationWizard.draftValue):"";
    };
    $$("[data-automation-value-step]").forEach(button=>automationBindCounter(
      button,
      ()=>stepAutomationValue(Number(button.dataset.automationValueStep)),
      valueText
    ));
    $$("[data-automation-value-preset]").forEach(button=>button.onclick=()=>setAutomationValueColor(button.dataset.automationValuePreset));
    $$("[data-automation-value-color]").forEach(input=>{input.onchange=()=>setAutomationValueColor(input.value)});
    $$("[data-automation-enum-value]").forEach(button=>button.onclick=()=>setAutomationEnumValue(button.dataset.automationEnumValue));
    $$("[data-automation-target]").forEach(button=>button.onclick=()=>chooseAutomationTarget(button.dataset.automationTarget));
    $$("[data-automation-map]").forEach(button=>button.onclick=()=>chooseAutomationMap(button.dataset.automationMap));
    $$("[data-automation-edit-target]").forEach(button=>button.onclick=()=>editAutomationTarget(Number(button.dataset.automationEditTarget)));
    $$("[data-automation-remove-target]").forEach(button=>button.onclick=()=>removeAutomationTarget(Number(button.dataset.automationRemoveTarget),button));
    $$("[data-automation-add-target]").forEach(button=>button.onclick=()=>addAutomationTarget());
    $$("[data-automation-add-wait-before]").forEach(button=>button.onclick=()=>addAutomationWaitBefore(Number(button.dataset.automationAddWaitBefore)));
    $$("[data-automation-autooff]").forEach(button=>button.onclick=()=>chooseAutomationAutoOff(button.dataset.automationAutooff));
    $$("[data-automation-autooff-step]").forEach(button=>button.onclick=()=>stepAutomationAutoOff(Number(button.dataset.automationAutooffStep)));
    $$("[data-automation-autooff-minutes]").forEach(button=>button.onclick=()=>setAutomationAutoOffMinutes(Number(button.dataset.automationAutooffMinutes),false));
    $$("[data-automation-autooff-custom]").forEach(button=>button.onclick=()=>openAutomationAutoOffCustom());
    $$("[data-automation-sun-edit]").forEach(button=>button.onclick=()=>chooseAutomationSunEdit(button.dataset.automationSunEdit));
    // Kaydırma sayacı: değer her adımda taze okunur, basılı tutuş sırasında ekran çizilmediği için.
    const sunOffset=()=>automationSunPart(state.automationWizard,automationSunEditing(state.automationWizard)).offset;
    $$("[data-automation-sun-step]").forEach(button=>automationBindCounter(
      button,
      ()=>setAutomationSunOffset(automationCounterNext(sunOffset(),Number(button.dataset.automationSunStep))),
      ()=>automationCounterText(sunOffset(),true)
    ));
    $$("[data-automation-open-location]").forEach(button=>button.onclick=()=>openHomeLocationSettings());
    $$("[data-automation-threshold-dir]").forEach(button=>button.onclick=()=>chooseAutomationThresholdDir(button.dataset.automationThresholdDir));
    $$("[data-automation-threshold-step]").forEach(button=>button.onclick=()=>stepAutomationThreshold(Number(button.dataset.automationThresholdStep)));
    // Elle giriş `change` ile okunur (odak kaybı / Enter): yazarken ekran baştan çizilmez.
    $$("[data-automation-threshold-value]").forEach(input=>{input.onchange=()=>setAutomationThresholdValue(input.value)});
    $$("[data-automation-trig-for]").forEach(button=>button.onclick=()=>toggleAutomationTrigFor(button.dataset.automationTrigFor));
    // Sayaç dakika gösterir: değer dakikaya yuvarlanır, 0:01 altına inmez, 24:00 üstüne çıkmaz.
    const trigForMinutes=()=>automationForMinutes(state.automationWizard?.triggerForSeconds);
    $$("[data-automation-trig-for-step]").forEach(button=>automationBindCounter(
      button,
      ()=>setAutomationTrigForSeconds(automationCounterNext(trigForMinutes(),Number(button.dataset.automationTrigForStep))*60),
      ()=>automationCounterText(trigForMinutes(),false)
    ));
    $$("[data-automation-wait]").forEach(button=>button.onclick=()=>toggleAutomationWait(button.dataset.automationWait));
    // Tetikleyiciden sonraki bekleme: sayaç saniye gösterir (0:00–5:00), adımı beş saniyedir.
    const waitSeconds=()=>automationWaitSeconds(state.automationWizard);
    $$("[data-automation-wait-step]").forEach(button=>automationBindCounter(
      button,
      ()=>setAutomationWaitSeconds(waitSeconds()+Number(button.dataset.automationWaitStep)*automationWaitStepSeconds),
      ()=>automationCounterText(waitSeconds(),false)
    ));
    $$("[data-automation-cond-kind]").forEach(button=>button.onclick=()=>chooseAutomationCondKind(button.dataset.automationCondKind));
    $$("[data-automation-cond-device]").forEach(button=>button.onclick=()=>chooseAutomationCondDevice(button.dataset.automationCondDevice));
    $$("[data-automation-clear-device]").forEach(button=>button.onclick=()=>clearAutomationPickedDevice(button.dataset.automationClearDevice));
    $$("[data-automation-cond-state]").forEach(button=>button.onclick=()=>chooseAutomationCondState(button.dataset.automationCondState));
    $$("[data-automation-cond-negate]").forEach(button=>button.onclick=()=>chooseAutomationCondNegate(button.dataset.automationCondNegate));
    $$("[data-automation-cond-mode]").forEach(button=>button.onclick=()=>chooseAutomationCondMode(button.dataset.automationCondMode));
    $$("[data-automation-cond-threshold-dir]").forEach(button=>button.onclick=()=>chooseAutomationCondThresholdDir(button.dataset.automationCondThresholdDir));
    $$("[data-automation-cond-threshold-step]").forEach(button=>button.onclick=()=>stepAutomationCondThreshold(button.dataset.automationCondThresholdStep));
    $$("[data-automation-cond-threshold-value]").forEach(input=>{input.onchange=()=>setAutomationCondThreshold(input.dataset.automationCondThresholdValue,input.value)});
    $$("[data-automation-cond-fresh]").forEach(button=>button.onclick=()=>chooseAutomationCondFresh(button.dataset.automationCondFresh));
    $$("[data-automation-cond-for]").forEach(button=>button.onclick=()=>toggleAutomationCondFor(button.dataset.automationCondFor));
    // Sayaç dakika gösterir: değer dakikaya yuvarlanır, 0:01 altına inmez, 24:00 üstüne çıkmaz.
    const condForMinutes=()=>automationForMinutes(state.automationWizard?.draftCondition?.forSeconds);
    $$("[data-automation-cond-for-step]").forEach(button=>automationBindCounter(
      button,
      ()=>setAutomationCondForSeconds(automationCounterNext(condForMinutes(),Number(button.dataset.automationCondForStep))*60),
      ()=>automationCounterText(condForMinutes(),false)
    ));
    $$("[data-automation-cond-time]").forEach(button=>button.onclick=()=>stepAutomationCondTime(button.dataset.automationCondTime,"from"));
    $$("[data-automation-cond-time-to]").forEach(button=>button.onclick=()=>stepAutomationCondTime(button.dataset.automationCondTimeTo,"to"));
    $$("[data-automation-cond-point]").forEach(button=>button.onclick=()=>chooseAutomationCondPoint(button.dataset.automationCondPoint));
    $$("[data-automation-cond-sun-step]").forEach(button=>button.onclick=()=>stepAutomationCondSunOffset(button.dataset.automationCondSunStep));
    $$("[data-automation-cond-preset]").forEach(button=>button.onclick=()=>chooseAutomationCondPreset(button.dataset.automationCondPreset));
    $$("[data-automation-cond-day]").forEach(button=>button.onclick=()=>toggleAutomationCondDay(button.dataset.automationCondDay));
    $$("[data-automation-add-cond]").forEach(button=>button.onclick=()=>addAutomationCondition());
    $$("[data-automation-edit-cond]").forEach(button=>button.onclick=()=>editAutomationCondition(Number(button.dataset.automationEditCond)));
    $$("[data-automation-remove-cond]").forEach(button=>button.onclick=()=>removeAutomationCondition(Number(button.dataset.automationRemoveCond),button));
    $$("[data-automation-action-kind]").forEach(button=>button.onclick=()=>chooseAutomationActionKind(button.dataset.automationActionKind));
    $$("[data-automation-delay]").forEach(button=>button.onclick=()=>setAutomationDelay(Number(button.dataset.automationDelay),false));
    $$("[data-automation-delay-custom]").forEach(button=>button.onclick=()=>openAutomationDelayCustom());
    $$("[data-automation-delay-step]").forEach(button=>button.onclick=()=>stepAutomationDelay(Number(button.dataset.automationDelayStep)));
    $$("[data-automation-group]").forEach(button=>button.onclick=()=>chooseAutomationGroup(button.dataset.automationGroup));
    $$("[data-automation-group-value]").forEach(button=>button.onclick=()=>chooseAutomationGroupValue(button.dataset.automationGroupValue));
    $$("[data-automation-scene-group]").forEach(button=>button.onclick=()=>chooseAutomationSceneGroup(button.dataset.automationSceneGroup));
    $$("[data-automation-scene]").forEach(button=>button.onclick=()=>chooseAutomationScene(Number(button.dataset.automationScene)));
    $$("[data-automation-duplicate]").forEach(button=>button.onclick=()=>duplicateAutomationDraft());
    const nameInput=$("#automationName");
    if(nameInput)nameInput.oninput=()=>{wizard.name=nameInput.value.slice(0,64);automationSyncFoot()};
  }
  // Arama her tuşta yalnız listeyi tazeler: odak ve imleç arama kutusunda kalır.
  function automationSearchInput(scope,value){
    const wizard=state.automationWizard;
    if(!wizard)return;
    if(scope==="trigger")wizard.triggerQuery=value;
    else if(scope==="cond")wizard.condQuery=value;
    else wizard.targetQuery=value;
    const list=$(`[data-automation-pick-list="${scope}"]`);
    if(!list)return;
    list.innerHTML=automationPickListHtml(wizard,scope);
    automationBindBody();
  }
  function chooseAutomationTab(token){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const [scope,tab]=String(token).split("|");
    if(scope==="trigger")wizard.triggerTab=tab;
    else if(scope==="cond")wizard.condTab=tab;
    else wizard.targetTab=tab;
    automationRedraw();
  }
  // Seçim yapılınca aktif blok yukarı kayıp söner, yeni blok aşağıdan gelir. Hareket azaltma
  // isteniyorsa geçiş anında uygulanır. Kaydırma yok: akış hep en üstten okunur.
  let automationAdvanceTimer=null;
  function cancelAutomationAdvance(){
    if(automationAdvanceTimer)clearTimeout(automationAdvanceTimer);
    automationAdvanceTimer=null;
  }
  function automationRedraw(){
    // Ekranı yalnız kullanıcı eylemleri tazeler: dokunuldu bilgisi buradan da işaretlenir.
    if(state.automationWizard)state.automationWizard.touched=true;
    // Sayaç basılı tutulurken ekran baştan çizilmez; basılan düğme yerinde kalsın diye.
    if(automationHoldQuiet)return;
    automationAnimate=false;
    renderAutomationWizard();
  }
  // Cihaz seçimi kendi geçişini taşır: blok topluca sönmez, seçilmeyen satırlar solar ve seçilen
  // yerinde kalır. Bayrak yalnız çağrı anında okunur, zamanlayıcı geri döndüğünde değil.
  let automationPickChoosing=false;
  function automationChooseDevice(scope,deviceId,mutate){
    const hook=scope==="cond"?"data-automation-cond-device":"data-automation-trigger-device";
    const list=$(`[data-automation-pick-list="${scope}"]`);
    const chosen=list&&typeof list.querySelector==="function"?list.querySelector(`[${hook}="${deviceId}"]`):null;
    if(!chosen||automationReducedMotion()){automationAdvance(mutate);return}
    list.classList.add("is-choosing");
    chosen.classList.add("is-chosen");
    const active=$("#automationBody [data-automation-active]");
    if(active)active.classList.add("automation-picking");
    automationPickChoosing=true;
    automationAdvance(mutate);
    automationPickChoosing=false;
  }
  function automationAdvance(mutate){
    const wizard=state.automationWizard;
    if(!wizard)return;
    cancelAutomationAdvance();
    wizard.touched=true;
    // Seçim geçişi çalışıyorsa blok söndürülmez: seçilen satırın görünür kalması gerekir.
    const leaving=automationPickChoosing?null:$("#automationBody [data-automation-active]");
    const holding=automationPickChoosing;
    const apply=()=>{
      automationAdvanceTimer=null;
      mutate();
      // Yeni bir adıma girilirken liste her zaman açık gelir; daralma yalnız adım içinde
      // seçim yapıldığında olur (bkz. automationPickedRowHtml).
      wizard.pickerOpen=true;
      automationAnimate=true;
      renderAutomationWizard();
    };
    if((!leaving&&!holding)||automationReducedMotion()){apply();return}
    if(leaving)leaving.classList.add("automation-leaving");
    automationAdvanceTimer=setTimeout(apply,190);
  }
  const automationTriggerReady=wizard=>wizard.triggerKind==="manual"||wizard.triggerKind==="time"||wizard.triggerKind==="sun"?true
    :wizard.triggerKind==="button"?Boolean(wizard.triggerDeviceId&&wizard.triggerAction)
    :automationDeviceKinds.includes(wizard.triggerKind)?Boolean(wizard.triggerDeviceId&&wizard.triggerProperty):false;
  const automationDraftMapReady=wizard=>Boolean(wizard.draftTargetId&&wizard.draftProperty)
    &&!(wizard.draftMapOn==="none"&&wizard.draftMapOff==="none");
  const automationWizardReady=wizard=>automationTriggerReady(wizard)
    &&wizard.targets.length>0
    &&(!automationAutoOffAvailable(wizard)||Boolean(wizard.autoOffTouched));
  // Hedefler dolduktan sonra sıra "sonrası"nda: kural bir şey açmıyorsa kapanma hiç sorulmaz.
  const automationAfterTargets=wizard=>automationAutoOffAvailable(wizard)&&!wizard.autoOffTouched?"autoOff":"name";
  // Tetikleyici tamamlanınca hedef kartı doğrudan açılır. İlk hedef seçilirken isteğe bağlı
  // başlangıç beklemesi aynı kartta görünür; kullanıcı "İleri" ile boş bir ara adım geçmez.
  const automationAfterTrigger=wizard=>wizard.targets.length?automationAfterTargets(wizard):"target";
  const automationNextStage=wizard=>{
    if(wizard.stage==="kind")return wizard.triggerKind==="manual"?automationAfterTrigger(wizard):wizard.triggerKind==="time"?"time":wizard.triggerKind==="sun"?"sun":"trigDevice";
    if(wizard.stage==="time"||wizard.stage==="sun"||wizard.stage==="trigEvent"||wizard.stage==="trigThreshold")return automationAfterTrigger(wizard);
    if(wizard.stage==="trigDevice")return wizard.triggerNumeric?"trigThreshold":"trigEvent";
    // Bekleme adımı atlanabilir: hedef yoksa eylem sorusuna, varsa (düzenlemede) sonrasına geçer.
    if(wizard.stage==="wait")return wizard.targets.length?automationAfterTargets(wizard):"target";
    if(wizard.stage==="target")return"action";
    if(wizard.stage==="action")return"map";
    if(wizard.stage==="group")return"groupAction";
    if(wizard.stage==="scene")return"sceneId";
    if(wizard.stage==="autoOff")return"name";
    return wizard.stage;
  };
  const automationStageAdvanceable=wizard=>{
    if(wizard.stage==="path")return false;
    if(wizard.stage==="kind")return Boolean(wizard.triggerKind);
    if(wizard.stage==="time")return true;
    // Güneş yolunda konum yoksa kural hiç çalışmaz; ileri düğmesi pasif kalır ve sebebi yazar.
    if(wizard.stage==="sun")return !automationSunReason();
    if(wizard.stage==="trigDevice")return Boolean(wizard.triggerDeviceId);
    if(wizard.stage==="trigEvent")return automationTriggerReady(wizard);
    if(wizard.stage==="trigThreshold")return Number.isFinite(wizard.thresholdValue);
    // Bekleme adımı hiçbir zaman engellemez: 0:00 "hemen çalışsın" demektir, tek dokunuşla geçilir.
    if(wizard.stage==="wait")return true;
    if(wizard.stage==="cond")return false;
    if(wizard.stage==="condTime")return Boolean(automationConditionFromDraft(wizard.draftCondition));
    if(wizard.stage==="condDevice")return Boolean(wizard.draftCondition?.deviceId);
    if(wizard.stage==="condState")return Boolean(automationConditionFromDraft(wizard.draftCondition));
    if(wizard.stage==="target")return Boolean(wizard.draftTargetId);
    if(wizard.stage==="delay")return Number.isFinite(wizard.draftDelaySeconds)&&wizard.draftDelaySeconds>=1;
    if(wizard.stage==="group"||wizard.stage==="scene")return Boolean(wizard.draftGroupId);
    if(wizard.stage==="groupAction")return false;
    if(wizard.stage==="sceneId")return wizard.draftSceneId!==null&&wizard.draftSceneId!==undefined;
    // Aç/kapat seçilince adım kendiliğinden ilerler; değer eyleminde "İleri" ayarı kesinleştirir.
    if(wizard.stage==="action")return automationMappingMode(wizard)
      ?Boolean(wizard.draftProperty)
      :Boolean(automationValueDraftControl(wizard));
    if(wizard.stage==="map")return automationDraftMapReady(wizard);
    // Son adımda tek iş kalır: kaydetmek. Eksik varsa düğme pasiftir, gerekçesi yanında yazar.
    if(wizard.stage==="name")return automationWizardReady(wizard);
    return true;
  };
  // Pasif "İleri" sessiz kalmasın: neyin eksik olduğunu düğmenin yanında yazar.
  const automationBlockedReason=wizard=>{
    if(automationStageAdvanceable(wizard))return"";
    if(wizard.stage==="kind")return"automationNeedTrigger";
    if(wizard.stage==="sun")return"automationNeedLocation";
    if(wizard.stage==="trigDevice")return"automationNeedDevice";
    if(wizard.stage==="trigEvent")return"automationNeedEvent";
    if(wizard.stage==="cond")return"automationNeedCondition";
    if(wizard.stage==="condDevice")return"automationNeedDevice";
    if(wizard.stage==="condState")return"automationNeedCondValue";
    if(wizard.stage==="condTime")return"automationNeedCondRange";
    if(wizard.stage==="target")return"automationNeedTarget";
    if(wizard.stage==="group"||wizard.stage==="scene")return"automationNeedGroup";
    if(wizard.stage==="groupAction")return"automationNeedAction";
    if(wizard.stage==="sceneId")return"automationNeedScene";
    if(wizard.stage==="action")return automationMappingMode(wizard)?"automationNeedTarget":"automationNeedAction";
    if(wizard.stage==="map")return"automationNeedMap";
    if(wizard.stage==="name")return wizard.targets.length?"":"automationNeedTarget";
    return"";
  };
  const automationBackStage=wizard=>{
    if(wizard.stage==="time"||wizard.stage==="sun"||wizard.stage==="trigDevice")return"kind";
    if(wizard.stage==="trigEvent"||wizard.stage==="trigThreshold")return"trigDevice";
    if(wizard.stage==="cond"||wizard.stage==="condTime"||wizard.stage==="condDevice")return automationTriggerEditStage(wizard);
    if(wizard.stage==="condState")return"condDevice";
    if(wizard.stage==="wait")return automationTriggerEditStage(wizard);
    // İlk hedef seçiminden geri doğrudan tetikleyiciye döner; bekleme artık hedefle aynı ekrandadır.
    if(wizard.stage==="target")return automationTriggerEditStage(wizard);
    if(wizard.stage==="action")return"target";
    if(wizard.stage==="delay"||wizard.stage==="group"||wizard.stage==="scene")return"target";
    if(wizard.stage==="groupAction")return"group";
    if(wizard.stage==="sceneId")return"scene";
    if(wizard.stage==="map"){
      const device=state.devices.find(item=>item.id===wizard.draftTargetId)||null;
      return device&&automationTargetScope(wizard).targetControls(device).length>1?"action":"target";
    }
    if(wizard.stage==="autoOff")return"target";
    if(wizard.stage==="name")return automationAutoOffAvailable(wizard)?"autoOff":"target";
    return null;
  };
  function automationSyncFoot(){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const paths=wizard.stage==="path";
    const directPick=wizard.stage==="target";
    const ready=automationWizardReady(wizard);
    const advanceable=automationStageAdvanceable(wizard);
    const stageGroup=automationStageGroup(wizard.stage);
    // Düğme artık yalnız “İleri” demez; nereye gidileceğini ya da hangi seçimin beklendiğini söyler.
    const nextKey=wizard.stage==="name"?"automationSaveRule"
      :wizard.stage==="target"?"automationPickDeviceContinue"
      :(wizard.stage==="action"||wizard.stage==="groupAction")&&!advanceable?"automationPickActionContinue"
      :wizard.stage==="autoOff"?"automationNextSave"
      :stageGroup==="then"?"automationCompleteSelection"
      :["time","sun","trigEvent","trigThreshold","condTime","condState","wait"].includes(wizard.stage)?"automationNextActions"
      :"automationContinue";
    for(const next of automationNextButtons()){
      next.hidden=paths;
      next.textContent=t(nextKey);
      next.disabled=paths||directPick||!advanceable;
    }
    // Geri düğmesi de hedefini söyler; alt adımlar kullanıcıya ayrı bölüm gibi gösterilmez.
    const backStage=automationBackStage(wizard);
    const backGroup=automationStageGroup(backStage);
    const backKey=paths||wizard.stage==="kind"?"cancel"
      :backStage==="autoOff"?"automationBackAfter"
      :backGroup==="then"?"automationBackActions":"automationBackTrigger";
    $("#automationBack").textContent=t(backKey);
    const hint=$("#automationNextHint");
    if(!hint)return;
    // Hazır olunca özet cümlesi okunur; değilse neyin eksik olduğu yazar.
    const sentence=!paths&&ready?automationWizardSentence(wizard):"";
    const reason=paths||directPick||ready||((wizard.stage==="action"||wizard.stage==="groupAction")&&!advanceable)?"":automationBlockedReason(wizard);
    hint.textContent=sentence||(reason?t(reason):"");
    hint.classList.toggle("ready",Boolean(sentence));
    hint.hidden=!hint.textContent;
    // Kutu metinle birlikte gizlenir: boş kutu kenarlık+gölgesiyle görünür kalmasın.
    const note=$("#automationNextNote");
    if(note)note.hidden=hint.hidden;
  }
  function renderAutomationWizard(){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const paths=wizard.stage==="path";
    const mapping=automationMappingMode(wizard);
    const group=automationStageGroup(wizard.stage);
    const titles={
      when:"automationWhenTitle",
      cond:"automationCondTitle",
      then:wizard.stage==="wait"?"automationWaitTitle"
        :wizard.stage==="map"?"automationMapTitle":mapping?"automationTargetTitle":"automationThenTitle",
      after:wizard.stage==="autoOff"?"automationAutoOffTitle":"automationReviewTitle"
    };
    const leads={
      when:"automationWhenLead",
      cond:"automationCondLead",
      then:wizard.stage==="wait"?"automationWaitLead"
        :wizard.stage==="map"
        ?(automationSunBoth(wizard)?"automationMapSunLead":"automationMapLead")
        :mapping?"automationTargetLead":"automationThenLead",
      after:wizard.stage==="autoOff"?"automationAutoOffLead":"automationReviewLead"
    };
    $("#automationTitle").textContent=t(paths?"automationPathTitle":titles[group]);
    $("#automationLead").textContent=t(paths?"automationPathLead":leads[group]);
    renderAutomationProgress(wizard,paths);
    const body=$("#automationBody");
    body.classList.toggle("is-path",paths);
    body.innerHTML=paths?automationPathHtml():automationFlowHtml(wizard);
    automationBindBody();
    automationSyncFoot();
    automationAnimate=false;
    wizard.fresh=null;
  }
  function chooseAutomationPath(path){
    const wizard=state.automationWizard;
    if(!wizard)return;
    if(path==="link"){openSimpleLink();return}
    if(path!=="rule")return;
    automationAdvance(()=>{wizard.stage="kind"});
  }
  // Tamamlanmış bir satıra basınca o soru yeniden açılır; seçim silinmez, seçenekler geri gelir.
  function goToAutomationStage(stage){
    const wizard=state.automationWizard;
    if(!wizard||!stage)return;
    automationAdvance(()=>{
      // Yeni koşul yolu temiz başlar: eski düzenlemenin sırası yeni koşulu ezmesin.
      if(stage==="cond"){wizard.draftCondition=null;wizard.draftConditionIndex=null}
      wizard.stage=stage;
    });
  }
  // Daralmış seçim satırındaki "Değiştir": liste geri açılır, seçim silinmez — kullanıcı
  // vazgeçip aynı satırı seçerse hiçbir şey değişmez.
  function reopenAutomationPicker(){
    const wizard=state.automationWizard;
    if(!wizard||wizard.pickerOpen)return;
    wizard.pickerOpen=true;
    automationRedraw();
  }
  function chooseAutomationTrigger(kind){
    const wizard=state.automationWizard;
    const choice=automationTriggerChoices.find(entry=>entry.kind===kind);
    if(!wizard||!choice?.ready)return;
    automationAdvance(()=>{
      if(wizard.triggerKind!==kind){
        // Eşleme yolu ile normal yolun hedef biçimi farklıdır; yol değişince hedefler sıfırlanır.
        // Güneş de eşleme yoludur: iki an, iki yön.
        if(automationMappingMode(wizard)!==(kind==="deviceState"||kind==="sun"))wizard.targets=[];
        automationClearTriggerDevice(wizard);
      }
      wizard.triggerKind=kind;
      wizard.triggerQuery="";
      wizard.triggerTab="all";
      wizard.stage=kind==="manual"?automationAfterTrigger(wizard):kind==="time"?"time":kind==="sun"?"sun":"trigDevice";
    });
  }
  function automationClearTriggerDevice(wizard){
    wizard.triggerDeviceId=null;
    wizard.triggerAction=null;
    wizard.triggerProperty=null;
    wizard.triggerEquals=null;
    wizard.triggerNumeric=false;
  }
  // §8.2 — tetikleyici değişince onun kendi kanalını çalıştıran hedef düşer; döngü hiç kurulmaz.
  function automationPruneTargets(wizard){
    if(wizard.triggerKind==="manual"||wizard.triggerKind==="time")return;
    if(wizard.triggerKind==="button"){
      wizard.targets=wizard.targets.filter(target=>target.deviceId!==wizard.triggerDeviceId);
      return;
    }
    if(!wizard.triggerProperty)return;
    const starter=automationChannelKey(wizard.triggerDeviceId,wizard.triggerProperty);
    wizard.targets=wizard.targets.filter(target=>automationChannelKey(target.deviceId,target.property)!==starter);
  }
  function automationApplyEvent(wizard,event){
    // Kanonik kayıt: düğme için `action`, sensör için `property`+`equals`. Etiket yalnızca sunum.
    wizard.triggerAction=wizard.triggerKind==="button"?event.action:null;
    wizard.triggerProperty=wizard.triggerKind==="button"?null:event.property;
    wizard.triggerNumeric=Boolean(event.numeric);
    wizard.triggerEquals=wizard.triggerKind==="button"||event.numeric?null:event.equals;
    if(event.numeric){
      const device=state.devices.find(item=>item.id===wizard.triggerDeviceId)||null;
      const current=device?.state?.[event.property];
      // Eşik kullanıcının bugünkü değerinin yakınından başlar; sıfırdan tırmandırmak zorunda kalmasın.
      if(Number.isFinite(current)&&wizard.thresholdValue===0)wizard.thresholdValue=Math.round(current);
    }
  }
  // Anahtar yolunda durum sorulmaz: tek kanallı anahtarda kanal da sorulmaz, doğrudan hedefe geçilir.
  function automationApplySingleChannel(wizard,device){
    const controls=automationStateControls(device);
    if(controls.length===1)wizard.triggerProperty=controls[0].property;
  }
  function chooseAutomationTriggerDevice(deviceId){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationChooseDevice("trigger",deviceId,()=>{
      automationClearTriggerDevice(wizard);
      wizard.triggerDeviceId=deviceId;
      wizard.triggerQuery="";
      const device=state.devices.find(item=>item.id===deviceId)||null;
      if(automationMappingMode(wizard)){
        automationApplySingleChannel(wizard,device);
      }else{
        const events=automationTriggerRows(device,wizard.triggerKind);
        // Tek anlamlı seçenek varsa sessizce seçilir. Kanıtsız cihazda bu atlama kapalı:
        // uyarı ve alternatif okunmadan adım geçilmesin.
        if(events.length===1&&!automationButtonUnproven(wizard,device))automationApplyEvent(wizard,events[0]);
      }
      automationPruneTargets(wizard);
      const unproven=automationButtonUnproven(wizard,device);
      // Sayısal özellikte sıra eşik sorusuna gelir; oraya kadar tetikleyici tamam sayılmaz.
      if(!unproven&&wizard.triggerNumeric){wizard.stage="trigThreshold";return}
      if(!unproven&&automationTriggerReady(wizard)){
        wizard.fresh="trigger";
        wizard.stage=automationAfterTrigger(wizard);
        return;
      }
      wizard.stage="trigEvent";
    });
  }
  // Durum bildiren cihazda düğme yolundan "açılınca/kapanınca" yoluna geçiş: cihaz seçimi korunur.
  function automationUseStateInstead(deviceId){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationAdvance(()=>{
      wizard.targets=[];
      automationClearTriggerDevice(wizard);
      wizard.triggerKind="deviceState";
      wizard.triggerDeviceId=deviceId;
      const device=state.devices.find(item=>item.id===deviceId)||null;
      automationApplySingleChannel(wizard,device);
      if(automationTriggerReady(wizard)){
        wizard.fresh="trigger";
        wizard.stage=automationAfterTrigger(wizard);
        return;
      }
      wizard.stage="trigEvent";
    });
  }
  function chooseAutomationChannel(property){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationAdvance(()=>{
      wizard.triggerProperty=property;
      wizard.triggerEquals=null;
      automationPruneTargets(wizard);
      wizard.fresh="trigger";
      wizard.stage=automationAfterTrigger(wizard);
    });
  }
  function chooseAutomationEvent(token){
    const wizard=state.automationWizard;
    const device=wizard?state.devices.find(item=>item.id===wizard.triggerDeviceId)||null:null;
    const event=automationTriggerRows(device,wizard?.triggerKind,wizard?.triggerAction).find(item=>item.token===token);
    if(!event)return;
    // Seçim engellenmez ama uyarı bir kez daha yüzeye çıkar: kullanıcı sonucu bilerek ilerlesin.
    if(automationButtonUnproven(wizard,device))showToast(t("automationButtonUnprovenWarning"),true);
    // §2.1 — süre satırı listenin altındadır: seçim adımı kapatsaydı satıra hiç ulaşılamazdı
    // (açmak için ekranda kalmak, kalmak için açmış olmak gerekirdi). Bu yüzden satırın
    // uygulanabilir olduğu her yolda adım açık kalır: seçilen olay işaretli durur, süre
    // erişilebilir kalır, kullanıcı alttaki birincil düğmeyle geçer. Sayısal eşik yolu eşik
    // ekranına gider; satır orada da var. Koşul adımındaki davranışın aynısı.
    if(automationTrigForEligible(wizard)&&!event.numeric){
      automationApplyEvent(wizard,event);
      automationPruneTargets(wizard);
      wizard.fresh="trigger";
      // Adım açık kalır ama liste daralır: yalnız seçilen satır + "Değiştir" görünür.
      wizard.pickerOpen=false;
      automationRedraw();
      return;
    }
    automationAdvance(()=>{
      automationApplyEvent(wizard,event);
      automationPruneTargets(wizard);
      if(wizard.triggerNumeric){wizard.stage="trigThreshold";return}
      wizard.fresh="trigger";
      wizard.stage=automationAfterTrigger(wizard);
    });
  }
  function chooseAutomationThresholdDir(direction){
    const wizard=state.automationWizard;
    if(!wizard||(direction!=="above"&&direction!=="below"))return;
    wizard.thresholdDir=direction;
    automationRedraw();
  }
  function stepAutomationThreshold(amount){
    const wizard=state.automationWizard;
    if(!wizard||!Number.isFinite(amount))return;
    const next=Math.round((wizard.thresholdValue+amount)*1000)/1000;
    wizard.thresholdValue=next;
    automationRedraw();
  }
  /* Elle girilen eşik. Adım otomatik olduğu için tam değer yazılabilmeli; okunamayan bir giriş
     sessizce kabul edilmez, ekran son geçerli değere döner (virgül de ondalık ayracı sayılır). */
  const automationTypedNumber=raw=>{
    // Boş alan sıfır değildir: `Number("")` sıfır döndüğü için ayrıca elenir.
    const text=String(raw??"").trim().replace(",",".");
    if(!text)return null;
    const parsed=Number(text);
    return Number.isFinite(parsed)?Math.round(parsed*1000)/1000:null;
  };
  function setAutomationThresholdValue(raw){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const next=automationTypedNumber(raw);
    if(next!==null)wizard.thresholdValue=next;
    automationRedraw();
  }
  function setAutomationCondThreshold(edge,raw){
    const draft=state.automationWizard?.draftCondition;
    if(!draft||(edge!=="above"&&edge!=="below"))return;
    const next=automationTypedNumber(raw);
    if(next!==null)draft[edge]=next;
    automationRedraw();
  }
  // §2.6 — pencere seçimi. 0 = sınırsız, yani alan hiç yazılmaz ve eski davranış geri gelir.
  function chooseAutomationCondFresh(value){
    const draft=state.automationWizard?.draftCondition;
    if(!draft)return;
    const seconds=Number(value);
    draft.freshWithinSeconds=Number.isFinite(seconds)&&seconds>0
      ?Math.min(seconds,maxAutomationCondFreshSeconds)
      :null;
    automationRedraw();
  }
  // Hangi anın ayarlandığını değiştirir; seçim silinmez, ayarlar ana özeldir.
  function chooseAutomationSunEdit(event){
    const wizard=state.automationWizard;
    if(!wizard||(event!=="sunrise"&&event!=="sunset"))return;
    wizard.sunEvent=event;
    automationRedraw();
  }
  function setAutomationSunOffset(value){
    const wizard=state.automationWizard;
    if(!wizard||!Number.isFinite(value))return;
    // Motor ±240 dakikayı reddeder; arayüz sınırı aşan bir değer hiç üretmez.
    const offset=Math.max(-maxAutomationSunOffset,Math.min(maxAutomationSunOffset,Math.round(value)));
    if(automationSunEditing(wizard)==="sunrise")wizard.sunriseOffset=offset;
    else wizard.sunOffset=offset;
    automationRedraw();
  }
  function stepAutomationTime(unit,amount){
    const wizard=state.automationWizard;
    if(!wizard||!Number.isFinite(amount))return;
    if(unit==="hour")wizard.hour=(wizard.hour+amount+24)%24;
    else wizard.minute=(wizard.minute+amount+60)%60;
    automationRedraw();
  }
  function toggleAutomationDay(value){
    const wizard=state.automationWizard;
    if(!wizard)return;
    // İki olaylı güneş yolunda gün çipleri o an ayarlanan ana aittir; öbür an dokunulmaz.
    const sunrise=automationSunBoth(wizard)&&automationSunEditing(wizard)==="sunrise";
    const current=sunrise?wizard.sunriseDays:wizard.days;
    let next;
    if(value==="all")next=[...automationWeekDays];
    else{
      const day=Number(value);
      if(automationEveryDay(current))next=[day];
      else{
        const days=current.includes(day)?current.filter(item=>item!==day):[...current,day];
        next=days.length?days:[day];
      }
    }
    if(sunrise)wizard.sunriseDays=next;
    else wizard.days=next;
    automationRedraw();
  }
  // Hedefte de önce cihaz seçilir, sonra alt öğesi. Eşleme yolunda tek kanallı cihazda kanal
  // sorusu hiç açılmaz: kanal sessizce seçilip eşleme formuna geçilir.
  function chooseAutomationTargetDevice(deviceId){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const scope=automationTargetScope(wizard);
    const device=scope.devices.find(item=>item.id===deviceId)||null;
    if(!device)return;
    const controls=scope.targetControls(device);
    automationAdvance(()=>{
      wizard.draftTargetId=deviceId;
      wizard.draftProperty=null;
      wizard.draftControlId=null;
      wizard.draftValueTarget=null;
      wizard.draftValue=null;
      wizard.targetQuery="";
      if(automationMappingMode(wizard)&&controls.length===1){
        wizard.draftProperty=controls[0].property;
        wizard.draftControlId=controls[0].id;
        if(!automationCanToggle(controls[0])){
          if(wizard.draftMapOn==="toggle")wizard.draftMapOn="on";
          if(wizard.draftMapOff==="toggle")wizard.draftMapOff="off";
        }
        wizard.stage="map";
        return;
      }
      wizard.stage="action";
    });
  }
  // Aynı kanal iki kez yazılmaz: yeni seçim eskisinin yerine geçer, eylem listesi şişmez.
  function automationCommitTarget(wizard,target){
    const key=automationChannelKey(target.deviceId,target.property);
    const index=wizard.targets.findIndex(item=>automationChannelKey(item.deviceId,item.property)===key);
    if(index>=0)wizard.targets.splice(index,1);
    wizard.targets.push(target);
    wizard.fresh=`target-${wizard.targets.length-1}`;
    wizard.draftTargetId=null;
    wizard.draftProperty=null;
    wizard.draftControlId=null;
    wizard.draftValueTarget=null;
    wizard.draftValue=null;
    wizard.targetQuery="";
    wizard.targetTab="all";
  }
  function chooseAutomationAction(token){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const [deviceId,controlId,mode]=String(token).split("|");
    const device=state.devices.find(item=>item.id===deviceId);
    const control=device?automationControls(device).find(item=>item.id===controlId):null;
    if(!control)return;
    if(mode==="toggle"&&!automationCanToggle(control))return;
    const value=mode==="toggle"?control.valueToggle:automationControlValue(control,mode==="on");
    automationAdvance(()=>{
      automationCommitTarget(wizard,{deviceId,property:control.property,controlId:control.id,value});
      wizard.stage=automationAfterTargets(wizard);
    });
  }
  // Değer seçeneği (parlaklık / ışık sıcaklığı / renk) ayarlanacak kumandayı açar. İkinci dokunuş
  // kapatır: aç/kapat yine varsayılan yoldur, değer ona alternatiftir.
  function chooseAutomationValue(token){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const [deviceId,controlId]=String(token).split("|");
    const control=automationValueControl(deviceId,controlId);
    if(!control)return;
    if(automationValueOpen(wizard,deviceId,controlId)){
      wizard.draftValueTarget=null;
      wizard.draftValue=null;
      automationRedraw();
      return;
    }
    wizard.draftValueTarget={deviceId,controlId};
    wizard.draftValue=automationValueSeed(control);
    automationRedraw();
  }
  /* "Tetikleyeni izle" tek dokunuşta biter: ayarlanacak bir değer yok, hedef doğrudan yazılır.
     Kayda yine bir `value` yazılır — tetikleyenin değeri çözülemezse motorun düşeceği yedek odur. */
  function chooseAutomationFollow(token){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const [deviceId,controlId]=String(token).split("|");
    const control=automationValueControl(deviceId,controlId);
    if(!control||!automationFollowAvailable(wizard,control))return;
    automationAdvance(()=>{
      automationCommitTarget(wizard,{
        deviceId,property:control.property,controlId:control.id,
        value:automationValueSeed(control),follow:{mode:automationFollowMode(control)}
      });
      wizard.stage=automationAfterTargets(wizard);
    });
  }
  function stepAutomationValue(direction){
    const wizard=state.automationWizard;
    const control=automationValueDraftControl(wizard);
    if(!control||control.kind==="color"||isAutomationEnumControl(control))return;
    if(control.kind==="level"||control.kind==="temperature"){
      const percent=automationValuePercent(control,wizard.draftValue)+Number(direction)*automationValuePercentStep;
      wizard.draftValue=automationValueRaw(control,percent);
    }else{
      const current=Number(wizard.draftValue);
      const range=automationValueRange(control);
      const span=Math.abs(range.max-range.min);
      const step=Number(control.step)>0?Number(control.step):(span>0?Math.max(span/10,1):1);
      const next=(Number.isFinite(current)?current:range.min)+Number(direction)*step;
      wizard.draftValue=Math.min(range.max,Math.max(range.min,Math.round(next*1000)/1000));
    }
    automationRedraw();
  }
  function setAutomationEnumValue(encoded){
    const wizard=state.automationWizard;
    const control=automationValueDraftControl(wizard);
    if(!control||!isAutomationEnumControl(control))return;
    let value;
    try{value=JSON.parse(decodeURIComponent(String(encoded||"")))}catch{return}
    if(!automationControlValues(control).some(option=>String(option)===String(value)))return;
    wizard.draftValue=value;
    automationRedraw();
  }
  function setAutomationValueColor(hex){
    const wizard=state.automationWizard;
    const control=automationValueDraftControl(wizard);
    if(!control||control.kind!=="color")return;
    const value=String(hex||"").toLowerCase();
    if(!/^#[0-9a-f]{6}$/.test(value))return;
    wizard.draftValue=value;
    automationRedraw();
  }
  // Aç/kapat tek dokunuşla biter; değer önce ayarlanır, "İleri" ile kesinleşir.
  function automationCommitValueTarget(){
    const wizard=state.automationWizard;
    const control=automationValueDraftControl(wizard);
    if(!control)return;
    const deviceId=wizard.draftValueTarget.deviceId;
    automationAdvance(()=>{
      automationCommitTarget(wizard,{deviceId,property:control.property,controlId:control.id,value:wizard.draftValue});
      wizard.stage=automationAfterTargets(wizard);
    });
  }
  // Anahtar yolunda burada yalnız hedef kanal seçilir; değer eşleme formunda belirlenir.
  function chooseAutomationTarget(token){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const [deviceId,controlId]=String(token).split("|");
    const device=state.devices.find(item=>item.id===deviceId);
    const control=device?automationControls(device).find(item=>item.id===controlId):null;
    if(!control)return;
    automationAdvance(()=>{
      wizard.draftTargetId=deviceId;
      wizard.draftProperty=control.property;
      wizard.draftControlId=control.id;
      // Hedef "Değiştir"i desteklemiyorsa eşleme takip yönüne düşer.
      if(!automationCanToggle(control)){
        if(wizard.draftMapOn==="toggle")wizard.draftMapOn="on";
        if(wizard.draftMapOff==="toggle")wizard.draftMapOff="off";
      }
      wizard.stage="map";
    });
  }
  function chooseAutomationMap(token){
    const wizard=state.automationWizard;
    if(!wizard)return;
    const [direction,mode]=String(token).split("|");
    if(!automationMapModes.includes(mode))return;
    // Güneş yolunda "Değiştir" anlamsız: iki olay da aynı şeyi yapardı (§9.1).
    if(mode==="toggle"&&automationSunBoth(wizard))return;
    if(direction==="on")wizard.draftMapOn=mode;
    else if(direction==="off")wizard.draftMapOff=mode;
    else return;
    automationRedraw();
  }
  function automationCommitMapTarget(){
    const wizard=state.automationWizard;
    if(!wizard||!automationDraftMapReady(wizard))return;
    automationAdvance(()=>{
      automationCommitTarget(wizard,{
        deviceId:wizard.draftTargetId,property:wizard.draftProperty,controlId:wizard.draftControlId,
        mapOn:wizard.draftMapOn,mapOff:wizard.draftMapOff
      });
      wizard.draftMapOn="on";
      wizard.draftMapOff="off";
      wizard.stage=automationAfterTargets(wizard);
    });
  }
  // ————— koşul akışı. Taslak tamamlanınca listeye eklenir; en fazla dört koşul (sunucu sınırı).
  function addAutomationCondition(){
    const wizard=state.automationWizard;
    if(!wizard||wizard.conditions.length>=maxAutomationConditions)return;
    automationAdvance(()=>{wizard.draftCondition=null;wizard.stage="cond"});
  }
  function chooseAutomationCondKind(kind){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationAdvance(()=>{
      if(kind==="timeRange"){
        wizard.draftCondition={
          type:"timeRange",
          from:{kind:"clock",hour:22,minute:0,event:"sunset",offset:0},
          to:{kind:"clock",hour:6,minute:0,event:"sunrise",offset:0},
          days:[...automationWeekDays],index:wizard.draftConditionIndex
        };
        wizard.stage="condTime";
        return;
      }
      if(kind!=="deviceState"&&kind!=="deviceStateFor"&&kind!=="deviceMeasurement")return;
      // Ölçüm yolunda ölçüm bildiren cihaz yoksa adım hiç açılmaz: boş liste göstermek yerine
      // seçenek zaten kapalı çizilir, buradaki kontrol o kapının ikinci kanadıdır.
      if(kind==="deviceMeasurement"&&!automationHasNumericDevices())return;
      // Süreli yol aynı taslağı kurar; yalnız süre satırı bir dakikayla açık başlar.
      // Ölçüm yolu da aynı taslak: `numericOnly` yalnız cihaz ve okuma listesini daraltır.
      wizard.draftCondition={type:"deviceState",deviceId:null,property:null,value:null,negate:false,numeric:false,numericOnly:kind==="deviceMeasurement",thresholdDir:"above",above:0,below:0,forSeconds:kind==="deviceStateFor"?60:null,freshWithinSeconds:null,index:wizard.draftConditionIndex};
      wizard.condQuery="";
      wizard.condTab="all";
      wizard.stage="condDevice";
    });
  }
  // Sayısal özellikte hazır değer yoktur: karşılaştırma satırı bugünkü okumanın yakınından başlar.
  function automationSeedCondNumeric(draft,device,property){
    const current=Number(device?.state?.[property]);
    const seed=Number.isFinite(current)?Math.round(current):0;
    draft.numeric=true;
    draft.property=property;
    draft.value=null;
    draft.negate=false;
    draft.thresholdDir="above";
    draft.above=seed;
    draft.below=seed;
  }
  function chooseAutomationCondDevice(deviceId){
    const wizard=state.automationWizard;
    if(!wizard||!wizard.draftCondition)return;
    automationChooseDevice("cond",deviceId,()=>{
      const draft=wizard.draftCondition;
      draft.deviceId=deviceId;
      draft.property=null;
      draft.value=null;
      draft.numeric=false;
      /* Ölçüm yolunda tek okuması olan cihaz seçilince ara adım sormaz: kullanıcı zaten
         "bir ölçüm" dedi, cihazın tek ölçümü var — doğrudan karşılaştırma ekranı açılır.
         Birden çok okuması varsa liste gelir, çünkü orada gerçek bir seçim vardır. */
      if(draft.numericOnly){
        const device=state.devices.find(item=>item.id===deviceId)||null;
        const numeric=automationNumericProperties(device);
        if(numeric.length===1)automationSeedCondNumeric(draft,device,numeric[0]);
      }
      wizard.condQuery="";
      wizard.stage="condState";
    });
  }
  // × ile seçim geri alınır: liste tamamen geri gelir, süre ölçütü gibi öbür cevaplar korunur.
  function clearAutomationPickedDevice(scope){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationAdvance(()=>{
      if(scope==="cond"){
        if(!wizard.draftCondition)return;
        wizard.draftCondition.deviceId=null;
        wizard.draftCondition.property=null;
        wizard.draftCondition.value=null;
        wizard.draftCondition.numeric=false;
        wizard.condQuery="";
        wizard.stage="condDevice";
        return;
      }
      automationClearTriggerDevice(wizard);
      wizard.triggerQuery="";
      wizard.stage="trigDevice";
    });
  }
  function chooseAutomationCondState(token){
    const wizard=state.automationWizard;
    const draft=wizard?.draftCondition;
    if(!draft)return;
    const device=state.devices.find(item=>item.id===draft.deviceId)||null;
    const row=automationConditionAllRows(device).find(item=>item.token===token);
    if(!row)return;
    if(row.numeric){
      automationSeedCondNumeric(draft,device,row.property);
      automationRedraw();
      return;
    }
    draft.numeric=false;
    draft.property=row.property;
    draft.value=row.equals;
    // Süre satırı listenin altındadır: seçim adımı kapatsaydı satıra hiç ulaşılamazdı. Adım
    // açık kalır, liste seçilen satıra daralır, kullanıcı alttaki birincil düğmeyle kesinleştirir.
    wizard.pickerOpen=false;
    automationRedraw();
  }
  // §9.2 — süre ölçütünü aç/kapat. Açılışta bir dakika: en sık kurulan "1 dakikadır hareket var".
  function toggleAutomationCondFor(value){
    const draft=state.automationWizard?.draftCondition;
    if(!draft)return;
    draft.forSeconds=value==="1"?60:null;
    automationRedraw();
  }
  // Sayaç 0:01 altına inmez, 24:00 üstüne çıkmaz; satır kapatılınca alan hiç yazılmaz.
  function setAutomationCondForSeconds(seconds){
    const draft=state.automationWizard?.draftCondition;
    if(!draft||!Number.isFinite(seconds))return;
    draft.forSeconds=Math.max(60,Math.min(maxAutomationCondForSeconds,Math.round(seconds)));
    automationRedraw();
  }
  // §2.1 — tetikleyicideki süre ölçütü. Koşuldakiyle aynı davranış: açılışta bir dakika.
  function toggleAutomationTrigFor(value){
    const wizard=state.automationWizard;
    if(!wizard)return;
    wizard.triggerForSeconds=value==="1"?60:null;
    automationRedraw();
  }
  function setAutomationTrigForSeconds(seconds){
    const wizard=state.automationWizard;
    if(!wizard||!Number.isFinite(seconds))return;
    wizard.triggerForSeconds=Math.max(60,Math.min(maxAutomationCondForSeconds,Math.round(seconds)));
    automationRedraw();
  }
  function chooseAutomationCondNegate(value){
    const wizard=state.automationWizard;
    if(!wizard?.draftCondition)return;
    wizard.draftCondition.negate=value==="1";
    automationRedraw();
  }
  // §2.4 — anahtar kuralın kendisine ait; taslak koşula değil.
  function chooseAutomationCondMode(mode){
    const wizard=state.automationWizard;
    if(!wizard)return;
    wizard.conditionMode=mode==="any"?"any":"all";
    automationRedraw();
  }
  function chooseAutomationCondThresholdDir(direction){
    const wizard=state.automationWizard;
    const draft=wizard?.draftCondition;
    if(!draft||!["above","below","between"].includes(direction))return;
    draft.thresholdDir=direction;
    // "Arasında"da iki uç birbirine eşit kalırsa ölçüt hiç sağlanmaz; üst uç bir adım açılır.
    if(direction==="between"&&!(Number(draft.above)<Number(draft.below))){
      const device=state.devices.find(item=>item.id===draft.deviceId)||null;
      draft.below=Math.round((Number(draft.above)+automationThresholdStepFor(device,draft.property))*1000)/1000;
    }
    automationRedraw();
  }
  function stepAutomationCondThreshold(token){
    const wizard=state.automationWizard;
    const draft=wizard?.draftCondition;
    if(!draft)return;
    const [edge,rawAmount]=String(token).split(":");
    const amount=Number(rawAmount);
    if(!Number.isFinite(amount)||(edge!=="above"&&edge!=="below"))return;
    draft[edge]=Math.round((Number(draft[edge])+amount)*1000)/1000;
    automationRedraw();
  }
  function stepAutomationCondTime(token,which){
    const wizard=state.automationWizard;
    const edge=wizard?.draftCondition?.[which==="to"?"to":"from"];
    if(!edge)return;
    const [unit,rawAmount]=String(token).split(":");
    const amount=Number(rawAmount);
    if(!Number.isFinite(amount))return;
    if(unit==="hour")edge.hour=(edge.hour+amount+24)%24;
    else edge.minute=(edge.minute+amount+60)%60;
    automationRedraw();
  }
  // §2.3 — ucun türü. Güneş uçları konum girilmeden seçilemez; kilit hem düğmede hem burada.
  function chooseAutomationCondPoint(token){
    const wizard=state.automationWizard;
    const [which,kind]=String(token).split(":");
    const edge=wizard?.draftCondition?.[which==="to"?"to":"from"];
    if(!edge||!["clock","sunrise","sunset"].includes(kind))return;
    if(kind!=="clock"&&automationSunReason())return;
    if(kind==="clock")edge.kind="clock";
    else{edge.kind="sun";edge.event=kind}
    automationRedraw();
  }
  function stepAutomationCondSunOffset(token){
    const wizard=state.automationWizard;
    const [which,rawAmount]=String(token).split(":");
    const edge=wizard?.draftCondition?.[which==="to"?"to":"from"];
    const amount=Number(rawAmount);
    if(!edge||!Number.isFinite(amount))return;
    // Sunucu ±240 dakikayı reddeder; arayüz sınırı aşan bir değer hiç üretmez.
    const next=Math.round((Number(edge.offset)||0)+amount);
    edge.offset=Math.max(-maxAutomationSunOffset,Math.min(maxAutomationSunOffset,next));
    automationRedraw();
  }
  // Hazır aralık: uçları doldurur, kullanıcı sonra tek tek değiştirebilir.
  function chooseAutomationCondPreset(name){
    const wizard=state.automationWizard;
    const draft=wizard?.draftCondition;
    if(!draft)return;
    if(name==="custom"){
      draft.from.kind="clock";
      draft.to.kind="clock";
      automationRedraw();
      return;
    }
    if((name!=="dark"&&name!=="daylight")||automationSunReason())return;
    draft.from.kind="sun";
    draft.to.kind="sun";
    draft.from.event=name==="dark"?"sunset":"sunrise";
    draft.to.event=name==="dark"?"sunrise":"sunset";
    draft.from.offset=0;
    draft.to.offset=0;
    automationRedraw();
  }
  function toggleAutomationCondDay(value){
    const wizard=state.automationWizard;
    const draft=wizard?.draftCondition;
    if(!draft)return;
    if(value==="all")draft.days=[...automationWeekDays];
    else{
      const day=Number(value);
      if(automationEveryDay(draft.days))draft.days=[day];
      else{
        const days=draft.days.includes(day)?draft.days.filter(item=>item!==day):[...draft.days,day];
        draft.days=days.length?days:[day];
      }
    }
    automationRedraw();
  }
  function commitAutomationCondition(){
    const wizard=state.automationWizard;
    const condition=automationConditionFromDraft(wizard?.draftCondition);
    if(!condition)return;
    const index=wizard.draftCondition.index;
    automationAdvance(()=>{
      if(Number.isInteger(index)&&wizard.conditions[index])wizard.conditions[index]=condition;
      else wizard.conditions.push(condition);
      wizard.fresh=`cond-${Number.isInteger(index)?index:wizard.conditions.length-1}`;
      wizard.draftCondition=null;
      wizard.draftConditionIndex=null;
      wizard.stage=wizard.targets.length?automationAfterTargets(wizard):"target";
    });
  }
  function editAutomationCondition(index){
    const wizard=state.automationWizard;
    const condition=wizard?.conditions?.[index];
    if(!condition)return;
    automationAdvance(()=>{
      wizard.draftCondition=automationConditionDraft(condition,index);
      wizard.draftConditionIndex=index;
      wizard.stage=condition.type==="timeRange"?"condTime":"condState";
    });
  }
  function removeAutomationCondition(index,button){
    const wizard=state.automationWizard;
    if(!wizard||!wizard.conditions[index])return;
    const node=button?.closest?.(".automation-node")||null;
    const drop=()=>{wizard.conditions.splice(index,1);automationRedraw()};
    cancelAutomationAdvance();
    if(!node||automationReducedMotion()){drop();return}
    node.classList.add("automation-leaving");
    automationAdvanceTimer=setTimeout(()=>{automationAdvanceTimer=null;drop()},190);
  }
  // ————— cihaz dışı eylemler. Hepsi hedef listesine sıradaki satır olarak girer.
  function chooseAutomationActionKind(kind){
    const wizard=state.automationWizard;
    if(!wizard)return;
    if((kind==="group"||kind==="scene")&&!(state.zigbeeGroups||[]).length)return;
    automationAdvance(()=>{
      wizard.draftGroupId=null;
      wizard.draftSceneId=null;
      wizard.stage=kind==="delay"?"delay":kind==="group"?"group":"scene";
    });
  }
  // Bekleme sayacını aç/kapat. Kapatınca süre sıfırlanır: kural yine "hemen çalışsın" olur.
  function toggleAutomationWait(value){
    const wizard=state.automationWizard;
    if(!wizard)return;
    wizard.waitOpen=value==="1";
    if(value!=="1")wizard.triggerWaitSeconds=0;
    automationRedraw();
  }
  // Tetikleyiciden sonraki bekleme: sıfıra inebilir (hemen çalışsın), tavanı sunucununkiyle aynı.
  function setAutomationWaitSeconds(value){
    const wizard=state.automationWizard;
    if(!wizard||!Number.isFinite(value))return;
    wizard.triggerWaitSeconds=Math.min(maxAutomationDelaySeconds,Math.max(0,Math.round(value)));
    // Süre verildiyse sayaç açıktır; sıfıra inen sayaç kapanmaz, kullanıcı kendi kapatır.
    if(wizard.triggerWaitSeconds>0)wizard.waitOpen=true;
    automationRedraw();
  }
  function setAutomationDelay(value,custom){
    const wizard=state.automationWizard;
    if(!wizard||!Number.isFinite(value))return;
    wizard.draftDelaySeconds=Math.min(maxAutomationDelaySeconds,Math.max(1,Math.round(value)));
    wizard.draftDelayCustom=Boolean(custom);
    automationRedraw();
  }
  const stepAutomationDelay=amount=>setAutomationDelay((state.automationWizard?.draftDelaySeconds??0)+amount,true);
  function openAutomationDelayCustom(){
    const wizard=state.automationWizard;
    if(!wizard)return;
    wizard.draftDelayCustom=true;
    automationRedraw();
  }
  function commitAutomationTargetEntry(target){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationAdvance(()=>{
      const index=wizard.draftTargetIndex;
      if(Number.isInteger(index)&&index>=0&&index<=wizard.targets.length)wizard.targets.splice(index,0,target);
      else wizard.targets.push(target);
      wizard.fresh=`target-${wizard.targets.length-1}`;
      wizard.draftTargetIndex=null;
      wizard.draftWaitBefore=false;
      wizard.draftGroupId=null;
      wizard.draftSceneId=null;
      wizard.stage=automationAfterTargets(wizard);
    });
  }
  const commitAutomationDelay=()=>{
    const wizard=state.automationWizard;
    if(!wizard)return;
    commitAutomationTargetEntry({kind:"delay",seconds:wizard.draftDelaySeconds});
  };
  function chooseAutomationGroup(groupId){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationAdvance(()=>{wizard.draftGroupId=groupId;wizard.stage="groupAction"});
  }
  function chooseAutomationGroupValue(mode){
    const wizard=state.automationWizard;
    if(!wizard||!wizard.draftGroupId)return;
    commitAutomationTargetEntry({kind:"group",groupId:wizard.draftGroupId,property:"state",value:mode==="off"?"OFF":"ON"});
  }
  function chooseAutomationSceneGroup(groupId){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationAdvance(()=>{wizard.draftGroupId=groupId;wizard.stage="sceneId"});
  }
  function chooseAutomationScene(sceneId){
    const wizard=state.automationWizard;
    if(!wizard||!wizard.draftGroupId||!Number.isFinite(sceneId))return;
    commitAutomationTargetEntry({kind:"scene",groupId:wizard.draftGroupId,sceneId});
  }
  function addAutomationTarget(){
    const wizard=state.automationWizard;
    if(!wizard)return;
    automationAdvance(()=>{
      wizard.draftTargetId=null;
      wizard.draftProperty=null;
      wizard.draftControlId=null;
      wizard.draftValueTarget=null;
      wizard.draftValue=null;
      wizard.targetQuery="";
      wizard.targetTab="all";
      wizard.draftTargetIndex=null;
      wizard.draftWaitBefore=false;
      wizard.stage="target";
    });
  }
  function addAutomationWaitBefore(index){
    const wizard=state.automationWizard;
    if(!wizard||!wizard.targets[index]||wizard.targets.length>=automationMaxTargets(wizard))return;
    automationAdvance(()=>{
      wizard.draftTargetIndex=index;
      wizard.draftWaitBefore=true;
      wizard.draftDelaySeconds=10;
      wizard.draftDelayCustom=false;
      wizard.stage="delay";
    });
  }
  // Hedef satırına tıklanınca o hedef taslağa döner ve seçenekleri yeniden açılır.
  function editAutomationTarget(index){
    const wizard=state.automationWizard;
    const target=wizard?.targets?.[index];
    if(!target)return;
    const kind=automationTargetKind(target);
    automationAdvance(()=>{
      wizard.targets.splice(index,1);
      // Satır yerinde düzenlenir: kaydedilince eski sırasına geri döner, eylem sırası korunur.
      wizard.draftTargetIndex=index;
      wizard.draftWaitBefore=false;
      wizard.targetQuery="";
      if(kind==="delay"){
        wizard.draftDelaySeconds=target.seconds;
        wizard.draftDelayCustom=!automationDelayPresets.includes(target.seconds);
        wizard.stage="delay";
        return;
      }
      if(kind==="group"){wizard.draftGroupId=target.groupId;wizard.stage="groupAction";return}
      if(kind==="scene"){wizard.draftGroupId=target.groupId;wizard.draftSceneId=target.sceneId;wizard.stage="sceneId";return}
      wizard.draftTargetIndex=null;
      wizard.draftTargetId=target.deviceId;
      wizard.draftProperty=target.property;
      wizard.draftControlId=target.controlId;
      wizard.draftMapOn=target.mapOn||"on";
      wizard.draftMapOff=target.mapOff||"off";
      // Değer eylemi düzenlemeye ayarlayıcısı açık gelir: satıra basınca sayaç/renk seçici hazır.
      // İzleyen hedefte ayarlanacak bir değer yoktur: sayaç açılmaz, seçenekler yeniden sorulur.
      const control=automationTargetControl(target);
      const valued=control&&isAutomationValueControl(control)&&!target.follow;
      wizard.draftValueTarget=valued?{deviceId:target.deviceId,controlId:control.id}:null;
      wizard.draftValue=valued?target.value:null;
      wizard.stage=automationMappingMode(wizard)?"map":"action";
    });
  }
  function removeAutomationTarget(index,button){
    const wizard=state.automationWizard;
    if(!wizard||!wizard.targets[index])return;
    const node=button?.closest?.(".automation-node")||null;
    const drop=()=>{
      wizard.targets.splice(index,1);
      automationRedraw();
    };
    cancelAutomationAdvance();
    if(!node||automationReducedMotion()){drop();return}
    node.classList.add("automation-leaving");
    automationAdvanceTimer=setTimeout(()=>{automationAdvanceTimer=null;drop()},190);
  }
  function chooseAutomationAutoOff(mode){
    const wizard=state.automationWizard;
    if(!wizard||!automationAutoOffModes.includes(mode))return;
    if(mode==="idle"&&!automationAutoOffIdleAvailable(wizard))return;
    wizard.autoOffMode=mode;
    wizard.autoOffCustom=false;
    wizard.autoOffTouched=true;
    // "Kapanmasın" tek dokunuşta kesinleşir; süre isteyen seçenekler kullanıcıyı bekler.
    if(mode==="none"){automationAdvance(()=>{wizard.fresh="autooff";wizard.stage="name"});return}
    automationRedraw();
  }
  function setAutomationAutoOffMinutes(value,custom){
    const wizard=state.automationWizard;
    if(!wizard||!Number.isFinite(value))return;
    const idle=wizard.autoOffMode==="idle";
    const next=Math.min(240,Math.max(idle?0:1,Math.round(value)));
    if(idle)wizard.autoOffIdleMinutes=next;else wizard.autoOffMinutes=next;
    wizard.autoOffCustom=Boolean(custom);
    wizard.autoOffTouched=true;
    automationRedraw();
  }
  const stepAutomationAutoOff=amount=>setAutomationAutoOffMinutes(automationAutoOffMinutes(state.automationWizard||{})+amount,true);
  function openAutomationAutoOffCustom(){
    const wizard=state.automationWizard;
    if(!wizard)return;
    wizard.autoOffCustom=true;
    wizard.autoOffTouched=true;
    automationRedraw();
  }
  // Sağ üstteki × tek dokunuşla çıkarır: üst üste "Geri" basmak gerekmiyor. Yol/tür sorusunda
  // henüz doldurulmuş bir şey yoktur, orada doğrudan kapanır; ötesinde veri kaybı onayı sorulur.
  const automationWizardDirty=wizard=>Boolean(wizard)
    &&Boolean(wizard.touched)
    &&(wizard.targets.length>0
      ||wizard.conditions.length>0
      ||(wizard.stage!=="path"&&wizard.stage!=="kind"));
  function closeAutomationWizard(){
    if(automationWizardDirty(state.automationWizard)&&!confirm(t("automationCloseConfirm")))return;
    cancelAutomationAdvance();
    $("#automationDialog").close();
  }
  function stepBackAutomation(){
    const wizard=state.automationWizard;
    cancelAutomationAdvance();
    if(!wizard||wizard.stage==="path"||wizard.stage==="kind"){$("#automationDialog").close();return}
    // Sayısal koşulda geri, cihazı değil özelliği geri alır: liste yeniden görünür.
    if(wizard.stage==="condState"&&wizard.draftCondition?.numeric){
      automationAdvance(()=>{wizard.draftCondition.numeric=false;wizard.draftCondition.property=null});
      return;
    }
    const back=automationBackStage(wizard);
    if(!back){$("#automationDialog").close();return}
    automationAdvance(()=>{
      if(wizard.stage==="delay"&&wizard.draftWaitBefore){wizard.draftTargetIndex=null;wizard.draftWaitBefore=false}
      wizard.stage=back;
    });
  }
  async function nextAutomationStep(){
    const wizard=state.automationWizard;
    cancelAutomationAdvance();
    if(!wizard||wizard.stage==="path")return;
    // Bekleyen eşleme cevabı önce kesinleşir; ancak ondan sonra kaydetme sırası gelir.
    if(wizard.stage==="action"&&!automationMappingMode(wizard)){automationCommitValueTarget();return}
    if(wizard.stage==="map"){automationCommitMapTarget();return}
    if(wizard.stage==="delay"){commitAutomationDelay();return}
    if(wizard.stage==="sceneId"){chooseAutomationScene(wizard.draftSceneId);return}
    if(wizard.stage==="condTime"||wizard.stage==="condState"){commitAutomationCondition();return}
    // Bekleme adımı cevaplanınca kapanan satır bir kez yumuşak yerleşir; öbür düğümlerle aynı dil.
    if(wizard.stage==="wait"){automationAdvance(()=>{wizard.fresh="wait";wizard.stage=automationNextStage(wizard)});return}
    if(wizard.stage==="name"){if(automationWizardReady(wizard))await saveAutomationWizard();return}
    if(!automationStageAdvanceable(wizard))return;
    automationAdvance(()=>{wizard.stage=automationNextStage(wizard)});
  }
  // Sunucu döngüyü reddederse ham hata metni yerine anlaşılır bir cümle gösterilir (§8.2).
  const automationErrorText=error=>/döngü|loop/i.test(String(error?.message||""))
    ?t("automationLoopWarning")
    :String(error?.message||"");
  async function saveAutomationWizard(){
    const wizard=state.automationWizard;
    if(!wizard)return;
    // Her hedef bir eylem satırıdır; anahtar yolunda hedef başına iki yön üretilir ve
    // "Bir şey yapma" seçilen yön hiç yazılmaz. Motor eylemleri sırayla çalıştırır.
    const asAction=target=>{
      const kind=automationTargetKind(target);
      if(kind==="delay")return{type:"delay",seconds:target.seconds};
      if(kind==="group")return{type:"group",groupId:target.groupId,property:target.property||"state",value:target.value};
      if(kind==="scene")return{type:"scene",groupId:target.groupId,sceneId:target.sceneId};
      const action={type:"device",deviceId:target.deviceId,property:target.property,controlId:target.controlId,value:target.value};
      // "İzle" kipi kaydın kendi alanıdır; `value` yedek olarak yazılmaya devam eder.
      if(target.follow)action.follow={mode:target.follow.mode};
      const autoOff=automationAutoOffPayload(wizard,target);
      return autoOff?{...action,autoOff}:action;
    };
    const built=automationMappingMode(wizard)
      ?wizard.targets.flatMap(target=>automationTargetKind(target)==="device"
        ?automationMapActionsFor(wizard,target)
        :[asAction(target)])
      // §9 — kapanış sözü açan eylemin kendi üstüne yazılır; ikinci bir kural kurulmaz.
      :wizard.targets.map(asAction);
    // Ara adımdaki bekleme her zaman ilk eylem olarak yazılır; 0:00 seçiliyse hiç yazılmaz — daha
    // önce yazılmış olsa bile listeye geri konmadığı için kaydedince kalkar.
    const waitSeconds=automationWaitSeconds(wizard);
    const actions=waitSeconds>0?[{type:"delay",seconds:waitSeconds},...built]:built;
    if(!actions.length)return;
    // Motor yalnız beklemeden oluşan bir kuralı reddeder; kullanıcı sunucu hatasıyla karşılaşmasın.
    if(actions.every(action=>action.type==="delay")){showToast(t("automationNeedRealAction"),true);return}
    if(actions.length>automationMaxActions){showToast(t("automationNeedTarget"),true);return}
    // §9.1 — güneşin iki olaylı yolu iki tetikleyici yazar; öbür yollarda liste tek satırdır.
    const triggers=automationWizardTriggers(wizard);
    const trigger=triggers[0];
    // §8.2 — yalnız tetikleyen kanalın kendisi engellenir; buton yolunda kanal yok, cihaz engellenir.
    const deviceActions=actions.filter(action=>action.type==="device");
    const loops=trigger?.type==="deviceState"
      ?deviceActions.some(action=>automationChannelKey(action.deviceId,action.property)===automationChannelKey(trigger.deviceId,trigger.property))
      :trigger?.type==="deviceAction"&&deviceActions.some(action=>action.deviceId===trigger.deviceId);
    if(loops){showToast(t("automationLoopWarning"),true);return}
    const buttons=automationNextButtons();
    buttons.forEach(button=>{button.disabled=true});
    const entry={
      id:wizard.id||automationNewId(),
      name:automationWizardName(wizard),
      enabled:wizard.enabled!==false,
      ...(wizard.triggerKind==="manual"?{manual:true}:{}),
      triggers,
      conditions:wizard.conditions.map(condition=>({...condition})),
      actions,
      lastRunAt:null,
      lastRunOk:null
    };
    // Varsayılan "hepsi" alanı hiç yazılmaz; tek koşullu kuralda anahtar zaten görünmez.
    if(wizard.conditions.length>1&&wizard.conditionMode==="any")entry.conditionMode="any";
    try{
      const data=await api("/api/automations");
      const automations=Array.isArray(data.automations)?data.automations:[];
      const index=automations.findIndex(item=>item.id===entry.id);
      if(index>=0)automations[index]={...entry,lastRunAt:automations[index].lastRunAt,lastRunOk:automations[index].lastRunOk};
      else automations.push(entry);
      await persistAutomations(automations,"automationSaved",{
        /* PIN katmanı normal DOM'dadır, otomasyon penceresi ise tarayıcının top layer'ındadır.
           PIN görünür olsun diye pencereyi geçici kapatırız; close dinleyicisi bayrak sayesinde
           taslağı korur. PIN kabul edilince aynı taslak yeniden açılır ve kayıt otomatik sürer. */
        before:()=>{
          automationWizardReauthorizing=true;
          const dialog=$("#automationDialog");
          if(dialog?.open)dialog.close();
        },
        after:()=>{
          const dialog=$("#automationDialog");
          if(dialog&&!dialog.open)dialog.showModal();
          renderAutomationWizard();
        }
      });
      $("#automationDialog").close();
    }catch(error){
      buttons.forEach(button=>{button.disabled=false});
      if(error?.code!=="ELEVATION_CANCELED")showToast(automationErrorText(error),true);
    }
  }
