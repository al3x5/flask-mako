# -*- encoding: utf-8 -*-
"""
    flask.ext.mako
    ~~~~~~~~~~~~~~~~~~~~~~~

    Extension implementing Mako Templates support in Flask with support for
    flask-babel

    :copyright: (c) 2012 by Béranger Enselme <benselme@gmail.com>
    :copyright: (c) 2024 by Nicolas Desprès <nicolas.despres@gmail.com>
    :license: BSD, see LICENSE for more details.
"""


import os, sys

from markupsafe import escape

from flask.signals import template_rendered
from flask import current_app
from flask import g as GLOBAL

from mako.lookup import TemplateLookup
from mako.template import Template
from mako import exceptions
from mako.exceptions import RichTraceback


itervalues = getattr(dict, 'itervalues', dict.values)

_BABEL_IMPORTS =  'from flask.ext.babel import gettext as _, ngettext, ' \
                  'pgettext, npgettext'
_FLASK_IMPORTS =  'from flask.helpers import url_for, get_flashed_messages'


class MakoTemplateError(RuntimeError):
    """A template has thrown an error during rendering."""

    def __init__(self, template):
        msg = "Error occurred while rendering template '{0}'".format(template.uri)
        super(MakoTemplateError, self).__init__(msg)
        self.template = template
        self.rich_traceback = RichTraceback()

_SKELETON_HTML = """\
<!doctype html>
<html lang=en>
  <head>
    <title>%(title)s</title>
    <link rel="stylesheet" href="?__debugger__=yes&amp;cmd=resource&amp;f=style.css">
    <link rel="shortcut icon"
        href="?__debugger__=yes&amp;cmd=resource&amp;f=console.png">
  </head>
  <body style="background-color: #fff">
    <div class="debugger">
      <h1>%(exception_type)s</h1>
      <div class="detail">
        <p class="errormsg">%(exception)s</p>
      </div>
      <p>While rendering Mako's template: %(template_uri)s</p>
      <h2 class="traceback">Traceback <em>(most recent call last)</em></h2>
      <div class="traceback">
        <ul>%(frames)s</ul>
      </div>
    </div>
  </body>
</html>
"""

_FRAME_HTML = """\
<li>
  <div class="frame">
    <h4>File <cite class="filename">"%(filename)s"</cite>,
        line <em class="line">%(lineno)s</em>,
        in <code class="function">%(function_name)s</code></h4>
    <div class="source %(classes)s">
      <pre class="line current">
        %(line)s
      </pre>
    </div>
  </div>
</li>
"""

def is_template_filename(filename, template):
    return any(filename.startswith(d) for d in template.lookup.directories)


def render_mako_error(e: MakoTemplateError) -> str:
    frames = []
    for filename, lineno, function_name, line in e.rich_traceback.traceback:
        classes = []
        if not is_template_filename(filename, e.template):
            classes.append("library")
        frames.append(_FRAME_HTML % dict(
            filename=escape(filename),
            lineno=escape(lineno),
            function_name=escape(function_name),
            line=escape(line),
            classes=" ".join(classes)
        ))

    return _SKELETON_HTML % dict(
        title="Mako template error",
        exception_type=escape(type(e.rich_traceback.error).__name__),
        exception=escape(str(e.rich_traceback.error)),
        template_uri=escape(str(os.path.basename(e.template.filename))),
        frames="\n".join(frames)
    )

class MakoTemplates(object):
    """
    Main class for bridging mako and flask. We try to stay as close as possible
    to how Jinja2 is used in Flask, while at the same time surfacing the useful
    stuff from Mako.

    """

    def __init__(self, app=None):
        if app is not None:
            self.init_app(app)


    def init_app(self, app):
        """
        Initialize a :class:`~flask.Flask` application
        for use with this extension. This method is useful for the factory
        pattern of extension initialization. Example::

            mako = MakoTemplates()

            app = Flask(__name__)
            mako.init_app(app)

        .. note::
            This call will fail if you called the :class:`MakoTemplates`
            constructor with an ``app`` argument.

        """
        if not hasattr(app, 'extensions'):
            app.extensions = {}

        app.extensions['mako'] = self

        app.config.setdefault('MAKO_INPUT_ENCODING', 'utf-8')
        app.config.setdefault('MAKO_OUTPUT_ENCODING', 'utf-8')
        app.config.setdefault('MAKO_MODULE_DIRECTORY', None)
        app.config.setdefault('MAKO_COLLECTION_SIZE', -1)
        app.config.setdefault('MAKO_IMPORTS', None)
        app.config.setdefault('MAKO_FILESYSTEM_CHECKS', True)
        app.config.setdefault('MAKO_TRANSLATE_EXCEPTIONS', True)
        app.config.setdefault('MAKO_DEFAULT_FILTERS', None)
        app.config.setdefault('MAKO_PREPROCESSOR', None)
        app.config.setdefault('MAKO_STRICT_UNDEFINED', False)
        # Register custom handler for error happening in Make's template.
        app.errorhandler(MakoTemplateError)(self.exception_handler)

    def exception_handler(self, e):
        assert isinstance(e, MakoTemplateError)
        return render_mako_error(e), 500


