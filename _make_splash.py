"""PyInstaller 정적 스플래시(splash.png) 생성 — 프리미엄 카드 + EcountERP·Wizfasta 로고."""
from PIL import Image, ImageDraw, ImageFont
import version

W, H = 560, 340
ACCENT = (20, 184, 166)
HEADER_DARK = (11, 92, 84)
GOLD = (201, 162, 39)
MUTED = (170, 180, 177)
ACCENT_DK = (13, 148, 136)
HAIRLINE = (231, 237, 235)

# 배경: 딥틸→민트 가로 그라데이션
img = Image.new("RGB", (W, H), ACCENT)
d = ImageDraw.Draw(img)
for x in range(W):
    t = x / (W - 1)
    d.line([(x, 0), (x, H)], fill=(
        int(HEADER_DARK[0] + (ACCENT[0] - HEADER_DARK[0]) * t),
        int(HEADER_DARK[1] + (ACCENT[1] - HEADER_DARK[1]) * t),
        int(HEADER_DARK[2] + (ACCENT[2] - HEADER_DARK[2]) * t)))

# 둥근 흰색 카드
m = 8
d.rounded_rectangle([m, m, W - m, H - m], radius=32, fill="white", outline=HAIRLINE, width=1)
# 상단 골드 악센트
d.rounded_rectangle([W // 2 - 28, m + 16, W // 2 + 28, m + 21], radius=2, fill=GOLD)


def font(sz, bold=False):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/" + ("malgunbd.ttf" if bold else "malgun.ttf"), sz)
    except Exception:
        return ImageFont.load_default()


def ctext(y, txt, fnt, fill):
    w = d.textlength(txt, font=fnt)
    d.text(((W - w) / 2, y), txt, font=fnt, fill=fill)


# 로고
ec = Image.open("assets/ecount_logo.png").convert("RGBA")
ec_h = 48
ec = ec.resize((round(ec.width * ec_h / ec.height), ec_h), Image.LANCZOS)
wz = Image.open("assets/wizfasta_logo_light.png").convert("RGBA")
wz_h = 54
wz = wz.resize((round(wz.width * wz_h / wz.height), wz_h), Image.LANCZOS)

gap = 24
xw = int(d.textlength("×", font=font(22, True)))
total = ec.width + gap + xw + gap + wz.width
x = (W - total) // 2
cy = 116
img.paste(ec, (x, cy - ec_h // 2), ec)
x += ec.width + gap
d.text((x, cy - 16), "×", font=font(22, True), fill=(194, 206, 203))
x += xw + gap
img.paste(wz, (x, cy - wz_h // 2), wz)

# 구분선 + 버전 · 게시자
d.line([(W // 2 - 160, 168), (W // 2 + 160, 168)], fill=HAIRLINE, width=1)
ctext(184, f"VERSION {version.APP_VERSION}", font(10, True), GOLD)
ctext(206, "T H E   F E E L   K O R E A   C O . , L T D .", font(9), MUTED)
ctext(248, "로딩 중…", font(11), (91, 107, 103))

img.save("splash.png")
print("splash.png(premium) ->", img.size)
