from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


EARBUD_SEED_PRODUCTS = [
    {
        "id": "earbud_001",
        "name": "AirBeat Lite",
        "category": "无线耳机",
        "target_audience": ["学生", "通勤"],
        "price": 179,
        "monthly_sales": 4200,
        "features": ["低延迟", "长续航", "学生价位", "轻量佩戴"],
    },
    {
        "id": "earbud_002",
        "name": "Campus Buds Pro",
        "category": "无线耳机",
        "target_audience": ["学生", "宿舍"],
        "price": 209,
        "monthly_sales": 3100,
        "features": ["主动降噪", "通话清晰", "续航强", "游戏模式"],
    },
    {
        "id": "earbud_003",
        "name": "StudyPods S2",
        "category": "无线耳机",
        "target_audience": ["学生", "游戏"],
        "price": 199,
        "monthly_sales": 5200,
        "features": ["低延迟", "轻量佩戴", "游戏模式", "蓝牙5.3"],
    },
    {
        "id": "earbud_004",
        "name": "DormCall Mini",
        "category": "无线耳机",
        "target_audience": ["学生", "网课"],
        "price": 159,
        "monthly_sales": 2800,
        "features": ["通话降噪", "轻量佩戴", "宿舍通话", "快充"],
    },
    {
        "id": "earbud_005",
        "name": "GameBeat G1",
        "category": "无线耳机",
        "target_audience": ["学生", "游戏"],
        "price": 229,
        "monthly_sales": 3600,
        "features": ["低延迟", "游戏模式", "强低音", "长续航"],
    },
    {
        "id": "earbud_006",
        "name": "QuietBuds Neo",
        "category": "无线耳机",
        "target_audience": ["通勤", "学生"],
        "price": 239,
        "monthly_sales": 1800,
        "features": ["主动降噪", "通透模式", "通话清晰", "长续航"],
    },
    {
        "id": "earbud_007",
        "name": "ClassMate TWS",
        "category": "无线耳机",
        "target_audience": ["学生", "网课"],
        "price": 149,
        "monthly_sales": 6100,
        "features": ["学生价位", "网课耳机", "轻量佩戴", "快充"],
    },
    {
        "id": "earbud_008",
        "name": "BassPods Youth",
        "category": "无线耳机",
        "target_audience": ["学生", "音乐"],
        "price": 189,
        "monthly_sales": 2700,
        "features": ["强低音", "长续航", "蓝牙5.3", "佩戴稳固"],
    },
    {
        "id": "earbud_009",
        "name": "ClearTalk C2",
        "category": "无线耳机",
        "target_audience": ["学生", "通勤"],
        "price": 199,
        "monthly_sales": 3300,
        "features": ["通话清晰", "通话降噪", "轻量佩戴", "长续航"],
    },
    {
        "id": "earbud_010",
        "name": "PocketPods SE",
        "category": "无线耳机",
        "target_audience": ["学生", "入门"],
        "price": 129,
        "monthly_sales": 7400,
        "features": ["学生价位", "小巧便携", "快充", "蓝牙5.3"],
    },
    {
        "id": "earbud_011",
        "name": "DelayZero X",
        "category": "无线耳机",
        "target_audience": ["学生", "游戏"],
        "price": 219,
        "monthly_sales": 2600,
        "features": ["低延迟", "游戏模式", "佩戴稳固", "长续航"],
    },
    {
        "id": "earbud_012",
        "name": "LibraryPods Air",
        "category": "无线耳机",
        "target_audience": ["学生", "自习"],
        "price": 169,
        "monthly_sales": 3900,
        "features": ["轻量佩戴", "低漏音", "长续航", "小巧便携"],
    },
]


