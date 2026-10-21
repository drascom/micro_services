  /* MOD — tek kullanıcı, iki mod. Giriş ekranı YOK: panel açılır açılmaz ev modundadır.
     Kurulum ekranları "yönetici modu" ister; o modun anahtarı tek bir PIN'dir (varsayılan 1234).
     Buradaki bayrak yalnız SUNUCUNUN söylediğini yansıtır — gizlenen düğme bir yetki değildir,
     yetkiyi her istekte sunucu yeniden sorar (`ELEVATION_REQUIRED`). */
  function setModeState(data){
    state.auth={
      elevated:data.elevated===true,
      mustChangePin:data.mustChangePin===true,
      secretKind:data.secretKind==="password"?"password":"pin",
      csrfToken:typeof data.csrfToken==="string"?data.csrfToken:state.auth.csrfToken,
      expiresAt:typeof data.expiresAt==="string"?data.expiresAt:state.auth.expiresAt,
      elevationExpiresAt:typeof data.elevationExpiresAt==="string"?data.elevationExpiresAt:null
    };
    applyAuthUi();
  }
  function setModeFormError(message=""){
    const error=$("#modePinError");
    error.textContent=message;
    error.hidden=!message;
  }
  /* Bir yönetici yazması, PIN penceresi açık değilken yetki süresinin dolduğunu öğrenebilir.
     İstek sahibi bu Promise'i bekler; PIN doğruysa işlemini taslağı kaybetmeden sürdürür,
     kullanıcı Vazgeç derse `false` alır. Normal menüden giriş bu bekleyiciyi oluşturmaz. */
  let pendingModeElevation=null;
  function settleModeElevation(granted){
    const pending=pendingModeElevation;
    pendingModeElevation=null;
    if(pending)pending.resolve(granted===true);
  }
  function requestAdminElevation(){
    if(state.auth.elevated===true)return Promise.resolve(true);
    if(pendingModeElevation)return pendingModeElevation.promise;
    let resolve;
    const promise=new Promise(done=>{resolve=done});
    pendingModeElevation={promise,resolve};
    openModePin();
    return promise;
  }
  /* Yükseltme sunucuda hareketsizlikte düşer. Panel bunu kendi başına bilemez; bayrağı yerelde
     düşürüp gerçeği bir sonraki sunucu yanıtından öğrenir. */
  function dropElevatedFlag(){
    if(!state.auth.elevated)return;
    state.auth.elevated=false;
    state.auth.elevationExpiresAt=null;
    applyAuthUi();
  }
  function applyAuthUi(){
    const elevated=state.auth.elevated===true;
    document.body.classList.toggle("home-mode",!elevated);
    // Yönetici ekranı açıkken mod düşerse kullanıcı boş bir sayfada kalmasın.
    if(!elevated&&["adminOverview","automations","connections","settings"].includes(document.body.dataset.activeView))activateView("home");
    if(!elevated&&$("#systemDetailDialog")?.open)$("#systemDetailDialog").close();
    $$("[data-mode-toggle]").forEach(button=>{
      const label=t(elevated?"backToHomeMode":"enterAdminMode");
      button.setAttribute("aria-label",label);
      button.title=label;
      button.classList.toggle("active",elevated);
      const text=button.querySelector("[data-mode-toggle-label]");
      if(text)text.textContent=label;
    });
    // Menü başlığındaki kısa alt satır yalnız etkin modu söyler.
    $("#appMenuRole").textContent=t(elevated?"adminModeOn":"homeModeOn");
    $("#modeStateBadge").textContent=t(elevated?"adminModeOn":"homeModeOn");
    // KALICI UYARI ŞERİDİ: PIN hâlâ fabrika varsayılanı (1234). `data-admin-only` taşıdığı için
    // ev modunda zaten görünmez; burada yalnız "varsayılan mı" sorusu kalır.
    const showDefaultPinWarning=elevated&&state.auth.mustChangePin;
    $("#defaultPinBanner").hidden=!showDefaultPinWarning;
    document.body.classList.toggle("has-default-pin-warning",showDefaultPinWarning);
    $("#modePinFieldHint").textContent=t(state.auth.secretKind==="password"?"adminSecretLegacyHint":"adminPinHint");
    const input=$("#modePinInput");
    input.inputMode=state.auth.secretKind==="password"?"text":"numeric";
    input.maxLength=state.auth.secretKind==="password"?128:8;
  }
  async function loadModeState(){setModeState(await api("/api/auth/session"))}
  function revealModePin(){
    setModeFormError();
    $("#modePinInput").value="";
    $("#modePinDialog").hidden=false;
    requestAnimationFrame(()=>focusModalHeading($("#modePinDialog")));
  }
  function openModePin(){
    if(state.auth.elevated)return;
    const menu=$("#appMenuDialog");
    /* PIN katmanı normal DOM'da, menü ise tarayıcının "top layer"ında bir <dialog>. Z-index
       bu sınırı aşamaz: PIN'i menü açıkken göstermek onu menünün arkasında bırakıyordu.
       Önce menünün gerçek `close` olayını bekleriz; 99-bind'in açan düğmeye odak dönüşü
       tamamlandıktan sonra PIN katmanı açılır ve başlık odağı devralır. */
    if(menu?.open){
      menu.addEventListener("close",revealModePin,{once:true});
      closeAppMenu();
      return;
    }
    revealModePin();
  }
  function closeModePin(granted=false){
    $("#modePinDialog").hidden=true;
    $("#modePinInput").value="";
    setModeFormError();
    settleModeElevation(granted);
  }
  async function submitModePin(event){
    event.preventDefault();
    const button=$("#modePinSubmit");
    setModeFormError();
    button.disabled=true;
    try{
      const data=await api("/api/mode/elevate",{method:"POST",body:JSON.stringify({pin:$("#modePinInput").value})});
      setModeState(data);
      const resumesBlockedAction=pendingModeElevation!==null;
      closeModePin(true);
      showToast(t("adminModeOn"));
      if(!resumesBlockedAction)activateView("adminOverview");
      // Yönetici ekranlarının verisi ev modunda hiç okunmuyor; mod açılınca bir kez getirilir.
      if(!resumesBlockedAction)try{await loadSettings()}catch{}
    }catch(error){setModeFormError(error.message)}
    finally{button.disabled=false}
  }
  async function leaveAdminMode(){
    try{setModeState(await api("/api/mode/leave",{method:"POST",body:"{}"}))}
    catch{dropElevatedFlag()}
    activateView("home");
    showToast(t("homeModeOn"));
  }
  function toggleAdminMode(){
    if(state.auth.elevated)leaveAdminMode();
    else openModePin();
  }
  function openAdminPinSettings(){
    const revealPinSettings=()=>{
      activateView("settings");
      requestAnimationFrame(()=>openSystemDetail(systemDetailButton("accountsCard"),{focusId:"adminPinInput"}));
    };
    const menu=$("#appMenuDialog");
    if(menu?.open){
      menu.addEventListener("close",revealPinSettings,{once:true});
      closeAppMenu();
      return;
    }
    revealPinSettings();
  }
  async function updateAdminPin(event){
    event.preventDefault();
    const input=$("#adminPinInput");
    const confirmation=$("#adminPinConfirm");
    const button=event.currentTarget.querySelector('button[type="submit"]');
    setAdminPinError();
    if(input.value!==confirmation.value){
      setAdminPinError(t("adminPinMismatch"));
      confirmation.focus();
      return;
    }
    button.disabled=true;
    try{
      const data=await api("/api/auth/admin-pin",{method:"PUT",body:JSON.stringify({pin:input.value})});
      input.value="";confirmation.value="";
      setModeState(data);
      showToast(t("adminPinChanged"));
    }catch(error){setAdminPinError(error.message)}
    finally{button.disabled=false}
  }
  function setAdminPinError(message=""){
    const error=$("#adminPinError");
    error.textContent=message;
    error.hidden=!message;
  }
  async function loadInstallationOnboarding(){
    const localComplete=locallyCompletedOnboarding();
    try{
      const data=await api("/api/onboarding");
      installationOnboardingComplete=data.installation?.onboardingComplete===true;
      if(localComplete&&!installationOnboardingComplete){
        const migrated=await api("/api/onboarding",{method:"PUT",body:JSON.stringify({completed:true})});
        installationOnboardingComplete=migrated.installation?.onboardingComplete===true;
      }
    }catch{
      installationOnboardingComplete=localComplete;
    }
  }
  async function markOnboardingComplete(){
    const data=await api("/api/onboarding",{method:"PUT",body:JSON.stringify({completed:true})});
    installationOnboardingComplete=data.installation?.onboardingComplete===true;
    if(!installationOnboardingComplete)throw new Error(t("operationFailed"));
    try{localStorage.setItem(onboardingStorageKey,"true")}catch{}
  }
