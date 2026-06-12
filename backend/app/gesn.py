"""Hardcoded MVP subset of ГЭСН/ФЕР codes we recognise.

The keys are simple substring matches against material / assembly types.
Production: load from ФЕР XML database; this file just unblocks the demo.
"""
GESN_MVP: dict[str, dict] = {
    "tpo_membrane": {
        "code": "ФЕР 12-01-002-01",
        "description": "Устройство кровли из ТПО мембраны",
        "unit": "м2",
        "rule": "membrane_area",
    },
    "wall_brick_380": {
        "code": "ФЕР 08-02-001-01",
        "description": "Кладка стен из кирпича 380 мм",
        "unit": "м3",
        "rule": "wall_volume_brick",
    },
    "wall_brick_250": {
        "code": "ФЕР 08-02-001-02",
        "description": "Кладка стен из кирпича 250 мм",
        "unit": "м3",
        "rule": "wall_volume_brick",
    },
    "concrete_prep": {
        "code": "ФЕР 06-01-001-01",
        "description": "Бетонная подготовка 100 мм",
        "unit": "м2",
        "rule": "slab_area",
    },
    "screed": {
        "code": "ФЕР 11-01-011-01",
        "description": "Стяжка цементно-песчаная 30-50 мм",
        "unit": "м2",
        "rule": "slab_area",
    },
    "insulation_xps": {
        "code": "ФЕР 12-01-013-01",
        "description": "Утеплитель XPS (ТЕХНОНИКОЛЬ CARBON и аналоги)",
        "unit": "м2",
        "rule": "insulation_area",
    },
    "insulation_psb": {
        "code": "ФЕР 12-01-013-02",
        "description": "Утеплитель ПСБС / пенополистирол М45",
        "unit": "м2",
        "rule": "insulation_area",
    },
    "glazing_vitrage": {
        "code": "ФЕР 09-04-001-01",
        "description": "Остекление витража алюминиевого профиля",
        "unit": "м2",
        "rule": "glazing_area",
    },
    "parapet_brick": {
        "code": "ФЕР 08-02-001-05",
        "description": "Кладка парапета из кирпича 120 мм",
        "unit": "м.п.",
        "rule": "parapet_length",
    },
    "roof_waterproofing_membrane": {
        "code": "ФЕР 12-01-002-03",
        "description": "Гидроизоляция кровли — мембрана в один слой",
        "unit": "м2",
        "rule": "membrane_area",
    },
    "geotextile": {
        "code": "ФЕР 12-01-002-08",
        "description": "Геотекстиль 300 г/м² под мембрану",
        "unit": "м2",
        "rule": "membrane_area",
    },
    "prof_list_n75": {
        "code": "ФЕР 09-01-001-04",
        "description": "Профилированный лист Н75-750",
        "unit": "м2",
        "rule": "slab_area",
    },
    "metal_beam": {
        "code": "ФЕР 09-03-001-01",
        "description": "Металлическая балка (двутавр / швеллер)",
        "unit": "м.п.",
        "rule": "parapet_length",  # placeholder: drive off spans for now
    },
    "hpl_panel": {
        "code": "ФЕР 09-04-002-01",
        "description": "HPL панели (FunderMax и аналоги) на фасаде/поле",
        "unit": "м2",
        "rule": "slab_area",
    },
    "sfb_panel": {
        "code": "ФЕР 09-04-002-02",
        "description": "Стеклофибробетонные панели на фасаде",
        "unit": "м2",
        "rule": "facade_area",
    },
    "concrete_slab_200": {
        "code": "ФЕР 06-01-014-01",
        "description": "Монолитная ж/б плита 200 мм",
        "unit": "м2",
        "rule": "slab_area",
    },
}


# Hand-tuned unit prices for MVP, in RUB. These are illustrative.
# Real production: load from ФЕР-2020 + index Минстроя per region/quarter.
UNIT_PRICES: dict[str, float] = {
    "ФЕР 12-01-002-01": 850,
    "ФЕР 12-01-002-03": 420,
    "ФЕР 12-01-002-08": 95,
    "ФЕР 08-02-001-01": 4200,
    "ФЕР 08-02-001-02": 4350,
    "ФЕР 08-02-001-05": 1850,
    "ФЕР 06-01-001-01": 320,
    "ФЕР 06-01-014-01": 1850,
    "ФЕР 11-01-011-01": 280,
    "ФЕР 12-01-013-01": 410,
    "ФЕР 12-01-013-02": 290,
    "ФЕР 09-04-001-01": 4500,
    "ФЕР 09-01-001-04": 540,
    "ФЕР 09-03-001-01": 7200,
    "ФЕР 09-04-002-01": 1850,
    "ФЕР 09-04-002-02": 2400,
}


def unit_price(code: str) -> float | None:
    return UNIT_PRICES.get(code)
