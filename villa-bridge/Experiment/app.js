(() => {
  "use strict";

  const rooms = [
    {
      id: "salon", name: "Salon", icon: "⌂", temperature: 23, sensors: ["Hava iyi", "Perde %60"],
      devices: [
        { id: "salon-duvar", name: "Duvar ışıkları", type: "Işık", icon: "💡", on: true, detail: "Sıcak beyaz · %72" },
        { id: "salon-kose", name: "Köşe lambası", type: "Işık", icon: "💡", on: false, detail: "Sıcak beyaz · %45" },
        { id: "salon-perde", name: "Salon perdesi", type: "Perde", icon: "▥", on: true, detail: "%60 açık", metric: "%60" },
        { id: "salon-sicaklik", name: "Oda sıcaklığı", type: "Sensör", icon: "🌡", sensor: true, detail: "23° · Nem %48", metric: "23°" }
      ]
    },
    {
      id: "mutfak", name: "Mutfak", icon: "◇", temperature: 22, sensors: ["Pencere kapalı", "2 açık"],
      devices: [
        { id: "mutfak-sol", name: "Tezgâh sol", type: "Işık", icon: "💡", on: true, detail: "Gün ışığı · %88" },
        { id: "mutfak-sag", name: "Tezgâh sağ", type: "Işık", icon: "💡", on: true, detail: "Gün ışığı · %88" },
        { id: "kahve", name: "Kahve makinesi", type: "Priz", icon: "▣", on: false, detail: "Hazır" },
        { id: "mutfak-kapi", name: "Balkon kapısı", type: "Sensör", icon: "▯", sensor: true, detail: "Kapalı", metric: "Kapalı" }
      ]
    },
    {
      id: "yatak", name: "Yatak odası", icon: "▱", temperature: 21, sensors: ["Hava iyi", "Sessiz"],
      devices: [
        { id: "yatak-tavan", name: "Tavan ışığı", type: "Işık", icon: "💡", on: false, detail: "Sıcak beyaz · %35" },
        { id: "yatak-gece", name: "Gece lambası", type: "Işık", icon: "◐", on: false, detail: "Çok sıcak · %15" },
        { id: "yatak-perde", name: "Yatak odası perdesi", type: "Perde", icon: "▥", on: false, detail: "Kapalı", metric: "Kapalı" }
      ]
    },
    {
      id: "banyo", name: "Banyo", icon: "◒", temperature: 24, sensors: ["Nem %56", "1 açık"],
      devices: [
        { id: "banyo-isik", name: "Banyo ışığı", type: "Işık", icon: "💡", on: true, detail: "Gün ışığı · %100" },
        { id: "banyo-nem", name: "Nem sensörü", type: "Sensör", icon: "◉", sensor: true, detail: "Nem normal", metric: "%56" }
      ]
    },
    {
      id: "koridor", name: "Koridor", icon: "↔", temperature: 22, sensors: ["Hareket yok", "Kapalı"],
      devices: [
        { id: "koridor-isik", name: "Koridor ışığı", type: "Işık", icon: "💡", on: false, detail: "Sıcak beyaz · %60" },
        { id: "koridor-hareket", name: "Hareket sensörü", type: "Sensör", icon: "⌁", sensor: true, detail: "Son hareket 8 dk önce", metric: "8 dk" }
      ]
    },
    {
      id: "balkon", name: "Balkon", icon: "☘", temperature: 25, sensors: ["Kapı kapalı", "Kapalı"],
      devices: [
        { id: "balkon-tavan", name: "Balkon tavan", type: "Işık", icon: "💡", on: false, detail: "Sıcak beyaz · %70" },
        { id: "balkon-duvar", name: "Balkon duvar", type: "Işık", icon: "💡", on: false, detail: "Sıcak beyaz · %70" },
        { id: "balkon-kapi", name: "Balkon kapısı", type: "Sensör", icon: "▯", sensor: true, detail: "Kapalı", metric: "Kapalı" }
      ]
    }
  ];

  const routines = [
    { id: "away", name: "Evden çık", icon: "↗", description: "Tüm ışıkları ve seçili prizleri kapatır." },
    { id: "night", name: "Gece", icon: "☾", description: "Alt katı kapatır, gece lambasını %15 açar." },
    { id: "guest", name: "Misafir", icon: "✦", description: "Salon ve balkon ışıklarını sıcak tona getirir." },
    { id: "alloff", name: "Tüm ışıkları kapat", icon: "○", description: "Evin bütün ışıklarını tek seferde kapatır." }
  ];

  const state = {
    mode: "resident",
    view: "home",
    roomId: "salon",
    minutes: 500,
    search: "",
    pendingConfirm: null,
    automationEnabled: { sunrise: true, away: true, nightHall: false, bathFan: true }
  };

  const residentNav = [
    { id: "home", label: "Ev", icon: "⌂" },
    { id: "rooms", label: "Odalar", icon: "▦" },
    { id: "routines", label: "Rutinler", icon: "✦" }
  ];
  const adminNav = [
    { id: "admin-overview", label: "Genel", icon: "⌂" },
    { id: "admin-devices", label: "Cihazlar", icon: "▣" },
    { id: "admin-automations", label: "Otomasyon", icon: "⌁" },
    { id: "admin-connections", label: "Bağlantı", icon: "∞" },
    { id: "admin-system", label: "Sistem", icon: "⚙" }
  ];

  const viewRoot = document.getElementById("viewRoot");
  const railNav = document.getElementById("railNav");
  const mobileNav = document.getElementById("mobileNav");
  const pageTitle = document.getElementById("pageTitle");
  const pageEyebrow = document.getElementById("pageEyebrow");
  const backButton = document.getElementById("backButton");
  const profileDrawer = document.getElementById("profileDrawer");
  const scrim = document.getElementById("scrim");
  const detailDialog = document.getElementById("detailDialog");
  const confirmDialog = document.getElementById("confirmDialog");
  const toast = document.getElementById("toast");
  let toastTimer = null;

  const allDevices = () => rooms.flatMap(room => room.devices.map(device => ({ ...device, roomId: room.id, roomName: room.name })));
  const getRoom = id => rooms.find(room => room.id === id) || rooms[0];
  const getDevice = id => {
    for (const room of rooms) {
      const device = room.devices.find(item => item.id === id);
      if (device) return { device, room };
    }
    return null;
  };
  const activeDevices = room => room.devices.filter(device => !device.sensor && device.on);
  const lightDevices = room => room.devices.filter(device => !device.sensor);

  function pad(value) { return String(value).padStart(2, "0"); }
  function timeText(minutes, offset = 0) {
    const total = (minutes + offset + 1440) % 1440;
    return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
  }

  function mixColor(a, b, amount) {
    const parse = value => value.match(/[a-f\d]{2}/gi).map(part => parseInt(part, 16));
    const aa = parse(a); const bb = parse(b);
    return `#${aa.map((value, index) => Math.round(value + (bb[index] - value) * amount).toString(16).padStart(2, "0")).join("")}`;
  }

  const skyStops = [
    { at: 0, top: "#071225", mid: "#132746", bottom: "#24384e" },
    { at: 300, top: "#142543", mid: "#3d526d", bottom: "#b16e69" },
    { at: 390, top: "#648db0", mid: "#d0b4b0", bottom: "#f2bf80" },
    { at: 540, top: "#66a6d2", mid: "#a9d2e6", bottom: "#e9dfc8" },
    { at: 840, top: "#4b92c2", mid: "#96c7e0", bottom: "#dbe6dc" },
    { at: 1080, top: "#5d799a", mid: "#c7887e", bottom: "#efae70" },
    { at: 1200, top: "#172744", mid: "#5b5267", bottom: "#a35e61" },
    { at: 1320, top: "#081529", mid: "#192c48", bottom: "#2b394c" },
    { at: 1440, top: "#071225", mid: "#132746", bottom: "#24384e" }
  ];

  function updateSky() {
    const minutes = state.minutes;
    const rightIndex = skyStops.findIndex(stop => stop.at >= minutes);
    const right = skyStops[Math.max(1, rightIndex)];
    const left = skyStops[Math.max(0, rightIndex - 1)];
    const amount = (minutes - left.at) / Math.max(1, right.at - left.at);
    const root = document.documentElement;
    root.style.setProperty("--sky-top", mixColor(left.top, right.top, amount));
    root.style.setProperty("--sky-mid", mixColor(left.mid, right.mid, amount));
    root.style.setProperty("--sky-bottom", mixColor(left.bottom, right.bottom, amount));

    const dayStart = 360; const dayEnd = 1200;
    const isDay = minutes >= dayStart && minutes <= dayEnd;
    const dayProgress = Math.max(0, Math.min(1, (minutes - dayStart) / (dayEnd - dayStart)));
    const nightMinutes = minutes > dayEnd ? minutes - dayEnd : minutes + (1440 - dayEnd);
    const nightProgress = Math.max(0, Math.min(1, nightMinutes / (1440 - dayEnd + dayStart)));
    root.style.setProperty("--celestial-x", `${8 + dayProgress * 84}%`);
    root.style.setProperty("--celestial-y", `${72 - Math.sin(dayProgress * Math.PI) * 58}%`);
    root.style.setProperty("--moon-x", `${8 + nightProgress * 84}%`);
    root.style.setProperty("--moon-y", `${68 - Math.sin(nightProgress * Math.PI) * 52}%`);
    root.style.setProperty("--sun-opacity", isDay ? "1" : "0");
    root.style.setProperty("--moon-opacity", isDay ? "0" : ".9");
    const stars = isDay ? 0 : Math.min(.9, Math.max(.2, Math.abs(minutes - 780) / 620));
    root.style.setProperty("--stars-opacity", String(stars));
    document.body.dataset.tone = minutes < 375 || minutes > 1160 ? "night" : "day";
    document.querySelector('meta[name="theme-color"]').content = mixColor(left.top, right.top, amount);

    updateLiveClock();
  }

  function weatherForTime() {
    if (state.minutes < 390 || state.minutes > 1180) return { icon: "🌙", temp: "19°", copy: "Açık gece" };
    if (state.minutes < 620) return { icon: "🌤️", temp: "21°", copy: "Hafif bulutlu" };
    if (state.minutes < 980) return { icon: "☀️", temp: "25°", copy: "Açık" };
    return { icon: "🌥️", temp: "23°", copy: "Parçalı bulutlu" };
  }

  function updateLiveClock() {
    const weather = weatherForTime();
    document.querySelectorAll("[data-live-time]").forEach(node => { node.textContent = timeText(state.minutes); });
    document.querySelectorAll("[data-weather-icon]").forEach(node => { node.textContent = weather.icon; });
    document.querySelectorAll("[data-weather-temp]").forEach(node => { node.textContent = weather.temp; });
    document.querySelectorAll("[data-weather-copy]").forEach(node => { node.textContent = weather.copy; });
    document.querySelectorAll("[data-london-time]").forEach(node => { node.textContent = `Londra ${timeText(state.minutes, -120)}`; });
    document.querySelectorAll("[data-istanbul-time]").forEach(node => { node.textContent = `İstanbul ${timeText(state.minutes, 0)}`; });
  }

  function setHeading(title, eyebrow, showBack = false) {
    pageTitle.textContent = title;
    pageEyebrow.textContent = eyebrow;
    backButton.hidden = !showBack;
  }

  function navButton(item) {
    const active = state.view === item.id || (item.id === "rooms" && state.view === "room");
    return `<button class="rail-button${active ? " active" : ""}" type="button" data-go="${item.id}"${active ? ' aria-current="page"' : ""}><span class="rail-icon">${item.icon}</span><span class="rail-label">${item.label}</span></button>`;
  }

  function renderNav() {
    const items = state.mode === "admin" ? adminNav : residentNav;
    const html = items.map(navButton).join("");
    railNav.innerHTML = html;
    mobileNav.innerHTML = `${html}<button class="rail-button" type="button" data-action="profile"><span class="rail-icon">●</span><span class="rail-label">Profil</span></button>`;
  }

  function roomCard(room) {
    const active = activeDevices(room).length;
    const controllable = lightDevices(room).length;
    return `<article class="room-card">
      <div class="room-card-head">
        <button class="room-card-main" type="button" data-room="${room.id}"><span class="room-icon">${room.icon}</span><span><h3>${room.name}</h3><p>${active ? `${active} cihaz açık` : "Her şey kapalı"} · ${controllable} kontrol</p></span></button>
        <span class="room-temp">${room.temperature}°</span>
      </div>
      <div class="room-card-foot"><span class="room-sensors"><span>${room.sensors[0]}</span></span><button class="master-switch${active ? " on" : ""}" type="button" data-room-master="${room.id}">${active ? "Kapat" : "Tümünü aç"}</button></div>
    </article>`;
  }

  function sceneCard(routine) {
    return `<button class="scene-card" type="button" data-scene="${routine.id}"><span class="scene-icon">${routine.icon}</span><strong>${routine.name}</strong></button>`;
  }

  function renderHome() {
    setHeading("Evim", "Günlük kontrol");
    viewRoot.innerHTML = `<div class="view-page"><div class="section-head"><div><h2>Hızlı sahneler</h2><p>Bir dokunuşla evin havasını değiştirin.</p></div><button class="text-button" type="button" data-go="routines">Tümü</button></div>
      <section class="scene-grid">${routines.map(sceneCard).join("")}</section>
      <div class="section-head"><div><h2>Odalar</h2><p>Önce odayı bulun, sonra ne yapmak istediğinizi seçin.</p></div><button class="text-button" type="button" data-go="rooms">Tüm odalar</button></div>
      <section class="room-grid">${rooms.slice(0, 6).map(roomCard).join("")}</section>
    </div>`;
    updateLiveClock();
  }

  function renderRooms() {
    setHeading("Odalar", "Eviniz");
    const active = rooms.reduce((total, room) => total + activeDevices(room).length, 0);
    viewRoot.innerHTML = `<div class="view-page">
      <section class="page-intro-card"><div><strong>Evin tamamı tek bakışta</strong><p>Bir odaya dokunarak ayrıntıları açabilirsiniz.</p></div><div class="intro-stat"><strong>${active}</strong><small>cihaz açık</small></div></section>
      <section class="room-grid">${rooms.map(roomCard).join("")}</section>
    </div>`;
  }

  function deviceCard(device) {
    const stateLabel = device.sensor ? device.metric || "Hazır" : device.on ? "Açık" : "Kapalı";
    return `<article class="device-card${device.offline ? " offline" : ""}">
      <div class="device-card-top"><span class="device-icon">${device.icon}</span>${device.sensor ? `<span class="device-metric">${stateLabel}</span>` : `<button class="device-state-button${device.on ? " on" : ""}" type="button" data-device-toggle="${device.id}">${stateLabel}</button>`}</div>
      <h3>${device.name}</h3><p>${device.detail}</p>
      <div class="device-card-foot"><span class="device-metric">${device.type}</span><button class="device-detail-link" type="button" data-device="${device.id}">Ayrıntılar ›</button></div>
    </article>`;
  }

  function renderRoom() {
    const room = getRoom(state.roomId);
    const active = activeDevices(room).length;
    setHeading(room.name, "Oda kontrolü", true);
    viewRoot.innerHTML = `<div class="view-page">
      <section class="room-detail-hero"><div class="room-detail-copy"><span class="room-icon">${room.icon}</span><div><h2>${room.name}</h2><p>${room.temperature}° · ${room.sensors.join(" · ")}</p></div></div><button class="large-switch${active ? " on" : ""}" type="button" data-room-master="${room.id}">${active ? "Odadaki ışıkları kapat" : "Odadaki ışıkları aç"}</button></section>
      <div class="section-head"><div><h2>Cihazlar</h2><p>${room.devices.length} cihaz bu odada.</p></div></div>
      <section class="device-grid">${room.devices.map(deviceCard).join("")}</section>
    </div>`;
  }

  function renderRoutines() {
    setHeading("Rutinler", "Tek dokunuş");
    viewRoot.innerHTML = `<div class="view-page">
      <section class="page-intro-card"><div><strong>Hazır ev anları</strong><p>Rutinleri burada çalıştırabilirsiniz. Düzenleme yalnız yönetici modundadır.</p></div><div class="intro-stat"><strong>${routines.length}</strong><small>hazır rutin</small></div></section>
      <section class="routine-grid">${routines.map(item => `<article class="routine-card"><span class="routine-icon">${item.icon}</span><div><h3>${item.name}</h3><p>${item.description}</p></div><button class="run-button" type="button" data-scene="${item.id}">Çalıştır</button></article>`).join("")}</section>
    </div>`;
  }

  function adminBanner() {
    return `<div class="admin-banner"><div><strong>Yönetici modu</strong><span>Buradaki değişiklikler yalnızca görsel prototipte uygulanır.</span></div><button type="button" data-action="exit-admin">Ev moduna dön</button></div>`;
  }

  function renderAdminOverview() {
    setHeading("Genel Bakış", "Yönetici merkezi");
    viewRoot.innerHTML = `<div class="view-page">${adminBanner()}
      <section class="metric-grid">
        <article class="metric-card"><span>Toplam cihaz</span><strong>${allDevices().length}</strong><small>6 odada düzenli</small></article>
        <article class="metric-card"><span>Çevrimiçi</span><strong>${allDevices().length - 1}</strong><small>Son kontrol şimdi</small></article>
        <article class="metric-card"><span>Otomasyon</span><strong>4</strong><small>3 etkin · 1 beklemede</small></article>
        <article class="metric-card"><span>Düşük pil</span><strong>1</strong><small>Balkon kapı sensörü</small></article>
      </section>
      <div class="admin-columns">
        <article class="panel-card"><h2>Servisler</h2><p>Bağlantıların örnek sağlık görünümü.</p><div class="status-list">
          <div class="status-row"><div><span class="status-dot"></span><span><strong>Villa Bridge</strong><small>Çalışıyor · 12 gündür açık</small></span></div><span class="status-badge">Sağlıklı</span></div>
          <div class="status-row"><div><span class="status-dot"></span><span><strong>Zigbee ağı</strong><small>18 cihaz · Kanal 20</small></span></div><span class="status-badge">Bağlı</span></div>
          <div class="status-row"><div><span class="status-dot"></span><span><strong>Home Assistant</strong><small>Otomatik keşif etkin</small></span></div><span class="status-badge">Bağlı</span></div>
          <div class="status-row"><div><span>●</span><span><strong>Balkon sensörü</strong><small>Pil seviyesi %12</small></span></div><span class="status-badge warn">İlgilenin</span></div>
        </div></article>
        <article class="panel-card"><h2>Son hareketler</h2><p>Evde ve yönetimde yakın zamanda olanlar.</p><div class="activity-list">
          <div class="activity-item"><span>💡</span><span><strong>Mutfak ışıkları açıldı</strong><small>Duvar düğmesi</small></span><time>2 dk</time></div>
          <div class="activity-item"><span>⌁</span><span><strong>Gece rutini güncellendi</strong><small>Yönetici</small></span><time>1 sa</time></div>
          <div class="activity-item"><span>▣</span><span><strong>Tüm cihazlar yedeklendi</strong><small>Otomatik yedek</small></span><time>Dün</time></div>
        </div></article>
      </div>
    </div>`;
  }

  function renderAdminDevices() {
    setHeading("Cihazlar", "Yönetici merkezi");
    const query = state.search.trim().toLocaleLowerCase("tr");
    const devices = allDevices().filter(item => `${item.name} ${item.roomName} ${item.type}`.toLocaleLowerCase("tr").includes(query));
    viewRoot.innerHTML = `<div class="view-page">${adminBanner()}
      <div class="toolbar"><input class="search-box" id="deviceSearch" type="search" value="${state.search}" placeholder="Cihaz veya oda ara…" aria-label="Cihaz ara"><button class="button" type="button" data-action="mock-add-device">＋ Cihaz ekle</button></div>
      <div class="data-table"><div class="table-row table-head"><span>Cihaz</span><span>Oda</span><span>Tür</span><span>Durum</span><span></span></div>${devices.map(item => `<div class="table-row"><div class="device-name-cell"><span class="device-icon">${item.icon}</span><span><strong>${item.name}</strong><small>${item.id}</small></span></div><span>${item.roomName}</span><span>${item.type}</span><span class="status-badge${item.on || item.sensor ? "" : " warn"}">${item.sensor ? item.metric || "Hazır" : item.on ? "Açık" : "Kapalı"}</span><button class="row-action" type="button" data-device="${item.id}" aria-label="${item.name} ayrıntıları">›</button></div>`).join("") || `<div class="table-row"><strong>Sonuç bulunamadı</strong></div>`}</div>
    </div>`;
    const search = document.getElementById("deviceSearch");
    search?.addEventListener("input", event => { state.search = event.target.value; renderAdminDevices(); document.getElementById("deviceSearch")?.focus(); });
  }

  const automations = [
    { id: "sunrise", icon: "☀", name: "Gün doğumunda perdeleri aç", detail: "Her gün · Gün doğumundan 15 dakika sonra" },
    { id: "away", icon: "↗", name: "Evden çıkınca kapat", detail: "Evden Çık sahnesi çalıştığında · 8 eylem" },
    { id: "nightHall", icon: "☾", name: "Gece koridor ışığı", detail: "23:30–06:30 · Hareket algılanırsa" },
    { id: "bathFan", icon: "◉", name: "Banyo nem uyarısı", detail: "Nem %70 üzerine çıkarsa · Bildirim" }
  ];

  function renderAdminAutomations() {
    setHeading("Otomasyonlar", "Yönetici merkezi");
    viewRoot.innerHTML = `<div class="view-page">${adminBanner()}
      <div class="toolbar"><div><strong>Ev kendi kendine çalışsın</strong><p style="margin:4px 0 0;color:var(--text-soft);font-size:12px">Kuralları yönetin veya yeni bir akış oluşturun.</p></div><button class="button" type="button" data-action="mock-new-automation">＋ Yeni otomasyon</button></div>
      <section class="automation-list">${automations.map(item => `<article class="automation-row"><span class="routine-icon">${item.icon}</span><div><h3>${item.name}</h3><p>${item.detail}</p></div><button class="switch${state.automationEnabled[item.id] ? " on" : ""}" type="button" data-automation-toggle="${item.id}" aria-label="${item.name} etkinliğini değiştir"></button><button class="row-action" type="button" data-action="mock-edit-automation" aria-label="Düzenle">›</button></article>`).join("")}</section>
    </div>`;
  }

  function renderAdminConnections() {
    setHeading("Bağlantılar", "Yönetici merkezi");
    const cards = [
      { icon: "⌁", title: "Zigbee", text: "Koordinatör ve evdeki Zigbee cihazlarının bağlantısı.", facts: [["Koordinatör", "Ağ üzerinden"], ["Kanal", "20"], ["Cihaz", "18 bağlı"]], action: "Bağlantıyı incele" },
      { icon: "◇", title: "Matter", text: "Villa Bridge cihazlarını Alexa veya Apple Home ile paylaşın.", facts: [["Durum", "Yayımlanıyor"], ["Ev sistemi", "2 bağlı"], ["Cihaz", "14 paylaşılıyor"]], action: "Yeni sistem bağla" },
      { icon: "⌂", title: "Home Assistant", text: "MQTT üzerinden otomatik keşif ve durum eşitleme.", facts: [["Keşif", "Etkin"], ["Broker", "Bağlı"], ["Son eşitleme", "Şimdi"]], action: "Ayarları göster" }
    ];
    viewRoot.innerHTML = `<div class="view-page">${adminBanner()}<section class="connection-grid">${cards.map(card => `<article class="connection-card"><span class="connection-logo">${card.icon}</span><h2>${card.title}</h2><p>${card.text}</p><div class="connection-facts">${card.facts.map(fact => `<div class="connection-fact"><span>${fact[0]}</span><strong>${fact[1]}</strong></div>`).join("")}</div><button class="button secondary" type="button" data-action="mock-connection">${card.action}</button></article>`).join("")}</section></div>`;
  }

  function renderAdminSystem() {
    setHeading("Sistem", "Yönetici merkezi");
    const items = [
      { icon: "●", title: "Kullanıcılar", text: "Ev kullanıcısı PIN’i ve yönetici parolası.", action: "Erişimi yönet" },
      { icon: "☀", title: "Görünüm ve gökyüzü", text: "Ortak panelin teması, renkleri ve canlı gökyüzü.", action: "Görünümü düzenle" },
      { icon: "▭", title: "Duvar tableti", text: "Parlaklık, ekran koruyucu ve gece davranışı.", action: "Tablet ayarları" },
      { icon: "▣", title: "Yedekleme", text: "Odalar, otomasyonlar ve Zigbee ağı için son yedek: bugün 03:00.", action: "Yedekleri göster" },
      { icon: "↻", title: "Servis bakımı", text: "Villa Bridge ve koordinatörü güvenli biçimde yeniden başlatın.", action: "Bakım seçenekleri", danger: true },
      { icon: "⚠", title: "Gelişmiş araçlar", text: "Ağ haritası, Zigbee grupları ve tanılama bilgileri.", action: "Gelişmiş araçları aç" }
    ];
    viewRoot.innerHTML = `<div class="view-page">${adminBanner()}<section class="settings-grid">${items.map(item => `<article class="settings-card${item.danger ? " danger-zone" : ""}"><div class="settings-head"><span class="settings-icon">${item.icon}</span><div><h2>${item.title}</h2><p>${item.text}</p></div></div><button class="settings-action" type="button" data-action="${item.danger ? "risky-action" : "mock-setting"}" data-label="${item.title}">${item.action} ›</button></article>`).join("")}</section></div>`;
  }

  function render() {
    document.body.dataset.mode = state.mode;
    renderNav();
    const renderers = {
      home: renderHome,
      rooms: renderRooms,
      room: renderRoom,
      routines: renderRoutines,
      "admin-overview": renderAdminOverview,
      "admin-devices": renderAdminDevices,
      "admin-automations": renderAdminAutomations,
      "admin-connections": renderAdminConnections,
      "admin-system": renderAdminSystem
    };
    (renderers[state.view] || renderHome)();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function go(view) {
    if (state.mode === "resident" && view.startsWith("admin-")) return;
    if (state.mode === "admin" && ["home", "rooms", "routines"].includes(view)) {
      state.mode = "resident";
      updateProfileCopy();
    }
    state.view = view;
    render();
  }

  function toggleRoom(roomId) {
    const room = getRoom(roomId);
    const shouldTurnOn = activeDevices(room).length === 0;
    room.devices.forEach(device => { if (!device.sensor) device.on = shouldTurnOn; });
    showToast(`${room.name}: ${shouldTurnOn ? "ışıklar açıldı" : "ışıklar kapatıldı"}.`);
    render();
  }

  function runScene(id) {
    if (id === "away" || id === "alloff") rooms.forEach(room => room.devices.forEach(device => { if (!device.sensor) device.on = false; }));
    if (id === "night") {
      rooms.forEach(room => room.devices.forEach(device => { if (!device.sensor) device.on = false; }));
      const nightLight = getDevice("yatak-gece"); if (nightLight) nightLight.device.on = true;
    }
    if (id === "guest") {
      ["salon-duvar", "salon-kose", "balkon-tavan", "balkon-duvar"].forEach(deviceId => { const found = getDevice(deviceId); if (found) found.device.on = true; });
    }
    const routine = routines.find(item => item.id === id);
    showToast(`“${routine?.name || "Rutin"}” çalıştırıldı.`);
    render();
  }

  function showToast(message) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("show");
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
  }

  function openDrawer() {
    profileDrawer.classList.add("open");
    profileDrawer.setAttribute("aria-hidden", "false");
    scrim.hidden = false;
  }
  function closeDrawer() {
    profileDrawer.classList.remove("open");
    profileDrawer.setAttribute("aria-hidden", "true");
    scrim.hidden = true;
  }

  function updateProfileCopy() {
    const admin = state.mode === "admin";
    document.getElementById("profileRole").textContent = admin ? "Yönetici" : "Ev kullanıcısı";
    document.getElementById("adminEntryTitle").textContent = admin ? "Ev moduna dön" : "Yönetici moduna geç";
    document.getElementById("adminEntryLead").textContent = admin ? "Günlük kontrollere geri dönün" : "Cihazlar, bağlantılar ve sistem";
  }

  function toggleAdmin() {
    state.mode = state.mode === "admin" ? "resident" : "admin";
    state.view = state.mode === "admin" ? "admin-overview" : "home";
    updateProfileCopy();
    closeDrawer();
    showToast(state.mode === "admin" ? "Yönetici modu açıldı — demo." : "Ev moduna dönüldü.");
    render();
  }

  function openInfoDialog(kind) {
    const weather = weatherForTime();
    document.getElementById("dialogBody").innerHTML = kind === "clock"
      ? `<div class="dialog-hero"><span class="device-icon">◷</span><div><p class="eyebrow">Dünya saatleri</p><h2>Saat ve alarm</h2><p>Ortak duvar panelindeki zaman bilgileri.</p></div></div><div class="dialog-section"><div class="status-list"><div class="status-row"><strong>Antalya</strong><b>${timeText(state.minutes)}</b></div><div class="status-row"><strong>Londra</strong><b>${timeText(state.minutes, -120)}</b></div><div class="status-row"><strong>New York</strong><b>${timeText(state.minutes, -420)}</b></div></div></div><div class="dialog-section"><h3>Sonraki alarm</h3><div class="status-row"><div><span>⏰</span><span><strong>Sabah alarmı</strong><small>Hafta içi</small></span></div><b>07:30</b></div></div>`
      : `<div class="dialog-hero"><span class="device-icon">${weather.icon}</span><div><p class="eyebrow">Antalya</p><h2>${weather.temp} · ${weather.copy}</h2><p>Bugün 27° / 19° · Yağmur olasılığı %10</p></div></div><div class="dialog-section"><h3>Saatlik tahmin</h3><div class="status-list"><div class="status-row"><strong>Şimdi</strong><b>${weather.temp}</b></div><div class="status-row"><strong>2 saat sonra</strong><b>24°</b></div><div class="status-row"><strong>Akşam</strong><b>21°</b></div></div></div><div class="dialog-section"><h3>Yarın</h3><div class="status-row"><span>Parçalı bulutlu</span><b>26° / 18°</b></div></div>`;
    if (!detailDialog.open) detailDialog.showModal();
  }

  function openDeviceDialog(deviceId) {
    const found = getDevice(deviceId);
    if (!found) return;
    const { device, room } = found;
    document.getElementById("dialogBody").innerHTML = `<div class="dialog-hero"><span class="device-icon">${device.icon}</span><div><p class="eyebrow">${room.name} · ${device.type}</p><h2>${device.name}</h2><p>${device.detail}</p></div></div>
      <div class="dialog-section"><h3>Hızlı kontrol</h3>${device.sensor ? `<div class="status-row"><span>Güncel değer</span><b>${device.metric || device.detail}</b></div>` : `<div class="control-row"><span>Açık / kapalı</span><button class="switch${device.on ? " on" : ""}" type="button" data-device-toggle="${device.id}" aria-label="Cihaz durumunu değiştir"></button></div><div class="control-row"><span>Parlaklık</span><input type="range" min="1" max="100" value="72" aria-label="Parlaklık"></div>`}</div>
      <div class="dialog-section"><h3>Cihaz bilgisi</h3><div class="status-list"><div class="status-row"><span>Oda</span><b>${room.name}</b></div><div class="status-row"><span>Bağlantı</span><span class="status-badge">Çevrimiçi</span></div><div class="status-row"><span>Pil / enerji</span><b>${device.sensor ? "%84" : "Şebeke"}</b></div></div></div>`;
    if (!detailDialog.open) detailDialog.showModal();
  }

  function requestConfirm(title, text) {
    state.pendingConfirm = { title, text };
    document.getElementById("confirmTitle").textContent = title;
    document.getElementById("confirmText").textContent = text;
    confirmDialog.showModal();
  }

  document.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.go) { go(button.dataset.go); return; }
    if (button.dataset.room) { state.roomId = button.dataset.room; go("room"); return; }
    if (button.dataset.roomMaster) { toggleRoom(button.dataset.roomMaster); return; }
    if (button.dataset.scene) { runScene(button.dataset.scene); return; }
    if (button.dataset.deviceToggle) {
      const found = getDevice(button.dataset.deviceToggle);
      if (found && !found.device.sensor) {
        found.device.on = !found.device.on;
        showToast(`${found.device.name} ${found.device.on ? "açıldı" : "kapatıldı"}.`);
        if (detailDialog.open) openDeviceDialog(found.device.id); else render();
      }
      return;
    }
    if (button.dataset.device) { openDeviceDialog(button.dataset.device); return; }
    if (button.dataset.automationToggle) {
      const id = button.dataset.automationToggle;
      state.automationEnabled[id] = !state.automationEnabled[id];
      showToast(`Otomasyon ${state.automationEnabled[id] ? "etkinleştirildi" : "duraklatıldı"}.`);
      renderAdminAutomations();
      return;
    }

    const action = button.dataset.action;
    if (!action) return;
    if (action === "profile") openDrawer();
    if (action === "close-drawer") closeDrawer();
    if (action === "toggle-admin" || action === "exit-admin") toggleAdmin();
    if (action === "back") go("rooms");
    if (action === "clock-detail") openInfoDialog("clock");
    if (action === "weather-detail") openInfoDialog("weather");
    if (action === "close-dialog") detailDialog.close();
    if (action === "cancel-confirm") { state.pendingConfirm = null; confirmDialog.close(); }
    if (action === "accept-confirm") {
      const title = state.pendingConfirm?.title || "İşlem";
      state.pendingConfirm = null;
      confirmDialog.close();
      showToast(`${title}: prototipte tamamlandı.`);
    }
    if (action === "risky-action") requestConfirm(button.dataset.label || "Bakım işlemi", "Gerçek Villa Bridge sisteminde bu işlem servisleri kısa süreliğine durdurabilir. Bu prototipte hiçbir gerçek işlem yapılmayacak.");
    if (["mock-add-device", "mock-new-automation", "mock-edit-automation", "mock-connection", "mock-setting"].includes(action)) showToast("Bu alan görsel prototipte örnek olarak gösteriliyor.");
  });

  scrim.addEventListener("click", closeDrawer);
  document.getElementById("brightnessSlider").addEventListener("input", event => {
    const value = Number(event.target.value);
    document.getElementById("brightnessValue").textContent = `${value}%`;
    document.documentElement.style.setProperty("--screen-dim", String((100 - value) / 170));
  });
  detailDialog.addEventListener("click", event => { if (event.target === detailDialog) detailDialog.close(); });

  setInterval(() => {
    state.minutes = (state.minutes + 6) % 1440;
    updateSky();
  }, 350);

  updateProfileCopy();
  render();
  updateSky();
})();
