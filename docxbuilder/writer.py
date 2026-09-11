# -*- coding: utf-8 -*-
"""
    sphinx-docxwriter
    ~~~~~~~~~~~~~~~~~~~~~~~~~~

    Modified custom docutils writer for OpenXML (docx).
    Original code from 'sphinxcontrib-documentwriter'

    :copyright:
        Copyright 2011 by haraisao at gmail dot com
    :license: MIT, see LICENSE for details.

    sphinxcontrib-docxwriter
    ~~~~~~~~~~~~~~~~~~~~~~~~~~

    Custom docutils writer for OpenXML (docx).

    :copyright:
        Copyright 2010 by shimizukawa at gmail dot com (Sphinx-users.jp).
    :license: BSD, see LICENSE for details.
    :modified:
        Modifications for sphinx_docxbuilder-jm by Nefti-sama
        https://github.com/Nefti-sama/sphinx_docxbuilder-jm
"""
from pprint import pprint
import hashlib
import os
import posixpath
import re
import sys

from docutils import nodes, writers
from sphinx import addnodes, version_info
from sphinx.environment.adapters.toctree import TocTree
from sphinx.ext import graphviz
from sphinx.ext.inheritance_diagram import get_graph_hash
from sphinx.locale import admonitionlabels, _
from sphinx.util import logging

from docxbuilder import docx
from docxbuilder.highlight import DocxPygmentsBridge

import xml.etree.ElementTree as ET

# Is the PIL imaging library installed?
try:
    from PIL import Image
except ImportError as exp:
    Image = None

# Is the math libraries installed?
try:
    import html.entities
    import mathml2omml
    import latex2mathml.converter

    def latex2omml(latex):
        """Convert a LaTeX equation into an OMML element, through MathML."""
        mathml = latex2mathml.converter.convert(latex)
        entities = {'dtdot': 0x22f1, 'midot': 0x00b7}
        entities.update(html.entities.name2codepoint)
        return docx.fromstring(mathml2omml.convert(mathml, entities))[0]
except ImportError:
    def latex2omml(latex):
        """Return the equation as a plain OMML run, the math libraries being absent."""
        return docx.make_omath_run(latex)

# Utility functions

def is_sphinx_version_lower_than(version):
    """Return true if current Sphinx version is less than specified version
    """
    major, minor, patch, _, _ = version_info
    return (major, minor, patch) < version

def findall(node, *args, **kwargs):
    """Return a list of the nodes matching the given condition.

    Node.findall supersedes the deprecated Node.traverse, but was only added
    in docutils 0.18, so fall back for older versions. A list is returned,
    as traverse did, because callers insert into the tree while iterating.
    """
    finder = getattr(node, 'findall', None)
    if finder is None:
        finder = node.traverse
    return list(finder(*args, **kwargs))

# Absolute SVG length units, as CSS px (the SVG user unit) per unit.
SVG_UNITS_IN_PX = {
    'px': 1.0,
    'pt': 96.0 / 72.0,
    'pc': 16.0,
    'in': 96.0,
    'cm': 96.0 / 2.54,
    'mm': 96.0 / 25.4,
}

SVG_LENGTH_RE = re.compile(r'([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*(.*)')

def parse_svg_length(value):
    '''Convert an SVG length to px, or return None if it is not absolute.

    Percentages and font-relative units (em, ex) are resolved against a
    viewport the writer does not have, so callers fall back to the viewBox.
    '''
    match = SVG_LENGTH_RE.match(value.strip())
    if match is None:
        return None
    number, unit = match.groups()
    if not unit:
        return float(number)
    factor = SVG_UNITS_IN_PX.get(unit.strip().lower())
    if factor is None:
        return None
    return float(number) * factor

def get_svg_size(filename):
    '''Return the natural (width, height) of an SVG in px.

    width and height are optional attributes and are frequently relative or
    carry a unit, so fall back to the viewBox the way a renderer does.
    '''
    root = ET.parse(filename).getroot()
    width = root.get('width')
    height = root.get('height')
    width = parse_svg_length(width) if width is not None else None
    height = parse_svg_length(height) if height is not None else None
    if width and height:
        return (width, height)

    viewbox = root.get('viewBox')
    if viewbox is not None:
        box = re.split(r'[\s,]+', viewbox.strip())
        if len(box) == 4:
            try:
                vbwidth, vbheight = float(box[2]), float(box[3])
            except ValueError:
                vbwidth = vbheight = 0.0
            if vbwidth > 0 and vbheight > 0:
                # Only one of the two was absolute: keep the aspect ratio.
                if width:
                    return (width, width * vbheight / vbwidth)
                if height:
                    return (height * vbwidth / vbheight, height)
                return (vbwidth, vbheight)
    raise RuntimeError('Failed to get the image size of %s' % filename)

def get_image_size(filename):
    """Return the natural (width, height) of an image, in centimetres.

    SVG is measured from its own markup; everything else needs PIL.
    """
    if Image is None:
        raise RuntimeError(
            'image size not fully specified and PIL not installed')
    # graphviz.render_dot and other Sphinx APIs hand back path objects whose
    # str behaviour is deprecated and goes away in Sphinx 10. Coerce rather
    # than using os.fspath, which needs Python 3.6.
    filename = str(filename)
    if filename.endswith(".svg"):
        width, height = get_svg_size(filename)
        cmperin = 2.54
        # get_svg_size reports CSS px, which are 1/96in by definition; this
        # is the density cairosvg rasterises at too.
        dpi = 96
        return (width * cmperin / dpi, height * cmperin / dpi)

    else:
        with Image.open(filename, 'r') as imageobj:
            dpi = imageobj.info.get('dpi', (72, 72))
            # dpi information can be (xdpi, ydpi) or xydpi
            try:
                iter(dpi)
            except StopIteration:
                dpi = (dpi, dpi)
            width = imageobj.size[0]
            height = imageobj.size[1]
            cmperin = 2.54
            return (width * cmperin / dpi[0], height * cmperin / dpi[1])

def convert_to_twip_size(size_with_unit, max_width):
    """Convert a CSS length into twips (1/1440 inch), the unit OOXML uses.

    A percentage is taken of max_width. A bare number is px.
    """
    if size_with_unit is None:
        return None
    if size_with_unit.endswith('%'):
        return max_width * float(size_with_unit[:-1]) / 100

    match = re.match(r'^(\d+(?:\.\d*)?)(\D*)$', size_with_unit)
    if not match:
        raise RuntimeError('Unexpected length unit: %s' % size_with_unit)
    size = float(match.group(1))
    unit = match.group(2)
    if not unit:
        unit = 'px'

    twipperin = 1440.0
    cmperin = 2.54
    twippercm = twipperin / cmperin
    ratio_map = {
        'em': 12 * twipperin / 144, # TODO: Use Body Text font size
        'ex': 12 * twipperin / 144,
        'mm': twippercm / 10, 'cm': twippercm, 'in': twipperin,
        'px': twipperin / 96, 'pt': twipperin / 72, 'pc': twipperin / 6,
    }
    ratio = ratio_map.get(unit)
    if ratio is None:
        raise RuntimeError('Unknown length unit: %s' % size_with_unit)
    return size * ratio

def convert_to_cm_size(twip_size):
    """Convert a twip length into centimetres."""
    if twip_size is None:
        return None
    twipperin = 1440.0
    cmperin = 2.54
    return twip_size / twipperin * cmperin

def convert_cm_to_twip(cm_size):
    """Convert a centimetre length into twips (1/1440 inch), the unit OOXML uses."""
    if cm_size is None:
        return None
    twipperin = 1440.0
    cmperin = 2.54
    return cm_size / cmperin * twipperin

def adjust_size(max_size, size, other_size):
    """Scale size down to max_size, keeping the aspect ratio.

    Returns the pair unchanged when it already fits.
    """
    if size > max_size:
        ratio = max_size / size
        return max_size, other_size * ratio
    return size, other_size

def has_caption(image_node):
    """Return true if the image node is a figure with a caption."""
    parent = image_node.parent
    if not isinstance(parent, nodes.figure):
        return False
    index = parent.index(image_node)
    caption_index = parent.first_child_matching_class(nodes.caption, index + 1)
    return caption_index is not None

#: The docutils "align" values that place a block on the page.  The image
#: directive also accepts top / middle / bottom, which align an *inline* image
#: against the surrounding text and have no meaning for a block of its own.
BLOCK_ALIGN_VALUES = frozenset(('left', 'center', 'right', 'default'))

def block_align(node):
    """Return the alignment a node asks for, if a block can be aligned by it.

    ``top``, ``middle`` and ``bottom`` are dropped: they position an inline
    image relative to the text baseline, which a paragraph of its own has no
    use for.  Unknown values are dropped as well, leaving the style's own
    alignment in place.
    """
    if node is None:
        return None
    align = node.get('align')
    return align if align in BLOCK_ALIGN_VALUES else None

def image_block_align(node):
    """Return the alignment of a block-level image, looking through its link.

    An image with a ``target`` is wrapped in a reference, and it is the
    reference that gets the paragraph, so the reference has to read the
    alignment of the image it wraps.
    """
    align = block_align(node)
    if align is None and is_image_link(node):
        align = block_align(node[0])
    return align

def is_image_link(node):
    """Return true if the node is a link whose whole content is one image.

    That is the shape an image with a ``target`` takes: the reference stands in
    for the image as the block, so it is the reference that gets the paragraph.
    """
    return (isinstance(node, nodes.reference)
            and len(node) == 1 and isinstance(node[0], nodes.image))

def figure_image(node):
    """Return the image a figure is built around, or None if it has none.

    The image is the figure's first child, or the reference wrapping it when
    the image carries a ``target``. A caption or a legend ends the search, so
    a picture inside a legend is not mistaken for the figure's own.
    """
    for child in node.children:
        if isinstance(child, nodes.image):
            return child
        if is_image_link(child):
            return child[0]
        if isinstance(child, (nodes.caption, nodes.legend)):
            break
    return None

def image_block_properties(node):
    """Return the (style, align, keep_next) of the paragraph a block image gets.

    ``node`` is the image, or the reference wrapping it when the image has a
    ``target``; either way the figure it may sit in is its parent, and it is
    the figure that decides the style and the alignment then.
    """
    if isinstance(node.parent, nodes.figure):
        return 'Figure', block_align(node.parent), has_caption(node)
    return 'Image', image_block_align(node), False

def format_secnumber(secnumber):
    """Return a section number as the prefix the document displays it with.

    Headings, the table of contents and references to a section all use this,
    so a link reads the way the heading it points at does.
    """
    return '.'.join(map(str, secnumber)) + ' '

def is_first_class_child(node):
    """Return true if the node is the first of its own class among its siblings."""
    parent = node.parent
    first_child_index = parent.first_child_matching_class(node.__class__)
    return parent[first_child_index] is node

def make_bookmark_name(docname, node_id):
    # The pattern Office enables to handle as a bookmark is ^(?!\d)\w{1,40}$
    """Return a bookmark name Word accepts for a node of a document."""
    md5 = hashlib.md5(('%s/%s' % (docname, node_id)).encode('utf8'))
    return '_' + md5.hexdigest()

def count_colspec(table_node):
    """Return the number of colspec nodes in a table node."""
    tgroup = next(
        (c for c in table_node.children if isinstance(c, nodes.tgroup)),
        None)
    if tgroup is None:
        return 0
    return sum((1 for c in tgroup.children if isinstance(c, nodes.colspec)))

#
#  DocxWriter class for sphinx
#


class DocxWriter(writers.Writer):
    """Docutils writer producing a .docx file.

    The work is done by the translator the builder creates; this class only
    carries the document properties and the resulting bytes.
    """
    supported = ('docx',)
    settings_spec = ('No options here.', '', ())
    settings_defaults = {}

    output = None

    def __init__(self, builder):
        """Store the builder and start with empty document properties."""
        writers.Writer.__init__(self)
        self.builder = builder

        self._title = ''
        self._author = ''
        self._props = {}

    def set_doc_properties(self, title, author, props):
        """Set the title, author and custom properties of the document."""
        self._title = title
        self._author = author
        self._props = props

    def translate(self):
        """Walk the doctree with a new translator and keep its output."""
        visitor = self.builder.create_translator(self.document, self.builder)
        self.document.walkabout(visitor)
        self.output = visitor.asbytes()

#
#  DocxTranslator class for sphinx
#

SECTION_CLASS_PATTERN = re.compile(
    r'^docx-section(?:-(portrait|landscape))?-(\d+)$')

# Node types that read their own classes and must not have a class style
# applied on top of them: inline resolves character styles itself, and an
# admonition turns its 'admonition-*' class into the table style it is drawn
# with.  nodes.Admonition covers the docutils admonitions; the three tagnames
# are the Sphinx nodes that are drawn as admonitions without subclassing it.
CLASS_STYLE_EXCLUDED_TAGS = frozenset(
    ('inline', 'seealso', 'versionmodified', 'todo_node'))

def to_error_string(contents):
    """Return a description of a contents object for an error message."""
    from xml.etree.ElementTree import tostring
    func = lambda xml: tostring(xml, encoding='utf8').decode('utf8')
    return type(contents).__name__ + '\n' + func(contents.to_xml())

class BookmarkElement(object):
    """Marker for contents that are a bookmark rather than document content."""
    pass

class ParagraphElement(object):
    """Marker for contents that become a w:p element."""
    pass

class TableElement(object):
    """Marker for contents that become a w:tbl element."""
    pass

class SdtElement(object):
    """Marker for contents that become a structured document tag."""
    pass

class BookmarkStart(BookmarkElement):
    """The opening half of a bookmark."""
    def __init__(self, bookmark_id, name):
        """Store the bookmark id and name."""
        self._id = bookmark_id
        self._name = name

    def to_xml(self):
        """Return the w:bookmarkStart element."""
        return docx.make_bookmark_start(self._id, self._name)

class BookmarkEnd(BookmarkElement):
    """The closing half of a bookmark."""
    def __init__(self, bookmark_id):
        """Store the id of the bookmark to close."""
        self._id = bookmark_id

    def to_xml(self):
        """Return the w:bookmarkEnd element."""
        return docx.make_bookmark_end(self._id)

