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


# 로고 로드/리사이즈
ec = Image.open("assets/ecount_logo.png").convert("RGBA")
ec_h = 46
ec = ec.resize((round(ec.width * ec_h / ec.height), ec_h), Image.LANCZOS)
wz = Image.open("assets/wizfasta_logo.png").convert("RGBA")
wz_h = 50
wz = wz.resize((round(wz.width * wz_h / wz.height), wz_h), Image.LANCZOS)

# 가로 배치 계산: [ec] (16) × (16) [chip]
gap = 16
chip_w, chip_h = wz.width + 34, wz.height + 20
x_mark_w = int(d.textlength("×", font=font(22, True)))
total = ec.width + gap + x_mark_w + gap + chip_w
x = (W - total) // 2
row_cy = 96

# EcountERP 로고
img.alpha_composite(ec, (x, row_cy - ec_h // 2))
x += ec.width + gap
# × 표시
d.text((x, row_cy - 16), "×", font=font(22, True), fill=ACCENT)
x += x_mark_w + gap
# Wizfasta: 어두운 라운드 칩 + 로고
chip = Image.new("RGBA", (chip_w, chip_h), (0, 0, 0, 0))
cd = ImageDraw.Draw(chip)
cd.rounded_rectangle([0, 0, chip_w - 1, chip_h - 1], radius=14, fill=DARKCHIP)
chip.alpha_composite(wz, ((chip_w - wz.width) // 2, (chip_h - wz.height) // 2))
img.alpha_composite(chip, (x, row_cy - chip_h // 2))

ctext(150, f"v{version.APP_VERSION}  ·  THE FEEL KOREA CO.,LTD.", font(12), MUTED)
ctext(196, "로딩 중…", font(13), ACCENT_DK)

img.convert("RGB").save("splash.png")
print("splash.png composited ->", img.size)
