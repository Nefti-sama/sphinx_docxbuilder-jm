import colorsys
from xml.sax import saxutils
from pygments.formatter import Formatter
from sphinx.highlighting import PygmentsBridge

# Word validates w:highlight against the fixed ST_HighlightColor name list,
# so this map is the palette every requested color is snapped to, not a place
# to add arbitrary colors. Achromatic names are left out, being unsuitable for
# highlight.
HIGHLIGHT_COLOR_MAP = {
    # 'black': [0x00, 0x00, 0x00],
    'blue': [0x00, 0x00, 0xFF],
    'cyan': [0x00, 0xFF, 0xFF],
    'darkBlue': [0x00, 0x00, 0x8B],
    'darkCyan': [0x00, 0x8B, 0x8B],
    # 'darkGray': [0xA9, 0xA9, 0xA9],
    'darkGreen': [0x00, 0x64, 0x00],
    'darkMagenta': [0x80, 0x00, 0x80],
    'darkRed': [0x8B, 0x00, 0x00],
    'darkYellow': [0x80, 0x80, 0x00],
    'green': [0x00, 0xFF, 0x00],
    # 'lightGray': [0xD3, 0xD3, 0xD3],
    'magenta': [0xFF, 0x00, 0xFF],
    'red': [0xFF, 0x00, 0x00],
    # 'white': [0xFF, 0xFF, 0xFF],
    'yellow': [0xFF, 0xFF, 0x00],
}

# Common CSS color names accepted as a highlight color. Word has no highlight
# value of its own for them, so each is snapped to the nearest name in
# HIGHLIGHT_COLOR_MAP: 'lightgreen' highlights green, 'orange' yellow.
COMMON_COLOR_MAP = {
    'aqua': [0x00, 0xFF, 0xFF],
    'chartreuse': [0x7F, 0xFF, 0x00],
    'crimson': [0xDC, 0x14, 0x3C],
    'fuchsia': [0xFF, 0x00, 0xFF],
    'gold': [0xFF, 0xD7, 0x00],
    'indigo': [0x4B, 0x00, 0x82],
    'lightblue': [0xAD, 0xD8, 0xE6],
    'lightgreen': [0x90, 0xEE, 0x90],
    'lightpink': [0xFF, 0xB6, 0xC1],
    'lightyellow': [0xFF, 0xFF, 0xE0],
    'lime': [0x00, 0xFF, 0x00],
    'maroon': [0x80, 0x00, 0x00],
    'navy': [0x00, 0x00, 0x80],
    'olive': [0x80, 0x80, 0x00],
    'orange': [0xFF, 0xA5, 0x00],
    'orchid': [0xDA, 0x70, 0xD6],
    'pink': [0xFF, 0xC0, 0xCB],
    'purple': [0x80, 0x00, 0x80],
    'salmon': [0xFA, 0x80, 0x72],
    'skyblue': [0x87, 0xCE, 0xEB],
    'teal': [0x00, 0x80, 0x80],
    'turquoise': [0x40, 0xE0, 0xD0],
    'violet': [0xEE, 0x82, 0xEE],
}

#: Highlight used when a color is neither a known name nor a hex string.
DEFAULT_HIGHLIGHT_COLOR = 'yellow'

def parse_color(color):
    """Return the RGB triple of a color, or None if it is not understood.

    Accepts a name from :data:`COMMON_COLOR_MAP` or :data:`HIGHLIGHT_COLOR_MAP`
    (case insensitive), and ``#rgb`` or ``#rrggbb`` hex.
    """
    if not isinstance(color, str):
        return None
    color = color.strip()
    for color_map in (COMMON_COLOR_MAP, HIGHLIGHT_COLOR_MAP):
        for name, rgb in color_map.items():
            if name.lower() == color.lower():
                return list(rgb)
    if not color.startswith('#'):
        return None
    digits = color[1:]
    if len(digits) == 3:
        digits = ''.join(d * 2 for d in digits)
    if len(digits) != 6:
        return None
    try:
        return [int(digits[idx:idx + 2], 16) for idx in range(0, 6, 2)]
    except ValueError:
        return None

def to_hsv(rgb):
    """Convert an RGB triple of 0-255 values into hue, saturation and value."""
    return colorsys.rgb_to_hsv(*[component / 255.0 for component in rgb])

def get_highlight_color_name(highlight_color):
    """Get color name nearest from the argument.

    The argument is a common color name or a hex string; see
    :func:`parse_color`. The returned name is always one Word accepts, so
    colors it has no highlight for, such as ``'lightgreen'``, snap to the
    closest one it does.
    """
    highlight_rgb = parse_color(highlight_color)
    if highlight_rgb is None:
        return DEFAULT_HIGHLIGHT_COLOR
    hue, sat, val = to_hsv(highlight_rgb)
    if sat < 0.1: # an achromatic color has no hue, and no good match here
        return DEFAULT_HIGHLIGHT_COLOR
    def dist(rgb):
        """Return the distance from the requested color, hue first.

        The palette is a set of saturated hues, so plain RGB distance picks
        badly for pale colors; matching hue before brightness does not.
        """
        other_hue, other_sat, other_val = to_hsv(rgb)
        hue_diff = abs(hue - other_hue)
        hue_diff = min(hue_diff, 1.0 - hue_diff) * 2.0
        return (4.0 * hue_diff ** 2
                + (val - other_val) ** 2
                + 0.5 * (sat - other_sat) ** 2)
    color_name, _ = min(
        ((name, dist(rgb)) for name, rgb in HIGHLIGHT_COLOR_MAP.items()),
        key=lambda name_and_dist: name_and_dist[1])
    return color_name

