# -*- coding: utf-8 -*-
'''
  Microsoft Word 2007 Document Composer

  Copyright 2011 by haraisao at gmail dot com

  This software based on 'python-docx' which developed by Mike MacCana.

  --------

  Open and modify Microsoft Word 2007 docx files
  (called 'OpenXML' and 'Office OpenXML' by Microsoft)

  Part of Python's docx module - http://github.com/mikemaccana/python-docx
  See LICENSE for licensing information.

  --------

  Modifications for sphinx_docxbuilder-jm by Nefti-sama
  https://github.com/Nefti-sama/sphinx_docxbuilder-jm
'''

import base64
import copy
import datetime
import io
import mimetypes
import os
import posixpath
import re
import time
import urllib.parse
import urllib.request
import zipfile
import six
from lxml import etree

# cairosvg maps one CSS px to one device px, which prints blurry at Word's
# resolutions. Oversampling raises the pixel density only: the display size
# comes from the SVG's own dimensions and does not change with this.
SVG_RASTER_SCALE = 2

# The a:ext uri Word 2016 stamps on the extension holding an SVG blip. The
# value is fixed by the format; clients match on it and ignore anything else.
SVG_BLIP_EXT_URI = '{96DAC541-7B7A-43D3-8B79-37D633B846F1}'

# The attributes an SVG points at an external file with. References made
# through CSS url() are not rewritten, so an SVG that pulls a bitmap in
# through a stylesheet still draws empty.
SVG_HREF_ATTRS = ('href', '{http://www.w3.org/1999/xlink}href')

# All Word prefixes / namespace matches used in document.xml & core.xml.
# LXML doesn't actually use prefixes (just the real namespace) , but these
# make it easier to copy Word output more easily.
NSPREFIXES = {
    # Text Content
    'mv': 'urn:schemas-microsoft-com:mac:vml',
    'mo': 'http://schemas.microsoft.com/office/mac/office/2008/main',
    've': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
    'o': 'urn:schemas-microsoft-com:office:office',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'v': 'urn:schemas-microsoft-com:vml',
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'w10': 'urn:schemas-microsoft-com:office:word',
    'wne': 'http://schemas.microsoft.com/office/word/2006/wordml',
    # Drawing
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
    'asvg': 'http://schemas.microsoft.com/office/drawing/2016/SVG/main',
    # Properties (core and extended)
    'cp': "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    'dc': "http://purl.org/dc/elements/1.1/",
    'dcterms': "http://purl.org/dc/terms/",
    'dcmitype': "http://purl.org/dc/dcmitype/",
    'ds': 'http://purl.oclc.org/ooxml/officeDocument/customXml',
    'xsi': "http://www.w3.org/2001/XMLSchema-instance",
    'ep': 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties',
    # Content Types (we're just making up our own namespaces here to save time)
    'ct': 'http://schemas.openxmlformats.org/package/2006/content-types',
    # Package Relationships (we're just making up our own namespaces here to save time)
    'pr': 'http://schemas.openxmlformats.org/package/2006/relationships',
    # Variant Types
    'vt': 'http://purl.oclc.org/ooxml/officeDocument/docPropsVTypes',
    # xml
    'xml': 'http://www.w3.org/XML/1998/namespace'
}

# pylint: disable=line-too-long
REL_TYPE_DOC = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument'
REL_TYPE_APP = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties'
REL_TYPE_CORE = 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties'
REL_TYPE_STYLES = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles'
REL_TYPE_NUMBERING = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering'
REL_TYPE_FOOTNOTES = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes'
REL_TYPE_HEADER = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/header'
REL_TYPE_FOOTER = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer'
REL_TYPE_CUSTOM = 'http://purl.oclc.org/ooxml/officeDocument/relationships/customProperties'

REL_TYPE_COMMENTS = 'http://purl.oclc.org/ooxml/officeDocument/relationships/comments'
REL_TYPE_ENDNOTES = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes'
REL_TYPE_FONT_TABLE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable'
REL_TYPE_GLOSSARY_DOCUMENT = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/glossaryDocument'
REL_TYPE_SETTINGS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings'
REL_TYPE_STYLES_WITH_EFFECTS = 'http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects'
REL_TYPE_THEME = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme'
REL_TYPE_WEB_SETTINGS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/webSettings'

REL_TYPE_CUSTOM_XML = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml'
REL_TYPE_CUSTOM_XML_PROPS = 'http://purl.oclc.org/ooxml/officeDocument/relationships/customXmlProps'
REL_TYPE_THUMBNAIL = 'http://purl.oclc.org/ooxml/officeDocument/relationships/metadata/thumbnail'


CONTENT_TYPE_DOC_MAIN = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
CONTENT_TYPE_STYLES = 'application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml'
CONTENT_TYPE_NUMBERING = 'application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml'
CONTENT_TYPE_FOOTNOTES = 'application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml'
CONTENT_TYPE_SETTINGS = 'application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml'
CONTENT_TYPE_CORE_PROPERTIES = 'application/vnd.openxmlformats-package.core-properties+xml'
CONTENT_TYPE_EXTENDED_PROPERTIES = 'application/vnd.openxmlformats-officedocument.extended-properties+xml'
CONTENT_TYPE_CUSTOM_PROPERTIES = 'application/vnd.openxmlformats-officedocument.custom-properties+xml'
CONTENT_TYPE_CUSTOM_XML_DATA_STORAGE_PROPERTIES = 'application/vnd.openxmlformats-officedocument.customXmlProperties+xml'
# pylint: enable=line-too-long

COVER_PAGE_PROPERTY_ITEMID = '{55AF091B-3C7A-41E3-B477-F2FDAA23CFDA}'

#####################

def xml_encode(value):
    """Escape control characters as ``_xNNNN_``, as OOXML requires."""
    value = re.sub(r'_(?=x[0-9a-fA-F]{4}_)', r'_x005f_', value)
    return re.sub(r'[\x00-\x1f]', lambda m: '_x%04x_' % ord(m.group(0)), value)

def norm_name(tagname):
    '''
       Convert the 'tagname' to a formal expression.
          'ns:tag' --> '{namespace}tag'
          'tag' --> 'tag'
    '''
    if tagname.startswith('{'):
        return tagname
    ns_name = tagname.split(':', 1)
    if len(ns_name) > 1:
        tagname = "{%s}%s" % (NSPREFIXES[ns_name[0]], ns_name[1])
    return tagname


def get_elements(xml, path):
    '''
       Get elements from a Element tree with 'path'.
    '''
    return xml.xpath(path, namespaces=NSPREFIXES)


def parse_tag_list(tag):
    """Split a tag specification into ``(tagname, attributes, text)``.

    The specification is a tag name, or a list of the name followed by a text
    string and/or an attribute dict, in either order.
    """
    tagname = ''
    tagtext = ''
    attributes = {}

    if isinstance(tag, str):
        tagname = tag
    elif isinstance(tag, list):
        tagname = tag[0]
        taglen = len(tag)
        if taglen > 1:
            if isinstance(tag[1], six.string_types):
                tagtext = tag[1]
            else:
                attributes = tag[1]
        if taglen > 2:
            if isinstance(tag[2], six.string_types):
                tagtext = tag[2]
            else:
                attributes = tag[2]
    else:
        raise RuntimeError("Invalid tag: %s" % tag)

    return tagname, attributes, tagtext


def extract_nsmap(tag, attributes):
    """Return the namespace map needed by a tag and its attribute names."""
    result = {}
    ns_name = tag.split(':', 1) if not tag.startswith('{') else []
    if len(ns_name) > 1 and NSPREFIXES.get(ns_name[0]):
        result[ns_name[0]] = NSPREFIXES[ns_name[0]]

    for attr in attributes:
        ns_name = attr.split(':', 1) if not attr.startswith('{') else []
        if len(ns_name) > 1 and NSPREFIXES.get(ns_name[0]):
            result[ns_name[0]] = NSPREFIXES[ns_name[0]]

    return result

def make_element_tree(arg, _xmlns=None):
    """Build an lxml element tree from nested tag specifications.

    ``arg`` is a list whose first item is a tag specification (see
    `parse_tag_list`) and whose remaining items are child specifications.
    """
    tagname, attributes, tagtext = parse_tag_list(arg[0])
    children = arg[1:]

    nsmap = extract_nsmap(tagname, attributes)

    if _xmlns is None:
        newele = etree.Element(norm_name(tagname), nsmap=nsmap)
    else:
        newele = etree.Element(norm_name(tagname), xmlns=_xmlns, nsmap=nsmap)

    if tagtext:
        newele.text = tagtext

    for attr in attributes:
        newele.set(norm_name(attr), attributes[attr])

    for child in children:
        chld = make_element_tree(child)
        if chld is not None:
            newele.append(chld)

    return newele


def get_attribute(xml, path, name):
    """Return an attribute of the first element matching ``path``, or None."""
    elems = get_elements(xml, path)
    if elems == []:
        return None
    return elems[0].attrib[norm_name(name)]

def get_max_attribute(elems, attribute, to_int=int):
    '''
       Get the maximum integer attribute among the specified elems
    '''
    if not elems:
        return 0
    return max(map(lambda e: to_int(e.get(attribute)), elems))

def fromstring(xml):
    """Parse string OOXML fragments"""
    ns = ' '.join('xmlns:%s="%s"' % (k, v) for k, v in NSPREFIXES.items())
    return etree.fromstring('<dummy %s>%s</dummy>' % (ns, xml)).getchildren()

def local_to_utc(value):
    """Convert a naive local datetime to UTC, keeping its microseconds."""
    utc = datetime.datetime.utcfromtimestamp(time.mktime(value.timetuple()))
    return utc.replace(microsecond=value.microsecond)

def convert_to_W3CDTF_string(value): # pylint: disable=invalid-name
    """Format a date, datetime or date string as W3CDTF, or None.

    None means the value is not a date this function recognises.
    """
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            offset = value.utcoffset()
            value = value.replace(tzinfo=None) - offset
        else:
            value = local_to_utc(value)
        return value.strftime('%Y-%m-%dT%H:%M:%SZ')
    if isinstance(value, datetime.date):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, six.string_types):
        for date_format in ('%Y', '%Y-%m', '%Y-%m-%d'):
            try:
                datetime.datetime.strptime(value, date_format)
            except ValueError:
                continue
            return value
        datetime_formats = [
            ('%Y-%m-%dT%H', '%Y-%m-%dT%H:%MZ'),
            ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%MZ'),
            ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ'),
            ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S.%fZ'),
        ]
        for from_format, to_format in datetime_formats:
            try:
                date_time = local_to_utc(
                    datetime.datetime.strptime(value, from_format))
            except ValueError:
                continue
            return date_time.strftime(to_format)
    return None

#
#  DocxDocument class
#   This class for analizing docx-file
#

CORE_PROPERTY_KEYS = (
        ('dc', 'title', {}),
        ('dc', 'creator', {}),
        ('dc', 'language', {}),
        ('cp', 'category', {}),
        ('cp', 'contentStatus', {}),
        ('dc', 'description', {}),
        ('dc', 'identifier', {}),
        ('cp', 'lastModifiedBy', {}),
        ('cp', 'lastPrinted', {}),
        ('cp', 'revision', {}),
        ('dc', 'subject', {}),
        ('cp', 'version', {}),
        ('cp', 'keywords', {}),
        ('dcterms', 'created', {'xsi:type': 'dcterms:W3CDTF'}),
        ('dcterms', 'modified', {'xsi:type': 'dcterms:W3CDTF'}),
)

APP_PROPERTY_KEYS = {
        'Manager': six.string_types,
        'Company': six.string_types,
}

COVER_PAGE_PROPERTY_KEYS = {
        'Abstract',
        'CompanyAddress',
        'CompanyEmail',
        'CompanyFax',
        'CompanyPhone',
        'PublishDate',
}

CUSTOM_PROPERTY_TYPES = (
        (bool, 'vt:bool', lambda v: str(v).lower()),
        (six.integer_types, 'vt:i8', str),
        (float, 'vt:r8', str),
        (six.string_types, 'vt:lpwstr', str),
        (datetime.datetime, 'vt:date', convert_to_W3CDTF_string),
)

