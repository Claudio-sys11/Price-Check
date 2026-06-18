"""version.py의 APP_VERSION으로 PyInstaller 버전 정보(version_info.txt)를 생성한다.

exe 파일 속성(자세히)에 게시자(CompanyName)·제품명·버전·저작권을 임베드해
'게시자 없음' 으로 표시되지 않게 한다. (단, Windows SmartScreen 경고를 완전히
없애려면 별도의 코드 서명 인증서가 필요하다.)
"""
import io

from version import APP_VERSION

parts = (APP_VERSION.split(".") + ["0", "0", "0", "0"])[:4]
nums = tuple(int(p) for p in parts)
ver4 = ".".join(str(n) for n in nums)

COMPANY = "THE FEEL KOREA CO.,LTD."
DESC = "실시간 재고 현황(EcountERP) 및 평균 원가(Wizfasta) 비교"
PRODUCT = "원가비교 프로그램"

content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={nums}, prodvers={nums},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041204b0', [
      StringStruct('CompanyName', '{COMPANY}'),
      StringStruct('FileDescription', '{DESC}'),
      StringStruct('FileVersion', '{ver4}'),
      StringStruct('InternalName', 'EcountInventory'),
      StringStruct('LegalCopyright', 'Copyright (C) {COMPANY}'),
      StringStruct('OriginalFilename', 'EcountInventory.exe'),
      StringStruct('ProductName', '{PRODUCT}'),
      StringStruct('ProductVersion', '{ver4}')])]),
    VarFileInfo([VarStruct('Translation', [0x0412, 0x04b0])])
  ]
)
"""

io.open("version_info.txt", "w", encoding="utf-8").write(content)
print("version_info.txt ->", ver4)
