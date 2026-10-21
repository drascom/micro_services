  function renderPairingProgress(){
    const session=state.pairingSession;if(!session)return;
    const pairingDevice=state.pairing?.device||returnedPairingDevice(session);
    const device=pairingDevice?state.devices.find(item=>item.id===pairingDevice.id):null;
    const reconnected=pairingDevice?.reconnected===true||session.reconnected===true;
    const visual=$("#pairingVisual");visual.className=`pairing-visual ${session.phase==="found"?"found":session.phase==="ready"?"ready":session.phase==="timed-out"?"timed-out":""}`.trim();
    $("#pairingPhase").textContent=t(session.phase==="found"?"pairingFound":session.phase==="ready"?(reconnected?"pairingReconnected":"pairingReady"):session.phase==="timed-out"?"pairingTimedOut":"pairingSearching");
    $("#pairingModalTitle").textContent=t(session.phase==="found"?"pairingFound":session.phase==="ready"?(reconnected?"pairingReconnected":"pairingReady"):session.phase==="timed-out"?"pairingTimedOut":"pairingTitle");
    $("#pairingModalText").textContent=t(session.phase==="found"?"pairingFinishing":session.phase==="ready"?(reconnected?"pairingReconnectComplete":"pairingComplete"):session.phase==="timed-out"?"pairingTimedOutLead":"pairingHint");
    $("#pairingDevice").hidden=!pairingDevice;
    if(pairingDevice)$("#pairingDeviceName").textContent=device?.name||pairingDevice.name||pairingDevice.id;
    $("#hidePairing").hidden=session.phase==="ready"||session.phase==="timed-out";
    $("#cancelPairing").textContent=t(session.phase==="timed-out"?"close":"stopSearch");
    /* Az önce kaldırılan bir cihaz yeniden eşleşmeye başlıyorsa uyar: silme sırasında cihaza giden
       "ağdan ayrıl" komutu mesh'te gecikirse yeni oturumu düşürebilir. */
    const removedRecently=Boolean(pairingDevice)&&(state.departures||[]).some(item=>item.id===pairingDevice.id&&item.reason==="removed");
    $("#pairingWarning").hidden=!removedRecently;
    if(removedRecently)$("#pairingWarning").textContent=t("pairingRemovedWarning");
    /* Cihaz bir an düşüp geri gelebiliyor. Bu tek satır bunu söyler; akış kesilmez, hiçbir adım
       kapanmaz — kullanıcı yalnız beklendiğini bilir. */
    const waiting=state.deviceReturnWait;
    $("#pairingWaitLine").hidden=!waiting;
    if(waiting)$("#pairingWaitLine").textContent=t("pairingDeviceWait");
  }
  /* Katılım olayı (`device_joined`/`device_announce`) yalnız ağa yeni giren ya da duyuru yapan
     cihaz için üretilir. Silinmiş sanılan bir cihaz aslında ağda kaldıysa yeni aramada hiç olay
     çıkarmaz: arama sonsuza dek sürer, kurulum adımları hiç açılmazdı. Bu yüzden arama sırasında
     listeye dönen cihaz da "bulundu" sayılır ve "yeniden bağlandı" yolundan ilerler. */
  function returnedPairingDevice(session){
    if(!session)return null;
    const known=session.knownIds||[];
    const returned=(state.devices||[]).find(device=>session.targetId
      ?device.id===session.targetId
      :!known.includes(device.id));
    if(!returned)return null;
    return{id:returned.id,name:returned.name,interviewCompleted:true,supported:null,reconnected:true};
  }
  function trackPairingProgress(){
    const session=state.pairingSession;if(!session||session.phase==="ready")return;
    const found=state.pairing?.device||returnedPairingDevice(session);
    if(found){
      session.foundId=found.id;
      session.reconnected=found.reconnected===true;
      session.phase=found.interviewCompleted===true?"ready":"found";
      if(session.phase==="ready"&&!session.completing){
        session.completing=true;
        schedulePairingNetworkClose();
        setTimeout(async()=>{
          if(state.pairingSession!==session)return;
          if($("#pairingDialog").open)$("#pairingDialog").close();
          showToast(t(session.reconnected?"pairingReconnectComplete":"pairingComplete"));
          await refresh();
          openPairingName(session.foundId,session.reconnected);
        },1200);
      }
      return;
    }
    if(state.pairing?.open===false)session.phase="timed-out";
  }
  /* Ağ, interview biter bitmez kapatılmaz. Zigbee revizyon 21 cihazlar katılımdan sonra güven
     merkezi bağlantı anahtarı değişimini sürdürüyor; `permit_join` o anda kapatılınca süreç
     yarıda kalıyor ve cihaz saniyeler içinde ağdan düşüyor. Bu yüzden kapatma en az bu kadar
     ertelenir; kullanıcı için akış (diyalog, bildirim, kurulum adımları) aynı kalır. */
  const pairingNetworkHoldMs=60000;
  function schedulePairingNetworkClose(){
    cancelPairingNetworkClose();
    /* Bekleyen kapatma oturuma bağlı: yeni bir eşleştirme başlarsa eskisi iptal edilir, yani
       eski oturumun zamanlayıcısı yeni açılan ağı kapatamaz. */
    const pending={ready:false,timer:null};
    state.pairingNetworkClose=pending;
    pending.timer=setTimeout(()=>{
      pending.timer=null;
      pending.ready=true;
      void closePairingNetworkIfIdle();
    },pairingNetworkHoldMs);
  }
  function cancelPairingNetworkClose(){
    const pending=state.pairingNetworkClose;
    if(pending?.timer)clearTimeout(pending.timer);
    state.pairingNetworkClose=null;
  }
  /* Bekleme dolsa bile isim/oda/rol adımları sürerken ağ kapatılmaz. Akış bittiğinde ya da
     iptal edildiğinde `refresh()` bunu yeniden çağırır, kapatma o an yapılır. Sekme kapanırsa
     zamanlayıcı sayfayla birlikte kaybolur; ağı sunucunun kendi 180 sn'lik süresi söndürür. */
  async function closePairingNetworkIfIdle(){
    const pending=state.pairingNetworkClose;
    // Dönüşü beklenen cihaz varken de ağ kapatılmaz: beklediğimiz şey tam da geri katılması.
    if(!pending||!pending.ready||setupFlowDeviceId()!==null||state.deviceReturnWait)return;
    state.pairingNetworkClose=null;
    try{await api("/api/pairing/stop",{method:"POST"})}catch{}
    state.pairing={open:false};
  }
  /* Kurulum akışının hangi cihaz üstünde olduğunu tek yerden söyler: isim, görsel, rol ve oda
     adımlarının hepsi aynı cihazı taşır. */
  function setupFlowDeviceId(){
    if(state.editing?.afterPairing)return state.editing.id;
    if(state.imageEditing?.afterPairing)return state.imageEditing.id;
    if(state.roleEditing?.afterPairing)return state.roleEditing.id;
    if(state.roomEditing?.afterPairing)return state.roomEditing.id;
    return null;
  }
  function deviceGoneCode(error){
    return error?.code==="DEVICE_LEFT"||error?.code==="DEVICE_REMOVED"?error.code:null;
  }
  /* Kaydetme sırasında cihaz bir an düşmüş olabilir: sunucunun "ağdan ayrıldı" yanıtı da akışı
     hemen bitirmez, aynı dönüş penceresi işletilir. Kullanıcının kendi kaldırdığı cihaz beklenmez. */
  function reportDeviceGone(id,code){
    if(code==="DEVICE_LEFT"&&setupFlowDeviceId()===id){awaitDeviceReturn(id);return}
    openDeviceLost(id,code);
  }
  /* Yeni katılan cihaz interview biter bitmez bir kez düşüp saniyeler içinde geri gelebiliyor:
     güven merkezi anahtar değişimi sürerken, ağ hâlâ açıkken bile olan bir şey. Böyle bir düşüş
     eşleştirmeyi bitirmez — cihaz bu pencere boyunca beklenir. Dönerse akış kaldığı yerden sürer,
     dönmezse pencere dolduğunda bugünkü "cihaz ağdan düştü" diyaloğu çıkar. */
  const deviceReturnWaitMs=30000;
  /* Saat tek yerden okunur: testler pencereyi gerçek beklemeden ilerletebilsin. */
  const pairingNow=()=>Date.now();
  /* Beklemeye giriş: hiçbir adım kapatılmaz, yalnız tek satırla haber verilir. `resume` doğruysa
     kurulum sihirbazı henüz açılmamıştır — cihaz döndüğü an açılacak. */
  function awaitDeviceReturn(id,resume=false,reconnected=false){
    if(state.deviceReturnWait?.id===id)return;
    state.deviceReturnWait={id,since:pairingNow(),resume,reconnected};
    showToast(t("pairingDeviceWait"));
    if(!state.pairingSession)return;
    renderPairingProgress();
    if(resume&&!$("#pairingDialog").open)$("#pairingDialog").showModal();
  }
  /* Kurulum açıkken cihaz listeden düşerse kullanıcı "Kaydet"e basana kadar bekletilmez: panel
     zaten periyodik yeniliyor. Ama sebep hemen "kayboldu" değildir; önce dönüş penceresi. */
  function watchSetupFlowDevice(){
    if(state.deviceLost)return;
    const waiting=state.deviceReturnWait;
    // Sihirbaz henüz açılmadıysa akışın cihazını bekleyen kaydın kendisi taşır.
    const id=setupFlowDeviceId()||waiting?.id||null;
    if(!id)return;
    if(state.devices.some(device=>device.id===id)){
      if(waiting?.id!==id)return;
      state.deviceReturnWait=null;
      showToast(t("pairingDeviceReturned"));
      if(!waiting.resume){renderPairingProgress();return}
      if($("#pairingDialog").open)$("#pairingDialog").close();
      openPairingName(id,waiting.reconnected===true);
      return;
    }
    const departure=(state.departures||[]).find(item=>item.id===id);
    // Kullanıcının kendi kaldırdığı cihaz beklenmez: dönmesi beklenen bir cihaz değil.
    if(departure?.reason==="removed"){openDeviceLost(id,"DEVICE_REMOVED");return}
    if(waiting?.id!==id){awaitDeviceReturn(id);return}
    if(pairingNow()-waiting.since<deviceReturnWaitMs)return;
    openDeviceLost(id,"DEVICE_LEFT");
  }
  function renderDeviceLost(){
    const lost=state.deviceLost;if(!lost)return;
    const removed=lost.code==="DEVICE_REMOVED";
    $("#deviceLostTitle").textContent=t(removed?"deviceRemovedTitle":"deviceLostTitle");
    $("#deviceLostLead").textContent=t(removed?"deviceRemovedLead":"deviceLostLead");
  }
  /* Akış kilitli kalmaz: her adım kapatılır, kullanıcıya baştan deneme ya da kapatma bırakılır. */
  function openDeviceLost(id,code){
    state.deviceLost={id,code};
    state.editing=null;state.imageEditing=null;state.roleEditing=null;state.roomEditing=null;state.pairingSession=null;state.deviceReturnWait=null;
    ["#nameDialog","#imageDialog","#deviceRoleDialog","#deviceRoomDialog","#pairingDialog"].forEach(selector=>{
      const dialog=$(selector);if(dialog?.open)dialog.close();
    });
    renderDeviceLost();
    if(!$("#deviceLostDialog").open)$("#deviceLostDialog").showModal();
  }
  function closeDeviceLost(){
    state.deviceLost=null;
    if($("#deviceLostDialog").open)$("#deviceLostDialog").close();
    render();
  }
  /* "Tekrar dene" önce cihazın kendiliğinden geri gelip gelmediğine bakar: silme komutu mesh'te
     düşüp cihaz ağda kaldıysa listede zaten durur, o hâlde arama açmanın anlamı yok — doğrudan
     kurulum adımları açılır. Değilse arama hedefi bu cihaz olarak başlatılır. */
  function retryDeviceLost(){
    const id=state.deviceLost?.id||null;
    closeDeviceLost();
    if(id&&state.devices.some(device=>device.id===id)){openPairingName(id,true);return}
    startPairing(true,id);
  }
  async function startPairing(open=true,targetId=null){
    if(open&&!state.overviewLoaded)return;
    /* Elle "Aramayı durdur" bilinçli bir eylem: beklemeden kapatılır. Yeni bir arama da eski
       oturumun bekleyen kapatmasını üstlenmez. */
    cancelPairingNetworkClose();
    if(open){
      /* Arama başlarken listedeki cihazlar not edilir: sonradan beliren bir kimlik, katılım
         olayı hiç gelmese bile "geri döndü" demektir. Yeni arama eski beklemeyi de sıfırlar. */
      state.deviceReturnWait=null;
      state.pairingSession={foundId:null,phase:"searching",hidden:false,completing:false,reconnected:false,targetId:targetId||null,knownIds:(state.devices||[]).map(device=>device.id)};
      renderPairingProgress();$("#pairingDialog").showModal();
    }
    try{
      const routerId=$("#pairingRouter")?.value||undefined;
      const data=await api(open?"/api/pairing/start":"/api/pairing/stop",{method:"POST",body:open?JSON.stringify({seconds:180,routerId}):undefined});
      state.pairing=open?data.pairing:{open:false};
      if(open)trackPairingProgress();
      if(!open){if($("#pairingDialog").open)$("#pairingDialog").close();state.pairingSession=null;state.deviceReturnWait=null;}
      render();showToast(open?t("searchStarted"):t("searchStopped"));
    }catch(error){
      if(open&&$("#pairingDialog").open)$("#pairingDialog").close();
      if(open)state.pairingSession=null;
      showToast(error.message,true);
    }
  }
  function configureNameDialog(afterPairing=false,reconnected=false){
    $("#nameDialogTitle").textContent=t(afterPairing?(reconnected?"confirmReconnectedName":"nameNewDevice"):"changeDisplayName");
    $("#nameDialogLead").textContent=t(afterPairing?(reconnected?"confirmReconnectedNameLead":"nameNewDeviceLead"):"displayNameLead");
    $("#cancelName").textContent=t(afterPairing?"skip":"cancel");
  }
  function finishPairingFlow(id){
    state.pairingSession=null;
    state.editing=null;
    render();
    showDevice(id,{fromPairing:true});
  }
  function continuePairingFlow(id){
    const device=state.devices.find(item=>item.id===id);
    if(device?.image?.selectionRequired){
      openImageChooser(id,true);
      return;
    }
    // Son adım: "Bu cihaz ne?" — atlanırsa rol Otomatik (tahmin) kalır.
    askDeviceRole(id,true);
  }
  function openPairingName(id,reconnected=false){
    const device=state.devices.find(item=>item.id===id);
    /* Cihaz tam bu anda listede değilse akış bitirilmez: kısa süreli bir düşüş olabilir, dönüşü
       beklenir. Eskiden burada akış sessizce biterdi; cihaz saniyeler sonra dönünce listede ham
       kimliğiyle kalır, kullanıcıya isim/tür/oda hiç sorulmazdı. */
    if(!device){awaitDeviceReturn(id,true,reconnected);return}
    state.deviceReturnWait=null;
    state.editing={id,channel:null,afterPairing:true,reconnected};
    configureNameDialog(true,reconnected);
    $("#nameInput").value=deviceNeedsName(device)?"":device.name;
    $("#nameInput").placeholder=device.model||t("newName");
    // Ad alanına odaklanmıyoruz; odak `showModal` ile başlıkta kalır (klavye ekranı kapatmasın).
    $("#nameDialog").showModal();
  }
  /* Yarım kalan kurulumu tamamlama: cihaz zaten ağda olduğu için arama açılmaz, doğrudan
     kurulum adımları çalışır — yeni katılmış cihazla aynı yol. */
  function finishDeviceSetup(id){
    if(!state.devices.some(device=>device.id===id))return;
    closeDeviceDetail();
    openPairingName(id,false);
  }
  function openRename(id,channel=null){
    const device=state.devices.find(item=>item.id===id);if(!device)return;
    const control=channel?device.controls.find(item=>item.id===channel):null;
    // Kanal kimliği bir düğme de olabilir (`button:1`); alias anahtarı aynı yolu kullanır.
    const button=channel&&!control?(device.buttons||[]).find(item=>item.id===channel):null;
    state.editing={id,channel,afterPairing:false};
    configureNameDialog(false);
    $("#nameInput").placeholder=t("newName");
    $("#nameInput").value=control?.name||(button?deviceButtonName(button):"")||device.name;
    // Ad alanı seçili açılmaz: metin yerinde durur, kullanıcı dokununca düzenler.
    $("#nameDialog").showModal();
  }
  async function saveName(){
    const name=$("#nameInput").value.trim();if(!state.editing||name.length<2)return;
    const editing={...state.editing};
    try{
      await api(`/api/devices/${encodeURIComponent(editing.id)}/name`,{method:"PUT",body:JSON.stringify({name,channel:editing.channel})});
      state.editing=null;$("#nameDialog").close();showToast(t("nameSaved"));await refresh();
      if(editing.afterPairing)continuePairingFlow(editing.id);
    }
    catch(error){
      const gone=deviceGoneCode(error);
      if(gone)reportDeviceGone(editing.id,gone);else showToast(error.message,true);
    }
  }
  function cancelName(){
    const editing=state.editing;
    state.editing=null;
    if($("#nameDialog").open)$("#nameDialog").close();
    if(editing?.afterPairing)continuePairingFlow(editing.id);
  }
  function renderImageChooser(){
    const editing=state.imageEditing;
    const device=editing?state.devices.find(item=>item.id===editing.id):null;
    if(!editing||!device)return;
    $("#imageDialogLead").textContent=t("chooseDeviceImageLead",{name:device.name});
    const choices=[...(device.image?.candidates||[]),{model:null,label:"genericIcon"}];
    $("#imageChoices").innerHTML=choices.map(candidate=>{
      const selected=editing.selected===candidate.model;
      return`<button class="image-choice${selected?" selected":""}" type="button" data-image-choice="${candidate.model===null?"__generic__":esc(candidate.model)}" aria-pressed="${selected}">${deviceVisualFor(device,candidate.model)}<span>${t(candidate.label)}</span>${candidate.model?`<small>${esc(candidate.model)}</small>`:""}</button>`;
    }).join("");
    $$("[data-image-choice]").forEach(button=>button.onclick=()=>{
      editing.selected=button.dataset.imageChoice==="__generic__"?null:button.dataset.imageChoice;
      renderImageChooser();
    });
    bindDeviceImages();
  }
  function openImageChooser(id,afterPairing=false){
    const device=state.devices.find(item=>item.id===id);
    if(!device)return;
    state.imageEditing={id,selected:device.image?device.image.model:device.model,afterPairing};
    $("#applyImageToModel").checked=false;
    renderImageChooser();
    $("#imageDialog").showModal();
  }
  async function saveImage(){
    const editing=state.imageEditing;
    if(!editing)return;
    const button=$("#saveImage");
    button.disabled=true;
    try{
      const data=await api(`/api/devices/${encodeURIComponent(editing.id)}/image`,{
        method:"PUT",
        body:JSON.stringify({imageModel:editing.selected,applyToModel:$("#applyImageToModel").checked})
      });
      const index=state.devices.findIndex(item=>item.id===editing.id);
      if(index>=0&&data.device)state.devices[index]=data.device;
      showToast(t("imageSaved"));
      $("#imageDialog").close();
      render();
    }catch(error){
      button.disabled=false;
      const gone=deviceGoneCode(error);
      if(gone)reportDeviceGone(editing.id,gone);else showToast(error.message,true);
    }
  }
  function removeDevice(id){
    const device=state.devices.find(item=>item.id===id);if(!device)return;
    state.removing={id,name:device.name};
    $("#removeDeviceWarning").textContent=t("removeDeviceWarning");
    $("#removeDeviceInstruction").textContent=t("confirmRemoval",{name:device.name});
    $("#removeConfirmation").value="";
    $("#removeConfirmation").placeholder=t("removeConfirmationPlaceholder");
    $("#confirmRemove").disabled=true;
    $("#forceRemove").disabled=true;
    // Onay alanına odaklanmıyoruz; uyarı metni okunsun diye odak başlıkta başlar.
    $("#removeDialog").showModal();
  }
  async function confirmDeviceRemoval(force=false){
    if(!state.removing)return;
    if(!validRemovalConfirmation($("#removeConfirmation").value,state.removing.name)){showToast(t("nameMismatch"),true);return;}
    /* Kullanıcı ne yazarsa yazsın sunucuya tek bir onay sözcüğü gider: onayı kullanıcıdan alan
       yüzey burası, sunucudaki denetim onaysız çağrıya karşı duruyor. */
    const confirmation="evet";
    const buttons=[$("#confirmRemove"),$("#forceRemove")];buttons.forEach(button=>button.disabled=true);
    try{
      const data=await api(`/api/devices/${encodeURIComponent(state.removing.id)}`,{method:"DELETE",body:JSON.stringify({confirmation,force})});
      if(Array.isArray(data.groups)){state.groups=data.groups;saveDashboardGroups();applyWidgetLayout()}
      $("#removeDialog").close();state.removing=null;showToast(t(force?"deviceForceRemoved":"deviceRemoved"));await refresh();
    }catch(error){buttons.forEach(button=>button.disabled=false);showToast(error.message,true)}
  }
  function renderFabrics(){
    if(!state.matter)return;
    $("#fabricList").innerHTML=state.matter.fabrics.length?state.matter.fabrics.map(item=>`<div class="fabric"><strong>${esc(item.name)}</strong><span class="online-text">${t("connected")}</span></div>`).join(""):`<div class="device-meta">${t("noPlatforms")}</div>`;
  }
  async function loadMatter(openDialog=false){
    try{state.matter=await api("/api/matter");renderFabrics();if(openDialog)showMatter();}
    catch(error){$("#fabricList").innerHTML=`<div class="device-meta offline-text">${t("connectionServiceUnavailable")}</div>`;if(openDialog)showToast(error.message,true)}
  }
  async function showMatter(){
    try{const result=await api("/api/matter/commissioning",{method:"POST",body:JSON.stringify({open:true})});state.matter=result.matter;$("#qr").innerHTML=state.matter.qrSvg;$("#manualCode").textContent=state.matter.manualPairingCode.replace(/(\d{4})(\d{3})(\d{4})/,"$1-$2-$3");$("#matterDialog").showModal();startMatterWatch();}
    catch(error){showToast(error.message,true)}
  }
  const matterWatchIntervalMs=4000;
  let matterWatchTimer=null;
  let matterWatchBaseline=null;
  function stopMatterWatch(){
    if(matterWatchTimer!==null){clearInterval(matterWatchTimer);matterWatchTimer=null}
    matterWatchBaseline=null;
  }
  function startMatterWatch(){
    stopMatterWatch();
    const fabrics=state.matter?.fabrics||[];
    matterWatchBaseline={count:fabrics.length,names:new Set(fabrics.map(item=>item.name)),advertised:state.matter?.advertising===true};
    matterWatchTimer=setInterval(pollMatterWatch,matterWatchIntervalMs);
  }
  async function pollMatterWatch(){
    if(!matterWatchBaseline||!$("#matterDialog").open){stopMatterWatch();return}
    if(document.hidden)return;
    let status;
    try{status=await api("/api/matter")}catch{return}
    if(!matterWatchBaseline||!$("#matterDialog").open)return;
    const baseline=matterWatchBaseline;
    const fabrics=Array.isArray(status.fabrics)?status.fabrics:[];
    state.matter=status;
    if(fabrics.length>baseline.count){
      const added=fabrics.find(item=>!baseline.names.has(item.name));
      stopMatterWatch();
      await closeMatterDialog();
      renderFabrics();
      showToast(added?.name?t("matterPairedNamed",{name:added.name}):t("matterPaired"));
      return;
    }
    if(status.advertising===true){baseline.advertised=true;return}
    if(!baseline.advertised)return;
    stopMatterWatch();
    await closeMatterDialog();
    renderFabrics();
    showToast(t("matterPairingExpired"),true);
  }
  const brightnessPercent=control=>Math.round((Number(control.value)-Number(control.min||1))/(Number(control.max||254)-Number(control.min||1))*100);
  const temperatureKelvin=value=>Math.round(1_000_000/Math.max(1,Number(value)));
  const lightControlsBusy=()=>{
    if(state.lightPointerDown)return true;
    const active=document.activeElement;
    return Boolean(active&&$("#lightControls").contains(active)&&active.closest('select,textarea,input:not([type="range"])'));
  };
  function renderLightDialog(){
    const device=state.devices.find(item=>item.id===state.lightDevice);
    if(!device)return;
    const power=device.controls.find(item=>item.kind==="switch"&&item.id==="main")||device.controls.find(item=>item.kind==="switch");
    const controls=device.controls.filter(item=>item.id.startsWith("main:")&&["level","temperature","color"].includes(item.kind));
    const powerPending=Boolean(power&&commandPending(device.id,power.property));
    $("#lightTitle").textContent=device.name;
    $("#lightPower").classList.toggle("on",Boolean(power?.value));
    $("#lightPower").classList.toggle("pending",powerPending);
    $("#lightPower").disabled=device.availability==="offline"||!power||powerPending;
    $("#lightPower").setAttribute("aria-label",`${device.name} ${powerPending?t("sendingCommand"):power?.value===true?t("off"):t("on")}`);
    $("#lightPower").onclick=()=>power&&command(device.id,power.property,!binaryControlActive(power));
    if(lightControlsBusy())return;
    $("#lightControls").innerHTML=controls.map(control=>{
      if(control.kind==="color")return`<label class="light-control"><span class="light-control-head"><span>${t("color")}</span><output>${esc(control.value)}</output></span><input class="color-picker" type="color" value="${esc(control.value||"#ffffff")}" data-light-color="${esc(device.id)}" data-property="${esc(control.property)}"></label>`;
      const temperature=control.kind==="temperature";
      const known=typeof control.value==="number";
      const value=known?Math.min(Number(control.max),Math.max(Number(control.min),Number(control.value))):Number(control.min);
      const output=known?(temperature?`${temperatureKelvin(value)} K`:`${brightnessPercent({...control,value})}%`):t("unknownState");
      return`<label class="light-control"><span class="light-control-head"><span>${temperature?t("colorTemperature"):t("brightness")}</span><output data-light-output>${output}</output></span><input class="${temperature?"temperature-slider":"brightness-slider"}" type="range" min="${control.min}" max="${control.max}" value="${value}" data-light-range="${esc(device.id)}" data-property="${esc(control.property)}" data-kind="${control.kind}"></label>`;
    }).join("");
    $$("[data-light-range]").forEach(input=>{
      input.oninput=()=>input.previousElementSibling.querySelector("output").textContent=input.dataset.kind==="temperature"?`${temperatureKelvin(input.value)} K`:`${Math.round((Number(input.value)-Number(input.min))/(Number(input.max)-Number(input.min))*100)}%`;
      input.onchange=()=>command(input.dataset.lightRange,input.dataset.property,Number(input.value));
    });
    $$("[data-light-color]").forEach(input=>input.onchange=()=>command(input.dataset.lightColor,input.dataset.property,input.value));
  }
  const detailBodyBusy=()=>{
    if(state.detailPointerDown)return true;
    const active=document.activeElement;
    return Boolean(active&&$("#deviceDetailBody").contains(active)&&active.closest('select,textarea,input:not([type="range"])'));
  };
  function renderDeviceDetail(){
    const device=state.devices.find(item=>item.id===state.detailDevice);
    if(!device){closeDeviceDetail();return}
    const preparing=device.preparing===true;
    const primaryStatus=primaryStatusForDevice(device,preparing);
    $("#deviceDetailIcon").className=`device-status-icon ${primaryStatus.tone}${device.availability==="offline"?" offline":""}`;
    $("#deviceDetailIcon").innerHTML=deviceTypeIcon(device);
    $("#deviceDetailKind").textContent=`${deviceKind(device)} · ${primaryStatus.label}`;
    $("#deviceDetailName").textContent=device.name;
    /* Alt "Kapat" şeridi kaldırıldı: penceredeki tek aksiyondu ve sağ üstteki çarpı zaten aynı işi
       yapıyor. Kurulumu yarım kalan cihazın "Kurulumu bitir" akışı gövdedeki kurtarma şeridinde. */
    // Başlıktaki rozet/kalem gövdeden bağımsız tazelenir, bu yüzden tıklaması burada bağlanır.
    const meta=$("#deviceDetailMeta");
    const metaSignature=`${device.id}|${linkQualityPercent(device)}|${state.language}`;
    if(meta.dataset.signature!==metaSignature){
      meta.dataset.signature=metaSignature;
      meta.innerHTML=deviceDetailMetaHtml(device);
      const rename=meta.querySelector("[data-rename]");
      if(rename)rename.onclick=event=>{event.preventDefault();event.stopPropagation();openRename(rename.dataset.rename)};
    }
    if(detailBodyBusy())return;
    $("#deviceDetailBody").innerHTML=deviceDetailBodyHtml(device);
    const technical=$("#deviceDetailBody .technical-details");
    if(technical)technical.ontoggle=()=>state.detailTechnicalOpen=technical.open;
    bindCards();
  }
  async function toggleDeviceRoom(deviceId,groupId){
    const device=state.devices.find(item=>item.id===deviceId);
    const group=state.groups.find(item=>item.id===groupId);
    if(!device||!group)return;
    const member=group.items.some(item=>item.deviceId===deviceId);
    const control=dashboardControlForDevice(device);
    const controlId=control?control.id:groupDeviceControlId;
    const groups=state.groups.map(item=>item.id!==groupId?item:{
      id:item.id,
      name:item.name,
      items:member?item.items.filter(entry=>entry.deviceId!==deviceId):[...item.items,{deviceId,controlId}]
    });
    await persistHomeGroups(groups);
    if($("#deviceDetailDialog").open)renderDeviceDetail();
  }
  /* Eşleştirmeden gelindiği bilgisi pencereye `detailFromPairing` ile taşınır: yalnız bitirme
     düğmesinin metnini değiştirir, kapanışta sıfırlanır. */
  function openDeviceDetail(id,options={}){
    if(!state.devices.some(item=>item.id===id))return;
    state.detailDevice=id;
    state.detailFromPairing=options.fromPairing===true;
    state.detailPointerDown=false;
    state.lightPanelMode=null;
    renderDeviceDetail();
    const dialog=$("#deviceDetailDialog");
    if(!dialog.open)dialog.showModal();
  }
  function closeDeviceDetail(){
    state.detailDevice=null;
    const dialog=$("#deviceDetailDialog");
    if(dialog.open)dialog.close();
  }
  function bindBackdropClose(selector,contentSelector,close){
    const dialog=$(selector);
    if(!dialog)return;
    const outside=event=>{
      const box=dialog.querySelector(contentSelector);
      if(!box)return false;
      const rect=box.getBoundingClientRect();
      return event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom;
    };
    let startedOutside=false;
    dialog.addEventListener("pointerdown",event=>{startedOutside=event.target===dialog&&outside(event)},{passive:true});
    dialog.addEventListener("click",event=>{
      const armed=startedOutside;
      startedOutside=false;
      if(!armed||event.detail===0)return;
      if(event.target===dialog&&outside(event))close();
    });
    dialog.addEventListener("close",()=>{startedOutside=false});
  }