def check_core_props(key, value, core_props):
    """Store ``value`` in ``core_props`` if ``key`` names a core property.

    Returns whether the key was consumed; raises RuntimeError when the key is
    a core property but the value cannot be used for it.
    """
    core_prop_keys = set(key for _, key, _ in CORE_PROPERTY_KEYS)
    if key not in core_prop_keys:
        key = key[0].lower() + key[1:]
        if key not in core_prop_keys:
            return False

    if key == 'lastPrinted':
        time_fmt = '%Y-%m-%dT%H:%M:%S'
        if isinstance(value, (datetime.datetime, datetime.date)):
            core_props['lastPrinted'] = value.strftime(time_fmt)
        else:
            try:
                datetime.datetime.strptime(value, time_fmt)
            except ValueError:
                raise RuntimeError('Invalid value')
            core_props['lastPrinted'] = value
        return True

    for doctime in ['created', 'modified']:
        if key != doctime:
            continue
        value = convert_to_W3CDTF_string(value)
        if value is None:
            raise RuntimeError('Invalid value')
        core_props[doctime] = value
        return True

    if isinstance(value, six.string_types):
        core_props[key] = value
        return True
    if isinstance(value, (list, tuple)):
        sep = ',' if key == 'keywords' else '; '
        try:
            core_props[key] = sep.join(value)
            return True
        except TypeError:
            raise RuntimeError('Invalid value type')
    raise RuntimeError('Invalid value type')

def check_app_props(key, value, app_props):
    """Store ``value`` in ``app_props`` if ``key`` names an extended property.

    Returns whether the key was consumed; raises RuntimeError on a value of
    the wrong type.
    """
    check_type = APP_PROPERTY_KEYS.get(key)
    if check_type is None:
        key = key[0].upper() + key[1:]
        check_type = APP_PROPERTY_KEYS.get(key)
    if check_type is None:
        return False
    if not isinstance(value, check_type):
        raise RuntimeError('Invalid value type')
    if isinstance(value, bool):
        value = str(value).lower()
    else:
        value = str(value)
    app_props[key] = value
    return True

def check_cover_page_props(key, value, cover_page_props):
    """Store ``value`` in ``cover_page_props`` if ``key`` names a cover page property.

    Returns whether the key was consumed; raises RuntimeError on an
    unusable value.
    """
    if key not in COVER_PAGE_PROPERTY_KEYS:
        key = key[0].upper() + key[1:]
        if key not in COVER_PAGE_PROPERTY_KEYS:
            return False
    if key == 'PublishDate':
        value = convert_to_W3CDTF_string(value)
        if value is None:
            raise RuntimeError('Invalid value')
    else:
        if not isinstance(value, six.string_types):
            raise RuntimeError('Invalid value')
    cover_page_props[key] = value
    return True

def check_custom_props(key, value, custom_props):
    """Store ``value`` in ``custom_props``, which accepts any supported type.

    Raises RuntimeError when no entry of `CUSTOM_PROPERTY_TYPES` matches.
    """
    for prop_type, _, _ in CUSTOM_PROPERTY_TYPES:
        if not isinstance(value, prop_type):
            continue
        custom_props[key] = value
        return True
    raise RuntimeError('Invalid value type')

def classify_properties(props):
    """Sort document properties into core, app, cover page and custom groups.

    Returns ``(props_map, invalids)``, where ``invalids`` maps a rejected key
    to the reason it was rejected.
    """
    props_map = {'core': {}, 'app': {}, 'cover_page': {}, 'custom': {}}
    invalids = {}
    for key, value in props.items():
        try:
            if check_core_props(key, value, props_map['core']):
                continue
            if check_app_props(key, value, props_map['app']):
                continue
            if check_cover_page_props(key, value, props_map['cover_page']):
                continue
            check_custom_props(key, value, props_map['custom'])
        except RuntimeError as e:
            invalids[key] = str(e)
    return props_map, invalids

# --- DOCPROPERTY field baking ------------------------------------------------
# Word refreshes DOCPROPERTY fields when it opens a document, so a style file
# usually ships them with a stale cached result (`<n/a>` is the common
# placeholder). Every other consumer -- LibreOffice headless, pandoc, PDF
# pipelines, plain text extraction -- reads that cached run verbatim. The
# helpers below rewrite the cached result with the value actually written into
# docProps, so the text is correct before Word ever touches the file.

# DOCPROPERTY names of the built-in properties that Word spells differently
# from the underlying XML element, mapped to (property category, key).
BUILTIN_PROPERTY_FIELDS = {
    'title': ('core', 'title'),
    'author': ('core', 'creator'),
    'subject': ('core', 'subject'),
    'keywords': ('core', 'keywords'),
    'comments': ('core', 'description'),
    'category': ('core', 'category'),
    'content status': ('core', 'contentStatus'),
    'last author': ('core', 'lastModifiedBy'),
    'revision number': ('core', 'revision'),
    'manager': ('app', 'Manager'),
    'company': ('app', 'Company'),
}

DOC_PROPERTY_INSTRUCTION = re.compile(r'\s*DOCPROPERTY\s+(?:"([^"]*)"|(\S+))')

def format_property_value(value):
    '''Render a property value the way Word displays it in a field.

    Returns None when the rendering is not ours to decide -- dates follow the
    reader's locale and booleans follow Word's own conventions -- so those
    fields keep the cached result from the style file.
    '''
    if isinstance(value, bool) or isinstance(value, datetime.date):
        return None
    if isinstance(value, six.string_types):
        return value
    if isinstance(value, six.integer_types) or isinstance(value, float):
        return str(value)
    return None

def make_doc_property_map(props):
    '''Map lower-cased DOCPROPERTY field names to their resolved values.
    '''
    prop_map = {}
    for name, value in props.get('custom', {}).items():
        text = format_property_value(value)
        if text is not None:
            prop_map[name.lower()] = text
    for field_name, (category, key) in BUILTIN_PROPERTY_FIELDS.items():
        text = format_property_value(props.get(category, {}).get(key))
        if text:
            prop_map.setdefault(field_name, text)
    return prop_map

def get_doc_property_name(instruction):
    '''Extract the property name from a field instruction, or None if the
       instruction is not a DOCPROPERTY field.
    '''
    match = DOC_PROPERTY_INSTRUCTION.match(instruction)
    if match is None:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)

def get_run_text(run):
    """Return the concatenated text of a run's ``w:t`` elements."""
    return ''.join(t.text or '' for t in run.findall(norm_name('w:t')))

def make_field_run(style_source, contents):
    '''Make a run carrying the run properties of 'style_source', so a
       replaced field result keeps the formatting of the original.
    '''
    run = etree.Element(norm_name('w:r'))
    if style_source is not None:
        run_prop = style_source.find(norm_name('w:rPr'))
        if run_prop is not None:
            run.append(copy.deepcopy(run_prop))
    run.append(contents)
    return run

def make_field_result_run(style_source, value):
    """Make a run holding ``value`` as the cached result of a field."""
    text = etree.Element(norm_name('w:t'))
    text.set(norm_name('xml:space'), 'preserve')
    text.text = value
    return make_field_run(style_source, text)

def make_field_char_run(style_source, char_type):
    """Make a run holding a ``w:fldChar`` of the given type."""
    fld_char = etree.Element(norm_name('w:fldChar'))
    fld_char.set(norm_name('w:fldCharType'), char_type)
    return make_field_run(style_source, fld_char)

def bake_simple_field(field, prop_map):
    '''Replace the result of a 'w:fldSimple' DOCPROPERTY field.
    '''
    name = get_doc_property_name(field.get(norm_name('w:instr'), ''))
    if name is None:
        return False
    value = prop_map.get(name.lower())
    if value is None:
        return False
    runs = field.findall(norm_name('w:r'))
    if len(runs) == 1 and len(field) == 1 and get_run_text(runs[0]) == value:
        return False
    result = make_field_result_run(runs[0] if runs else None, value)
    for child in list(field):
        field.remove(child)
    field.append(result)
    return True

def apply_doc_property_field(parent, field, prop_map):
    '''Replace the cached result of one complex DOCPROPERTY field.
    '''
    if field['nested']:
        # A field whose result is computed from other fields is none of our
        # business; leave it for Word.
        return False
    name = get_doc_property_name(''.join(field['instructions']))
    if name is None:
        return False
    value = prop_map.get(name.lower())
    if value is None:
        return False
    results = field['results']
    if len(results) == 1 and get_run_text(results[0]) == value:
        return False
    style_source = results[0] if results else field['begin']
    result = make_field_result_run(style_source, value)
    if field['separate'] is None:
        # No cached result at all: give the field one, so readers that do not
        # evaluate fields have something to show.
        index = parent.index(field['end'])
        parent.insert(index, make_field_char_run(style_source, 'separate'))
        parent.insert(index + 1, result)
    else:
        parent.insert(parent.index(results[0] if results else field['end']),
                      result)
        for run in results:
            parent.remove(run)
    return True

def bake_complex_fields(parent, prop_map):
    '''Replace the cached results of the complex DOCPROPERTY fields whose runs
       are direct children of 'parent'.
    '''
    run_tag = norm_name('w:r')
    fld_char_tag = norm_name('w:fldChar')
    fld_char_type = norm_name('w:fldCharType')
    instr_tag = norm_name('w:instrText')
    changed = False
    depth = 0
    field = None
    for child in list(parent):
        if child.tag != run_tag:
            continue
        fld_char = child.find(fld_char_tag)
        char_type = None if fld_char is None else fld_char.get(fld_char_type)
        if char_type == 'begin':
            depth += 1
            if depth == 1:
                field = {'begin': child, 'end': None, 'separate': None,
                         'instructions': [], 'results': [], 'nested': False}
            elif field is not None:
                field['nested'] = True
            continue
        if depth == 0:
            continue
        if char_type == 'end':
            depth -= 1
            if depth == 0 and field is not None:
                field['end'] = child
                if apply_doc_property_field(parent, field, prop_map):
                    changed = True
                field = None
            continue
        if depth != 1 or field is None:
            continue
        if char_type == 'separate':
            field['separate'] = child
        elif field['separate'] is None:
            instr = child.find(instr_tag)
            if instr is not None:
                field['instructions'].append(instr.text or '')
        else:
            field['results'].append(child)
    return changed

def bake_doc_property_fields(xml, prop_map):
    '''Rewrite the cached results of every DOCPROPERTY field in 'xml'.

       Returns True if anything changed.
    '''
    if xml is None or not prop_map:
        return False
    changed = False
    for field in list(xml.iter(norm_name('w:fldSimple'))):
        if bake_simple_field(field, prop_map):
            changed = True
    run_tag = norm_name('w:r')
    parents = {}
    for fld_char in xml.iter(norm_name('w:fldChar')):
        run = fld_char.getparent()
        if run is None or run.tag != run_tag:
            continue
        parent = run.getparent()
        if parent is not None:
            parents[id(parent)] = parent
    for parent in parents.values():
        if bake_complex_fields(parent, prop_map):
            changed = True
    return changed

def get_orient(section_prop):
    """Return the page orientation of a section, ``portrait`` by default."""
    page_size = get_elements(section_prop, 'w:pgSz')[0]
    return page_size.attrib.get(norm_name('w:orient'), 'portrait')

def rotate_orient(section_prop):
    """Swap a section's page width and height, and return the section."""
    page_size = get_elements(section_prop, 'w:pgSz')[0]
    orient_attr = norm_name('w:orient')
    current_orient = page_size.attrib.get(orient_attr, 'portrait')
    orient = 'landscape' if current_orient == 'portrait' else 'portrait'
    w_attr = norm_name('w:w')
    h_attr = norm_name('w:h')
    width = page_size.attrib.get(w_attr)
    height = page_size.attrib.get(h_attr)
    page_size.attrib[w_attr] = height
    page_size.attrib[h_attr] = width
    page_size.attrib[orient_attr] = orient
    return section_prop

def set_title_page(section_prop, is_title_page):
    """Turn a section's separate first page header and footer on or off."""
    value = 'true' if is_title_page else 'false'
    title_page = get_elements(section_prop, 'w:titlePg')
    if not title_page:
        section_prop.append(
            make_element_tree([['w:titlePg', {'w:val': value}]]))
        return
    title_page[0].attrib[norm_name('w:val')] = value

def set_page_number(section_prop, page_number=None):
    """Set the starting page number of a section, or clear it when None."""
    page_number_type = get_elements(section_prop, 'w:pgNumType')
    if not page_number_type:
        if page_number is not None:
            section_prop.append(
                make_element_tree(
                    [['w:pgNumType', {'w:start': str(page_number)}]]))
        return
    if page_number is None:
        try:
            del page_number_type[-1].attrib[norm_name('w:start')]
        except KeyError:
            pass
    else:
        page_number_type[-1].attrib[norm_name('w:start')] = str(page_number)

def copy_section_property(section_prop, is_continuous_section):
    """Deep copy a section property.

    A continuous section starts neither a new first page nor a new page
    numbering, so both are cleared in the copy.
    """
    section_prop = copy.deepcopy(section_prop)
    if is_continuous_section:
        set_title_page(section_prop, False)
        set_page_number(section_prop, page_number=None)
    return section_prop