EARBUD_SEED_REVIEWS = [
    ("earbud_001", 4, "延迟低，打游戏还可以，就是耳塞戴久了有点胀。"),
    ("earbud_001", 3, "续航宣传挺长，实际用下来比预期短一些。"),
    ("earbud_002", 4, "降噪不错，宿舍开会通话清楚，但 app 设置有点复杂。"),
    ("earbud_002", 3, "偶尔会断连，重新放回盒子才能恢复。"),
    ("earbud_003", 5, "游戏模式延迟很低，耳机也比较轻。"),
    ("earbud_003", 3, "充电盒外壳容易刮花，低频一般。"),
    ("earbud_004", 4, "网课收音清楚，价格合适，佩戴还算舒服。"),
    ("earbud_004", 2, "连接稳定性一般，走远一点就卡。"),
    ("earbud_005", 4, "低延迟和低音不错，但耳机偏大。"),
    ("earbud_005", 3, "续航还可以，佩戴舒适度一般。"),
    ("earbud_006", 4, "降噪效果在这个价位还行，通话比较清楚。"),
    ("earbud_006", 3, "价格偏高，学生党有点犹豫。"),
    ("earbud_007", 5, "便宜好用，上网课足够，戴久了不压耳。"),
    ("earbud_007", 3, "音质普通，连接偶尔不稳定。"),
    ("earbud_008", 4, "低音强，续航不错，适合听歌。"),
    ("earbud_008", 3, "戴着跑步还行，但耳塞尺寸不太适合所有人。"),
    ("earbud_009", 4, "通话清晰，宿舍开会对方听得清。"),
    ("earbud_009", 3, "降噪一般，地铁上还是有噪音。"),
    ("earbud_010", 5, "价格低，充电快，放包里很方便。"),
    ("earbud_010", 2, "续航缩水明显，连接有时慢。"),
    ("earbud_011", 4, "打游戏延迟低，佩戴也稳。"),
    ("earbud_011", 3, "耳机仓有点大，携带不太方便。"),
    ("earbud_012", 4, "自习室低漏音很好，戴着轻。"),
    ("earbud_012", 3, "音量偏小，户外不够用。"),
]


EARBUD_SPECIAL_PRODUCTS = [
    {
        "id": "earbud_095",
        "name": "Campus Buds 旧款清仓",
        "category": "无线耳机",
        "target_audience": ["学生", "预算敏感"],
        "price": 59,
        "original_price": 159,
        "monthly_sales": 360,
        "features": ["蓝牙5.1", "基础续航", "轻量佩戴", "旧款清仓"],
        "brand_tier": "entry",
        "sales_context": "clearance",
        "price_explanation": "旧款停产清仓，库存有限且不再补货",
        "test_case": "explainable_extreme",
        "expected_statistical_flag": True,
        "expected_excluded": False,
        "expected_market_layer": "adjacent_tier",
    },
    {
        "id": "earbud_096",
        "name": "RenewPods R1 官方翻新",
        "category": "无线耳机",
        "target_audience": ["学生", "入门"],
        "price": 49,
        "original_price": 199,
        "monthly_sales": 210,
        "features": ["蓝牙5.2", "快充", "官方翻新", "六个月保修"],
        "condition": "refurbished",
        "brand_tier": "entry",
        "sales_context": "refurbished_clearance",
        "price_explanation": "官方翻新旧款，存在轻微外观瑕疵并缩短保修期",
        "test_case": "explainable_extreme",
        "expected_statistical_flag": True,
        "expected_excluded": False,
        "expected_market_layer": "adjacent_tier",
    },
    {
        "id": "earbud_097",
        "name": "Auraluxe ANC Studio",
        "category": "无线耳机",
        "target_audience": ["商务", "音乐"],
        "price": 699,
        "monthly_sales": 620,
        "features": ["自适应主动降噪", "空间音频", "高清编解码", "两年保修"],
        "brand_tier": "premium",
        "sales_context": "regular",
        "price_explanation": "高端品牌、自适应降噪、空间音频和延长保修共同支撑溢价",
        "test_case": "explainable_extreme",
        "expected_statistical_flag": True,
        "expected_excluded": False,
        "expected_market_layer": "adjacent_tier",
    },
    {
        "id": "earbud_098",
        "name": "Reference Pods Signature",
        "category": "无线耳机",
        "target_audience": ["发烧友", "创作者"],
        "price": 899,
        "monthly_sales": 180,
        "features": ["双单元", "无损音频", "个性化听感", "三年保修"],
        "brand_tier": "luxury",
        "sales_context": "regular",
        "price_explanation": "旗舰声学结构、定制听感服务和三年保修能够解释高价",
        "test_case": "explainable_extreme",
        "expected_statistical_flag": True,
        "expected_excluded": False,
        "expected_market_layer": "adjacent_tier",
    },
    {
        "id": "earbud_099",
        "name": "硅胶耳塞套三对装（误归类）",
        "category": "无线耳机",
        "target_audience": ["配件用户"],
        "price": 9.9,
        "monthly_sales": 4500,
        "features": ["耳塞套", "三对装", "替换配件"],
        "product_type": "accessory",
        "package_type": "accessory_pack",
        "sales_context": "regular",
        "data_quality_flags": ["category_mismatch", "non_product_accessory"],
        "test_case": "dirty_outlier",
        "expected_statistical_flag": True,
        "expected_excluded": True,
        "expected_market_layer": "excluded",
    },
    {
        "id": "earbud_100",
        "name": "Imported Buds Price Unit Error",
        "category": "无线耳机",
        "target_audience": ["通勤"],
        "price": 9999,
        "monthly_sales": 0,
        "features": ["蓝牙5.3", "快充"],
        "price_unit": "fen_mislabeled_as_yuan",
        "sales_context": "data_import",
        "data_quality_flags": ["currency_unit_mismatch", "suspected_import_error"],
        "test_case": "dirty_outlier",
        "expected_statistical_flag": True,
        "expected_excluded": True,
        "expected_market_layer": "excluded",
    },
]


