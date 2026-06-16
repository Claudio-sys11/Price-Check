"""PyInstaller 정적 스플래시(splash.png) 생성 — 테두리 없는 흰 배경 + 로고."""
from PIL import Image, ImageDraw, ImageFont
import version

W, H = 560, 340
GOLD = (201, 162, 39)
MUTED = (170, 180, 177)
HAIRLINE = (231, 237, 235)

img = Image.new("RGB", (W, H), "white")   # 테두리 없는 흰 배경
d = ImageDraw.Draw(img)
# 상단 가운데 골드 악센트(작은 둥근 바)
d.rounded_rectangle([W // 2 - 28, 26, W // 2 + 28, 31], radius=2, fill=GOLD)


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
cy = 120
img.paste(ec, (x, cy - ec_h // 2), ec)
x += ec.width + gap
d.text((x, cy - 16), "×", font=font(22, True), fill=(194, 206, 203))
x += xw + gap
img.paste(wz, (x, cy - wz_h // 2), wz)

# 구분선 + 버전 · 게시자
d.line([(W // 2 - 160, 172), (W // 2 + 160, 172)], fill=HAIRLINE, width=1)
ctext(188, f"VERSION {version.APP_VERSION}", font(10, True), GOLD)
ctext(210, "T H E   F E E L   K O R E A   C O . , L T D .", font(9), MUTED)
ctext(252, "로딩 중…", font(11), (91, 107, 103))

img.save("splash.png")
print("splash.png(white) ->", img.size)
