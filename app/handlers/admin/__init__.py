"""Admin paneli (paket).

Avval bitta katta admin.py edi; endi bo'limlarga ajratilgan. Bo'limlar
eski fayldagi tartibda ulanadi, shuning uchun tugmalar va buyruqlar
oldingidek ishlaydi.
"""

from aiogram import Router

router = Router()

from . import _common  # noqa: E402
from . import asosiy  # noqa: E402
from . import ommaviy_savol  # noqa: E402
from . import savol_qoshish  # noqa: E402
from . import fayl_import  # noqa: E402
from . import katalog  # noqa: E402
from . import statistika  # noqa: E402
from . import foydalanuvchilar  # noqa: E402
from . import rollar  # noqa: E402
from . import oquvchi_boshqaruv  # noqa: E402

router.include_router(asosiy.router)
router.include_router(ommaviy_savol.router)
router.include_router(savol_qoshish.router)
router.include_router(fayl_import.router)
router.include_router(katalog.router)
router.include_router(statistika.router)
router.include_router(foydalanuvchilar.router)
router.include_router(rollar.router)
router.include_router(oquvchi_boshqaruv.router)

# Eski admin.py dagi nomlar boshqa joylardan chaqirilsa ishlashi uchun
for _module in (_common, asosiy, ommaviy_savol, savol_qoshish, fayl_import, katalog, statistika, foydalanuvchilar, rollar):
    for _name, _value in vars(_module).items():
        if _name.startswith("__") or _name == "router":
            continue
        globals().setdefault(_name, _value)
