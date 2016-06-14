import os
import json
import yaml

import apt_pkg

from charms.reactive import (
    helpers,
    when,
    when_not,
    when_file_changed,
    set_state,
    remove_state,
    helpers,
)
from charms.reactive.bus import get_states

from charmhelpers.core import hookenv, host
from charmhelpers.core.templating import render
from charmhelpers.fetch import apt_install, apt_update, add_source

from jinja2 import Template

BASE_DIR = '/etc/telegraf'

CONFIG_FILE = 'telegraf.conf'

CONFIG_DIR = 'telegraf.d'


# Utilities #
def exec_timeout_supported():
    apt_pkg.init()
    apt_pkg.config.set("Dir::Cache::pkgcache", "")
    cache = apt_pkg.Cache()
    pkg = cache['telegraf']
    timeout_support = True
    if '0.12' in pkg.current_ver.ver_str:
        timeout_support = False
    return timeout_support


def get_templates_dir():
    return os.path.join(hookenv.charm_dir(), 'templates')


def get_main_config_path():
    return os.path.join(BASE_DIR, CONFIG_FILE)


def get_configs_dir():
    return os.path.join(BASE_DIR, CONFIG_DIR)


def list_supported_plugins():
    return [k for k in hookenv.metadata()['requires'].keys()
            if k != 'juju-info'] + \
        [k for k in hookenv.metadata()['provides'].keys()
            if k != 'juju-info']


def list_config_files():
    config_files = [get_main_config_path()]
    # only include config files for configured plugins
    current_states = get_states()
    for plugin in list_supported_plugins():
        if 'plugins.{}.configured'.format(plugin) in current_states.keys():
            config_path = '{}/{}.conf'.format(get_configs_dir(), plugin)
            config_files.append(config_path)
    if 'extra_plugins.configured' in current_states.keys():
        config_files.append('{}/extra_plugins.conf'.format(get_configs_dir()))
    return config_files


def get_remote_unit_name():
    for rel_type in hookenv.metadata()['requires'].keys():
        rels = hookenv.relations_of_type(rel_type)
        if rels and len(rels) >= 1:
            rel = rels[0]
            if rel['private-address'] == hookenv.unit_private_ip():
                return rel['__unit__']


def render_base_inputs():
    extra_options = get_extra_options()
    # use base inputs from charm templates
    with open(os.path.join(get_templates_dir(), 'base_inputs.conf'), 'r') as fd:
        return render_template(
            fd.read(),
            {'extra_options': extra_options['inputs']})


def get_extra_options():
    extra_options = {'inputs': {}, 'outputs': {}}
    extra_options_raw = hookenv.config()['extra_options']
    extra_opts = yaml.load(extra_options_raw) or {}
    extra_options.update(extra_opts)
    # jsonify value, required as the telegraf config values format is similar
    # to raw json
    json_vals = {}
    # kind level
    for k, v in extra_options.items():
        json_vals[k] = {}
        # plugins level
        for plugin, values in v.items():
            json_vals[k][plugin] = {}
            # inner plugin (aka key:value)
            for key, val in values.items():
                if key in ('tagpass', 'tagdrop'):
                    # this is a tagpass/drop, we need to go deeper
                    json_vals[k][plugin][key] = {}
                    for tag, tagvalue in val.items():
                        json_vals[k][plugin][key][tag] = json.dumps(tagvalue)
                else:
                    json_vals[k][plugin][key] = json.dumps(val)
    return json_vals


def render_extra_options(kind, name, extra_options=None):
    template = """
  {% if extra_options %}
  {% for key, value in extra_options.items() %}
  {% if key != 'tagpass' and key != 'tagdrop' %}
  {{ key }} = {{ value }}
  {% endif %}
  {% endfor %}
  {% for key, value in extra_options.items() %}
  {% if key == 'tagpass' or key == 'tagdrop' %}
  [{{ kind }}.{{ name }}.{{ key }}]
    {% for tag, tagvalue in value.items() %}
    {{ tag }} = {{ tagvalue }}
    {% endfor %}
  {% endif %}
  {% endfor %}
  {% endif %}
  """
    if extra_options is None:
        extra_options = get_extra_options()
    context = {"extra_options": extra_options[kind].get(name, {}),
               "kind": kind,
               "name": name}
    return render_template(template, context)


def render_template(template, context):
    tmpl = Template(template, lstrip_blocks=True, trim_blocks=True)
    return tmpl.render(**context)


# States


