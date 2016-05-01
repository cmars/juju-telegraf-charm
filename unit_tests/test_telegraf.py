"""actions.py tests"""
import os
import getpass
import json

from functools import partial

import pytest

from charmhelpers.core.templating import render

import reactive

os.environ['CHARM_DIR'] = os.path.join(os.path.dirname(reactive.__file__), "../")

from reactive import telegraf


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmpdir):
    monkeypatch.setitem(os.environ, 'JUJU_UNIT_NAME', 'telegraf-0')
    # patch host.write for non-root
    user = getpass.getuser()
    partial_write_file = partial(telegraf.host.write_file, owner=user, group=user)
    monkeypatch.setattr(telegraf.host, 'write_file', partial_write_file)


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


@pytest.fixture()
def config_dir(monkeypatch, tmpdir):
    p = tmpdir.mkdir("configs")
    monkeypatch.setattr(telegraf, 'CONFIG_DIR', p.strpath)
    return p


@pytest.fixture()
def config(monkeypatch):
    config = {'extra_options': ''}
    monkeypatch.setattr(telegraf.hookenv, 'config', lambda: config)
    return config


def test_render_extra_options(monkeypatch, config):
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


def test_get_extra_options(monkeypatch, config):
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


def test_render_extra_options_override(monkeypatch, config):
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


def test_render_base_inputs(monkeypatch, config):
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


def test_elasticsearch_input(monkeypatch, config_dir, config):
    relations = [{'host': '1.2.3.4', 'port': 1234}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)

    telegraf.elasticsearch_input('test')
    expected = """
[[inputs.elasticsearch]]
  servers = ["http://1.2.3.4:1234"]
"""
    assert config_dir.join('elasticsearch.conf').read().strip() == expected.strip()

def test_elasticsearch_input_no_relations(monkeypatch, config_dir):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.elasticsearch_input('test')
    assert not config_dir.join('elasticsearch.conf').exists()


def test_memcached_input(monkeypatch, config_dir, config):
    relations = [{'host': '1.2.3.4', 'port': 1234}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.memcached_input('test')
    expected = """
[[inputs.memcached]]
  servers = ["1.2.3.4:1234"]
"""
    assert config_dir.join('memcached.conf').read().strip() == expected.strip()

def test_memcached_input_no_relations(monkeypatch, config_dir):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.memcached_input('test')
    assert not config_dir.join('memcached.conf').exists()


def test_mongodb_input(monkeypatch, config_dir, config):
    relations = [{'private-address': '1.2.3.4', 'port': 1234}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.mongodb_input('test')
    expected = """
[[inputs.mongodb]]
  servers = ["1.2.3.4:1234"]
"""
    assert config_dir.join('mongodb.conf').read().strip() == expected.strip()


def test_mongodb_input_no_relations(monkeypatch, config_dir):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.mongodb_input('test')
    assert not config_dir.join('mongodb.conf').exists()


def test_postgresql_input(monkeypatch, config_dir, config):
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
    assert config_dir.join('postgresql.conf').read().strip() == expected.strip()


def test_postgresql_input_no_relations(monkeypatch, config_dir):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.postgresql_input('test')
    assert not config_dir.join('postgresql.conf').exists()


def test_haproxy_input(monkeypatch, config_dir, config):
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
    assert config_dir.join('haproxy.conf').read().strip() == expected.strip()


def test_haproxy_input_no_relations(monkeypatch, config_dir):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.haproxy_input('test')
    assert not config_dir.join('haproxy.conf').exists()


def test_haproxy_input_not_enabled(monkeypatch, config_dir):
    relations = [{'private-address': '1.2.3.4',
                  'port': 1234,
                  'user': 'foo',
                  'password': 'bar',
                  'enabled': 'False'}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    telegraf.haproxy_input('test')
    assert not config_dir.join('haproxy.conf').exists()


def test_apache_input(monkeypatch, config_dir, config):
    relations = [{'__relid__': 'apache:0', 'private-address': '1.2.3.4'}]
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: relations)
    monkeypatch.setattr(telegraf.hookenv, 'relation_set', lambda *a, **kw: None)
    telegraf.apache_input('test')
    expected = """
[[inputs.apache]]
  urls = ["http://1.2.3.4:8080/server-status?auto"]
"""
    assert config_dir.join('apache.conf').read().strip() == expected.strip()


def test_apache_input_no_relations(monkeypatch, config_dir):
    monkeypatch.setattr(telegraf.hookenv, 'relations_of_type', lambda n: [])
    telegraf.apache_input('test')
    assert not config_dir.join('apache.conf').exists()


def test_influxdb_api_output(monkeypatch, config_dir, config):
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
    assert config_dir.join('influxdb-api.conf').read().strip() == expected.strip()
