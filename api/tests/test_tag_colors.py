import pytest

from pyxie_core.tag_colors import TagColorError, apply_changes, auto_color, build_tag_style, norm_hex, parse_tag_style, readable_fg, view


def test_parse_string_and_roundtrip():
    s = parse_tag_style("color-map=web:3366CC:FFFFFF;db:00AA00,ordering=alphabetical,shape=full")
    assert s["colors"] == {"web": ("3366CC", "FFFFFF"), "db": ("00AA00", None)}
    assert s["ordering"] == "alphabetical" and s["shape"] == "full"
    again = parse_tag_style(build_tag_style(s))
    assert again["colors"] == s["colors"] and again["ordering"] == "alphabetical"


def test_parse_dict_and_empty():
    assert parse_tag_style({"color-map": "a:FF0000", "shape": "circle"})["colors"] == {"a": ("FF0000", None)}
    assert parse_tag_style({"color-map": ["a:FF0000:000000", "b:00FF00"]})["colors"]["b"] == ("00FF00", None)
    assert parse_tag_style(None)["colors"] == {} and build_tag_style(parse_tag_style(None)) == ""


def test_unknown_keys_survive_a_write():
    s = parse_tag_style("color-map=a:FF0000,future-key=yes")
    assert "future-key=yes" in build_tag_style(s)


def test_apply_changes_sets_clears_and_validates():
    s = parse_tag_style("color-map=a:FF0000;b:00FF00")
    n = apply_changes(s, {"a": None, "c": "#0000ff"})
    assert set(n["colors"]) == {"b", "c"} and n["colors"]["c"][0] == "0000FF"
    assert n["colors"]["c"][1] == "FFFFFF"  # readable text on dark blue
    with pytest.raises(TagColorError):
        apply_changes(s, {"a": "red"})


def test_hex_and_contrast_helpers():
    assert norm_hex("#abc123") == "ABC123" and norm_hex("xyz") is None and norm_hex(None) is None
    assert readable_fg("FFFFFF") == "000000" and readable_fg("000000") == "FFFFFF"


def test_auto_color_matches_what_proxmox_shows():
    # read from Proxmox 8.2's own web UI (computed tag backgrounds), 2026-10-15
    golden = {"windows": "9A8274", "auto": "B858DD", "linux": "8F8CBB", "AI": "B38966", "community-script": "5FA074"}
    for tag, hexcolor in golden.items():
        assert auto_color(tag) == hexcolor, tag
    assert auto_color("AI") != auto_color("ai")  # the spelling matters


def test_overrides_match_the_exact_spelling_like_proxmox():
    # verified live: Proxmox colored AI from an `AI` entry and ignored an `ai` entry
    s = parse_tag_style("color-map=AI:FF0000")
    assert "AI" in s["colors"]
    n = apply_changes(s, {"AI": "00FF00"})
    assert list(n["colors"]) == ["AI"] and n["colors"]["AI"][0] == "00FF00"
    both = apply_changes(s, {"ai": "0000FF"})
    assert set(both["colors"]) == {"AI", "ai"}
    assert apply_changes(s, {"AI": None})["colors"] == {}
