"""actions.py tests"""
import base64
import os
import getpass
import json

from functools import partial

import yaml
import pytest
import py

from charms.reactive import bus, helpers, RelationBase
from charmhelpers.core import hookenv
from charmhelpers.core.hookenv import Config
from charmhelpers.core.templating import render


import reactive

from reactive import telegraf


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmpdir):
    monkeypatch.setitem(os.environ, 'JUJU_UNIT_NAME', 'telegraf-0')
    monkeypatch.setattr(telegraf, 'get_remote_unit_name', lambda: 'remote-unit-0')
    monkeypatch.setattr(telegraf, 'exec_timeout_supported', lambda: True)
    # patch host.write for non-root
    user = getpass.getuser()
    orig_write_file = telegraf.host.write_file

    def intercept_write_file(*a, **kw):
        # fix the args to use non-root, this is for callers that pass
        # owner/group as positional arguments like
        # charmhelpers.core.templating.render
        if len(a) > 2:
            if a[2] == 'root' and a[3] == 'root':
                # make all files writable by owner, as we need don't run as root
                a = (a[0], a[1], user, user, 0o744)
        else:
            kw['owner'] = user
            kw['group'] = user
            # make all files writable by owner, as we need don't run as root
            kw['perms'] = 0o744
        return orig_write_file(*a, **kw)
    monkeypatch.setattr(telegraf.host, 'write_file', intercept_write_file)


@pytest.fixture(autouse=True)
def cleanup(request):
    def unit_state_cleanup():
        # cleanup unitdata
        from charmhelpers.core import unitdata
        unitdata._KV = None
        # rm unit-state.db file
        unit_state_db = os.path.join(telegraf.hookenv.charm_dir(), '.unit-state.db')
        if os.path.exists(unit_state_db):
            os.unlink(unit_state_db)
    request.addfinalizer(unit_state_cleanup)


@pytest.fixture()
def temp_charm_dir(monkeypatch, tmpdir):
    charm_dir = tmpdir.mkdir("charm_dir")
    os.environ['CHARM_DIR'] = charm_dir.strpath
    # also monkeypatch get_templates_dir to fix the path
    real_charm_dir = os.path.join(os.path.dirname(reactive.__file__), "../")
    monkeypatch.setattr(telegraf, 'get_templates_dir',
                        lambda: os.path.join(real_charm_dir, 'templates'))
    # fix hookenv.metadata
    with open(os.path.join(real_charm_dir, 'metadata.yaml')) as md:
        metadata = yaml.safe_load(md)
    monkeypatch.setattr(telegraf.hookenv, 'metadata', lambda: metadata)



@pytest.fixture(autouse=True)
def temp_config_dir(monkeypatch, tmpdir):
    base_dir = tmpdir.mkdir("etc_telegraf")
    configs_dir = base_dir.mkdir(telegraf.CONFIG_DIR)
    monkeypatch.setattr(telegraf, 'BASE_DIR', base_dir.strpath)


@pytest.fixture(autouse=True)
def config(monkeypatch, temp_charm_dir):
    raw_config = yaml.load(open('config.yaml', 'r'))
    data = dict((k, v['default']) for k, v in raw_config['options'].items())
    config = Config(data)
    monkeypatch.setattr(telegraf.hookenv, 'config', lambda: config)
    return config


# utilities
def base_dir():
    return py.path.local(telegraf.BASE_DIR)


def configs_dir():
    return py.path.local(telegraf.get_configs_dir())


def persist_state():
    """Fake persistent state by calling helpers that modify unitdata.kv"""
    states = [k for k in bus.get_states().keys()
              if k.startswith('plugins') or k.startswith('extra_plugins')]
    helpers.any_file_changed(telegraf.list_config_files())
    if states:
        helpers.data_changed('active_plugins', states)