def get_contents_width(section_property):
    """Return the text width of a section in twips, columns accounted for."""
    width = get_contents_size(section_property, 'w:w', ('w:left', 'w:right'))
    cols_elems = get_elements(section_property, 'w:cols')
    if not cols_elems:
        return width
    cols = cols_elems[-1]
    num = int(cols.attrib.get(norm_name('w:num'), '1'))
    space = int(cols.attrib.get(norm_name('w:space'), '720'))
    return (width - (space * (num - 1))) // num # TODO non equal col width

def get_contents_height(section_property):
    """Return the text height of a section in twips."""
    return get_contents_size(section_property, 'w:h', ('w:top', 'w:bottom'))

def get_contents_size(section_property, size_prop, margin_props):
    """Return a page dimension in twips, less the two given margins."""
    paper_size = get_elements(section_property, 'w:pgSz')[0]
    size = int(paper_size.get(norm_name(size_prop)))
    paper_margin = get_elements(section_property, 'w:pgMar')[0]
    margin = (
        int(paper_margin.get(norm_name(margin_props[0]))) +
        int(paper_margin.get(norm_name(margin_props[1]))))
    return size - margin

def make_default_page_size():
    """Make a US Letter portrait ``w:pgSz`` element."""
    return make_element_tree([['w:pgSz', {
        'w:w': '12240', 'w:h': '15840', 'w:orient': 'portrait',
    }]])

def make_default_page_margin():
    """Make a ``w:pgMar`` element with one inch margins."""
    return make_element_tree([['w:pgMar', {
        'w:top': '1440', 'w:right': '1440',
        'w:bottom': '1440', 'w:left': '1440',
        'w:header': '720', 'w:footer': '720', 'w:gutter': '0',
    }]])

# Paragraphs and Runs

def get_properties_tree(prop):
    """Convert a property element back into a tag specification tree."""
    tree = [[prop.tag, prop.attrib]]
    tree.extend((get_properties_tree(child_prop) for child_prop in prop))
    return tree

def get_paragraph_properties(paragraph):
    """Return a paragraph's properties as tag specification trees."""
    props = get_elements(paragraph, 'w:pPr')
    if not props:
        return []
    return [get_properties_tree(prop) for prop in props[0]]

def get_paragraph_contents(paragraph):
    """Return a paragraph's child elements, minus its ``w:pPr``."""
    return get_elements(paragraph, '*[not(self::w:pPr)]')

def add_page_break_before_to_first_paragraph(xml):
    """Make the first paragraph of ``xml`` start on a new page."""
    paragraphs = get_elements(xml, '//w:p')
    if not paragraphs:
        return
    para = paragraphs[0]
    p_props = get_elements(para, 'w:pPr')
    tree = [['w:pageBreakBefore', {'w:val': '1'}]]
    if p_props:
        p_props[0].append(make_element_tree(tree))
    else:
        para.append(make_element_tree([['w:pPr', tree]]))

def make_run_style_property(style_id):
    """Return the run property dict applying a character style, if any."""
    if style_id is None:
        return {}
    return {'w:rStyle': {'w:val': style_id}}

def make_paragraph(
        indent, right_indent, style, align, keep_lines, keep_next, list_info,
        properties=None):
    """Make a paragraph with the given properties and no content.

    ``list_info`` is a ``(num_id, level)`` pair making the paragraph a list
    item; ``properties`` holds extra property trees to append.
    """
    style_tree = [['w:pPr']]
    if style is not None:
        style_tree.append([['w:pStyle', {'w:val': style}]])
    ind_attrs = {}
    if list_info is not None:
        num_id, list_level = list_info
        style_tree.append([
            ['w:numPr'],
            [['w:ilvl', {'w:val': str(list_level)}]],
            [['w:numId', {'w:val': str(num_id)}]],
        ])
    if indent is not None:
        ind_attrs['w:leftChars'] = '0'
        ind_attrs['w:left'] = str(indent)
    if right_indent is not None:
        ind_attrs['w:right'] = str(right_indent)
    if ind_attrs:
        style_tree.append([['w:ind', ind_attrs]])
    if align is not None:
        style_tree.append([['w:jc', {'w:val': align}]])
    if keep_lines:
        style_tree.append([['w:keepLines']])
    if keep_next:
        style_tree.append([['w:keepNext']])
    if properties is not None:
        style_tree.extend(properties)

    paragraph_tree = [['w:p'], style_tree]
    return make_element_tree(paragraph_tree)

def make_paragraph_spacing_property(**kwargs):
    """Make a ``w:spacing`` tree from ``before``, ``after`` and ``line``."""
    attr = {}
    for key in ['before', 'after', 'line']:
        value = kwargs.get(key)
        if value is None:
            continue
        attr['w:' + key] = str(value)
    return [['w:spacing', attr]]

def make_paragraph_shading_property(pattern, **kwargs):
    """Make a ``w:shd`` tree from a fill pattern, ``color`` and ``fill``."""
    attr = {'w:val': pattern}
    if kwargs:
        for key in ['color', 'fill']:
            value = kwargs.get(key)
            if value is None:
                continue
            attr['w:' + key] = value
    return [['w:shd', attr]]

def make_paragraph_border_property(**kwargs):
    """Make a ``w:pBdr`` tree from per-side border descriptions.

    Each keyword names a side and takes a dict of border attributes, or None
    to suppress that side's border.
    """
    key_list = [
        ('size', 'w:sz'), ('space', 'w:space'), ('color', 'w:color'),
        ('shadow', 'w:shadow'), ('frame', 'w:frame')
    ]
    border_tree = [['w:pBdr']]
    for kind in ['top', 'left', 'bottom', 'right', 'between', 'bar']:
        if kind not in kwargs:
            continue
        value = kwargs[kind]
        if value is None:
            attr = {'w:val': 'nil'}
        else:
            pattern = value['pattern']
            attr = {'w:val': pattern if pattern is not None else 'nil'}
            for key, attr_key in key_list:
                val = value.get(key)
                if val is not None:
                    attr[attr_key] = str(val)
        border_tree.append([['w:' + kind, attr]])
    return border_tree

def make_border_info(border_attrs):
    """Convert the attributes of a border element into a border description."""
    identity = lambda x: x
    to_bool = lambda x: x in ('true', '1')
    attr_list = [
        ('w:val', 'pattern', identity), ('w:color', 'color', identity),
        ('w:sz', 'size', int), ('w:space', 'space', int),
        ('w:shadow', 'shadow', to_bool), ('w:frame', 'frame', to_bool)
    ]
    border_info = {}
    for attr, key, convert in attr_list:
        val = border_attrs.get(norm_name(attr))
        if val is not None:
            border_info[key] = convert(val)
    return border_info

def make_section_prop_paragraph(section_prop):
    """Make an empty paragraph carrying a section property."""
    para = make_element_tree([['w:p'], [['w:pPr']]])
    para[0].append(section_prop)
    return para

def make_run(text, style, preserve_space):
    """Make a text run in the given run style.

    With ``preserve_space``, spacing is kept and newlines become breaks;
    otherwise newlines collapse into spaces.
    """
    run_tree = [['w:r']]
    run_prop = [['w:rPr']]
    for tagname, attrib in style.items():
        run_prop.append([[tagname, attrib]])
    if len(run_prop) != 1:
        run_tree.append(run_prop)
    if preserve_space:
        lines = text.split('\n')
        for index, line in enumerate(lines):
            run_tree.append([['w:t', line, {'xml:space': 'preserve'}]])
            if index != len(lines) - 1:
                run_tree.append([['w:br']])
    else:
        text = text.replace('\n', ' ')
        attrs = {}
        if text.startswith(' ') or text.endswith(' '):
            attrs['xml:space'] = 'preserve'
        run_tree.append([['w:t', text, attrs]])
    return make_element_tree(run_tree)

def make_break_run():
    """Make a run holding a single line break."""
    return make_element_tree([['w:r'], [['w:br']]])

def make_inline_picture_run(
        rid, picid, picname, cmwidth, cmheight, picdescription,
        nochangeaspect=True, nochangearrowheads=True, svg_rid=None):
    '''
      Take a relationship id, picture file name, and return a run element
      containing the image

      svg_rid, when given, names a vector copy of the same image. Word 2016
      and later draw that and ignore rid; older clients know nothing about
      the extension carrying it and draw the raster image rid points at.

      This function is based on 'python-docx' library
    '''
    non_visual_pic_prop_attrs = {
        'id': str(picid), 'name': picname, 'descr': picdescription
    }
    # OpenXML measures on-screen objects in English Metric Units
    emupercm = 360000
    ext_attrs = {
        'cx': str(int(cmwidth * emupercm)),
        'cy': str(int(cmheight * emupercm))
    }

    blip_tree = [['a:blip', {'r:embed': rid}]]
    if svg_rid is not None:
        blip_tree.append(
            [['a:extLst'],
             [['a:ext', {'uri': SVG_BLIP_EXT_URI}],
              [['asvg:svgBlip', {'r:embed': svg_rid}]]
             ]
            ])

    # There are 3 main elements inside a picture
    pic_tree = [
        ['pic:pic'],
        [['pic:nvPicPr'],  # The non visual picture properties
         [['pic:cNvPr', non_visual_pic_prop_attrs]],
         [['pic:cNvPicPr'],
          [['a:picLocks', {
              'noChangeAspect': str(int(nochangeaspect)),
              'noChangeArrowheads': str(int(nochangearrowheads))}]
          ]
         ]
        ],
        # The Blipfill - specifies how the image fills the picture
        # area (stretch, tile, etc.)
        [['pic:blipFill'],
         blip_tree,
         [['a:srcRect']],
         [['a:stretch'], [['a:fillRect']]]
        ],
        [['pic:spPr', {'bwMode': 'auto'}],  # The Shape properties
         [['a:xfrm'],
          [['a:off', {'x': '0', 'y': '0'}]],
          [['a:ext', ext_attrs]]
         ],
         [['a:prstGeom', {'prst': 'rect'}], ['a:avLst']],
         [['a:noFill']]
        ]
    ]

    graphic_tree = [
        ['a:graphic'],
        [['a:graphicData', {
            'uri': 'http://schemas.openxmlformats.org/drawingml/2006/picture'}],
         pic_tree
        ]
    ]

    inline_tree = [
        ['wp:inline', {'distT': "0", 'distB': "0", 'distL': "0", 'distR': "0"}],
        [['wp:extent', ext_attrs]],
        [['wp:effectExtent', {'l': '25400', 't': '0', 'r': '0', 'b': '0'}]],
        [['wp:docPr', non_visual_pic_prop_attrs]],
        [['wp:cNvGraphicFramePr'],
         [['a:graphicFrameLocks', {'noChangeAspect': '1'}]]
        ],
        graphic_tree
    ]

    run_tree = [
        ['w:r'],
        [['w:rPr'], [['w:noProof']]],
        [['w:drawing'], inline_tree]
    ]
    return make_element_tree(run_tree)

def make_omath_paragraph(omath_elems):
    """Make a centred ``m:oMathPara`` holding the given equations, line by line."""
    omath_paragraph = make_element_tree([
        ['m:oMathPara'],
        [['m:oMathParaPr'], [['m:jc', {'m:val': 'center'}]]],
    ])
    if omath_elems:
        for omath in omath_elems[:-1]:
            omath.append(make_element_tree([['m:r'], [['w:br']]]))
            omath_paragraph.append(omath)
        omath_paragraph.append(omath_elems[-1])
    return omath_paragraph

def make_omath_run(equation):
    """Make an ``m:oMath`` element from an equation, one run per line."""
    omath_tree = [['m:oMath']]
    equations = equation.split('\n')
    omath_tree.extend(
        [['m:r'], [['m:t', eq]], [['w:br']]] for eq in equations[:-1])
    omath_tree.append([['m:r'], [['m:t', equations[-1]]]])
    return make_element_tree(omath_tree)

# Tables

def make_table(
        style, width, indent, align, grid_col_list, has_head, has_first_column,
        properties=None):
    """Make an empty table with the given style, width and column grid.

    ``width`` is a fraction of the text width, or None for an automatic
    width; ``grid_col_list`` holds the column widths in twips.
    """
    look_attrs = {
        'w:noHBand': 'false', 'w:noVBand': 'false',
        'w:lastRow': 'false', 'w:lastColumn': 'false'
    }
    look_attrs['w:firstRow'] = 'true' if has_head else 'false'
    look_attrs['w:firstColumn'] = 'true' if has_first_column else 'false'
    if width is not None:
        width_attr = {'w:w': '%f%%' % (width * 100), 'w:type': 'pct'}
    else:
        width_attr = {'w:w': '0', 'w:type': 'auto'}
    property_tree = [
        ['w:tblPr'],
        [['w:tblW', width_attr]],
        [['w:tblInd', {'w:w': str(indent), 'w:type': 'dxa'}]],
        [['w:tblLook', look_attrs]],
    ]
    if style is not None:
        property_tree.insert(1, [['w:tblStyle', {'w:val': style}]])
    if align is not None:
        property_tree.append([['w:jc', {'w:val': align}]])
    if properties is not None:
        property_tree.extend(properties)

    table_grid_tree = [['w:tblGrid']]
    for grid_col in grid_col_list:
        table_grid_tree.append([['w:gridCol', {'w:w': str(int(grid_col))}]])

    table_tree = [
        ['w:tbl'],
        property_tree,
        table_grid_tree
    ]
    return make_element_tree(table_tree)

