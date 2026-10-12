"""Minimal Word (.docx) support so business users never have to touch markdown.

* ``docx_to_markdown`` - read rules written in Word: headings, paragraphs, bullet/numbered lists, bold/italic.
* ``markdown_to_docx`` - write agent outputs as Word documents with the same subset.

Deliberately dependency-free (zip + XML). Tables and images are reduced to their text.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_to_markdown(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    lines: list[str] = []
    for para in root.iter(f"{W}p"):
        style = para.find(f"{W}pPr/{W}pStyle")
        style_name = (style.get(f"{W}val") or "") if style is not None else ""
        is_list = para.find(f"{W}pPr/{W}numPr") is not None or style_name.lower().startswith("list")
        text = "".join(_run_text(r) for r in para.iter(f"{W}r")).strip()
        if not text:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        heading = re.match(r"(?i)heading(\d)", style_name)
        # Blank line between blocks, except between consecutive list items.
        if lines and lines[-1] != "" and not (is_list and lines[-1].startswith("- ")):
            lines.append("")
        if heading:
            lines.append(f"{'#' * min(int(heading.group(1)), 6)} {text}")
        elif style_name.lower() == "title":
            lines.append(f"# {text}")
        elif is_list:
            lines.append(f"- {text.lstrip('•· ')}")
        else:
            lines.append(text)
    return "\n".join(lines).strip() + "\n"


def _run_text(run: ET.Element) -> str:
    text = "".join(t.text or "" for t in run.iter(f"{W}t"))
    if not text.strip():
        return text
    props = run.find(f"{W}rPr")
    if props is not None and props.find(f"{W}b") is not None:
        text = f"**{text}**"
    elif props is not None and props.find(f"{W}i") is not None:
        text = f"*{text}*"
    return text


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
 Target="styles.xml"/>
</Relationships>"""


def _style(style_id: str, size: int, bold: bool = True) -> str:
    b = "<w:b/>" if bold else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{style_id}"/>'
        f'<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>'
        f'<w:rPr>{b}<w:sz w:val="{size}"/></w:rPr></w:style>'
    )


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="120"/></w:pPr><w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos"/>'
    '<w:sz w:val="22"/></w:rPr></w:style>'
    + _style("Title", 40)
    + _style("Heading1", 32)
    + _style("Heading2", 28)
    + _style("Heading3", 24)
    + '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:style>'
    "</w:styles>"
)


def _runs(text: str) -> str:
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if not part:
            continue
        props = ""
        if part.startswith("**") and part.endswith("**"):
            part, props = part[2:-2], "<w:rPr><w:b/></w:rPr>"
        elif part.startswith("*") and part.endswith("*") and len(part) > 1:
            part, props = part[1:-1], "<w:rPr><w:i/></w:rPr>"
        out.append(f'<w:r>{props}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(out)


def markdown_to_docx(markdown: str, title: str | None = None) -> bytes:
    paragraphs: list[str] = []

    def para(text: str, style: str | None = None) -> None:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        paragraphs.append(f"<w:p>{ppr}{_runs(text)}</w:p>")

    if title:
        para(title, "Title")
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", stripped)
        if heading:
            para(heading.group(2), f"Heading{min(len(heading.group(1)), 3)}")
        elif bullet:
            prefix = "• " if stripped[0] in "-*+" else stripped.split()[0] + " "
            para(prefix + bullet.group(1), "ListBullet")
        else:
            para(stripped)

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + "".join(paragraphs)
        + "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _STYLES)
    return buffer.getvalue()