# Tests
def test_get_remote_unit_name(monkeypatch):
    monkeypatch.undo()
    # fix hookenv.metadata
    real_charm_dir = os.path.join(os.path.dirname(reactive.__file__), "../")
    with open(os.path.join(real_charm_dir, 'metadata.yaml')) as md:
        metadata = yaml.safe_load(md)
    monkeypatch.setattr(telegraf.hookenv, 'metadata', lambda: metadata)
    relations = []
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    monkeypatch.setattr(telegraf.hookenv, 'unit_private_ip', lambda: '1.2.3.4')
    assert telegraf.get_remote_unit_name() is None
    relations = [{'private-address': '1.2.3.4', '__unit__': 'remote-0'}]
    assert telegraf.get_remote_unit_name() == 'remote-0'


def test_inputs_config_set(monkeypatch, config):
    config['inputs_config'] = """
    [[inputs.cpu]]
        percpu = true
"""

    def check(*a, **kw):
        assert kw['context']['inputs'] == config['inputs_config']
    monkeypatch.setattr(telegraf, 'render', check)
    telegraf.configure_telegraf()


def test_old_base64_inputs_and_outputs(monkeypatch, config):
    config['inputs_config'] = base64.b64encode(b"""
    [[inputs.cpu]]
        percpu = true
""").decode('utf-8')
    config['outputs_config'] = base64.b64encode(b"""
    [[outputs.fake]]
        foo = true
""").decode('utf-8')

    def check(*a, **kw):
        expected = base64.b64decode(config['inputs_config']).decode('utf-8')
        assert kw['context']['inputs'] == expected
        expected = base64.b64decode(config['outputs_config']).decode('utf-8')
        assert kw['context']['outputs'] == expected
    monkeypatch.setattr(telegraf, 'render', check)
    telegraf.configure_telegraf()


def test_inputs_config_not_set(monkeypatch, config):
    config['inputs_config'] = ""

    def check(*a, **kw):
        assert kw['context']['inputs'] == telegraf.render_base_inputs()
    monkeypatch.setattr(telegraf, 'render', check)
    telegraf.configure_telegraf()


def test_outputs_config(monkeypatch, config):
    config['outputs_config'] = """
    [[outputs.foo]]
        server = "http://localhost:42"
"""

    def check(*a, **kw):
        assert kw['context']['outputs'] == config['outputs_config']
    monkeypatch.setattr(telegraf, 'render', check)
    telegraf.configure_telegraf()


def test_extra_plugins(config):
    config['extra_plugins'] = """[[inputs.foo]]
    some_option = "http://foo.bar.com"
[[outputs.baz]]
    option = "enabled"
    """
    telegraf.configure_extra_plugins()
    assert configs_dir().join('extra_plugins.conf').read() == config['extra_plugins']


def test_render_extra_options(config):
    extra_options = """
    inputs:
        test:
            boolean: true
            string: 10s
            list: ["a", "b"]
"""
    config['extra_options'] = extra_options
    content = telegraf.render_extra_options('inputs', 'test')
    expected = """  boolean = true\n  list = ["a", "b"]\n  string = "10s"\n"""
    assert sorted(content.split()) == sorted(expected.split())


def test_get_extra_options(config):
    extra_options = """
    inputs:
        test:
            boolean: true
            string: somestring
            list: ["a", "b"]
            tagdrop:
                tag: ["foo", "bar"]
"""
    config['extra_options'] = extra_options
    extra_opts = telegraf.get_extra_options()
    expected = {
        "inputs": {
            "test": {
                "boolean": "true",
                "string": '"somestring"',
                "list": '["a", "b"]',
                "tagdrop": {
                    "tag": '["foo", "bar"]'
                }
            }
        },
        "outputs": {}
    }
    assert extra_opts == expected


def test_render_extra_options_override(config):
    extra_options = """
    inputs:
        test:
            boolean: true
            string: 10s
            list: ["a", "b"]
"""
    config['extra_options'] = extra_options
    # clone extra_options and use a modified version
    options = {'inputs': {'test': {'string': json.dumps("20s")}}}
    content = telegraf.render_extra_options('inputs', 'test', extra_options=options)
    expected = """  string = "20s"\n"""
    assert sorted(content.split()) == sorted(expected.split())