@when_not('telegraf.installed')
def install_telegraf():
    # Do your setup here.
    #
    # If your charm has other dependencies before it can install,
    # add those as @when() clauses above., or as additional @when()
    # decorated handlers below
    #
    # See the following for information about reactive charms:
    #
    #  * https://jujucharms.com/docs/devel/developer-getting-started
    #  * https://github.com/juju-solutions/layer-basic#overview
    #
    config = hookenv.config()
    if config['apt_repository'] and config['apt_repository_key']:
        add_source(config['apt_repository'],
                   config['apt_repository_key'])
        apt_update()
    apt_install(config['package_name'], fatal=True)
    set_state('telegraf.installed')


@when('telegraf.installed')
@when_not('telegraf.configured')
def configure_telegraf():
    config = hookenv.config()
    context = config.copy()
    inputs = config.get('inputs_config', '')
    outputs = config.get('outputs_config', '')
    tags = []
    if config['tags']:
        for tag in config['tags'].split(','):
            key, value = tag.split("=")
            tags.append('{} = "{}"'.format(key, value))
    context["tags"] = tags
    if inputs:
        context["inputs"] = inputs
    else:
        # use base inputs from charm templates
        context["inputs"] = render_base_inputs()
    if outputs:
        context["outputs"] = outputs
    else:
        context["outputs"] = ""
        hookenv.log("No output plugins in main config.")
    config_path = get_main_config_path()
    if config["hostname"] == "UNIT_NAME":
        remote_unit_name = get_remote_unit_name()
        if remote_unit_name is not None:
            context["hostname"] = remote_unit_name.replace('/', '-')
        else:
            hookenv.log("Waiting for relation to render config file.")
            # if UNIT_NAME in hostname config and relation not yet available,
            # make telegraf unable to start to not get weird metrics names
            if os.path.exists(config_path):
                os.unlink(config_path)
            return
    hookenv.log("Updating main config file")
    render(source='telegraf.conf.tmpl', templates_dir=get_templates_dir(),
           target=config_path, context=context)
    set_state('telegraf.configured')


@when('config.changed')
def handle_config_changes():
    config = hookenv.config()
    if config.changed('extra_options'):
        for plugin in list_supported_plugins():
            remove_state('plugins.{}.configured'.format(plugin))
    if config.changed('apt_repository') or config.changed('package_name'):
        remove_state('telegraf.installed')
    # if something else changed, let's reconfigure telegraf itself just in case
    if config.changed('extra_plugins'):
        remove_state('extra_plugins.configured')
    remove_state('telegraf.configured')


@when('telegraf.configured')
@when_not('extra_plugins.configured')
def configure_extra_plugins():
    config = hookenv.config()
    plugins = config['extra_plugins']
    if plugins:
        config_path = '{}/extra_plugins.conf'.format(get_configs_dir())
        host.write_file(config_path, plugins.encode('utf-8'))
        set_state('extra_plugins.configured')


@when('elasticsearch.available')
def elasticsearch_input(es):
    template = """
[[inputs.elasticsearch]]
  servers = {{ servers }}
"""
    hosts = []
    rels = hookenv.relations_of_type('elasticsearch')
    for rel in rels:
        es_host = rel.get('host')
        port = rel.get('port')
        if not es_host or not port:
            hookenv.log('No host received for relation: {}.'.format(rel))
            continue
        hosts.append("http://{}:{}".format(es_host, port))
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'elasticsearch')
    if hosts:
        context = {"servers": json.dumps(hosts)}
        input_config = render_template(template, context) + \
            render_extra_options("inputs", "elasticsearch")
        hookenv.log("Updating {} plugin config file".format('elasticsearch'))
        host.write_file(config_path, input_config.encode('utf-8'))
        set_state('plugins.elasticsearch.configured')
    elif os.path.exists(config_path):
        os.unlink(config_path)
        remove_state('plugins.elasticsearch.configured')


@when('memcached.available')
def memcached_input(memcache):
    template = """
[[inputs.memcached]]
  servers = {{ servers }}
"""
    required_keys = ['host', 'port']
    rels = hookenv.relations_of_type('memcached')
    addresses = []
    for rel in rels:
        if all([rel.get(key) for key in required_keys]):
            addr = rel['host']
            port = rel['port']
            address = '{}:{}'.format(addr, port)
            addresses.append(address)
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'memcached')
    if addresses:
        context = {"servers": json.dumps(addresses)}
        input_config = render_template(template, context) + \
            render_extra_options("inputs", "memcached")
        hookenv.log("Updating {} plugin config file".format('memcached'))
        host.write_file(config_path, input_config.encode('utf-8'))
        set_state('plugins.memcached.configured')
    elif os.path.exists(config_path):
        os.unlink(config_path)