EARBUD_SPECIAL_REVIEWS = [
    ("earbud_095", 4, "清仓价很划算，基础听歌够用，但旧款协议和续航比较一般。"),
    ("earbud_095", 3, "库存不多，售后说明清楚，适合作为备用耳机。"),
    ("earbud_096", 4, "官方翻新价格低，功能正常，外壳有轻微划痕。"),
    ("earbud_096", 3, "保修只有六个月，适合预算有限的用户。"),
    ("earbud_097", 5, "自适应降噪和空间音频效果明显，售后期限也更长。"),
    ("earbud_097", 4, "价格高，但通话、降噪和做工符合高端定位。"),
    ("earbud_098", 5, "双单元解析力好，个性化听感服务适合发烧友。"),
    ("earbud_098", 4, "旗舰声音不错，不过普通通勤用户没有必要买这么贵。"),
    ("earbud_099", 2, "这只是替换耳塞套，不是完整耳机，分类明显不对。"),
    ("earbud_099", 1, "下单前才发现是配件，不能拿来和耳机整机比较。"),
    ("earbud_100", 1, "页面价格单位疑似导入错误，无法按当前标价购买。"),
    ("earbud_100", 1, "商品信息不完整，价格看起来像把分误当成了元。"),
]


KEYBOARD_SPECIAL_PRODUCTS = [
    {
        "id": "keyboard_095",
        "name": "OfficeKey 87 旧款清仓",
        "category": "机械键盘",
        "target_audience": ["学生", "预算敏感"],
        "price": 49,
        "original_price": 259,
        "monthly_sales": 280,
        "features": ["87键", "有线连接", "段落轴", "旧款清仓"],
        "brand_tier": "entry",
        "sales_context": "clearance",
        "price_explanation": "旧款有线库存清仓，不再提供新配色和长期备件",
        "test_case": "explainable_extreme",
        "expected_statistical_flag": True,
        "expected_excluded": False,
        "expected_market_layer": "adjacent_tier",
    },
    {
        "id": "keyboard_096",
        "name": "RenewBoard 68 官方翻新",
        "category": "机械键盘",
        "target_audience": ["办公", "入门"],
        "price": 39,
        "original_price": 329,
        "monthly_sales": 150,
        "features": ["68键", "线性轴", "官方翻新", "六个月保修"],
        "condition": "refurbished",
        "brand_tier": "entry",
        "sales_context": "refurbished_clearance",
        "price_explanation": "展示样机翻新销售，缺少原包装且保修期缩短",
        "test_case": "explainable_extreme",
        "expected_statistical_flag": True,
        "expected_excluded": False,
        "expected_market_layer": "adjacent_tier",
    },
    {
        "id": "keyboard_097",
        "name": "SwitchLab CNC Magnetic Pro",
        "category": "机械键盘",
        "target_audience": ["电竞", "发烧友"],
        "price": 999,
        "monthly_sales": 430,
        "features": ["CNC铝壳", "磁轴", "可调触发", "两年保修"],
        "brand_tier": "premium",
        "sales_context": "regular",
        "price_explanation": "CNC铝壳、磁轴可调触发和延长保修支撑高端价格",
        "test_case": "explainable_extreme",
        "expected_statistical_flag": True,
        "expected_excluded": False,
        "expected_market_layer": "adjacent_tier",
    },
    {
        "id": "keyboard_098",
        "name": "ArtisanBoard Custom 75",
        "category": "机械键盘",
        "target_audience": ["创作者", "收藏"],
        "price": 1299,
        "monthly_sales": 90,
        "features": ["定制外壳", "客制化轴体", "PBT键帽", "装配服务"],
        "brand_tier": "luxury",
        "sales_context": "made_to_order",
        "price_explanation": "小批量客制化外壳、轴体、键帽和人工装配服务支撑高价",
        "test_case": "explainable_extreme",
        "expected_statistical_flag": True,
        "expected_excluded": False,
        "expected_market_layer": "adjacent_tier",
    },
    {
        "id": "keyboard_099",
        "name": "拔键器工具（误归类）",
        "category": "机械键盘",
        "target_audience": ["配件用户"],
        "price": 8.8,
        "monthly_sales": 3900,
        "features": ["拔键器", "维修工具"],
        "product_type": "accessory",
        "package_type": "accessory",
        "data_quality_flags": ["category_mismatch", "non_product_accessory"],
        "test_case": "dirty_outlier",
        "expected_statistical_flag": True,
        "expected_excluded": True,
        "expected_market_layer": "excluded",
    },
    {
        "id": "keyboard_100",
        "name": "Imported Keyboard Price Unit Error",
        "category": "机械键盘",
        "target_audience": ["办公"],
        "price": 12999,
        "monthly_sales": 0,
        "features": ["98键", "三模连接"],
        "price_unit": "fen_mislabeled_as_yuan",
        "sales_context": "data_import",
        "data_quality_flags": ["currency_unit_mismatch", "suspected_import_error"],
        "test_case": "dirty_outlier",
        "expected_statistical_flag": True,
        "expected_excluded": True,
        "expected_market_layer": "excluded",
    },
]


