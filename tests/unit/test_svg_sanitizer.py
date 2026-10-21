import pytest

from app.core.errors import DomainValidationError
from app.core.svg_sanitizer import sanitize_svg


def test_strips_script_foreignobject_on_attributes_and_external_href() -> None:
    dirty = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script><foreignObject/>'
        b'<a href="http://evil"><rect onclick="x()" width="1" height="1"/></a></svg>'
    )
    clean = sanitize_svg(dirty).decode()
    assert "script" not in clean and "foreignObject" not in clean
    assert "onclick" not in clean and "evil" not in clean
    assert "<rect" in clean and 'width="1"' in clean  # benign content survives


def test_keeps_same_document_references_and_drops_external_ones() -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<defs><linearGradient id="g"/></defs><use xlink:href="#g"/>'
        b'<use xlink:href="https://evil.example/x.svg#a"/><rect fill="url(#g)"/></svg>'
    )
    clean = sanitize_svg(svg).decode()
    assert "#g" in clean and "evil.example" not in clean


def test_drops_unsafe_style_and_unknown_elements() -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(http://evil/x)"/>'
        b'<animate attributeName="x"/><iframe/><circle r="1" style="fill:red"/></svg>'
    )
    clean = sanitize_svg(svg).decode()
    assert "evil" not in clean and "animate" not in clean and "iframe" not in clean
    assert "fill:red" in clean


@pytest.mark.parametrize(
    "bad",
    [
        b"not xml at all",
        b"<svg",
        b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>',
        b'<!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><svg xmlns="http://www.w3.org/2000/svg">&b;</svg>',
        b"<html xmlns='http://www.w3.org/1999/xhtml'/>",
        b"<svg/>",  # no SVG namespace
        b"",
    ],
)
def test_unparsable_or_xxe_svg_raises_upload_invalid(bad: bytes) -> None:
    with pytest.raises(DomainValidationError) as e:
        sanitize_svg(bad)
    assert e.value.code == "UPLOAD_INVALID"
