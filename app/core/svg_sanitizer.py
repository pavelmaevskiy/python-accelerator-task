"""Allowlist SVG sanitiser: the one place this epic sanitises uploaded content.

Parses with defusedxml (no DTDs, no entity expansion), keeps only known-safe elements and
attributes, drops ``script``/``foreignObject``/``on*``/external references, and re-serialises.
"""

import re
import xml.etree.ElementTree as ET  # noqa: S405  # serialisation/building only; parsing is defused

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from app.core.errors import DomainValidationError

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

ALLOWED_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "text", "tspan", "title", "desc", "linearGradient", "radialGradient", "stop", "clipPath",
        "mask", "use", "symbol", "pattern", "image", "a",
    }
)  # fmt: skip
_HREF_ATTRS = {"href", f"{{{XLINK_NS}}}href"}
_UNSAFE_STYLE = re.compile(r"url\s*\(|expression\s*\(|javascript:|@import", re.IGNORECASE)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_attrs(el: ET.Element) -> None:
    for name in list(el.attrib):
        local = _local(name)
        value = el.attrib[name]
        if local.lower().startswith("on"):
            del el.attrib[name]
        elif name in _HREF_ATTRS or local == "href":
            if not value.startswith("#"):  # only same-document references
                del el.attrib[name]
        elif local == "style" and _UNSAFE_STYLE.search(value):
            del el.attrib[name]
        elif _UNSAFE_STYLE.search(value) and local in {"fill", "stroke", "filter", "clip-path"}:
            if not re.fullmatch(r"url\(#[\w.-]+\)", value.strip()):
                del el.attrib[name]


def _sanitize(el: ET.Element) -> None:
    for child in list(el):
        if _local(child.tag) not in ALLOWED_ELEMENTS or not child.tag.startswith(f"{{{SVG_NS}}}"):
            el.remove(child)
            continue
        _clean_attrs(child)
        _sanitize(child)


def sanitize_svg(data: bytes) -> bytes:
    """Return a sanitised copy of ``data`` or raise ``UPLOAD_INVALID``."""
    try:
        root = SafeET.fromstring(data, forbid_dtd=True)
    except (SafeET.ParseError, DefusedXmlException, ValueError, UnicodeDecodeError) as exc:
        raise DomainValidationError("UPLOAD_INVALID", "SVG could not be parsed safely") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise DomainValidationError("UPLOAD_INVALID", "File is not an SVG document")
    _clean_attrs(root)
    _sanitize(root)
    out: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return out
