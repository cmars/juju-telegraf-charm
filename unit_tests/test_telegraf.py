"""actions.py tests"""
import os
import getpass
import json

from functools import partial

import yaml
import pytest
import py

from charms.reactive import bus
from charmhelpers.core import hookenv
from charmhelpers.core.hookenv import Config
from charmhelpers.core.templating import render


import reactive

os.environ['CHARM_DIR'] = os.path.join(os.path.dirname(reactive.__file__), "../")

from reactive import telegraf


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmpdir):
    monkeypatch.setitem(os.environ, 'JUJU_UNIT_NAME', 'telegraf-0')
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
        unit_state_db = os.path.join(os.environ['CHARM_DIR'], '.unit-state.db')
        if os.path.exists(unit_state_db):
            os.unlink(unit_state_db)
    request.addfinalizer(unit_state_cleanup)


@pytest.fixture(autouse=True)
def temp_config_dir(monkeypatch, tmpdir):
    base_dir = tmpdir.mkdir("etc_telegraf")
    configs_dir = base_dir.mkdir(telegraf.CONFIG_DIR)
    monkeypatch.setattr(telegraf, 'BASE_DIR', base_dir.strpath)


@pytest.fixture(autouse=True)
def config(monkeypatch):
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


# Tests
def test_inputs_config_set(monkeypatch, config):
    config['inputs_config'] = """
    [[inputs.cpu]]
        percpu = true
"""

    def check(*a, **kw):
        assert kw['context']['inputs'] == config['inputs_config']
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
                  'allowed-units': ['telegraf-0']}]
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


def test_influxdb_api_output(monkeypatch, config):
    relations = [{'hostname': '1.2.3.4',
                  'port': 1234,
                  'user': 'foo',
                  'password': 'bar'}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.influxdb_api_output('test')
    expected = render(source='influxdb-api.conf.tmpl', target=None,
                      context={'username': 'foo',
                               'password': 'bar',
                               'urls': '["http://1.2.3.4:1234"]'})
    assert configs_dir().join('influxdb-api.conf').read().strip() == expected.strip()


def test_basic_config_changed(monkeypatch, config):
    service_restart_called = []
    monkeypatch.setattr(telegraf.host, 'service_restart',
                        lambda s: service_restart_called.append(s))
    bus.set_state('telegraf.installed')
    bus.dispatch()
    assert not configs_dir().join('extra_plugins.conf').exists()
    config.save()
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
    assert len(service_restart_called) == 1