def make_row(index, is_head, cant_split, set_tbl_header, height):
    """Make an empty table row.

    ``index`` selects the banding; ``cant_split`` keeps the row on one page,
    ``set_tbl_header`` repeats it at the top of every page.
    """
    row_style_attrs = {
        'w:evenHBand': ('true' if index % 2 != 0 else 'false'),
        'w:oddHBand': ('true' if index % 2 == 0 else 'false'),
        'w:firstRow': ('true' if is_head else 'false'),
    }
    property_tree = [
        ['w:trPr'],
        [['w:cnfStyle', row_style_attrs]],
    ]
    if cant_split:
        property_tree.append([['w:cantSplit']])
    if set_tbl_header:
        property_tree.append([['w:tblHeader']])
    if height is not None:
        property_tree.append(
            [['w:trHeight', {'w:hRule': 'atLeast', 'w:val': str(height)}]])
    return make_element_tree([['w:tr'], property_tree])

def make_cell(index, is_first_column, cellsize, grid_span, vmerge, rotation,
              no_wrap=None, valign=None):
    """Make an empty table cell.

    ``grid_span`` spans columns, ``vmerge`` (``restart`` or ``continue``)
    merges rows, and ``rotation`` turns the text sideways.
    """
    cell_style = {
        'w:evenVBand': ('true' if index % 2 != 0 else 'false'),
        'w:oddVBand': ('true' if index % 2 == 0 else 'false'),
        'w:firstColumn': ('true' if is_first_column else 'false'),
    }
    property_tree = [
        ['w:tcPr'],
        [['w:cnfStyle', cell_style]],
    ]
    if cellsize is not None:
        property_tree.append(
            [['w:tcW', {'w:w': '%f%%' % (cellsize * 100), 'w:type': 'pct'}]])
    if grid_span > 1:
        property_tree.append([['w:gridSpan', {'w:val': str(grid_span)}]])
    if vmerge is not None:
        property_tree.append([['w:vMerge', {'w:val': vmerge}]])
    if rotation:
        property_tree.append([['w:textDirection', {'w:val': 'tbRlV'}]])
    if no_wrap is not None:
        property_tree.append([['w:noWrap', {'w:val': str(int(no_wrap))}]])
    if valign is not None:
        property_tree.append([['w:vAlign', {'w:val': valign}]])
    return make_element_tree([['w:tc'], property_tree])

def make_table_cell_margin_property(**kwargs):
    """Make a ``w:tblCellMar`` tree from per-side margin widths."""
    margin_tree = [['w:tblCellMar']]
    for kind in ['top', 'left', 'bottom', 'right']:
        if kind in kwargs:
            margin_tree.append(
                [['w:' + kind, make_table_width_attr(kwargs[kind])]])
    return margin_tree

def make_table_cell_spacing_property(val):
    """Make a ``w:tblCellSpacing`` tree from a width value."""
    return [['w:tblCellSpacing', make_table_width_attr(val)]]

def make_table_width_attr(val):
    """Convert a width value into OOXML width attributes.

    None gives no width, ``'auto'`` an automatic one, a float up to 1.0 a
    percentage, and anything else a measure in twips.
    """
    if val is None:
        return {'w:type': 'nil', 'w:w': '0'}
    if val == 'auto':
        return {'w:type': 'auto', 'w:w': '0'}
    if isinstance(val, float) and val <= 1.0:
        return {'w:type': 'pct', 'w:w': '%f%%' % (val * 100)}
    return {'w:type': 'dxa', 'w:w': str(int(val))}

# Footnotes

def make_footnote_reference(footnote_id, style_id):
    """Make a run referring to a footnote by id."""
    run_tree = [
        ['w:r'],
        [['w:footnoteReference', {'w:id': str(footnote_id)}]],
    ]
    if style_id is not None:
        run_tree.insert(1, [['w:rPr'], [['w:rStyle', {'w:val': style_id}]]])
    return make_element_tree(run_tree)

def make_footnote_ref(style_id):
    """Make a run holding the footnote's own number, for use inside it."""
    run_tree = [
        ['w:r'],
        [['w:footnoteRef']],
    ]
    if style_id is not None:
        run_tree.insert(1, [['w:rPr'], [['w:rStyle', {'w:val': style_id}]]])
    return make_element_tree(run_tree)


# Annotations

def make_bookmark_start(bookmark_id, name):
    """Make the start element of a named bookmark."""
    return make_element_tree([
        ['w:bookmarkStart', {'w:id': str(bookmark_id), 'w:name': name}]
    ])

def make_bookmark_end(bookmark_id):
    """Make the end element of a bookmark."""
    return make_element_tree([['w:bookmarkEnd', {'w:id': str(bookmark_id)}]])


# Hyperlinks

def make_hyperlink(relationship_id, anchor):
    """Make an empty hyperlink to a relationship, an anchor, or both."""
    attrs = {}
    if relationship_id is not None:
        attrs['r:id'] = relationship_id
    if anchor is not None:
        attrs['w:anchor'] = anchor
    hyperlink_tree = [['w:hyperlink', attrs]]
    return make_element_tree(hyperlink_tree)

# Structured Document Tags

def _make_toc_hyperlink(text, anchor):
    """Make one table of contents entry: its text, a tab, and a page reference."""
    return [
        ['w:hyperlink', {'w:anchor': anchor, 'w:history': '1'}],
        [['w:r'], [['w:t', text]]],
        [['w:r'], [['w:rPr'], [['w:webHidden']]], [['w:tab']]],
        [['w:r'], [['w:fldChar', {'w:fldCharType': 'begin'}]]],
        [['w:r'], [['w:rPr'], [['w:webHidden']]],
         [['w:instrText', r' PAGEREF %s \h ' % anchor, {'xml:space': 'preserve'}]]
        ],
        [['w:r'], [['w:fldChar', {'w:fldCharType': 'separate'}]]],
        [['w:r'], [['w:rPr'], [['w:webHidden']]], [['w:t', 'X']]],
        [['w:r'], [['w:fldChar', {'w:fldCharType': 'end'}]]],
    ]

def make_table_of_contents(
        toc_title, style_id, maxlevel, bookmark, paragraph_width, outlines):
    '''
       Create the Table of Content
    '''
    sdt_content_tree = [['w:sdtContent']]
    if toc_title is not None:
        title_tree = [
            ['w:p'],
            [['w:r'], [['w:t', toc_title]]]
        ]
        if style_id is not None:
            title_tree.insert(
                1, [['w:pPr'], [['w:pStyle', {'w:val': style_id}]]])
        sdt_content_tree.append(title_tree)
    if maxlevel is not None:
        instr = r' TOC \o "1-%d" \b "%s" \h \z \u ' % (maxlevel, bookmark)
    else:
        instr = r' TOC \o \b "%s" \h \z \u ' % bookmark
    tab_pos = str(paragraph_width - 10)
    tabs_tree = [
        ['w:tabs'],
        [['w:tab', {'w:val': 'right', 'w:leader': 'dot', 'w:pos': tab_pos}]]
    ]
    run_prop_tree = [['w:rPr'], [['w:b', {'w:val': '0'}]], [['w:noProof']]]
    if outlines:
        prop_tree = [['w:pPr'], tabs_tree, run_prop_tree]
        toc_style_id = outlines[0][1]
        if toc_style_id is not None:
            prop_tree.insert(1, [['w:pStyle', {'w:val': toc_style_id}]])
        sdt_content_tree.append([
            ['w:p'],
            prop_tree,
            [['w:r'], [['w:fldChar', {'w:fldCharType': 'begin'}]]],
            [['w:r'], [['w:instrText', instr, {'xml:space': 'preserve'}]]],
            [['w:r'], [['w:fldChar', {'w:fldCharType': 'separate'}]]],
            _make_toc_hyperlink(outlines[0][0], outlines[0][2]),
        ])
        for text, toc_style_id, anchor in outlines[1:]:
            prop_tree = [['w:pPr'], tabs_tree, run_prop_tree]
            if toc_style_id is not None:
                prop_tree.insert(1, [['w:pStyle', {'w:val': toc_style_id}]])
            sdt_content_tree.append(
                [['w:p'], prop_tree, _make_toc_hyperlink(text, anchor)])
        sdt_content_tree.append([
            ['w:p'],
            [['w:r'], [['w:fldChar', {'w:fldCharType': 'end'}]]]
        ])
    else:
        sdt_content_tree.append([
            ['w:p'],
            [['w:pPr'],
             tabs_tree,
             run_prop_tree,
            ],
            [['w:r'], [['w:fldChar', {'w:fldCharType': 'begin'}]]],
            [['w:r'], [['w:instrText', instr, {'xml:space': 'preserve'}]]],
            [['w:r'], [['w:fldChar', {'w:fldCharType': 'end'}]]],
        ])

    toc_tree = [
        ['w:sdt'],
        [['w:sdtPr'],
         [['w:docPartObj'],
          [['w:docPartGallery', {'w:val': 'Table of Contents'}]],
          [['w:docPartUnique']]
         ]
        ],
        sdt_content_tree
    ]
    return make_element_tree(toc_tree)

def make_vml_textbox(style, color, contents, wrap_style=None):
    """Make a run holding a VML rectangle with the given contents.

    The rectangle grows to fit its text; ``wrap_style`` sets how the
    surrounding text flows around it.
    """
    rect_tree = [
        ['v:rect', {'style': style, 'fillcolor': color}],
        [['v:textbox', {'style': 'mso-fit-shape-to-text:true'}],
         [['w:txbxContent']]
        ]
    ]
    if wrap_style is not None:
        rect_tree.append([['w10:wrap', wrap_style]])
    txbx = make_element_tree([['w:r'], [['w:pict'], rect_tree]])
    txbx[0][0][0][0].extend(contents)
    return txbx

def get_left(ind):
    """Return the left indent of a ``w:ind`` element, ``w:start`` included."""
    left = ind.get(norm_name('w:left'), None)
    if left is not None:
        return left
    return ind.get(norm_name('w:start'), '0')

def create_rels_path(path):
    """Return the path of the relationship file belonging to ``path``."""
    return posixpath.join(
        posixpath.dirname(path), '_rels',
        posixpath.basename(path) + '.rels')

def get_relation_target(relationships, rel_type):
    """Return the target of the first relationship of ``rel_type``."""
    return get_attribute(
        relationships, 'pr:Relationship[@Type="%s"]' % rel_type, 'Target')

def get_relation_ids(relationships):
    """Return the numbers of the ``rIdN`` identifiers already in use."""
    if relationships is None:
        return []
    rids = []
    id_attr = norm_name('Id')
    for rel in get_elements(relationships, 'pr:Relationship'):
        match = re.match(r'rId(\d+)', rel.get(id_attr))
        if match is not None:
            rids.append(int(match.group(1)))
    return rids

def make_relationships(relationships):
    '''Generate a relationships
    '''
    rel_tree = [['Relationships']]
    for attributes in relationships:
        rel_tree.append([['Relationship', attributes]])
    return make_element_tree(rel_tree, NSPREFIXES['pr'])


