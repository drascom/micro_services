  /* TEK KAPI: her `showModal()` sonrası odak pencerenin başlığına taşınır. Tarayıcı, `autofocus`
     yokken diyalogun ilk odaklanabilir öğesine odaklanır — bu çoğu pencerede bir metin alanı ve
     tablette Android klavyesi açılıp ekranın yarısını kapatıyordu. Açan yer sonradan başka bir
     öğeye odaklanmak isterse (düğme, sekme) kendi çağrısı bunun ÜSTÜNE yazar; çağrı sırası
     korunur. Diyalog başına ayrı kural yazılmaz, yeni pencereler de kendiliğinden uyar. */
  const nativeShowModal=HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.showModal=function(...args){
    nativeShowModal.apply(this,args);
    focusModalHeading(this);
  };
  $$(".nav-button").forEach(button=>button.onclick=()=>activateView(button.dataset.view));
  $$("[data-app-menu]").forEach(button=>button.onclick=()=>toggleAppMenu(button));
  // Ana ekrandaki "Otomasyon" gibi kestirme düğmeler: pencere açmaz, doğrudan görünüm değiştirir.
  // `.nav-button` DEĞİL — o sınıf menü ızgarasının biçimini ve `active` işaretini taşıyor.
  // (Alt sayfa başlıklarındaki düğme artık ana ekrana atmıyor, menüyü açıyor — yukarıdaki
  // `[data-app-menu]` kancası hepsini birden bağlıyor; ana ekrana dönüş menüdeki Home satırında.)
  $$("[data-view-link]").forEach(button=>button.onclick=()=>activateView(button.dataset.viewLink));
  $b("#homeHealth").onclick=openHomeHealthAttention;
  $b("#closeHomeAttention").onclick=closeHomeAttentionDialog;
  $b("#homeAttentionList").onclick=event=>{
    const button=event.target.closest?.("[data-home-attention-repair]");
    if(button)repairHomeAttentionDevice(button.dataset.homeAttentionRepair);
  };
  $b("#closeAppMenu").onclick=closeAppMenu;
  $b("#closeRoomDetail").onclick=closeRoomDetail;
  $b("#roomDetailDialog").addEventListener("close",()=>{state.roomDetail=null});
  $b("#closeSystemDetail").onclick=closeSystemDetail;
  $b("#systemDetailDialog").addEventListener("close",restoreSystemDetail);
  $b("#closeHaSetup").onclick=closeHomeAssistantSetup;
  $b("#haSetupDialog").addEventListener("close",renderHomeAssistant);
  $b("#appMenuDialog").addEventListener("close",()=>{
    $$("[data-app-menu]").forEach(item=>item.setAttribute("aria-expanded","false"));
    const opener=state.appMenuOpener;
    state.appMenuOpener=null;
    if(opener&&opener.isConnected&&opener.offsetParent!==null)opener.focus();
  });
  /* ARKA PLANA DOKUNUNCA KAPANIR — AMA YALNIZ ORADA BAŞLAYAN DOKUNUŞTA.
     Tek parmak dokunuşu `pointerdown → pointerup → click` üretir ve menü `click` içinde açılır.
     Yalnız `click` hedefine bakan bir kural, menüyü AÇAN dokunuşun kendisiyle menüyü kapatabilir:
     dokunuş menü yokken başlar, menü açılır, aynı hareketin devamı artık modal katmanın üstüne
     düşer. Sonuç kullanıcıya "menü hiç açılmıyor" gibi görünür — tek kare bile durmaz.
     Bu yüzden kapanma iki koşula bağlı: hareket arka planda BAŞLAMIŞ ve orada BİTMİŞ olmalı. */
  let appMenuBackdropPointer=false;
  $b("#appMenuDialog").addEventListener("pointerdown",event=>{appMenuBackdropPointer=event.target===$("#appMenuDialog")});
  $b("#appMenuDialog").addEventListener("click",event=>{
    const onBackdrop=event.target===$("#appMenuDialog")&&appMenuBackdropPointer;
    appMenuBackdropPointer=false;
    if(onBackdrop)closeAppMenu();
  });
  $$("[data-device-layout]").forEach(button=>button.onclick=()=>setDeviceLayout(button.dataset.deviceLayout));
  window.addEventListener("resize",()=>{if(state.deviceLayout==="grid")applyDeviceColumns(state.deviceColumns)},{passive:true});
  function syncSearchClear(){$("#clearSearch").hidden=$("#search").value.length===0}
  $b("#clearSearch").onclick=()=>{$("#search").value="";syncSearchClear();filterDevices();bindCards();$("#search").focus()};
  $b("#deviceAttention").ontoggle=()=>setAttentionOpen($("#deviceAttention").open);
  $b("#deviceColumns").oninput=()=>applyDeviceColumns($("#deviceColumns").value);
  $b("#deviceColumns").onchange=()=>setDeviceColumns($("#deviceColumns").value);
  $$("[data-theme-mode]").forEach(button=>button.onclick=()=>setThemeMode(button.dataset.themeMode));
  $$("[data-system-jump]").forEach(button=>button.onclick=()=>openSystemDetail(button));
  $$("[data-theme-toggle]").forEach(button=>button.onclick=()=>setThemeMode(document.documentElement.dataset.theme==="dark"?"light":"dark"));
  $$("[data-language-cycle]").forEach(button=>button.onclick=cycleLanguage);
  /* ARKA PLAN AYARLARI. Kaydırıcılar `input` ile ANINDA boyar (canlı önizleme), kayıt
     `updateSkySettings` içinde gecikmeli — sürüklerken kare başına iş yalnız birkaç değişken
     yazımı, yeniden düzen yok. */
  $$("[data-sky-milkyway]").forEach(button=>button.onclick=()=>updateSkySettings({milkyway:button.dataset.skyMilkyway}));
  $b("#skyDensity").oninput=()=>updateSkySettings({density:Number($("#skyDensity").value)/100});
  $b("#skyStarGain").oninput=()=>updateSkySettings({starGain:Number($("#skyStarGain").value)/100});
  $b("#skyMountainOn").onchange=()=>updateSkySettings({mountain:$("#skyMountainOn").checked});
  $b("#skyMountainHeight").oninput=()=>updateSkySettings({mountainHeight:Number($("#skyMountainHeight").value)});
  $b("#skyResetButton").onclick=resetSkySettings;
  $b("#skyModeSwitch").onclick=()=>setThemeMode("sun");
  /* SAAT ÖNİZLEMESİ. Kaydırıcı gökyüzünü sürüklendiği dakikada dondurur, hızlı sıçramalar gerçek
     gün doğumu/batımından hesaplanır, "şimdiye dön" ve rozet önizlemeyi kapatır. Hiçbir yere
     kaydedilmez; arka plan sayfasından çıkınca `activateView` zaten kapatıyor. */
  /* OYNAT/DURAKLAT: aynı 40 sn'lik döngüyü (`?sky=preview` ile ortak) açar/kapatır; duraklatınca
     saat bulunduğu dakikada donar. Kaydırıcıya ya da sıçrama çipine dokunmak akışı bitirir. */
  $b("#skyPreviewPlay").onclick=()=>setSkyPlay(!skyPlay.on);
  $b("#skyPreviewHour").oninput=()=>setSkyScrub(Number($("#skyPreviewHour").value));
  $$("[data-sky-jump]").forEach(button=>button.onclick=()=>setSkyScrub(skyScrubMarks()[button.dataset.skyJump]));
  $b("#skyPreviewNow").onclick=()=>setSkyScrub(null);
  $b("#skyPreviewBadge").onclick=()=>setSkyScrub(null);
  /* AY EVRESİ ÖNİZLEMESİ. Kaydırıcı bir sinodik ayı 0..100 olarak sürer, sıçramalar dördünlere
     oturur, "gerçek evre" yalnız evreyi bırakır (saat donmuşsa donmuş kalır); rozet ve "şimdiye
     dön" ikisini birden kapatır. */
  $b("#skyPreviewPhase").oninput=()=>setSkyPhase(Number($("#skyPreviewPhase").value)/100);
  const skyMoonMarks={new:0,first:.25,full:.5,last:.75};
  $$("[data-sky-moon]").forEach(button=>button.onclick=()=>setSkyPhase(skyMoonMarks[button.dataset.skyMoon]));
  $b("#skyPreviewMoonNow").onclick=()=>setSkyPhase(null);
  /* Önizleme sahnesinin ölçeği çerçevesinin iki kenarından çıkar; düzen yeniden boyutlanınca
     (tablet dönünce, pencere değişince, tek sütuna düşünce) yeniden ölçülmeli. `ResizeObserver`
     varsa hem ızgayı hem çerçeveyi izler — düzenin oturduğu anı yakalayan tek yol o. `resize`
     olayı her hâlde bağlanır: pencere yalnız BOYCA değişirse genişlikler sabit kalır ve
     gözlemci hiç tetiklenmez. */
  const skyStageFrame=$(".sky-stage-frame");
  const skyLayout=$(".sky-layout");
  if(typeof ResizeObserver==="function"){
    const skyStageObserver=new ResizeObserver(measureSkyStage);
    if(skyLayout)skyStageObserver.observe(skyLayout);
    if(skyStageFrame)skyStageObserver.observe(skyStageFrame);
  }
  window.addEventListener("resize",measureSkyStage);
  /* "Güneşe göre" kipi de dinler: gün doğumu/batımı hiç bilinmiyorsa o kip sistem tercihine
     düşüyor, o hâlde sistem değişince yeniden boyanmalı. */
  const handleSystemThemeChange=()=>{if(state.themeMode==="system"||state.themeMode==="sun")applyTheme()};
  if(themeMedia){
    if(typeof themeMedia.addEventListener==="function")themeMedia.addEventListener("change",handleSystemThemeChange);
    else if(typeof themeMedia.addListener==="function")themeMedia.addListener(handleSystemThemeChange);
  }
  $b("#hidePairing").onclick=()=>{if(state.pairingSession)state.pairingSession.hidden=true;$("#pairingDialog").close();render()};
  $b("#showPairing").onclick=()=>{if(!state.pairingSession)return;state.pairingSession.hidden=false;renderPairingProgress();$("#pairingDialog").showModal();render()};
  $b("#cancelPairing").onclick=()=>startPairing(false);
  $b("#pairingDialog").onclose=()=>{if(state.pairingSession&&state.pairing?.open)state.pairingSession.hidden=true;render()};
  $b("#nameForm").onsubmit=event=>{event.preventDefault();saveName()};
  $b("#cancelName").onclick=cancelName;
  $b("#nameDialog").onclose=()=>{const editing=state.editing;state.editing=null;if(editing?.afterPairing)continuePairingFlow(editing.id)};
  $b("#noteForm").onsubmit=saveDeviceNote;
  $b("#cancelNote").onclick=()=>$("#noteDialog").close();
  $b("#noteDialog").onclose=()=>{state.noteEditing=null};
  $b("#deviceOptionsForm").onsubmit=saveDeviceOptions;
  $b("#cancelDeviceOptions").onclick=()=>$("#deviceOptionsDialog").close();
  $b("#deviceOptionsDialog").onclose=()=>{state.optionsDevice=null};
  $b("#imageForm").onsubmit=event=>{event.preventDefault();saveImage()};
  $b("#cancelImage").onclick=()=>$("#imageDialog").close();
  $b("#imageDialog").onclose=()=>{const editing=state.imageEditing;state.imageEditing=null;$("#saveImage").disabled=false;if(editing?.afterPairing)askDeviceRole(editing.id,true)};
  $b("#skipDeviceRole").onclick=()=>$("#deviceRoleDialog").close();
  /* Rolden sonra oda adımı gelir; oda atlanırsa cihaz "Odasız" kartında bekler. */
  $b("#deviceRoleDialog").onclose=()=>{const editing=state.roleEditing;state.roleEditing=null;if(editing?.afterPairing)askDeviceRoom(editing.id,true);else render()};
  $b("#skipDeviceRoom").onclick=()=>$("#deviceRoomDialog").close();
  $b("#deviceRoomForm").onsubmit=createDeviceRoom;
  $b("#closeDeviceLost").onclick=closeDeviceLost;$b("#retryDeviceLost").onclick=retryDeviceLost;$b("#deviceLostDialog").onclose=()=>{state.deviceLost=null};
  $b("#deviceRoomDialog").onclose=()=>{const editing=state.roomEditing;state.roomEditing=null;if(editing?.afterPairing)finishPairingFlow(editing.id);else render()};
  $b("#groupForm").onsubmit=event=>{event.preventDefault();saveDashboardGroup()};
  $b("#groupName").oninput=updateGroupSelection;
  /* Cihaz süzgeci yalnız listeyi yeniden çizer; seçim kümesine dokunmaz. Enter formu
     göndermesin — arama kutusundayken kaydetme beklenmeyen bir sonuç olur. */
  $b("#groupDeviceSearch").oninput=renderGroupDeviceChoices;
  $b("#groupDeviceSearch").onkeydown=event=>{if(event.key==="Enter")event.preventDefault()};
  $b("#cancelGroup").onclick=closeAddDialog;
  $b("#deleteGroup").onclick=requestGroupDelete;$b("#groupMoveLeft").onclick=()=>moveDashboardGroup(-1);$b("#groupMoveRight").onclick=()=>moveDashboardGroup(1);$b("#cancelGroupDelete").onclick=()=>$("#groupDeleteDialog").close();$b("#confirmGroupDelete").onclick=confirmGroupDelete;$b("#groupDeleteDialog").onclose=()=>{state.groupDeleting=null};
  $$("[data-open-group-create]").forEach(button=>button.onclick=()=>openGroupEditor());
  $b("#closeDeviceActions").onclick=()=>$("#deviceActionDialog").close();
  $b("#confirmDeviceAction").onclick=()=>{const pending=state.pendingConfirm;$("#deviceActionDialog").close();if(!pending)return;command(pending.id,pending.property,pending.value)};
  $b("#deviceActionDialog").onclose=()=>{state.pendingConfirm=null};
  $b("#onboardingNext").onclick=nextOnboardingStep;
  $b("#onboardingBack").onclick=previousOnboardingStep;
  $b("#skipOnboarding").onclick=skipOnboarding;
  $b("#onboardingDialog").oncancel=event=>event.preventDefault();
  $b("#modePinForm").onsubmit=submitModePin;
  $b("#modePinForm").addEventListener("input",()=>setModeFormError());
  $b("#modePinCancel").onclick=()=>closeModePin();
  $$("[data-mode-toggle]").forEach(button=>button.onclick=toggleAdminMode);
  /* Mod şeridindeki "Ev moduna dön" — `data-mode-toggle` DEĞİL, çünkü o kanca iki yönlü bir
     anahtar (kapalıyken PIN sorar) ve etiketini `applyAuthUi` yazar. Şerit yalnız yönetici
     modunda çizildiği için burada tek yön geçerli: doğrudan 20-auth.js'in `leaveAdminMode`i. */
  $b("#leaveAdminModeButton").onclick=leaveAdminMode;
  $b("#menuChangePin").onclick=openAdminPinSettings;
  $b("#defaultPinBannerAction").onclick=openAdminPinSettings;
  $b("#reviewZigbeeConnection").onclick=()=>openSystemDetail(systemDetailButton("debugCard"));
  $b("#adminPinForm").onsubmit=updateAdminPin;
  $b("#adminPinForm").addEventListener("input",()=>setAdminPinError());
  $b("#coachNext").onclick=nextCoachStep;
  $b("#coachBack").onclick=previousCoachStep;
  $b("#coachSkip").onclick=finishCoach;
  $b("#agentTokenForm").onsubmit=createAgentToken;
  $b("#copyAgentToken").onclick=copyAgentToken;
  $b("#restartOnboarding").onclick=openOnboarding;
  $b("#restartDashboardTour").onclick=restartDashboardGuide;
  // Tek yedek: ayarlar + odalar + Zigbee ağı tek dosyada, tek düğmede.
  $b("#downloadFullBackup").onclick=downloadFullBackup;
  $b("#chooseFullRestore").onclick=()=>$("#fullRestoreFile").click();
  $b("#fullRestoreFile").onchange=chooseFullRestore;
  $b("#cancelHomeRestore").onclick=closeHomeRestore;
  $b("#confirmHomeRestore").onclick=applyHomeRestore;
  $$("input[name=homeBackupMode]").forEach(radio=>{radio.onchange=previewHomeBackup});
  $b("#addInstallCode").onclick=addInstallCode;
  $b("#scanNetworkMap").onclick=scanNetworkMap;
  $b("#createZigbeeGroup").onclick=createZigbeeGroup;
  $b("#bindDevices").onclick=()=>bindZigbeeDevices(true);
  $b("#unbindDevices").onclick=()=>bindZigbeeDevices(false);
  $b("#bindSource").onchange=renderBindingEndpoints;
  $b("#bindTarget").onchange=renderBindingEndpoints;
  // "Yeni otomasyon" doğrudan modalı açar; yol seçimi de modalin ilk adımıdır.
  $b("#newAutomation").onclick=()=>openAutomationWizard();
  $b("#simpleLinkBack").onclick=stepBackSimpleLink;
  $b("#simpleLinkSave").onclick=saveSimpleLink;
  $b("#simpleLinkDialog").addEventListener("close",()=>{state.simpleLink=null});
  bindBackdropClose("#simpleLinkDialog",".simple-link-modal",()=>$("#simpleLinkDialog").close());
  $b("#automationBack").onclick=stepBackAutomation;
  $b("#automationNext").onclick=nextAutomationStep;
  $b("#closeAutomationWizard").onclick=closeAutomationWizard;
  $b("#automationDialog").addEventListener("close",()=>{
    cancelAutomationAdvance();
    if(automationWizardReauthorizing){automationWizardReauthorizing=false;return}
    state.automationWizard=null;
  });
  bindBackdropClose("#automationDialog",".automation-modal",closeAutomationWizard);
  $b("#closeAutomationActions").onclick=()=>$("#automationActionDialog").close();
  $b("#runAutomationNow").onclick=runAutomationNow;
  $b("#editAutomation").onclick=()=>{const id=state.automationContext;$("#automationActionDialog").close();if(id)openAutomationWizard(id)};
  $b("#duplicateAutomation").onclick=duplicateAutomation;
  $b("#deleteAutomation").onclick=deleteAutomation;
  $b("#revertAgentAutomations").onclick=revertAgentAutomations;
  $b("#automationActionDialog").onclose=()=>{state.automationContext=null};
  window.addEventListener("resize",()=>{if(state.coach)positionCoach();updateWidgetScrollHint()});
  $$(".add-device").forEach(button=>button.onclick=()=>startPairing(true));$b("#stopPairing").onclick=()=>startPairing(false);$b("#refreshButton").onclick=refresh;$b("#search").oninput=()=>{syncSearchClear();filterDevices();bindCards()};$b("#removeConfirmation").oninput=()=>{const disabled=!state.removing||!validRemovalConfirmation($("#removeConfirmation").value,state.removing.name);$("#confirmRemove").disabled=disabled;$("#forceRemove").disabled=disabled};$b("#cancelRemove").onclick=()=>{$("#removeDialog").close();state.removing=null};$b("#confirmRemove").onclick=()=>confirmDeviceRemoval(false);$b("#forceRemove").onclick=()=>confirmDeviceRemoval(true);$b("#openMatter").onclick=()=>loadMatter(true);$b("#closeMatter").onclick=closeMatterDialog;$b("#closeLight").onclick=()=>$("#lightDialog").close();$b("#lightDialog").addEventListener("close",()=>{state.lightPointerDown=false});$b("#lightControls").addEventListener("pointerdown",()=>{state.lightPointerDown=true},{passive:true});["pointerup","pointercancel"].forEach(type=>$b("#lightControls").addEventListener(type,()=>{state.lightPointerDown=false},{passive:true}));$b("#closeDeviceDetail").onclick=closeDeviceDetail;$b("#deviceDetailDialog").addEventListener("close",()=>{state.detailDevice=null;state.detailFromPairing=false;state.detailPointerDown=false});$b("#deviceDetailBody").addEventListener("pointerdown",()=>{state.detailPointerDown=true},{passive:true});["pointerup","pointercancel"].forEach(type=>$b("#deviceDetailBody").addEventListener(type,()=>{state.detailPointerDown=false},{passive:true}));$b("#showLightDevice").onclick=()=>{const id=state.lightDevice;$("#lightDialog").close();if(id)showDevice(id)};$b("#saveNetworkSettings").onclick=saveNetworkSettings;$b("#saveBatteryThreshold").onclick=saveBatteryThreshold;$$("[data-settings-tab]").forEach(button=>button.onclick=()=>activateSettingsTab(button.dataset.settingsTab));activateSettingsTab(savedSettingsTab);$b("#homeLocationManualForm").onsubmit=saveHomeLocationForm;$b("#chooseHomeLocation").onclick=openHomeLocationManager;$b("#closeHomeLocationDialog").onclick=()=>$("#homeLocationDialog").close();$b("#homeLocationSearch").oninput=()=>scheduleLocationSearch("home");$b("#useWeatherLocationForHome").onclick=useWeatherLocationForHome;$b("#toggleHaSetup").onclick=toggleHomeAssistantSetup;$b("#toggleHaDiscovery").onclick=toggleHomeAssistantDiscovery;$b("#toggleHaPassword").onclick=()=>{state.mqttPasswordVisible=!state.mqttPasswordVisible;renderHomeAssistant()};$b("#toggleDebug").onclick=toggleDebugMode;$b("#refreshDebugErrors").onclick=loadDebugErrors;$b("#clearDebugErrors").onclick=clearDebugErrors;$b("#refreshDebugNetworkEvents").onclick=loadDebugNetworkEvents;$b("#openWifiSettings").onclick=openWifiSettings;$b("#requestBatteryExemption").onclick=requestBatteryExemption;$b("#grantSystemBrightness").onclick=requestSystemBrightnessPermission;$b("#menuBrightness").onchange=()=>setMenuBrightness($("#menuBrightness").value);$b("#screenIdleSeconds").onchange=()=>updateScreenPreference({idleSeconds:Number($("#screenIdleSeconds").value)});$b("#screenDimEnabled").onchange=()=>updateScreenPreference({dimEnabled:$("#screenDimEnabled").checked});$b("#screenDimBrightness").onchange=()=>updateScreenPreference({dimBrightness:Number($("#screenDimBrightness").value)});$b("#screenNightEnabled").onchange=()=>updateScreenPreference({nightEnabled:$("#screenNightEnabled").checked});$b("#screenNightStart").onchange=()=>updateScreenPreference({nightStart:$("#screenNightStart").value});$b("#screenNightEnd").onchange=()=>updateScreenPreference({nightEnd:$("#screenNightEnd").value});$b("#screenNightBrightness").onchange=()=>updateScreenPreference({nightBrightness:Number($("#screenNightBrightness").value)});
  /* Sürüklerken yalnız rakam güncellenir; ilke parmak kalkınca (`change`) gönderilir — her
     karede köprüye gitmenin anlamı yok. */
  [["#menuBrightness","#menuBrightnessValue"],["#screenDimBrightness","#screenDimBrightnessValue"],["#screenNightBrightness","#screenNightBrightnessValue"]].forEach(([slider,output])=>{
    $b(slider).oninput=()=>{$(output).textContent=`${$(slider).value}%`};
  });
  $b("#restartCoordinator").onclick=restartCoordinator;$b("#restartService").onclick=restartService;$b("#stopRuntime").onclick=stopAndroidRuntime;$b("#cancelRuntimeStop").onclick=()=>$("#runtimeStopDialog").close();$b("#confirmRuntimeStop").onclick=confirmAndroidRuntimeStop;$b("#addWidget").onclick=openWidgetCatalog;$b("#editDashboard").onclick=()=>setDashboardEditing(!state.dashboardEditing);$b("#dismissWidgetDialog").onclick=closeAddDialog;$b("#hubClockZone").onclick=openClockManager;$b("#hubWeatherZone").onclick=openWeatherDialog;$b("#closeClockDialog").onclick=()=>$("#clockDialog").close();$b("#changeWeatherLocation").onclick=()=>{$("#weatherDialog").close();openWeatherLocationManager()};$b("#closeWeatherLocationDialog").onclick=()=>$("#weatherLocationDialog").close();$b("#clockCitySearch").oninput=()=>scheduleLocationSearch("clock");$b("#weatherLocationSearch").oninput=()=>scheduleLocationSearch("weather");$b("#widgetScrollLeft").onclick=()=>scrollWidgetRail(-1);$b("#widgetScrollHint").onclick=scrollWidgetRailForward;$b("#widgetBoard").addEventListener("pointerdown",touchDashboardEditing,{passive:true});$b("#widgetRail").addEventListener("scroll",()=>requestAnimationFrame(updateWidgetScrollHint),{passive:true});$$("[data-open-widget-catalog]").forEach(button=>button.onclick=openWidgetCatalog);$$("[data-add-tab]").forEach(button=>button.onclick=()=>setAddDialogTab(button.dataset.addTab));$b(".modal-tabs").addEventListener("keydown",event=>{const steps={ArrowRight:1,ArrowLeft:-1,ArrowDown:1,ArrowUp:-1};if(!(event.key in steps))return;event.preventDefault();focusAddDialogTab(steps[event.key])});$b("#widgetDialog").addEventListener("close",resetAddDialog);
  async function closeMatterDialog(){
    try{await api("/api/matter/commissioning",{method:"POST",body:JSON.stringify({open:false})})}catch{}
    $("#matterDialog").close();
  }
  $b("#repairDialog").addEventListener("cancel",event=>event.preventDefault());
  bindBackdropClose("#widgetDialog",".add-modal",closeAddDialog);
  bindBackdropClose("#homeAttentionDialog",".home-attention-modal",closeHomeAttentionDialog);
  bindBackdropClose("#roomDetailDialog",".room-detail-modal",closeRoomDetail);
  bindBackdropClose("#systemDetailDialog",".system-detail-modal",closeSystemDetail);
  bindBackdropClose("#haSetupDialog",".room-detail-modal",closeHomeAssistantSetup);
  bindBackdropClose("#deviceDetailDialog",".device-detail-modal",closeDeviceDetail);
  bindBackdropClose("#lightDialog",".light-modal",()=>$("#lightDialog").close());
  /* Hızlı kumanda penceresi: kapatma çarpısı, `Esc` ve odak tuzağı `<dialog>`ın kendisinden gelir;
     açılışta odak başlığa verilir, hiçbir metin alanına gitmez. "Ayrıntılar" yeni bir ekran açmaz —
     pencereyi kapatıp bugün zaten var olan cihaz detay sayfasını açar. */
  $b("#closeQuickControl").onclick=closeQuickControl;
  $b("#quickControlDialog").addEventListener("close",()=>{state.quickControl=null;state.quickPointerDown=false});
  $b("#quickControlBody").addEventListener("pointerdown",()=>{state.quickPointerDown=true},{passive:true});
  ["pointerup","pointercancel"].forEach(type=>$b("#quickControlBody").addEventListener(type,()=>{state.quickPointerDown=false},{passive:true}));
  $b("#quickControlDetails").onclick=()=>{const id=state.quickControl?.id;closeQuickControl();if(id)openDeviceDetail(id)};
  bindBackdropClose("#quickControlDialog",".quick-modal",closeQuickControl);
  bindBackdropClose("#matterDialog",".modal",closeMatterDialog);
  bindBackdropClose("#weatherDialog",".modal",()=>$("#weatherDialog").close());
  /* Sağ üst çarpılar: kapatmayı kestirmeden `close()` ile yapmaz, modalin KENDİ kapatma
     düğmesini tetikler (form sıfırlama, durum temizleme, akışın bir sonraki adımı aynı yerden
     geçsin). Kendi düğmesi olmayan modalde pencereyi doğrudan kapatır. Kimliği devraldığı için
     zaten bağlı olan çarpılar (ör. `#closeMatter`) bu listeye girmez. */
  $$("[data-dismiss-modal]").forEach(button=>button.onclick=()=>{
    const owner=button.dataset.dismissModal?$(`#${button.dataset.dismissModal}`):null;
    if(owner)owner.click();
    else button.closest("dialog")?.close();
  });
  $b("#matterDialog").addEventListener("close",stopMatterWatch);
  /* Alarm: pencerede ayrı bir "Kaydet" yok, her alan değişince yazılır (saat penceresinin geri
     kalanı da böyle çalışıyor). Çalma katmanı `Esc` ya da düğmeyle kapanır; iki yol da aynı
     `close` olayından geçtiği için ses her hâlükârda susar. */
  $b("#alarmEnabled").onchange=saveAlarmSetting;
  /* Saat seçimi panelin kendi pop-up'ından gelir (yerel saat penceresi yok). Yazma yalnız
     "Kaydet"te olur; kapatmanın üç yolu da (çarpı, "Vazgeç", Esc/dışarı tıklama) eski değeri
     bırakır. Dinleyiciler tekerleğin kendisinde: satırlar yeniden çizildiğinde bağ kopmaz. */
  $b("#alarmTimeButton").onclick=openAlarmTimePicker;
  $b("#closeAlarmTimeDialog").onclick=()=>$("#alarmTimeDialog").close();
  $b("#cancelAlarmTime").onclick=()=>$("#alarmTimeDialog").close();
  $b("#confirmAlarmTime").onclick=applyAlarmTimePicker;
  bindBackdropClose("#alarmTimeDialog",".time-picker-card",()=>$("#alarmTimeDialog").close());
  ["#alarmHourWheel","#alarmMinuteWheel"].forEach(selector=>{
    const wheel=$b(selector);
    // Kaydırma olayı saniyede onlarca kez gelir; boyama kare başına bir kez yapılır.
    let painting=false;
    wheel.addEventListener("scroll",()=>{
      if(painting)return;
      painting=true;
      requestAnimationFrame(()=>{painting=false;alarmWheelPaint(wheel)});
    },{passive:true});
    wheel.addEventListener("keydown",event=>{if(alarmWheelKey(wheel,event.key))event.preventDefault()});
    wheel.addEventListener("click",event=>{
      const option=event.target.closest(".time-wheel-option");
      if(option)alarmWheelScrollTo(wheel,Array.prototype.indexOf.call(wheel.children,option),true);
    });
  });
  $b("#alarmRepeat").onchange=saveAlarmSetting;
  $b("#alarmWeekday").onchange=saveAlarmSetting;
  $b("#stopAlarm").onclick=stopAlarmRing;
  $b("#alarmDialog").addEventListener("close",stopAlarmRing);
  $b("#themePackageSelect").onchange=async event=>{await selectThemePackage(event.target.value);renderAppearanceSettings()};
  $b("#saveThemeOverrides").onclick=persistAppearancePalette;
  $b("#resetThemeOverrides").onclick=resetAppearancePalette;
  /* Otomatik oynatma kısıtı: ses bağlamı ilk kullanıcı dokunuşunda kurulur ve canlı tutulur.
     Dinleyici kalıcıdır — bağlam tarayıcı tarafından askıya alınırsa (uyku, sekme değişimi)
     bir sonraki dokunuş onu geri getirir. */
  ["pointerdown","keydown","touchstart"].forEach(type=>document.addEventListener(type,unlockAlarmAudio,{capture:true,passive:true}));
  async function startApplication(){
    if(applicationStarted){
      const reload=[refresh(),loadHomeGroups(),loadHomeVisibility(),loadHomeFavorites(),loadAutomations(),loadHomeLocation(),loadWeather(),loadWorldClockZones(),initializeThemeRuntime()];
      if(state.auth.elevated)reload.push(loadSettings());
      await Promise.allSettled(reload);
      await ensureQuickSceneExamples();
      renderHomeScenes();
      renderRoutines();
      await migrateLocalGroups();
      await migrateWeatherLocation();
      await migrateWorldClockZones();
      return;
    }
    applicationStarted=true;
    setupPullToRefresh();configureAndroidActions();bindScreensaver();bindWidgetControls();applyWidgetLayout();
    const startup=[refresh(),loadHomeGroups(),loadHomeVisibility(),loadHomeFavorites(),loadAutomations(),loadHomeLocation(),loadWeather(),loadWorldClockZones(),loadInstallationOnboarding(),initializeThemeRuntime()];
    if(state.auth.elevated)startup.push(loadSettings());
    await Promise.allSettled(startup);
    await ensureQuickSceneExamples();
    // Sahne şeridi ve Rutinler listesi otomasyonlar okunduktan SONRA doğar: ilk kare boş kalmasın.
    renderHomeScenes();
    renderRoutines();
    await migrateLocalGroups();
    // Sunucuda konum yoksa cihazda kalmış eski seçim bir kez yukarı taşınır; hava okunduktan
    // SONRA çalışır, yoksa sunucunun kendi konumunu ezerdi.
    await migrateWeatherLocation();
    // Aynı kural dünya saati şehirleri için: sunucuda liste yoksa cihazdaki bir kez yukarı taşınır.
    await migrateWorldClockZones();
    /* `state.setupPending`: sunucu kurulumu yarım. Tarayıcıda "tamamlandı" işareti olsa bile
       sihirbaz açılır — yarım kurulumu atlatan tek şey olmasın. */
    if(!onboardingComplete()||state.setupPending===true)openOnboarding();
    else requestAnimationFrame(maybeStartDashboardTour);
    setInterval(()=>{if(!document.hidden)refresh()},8000);
    scheduleWorldClockTick();
    // Hava sunucudan okunuyor: sormak ucuz, kör 30 dakikalık bekleme kalktı.
    setInterval(()=>{if(!document.hidden)refreshWeatherIfNeeded()},300000);
    setInterval(()=>{if(state.pairing?.open)render()},1000);
    // Ekran yeniden görünür olduğunda hava HEMEN tazelenir — uykudan dönen tablet eski değeri
    // göstermesin.
    document.addEventListener("visibilitychange",()=>{if(document.hidden)return;refresh();loadWeather()});
  }
  async function initialize(){
    // Arka plan ayarları temadan ÖNCE okunur: `applyTheme` güneş takibini kurabiliyor ve o da
    // ilk karede `--star-a`'yı kullanıcının çarpanıyla yazsın.
    loadSkySettings();
    applySkySettings();
    applyTheme();
    document.body.dataset.activeView="home";
    // Kökteki ikiz: güneş katmanı `html::before` üstünde duruyor, o da ekran işaretini görsün.
    document.documentElement.dataset.activeView="home";
    try{await loadLanguages()}
    catch(error){showToast(error.message,true)}
    if(Object.keys(translations).length)applyLanguage();
    /* Bağlama zinciri eksik düğüm gördüyse sessiz kalmayız: panel çalışmaya devam eder ama
       kullanıcı neden bazı düğmelerin ölü olduğunu bilsin ve sert yenilesin. Konsolda seçici
       listesi zaten var (`$b`). */
    if(missingBindTargets.size)showToast(t("stalePageReload"),true);
    configureAndroidActions();
    /* Giris ekrani yok: mod durumu okunur (ayni istek ev oturumu cerezini de kurar) ve panel
       dogrudan ev modunda acilir. Durum okunamasa bile panel acilir — kullanici kapida kalmaz,
       yonetici ekranlarini zaten sunucu koruyor. */
    try{await loadModeState()}
    catch(error){showToast(error.message,true);applyAuthUi()}
    await startApplication();
  }
  initialize();
