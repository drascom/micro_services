  function openRepairProgress(name){
    $("#repairDeviceName").textContent=name;
    const dialog=$("#repairDialog");
    if(!dialog.open)dialog.showModal();
    clearTimeout(openRepairProgress.timer);
    openRepairProgress.timer=setTimeout(closeRepairProgress,20000);
  }
  function closeRepairProgress(){
    clearTimeout(openRepairProgress.timer);
    const dialog=$("#repairDialog");
    if(dialog.open)dialog.close();
  }
  async function reconfigureDevice(id){
    const button=$$("[data-reconfigure]").find(item=>item.dataset.reconfigure===id);
    if(button)button.disabled=true;
    const device=state.devices.find(item=>item.id===id);
    closeDeviceDetail();
    openRepairProgress(device?.name||id);
    try{
      await api(`/api/devices/${encodeURIComponent(id)}/reconfigure`,{method:"POST"});
      await refresh();
      closeRepairProgress();
      showToast(t("repairComplete"));
    }catch(error){
      closeRepairProgress();
      showToast(error.message,true);
    }
    finally{closeRepairProgress();if(button)button.disabled=false}
  }
  async function openDeviceNote(id){
    const device=state.devices.find(item=>item.id===id);
    if(!device)return;
    state.noteEditing=id;
    $("#noteDeviceName").textContent=device.name;
    $("#noteInput").value="";
    $("#noteDialog").showModal();
    try{
      const data=await api(`/api/devices/${encodeURIComponent(id)}/note`);
      if(state.noteEditing===id)$("#noteInput").value=data.note||"";
    }catch(error){showToast(error.message,true)}
  }
  async function scheduleOta(id,enabled=true){
    const button=$$("[data-ota]").find(item=>item.dataset.ota===id);
    if(button)button.disabled=true;
    try{
      await api(`/api/devices/${encodeURIComponent(id)}/ota-schedule`,{method:"PUT",body:JSON.stringify({enabled})});
      showToast(t(enabled?"updateScheduled":"updateUnscheduled"));
    }catch(error){showToast(error.message,true)}
    finally{if(button)button.disabled=false}
  }
  async function checkOta(id){
    const button=$$("[data-ota-check]").find(item=>item.dataset.otaCheck===id);
    if(button)button.disabled=true;
    try{
      const data=await api(`/api/devices/${encodeURIComponent(id)}/ota-check`,{method:"POST"});
      showToast(t(data.ota?.available?"updateAvailable":"noUpdateAvailable"));
      await refresh();
    }catch(error){showToast(error.message,true)}
    finally{if(button)button.disabled=false}
  }
  // Rol yalnız lamba↔anahtar karışıklığını çözer. Perde, kilit, sensör gibi tipler otomatik
  // tanımadan gelmeye devam eder; onlarda seçenek hiç gösterilmez.
  const deviceRoleOptions=["auto","light","switch"];
  const deviceRoleLabels={auto:"deviceRoleAuto",light:"deviceRoleLight",switch:"deviceRoleSwitch"};
  const deviceRoleIcons={auto:"sensor",light:"light",switch:"switch"};
  const deviceRoleAskable=device=>Boolean(device)&&(device.category==="light"||device.category==="switch"
    ||(device.category==="unknown"&&(device.controls||[]).some(control=>control.kind==="switch")));
  // UID kuralı: yazma ucu IEEE adresine, kanal verilirse IEEE + kanal kimliğine gider — dost isme asla.
  async function saveDeviceRole(id,role,channel=null){
    const payload=channel?{role,channel}:{role};
    const data=await api(`/api/devices/${encodeURIComponent(id)}/role`,{method:"PUT",body:JSON.stringify(payload)});
    const device=state.devices.find(item=>item.id===id);
    if(device){
      device.detectedCategory=data.detectedCategory;
      device.category=data.deviceCategory??data.category;
      if(channel){
        const control=(device.controls||[]).find(item=>item.kind==="switch"&&item.id===channel);
        if(control){control.role=data.role;control.category=data.category}
      }else device.role=data.role;
    }
    return data;
  }
  function renderDeviceRoleDialog(){
    const editing=state.roleEditing;
    const device=editing?state.devices.find(item=>item.id===editing.id):null;
    if(!device)return;
    $("#deviceRoleLead").textContent=t("deviceRoleLead",{name:device.name});
    const guess=deviceCategoryLabels[device.detectedCategory];
    $("#deviceRoleChoices").innerHTML=deviceRoleOptions.map(role=>{
      const selected=(device.role||"auto")===role;
      const note=role==="auto"
        ?`<small>${esc(guess?t("deviceRoleAutoGuess",{kind:t(guess)}):t("deviceRoleAutoUnknown"))}</small>`
        :"";
      return`<button class="device-role-choice" type="button" data-device-role="${role}" aria-pressed="${selected}">${deviceIconSvg(deviceRoleIcons[role])}<span>${esc(t(deviceRoleLabels[role]))}</span>${note}</button>`;
    }).join("");
    $$("[data-device-role]").forEach(button=>button.onclick=()=>chooseDeviceRole(button.dataset.deviceRole));
  }
  // Kart içindeki rol satırı: seçim anında kaydedilir, ayrı "Kaydet" adımı yok.
  async function changeDeviceRole(select,id,channel,role){
    if(select)select.disabled=true;
    try{
      await saveDeviceRole(id,role,channel||null);
      showToast(t("deviceRoleSaved"));
    }catch(error){showToast(error.message,true)}
    finally{
      if(select){select.disabled=false;if(document.activeElement===select)select.blur()}
      render();
    }
  }
  async function chooseDeviceRole(role){
    const editing=state.roleEditing;
    if(!editing)return;
    try{
      await saveDeviceRole(editing.id,role);
      showToast(t("deviceRoleSaved"));
      $("#deviceRoleDialog").close();
    }catch(error){
      const gone=deviceGoneCode(error);
      if(gone)reportDeviceGone(editing.id,gone);else showToast(error.message,true);
    }
  }
  function askDeviceRole(id,afterPairing=false){
    const device=state.devices.find(item=>item.id===id);
    if(!deviceRoleAskable(device)){if(afterPairing)askDeviceRoom(id,true);return}
    state.roleEditing={id,afterPairing};
    renderDeviceRoleDialog();
    $("#deviceRoleDialog").showModal();
  }
  /* Eşleştirmenin son adımı: cihaz hangi odada? Oda = mevcut grup üyeliği, yeni bir depo açılmaz.
     "Sonra" denirse cihaz Genel görünümdeki "Odasız" kartında bekler. */
  function renderDeviceRoomDialog(){
    const editing=state.roomEditing;
    const device=editing?state.devices.find(item=>item.id===editing.id):null;
    if(!device)return;
    $("#deviceRoomLead").textContent=t("deviceRoomLead",{name:device.name});
    $("#deviceRoomChoices").innerHTML=state.groups.length
      ?state.groups.map(group=>{
        const member=deviceInRoom(device,group.id);
        return`<button class="device-role-choice" type="button" data-device-room="${esc(group.id)}" aria-pressed="${member}">${deviceIconSvg("group")}<span>${esc(group.name)}</span></button>`;
      }).join("")
      :`<p class="device-room-empty">${esc(t("deviceRoomNone"))}</p>`;
    $$("[data-device-room]").forEach(button=>button.onclick=()=>chooseDeviceRoom(button.dataset.deviceRoom));
  }
  async function chooseDeviceRoom(groupId){
    const editing=state.roomEditing;
    if(!editing)return;
    if(!deviceInRoom({id:editing.id},groupId))await toggleDeviceRoom(editing.id,groupId);
    if($("#deviceRoomDialog").open)$("#deviceRoomDialog").close();
  }
  async function createDeviceRoom(event){
    event.preventDefault();
    const editing=state.roomEditing;
    const name=$("#deviceRoomName").value.trim();
    const device=editing?state.devices.find(item=>item.id===editing.id):null;
    if(!device||name.length<2)return;
    const control=dashboardControlForDevice(device);
    const id=`${Date.now().toString(36)}-${Math.random().toString(36).slice(2,7)}`;
    $("#deviceRoomName").value="";
    state.widgets.push(groupWidgetId(id));
    saveWidgetLayout();
    await persistHomeGroups([...state.groups,{id,name:name.slice(0,32),items:[{deviceId:device.id,controlId:control?control.id:groupDeviceControlId}]}],"groupCreated");
    if($("#deviceRoomDialog").open)$("#deviceRoomDialog").close();
  }
  function askDeviceRoom(id,afterPairing=false){
    const device=state.devices.find(item=>item.id===id);
    if(!device){if(afterPairing)finishPairingFlow(id);return}
    state.roomEditing={id,afterPairing};
    $("#deviceRoomName").value="";
    renderDeviceRoomDialog();
    $("#deviceRoomDialog").showModal();
  }
  function openDeviceOptions(id){
    const device=state.devices.find(item=>item.id===id);
    if(!device)return;
    state.optionsDevice=id;
    $("#deviceOptionsName").textContent=device.name;
    $("#deviceTransition").value=String(device.options?.transition??0);
    $("#deviceDebounce").value=String(device.options?.debounce??0);
    $("#deviceRetain").checked=device.options?.retain===true;
    $("#deviceOptionsDialog").showModal();
  }
  async function saveDeviceOptions(event){
    event.preventDefault();
    const id=state.optionsDevice;
    if(!id)return;
    const device=state.devices.find(item=>item.id===id);
    try{
      const data=await api(`/api/devices/${encodeURIComponent(id)}/options`,{method:"PUT",body:JSON.stringify({
        transition:Number($("#deviceTransition").value),
        debounce:Number($("#deviceDebounce").value),
        retain:$("#deviceRetain").checked
      })});
      if(device)device.options={...device.options,...data.options};
      $("#deviceOptionsDialog").close();
      showToast(t("optionsSaved"));
      render();
    }catch(error){showToast(error.message,true)}
  }
  async function saveDeviceNote(event){
    event.preventDefault();
    const id=state.noteEditing;
    if(!id)return;
    try{
      await api(`/api/devices/${encodeURIComponent(id)}/note`,{method:"PUT",body:JSON.stringify({note:$("#noteInput").value})});
      $("#noteDialog").close();
      showToast(t("noteSaved"));
    }catch(error){showToast(error.message,true)}
  }
  async function addInstallCode(){
    const input=$("#installCode");
    const value=input.value.trim();
    if(!value)return;
    const button=$("#addInstallCode");
    button.disabled=true;
    try{
      await api("/api/zigbee/install-code",{method:"POST",body:JSON.stringify({value})});
      input.value="";
      showToast(t("installCodeAdded"));
    }catch(error){showToast(error.message,true)}
    finally{button.disabled=false}
  }
  const networkQualityPercent=value=>Math.round(Math.max(0,Math.min(255,Number(value)||0))/255*100);
  const networkQualityTone=quality=>quality>=55?"strong":quality>=25?"good":"weak";
  function networkGraphLabel(value){
    const label=String(value||"—");
    return label.length>19?`${label.slice(0,17)}…`:label;
  }
  function renderNetworkGraph(map){
    const nodes=Array.isArray(map.nodes)?map.nodes:[];
    const links=Array.isArray(map.links)?map.links:[];
    if(!nodes.length)return`<div class="network-map-empty">${t("noNetworkMap")}</div>`;
    const nodeById=new Map(nodes.map(node=>[String(node.id).toLowerCase(),node]));
    const columns=[[],[],[]];
    nodes.forEach(node=>{
      const type=String(node.type||"").toLowerCase();
      columns[type==="coordinator"?0:type==="router"?1:2].push(node);
    });
    columns.forEach(column=>column.sort((left,right)=>String(left.name||left.id).localeCompare(String(right.name||right.id),state.language)));
    const height=Math.max(360,Math.max(...columns.map(column=>column.length))*72+96);
    const xPositions=[110,500,890];
    const positions=new Map();
    columns.forEach((column,columnIndex)=>column.forEach((node,index)=>{
      const y=column.length===1?height/2:64+index*(height-128)/(column.length-1);
      positions.set(String(node.id).toLowerCase(),{x:xPositions[columnIndex],y});
    }));
    const edgeMarkup=links.map(link=>{
      const fromId=String(link.from).toLowerCase(),toId=String(link.to).toLowerCase();
      const fromPosition=positions.get(fromId),toPosition=positions.get(toId);
      if(!fromPosition||!toPosition)return"";
      const quality=networkQualityPercent(link.quality);
      const tone=networkQualityTone(quality);
      const controlX=fromPosition.x===toPosition.x?fromPosition.x+46:(fromPosition.x+toPosition.x)/2;
      const path=`M ${fromPosition.x} ${fromPosition.y} C ${controlX} ${fromPosition.y}, ${controlX} ${toPosition.y}, ${toPosition.x} ${toPosition.y}`;
      const from=nodeById.get(fromId),to=nodeById.get(toId);
      return`<path class="network-edge ${tone}" d="${path}" stroke-width="${(1.4+quality/28).toFixed(1)}"><title>${esc(from?.name||link.from)} → ${esc(to?.name||link.to)} · ${quality}%</title></path>`;
    }).join("");
    const nodeMarkup=nodes.map(node=>{
      const nodeId=String(node.id).toLowerCase();
      const position=positions.get(nodeId);
      if(!position)return"";
      const attached=links.filter(link=>String(link.from).toLowerCase()===nodeId||String(link.to).toLowerCase()===nodeId);
      const quality=attached.length?Math.round(attached.reduce((sum,link)=>sum+networkQualityPercent(link.quality),0)/attached.length):null;
      const type=String(node.type||"").toLowerCase();
      const typeClass=type==="coordinator"?"coordinator":type==="router"?"router":"end-device";
      const symbol=typeClass==="coordinator"?"◆":typeClass==="router"?"◇":"•";
      return`<g class="network-node ${typeClass}" transform="translate(${position.x} ${position.y})"><title>${esc(node.name||node.id)}${quality===null?"":` · ${quality}%`}</title><circle class="node-halo" r="21"></circle><text class="node-symbol" x="0" y="0">${symbol}</text><text class="node-name" x="0" y="36">${esc(networkGraphLabel(node.name||node.id))}</text><text class="node-quality" x="0" y="51">${quality===null?"—":`${quality}%`}</text></g>`;
    }).join("");
    return`<section class="network-graph"><div class="network-graph-head"><div class="network-graph-copy"><strong>${t("networkMap")}</strong><span>${t("networkGraphLead")}</span></div><div class="network-legend"><span class="coordinator"><i></i>${t("coordinator")}</span><span class="router"><i></i>${t("networkRouter")}</span><span class="end-device"><i></i>${t("networkEndDevice")}</span><span class="link strong"><i></i>${t("networkStrongLink")}</span><span class="link weak"><i></i>${t("networkWeakLink")}</span></div></div><div class="network-graph-scroll"><svg class="network-graph-svg" viewBox="0 0 1000 ${height}" role="img" aria-label="${esc(t("networkMap"))}" preserveAspectRatio="xMidYMid meet">${edgeMarkup}${nodeMarkup}</svg></div></section>`;
  }
  async function scanNetworkMap(){
    const button=$("#scanNetworkMap");
    const results=$("#networkMapResults");
    button.disabled=true;
    results.innerHTML=`<div class="network-map-loading">${t("mappingNetwork")}</div>`;
    try{
      const data=await api("/api/zigbee/network-map");
      const map=data.map||{nodes:[],links:[]};
      results.innerHTML=renderNetworkGraph(map);
    }catch(error){results.innerHTML="";showToast(error.message,true)}
    finally{button.disabled=false}
  }
  async function createZigbeeGroup(){
    const input=$("#zigbeeGroupName");
    const name=input.value.trim();
    if(name.length<2)return;
    try{
      const data=await api("/api/groups",{method:"POST",body:JSON.stringify({name})});
      state.zigbeeGroups=data.groups||[];
      input.value="";
      renderZigbeeGroups();
    }catch(error){showToast(error.message,true)}
  }
  async function renameZigbeeGroup(id){
    const input=$$("[data-zgroup-name]").find(item=>item.dataset.zgroupName===id);
    const name=input?.value.trim();
    if(!name||name.length<2)return;
    try{
      const data=await api(`/api/groups/${encodeURIComponent(id)}`,{method:"PUT",body:JSON.stringify({name})});
      state.zigbeeGroups=data.groups||[];
      renderZigbeeGroups();
    }catch(error){showToast(error.message,true)}
  }
  function addZigbeeGroupMember(id){
    const select=$$("[data-zgroup-device]").find(item=>item.dataset.zgroupDevice===id);
    if(select?.value)setZigbeeGroupMember(id,select.value,true);
  }
  async function setZigbeeGroupMember(id,deviceId,add){
    try{
      const data=await api(`/api/groups/${encodeURIComponent(id)}/member`,{method:"PUT",body:JSON.stringify({deviceId,add})});
      state.zigbeeGroups=data.groups||[];
      renderZigbeeGroups();
    }catch(error){showToast(error.message,true)}
  }
  async function deleteZigbeeGroup(id){
    if(!confirm(t("deleteZigbeeGroupConfirm")))return;
    try{
      const data=await api(`/api/groups/${encodeURIComponent(id)}`,{method:"DELETE"});
      state.zigbeeGroups=data.groups||[];
      renderZigbeeGroups();
    }catch(error){showToast(error.message,true)}
  }
  function renderBindingEndpoints(){
    const source=state.devices.find(device=>device.id===$("#bindSource").value);
    const target=state.devices.find(device=>device.id===$("#bindTarget").value);
    const sourceEndpoint=$("#bindSourceEndpoint"),targetEndpoint=$("#bindTargetEndpoint");
    const previousSource=sourceEndpoint.value,previousTarget=targetEndpoint.value;
    const sourceEndpoints=(source?.endpoints||[]).filter(endpoint=>endpoint.outputClusters.length);
    const targetEndpoints=(target?.endpoints||[]).filter(endpoint=>endpoint.inputClusters.length);
    sourceEndpoint.innerHTML=`<option value="">${t("automaticEndpoint")}</option>`+sourceEndpoints.map(endpoint=>`<option value="${endpoint.id}">${esc(endpoint.name)}</option>`).join("");
    targetEndpoint.innerHTML=`<option value="">${target?t("automaticEndpoint"):t("groupTarget")}</option>`+targetEndpoints.map(endpoint=>`<option value="${endpoint.id}">${esc(endpoint.name)}</option>`).join("");
    targetEndpoint.disabled=!target;
    if([...sourceEndpoint.options].some(option=>option.value===previousSource))sourceEndpoint.value=previousSource;
    if([...targetEndpoint.options].some(option=>option.value===previousTarget))targetEndpoint.value=previousTarget;
  }
  function renderBindingList(){
    const bindingTarget=binding=>binding.targetType==="group"
      ?state.zigbeeGroups.find(group=>group.id===binding.targetId)
      :state.devices.find(device=>device.id===binding.targetId);
    const rows=state.devices
      .flatMap(device=>(device.endpoints||[]).flatMap(endpoint=>(endpoint.bindings||[]).map(binding=>({device,endpoint,binding}))))
      .filter(({binding})=>Boolean(bindingTarget(binding)));
    $("#zigbeeBindingList").innerHTML=rows.length?rows.map(({device,endpoint,binding})=>{
      const target=bindingTarget(binding);
      const targetEndpoint=binding.targetEndpoint?` · EP ${binding.targetEndpoint}`:"";
      return`<div class="zigbee-binding-row"><span><strong>${esc(device.name)} · ${esc(endpoint.name)}</strong><br>${esc(binding.cluster)} → ${esc(target?.name||binding.targetId)}${targetEndpoint}</span><button class="danger-button" type="button" data-remove-binding="${esc(device.id)}" data-from-endpoint="${endpoint.id}" data-binding-target="${esc(binding.targetId)}" data-to-endpoint="${binding.targetEndpoint??""}" data-binding-cluster="${esc(binding.cluster)}" title="${t("removeBinding")}">×</button></div>`;
    }).join(""):`<div class="zigbee-group-empty"><span aria-hidden="true">⌁</span><div><strong>${t("noBindings")}</strong><p>${t("noBindingsLead")}</p></div></div>`;
    $$("[data-remove-binding]").forEach(button=>button.onclick=()=>removeExistingBinding(button));
  }
  async function removeExistingBinding(button){
    const fromEndpoint=Number(button.dataset.fromEndpoint);
    const toEndpoint=button.dataset.toEndpoint?Number(button.dataset.toEndpoint):undefined;
    try{
      const data=await api("/api/zigbee/bind",{method:"POST",body:JSON.stringify({
        fromId:button.dataset.removeBinding,
        toId:button.dataset.bindingTarget,
        bind:false,
        clusters:[button.dataset.bindingCluster],
        fromEndpoint,
        toEndpoint
      })});
      state.devices=data.devices||state.devices;
      renderZigbeeGroups();
      showToast(t("bindingRemoved"));
    }catch(error){showToast(error.message,true)}
  }
  async function bindZigbeeDevices(bind){
    const fromId=$("#bindSource").value,toId=$("#bindTarget").value;
    if(!fromId||!toId||fromId===toId)return;
    const fromEndpoint=$("#bindSourceEndpoint").value?Number($("#bindSourceEndpoint").value):undefined;
    const toEndpoint=$("#bindTargetEndpoint").value?Number($("#bindTargetEndpoint").value):undefined;
    try{
      const data=await api("/api/zigbee/bind",{method:"POST",body:JSON.stringify({fromId,toId,bind,fromEndpoint,toEndpoint})});
      state.devices=data.devices||state.devices;
      renderZigbeeGroups();
      showToast(t(bind?"bindingSaved":"bindingRemoved"));
    }catch(error){showToast(error.message,true)}
  }
  /* TEK YEDEK — ayarlar, odalar ve Zigbee ağı tek bir dosyada (`/api/backup/full`, ZIP).
     Eski iki düğme (ev yedeği + Zigbee yedeği) kaldırıldı; uçlar sunucuda duruyor.

     İNDİRME NEDEN BLOB DEĞİL: eski kod yanıtı `fetch` ile belleğe alıp `URL.createObjectURL` +
     `<a download>` ile kaydediyordu ve BAŞARI BİLDİRİMİNİ tıklamanın gerçekten dosya bırakıp
     bırakmadığına BAKMADAN gösteriyordu — kullanıcının gördüğü "indirildi diyor ama dosya yok"
     tam olarak buydu. Artık indirmeyi tarayıcının kendi yolu yapıyor: gizli bir çerçeve sunucu
     ucuna gider, sunucu `Content-Disposition: attachment` yollar, tarayıcı gezinmeyi indirmeye
     çevirir. Bu çevrim gerçekleştiğinde çerçevenin `load` olayı HİÇ ateşlenmez; ateşlenmesi
     sunucunun belge (yani hata) döndürdüğü anlamına gelir. Doğru/yanlış ayrımı buradan gelir,
     tahminden değil. */
  const backupDownloadCleanupDelay=60000;
  function downloadFullBackup(){
    const button=$("#downloadFullBackup");
    button.disabled=true;
    const frame=document.createElement("iframe");
    frame.hidden=true;
    frame.setAttribute("aria-hidden","true");
    frame.addEventListener("load",()=>{
      let message=t("operationFailed");
      try{
        const data=JSON.parse(frame.contentDocument?.body?.textContent||"");
        if(data&&data.error)message=data.error;
      }catch{}
      frame.remove();
      button.disabled=false;
      showToast(message,true);
    });
    document.body.append(frame);
    frame.src="/api/backup/full";
    showToast(t("backupPreparing"));
    setTimeout(()=>{button.disabled=false},3000);
    setTimeout(()=>{if(frame.isConnected)frame.remove()},backupDownloadCleanupDelay);
  }
  let pendingHomeBackup=null;
  /* Dosya sunucuya base64 gövdeyle gider: ZIP ikili, panelin `api()` yardımcısı ise JSON
     konuşuyor. Parça parça kodlanır, tek seferde `String.fromCharCode(...bytes)` çağrısı
     birkaç MB'lık dosyada yığın taşırırdı. */
  async function encodeBackupFile(file){
    const bytes=new Uint8Array(await file.arrayBuffer());
    let binary="";
    for(let index=0;index<bytes.length;index+=0x8000){
      binary+=String.fromCharCode.apply(null,bytes.subarray(index,index+0x8000));
    }
    return btoa(binary);
  }
  function selectedHomeBackupMode(){
    return $$("input[name=homeBackupMode]").find(radio=>radio.checked)?.value||"merge";
  }
  function renderHomeBackupSummary(preview){
    const host=$("#homeBackupSummary");
    host.textContent="";
    const summary=preview?.summary;
    const sections=(summary?.sections||[]).filter(section=>section.incoming>0||section.removed>0);
    sections.forEach(section=>{
      const row=document.createElement("div");
      row.className="home-backup-row";
      const name=document.createElement("strong");
      name.textContent=t(`homeBackupSection${section.section.charAt(0).toUpperCase()}${section.section.slice(1)}`);
      const detail=document.createElement("span");
      detail.textContent=summary.mode==="replace"
        ?t("homeBackupCountsReplace",{added:section.added,overwritten:section.overwritten,removed:section.removed})
        :t("homeBackupCountsMerge",{added:section.added,overwritten:section.overwritten});
      row.append(name,detail);
      host.append(row);
    });
    if(preview?.zigbee){
      const row=document.createElement("div");
      row.className="home-backup-row";
      const name=document.createElement("strong");
      name.textContent=t("homeBackupSectionZigbee");
      const detail=document.createElement("span");
      detail.textContent=t("homeBackupZigbeeFiles",{count:preview.zigbee.files});
      row.append(name,detail);
      host.append(row);
    }
    if(!sections.length&&!preview?.zigbee){
      const empty=document.createElement("p");
      empty.textContent=t("homeBackupNothing");
      host.append(empty);
    }
    if(summary){
      const note=document.createElement("p");
      note.className="home-backup-note";
      note.textContent=summary.totalSkippedMissingDevices>0
        ?t("homeBackupSkipped",{count:summary.totalSkippedMissingDevices})
        :t("homeBackupNoSkips");
      host.append(note);
    }
    if(preview?.zigbeeArchiveOnly){
      const note=document.createElement("p");
      note.className="home-backup-note";
      note.textContent=t("homeBackupZigbeeArchiveOnly");
      host.append(note);
    }
    if(preview?.zigbee){
      const note=document.createElement("p");
      note.className="home-backup-note";
      note.textContent=t("homeBackupRestartNote");
      host.append(note);
    }
  }
  async function previewHomeBackup(){
    if(!pendingHomeBackup)return;
    const button=$("#confirmHomeRestore");
    button.disabled=true;
    $("#homeBackupSummary").textContent=t("homeBackupChecking");
    try{
      const data=await api("/api/backup/full/preview",{method:"POST",body:JSON.stringify({file:pendingHomeBackup,mode:selectedHomeBackupMode()})});
      renderHomeBackupSummary(data);
      button.disabled=false;
    }catch(error){$("#homeBackupSummary").textContent=error.message}
  }
  /* Tek "Yedeği geri yükle" akışı: dosya tipi İÇERİĞİNDEN tanınır (sunucuda), böylece hem yeni
     birleşik ZIP hem de kullanıcının elindeki eski ev-only / zigbee-only JSON dosyaları aynı
     düğmeden yüklenir. */
  async function chooseFullRestore(event){
    const input=event.currentTarget;
    const file=input.files?.[0];
    input.value="";
    if(!file)return;
    try{
      // Sunucudaki `maximumFullBackupBytes` ile aynı sınır: base64 gövde 30 MB'ı aşmasın.
      if(file.size>20*1024*1024||file.size===0)throw new Error(t("homeBackupInvalidFile"));
      pendingHomeBackup=await encodeBackupFile(file);
    }catch(error){
      pendingHomeBackup=null;
      showToast(error.message||t("homeBackupInvalidFile"),true);
      return;
    }
    $$("input[name=homeBackupMode]").forEach(radio=>{radio.checked=radio.value==="merge"});
    $("#homeBackupFileMeta").textContent=file.name;
    $("#confirmHomeRestore").disabled=true;
    $("#homeBackupDialog").showModal();
    await previewHomeBackup();
  }
  function closeHomeRestore(){
    pendingHomeBackup=null;
    $("#homeBackupDialog").close();
  }
  async function applyHomeRestore(){
    if(!pendingHomeBackup)return;
    const button=$("#confirmHomeRestore");
    button.disabled=true;
    try{
      const data=await api("/api/backup/full/restore",{method:"POST",body:JSON.stringify({
        file:pendingHomeBackup,
        mode:selectedHomeBackupMode(),
        confirmation:"RESTORE"
      })});
      pendingHomeBackup=null;
      $("#homeBackupDialog").close();
      // Zigbee bölümü de geldiyse her şey yerine kondu ve servis TEK SEFER iniyor.
      if(data.restarting){showToast(t("restoreStarting"));waitForRestart();return}
      showToast(t("homeBackupRestored"));
      await Promise.allSettled([refresh(),loadHomeGroups(),loadHomeVisibility(),loadHomeFavorites(),loadAutomations()]);
      render();
    }catch(error){
      button.disabled=false;
      showToast(error.message,true);
    }
  }
  function waitForRestart(){
    let observedOffline=false;
    let attempts=0;
    const poll=async()=>{
      attempts+=1;
      try{
        const response=await fetch("/api/health",{cache:"no-store"});
        if(observedOffline&&response.ok){location.reload();return;}
      }catch{observedOffline=true}
      if(attempts<45)setTimeout(poll,1500);
      else location.reload();
    };
    setTimeout(poll,5000);
  }
  /* AYAR KAYDETMENİN İKİ YOLU — hangi alanın değiştiğine göre ayrılır.
     Koordinatör/MQTT/Matter adresleri artık YALNIZ kurulum sihirbazında soruluyor; buradaki
     iki işlev sihirbazın sormadığı alanları kaydeder. Gövde her zaman tam ayar nesnesidir
     (sunucu kısmi gövde kabul etmiyor), değişmeyen her alan kayıtlı değerinden okunur —
     böylece bir sekmedeki kaydetme öbür sekmedeki dokunulmamış alanı sürüklemez. */
  function settingsPayload(overrides){
    const current=state.settings;
    return{
      zigbee:{adapterUrl:current.zigbee.adapterUrl,channel:current.zigbee.channel},
      mqtt:{url:current.mqtt.url,baseTopic:current.mqtt.baseTopic},
      matter:{wsUrl:current.matter.wsUrl},
      homeAssistant:{discoveryEnabled:current.homeAssistant?.discoveryEnabled===true},
      alerts:{lowBatteryThreshold:current.alerts?.lowBatteryThreshold??15},
      selfHealing:{enabled:current.selfHealing?.enabled!==false,probeOffline:current.selfHealing?.probeOffline===true},
      debug:{enabled:current.debug?.enabled!==false},
      ...overrides
    };
  }
  /* Kanal değişikliği telsizi yeniden kurar: onay + yeniden başlatma ister. Otomatik onarım
     anahtarı sunucuda anında işler, onun için yeniden başlatma yoktur. */
  async function saveNetworkSettings(){
    if(!state.settings)return showToast(t("settingsUnavailable"),true);
    const channel=Number($("#zigbeeChannel").value);
    const restartNeeded=channel!==Number(state.settings.zigbee.channel);
    const settings=settingsPayload({
      zigbee:{adapterUrl:state.settings.zigbee.adapterUrl,channel},
      selfHealing:{enabled:$("#selfHealingEnabled").value!=="false",probeOffline:state.settings?.selfHealing?.probeOffline===true}
    });
    const payload=settingsWithChannelConfirmation(settings);
    if(!payload)return;
    if(restartNeeded&&!confirm(t("restartConfirm")))return;
    const button=$("#saveNetworkSettings");
    button.disabled=true;
    try{
      const data=await api("/api/settings",{method:"PUT",body:JSON.stringify(payload)});
      state.settings=data.settings||state.settings;
      if(!restartNeeded){showToast(t("settingsStored"));button.disabled=false;return}
      showToast(t("settingsSaved"));
      await api("/api/settings/apply",{method:"POST",body:JSON.stringify({confirmation:"APPLY"})});
      waitForRestart();
    }catch(error){button.disabled=false;showToast(error.message,true)}
  }
  /* Düşük pil eşiği günlük bir ayardır: sunucu anında uygular, yeniden başlatma yoktur. */
  async function saveBatteryThreshold(){
    if(!state.settings)return showToast(t("settingsUnavailable"),true);
    const input=$("#lowBatteryThreshold");
    if(!input.reportValidity())return;
    const button=$("#saveBatteryThreshold");
    button.disabled=true;
    try{
      const data=await api("/api/settings",{method:"PUT",body:JSON.stringify(settingsPayload({alerts:{lowBatteryThreshold:Number(input.value)}}))});
      state.settings=data.settings||state.settings;
      showToast(t("settingsStored"));
      render();
    }catch(error){showToast(error.message,true)}
    finally{button.disabled=false}
  }