class StyleInfo(object):
    """A style of the style file, and the properties it defines."""
    style_id_attr = norm_name('w:styleId')
    type_attr = norm_name('w:type')

    def __init__(self, style):
        """Wrap a ``w:style`` element.

        A style hidden until used has its ``w:semiHidden`` elements remembered,
        so `used` can reveal it.
        """
        self._style = style
        if get_elements(style, 'w:unhideWhenUsed'):
            self._semihidden_elems = get_elements(style, 'w:semiHidden')
        else:
            self._semihidden_elems = []

    @property
    def style_id(self):
        """The identifier of the style."""
        return self._style.attrib[type(self).style_id_attr]

    @property
    def style_type(self):
        """The kind of the style: paragraph, character, table or numbering."""
        return self._style.attrib[type(self).type_attr]

    def get_based_style_id(self):
        """Return the id of the style this one is based on, or None."""
        based_on_elems = get_elements(self._style, 'w:basedOn')
        if not based_on_elems:
            return None
        return based_on_elems[-1].attrib[norm_name('w:val')]

    def get_border_info(self, kind):
        """Return the attributes of one paragraph border side, or None."""
        border_elems = get_elements(self._style, 'w:pPr/w:pBdr/w:' + kind)
        if not border_elems:
            return None
        return border_elems[-1].attrib

    def get_run_style_property(self):
        """Return the run properties of the style as ``(tag, attributes)`` pairs."""
        props = get_elements(self._style, 'w:rPr')
        if not props:
            return {}
        return [(prop.tag, prop.attrib)
                for prop in props[0] if not prop.tag.endswith('rPrChange')]

    def get_table_horizon_margin(self):
        """Return the ``(left, right)`` cell margins of a table style in twips.

        Either is None when the style does not set it in absolute units.
        """
        cell_margin_elems = get_elements(self._style, 'w:tblPr/w:tblCellMar')
        if not cell_margin_elems:
            return (None, None)

        cell_margin = cell_margin_elems[-1]
        type_attr = norm_name('w:type')
        w_attr = norm_name('w:w')
        def get_margin(elem):
            """Return a margin in twips, or None when it is not in absolute units."""
            if elem is None or elem.get(type_attr) != 'dxa':
                return None
            return int(elem.get(w_attr))
        left = cell_margin.find('w:left', NSPREFIXES)
        right = cell_margin.find('w:right', NSPREFIXES)
        return (get_margin(left), get_margin(right))

    def used(self):
        """Mark the style as used, so Word stops hiding it from the style list."""
        for semihidden in self._semihidden_elems:
            self._style.remove(semihidden)
        self._semihidden_elems = []


class DocxDocument: # pylint: disable=too-many-public-methods
    """A docx file opened for reading, usually the style file."""
    def __init__(self, docxfile):
        '''
          Constructor
        '''
        self.docx = zipfile.ZipFile(docxfile)
        docpath = get_relation_target(
            self.get_xmltree('_rels/.rels'), REL_TYPE_DOC)
        if docpath.startswith('/'):
            docpath = docpath[1:]

        self.docpath = docpath
        self.document = self.get_xmltree(docpath)
        self.relationships = self.get_xmltree(create_rels_path(docpath))
        self.footnotes = self._get_rel_target_xml(REL_TYPE_FOOTNOTES)
        self.numbering = self._get_rel_target_xml(REL_TYPE_NUMBERING)
        self.styles = self._get_rel_target_xml(REL_TYPE_STYLES)

    def _get_rel_target_path(self, rel_type):
        """Return the path of the document part with ``rel_type``, or None."""
        target = get_relation_target(self.relationships, rel_type)
        if target is None:
            return None
        return posixpath.normpath(
            posixpath.join(posixpath.dirname(self.docpath), target))

    def _get_rel_target_xml(self, rel_type):
        """Return the parsed document part with ``rel_type``, or None."""
        target_path = self._get_rel_target_path(rel_type)
        if target_path is None:
            return None
        return self.get_xmltree(target_path)

    def _get_elements_until_target(self, target_elem_xpath):
        """Return copies of the body elements up to the first match of an xpath."""
        body = get_elements(self.document, '/w:document/w:body')
        if not body:
            return []
        target_elems = get_elements(body[0], target_elem_xpath)
        if not target_elems:
            return []
        elements = []
        for elem in body[0]:
            elements.append(copy.deepcopy(elem))
            if elem is target_elems[0]:
                break
        return elements

    def get_custom_xml_path(self, itemid):
        """Return the path of the custom XML part with ``itemid``, or None."""
        rels = get_elements(
            self.relationships,
            'pr:Relationship[@Type="%s"]' % REL_TYPE_CUSTOM_XML)
        docpath_dir = posixpath.dirname(self.docpath)
        for rel in rels:
            target = rel.attrib['Target']
            custom_xml_path = posixpath.normpath(
                posixpath.join(docpath_dir, target))
            custom_xml_rel = self.get_xmltree(create_rels_path(custom_xml_path))
            if custom_xml_rel is None:
                continue
            props_target = get_relation_target(
                custom_xml_rel, REL_TYPE_CUSTOM_XML_PROPS)
            if props_target is None:
                continue
            props = self.get_xmltree(posixpath.normpath(posixpath.join(
                posixpath.dirname(custom_xml_path), props_target)))
            if props is None:
                continue
            if get_attribute(props, '/ds:datastoreItem', 'ds:itemID') == itemid:
                return custom_xml_path
        return None

    @property
    def settings(self):
        """The parsed settings part, or None."""
        return self._get_rel_target_xml(REL_TYPE_SETTINGS)

    @property
    def footnotes_relationships(self):
        """The parsed relationships of the footnotes part."""
        return self.get_xmltree(
            create_rels_path(self._get_rel_target_path(REL_TYPE_FOOTNOTES)))

    @property
    def numbering_relationships(self):
        """The parsed relationships of the numbering part."""
        return self.get_xmltree(
            create_rels_path(self._get_rel_target_path(REL_TYPE_NUMBERING)))

    def get_xmltree(self, fname):
        '''
          Extract a document tree from the docx file
        '''
        try:
            return etree.fromstring(self.docx.read(fname))
        except KeyError:
            return None

    def extract_style_info(self):
        '''
          Extract all style name/id/type from the docx file
        '''
        val_attr = norm_name('w:val')
        def get_info(style):
            """Return the ``(name, info)`` pair of a style, named by its id if unnamed."""
            info = StyleInfo(style)
            names = get_elements(style, 'w:name')
            style_name = names[0].attrib[val_attr] if names else info.style_id
            return (style_name, info)
        return dict(get_info(s) for s in get_elements(self.styles, 'w:style'))

    def get_default_style_name(self, style_type):
        '''
          Extract the last default style's id with style_type
        '''
        xpath = 'w:style[@w:type="%s" and (@w:default="1" or @w:default="true")]'
        styles = get_elements(self.styles, xpath % style_type)
        if not styles:
            return None
        name = get_attribute(styles[-1], 'w:name', 'w:val')
        if name is not None:
            return name
        return styles[-1].attrib[norm_name('w:styleId')]

    def get_section_properties(self):
        """Return every ``w:sectPr`` element of the document."""
        return get_elements(self.document, '//w:sectPr')

    def get_coverpage(self):
        """Return a copy of the cover page content control, or None."""
        coverpages = get_elements(
            self.document,
            '//w:sdt[w:sdtPr/w:docPartObj/w:docPartGallery[@w:val="Cover Pages"]]')
        return copy.deepcopy(coverpages[0]) if coverpages else None

    def get_first_section_elements(self, n=1):
        """Return the body elements up to the end of the nth section.

        Used to lift the cover pages out of the style file.
        """
        return self._get_elements_until_target('./*[.//w:pPr/w:sectPr][%d]' % n)

    def get_first_page_elements(self, n=1):
        """Return the body elements up to the nth explicit page break."""
        return self._get_elements_until_target('./*[.//w:br[@w:type="page"]][%d]' % n)

    def get_image_numbers(self):
        """Return the numbers of the ``word/media/imageN`` files in use."""
        img_nums = []
        for path in self.docx.namelist():
            match = re.match(r'word/media/image(\d+)\.\w+', path)
            if match is not None:
                img_nums.append(int(match.group(1)))
        return img_nums

    def get_custom_xml_numbers(self):
        """Return the numbers of the ``customXml/itemN`` files in use."""
        nums = []
        for path in self.docx.namelist():
            match = re.match(r'customXml/item(?:Props)?(\d+)\.xml', path)
            if match is not None:
                nums.append(int(match.group(1)))
        return nums

    def collect_items(self, zip_docxfile, collected_files):
        """Copy the named files from this document into ``zip_docxfile``."""
        # Add & compress support files
        for fname in collected_files:
            zip_docxfile.writestr(fname, self.docx.read(fname))

    def collect_relation_files(self, rel_files, rel_attrs, basedir):
        """Add to ``rel_files`` the internal targets of ``rel_attrs``.

        Targets are resolved against ``basedir``, and their own relationship
        files are followed as well.
        """
        for attr in rel_attrs:
            if attr.get('TargetMode', 'Internal') == 'External':
                continue
            filepath = posixpath.normpath(
                posixpath.join(basedir, attr['Target']))
            if filepath.startswith('/'):
                filepath = filepath[1:]
            rel_files.add(filepath)
            rel_filepath = create_rels_path(filepath)
            rel_xml = self.get_xmltree(rel_filepath)
            if rel_xml is not None:
                rel_files.add(rel_filepath)
                self.collect_relation_files(
                    rel_files,
                    (r.attrib for r in get_elements(
                        rel_xml, '/pr:Relationships/pr:Relationship')),
                    posixpath.dirname(filepath))

    def collect_all_relation_files(self, rel_attrs):
        """Return every internal file reachable from ``rel_attrs``."""
        rel_files = set()
        self.collect_relation_files(
            rel_files, rel_attrs, posixpath.dirname(self.docpath))
        return rel_files

    def collect_num_ids(self, rel_attrs):
        """Collect num id used in files referenced by rel_attrs
        """
        num_ids = set()
        val_attr = norm_name('w:val')
        for attr in rel_attrs:
            if attr.get('TargetMode', 'Internal') == 'External':
                continue
            filepath = posixpath.normpath(
                posixpath.join(self.docpath, attr['Target']))
            if filepath.startswith('/'):
                filepath = filepath[1:]
            xml = self.get_xmltree(filepath)
            if xml is None:
                continue
            num_id_elems = get_elements(xml, '//w:numId')
            num_ids.update(
                [int(num_id.get(val_attr)) for num_id in num_id_elems])
        return num_ids

############
# Numbering
    def get_numbering_style_id(self, style):
        """Return the ``w:numId`` a named paragraph style refers to, or None."""
        style_elems = get_elements(self.styles, '/w:styles/w:style')
        for style_elem in style_elems:
            name_elem = get_elements(style_elem, 'w:name')[0]
            name = name_elem.attrib[norm_name('w:val')]
            if name == style:
                all_num_ids = get_elements(style_elem, 'w:pPr/w:numPr/w:numId')
                if not all_num_ids:
                    return None
                num_pr = all_num_ids[-1]
                value = num_pr.attrib[norm_name('w:val')]
                return value
        return None

    def get_elems_from_numbering(self, elem_tag):
        """Return elements matching ``elem_tag`` in the numbering part."""
        if self.numbering is None:
            return []
        return get_elements(self.numbering, elem_tag)

    def get_indent(self, style_id):
        """Return the left indent a style defines, or None."""
        ind_elems = get_elements(
            self.styles,
            '/w:styles/w:style[@w:styleId="%s"]/w:pPr/w:ind' % style_id)
        if not ind_elems:
            return None
        return get_left(ind_elems[0])

##########

class IdPool(object):
    """Hands out identifiers, skipping the ones already taken."""
    def __init__(self, used_ids, init_id=1):
        """Start handing out ids at ``init_id``, skipping the used ones."""
        self._used_ids = set(used_ids)
        self._next_id = init_id

    def next_id(self):
        """Return the next unused id."""
        while self._next_id in self._used_ids:
            self._next_id += 1
        next_id = self._next_id
        self._next_id += 1
        return next_id

class IdElements(object):
    """Elements indexed by an integer identifier attribute."""
    def __init__(self, elems, attr, to_int=int, init_id=0):
        """Index ``elems`` by the integer value of ``attr``."""
        self._next_id = init_id
        self._elems = dict((to_int(elem.get(attr)), elem) for elem in elems)
        self._attr = attr
        self._to_int = to_int

    def next_id(self):
        """Return the next id no element holds."""
        while self._next_id in self._elems:
            self._next_id += 1
        next_id = self._next_id
        self._next_id += 1
        return next_id

    def append(self, elem):
        """Add an element under the id its ``attr`` holds."""
        self._elems[self._to_int(elem.get(self._attr))] = elem

    def get(self, key, default=None):
        """Return the element with ``key``, or ``default``."""
        return self._elems.get(self._to_int(key), default)

    def __iter__(self):
        """Iterate over ``(id, element)`` pairs."""
        return iter(self._elems.items())

def collect_used_rel_attrs(relationships, xml, used_rel_types):
    """Return the attributes of the relationships ``xml`` actually needs.

    A relationship is kept when its type is in ``used_rel_types`` or when its
    id appears anywhere in ``xml``.
    """
    if relationships is None:
        return []
    used_rel_attrs = []
    for rel in get_elements(relationships, 'pr:Relationship'):
        if (rel.get('Type') in used_rel_types
                or get_elements(xml, '(.//*[@*="%s"])[1]' % rel.get('Id'))):
            used_rel_attrs.append(rel.attrib)
    return used_rel_attrs

class CoverPagePropertyInfo(object):
    """Where the cover page properties live, or where they will be created."""
    def __init__(self, does_create, info):
        """Record whether the cover page property part has to be created."""
        self.does_create = does_create
        self._path_or_id = info

    @property
    def path(self):
        """The path of the existing part; meaningful when it is not created."""
        return self._path_or_id

    @property
    def id(self):
        """The number of the part to create; meaningful when it is created."""
        return self._path_or_id