class Paragraph(ParagraphElement):
    """A paragraph being built up run by run.

    Runs go on a stack of contents lists so a hyperlink can collect its own
    runs and wrap them, and text styles on a second stack so nested inline
    markup combines.
    """
    DEFAULT_STYLE = 0
    DOCXBUILDER_STYLE = 1
    TABLE_BOTTOM_MARGIN_STYLE = 2

    def __init__(self, indent=None, right_indent=None,
                 paragraph_style=None, style_kind=DEFAULT_STYLE, align=None,
                 keep_lines=False, keep_next=False,
                 list_info=None, preserve_space=False):
        """Set the paragraph properties and start with an empty contents list."""
        # A stack, not a list: begin_hyperlink pushes a level so the runs it
        # collects can be wrapped, then merged back on end_hyperlink.
        self._contents_stack = [[]]
        self._text_style_stack = []
        self._preserve_space = preserve_space
        self._indent = indent
        self._right_indent = right_indent
        self._style = paragraph_style
        self._style_kind = style_kind
        self._align = align
        self._keep_lines = keep_lines
        self._keep_next = keep_next
        self._list_info = list_info

    @property
    def is_default_style(self):
        """True if the paragraph carries no style of the document's own."""
        return self._style_kind == Paragraph.DEFAULT_STYLE

    @property
    def is_table_bottom_margin_style(self):
        """True if the paragraph is the spacer added below a table."""
        return self._style_kind == Paragraph.TABLE_BOTTOM_MARGIN_STYLE

    def add_text(self, text):
        """Append a run of text, in the styles currently pushed."""
        style = {}
        for text_style in self._text_style_stack:
            style.update(text_style)
        self._contents_stack[-1].append(
            docx.make_run(text, style, self._preserve_space))

    def add_break(self):
        """Append a line break run."""
        self._contents_stack[-1].append(docx.make_break_run())

    def add_picture(self, rid, picid, filename, width, height, alt,
                    svg_rid=None):
        """Append an inline picture run."""
        self._contents_stack[-1].append(
            docx.make_inline_picture_run(
                rid, picid, filename, width, height, alt, svg_rid=svg_rid))

    def add_math(self, equation):
        """Append an equation as OMML.

        A conversion failure still leaves the raw LaTeX in the document, so the
        text is not lost, before the error propagates.
        """
        try:
            self._contents_stack[-1].append(latex2omml(equation))
        except:
            self._contents_stack[-1].append(docx.make_omath_run(equation))
            raise

    def add_footnote_reference(self, footnote_id, style_id):
        """Append a reference to a footnote, as used in the body text."""
        self._contents_stack[-1].append(
            docx.make_footnote_reference(footnote_id, style_id))

    def add_footnote_ref(self, style_id):
        """Append the footnote's own mark, as used in the footnote itself."""
        self._contents_stack[-1].append(docx.make_footnote_ref(style_id))

    def add_textbox(self, style, color, contents, wrap_style=None):
        """Append a VML textbox holding the given contents."""
        self._contents_stack[-1].append(docx.make_vml_textbox(
            style, color, (c.to_xml() for c in contents), wrap_style))

    def push_style(self, text_style):
        """Push a text style onto the stack, applied to subsequent runs."""
        self._text_style_stack.append(text_style)

    def pop_style(self):
        """Pop the most recently pushed text style."""
        self._text_style_stack.pop()

    def begin_hyperlink(self, hyperlink_style_id):
        """Start collecting runs for a hyperlink, in the hyperlink style."""
        self._contents_stack.append([])
        self._text_style_stack.append(
            docx.make_run_style_property(hyperlink_style_id))

    def end_hyperlink(self, rid, anchor, tooltip=None):
        """Finish a hyperlink and append it.

        Without a target there is nothing to link to, so the runs are appended
        plainly instead, and the tooltip has nothing to hang on either.
        """
        self._text_style_stack.pop()
        if rid is not None or anchor is not None:
            hyperlink = docx.make_hyperlink(rid, anchor, tooltip)
            hyperlink.extend(self._contents_stack.pop())
            self._contents_stack[-1].append(hyperlink)
        else:
            run_list = self._contents_stack.pop()
            self._contents_stack[-1].extend(run_list)

    def keep_next(self):
        """Ask Word to keep this paragraph on the same page as the next."""
        self._keep_next = True

    def extract_contents(self):
        """Pop and return the current contents list."""
        return self._contents_stack.pop()

    def append(self, contents):
        """Append a nested paragraph or a bookmark.

        A nested paragraph is flattened into this one, as a line block or a list
        item has no paragraph of its own in the output.
        """
        if isinstance(contents, Paragraph): # for nested line_block or list_item
            self._contents_stack[-1].extend(contents.extract_contents())
        elif isinstance(contents, BookmarkElement):
            self._contents_stack[-1].append(contents.to_xml())
        else:
            raise RuntimeError('Can not append %s' % to_error_string(contents))

    def to_xml(self):
        """Return the w:p element with all its runs."""
        para = docx.make_paragraph(
            self._indent, self._right_indent, self._style, self._align,
            self._keep_lines, self._keep_next, self._list_info)
        para.extend(self._contents_stack[0])
        return para

class Table(TableElement):
    """A table being built up row by row.

    Cells are held as a grid of ``[vmerge, contents]`` pairs, with None
    standing for a cell merged into the one on its left.
    """
    def __init__(
            self, table_style, table_width, colsize_list, indent, align,
            keep_next, cant_split_row, set_table_header, rotation_header_height,
            fit_content, no_wrap):
        """Set the table properties and start with an empty grid."""
        self._style = table_style
        self._table_width = table_width # (max table width(dax), table width(%))
        self._colspec_list = []
        self._colsize_list = colsize_list
        self._indent = indent
        self._align = align
        self._stub = 0
        self._head = []
        self._body = []
        self._current_target = self._body
        self._current_row_index = -1
        self._current_cell_index = -1
         # 0: not set, 1: set header, 2: set first row, 3: set all rows
        self._keep_next = keep_next
        self._cant_split_row = cant_split_row
        self._set_table_header = set_table_header
        self._rotation_header_height = rotation_header_height
        self._fit_content = fit_content
        self._no_wrap = no_wrap

    @property
    def style(self):
        """The style id of the table."""
        return self._style

    def keep_next(self):
        '''Set keep_next to set first row. This method is supposed to be
           called from only Table.make_cell.
        '''
        self._keep_next = 2

    def add_colspec(self, colspec):
        """Add a column width from a colspec node, as a relative number."""
        self._colspec_list.append(colspec)

    def add_stub(self):
        """Count one more leading column as a stub (row header)."""
        self._stub += 1

    def start_head(self):
        """Direct subsequent rows into the table header."""
        self._current_target = self._head
        self._current_row_index = -1

    def start_body(self):
        """Direct subsequent rows into the table body."""
        self._current_target = self._body
        self._current_row_index = -1

    def add_row(self):
        """Start a new row, or move into one already made by a row span.

        Cells created by an earlier vertical merge are skipped over.
        """
        self._current_row_index += 1
        if self._current_row_index < len(self._current_target):
            # A row span made this row already: start just before the first
            # cell it did not fill, so add_cell lands on that one.
            row = self._current_target[self._current_row_index]
            for index, cell in enumerate(row):
                if cell is not None and cell[0] != 'continue':
                    self._current_cell_index = index - 1
                    break
            else:
                # Every cell is spanned; the next one goes past the end.
                self._current_cell_index = index
        else:
            self._current_target.append([])
            self._current_cell_index = -1

    def add_cell(self, morerows, morecols):
        """Start a new cell, reserving the grid places its spans cover.

        Cells to the right become None, and cells below get a 'continue' vertical
        merge, growing the grid where the spans run past it.
        """
        row = self._current_target[self._current_row_index]
        self._current_cell_index += (
            Table._get_grid_span(row, self._current_cell_index))
        if not self._current_cell_index < len(row):
            row.append([None if morerows == 0 else 'restart', []])

        cell_index = self._current_cell_index
        start = cell_index + 1
        # The columns to the right are covered by this cell's grid span.
        row[start:start + morecols] = (None for _ in range(morecols))

        # Each row below gets a 'continue' cell in the same column, plus the
        # same span to its right; rows that do not exist yet are made here.
        for idx in range(1, morerows + 1):
            if not self._current_row_index + idx < len(self._current_target):
                self._current_target.append([])
            row = self._current_target[self._current_row_index + idx]
            if cell_index < len(row):
                row[cell_index] = ['continue', []]
            else:
                row.extend([None, []] for _ in range(cell_index - len(row)))
                row.append(['continue', []])
            row[start:start + morecols] = (None for _ in range(morecols))

    def current_cell_width(self):
        """Return the width of the current cell in dxa, or None if unknown."""
        if self._colspec_list:
            self._reset_colsize_list()
            self._colspec_list = []
        index = self._current_cell_index
        if not index < len(self._colsize_list):
            return None
        grid_span = Table._get_grid_span(
            self._current_target[self._current_row_index], index)
        ratio = sum(self._colsize_list[index:index + grid_span])
        return int(self._table_width[0] * ratio)

    def append(self, contents):
        """Append contents to the current cell."""
        row = self._current_target[self._current_row_index]
        row[self._current_cell_index][1].append(contents)

    def to_xml(self):
        """Return the w:tbl element with all its rows."""
        table = docx.make_table(
            self._style,
            self._table_width[1],
            self._indent, self._align,
            (self._table_width[0] * col for col in self._colsize_list),
            self._head, self._stub > 0)
        for index, row in enumerate(self._head):
            table.append(self.make_row(index, row, True))
        for index, row in enumerate(self._body):
            table.append(self.make_row(index, row, False))
        return table

    def make_row(self, index, row, is_head):
        # Non-first header needs tblHeader to be applied first row style
        """Return the w:tr element for one row of the grid."""
        set_tbl_header = is_head and (self._set_table_header or index > 0)
        rotation = is_head and (self._rotation_header_height is not None)
        if rotation:
            height = self._rotation_header_height * self._table_width[0] // 100
        else:
            height = None
        row_elem = docx.make_row(
            index, is_head, self._cant_split_row, set_tbl_header, height)
        keep_next = self._set_keep_next(is_head, index)
        for idx, elem in enumerate(row):
            if elem is None: # Merged with the previous cell
                continue
            vmerge, cell = elem
            row_elem.append(
                self.make_cell(idx, vmerge, cell, row, keep_next, rotation))
        return row_elem

    def make_cell(self, index, vmerge, cell, row, keep_next, rotation):
        """Return the w:tc element for one cell of the grid.

        Word requires a paragraph as the last thing in a cell, so an empty one is
        added where the contents do not end in one.
        """
        grid_span = Table._get_grid_span(row, index)
        is_stub = index < self._stub
        if self._no_wrap:
            # Word honours w:noWrap only where the cell is free to grow, so a
            # cell that must not wrap cannot carry a preferred width as well.
            cellsize = None
            no_wrap = True
        elif self._fit_content:
            cellsize = None
            no_wrap = is_stub
        else:
            cellsize = sum(self._colsize_list[index:index + grid_span])
            no_wrap = None
        cell_elem = docx.make_cell(
            index, is_stub, cellsize, grid_span, vmerge, rotation, no_wrap)

        contents_types = (ParagraphElement, TableElement, SdtElement)
        # The last element must be paragraph for Microsoft word
        last = next(
            (e for e in reversed(cell) if isinstance(e, contents_types)),
            None)
        if last is None or isinstance(last, TableElement):
            cell.append(Paragraph())

        if keep_next:
            first = next(e for e in cell if isinstance(e, contents_types))
            first.keep_next()
        cell_elem.extend(c.to_xml() for c in cell)
        return cell_elem

    def _reset_colsize_list(self):
        """Recompute the column widths as ratios of the collected colspecs."""
        total = float(sum(self._colspec_list))
        self._colsize_list = [colspec / total for colspec in self._colspec_list]

    @staticmethod
    def _get_grid_span(row, cell_index):
        """Return how many columns the cell at cell_index spans."""
        grid_span = 1
        for cell in row[cell_index + 1:]:
            if cell is not None:
                break
            grid_span += 1
        return grid_span

    def _set_keep_next(self, is_head, index):
        """Return whether the row at index should be kept with the next one."""
        if self._keep_next == 0:
            return False
        if self._keep_next == 1:
            return is_head
        if self._keep_next == 2:
            return (is_head or not self._head) and index == 0
        if self._keep_next == 3:
            return True
        return False

class TOC(SdtElement):
    """A table of contents field."""
    def __init__(
            self, title, title_style_id, maxlevel, bookmark, paragraph_width,
            outlines):
        """Store the title, depth and outline entries of the contents."""
        self._title = title
        self._title_style_id = title_style_id
        self._maxlevel = maxlevel
        self._bookmark = bookmark
        self._paragraph_width = paragraph_width
        self._outlines = outlines

    def to_xml(self):
        """Return the structured document tag holding the field."""
        return docx.make_table_of_contents(
            self._title, self._title_style_id,
            self._maxlevel, self._bookmark, self._paragraph_width,
            self._outlines)

class SectionPropertyManager(object):
    """Tracks which section property the document is currently in.

    A change of section is not written where it happens: it is held back
    until the next content is appended, and dropped again if the document
    rotates straight back, so an empty section is never emitted.
    """
    def __init__(self, default_orient, sect_props):
        """Start in the default orientation, with no pending section."""
        self._default_orient = default_orient
        self._sect_props = sect_props
        self._current_orient = self._default_orient
        self._current_sect_index = {'portrait': 0, 'landscape': 0}
        self._last_section = None
        self._no_title_page = [False, False] # [current, last]

    def get_current_section(self):
        """Return the section property in force now."""
        orient, index = self._current_section
        return self._sect_props[orient][index]

    def get_last_section(self):
        """Return the pending section property and whether it hides the title page."""
        if self._last_section is None:
            orient, index = self._current_section
            no_title_page = self._no_title_page[0]
        else:
            orient, index = self._last_section
            no_title_page = self._no_title_page[1]
        return self._sect_props[orient][index], no_title_page

    def rotate_to(self, orient=None):
        """Switch to an orientation, defaulting back to the document's own."""
        if orient is None:
            orient = self._default_orient
        if self._current_orient != orient:
            if self._last_section is not None:
                # last_orient must be equal to orient, then addition of section
                # property is enable to be postponed
                self._last_section = None
                self._no_title_page[0] = self._no_title_page[1]
            else:
                self._last_section = self._current_section
                self._no_title_page[1] = self._no_title_page[0]
                self._no_title_page[0] = True
            self._current_orient = orient

    def set_current_section(self, index, orient=None):
        """Switch to the indexth section property of an orientation."""
        if orient is None:
            orient = self._default_orient
        if index >= len(self._sect_props[orient]):
            raise RuntimeError("Not found %dth %s section" % (index, orient))
        if self._last_section is None:
            self._last_section = self._current_section
            self._no_title_page[1] = self._no_title_page[0]
            self._no_title_page[0] = False
        else:
            if self._last_section == (orient, index):
                self._last_section = None
                self._no_title_page[0] = self._no_title_page[1]
            else:
                self._no_title_page[0] = False
        self._current_orient = orient
        self._current_sect_index[orient] = index

    def pop_last_section(self):
        """Take the pending section property, or None if there is none."""
        if self._last_section is None:
            return None
        orient, index = self._last_section
        section_prop = self._sect_props[orient][index]
        self._last_section = None
        return section_prop, self._no_title_page[1]

    @property
    def _current_section(self):
        """The (orientation, index) pair in force now."""
        current_orient = self._current_orient
        current_index = self._current_sect_index[current_orient]
        return (current_orient, current_index)