class DocxFormatter(Formatter):
    """Pygments formatter writing highlighted code as WordprocessingML."""
    def __init__(self, **options):
        """Read the line number, highlighted line and trimming options."""
        super(DocxFormatter, self).__init__(**options)
        self.linenos = options.get('linenos', False)
        self.hl_lines = options.get('hl_lines', [])
        self.linenostart = options.get('linenostart', 1)
        self.trim_last_line_break = options.get('trim_last_line_break', False)
        self.highlight = get_highlight_color_name(self.style.highlight_color)

    def format_unencoded(self, tokensource, outfile):
        """Write the highlighted tokens as WordprocessingML.

        Tokens are split into lines, then written as a single paragraph, or as a
        two-column table when line numbers are requested.
        """
        # pylint: disable=too-many-branches
        lines = [[]]
        for ttype, value in tokensource:
            if value == '\n':
                lines.append([])
            else:
                while not self.style.styles_token(ttype) and ttype.parent:
                    ttype = ttype.parent
                style = self.style.style_for_token(ttype)
                buf = []
                if style['bgcolor']:
                    buf.append(r'<w:shd w:themeFill="%s" />' % style['bgcolor'])
                if style['color']:
                    buf.append(r'<w:color w:val="%s" />' % style['color'])
                if style['bold']:
                    buf.append(r'<w:b />')
                if style['italic']:
                    buf.append(r'<w:i />')
                if style['underline']:
                    buf.append(r'<w:u />')
                if style['border']:
                    buf.append(r'<w:bdr w:val="single" w:space="0" w:color="%s" />' %
                               style['border'])

                style = ''.join(buf)
                value = saxutils.escape(value)
                index = 0
                while index < len(value):
                    idx = value.find('\n', index)
                    if idx == -1:
                        lines[-1].append((value[index:], style))
                        break
                    else:
                        lines[-1].append((value[index:idx], style))
                        lines.append([])
                        index = idx + 1

        if self.trim_last_line_break and lines[-1] == []:
            lines.pop()

        if self.linenos:
            self.output_as_table_with_linenos(outfile, lines)
        else:
            self.output_as_paragraph(outfile, lines)

    def output_as_paragraph(self, outfile, lines):
        """Write the lines as one paragraph, separated by line breaks."""
        outfile.write(
            '<w:p xmlns:w='
            '"http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
            '>')
        outfile.write(
            '<w:pPr>'
            '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>'
            '</w:pPr>' % self.style.background_color[1:7])
        for lineno, tokens in enumerate(lines, 1):
            self.output_line(outfile, lineno, tokens)
            if lineno != len(lines):
                outfile.write(r'<w:r><w:br /></w:r>')
        outfile.write('</w:p>')

    def output_as_table_with_linenos(self, outfile, lines):
        """Write the lines as a table whose first column holds the line numbers."""
        outfile.write(
            '<w:tbl xmlns:w='
            '"http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
            '>')
        bgcolor = self.style.background_color[1:7]
        for lineno, tokens in enumerate(lines, 1):
            outfile.write('<w:tr>')
            outfile.write('<w:tc><w:p>')
            outfile.write('<w:pPr><w:shd w:val="clear"/></w:pPr>')
            outfile.write(
                '<w:r><w:t>%d</w:t></w:r>' % (self.linenostart + lineno - 1))
            outfile.write('</w:p></w:tc>')
            outfile.write('<w:tc><w:p>')
            outfile.write(
                '<w:pPr>'
                '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>'
                '</w:pPr>' % bgcolor)
            self.output_line(outfile, lineno, tokens)
            outfile.write('</w:p></w:tc>')
            outfile.write('</w:tr>')
        outfile.write('</w:tbl>')

    def output_line(self, outfile, lineno, tokens):
        """Write one line of tokens as runs, highlighting it if requested."""
        for text, style in tokens:
            outfile.write(r'<w:r>')
            if lineno in self.hl_lines:
                style += r'<w:highlight w:val="%s" />' % self.highlight
            if style:
                outfile.write(r'<w:rPr>%s</w:rPr>' % style)
            if text.find(' ') != -1:
                outfile.write(r'<w:t xml:space="preserve">')
            else:
                outfile.write(r'<w:t>')
            outfile.write(text)
            outfile.write(r'</w:t>')
            outfile.write(r'</w:r>')

class DocxPygmentsBridge(PygmentsBridge):
    """Sphinx's highlighting bridge, using :class:`DocxFormatter`."""
    def __init__(self, dest, stylename, trim_doctest_flags=None):
        """Create the bridge, using :class:`DocxFormatter` as its formatter."""
        if trim_doctest_flags is not None:
            PygmentsBridge.__init__(self, dest, stylename, trim_doctest_flags)
        else:
            PygmentsBridge.__init__(self, dest, stylename)
        self.formatter = DocxFormatter

    def highlight_block(self, source, lang, *args, **kwargs):
        """Highlight a code block, keeping its original trailing line break."""
        # pylint: disable=arguments-differ
        # highlight_block may append a line break to the tail of the code
        kwargs['trim_last_line_break'] = not source.endswith('\n')
        return super(DocxPygmentsBridge, self).highlight_block(
            source, lang, *args, **kwargs)