def get_cover_page_prop_info(style_docx):
    """Locate the cover page property part of a style file, or reserve a number for one."""
    path = style_docx.get_custom_xml_path(COVER_PAGE_PROPERTY_ITEMID)
    if path is None:
        id_pool = IdPool(style_docx.get_custom_xml_numbers())
        return CoverPagePropertyInfo(True, id_pool.next_id())
    return CoverPagePropertyInfo(False, path)

def collect_referenced_footnotes(footnotes, xml):
    """Return the footnotes a document needs, keyed by id.

    Footnotes nothing refers to are dropped, while the separator footnotes
    Word expects are kept, and created when the style file has none. Returns
    ``(footnote_map, footnote_id_map, footnote_id_pool)``.
    """
    id_attr = norm_name('w:id')
    ref_footnote_ids = set(
        f.get(id_attr) for f in get_elements(xml, '//w:footnoteReference'))
    footnote_map = {}
    footnote_id_map = {}
    footnote_type_set = set()
    type_attr = norm_name('w:type')
    for footnote in footnotes:
        fid = footnote.get(id_attr)
        ftype = footnote.get(type_attr, 'normal')
        if fid in ref_footnote_ids or ftype != 'normal':
            footnote_map[fid] = footnote
            footnote_id_map[int(fid)] = fid
            footnote_type_set.add(ftype)
    footnote_id_pool = IdPool(int(fid) for fid in footnote_map)
    def make_footnote(footnote_id, footnote_type):
        """Make one of the separator footnotes Word expects to find."""
        return make_element_tree([
            ['w:footnote', {
                'w:type': footnote_type, 'w:id': footnote_id,
            }],
            [['w:p'],
             [['w:pPr'], [['w:spacing', {'w:after': '0'}]]],
             [['w:r'], [['w:' + footnote_type]]],
            ],
        ])
    required_footnote_types = ['separate', 'continuationSeparator']
    for ftype in required_footnote_types:
        if ftype not in footnote_type_set:
            fid = str(footnote_id_pool.next_id())
            footnote_map[str(fid)] = make_footnote(fid, ftype)
            footnote_id_map[int(fid)] = fid
    return footnote_map, footnote_id_map, footnote_id_pool

def read_as_data_uri(basedir, href):
    '''Return what href points at as a data URI, or None to leave it alone.

    Only local image files are read. Fragments, data URIs and anything named
    over the network are already self-contained or out of reach, and a target
    that is missing or is not an image is left alone too, so that a stray
    reference can not pull an unrelated file into the document.
    '''
    if not href or href.startswith('#'):
        return None
    parsed = urllib.parse.urlparse(href)
    if parsed.scheme == 'file':
        path = urllib.request.url2pathname(parsed.path)
    elif parsed.scheme:
        return None
    else:
        path = urllib.parse.unquote(parsed.path)
    path = os.path.join(basedir, path)

    mimetype, _ = mimetypes.guess_type(path)
    if mimetype is None or not mimetype.startswith('image/'):
        return None
    try:
        with open(path, 'rb') as target:
            data = target.read()
    except OSError:
        return None
    return 'data:%s;base64,%s' % (
        mimetype, base64.b64encode(data).decode('ascii'))


def inline_svg_references(imagepath):
    '''Return the SVG with its referenced images inlined, or None if it has
    none to inline.

    Both ways of drawing the image read the file away from its directory:
    cairosvg opens no local file unless entity expansion is enabled with it,
    and the copy Word draws is a package part with nothing to resolve a
    relative path against. Turning the references into data URIs makes the
    image self-contained, so it draws the same either way.
    '''
    tree = etree.parse(imagepath)
    basedir = os.path.dirname(imagepath)
    inlined = False
    for elem in tree.getroot().iter():
        for attr in SVG_HREF_ATTRS:
            data_uri = read_as_data_uri(basedir, elem.get(attr))
            if data_uri is not None:
                elem.set(attr, data_uri)
                inlined = True
    if not inlined:
        # Leave the source to be embedded as it is rather than round-tripping
        # it through the parser for nothing.
        return None
    return etree.tostring(tree, xml_declaration=True, encoding='UTF-8')


def rasterize_svg(imagepath, content=None):
    '''Render an SVG to PNG bytes, for clients that can not draw the vector.

    The bitmap is returned rather than written beside the SVG, where it would
    linger in the user's source tree.
    '''
    try:
        from cairosvg import svg2png
    except ImportError:
        raise RuntimeError('SVG images need cairosvg, which is not installed')
    if content is not None:
        return svg2png(bytestring=content, scale=SVG_RASTER_SCALE)
    # Handing cairosvg the path rather than the file's text leaves the
    # encoding to the XML declaration instead of the locale.
    return svg2png(url=imagepath, scale=SVG_RASTER_SCALE)


#
# DocxComposer Class
#


class DocxComposer: # pylint: disable=too-many-public-methods
    """Composes a docx file, taking its styles from a style file."""
    def __init__(self, stylefile, cover_pages):
        '''
           Constructor
        '''
        self._id = 100
        self.style_docx = DocxDocument(stylefile)

        self._style_info = self.style_docx.extract_style_info()
        self._abstract_nums = IdElements(
            self.style_docx.get_elems_from_numbering('w:abstractNum'),
            norm_name('w:abstractNumId'))
        self._nums = IdElements(
            self.style_docx.get_elems_from_numbering('w:num'),
            norm_name('w:numId'), init_id=1)

        # document part -> (relationships, relationship id pool)
        self._relationships_map = {
            'document': ([], IdPool(
                get_relation_ids(self.style_docx.relationships))),
            'footnotes': ([], IdPool(
                get_relation_ids(self.style_docx.footnotes_relationships))),
        }
        self._cover_page_prop_info = get_cover_page_prop_info(self.style_docx)
        self._add_required_relationships(self._cover_page_prop_info)
        self._hyperlink_rid_map = {} # target => relationship id
        # media key => (relationship id map, imagename, srcpath, content).
        # The key is the image path, except for the raster copy of an SVG,
        # which is keyed (imagepath, 'svg') so both parts of one source file
        # can be cached. Media is normally copied straight from srcpath, but
        # a part that has to be generated, as that raster copy is, is held as
        # bytes in content so nothing is written next to the user's sources.
        self._image_info_map = {}
        self._img_num_pool = IdPool(self.style_docx.get_image_numbers())

        self.document = make_element_tree([['w:document'], [['w:body']]])
        self.docbody = get_elements(self.document, '/w:document/w:body')[0]
        if cover_pages:
            self.docbody.extend(self.get_coverpage_elements(cover_pages))

        footnote_info = collect_referenced_footnotes(
            self.style_docx.footnotes, self.docbody)
        self._footnote_map = footnote_info[0] # docname#id => footnote contents
        self._footnote_id_map = footnote_info[1] # footnote_id => docname#id
        self._footnote_id_pool = footnote_info[2]

        self._run_style_property_cache = {}
        self._table_margin_cache = {}

    def get_coverpage_elements(self, n=1):
        """Return the first ``n`` pages of the style file, as cover pages."""
        cover_elems = self.style_docx.get_first_page_elements(n)
        if cover_elems:
            return cover_elems
        return []

    def new_id(self):
        """Return a fresh id for a drawing or another numbered object."""
        self._id += 1
        return self._id

    def get_section_properties(self):
        """Return the section properties of the style file, grouped by orientation.

        Missing page size and margins are filled in with the defaults, and a
        missing orientation is derived by rotating the other one. Returns
        ``(first_orient, {'portrait': [...], 'landscape': [...]})``.
        """
        result = {'portrait': [], 'landscape': []}
        section_props = self.style_docx.get_section_properties()
        if not section_props:
            section_props = [make_element_tree([['w:sectPr']])]
        first_orient = get_orient(section_props[0])
        for prop in section_props:
            if not get_elements(prop, 'w:pgSz'):
                prop.append(make_default_page_size())
            if not get_elements(prop, 'w:pgMar'):
                prop.append(make_default_page_margin())
            result[get_orient(prop)].append(prop)
        for ori1, ori2 in [('portrait', 'landscape'), ('landscape', 'portrait')]:
            if not result[ori1]:
                result[ori1].append(
                    rotate_orient(copy.deepcopy(result[ori2][0])))
        return first_orient, result

    def get_max_bookmark_id(self):
        """Return the highest bookmark id currently in the document."""
        return get_max_attribute(
            get_elements(self.document, '//w:bookmarkStart'),
            norm_name('w:id'))

    def get_style_info(self, style_name):
        """Return the :class:`StyleInfo` for a style name, matched case-insensitively."""
        style_info = self._style_info.get(style_name, None)
        if style_info is not None:
            return style_info
        style_info = self._style_info.get(style_name.lower(), None)
        if style_info is not None:
            return style_info
        return None

    def get_style_info_from_id(self, style_id):
        """Return the :class:`StyleInfo` with a style id, or None."""
        for style_info in self._style_info.values():
            if style_info.style_id == style_id:
                return style_info
        return None

    def get_style_id(self, style_name, style_type):
        """Return the id of a style of the expected type, or None.

        Looking a style up counts as using it, so Word stops hiding it.
        """
        if style_name is None:
            return None
        style_info = self.get_style_info(style_name)
        if style_info is None:
            return None
        if style_info.style_type != style_type:
            return None
        style_info.used()
        return style_info.style_id

    def get_indent(self, style_name, default):
        """Return the left indent of a paragraph style, or ``default``."""
        style_info = self.get_style_info(style_name)
        if style_info is None or style_info.style_type != 'paragraph':
            return default
        indent = self.style_docx.get_indent(style_info.style_id)
        if indent is None:
            return default
        return int(indent)

    def get_border_info(self, style_id, kind):
        """Return one border side of a paragraph style, inherited from its base style if needed."""
        if style_id is None:
            return None
        style_info = self.get_style_info_from_id(style_id)
        if style_info is None or style_info.style_type != 'paragraph':
            return None
        border_attrs = style_info.get_border_info(kind)
        if border_attrs is not None:
            return make_border_info(border_attrs)
        return self.get_border_info(style_info.get_based_style_id(), kind)

    def get_run_style_property(self, style_id):
        """Return the run properties of a character style, merged with those of its base styles."""
        if style_id is None:
            return {}
        style_prop = self._run_style_property_cache.get(style_id)
        if style_prop is not None:
            return style_prop
        style_info = self.get_style_info_from_id(style_id)
        if style_info is None or style_info.style_type != 'character':
            return self._run_style_property_cache.setdefault(style_id, {})
        based_style_id = style_info.get_based_style_id()
        style_prop = {}
        if based_style_id is not None:
            style_prop.update(self.get_run_style_property(based_style_id))
        style_prop.update(style_info.get_run_style_property())
        return self._run_style_property_cache.setdefault(style_id, style_prop)

    def get_bullet_list_num_id(self, style_name):
        """Return the ``w:numId`` a list style refers to, or None."""
        return self.style_docx.get_numbering_style_id(style_name)

    def get_table_cell_margin(self, style_id):
        """Return the total horizontal space a table style takes from a cell's contents."""
        misc_margin = 8 * 2 * 10 # Miscellaneous margin (e.g. border width)
        left, right = self.get_table_horizon_margin(style_id)
        margin = left + right + misc_margin
        return margin

    def get_table_horizon_margin(self, style_id):
        """Return the ``(left, right)`` cell margins of a table style in twips.

        Margins the style leaves unset are taken from its base style, and
        finally from Word's own defaults.
        """
        default_margin = (115, 115)
        if style_id is None:
            return default_margin
        margin = self._table_margin_cache.get(style_id)
        if margin is not None:
            return margin

        style_info = self.get_style_info_from_id(style_id)
        if style_info is None or style_info.style_type != 'table':
            return self._table_margin_cache.setdefault(style_id, default_margin)
        left, right = style_info.get_table_horizon_margin()
        if left is None or right is None:
            based_left, based_right = self.get_table_horizon_margin(
                style_info.get_based_style_id())
            left = left or based_left
            right = right or based_right
        return self._table_margin_cache.setdefault(style_id, (left, right))

    def bake_property_fields_in_parts(
            self, inherited_files, rel_attrs, prop_map):
        '''Resolve the DOCPROPERTY fields of the header and footer parts
           inherited from the style file.

           Returns a list of (path, xml) for the parts that changed; the caller
           writes those instead of copying the style file's originals.
        '''
        if not prop_map:
            return []
        basedir = posixpath.dirname(self.style_docx.docpath)
        baked = []
        seen = set()
        for attr in rel_attrs:
            if attr.get('Type') not in (REL_TYPE_HEADER, REL_TYPE_FOOTER):
                continue
            if attr.get('TargetMode', 'Internal') == 'External':
                continue
            path = posixpath.normpath(
                posixpath.join(basedir, attr['Target'])).lstrip('/')
            if path not in inherited_files or path in seen:
                continue
            seen.add(path)
            xml = self.style_docx.get_xmltree(path)
            if bake_doc_property_fields(xml, prop_map):
                baked.append((path, xml))
        return baked

    def asbytes(self, set_update_fields, props, bake_property_fields=True):
        '''Generate the composed document as docx binary.
        '''
        prop_map = make_doc_property_map(props) if bake_property_fields else {}
        bake_doc_property_fields(self.document, prop_map)

        xml_files = [
            ('_rels/.rels', self.make_root_rels()),
            ('docProps/app.xml', self.make_app(props['app'])),
            ('docProps/core.xml', self.make_core(props['core'])),
            ('docProps/custom.xml', self.make_custom(props['custom'])),
        ]

        inherited_rel_attrs = self.collect_inherited_rel_attrs()
        footnotes = self.make_footnotes()
        bake_doc_property_fields(footnotes, prop_map)
        numbering = self.make_numbering(inherited_rel_attrs, footnotes)

        document_rels = self.make_document_rels(inherited_rel_attrs)
        xml_files.append(('word/_rels/document.xml.rels', document_rels))
        footnotes_rel_attrs = collect_used_rel_attrs(
            self.style_docx.footnotes_relationships, footnotes, set())
        footnotes_rels = self.make_footnotes_rels(footnotes_rel_attrs)
        if footnotes_rels is not None:
            xml_files.append(('word/_rels/footnotes.xml.rels', footnotes_rels))
        numbering_rel_attrs = collect_used_rel_attrs(
            self.style_docx.numbering_relationships, numbering, set())
        if numbering_rel_attrs:
            numbering_rels = self.make_numbering_rels(numbering_rel_attrs)
            xml_files.append(('word/_rels/numbering.xml.rels', numbering_rels))
        settings = self.make_settings(set_update_fields)

        xml_files.append(('word/document.xml', self.document))
        xml_files.append(('word/footnotes.xml', footnotes))
        xml_files.append(('word/numbering.xml', numbering))
        xml_files.append(('word/styles.xml', self.style_docx.styles))
        xml_files.append(('word/settings.xml', settings))

        inherited_files = self.style_docx.collect_all_relation_files(
            inherited_rel_attrs + footnotes_rel_attrs + numbering_rel_attrs)
        content_types = self.make_content_types(inherited_files)
        xml_files.append(('[Content_Types].xml', content_types))

        # Keep the baked parts in inherited_files: make_content_types has
        # already run, but the content type overrides are keyed off it.
        baked_parts = self.bake_property_fields_in_parts(
            inherited_files, inherited_rel_attrs, prop_map)
        xml_files.extend(baked_parts)

        if self._cover_page_prop_info.does_create:
            xml_files.extend(
                self.make_coverpage_props_items(props['cover_page']))
        else:
            cover_page_props = self.make_cover_page_props(props['cover_page'])
            xml_files.append(
                (self._cover_page_prop_info.path, cover_page_props))
            inherited_files.remove(self._cover_page_prop_info.path)

        bytes_io = io.BytesIO()
        with zipfile.ZipFile(
                bytes_io, mode='w', compression=zipfile.ZIP_DEFLATED) as out:
            self.style_docx.collect_items(
                out,
                inherited_files.difference(path for path, _ in baked_parts))
            for xmlpath, xml in xml_files:
                treestring = etree.tostring(
                    xml, xml_declaration=True,
                    encoding='UTF-8', standalone='yes')
                out.writestr(xmlpath, treestring)
            for _, picname, srcpath, content in self._image_info_map.values():
                if content is None:
                    out.write(srcpath, 'word/media/' + picname)
                else:
                    out.writestr('word/media/' + picname, content)

        return bytes_io.getvalue()


 ##################