KEYBOARD_SPECIAL_REVIEWS = [
    ("keyboard_095", 4, "清仓价适合入门，有线连接稳定，但旧款配件选择少。"),
    ("keyboard_095", 3, "价格很低，适合宿舍备用，售后说明是不再补货。"),
    ("keyboard_096", 4, "翻新后按键正常，外壳有使用痕迹，包装不完整。"),
    ("keyboard_096", 3, "适合低预算办公，但保修比新品短。"),
    ("keyboard_097", 5, "磁轴触发调节细，铝壳做工和游戏响应都很好。"),
    ("keyboard_097", 4, "价格高，但结构、轴体和保修明显不是普通入门键盘。"),
    ("keyboard_098", 5, "定制外壳和装配服务细致，适合客制化玩家。"),
    ("keyboard_098", 4, "客制化溢价明显，普通办公用户不需要这个配置。"),
    ("keyboard_099", 1, "这是拔键器工具，不是机械键盘整机。"),
    ("keyboard_099", 2, "商品分类放错了，价格不能和键盘相比。"),
    ("keyboard_100", 1, "标价疑似把分当成元，商品页面数据不可信。"),
    ("keyboard_100", 1, "价格单位错误，当前记录不应参与市场统计。"),
]


def _synthetic_product(product: dict[str, Any]) -> dict[str, Any]:
    category = str(product.get("category", ""))
    defaults = {
        "condition": "new",
        "brand_tier": "mass_market",
        "product_type": "earbuds" if category == "无线耳机" else "keyboard",
        "package_type": "single_product",
        "sales_context": "regular",
        "currency": "CNY",
        "price_unit": "yuan_per_item",
        "test_case": "regular",
        "expected_statistical_flag": False,
        "expected_excluded": False,
        "expected_market_layer": "candidate",
    }
    return defaults | product | {"source_type": "synthetic_seed", "synthetic": True}


