"""The admin question of a stage that needs a human, in plain Turkish: what the system did, what it could not find (per failed
check, with the measured value) and what the admin can show / do. Replaces the bare ``check=value (>=bound)`` line."""
from __future__ import annotations

from typing import Optional

# check name -> (what could not be found, {value}/{bound} filled in; what the admin can do)
CHECKS: dict[str, tuple[str, str]] = {
    "sections_valid_count": ("Ana sayfada kullanılabilir kart bölümü bulamadım (geçerli bölüm: {value}, en az {bound} gerekiyor). "
                             "Bir bölümün geçerli sayılması için yeterli kart, her kartta adres + başlık ve kart adreslerinin "
                             "tek kalıpta olması gerekiyor.",
                             "Ana sayfada dizi/film kartlarının durduğu bir bölümün kapsayıcısını göster (ör. 'Son eklenen "
                             "diziler div.populer içinde, her kart article.item') ya da bir kartın adresini örnek ver."),
    "target_one_cluster": ("Bulduğum bölümlerden en az birinde kart adresleri karışık: aynı bölümde hem dizi sayfası hem bölüm "
                           "sayfası (ya da başka türde) bağlantılar var, bu yüzden bölümün neyi gösterdiğine karar veremedim.",
                           "Karışık bölümün kartları neye gidiyor söyle (ör. 'Yeni bölümler bölümündeki kartlar bölüm "
                           "sayfasına gider') ya da o bölümü yok saymamı iste."),
    "section_cards_min": ("Bazı bölümlerde çok az kart buldum (en az: {value}, gereken: {bound}).",
                          "Kartların seçicisini ipucu olarak yaz ya da az kartlı bölümü atlamamı söyle."),
    "url_fill": ("Kartların bir kısmında adres okuyamadım (doluluk {value}, gereken {bound}).",
                 "Kartta bağlantının durduğu öğeyi göster (ör. 'adres a.poster'ın href'inde')."),
    "title_fill": ("Kartların bir kısmında başlık okuyamadım (doluluk {value}, gereken {bound}).",
                   "Başlığın durduğu öğeyi göster (ör. 'başlık h2.title içinde' ya da 'img alt özniteliğinde')."),
    "poster_fill": ("Kartların bir kısmında afiş bulamadım (doluluk {value}, gereken {bound}).",
                    "Afişin durduğu öğeyi göster (ör. 'afiş img'nin data-src özniteliğinde') ya da afiş alanını atla."),
    "samples_count": ("Ölçecek yeterli örnek sayfa bulamadım ({value}, en az {bound}).",
                      "Bu aşama için 1-2 örnek adres ver."),
    "valid_count": ("Liste sayfasında yeterli satır okuyamadım ({value}, en az {bound}).",
                    "Liste sayfasında bir dizi/film satırının kapsayıcısını göster ya da doğru liste adresini ver."),
    "pagination_ok": ("Listenin 2. sayfasına geçişi bulamadım.",
                      "Sonraki sayfa bağlantısını göster (ör. 'a.next') ya da listenin tek sayfa olduğunu söyle."),
    "key_ok_ratio": ("Başlık sayfası adreslerinden kalıcı bir anahtar çıkaramadım (oran {value}).",
                     "Bir dizi/film sayfası adresi ver ve adresin hangi kısmının dizinin adı olduğunu söyle."),
    "type_ok": ("Sayfanın dizi mi film mi olduğuna karar veremedim.",
                "Dizi ve film adreslerinin nasıl ayrıldığını söyle (ör. '/diziler/... dizi, /film/... film')."),
    "series_samples": ("Bölüm listesi olan yeterli dizi bulamadım ({value}, en az {bound}).", "Bölümleri listelenen bir dizi sayfası ver."),
    "episodes_ok_ratio": ("Bazı dizi sayfalarında bölüm listesini bulamadım (oran {value}).",
                          "Bölüm listesinin kapsayıcısını göster (ör. 'bölüm listesi div.bolumler a')."),
    "numbers_ok": ("Bölüm adreslerinden sezon/bölüm numarasını çıkaramadım.", "Bir bölüm adresi ver ve numaranın nerede yazdığını söyle."),
    "candidate_ratio": ("Bazı bölüm/film sayfalarında oynatıcı bulamadım (oran {value}).",
                        "Oynatıcının ya da kaynak düğmelerinin durduğu öğeyi göster (ör. 'oynatıcı #player iframe', "
                        "'kaynaklar ul.sources li, tıklayınca yükleniyor')."),
    "reached_samples": ("Oynatıcı adayı buldum ama hiçbir örnekte zincir sitenin kendi sayfasından video sağlayıcısına varmadı.",
                        "Oynayan bir bölüm adresi ver; oynatıcı ara sayfadaysa nasıl açıldığını söyle."),
    "episode_pages_ok": ("Ürettiğim bölüm adresleri açılmadı (404 / dizi sayfasına döndü).",
                         "Açılan bir bölüm adresi ver; adres birleştirme hatası olabilir."),
    "title_link_ok": ("Bölüm sayfasından dizinin kendi sayfasına giden bağlantıyı bulamadım.",
                      "Bölüm sayfasında diziye dönen bağlantıyı göster (ör. 'a.back')."),
    "playable_ratio": ("Örneklerin çoğunda video oynatamadım (oran {value}, gereken {bound}).",
                       "Oynayan bir bölüm adresi ver; kaynak bir sekmenin/düğmenin arkasındaysa onu göster."),
    "alt_playable_ratio": ("Hiçbir kaynak alternatifi örneklerin 2/3'ünde oynamadı (en iyisi {value}).",
                           "Hangi kaynak (sekme/sunucu) sende oynuyor söyle; o alternatifi öne alırım."),
    "search_ok": ("Arama sonucunda bilinen bir başlığı bulamadım.", "Arama adresini ver (ör. '/?s=kelime')."),
    "llm_reply_invalid": ("Önerimi (LLM yanıtı) kullanamadım: biçimi bozuktu.", "Aşamayı yeniden dene ya da bir ipucu yaz."),
    "map_valid": ("Önerdiğim kural haritada geçersizdi.", "Aşamayı yeniden dene ya da bir ipucu yaz."),
}