def _create_lookup(app):
    """Returns a :class:`TemplateLookup <mako.lookup.TemplateLookup>`
    instance that looks for templates from the same places as Flask, ie.
    subfolders named 'templates' in both the app folder and its blueprints'
    folders.

    If flask-babel is installed it will add support for it in the templates
    by adding the appropriate imports clause.

    """
    imports = app.config['MAKO_IMPORTS'] or []
    imports.append(_FLASK_IMPORTS)

    if 'babel' in app.extensions:
        imports.append(_BABEL_IMPORTS)

    # for beaker
    cache_impl = app.config.get('MAKO_CACHE_IMPL')
    cache_args = app.config.get('MAKO_CACHE_ARGS')

    kw = {
        'input_encoding': app.config['MAKO_INPUT_ENCODING'],
        'output_encoding': app.config['MAKO_OUTPUT_ENCODING'],
        'module_directory': app.config['MAKO_MODULE_DIRECTORY'],
        'collection_size': app.config['MAKO_COLLECTION_SIZE'],
        'imports': imports,
        'filesystem_checks': app.config['MAKO_FILESYSTEM_CHECKS'],
        'default_filters': app.config['MAKO_DEFAULT_FILTERS'],
        'preprocessor': app.config['MAKO_PREPROCESSOR'],
        'strict_undefined': app.config['MAKO_STRICT_UNDEFINED'],
    }

    if cache_impl:
        kw['cache_impl'] = cache_impl

    if cache_args:
        kw['cache_args'] = cache_args

    if isinstance(app.template_folder, (list, tuple)):
        paths = [os.path.join(app.root_path, tf) for tf in app.template_folder]
    else:
        paths = [os.path.join(app.root_path, app.template_folder)]
    blueprints = getattr(app, 'blueprints', {})
    for blueprint in itervalues(blueprints):
        bp_tf = blueprint.template_folder
        if bp_tf:
            if isinstance(bp_tf, (list, tuple)):
                paths.extend([os.path.join(blueprint.root_path, tf)
                              for tf in bp_tf])
            else:
                paths.append(os.path.join(blueprint.root_path, bp_tf))
    paths = [path for path in paths if os.path.isdir(path)]
    return TemplateLookup(directories=paths, **kw)


def _lookup(app):
    key_name = "_mako_lookup"
    ctxt = GLOBAL.get(key_name)
    if ctxt is None:
        ctxt = _create_lookup(app)
        setattr(GLOBAL, key_name, ctxt)
    return ctxt


def _render(template, context, app):
    """Renders the template and fires the signal"""
    context.update(app.jinja_env.globals)
    app.update_template_context(context)
    try:
        rv = template.render(**context)
        template_rendered.send(app, template=template, context=context)
        return rv
    except:
        translate = app.config.get("MAKO_TRANSLATE_EXCEPTIONS")
        if translate:
            translated = MakoTemplateError(template)
            raise translated
        else:
            raise


def render_template(template_name, **context):
    """Renders a template from the template folder with the given
    context.

    :param template_name: the name of the template to be rendered
    :param context: the variables that should be available in the
                    context of the template.
    """
    app = current_app
    return _render(_lookup(app).get_template(template_name),
                   context, app)


def render_template_string(source, **context):
    """Renders a template from the given template source string
    with the given context.

    :param source: the sourcecode of the template to be
                          rendered
    :param context: the variables that should be available in the
                    context of the template.
    """
    template = Template(source, lookup=_lookup(current_app))
    return _render(template, context, current_app)


def render_template_def(template_name, def_name, **context):
    """Renders a specific def from a given
    template from the template folder with the given
    context. Useful for implementing this AJAX pattern:

    http://techspot.zzzeek.org/2008/09/01/ajax-the-mako-way

    :param template_name: the name of the template file containing the def
                    to be rendered
    :param def_name: the name of the def to be rendered
    :param context: the variables that should be available in the
                    context of the template.
    """
    template = _lookup(current_app).get_template(template_name)
    return _render(template.get_def(def_name), context, current_app)
