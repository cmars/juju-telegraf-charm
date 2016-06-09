# Overview

This is a subordinate charm to deploy telegraf metrics agent to collect metrics from all services deployed in the environment.

For details about telegraf see: https://github.com/influxdata/telegraf 

# Usage

Deploy telegraf alonside your service, and also a time series storage (in this case, influxdb)

    juju deploy telegraf 
    juju deploy influxdb 
    juju deploy some-service

Add the relations: 

    juju add-relation telegraf:juju-info some-service:juju-info 
    juju add-relation telegraf:influxdb-api influxdb:api


# Configuration

By default there is no output plugin configured, but a basic set of input plugins are setup, which can be overriden with inputs_config charm config.

To configure any of the (default or via relation) plugins, the extra_options charm config can be used. It's string in yaml format, for example: 

    inputs:
        cpu:
            percpu: false
            fielddrop: ["time_*"]
        disk:
            mount_points: ["/"]
            ignore_fs: ["tmpfs", "devtmpfs"]
        elasticsearch:
            local: false
            cluster_health: true
        postgresql:
            databases: ["foo", "bar"]
            tagpass: 
                db: ["template", "postgres"]
    outputs:
        influxdb:
            precision: ms

This extra options will only be applied to plugins defined in templates/base_inputs.conf and any other plugins configured via relations.

## Apache input

For the apache input plugin, the charm provides the apache relation which uses apache-website interface. Current apache charm disables mod_status  and in order to telegraf apache input to work 'status' should be removed from the list of disable_modules in the apache charm config.

## Postgresql input 

Due to a [bug/regression](https://bugs.launchpad.net/postgresql-charm/+bug/1560262) in the new postgresql-charm in order to get actual postgresql metrics, two relations need to be established between telegraf and the postgresql service, first a plain juju-info relation to get telegraf setup and then a regular postgresql/db one. e.g:

juju add-relation telegraf:juju-info postgresql:juju-info 
juju add-relation telegraf:postgresql postgresql:db

## Output 

The only output plugin supported via relation is influxdb, any other output plugin needs to be configured manually (via juju set)

To use a different metrics storage, e.g: graphite. the plugin configuration needs to be set as a base64 string in outputs_config configuration.

For exmaple, save the following config to a file: 

    [[outputs.graphite]]
      servers = ["10.0.3.231:2003"]
      prefix = "juju_local.devel.telegraf"
      timeout = 10

And then 

    juju set telegraf outputs_config="$(cat graphite-output.conf | base64)"

This will make telegraf agents to send the metrics to the graphite instance.

# Contact Information

- Upstream https://github.com/influxdata/telegraf
- Upstream bug tracker https://github.com/influxdata/telegraf/issues
