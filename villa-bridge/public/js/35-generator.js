  /* JENERATÖR ÇEKİRDEĞİ.

     Ana ekran elle kurulan bir liste değil, eldeki cihazlardan ŞABLONLARLA türetilen bir sonuçtur.
     Bu dosya o türetmenin veri katmanıdır: girdisi yalnız `state` (ve çağrıdan gelen diziler),
     çıktısı düz JavaScript nesneleri. Burada HTML üretilmez, komut gönderilmez, `state`
     DEĞİŞTİRİLMEZ ve ağa çıkılmaz — aynı girdi her zaman aynı çıktıyı verir.

     Kural marka/model değil YETENEK okur: bir cihazın nasıl gösterileceğini `device-controls.ts`in
     ürettiği kontrol `kind`ları söyler. Böylece eve yeni bir üretici girdiğinde burada hiçbir şey
     değişmez.

     Metin üretilmez: kart adları cihazın/grubun kendi adıdır, çeviri gereken yerler `nameKey`
     olarak dışarı verilir. Çeviri (`t`) seçili dile bağlıdır; jeneratörün saflığı ona bağlanmaz. */

  /* Dört şablon: aç/kapa · kısılabilir · renk/ısı · salt okunur. Kontrolün `kind`ı hangi kümedeyse
     şablon odur; kümelerin dışındaki her şey (ölçüm, kontrolsüz cihaz) salt okunurdur. */
  const genSwitchKinds=new Set(["switch","fan","siren","lock","cover","select"]);
  const genLevelKinds=new Set(["level","position","number","climate"]);
  const genColorKinds=new Set(["color","temperature"]);
  const genTemplateRank={readonly:0,switch:1,level:2,color:3};
  const genTemplateForControl=control=>{
    if(!control||typeof control.kind!=="string")return"readonly";
    if(genColorKinds.has(control.kind))return"color";
    if(genLevelKinds.has(control.kind))return"level";
    if(genSwitchKinds.has(control.kind))return"switch";
    return"readonly";
  };
  /* Cihazın şablonu = kontrollerinin EN YETENEKLİSİ. Parlaklığı da rengi de olan bir ampul "renk"
     şablonuna düşer; yalnız aç/kapası olan priz "aç/kapa"da kalır. Ayar niteliğindeki (adminOnly)
     kontroller sayılmaz — onlar ev sakininin ekranına ait değil. */
  const genTemplateForDevice=device=>{
    const controls=Array.isArray(device?.controls)?device.controls.filter(control=>control?.adminOnly!==true):[];
    let best="readonly";
    for(const control of controls){
      const template=genTemplateForControl(control);
      if(genTemplateRank[template]>genTemplateRank[best])best=template;
    }
    return best;
  };

  /* Kontrolsüz cihazın (sensör) "açık" sayılıp sayılmadığı kendi durumundan okunur. Liste jeneriktir:
     cihaza değil ÖZELLİĞE bakar. `contact` Z2M sözleşmesinde ters kutupludur (`false` = açık). */
  const genActiveStateProperties=["presence","occupancy","smoke","carbon_monoxide","water_leak","gas","vibration","tamper"];
  const genDeviceSignal=device=>{
    if(!device)return"off";
    if(device.availability==="offline")return"offline";
    if(isAlert(device))return"alert";
    const value=device.state||{};
    if(value.contact===false)return"active";
    return genActiveStateProperties.some(property=>value[property]===true)?"active":"off";
  };
  /* Döşemenin "açık mı" sorusu: kontrol varsa kontrolün kendi eylemi (`dashboardControlAction`),
     yoksa cihazın durum işareti. Tek doğru kaynak budur, ikinci bir eşik tanımlanmaz. */
  const genTileActive=(device,control)=>control
    ?dashboardControlAction(control)?.active===true
    :["active","alert"].includes(genDeviceSignal(device));

  const genDeviceLookup=devices=>{
    const map=new Map();
    for(const device of Array.isArray(devices)?devices:[]){
      if(device&&typeof device.id==="string")map.set(device.id,device);
    }
    return map;
  };
  /* Kart öğesi (`{deviceId,controlId}`) → döşeme modeli. Çözülemeyen öğe atılır: silinmiş bir cihazın
     kaydı karta hayalet döşeme basmaz. `@device` kontrolsüz cihaz demektir (sensör). */
  const genTileModel=(item,lookup)=>{
    const device=lookup.get(item?.deviceId);
    if(!device)return null;
    const control=item.controlId===groupDeviceControlId
      ?null
      :(device.controls||[]).find(candidate=>candidate.id===item.controlId&&isDashboardControl(candidate))||null;
    if(item.controlId!==groupDeviceControlId&&!control)return null;
    return{
      deviceId:device.id,
      controlId:control?control.id:groupDeviceControlId,
      name:control?control.name:device.name,
      template:control?genTemplateForControl(control):genTemplateForDevice(device),
      icon:deviceIconKind(device,control),
      active:genTileActive(device,control),
      availability:device.availability,
      signal:genDeviceSignal(device)
    };
  };

  /* Kartın ikonu döşemelerinin ÇOĞUNLUĞUNDAN gelir: ışık dolu oda ışık, perde dolu oda perde ikonu
     alır. Eşitlikte ilk döşemenin ikonu kazanır — sonuç sıraya bağlı ama deterministik.
     Oda kendi ikonunu SEÇTİYSE (`HomeGroup.icon`) seçim kazanır; alan İSTEĞE BAĞLI olduğu için
     ikonsuz eski odalar bu türetmeyle bugünkü görünümlerinde kalır. */
  const genCardIcon=tiles=>{
    const counts=new Map();
    for(const tile of tiles)counts.set(tile.icon,(counts.get(tile.icon)||0)+1);
    let best=null;
    let bestCount=0;
    for(const tile of tiles){
      const count=counts.get(tile.icon)||0;
      if(count>bestCount){best=tile.icon;bestCount=count}
    }
    return best;
  };

  /* Kısa sensör özeti. UYDURMA YOK: yalnız cihazın gerçekten yayımladığı sayısal ölçümler girer,
     tanı/ayar (`diagnostic`, `config`) alanları ev sakinine kalabalıktır, elenir. Aynı özellik birden
     çok cihazdan geliyorsa ortalaması alınır (iki termometreli oda tek sıcaklık söyler). Hiç ölçüm
     yoksa çağıran taraf alanı hiç görmez. */
  const genSensorSummaryLimit=3;
  const genSensorSummary=devices=>{
    const buckets=new Map();
    for(const device of devices){
      for(const reading of Array.isArray(device?.readings)?device.readings:[]){
        if(!reading||reading.type!=="numeric")continue;
        if(reading.category==="diagnostic"||reading.category==="config")continue;
        if(typeof reading.value!=="number"||!Number.isFinite(reading.value))continue;
        const property=String(reading.property||"");
        if(!property)continue;
        const bucket=buckets.get(property);
        if(bucket){bucket.total+=reading.value;bucket.count+=1;continue}
        buckets.set(property,{
          property,
          name:reading.name||property,
          unit:reading.unit||null,
          total:reading.value,
          count:1
        });
      }
    }
    return[...buckets.values()].slice(0,genSensorSummaryLimit).map(bucket=>{
      const average=bucket.total/bucket.count;
      return{
        property:bucket.property,
        name:bucket.name,
        unit:bucket.unit,
        deviceCount:bucket.count,
        value:Number.isInteger(average)?average:Math.round(average*10)/10
      };
    });
  };

  const genUngroupedCardId="generated:ungrouped";
  /* Grup (oda) kartının içeriği. `group` panelin grup şekli: `{id,name,items:[{deviceId,controlId}]}`.
     `devices` verilmezse `state.devices` okunur. Sensör özeti VARSA `sensors` alanı çıkar; yoksa alan
     hiç üretilmez — boş dizi de bir iddiadır, o iddia atılmaz. */
  const genCardModelForGroup=(group,devices)=>{
    const list=Array.isArray(devices)?devices:state.devices;
    const lookup=genDeviceLookup(list);
    const items=Array.isArray(group?.items)?group.items:[];
    const tiles=items.map(item=>genTileModel(item,lookup)).filter(Boolean);
    const deviceIds=[...new Set(tiles.map(tile=>tile.deviceId))];
    const cardDevices=deviceIds.map(id=>lookup.get(id)).filter(Boolean);
    const model={
      id:group?.id??null,
      name:typeof group?.name==="string"?group.name:null,
      nameKey:group?.nameKey??null,
      locked:group?.locked===true,
      icon:(typeof group?.icon==="string"&&group.icon)||genCardIcon(tiles),
      tiles,
      deviceCount:deviceIds.length,
      activeCount:tiles.filter(tile=>tile.active).length,
      offlineCount:cardDevices.filter(device=>device.availability==="offline").length
    };
    const sensors=genSensorSummary(cardDevices);
    if(sensors.length)model.sensors=sensors;
    return model;
  };

  /* Hiçbir gruba (odaya) girmemiş cihazların toplayıcı kartı. HİÇBİR CİHAZ KAYBOLMAZ: bir cihaz ya bir
     odanın kartındadır ya buradadır. Kontrolü olan cihaz varsayılan döşemesiyle, olmayan `@device`
     ile girer. Adı çeviriden gelir, o yüzden `nameKey` taşınır. */
  const genUngroupedCardModel=(devices,groups)=>{
    const list=Array.isArray(devices)?devices:state.devices;
    const rooms=Array.isArray(groups)?groups:state.groups;
    const assigned=new Set();
    for(const room of rooms){
      for(const item of Array.isArray(room?.items)?room.items:[]){
        if(item&&typeof item.deviceId==="string")assigned.add(item.deviceId);
      }
    }
    const items=list.filter(device=>device&&!assigned.has(device.id)).map(device=>{
      const control=dashboardControlForDevice(device);
      return{deviceId:device.id,controlId:control?control.id:groupDeviceControlId};
    });
    return genCardModelForGroup({id:genUngroupedCardId,name:null,nameKey:"noRoomGroup",items,locked:true},list);
  };

  /* Sahne kataloğu ayrı bir depo değildir; yalnız `manual:true` olan gerçek otomasyonların
     kullanıcı yüzüdür. Zaman, güneş, sensör ve düğme tetikleyicili kurallar Otomasyonlar
     ekranında kalır ve hızlı sahnelere sızmaz. */
  const genSceneCatalog=automations=>{
    const list=Array.isArray(automations)?automations:state.automations;
    return(Array.isArray(list)?list:[]).filter(entry=>entry?.manual===true&&typeof entry.id==="string").map(entry=>{
      const triggers=Array.isArray(entry.triggers)?entry.triggers:[];
      return{
        id:entry.id,
        name:typeof entry.name==="string"&&entry.name?entry.name:entry.id,
        enabled:entry.enabled!==false,
        kind:"manual",
        triggerCount:triggers.length,
        actionCount:Array.isArray(entry.actions)?entry.actions.length:0,
        runPath:`/api/automations/${encodeURIComponent(entry.id)}/run`,
        lastRunAt:entry.lastRunAt??null,
        lastRunOk:entry.lastRunOk??null
      };
    });
  };

  /* Ev sağlığı özeti. Ölçüt cihazın kendi bildirdikleridir; sıralama cihaz listesinin sırasıdır.
     Bir cihaz birden çok sebeple dikkat isteyebilir — hepsi tek satırda `reasons` olarak durur,
     cihaz iki kez sayılmaz. */
  const genHealthReasons=device=>{
    const reasons=[];
    if(device.availability==="offline")reasons.push("offline");
    if(criticalAlert(device))reasons.push("critical");
    else if(isAlert(device))reasons.push("alert");
    if(device.supported===false)reasons.push("unsupported");
    if(device.interviewCompleted===false)reasons.push("setupIncomplete");
    if(hasLowBattery(device))reasons.push("lowBattery");
    return reasons;
  };
  const genHomeHealthModel=devices=>{
    const list=(Array.isArray(devices)?devices:state.devices).filter(Boolean);
    const attention=[];
    let offline=0;
    let online=0;
    let unknown=0;
    let lowBattery=0;
    let critical=0;
    for(const device of list){
      if(device.availability==="offline")offline+=1;
      else if(device.availability==="online")online+=1;
      else unknown+=1;
      const reasons=genHealthReasons(device);
      if(reasons.includes("lowBattery"))lowBattery+=1;
      if(reasons.includes("critical"))critical+=1;
      if(reasons.length)attention.push({deviceId:device.id,name:device.name,icon:deviceIconKind(device,null),reasons});
    }
    return{
      deviceCount:list.length,
      online,
      offline,
      unknown,
      lowBatteryCount:lowBattery,
      criticalCount:critical,
      attention,
      ok:attention.length===0
    };
  };
