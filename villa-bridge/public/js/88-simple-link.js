  // Küme kimliği iki dilde gelir: gölge kipinde Zigbee2MQTT AD ("genOnOff"), doğrudan kipte
  // zigbee-herdsman SAYI (6) yollar. Yalnız ada bakan eski kural doğrudan kipte hiçbir kaynak
  // bulamıyordu; iki yazım da tanınır.
  const linkClusterIds={genOnOff:6,genLevelCtrl:8};
  const linkClusterNames=Object.keys(linkClusterIds);
  const endpointClusters=(endpoint,direction)=>(direction==="out"?endpoint.outputClusters:endpoint.inputClusters)||[];
  const endpointHasCluster=(endpoint,direction,name)=>endpointClusters(endpoint,direction)
    .some(cluster=>String(cluster)===name||Number(cluster)===linkClusterIds[name]);
  const linkSourceEndpoint=device=>(device.endpoints||[])
    .find(endpoint=>linkClusterNames.some(name=>endpointHasCluster(endpoint,"out",name)));
  const linkTargetEndpoint=device=>(device.endpoints||[]).find(endpoint=>endpointHasCluster(endpoint,"in","genOnOff"));
  /* Doğrudan bağlama (Zigbee binding) yalnız kaynağın ÇIKIŞ kümesinden kurulabilir. Bir kumanda
     tuş olayını satıcıya özel bir komutla GİRİŞ kümesi üzerinden yolluyorsa (Tuya sahne
     anahtarları böyledir) bağlama kurulsa bile hiçbir komut gitmez. Böyle kumandalar listeden
     atılmaz — kullanıcı kendi kumandasını burada arar — ama seçilince köprü yoluna (kural)
     devredilir. Kural jeneriktir: model ya da satıcı listesi yok, yalnız cihazın bildirdiği
     kümelere bakılır. */
  const canBindDirectly=device=>Boolean(linkSourceEndpoint(device));
  const isButtonStarter=device=>(device?.buttons||[]).length>0||(device?.actionTypes||[]).length>0;
  const isLinkStarter=device=>canBindDirectly(device)||isButtonStarter(device);
  const isProtectedDevice=device=>(device.controls||[]).some(control=>control.kind==="lock"||control.kind==="siren");
  const isLinkTarget=device=>!isProtectedDevice(device)&&(device.controls||[]).some(control=>control.kind==="switch"||control.kind==="level");
  function linkClustersFor(source,target){
    const from=source.endpoints||[];
    const to=target?target.endpoints||[]:null;
    return linkClusterNames.filter(name=>
      from.some(endpoint=>endpointHasCluster(endpoint,"out",name))
      &&(!to||to.some(endpoint=>endpointHasCluster(endpoint,"in",name)))
    );
  }
  function simpleLinks(){
    const rows=new Map();
    state.devices.filter(isLinkStarter).forEach(device=>{
      (device.endpoints||[]).forEach(endpoint=>{
        (endpoint.bindings||[]).forEach(binding=>{
          if(!linkClusterNames.includes(binding.cluster))return;
          const target=binding.targetType==="group"
            ?state.zigbeeGroups.find(group=>group.id===binding.targetId)
            :state.devices.find(item=>item.id===binding.targetId);
          if(!target)return;
          const key=JSON.stringify([device.id,endpoint.id,binding.targetId,binding.targetEndpoint??""]);
          const known=rows.get(key);
          if(known){if(!known.clusters.includes(binding.cluster))known.clusters.push(binding.cluster);return}
          rows.set(key,{key,sourceId:device.id,sourceName:device.name,fromEndpoint:endpoint.id,targetId:binding.targetId,targetName:target.name,toEndpoint:binding.targetEndpoint,clusters:[binding.cluster]});
        });
      });
    });
    return [...rows.values()].sort((left,right)=>String(left.sourceName).localeCompare(String(right.sourceName),state.language));
  }
  async function removeSimpleLink(key){
    const link=simpleLinks().find(item=>item.key===key);
    if(!link||!confirm(t("simpleLinkRemoveConfirm")))return;
    try{
      const data=await api("/api/zigbee/bind",{method:"POST",body:JSON.stringify({
        fromId:link.sourceId,
        toId:link.targetId,
        bind:false,
        clusters:link.clusters,
        fromEndpoint:link.fromEndpoint,
        toEndpoint:link.toEndpoint??undefined
      })});
      state.devices=data.devices||state.devices;
      renderAutomations();
      renderZigbeeGroups();
      showToast(t("simpleLinkRemoved"));
    }catch(error){showToast(error.message,true)}
  }
  function openSimpleLink(){
    // Sihirbaz kapanır, bağlantı penceresi açılır: iki diyalog üst üste binmez.
    const wizardDialog=$("#automationDialog");
    if(wizardDialog.open)wizardDialog.close();
    state.simpleLink={step:1,sourceId:null,targetId:null,targetType:"device",room:null};
    const dialog=$("#simpleLinkDialog");
    if(!dialog.open)dialog.showModal();
    renderSimpleLink();
  }
  const simpleLinkChoiceHtml=(id,type,name,meta,active)=>`<button class="simple-link-choice${active?" active":""}" type="button" data-link-choice="${esc(id)}" data-link-type="${type}" aria-pressed="${active}"><strong>${esc(name)}</strong><small>${esc(meta)}</small></button>`;
  function renderSimpleLink(){
    const link=state.simpleLink;
    if(!link)return;
    const firstStep=link.step===1;
    $("#simpleLinkStep").textContent=t("simpleLinkStepCount",{step:link.step,total:2});
    $("#simpleLinkTitle").textContent=t(firstStep?"simpleLinkStepOneTitle":"simpleLinkStepTwoTitle");
    $("#simpleLinkLead").textContent=t(firstStep?"simpleLinkStepOneLead":"simpleLinkStepTwoLead");
    const rooms=$("#simpleLinkRooms");
    rooms.hidden=firstStep||state.groups.length===0;
    rooms.innerHTML=rooms.hidden?"":[{id:"",name:t("roomFilterAll")},...state.groups].map(group=>{
      const active=(group.id||null)===link.room;
      return`<button class="room-chip${active?" active":""}" type="button" data-link-room="${esc(group.id)}" aria-pressed="${active}">${esc(group.name)}</button>`;
    }).join("");
    const byName=(left,right)=>String(left.name).localeCompare(String(right.name),state.language);
    let choices="";
    if(firstStep){
      const starters=state.devices.filter(isLinkStarter).sort(byName);
      // İki küme: doğrudan bağlanabilenler ve yalnız köprü üzerinden çalışabilenler. İkincisi
      // gizlenmez — kullanıcı kumandasını burada arar — ama seçilince kural yoluna devredilir.
      const groups=[
        {devices:starters.filter(canBindDirectly),head:"simpleLinkDirectGroup",direct:true},
        {devices:starters.filter(device=>!canBindDirectly(device)),head:"simpleLinkBridgeGroup",direct:false}
      ].filter(group=>group.devices.length>0);
      const labelled=groups.length>1;
      choices=starters.length
        ?groups.map(group=>{
          const head=labelled?`<p class="automation-group-head">${esc(t(group.head))}</p>`:"";
          const rows=group.devices.map(device=>simpleLinkChoiceHtml(
            device.id,
            "device",
            device.name,
            group.direct?deviceKind(device):automationJoin(deviceKind(device),t("simpleLinkBridgeOnly")),
            device.id===link.sourceId
          )).join("");
          return`${head}${rows}`;
        }).join("")
        :`<div class="empty">${t("simpleLinkNoStarters")}</div>`;
    }else{
      const targets=state.devices
        .filter(device=>isLinkTarget(device)&&device.id!==link.sourceId&&(!link.room||deviceInRoom(device,link.room)))
        .sort(byName);
      const groups=link.room?[]:[...state.zigbeeGroups].sort(byName);
      choices=[
        ...targets.map(device=>simpleLinkChoiceHtml(device.id,"device",device.name,deviceKind(device),link.targetType==="device"&&device.id===link.targetId)),
        ...groups.map(group=>simpleLinkChoiceHtml(group.id,"group",group.name,t("groupMembers",{count:group.members}),link.targetType==="group"&&group.id===link.targetId))
      ].join("")||`<div class="empty">${t("simpleLinkNoTargets")}</div>`;
    }
    $("#simpleLinkChoices").innerHTML=choices;
    $$("[data-link-choice]").forEach(button=>button.onclick=()=>chooseSimpleLink(button.dataset.linkChoice,button.dataset.linkType));
    $$("[data-link-room]").forEach(button=>button.onclick=()=>{link.room=button.dataset.linkRoom||null;link.targetId=null;renderSimpleLink()});
    const source=state.devices.find(device=>device.id===link.sourceId);
    const target=link.targetType==="group"
      ?state.zigbeeGroups.find(group=>group.id===link.targetId)
      :state.devices.find(device=>device.id===link.targetId);
    const ready=!firstStep&&Boolean(source&&target);
    const summary=$("#simpleLinkSummary");
    summary.hidden=!ready;
    summary.innerHTML=ready?`<strong>${t("simpleLinkSummary",{source:esc(source.name),target:esc(target.name)})}</strong><p>${t("simpleLinkDirectNote")}</p>`:"";
    $("#simpleLinkBack").textContent=t(firstStep?"cancel":"back");
    const save=$("#simpleLinkSave");
    save.textContent=t("save");
    save.hidden=firstStep;
    save.disabled=!ready;
  }
  /* Doğrudan bağlanamayan kumanda seçilince kullanıcı çıkmaza düşmesin: bağlantı penceresi
     kapanır ve kural sihirbazı aynı kumanda düğme tetikleyicisi olarak seçilmiş hâlde açılır.
     Komut o zaman köprü üzerinden gider — Villa Bridge basışı duyar ve hedefe kendisi yazar.
     Farkı: doğrudan bağlantı köprü kapalıyken de çalışır, köprü yolu çalışan bir sisteme
     muhtaçtır ama her kumandada çalışır. */
  function handOffToRule(deviceId){
    $("#simpleLinkDialog").close();
    openAutomationWizard();
    const wizard=state.automationWizard;
    if(!wizard)return;
    wizard.triggerKind="button";
    wizard.triggerQuery="";
    wizard.triggerTab="all";
    wizard.stage="trigDevice";
    renderAutomationWizard();
    chooseAutomationTriggerDevice(deviceId);
    showToast(t("simpleLinkBridgeHandoff"));
  }
  function chooseSimpleLink(id,type){
    const link=state.simpleLink;
    if(!link)return;
    if(link.step===1){
      const source=state.devices.find(device=>device.id===id);
      if(source&&!canBindDirectly(source)){handOffToRule(id);return}
      link.sourceId=id;link.targetId=null;link.step=2;
    }
    else{link.targetId=id;link.targetType=type}
    renderSimpleLink();
  }
  function stepBackSimpleLink(){
    const link=state.simpleLink;
    if(!link||link.step===1){$("#simpleLinkDialog").close();return}
    link.step=1;
    link.targetId=null;
    renderSimpleLink();
  }
  async function saveSimpleLink(){
    const link=state.simpleLink;
    if(!link)return;
    const source=state.devices.find(device=>device.id===link.sourceId);
    const targetDevice=link.targetType==="device"?state.devices.find(device=>device.id===link.targetId):null;
    const targetGroup=link.targetType==="group"?state.zigbeeGroups.find(group=>group.id===link.targetId):null;
    const targetId=targetDevice?.id||targetGroup?.id;
    if(!source||!targetId)return;
    const clusters=linkClustersFor(source,targetDevice);
    const button=$("#simpleLinkSave");
    button.disabled=true;
    try{
      const data=await api("/api/zigbee/bind",{method:"POST",body:JSON.stringify({
        fromId:source.id,
        toId:targetId,
        bind:true,
        clusters:clusters.length?clusters:undefined,
        fromEndpoint:linkSourceEndpoint(source)?.id,
        toEndpoint:targetDevice?linkTargetEndpoint(targetDevice)?.id:undefined
      })});
      state.devices=data.devices||state.devices;
      $("#simpleLinkDialog").close();
      renderAutomations();
      renderZigbeeGroups();
      showToast(t("simpleLinkSaved"));
    }catch(error){button.disabled=false;showToast(error.message,true)}
  }