def test_render_base_inputs(config):
    base_inputs_opts = """
inputs:
    cpu:
        foo: 10s
        percpu: false
        fielddrop: ["time_*"]
        tagpass:
            cpu: ["cpu0"]
"""
    config['extra_options'] = base_inputs_opts
    content = telegraf.render_base_inputs()
    expected = """
# Read metrics about cpu usage
[[inputs.cpu]]
  fielddrop = ["time_*"]
  foo = "10s"
  percpu = false
  [inputs.cpu.tagpass]
    cpu = ["cpu0"]
"""
    assert content[:len(expected)] == expected


def test_check_port(monkeypatch):
    open_ports = set()
    monkeypatch.setattr(telegraf.hookenv, 'open_port',
                        lambda p: open_ports.add(p))
    telegraf.check_port('test_check_port', 10042)
    assert 10042 in open_ports


def test_check_port_replace_old_port(monkeypatch):
    open_ports = set()
    monkeypatch.setattr(telegraf.hookenv, 'open_port',
                        lambda p: open_ports.add(p))
    monkeypatch.setattr(telegraf.hookenv, 'close_port',
                        lambda p: open_ports.remove(p))
    telegraf.check_port('test_check_port', 10042)
    assert 10042 in open_ports
    telegraf.check_port('test_check_port', 10043)
    assert 10043 in open_ports
    assert 10042 not in open_ports


def test_get_prometheus_port(monkeypatch, config):
    config['prometheus_output_port'] = ''
    assert telegraf.get_prometheus_port() is False
    config['prometheus_output_port'] = 'default'
    assert telegraf.get_prometheus_port() == 9103
    config['prometheus_output_port'] = '9126'
    assert telegraf.get_prometheus_port() == 9126


def test_prometheus_global(monkeypatch, config):
    open_ports = set()
    monkeypatch.setattr(telegraf.hookenv, 'open_port',
                        lambda p: open_ports.add(p))
    monkeypatch.setattr(telegraf.hookenv, 'close_port',
                        lambda p: open_ports.remove(p))
    config['prometheus_output_port'] = 'default'
    telegraf.configure_telegraf()
    expected = """
[[outputs.prometheus_client]]
  listen = ":9103"
"""
    config_file = base_dir().join('telegraf.conf')
    assert expected in config_file.read()


def test_prometheus_global_with_extra_options(monkeypatch, config):
    open_ports = set()
    monkeypatch.setattr(telegraf.hookenv, 'open_port',
                        lambda p: open_ports.add(p))
    monkeypatch.setattr(telegraf.hookenv, 'close_port',
                        lambda p: open_ports.remove(p))
    config['prometheus_output_port'] = 'default'
    config['extra_options'] = """
outputs:
  prometheus_client:
    namedrop: ["aerospike*"]
    tagpass:
      cpu: ["cpu0"]

"""
    telegraf.configure_telegraf()
    expected = """
[[outputs.prometheus_client]]
  listen = ":9103"
  namedrop = ["aerospike*"]
  [outputs.prometheus_client.tagpass]
    cpu = ["cpu0"]
"""
    config_file = base_dir().join('telegraf.conf')
    print(config_file.read())
    assert expected in config_file.read()


# Plugin tests


def test_elasticsearch_input(monkeypatch, config):
    relations = [{'host': '1.2.3.4', 'port': 1234}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)

    telegraf.elasticsearch_input('test')
    expected = """
[[inputs.elasticsearch]]
  servers = ["http://1.2.3.4:1234"]
"""
    assert configs_dir().join('elasticsearch.conf').read().strip() == expected.strip()


def test_elasticsearch_input_no_relations(monkeypatch):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.elasticsearch_input('test')
    assert not configs_dir().join('elasticsearch.conf').exists()


