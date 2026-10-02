/* proverbs.js - yukleme modalinda gosterilen Turk atasozleri + tekrarsiz rastgele secici. ES2017. */
(function (g) {
  'use strict';
  var DZ = g.DZ = g.DZ || {};

  var LIST = [
    'Damlaya damlaya göl olur.',
    'Sabreden derviş muradına ermiş.',
    'Ağaç yaşken eğilir.',
    'Bir elin nesi var, iki elin sesi var.',
    'Ak akçe kara gün içindir.',
    'Aç ayı oynamaz.',
    'Acele işe şeytan karışır.',
    'Ateş düştüğü yeri yakar.',
    'Az veren candan, çok veren maldan.',
    'Bugünün işini yarına bırakma.',
    'Bin bilsen de bir bilene danış.',
    'Bir fincan kahvenin kırk yıllık hatırı vardır.',
    'Birlikten kuvvet doğar.',
    'Dost acı söyler.',
    'Dost kara günde belli olur.',
    'El elin eşeğini türkü çağırarak arar.',
    'Gülü seven dikenine katlanır.',
    'Gözden ırak olan gönülden de ırak olur.',
    'Görünen köy kılavuz istemez.',
    'Her yiğidin bir yoğurt yiyişi vardır.',
    'İki gönül bir olunca samanlık seyran olur.',
    'İnsan yedisinde neyse yetmişinde de odur.',
    'İyilik yap denize at, balık bilmezse Halik bilir.',
    'Komşu komşunun külüne muhtaçtır.',
    'Körle yatan şaşı kalkar.',
    'Laf ile peynir gemisi yürümez.',
    'Misafir umduğunu değil, bulduğunu yer.',
    'Mum dibine ışık vermez.',
    'Sakla samanı, gelir zamanı.',
    'Sütten ağzı yanan yoğurdu üfleyerek yer.',
    'Taş yerinde ağırdır.',
    'Tatlı dil yılanı deliğinden çıkarır.',
    'Ummadığın taş baş yarar.',
    'Üzüm üzüme baka baka kararır.',
    'Vakit nakittir.',
    'Yalancının mumu yatsıya kadar yanar.',
    'Yarım hekim candan, yarım hoca dinden eder.',
    'Zararın neresinden dönülse kârdır.',
    'Ağlamayan çocuğa meme vermezler.',
    'Anasını gör, kızını al.',
    'Armut piş, ağzıma düş.',
    'Atı alan Üsküdar\'ı geçti.',
    'Balık baştan kokar.',
    'Bakarsan bağ olur, bakmazsan dağ olur.',
    'Bir çiçekle bahar olmaz.',
    'Çivi çiviyi söker.',
    'Demir tavında dövülür.',
    'Dilin kemiği yok.',
    'Doğru söyleyeni dokuz köyden kovarlar.',
    'Eğri oturalım, doğru konuşalım.',
    'El eli yıkar, iki el yüzü yıkar.',
    'Emek olmadan yemek olmaz.',
    'Fazla mal göz çıkarmaz.',
    'Gelen gideni aratır.',
    'Gülme komşuna, gelir başına.',
    'Havlayan köpek ısırmaz.',
    'Her horoz kendi çöplüğünde öter.',
    'İşleyen demir ışıldar.',
    'İt ürür, kervan yürür.',
    'Keskin sirke küpüne zarar.',
    'Komşunun tavuğu komşuya kaz görünür.',
    'Ne ekersen onu biçersin.',
    'Nerede birlik, orada dirlik.',
    'Rüzgâr eken fırtına biçer.',
    'Söz gümüşse sükût altındır.',
    'Sürüden ayrılanı kurt kapar.',
    'Tencere yuvarlanmış, kapağını bulmuş.',
    'Terzi kendi söküğünü dikemez.',
    'Yağmurdan kaçarken doluya tutulmak.',
    'Ayağını yorganına göre uzat.',
    'Öfkeyle kalkan zararla oturur.',
    'Bal tutan parmağını yalar.',
    'Kedi uzanamadığı ciğere pis der.',
    'Ateş olmayan yerden duman çıkmaz.',
    'Zaman her şeyin ilacıdır.',
    'Kervan yolda düzülür.',
    'Sel gider, kum kalır.',
    'Su akar, yolunu bulur.',
    'Damdan düşen halinden bilir.',
    'Bir musibet bin nasihatten yeğdir.',
    'Çok konuşan çok yanılır.',
    'Düşenin dostu olmaz.',
    'Acıkan doymam sanır, doyan acıkmam sanır.',
    'Gemisini kurtaran kaptan.',
    'Hamama giren terler.',
    'Mart kapıdan baktırır, kazma kürek yaktırır.',
    'Ne verirsen elinle, o gider seninle.',
    'Pireye kızıp yorgan yakmak.',
    'Sabır acıdır, meyvesi tatlıdır.',
    'Kaz gelen yerden tavuk esirgenmez.',
    'Yolcu yolunda gerek.',
    'Yiğidi öldür, hakkını yeme.',
    'Zenginin malı züğürdün çenesini yorar.',
    'Kaşıkla verdiğini kepçeyle alır.'
  ];

  /* Fisher-Yates; rng() [0,1) dondurmeli (varsayilan Math.random). Girdiyi bozmaz. */
  function shuffle(list, rng) {
    var r = typeof rng === 'function' ? rng : Math.random;
    var out = Array.isArray(list) ? list.slice() : [];
    for (var i = out.length - 1; i > 0; i--) {
      var j = Math.floor(r() * (i + 1));
      if (j > i) j = i;
      if (j < 0) j = 0;
      var t = out[i]; out[i] = out[j]; out[j] = t;
    }
    return out;
  }

  /* Tekrarsiz rastgele secici: bir tur bitmeden hicbir atasozu tekrar gelmez; tur
     bitince yeniden karistirilir ve yeni turun ilki bir onceki turun sonuncusu olmaz.
     Bos/gecersiz listede coker degil, '' dondurur. */
  function createPicker(list, rng) {
    var source = Array.isArray(list) ? list : LIST;
    var queue = [];
    var last = null;
    function refill() {
      queue = shuffle(source, rng);
      if (queue.length > 1 && queue[queue.length - 1] === last) {
        var t = queue[0]; queue[0] = queue[queue.length - 1]; queue[queue.length - 1] = t;
      }
    }
    return {
      next: function () {
        if (!source.length) return '';
        if (!queue.length) refill();
        last = queue.pop();
        return last;
      },
      size: function () { return source.length; }
    };
  }

  DZ.proverbs = { list: LIST, shuffle: shuffle, createPicker: createPicker };
})(window);