def _synthetic_reviews(rows: list[tuple[str, int, str]]) -> list[dict[str, Any]]:
    return [
        {
            "product_id": product_id,
            "rating": rating,
            "text": text,
            "source_type": "synthetic_seed",
            "synthetic": True,
        }
        for product_id, rating, text in rows
    ]


def build_earbuds() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    products = [_synthetic_product(product) for product in EARBUD_SEED_PRODUCTS]
    reviews = _synthetic_reviews(EARBUD_SEED_REVIEWS)
    name_prefixes = ("Nova", "Echo", "Pulse", "Cloud", "Urban", "Melo", "Aero", "Comet")
    name_suffixes = ("Buds", "Pods", "Air", "Mini", "Neo", "Lite", "Go", "S")
    audience_cycle = ("通勤", "网课", "音乐", "自习", "运动", "入门")
    feature_sets = (
        ("低延迟", "蓝牙5.3", "长续航", "轻量佩戴"),
        ("通话降噪", "快充", "通话清晰", "小巧便携"),
        ("主动降噪", "通透模式", "长续航", "佩戴稳固"),
        ("低漏音", "蓝牙5.3", "轻量佩戴", "学生价位"),
    )
    price_pairs = ((139, 239), (149, 229), (159, 219), (169, 209), (179, 199), (189, 189))
    generated_prices = [
        value
        for pair_index in range(41)
        for value in price_pairs[pair_index % len(price_pairs)]
    ]
    positive_reviews = (
        "连接稳定，日常通勤使用方便，续航表现符合预期。",
        "佩戴比较轻，网课和通话声音清楚，快充很实用。",
        "蓝牙连接速度快，声音均衡，收纳盒携带方便。",
        "长时间听歌基本稳定，触控操作简单。",
    )
    critical_reviews = (
        "偶尔连接有时慢，复杂环境下会有短暂卡顿。",
        "耳塞戴久后佩戴舒适度一般，希望增加更多尺寸。",
        "标称续航较长，但高音量使用时续航比预期短。",
        "降噪一般，地铁等噪音环境仍能听到背景声。",
    )
    for offset, price in enumerate(generated_prices, start=13):
        index = offset - 13
        product_id = f"earbud_{offset:03d}"
        products.append(
            _synthetic_product(
                {
                    "id": product_id,
                    "name": (
                        f"{name_prefixes[index % len(name_prefixes)]} "
                        f"{name_suffixes[(index // len(name_prefixes)) % len(name_suffixes)]} "
                        f"{offset:02d}"
                    ),
                    "category": "无线耳机",
                    "target_audience": ["学生", audience_cycle[index % len(audience_cycle)]],
                    "price": price,
                    "monthly_sales": 900 + ((index * 379) % 6500),
                    "features": list(feature_sets[index % len(feature_sets)]),
                }
            )
        )
        reviews.extend(
            _synthetic_reviews(
                [
                    (
                        product_id,
                        4 + (index % 2),
                        positive_reviews[index % len(positive_reviews)],
                    ),
                    (
                        product_id,
                        2 + (index % 2),
                        critical_reviews[index % len(critical_reviews)],
                    ),
                ]
            )
        )
    products.extend(_synthetic_product(product) for product in EARBUD_SPECIAL_PRODUCTS)
    reviews.extend(_synthetic_reviews(EARBUD_SPECIAL_REVIEWS))
    return products, reviews