########
# Numbering Style

    def get_numbering_left(self, style_name):
        '''
           Get numbering indeces...
        '''
        num_id = self.style_docx.get_numbering_style_id(style_name)
        if num_id is None:
            return []

        num = self._nums.get(num_id)
        if num is None:
            return []

        abst_num_id = get_attribute(num, 'w:abstractNumId', 'w:val')
        abstract_num = self._abstract_nums.get(abst_num_id)
        if abstract_num is None:
            return []

        indent_info = []
        ilvl_attr = norm_name('w:ilvl')
        for lvl in get_elements(abstract_num, 'w:lvl'):
            ind = get_elements(lvl, 'w:pPr/w:ind')
            if ind:
                indent_info.append(
                    (int(lvl.get(ilvl_attr)), int(get_left(ind[-1]))))
        indent_info.sort()

        indents = []
        for lvl, indent in indent_info:
            while len(indents) < lvl:
                indents.append(indents[-1] if indents else 0)
            indents.append(indent)
        return indents

    num_format_map = {
        'bullet': 'bullet',
        'arabic': 'decimal',
        'loweralpha': 'lowerLetter',
        'upperalpha': 'upperLetter',
        'lowerroman': 'lowerRoman',
        'upperroman': 'upperRoman',
    }

    def add_numbering_style(
            self, start_val, lvl_txt, typ, indent, style_id=None, font=None):
        '''
           Create a new numbering definition
        '''
        abstract_num_id = self._abstract_nums.next_id()
        typ = self.__class__.num_format_map.get(typ, 'decimal')
        lvl_tree = [
            ['w:lvl', {'w:ilvl': '0'}],
            [['w:start', {'w:val': str(start_val)}]],
            [['w:lvlText', {'w:val': lvl_txt}]],
            [['w:lvlJc', {'w:val': 'left'}]],
            [['w:numFmt', {'w:val': typ}]],
            [['w:pPr'], [['w:ind', {
                'w:left': str(indent), 'w:hanging': str(int(indent * 0.75))
            }]]],
        ]
        if style_id is not None:
            lvl_tree.append([['w:pStyle', {'w:val': style_id}]])
        if font is not None:
            lvl_tree.append([
                ['w:rPr'], [['w:rFonts', {'w:ascii': font, 'w:hAnsi': font}]]
            ])
        abstnum = make_element_tree([
            ['w:abstractNum', {'w:abstractNumId': str(abstract_num_id)}],
            [['w:multiLevelType', {'w:val': 'singleLevel'}]],
            lvl_tree,
        ])
        self._abstract_nums.append(abstnum)

        num_id = self._nums.next_id()
        num_tree = [
            ['w:num', {'w:numId': str(num_id)}],
            [['w:abstractNumId', {'w:val': str(abstract_num_id)}]],
        ]
        num = make_element_tree(num_tree)
        self._nums.append(num)
        return num_id

    def get_default_style_names(self):
        '''
           Return default paragraph, character, table style ids
        '''
        paragraph_style_id = self.style_docx.get_default_style_name('paragraph')
        character_style_id = self.style_docx.get_default_style_name('character')
        table_style_id = self.style_docx.get_default_style_name('table')
        return paragraph_style_id, character_style_id, table_style_id

    def create_style(
            self, style_type, new_style_name, based_style_name, is_custom,
            is_hidden=False):
        '''
           Create a new style_stype style with new_style_id,
           which is based on based_style_id.
        '''
        return self._create_style(
            style_type, new_style_name, is_custom, is_hidden,
            based_style_name=based_style_name)

    def create_list_style(
            self, new_style_name, format_type, lvl_text, font, indent):
        """Create a paragraph style that numbers or bullets its paragraphs.

        The style gets a numbering definition of its own; see
        `add_numbering_style` for ``format_type``, ``lvl_text`` and ``font``.
        """
        def make_property_tree(new_style_id):
            """Return the paragraph properties tying the style to a new numbering definition."""
            num_id = self.add_numbering_style(
                1, lvl_text, format_type, indent, new_style_id, font)
            return [
                ['w:pPr'],
                [['w:numPr'],
                 [['w:ilvl', {'w:val': '0'}]],
                 [['w:numId', {'w:val': str(num_id)}]],
                ],
            ]
        is_custom = False
        is_hidden = False
        return self._create_style(
            'paragraph', new_style_name, is_custom, is_hidden,
            make_property_tree=make_property_tree)

    def create_empty_paragraph_style(
            self, new_style_name, after_space, with_border, is_hidden):
        '''
           Create a new empty paragraph style
        '''
        def make_property_tree(_):
            """Return the paragraph properties: the spacing, a small font, and the rule."""
            property_tree = [
                ['w:pPr'],
                [['w:spacing', {
                    'w:before': '0', 'w:beforeAutospacing': '0',
                    'w:after': str(after_space), 'w:afterAutospacing': '0',
                }]],
                [['w:rPr'], [['w:sz', {'w:val': '16'}]]],
            ]
            if with_border:
                property_tree.append([
                    ['w:pBdr'],
                    [['w:bottom', {
                        'w:val': 'single', 'w:sz': '8', 'w:space': '1'
                    }]]
                ])
            return property_tree
        is_custom = True
        return self._create_style(
            'paragraph', new_style_name, is_custom, is_hidden,
            make_property_tree=make_property_tree)

    def _create_style(
            self, style_type, new_style_name, is_custom, is_hidden,
            based_style_name=None, make_property_tree=None):
        """Add a style to the style file, unless that name is already taken.

        ``make_property_tree`` is called with the new style id and returns the
        property tree to append. Returns whether the style was created.
        """
        if self.get_style_info(new_style_name) is not None:
            return False
        new_style_id = new_style_name
        style_tree = [
            ['w:style', {
                'w:type': style_type,
                'w:customStye': '1' if is_custom else '0',
                'w:styleId': new_style_id
            }],
            [['w:name', {'w:val': new_style_name}]],
            [['w:semiHidden']],
            [['w:qFormat']],
        ]
        if not is_hidden:
            style_tree.append([['w:unhideWhenUsed']])
        if based_style_name is not None:
            based_info = self.get_style_info(based_style_name)
            if based_info is not None and based_info.style_type == style_type:
                style_tree.append(
                    [['w:basedOn', {'w:val': based_info.style_id}]])
        if make_property_tree is not None:
            style_tree.append(make_property_tree(new_style_id))
        new_style = make_element_tree(style_tree)
        self.style_docx.styles.append(new_style)
        self._style_info[new_style_name] = StyleInfo(new_style)
        return True

    def _add_required_relationships(self, cover_page_prop_info):
        """Add the relationships every document needs, and the cover page part when it is created."""
        relationships, id_pool = self._relationships_map['document']
        required_rel_types = (
            (REL_TYPE_STYLES, 'styles.xml'),
            (REL_TYPE_NUMBERING, 'numbering.xml'),
            (REL_TYPE_FOOTNOTES, 'footnotes.xml'),
            (REL_TYPE_SETTINGS, 'settings.xml'),
        )
        for rel_type, target in required_rel_types:
            relationships.append({
                'Id': 'rId%d' % id_pool.next_id(),
                'Type': rel_type, 'Target': target
            })
        if cover_page_prop_info.does_create:
            relationships.append({
                'Id': 'rId%d' % id_pool.next_id(),
                'Type': REL_TYPE_CUSTOM_XML,
                'Target': '../customXml/item%d.xml' % cover_page_prop_info.id,
            })

    def add_hyperlink_relationship(self, target, part):
        """Return the relationship id of an external target, adding it if new."""
        rid_map = self._hyperlink_rid_map.get(target)
        if rid_map is not None:
            rid = rid_map.get(part, None)
            if rid is not None:
                return rid
        else:
            rid_map = {}

        relationships, id_pool = self._relationships_map[part]
        rid = 'rId%d' % id_pool.next_id()
        relationships.append({
            'Id': rid,
            'Type': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
            'Target': target,
            'TargetMode': 'External'
        })
        rid_map[part] = rid
        self._hyperlink_rid_map[target] = rid_map
        return rid

    def _add_media_relationship(self, key, part, make_media):
        '''Return the relationship id of a media part, adding it if new.

        key identifies the media part in the cache, which is what makes a
        second use of the same image reuse the relationship instead of
        allocating one that points at a media part the next call overwrites.
        make_media is consulted only the first time key is seen, and returns
        the (extension, srcpath, content) of the part to embed.
        '''
        rid_map, picname, srcpath, content = self._image_info_map.get(
            key, (None, None, None, None))
        if rid_map is not None:
            rid = rid_map.get(part, None)
            if rid is not None:
                return rid
        else:
            picext, srcpath, content = make_media()
            rid_map = {}
            picname = 'image%d%s' % (self._img_num_pool.next_id(), picext)

        relationships, id_pool = self._relationships_map[part]
        rid = 'rId%d' % id_pool.next_id()
        relationships.append({
            'Id': rid,
            'Type': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image',
            'Target': 'media/' + picname
        })
        rid_map[part] = rid
        self._image_info_map[key] = (rid_map, picname, srcpath, content)
        return rid

    def add_image_relationship(self, imagepath, part):
        '''Return the (rid, svg_rid) pair identifying an image.

        svg_rid is None for images Word draws directly. An SVG is embedded
        twice instead: as the vector original svg_rid names, and as the
        raster copy rid names for clients that can not draw the vector.
        '''
        imagepath = os.path.abspath(imagepath)
        _, picext = os.path.splitext(imagepath)

        if picext == '.svg':
            svg = None
            read = False

            def load_svg():
                """Return the SVG with its references inlined, reading the source at most once."""
                # Both media parts come out of one read of the source, which
                # neither needs when the cache already holds them.
                nonlocal svg, read
                if not read:
                    svg = inline_svg_references(imagepath)
                    read = True
                return svg

            rid = self._add_media_relationship(
                imagepath, part,
                lambda: ('.png', imagepath, rasterize_svg(
                    imagepath, load_svg())))
            # A second cache key, so both parts of the same source file get
            # their own media name and relationship.
            svg_rid = self._add_media_relationship(
                (imagepath, 'svg'), part,
                lambda: ('.svg', imagepath, load_svg()))
            return (rid, svg_rid)

        if picext == '.jpg':
            picext = '.jpeg'
        rid = self._add_media_relationship(
            imagepath, part, lambda: (picext, imagepath, None))
        return (rid, None)

    def get_footnote_id(self, key):
        """Reserve a footnote id for ``key`` and return it."""
        fid = self._footnote_id_pool.next_id()
        self._footnote_id_map[fid] = key
        return fid

    def append_footnote(self, key, contents):
        """Store the contents of the footnote registered under ``key``."""
        footnote = make_element_tree([['w:footnote']])
        footnote.extend(contents)
        self._footnote_map[key] = footnote

    def collect_inherited_rel_attrs(self):
        """Collect relationships inherited from style file.
        """
        implicit_rel_types = {
            REL_TYPE_COMMENTS,
            REL_TYPE_ENDNOTES,
            REL_TYPE_FONT_TABLE,
            REL_TYPE_GLOSSARY_DOCUMENT,
            REL_TYPE_STYLES_WITH_EFFECTS,
            REL_TYPE_THEME,
            REL_TYPE_WEB_SETTINGS,
            REL_TYPE_CUSTOM_XML,
            REL_TYPE_CUSTOM_XML_PROPS,
            REL_TYPE_THUMBNAIL,
        }
        return collect_used_rel_attrs(
            self.style_docx.relationships, self.document, implicit_rel_types)

    def make_content_types(self, inherited_files):
        '''create [Content_Types].xml
        '''
        filename = '[Content_Types].xml'
        content_types = self.style_docx.get_xmltree(filename)

        types_tree = [['Types']]
        # Add support for filetypes
        filetypes = {
            'rels': 'application/vnd.openxmlformats-package.relationships+xml',
            'xml': 'application/xml',
            'jpeg': 'image/jpeg',
            'jpg': 'image/jpeg',
            'gif': 'image/gif',
            'png': 'image/png',
            'emf': 'image/x-emf',
            'tiff': 'image/tiff',
            'tif': 'image/tiff',
            'bmp': 'image/bmp',
            'ico': 'image/vnd.microsoft.icon',
            'webp': 'image/webp',
            'svg': 'image/svg+xml',
        }
        for ext, ctype in filetypes.items():
            types_tree.append(
                [['Default', {'Extension': ext, 'ContentType': ctype}]])
        for elem in get_elements(content_types, '/ct:Types/ct:Default'):
            ext = elem.attrib['Extension']
            if ext in filetypes:
                continue
            types_tree.append([['Default', {
                'Extension': ext, 'ContentType': elem.attrib['ContentType']
            }]])

        required_content_types = [
            ('/docProps/core.xml', CONTENT_TYPE_CORE_PROPERTIES),
            ('/docProps/app.xml', CONTENT_TYPE_EXTENDED_PROPERTIES),
            ('/docProps/custom.xml', CONTENT_TYPE_CUSTOM_PROPERTIES),
            ('/word/document.xml', CONTENT_TYPE_DOC_MAIN),
            ('/word/styles.xml', CONTENT_TYPE_STYLES),
            ('/word/numbering.xml', CONTENT_TYPE_NUMBERING),
            ('/word/footnotes.xml', CONTENT_TYPE_FOOTNOTES),
            ('/word/settings.xml', CONTENT_TYPE_SETTINGS),
        ]
        for name, ctype in required_content_types:
            types_tree.append([['Override', {
                'PartName': name, 'ContentType': ctype,
            }]])
        if self._cover_page_prop_info.does_create:
            name = '/customXml/itemProps%d.xml' % self._cover_page_prop_info.id
            types_tree.append([['Override', {
                'PartName': name,
                'ContentType': CONTENT_TYPE_CUSTOM_XML_DATA_STORAGE_PROPERTIES,
            }]])
        for elem in get_elements(content_types, '/ct:Types/ct:Override'):
            name = elem.attrib['PartName']
            if name[1:] not in inherited_files:
                continue
            types_tree.append([['Override', {
                'PartName': name, 'ContentType': elem.attrib['ContentType']
            }]])

        return make_element_tree(types_tree, NSPREFIXES['ct'])

    def make_core(self, props): # pylint: disable=no-self-use
        '''Create core properties (common document properties referred to in
           the 'Dublin Core' specification).
        '''
        coreprops_tree = [['cp:coreProperties']]
        for ns, prop, attr in CORE_PROPERTY_KEYS:
            value = props.get(prop, None)
            if value is None:
                continue
            value = xml_encode(value)
            coreprops_tree.append([['%s:%s' % (ns, prop), attr, value]])

        return make_element_tree(coreprops_tree)

    def make_app(self, props): # pylint: disable=no-self-use
        """Create app-specific properties."""
        appprops_tree = [
            ['Properties'],
            [['DocSecurity', '0']],
            [['ScaleCrop', 'false']],
            [['LinksUpToDate', 'false']],
            [['SharedDoc', 'false']],
            [['HyperlinksChanged', 'false']],
        ]
        appprops_tree.extend(
            ([[key, xml_encode(value)]] for key, value in props.items()))
        return make_element_tree(appprops_tree, NSPREFIXES['ep'])

    def make_custom(self, custom_props): # pylint: disable=no-self-use
        """Create ``docProps/custom.xml`` from the user defined properties."""
        props_tree = [['Properties']]
        # User defined pid must start from 2
        for pid, (name, value) in enumerate(custom_props.items(), 2):
            for type_, type_elem, to_str in CUSTOM_PROPERTY_TYPES:
                if not isinstance(value, type_):
                    continue
                # Fmtid of user defined properties
                fmtid = '{D5CDD505-2E9C-101B-9397-08002B2CF9AE}'
                attr = {'pid': str(pid), 'fmtid': fmtid, 'name': name}
                props_tree.append(
                    [['property', attr], [[type_elem, to_str(value)]]])
                break
        xmlns = 'http://purl.oclc.org/ooxml/officeDocument/customProperties'
        return make_element_tree(props_tree, xmlns)

    def make_coverpage_props_items(self, props):
        """Create the cover page property part, its data storage properties and its relationships."""
        item_path = 'customXml/item%d.xml' % self._cover_page_prop_info.id
        item = self.make_cover_page_props(props)
        prop_path = 'customXml/itemProps%d.xml' % self._cover_page_prop_info.id
        prop = self.make_cover_page_data_storage_props()
        rels_path = create_rels_path(item_path)
        rels = self.make_item_rels(posixpath.basename(prop_path))
        return ((item_path, item), (prop_path, prop), (rels_path, rels))

    def make_cover_page_props(self, props): # pylint: disable=no-self-use
        """Create the cover page property part from the cover page properties."""
        props_tree = [['CoverPageProperties']]
        props_tree.extend(([[key, value]] for key, value in props.items()))
        xmlns = 'http://schemas.microsoft.com/office/2006/coverPageProps'
        return make_element_tree(props_tree, xmlns)

    def make_cover_page_data_storage_props(self): # pylint: disable=no-self-use
        """Create the data storage properties identifying the cover page property part."""
        uri = 'http://schemas.microsoft.com/office/2006/coverPageProps'
        props_tree = [
            ['ds:datastoreItem', {'ds:itemID': COVER_PAGE_PROPERTY_ITEMID}],
            [['ds:schemaRefs'],
             [['ds:schemaRef', {'ds:uri': uri}]],
            ],
        ]
        return make_element_tree(props_tree)

    def make_item_rels(self, target): # pylint: disable=no-self-use
        """Create the relationships of a custom XML item, pointing at its properties."""
        return make_relationships([{
            'Id': 'rId1',
            'Type': REL_TYPE_CUSTOM_XML_PROPS,
            'Target': target,
        }])

    def make_document_rels(self, stylerels):
        """Create ``word/_rels/document.xml.rels`` from the new and inherited relationships."""
        rel_list = []
        docrel_list, _ = self._relationships_map['document']
        rel_list.extend(docrel_list)
        rel_list.extend(stylerels)
        return make_relationships(rel_list)

    def make_footnotes_rels(self, footnotes_rel_attrs):
        """Create the footnote relationships, or None when there are none."""
        rel_list = []
        footnotes_rel_list, _ = self._relationships_map['footnotes']
        rel_list.extend(footnotes_rel_list)
        rel_list.extend(footnotes_rel_attrs)
        if not rel_list:
            return None
        return make_relationships(rel_list)

    def make_numbering_rels(self, numbering_rel_attrs): # pylint: disable=no-self-use
        """Create the numbering relationships inherited from the style file."""
        return make_relationships(numbering_rel_attrs)

    def make_root_rels(self): # pylint: disable=no-self-use
        """Create ``_rels/.rels``, pointing at the document and its properties."""
        rel_list = [
            (REL_TYPE_CORE, 'docProps/core.xml'),
            (REL_TYPE_APP, 'docProps/app.xml'),
            (REL_TYPE_CUSTOM, 'docProps/custom.xml'),
            (REL_TYPE_DOC, 'word/document.xml'),
        ]
        return make_relationships(
            {'Id': 'rId%d' % rid, 'Type': rtype, 'Target': target}
            for rid, (rtype, target) in enumerate(rel_list, 1))

    def make_footnotes(self):
        """Create ``word/footnotes.xml``, numbering the collected footnotes."""
        footnotes = make_element_tree([['w:footnotes']])
        id_attr = norm_name('w:id')
        for fid, key in self._footnote_id_map.items():
            footnote = copy.deepcopy(self._footnote_map[key])
            footnote.set(id_attr, str(fid))
            footnotes.append(footnote)
        return footnotes

    def make_numbering(self, inherited_rel_attrs, footnotes):
        """Create numbering.xml from nums and abstract nums in use
        """
        used_num_ids = self.style_docx.collect_num_ids(inherited_rel_attrs)
        val_attr = norm_name('w:val')
        def update_used_num_ids(xml):
            """Add every ``w:numId`` referenced in ``xml`` to the used set."""
            elems = get_elements(xml, '//w:numId')
            used_num_ids.update((int(num_id.get(val_attr)) for num_id in elems))
        update_used_num_ids(self.style_docx.styles)
        update_used_num_ids(self.document)
        update_used_num_ids(footnotes)

        nums = [num for num_id, num in self._nums if num_id in used_num_ids]
        get_abst_num_id = lambda num: int(
            get_elements(num, 'w:abstractNumId')[-1].get(val_attr))
        abst_num_ids = set((get_abst_num_id(num) for num in nums))
        abstract_nums = [
            abst_num for abst_num_id, abst_num in self._abstract_nums
            if abst_num_id in abst_num_ids
        ]

        numbering = make_element_tree(['w:numbering'])
        numbering.extend(abstract_nums)
        numbering.extend(nums)

        for elem in get_elements(numbering, '//w:lvlPicBulletId'):
            bullet_id = elem.get(val_attr)
            num_pic_bullet_elems = get_elements(
                self.style_docx.numbering,
                'w:numPicBullet[@w:numPicBulletId="%s"]' % bullet_id)
            if num_pic_bullet_elems:
                numbering.insert(0, num_pic_bullet_elems[-1])
        return numbering

    def make_settings(self, set_update_fields):
        """Create settings.xml.

        If set_update_fields is true, w:updateFields is set
        """
        settings = self.style_docx.settings
        if settings is None:
            settings = make_element_tree([['w:settings']])
        if set_update_fields:
            update_fields = get_elements(settings, 'w:updateFields')
            if update_fields:
                update_fields[-1].attrib[norm_name('w:val')] = 'true'
            else:
                settings.append(make_element_tree([['w:updateFields']]))
        return settings
