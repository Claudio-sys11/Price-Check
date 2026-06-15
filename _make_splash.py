"""PyInstaller 정적 스플래시(splash.png) 생성 — EcountERP·Wizfasta 로고 배치."""
from PIL import Image, ImageDraw, ImageFont
import version

W, H = 520, 300
ACCENT = (20, 184, 166)
DARKCHIP = (43, 47, 58)
MUTED = (107, 114, 128)
ACCENT_DK = (13, 148, 136)

img = Image.new("RGBA", (W, H), ACCENT + (255,))
d = ImageDraw.Draw(img)
d.rounded_rectangle([12, 12, W - 12, H - 12], radius=18, fill="white")
d.rounded_rectangle([12, 12, W - 12, 34], radius=10, fill=ACCENT)
d.rectangle([12, 24, W - 12, 34], fill=ACCENT)


def font(sz, bold=False):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/" + ("malgunbd.ttf" if bold else "malgun.ttf"), sz)
    except Exception:
        return ImageFont.load_default()


def ctext(y, txt, fnt, fill):
    w = d.textlength(txt, font=fnt)
    d.text(((W - w) / 2, y), txt, font=fnt, fill=fill)


# 로고 로드/리사이즈 (둘 다 흰 배경 위에 배치)
ec = Image.open("assets/ecount_logo.png").convert("RGBA")
ec_h = 46
ec = ec.resize((round(ec.width * ec_h / ec.height), ec_h), Image.LANCZOS)
wz = Image.open("assets/wizfasta_logo_light.png").convert("RGBA")   # 흰 배경용(리컬러)
wz_h = 52
wz = wz.resize((round(wz.width * wz_h / wz.height), wz_h), Image.LANCZOS)

# 가로 배치 계산: [ec] (16) × (16) [wz]
gap = 16
x_mark_w = int(d.textlength("×", font=font(22, True)))
total = ec.width + gap + x_mark_w + gap + wz.width
x = (W - total) // 2
row_cy = 96

img.alpha_composite(ec, (x, row_cy - ec_h // 2))
x += ec.width + gap
d.text((x, row_cy - 16), "×", font=font(22, True), fill=ACCENT)
x += x_mark_w + gap
img.alpha_composite(wz, (x, row_cy - wz.height // 2))

ctext(150, f"v{version.APP_VERSION}  ·  THE FEEL KOREA CO.,LTD.", font(12), MUTED)
ctext(196, "로딩 중…", font(13), ACCENT_DK)

img.convert("RGB").save("splash.png")
print("splash.png composited ->", img.size)