def build_keyboards() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    products: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    name_prefixes = ("KeyNova", "SwitchLab", "CodeDeck", "GameType", "OfficeKey", "PixelBoard")
    layouts = ("98键", "87键", "75配列", "68键", "104键")
    switches = ("线性轴", "段落轴", "静音轴", "提前触发轴")
    audience_sets = (
        ("游戏", "学生"),
        ("办公", "程序员"),
        ("游戏", "电竞"),
        ("办公", "学生"),
        ("创作者", "程序员"),
    )
    price_pairs = (
        (159, 499),
        (179, 479),
        (199, 459),
        (219, 439),
        (239, 419),
        (259, 399),
        (279, 379),
        (299, 359),
        (319, 339),
    )
    positive_reviews = (
        "轴体手感顺滑，键位布局紧凑，长时间打字比较舒服。",
        "无线连接稳定，切换设备方便，适合办公和编程。",
        "游戏响应快，键帽字符清晰，背光调节直观。",
        "热插拔更换轴体方便，结构稳定，整体做工不错。",
    )
    critical_reviews = (
        "大键声音偏响，夜间使用会觉得噪音明显。",
        "键帽表面容易留下油印，长期使用需要经常清洁。",
        "无线模式偶尔唤醒较慢，希望提升连接稳定性。",
        "软件设置项目较多，第一次使用不够直观。",
    )
    for index in range(94):
        product_number = index + 1
        pair = price_pairs[(index // 2) % len(price_pairs)]
        price = pair[index % 2]
        product_id = f"keyboard_{product_number:03d}"
        layout = layouts[index % len(layouts)]
        switch = switches[(index // len(layouts)) % len(switches)]
        features = [layout, switch, "热插拔", "全键无冲"]
        if index % 3 == 0:
            features[2] = "三模连接"
        if index % 4 == 0:
            features[3] = "RGB背光"
        products.append(
            _synthetic_product(
                {
                    "id": product_id,
                    "name": (
                        f"{name_prefixes[index % len(name_prefixes)]} "
                        f"{layout} K{product_number:03d}"
                    ),
                    "category": "机械键盘",
                    "target_audience": list(audience_sets[index % len(audience_sets)]),
                    "price": price,
                    "monthly_sales": 500 + ((index * 293) % 5200),
                    "features": features,
                }
            )
        )
        reviews.extend(
            _synthetic_reviews(
                [
                    (
                        product_id,
                        4 + (index % 2),
                        positive_reviews[index % len(positive_reviews)],
                    ),
                    (
                        product_id,
                        2 + (index % 2),
                        critical_reviews[index % len(critical_reviews)],
                    ),
                ]
            )
        )
    products.extend(_synthetic_product(product) for product in KEYBOARD_SPECIAL_PRODUCTS)
    reviews.extend(_synthetic_reviews(KEYBOARD_SPECIAL_REVIEWS))
    return products, reviews


def build_distribution_report(
    datasets: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    report: dict[str, Any] = {"dataset_version": "tenant-market-v4"}
    for category, products in datasets.items():
        prices = [float(product["price"]) for product in products]
        test_cases: dict[str, int] = {}
        for product in products:
            test_case = str(product["test_case"])
            test_cases[test_case] = test_cases.get(test_case, 0) + 1
        report[category] = {
            "product_count": len(products),
            "price_min": min(prices),
            "price_max": max(prices),
            "price_mean": round(mean(prices), 4),
            "price_median": median(prices),
            "test_case_counts": test_cases,
            "special_sample_ids": [
                str(product["id"])
                for product in products
                if product["test_case"] != "regular"
            ],
        }
    return report


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    earbuds, earbud_reviews = build_earbuds()
    keyboards, keyboard_reviews = build_keyboards()
    assert len(earbuds) == len(keyboards) == 100
    assert len(earbud_reviews) == len(keyboard_reviews) == 200
    write_json(DATA_DIR / "products" / "wireless_earbuds_competitors.json", earbuds)
    write_json(DATA_DIR / "reviews" / "wireless_earbuds_reviews.json", earbud_reviews)
    write_json(DATA_DIR / "products" / "mechanical_keyboards_competitors.json", keyboards)
    write_json(DATA_DIR / "reviews" / "mechanical_keyboards_reviews.json", keyboard_reviews)
    write_json(
        ROOT / "reports" / "v51" / "market_fixture_distribution.json",
        build_distribution_report({"无线耳机": earbuds, "机械键盘": keyboards}),
    )
    print(
        "Generated 100 products and 200 reviews per category, including "
        "94 regular, 2 dirty-outlier and 4 explainable-extreme products."
    )


if __name__ == "__main__":
    main()