@when('mongodb.database.available')
def mongodb_input(mongodb):
    template = """
[[inputs.mongodb]]
  servers = {{ servers }}
"""
    rels = hookenv.relations_of_type('mongodb')
    mongo_addresses = []
    for rel in rels:
        addr = rel['private-address']
        port = rel.get('port', None)
        if port:
            mongo_address = '{}:{}'.format(addr, port)
        else:
            mongo_address = addr
        mongo_addresses.append(mongo_address)
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'mongodb')
    if mongo_addresses:
        context = {"servers": json.dumps(mongo_addresses)}
        input_config = render_template(template, context) + \
            render_extra_options("inputs", "mongodb")
        hookenv.log("Updating {} plugin config file".format('mongodb'))
        host.write_file(config_path, input_config.encode('utf-8'))
        set_state('plugins.mongodb.configured')
    elif os.path.exists(config_path):
        os.unlink(config_path)


@when('postgresql.database.available')
def postgresql_input(db):
    template = """
[[inputs.postgresql]]
  address = "host={{host}} user={{user}} password={{password}} dbname={{database}}"
"""
    required_keys = ['host', 'user', 'password', 'database']
    rels = hookenv.relations_of_type('postgresql')
    inputs = []
    for rel in rels:
        if all([rel.get(key) for key in required_keys]) \
                and hookenv.local_unit() in rel.get('allowed-units') \
                and rel['private-address'] == hookenv.unit_private_ip():
            context = rel.copy()
            inputs.append(render_template(template, context) +
                          render_extra_options("inputs", "postgresql"))
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'postgresql')
    if inputs:
        hookenv.log("Updating {} plugin config file".format('postgresql'))
        host.write_file(config_path, '\n'.join(inputs).encode('utf-8'))
        set_state('plugins.postgresql.configured')
    elif os.path.exists(config_path):
        os.unlink(config_path)


@when('haproxy.available')
def haproxy_input(haproxy):
    template = """
[[inputs.haproxy]]
  servers = {{ servers }}
"""
    rels = hookenv.relations_of_type('haproxy')
    haproxy_addresses = []
    for rel in rels:
        enabled = rel.get('enabled', False)
        # Juju gives us a string instead of a boolean, fix it
        if isinstance(enabled, str):
            if enabled in ['y', 'yes', 'true', 't', 'on', 'True']:
                enabled = True
            else:
                enabled = False
        if not enabled:
            continue
        addr = rel['private-address']
        if addr == hookenv.unit_private_ip():
            addr = "localhost"
        port = rel['port']
        user = rel['user']
        password = rel.get('password', None)
        userpass = user
        if password:
            userpass += ":{}".format(password)
        haproxy_address = 'http://{}@{}:{}'.format(userpass, addr, port)
        haproxy_addresses.append(haproxy_address)
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'haproxy')
    if haproxy_addresses:
        input_config = render_template(template, {"servers": json.dumps(haproxy_addresses)}) + \
            render_extra_options("inputs", "haproxy")
        hookenv.log("Updating {} plugin config file".format('haproxy'))
        host.write_file(config_path, input_config.encode('utf-8'))
        set_state('plugins.haproxy.configured')
    elif os.path.exists(config_path):
        os.unlink(config_path)


@when('apache.available')
def apache_input(apache):
    template = """
[[inputs.apache]]
  urls = {{ urls }}
"""
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'apache')
    port = '8080'
    vhost = render(source='apache-server-status.tmpl',
                   templates_dir=get_templates_dir(),
                   target=None,
                   context={'port': port})
    relation_info = {"ports": port,
                     "domain": "apache-status",
                     "enabled": True,
                     "site_config": vhost,
                     "site_modules": "status"}
    urls = []
    rels = hookenv.relations_of_type('apache')
    for rel in rels:
        hookenv.relation_set(rel['__relid__'], relation_settings=relation_info)
        addr = rel['private-address']
        url = 'http://{}:{}/server-status?auto'.format(addr, port)
        urls.append(url)
    if urls:
        context = {"urls": json.dumps(urls)}
        input_config = render_template(template, context) + \
            render_extra_options("inputs", "apache")
        hookenv.log("Updating {} plugin config file".format('apache'))
        host.write_file(config_path, input_config.encode('utf-8'))
        set_state('plugins.apache.configured')
    elif os.path.exists(config_path):
        os.unlink(config_path)


