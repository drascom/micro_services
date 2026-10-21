# DiziFlix marka gorselleri: docs/brand/logo-a.png + logo-b.png -> tizen-client/img/*.png + tizen-client/icon.png
#                                                                -> android/app/src/main/res/** (ikon, TV afisi, uygulama ici logolar)
# Calistir (repo kokunden, Pillow gerekir):  Q=1 server/venv/bin/python docs/brand/make_brand.py [all|tizen|android]   (Q=1: 256 renk paletli, < 150 KB)
#   tizen   -> yalniz Tizen ciktilari      android -> yalniz Android ciktilari (Tizen dosyalarina DOKUNMAZ)      all (varsayilan) -> ikisi
import os, sys
from PIL import Image, ImageDraw, ImageChops, ImageFilter

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
OUT = ROOT + '/tizen-client/img'
A = Image.open(ROOT + '/docs/brand/logo-a.png').convert('RGBA')
B = Image.open(ROOT + '/docs/brand/logo-b.png').convert('RGBA')

def trim(im, thr=10, pad=4):
    a = im.split()[3].point(lambda v: 255 if v > thr else 0)
    bb = a.getbbox()
    l, t, r, b = bb
    l = max(0, l - pad); t = max(0, t - pad); r = min(im.width, r + pad); b = min(im.height, b + pad)
    return im.crop((l, t, r, b))

def fade_bottom(im, fade):
    W, H = im.size
    m = Image.new('L', (W, H), 255)
    d = ImageDraw.Draw(m)
    for i in range(fade):
        t = i / fade; t = t * t * (3 - 2 * t)
        d.line([(0, H - 1 - i), (W, H - 1 - i)], fill=int(255 * t))
    im = im.copy(); im.putalpha(ImageChops.multiply(im.split()[3], m))
    return im

def resize_w(im, w):
    return im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)

def resize_h(im, h):
    return im.resize((round(im.width * h / im.height), h), Image.LANCZOS)

def save(im, name, quant=False):
    p = os.path.join(OUT, name) if not name.startswith('/') else name
    if quant:
        im = im.quantize(colors=256, method=Image.FASTOCTREE, dither=Image.FLOYDSTEINBERG)
    im.save(p, optimize=True)
    print(name, im.size, round(os.path.getsize(p) / 1024, 1), 'KB')

# --- karakter (yazisiz): LOGO_A, yazi basladigi satirdan once kes + alt kenari yumusat
mascot_full = fade_bottom(A.crop((100, 80, 1170, 838)), 44)
mascot = trim(mascot_full)

variant = sys.argv[1] if len(sys.argv) > 1 else 'all'
W_WIDE = int(os.environ.get('W_WIDE', 720))
H_SQ = int(os.environ.get('H_SQ', 640))
Q = os.environ.get('Q', '0') == '1'

