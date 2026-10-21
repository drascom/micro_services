  /* GENEL BAKIŞ — yönetici modunun ilk ekranı (not §4.2).

     Yönetici moduna geçen kişinin ilk sorusu "evde durum ne?"dir; bugüne kadar bunun cevabı
     Ayarlar'ın içine, Bağlantılar'ın kartlarına ve Cihazlar listesine dağılmıştı. Bu ekran o
     cevabı TEK yerde toplar: cihaz sayısı · çevrimiçi · otomasyon · düşük pil · servis sağlığı.

     VERİ ÜRETİLMEZ. Ekrandaki her sayı `35-generator.js`in saf modellerinden (`genHomeHealthModel`,
     `genSceneCatalog`) ya da sunucunun kendi sağlık nesnesinden (`state.health`) gelir; burada
     ikinci bir eşik, ikinci bir sayım kuralı TANIMLANMAZ. Bilinmeyen bir şey "iyi" sayılmaz:
     henüz okunmamış servis "bilinmiyor" der (nötr ton), yalan bir yeşil basılmaz.

     TEKNİK TERİM SERBEST. Ev modunun sadeleştirme kuralı (not §11) burada geçmez: bu ekran
     yalnız yönetici modunda çizilir (`data-admin-only` + sunucu her istekte yetkiyi sorar),
     bu yüzden "MQTT", "Zigbee", "Matter" kendi adlarıyla yazılır. */
  const adminOverviewAttentionLimit=8;
  /* `genHealthReasons`in ürettiği sebep adları → çeviri anahtarı. Sebep listesi jeneratörün
     sözleşmesidir; burada yalnız ADLANDIRILIR, yeniden hesaplanmaz. */
  const adminOverviewReasonKeys={
    offline:"adminReasonOffline",
    critical:"adminReasonCritical",
    alert:"adminReasonAlert",
    unsupported:"unsupportedDevice",
    setupIncomplete:"setupIncomplete",
    lowBattery:"adminReasonLowBattery"
  };
  /* Matter durumu ana veri turunda GELMEZ (`/api/overview` onu taşımaz), ayrı uçtan okunur.
     Okuma denendi mi bilinmezse "bilinmiyor" ile "kapalı" ayırt edilemez; bayrak o ayrımı tutar. */
  let adminOverviewMatterProbed=false;

  const adminOverviewTone=value=>value===null?"unknown":(value?"ok":"bad");
  const adminOverviewToneLabel=tone=>t(tone==="ok"?"adminServiceUp":tone==="bad"?"adminServiceDown":"adminServiceUnknown");

  /* Servis satırları. Her satırın cevabı ÜÇ hâlli: açık · kapalı · bilinmiyor.
     `null` "henüz okunmadı" demektir ve asla `false` yerine geçmez. */
  function adminOverviewServices(){
    const health=state.health||null;
    const matterKnown=Boolean(state.matter)||adminOverviewMatterProbed;
    const settings=state.settings||null;
    return[
      {
        key:"adminServiceSource",
        tone:adminOverviewTone(health?health.sourceOnline===true:null),
        note:health&&health.mode?t("adminServiceModeNote",{mode:String(health.mode)}):""
      },
      {
        key:"adminServiceMqtt",
        tone:adminOverviewTone(health?health.mqttConnected===true:null),
        note:health&&health.lastMessageAt?t("adminServiceLastMessage",{time:ago(health.lastMessageAt)}):""
      },
      {
        key:"adminServiceMatter",
        tone:adminOverviewTone(matterKnown?state.matter?.online===true:null),
        note:state.matter?t("adminServiceFabrics",{count:(state.matter.fabrics||[]).length}):""
      },
      ...adminOverviewFailoverRows(health),
      {
        key:"adminServiceHomeAssistant",
        tone:adminOverviewTone(settings?settings.homeAssistant?.discoveryEnabled===true:null),
        note:""
      }
    ];
  }

  /* Failover satırı (tablet-failover-plani.md Faz 5/6). Panel yalnız koordinatörün SAHİBİ
     düğümde açılır; satır sahipliği (epoch) ve karşı düğümün replika tazeliğini gösterir.
     Çalışma zamanı failover yürütmüyorsa (`node.failover` yok) satır hiç çizilmez. */
  function adminOverviewFailoverRows(health){
    const failover=health&&health.node&&health.node.failover;
    if(!failover)return[];
    const parts=[t("adminFailoverEpoch",{state:String(failover.state||"?"),epoch:String(failover.epoch??"?")})];
    let tone=failover.state==="owner"?"ok":"bad";
    if(failover.warning){parts.push(String(failover.warning));tone="bad"}
    for(const peer of Array.isArray(failover.peers)?failover.peers:[]){
      const replication=peer.replication||null;
      if(!replication){parts.push(t("adminFailoverPeerUnknown",{node:String(peer.nodeId||"?")}));continue}
      if(replication.error||replication.halted){
        tone="bad";
        parts.push(t("adminFailoverPeerError",{node:String(peer.nodeId||"?"),error:String(replication.error||"halted")}));
      }else if(replication.replica&&replication.replica.receivedAt){
        parts.push(t("adminFailoverPeerSynced",{node:String(peer.nodeId||"?"),time:ago(new Date(replication.replica.receivedAt).toISOString()),epoch:String(replication.replica.epoch)}));
      }else{
        parts.push(t("adminFailoverPeerNever",{node:String(peer.nodeId||"?")}));
      }
    }
    return[{key:"adminServiceFailover",tone,note:parts.join(" · ")}];
  }

  /* Sayı döşemeleri. Hızlı sahne kataloğu artık yalnız `manual:true` kayıtları içerir; yönetici
     özeti ise elle ve otomatik çalışan bütün kuralları saymalıdır. */
  function adminOverviewStats(health,catalog){
    return[
      {key:"adminStatDevices",value:String(health.deviceCount),note:health.unknown?t("adminStatUnknownNote",{count:health.unknown}):"",tone:"plain"},
      {key:"adminStatOnline",value:`${health.online}/${health.deviceCount}`,note:health.offline?t("offlineDevices",{count:health.offline}):"",tone:health.offline?"bad":"ok"},
      {key:"adminStatAutomations",value:String(catalog.length),note:t("adminStatAutomationsNote",{count:catalog.filter(entry=>entry.enabled).length}),tone:"plain"},
      {key:"adminStatLowBattery",value:String(health.lowBatteryCount),note:health.criticalCount?t("adminStatCriticalNote",{count:health.criticalCount}):"",tone:health.lowBatteryCount?"warn":"ok"}
    ];
  }

  function renderAdminOverview(){
    const statBox=$("#adminOverviewStats");
    if(!statBox)return;
    const health=genHomeHealthModel();
    const catalog=Array.isArray(state.automations)?state.automations:[];
    statBox.innerHTML=adminOverviewStats(health,catalog).map(stat=>`
      <article class="admin-stat" data-tone="${stat.tone}">
        <span class="admin-stat-label">${esc(t(stat.key))}</span>
        <strong class="admin-stat-value">${esc(stat.value)}</strong>
        <span class="admin-stat-note">${esc(stat.note)}</span>
      </article>`).join("");

    const serviceBox=$("#adminServiceList");
    if(serviceBox){
      serviceBox.innerHTML=adminOverviewServices().map(row=>`
        <li class="admin-service-row" data-tone="${row.tone}">
          <span class="admin-service-dot" aria-hidden="true"></span>
          <span class="admin-service-copy"><strong>${esc(t(row.key))}</strong><small>${esc(row.note)}</small></span>
          <span class="admin-service-state">${esc(adminOverviewToneLabel(row.tone))}</span>
        </li>`).join("");
    }

    const attentionBox=$("#adminAttentionList");
    if(attentionBox){
      const rows=health.attention.slice(0,adminOverviewAttentionLimit);
      attentionBox.innerHTML=rows.length
        ?rows.map(item=>{
          const reasons=item.reasons.map(reason=>t(adminOverviewReasonKeys[reason]||"deviceNeedsAttention",{name:item.name})).join(" · ");
          return`<button class="admin-attention-row" type="button" data-admin-attention="${esc(item.deviceId)}">
            <span class="admin-attention-glyph" aria-hidden="true">${deviceIconSvg(item.icon)}</span>
            <span class="admin-attention-copy"><strong>${esc(item.name)}</strong><small>${esc(reasons)}</small></span>
          </button>`;
        }).join("")
        :`<p class="admin-empty">${esc(t("adminAttentionEmpty"))}</p>`;
      const more=$("#adminAttentionMore");
      if(more){
        const hidden=health.attention.length-rows.length;
        more.textContent=hidden>0?t("moreCriticalAlerts",{count:hidden}):"";
        more.hidden=hidden<=0;
      }
      $$("[data-admin-attention]").forEach(button=>{
        button.onclick=()=>showDevice(button.dataset.adminAttention);
      });
    }
  }

  /* Ekrana girişte tazeleme. Üç okuma da başarısız olabilir ve HİÇBİRİ ekranı düşürmez: eldeki
     modelle bir kez çizilir, gelen her cevaptan sonra yeniden çizilir. `loadMatter()` kendi
     hatasını zaten yutar; yine de zincir kırılmasın diye burada da yakalanır. */
  async function loadAdminOverview(){
    renderAdminOverview();
    await Promise.all([
      loadSettings().catch(()=>{}),
      loadAutomations().catch(()=>{}),
      loadMatter().catch(()=>{}).finally(()=>{adminOverviewMatterProbed=true})
    ]);
    renderAdminOverview();
  }
