# -*- coding: utf-8 -*-
import os
from distutils.command import build
from setuptools import setup

class CustomBuild(build.build, object):
    def run(self):
        from create_style_file import create_style_file
        create_style_file()
        return super(CustomBuild, self).run()

BASEDIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASEDIR, 'README.rst'), 'r') as f:
    LONG_DESCRIPTION = f.read()

setup(
    name='sphinx_docxbuilder-jm',
    version='1.3.0',
    description='Sphinx docx builder extension',
    long_description=LONG_DESCRIPTION,
    url='https://github.com/Nefti-sama/sphinx_docxbuilder-jm',
    author='Nefti-sama',
    author_email='no-mail-yet@gmail.com',
    keywords=['sphinx', 'extension', 'docx', 'OpenXML'],
    packages=[
        'docxbuilder',
        'docxbuilder.docx',
    ],
    install_requires=[
        "Sphinx>=1.7.6",
        "lxml",
        "pillow",
        "six",
    ],
    extras_require={
        'math': ['latex2mathml', 'mathml2omml'],
    },
    python_requires='>=3.5,!=3.0.*,!=3.1.*,!=3.2.*,!=3.3.*,!=3.4.*',
    package_data={
        'docxbuilder.docx': ['style.docx'],
    },
    classifiers=[
        'Framework :: Sphinx :: Extension',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Topic :: Documentation :: Sphinx',
    ],
    cmdclass={
        'build': CustomBuild,
    }
)
