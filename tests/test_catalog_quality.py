from app.modules.catalog.services.quality import catalog_item_completeness_percent


def test_completeness_empty_card_is_low():
    score = catalog_item_completeness_percent(
        title="ab",
        description="",
        category_id=None,
        status="draft",
        attributes_count=0,
        media=[],
        pricing=None,
    )
    assert score == 0


def test_completeness_full_card_matches_frontend_max():
    # Same weights as front scoreCatalogItem: max achievable points = 95
    score = catalog_item_completeness_percent(
        title="A" * 40,
        description="D" * 160,
        category_id=1,
        status="active",
        attributes_count=5,
        media=[
            {"media_type": "image"},
            {"media_type": "image"},
            {"media_type": "image"},
            {"media_type": "video"},
            {"media_type": "document"},
        ],
        pricing={"fixed_price": 10.0, "tiers": []},
    )
    assert score == 95


def test_completeness_partial_weights():
    score = catalog_item_completeness_percent(
        title="A" * 40,  # +10
        description="D" * 50,  # +8
        category_id=3,  # +10
        status="draft",
        attributes_count=2,  # +8
        media=[{"media_type": "image"}],  # +8
        pricing={"hourly_rate": 5.0, "tiers": []},  # +10
    )
    assert score == 54