if variant in ('all', 'tizen'):
    # tam logolar
    save(resize_w(trim(B), W_WIDE), 'logo-wide.png', Q)
    save(resize_h(trim(A), H_SQ), 'logo-square.png', Q)
    # karakter: modal (120px -> 240 ast)
    save(resize_h(mascot, 240), 'mascot.png', Q)
    # kucuk amblem (ayni karakter; topbar 48px, ayarlar ~32px -> 96px yukseklik ast)
    save(resize_h(mascot, 96), 'mascot-sm.png', Q)

    # uygulama ikonu 512x512 (opak, sicak koyu zemin + yumusak isik) ve favicon 128
    S = 512
    bg = Image.new('RGB', (S, S), (22, 14, 9))
    glow = Image.new('L', (S, S), 0)
    ImageDraw.Draw(glow).ellipse((S*0.08, S*0.12, S*0.92, S*0.92), fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    warm = Image.new('RGB', (S, S), (92, 58, 20))
    bg = Image.composite(warm, bg, glow).convert('RGBA')
    ch = resize_w(mascot, int(S * 0.94))
    x = (S - ch.width) // 2
    y = (S - ch.height) // 2 + 6
    bg.alpha_composite(ch, (x, y))
    icon = bg.convert('RGB')
    icon = icon.quantize(colors=256, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG) if Q else icon
    icon.save(ROOT + '/tizen-client/icon.png', optimize=True)
    print('icon.png', icon.size, round(os.path.getsize(ROOT + '/tizen-client/icon.png') / 1024, 1), 'KB')
    fav = icon.convert('RGB').resize((128, 128), Image.LANCZOS)
    fav.save(OUT + '/favicon.png', optimize=True)
    print('favicon.png', fav.size, round(os.path.getsize(OUT + '/favicon.png') / 1024, 1), 'KB')


# =====================================================================================================================
# ANDROID (android/app/src/main/res): uygulama (launcher) ikonu, TV afisi, uygulama ici logolar. Yoksayilan: Tizen ciktilari.
# =====================================================================================================================
if variant in ('all', 'android'):
    RES = ROOT + '/android/app/src/main/res'
    DBG = os.environ.get('ANDROID_PREVIEW')          # doluysa onizleme PNG'leri bu klasore de yazilir

    def asave(im, rel, quant=True):
        p = os.path.join(RES, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if quant:
            im = im.quantize(colors=256, method=Image.FASTOCTREE, dither=Image.FLOYDSTEINBERG) if im.mode == 'RGBA' else \
                im.convert('RGB').quantize(colors=256, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
        im.save(p, optimize=True)
        print(rel, im.size, round(os.path.getsize(p) / 1024, 1), 'KB')
        if DBG:
            im.convert('RGBA').save(os.path.join(DBG, rel.replace('/', '_')))

    def warm_bg(w, h):
        """Tizen icon.png zemini: sicak koyu kahve + yumusak isik (S=512 icin ayni sabitler, olcege gore)."""
        S = max(w, h)
        bg = Image.new('RGB', (w, h), (22, 14, 9))
        glow = Image.new('L', (w, h), 0)
        ImageDraw.Draw(glow).ellipse((w * 0.08, h * 0.12, w * 0.92, h * 0.92), fill=150)
        glow = glow.filter(ImageFilter.GaussianBlur(70 * S / 512))
        warm = Image.new('RGB', (w, h), (92, 58, 20))
        return Image.composite(warm, bg, glow).convert('RGBA')

    def place(canvas, im, cx, cy):
        canvas.alpha_composite(im, (round(cx - im.width / 2), round(cy - im.height / 2)))

    # --- adaptive ikon on plani: 432x432 (108 dp @ xxxhdpi), seffaf; karakter guvenli bolgede (66 dp = %61 cap)
    FG = 432
    fg_char = resize_w(mascot, round(FG * 0.60))
    fg = Image.new('RGBA', (FG, FG), (0, 0, 0, 0))
    place(fg, fg_char, FG / 2, FG / 2 + FG * 0.015)
    asave(fg, 'drawable-nodpi/ic_launcher_foreground.png')

    # --- tema ikonu (monochrome, Android 13+): parlak (altin) alanlar opak, koyu cizgi/goz seffaf -> ayrintilar okunur
    lum = fg.convert('L').point(lambda v: 255 if v > 150 else int(max(0, (v - 70)) * 255 / 80))
    mono = Image.new('RGBA', (FG, FG), (0, 0, 0, 0))
    mono_a = ImageChops.multiply(fg.split()[3], lum)
    mono.putalpha(mono_a)
    asave(mono, 'drawable-nodpi/ic_launcher_monochrome.png')

    # --- eski API ikonlari (adaptive oncesi yedek): kare yuvarlatilmis + yuvarlak, mdpi..xxxhdpi
    def legacy(size, round_icon):
        N = 512
        bg = warm_bg(N, N)
        char = resize_w(mascot, round(N * (0.74 if round_icon else 0.90)))
        place(bg, char, N / 2, N / 2 + 6)
        m = Image.new('L', (N, N), 0)
        d = ImageDraw.Draw(m)
        if round_icon:
            d.ellipse((0, 0, N - 1, N - 1), fill=255)
        else:
            d.rounded_rectangle((0, 0, N - 1, N - 1), radius=int(N * 0.18), fill=255)
        bg.putalpha(m)
        return bg.resize((size, size), Image.LANCZOS)

    for dens, px in (('mdpi', 48), ('hdpi', 72), ('xhdpi', 96), ('xxhdpi', 144), ('xxxhdpi', 192)):
        asave(legacy(px, False), 'mipmap-%s/ic_launcher.png' % dens, quant=False)
        asave(legacy(px, True), 'mipmap-%s/ic_launcher_round.png' % dens, quant=False)

    # --- TV afisi (Android TV launcher): 320x180 dp, yazili tam logo (LOGO_B) sicak koyu zeminde
    def banner(w, h):
        bg = warm_bg(w, h)
        logo = resize_h(trim(B), round(h * 0.90))
        place(bg, logo, w / 2, h / 2)
        return bg.convert('RGB')

    asave(banner(640, 360), 'drawable-xhdpi/tv_banner.png')
    asave(banner(960, 540), 'drawable-xxhdpi/tv_banner.png')

    # --- uygulama ici logolar (drawable-nodpi; Tizen ile ayni gorsel/olcu): profil (genis), yukleme (kare), hata/yukleme (karakter), ust menu (amblem)
    asave(resize_w(trim(B), W_WIDE), 'drawable-nodpi/brand_logo_wide.png')
    asave(resize_h(trim(A), H_SQ), 'drawable-nodpi/brand_logo_square.png')
    asave(resize_h(mascot, 240), 'drawable-nodpi/brand_mascot.png')
    asave(resize_h(mascot, 96), 'drawable-nodpi/brand_mascot_sm.png')
