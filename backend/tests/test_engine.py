from app.agents.base import obj, validate
from app.services.requirement_template import build_template, completeness, parse_upload, questions

SCHEMA = obj({
    "headline": {"type": "string"},
    "score": {"type": "integer"},
    "items": {"type": "array", "items": obj({"kind": {"type": "string", "enum": ["a", "b"]}, "ok": {"type": "boolean"}})},
})


def test_validate_accepts_good_input():
    assert validate(SCHEMA, {"headline": "x", "score": 3, "items": [{"kind": "a", "ok": True}]}) == []


def test_validate_reports_missing_wrong_types_and_enums():
    errs = validate(SCHEMA, {"score": "3", "items": [{"kind": "z", "ok": 1}]})
    assert "input.headline is missing" in errs
    assert "input.score should be integer" in errs
    assert any("must be one of" in e for e in errs)
    assert "input.items[0].ok should be boolean" in errs


def test_bool_is_not_an_integer():
    assert validate({"type": "integer"}, True) == ["input should be integer"]


def test_word_template_round_trip():
    qs = questions()
    first = next(iter(qs))
    docx = build_template("Demo", {first: "Hello from the template"})
    parsed = parse_upload("demo.docx", docx)
    assert parsed["answers"][first] == "Hello from the template"


def test_plain_text_upload_is_context():
    assert parse_upload("notes.md", b"# Notes\nsomething")["text"].startswith("# Notes")


def test_completeness_counts_required():
    c = completeness({})
    assert c["answered"] == 0 and c["required_total"] > 0 and len(c["missing_required"]) == c["required_total"]