@when('exec.available')
def exec_input(exec_rel):
    template = """
{% for cmd in commands %}
[[inputs.exec]]
  commands = {{ cmd.commands }}
  {% for key, value in cmd|dictsort %}
      {% if key not in ["commands", "tags"] %}
  {{ key }} = "{{ value }}"
      {% endif %}
  {% endfor %}
  {% if cmd.tags %}
  [inputs.exec.tags]
    {% for tag, tag_value in cmd.tags|dictsort %}
    {{ tag }} = "{{ tag_value }}"
    {% endfor %}
  {% endif %}

{% endfor %}
"""
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'exec')
    commands = exec_rel.commands()
    if not commands:
        hookenv.log("No Commands defined in the exec relation, doing nothing.")
        return
    timeout_support = exec_timeout_supported()
    pre_proc_cmds = []
    for command in commands:
        if not timeout_support:
            command.pop('timeout')
        run_on_this_unit = command.pop('run_on_this_unit')
        if run_on_this_unit:
            pre_proc_cmds.append(dict((k, v) for k, v in command.items()))
    if pre_proc_cmds:
        input_config = render_template(template, {'commands': pre_proc_cmds})
        hookenv.log("Updating {} plugin config file".format('exec'))
        host.write_file(config_path, input_config.encode('utf-8'))
        set_state('plugins.exec.configured')


@when_not('exec.available')
@when('plugins.exec.configured')
def exec_input_departed():
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'exec')
    rels = hookenv.relations_of_type('exec')
    if not rels:
        remove_state('plugins.exec.configured')
        if os.path.exists(config_path):
            os.unlink(config_path)


@when('influxdb-api.available')
def influxdb_api_output(influxdb):
    required_keys = ['hostname', 'port', 'user', 'password']
    rels = hookenv.relations_of_type('influxdb-api')
    endpoints = []
    user = None
    password = None
    for rel in rels:
        if all([rel.get(key) for key in required_keys]):
            endpoints.append("http://{}:{}".format(rel['hostname'], rel['port']))
            if user is None:
                user = rel['user']
            if password is None:
                password = rel['password']
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'influxdb-api')
    if endpoints:
        hookenv.log("Updating {} plugin config file".format('influxdb-api'))
        content = render(source='influxdb-api.conf.tmpl', target=None,
                         templates_dir=get_templates_dir(),
                         context={'urls': json.dumps(endpoints),
                                  'username': '{}'.format(user),
                                  'password': '{}'.format(password)})
        extra_opts = render_extra_options("outputs", "influxdb")
        host.write_file(config_path, '\n'.join([content, extra_opts]).encode('utf-8'))
        set_state('plugins.influxdb-api.configured')
    elif os.path.exists(config_path):
        os.unlink(config_path)


@when('prometheus-client.available')
def prometheus_client(prometheus):
    template = """
[[outputs.prometheus_client]]
  listen = "{{ listen }}"
"""
    port = 9126
    extra_options = get_extra_options()
    options = extra_options['outputs'].get('prometheus-client', {})
    listen = options.pop('listen', None)
    if listen is not None:
        hookenv.log("Configuring prometheus_client plugin to listen on: '{}'".format(listen))
        port = int(listen.split(":", 1)[1])
    else:
        listen = ":{}".format(port)
    prometheus.configure(port)
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'prometheus-client')
    hookenv.log("Updating {} plugin config file".format('prometheus-client'))
    context = {"listen": listen}
    content = render_template(template, context) + \
        render_extra_options("outputs", "prometheus_client")
    host.write_file(config_path, content.encode('utf-8'))
    set_state('plugins.prometheus-client.configured')


@when_not('prometheus-client.available')
@when('plugins.prometheus-client.configured')
def prometheus_client_departed():
    hookenv.log("prometheus-client relation not available")
    config_path = '{}/{}.conf'.format(get_configs_dir(), 'prometheus-client')
    rels = hookenv.relations_of_type('prometheus-client')
    if not rels and os.path.exists(config_path):
        hookenv.log("Deleting {} plugin config file".format('prometheus-client'))
        os.unlink(config_path)
        remove_state('plugins.prometheus-client.configured')


@when('telegraf.configured')
def start_or_restart():
    states = sorted([k for k in get_states().keys()
                     if k.startswith('plugins') or k.startswith('extra_plugins')])

    config_files_changed = helpers.any_file_changed(list_config_files())
    active_plugins_changed = helpers.data_changed('active_plugins', states or '')
    if config_files_changed or active_plugins_changed:
        hookenv.log("Restarting telegraf")
        host.service_restart('telegraf')
    else:
        hookenv.log("Not restarting: active_plugins_changed={} | "
                    "config_files_changed={}".format(active_plugins_changed,
                                                     config_files_changed))