def _fmt(text: str, check: dict) -> str:
    def num(v):
        return round(v, 2) if isinstance(v, float) else v
    return text.replace("{value}", str(num(check.get("value")))).replace("{bound}", str(num(check.get("bound"))))


MAX_URLS = 3      # addresses listed under "Baktığım sayfalar" (the rest: "+N daha")
BULLET = " • "
TITLE = "Yardım gerekiyor"


def sections(evidence: Optional[dict], checks: list, notes: Optional[list] = None, *, extra: str = "") -> list[dict]:
    """The question as ``[{title, items: [{text, lines?}]}]``: Ne yaptım / Bulamadığım / Ayrıntı / Senden istediğim (empty
    sections left out; ``lines`` = one address per line under an item)."""
    ev = evidence or {}
    did: list[dict] = []
    if ev.get("looked_for"):
        did.append({"text": f"Aradığım: {ev['looked_for']}"})
    tried = [u for u in ev.get("tried") or [] if isinstance(u, str)]
    if tried:
        lines = tried[:MAX_URLS] + ([f"+{len(tried) - MAX_URLS} daha"] if len(tried) > MAX_URLS else [])
        did.append({"text": f"Baktığım sayfalar ({len(tried)}):", "lines": lines})
    if ev.get("summary"):
        did.append({"text": f"Sonuç: {ev['summary']}"})
    failed = [c for c in checks or [] if not c.get("ok") and not c.get("optional")]
    missing, asks = [], []
    for c in failed[:4]:
        known = CHECKS.get(c.get("name") or "")
        if known:
            missing.append({"text": _fmt(known[0], c)})
            if known[1] not in asks:
                asks.append(known[1])
        else:
            missing.append({"text": f"'{c.get('name')}' ölçümü geçmedi (değer {c.get('value')}, eşik {c.get('bound')})."})
    detail = [{"text": str(n)} for n in notes or [] if n][:2]
    if extra:
        detail.append({"text": extra})
    want = [{"text": a} for a in asks] or [{"text": "Bir ipucu yaz ya da örnek adres ver."}]
    want.append({"text": "İpucu kutusuna yazıp yeniden dene."})
    out = [{"title": "Ne yaptım", "items": did}, {"title": "Bulamadığım", "items": missing},
           {"title": "Ayrıntı", "items": detail}, {"title": "Senden istediğim", "items": want}]
    return [sec for sec in out if sec["items"]]


def plain(secs: list[dict]) -> str:
    """The sections as plain text, one heading per line, items as `` • `` lines, an item's addresses indented below it
    (the message history keeps this text; the admin parses it back into the same sections)."""
    lines = [TITLE]
    for sec in secs:
        lines.append(sec["title"])
        for item in sec["items"]:
            lines.append(BULLET + item["text"])
            lines += ["    " + extra for extra in item.get("lines") or []]
    return "\n".join(lines)


def question(evidence: Optional[dict], checks: list, notes: Optional[list] = None, *, extra: str = "") -> str:
    """The plain-text question (``Yardım gerekiyor`` + one heading per line + `` • `` items): see :func:`sections`."""
    return plain(sections(evidence, checks, notes, extra=extra))
