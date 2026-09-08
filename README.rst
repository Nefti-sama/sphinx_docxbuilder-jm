######################
sphinx_docxbuilder-jm
######################

.. note::

   This extension butchered the `original docxbuilder <https://github.com/amedama41/docxbuilder>`_ to add some functionality.

Added:

* svg-support (basic implementation, svg is converted to png)
* Multiple cover pages (`<https://github.com/amedama41/docxbuilder/pull/12>`_)

Docxbuilder is a Sphinx extension to build docx formatted documents.


************
Requirements
************

:Python: 3.5 or later (could work with 2.7, but it's 2022)-
:Sphinx: 1.7.6 or later

*******
Install
*******

Add to your requirements.txt:
``-e git://github.com/Nefti-sama/sphinx_docxbuilder-jm.git#egg=sphinx_docxbuilder-jm``

***********
Development
***********

The default style file ``docxbuilder/docx/style.docx`` is generated, not
committed: ``create_style_file.py`` zips ``style_file/docx/`` into it, and
``.gitignore`` excludes the result. ``setup.py`` hooks that into the ``build``
command, but a PEP 660 editable install (``pip install -e .`` with any recent
pip) does not run the legacy ``build`` cmdclass, so the file is never created.

After cloning the repository, and after every ``git clean -xdf`` or ``make
clean``, regenerate it::

   python create_style_file.py

Without it, any build which does not set ``docx_style`` in **conf.py** aborts
with::

   FileNotFoundError: [Errno 2] No such file or directory: '.../docxbuilder/docx/style.docx'

Edit the default style under ``style_file/docx/``, not ``style.docx`` itself;
the latter is overwritten on the next regeneration. ``make update_style_file``
does the full round trip, rebuilding ``style_file/docx/`` from the Sphinx
project in ``style_file/`` first; note that it calls
``./create_style_file.py``, which needs the executable bit and a ``python``
on PATH.

*****
Usage
*****

Add 'sphinx_docxbuilder-jm' to ``extensions`` configuration of **conf.py**:

.. code:: python

   extensions = ['docxbuilder']

and build your documents::

   make docx

You can control the generated document by adding configurations into ``conf.py``:

.. code:: python

   docx_documents = [
       ('index', 'docxbuilder.docx', {
            'title': project,
            'creator': author,
            'subject': 'A manual of docxbuilder',
        }, True),
   ]
   docx_style = 'path/to/custom_style.docx'
   docx_pagebreak_before_section = 1

For more details, see `the (original) documentation <https://docxbuilder.readthedocs.io/en/latest/>`_.

Style file
==========

Generated docx file's design is customized by a style file
(The default style is ``docxbuilder/docx/style.docx``).
The style file is a docx file, which defines some paragraph,
character, and table styles.

The below lists shows typical styles.

Character styles:

* Emphasis
* Strong
* Literal
* Hyperlink
* Footnote Reference

Paragraph styles:

* Body Text
* Footnote Text
* Definition Term
* Literal Block
* Image Caption, Table Caution, Literal Caption
* Heading 1, Heading 2, ..., Heading *N*
* TOC Heading
* toc 1, toc 2, ..., toc *N*
* List Bullet
* List Number

Table styles:

* Table
* Field List
* Admonition Note

****
TODO
****

- Support math role and directive.
- Support tabular_col_spec directive.
- Support URL path for images.
- Cleanup generated png-files when using svg after the make-process
- Refine generated tables

*******
Licence
*******

MIT Licence

