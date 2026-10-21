  /* TV / KUMANDA GEZİNMESİ
     Tarayıcının deneysel spatial-navigation desteğine bağlı değiliz. Görünür ve odaklanabilir
     öğelerin ekrandaki gerçek dikdörtgenleri ölçülür; dört yön tuşu aynı satır/sütundaki en yakın
     adaya gider. Diyalog açıksa arka sayfa aday değildir. Dokunma, fare ve Tab akışı değişmez. */
  const tvNavSelector=[
    "button:not([disabled])","a[href]","input:not([disabled]):not([type=hidden])",
    "select:not([disabled])","textarea:not([disabled])","summary",
    "[role=button]","[role=switch]","[role=slider]","[role=tab]",
    "[tabindex]:not([tabindex=\"-1\"])"
  ].join(",");
  const tvNavDirections=new Map([
    ["ArrowLeft","left"],["Left","left"],["ArrowRight","right"],["Right","right"],
    ["ArrowUp","up"],["Up","up"],["ArrowDown","down"],["Down","down"]
  ]);
  const tvNavKeyCodes=new Map([[37,"left"],[38,"up"],[39,"right"],[40,"down"]]);
  const tvNavBackKeys=new Set(["Escape","Esc","Back","BrowserBack","GoBack"]);
  const tvNavBackCodes=new Set([8,27,461,10009]);
  let tvNavFocused=null;

  const tvNavElement=target=>target instanceof Element?target:null;
  const tvNavEditable=target=>Boolean(target?.matches?.("textarea,[contenteditable=true],[contenteditable=plaintext-only],input:not([type]),input[type=text],input[type=search],input[type=email],input[type=tel],input[type=url],input[type=password],input[type=number]"));
  const tvNavOwnsArrows=(target,direction)=>{
    if(!target)return false;
    if(target.closest?.("[role=tablist]"))return true;
    if(target.matches?.("textarea,[contenteditable=true],[contenteditable=plaintext-only]"))return true;
    if(target.closest?.("[role=listbox],.time-picker-wheels"))return direction==="up"||direction==="down";
    if(target.matches?.("select"))return direction==="up"||direction==="down";
    if(target.matches?.("[role=slider]"))return direction==="up"||direction==="down";
    if(target.matches?.("input[type=range]"))return direction==="left"||direction==="right";
    if(target.matches?.("input[type=radio],input[type=number]"))return true;
    if(tvNavEditable(target))return direction==="left"||direction==="right";
    return false;
  };
  const tvNavVisible=element=>{
    if(!element?.isConnected||element.closest("[hidden],[inert],[aria-hidden=true]"))return false;
    if(element.matches("[disabled],[aria-disabled=true]"))return false;
    const style=getComputedStyle(element);
    if(style.display==="none"||style.visibility==="hidden"||Number(style.opacity)===0)return false;
    const rect=element.getBoundingClientRect();
    return rect.width>1&&rect.height>1;
  };
  const tvNavTopDialog=()=>{
    const focused=document.activeElement?.closest?.("dialog[open]");
    if(focused)return focused;
    const dialogs=Array.from(document.querySelectorAll("dialog[open]")).filter(tvNavVisible);
    return dialogs[dialogs.length-1]||null;
  };
  const tvNavScope=()=>tvNavTopDialog()
    ||(document.querySelector("#coachLayer:not([hidden])")?document.querySelector("#coachTooltip"):null)
    ||document;
  const tvNavCandidates=root=>Array.from(root.querySelectorAll(tvNavSelector)).filter((element,index,all)=>{
    if(!tvNavVisible(element)||element.getAttribute("tabindex")==="-1")return false;
    // Aynı öğe birden çok seçiciye uysa bile tek adaydır. İç içe adaylar bilerek korunur:
    // cihaz kartından kartın aç/kapa düğmesine sağ tuşuyla geçilebilmesi gerekir.
    return all.indexOf(element)===index;
  });
  const tvNavCenter=rect=>({x:rect.left+rect.width/2,y:rect.top+rect.height/2});
  const tvNavAxisGap=(startA,endA,startB,endB)=>endA<startB?startB-endA:endB<startA?startA-endB:0;
  const tvNavDirectional=(source,candidate,direction)=>{
    const from=tvNavCenter(source);
    const to=tvNavCenter(candidate);
    if(direction==="left")return to.x<from.x-2;
    if(direction==="right")return to.x>from.x+2;
    if(direction==="up")return to.y<from.y-2;
    return to.y>from.y+2;
  };
  const tvNavScore=(source,candidate,direction)=>{
    const from=tvNavCenter(source);
    const to=tvNavCenter(candidate);
    const horizontal=direction==="left"||direction==="right";
    const primary=horizontal?Math.abs(to.x-from.x):Math.abs(to.y-from.y);
    const cross=horizontal?Math.abs(to.y-from.y):Math.abs(to.x-from.x);
    const crossGap=horizontal
      ?tvNavAxisGap(source.top,source.bottom,candidate.top,candidate.bottom)
      :tvNavAxisGap(source.left,source.right,candidate.left,candidate.right);
    // Aynı görsel satır/sütun önce gelir. Hizalı olmayan adaylarda çapraz uzaklık belirgin biçimde
    // cezalandırılır; bu, iki boyutlu kart ızgarasında odağın çapraz sıçramasını önler.
    return primary+cross*.18+crossGap*3.4+(crossGap>0?240:0);
  };
  const tvNavEntry=(candidates,direction)=>{
    const horizontal=direction==="left"||direction==="right";
    const sign=direction==="left"||direction==="up"?-1:1;
    return [...candidates].sort((a,b)=>{
      const ac=tvNavCenter(a.getBoundingClientRect());
      const bc=tvNavCenter(b.getBoundingClientRect());
      const primary=(horizontal?ac.x-bc.x:ac.y-bc.y)*sign;
      if(Math.abs(primary)>1)return primary;
      return horizontal?ac.y-bc.y:ac.x-bc.x;
    })[0]||null;
  };
  const tvNavMarkFocus=element=>{
    if(tvNavFocused&&tvNavFocused!==element)tvNavFocused.classList.remove("tv-spatial-focus");
    tvNavFocused=element;
    if(!element)return;
    document.body.classList.add("tv-navigation-active");
    element.classList.add("tv-spatial-focus");
  };
  const tvNavFocus=element=>{
    if(!element)return;
    try{element.focus({preventScroll:true})}catch{element.focus()}
    tvNavMarkFocus(element);
    try{element.scrollIntoView({block:"nearest",inline:"nearest"})}catch{element.scrollIntoView()}
  };
  const tvNavMove=direction=>{
    const root=tvNavScope();
    const candidates=tvNavCandidates(root);
    if(!candidates.length)return false;
    const active=tvNavElement(document.activeElement);
    if(!active||!root.contains(active)||!tvNavVisible(active)){
      tvNavFocus(tvNavEntry(candidates,direction));
      return true;
    }
    const source=active.getBoundingClientRect();
    const possible=candidates
      .filter(candidate=>candidate!==active&&!candidate.contains(active))
      .filter(candidate=>tvNavDirectional(source,candidate.getBoundingClientRect(),direction))
      .map(candidate=>({candidate,score:tvNavScore(source,candidate.getBoundingClientRect(),direction)}))
      .sort((left,right)=>left.score-right.score);
    if(!possible.length)return false;
    tvNavFocus(possible[0].candidate);
    return true;
  };
  const tvNavCancelDialog=dialog=>{
    if(!dialog?.open)return false;
    const event=new Event("cancel",{bubbles:false,cancelable:true});
    if(dialog.dispatchEvent(event))dialog.close();
    return true;
  };
  const tvNavBack=()=>{
    const compactHelp=document.querySelector(".compact-help-button[aria-expanded=true]");
    if(compactHelp&&typeof closeCompactHelp==="function"){
      closeCompactHelp();
      tvNavFocus(compactHelp);
      return true;
    }
    const dialog=tvNavTopDialog();
    if(dialog?.id==="automationDialog"&&typeof stepBackAutomation==="function"){stepBackAutomation();return true}
    if(dialog?.id==="simpleLinkDialog"&&typeof stepBackSimpleLink==="function"){stepBackSimpleLink();return true}
    if(dialog?.id==="onboardingDialog"){
      const back=document.querySelector("#onboardingBack");
      if(tvNavVisible(back))back.click();
      return true;
    }
    if(dialog)return tvNavCancelDialog(dialog);
    const coach=document.querySelector("#coachLayer:not([hidden])");
    if(coach){
      const back=document.querySelector("#coachBack");
      (tvNavVisible(back)?back:document.querySelector("#coachSkip"))?.click();
      return true;
    }
    const view=document.body.dataset.activeView;
    if(view&&view!=="home"&&typeof activateView==="function"){
      activateView("home");
      requestAnimationFrame(()=>tvNavFocus(document.querySelector("#home [data-app-menu]")));
      return true;
    }
    // Ana ekranda tarayıcının geçmişe gidip uygulamadan çıkmasını engelleriz. Menüye geçiş için
    // ekrandaki belirgin menü düğmesi kullanılmaya devam eder.
    return view==="home";
  };
  const tvNavActivate=target=>{
    if(!target||target.matches("button,a[href],select,textarea,summary,input:not([type=checkbox]):not([type=radio])"))return false;
    if(target.matches("input[type=checkbox],input[type=radio]")||target.matches("[role=button],[role=switch],[role=slider],[tabindex]")){
      target.click();
      return true;
    }
    return false;
  };
  const tvNavKeyDirection=event=>tvNavDirections.get(event.key)||tvNavKeyCodes.get(Number(event.keyCode||event.which))||null;
  const tvNavIsBack=event=>tvNavBackKeys.has(event.key)||tvNavBackCodes.has(Number(event.keyCode||event.which));
  const tvNavIsEnter=event=>event.key==="Enter"||event.key==="OK"||Number(event.keyCode||event.which)===13;

  document.addEventListener("keydown",event=>{
    // Ekran koruyucunun kendi "her tuşla uyan" davranışı önceliklidir; odağı görünmeyen ana
    // sayfaya taşımayız ve olay koruyucuya kadar normal biçimde yayılır.
    if(document.querySelector("#screensaver:not([hidden])"))return;
    if(event.key==="Tab"){
      document.body.classList.add("tv-navigation-active");
      return;
    }
    const target=tvNavElement(document.activeElement)||tvNavElement(event.target);
    if(tvNavIsBack(event)){
      if(tvNavEditable(target)&&Number(event.keyCode||event.which)===8&&event.key!=="Escape")return;
      if(event.repeat)return;
      if(tvNavBack()){
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      return;
    }
    const direction=tvNavKeyDirection(event);
    if(direction){
      if(tvNavOwnsArrows(target,direction))return;
      document.body.classList.add("tv-navigation-active");
      if(tvNavMove(direction)){
        event.preventDefault();
        event.stopPropagation();
      }
      return;
    }
    if(tvNavIsEnter(event)&&!event.repeat&&tvNavActivate(target)){
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  },true);
  document.addEventListener("focusin",event=>{
    if(document.body.classList.contains("tv-navigation-active"))tvNavMarkFocus(tvNavElement(event.target));
  });
  document.addEventListener("pointerdown",()=>{
    document.body.classList.remove("tv-navigation-active");
    if(tvNavFocused)tvNavFocused.classList.remove("tv-spatial-focus");
    tvNavFocused=null;
  },{capture:true,passive:true});
