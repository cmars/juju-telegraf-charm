import json

from charms.reactive import hook
from charms.reactive import RelationBase
from charms.reactive import scopes


class ExecRequires(RelationBase):
    scope = scopes.UNIT

    @hook('{requires:telegraf-exec}-relation-{joined,changed}')
    def changed(self):
        conv = self.conversation()
        conv.set_state('{relation_name}.connected')
        commands = conv.get_remote('commands') # list of commands to run
        data_format = conv.get_remote('data_format') # json, graphite, influx
        if commands and data_format:
            conv.set_state('{relation_name}.available')

    @hook('{requires:telegraf-exec}-relation-{departed,broken}')
    def broken(self):
        conv = self.conversation()
        conv.remove_state('{relation_name}.available')

    def commands(self):
        cmds = []
        for conv in self.conversations():
            commands = conv.get_remote('commands') # list of commands to run
            data_format = conv.get_remote('data_format') # json, graphite, influx
            cmd = {'commands': commands, 'data_format': data_format}
            # timeout is otional because we have telegraf 0.12.1 around
            cmd['timeout'] = conv.get_remote('timeout') or "5s"
            # other optional configs
            optionals = ['name_suffix', 'name_prefix', 'name_override', 'tags',
                         'interval',]
            for optional in optionals:
                value = conv.get_remote(optional)
                if value:
                    if optional == 'tags':
                        cmd[optional] = json.loads(value)
                    else:
                        cmd[optional] = value
            # by default run_on_this_unit is True, we risk to run the command
            # everywhere than not running it at all
            cmd['run_on_this_unit'] = True
            only_leader = conv.get_remote('run_on_this_unit')
            if only_leader in ('false', 'False'):
                cmd['run_on_this_unit'] = False
            cmds.append(cmd)
        return cmds
