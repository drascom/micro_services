  /* KOORDİNATÖR ADRESİ TESTİ
     Sunucu koordinatöre tek bayt yazmaz: TCP'de bağlan-kapat, seri yolda yalnız dosya bakışı.
     Bu yüzden başarı metni "doğrulandı" DEMEZ, "adrese ulaşıldı" der — SLZB gibi köprüler
     birden çok istemcinin bağlantısını kabul ettiği için ulaşmak ne doğru koordinatör olduğunu
     ne de sahiplenilebileceğini kanıtlar. Caveat metni (`adapterTestNotVerified`) ayrı durur. */
  const coordinatorProbeText=status=>({
    "active":{key:"adapterTestActive",tone:"ok"},
    "active-degraded":{key:"adapterTestActiveDegraded",tone:"bad"},
    "reachable":{key:"adapterTestReached",tone:"ok",caveat:true},
    "refused":{key:"adapterTestRefused",tone:"bad"},
    "timeout":{key:"adapterTestTimeout",tone:"bad"},
    "dns-failed":{key:"adapterTestDnsFailed",tone:"bad"},
    "serial-present":{key:"adapterTestSerialFound",tone:"ok",caveat:true},
    "serial-missing":{key:"adapterTestSerialMissing",tone:"bad"},
    "serial-not-a-device":{key:"adapterTestSerialNotDevice",tone:"bad"},
    "serial-no-access":{key:"adapterTestSerialNoAccess",tone:"bad"}
  }[status]||{key:"adapterTestUnreachable",tone:"bad"});
  function setAdapterTestResult(node,message,tone,caveat){
    if(!node)return;
    node.dataset.tone=tone;
    node.innerHTML=`<span>${esc(message)}</span>${caveat?`<span class="adapter-test-caveat">${esc(t("adapterTestNotVerified"))}</span>`:""}`;
  }
  function clearAdapterTestResult(node){
    if(!node)return;
    node.dataset.tone="";
    node.textContent="";
  }
  /* Tek yoklama noktası: ayarlar sayfası da kurulum sihirbazı da buradan geçer. Dönen değer
     "adrese ulaşıldı mı" — kurulum kapısı bu değere bakar. */
  async function probeZigbeeAdapter(input,output,button){
    const address=(input?.value||"").trim();
    if(!address){setAdapterTestResult(output,t("adapterTestEmpty"),"bad",false);return false}
    setAdapterTestResult(output,t("adapterTesting"),"busy",false);
    if(button)button.disabled=true;
    try{
      const data=await api("/api/settings/zigbee-adapter/test",{method:"POST",body:JSON.stringify({address})});
      /* Sunucunun kanonik biçimi alana geri yazılır (çıplak sunucu adı → `tcp://sunucu:6638`),
         böylece kaydedilen değer test edilen değerin aynısı olur. */
      if(data.result?.address)input.value=data.result.address;
      const info=coordinatorProbeText(data.result?.status);
      setAdapterTestResult(output,t(info.key,{address:data.result?.address||address}),info.tone,info.caveat===true);
      return info.tone==="ok";
    }catch(error){
      setAdapterTestResult(output,error.message,"bad",false);
      return false;
    }finally{
      if(button)button.disabled=false;
    }
  }
  /* AYARLARIN İKİ SEKMESİ. Rol ayrımı DEĞİL — `#settings` bölümünün tamamı `data-admin-only`,
     iki sekmeyi de yalnız yönetici görür. Sekme yalnız kalabalığı böler: günlük dokunulan
     ayarlar bir yanda, kurulumda bir kez dokunulanlar öbür yanda. Seçim cihazda kalır. */
  let settingsTab=savedSettingsTab;
  function activateSettingsTab(tab){
    settingsTab=tab==="setup"?"setup":"usage";
    try{localStorage.setItem(settingsTabStorageKey,settingsTab)}catch{}
    $$("[data-settings-tab]").forEach(button=>{
      const selected=button.dataset.settingsTab===settingsTab;
      button.setAttribute("aria-selected",String(selected));
      button.tabIndex=selected?0:-1;
    });
    const usage=$("#settingsPanelUsage");
    const setup=$("#settingsPanelSetup");
    if(usage)usage.hidden=settingsTab!=="usage";
    if(setup)setup.hidden=settingsTab!=="setup";
  }
  /* SİSTEM AYRINTI ÇEKMECESİ. Ana Sistem görünümü yalnız anlaşılır özet kartlarını taşır.
     Mevcut form düğümleri kopyalanmaz: ilgili kartlar sağ çekmeceye taşınır, kapanışta tam
     yerlerine geri konur. Böylece bağlı olay dinleyicileri ve form kimlikleri değişmeden kalır. */
  let systemDetailEntries=[];
  function restoreSystemDetail(){
    for(const entry of [...systemDetailEntries].reverse()){
      if(entry.next?.parentNode===entry.parent)entry.parent.insertBefore(entry.node,entry.next);
      else entry.parent.appendChild(entry.node);
    }
    systemDetailEntries=[];
    const body=$("#systemDetailBody");
    if(body)body.replaceChildren();
  }
  function closeSystemDetail(){
    const dialog=$("#systemDetailDialog");
    if(dialog?.open)dialog.close();
  }
  function systemDetailButton(targetId){
    return $$('[data-system-jump]').find(button=>button.dataset.systemJump===targetId)||null;
  }
  function openSystemDetail(button,{focusId=""}={}){
    if(!button)return;
    const dialog=$("#systemDetailDialog");
    const body=$("#systemDetailBody");
    if(!dialog||!body||dialog.open)return;
    const ids=(button.dataset.systemTargets||button.dataset.systemJump||"").split(",").map(id=>id.trim()).filter(Boolean);
    const nodes=ids.map(id=>document.getElementById(id)).filter(Boolean);
    if(!nodes.length)return;
    body.replaceChildren();
    systemDetailEntries=nodes.map(node=>({node,parent:node.parentNode,next:node.nextSibling}));
    for(const node of nodes)body.appendChild(node);
    $("#systemDetailIcon").textContent=button.querySelector(".system-hub-glyph")?.textContent?.trim()||"•";
    $("#systemDetailTitle").textContent=button.querySelector("strong")?.textContent?.trim()||t("navSystem");
    $("#systemDetailLead").textContent=button.querySelector("small")?.textContent?.trim()||"";
    if(!nodes.some(node=>!node.hidden)){
      const empty=document.createElement("p");
      empty.className="system-detail-empty";
      empty.textContent=t("systemDetailUnavailable");
      body.appendChild(empty);
    }
    dialog.showModal();
    if(focusId)requestAnimationFrame(()=>document.getElementById(focusId)?.focus({preventScroll:true}));
  }
  /* SİSTEM YENİDEN BAŞLATMA
     Koordinatör düğmesi ancak sunucu "yapılabilir" derse etkinleşir: adres bir USB seri yolsa
     ya da kurulum yarımsa uzaktan telsiz reseti diye bir şey yoktur. Karar istemcide TAHMİN
     EDİLMEZ — `/api/settings` yanıtındaki `zigbee.coordinatorRestart` bayrağından okunur; adres
     panele hiç gelmez. Servis düğmesi her zaman açıktır, çünkü ona her kipte basılabilir. */
  function renderSystemRestart(){
    const button=$("#restartCoordinator");
    if(!button)return;
    const capability=state.zigbeeCapabilities?.coordinatorRestart;
    const supported=capability?.supported===true;
    button.disabled=!supported;
    const note=$("#coordinatorRestartUnavailable");
    if(!note)return;
    note.hidden=supported;
    if(!supported)note.textContent=t(capability?.reason==="serial"?"coordinatorRestartSerial":"coordinatorRestartUnset");
  }
  function setSystemRestartStatus(message){
    const node=$("#systemRestartStatus");
    if(!node)return;
    node.textContent=message||"";
    node.hidden=!message;
  }
  async function restartCoordinator(){
    if(!confirm(t("coordinatorRestartConfirm")))return;
    const button=$("#restartCoordinator");
    button.disabled=true;
    setSystemRestartStatus(t("coordinatorRestartRunning"));
    try{
      const data=await api("/api/system/coordinator-restart",{method:"POST"});
      const seconds=Number(data?.estimatedSeconds)>0?Math.ceil(Number(data.estimatedSeconds)):30;
      setSystemRestartStatus(t("coordinatorRestartDone",{seconds}));
      showToast(t("coordinatorRestartStarted"));
    }catch(error){
      setSystemRestartStatus("");
      showToast(error.message,true);
    }finally{renderSystemRestart()}
  }
  /* Geri sayım yalnız bilgi verir; paneli geri getiren şey `waitForRestart` yoklamasıdır
     (önce çevrimdışı görülür, sonra sağlık ucu dönünce sayfa yeniden yüklenir). */
  function startServiceRestartCountdown(seconds){
    clearInterval(startServiceRestartCountdown.timer);
    let left=Math.max(1,Math.ceil(seconds));
    const tick=()=>{
      setSystemRestartStatus(t("serviceRestartCountdown",{seconds:left}));
      left-=1;
      if(left<0)clearInterval(startServiceRestartCountdown.timer);
    };
    tick();
    startServiceRestartCountdown.timer=setInterval(tick,1000);
  }
  async function restartService(){
    if(!confirm(t("serviceRestartConfirm")))return;
    const button=$("#restartService");
    button.disabled=true;
    try{
      const data=await api("/api/system/restart",{method:"POST",body:JSON.stringify({confirmation:"RESTART"})});
      const seconds=Number(data?.estimatedSeconds)>0?Number(data.estimatedSeconds):25;
      showToast(t("serviceRestartStarted"));
      startServiceRestartCountdown(seconds);
      waitForRestart();
    }catch(error){
      button.disabled=false;
      setSystemRestartStatus("");
      showToast(error.message,true);
    }
  }
  async function loadSettings(){
    try{
      const data=await api("/api/settings");
      state.settings=data.settings;
      state.network=data.network||null;
      state.mqttAccess=data.mqttAccess||null;
      state.zigbeeCapabilities=data.zigbee||null;
      const connectionCoordinator=$("#connectionsZigbeeCoordinator");
      if(connectionCoordinator)connectionCoordinator.textContent=state.settings.zigbee.adapterUrl;
      const connectionChannel=$("#connectionsZigbeeChannel");
      if(connectionChannel)connectionChannel.textContent=String(state.settings.zigbee.channel);
      /* Kurulum yarımsa sihirbaz zorunludur: sunucu koordinatöre hiç bağlanmamıştır. */
      state.setupPending=data.setupPending===true;
      renderConnectedServerAddress();
      renderSystemRestart();
      $("#zigbeeChannel").value=String(state.settings.zigbee.channel);
      $("#lowBatteryThreshold").value=String(state.settings.alerts?.lowBatteryThreshold??15);
      $("#selfHealingEnabled").value=state.settings.selfHealing?.enabled===false?"false":"true";
      renderHomeAssistant();
      renderDebugSettings();
      if(state.settings.debug?.enabled===true)await loadDebugErrors();
      await loadDebugNetworkEvents();
      await loadAgentTokens();
    }catch(error){showToast(t("settingsUnavailable"),true)}
  }
  const themeHex=value=>{
    const text=String(value||"").trim();
    if(/^#[0-9a-f]{6}$/i.test(text))return text;
    const match=text.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
    return match?`#${[match[1],match[2],match[3]].map(part=>Math.max(0,Math.min(255,Number(part))).toString(16).padStart(2,"0")).join("")}`:"#000000";
  };
  function renderAppearanceSettings(){
    const select=$("#themePackageSelect");
    if(!select||!themeRuntime.ready)return;
    const active=activeThemePackage();
    select.innerHTML=themeRuntime.packages.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("");
    select.value=active?.id||"";
    const overrides=themeRuntime.appearance?.overrides?.[active?.id]||{};
    $$('[data-theme-override]').forEach(input=>{
      const parts=input.dataset.themeOverride.split(".");
      let value;
      if(parts[0]==="solar")value=overrides.solar?.[parts[1]]?.[parts[2]]??active?.palettes.solar.anchors[parts[1]].colors[parts[2]];
      else value=overrides[parts[0]]?.[parts[1]]??active?.palettes[parts[0]].colors[parts[1]];
      input.value=themeHex(value);
    });
  }
  /* Gökyüzü renk kutuları Ayarlar'dan kaldırıldı (o iş "Arka plan" ekranının). Kayıtlı solar
     üstyazımlar YİNE DE olduğu gibi taşınır — burada gösterilmeyen bir değer, buradan yapılan
     bir kaydetmeyle silinmemeli. */
  async function persistAppearancePalette(){
    const theme=activeThemePackage();
    if(!theme)return;
    const stored=themeRuntime.appearance?.overrides?.[theme.id]||{};
    const item={light:{},dark:{},...(stored.solar?{solar:stored.solar}:{})};
    $$('[data-theme-override]').forEach(input=>{
      const parts=input.dataset.themeOverride.split(".");
      if(parts[0]==="solar")return;
      item[parts[0]][parts[1]]=input.value;
    });
    try{await saveThemeOverrides({[theme.id]:item});showToast(t("themeColorsSaved"));renderAppearanceSettings()}
    catch(error){showToast(error.message,true)}
  }
  async function resetAppearancePalette(){
    const theme=activeThemePackage();
    if(!theme||!themeRuntime.appearance)return;
    const overrides={...themeRuntime.appearance.overrides};delete overrides[theme.id];
    try{
      const response=await api("/api/appearance",{method:"PUT",body:JSON.stringify({...themeRuntime.appearance,overrides})});
      themeRuntime.appearance=response.appearance;applyThemePackage();renderAppearanceSettings();showToast(t("themeColorsReset"));
    }catch(error){showToast(error.message,true)}
  }
  /* Ajan token'ları: kullanıcının kendi yazdığı LLM istemcisi `/mcp` ucuna bu token'la bağlanır.
     Ham değer yalnız üretim yanıtında bir kez gelir; listede yalnız ad ve zaman damgaları durur.
     Kart `data-admin-only` taşır, ev kullanıcısı hiç görmez. */
  function renderAgentTokens(){
    const tokens=state.agentTokens||[];
    $("#agentTokenList").innerHTML=tokens.length?tokens.map(token=>`<article class="agent-token-row"><div><strong>${esc(token.name)}</strong><small>${t("agentTokenCreatedAt",{at:ago(token.createdAt)})} · ${token.lastUsedAt?t("agentTokenLastUsed",{at:ago(token.lastUsedAt)}):t("agentTokenNeverUsed")}</small></div><button class="quiet" type="button" data-revoke-agent-token="${esc(token.id)}">${t("agentTokenRevoke")}</button></article>`).join(""):`<div class="agent-token-empty">${t("agentTokenEmpty")}</div>`;
    $$("[data-revoke-agent-token]").forEach(button=>button.onclick=()=>revokeAgentToken(button.dataset.revokeAgentToken));
  }
  async function loadAgentTokens(){
    try{
      const data=await api("/api/agent-tokens");
      state.agentTokens=Array.isArray(data.tokens)?data.tokens:[];
    }catch(error){state.agentTokens=[]}
    renderAgentTokens();
  }
  async function createAgentToken(event){
    event.preventDefault();
    const input=$("#agentTokenName");
    if(!input.reportValidity())return;
    const button=$("#agentTokenForm button");
    button.disabled=true;
    try{
      const data=await api("/api/agent-tokens",{method:"POST",body:JSON.stringify({name:input.value.trim()})});
      input.value="";
      $("#agentTokenValue").textContent=data.token;
      $("#agentTokenReveal").hidden=false;
      await loadAgentTokens();
    }catch(error){showToast(error.message,true)}
    finally{button.disabled=false}
  }
  async function copyAgentToken(){
    const value=$("#agentTokenValue").textContent||"";
    if(!value)return;
    try{
      /* Android WebView'de pano API'si her zaman yok; eski seçim yoluna düşülür. */
      if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(value);
      else{
        const field=document.createElement("textarea");
        field.value=value;
        field.setAttribute("readonly","");
        field.style.position="fixed";
        field.style.opacity="0";
        document.body.appendChild(field);
        field.select();
        document.execCommand("copy");
        field.remove();
      }
      showToast(t("agentTokenCopied"));
    }catch(error){showToast(t("agentTokenCopyFailed"),true)}
  }
  async function revokeAgentToken(id){
    const token=(state.agentTokens||[]).find(item=>item.id===id);
    if(!token||!confirm(t("agentTokenRevokeConfirm",{name:token.name})))return;
    try{
      await api(`/api/agent-tokens/${encodeURIComponent(id)}`,{method:"DELETE"});
      $("#agentTokenReveal").hidden=true;
      $("#agentTokenValue").textContent="";
      showToast(t("agentTokenRevoked"));
      await loadAgentTokens();
    }catch(error){showToast(error.message,true)}
  }
  const onboardingStepCount=6;
  const onboardingIcon=kind=>{
    const paths={
      welcome:'<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v10h13V10M9 20v-6h6v6"/>',
      hub:'<rect x="5" y="3" width="14" height="18" rx="3"/><path d="M9 8h6M9 12h6M9 16h2"/>',
      zigbee:'<path d="M6 17c5-1 8-4 9-10 3 4 3 9-1 12-3 2-7 1-8-2Z"/><path d="M7 18c2-3 5-5 9-6"/>',
      services:'<path d="M8 12a4 4 0 0 1 4-4h3a4 4 0 1 1 0 8h-2"/><path d="M16 12a4 4 0 0 1-4 4H9a4 4 0 1 1 0-8h2"/>',
      network:'<path d="M5 9a10 10 0 0 1 14 0M8 12a6 6 0 0 1 8 0m-5 4a2 2 0 0 1 2 0"/><circle cx="12" cy="19" r=".8" fill="currentColor" stroke="none"/>',
      ready:'<circle cx="12" cy="12" r="9"/><path d="m8 12 2.6 2.6L16.5 9"/>'
    };
    return`<div class="onboarding-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${paths[kind]||paths.welcome}</svg></div>`;
  };
  function onboardingHero(icon,title,lead,content,values={}){
    return`<div class="onboarding-hero">${onboardingIcon(icon)}<div class="onboarding-copy"><h2>${t(title,values)}</h2><p>${t(lead,values)}</p></div></div><div class="onboarding-content">${content}</div>`;
  }
  function onboardingSettingsFromDraft(){
    return{
      zigbee:{adapterUrl:state.onboardingDraft.zigbeeAdapterUrl,channel:state.onboardingDraft.zigbeeChannel},
      mqtt:{url:state.onboardingDraft.mqttUrl,baseTopic:state.onboardingDraft.mqttBaseTopic},
      matter:{wsUrl:state.onboardingDraft.matterWsUrl},
      homeAssistant:{discoveryEnabled:state.settings?.homeAssistant?.discoveryEnabled===true},
      alerts:{lowBatteryThreshold:state.settings?.alerts?.lowBatteryThreshold??15},
      selfHealing:{enabled:state.settings?.selfHealing?.enabled!==false,probeOffline:state.settings?.selfHealing?.probeOffline===true},
      debug:{enabled:state.settings?.debug?.enabled!==false}
    };
  }
  function settingsWithChannelConfirmation(settings){
    const previous=Number(state.settings?.zigbee?.channel);
    const next=Number(settings.zigbee?.channel);
    if(Number.isInteger(previous)&&previous!==next){
      if(!confirm(t("zigbeeChannelConfirm",{from:previous,to:next})))return null;
      return{...settings,zigbeeChannelConfirmation:"CHANGE"};
    }
    return settings;
  }
  /* Kanal onayının kardeşi: adres değiştiyse sunucu açık onay ister. Kurulum sihirbazında
     kullanıcı adresi zaten yazıp test etmiştir (`silent`), ikinci bir soru sorulmaz. */
  function settingsWithAdapterConfirmation(settings,silent=false){
    const previous=String(state.settings?.zigbee?.adapterUrl||"");
    const next=String(settings?.zigbee?.adapterUrl||"");
    if(!previous||previous===next)return settings;
    if(!silent&&!confirm(t("zigbeeAdapterConfirm",{from:previous,to:next})))return null;
    return{...settings,zigbeeAdapterConfirmation:"CHANGE"};
  }
  function renderOnboarding(){
    const draft=state.onboardingDraft;
    if(!draft)return;
    if(state.remoteOnboarding){
      const address=connectedServerAddress();
      $("#onboardingProgress").hidden=true;
      $("#onboardingBody").innerHTML=onboardingHero(
        "services",
        "serverSetupDetectedTitle",
        "serverSetupDetectedLead",
        `<div class="onboarding-summary"><div class="onboarding-summary-row"><span>${t("serverAddress")}</span><code>${esc(address)}</code></div></div><p class="onboarding-help">${t("serverSetupDetectedHelp")}</p>`,
        {address}
      );
      $("#onboardingBack").hidden=true;
      $("#skipOnboarding").hidden=true;
      $(".onboarding-actions").classList.add("final");
      $("#onboardingNext").textContent=t("openServerSettings");
      return;
    }
    $("#onboardingProgress").hidden=false;
    const step=state.onboardingStep;
    $("#onboardingProgress").innerHTML=Array.from({length:onboardingStepCount},(_,index)=>`<span class="${index<=step?"active":""}"></span>`).join("");
    let body="";
    if(step===0)body=onboardingHero("welcome","setupWelcomeTitle","setupWelcomeLead",`<div class="onboarding-choices"><button class="onboarding-choice ${state.language==="en"?"selected":""}" type="button" data-onboarding-language="en">English<small>Continue in English</small></button><button class="onboarding-choice ${state.language==="tr"?"selected":""}" type="button" data-onboarding-language="tr">Türkçe<small>Türkçe devam et</small></button></div>`);
    if(step===1)body=onboardingHero("hub","setupHubTitle","setupHubLead",`<div class="onboarding-features"><div class="onboarding-feature"><strong>Zigbee</strong><span>${t("setupHubZigbee")}</span></div><div class="onboarding-feature"><strong>MQTT</strong><span>${t("setupHubMqtt")}</span></div><div class="onboarding-feature"><strong>Matter</strong><span>${t("setupHubMatter")}</span></div></div>`);
    if(step===2)body=onboardingHero("zigbee","setupZigbeeTitle","setupZigbeeLead",`<div class="onboarding-fields"><div class="onboarding-field"><label for="onboardingZigbeeUrl">${t("adapterUrl")}</label><input id="onboardingZigbeeUrl" type="text" inputmode="url" autocomplete="off" spellcheck="false" value="${esc(draft.zigbeeAdapterUrl)}" placeholder="tcp://192.0.2.10:6638" required><div class="adapter-test-row"><button id="onboardingZigbeeTest" class="secondary" type="button">${t("adapterTest")}</button><span id="onboardingZigbeeTestResult" class="adapter-test-result" role="status"></span></div></div></div><p class="onboarding-help">${t(state.zigbeeCapabilities?.serialSupported?"adapterUrlHelp":"adapterUrlHelpTcpOnly")}</p><p class="onboarding-help">${t("adapterTestRequiredHelp")}</p>${state.health?.mode==="shadow"?`<p class="onboarding-help">${t("adapterManagedByZ2m")}</p>`:""}`);
    if(step===3)body=onboardingHero("services","setupServicesTitle","setupServicesLead",`<div class="onboarding-fields"><div class="onboarding-field"><label for="onboardingMqttUrl">${t("mqttUrl")}</label><input id="onboardingMqttUrl" type="url" value="${esc(draft.mqttUrl)}" required></div><div class="onboarding-field"><label for="onboardingMqttTopic">${t("baseTopic")}</label><input id="onboardingMqttTopic" value="${esc(draft.mqttBaseTopic)}" required></div><div class="onboarding-field"><label for="onboardingMatterUrl">${t("matterUrl")}</label><input id="onboardingMatterUrl" type="url" value="${esc(draft.matterWsUrl)}" required></div></div><p class="onboarding-help">${t("setupRecommendedValues")}</p>`);
    if(step===4){
      const ip=state.network?.preferredAddress||state.network?.addresses?.[0]||t("ipUnavailable");
      body=onboardingHero("network","setupNetworkTitle","setupNetworkLead",`<div class="onboarding-ip"><div><span>${t("haCurrentIp")}</span><br><code>${esc(ip)}</code></div><button id="onboardingWifiSettings" class="secondary" type="button">${t("openWifiSettings")}</button></div><p class="onboarding-help">${t("setupNetworkHelp")}</p>`);
    }
    if(step===5)body=onboardingHero("ready","setupReadyTitle","setupReadyLead",`<div class="onboarding-summary"><div class="onboarding-summary-row"><span>Zigbee</span><code>${esc(draft.zigbeeAdapterUrl)}</code></div><div class="onboarding-summary-row"><span>MQTT</span><code>${esc(draft.mqttUrl)}</code></div><div class="onboarding-summary-row"><span>Matter</span><code>${esc(draft.matterWsUrl)}</code></div></div>`);
    $("#onboardingBody").innerHTML=body;
    $("#onboardingBack").hidden=step===0;
    /* Kurulum yarımken atlama yoktur: sunucu koordinatörsüz duruyor, atlayan kullanıcı
       çalışmayan bir panelle baş başa kalırdı. */
    $("#skipOnboarding").hidden=step===5||state.setupPending===true;
    $(".onboarding-actions").classList.toggle("final",step===5);
    $("#onboardingNext").textContent=t(step===5?"finishSetup":"next");
    $$("[data-onboarding-language]").forEach(button=>button.onclick=()=>{
      state.onboardingDraft.language=button.dataset.onboardingLanguage;
      setLanguage(button.dataset.onboardingLanguage);
      renderOnboarding();
    });
    /* Adres alanı her değiştiğinde test bayrağı düşer: yazılan yeni adres denenmemiştir. */
    const zigbeeInput=$("#onboardingZigbeeUrl");
    if(zigbeeInput){
      const output=$("#onboardingZigbeeTestResult");
      zigbeeInput.oninput=()=>{
        draft.zigbeeAdapterUrl=zigbeeInput.value.trim();
        draft.zigbeeAdapterVerified=false;
        clearAdapterTestResult(output);
      };
      $("#onboardingZigbeeTest").onclick=async()=>{
        draft.zigbeeAdapterVerified=await probeZigbeeAdapter(zigbeeInput,output,$("#onboardingZigbeeTest"));
        draft.zigbeeAdapterUrl=zigbeeInput.value.trim();
      };
    }
    const wifi=$("#onboardingWifiSettings");
    if(wifi){
      wifi.hidden=!bridgeSafe(()=>typeof window.VillaAndroid?.openWifiSettings==="function",false);
      wifi.onclick=openWifiSettings;
    }
  }
  function captureOnboardingStep(forward=true){
    const draft=state.onboardingDraft;
    if(state.onboardingStep===2){
      const input=$("#onboardingZigbeeUrl");
      if(!input.reportValidity())return false;
      draft.zigbeeAdapterUrl=input.value.trim();
      /* KAPI: test edilmemiş adresle ilerlenmez. Yanlış adres kaydedilirse servis bir daha
         açılmaz, onu düzeltecek panel de gelmez; kurtarma bant dışıdır (SSH + `.settings-backup`).
         Testi zorunlu kılmanın tek sebebi budur. */
      if(forward&&draft.zigbeeAdapterVerified!==true){
        showToast(t("adapterTestRequired"),true);
        return false;
      }
    }
    if(state.onboardingStep===3){
      const inputs=[$("#onboardingMqttUrl"),$("#onboardingMqttTopic"),$("#onboardingMatterUrl")];
      if(inputs.some(input=>!input.reportValidity()))return false;
      draft.mqttUrl=inputs[0].value.trim();
      draft.mqttBaseTopic=inputs[1].value.trim();
      draft.matterWsUrl=inputs[2].value.trim();
    }
    return true;
  }
  function openOnboarding(){
    if(!state.settings)return showToast(t("settingsUnavailable"),true);
    state.remoteOnboarding=state.androidMonitor;
    state.onboardingStep=0;
    state.onboardingDraft=state.remoteOnboarding?{}:{
      language:state.language,
      /* İlk kurulumda kayıtlı değer yer tutucudur (`tcp://192.0.2.10:6638`); alan boş açılır
         ki kullanıcı sahte bir adresi kendi adresi sanmasın. */
      zigbeeAdapterUrl:state.setupPending===true?"":state.settings.zigbee.adapterUrl,
      /* Kayıtlı adres bile olsa sihirbaz test görmeden ilerlemez: kurulumu bitiren kişi
         adresin ŞU AN yanıt verdiğini görmüş olmalı. */
      zigbeeAdapterVerified:false,
      zigbeeChannel:state.settings.zigbee.channel,
      mqttUrl:state.settings.mqtt.url,
      mqttBaseTopic:state.settings.mqtt.baseTopic,
      matterWsUrl:state.settings.matter.wsUrl
    };
    renderOnboarding();
    if(!$("#onboardingDialog").open)$("#onboardingDialog").showModal();
  }
  function skipOnboarding(){
    $("#onboardingDialog").close();
    state.remoteOnboarding=false;
    activateView("devices");
    window.scrollTo({top:0,behavior:"smooth"});
    requestAnimationFrame(maybeStartDeviceHint);
  }
  async function finishOnboarding(){
    const button=$("#onboardingNext");
    button.disabled=true;
    try{
      const settings=onboardingSettingsFromDraft();
      const current={
        zigbee:state.settings.zigbee,
        mqtt:state.settings.mqtt,
        matter:state.settings.matter,
        homeAssistant:state.settings.homeAssistant,
        alerts:state.settings.alerts,
        debug:state.settings.debug
      };
      if(JSON.stringify(settings)!==JSON.stringify(current)){
        const withChannel=settingsWithChannelConfirmation(settings);
        if(!withChannel)return;
        // Adres onayı sihirbazda sessizdir: kullanıcı adresi bu adımda yazıp test etti.
        const payload=settingsWithAdapterConfirmation(withChannel,true);
        if(!payload)return;
        const data=await api("/api/settings",{method:"PUT",body:JSON.stringify(payload)});
        state.settings=data.settings;
        showToast(t("setupSavedRestart"));
      }
      await markOnboardingComplete();
      /* İlk kurulumda koordinatör adresi yalnızca dosyaya yazıldı; onu sahiplenmek için
         servis yeniden başlamalı. Mevcut apply deseni kullanılır — Android'de de aynı uç. */
      if(state.setupPending===true){
        state.setupPending=false;
        await api("/api/settings/apply",{method:"POST",body:JSON.stringify({confirmation:"APPLY"})});
        showToast(t("setupSavedRestart"));
      }
      $("#onboardingDialog").close();
      activateView("devices");
      window.scrollTo({top:0,behavior:"smooth"});
      requestAnimationFrame(maybeStartDeviceHint);
    }catch(error){showToast(error.message,true)}
    finally{button.disabled=false}
  }
  function nextOnboardingStep(){
    if(state.remoteOnboarding){
      $("#onboardingDialog").close();
      state.remoteOnboarding=false;
      state.onboardingDraft=null;
      activateView("settings");
      activateSettingsTab("setup");
      requestAnimationFrame(()=>$("#networkSettingsCard").scrollIntoView({behavior:"smooth",block:"start"}));
      return;
    }
    if(!captureOnboardingStep())return;
    if(state.onboardingStep===onboardingStepCount-1){finishOnboarding();return}
    state.onboardingStep+=1;
    renderOnboarding();
  }
  function previousOnboardingStep(){
    if(state.onboardingStep===0)return;
    captureOnboardingStep(false);
    state.onboardingStep-=1;
    renderOnboarding();
  }
  const dashboardTourSteps=()=>[
    {target:"#homeSummary",fallback:"#home .home-heading",title:"tourStatusTitle",text:"tourStatusLead"},
    {target:"#quickScenes",title:"tourQuickTitle",text:"tourQuickLead"},
    {target:"#addWidget",title:"tourAddTitle",text:"tourAddLead"},
    {target:"#editDashboard",title:"tourEditTitle",text:"tourEditLead"},
    {target:"#widgetRail",fallback:"#widgetBoard",title:"tourWidgetsTitle",text:"tourWidgetsLead"},
    /* Profil/menü adımı önce SOL RAYIN altındaki düğmeyi arar; ray çizilmediğinde (dar ekran)
       `coachTarget` sıfır ölçüyü görüp başlıktaki menü düğmesine, o da yoksa eylem grubuna düşer. */
    {target:".rail-profile",fallback:"#home [data-app-menu]",title:"tourMenuTitle",text:"tourMenuLead"}
  ];
  function coachTarget(step){
    const primary=$(step.target);
    const rect=primary?.getBoundingClientRect();
    if(primary&&rect.width>2&&rect.height>2)return primary;
    return step.fallback?$(step.fallback):primary;
  }
  function positionCoach(){
    if(!state.coach)return;
    const step=state.coach.steps[state.coach.index];
    const target=coachTarget(step);
    if(!target){nextCoachStep();return}
    const rect=target.getBoundingClientRect();
    const pad=7;
    const spotlight=$("#coachSpotlight");
    spotlight.style.left=`${Math.max(5,rect.left-pad)}px`;
    spotlight.style.top=`${Math.max(5,rect.top-pad)}px`;
    spotlight.style.width=`${Math.min(innerWidth-10,rect.width+pad*2)}px`;
    spotlight.style.height=`${Math.min(innerHeight-10,rect.height+pad*2)}px`;
    const tooltip=$("#coachTooltip");
    const tooltipRect=tooltip.getBoundingClientRect();
    const below=rect.bottom+tooltipRect.height+22<innerHeight;
    const top=below?rect.bottom+16:Math.max(12,rect.top-tooltipRect.height-16);
    const left=Math.max(14,Math.min(innerWidth-tooltipRect.width-14,rect.left+(rect.width-tooltipRect.width)/2));
    tooltip.style.top=`${top}px`;
    tooltip.style.left=`${left}px`;
  }
  function renderCoach(){
    const coach=state.coach;
    if(!coach)return;
    const step=coach.steps[coach.index];
    const target=coachTarget(step);
    if(!target){nextCoachStep();return}
    const rect=target.getBoundingClientRect();
    if(rect.top<78||rect.bottom>innerHeight-16)target.scrollIntoView({block:"center",behavior:"smooth"});
    $("#coachStep").textContent=t("tourStep",{current:coach.index+1,total:coach.steps.length});
    $("#coachTitle").textContent=t(step.title);
    $("#coachText").textContent=t(step.text);
    $("#coachBack").hidden=coach.index===0;
    $("#coachNext").textContent=t(coach.index===coach.steps.length-1?"finishTour":"next");
    requestAnimationFrame(()=>setTimeout(positionCoach,rect.top<78||rect.bottom>innerHeight-16?220:0));
  }
  function startCoach(name,steps){
    if(state.coach||document.querySelector("dialog[open]"))return;
    state.coach={name,steps,index:0};
    $("#coachLayer").hidden=false;
    renderCoach();
  }
  function finishCoach(){
    if(!state.coach)return;
    const key=state.coach.name==="dashboard"?dashboardTourStorageKey:deviceHintStorageKey;
    try{localStorage.setItem(key,"true")}catch{}
    state.coach=null;
    $("#coachLayer").hidden=true;
  }
  function nextCoachStep(){
    if(!state.coach)return;
    if(state.coach.index>=state.coach.steps.length-1){finishCoach();return}
    state.coach.index+=1;
    renderCoach();
  }
  function previousCoachStep(){
    if(!state.coach||state.coach.index===0)return;
    state.coach.index-=1;
    renderCoach();
  }
  function maybeStartDashboardTour(){
    if(document.body.dataset.activeView!=="home"||!onboardingComplete()||dashboardTourComplete())return;
    startCoach("dashboard",dashboardTourSteps());
  }
  function maybeStartDeviceHint(){
    if(document.body.dataset.activeView!=="devices"||!onboardingComplete()||deviceHintComplete()||!state.overviewLoaded||state.devices.length)return;
    startCoach("device",[{target:"#devices .add-device",title:"deviceHintTitle",text:"deviceHintLead"}]);
  }
  function restartDashboardGuide(){
    try{localStorage.removeItem(dashboardTourStorageKey)}catch{}
    activateView("home");
    requestAnimationFrame(maybeStartDashboardTour);
  }
  function renderDebugErrors(){
    const errors=state.debugErrors;
    $("#debugLogTitle").textContent=t("recentErrors",{count:errors.length});
    $("#debugErrorList").innerHTML=errors.length?errors.map(error=>`<article class="debug-error-row"><strong>${esc(error.operation)} · HTTP ${esc(error.statusCode)}</strong><time datetime="${esc(error.timestamp)}">${ago(error.timestamp)}</time><p>${esc(error.message)}</p></article>`).join(""):`<div class="debug-empty">${t("noDebugErrors")}</div>`;
    renderSystemAlertBar();
  }
  function renderDebugSettings(){
    const enabled=state.settings?.debug?.enabled===true;
    $("#toggleDebug").classList.toggle("on",enabled);
    $("#toggleDebug").setAttribute("aria-pressed",String(enabled));
    $("#toggleDebug").setAttribute("aria-label",t("debugMode"));
    $("#debugModeStatus").textContent=t(enabled?"enabled":"disabled");
    $("#debugLogPanel").hidden=!enabled;
    if(!enabled){state.debugErrors=[];renderDebugErrors()}
  }
  async function loadDebugErrors(){
    if(state.settings?.debug?.enabled!==true)return;
    try{
      const data=await api("/api/debug/errors");
      state.debugErrors=Array.isArray(data.errors)?data.errors:[];
      renderDebugErrors();
    }catch(error){showToast(error.message,true)}
  }
  const debugNetworkReasonKeys={joined:"deviceEventJoined",left:"deviceEventLeft",removed:"deviceEventRemoved"};
  const debugNetworkFullTime=iso=>{const at=new Date(iso);return Number.isNaN(at.getTime())?"":at.toLocaleString(state.language||undefined)};
  function renderDebugNetworkEvents(){
    // Sunucu en yeniden eskiye veriyor; sıralama yine de burada garanti edilir.
    const events=[...(state.debugNetworkEvents||[])].sort((a,b)=>String(b.at||"").localeCompare(String(a.at||"")));
    $("#debugNetworkTitle").textContent=t("deviceNetworkEvents",{count:events.length});
    $("#debugNetworkList").innerHTML=events.length?events.map(event=>{
      const label=t(debugNetworkReasonKeys[event.reason]||"deviceEventLeft");
      return`<article class="debug-network-row" data-network-reason="${esc(event.reason)}"><strong>${esc(event.name||event.id)}</strong><time datetime="${esc(event.at)}" title="${esc(debugNetworkFullTime(event.at))}">${ago(event.at)}</time><p>${esc(label)} · ${esc(event.id)}</p></article>`;
    }).join(""):`<div class="debug-empty">${t("noDeviceNetworkEvents")}</div>`;
  }
  async function loadDebugNetworkEvents(){
    try{
      const data=await api("/api/debug/network-events");
      state.debugNetworkEvents=Array.isArray(data.events)?data.events:[];
    }catch(error){state.debugNetworkEvents=[]}
    renderDebugNetworkEvents();
  }
  async function toggleDebugMode(){
    if(!state.settings)return;
    const button=$("#toggleDebug");
    button.disabled=true;
    const enabled=state.settings.debug?.enabled!==true;
    try{
      const settings={...state.settings,debug:{enabled}};
      await api("/api/settings",{method:"PUT",body:JSON.stringify(settings)});
      state.settings=settings;
      renderDebugSettings();
      if(enabled)await loadDebugErrors();
      showToast(t(enabled?"debugEnabled":"debugDisabled"));
    }catch(error){showToast(error.message,true)}
    finally{button.disabled=false}
  }
  async function clearDebugErrors(){
    const button=$("#clearDebugErrors");
    button.disabled=true;
    try{
      await api("/api/debug/errors",{method:"DELETE"});
      state.debugErrors=[];
      renderDebugErrors();
    }catch(error){showToast(error.message,true)}
    finally{button.disabled=false}
  }
  function renderHomeAssistant(){
    if(!state.settings)return;
    const broker=new URL(state.settings.mqtt.url);
    const localNames=["127.0.0.1","localhost","::1","[::1]","0.0.0.0"];
    const preferredAddress=state.network?.preferredAddress||null;
    $("#haBroker").textContent=localNames.includes(broker.hostname)&&preferredAddress?preferredAddress:broker.hostname;
    $("#haPort").textContent=broker.port||"1883";
    $("#haProtocol").textContent=state.mqttAccess?.protocol||"3.1.1";
    const authenticationRequired=state.mqttAccess?.authenticationRequired===true;
    $("#haUsername").textContent=authenticationRequired?state.mqttAccess.username:t("leaveBlank");
    const password=authenticationRequired?String(state.mqttAccess.password??""):"";
    $("#haPassword").textContent=authenticationRequired?(state.mqttPasswordVisible?password:"••••••••"):t("leaveBlank");
    $("#toggleHaPassword").hidden=!authenticationRequired;
    $("#toggleHaPassword").textContent=state.mqttPasswordVisible?"◌":"◉";
    $("#toggleHaPassword").setAttribute("aria-label",t(state.mqttPasswordVisible?"hidePassword":"showPassword"));
    $("#haBaseTopic").textContent=state.settings.mqtt.baseTopic;
    $("#haCurrentIp").textContent=preferredAddress||state.network?.addresses?.[0]||t("ipUnavailable");
    const enabled=state.settings.homeAssistant?.discoveryEnabled===true;
    const status=$("#haDiscoveryStatus");
    status.textContent=t(enabled?"enabled":"disabled");
    status.className=enabled?"online-text":"unknown-text";
    $("#toggleHaDiscovery").textContent=t(enabled?"disableDiscovery":"enableDiscovery");
    const setupExpanded=$("#haSetupDialog")?.open===true;
    $("#toggleHaSetup").textContent=t(setupExpanded?"hideHomeAssistantSetup":"connectHomeAssistant");
    $("#toggleHaSetup").setAttribute("aria-expanded",String(setupExpanded));
  }
  function connectedServerAddress(){
    const discoveredAddress=state.androidMonitor&&bridgeSafe(()=>typeof window.VillaAndroid?.connectedServerAddress==="function",false)
      ?String(bridgeSafe(()=>window.VillaAndroid?.connectedServerAddress(),"")||"").trim():"";
    return discoveredAddress||state.network?.preferredAddress||state.network?.addresses?.[0]||t("ipUnavailable");
  }
  function renderConnectedServerAddress(){
    const address=connectedServerAddress();
    const addressOutput=$("#connectedServerAddress");
    const restartButton=$("#restartOnboarding");
    /* Açıklama `compact-help` tarafından body katmanına taşınabilir. Kartın içindeki konuma
       değil kalıcı kimliğine bağlanmak, dil veya görünüm yenilenirken düğümü kaybetmez. */
    const guideLead=$("#setupWizardLead")||$('[data-i18n="guidesLead"]');
    if(addressOutput)addressOutput.textContent=address;
    if(restartButton)restartButton.textContent=t(state.androidMonitor?"openServerSettings":"runSetupAgain");
    if(guideLead)guideLead.textContent=t(state.androidMonitor?"serverGuidesLead":"guidesLead");
  }
  /* DUVARDAKİ TABLETİN EKRANI VE AYAKTA KALMASI.
     Kullanıcının gördüğü her ayar burada — Android tarafında ayar ekranı yoktur, orası yalnız
     uygular. Bu yüzden gece penceresinin ne zaman başladığına, hangi kademenin ne demek
     olduğuna karar veren taraf da burasıdır: köprüye yalnızca "şu an geçerli olan" sayılar
     gider (kip, bekleme süresi, iki parlaklık). Tercihler cihazda kalır (`localStorage`) —
     duvardaki tabletin ekran davranışı evin ortak ayarı değil, o cihazın kendi ayarıdır. */
  const screenPolicyStorageKey="villa-screen-policy";
  const screenPolicyDefaults={brightness:-1,idleSeconds:120,dimEnabled:true,dimBrightness:12,nightEnabled:false,nightStart:"23:00",nightEnd:"07:00",nightBrightness:6};
  const screenIdleChoices=[30,60,120,300,900];
  const screenPolicyPreferences=(()=>{
    const stored=(()=>{try{return JSON.parse(localStorage.getItem(screenPolicyStorageKey)||"null")}catch{return null}})();
    const value={...screenPolicyDefaults,...(stored&&typeof stored==="object"?stored:{})};
    const clamp=(number,minimum,maximum,fallback)=>Number.isFinite(Number(number))?Math.min(maximum,Math.max(minimum,Math.round(Number(number)))):fallback;
    const clock=(text,fallback)=>/^([01]\d|2[0-3]):[0-5]\d$/.test(String(text))?String(text):fallback;
    return{
      /* -1 "henüz elleşilmedi" demek: panel cihazın kendi parlaklığını olduğu gibi bırakır ve
         açılışta oradan okur. Kullanıcı kaydırıcıyı ilk kez oynattığında gerçek bir değer olur. */
      brightness:Number(value.brightness)===-1?-1:clamp(value.brightness,5,100,screenPolicyDefaults.brightness),
      idleSeconds:screenIdleChoices.includes(Number(value.idleSeconds))?Number(value.idleSeconds):screenPolicyDefaults.idleSeconds,
      dimEnabled:value.dimEnabled!==false,
      dimBrightness:clamp(value.dimBrightness,2,60,screenPolicyDefaults.dimBrightness),
      nightEnabled:value.nightEnabled===true,
      nightStart:clock(value.nightStart,screenPolicyDefaults.nightStart),
      nightEnd:clock(value.nightEnd,screenPolicyDefaults.nightEnd),
      nightBrightness:clamp(value.nightBrightness,1,40,screenPolicyDefaults.nightBrightness)
    };
  })();
  let screenPolicyTimer=null;
  /* Karartma şu anda UYGULANMIŞ durumda mı. `state.screensaverOpen` ile aynı şey değil:
     ekran koruyucu açılırken önce cihazın parlaklığı okunur, karartma ondan sonra yazılır. */
  let screenDimApplied=false;
  const screenBridgeAvailable=()=>bridgeSafe(()=>typeof window.VillaAndroid?.applyScreenPolicy==="function",false);
  function saveScreenPreferences(){try{localStorage.setItem(screenPolicyStorageKey,JSON.stringify(screenPolicyPreferences))}catch{}}
  /* Gece penceresi gece yarısını aşabilir (23:00 → 07:00); o durumda "aralık dışı" değil
     "iki parçanın birleşimi" aranır. */
  function screenNightActive(now=new Date()){
    if(!screenPolicyPreferences.nightEnabled)return false;
    const minutes=text=>{const[hour,minute]=String(text).split(":").map(Number);return hour*60+minute};
    const current=now.getHours()*60+now.getMinutes();
    const start=minutes(screenPolicyPreferences.nightStart);
    const end=minutes(screenPolicyPreferences.nightEnd);
    if(start===end)return false;
    return start<end?current>=start&&current<end:current>=start||current<end;
  }
  /* Köprüye giden ilke: kullanımdaki değer, kararmış değer ve şu anda hangisinde olduğumuz.
     Gece penceresi açıksa ikisi de gece değerlerine düşer. `-1` "dokunma" demektir — kullanıcı
     kaydırıcıyı hiç oynatmadıysa tabletin kendi parlaklığı olduğu gibi kalır. */
  function effectiveScreenPolicy(dimmed){
    const night=screenNightActive();
    const active=night?screenPolicyPreferences.nightBrightness:screenPolicyPreferences.brightness;
    const dim=night
      ?Math.min(screenPolicyPreferences.nightBrightness,screenPolicyPreferences.dimBrightness)
      :screenPolicyPreferences.dimBrightness;
    return{
      activeBrightness:active,
      idleBrightness:screenPolicyPreferences.dimEnabled?dim:active,
      dimmed:dimmed===true
    };
  }
  function pushScreenPolicy(dimmed=screenDimApplied){
    if(!screenBridgeAvailable())return;
    const payload=JSON.stringify(effectiveScreenPolicy(dimmed));
    bridgeSafe(()=>window.VillaAndroid?.applyScreenPolicy(payload));
  }
  /* EKRAN KORUYUCUYLA TEK SAYAÇ. Kararma ayrı bir zamanlayıcıya bağlı değil: panelin zaten
     var olan ekran koruyucusu açılınca karartır, dokunup kapatılınca geri getirir. Bekleme
     süresi de aynı ayardan gelir (`screensaverDelay`), böylece iki farklı süre olmaz. */
  function applyScreensaverDimming(dimmed){
    if(!screenBridgeAvailable())return;
    /* Karartmadan ÖNCE cihazın o anki parlaklığı okunur, yoksa geri getirilecek bir değer
       kalmaz: kullanıcı kaydırıcıya hiç dokunmadıysa tercih `-1` ("dokunma") olurdu ve ekran
       koruyucu kapandığında ekran kararmış hâlde kalırdı. */
    if(dimmed)adoptSystemBrightness();
    screenDimApplied=dimmed===true;
    pushScreenPolicy(dimmed);
  }
  /* Cihazın kendi parlaklığını panele okumak: kullanıcı Android ayarlarından değiştirdiyse
     kaydırıcı bir sonraki açılışta o değeri gösterir. Gece penceresi işlerken okunmaz —
     o an ekranda duran değer bizim yazdığımız gece değeridir, onu "kullanıcı tercihi" sanmak
     gündüz parlaklığını gecenin üstüne yazardı. */
  function adoptSystemBrightness(){
    const screen=hostStatusSnapshot()?.screen;
    /* Kararma uygulanmışken ya da gece penceresi işlerken okunmaz: o an ekranda duran değer
       zaten bizim yazdığımızdır, onu "kullanıcı tercihi" sanmak gündüz parlaklığını gecenin
       üstüne yazardı. */
    if(!screen||screen.systemWritable!==true||screenNightActive()||screenDimApplied)return;
    const current=Number(screen.currentBrightness);
    if(!Number.isFinite(current)||current<1)return;
    if(current===screenPolicyPreferences.brightness)return;
    screenPolicyPreferences.brightness=current;
    saveScreenPreferences();
  }
  function updateScreenPreference(patch){
    Object.assign(screenPolicyPreferences,patch);
    saveScreenPreferences();
    pushScreenPolicy();
    refreshScreensaverDelay();
    renderTabletCare();
    renderMenuBrightness();
  }
  /* Bekleme süresi ekran koruyucunun süresidir; değişince sayaç yeniden kurulur. */
  function refreshScreensaverDelay(){
    screensaverDelay=screenPolicyPreferences.idleSeconds*1000;
    scheduleScreensaver();
  }
  function hostStatusSnapshot(){
    const raw=bridgeSafe(()=>window.VillaAndroid?.hostStatus());
    if(typeof raw!=="string"||!raw)return null;
    /* Ayrıştırma da köprünün parçası: konak bir gün geçersiz ya da kırpılmış JSON dönerse
       satır boş kalır, panel ayakta kalır. */
    const parsed=bridgeSafe(()=>JSON.parse(raw));
    return parsed&&typeof parsed==="object"?parsed:null;
  }
  function requestBatteryExemption(){
    if(!bridgeSafe(()=>typeof window.VillaAndroid?.requestBatteryExemption==="function",false))return showToast(t("batteryGuardAndroidOnly"),true);
    bridgeSafe(()=>window.VillaAndroid?.requestBatteryExemption());
    /* Sistem penceresi kendi başına açılır; kullanıcı geri döndüğünde durum tazelensin.
       Reddetmek serbest — panel bir daha sormaz, yalnız durumu gösterir. */
    setTimeout(renderTabletCare,2000);
  }
  /* PARLAKLIK UYGULAMA MENÜSÜNDE. Günlük bir ayardır, kurulum ayarı değil: yönetici kısıtı
     yok, ev sakini de duvardaki tabletin parlaklığını değiştirebilir. Kurulum nitelikli olan
     (gece penceresi, bekleme süresi, kararma) ayarlar sayfasındaki kartta kalır. */
  function renderMenuBrightness(){
    const row=$("#appMenuBrightness");
    if(!row)return;
    const available=screenBridgeAvailable();
    row.hidden=!available;
    if(!available)return;
    const screen=hostStatusSnapshot()?.screen;
    const level=screenPolicyPreferences.brightness>0
      ?screenPolicyPreferences.brightness
      :Number(screen?.currentBrightness)>0?Number(screen.currentBrightness):70;
    $("#menuBrightness").value=String(level);
    $("#menuBrightnessValue").textContent=`${level}%`;
    /* İzin yoksa uygulama çalışmaya devam eder; kaydırıcı yalnızca bu ekranı karartır.
       Kullanıcı bunu bilsin diye satır açıkça yazılır, düğme zorlamaz. */
    const fallback=available&&screen?.systemWritable!==true;
    $("#menuBrightnessNote").hidden=!fallback;
    $("#grantSystemBrightness").hidden=!fallback;
    /* Otomatik parlaklık açıkken elle yazılan değeri ışık sensörü geri alır; ilk elle
       ayarda kip elle kipe alınıyor ve bu sessizce yapılmıyor. */
    $("#menuBrightnessAutoNote").hidden=screen?.automatic!==true;
  }
  function setMenuBrightness(level){
    updateScreenPreference({brightness:Math.min(100,Math.max(5,Math.round(Number(level)||70)))});
  }
  function requestSystemBrightnessPermission(){
    if(!bridgeSafe(()=>typeof window.VillaAndroid?.requestSystemBrightnessPermission==="function",false))return showToast(t("screenBrightnessAndroidOnly"),true);
    bridgeSafe(()=>window.VillaAndroid?.requestSystemBrightnessPermission());
    setTimeout(()=>{renderMenuBrightness();renderTabletCare()},2000);
  }
  function renderTabletCare(){
    const card=$("#tabletCareCard");
    const screenCard=$("#screenSleepCard");
    if(!card)return;
    const available=screenBridgeAvailable();
    card.hidden=!available;
    if(screenCard)screenCard.hidden=!available;
    if(!available)return;
    const status=hostStatusSnapshot();
    const exempt=status?.batteryExempt===true;
    $("#batteryGuardStatus").textContent=t(exempt?"batteryGuardProtected":"batteryGuardLimited");
    $("#batteryGuardStatus").className=exempt?"tablet-care-badge online-text":"tablet-care-badge unknown-text";
    $("#requestBatteryExemption").textContent=t(exempt?"batteryGuardReview":"batteryGuardAction");
    const systemWritable=status?.screen?.systemWritable===true;
    $("#systemBrightnessStatus").textContent=t(systemWritable?"screenBrightnessSystemOn":"screenBrightnessSystemOff");
    $("#systemBrightnessStatus").className=systemWritable?"tablet-care-badge online-text":"tablet-care-badge unknown-text";
    const restarts=Number(status?.restarts||0);
    $("#watchdogSummary").textContent=status?.exhausted===true
      ?t("watchdogExhausted",{count:restarts})
      :restarts>0
        ?t("watchdogRestarts",{count:restarts,max:Number(status?.maxRestarts||0),seconds:Math.round(Number(status?.nextDelayMs||0)/1000),code:Number(status?.lastExitCode||0)})
        :t("watchdogHealthy");
    $("#screenIdleSeconds").value=String(screenPolicyPreferences.idleSeconds);
    $("#screenDimEnabled").checked=screenPolicyPreferences.dimEnabled;
    $("#screenDimBrightness").value=String(screenPolicyPreferences.dimBrightness);
    $("#screenDimBrightness").disabled=!screenPolicyPreferences.dimEnabled;
    $("#screenDimBrightnessValue").textContent=`${screenPolicyPreferences.dimBrightness}%`;
    $("#screenNightEnabled").checked=screenPolicyPreferences.nightEnabled;
    $("#screenNightStart").value=screenPolicyPreferences.nightStart;
    $("#screenNightEnd").value=screenPolicyPreferences.nightEnd;
    $("#screenNightBrightness").value=String(screenPolicyPreferences.nightBrightness);
    $("#screenNightBrightnessValue").textContent=`${screenPolicyPreferences.nightBrightness}%`;
    $$("#screenSleepCard .tablet-night-fields .setting-field").forEach(field=>field.classList.toggle("disabled",!screenPolicyPreferences.nightEnabled));
    ["#screenNightStart","#screenNightEnd","#screenNightBrightness"].forEach(id=>{$(id).disabled=!screenPolicyPreferences.nightEnabled});
    $("#screenNightState").hidden=!screenNightActive();
  }
  /* Gece penceresi dakikada bir yeniden değerlendirilir: saat 23:00'ü geçtiğinde ekran kimse
     dokunmadan kararmalı. Zamanlayıcı yalnız Android kabuğunda kurulur. */
  function startScreenPolicyTimer(){
    if(screenPolicyTimer||!screenBridgeAvailable())return;
    let wasNight=screenNightActive();
    screenPolicyTimer=setInterval(()=>{
      const night=screenNightActive();
      if(night===wasNight)return;
      wasNight=night;
      pushScreenPolicy();
      renderTabletCare();
      renderMenuBrightness();
    },60000);
  }
  /* AÇILIŞI KESEMEZ. `initialize()` bu işlevi mod durumu okunmadan ÖNCE çağırıyor: burada
     fırlayan bir hata `loadModeState()`e hiç gelinmemesi, yani hiç açılmayan bir panel demekti. Aynı
     işlev bağlama zincirinde de `bindScreensaver()`den önce duruyor. Bu yüzden gövde tümüyle
     korunuyor — konak yüzeyi eksik ya da bozuksa panel o yeteneksiz devam eder. */
  function configureAndroidActions(){
    try{renderAndroidActions()}
    catch(error){console.warn("Panel: Android yüzeyi hazırlanamadı; panel bu yeteneksiz devam ediyor.",error)}
  }
  function renderAndroidActions(){
    const button=$("#openWifiSettings");
    const available=bridgeSafe(()=>typeof window.VillaAndroid?.openWifiSettings==="function",false);
    if(button)button.hidden=!available;
    const runtimeAvailable=
      bridgeSafe(()=>typeof window.VillaAndroid?.stopRuntime==="function",false)&&
      bridgeSafe(()=>typeof window.VillaAndroid?.runtimeStatus==="function",false);
    document.body.classList.toggle("android-app",runtimeAvailable);
    const status=runtimeAvailable?String(bridgeSafe(()=>window.VillaAndroid?.runtimeStatus(),"")||""):"";
    state.androidMonitor=status==="android-monitor";
    if($("#tabletIpGuide"))$("#tabletIpGuide").hidden=state.androidMonitor;
    if($("#androidRuntimeCard"))$("#androidRuntimeCard").hidden=!runtimeAvailable||status==="android-monitor";
    renderConnectedServerAddress();
    renderSystemAlertBar();
    if(runtimeAvailable&&$("#runtimeStatusText")){
      $("#runtimeStatusText").textContent=t(status==="stopped"?"runtimeStopped":"runtimeRunning");
    }
    /* Açılışta önce cihazın kendi parlaklığı okunur (kullanıcı Android ayarlarından
       değiştirmiş olabilir), sonra yalnız gece penceresi işliyorsa yazılır. Elleşilmemiş
       bir tabletin parlaklığı panel açıldı diye değişmez. */
    adoptSystemBrightness();
    if(screenNightActive())pushScreenPolicy();
    refreshScreensaverDelay();
    startScreenPolicyTimer();
    renderTabletCare();
    renderMenuBrightness();
  }
  /* Ekran koruyucu kancası panelin en sıcak yolunda (her açılış/kapanış): köprü hatası
     ekran koruyucunun kendisini bozmamalı, saat/tarih her hâlde görünmeli. */
  function safeScreensaverDimming(dimmed){
    try{applyScreensaverDimming(dimmed)}
    catch(error){console.warn("Panel: ekran koruyucu kararması uygulanamadı.",error)}
  }
  function openWifiSettings(){
    if(bridgeSafe(()=>typeof window.VillaAndroid?.openWifiSettings==="function",false)){
      bridgeSafe(()=>window.VillaAndroid?.openWifiSettings());
      return;
    }
    showToast(t("wifiSettingsAndroidOnly"),true);
  }
  function stopAndroidRuntime(){
    if(!bridgeSafe(()=>typeof window.VillaAndroid?.stopRuntime==="function",false))return;
    $("#runtimeStopDialog").showModal();
  }
  function confirmAndroidRuntimeStop(){
    $("#runtimeStopDialog").close();
    $("#stopRuntime").disabled=true;
    bridgeSafe(()=>window.VillaAndroid?.stopRuntime());
  }
  function toggleHomeAssistantSetup(){
    const dialog=$("#haSetupDialog");
    if(dialog.open)dialog.close();
    else dialog.showModal();
    renderHomeAssistant();
  }
  function closeHomeAssistantSetup(){
    const dialog=$("#haSetupDialog");
    if(dialog?.open)dialog.close();
  }
  async function toggleHomeAssistantDiscovery(){
    if(!state.settings)return;
    const button=$("#toggleHaDiscovery");
    const enabled=state.settings.homeAssistant?.discoveryEnabled!==true;
    button.disabled=true;
    try{
      const data=await api("/api/home-assistant/discovery",{method:"PUT",body:JSON.stringify({enabled})});
      state.settings=data.settings;
      renderHomeAssistant();
      showToast(t(enabled?"discoveryEnabledLive":"discoveryDisabledLive"));
    }catch(error){showToast(error.message,true)}
    finally{button.disabled=false}
  }
  async function loadHomeGroups(){
    try{
      const data=await api("/api/home-groups");
      const groups=Array.isArray(data.groups)?data.groups:[];
      if(!groups.length&&!groupsMigrated()&&savedGroups.length){state.pendingGroupMigration=true;render();return}
      state.groups=groups;
      saveDashboardGroups();
      markGroupsMigrated();
      applyWidgetLayout();
      render();
    }catch(error){showToast(error.message,true)}
  }
  /* Göç bir kez ve sessiz: bugüne kadar cihazda tutulan seçimler sunucuya taşınır, sonra yerel
     kayıt silinir. Grup görünürlüğü eski düzende kart sırasının içindeydi
     (`villa-dashboard-removed-widgets` içindeki `group:` girdileri); o da buraya alınır. */
  function legacyVisibility(){
    const read=(key,fallback)=>{try{
      const value=JSON.parse(localStorage.getItem(key)||fallback);
      return Array.isArray(value)?value.filter(item=>typeof item==="string"&&item):[];
    }catch{return[]}};
    return{
      hiddenDevices:read(hiddenTilesStorageKey,"[]"),
      hiddenGroups:read(removedWidgetsKey,"[]")
        .filter(id=>id.startsWith(groupWidgetPrefix))
        .map(id=>id.slice(groupWidgetPrefix.length))
    };
  }
  function clearLegacyVisibility(){
    try{localStorage.removeItem(hiddenTilesStorageKey)}catch{}
    let dropped=false;
    for(const id of[...state.removedWidgets]){
      if(!id.startsWith(groupWidgetPrefix))continue;
      state.removedWidgets.delete(id);
      dropped=true;
    }
    if(dropped)saveRemovedWidgets();
  }
  async function loadHomeVisibility(){
    let visibility;
    try{
      visibility=(await api("/api/home-visibility")).visibility;
    }catch(error){
      // Çevrimdışı: panel son bilinen değerle çalışmaya devam eder, kayıt sıfırlanmaz.
      showToast(t("visibilityLoadFailed",{error:error.message}),true);
      return;
    }
    const legacy=legacyVisibility();
    const serverEmpty=!(visibility?.hiddenDevices||[]).length&&!(visibility?.hiddenGroups||[]).length;
    if(serverEmpty&&(legacy.hiddenDevices.length||legacy.hiddenGroups.length)){
      state.hiddenTiles=new Set(legacy.hiddenDevices);
      state.hiddenGroups=new Set(legacy.hiddenGroups);
      // Yazma başarısızsa yerel kayıt yerinde kalır: seçimler bir sonraki açılışta yeniden denenir.
      if(await saveHomeVisibility())clearLegacyVisibility();
    }else{
      applyVisibility(visibility);
      clearLegacyVisibility();
    }
    applyWidgetLayout();
    render();
  }
  /* Favoriler görünürlükle aynı yolu izler: karar sunucuda, panel yalnız okur ve yazar. Göç yok —
     depo (`home-favorites.json`) baştan sunucudaydı, cihazda hiç yerel bir favori kaydı olmadı.
     Çevrimdışıysa panel son bilinen kayıtla çalışmaya devam eder, liste sıfırlanmaz. */
  async function loadHomeFavorites(){
    let favorites;
    try{
      favorites=(await api("/api/favorites")).favorites;
    }catch(error){
      showToast(t("favoritesLoadFailed",{error:error.message}),true);
      return;
    }
    applyFavorites(favorites);
    applyWidgetLayout();
    render();
  }
  function migratableLocalGroups(){
    if(!state.devices.length)return savedGroups;
    return savedGroups.map(group=>({id:group.id,name:group.name,items:group.items.filter(item=>{
      const device=state.devices.find(candidate=>candidate.id===item.deviceId);
      if(!device)return false;
      return item.controlId===groupDeviceControlId||device.controls.some(control=>control.id===item.controlId&&isDashboardControl(control));
    })})).filter(group=>group.items.length>0);
  }
  async function migrateLocalGroups(){
    if(!state.pendingGroupMigration)return;
    state.pendingGroupMigration=false;
    const groups=migratableLocalGroups();
    if(!groups.length){markGroupsMigrated();return}
    try{
      const data=await api("/api/home-groups",{method:"PUT",body:JSON.stringify({groups})});
      state.groups=Array.isArray(data.groups)?data.groups:groups;
      saveDashboardGroups();
      markGroupsMigrated();
      applyWidgetLayout();
      render();
      showToast(t("groupsMigrated",{count:state.groups.length}));
    }catch(error){showToast(t("groupsMigrationFailed",{error:error.message}),true)}
  }
  async function persistHomeGroups(groups,successKey){
    state.groups=groups;
    saveDashboardGroups();
    applyWidgetLayout();
    render();
    try{
      const data=await api("/api/home-groups",{method:"PUT",body:JSON.stringify({groups})});
      if(Array.isArray(data.groups)){state.groups=data.groups;saveDashboardGroups();applyWidgetLayout();render()}
      markGroupsMigrated();
      if(successKey)showToast(t(successKey));
    }catch(error){showToast(t("groupSaveFailed",{error:error.message}),true)}
  }