class Document(object):
    """The body of the document being assembled.

    Wraps the body element with the bookkeeping that cannot be done per
    element: pending page breaks, pending section properties, and the spacer
    paragraph below a table.
    """
    def __init__(self, body, default_orient, sect_props):
        """Store the body element and start its section manager."""
        self._body = body
        self._add_pagebreak = False
        self._section = SectionPropertyManager(default_orient, sect_props)
        self._last_table_bottom_margin_index = None

    def add_pagebreak(self):
        """Ask for a page break before the next content appended."""
        self._add_pagebreak = True

    def add_last_section_property(self):
        """Append the final section property, which closes the document body."""
        section_prop, no_title_page = self._section.get_last_section()
        section_prop = docx.copy_section_property(section_prop, no_title_page)
        self._body.append(section_prop)

    def set_page_oriented(self, orient=None):
        """Rotate to an orientation, defaulting back to the document's own."""
        self._section.rotate_to(orient)

    def set_section(self, index, orient):
        """Switch to the indexth section property of an orientation."""
        self._section.set_current_section(index, orient)

    def get_current_page_width(self):
        """Return the printable width of the current section, in twips."""
        return docx.get_contents_width(self._section.get_current_section())

    def get_current_page_height(self):
        """Return the printable height of the current section, in twips."""
        return docx.get_contents_height(self._section.get_current_section())

    def append(self, contents):
        """Append contents, applying any pending break or section property.

        A page break makes the spacer paragraph below a table pointless, so it is
        removed rather than left at the foot of the page.
        """
        xml = contents.to_xml()
        if not isinstance(contents, BookmarkElement):
            self._add_section_prop_if_necessary()
            if self._add_pagebreak:
                self._remove_last_table_bottom_margin_paragraph()
                docx.add_page_break_before_to_first_paragraph(xml)
                self._add_pagebreak = False
            if Document._is_table_bottom_margin_paragraph(contents):
                self._last_table_bottom_margin_index = len(self._body)
            else:
                self._last_table_bottom_margin_index = None
        self._body.append(xml)

    def _remove_last_table_bottom_margin_paragraph(self):
        """Drop the spacer paragraph below the last table, if one is still pending."""
        if self._last_table_bottom_margin_index is not None:
            del self._body[self._last_table_bottom_margin_index]
        self._last_table_bottom_margin_index = None

    def _add_section_prop_if_necessary(self):
        """Append the pending section property, if a section change is waiting."""
        section = self._section.pop_last_section()
        if section is None:
            return
        self._remove_last_table_bottom_margin_paragraph()
        section_prop, no_title_page = section
        section_prop = docx.copy_section_property(section_prop, no_title_page)
        self._body.append(docx.make_section_prop_paragraph(section_prop))

    @staticmethod
    def _is_table_bottom_margin_paragraph(contents):
        """Return true if contents is the spacer paragraph added below a table."""
        return (isinstance(contents, Paragraph)
                and contents.is_table_bottom_margin_style)

class Raw(object):
    """Contents that are already an XML element, from a raw docx node."""
    def __init__(self, raw_xml):
        """Store the element."""
        self._raw_xml = raw_xml

    def to_xml(self):
        """Return the element as it was given."""
        return self._raw_xml

class LiteralBlock(ParagraphElement):
    """A code block, as a single paragraph.

    Pygments has already produced the runs; this only wraps them in a
    paragraph with the block's own indent and style.
    """
    def __init__(self, highlighted, style_id, indent, right_indent, keep_lines):
        """Store the highlighted XML and the paragraph properties."""
        self._args = [highlighted, style_id, indent, right_indent, keep_lines]
        self._keep_next = False

    def keep_next(self):
        """Ask Word to keep this block on the same page as the next paragraph."""
        self._keep_next = True

    def to_xml(self):
        """Return the w:p element holding the highlighted code."""
        highlighted, style_id, indent, right_indent, keep_lines = self._args
        highlighted = docx.fromstring(highlighted)[0]
        para = docx.make_paragraph(
            indent, right_indent, style_id, None,
            keep_lines, self._keep_next, None,
            properties=docx.get_paragraph_properties(highlighted))
        para.extend(docx.get_paragraph_contents(highlighted))
        return para

class LiteralBlockTable(TableElement):
    """A code block with line numbers, as a two column table.

    The numbers go in a narrow first column, the code in the second, with the
    borders drawn so the rows read as one block rather than a grid.
    """
    def __init__(
            self, highlighted, top_space,
            style_id, table_width, indent, keep_next):
        """Store the highlighted XML and the table properties."""
        self._args = [highlighted, top_space, style_id, table_width, indent]
         # 0: not set, 1: set header, 2: set first row, 3: set all rows
        self._keep_next = 3 if keep_next else 0

    def keep_next(self):
        """Ask Word to keep the first row on the same page as the next paragraph."""
        self._keep_next = max(2, self._keep_next)

    def to_xml(self):
        """Return the w:tbl element holding the numbered code."""
        highlighted, top_space, style_id, table_width, indent = self._args
        org_tbl = docx.fromstring(highlighted)[0]
        table = docx.make_table(
            None, table_width[1], indent, None,
            [table_width[0] * 0.1, table_width[0] * 0.9], False, True,
            properties=[
                docx.make_table_cell_spacing_property(None),
                docx.make_table_cell_margin_property(
                    top=None, left=108, bottom=None, right=108),
            ])
        no_spacing = docx.make_paragraph_spacing_property(before=0, after=0)
        lineno_border = docx.make_paragraph_border_property(
            top=None, bottom=None, left=None, right=None)
        middle_border = docx.make_paragraph_border_property(
            top=None, bottom=None)
        last_index = len(org_tbl) - 1
        if last_index == 0:
            border = {0: docx.make_paragraph_border_property()}
        else:
            border = {
                0: docx.make_paragraph_border_property(bottom=None),
                last_index: docx.make_paragraph_border_property(top=None),
            }

        for index, org_row in enumerate(org_tbl):
            row = docx.make_row(index, False, False, False, None)
            if index == 0:
                spacing = docx.make_paragraph_spacing_property(
                    before=(top_space or 0), after=0)
            else:
                spacing = no_spacing
            cell1 = docx.make_cell(
                0, True, None, 1, None, False, no_wrap=True, valign='top')
            keep_next = self._is_keep_next(index)
            para1_props = docx.get_paragraph_properties(org_row[0][0])
            para1_props.extend([spacing, lineno_border])
            para1 = docx.make_paragraph(
                None, None, style_id, 'right', False, keep_next, None,
                properties=para1_props)
            para1.extend(docx.get_paragraph_contents(org_row[0][0]))
            cell1.append(para1)
            row.append(cell1)

            cell2 = docx.make_cell(
                1, False, 0.99, 1, None, False, no_wrap=False, valign='top')
            para2_props = docx.get_paragraph_properties(org_row[1][0])
            para2_props.extend([no_spacing, border.get(index, middle_border)])
            para2 = docx.make_paragraph(
                None, None, style_id, None, False, False, None,
                properties=para2_props)
            para2.extend(docx.get_paragraph_contents(org_row[1][0]))
            cell2.append(para2)
            row.append(cell2)
            table.append(row)
        return table

    def _is_keep_next(self, index):
        """Return whether the row at index should be kept with the next one."""
        return (self._keep_next == 2 and index == 0) or (self._keep_next == 3)

class MathBlock(ParagraphElement):
    """A displayed equation, as a paragraph of OMML."""
    def __init__(self, equations, indent, right_indent, style_id):
        """Store the equations and the paragraph properties."""
        self._equations = equations
        self._indent = indent
        self._right_indent = right_indent
        self._style_id = style_id
        self._keep_next = False

    def keep_next(self):
        """Ask Word to keep this block on the same page as the next paragraph."""
        self._keep_next = True

    def to_xml(self):
        """Return the w:p element holding the equations."""
        para = docx.make_paragraph(
            self._indent, self._right_indent, self._style_id, None,
            False, self._keep_next, None)
        para.append(docx.make_omath_paragraph(self._equations))
        return para

class ContentsList(object):
    """A list of contents objects, built up while visiting a node's children."""
    def __init__(self):
        """Start with an empty list."""
        self._contents_list = []

    def append(self, contents):
        """Append contents to the list."""
        self._contents_list.append(contents)

    def __iter__(self):
        """Iterate over the contents."""
        return iter(self._contents_list)

    def __len__(self):
        """Return the number of contents."""
        return len(self._contents_list)

    def __getitem__(self, key):
        """Return the contents at a key."""
        return self._contents_list[key]

class FixedTopParagraphList(ContentsList):
    """A contents list whose first paragraph is made in advance.

    A list item or a definition starts with a paragraph that carries the
    bullet or the term, so the first content visited has to merge into it
    rather than follow it.
    """
    def __init__(self, top_paragraph):
        """Start the list with the given paragraph already in it."""
        super(FixedTopParagraphList, self).__init__()
        self._top_paragraph = top_paragraph
        self._available_top_paragraph = True
        super(FixedTopParagraphList, self).append(self._top_paragraph)

    def append(self, contents):
        """Append contents, merging the first one into the top paragraph.

        Only a paragraph of the default style merges; anything else, a table say,
        would lose its own formatting, so it follows instead.
        """
        if len(self) == 1:
            if isinstance(contents, BookmarkElement):
                self._top_paragraph.append(contents)
                return
            if self._available_top_paragraph:
                self._available_top_paragraph = False
                if isinstance(contents, Paragraph) and contents.is_default_style:
                    self._top_paragraph.append(contents)
                    return
        super(FixedTopParagraphList, self).append(contents)

class DefinitionListItem(ContentsList):
    """A contents list that remembers the last term paragraph added."""
    def __init__(self):
        """Start with an empty list and no term."""
        super(DefinitionListItem, self).__init__()
        self._last_term = None

    @property
    def last_term(self):
        """The paragraph of the term added last, or None."""
        return self._last_term

    def add_term(self, term_paragraph):
        """Append a term paragraph and remember it."""
        self._contents_list.append(term_paragraph)
        self._last_term = term_paragraph

class Contenxt(object):
    """The indent, width and list depth in force at one point in the tree.

    The translator pushes one per nesting level, so a nested block knows how
    much room is left to it.
    """
    def __init__(self, indent, right_indent, width, list_level):
        """Store the indents, width and list level."""
        self.indent = indent
        self.right_indent = right_indent
        self.width = width
        self.list_level = list_level

    @property
    def paragraph_width(self):
        """The width left for a paragraph after both indents."""
        return self.width - self.indent - self.right_indent