def test_memcached_input(monkeypatch, config):
    relations = [{'host': '1.2.3.4', 'port': 1234}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.memcached_input('test')
    expected = """
[[inputs.memcached]]
  servers = ["1.2.3.4:1234"]
"""
    assert configs_dir().join('memcached.conf').read().strip() == expected.strip()


def test_memcached_input_no_relations(monkeypatch):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.memcached_input('test')
    assert not configs_dir().join('memcached.conf').exists()


def test_mongodb_input(monkeypatch, config):
    relations = [{'private-address': '1.2.3.4', 'port': 1234}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.mongodb_input('test')
    expected = """
[[inputs.mongodb]]
  servers = ["1.2.3.4:1234"]
"""
    assert configs_dir().join('mongodb.conf').read().strip() == expected.strip()


def test_mongodb_input_no_relations(monkeypatch):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.mongodb_input('test')
    assert not configs_dir().join('mongodb.conf').exists()


def test_postgresql_input(monkeypatch, config):
    relations = [{'host': '1.2.3.4',
                  'port': 1234,
                  'user': 'foo',
                  'password': 'bar',
                  'database': 'the-db-name',
                  'allowed-units': ['telegraf-0'],
                  'private-address': '1.2.3.4'}]
    monkeypatch.setattr(telegraf.hookenv, 'unit_private_ip', lambda: '1.2.3.4')
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.postgresql_input('test')
    expected = """
[[inputs.postgresql]]
  address = "host=1.2.3.4 user=foo password=bar dbname=the-db-name"
"""
    assert configs_dir().join('postgresql.conf').read().strip() == expected.strip()


def test_postgresql_input_no_relations(monkeypatch):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.postgresql_input('test')
    assert not configs_dir().join('postgresql.conf').exists()


def test_haproxy_input(monkeypatch, config):
    relations = [{'private-address': '1.2.3.4',
                  'port': 1234,
                  'user': 'foo',
                  'password': 'bar',
                  'enabled': 'True'}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    monkeypatch.setattr(telegraf.hookenv, 'unit_private_ip', lambda: '1.2.3.4')
    telegraf.haproxy_input('test')
    expected = """
[[inputs.haproxy]]
  servers = ["http://foo:bar@localhost:1234"]
"""
    assert configs_dir().join('haproxy.conf').read().strip() == expected.strip()


def test_haproxy_input_no_relations(monkeypatch):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.haproxy_input('test')
    assert not configs_dir().join('haproxy.conf').exists()


def test_haproxy_input_not_enabled(monkeypatch):
    relations = [{'private-address': '1.2.3.4',
                  'port': 1234,
                  'user': 'foo',
                  'password': 'bar',
                  'enabled': 'False'}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.haproxy_input('test')
    assert not configs_dir().join('haproxy.conf').exists()


def test_apache_input(monkeypatch, config):
    relations = [{'__relid__': 'apache:0', 'private-address': '1.2.3.4'}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    monkeypatch.setattr(telegraf.hookenv, 'relation_set', lambda *a, **kw: None)
    telegraf.apache_input('test')
    expected = """
[[inputs.apache]]
  urls = ["http://1.2.3.4:8080/server-status?auto"]
"""
    assert configs_dir().join('apache.conf').read().strip() == expected.strip()


def test_apache_input_no_relations(monkeypatch):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.apache_input('test')
    assert not configs_dir().join('apache.conf').exists()


def test_exec_input(mocker, monkeypatch):
    interface = mocker.Mock(spec=RelationBase)
    interface.commands = mocker.Mock()
    command = {'commands': ['/srv/bin/test.sh', '/bin/true'],
               'data_format': 'json',
               'timeout': '5s',
               'run_on_this_unit': True}
    interface.commands.return_value = [command.copy()]
    telegraf.exec_input(interface)
    expected = """
[[inputs.exec]]
  commands = ['/srv/bin/test.sh', '/bin/true']
  data_format = "json"
  timeout = "5s"
"""
    assert configs_dir().join('exec.conf').read().strip() == expected.strip()
    # add a second relation/command set
    interface.commands.return_value = [command.copy(), command.copy()]
    telegraf.exec_input(interface)
    expected = expected +  expected
    assert configs_dir().join('exec.conf').read().strip() == expected.strip()


def test_exec_input_with_tags(mocker, monkeypatch):
    interface = mocker.Mock(spec=RelationBase)
    interface.commands = mocker.Mock()
    commands = [{'commands': ['/srv/bin/test.sh', '/bin/true'],
                 'data_format': 'json',
                 'timeout': '5s',
                 'run_on_this_unit': True,
                 'tags': {'test': 'test'}}]
    interface.commands.return_value = commands
    telegraf.exec_input(interface)
    expected = """
[[inputs.exec]]
  commands = ['/srv/bin/test.sh', '/bin/true']
  data_format = "json"
  timeout = "5s"
  [inputs.exec.tags]
    test = "test"
"""
    assert configs_dir().join('exec.conf').read().strip() == expected.strip()


def test_exec_input_no_leader(mocker, monkeypatch):
    interface = mocker.Mock(spec=RelationBase)
    interface.commands = mocker.Mock()
    commands = [{'commands': ['/srv/bin/test.sh', '/bin/true'],
                 'data_format': 'json',
                 'timeout': '5s',
                 'run_on_this_unit': False}]
    interface.commands.return_value = commands
    telegraf.exec_input(interface)
    assert not configs_dir().join('exec.conf').exists()


def test_exec_input_all_units(mocker, monkeypatch):
    interface = mocker.Mock(spec=RelationBase)
    interface.commands = mocker.Mock()
    commands = [{"commands": ["/srv/bin/test.sh", "/bin/true"],
                 'data_format': 'json',
                 'timeout': '5s',
                 'run_on_this_unit': True}]
    interface.commands.return_value = commands
    telegraf.exec_input(interface)
    expected = """
[[inputs.exec]]
  commands = ['/srv/bin/test.sh', '/bin/true']
  data_format = "json"
  timeout = "5s"
"""
    assert configs_dir().join('exec.conf').read().strip() == expected.strip()


def test_exec_input_no_timeout_support(mocker, monkeypatch):
    interface = mocker.Mock(spec=RelationBase)
    interface.commands = mocker.Mock()
    commands = [{'commands': ['/srv/bin/test.sh', '/bin/true'],
                 'data_format': 'json',
                 'timeout': '5s',
                 'run_on_this_unit': True}]
    interface.commands.return_value = commands
    expected = """
[[inputs.exec]]
  commands = ['/srv/bin/test.sh', '/bin/true']
  data_format = "json"
"""
    monkeypatch.setattr(telegraf, 'exec_timeout_supported', lambda: False)
    telegraf.exec_input(interface)
    assert configs_dir().join('exec.conf').read().strip() == expected.strip()


def test_exec_input_departed(mocker, monkeypatch):
    configs_dir().join('exec.conf').write('empty')
    relations = [1]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.exec_input_departed()
    assert configs_dir().join('exec.conf').exists()
    relations.pop()
    telegraf.exec_input_departed()
    assert not configs_dir().join('exec.conf').exists()


def test_influxdb_api_output(monkeypatch, config):
    relations = [{'hostname': '1.2.3.4',
                  'port': 1234,
                  'user': 'foo',
                  'password': 'bar'}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.influxdb_api_output('test')
    expected = render(source='influxdb-api.conf.tmpl', target=None,
                      templates_dir=telegraf.get_templates_dir(),
                      context={'username': 'foo',
                               'password': 'bar',
                               'urls': '["http://1.2.3.4:1234"]'})
    assert configs_dir().join('influxdb-api.conf').read().strip() == expected.strip()


def test_prometheus_client_output(mocker, monkeypatch, config):
    monkeypatch.setattr(telegraf.hookenv, 'open_port',
                        lambda p: None)
    interface = mocker.Mock(spec=RelationBase)
    interface.configure = mocker.Mock()
    telegraf.prometheus_client(interface)
    expected = """
    [[outputs.prometheus_client]]
  listen = ":9126"
"""
    assert configs_dir().join('prometheus-client.conf').read().strip() == expected.strip()


def test_prometheus_client_output_departed(mocker, monkeypatch, config):
    configs_dir().join('prometheus-client.conf').write('empty')
    relations = [1]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.prometheus_client_departed()
    assert configs_dir().join('prometheus-client.conf').exists()
    relations.pop()
    telegraf.prometheus_client_departed()
    assert not configs_dir().join('prometheus-client.conf').exists()


# Integration tests (kind of)
def test_basic_config(mocker, config):
    service_restart = mocker.patch('reactive.telegraf.host.service_restart')
    bus.set_state('telegraf.installed')
    assert not base_dir().join('telegraf.conf').exists()
    bus.dispatch()
    assert 'telegraf.configured' in bus.get_states().keys()
    assert base_dir().join('telegraf.conf').exists()
    service_restart.assert_called_once_with('telegraf')


def test_config_changed_apt(mocker, config):
    service_restart = mocker.patch('reactive.telegraf.host.service_restart')
    apt_install = mocker.patch('reactive.telegraf.apt_install')
    apt_update = mocker.patch('reactive.telegraf.apt_update')
    add_source = mocker.patch('reactive.telegraf.add_source')
    bus.set_state('telegraf.installed')
    config.save()
    config['apt_repository'] = "ppa:test-repo"
    config['apt_repository_key'] = "test-repo-key"
    bus.set_state('config.changed')
    bus.dispatch()
    add_source.assert_called_once_with('ppa:test-repo', 'test-repo-key')
    assert apt_update.called and apt_update.call_count == 1
    apt_install.assert_called_once_with('telegraf', fatal=True)
    service_restart.assert_called_once_with('telegraf')


def test_config_changed_extra_options(mocker, config):
    service_restart = mocker.patch('reactive.telegraf.host.service_restart')
    bus.set_state('telegraf.installed')
    bus.set_state('plugins.haproxy.configured')
    config.save()
    config.load_previous()
    config['extra_options'] = yaml.dump({'inputs': {'haproxy': {'timeout': 10}}})
    bus.set_state('config.changed')
    bus.dispatch()
    assert 'plugins.haproxy.configured' not in bus.get_states().keys()
    service_restart.assert_called_once_with('telegraf')


def test_config_changed_extra_plugins(mocker, config):
    service_restart = mocker.patch('reactive.telegraf.host.service_restart')
    bus.set_state('telegraf.installed')
    assert not configs_dir().join('extra_plugins.conf').exists()
    config.save()
    config.load_previous()
    # once the config is saved, change it. This will also trigger a service
    # restart
    config['extra_plugins'] = """[[inputs.foo]]
    some_option = "http://foo.bar.com"
[[outputs.baz]]
    option = "enabled"
    """
    bus.set_state('config.changed')
    bus.dispatch()
    assert configs_dir().join('extra_plugins.conf').exists()
    assert configs_dir().join('extra_plugins.conf').read() == config['extra_plugins']
    service_restart.assert_called_once_with('telegraf')


def test_restart_on_output_plugin_relation_departed(mocker, monkeypatch, config):
    service_restart = mocker.patch('reactive.telegraf.host.service_restart')
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    monkeypatch.setattr(telegraf.hookenv, 'open_port',
                        lambda p: None)
    bus.discover()
    bus.set_state('telegraf.installed')
    bus.set_state('telegraf.configured')
    interface = mocker.Mock(spec=RelationBase)
    interface.configure = mocker.Mock()
    telegraf.prometheus_client(interface)
    assert configs_dir().join('prometheus-client.conf').exists()
    # dispatch, file should be gone and telegraf restarted.
    bus.dispatch()
    assert not configs_dir().join('prometheus-client.conf').exists()
    service_restart.assert_called_once_with('telegraf')