class DocxTranslator(nodes.NodeVisitor):
    # pylint: disable=too-many-public-methods
    """Visitor to generate a docx document.

    :var builder: Sphinx builder.
    """

    TABLE_BOTTOM_MARGIN_STYLE_NAME = 'Table Bottom Margin'

    def __init__(self, document, builder):
        """Set up the composer, the stacks and the docxbuilder styles.

        The translator keeps three parallel stacks: _doc_stack of contents being
        built, _ctx_stack of the indent and width in force, and _docname_stack of
        the file being visited, which bookmarks are named after.
        """
        nodes.NodeVisitor.__init__(self, document)
        self._builder = builder
        self.builder = self._builder # Needs for graphviz.render_dot
        stylefile = builder.config['docx_style']
        if stylefile:
            stylefile = os.path.join(builder.confdir, os.path.join(stylefile))
        else: # Use default style file
            stylefile = os.path.join(
                os.path.dirname(__file__), 'docx/style.docx')
        self._docx = docx.DocxComposer(
            stylefile, int(builder.config['docx_coverpage']))
        default_orient, sect_props = self._docx.get_section_properties()
        self._doc_stack = []
        self._doc_stack.append(
            Document(self._docx.docbody, default_orient, sect_props))
        self._docname_stack = []
        self._section_level = 0
        self._ctx_stack = [
            Contenxt(0, 0, self._doc_stack[-1].get_current_page_width(), 0)
        ]
        self._relationship_stack = ['document']
        self._line_block_level = 0
        self._list_id_stack = []
        self._basic_indent = self._docx.get_indent('List Paragraph', 320)
        self._language = builder.config.highlight_language
        self._linenothreshold = sys.maxsize
        if is_sphinx_version_lower_than((1, 8, 0)):
            trim_doctest_flags = builder.config.trim_doctest_flags
        else:
            trim_doctest_flags = None
        self._highlighter = DocxPygmentsBridge(
            'html', builder.config.pygments_style, trim_doctest_flags)
        self._numsec_map = builder.make_numsec_map()
        self._numfig_map = builder.make_numfig_map()
        self._bookmark_id = self._docx.get_max_bookmark_id()
        self._bookmark_id_map = {} # bookmark name => BookmarkStart id
        self._logger = logging.getLogger('docxbuilder')

        self._create_docxbuilder_styles()
        self._bullet_list_id = self._docx.get_bullet_list_num_id('List Bullet')
        bullet_list_indents = self._docx.get_numbering_left('List Bullet')
        if not bullet_list_indents:
            self._logger.info('List Bullet style has no numbering style')
        self._bullet_list_indents = bullet_list_indents
        number_list_indents = self._docx.get_numbering_left('List Number')
        if not number_list_indents:
            self._logger.info('List Number style has no numbering style')
            self._number_list_indent = 0
        else:
            self._number_list_indent = number_list_indents[0]
        self._default_paragraph_style_stack = []
        self._append_default_paragraph_style('Body Text')
        self._class_style_stack = []

    def asbytes(self):
        """Return the finished document as the bytes of a .docx file."""
        props = self._builder.doc_properties
        props, invalids = docx.classify_properties(props)
        for key, reason in invalids.items():
            self._logger.warning(
                'invalid property is found in docx_documents "%s"(%s)'
                % (key, reason))
        props['core'].setdefault(
            'language', self._builder.config.language or 'en')
        return self._docx.asbytes(
            self._builder.config.docx_update_fields, props,
            self._builder.config.docx_bake_property_fields)

    def _get_custom_style(self, classes, style_type):
        """Return the style name mapped to one of the classes, or None."""
        custom_styles = self._builder.config.docx_style_names
        for cls in classes:
            style_name = custom_styles.get(cls)
            if style_name is None:
                continue
            if self._docx.get_style_id(style_name, style_type) is None:
                continue
            return style_name
        return None

    def _get_custom_charcter_styles(self, classes):
        """Yield the character style names mapped to the classes."""
        custom_styles = self._builder.config.docx_style_names
        return (custom_styles[c] for c in classes if c in custom_styles)

    def _push_class_style(self, node):
        """Apply the style a "rst-class" names, whatever node it is put on.

        HTML writes every class into the class attribute, where CSS can act on
        it; this is the general analogue.  A class listed in docx_style_names
        resolves to a paragraph style, which becomes the default for the
        paragraphs the node and its children produce, or failing that to a
        character style, which is pushed onto the paragraph being built.

        A paragraph made with an explicit style of its own - a title, a
        caption, a literal block - is unaffected, and so are the nodes in
        CLASS_STYLE_EXCLUDED_TAGS, which consume their classes themselves.

        Returns a token for _unwind_class_style, or None if nothing was pushed.
        """
        if not isinstance(node, nodes.Element):
            return None
        if (node.tagname in CLASS_STYLE_EXCLUDED_TAGS
                or isinstance(node, nodes.Admonition)):
            return None
        classes = node.get('classes', [])
        if not classes:
            return None
        style_name = self._get_custom_style(classes, 'paragraph')
        if style_name is not None:
            self._append_default_paragraph_style(style_name)
            return ('paragraph', None)
        style_name = self._get_custom_style(classes, 'character')
        if style_name is not None and isinstance(self._doc_stack[-1], Paragraph):
            # Remember the paragraph itself: by the time the node departs, its
            # own depart method may already have popped it off the doc stack.
            target = self._doc_stack[-1]
            self._push_style(style_name)
            return ('character', target)
        return None

    def _unwind_class_style(self, node):
        """Drop what _push_class_style pushed for the node, if anything.

        Everything pushed above the node is dropped with it, so a visit method
        that raises SkipNode part way through a subtree cannot leak a style
        into the rest of the document.
        """
        for index in range(len(self._class_style_stack) - 1, -1, -1):
            if self._class_style_stack[index][0] is node:
                break
        else:
            return
        while len(self._class_style_stack) > index:
            _node, token = self._class_style_stack.pop()
            if token is None:
                continue
            kind, target = token
            if kind == 'paragraph':
                self._pop_default_paragraph_style()
            else:
                target.pop_style()

    def _append_default_paragraph_style(self, style_name):
        """Push a style used by paragraphs that ask for none of their own."""
        self._default_paragraph_style_stack.append(
            self._docx.get_style_id(style_name, 'paragraph'))

    def _pop_default_paragraph_style(self):
        """Pop the most recently pushed default paragraph style."""
        self._default_paragraph_style_stack.pop()

    def _pop_and_append(self):
        """Pop the top contents and append it to the one below."""
        contents = self._doc_stack.pop()
        if isinstance(contents, ContentsList):
            for content in contents:
                self._doc_stack[-1].append(content)
        else:
            self._doc_stack[-1].append(contents)

    def _append_bookmark_start(self, ids):
        """Open a bookmark for each of the node ids."""
        docname = self._docname_stack[-1]
        for node_id in ids:
            name = make_bookmark_name(docname, node_id)
            self._bookmark_id += 1
            self._bookmark_id_map[name] = self._bookmark_id
            self._doc_stack[-1].append(BookmarkStart(self._bookmark_id, name))

    def _append_bookmark_end(self, ids):
        """Close the bookmarks opened for the node ids."""
        docname = self._docname_stack[-1]
        for node_id in ids:
            name = make_bookmark_name(docname, node_id)
            bookmark_id = self._bookmark_id_map.pop(name, None)
            if bookmark_id is None:
                continue
            self._doc_stack[-1].append(BookmarkEnd(bookmark_id))

    def _make_paragraph(
            self, indent=None, right_indent=None, style=None, align=None,
            keep_lines=False, keep_next=False,
            list_info=None, preserve_space=False):
        """Return a new paragraph, in the given style or the default one.

        A style of the document's own is looked up by name; without one the
        paragraph takes the default style currently pushed, which is what makes
        it eligible to merge into a list item or a definition.
        """
        if style is not None:
            style_id = self._docx.get_style_id(style, 'paragraph')
            if style == DocxTranslator.TABLE_BOTTOM_MARGIN_STYLE_NAME:
                style_kind = Paragraph.TABLE_BOTTOM_MARGIN_STYLE
            else:
                style_kind = Paragraph.DOCXBUILDER_STYLE
        else:
            style_id = self._default_paragraph_style_stack[-1]
            style_kind = Paragraph.DEFAULT_STYLE
        if align == 'default':
            align = 'center'
        return Paragraph(
            indent, right_indent, style_id, style_kind, align,
            keep_lines, keep_next, list_info, preserve_space)

    def _append_table(
            self, table_style, table_width, colsize_list, is_indent, align=None,
            in_single_page=False, row_splittable=True,
            header_in_all_page=False, rotation_header_height=None,
            fit_content=False, no_wrap=False, is_fixed_width=True):
        """Push a new table and the context its cells are laid out in.

        Returns the table, which the caller fills in through start_head,
        add_row and the visit methods of its children.
        """
        self._append_default_paragraph_style(None)
        table_style_id = self._docx.get_style_id(table_style, 'table')
        indent = self._ctx_stack[-1].indent if is_indent else 0
        if align == 'default':
            align = 'center'
        keep_next = 3 if in_single_page else 1
        max_table_width = table_width
        if is_fixed_width:
            table_width = float(table_width) / self._ctx_stack[-1].width
        else:
            table_width = None
        tbl = Table(
            table_style_id,
            (max_table_width, table_width),
            colsize_list, indent, align,
            keep_next, not row_splittable, header_in_all_page,
            rotation_header_height, fit_content, no_wrap)
        self._doc_stack.append(tbl)
        self._append_new_ctx(indent=0, right_indent=0, width=table_width)
        return tbl

    def _pop_and_append_table(self):
        """Pop the table, append it, and add the spacer paragraph below it.

        Word runs two adjacent tables together, so the spacer keeps them apart;
        Document drops it again where it would fall at a page break.
        """
        self._ctx_stack.pop()
        self._pop_and_append()
        # Append a paragaph as a margin between the table and the next element
        self._doc_stack[-1].append(
            self._make_paragraph(
                style=DocxTranslator.TABLE_BOTTOM_MARGIN_STYLE_NAME))
        self._pop_default_paragraph_style()

    def _add_table_cell(self, morerows=0, morecols=0):
        """Start a cell and narrow the context to what fits inside it.

        The cell margins come out of the usable width, so nested content is
        measured against the text area rather than the cell.
        """
        tbl = self._doc_stack[-1]
        tbl.add_cell(morerows, morecols)
        width = tbl.current_cell_width()
        if width is not None:
            margin = self._docx.get_table_cell_margin(tbl.style)
            self._ctx_stack[-1].width = width - margin

    def _push_style(self, style_name, based_style_name=None):
        """Push a character style, creating it from based_style_name if needed."""
        if based_style_name is not None:
            self._docx.create_style(
                'character', style_name, based_style_name, True, False)
        style_id = self._docx.get_style_id(style_name, 'character')
        if self._builder.config.docx_nested_character_style:
            style = self._docx.get_run_style_property(style_id)
        else:
            style = docx.make_run_style_property(style_id)
        self._doc_stack[-1].push_style(style)

    def _append_new_ctx(
            self, indent=None, right_indent=None, width=None):
        """Push a context, inheriting whatever is not given from the current one."""
        if indent is None:
            indent = self._ctx_stack[-1].indent
        if right_indent is None:
            right_indent = self._ctx_stack[-1].right_indent
        if width is None:
            width = self._ctx_stack[-1].width
        self._ctx_stack.append(Contenxt(indent, right_indent, width, 0))

    def _set_page_oriented(self):
        """Switch to landscape and push a context of the wider page."""
        self._doc_stack[-1].set_page_oriented('landscape')
        self._append_new_ctx(
            indent=0, right_indent=0,
            width=self._doc_stack[-1].get_current_page_width())

    def _clear_page_oriented(self):
        """Pop the landscape context and rotate back."""
        self._ctx_stack.pop()
        self._doc_stack[-1].set_page_oriented()

    def _get_numsec(self, ids):
        """Return the section number for a node, as a display prefix.

        The first section of a file is keyed without a hash, so it is looked up
        separately.
        """
        for node_id in ids:
            num = self._numsec_map.get('%s/#%s' % (self._docname_stack[-1], node_id))
            if num:
                return format_secnumber(num)
        # First section of each file has no hash
        num = self._numsec_map.get('%s/' % self._docname_stack[-1], None)
        if num:
            return format_secnumber(num)
        return None

    def _get_numfig(self, figtype, ids):
        """Return the figure or table number for a node, as a display prefix."""
        item = self._numfig_map.get(figtype)
        if item is None:
            return None
        prefix, num_map = item
        if prefix is None:
            return None
        for node_id in ids:
            num = num_map.get('%s/%s' % (self._docname_stack[-1], node_id))
            if num:
                return prefix % ('.'.join(map(str, num)) + ' ')
        return None

    def _get_table_option(self, classes, option, default_value):
        """Return a table option, from the node classes or the configuration.

        A 'docx-<option>' class turns it on and 'docx-no-<option>' off, either
        overriding docx_table_options.
        """
        if 'docx-%s' % option in classes:
            return True
        if 'docx-no-%s' % option in classes:
            return False
        return self._builder.config.docx_table_options.get(
            option.replace('-', '_'), default_value)

    @staticmethod
    def _get_rotation_header_height(classes):
        """Return the header height from a 'docx-rotation-header-N' class, or None."""
        for cls in reversed(classes):
            match = re.match(r'^docx-rotation-header-(\d+)$', cls)
            if not match:
                continue
            return int(match.group(1))
        return None

    def _is_landscape_table(self, node):
        """Return true if the table should go on a landscape page.

        Only a table directly in the body can rotate the page, and a table wider
        than docx_table_options['landscape_columns'] does so by itself.
        """
        if not isinstance(self._doc_stack[-1], Document):
            return False
        option = self._get_table_option(node.get('classes'), 'landscape', None)
        if option is not None:
            return option
        landscape_columns = self._builder.config.docx_table_options.get(
            'landscape_columns', 0)
        if landscape_columns < 1:
            return False
        return landscape_columns <= count_colspec(node)

    def _is_landscape_figure(self, node):
        """Return true if the figure should go on a landscape page."""
        if not isinstance(self._doc_stack[-1], Document):
            return False
        return 'docx-landscape' in node.get('classes')

    def _set_section(self, node):
        """Switch the section property if the node has a 'docx-section-*' class."""
        for cls in node.get('classes'):
            match = SECTION_CLASS_PATTERN.match(cls)
            if not match:
                continue
            try:
                self._doc_stack[0].set_section(
                    int(match.group(2)), match.group(1))
            except RuntimeError as e:
                self._logger.warning(e, location=node)

    def _check_section_class(self, node):
        '''Warn about a 'docx-section-*' class which will have no effect.

           The class does something only on a section node, and only if it is
           spelled exactly right. Both mistakes are otherwise silent: the
           document builds, and the section property is quietly not switched.
        '''
        if not isinstance(node, nodes.Element):
            return
        for cls in node.get('classes', []):
            if not cls.startswith('docx-section'):
                continue
            if SECTION_CLASS_PATTERN.match(cls) is None:
                self._logger.warning(
                    'Unknown section class "%s" is ignored;'
                    ' expected docx-section-N, docx-section-portrait-N'
                    ' or docx-section-landscape-N' % cls, location=node)
            elif not isinstance(node, nodes.section):
                self._logger.warning(
                    'Section class "%s" is ignored because it is applied to'
                    ' a %s node; only a section switches the section'
                    ' property. Note that a "rst-class" directive applies to'
                    ' the element following it, so it has to be placed'
                    ' directly above a section title.'
                    % (cls, node.tagname), location=node)

    def dispatch_visit(self, node):
        """Check the node's classes, apply them, then dispatch to its visit."""
        self._check_section_class(node)
        self._class_style_stack.append((node, self._push_class_style(node)))
        try:
            return nodes.NodeVisitor.dispatch_visit(self, node)
        except BaseException:
            # SkipNode and SkipDeparture both mean dispatch_depart will never
            # run for this node, so unwind here rather than leak the style.
            self._unwind_class_style(node)
            raise

    def dispatch_departure(self, node):
        """Dispatch to the node's depart method, then drop its class style."""
        try:
            return nodes.NodeVisitor.dispatch_departure(self, node)
        finally:
            self._unwind_class_style(node)

    def _convert_math(self, latex, node):
        """Convert LaTeX to OMML, warning and falling back to the raw text."""
        try:
            return latex2omml(latex)
        except Exception as e: # pylint: disable=broad-except
            self._logger.warning(
                'Failed to convert math %s: %s', latex, e, location=node)
            return docx.make_omath_run(latex)

    def visit_admonition_node(self, node, add_title=False):
        """Insert a table of admonition represented by the node.

        This function shall be called from visit method for the node

        :param node: A node which represents an admonition.
        :param add_title: If add_title is true, the text corresponding to
            the node tagname is used as the admonition title. If false,
            the first child of the node is used.
        """
        self._append_bookmark_start(node.get('ids', []))
        self._append_default_paragraph_style(None)
        self._doc_stack.append(ContentsList())
        if add_title:
            para = self._make_paragraph()
            para.add_text(admonitionlabels[node.tagname] + ':')
            self._doc_stack[-1].append(para)

    def depart_admonition_node(
            self, node, style=None, align='center', margin='10%'):
        """Insert a table of admonition represented by the node.

        This function shall be called from depart method for the node

        :param node: A node which represents an admonition.
        :param style: A style name applied to the admonition table. If
            style is None, the node's admonition class or tagname is used.
        :param align: Alignment of the admonition table.
        :param margin: A total margin between the table and page.
        """
        contents = self._doc_stack.pop()
        if align is not None:
            base_width = self._ctx_stack[-1].width
            is_indent = False
        else:
            base_width = self._ctx_stack[-1].paragraph_width
            is_indent = True
        if isinstance(margin, int):
            table_width = base_width - margin
        else:
            table_width = max(
                base_width - convert_to_twip_size(margin, base_width), 1)
        if style is None:
            #TODO: Make sure that the style is at least admonition
            style = next((
                ' '.join(word.capitalize() for word in c.split('-'))
                for c in node.get('classes') if c.startswith('admonition-')),
                         'Admonition %s' % node.tagname.capitalize())
            self._docx.create_style('table', style, 'Based Admonition', True)
        # An admonition must not be torn across a page break: in_single_page
        # keeps every row with the next one, row_splittable=False adds
        # w:cantSplit so no single row breaks either. Word still splits one
        # that cannot fit on a page at all.
        tbl = self._append_table(
            style, table_width, [1.0], is_indent, align, fit_content=False,
            in_single_page=True, row_splittable=False)
        tbl.start_head()
        tbl.add_row()
        self._add_table_cell()
        # The header row takes the bookmark starts, the title, and any
        # bookmark ends that immediately follow it; everything after that is
        # the body. Splitting anywhere else would put a bookmark start in one
        # cell and its end in another.
        idx = -1
        for idx, content in enumerate(contents):
            tbl.append(content)
            if not isinstance(content, BookmarkElement):
                break
        idx = idx + 1
        for idx, content in enumerate(contents[idx:], idx):
            if not isinstance(content, BookmarkEnd):
                break
            tbl.append(content)
        body_contents = contents[idx:]
        if body_contents:
            tbl.start_body()
            tbl.add_row()
            self._add_table_cell()
            for content in body_contents:
                tbl.append(content)
        self._pop_and_append_table()
        self._pop_default_paragraph_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_image_node(self, node, alt, get_filepath):
        """Insert an image represented by the node.

        This function shall be called from visit method for the node

        :param node: A node which represents an image.
        :param alt: An alternative text used when get_filepath is unable to
            return a valid image path. alt may be a tuple of the text and
            a string which specified an language in order to highlight the
            text.
        :param get_filepath: A function which extract a path of the image
            from the node. This function must take the translator as first
            parameter, and the node as second parameter.
        """
        self._append_bookmark_start(node.get('ids', []))

        if not isinstance(self._doc_stack[-1], Paragraph):
            style, align, keep_next = image_block_properties(node)
            self._doc_stack.append(self._make_paragraph(
                self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
                style=style, align=align, keep_next=keep_next))
            needs_pop = True
        else:
            needs_pop = False

        if isinstance(alt, tuple):
            alt, alt_lang = alt
        else:
            alt_lang = None
        try:
            filepath = get_filepath(self, node)
            if filepath is None or not os.path.exists(filepath):
                raise RuntimeError('Failed to get filepath')
            width, height = self._get_image_scaled_size(node, filepath)
            rid, svg_rid = self._docx.add_image_relationship(
                filepath, self._relationship_stack[-1])
            filename = os.path.basename(filepath)
            self._doc_stack[-1].add_picture(
                rid, self._docx.new_id(), filename, width, height, alt,
                svg_rid=svg_rid)
        except Exception as e: # pylint: disable=broad-except
            self._logger.warning(e, location=node)
            if alt_lang is not None and needs_pop:
                highlighted = self._highlighter.highlight_block(alt, alt_lang)
                literal_block = LiteralBlock(
                    highlighted,
                    self._docx.get_style_id('LiteralBlock', 'paragraph'),
                    0, 0, False)
                width = convert_to_cm_size(self._ctx_stack[-1].paragraph_width)
                self._doc_stack[-1].add_textbox(
                    'width:%fcm' % width, 'white', [literal_block])
            else:
                self._push_style('Problematic')
                self._doc_stack[-1].add_text(alt)
                self._doc_stack[-1].pop_style()

        if needs_pop:
            self._pop_and_append()

        self._append_bookmark_end(node.get('ids', []))
        raise nodes.SkipNode

    def visit_math_block_node(self, node, latex):
        """Insert a displayed equation represented by the node.

        This function shall be called from visit method for the node.

        :param node: A node which represents a math block.
        :param latex: The LaTeX source, blank line separated for several
            equations in the one block.
        """
        self._append_bookmark_start(node.get('ids', []))
        equations = [
            self._convert_math(eq, node) for eq in re.split(r'\n{2,}', latex)]
        self._doc_stack[-1].append(MathBlock(
            equations,
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
            self._docx.get_style_id('Math Block', 'paragraph')))
        self._append_bookmark_end(node.get('ids', []))
        raise nodes.SkipNode

    def visit_start_of_file(self, node):
        """Enter an included file, breaking the page if it is shallow enough."""
        self._docname_stack.append(node['docname'])
        self._append_bookmark_start([''])
        config = self._builder.config
        if (self._section_level < config.docx_pagebreak_before_file
                and isinstance(self._doc_stack[-1], Document)):
            self._doc_stack[-1].add_pagebreak()
        self._append_bookmark_start(node.get('ids', []))

    def depart_start_of_file(self, node):
        """Leave an included file."""
        self._append_bookmark_end(node.get('ids', []))
        self._append_bookmark_end([''])
        self._docname_stack.pop()

    def visit_Text(self, node): # pylint: disable=invalid-name
        """Add the text of the node to the current paragraph."""
        self._doc_stack[-1].add_text(node.astext())

    def depart_Text(self, node): # pylint: disable=invalid-name
        """Do nothing: the text was added on visit."""
        pass

    def visit_document(self, node):
        """Enter the document, bookmarking it under its docname."""
        self._docname_stack.append(node['docname'])
        self._append_bookmark_start([''])

    def depart_document(self, _node):
        """Leave the document and write the closing section property."""
        self._append_bookmark_end([''])
        self._docname_stack.pop()
        self._doc_stack[-1].add_last_section_property()

    def visit_title(self, node):
        """Start the title paragraph, in the style its parent calls for.

        A section title becomes a heading of the current level; a table, an
        admonition and anything else each get their own style.
        """
        self._append_bookmark_start(node.get('ids', []))
        if isinstance(node.parent, nodes.table):
            style = 'Table Caption'
            title_num = self._get_numfig('table', node.parent['ids'])
            indent = self._ctx_stack[-1].indent
            right_indent = self._ctx_stack[-1].right_indent
            align = node.parent.get('align')
        elif isinstance(node.parent, nodes.section):
            style = 'Heading %d' % self._section_level
            self._docx.create_style('paragraph', style, 'Heading', False)
            title_num = self._get_numsec(node.parent['ids'])
            indent = None
            right_indent = None
            align = None
        elif isinstance(node.parent, nodes.Admonition):
            style = None # admonition's style is customized by Admonition
            title_num = None
            indent = self._ctx_stack[-1].indent
            right_indent = self._ctx_stack[-1].right_indent
            align = None
        else:
            style = '%s Title Heading' % node.parent.tagname.capitalize()
            self._docx.create_style('paragraph', style, 'Title Heading', True)
            title_num = None
            indent = self._ctx_stack[-1].indent
            right_indent = self._ctx_stack[-1].right_indent
            align = None
        self._doc_stack.append(self._make_paragraph(
            indent, right_indent, style, align, keep_next=True))
        if title_num is not None:
            self._doc_stack[-1].add_text(title_num)

    def depart_title(self, node):
        """Finish the title paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_subtitle(self, node):
        """Start the subtitle paragraph, in a style named after its parent."""
        self._append_bookmark_start(node.get('ids', []))
        style = '%s Subtitle Heading' % node.parent.tagname.capitalize()
        self._docx.create_style('paragraph', style, 'Subtitle Heading', True)
        self._doc_stack.append(self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
            style))

    def depart_subtitle(self, node):
        """Finish the subtitle paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_section(self, node):
        """Enter a section, breaking the page if it is shallow enough."""
        self._set_section(node)
        config = self._builder.config
        if (self._section_level < config.docx_pagebreak_before_section
                and isinstance(self._doc_stack[-1], Document)):
            self._doc_stack[-1].add_pagebreak()
        self._append_bookmark_start(node.get('ids', []))
        self._section_level += 1

    def depart_section(self, node):
        """Leave a section."""
        self._section_level -= 1
        self._append_bookmark_end(node.get('ids', []))

    def visit_topic(self, node):
        """Start collecting a topic, which becomes a textbox."""
        self._append_bookmark_start(node.get('ids', []))
        self._append_new_ctx(width=self._ctx_stack[-1].paragraph_width - 100)
        self._doc_stack.append(ContentsList())

    def depart_topic(self, node):
        """Put the collected topic into a shaded textbox."""
        width = convert_to_cm_size(self._ctx_stack[-1].paragraph_width)
        self._ctx_stack.pop()
        para = self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
            align='center')
        # TODO: enable to configure color
        para.add_textbox('width:%fcm' % width, '#ddeeff', self._doc_stack.pop())
        self._doc_stack[-1].append(para)
        self._append_bookmark_end(node.get('ids', []))

    def visit_sidebar(self, node):
        """Start collecting a sidebar, which becomes a floating textbox."""
        self._append_bookmark_start(node.get('ids', []))
        self._append_new_ctx(width=self._ctx_stack[-1].paragraph_width / 2)
        self._doc_stack.append(ContentsList())

    def depart_sidebar(self, node):
        # TODO: enable to configure color, width, and position
        """Put the collected sidebar into a textbox floated to the right."""
        width = convert_to_cm_size(self._ctx_stack[-1].paragraph_width)
        self._ctx_stack.pop()
        style = ';'.join([
            'width:%fcm' % width,
            'mso-position-horizontal:right',
            'mso-position-vertical-relative:text',
            'position:absolute',
        ])
        wrap_style = {'type': 'square', 'anchory': 'text', 'side': 'left',}
        para = self._make_paragraph()
        para.add_textbox(style, '#ddeeff', self._doc_stack.pop(), wrap_style)
        self._doc_stack[-1].append(para)
        self._append_bookmark_end(node.get('ids', []))

    def visit_transition(self, _node):
        """Emit an empty paragraph carrying the Transition style's border."""
        self._doc_stack[-1].append(self._make_paragraph(style='Transition'))

    def depart_transition(self, node):
        """Do nothing: the transition was emitted on visit."""
        pass

    def visit_paragraph(self, node):
        """Start a paragraph at the current indent."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack.append(self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent))

    def depart_paragraph(self, node):
        """Finish the paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_compound(self, node):
        """Bookmark the compound; its children render themselves."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_compound(self, node):
        """Close the compound's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_container(self, node):
        """Bookmark the container; its children render themselves."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_container(self, node):
        """Close the container's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_literal_block(self, node):
        """Highlight a code block and push it as a paragraph or a table.

        A parsed-literal has markup of its own, so it is built run by run
        instead. Line numbers need a two column table; without them the block is
        one paragraph. Short blocks are kept together on a page. ``:force:``
        highlights code the lexer cannot read, rather than falling back to plain
        text.
        """
        self._append_bookmark_start(node.get('ids', []))
        text = node.astext()
        # Short blocks read badly split over a page break; long ones would
        # leave too much white space to be worth keeping together.
        keep_lines = (text.count('\n') + 1 < 20)
        if node.rawsource != text: # Maybe parsed-literal
            self._doc_stack.append(self._make_paragraph(
                self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
                'Literal Block', keep_lines=keep_lines, preserve_space=True))
            return

        language = node.get('language', self._language)
        linenos = node.get(
            'linenos',
            (node.rawsource.count('\n') >= self._linenothreshold - 1))
        # A copy: the arguments are the node's own dictionary, and force is
        # this build's answer to the question, not something to leave behind on
        # the node for the next builder to read.
        highlight_args = dict(node.get('highlight_args', {}))
        highlight_args['force'] = node.get('force', False)
        config = self._builder.config
        opts = (config.highlight_options
                if language == config.highlight_language else {})
        highlighted = self._highlighter.highlight_block(
            node.rawsource, language,
            linenos=linenos, opts=opts, location=node, **highlight_args)
        style_id = self._docx.get_style_id('Literal Block', 'paragraph')
        ctx = self._ctx_stack[-1]
        if linenos:
            table_width = ctx.paragraph_width
            border_info = self._docx.get_border_info(style_id, 'top')
            if border_info is not None:
                top_space = int(
                    border_info.get('size', 1) * 2.5 +
                    border_info.get('space', 0) * 20)
            else:
                top_space = 0
            block = LiteralBlockTable(
                highlighted, top_space, style_id,
                (table_width, float(table_width) / ctx.width),
                ctx.indent, keep_lines)
        else:
            block = LiteralBlock(
                highlighted, style_id,
                ctx.indent, ctx.right_indent, keep_lines)
        self._doc_stack.append(block)
        raise nodes.SkipChildren

    def depart_literal_block(self, node):
        """Append the code block, with a spacer below it if it was a table."""
        if isinstance(self._doc_stack[-1], LiteralBlockTable):
            self._pop_and_append()
            self._doc_stack[-1].append(
                self._make_paragraph(
                    style=DocxTranslator.TABLE_BOTTOM_MARGIN_STYLE_NAME))
        else:
            self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_doctest_block(self, node):
        """Highlight a doctest block as Python, whatever the default language."""
        org_lang = self._language
        self._language = 'python3'
        try:
            self.visit_literal_block(node)
        finally:
            self._language = org_lang

    def depart_doctest_block(self, node):
        """Append the doctest block."""
        self.depart_literal_block(node)

    def visit_math_block(self, node):
        """Insert the block's equations."""
        self.visit_math_block_node(node, node.astext())

    def visit_line_block(self, node):
        """Start a line block; the whole block is one paragraph."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack.append(self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent))
        self._line_block_level += 1

    def depart_line_block(self, node):
        """Finish the line block."""
        self._line_block_level -= 1
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_line(self, node):
        """Break to a new line and indent it by its nesting depth."""
        self._append_bookmark_start(node.get('ids', []))
        if self._line_block_level != 1 or not is_first_class_child(node):
            self._doc_stack[-1].add_break()
        indent = ''.join('    ' for _ in range(self._line_block_level - 1))
        self._doc_stack[-1].add_text(indent)

    def depart_line(self, node):
        """Close the line's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_block_quote(self, node):
        """Indent the quoted content by one step."""
        self._append_bookmark_start(node.get('ids', []))
        self._ctx_stack[-1].indent += self._basic_indent

    def depart_block_quote(self, node):
        """Remove the quote's indent."""
        self._ctx_stack[-1].indent -= self._basic_indent
        self._append_bookmark_end(node.get('ids', []))

    def visit_attribution(self, node):
        """Start the attribution paragraph, opening with an em dash."""
        self._append_bookmark_start(node.get('ids', []))
        para = self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent)
        para.add_text(u'— ')
        self._doc_stack.append(para)

    def depart_attribution(self, node):
        """Finish the attribution paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_table(self, node):
        """Enter a table, rotating the page if it is a landscape one."""
        if self._is_landscape_table(node):
            self._set_page_oriented()
        self._append_bookmark_start(node.get('ids', []))

    def depart_table(self, node):
        """Leave the table and rotate the page back."""
        self._append_bookmark_end(node.get('ids', []))
        if self._is_landscape_table(node):
            self._clear_page_oriented()

    def visit_tgroup(self, node):
        """Push the table itself, sized and styled from the table node.

        An explicit width is fixed; without one the table takes the width
        available and lets Word fit the columns.
        """
        self._append_bookmark_start(node.get('ids', []))
        align = node.parent.get('align')
        classes = node.parent.get('classes')
        width = node.parent.get('width')
        if width is not None:
            table_width = convert_to_twip_size(
                width, self._ctx_stack[-1].paragraph_width)
            is_fixed_width = True
        else:
            table_width = self._ctx_stack[-1].paragraph_width
            is_fixed_width = False
        custom_style = self._get_custom_style(classes, 'table')
        self._append_table(
            custom_style if custom_style is not None else 'Table',
            table_width, [1.0], True, align, is_fixed_width=is_fixed_width,
            in_single_page=self._get_table_option(
                classes, 'in-single-page', False),
            row_splittable=self._get_table_option(
                classes, 'row-splittable', True),
            header_in_all_page=self._get_table_option(
                classes, 'header-in-all-page', False),
            rotation_header_height=DocxTranslator._get_rotation_header_height(
                classes),
            fit_content=('colwidths-auto' in classes),
            no_wrap=('nowrap' in classes))

    def depart_tgroup(self, node):
        """Append the finished table."""
        self._pop_and_append_table()
        self._append_bookmark_end(node.get('ids', []))

    def visit_colspec(self, node):
        """Record the column width, and count the column as a stub if it is one."""
        self._append_bookmark_start(node.get('ids', []))
        table = self._doc_stack[-1]
        table.add_colspec(node['colwidth'])
        if node.get('stub', 0) == 1:
            table.add_stub()

    def depart_colspec(self, node):
        """Close the colspec's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_thead(self, node):
        """Direct the following rows into the table header."""
        self._append_bookmark_start(node.get('ids', []))
        table = self._doc_stack[-1]
        table.start_head()

    def depart_thead(self, node):
        """Close the header's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_tbody(self, node):
        """Direct the following rows into the table body."""
        self._append_bookmark_start(node.get('ids', []))
        table = self._doc_stack[-1]
        table.start_body()

    def depart_tbody(self, node):
        """Close the body's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_row(self, node):
        """Start a new table row."""
        self._append_bookmark_start(node.get('ids', []))
        table = self._doc_stack[-1]
        table.add_row()

    def depart_row(self, node):
        """Close the row's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_entry(self, node):
        """Start a new cell, with any row and column spans it declares."""
        self._append_bookmark_start(node.get('ids', []))
        self._add_table_cell(node.get('morerows', 0), node.get('morecols', 0))

    def depart_entry(self, node):
        """Close the cell's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_figure(self, node):
        """Enter a figure, padding the context to its width and alignment.

        Word has no alignment for a block of content, so the figure is aligned
        by indenting it: the width it does not use becomes margin on one side or
        split between both. Without a width of its own the figure is as wide as
        the picture it holds, so that an aligned figure has room to move and a
        caption wraps under the picture instead of across the whole column.
        """
        if self._is_landscape_figure(node):
            self._set_page_oriented()
        self._append_bookmark_start(node.get('ids', []))
        paragraph_width = self._ctx_stack[-1].paragraph_width
        width = self._get_figure_width(node, paragraph_width)
        delta_width = paragraph_width - width
        align = node.get('align', 'left')
        if align == 'left':
            self._append_new_ctx(
                right_indent=self._ctx_stack[-1].right_indent + delta_width)
        elif align in ('center', 'default'):
            padding = delta_width // 2
            self._append_new_ctx(
                indent=self._ctx_stack[-1].indent + padding,
                right_indent=self._ctx_stack[-1].right_indent + padding)
        elif align == 'right':
            self._append_new_ctx(
                indent=self._ctx_stack[-1].indent + delta_width)
        else:
            self._logger.warning(
                'Unknown figure align: %s' % align, location=node)
            self._append_new_ctx(
                right_indent=self._ctx_stack[-1].right_indent + delta_width)

    def depart_figure(self, node):
        """Leave the figure and drop its padding."""
        self._ctx_stack.pop()
        self._append_bookmark_end(node.get('ids', []))
        if self._is_landscape_figure(node):
            self._clear_page_oriented()

    def visit_caption(self, node):
        """Start the caption paragraph, numbered if the document numbers figures."""
        self._append_bookmark_start(node.get('ids', []))
        if isinstance(node.parent, nodes.figure):
            style = 'Image Caption'
            figtype = 'figure'
            align = node.parent.get('align')
            keep_next = False
        else:
            style = 'Literal Caption'
            figtype = 'code-block'
            align = None
            keep_next = True
        self._doc_stack.append(self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent, style,
            align, keep_next=keep_next))
        caption_num = self._get_numfig(figtype, node.parent['ids'])
        if caption_num is not None:
            self._doc_stack[-1].add_text(caption_num)

    def depart_caption(self, node):
        """Finish the caption paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_legend(self, node):
        """Make Legend the default style for the legend's paragraphs."""
        self._append_bookmark_start(node.get('ids', []))
        self._append_default_paragraph_style('Legend')

    def depart_legend(self, node):
        """Restore the previous default paragraph style."""
        self._pop_default_paragraph_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_footnote(self, node):
        """Start collecting a footnote, opening it with its own mark."""
        self._relationship_stack.append('footnotes')
        para = self._make_paragraph(None, None, 'Footnote Text')
        para.add_footnote_ref(
            self._docx.get_style_id('Footnote Reference', 'character'))
        para.add_text(' ')
        self._doc_stack.append(FixedTopParagraphList(para))
        self._append_bookmark_start(node.get('ids', []))

    def depart_footnote(self, node):
        """Store the footnote under each of its ids, out of the document body."""
        self._append_bookmark_end(node.get('ids', []))
        footnote = self._doc_stack.pop()
        contents = [c.to_xml() for c in footnote]
        for node_id in node.get('ids'):
            self._docx.append_footnote(
                '%s#%s' % (self._docname_stack[-1], node_id), contents)
        self._relationship_stack.pop()

    def visit_citation(self, node):
        """Start collecting a citation, as a bibliography entry."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack.append(FixedTopParagraphList(self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
            style='Bibliography')))

    def depart_citation(self, node):
        """Append the citation."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_label(self, node):
        """Add the label of a citation, and skip it.

        A footnote's label is the mark added on visit, so it is dropped.
        """
        if isinstance(node.parent, nodes.footnote):
            raise nodes.SkipNode
        if isinstance(node.parent, nodes.citation):
            self._doc_stack[-1][0].add_text('[%s] ' % node.astext())
            raise nodes.SkipNode

    def depart_label(self, node):
        """Do nothing: the label was handled on visit."""
        pass

    def visit_rubric(self, node):
        """Start the rubric paragraph.

        The 'Footnotes' rubric Sphinx adds is dropped: Word puts footnotes at the
        foot of the page, so a heading for them has nothing under it.
        """
        if node.astext() in ('Footnotes', _('Footnotes')):
            raise nodes.SkipNode
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack.append(self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
            'Rubric Title Heading'))

    def depart_rubric(self, node):
        """Finish the rubric paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_bullet_list(self, node):
        """Enter a bullet list, indenting by the step its level calls for."""
        self._append_bookmark_start(node.get('ids', []))
        self._ctx_stack[-1].list_level += 1
        self._ctx_stack[-1].indent += self._get_additional_list_indent(
            self._ctx_stack[-1].list_level - 1)

    def depart_bullet_list(self, node):
        """Leave the bullet list and remove its indent."""
        self._ctx_stack[-1].indent -= self._get_additional_list_indent(
            self._ctx_stack[-1].list_level - 1)
        self._ctx_stack[-1].list_level -= 1
        self._append_bookmark_end(node.get('ids', []))

    def visit_enumerated_list(self, node):
        """Enter a numbered list, adding a numbering definition for its format."""
        self._append_bookmark_start(node.get('ids', []))
        self._ctx_stack[-1].indent += self._number_list_indent
        enumtype = node.get('enumtype', 'arabic')
        prefix = node.get('prefix', '')
        suffix = node.get('suffix', '')
        start = node.get('start', 1)
        self._list_id_stack.append(self._docx.add_numbering_style(
            start, '{}%1{}'.format(prefix, suffix), enumtype,
            self._number_list_indent))

    def depart_enumerated_list(self, node):
        """Leave the numbered list and remove its indent."""
        self._ctx_stack[-1].indent -= self._number_list_indent
        self._list_id_stack.pop()
        self._append_bookmark_end(node.get('ids', []))

    def visit_list_item(self, node):
        """Start a list item, whose first paragraph carries the bullet or number.

        Anything the item contains beyond that first paragraph follows it
        unnumbered, so a multi paragraph item gets one marker.
        """
        self._append_bookmark_start(node.get('ids', []))
        if isinstance(node.parent, nodes.enumerated_list):
            style = 'List Number'
            list_info = (self._list_id_stack[-1], 0)
        else:
            style = 'List Bullet'
            if self._bullet_list_id is not None:
                max_level = max(len(self._bullet_list_indents) - 1, 0)
                list_indent_level = min(
                    self._ctx_stack[-1].list_level - 1, max_level)
                list_info = (self._bullet_list_id, list_indent_level)
            else:
                list_info = None
        self._doc_stack.append(FixedTopParagraphList(
            self._make_paragraph(
                self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
                style, list_info=list_info)))

    def depart_list_item(self, node):
        """Append the list item."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_definition_list(self, node):
        """Bookmark the definition list; its items render themselves."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_definition_list(self, node):
        """Close the list's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_definition_list_item(self, node):
        """Start collecting one term and its definition."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack.append(DefinitionListItem())

    def depart_definition_list_item(self, node):
        """Append the definition list item."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_term(self, node):
        """Start the term paragraph, kept with the definition below it."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack.append(self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
            'Definition Term', keep_next=True))

    def depart_term(self, node):
        """Add the term to the item, which keeps it for any classifier."""
        term_paragraph = self._doc_stack.pop()
        self._doc_stack[-1].add_term(term_paragraph)
        self._append_bookmark_end(node.get('ids', []))

    def visit_classifier(self, node):
        """Continue the term paragraph, after a colon separator."""
        self._append_bookmark_start(node.get('ids', []))
        term_paragraph = self._doc_stack[-1].last_term
        self._doc_stack.append(term_paragraph)
        term_paragraph.add_text(' : ')

    def depart_classifier(self, node):
        """Stop adding to the term paragraph."""
        self._doc_stack.pop()
        self._append_bookmark_end(node.get('ids', []))

    def visit_definition(self, node):
        """Indent the definition and make Definition its default style."""
        self._append_bookmark_start(node.get('ids', []))
        self._ctx_stack[-1].indent += self._basic_indent
        self._append_default_paragraph_style('Definition')

    def depart_definition(self, node):
        """Restore the style and remove the definition's indent."""
        self._pop_default_paragraph_style()
        self._ctx_stack[-1].indent -= self._basic_indent
        self._append_bookmark_end(node.get('ids', []))

    def visit_field_list(self, node):
        """Push a two column table, field names in a stub column."""
        self._append_bookmark_start(node.get('ids', []))
        table_width = self._ctx_stack[-1].paragraph_width
        table = self._append_table(
            'Field List', table_width, [0.25, 0.75], True,
            is_fixed_width=False, fit_content=True)
        table.add_stub()

    def depart_field_list(self, node):
        """Append the field list table."""
        self._pop_and_append_table()
        self._append_bookmark_end(node.get('ids', []))

    def visit_field(self, node):
        """Start a row for one field."""
        self._append_bookmark_start(node.get('ids', []))
        table = self._doc_stack[-1]
        table.add_row()

    def depart_field(self, node):
        """Close the field's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_field_name(self, node):
        """Start the name cell of a field."""
        self._append_bookmark_start(node.get('ids', []))
        self._add_table_cell()
        self._doc_stack.append(self._make_paragraph())

    def depart_field_name(self, node):
        """Finish the name cell, ending it with a colon."""
        self._doc_stack[-1].add_text(':')
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_field_body(self, node):
        """Start the body cell of a field."""
        self._append_bookmark_start(node.get('ids', []))
        self._add_table_cell()

    def depart_field_body(self, node):
        """Close the body cell's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_option_list(self, node):
        """Push a single column table for the option list."""
        self._append_bookmark_start(node.get('ids', []))
        table_width = self._ctx_stack[-1].paragraph_width
        self._append_table(
            'Option List', table_width, [1.0], True, fit_content=False)

    def depart_option_list(self, node):
        """Append the option list table."""
        self._pop_and_append_table()
        self._append_bookmark_end(node.get('ids', []))

    def visit_option_list_item(self, node):
        """Bookmark the option list item."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_option_list_item(self, node):
        """Close the item's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_option_group(self, node):
        """Start a row holding the options, kept with their description."""
        self._append_bookmark_start(node.get('ids', []))
        table = self._doc_stack[-1]
        table.add_row()
        self._add_table_cell()
        self._doc_stack.append(self._make_paragraph(0, keep_next=True))

    def depart_option_group(self, node):
        """Finish the option group paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_option(self, node):
        """Separate this option from the previous one with a comma."""
        self._append_bookmark_start(node.get('ids', []))
        if not is_first_class_child(node):
            self._doc_stack[-1].add_text(', ')

    def depart_option(self, node):
        """Close the option's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_option_string(self, node):
        """Bookmark the option string; its text renders itself."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_option_string(self, node):
        """Close the option string's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_option_argument(self, node):
        """Add the delimiter before the argument and emphasise it."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack[-1].add_text(node.get('delimiter', ' '))
        self._push_style('Option Argument', 'Emphasis')

    def depart_option_argument(self, node):
        """Stop emphasising the argument."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_description(self, node):
        """Start a row for the option description, indented under the options."""
        self._append_bookmark_start(node.get('ids', []))
        table = self._doc_stack[-1]
        table.add_row()
        self._add_table_cell()
        self._ctx_stack[-1].indent += self._basic_indent

    def depart_description(self, node):
        """Remove the description's indent."""
        self._ctx_stack[-1].indent -= self._basic_indent
        self._append_bookmark_end(node.get('ids', []))

    def visit_attention(self, node):
        """Start an attention admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_attention(self, node):
        """Finish the attention admonition."""
        self.depart_admonition_node(node)

    def visit_caution(self, node):
        """Start a caution admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_caution(self, node):
        """Finish the caution admonition."""
        self.depart_admonition_node(node)

    def visit_danger(self, node):
        """Start a danger admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_danger(self, node):
        """Finish the danger admonition."""
        self.depart_admonition_node(node)

    def visit_error(self, node):
        """Start an error admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_error(self, node):
        """Finish the error admonition."""
        self.depart_admonition_node(node)

    def visit_hint(self, node):
        """Start a hint admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_hint(self, node):
        """Finish the hint admonition."""
        self.depart_admonition_node(node)

    def visit_important(self, node):
        """Start an important admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_important(self, node):
        """Finish the important admonition."""
        self.depart_admonition_node(node)

    def visit_note(self, node):
        """Start a note admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_note(self, node):
        """Finish the note admonition."""
        self.depart_admonition_node(node)

    def visit_tip(self, node):
        """Start a tip admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_tip(self, node):
        """Finish the tip admonition."""
        self.depart_admonition_node(node)

    def visit_warning(self, node):
        """Start a warning admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_warning(self, node):
        """Finish the warning admonition."""
        self.depart_admonition_node(node)

    def visit_admonition(self, node):
        """Start a generic admonition, whose title is its first child."""
        self.visit_admonition_node(node)

    def depart_admonition(self, node):
        #self.depart_admonition_node(node, 'Admonition')
        """Finish the generic admonition."""
        self.depart_admonition_node(node)

    def visit_substitution_definition(self, node): # pylint: disable=no-self-use
        """Skip the definition: the substitution is already resolved in the tree."""
        raise nodes.SkipNode # TODO

    def visit_comment(self, node): # pylint: disable=no-self-use
        """Skip the comment, which is not part of the output."""
        raise nodes.SkipNode # TODO

    def visit_pending(self, node): # pylint: disable=no-self-use
        """Skip the pending node."""
        raise nodes.SkipNode # TODO

    def visit_system_message(self, node): # pylint: disable=no-self-use
        """Skip the message; it is reported through the log instead."""
        raise nodes.SkipNode # TODO


    def visit_emphasis(self, node):
        """Emphasise the following text."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Emphasis')

    def depart_emphasis(self, node):
        """Stop emphasising."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_strong(self, node):
        """Embolden the following text."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Strong')

    def depart_strong(self, node):
        """Stop emboldening."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_literal(self, node):
        """Set the following text in the literal style."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Literal')

    def depart_literal(self, node):
        """Leave the literal style."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_math(self, node):
        """Add an inline equation, warning and dropping it if it will not convert."""
        self._append_bookmark_start(node.get('ids', []))
        latex = node.get('latex', node.astext())
        try:
            self._doc_stack[-1].add_math(latex)
        except Exception as e: # pylint: disable=broad-except
            self._logger.warning(
                'Failed to convert math %s: %s', latex, e, location=node)
        self._append_bookmark_end(node.get('ids', []))
        raise nodes.SkipNode

    def visit_reference(self, node):
        """Start a hyperlink, making a paragraph for it if there is none.

        A standalone reference, an image link in a figure say, has no paragraph
        of its own; the None below it marks one as owed back on depart.
        """
        self._append_bookmark_start(node.get('ids', []))
        if not isinstance(self._doc_stack[-1], Paragraph):
            self._doc_stack.append(None) # Marker for depart_reference to pop
            # The reference stands on its own, so it needs a paragraph.
            # depart_reference finds the None and knows to pop that paragraph.
            if is_image_link(node):
                # An image with a :target: hands its block over to this
                # reference, so the paragraph the image would have made in
                # visit_image_node is made here, with the same properties.
                style, align, keep_next = image_block_properties(node)
            else:
                # Get align because parent may be a figure element
                style, align, keep_next = None, block_align(node.parent), False
            self._doc_stack.append(self._make_paragraph(
                self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
                style=style, align=align, keep_next=keep_next))
        self._doc_stack[-1].begin_hyperlink(
            self._docx.get_style_id('Hyperlink', 'character'))
        secnumber = node.get('secnumber')
        if secnumber:
            # The number belongs to the link, as it does in HTML, so that it
            # reads and highlights as one with the title that follows it.
            self._doc_stack[-1].add_text(format_secnumber(secnumber))

    def depart_reference(self, node):
        """Finish the hyperlink, targeting a bookmark or an external URL.

        An internal reference resolves to the bookmark of its target document,
        an external one to a relationship in the current part.
        """
        refuri = node.get('refuri', None)
        if refuri:
            if node.get('internal', False):
                rid = None
                anchor = self._get_bookmark_name(refuri)
            else:
                rid = self._docx.add_hyperlink_relationship(
                    refuri, self._relationship_stack[-1])
                anchor = None
        else:
            rid = None
            anchor = make_bookmark_name(
                self._docname_stack[-1], node.get('refid'))
        self._doc_stack[-1].end_hyperlink(rid, anchor, node.get('reftitle'))
        if self._doc_stack[-2] is None:
            del self._doc_stack[-2]
            self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_footnote_reference(self, node):
        """Add a reference to the footnote the node points at."""
        self._append_bookmark_start(node.get('ids', []))
        refid = node.get('refid', None)
        if refid is not None:
            fid = self._docx.get_footnote_id(
                '%s#%s' % (self._docname_stack[-1], refid))
            self._doc_stack[-1].add_footnote_reference(
                fid,
                self._docx.get_style_id('Footnote Reference', 'character'))
        self._append_bookmark_end(node.get('ids', []))
        raise nodes.SkipNode

    def visit_citation_reference(self, node):
        """Bookmark the citation reference; its text renders itself."""
        self._append_bookmark_start(node.get('ids', []))
        # TODO

    def depart_citation_reference(self, node):
        """Close the reference's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_substitution_reference(self, node):
        """Bookmark the substitution reference; its text renders itself."""
        self._append_bookmark_start(node.get('ids', []))
        # TODO

    def depart_substitution_reference(self, node):
        """Close the reference's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_title_reference(self, node):
        """Set the following text in the title reference style."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Title Reference')

    def depart_title_reference(self, node):
        """Leave the title reference style."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_abbreviation(self, node):
        """Set the following text in the abbreviation style."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Abbreviation') # TODO

    def depart_abbreviation(self, node):
        """Leave the style, adding the explanation in brackets if there is one."""
        self._doc_stack[-1].pop_style()
        explanation = node.get('explanation')
        if explanation:
            self._doc_stack[-1].add_text(' (%s)' % explanation)
        self._append_bookmark_end(node.get('ids', []))

    def visit_acronym(self, node):
        """Bookmark the acronym; its text renders itself."""
        self._append_bookmark_start(node.get('ids', []))
        # TODO

    def depart_acronym(self, node):
        """Close the acronym's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_subscript(self, node):
        """Set the following text as a subscript."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Subscript')

    def depart_subscript(self, node):
        """Leave the subscript style."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_superscript(self, node):
        """Set the following text as a superscript."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Superscript')

    def depart_superscript(self, node):
        """Leave the superscript style."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_inline(self, node):
        """Push the character styles the node's classes map to."""
        self._append_bookmark_start(node.get('ids', []))
        classes = node.get('classes')
        if 'versionmodified' in classes:
            self._push_style('Versionmodified', 'Emphasis')
        else:
            for style_name in self._get_custom_charcter_styles(classes):
                self._push_style(style_name)

    def depart_inline(self, node):
        """Pop the styles pushed for the node's classes."""
        self._append_bookmark_end(node.get('ids', []))
        classes = node.get('classes')
        if 'versionmodified' in classes:
            self._doc_stack[-1].pop_style()
        else:
            for _style_name in self._get_custom_charcter_styles(classes):
                self._doc_stack[-1].pop_style()

    def visit_problematic(self, node):
        """Set the following text in the problematic style."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Problematic')

    def depart_problematic(self, node):
        """Leave the problematic style."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_generated(self, node):
        """Bookmark the generated text, which renders itself."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_generated(self, node):
        """Close the node's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_target(self, node):
        """Bookmark the target, which is what a reference to it points at."""
        self._append_bookmark_start(node.get('ids', []))
        # TODO

    def depart_target(self, node):
        """Close the target's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_image(self, node):
        """Insert an image, found by its uri relative to the source."""
        self.visit_image_node(
            node, node.get('alt', node['uri']),
            DocxTranslator._get_image_filepath)

    def visit_raw(self, node):
        """Insert raw docx markup, at the top level of the document only.

        Anything not in the docx format is skipped, and invalid markup is
        warned about rather than corrupting the file.
        """
        if node.get('format', None) != 'docx':
            raise nodes.SkipNode
        if not isinstance(self._doc_stack[-1], Document):
            # TODO: nested raw markup
            self._logger.warning(
                'Not support nested raw markup', location=node)
            raise nodes.SkipNode
        try:
            elems = docx.fromstring(node.rawsource)
        except Exception:
            self._logger.warning('Invalid raw markup', location=node)
            raise nodes.SkipNode
        for elem in elems:
            self._doc_stack[-1].append(Raw(elem))
        raise nodes.SkipNode

    def visit_toctree(self, node):
        """Insert a table of contents field, with the outlines it resolves to.

        Word builds the field itself on update; the outlines are written out as
        well so the contents read correctly before that happens.
        """
        if node.get('hidden', False):
            return
        caption = node.get('caption')
        maxdepth = node.get('maxdepth', -1)
        maxlevel = self._section_level + maxdepth if maxdepth > 0 else None
        refid = node.get('docx_expanded_toctree_refid')
        if refid is None:
            self._logger.warning(
                'No docx_expanded_toctree_refid', location=node)
            return
        bookmark = make_bookmark_name(self._docname_stack[-1], refid)
        config = self._builder.config
        if (self._section_level <= config.docx_pagebreak_before_table_of_contents
                and isinstance(self._doc_stack[-1], Document)):
            self._doc_stack[-1].add_pagebreak()
        self._doc_stack[-1].append(TOC(
            caption, self._docx.get_style_id('TOC Heading', 'paragraph'),
            maxlevel, bookmark, self._ctx_stack[-1].paragraph_width,
            self._collect_outlines(node, maxdepth)))
        if (self._section_level <= config.docx_pagebreak_after_table_of_contents
                and isinstance(self._doc_stack[-1], Document)):
            self._doc_stack[-1].add_pagebreak()

    def depart_toctree(self, node):
        """Do nothing: the contents were inserted on visit."""
        pass

    def visit_compact_paragraph(self, node):
        """Bookmark the paragraph; its children render themselves."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_compact_paragraph(self, node):
        """Close the paragraph's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_literal_emphasis(self, node):
        """Set the following text in the literal and emphasis styles."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Literal')
        self._push_style('Emphasis')

    def depart_literal_emphasis(self, node):
        """Leave both styles."""
        self._doc_stack[-1].pop_style()
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_literal_strong(self, node):
        """Set the following text in the literal and strong styles."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Literal')
        self._push_style('Strong')

    def depart_literal_strong(self, node):
        """Leave both styles."""
        self._doc_stack[-1].pop_style()
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_highlightlang(self, node):
        """Set the language and line number threshold for the code blocks below."""
        self._language = node.get('lang', 'guess')
        self._linenothreshold = node.get(
            'linenothreshold', self._linenothreshold)
        raise nodes.SkipNode

    def visit_glossary(self, node):
        """Bookmark the glossary; its definition list renders itself."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_glossary(self, node):
        """Close the glossary's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc(self, node):
        """Push a table for a description, signature in the header row."""
        self._append_bookmark_start(node.get('ids', []))
        table_width = self._ctx_stack[-1].paragraph_width
        style_name = '%s Descriptions' % node.get('desctype', '').capitalize()
        self._docx.create_style(
            'table', style_name, 'Admonition Descriptions', True)
        table = self._append_table(
            style_name, table_width, [1.0], True, fit_content=False)
        table.start_head()
        table.add_row()
        self._add_table_cell()

    def depart_desc(self, node):
        """Append the description table."""
        self._pop_and_append_table()
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_signature(self, node):
        """Start the signature paragraph."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack.append(self._make_paragraph())

    def depart_desc_signature(self, node):
        """Finish the signature paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_signature_line(self, node):
        """Break to a new line for each line after the first."""
        self._append_bookmark_start(node.get('ids', []))
        if not is_first_class_child(node):
            self._doc_stack[-1].add_break()

    def depart_desc_signature_line(self, node):
        """Close the line's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_name(self, node):
        """Set the object name in the description name style."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Desc Name', 'Strong')

    def depart_desc_name(self, node):
        """Leave the description name style."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_addname(self, node):
        """Set the module or class prefix in the description name style."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Desc Name', 'Strong')

    def depart_desc_addname(self, node):
        """Leave the description name style."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_type(self, node):
        """Bookmark the type; its text renders itself."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_desc_type(self, node):
        """Close the type's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_returns(self, node):
        """Add the arrow before the return annotation."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack[-1].add_text(u' → ')

    def depart_desc_returns(self, node):
        """Close the node's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_parameterlist(self, node):
        """Open the parameter list bracket."""
        self._doc_stack[-1].add_text('(')
        self._append_bookmark_start(node.get('ids', []))

    def depart_desc_parameterlist(self, node):
        """Close the parameter list bracket."""
        self._doc_stack[-1].add_text(')')
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_type_parameter_list(self, node):
        """Open the type parameter list bracket."""
        self._doc_stack[-1].add_text('[')
        self._append_bookmark_start(node.get('ids', []))

    def depart_desc_type_parameter_list(self, node):
        """Close the type parameter list bracket."""
        self._doc_stack[-1].add_text(']')
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_parameter(self, node):
        """Separate this parameter from the previous one and emphasise it."""
        self._append_bookmark_start(node.get('ids', []))
        parent = node.parent
        if parent.children[0] is not node:
            self._doc_stack[-1].add_text(parent.child_text_separator)
        if not node.get('noemph', False):
            self._push_style('Emphasis')

    def depart_desc_parameter(self, node):
        """Stop emphasising the parameter."""
        if not node.get('noemph', False):
            self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_type_parameter(self, node):
        """Separate this type parameter from the previous one and emphasise it."""
        self._append_bookmark_start(node.get('ids', []))
        parent = node.parent
        if parent.children[0] is not node:
            self._doc_stack[-1].add_text(parent.child_text_separator)
        if not node.get('noemph', False):
            self._push_style('Emphasis')

    def depart_desc_type_parameter(self, node):
        """Stop emphasising the type parameter."""
        if not node.get('noemph', False):
            self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_optional(self, node):
        """Open the bracket around the optional parameters."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack[-1].add_text('[')
        parent = node.parent
        if parent.children[0] is not node:
            self._doc_stack[-1].add_text(parent.child_text_separator)

    def depart_desc_optional(self, node):
        """Close the bracket around the optional parameters."""
        self._doc_stack[-1].add_text(']')
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_annotation(self, node):
        """Set the annotation in the description annotation style."""
        self._append_bookmark_start(node.get('ids', []))
        self._push_style('Desc Annotation', 'Emphasis')

    def depart_desc_annotation(self, node):
        """Leave the description annotation style."""
        self._doc_stack[-1].pop_style()
        self._append_bookmark_end(node.get('ids', []))

    def visit_desc_content(self, node):
        """Start the body row of the description, unless it has no content."""
        self._append_bookmark_start(node.get('ids', []))
        if len(node) == 0: # pylint: disable=len-as-condition
            return
        table = self._doc_stack[-1]
        table.start_body()
        table.add_row()
        self._add_table_cell()

    def depart_desc_content(self, node):
        """Close the content's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_productionlist(self, node):
        """Push a two column table, token names in a stub column."""
        self._append_bookmark_start(node.get('ids', []))
        table_width = self._ctx_stack[-1].paragraph_width
        tbl = self._append_table(
            'Production List', table_width, [0.25, 0.75], True,
            fit_content=True)
        tbl.add_stub() # keep the token name column from wrapping
        tbl.start_body()

    def depart_productionlist(self, node):
        """Append the production list table."""
        self._pop_and_append_table()
        self._append_bookmark_end(node.get('ids', []))

    @staticmethod
    def _split_production(node):
        """Split a production node into (name, separator, definition).

        Sphinx builds each production as an optional literal_strong holding the
        token name, a Text separator padding ' ::= ' so that every rule of the
        block lines up in a fixed-width font, the definition, and a trailing
        newline. Here the table does the aligning, so the padding and the
        newline are dropped and only the bare separator is kept. Continuation
        lines have an empty tokenname, no literal_strong and an all-space
        separator; they become a row with an empty name cell.
        """
        children = list(node.children)
        if (children and isinstance(children[-1], nodes.Text)
                and not children[-1].astext().strip()):
            children.pop()
        name = []
        if children and isinstance(children[0], addnodes.literal_strong):
            name.append(children.pop(0))
        separator = ''
        if children and isinstance(children[0], nodes.Text):
            text = children[0].astext().strip()
            if text in ('', '::='):
                separator = text
                children.pop(0)
        return name, separator, children

    def visit_production(self, node):
        """Lay one grammar rule out as a table row: name, then definition.

        The children are dispatched by hand because they go into two different
        cells, so the node is skipped afterwards.
        """
        name, separator, definition = DocxTranslator._split_production(node)
        self._doc_stack[-1].add_row()

        # The bookmarks have to be opened after the first cell exists and
        # closed before the last one is popped: until then the table's current
        # cell is still the previous row's.
        self._add_table_cell()
        self._doc_stack.append(self._make_paragraph())
        self._append_bookmark_start(node.get('ids', []))
        for child in name:
            child.walkabout(self)
        self._pop_and_append()

        self._add_table_cell()
        self._doc_stack.append(self._make_paragraph(preserve_space=True))
        self._push_style('Literal')
        if separator:
            self._doc_stack[-1].add_text(separator + ' ')
        for child in definition:
            child.walkabout(self)
        self._append_bookmark_end(node.get('ids', []))
        self._doc_stack[-1].pop_style()
        self._pop_and_append()

        # The children were dispatched by hand above, into two different cells.
        raise nodes.SkipNode

    def visit_seealso(self, node):
        """Start a see also admonition, titled from its tagname."""
        self.visit_admonition_node(node, add_title=True)

    def depart_seealso(self, node):
        """Finish the see also admonition."""
        self.depart_admonition_node(node)

    def visit_tabular_col_spec(self, _node): # pylint: disable=no-self-use
        """Skip the column spec: it is a LaTeX directive with no docx meaning."""
        raise nodes.SkipNode # TODO

    def visit_acks(self, node):
        # Sphinx guarantees a single bullet_list child, which renders itself;
        # the HTML writer passes acks through the same way. LaTeX instead
        # flattens the names into one comma-separated sentence, but a format
        # with real lists has no reason to throw the list away.
        """Bookmark the acknowledgements; the list inside renders itself."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_acks(self, node):
        """Close the node's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_centered(self, node):
        """Start a centred paragraph."""
        self._append_bookmark_start(node.get('ids', []))
        self._doc_stack.append(self._make_paragraph(
            self._ctx_stack[-1].indent, self._ctx_stack[-1].right_indent,
            align='center'))

    def depart_centered(self, node):
        """Finish the centred paragraph."""
        self._pop_and_append()
        self._append_bookmark_end(node.get('ids', []))

    def visit_hlist(self, node):
        """Push a table of equal columns, one per column of the list."""
        self._append_bookmark_start(node.get('ids', []))
        table_width = self._ctx_stack[-1].paragraph_width
        numcols = len(node)
        colsize_list = [1.0 / numcols for _ in range(numcols)]
        tbl = self._append_table(
            'Horizontal List',
            table_width, colsize_list, True, fit_content=False)
        tbl.add_row()

    def depart_hlist(self, node):
        """Append the horizontal list table."""
        self._pop_and_append_table()
        self._append_bookmark_end(node.get('ids', []))

    def visit_hlistcol(self, node):
        """Start the cell for one column of the list."""
        self._append_bookmark_start(node.get('ids', []))
        self._add_table_cell()

    def depart_hlistcol(self, node):
        """Close the column's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_versionmodified(self, node):
        """Start a versionmodified admonition, whose text is its own title."""
        self.visit_admonition_node(node)

    def depart_versionmodified(self, node):
        """Finish it, in a style named after the kind of change."""
        style_name = 'Admonition ' + node.get('type').capitalize()
        self._docx.create_style(
            'table', style_name, 'Admonition Versionmodified', True)
        self.depart_admonition_node(
            node, style=style_name, align=None, margin=0)

    def visit_index(self, node):
        """Bookmark the index entry; the index itself is not built."""
        self._append_bookmark_start(node.get('ids', []))
        # TODO

    def depart_index(self, node):
        """Close the entry's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_pending_xref(self, node):
        """Bookmark the cross reference; its text renders itself."""
        self._append_bookmark_start(node.get('ids', []))
        # TODO

    def depart_pending_xref(self, node):
        """Close the reference's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_download_reference(self, node):
        """Bookmark the download reference; the file itself is not embedded."""
        self._append_bookmark_start(node.get('ids', []))
        # TODO

    def depart_download_reference(self, node):
        """Close the reference's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_number_reference(self, node):
        """Start the numbered reference, as an ordinary hyperlink."""
        self.visit_reference(node)

    def depart_number_reference(self, node):
        """Finish the numbered reference."""
        self.depart_reference(node)

    def visit_meta(self, _node): # pylint: disable=no-self-use
        """Skip the metadata, which belongs to HTML."""
        raise nodes.SkipNode

    def visit_graphviz(self, node):
        """Render the graph to PNG and insert it, falling back to the dot source."""
        def get_filepath(self, node):
            """Render the dot code to a PNG and return its path."""
            _fname, filepath = graphviz.render_dot(
                self, node['code'], node['options'], 'png')
            if filepath is None:
                raise RuntimeError('Failed to generate a graphviz image')
            return filepath
        self.visit_image_node(
            node, node.get('alt', (node['code'], 'dot')), get_filepath)

    def visit_inheritance_diagram(self, node):
        # inheritance_diagram derives from the graphviz node, but carries an
        # InheritanceGraph instead of ready-made dot code, so generate the code
        # first and then reuse the graphviz path. visit_image_node raises
        # SkipNode, which also discards the pending_xref children the extension
        # attaches for the HTML image map.
        """Generate the diagram's dot code, render it, and insert the image."""
        def get_filepath(self, node):
            """Generate the diagram's dot code, render it to a PNG, return its path."""
            code = node['graph'].generate_dot(
                'inheritance%s' % get_graph_hash(node), env=self._builder.env)
            _fname, filepath = graphviz.render_dot(
                self, code, {}, 'png', 'inheritance')
            if filepath is None:
                raise RuntimeError('Failed to generate an inheritance diagram')
            return filepath
        self.visit_image_node(
            node, 'Inheritance diagram of ' + node['content'], get_filepath)

    def visit_autosummary_table(self, node):
        """Bookmark the summary table; the table inside renders itself."""
        self._append_bookmark_start(node.get('ids', []))

    def depart_autosummary_table(self, node):
        """Close the table's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_autosummary_toc(self, node):
        # The toctree generated by autosummary is only a list of the stub
        # pages; HTML and LaTeX both suppress it. Descend so that the stub
        # pages themselves enter the document, but do not emit a table of
        # contents for them.
        """Hide the stub page toctree, but still descend into the stub pages."""
        for child in node.children:
            if isinstance(child, addnodes.toctree):
                child['hidden'] = True
        self._append_bookmark_start(node.get('ids', []))

    def depart_autosummary_toc(self, node):
        """Close the node's bookmarks."""
        self._append_bookmark_end(node.get('ids', []))

    def visit_refcount(self, _node): # pylint: disable=no-self-use
        """Skip the reference count annotation."""
        raise nodes.SkipNode # TODO

    def depart_refcount(self, node):
        """Do nothing: the node was skipped on visit."""
        pass

    if is_sphinx_version_lower_than((1, 8, 0)):
        def visit_displaymath(self, node):
            """Insert the equations of a pre-1.8 displaymath node."""
            self.visit_math_block_node(node, node.get('latex'))

    def visit_todo_node(self, node):
        """Start a todo admonition, whose title is its first child."""
        self.visit_admonition_node(node)

    def depart_todo_node(self, node):
        """Finish the todo admonition."""
        self.depart_admonition_node(node)

    def unknown_visit(self, node):
        """Warn about a node with no visit method and skip it."""
        self._logger.warning(
            'Ignore unknown node ' + node.tagname, location=node)
        raise nodes.SkipNode

    def _get_bookmark_name(self, refuri):
        # For such case that the target is in a different directory
        """Return the bookmark a reference URI points at.

        The URI is relative to the referring document, and may name a document,
        a document and an anchor, or an anchor in this one.
        """
        refuri = posixpath.normpath(
            posixpath.join(posixpath.dirname(self._docname_stack[-1]), refuri))
        if refuri in self._builder.env.all_docs:
            return make_bookmark_name(refuri, '')
        hashindex = refuri.rfind('#') # Use rfind because docname includes #.
        if hashindex != -1 and refuri[:hashindex] in self._builder.env.all_docs:
            return make_bookmark_name(refuri[:hashindex], refuri[hashindex+1:])
        if hashindex == 0:
            return make_bookmark_name(self._docname_stack[-1], refuri[1:])
        self._logger.warning('Missing refuri :' + refuri)
        return ''

    def _get_additional_list_indent(self, list_level):
        """Return how much further to indent a bullet list at this level.

        The step comes from the List Bullet numbering definition, so lists line
        up with their bullets; past the levels it defines, the basic indent is
        used.
        """
        if list_level >= len(self._bullet_list_indents):
            return self._basic_indent
        if list_level == 0:
            parent_indent = 0
        else:
            parent_indent = self._bullet_list_indents[list_level - 1]
        return self._bullet_list_indents[list_level] - parent_indent

    def _get_image_scaled_size(self, node, filename, quiet=False):
        """Return the size to draw an image at, in centimetres.

        A missing dimension is taken from the file, keeping the aspect ratio,
        then the result is scaled down to fit both the paragraph width and the
        page height. A caller measuring ahead of the image itself passes
        ``quiet`` so a bad length is reported once, where it is drawn.
        """
        paragraph_width = self._ctx_stack[-1].paragraph_width
        width = self._get_cm_size(node, 'width', paragraph_width, quiet)
        height = self._get_cm_size(node, 'height', quiet=quiet)

        if width is None and height is None:
            width, height = get_image_size(filename)
        elif width is None:
            img_width, img_height = get_image_size(filename)
            width = img_width * height / img_height
        elif height is None:
            img_width, img_height = get_image_size(filename)
            height = img_height * width / img_width

        scale = node.get('scale')
        if scale is not None:
            scale = float(scale) / 100
            width *= scale
            height *= scale

        width, height = adjust_size(
            convert_to_cm_size(paragraph_width), width, height)
        # 600 is margin for caption
        max_height = self._doc_stack[0].get_current_page_height() - 600
        height, width = adjust_size(
            convert_to_cm_size(max_height), height, width)

        return width, height

    def _get_image_filepath(self, node):
        """Return the path of an image file, trying the output directories too.

        Extensions write generated images to the image directory or the output
        directory instead of next to the source, so both are tried.
        """
        uri = node['uri']
        if uri.find('://') != -1:
            raise RuntimeError('Not support remote image files yet')
        filepath = os.path.join(self._builder.srcdir, uri)
        if not os.path.exists(filepath):
            # Some extensions output images in imagedir
            filepath = os.path.join(
                self._builder.outdir, self._builder.imagedir, uri)
        if not os.path.exists(filepath):
            # Some extensions output images in outdir
            filepath = os.path.join(self._builder.outdir, uri)
        return filepath

    def _get_figure_width(self, node, paragraph_width):
        """Return the width of a figure, in twips.

        ``:figwidth:`` decides it when the figure has one. Otherwise the figure
        is as wide as the picture it holds, and falls back to the whole column
        when that picture cannot be measured here -- a diagram an extension
        renders later, a missing file, or no image at all.
        """
        width = self._get_cm_size(node, 'width', paragraph_width)
        if width is None:
            image = figure_image(node)
            if image is not None:
                width = self._get_image_drawn_width(image)
        if width is None:
            return paragraph_width
        return convert_cm_to_twip(width)

    def _get_image_drawn_width(self, node):
        """Return the width an image is drawn at, in cm, or None if unknown.

        A figure needs the size before the image itself is visited. Anything
        that goes wrong here goes wrong again in visit_image_node, which reports
        it, so this one stays quiet and leaves the width undecided.
        """
        try:
            filepath = self._get_image_filepath(node)
            if filepath is None or not os.path.exists(filepath):
                return None
            return self._get_image_scaled_size(node, filepath, quiet=True)[0]
        except Exception: # pylint: disable=broad-except
            return None

    def _get_cm_size(self, node, attr, max_width=0, quiet=False):
        """Return a length attribute of the node in cm, warning if it will not parse."""
        try:
            return convert_to_cm_size(
                convert_to_twip_size(node.get(attr), max_width))
        except (RuntimeError, ValueError, OverflowError) as e:
            if not quiet:
                self._logger.warning(e, location=node)
            return None

    def _collect_outlines(self, node, maxdepth):
        """Return the (text, style, bookmark) entries for a table of contents.

        The toctree is resolved as the HTML builder would, and each entry is
        mapped to the paragraph style of its depth.
        """
        toctree = TocTree(self._builder.env).resolve(
            self._docname_stack[-1], self._builder, node,
            maxdepth=maxdepth, includehidden=True)
        if toctree is None:
            return []
        outlines = []
        for outline in findall(
                toctree, addnodes.compact_paragraph, include_self=False):
            classes = outline.get('classes')
            level_class = next(c for c in classes if c.startswith('toctree-l'))
            ref = outline[0]
            secnum = ref.get('secnumber')
            if secnum is not None:
                text = format_secnumber(secnum) + ref.astext()
            else:
                text = ref.astext()
            outlines.append((
                text,
                self._docx.get_style_id(
                    level_class.replace('toctree-l', 'toc '), 'paragraph'),
                self._get_bookmark_name(ref.get('refuri'))))
        return outlines

    def _create_docxbuilder_styles(self):
        """Create the styles the writer needs, based on those in the style file.

        A style already in the style file is left alone, so a user file can
        override any of them.
        """
        self._docx.create_empty_paragraph_style('Transition', 100, True, False)
        self._docx.create_empty_paragraph_style(
            DocxTranslator.TABLE_BOTTOM_MARGIN_STYLE_NAME, 0, False, True)

        default_paragraph, _, default_table = self._docx.get_default_style_names()
        paragraph_styles = [
            ('Body Text', default_paragraph, False, False),
            ('Footnote Text', default_paragraph, False, False),
            ('Bibliography', default_paragraph, False, False),
            ('Definition Term', default_paragraph, True, False),
            ('Definition', default_paragraph, True, False),
            ('Literal Block', default_paragraph, True, False),
            ('Math Block', default_paragraph, True, False),
            ('Image', default_paragraph, True, False),
            ('Figure', default_paragraph, True, False),
            ('Legend', default_paragraph, True, False),
            ('Caption', default_paragraph, False, True),
            ('Table Caption', 'Caption', True, False),
            ('Image Caption', 'Caption', True, False),
            ('Literal Caption', 'Caption', True, False),
            ('Heading', default_paragraph, True, True),
            ('Title Heading', 'Heading', True, True),
            ('TOC Heading', 'Title Heading', False, False),
            ('Rubric Title Heading', 'Title Heading', True, False),
            ('Subtitle Heading', 'Heading', True, True),
        ]
        for new_style, based_style, is_custom, is_hidden in paragraph_styles:
            self._docx.create_style(
                'paragraph', new_style, based_style, is_custom, is_hidden)

        self._docx.create_list_style(
            'List Bullet', 'bullet', '\uf0b7', 'Symbol', self._basic_indent)
        self._docx.create_list_style(
            'List Number', 'arabic', '%1.', None, self._basic_indent)

        table_styles = [
            ('List Table', default_table, False, True),
            ('Table', default_table, False, False),
            ('Based Admonition', default_table, False, True),
            ('Field List', 'List Table', False, False),
            ('Option List', 'List Table', False, False),
            ('Horizontal List', 'List Table', False, False),
            ('Production List', 'List Table', False, False),
            ('Admonition', 'Based Admonition', False, False),
            ('Admonition Descriptions', 'Based Admonition', False, True),
            ('Admonition Versionmodified', 'Based Admonition', True, True),
        ]
        for new_style, based_style, is_custom, is_hidden in table_styles:
            self._docx.create_style(
                'table', new_style, based_style, is_custom, is_hidden)
